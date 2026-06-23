"""Unit tests for :mod:`bramble.oauth_config`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from bramble.oauth_config import (
    ENV_OAUTH_ACCESS_TOKEN_TTL,
    ENV_OAUTH_ALLOW_WRITE,
    ENV_OAUTH_AUTH_CODE_TTL,
    ENV_OAUTH_DB_PATH,
    ENV_OAUTH_ENABLE_DCR,
    ENV_OAUTH_OWNER_COOKIE_SECURE,
    ENV_OAUTH_OWNER_SECRET_FILE,
    ENV_OAUTH_OWNER_SESSION_IDLE_SECONDS,
    ENV_OAUTH_PUBLIC_BASE_URL,
    ENV_OAUTH_REFRESH_TOKEN_TTL,
    ENV_OAUTH_SCOPES,
    ENV_OAUTH_STATIC_CLIENT_ID,
    ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS,
    ENV_OAUTH_STATIC_CLIENT_SECRET,
    OAuthConfig,
)

_BASE = "https://journal.last-strawberry.com"


# ---------------------------------------------------------------------------
# Direct construction & defaults
# ---------------------------------------------------------------------------
class TestConstructionDefaults:
    def test_minimal_construct_has_sane_defaults(self) -> None:
        cfg = OAuthConfig(public_base_url=_BASE)
        assert cfg.public_base_url == _BASE
        assert cfg.oauth_db_path == Path("./data/oauth.db")
        assert cfg.scopes == ("journal:read",)
        assert cfg.enable_dcr is True
        assert cfg.access_token_ttl == 3600
        assert cfg.refresh_token_ttl == 2_592_000
        assert cfg.auth_code_ttl == 300
        assert cfg.has_static_client is False

    def test_resource_url_appends_mcp(self) -> None:
        cfg = OAuthConfig(public_base_url=_BASE)
        assert cfg.resource_url == f"{_BASE}/mcp"

    def test_is_frozen(self) -> None:
        cfg = OAuthConfig(public_base_url=_BASE)
        with pytest.raises(FrozenInstanceError):
            cfg.public_base_url = "https://evil.example"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# public_base_url validation
# ---------------------------------------------------------------------------
class TestPublicBaseUrl:
    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            OAuthConfig(public_base_url=123)  # type: ignore[arg-type]

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            OAuthConfig(public_base_url="   ")

    def test_rejects_trailing_slash(self) -> None:
        with pytest.raises(ValueError, match="trailing slash"):
            OAuthConfig(public_base_url=f"{_BASE}/")

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ValueError, match="http"):
            OAuthConfig(public_base_url="ftp://journal.example")

    def test_rejects_missing_host(self) -> None:
        with pytest.raises(ValueError):
            OAuthConfig(public_base_url="https://")

    def test_rejects_plain_http_for_public_host(self) -> None:
        with pytest.raises(ValueError, match="https"):
            OAuthConfig(public_base_url="http://journal.last-strawberry.com")

    def test_allows_plain_http_localhost(self) -> None:
        cfg = OAuthConfig(public_base_url="http://localhost:8765")
        assert cfg.public_base_url == "http://localhost:8765"

    def test_allows_plain_http_loopback_ip(self) -> None:
        cfg = OAuthConfig(public_base_url="http://127.0.0.1:8765")
        assert cfg.public_base_url == "http://127.0.0.1:8765"


# ---------------------------------------------------------------------------
# scopes / ttls validation
# ---------------------------------------------------------------------------
class TestScopesAndTtls:
    def test_scopes_must_be_tuple(self) -> None:
        with pytest.raises(TypeError):
            OAuthConfig(public_base_url=_BASE, scopes=["journal:read"])  # type: ignore[arg-type]

    def test_scopes_must_not_be_empty(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            OAuthConfig(public_base_url=_BASE, scopes=())

    def test_scope_with_whitespace_rejected(self) -> None:
        with pytest.raises(ValueError, match="whitespace"):
            OAuthConfig(public_base_url=_BASE, scopes=("journal read",))

    @pytest.mark.parametrize("field", ["access_token_ttl", "auth_code_ttl"])
    @pytest.mark.parametrize("bad", [0, -1])
    def test_ttl_must_be_positive(self, field: str, bad: int) -> None:
        with pytest.raises(ValueError):
            OAuthConfig(public_base_url=_BASE, **{field: bad})

    @pytest.mark.parametrize("field", ["access_token_ttl", "auth_code_ttl"])
    def test_ttl_rejects_bool(self, field: str) -> None:
        with pytest.raises(TypeError):
            OAuthConfig(public_base_url=_BASE, **{field: True})

    def test_refresh_ttl_accepts_none(self) -> None:
        cfg = OAuthConfig(public_base_url=_BASE, refresh_token_ttl=None)
        assert cfg.refresh_token_ttl is None

    def test_refresh_ttl_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError):
            OAuthConfig(public_base_url=_BASE, refresh_token_ttl=0)


# ---------------------------------------------------------------------------
# static client (all-or-nothing) validation
# ---------------------------------------------------------------------------
class TestStaticClient:
    def test_full_static_client_ok(self) -> None:
        cfg = OAuthConfig(
            public_base_url=_BASE,
            static_client_id="bramble-static",
            static_client_secret="s3cr3t",
            static_client_redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
        )
        assert cfg.has_static_client is True

    def test_secret_without_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="without static_client_id"):
            OAuthConfig(public_base_url=_BASE, static_client_secret="s3cr3t")

    def test_id_without_secret_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires static_client_secret"):
            OAuthConfig(
                public_base_url=_BASE,
                static_client_id="bramble-static",
                static_client_redirect_uris=("https://claude.ai/cb",),
            )

    def test_id_without_redirect_uris_rejected(self) -> None:
        with pytest.raises(ValueError, match="redirect URI"):
            OAuthConfig(
                public_base_url=_BASE,
                static_client_id="bramble-static",
                static_client_secret="s3cr3t",
            )

    def test_plain_http_redirect_for_public_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="https"):
            OAuthConfig(
                public_base_url=_BASE,
                static_client_id="bramble-static",
                static_client_secret="s3cr3t",
                static_client_redirect_uris=("http://claude.ai/cb",),
            )

    def test_localhost_http_redirect_allowed(self) -> None:
        cfg = OAuthConfig(
            public_base_url=_BASE,
            static_client_id="bramble-static",
            static_client_secret="s3cr3t",
            static_client_redirect_uris=("http://localhost:1234/cb",),
        )
        assert cfg.has_static_client is True


# ---------------------------------------------------------------------------
# from_env resolution
# ---------------------------------------------------------------------------
class TestFromEnv:
    def test_requires_public_base_url(self) -> None:
        with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
            OAuthConfig.from_env(env={})

    def test_defaults_when_only_base_url(self) -> None:
        cfg = OAuthConfig.from_env(env={ENV_OAUTH_PUBLIC_BASE_URL: _BASE})
        assert cfg.public_base_url == _BASE
        assert cfg.oauth_db_path == Path("./data/oauth.db")
        assert cfg.scopes == ("journal:read",)
        assert cfg.enable_dcr is True
        assert cfg.refresh_token_ttl == 2_592_000

    def test_full_env_resolution(self) -> None:
        env = {
            ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
            ENV_OAUTH_DB_PATH: "/opt/bramble/data/oauth.db",
            ENV_OAUTH_SCOPES: "journal:read, journal:write",
            ENV_OAUTH_ENABLE_DCR: "false",
            ENV_OAUTH_ACCESS_TOKEN_TTL: "900",
            ENV_OAUTH_REFRESH_TOKEN_TTL: "none",
            ENV_OAUTH_AUTH_CODE_TTL: "120",
            ENV_OAUTH_STATIC_CLIENT_ID: "bramble-static",
            ENV_OAUTH_STATIC_CLIENT_SECRET: "s3cr3t",
            ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS: (
                "https://claude.ai/cb https://claude.ai/cb2"
            ),
        }
        cfg = OAuthConfig.from_env(env=env)
        assert cfg.oauth_db_path == Path("/opt/bramble/data/oauth.db")
        assert cfg.scopes == ("journal:read", "journal:write")
        assert cfg.enable_dcr is False
        assert cfg.access_token_ttl == 900
        assert cfg.refresh_token_ttl is None
        assert cfg.auth_code_ttl == 120
        assert cfg.static_client_id == "bramble-static"
        assert cfg.static_client_secret == "s3cr3t"
        assert cfg.static_client_redirect_uris == (
            "https://claude.ai/cb",
            "https://claude.ai/cb2",
        )

    def test_invalid_bool_env_raises(self) -> None:
        with pytest.raises(ValueError, match="enable_dcr"):
            OAuthConfig.from_env(
                env={
                    ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
                    ENV_OAUTH_ENABLE_DCR: "perhaps",
                }
            )

    def test_invalid_int_env_raises(self) -> None:
        with pytest.raises(ValueError, match="access_token_ttl"):
            OAuthConfig.from_env(
                env={
                    ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
                    ENV_OAUTH_ACCESS_TOKEN_TTL: "lots",
                }
            )

    def test_empty_static_secret_treated_as_unset(self) -> None:
        # An empty env value must not partially configure a static client.
        cfg = OAuthConfig.from_env(
            env={
                ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
                ENV_OAUTH_STATIC_CLIENT_ID: "",
                ENV_OAUTH_STATIC_CLIENT_SECRET: "",
            }
        )
        assert cfg.has_static_client is False

    def test_zero_refresh_ttl_is_rejected_not_silently_infinite(self) -> None:
        # REFRESH_TOKEN_TTL=0 must fail validation, never mean "no expiry".
        with pytest.raises(ValueError, match="refresh_token_ttl"):
            OAuthConfig.from_env(
                env={
                    ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
                    ENV_OAUTH_REFRESH_TOKEN_TTL: "0",
                }
            )

    def test_none_refresh_ttl_still_means_no_expiry(self) -> None:
        cfg = OAuthConfig.from_env(
            env={
                ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
                ENV_OAUTH_REFRESH_TOKEN_TTL: "none",
            }
        )
        assert cfg.refresh_token_ttl is None


# ---------------------------------------------------------------------------
# Owner-gate (Phase 6.6) config
# ---------------------------------------------------------------------------
class TestOwnerGate:
    def test_defaults(self) -> None:
        cfg = OAuthConfig(public_base_url=_BASE)
        assert cfg.owner_secret_file == Path("./secrets/oauth-owner.json")
        assert cfg.owner_session_idle_seconds == 900
        assert cfg.owner_session_absolute_seconds == 28_800
        assert cfg.owner_login_max_attempts == 5
        assert cfg.owner_cookie_secure is True
        assert cfg.allow_oauth_write is False  # read-only by default

    def test_allow_write_must_be_bool(self) -> None:
        with pytest.raises(TypeError):
            OAuthConfig(public_base_url=_BASE, allow_oauth_write="yes")  # type: ignore[arg-type]

    def test_allow_write_from_env(self) -> None:
        cfg = OAuthConfig.from_env(
            env={ENV_OAUTH_PUBLIC_BASE_URL: _BASE, ENV_OAUTH_ALLOW_WRITE: "true"}
        )
        assert cfg.allow_oauth_write is True

    def test_absolute_must_be_at_least_idle(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            OAuthConfig(
                public_base_url=_BASE,
                owner_session_idle_seconds=1000,
                owner_session_absolute_seconds=500,
            )

    def test_cookie_secure_must_be_bool(self) -> None:
        with pytest.raises(TypeError):
            OAuthConfig(public_base_url=_BASE, owner_cookie_secure="yes")  # type: ignore[arg-type]

    def test_login_attempts_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            OAuthConfig(public_base_url=_BASE, owner_login_max_attempts=0)

    def test_from_env_resolution(self) -> None:
        cfg = OAuthConfig.from_env(
            env={
                ENV_OAUTH_PUBLIC_BASE_URL: _BASE,
                ENV_OAUTH_OWNER_SECRET_FILE: "/opt/bramble/secrets/oauth-owner.json",
                ENV_OAUTH_OWNER_SESSION_IDLE_SECONDS: "600",
                ENV_OAUTH_OWNER_COOKIE_SECURE: "false",
            }
        )
        assert cfg.owner_secret_file == Path("/opt/bramble/secrets/oauth-owner.json")
        assert cfg.owner_session_idle_seconds == 600
        assert cfg.owner_cookie_secure is False
