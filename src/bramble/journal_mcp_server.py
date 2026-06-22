"""Bramble's MCP server façade.

The :class:`JournalMCPServer` owns the FastMCP instance and the tool
registrations. It deliberately keeps the FastMCP setup and the
:class:`JournalDB` accessor separate: ``app`` is the thing FastMCP
needs, ``db`` is the thing tools talk to. Tests construct the server
with an in-memory or temp-file ``JournalDB`` and connect via the
FastMCP in-process ``Client``.

The ``auth_validator`` and ``rate_limiter`` constructor arguments are
consumed by :class:`_AuthRateLimitMiddleware`, which FastMCP runs
before every tool call. They are supplied together for the
authenticated ``http`` transport and left unset for local ``stdio``
use, where no token gate applies.

Tool registration happens once in :meth:`__init__` via
:meth:`_register_tools`, which is the file's central index of which
MCP tools exist.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.dependencies import (
    get_access_token,
    get_http_headers,
    get_http_request,
)
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from bramble.agent_guide import AGENT_GUIDE, AGENT_GUIDE_VERSION
from bramble.auth_validator import AuthValidator
from bramble.journal_context import JournalContext
from bramble.journal_db import JournalDB
from bramble.journal_digest import JournalDigest
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.mcp_errors import translate_errors
from bramble.open_item import OpenItemView
from bramble.rate_limiter import RateLimiter
from bramble.static_token_verifier import STATIC_CLIENT_PREFIX

logger = logging.getLogger(__name__)

# Set by :class:`_AuthRateLimitMiddleware` to the project a request's
# bearer token belongs to, so ``journal_append`` can enforce the
# write-scope binding (Phase-3 Decision B). It stays ``None`` for the
# stdio transport and for any server built without an auth validator,
# which is exactly when no scope binding should apply.
_token_project: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bramble_token_project", default=None
)

# Peers from which an ``X-Forwarded-For`` header may be trusted. Plesk's
# Nginx terminates TLS on the same host and proxies to loopback, so a
# loopback peer is our own proxy. Any other peer could forge the header.
_TRUSTED_PROXY_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})


# ---------------------------------------------------------------------------
# Validation helpers (Decision E: kebab-case enforced in the MCP layer)
# ---------------------------------------------------------------------------
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _require_kebab_case(project: str) -> None:
    """Reject any project identifier that is not strict kebab-case.

    The pattern matches lowercase letters, digits and hyphens with a
    non-hyphen first character. Whitespace, underscores, mixed case
    and empty strings all fail. The check lives in the MCP layer only
    – :class:`JournalDB` itself remains project-agnostic.
    """

    if not isinstance(project, str):
        raise TypeError("project must be a string")
    if not _KEBAB_CASE_RE.match(project):
        raise ValueError(
            f"project {project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$"
        )


def _require_kebab_case_filters(projects: list[str] | None) -> None:
    if projects is None:
        return
    if isinstance(projects, (str, bytes)):
        raise TypeError("projects must be an iterable of project strings")
    for project in projects:
        _require_kebab_case(project)


def _entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    """Serialise a :class:`JournalEntry` to a plain MCP-friendly dict."""

    return {
        "id": entry.id,
        "project": entry.project,
        "timestamp": entry.timestamp_iso(),
        "status": entry.status.value,
        "phase": entry.phase,
        "title": entry.title,
        "content": entry.content,
        "actor": entry.actor,
        "client": entry.client,
        "source": entry.source,
        "tags": list(entry.tags),
        "links": [
            {"to_entry_id": link.entry_id, "relation": link.relation.value}
            for link in entry.links
        ],
        "backlinks": [
            {"from_entry_id": link.entry_id, "relation": link.relation.value}
            for link in entry.backlinks
        ],
    }


def _open_item_to_dict(view: OpenItemView) -> dict[str, Any]:
    """Serialise an :class:`OpenItemView` to an MCP-friendly dict.

    The underlying entry fields are kept at the top level (backward
    compatible with the pre-Phase-4f entry shape) and augmented with the
    open-item lifecycle annotations.
    """

    data = _entry_to_dict(view.entry)
    data["open_state"] = view.state
    data["resolution_reason"] = view.resolution_reason
    data["resolved_by_id"] = view.resolved_by_id
    data["age_days"] = view.age_days
    return data


# journal_context is the first call of every session. Returning full entry
# bodies for every slice makes the payload large enough that big projects
# overflow the client's tool-result budget and spill to a file. The curated
# context exists to orient, so it returns a content preview by default; full
# bodies stay available via journal_read / journal_search (or full=True).
_CONTEXT_CONTENT_PREVIEW_CHARS = 500


def _truncate_content(content: str, max_chars: int) -> str:
    snippet = content[:max_chars]
    # Avoid cutting mid-word when a space sits near the end of the window.
    space = snippet.rfind(" ")
    if space >= max_chars - 40:
        snippet = snippet[:space]
    return snippet.rstrip() + " …"


def _preview_entry_to_dict(
    entry: JournalEntry, *, max_chars: int | None
) -> dict[str, Any]:
    """Serialise an entry, previewing ``content`` when ``max_chars`` is set.

    ``content_chars`` always carries the original length and
    ``content_truncated`` whether the preview was shortened, so a caller can
    decide to fetch the full entry. ``max_chars=None`` keeps the full body.
    """

    data = _entry_to_dict(entry)
    full = data["content"]
    data["content_chars"] = len(full)
    if max_chars is not None and len(full) > max_chars:
        data["content"] = _truncate_content(full, max_chars)
        data["content_truncated"] = True
    else:
        data["content_truncated"] = False
    return data


def _preview_open_item_to_dict(
    view: OpenItemView, *, max_chars: int | None
) -> dict[str, Any]:
    data = _preview_entry_to_dict(view.entry, max_chars=max_chars)
    data["open_state"] = view.state
    data["resolution_reason"] = view.resolution_reason
    data["resolved_by_id"] = view.resolved_by_id
    data["age_days"] = view.age_days
    return data


def _digest_to_dict(digest: JournalDigest) -> dict[str, Any]:
    return {
        "range": {
            "since": digest.range_since.isoformat(),
            "until": digest.range_until.isoformat(),
        },
        "projects": list(digest.projects),
        "counts_by_project": dict(digest.counts_by_project),
        "counts_by_status": dict(digest.counts_by_status),
        "entries": [_entry_to_dict(entry) for entry in digest.entries],
        "open_items": [_entry_to_dict(entry) for entry in digest.open_items],
        "bugfixes": [_entry_to_dict(entry) for entry in digest.bugfixes],
        "decisions": [_entry_to_dict(entry) for entry in digest.decisions],
    }


def _context_to_dict(context: JournalContext, *, full: bool = False) -> dict[str, Any]:
    max_chars = None if full else _CONTEXT_CONTENT_PREVIEW_CHARS
    return {
        "project": context.project,
        "recent": [
            _preview_entry_to_dict(entry, max_chars=max_chars)
            for entry in context.recent
        ],
        "open_items": [
            _preview_open_item_to_dict(view, max_chars=max_chars)
            for view in context.open_items
        ],
        "recent_bugfixes": [
            _preview_entry_to_dict(entry, max_chars=max_chars)
            for entry in context.recent_bugfixes
        ],
        "recent_decisions": [
            _preview_entry_to_dict(entry, max_chars=max_chars)
            for entry in context.recent_decisions
        ],
        "related_projects": list(context.related_projects),
        "suggested_searches": list(context.suggested_searches),
    }


def _mcp_source(source: str | None) -> str:
    if source is None or not source.strip():
        return "mcp"
    return source


def _default_resolve_content(ids: list[int]) -> str:
    refs = ", ".join(f"#{entry_id}" for entry_id in ids)
    return (
        "Status closure (append-only): closes the following open items "
        f"via resolves links: {refs}."
    )


def _enforce_project_scope(project: str) -> None:
    """Reject a write whose project does not match the bearer token.

    :class:`_AuthRateLimitMiddleware` stashes the token's project in
    :data:`_token_project` before the tool runs. When that is unset –
    stdio transport, or a server built without an auth validator – no
    binding applies and any project may be written (Decision B).
    """

    token_project = _token_project.get()
    if token_project is not None and project != token_project:
        raise ValueError(
            f"token is scoped to project {token_project!r}; "
            f"it cannot append to {project!r}"
        )


# ---------------------------------------------------------------------------
# Request-credential extraction (Phase-3 auth)
# ---------------------------------------------------------------------------
def _bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    Returns ``None`` for a missing header, a non-Bearer scheme, or an
    empty token – every one of which is an authentication failure.
    """

    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _resolve_client_ip(request: Any) -> str:
    """Return the real client IP for ``request``.

    ``X-Forwarded-For`` is trusted only when the immediate peer is a
    local proxy (:data:`_TRUSTED_PROXY_HOSTS`); a request from any
    other peer uses its peer address verbatim. This is the
    ``X-Forwarded-For`` spoofing mitigation from the Phase-3 risk list:
    without it an attacker could forge the header and dodge Fail2Ban.
    """

    peer = request.client.host if request.client else "unknown"
    if peer in _TRUSTED_PROXY_HOSTS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Plesk-Nginx appends, so the left-most entry is the
            # originating client.
            original = forwarded.split(",")[0].strip()
            if original:
                return original
    return peer


