"""Integration tests for :mod:`bramble.oauth_owner_gate` (driven via ASGI)."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import httpx

from bramble.admin_auth import (
    AdminAuthenticator,
    LoginRateLimiter,
    SessionStore,
    hash_admin_password,
)
from bramble.consent_store import ConsentApprovalStore
from bramble.oauth_owner_gate import OAuthOwnerGate, build_owner_templates

_USER = "owner"
_PASS = "correct-horse-battery"
_AUTHZ = (
    "/authorize?client_id=claude&redirect_uri=https://claude.ai/cb"
    "&scope=journal:read&code_challenge=abc"
)


async def _inner(scope, receive, send) -> None:
    """Stand-in for the framework: marks delegation distinctly per path."""

    path = scope["path"]
    body = b"DELEGATED" if path == "/authorize" else b"INNER:" + path.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _secret_file(tmp_path: Path) -> Path:
    p = tmp_path / "oauth-owner.json"
    p.write_text(
        json.dumps({"username": _USER, "password_hash": hash_admin_password(_PASS)}),
        encoding="utf-8",
    )
    return p


def _build_gate(tmp_path: Path, *, max_attempts: int = 5) -> OAuthOwnerGate:
    return OAuthOwnerGate(
        _inner,
        authenticator=AdminAuthenticator(_secret_file(tmp_path)),
        sessions=SessionStore(idle_seconds=900, absolute_seconds=3600),
        login_limiter=LoginRateLimiter(
            max_attempts=max_attempts, window_seconds=300
        ),
        approvals=ConsentApprovalStore(),
        templates=build_owner_templates(),
        cookie_secure=False,  # the test client speaks http
        cookie_max_age=900,
    )


def _client(gate: OAuthOwnerGate) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gate),
        base_url="http://testserver",
        follow_redirects=False,
    )


def _hidden(body: str, name: str) -> str:
    m = re.search(rf'name="{name}" value="([^"]*)"', body)
    assert m, f"hidden field {name} not found"
    return html.unescape(m.group(1))


async def _login(client: httpx.AsyncClient, *, password: str = _PASS) -> httpx.Response:
    return await client.post(
        "/oauth/login",
        data={"username": _USER, "password": password, "next": _AUTHZ},
    )


# ---------------------------------------------------------------------------
class TestPassthrough:
    async def test_non_gated_paths_pass_through(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            assert (await c.get("/mcp")).text == "INNER:/mcp"
            assert (await c.get("/.well-known/x")).text == "INNER:/.well-known/x"


class TestLogin:
    async def test_unauthenticated_authorize_shows_login(
        self, tmp_path: Path
    ) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            r = await c.get(_AUTHZ)
        assert r.status_code == 200
        assert "Sign in" in r.text
        assert r.text != "DELEGATED"

    async def test_bad_credentials_rejected(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            r = await _login(c, password="wrong")
        assert r.status_code == 401
        assert "Login failed" in r.text

    async def test_login_is_rate_limited(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path, max_attempts=2)) as c:
            assert (await _login(c, password="wrong")).status_code == 401
            assert (await _login(c, password="wrong")).status_code == 401
            assert (await _login(c, password="wrong")).status_code == 429

    async def test_good_login_sets_session_cookie(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            r = await _login(c)
        assert r.status_code == 303
        assert "bramble_oauth_owner" in r.headers.get("set-cookie", "")


class TestConsentFlow:
    async def test_session_alone_does_not_issue_code(self, tmp_path: Path) -> None:
        # The core property: being logged in is NOT consent. /authorize must
        # show the consent page, not delegate.
        async with _client(_build_gate(tmp_path)) as c:
            await _login(c)
            r = await c.get(_AUTHZ)
        assert r.status_code == 200
        assert r.text != "DELEGATED"
        assert "Authorize this connector" in r.text

    async def test_full_flow_login_consent_delegates(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            await _login(c)
            consent = await c.get(_AUTHZ)
            csrf = _hidden(consent.text, "csrf_token")
            authorize_query = _hidden(consent.text, "authorize_query")

            approve = await c.post(
                "/oauth/consent",
                data={
                    "csrf_token": csrf,
                    "authorize_query": authorize_query,
                    "decision": "approve",
                },
            )
            assert approve.status_code == 303

            delegated = await c.get(_AUTHZ)
        assert delegated.text == "DELEGATED"

    async def test_consent_without_csrf_is_forbidden(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            await _login(c)
            consent = await c.get(_AUTHZ)
            authorize_query = _hidden(consent.text, "authorize_query")
            r = await c.post(
                "/oauth/consent",
                data={
                    "csrf_token": "forged",
                    "authorize_query": authorize_query,
                    "decision": "approve",
                },
            )
        assert r.status_code == 403
        # And /authorize still does not delegate.
        async with _client(_build_gate(tmp_path)) as c2:
            await _login(c2)
            assert (await c2.get(_AUTHZ)).text != "DELEGATED"

    async def test_consent_deny_does_not_delegate(self, tmp_path: Path) -> None:
        async with _client(_build_gate(tmp_path)) as c:
            await _login(c)
            consent = await c.get(_AUTHZ)
            csrf = _hidden(consent.text, "csrf_token")
            authorize_query = _hidden(consent.text, "authorize_query")
            denied = await c.post(
                "/oauth/consent",
                data={
                    "csrf_token": csrf,
                    "authorize_query": authorize_query,
                    "decision": "deny",
                },
            )
            assert denied.status_code == 200
            assert "denied" in denied.text.lower()
            assert (await c.get(_AUTHZ)).text != "DELEGATED"

    async def test_approval_is_bound_to_request_fingerprint(
        self, tmp_path: Path
    ) -> None:
        # Approving one request must not authorize a different client.
        async with _client(_build_gate(tmp_path)) as c:
            await _login(c)
            consent = await c.get(_AUTHZ)
            csrf = _hidden(consent.text, "csrf_token")
            authorize_query = _hidden(consent.text, "authorize_query")
            await c.post(
                "/oauth/consent",
                data={
                    "csrf_token": csrf,
                    "authorize_query": authorize_query,
                    "decision": "approve",
                },
            )
            # A DIFFERENT client/redirect must still hit consent, not delegate.
            other = await c.get(
                "/authorize?client_id=evil&redirect_uri=https://evil.test/cb"
                "&scope=journal:read&code_challenge=abc"
            )
        assert other.text != "DELEGATED"
