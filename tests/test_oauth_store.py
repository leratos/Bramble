"""Unit tests for :mod:`bramble.oauth_store`."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull

from bramble.oauth_store import OAuthStore


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path: Path) -> OAuthStore:
    s = OAuthStore(tmp_path / "oauth.db")
    s.initialize()
    return s


def _client(client_id: str = "client-1") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="super-secret",
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        scope="journal:read",
        client_name="Claude",
    )


def _auth_code(
    code: str = "code-1", *, client_id: str = "client-1", expires_at: float = 2000.0
) -> AuthorizationCode:
    return AuthorizationCode(
        code=code,
        scopes=["journal:read"],
        expires_at=expires_at,
        client_id=client_id,
        code_challenge="challenge-xyz",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
    )


def _access(
    token: str = "at-1", *, client_id: str = "client-1", expires_at: int | None = None
) -> AccessToken:
    return AccessToken(
        token=token,
        client_id=client_id,
        scopes=["journal:read"],
        expires_at=expires_at,
    )


def _refresh(
    token: str = "rt-1", *, client_id: str = "client-1", expires_at: int | None = None
) -> RefreshToken:
    return RefreshToken(
        token=token,
        client_id=client_id,
        scopes=["journal:read"],
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_initialize_is_idempotent(self, tmp_path: Path) -> None:
        s = OAuthStore(tmp_path / "oauth.db")
        s.initialize()
        s.initialize()  # must not raise
        assert (tmp_path / "oauth.db").exists()

    def test_initialize_creates_parent_dirs(self, tmp_path: Path) -> None:
        s = OAuthStore(tmp_path / "nested" / "dir" / "oauth.db")
        s.initialize()
        assert s.db_path.exists()

    def test_db_path_accepts_str(self, tmp_path: Path) -> None:
        s = OAuthStore(str(tmp_path / "oauth.db"))
        s.initialize()
        assert isinstance(s.db_path, Path)

    def test_db_path_rejects_bad_type(self) -> None:
        with pytest.raises(TypeError):
            OAuthStore(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
class TestClients:
    def test_save_and_get_round_trip(self, store: OAuthStore) -> None:
        client = _client()
        store.save_client(client)
        got = store.get_client("client-1")
        assert got == client
        assert got.client_secret == "super-secret"
        assert got.scope == "journal:read"

    def test_get_missing_returns_none(self, store: OAuthStore) -> None:
        assert store.get_client("nope") is None

    def test_save_is_upsert(self, store: OAuthStore) -> None:
        store.save_client(_client())
        updated = OAuthClientInformationFull(
            client_id="client-1",
            client_secret="rotated",
            redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
            scope="journal:read",
        )
        store.save_client(updated)
        got = store.get_client("client-1")
        assert got is not None
        assert got.client_secret == "rotated"

    def test_save_rejects_non_model(self, store: OAuthStore) -> None:
        with pytest.raises(TypeError):
            store.save_client({"client_id": "x"})  # type: ignore[arg-type]

    def test_save_rejects_client_without_id(self, store: OAuthStore) -> None:
        client = OAuthClientInformationFull(
            redirect_uris=["https://claude.ai/cb"],
        )
        with pytest.raises(ValueError, match="client_id"):
            store.save_client(client)


# ---------------------------------------------------------------------------
# Authorization codes
# ---------------------------------------------------------------------------
class TestAuthCodes:
    def test_save_get_delete(self, store: OAuthStore) -> None:
        store.save_auth_code(_auth_code())
        got = store.get_auth_code("code-1")
        assert got is not None
        assert got.code_challenge == "challenge-xyz"
        assert got.client_id == "client-1"

        assert store.delete_auth_code("code-1") is True
        assert store.get_auth_code("code-1") is None
        # Second delete is a no-op and reports it.
        assert store.delete_auth_code("code-1") is False

    def test_get_missing_returns_none(self, store: OAuthStore) -> None:
        assert store.get_auth_code("nope") is None


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------
class TestAccessTokens:
    def test_round_trip_no_expiry(self, store: OAuthStore) -> None:
        store.save_access_token(_access(expires_at=None))
        got = store.get_access_token("at-1")
        assert got is not None
        assert got.expires_at is None
        assert got.scopes == ["journal:read"]

    def test_round_trip_with_expiry(self, store: OAuthStore) -> None:
        store.save_access_token(_access(expires_at=9999))
        got = store.get_access_token("at-1")
        assert got is not None
        assert got.expires_at == 9999

    def test_delete(self, store: OAuthStore) -> None:
        store.save_access_token(_access())
        assert store.delete_access_token("at-1") is True
        assert store.get_access_token("at-1") is None


# ---------------------------------------------------------------------------
# Refresh tokens + pairing
# ---------------------------------------------------------------------------
class TestRefreshTokens:
    def test_round_trip(self, store: OAuthStore) -> None:
        store.save_refresh_token(_refresh())
        got = store.get_refresh_token("rt-1")
        assert got is not None
        assert got.client_id == "client-1"

    def test_pairing_both_directions(self, store: OAuthStore) -> None:
        store.save_refresh_token(_refresh(), access_token="at-1")
        assert store.get_paired_access_token("rt-1") == "at-1"
        assert store.get_refresh_for_access("at-1") == "rt-1"

    def test_pairing_absent_returns_none(self, store: OAuthStore) -> None:
        store.save_refresh_token(_refresh())  # no pairing
        assert store.get_paired_access_token("rt-1") is None
        assert store.get_refresh_for_access("at-1") is None

    def test_delete(self, store: OAuthStore) -> None:
        store.save_refresh_token(_refresh())
        assert store.delete_refresh_token("rt-1") is True
        assert store.get_refresh_token("rt-1") is None


# ---------------------------------------------------------------------------
# Expiry purge
# ---------------------------------------------------------------------------
class TestPurgeExpired:
    def test_purges_only_expired_keeps_future_and_null(
        self, store: OAuthStore
    ) -> None:
        now = 1000.0
        # Auth codes: one past, one future (expires_at is required).
        store.save_auth_code(_auth_code("code-past", expires_at=500.0))
        store.save_auth_code(_auth_code("code-future", expires_at=2000.0))
        # Access tokens: past, future, never-expiring (None).
        store.save_access_token(_access("at-past", expires_at=500))
        store.save_access_token(_access("at-future", expires_at=2000))
        store.save_access_token(_access("at-null", expires_at=None))
        # Refresh tokens: past + null.
        store.save_refresh_token(_refresh("rt-past", expires_at=500))
        store.save_refresh_token(_refresh("rt-null", expires_at=None))

        counts = store.purge_expired(now)

        assert counts == {
            "oauth_auth_codes": 1,
            "oauth_access_tokens": 1,
            "oauth_refresh_tokens": 1,
        }
        assert store.get_auth_code("code-past") is None
        assert store.get_auth_code("code-future") is not None
        assert store.get_access_token("at-past") is None
        assert store.get_access_token("at-future") is not None
        assert store.get_access_token("at-null") is not None
        assert store.get_refresh_token("rt-past") is None
        assert store.get_refresh_token("rt-null") is not None

    def test_boundary_at_equals_now_is_purged(self, store: OAuthStore) -> None:
        store.save_access_token(_access("at-boundary", expires_at=1000))
        store.purge_expired(1000.0)
        assert store.get_access_token("at-boundary") is None

    def test_rejects_bad_now(self, store: OAuthStore) -> None:
        with pytest.raises(TypeError):
            store.purge_expired(True)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            store.purge_expired("1000")  # type: ignore[arg-type]