def _extract_credentials() -> tuple[str | None, str]:
    """Pull the bearer token and client IP out of the current request.

    The middleware is mounted only for the ``http`` transport, so an
    HTTP request is normally present. If it is missing the request
    cannot be authenticated, so this returns no token and the caller
    fails closed.
    """

    try:
        request = get_http_request()
    except RuntimeError:
        return None, "unknown"
    headers = get_http_headers(include={"authorization"})
    return _bearer_token(headers.get("authorization")), _resolve_client_ip(request)


class _AuthRateLimitMiddleware(Middleware):
    """FastMCP middleware enforcing bearer-token auth and rate limits.

    Runs before every tool call on the ``http`` transport. This class
    is only the wiring that bridges FastMCP's hook to Bramble's own
    :class:`~bramble.auth_validator.AuthValidator` and
    :class:`~bramble.rate_limiter.RateLimiter`; the auth and
    rate-limit logic itself lives in those two classes.
    """

    def __init__(
        self, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        self._auth_validator = auth_validator
        self._rate_limiter = rate_limiter

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        token, client_ip = _extract_credentials()
        project = self._authorize(token=token, client_ip=client_ip)
        # Hand the resolved project to journal_append's scope check.
        reset = _token_project.set(project)
        try:
            return await call_next(context)
        finally:
            _token_project.reset(reset)

    def _authorize(self, *, token: str | None, client_ip: str) -> str:
        """Decide a single request: return the token's project or raise.

        Kept free of FastMCP types – it takes the already-extracted
        token and client IP – so it is unit-testable in-process. The
        order matters: the per-IP limit is a backstop that applies
        before the token is known, then auth, then the per-token limit.
        """

        if not self._rate_limiter.allow_ip(client_ip):
            raise ToolError("rate limit exceeded; slow down")
        project = self._auth_validator.authenticate(token, client_ip=client_ip)
        if project is None:
            raise ToolError(
                "authentication required: missing or invalid bearer token"
            )
        if not self._rate_limiter.allow_project(project):
            raise ToolError("rate limit exceeded; slow down")
        return project


# Tools that mutate the journal. The OAuth path is read-only (Phase-6
# decision D3): a principal whose scopes lack ``journal:write`` may not call
# these. Static tokens carry journal:write and keep their write capability.
_WRITE_TOOLS: frozenset[str] = frozenset({"journal_append", "journal_resolve"})

# The scope a principal must hold to call a write tool.
_WRITE_SCOPE = "journal:write"


class _PrincipalRateLimitMiddleware(Middleware):
    """Rate-limit + scope policy for the OAuth (``MultiAuth``) http mode.

    When the server is built with an ``auth_provider`` (FastMCP native
    auth), the bearer token is validated at the ASGI layer and the
    authenticated principal is exposed via
    :func:`~fastmcp.server.dependencies.get_access_token`. This tool
    middleware enforces the Bramble-specific policy the framework does
    not:

    * per-IP and per-principal rate limits;
    * the read-only gate – an OAuth principal (no ``journal:write`` scope)
      cannot call a write tool, while static tokens may;
    * the project write-scope binding – a static token is bound to its
      project (``client_id == "static:<project>"``) so ``journal_append``
      can only write there. OAuth principals carry no project binding and
      cannot write anyway.

    A request that reaches the tool layer with no principal is refused
    (defence in depth): on the real http path the ASGI auth gate fails
    such a request before it gets here, so this guards the in-process and
    misconfiguration cases. The decision logic lives in :meth:`_authorize`
    (free of FastMCP types) so it is unit-testable in-process.
    """

    def __init__(self, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        principal = get_access_token()
        client_ip = self._client_ip()
        tool_name = getattr(context.message, "name", "") or ""
        project = self._authorize(
            principal=principal, client_ip=client_ip, tool_name=tool_name
        )
        reset = _token_project.set(project)
        try:
            return await call_next(context)
        finally:
            _token_project.reset(reset)

    @staticmethod
    def _client_ip() -> str:
        try:
            request = get_http_request()
        except RuntimeError:
            return "unknown"
        return _resolve_client_ip(request)

    def _authorize(
        self, *, principal: Any, client_ip: str, tool_name: str
    ) -> str | None:
        """Apply rate-limit + scope policy; return the bound project or None.

        Takes the already-resolved principal (an ``AccessToken`` or
        ``None``) so it can be unit-tested without a live request. The
        per-IP limit is the backstop applied first; then the principal is
        required; then the per-principal limit, the write gate and the
        project binding.
        """

        if not self._rate_limiter.allow_ip(client_ip):
            raise ToolError("rate limit exceeded; slow down")
        if principal is None:
            raise ToolError(
                "authentication required: missing or invalid bearer token"
            )
        principal_key = getattr(principal, "client_id", None) or "unknown"
        if not self._rate_limiter.allow_project(principal_key):
            raise ToolError("rate limit exceeded; slow down")

        # Derive the project binding first: only a static principal
        # ("static:<project>") carries one; OAuth principals get None.
        client_id = getattr(principal, "client_id", None) or ""
        project = (
            client_id[len(STATIC_CLIENT_PREFIX) :]
            if client_id.startswith(STATIC_CLIENT_PREFIX)
            else None
        )

        # A write tool requires BOTH the journal:write scope AND a project
        # binding. Requiring the binding closes a hole: if an operator adds
        # journal:write to BRAMBLE_OAUTH_SCOPES, an OAuth principal would have
        # the scope but project=None, and _enforce_project_scope treats None as
        # unrestricted – letting it write to any project. No project => no write.
        scopes = set(getattr(principal, "scopes", None) or ())
        if tool_name in _WRITE_TOOLS and (_WRITE_SCOPE not in scopes or project is None):
            raise ToolError(
                f"this token is read-only; {tool_name} requires write access "
                "bound to a project"
            )
        return project


class JournalMCPServer:
    """MCP-facing server that exposes :class:`JournalDB` operations.

    Parameters
    ----------
    db:
        The :class:`JournalDB` instance to read from / write to. Must
        already be initialised (``db.initialize()`` was called).
    auth_validator:
        Resolves bearer tokens to projects. Supply together with
        ``rate_limiter`` to gate the ``http`` transport with the Phase-3
        static-bearer path; leave unset for ``stdio``. Providing only one
        of the two raises. Mutually exclusive with ``auth_provider``.
    rate_limiter:
        Token-bucket request limiter. Required by both the static-bearer
        path (with ``auth_validator``) and the OAuth path (with
        ``auth_provider``).
    auth_provider:
        A FastMCP :class:`AuthProvider` (Phase-6: a ``MultiAuth`` wrapping
        the self-hosted OAuth AS plus a static-token verifier). When
        supplied, it is handed to ``FastMCP(auth=...)`` so the framework
        mounts the discovery / ``/authorize`` / ``/token`` / ``/register``
        routes and gates ``/mcp`` at the ASGI layer; a
        :class:`_PrincipalRateLimitMiddleware` then enforces rate limits,
        the read-only gate and the project binding. Requires
        ``rate_limiter`` and is mutually exclusive with ``auth_validator``.
    """

    def __init__(
        self,
        db: JournalDB,
        *,
        auth_validator: AuthValidator | None = None,
        rate_limiter: RateLimiter | None = None,
        auth_provider: AuthProvider | None = None,
    ) -> None:
        if not isinstance(db, JournalDB):
            raise TypeError("db must be a JournalDB instance")
        if auth_provider is not None:
            if auth_validator is not None:
                raise ValueError(
                    "auth_provider is mutually exclusive with auth_validator: "
                    "in the OAuth mode the static-token path lives inside the "
                    "provider (MultiAuth verifier), not a separate validator"
                )
            if rate_limiter is None:
                raise ValueError("auth_provider requires a rate_limiter")
        elif (auth_validator is None) != (rate_limiter is None):
            raise ValueError(
                "auth_validator and rate_limiter must be provided together: "
                "both for the authenticated http transport, or neither"
            )

        self._db = db
        self._auth_validator = auth_validator
        self._rate_limiter = rate_limiter
        self._auth_provider = auth_provider

        app_kwargs: dict[str, Any] = {
            "name": "bramble",
            "instructions": (
                "Shared development journal across projects. "
                "At session start, first call journal_guide for the "
                "canonical, project-agnostic working conventions "
                "(append-only model, statuses, tags, corrections, "
                "open-item/resolves semantics, DoD) and follow them. "
                "Use journal_append to record new entries, journal_read "
                "to fetch recent entries, journal_search or "
                "journal_search_all for full-text search, journal_context "
                "for curated session-start context, journal_digest for "
                "period summaries, journal_open_items for open-task "
                "snapshots, journal_resolve to close open items via "
                "resolves links, and journal_list_projects for an overview."
            ),
        }
        if auth_provider is not None:
            app_kwargs["auth"] = auth_provider
        self._app = FastMCP(**app_kwargs)
        self._register_tools()
        if auth_provider is not None and rate_limiter is not None:
            self._app.add_middleware(_PrincipalRateLimitMiddleware(rate_limiter))
        elif auth_validator is not None and rate_limiter is not None:
            self._app.add_middleware(
                _AuthRateLimitMiddleware(auth_validator, rate_limiter)
            )

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------
    @property
    def app(self) -> FastMCP:
        """The underlying FastMCP application.

        Used by tests (passed to :class:`fastmcp.Client` for in-process
        calls) and by :mod:`bramble.__main__` to call ``run()``.
        """

        return self._app

    @property
    def db(self) -> JournalDB:
        """The :class:`JournalDB` instance the tools operate on."""

        return self._db

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------
    def _register_tools(self) -> None:
        """Register all MCP tools on :attr:`app`.

        All tools live in this single method on purpose: the
        ``self`` closure means each tool implicitly carries the
        :class:`JournalDB` it talks to, and keeping the registrations
        together makes the public surface of the server obvious at a
        glance.
        """

        app = self._app
        db = self._db
        # journal_resolve classifies (read) then appends (write) across two
        # connections, so two concurrent resolves of the same id could both
        # pass the "open" check before either commits and both write a closing
        # note. Bramble runs as a single process, so this per-process lock
        # serialises journal_resolve and closes that window. The harm is only a
        # redundant append-only entry; resolve is rare, so serialising is free.
        resolve_lock = asyncio.Lock()

        @app.tool
        @translate_errors
        async def journal_read(project: str, n: int = 80) -> list[dict[str, Any]]:
            """Return the ``n`` most recent journal entries for ``project``.

            Entries are returned newest first. ``project`` must match
            the kebab-case pattern ``^[a-z0-9][a-z0-9-]*$``.
            """

            _require_kebab_case(project)
            entries = await asyncio.to_thread(db.read, project, n)
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_append(
            project: str,
            status: str,
            content: str,
            phase: str | None = None,
            title: str | None = None,
            tags: list[str] | None = None,
            links: list[dict[str, Any]] | None = None,
            actor: str | None = None,
            client: str | None = None,
            source: str | None = None,
        ) -> dict[str, Any]:
            """Append a new journal entry and return it with its assigned id.

            ``status`` must be one of: ``in_arbeit``, ``abgeschlossen``,
            ``notiz``, ``bugfix``. The timestamp is set server-side
            (``datetime.now(UTC)``); clients cannot override it.

            On the authenticated ``http`` transport the bearer token is
            bound to one project: appending to a different project is
            rejected (Phase-3 Decision B).
            """

            _require_kebab_case(project)
            _enforce_project_scope(project)
            allowed = ", ".join(s.value for s in JournalStatus)
            try:
                status_enum = JournalStatus(status)
            except ValueError as exc:
                raise ValueError(
                    f"status {status!r} is not allowed; must be one of: {allowed}"
                ) from exc

            entry = JournalEntry(
                project=project,
                status=status_enum,
                content=content,
                phase=phase,
                title=title,
                actor=actor,
                client=client,
                source=_mcp_source(source),
                tags=tags or (),
                links=links or (),
            )
            persisted = await asyncio.to_thread(db.append, entry)
            return _entry_to_dict(persisted)

        @app.tool
        @translate_errors
        async def journal_resolve(
            project: str,
            resolves: list[int],
            title: str | None = None,
            content: str | None = None,
        ) -> dict[str, Any]:
            """Close open work items by writing one append-only resolving entry.

            The reliable way to mark a stale ``in_arbeit`` entry done is a
            ``resolves`` link from a later entry — merely mentioning ids in
            prose (e.g. "#655 is done") does NOT close them. This tool writes a
            single ``notiz`` that links ``resolves -> <id>`` for every id in
            ``resolves`` and reports exactly which ids it closed and which it
            skipped, so the closure is verifiable.

            ``resolves`` must list ids of ``in_arbeit`` entries in ``project``.
            Ids that are missing, belong to another project, are not
            ``in_arbeit``, or are already effectively closed are skipped (no
            link created) and listed under ``skipped`` (``missing`` /
            ``other_project`` / ``not_in_arbeit`` / ``already_resolved``). If
            nothing is resolvable, no entry is written. On the authenticated
            ``http`` transport the bearer token is bound to one project
            (Phase-3 Decision B).
            """

            _require_kebab_case(project)
            _enforce_project_scope(project)
            async with resolve_lock:
                classification = await asyncio.to_thread(
                    db.classify_resolve_targets, project, resolves
                )
                resolvable = [
                    t for t, state in classification.items() if state == "open"
                ]
                skipped = {
                    "missing": [
                        t for t, s in classification.items() if s == "missing"
                    ],
                    "other_project": [
                        t for t, s in classification.items() if s == "other_project"
                    ],
                    "not_in_arbeit": [
                        t for t, s in classification.items() if s == "not_in_arbeit"
                    ],
                    "already_resolved": [
                        t for t, s in classification.items() if s == "already_resolved"
                    ],
                }

                entry_dict: dict[str, Any] | None = None
                if resolvable:
                    if content and content.strip():
                        body = content
                    else:
                        body = _default_resolve_content(resolvable)
                    entry = JournalEntry(
                        project=project,
                        status=JournalStatus.NOTIZ,
                        content=body,
                        title=title,
                        source=_mcp_source(None),
                        links=[
                            {"to_entry_id": t, "relation": "resolves"}
                            for t in resolvable
                        ],
                    )
                    persisted = await asyncio.to_thread(db.append, entry)
                    entry_dict = _entry_to_dict(persisted)

            return {
                "entry": entry_dict,
                "resolved": resolvable,
                "skipped": skipped,
            }

        @app.tool
        @translate_errors
        async def journal_search(
            project: str,
            query: str,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            """Full-text-search ``project``'s entries for ``query``.

            Pass SQLite FTS5 MATCH syntax directly: bare words for AND,
            ``OR`` for alternation, double-quoted strings for phrase
            search, and ``NEAR()`` for proximity. Malformed FTS5
            syntax returns an empty list (not an error) to match the
            Phase-1 ``JournalDB.search`` behaviour.
            """

            _require_kebab_case(project)
            entries = await asyncio.to_thread(db.search, project, query, limit)
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_search_all(
            query: str,
            limit: int = 20,
            projects: list[str] | None = None,
            statuses: list[str] | None = None,
            tags: list[str] | None = None,
        ) -> list[dict[str, Any]]:
            """Full-text-search journal entries across projects.

            Pass SQLite FTS5 MATCH syntax directly. Optional filters
            narrow results by ``projects``, ``statuses`` and ``tags``;
            multiple tags use AND semantics. At most 100 results can be
            requested. Malformed FTS5 syntax returns an empty list.
            """

            _require_kebab_case_filters(projects)
            entries = await asyncio.to_thread(
                db.search_all,
                query,
                limit,
                projects=projects,
                statuses=statuses,
                tags=tags,
            )
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_digest(
            project: str | None = None,
            since: str = "7d",
            until: str | None = None,
            tags: list[str] | None = None,
            limit: int = 80,
        ) -> dict[str, Any]:
            """Return a structured journal digest for a time range.

            ``since`` accepts ``24h``, ``7d``, ``30d`` or an ISO
            timestamp. ``until`` accepts an ISO timestamp and defaults
            to now. The tool is read-only and returns deterministic
            counts plus capped entry lists.
            """

            if project is not None:
                _require_kebab_case(project)
            digest = await asyncio.to_thread(
                db.digest,
                project=project,
                since=since,
                until=until,
                tags=tags,
                limit=limit,
            )
            return _digest_to_dict(digest)

        @app.tool
        @translate_errors
        async def journal_context(
            project: str,
            n_recent: int = 10,
            include_cross_project: bool = True,
            full: bool = False,
        ) -> dict[str, Any]:
            """Return curated session-start context for one project.

            The output combines project-local recency with deterministic
            slices (open items, bugfixes, decisions) and optional
            cross-project related-project hints. The tool is strictly
            read-only and backward compatible with sparse metadata.

            To keep this first-of-session call small, entry ``content`` is
            previewed (truncated) by default; each entry carries
            ``content_chars`` and ``content_truncated`` so you can fetch the
            full body via journal_read/journal_search. Pass ``full=True`` to
            get untruncated content.
            """

            _require_kebab_case(project)
            if not isinstance(full, bool):
                raise TypeError("full must be a bool")
            context = await asyncio.to_thread(
                db.context,
                project=project,
                n_recent=n_recent,
                include_cross_project=include_cross_project,
            )
            return _context_to_dict(context, full=full)

        @app.tool
        @translate_errors
        async def journal_open_items(
            project: str | None = None,
            limit: int = 50,
            include_resolved: bool = False,
            stale_after_days: int = 30,
        ) -> list[dict[str, Any]]:
            """Return newest open work items with resolution + staleness.

            Base set is ``status='in_arbeit'``. The journal is append-only,
            so completion is a *new* entry; this tool infers which started
            items are effectively done instead of reporting every one as
            open. Each item carries ``open_state`` (``open`` | ``stale`` |
            ``resolved``), ``resolution_reason`` (``link`` | ``text`` |
            ``title`` | ``phase`` | ``null``), ``resolved_by_id`` and
            ``age_days``.

            * ``resolved`` items are inferred-closed and hidden unless
              ``include_resolved`` is true; the reason/resolver make the
              suppression auditable.
            * ``stale`` items are unresolved but older than
              ``stale_after_days`` (default 30); they are still returned,
              just flagged.
            * the most reliable close signal is an explicit ``resolves``
              link from the completing entry to the open one.

            Optional ``project`` narrows to one project; otherwise the
            result is cross-project. At most 100 rows can be requested.
            """

            if project is not None:
                _require_kebab_case(project)
            views = await asyncio.to_thread(
                db.open_items_view,
                project=project,
                limit=limit,
                include_resolved=include_resolved,
                stale_after_days=stale_after_days,
            )
            return [_open_item_to_dict(view) for view in views]

        @app.tool
        @translate_errors
        async def journal_list_projects() -> list[dict[str, Any]]:
            """List all projects with entry counts and most recent timestamps.

            Returned in descending order of ``last_timestamp`` (most
            recent activity first), with alphabetical tie-break for
            stability.
            """

            summaries = await asyncio.to_thread(db.project_overview)
            return [
                {
                    "project": s.name,
                    "entry_count": s.entry_count,
                    "last_timestamp": s.last_timestamp_iso(),
                }
                for s in summaries
            ]

        @app.tool
        @translate_errors
        async def journal_guide() -> dict[str, Any]:
            """Return the canonical shared agent working conventions.

            This is the single source of truth for the project-agnostic
            Bramble journal workflow (append-only model, statuses, tags,
            corrections, open-item/resolves semantics, session start/end
            and Definition of Done). Projects reference this guide instead
            of copying the conventions into each project's instructions, so
            there is one place to maintain and no drift.

            Call this at session start and follow it. ``version`` is the ISO
            date of the last change, so a caller can tell whether the guide
            moved since it last read it.
            """

            return {"version": AGENT_GUIDE_VERSION, "guide": AGENT_GUIDE}

    # ------------------------------------------------------------------
    # Transport entry point
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        transport: str = "stdio",
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        """Start serving via the requested transport (blocks).

        :param transport: ``"stdio"`` for Claude Desktop / Code, or
            ``"http"`` for the HTTP stub (Phase 3 puts Nginx + auth in
            front).
        :param host: Bind host for ``http`` transport.
        :param port: Bind port for ``http`` transport.
        """

        if transport == "stdio":
            logger.info("starting MCP server on stdio")
            self._app.run(transport="stdio")
        elif transport == "http":
            if host is None or port is None:
                raise ValueError("http transport requires host and port")
            logger.info(
                "starting MCP server on http",
                extra={"host": host, "port": port},
            )
            self._app.run(transport="http", host=host, port=port)
        else:
            raise ValueError(
                f"unsupported transport {transport!r}; expected 'stdio' or 'http'"
            )
