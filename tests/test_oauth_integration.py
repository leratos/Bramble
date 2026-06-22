"""Integration tests for the Phase-6 OAuth stack wired into the server.

These exercise the seams between the pieces built in 6.2/6.3/6.4:

* ``MultiAuth`` coexistence – a single ``verify_token`` accepts both an
  OAuth-issued access token and a legacy static bearer token, and rejects
  anything else (the "static path must not break" guarantee at the
  verification layer).
* The discovery surface FastMCP mounts for ``FastMCP(auth=...)`` – both
  well-known documents and the ``401 + WWW-Authenticate`` on ``/mcp`` that
  drives Claude's OAuth flow.

The end-to-end transport seam (ASGI auth sets the principal that the tool
middleware reads) is validated by the live Claude connect in the DoD; here
the principal layer is verified through ``MultiAuth.verify_token`` and the
middleware logic is unit-tested in ``test_journal_mcp_server``.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp.server.auth.auth import MultiAuth
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from bramble.admin_auth import hash_admin_password
from bramble.auth_validator import AuthValidator
from bramble.journal_db import JournalDB
from bramble.journal_mcp_server import JournalMCPServer
from bramble.oauth_config import OAuthConfig
from bramble.oauth_owner_gate import build_owner_gate
from bramble.oauth_provider import BrambleOAuthProvider
from bramble.oauth_store import OAuthStore
from bramble.rate_limiter import RateLimiter
from bramble.static_token_verifier import STATIC_CLIENT_PREFIX, StaticTokenVerifier

_BASE = "https://journal.last-strawberry.com"
_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class _Stack:
    def __init__(self, tmp_path: Path) -> None:
        tokens = tmp_path / "tokens.json"
        tokens.write_text(json.dumps({"bramble": "tok-bramble"}), encoding="utf-8")
        self.validator = AuthValidator(tokens)
        self.store = OAuthStore(tmp_path / "oauth.db")
        self.store.initialize()
        self.config = OAuthConfig(public_base_url=_BASE)
        self.provider = BrambleOAuthProvider(store=self.store, config=self.config)
        self.multi = MultiAuth(
            server=self.provider,
            verifiers=[StaticTokenVerifier(self.validator)],
            base_url=_BASE,
        )

    async def mint_access_token(self, client_id: str = "claude-dcr") -> str:
        client = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[_REDIRECT],
            scope="journal:read",
        )
        await self.provider.register_client(client)
        params = AuthorizationParams(
            state="s",
            scopes=["journal:read"],
            code_challenge="challenge",
            redirect_uri=_REDIRECT,
            redirect_uri_provided_explicitly=True,
            resource=None,
        )
        redirect = await self.provider.authorize(client, params)
        code = parse_qs(urlparse(redirect).query)["code"][0]
        code_obj = await self.provider.load_authorization_code(client, code)
        token = await self.provider.exchange_authorization_code(client, code_obj)
        return token.access_token


@pytest.fixture
def stack(tmp_path: Path) -> _Stack:
    return _Stack(tmp_path)


# ---------------------------------------------------------------------------
# MultiAuth coexistence at the verification layer
# ---------------------------------------------------------------------------
class TestMultiAuthCoexistence:
    async def test_oauth_token_is_accepted(self, stack: _Stack) -> None:
        access_token = await stack.mint_access_token()
        principal = await stack.multi.verify_token(access_token)
        assert principal is not None
        assert principal.client_id == "claude-dcr"
        assert "journal:read" in principal.scopes
        # OAuth path is read-only.
        assert "journal:write" not in principal.scopes

    async def test_static_token_is_accepted(self, stack: _Stack) -> None:
        principal = await stack.multi.verify_token("tok-bramble")
        assert principal is not None
        assert principal.client_id == f"{STATIC_CLIENT_PREFIX}bramble"
        assert "journal:write" in principal.scopes  # static stays read-write

    async def test_garbage_token_is_rejected(self, stack: _Stack) -> None:
        assert await stack.multi.verify_token("not-a-real-token") is None

    async def test_revoked_oauth_token_is_rejected(self, stack: _Stack) -> None:
        access_token = await stack.mint_access_token()
        principal = await stack.multi.verify_token(access_token)
        assert principal is not None
        await stack.provider.revoke_token(principal)
        assert await stack.multi.verify_token(access_token) is None


# ---------------------------------------------------------------------------
# Discovery surface mounted by FastMCP(auth=...)
# ---------------------------------------------------------------------------
@pytest.fixture
def http_app(tmp_path: Path, db: JournalDB):
    stack = _Stack(tmp_path)
    server = JournalMCPServer(
        db,
        auth_provider=stack.multi,
        rate_limiter=RateLimiter(per_token_rpm=60, per_ip_rpm=120),
    )
    return server.app.http_app()


async def _get(http_app, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=http_app)
    async with (
        http_app.router.lifespan_context(http_app),
        httpx.AsyncClient(transport=transport, base_url=_BASE) as client,
    ):
        return await client.get(path, **kwargs)


class TestDiscoveryEndpoints:
    async def test_authorization_server_metadata(self, http_app) -> None:
        resp = await _get(http_app, "/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        body = resp.json()
        assert body["authorization_endpoint"] == f"{_BASE}/authorize"
        assert body["token_endpoint"] == f"{_BASE}/token"
        assert body["registration_endpoint"] == f"{_BASE}/register"
        assert body["response_types_supported"] == ["code"]
        assert body["code_challenge_methods_supported"] == ["S256"]

    async def test_protected_resource_metadata(self, http_app) -> None:
        resp = await _get(http_app, "/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resource"] == f"{_BASE}/mcp"
        assert _BASE + "/" in [s.rstrip() for s in body["authorization_servers"]] or (
            f"{_BASE}/" in body["authorization_servers"]
        )

    async def test_mcp_requires_auth_and_points_at_metadata(self, http_app) -> None:
        resp = await _get(
            http_app, "/mcp", headers={"Accept": "text/event-stream"}
        )
        assert resp.status_code == 401
        www_auth = resp.headers.get("www-authenticate", "")
        assert "resource_metadata=" in www_auth
        assert "/.well-known/oauth-protected-resource/mcp" in www_auth


# ---------------------------------------------------------------------------
# Owner gate composed onto the full FastMCP app (Phase 6.6)
# ---------------------------------------------------------------------------
@pytest.fixture
def gated_http_app(tmp_path: Path, db: JournalDB):
    stack = _Stack(tmp_path)
    owner_secret = tmp_path / "oauth-owner.json"
    owner_secret.write_text(
        json.dumps({"username": "owner", "password_hash": hash_admin_password("pw")}),
        encoding="utf-8",
    )
    gate = build_owner_gate(
        OAuthConfig(
            public_base_url=_BASE,
            owner_secret_file=owner_secret,
            owner_cookie_secure=False,
        )
    )
    server = JournalMCPServer(
        db,
        auth_provider=stack.multi,
        rate_limiter=RateLimiter(per_token_rpm=60, per_ip_rpm=120),
    )
    return server.app.http_app(middleware=[gate])


class TestOwnerGateComposition:
    async def test_authorize_is_gated_by_owner_login(self, gated_http_app) -> None:
        resp = await _get(
            gated_http_app,
            "/authorize?client_id=x&redirect_uri=https://claude.ai/cb"
            "&scope=journal:read&code_challenge=abc",
        )
        assert resp.status_code == 200
        assert "Sign in" in resp.text  # the login page, not a framework response

    async def test_mcp_still_requires_auth_under_the_gate(self, gated_http_app) -> None:
        resp = await _get(
            gated_http_app, "/mcp", headers={"Accept": "text/event-stream"}
        )
        assert resp.status_code == 401  # passed through to the framework auth

    async def test_well_known_passes_through_the_gate(self, gated_http_app) -> None:
        resp = await _get(
            gated_http_app, "/.well-known/oauth-authorization-server"
        )
        assert resp.status_code == 200
