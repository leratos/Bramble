"""Resource-owner login + consent gate in front of the OAuth ``/authorize``.

Phase 6.6. The self-hosted Authorization Server must not issue an
authorization code to just anyone who can reach ``/authorize`` (a
self-registered DCR client otherwise gets a read token to the whole
journal). :class:`OAuthOwnerGate` is a raw ASGI middleware that gates only
the interactive endpoints and lets every other request — crucially the
streaming ``/mcp`` and the programmatic ``/token`` / ``/register`` /
discovery routes — pass straight through untouched.

Flow on ``GET /authorize``:

1. No owner session -> render the login page (the original ``/authorize``
   URL is carried as ``next``).
2. ``POST /oauth/login`` (rate-limited) -> verify against the dedicated
   Argon2id owner secret, create a session, set an ``HttpOnly`` /
   ``SameSite=Strict`` cookie, redirect back to ``/authorize``.
3. With a session but no approval -> render the consent page showing the
   client / redirect / scope, with a CSRF token.
4. ``POST /oauth/consent`` (CSRF-checked) approve -> record a one-time,
   request-bound approval, redirect to ``/authorize``.
5. ``GET /authorize`` with a session and a matching approval -> delegate to
   the framework's authorize handler, which issues the code.

The CSRF-protected consent is what defeats a login-CSRF: even if an
attacker forces the owner to log in, they cannot forge the consent POST
(no session CSRF token), so no code is issued for the attacker's client.

It reuses :mod:`bramble.admin_auth` (``AdminAuthenticator``,
``SessionStore``, ``LoginRateLimiter``) against a *dedicated* owner secret
file, keeping the public connector login separate from the SSH-tunnelled
admin UI.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from bramble.admin_auth import (
    AdminAuthenticator,
    AdminSession,
    LoginRateLimiter,
    SessionStore,
)
from bramble.consent_store import ConsentApprovalStore
from bramble.oauth_config import OAuthConfig
from bramble.oauth_store import OAuthStore

_TEMPLATES_DIR = Path(__file__).parent / "templates" / "oauth"
# Same kebab-case project identifier the journal uses.
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

logger = logging.getLogger(__name__)

OWNER_SESSION_COOKIE = "bramble_oauth_owner"
AUTHORIZE_PATH = "/authorize"
LOGIN_PATH = "/oauth/login"
CONSENT_PATH = "/oauth/consent"

_MAX_FORM_BYTES = 64 * 1024
# Peers from which X-Forwarded-For may be trusted (our own loopback proxy).
_TRUSTED_PROXY_HOSTS = frozenset({"127.0.0.1", "::1"})
# The /authorize query fields that bind a consent to one request.
_FINGERPRINT_FIELDS = ("client_id", "redirect_uri", "scope", "code_challenge")


class OAuthOwnerGate:
    """ASGI middleware gating ``/authorize`` behind owner login + consent."""

    def __init__(
        self,
        app: Any,
        *,
        authenticator: AdminAuthenticator,
        sessions: SessionStore,
        login_limiter: LoginRateLimiter,
        approvals: ConsentApprovalStore,
        templates: Environment,
        cookie_secure: bool,
        cookie_max_age: int,
        store: OAuthStore,
        allow_write: bool = False,
    ) -> None:
        self.app = app
        self._authenticator = authenticator
        self._sessions = sessions
        self._login_limiter = login_limiter
        self._approvals = approvals
        self._templates = templates
        self._cookie_secure = cookie_secure
        self._cookie_max_age = cookie_max_age
        self._store = store
        self._allow_write = allow_write

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == AUTHORIZE_PATH and method == "GET":
            await self._handle_authorize(scope, receive, send)
            return
        if path == LOGIN_PATH and method == "POST":
            await self._respond(scope, receive, send, await self._login(scope, receive))
            return
        if path == CONSENT_PATH and method == "POST":
            await self._respond(
                scope, receive, send, await self._consent(scope, receive)
            )
            return
        # Everything else (incl. streaming /mcp) passes through unbuffered.
        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    # /authorize
    # ------------------------------------------------------------------
    async def _handle_authorize(self, scope: Any, receive: Any, send: Any) -> None:
        request = Request(scope, receive)
        session = self._session_from(request)
        if session is None:
            next_url = AUTHORIZE_PATH
            if request.url.query:
                next_url = f"{AUTHORIZE_PATH}?{request.url.query}"
            await self._respond(
                scope, receive, send, self._render_login(next_url)
            )
            return

        params = dict(request.query_params)
        fingerprint = self._fingerprint(params)
        if self._approvals.consume(session_id=self._sid(request), fingerprint=fingerprint):
            # Owner authenticated and approved this exact request -> let the
            # framework's /authorize handler issue the code.
            await self.app(scope, receive, send)
            return

        await self._respond(
            scope, receive, send, self._render_consent(session, request.url.query, params)
        )

    # ------------------------------------------------------------------
    # /oauth/login
    # ------------------------------------------------------------------
    async def _login(self, scope: Any, receive: Any) -> Response:
        request = Request(scope, receive)
        client_ip = self._client_ip(request)
        form = await self._read_form(request)
        next_url = _safe_next(form.get("next"))

        if not self._login_limiter.allow(client_ip):
            logger.warning(
                "oauth owner login throttled", extra={"client_ip": client_ip}
            )
            return self._render_login(
                next_url, error="Too many attempts. Try again later.", status=429
            )

        username = form.get("username", "")
        password = form.get("password", "")
        if self._authenticator.verify(username, password):
            self._login_limiter.record_success(client_ip)
            session_id = self._sessions.create(self._authenticator.username)
            response = RedirectResponse(url=next_url, status_code=303)
            self._set_cookie(response, session_id)
            return response

        self._login_limiter.record_failure(client_ip)
        logger.warning("oauth owner login failed", extra={"client_ip": client_ip})
        return self._render_login(next_url, error="Login failed.", status=401)

    # ------------------------------------------------------------------
    # /oauth/consent
    # ------------------------------------------------------------------
    async def _consent(self, scope: Any, receive: Any) -> Response:
        request = Request(scope, receive)
        session = self._session_from(request)
        form = await self._read_form(request)
        authorize_query = form.get("authorize_query", "")
        if session is None:
            next_url = f"{AUTHORIZE_PATH}?{authorize_query}" if authorize_query else AUTHORIZE_PATH
            return self._render_login(_safe_next(next_url))

        if not _csrf_ok(session, form):
            logger.warning("oauth consent CSRF rejected")
            return HTMLResponse("CSRF validation failed.", status_code=403)

        params = dict(_parse_query(authorize_query))
        if form.get("decision") != "approve":
            return HTMLResponse(
                self._templates.get_template("denied.html").render(),
                status_code=200,
            )

        # Record the owner's write grant for this connector (keyed by
        # client_id). Honoured only when write is enabled; an empty project
        # (or write disabled) records an explicit read-only grant, so a
        # re-consent can also downgrade a previous write grant.
        client_id = params.get("client_id", "")
        project = (form.get("project") or "").strip()
        if self._allow_write and project:
            if not _KEBAB_CASE_RE.match(project):
                return self._render_consent(
                    session,
                    authorize_query,
                    params,
                    error=f"Invalid project name {project!r} – use kebab-case.",
                )
            can_write, grant_project = True, project
        else:
            can_write, grant_project = False, None
        if client_id:
            await asyncio.to_thread(
                self._store.save_client_grant,
                client_id,
                project=grant_project,
                can_write=can_write,
            )

        self._approvals.approve(
            session_id=self._sid(request), fingerprint=self._fingerprint(params)
        )
        target = AUTHORIZE_PATH
        if authorize_query:
            target = f"{AUTHORIZE_PATH}?{authorize_query}"
        return RedirectResponse(url=_safe_next(target), status_code=303)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_login(
        self, next_url: str, *, error: str | None = None, status: int = 200
    ) -> Response:
        html = self._templates.get_template("login.html").render(
            next_url=next_url, error=error
        )
        return HTMLResponse(html, status_code=status)

    def _render_consent(
        self,
        session: AdminSession,
        authorize_query: str,
        params: dict[str, str],
        *,
        error: str | None = None,
    ) -> Response:
        html = self._templates.get_template("consent.html").render(
            csrf_token=session.csrf_token,
            authorize_query=authorize_query,
            client_id=params.get("client_id", ""),
            redirect_uri=params.get("redirect_uri", ""),
            scope=params.get("scope", ""),
            allow_write=self._allow_write,
            error=error,
        )
        return HTMLResponse(html, status_code=200 if error is None else 400)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _session_from(self, request: Request) -> AdminSession | None:
        return self._sessions.get(request.cookies.get(OWNER_SESSION_COOKIE))

    @staticmethod
    def _sid(request: Request) -> str | None:
        return request.cookies.get(OWNER_SESSION_COOKIE)

    def _set_cookie(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            key=OWNER_SESSION_COOKIE,
            value=session_id,
            max_age=self._cookie_max_age,
            httponly=True,
            secure=self._cookie_secure,
            samesite="strict",
            path="/",
        )

    @staticmethod
    def _fingerprint(params: dict[str, str]) -> str:
        canonical = "\n".join(params.get(f, "") for f in _FINGERPRINT_FIELDS)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if peer in _TRUSTED_PROXY_HOSTS:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                original = forwarded.split(",")[0].strip()
                if original:
                    return original
        return peer

    @staticmethod
    async def _read_form(request: Request) -> dict[str, str]:
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > _MAX_FORM_BYTES:
                    return {}
            except ValueError:
                return {}
        body = await request.body()
        if len(body) > _MAX_FORM_BYTES:
            return {}
        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError:
            return {}
        return dict(parse_qsl(decoded, keep_blank_values=True))

    @staticmethod
    async def _respond(scope: Any, receive: Any, send: Any, response: Response) -> None:
        await response(scope, receive, send)


def _safe_next(value: str | None) -> str:
    """Constrain post-login/consent redirects to our own /authorize path."""

    if not value or "\r" in value or "\n" in value:
        return AUTHORIZE_PATH
    if value == AUTHORIZE_PATH or value.startswith(f"{AUTHORIZE_PATH}?"):
        return value
    return AUTHORIZE_PATH


def build_owner_templates() -> Environment:
    """Jinja2 environment for the gate's HTML, with HTML autoescaping on.

    Autoescaping is essential here: the consent page echoes the requesting
    client_id / redirect_uri / scope, which are attacker-influenced.
    """

    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def build_owner_gate(config: OAuthConfig, store: OAuthStore) -> Middleware:
    """Construct the ASGI gate middleware from an :class:`OAuthConfig`.

    Loads the dedicated owner secret (fails fast if it is missing — OAuth
    must not run without an owner credential) and wires the reused
    admin-auth primitives plus the ``OAuthStore`` (for write grants).
    Returns a Starlette ``Middleware`` ready to hand to
    ``http_app(middleware=[...])``.
    """

    authenticator = AdminAuthenticator(config.owner_secret_file)
    sessions = SessionStore(
        idle_seconds=config.owner_session_idle_seconds,
        absolute_seconds=config.owner_session_absolute_seconds,
    )
    login_limiter = LoginRateLimiter(
        max_attempts=config.owner_login_max_attempts,
        window_seconds=config.owner_login_window_seconds,
    )
    return Middleware(
        OAuthOwnerGate,
        authenticator=authenticator,
        sessions=sessions,
        login_limiter=login_limiter,
        approvals=ConsentApprovalStore(),
        templates=build_owner_templates(),
        cookie_secure=config.owner_cookie_secure,
        cookie_max_age=config.owner_session_idle_seconds,
        store=store,
        allow_write=config.allow_oauth_write,
    )


def _csrf_ok(session: AdminSession, form: dict[str, str]) -> bool:
    return secrets.compare_digest(form.get("csrf_token", ""), session.csrf_token)


def _parse_query(query: str) -> list[tuple[str, str]]:
    return parse_qsl(query, keep_blank_values=True)
