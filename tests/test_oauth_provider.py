"""Unit + flow tests for :mod:`bramble.oauth_provider`."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull

from bramble.oauth_config import OAuthConfig
from bramble.oauth_provider import BrambleOAuthProvider
from bramble.oauth_store import OAuthStore

_BASE = "https://journal.last-strawberry.com"
_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_provider(
    tmp_path: Path,
    *,
    access_token_ttl: int = 3600,
    refresh_token_ttl: int | None = 2_592_000,
    auth_code_ttl: int = 300,
) -> tuple[BrambleOAuthProvider, OAuthStore, _Clock]:
    store = OAuthStore(tmp_path / "oauth.db")
    store.initialize()
    config = OAuthConfig(
        public_base_url=_BASE,
        access_token_ttl=access_token_ttl,
        refresh_token_ttl=refresh_token_ttl,
        auth_code_ttl=auth_code_ttl,
    )
    clock = _Clock()
    provider = BrambleOAuthProvider(store=store, config=config, time_source=clock)
    return provider, store, clock


def _client(client_id: str = "client-1") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="secret",
        redirect_uris=[_REDIRECT],
        scope="journal:read",
    )


def _params() -> AuthorizationParams:
    return AuthorizationParams(
        state="state-123",
        scopes=["journal:read"],
        code_challenge="challenge-abc",
        redirect_uri=_REDIRECT,
        redirect_uri_provided_explicitly=True,
        resource=None,
    )


async def _register_and_authorize(
    provider: BrambleOAuthProvider, client: OAuthClientInformationFull
) -> str:
    await provider.register_client(client)
    redirect = await provider.authorize(client, _params())
    code = parse_qs(urlparse(redirect).query)["code"][0]
    return code


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_rejects_bad_store(self, tmp_path: Path) -> None:
        cfg = OAuthConfig(public_base_url=_BASE)
        with pytest.raises(TypeError):
            BrambleOAuthProvider(store=object(), config=cfg)  # type: ignore[arg-type]

    def test_rejects_bad_config(self, tmp_path: Path) -> None:
        store = OAuthStore(tmp_path / "oauth.db")
        store.initialize()
        with pytest.raises(TypeError):
            BrambleOAuthProvider(store=store, config=object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------
class TestRegistration:
    async def test_register_then_get(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        await provider.register_client(_client())
        got = await provider.get_client("client-1")
        assert got is not None
        assert got.client_secret == "secret"

    async def test_get_unknown_returns_none(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        assert await provider.get_client("nope") is None

    async def test_invalid_scope_rejected(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = OAuthClientInformationFull(
            client_id="c2",
            redirect_uris=[_REDIRECT],
            scope="journal:read journal:admin",
        )
        with pytest.raises(ValueError, match="not valid"):
            await provider.register_client(client)

    async def test_missing_client_id_rejected(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = OAuthClientInformationFull(redirect_uris=[_REDIRECT])
        with pytest.raises(ValueError, match="client_id"):
            await provider.register_client(client)


# ---------------------------------------------------------------------------
# Authorize
# ---------------------------------------------------------------------------
class TestAuthorize:
    async def test_authorize_returns_redirect_with_code_and_state(
        self, tmp_path: Path
    ) -> None:
        provider, store, _ = _make_provider(tmp_path)
        client = _client()
        await provider.register_client(client)
        redirect = await provider.authorize(client, _params())
        q = parse_qs(urlparse(redirect).query)
        assert "code" in q
        assert q["state"] == ["state-123"]
        # The code is persisted with its PKCE challenge and scope.
        stored = store.get_auth_code(q["code"][0])
        assert stored is not None
        assert stored.code_challenge == "challenge-abc"
        assert stored.scopes == ["journal:read"]

    async def test_authorize_unregistered_client_raises(
        self, tmp_path: Path
    ) -> None:
        from mcp.server.auth.provider import AuthorizeError

        provider, _, _ = _make_provider(tmp_path)
        with pytest.raises(AuthorizeError):
            await provider.authorize(_client("ghost"), _params())

    async def test_authorize_filters_unregistered_scopes(
        self, tmp_path: Path
    ) -> None:
        provider, store, _ = _make_provider(tmp_path)
        client = _client()
        await provider.register_client(client)
        params = AuthorizationParams(
            state="s",
            scopes=["journal:read", "journal:write"],  # write not registered
            code_challenge="c",
            redirect_uri=_REDIRECT,
            redirect_uri_provided_explicitly=True,
            resource=None,
        )
        redirect = await provider.authorize(client, params)
        code = parse_qs(urlparse(redirect).query)["code"][0]
        stored = store.get_auth_code(code)
        assert stored is not None
        assert stored.scopes == ["journal:read"]

    async def test_omitted_scope_defaults_to_client_scope(
        self, tmp_path: Path
    ) -> None:
        # A request without a scope parameter must still yield a usable token,
        # not an empty-scope one the resource would reject.
        provider, store, _ = _make_provider(tmp_path)
        client = _client()
        await provider.register_client(client)
        params = AuthorizationParams(
            state="s",
            scopes=None,
            code_challenge="c",
            redirect_uri=_REDIRECT,
            redirect_uri_provided_explicitly=True,
            resource=None,
        )
        redirect = await provider.authorize(client, params)
        code = parse_qs(urlparse(redirect).query)["code"][0]
        stored = store.get_auth_code(code)
        assert stored is not None
        assert stored.scopes == ["journal:read"]


# ---------------------------------------------------------------------------
# Full authorization-code flow
# ---------------------------------------------------------------------------
class TestAuthCodeExchange:
    async def test_happy_path(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        code = await _register_and_authorize(provider, client)

        code_obj = await provider.load_authorization_code(client, code)
        assert code_obj is not None

        token = await provider.exchange_authorization_code(client, code_obj)
        assert token.access_token
        assert token.refresh_token
        assert token.expires_in == 3600
        assert token.scope == "journal:read"

        access = await provider.verify_token(token.access_token)
        assert access is not None
        assert access.client_id == "client-1"
        assert access.scopes == ["journal:read"]

    async def test_code_is_single_use(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        code = await _register_and_authorize(provider, client)
        code_obj = await provider.load_authorization_code(client, code)
        assert code_obj is not None

        await provider.exchange_authorization_code(client, code_obj)
        # Second exchange of the same code must fail (consumed).
        with pytest.raises(TokenError):
            await provider.exchange_authorization_code(client, code_obj)

    async def test_load_code_wrong_client_returns_none(
        self, tmp_path: Path
    ) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        code = await _register_and_authorize(provider, client)
        other = _client("other")
        await provider.register_client(other)
        assert await provider.load_authorization_code(other, code) is None

    async def test_expired_code_returns_none_and_is_deleted(
        self, tmp_path: Path
    ) -> None:
        provider, store, clock = _make_provider(tmp_path, auth_code_ttl=300)
        client = _client()
        code = await _register_and_authorize(provider, client)
        clock.advance(301)
        assert await provider.load_authorization_code(client, code) is None
        assert store.get_auth_code(code) is None


# ---------------------------------------------------------------------------
# Access-token expiry (deliberate deviation: refresh survives)
# ---------------------------------------------------------------------------
class TestAccessExpiry:
    async def test_access_expires_but_refresh_survives(
        self, tmp_path: Path
    ) -> None:
        provider, _, clock = _make_provider(
            tmp_path, access_token_ttl=3600, refresh_token_ttl=2_592_000
        )
        client = _client()
        code = await _register_and_authorize(provider, client)
        code_obj = await provider.load_authorization_code(client, code)
        assert code_obj is not None
        token = await provider.exchange_authorization_code(client, code_obj)

        clock.advance(3601)  # past the access TTL, well within the refresh TTL
        assert await provider.verify_token(token.access_token) is None
        # The refresh token must still be usable so the client can re-auth.
        refresh_obj = await provider.load_refresh_token(client, token.refresh_token)
        assert refresh_obj is not None


# ---------------------------------------------------------------------------
# Refresh-token flow
# ---------------------------------------------------------------------------
class TestRefreshFlow:
    async def _issue(
        self, provider: BrambleOAuthProvider, client: OAuthClientInformationFull
    ):
        code = await _register_and_authorize(provider, client)
        code_obj = await provider.load_authorization_code(client, code)
        assert code_obj is not None
        return await provider.exchange_authorization_code(client, code_obj)

    async def test_refresh_rotates_and_invalidates_old(
        self, tmp_path: Path
    ) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        token = await self._issue(provider, client)

        refresh_obj = await provider.load_refresh_token(client, token.refresh_token)
        assert refresh_obj is not None
        new = await provider.exchange_refresh_token(
            client, refresh_obj, ["journal:read"]
        )
        assert new.access_token != token.access_token
        assert new.refresh_token != token.refresh_token

        # Old pair is dead; new access verifies.
        assert await provider.load_refresh_token(client, token.refresh_token) is None
        assert await provider.verify_token(token.access_token) is None
        assert await provider.verify_token(new.access_token) is not None

    async def test_refresh_scope_escalation_rejected(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        token = await self._issue(provider, client)
        refresh_obj = await provider.load_refresh_token(client, token.refresh_token)
        assert refresh_obj is not None
        with pytest.raises(TokenError):
            await provider.exchange_refresh_token(
                client, refresh_obj, ["journal:read", "journal:write"]
            )

    async def test_refresh_no_expiry_when_ttl_none(self, tmp_path: Path) -> None:
        provider, _, clock = _make_provider(tmp_path, refresh_token_ttl=None)
        client = _client()
        token = await self._issue(provider, client)
        clock.advance(10_000_000)  # far future
        assert await provider.load_refresh_token(client, token.refresh_token) is not None

    async def test_refresh_expires_when_ttl_set(self, tmp_path: Path) -> None:
        provider, _, clock = _make_provider(tmp_path, refresh_token_ttl=1000)
        client = _client()
        token = await self._issue(provider, client)
        clock.advance(1001)
        assert await provider.load_refresh_token(client, token.refresh_token) is None

    async def test_refresh_is_single_use(self, tmp_path: Path) -> None:
        # Re-using a consumed refresh token must fail rather than mint a second
        # pair (concurrent-rotation guard via the atomic consume).
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        token = await self._issue(provider, client)
        refresh_obj = await provider.load_refresh_token(client, token.refresh_token)
        assert refresh_obj is not None
        await provider.exchange_refresh_token(client, refresh_obj, ["journal:read"])
        with pytest.raises(TokenError):
            await provider.exchange_refresh_token(client, refresh_obj, ["journal:read"])


# ---------------------------------------------------------------------------
# Revocation (explicit, cascades)
# ---------------------------------------------------------------------------
class TestRevocation:
    async def _issue(self, provider, client):
        code = await _register_and_authorize(provider, client)
        code_obj = await provider.load_authorization_code(client, code)
        return await provider.exchange_authorization_code(client, code_obj)

    async def test_revoke_access_cascades_to_refresh(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        token = await self._issue(provider, client)
        access = await provider.verify_token(token.access_token)
        assert access is not None

        await provider.revoke_token(access)
        assert await provider.verify_token(token.access_token) is None
        assert await provider.load_refresh_token(client, token.refresh_token) is None

    async def test_revoke_refresh_cascades_to_access(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        client = _client()
        token = await self._issue(provider, client)
        refresh = await provider.load_refresh_token(client, token.refresh_token)
        assert refresh is not None

        await provider.revoke_token(refresh)
        assert await provider.load_refresh_token(client, token.refresh_token) is None
        assert await provider.verify_token(token.access_token) is None

    async def test_revoke_unknown_token_is_noop(self, tmp_path: Path) -> None:
        provider, _, _ = _make_provider(tmp_path)
        ghost = AccessToken(
            token="never-issued", client_id="x", scopes=["journal:read"]
        )
        await provider.revoke_token(ghost)  # must not raise
        # A bare RefreshToken counterpart likewise.
        ghost_r = RefreshToken(
            token="never-issued-r", client_id="x", scopes=["journal:read"]
        )
        await provider.revoke_token(ghost_r)
