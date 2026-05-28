"""Tests for :mod:`bramble.admin_config`."""

from __future__ import annotations

from pathlib import Path

import pytest

from bramble.admin_config import (
    ENV_ADMIN_ALLOWED_HOSTS,
    ENV_ADMIN_COOKIE_SECURE,
    ENV_ADMIN_HOST,
    ENV_ADMIN_PORT,
    ENV_ADMIN_SECRET_FILE,
    AdminConfig,
)
from bramble.server_config import ENV_DB_PATH, ENV_TOKENS_FILE


class TestAdminConfigConstruction:
    def test_defaults_are_loopback_admin_values(self) -> None:
        cfg = AdminConfig(db_path=Path("./data/bramble.db"))

        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8770
        assert cfg.admin_secret_file == Path("./secrets/admin-ui.json")
        assert cfg.tokens_file == Path("./secrets/tokens.json")
        assert cfg.session_idle_seconds == 1800
        assert cfg.session_absolute_seconds == 28800
        assert cfg.login_max_attempts == 5
        assert cfg.allowed_hosts == ("127.0.0.1", "localhost")

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
    def test_rejects_non_loopback_host(self, host: str) -> None:
        with pytest.raises(ValueError, match="loopback"):
            AdminConfig(db_path=Path("./data/bramble.db"), host=host)

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_accepts_loopback_hosts(self, host: str) -> None:
        cfg = AdminConfig(db_path=Path("./data/bramble.db"), host=host)
        assert cfg.host == host

    def test_absolute_timeout_must_cover_idle_timeout(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            AdminConfig(
                db_path=Path("./data/bramble.db"),
                session_idle_seconds=60,
                session_absolute_seconds=30,
            )


class TestAdminConfigFromSources:
    def test_env_overrides_defaults(self) -> None:
        env = {
            ENV_DB_PATH: "/tmp/bramble.db",
            ENV_ADMIN_HOST: "localhost",
            ENV_ADMIN_PORT: "8771",
            ENV_ADMIN_SECRET_FILE: "/opt/bramble/secrets/admin-ui.json",
            ENV_TOKENS_FILE: "/opt/bramble/secrets/tokens.json",
            ENV_ADMIN_COOKIE_SECURE: "true",
            ENV_ADMIN_ALLOWED_HOSTS: "127.0.0.1,localhost,testserver",
        }

        cfg = AdminConfig.from_sources(argv=[], env=env)

        assert cfg.db_path == Path("/tmp/bramble.db")
        assert cfg.host == "localhost"
        assert cfg.port == 8771
        assert cfg.admin_secret_file == Path("/opt/bramble/secrets/admin-ui.json")
        assert cfg.tokens_file == Path("/opt/bramble/secrets/tokens.json")
        assert cfg.cookie_secure is True
        assert cfg.allowed_hosts == ("127.0.0.1", "localhost", "testserver")

    def test_cli_overrides_env(self) -> None:
        env = {
            ENV_ADMIN_PORT: "8771",
            ENV_ADMIN_SECRET_FILE: "/env/admin.json",
            ENV_TOKENS_FILE: "/env/tokens.json",
        }
        argv = [
            "--port",
            "8772",
            "--admin-secret-file",
            "/cli/admin.json",
            "--tokens-file",
            "/cli/tokens.json",
            "--allowed-host",
            "testserver",
        ]

        cfg = AdminConfig.from_sources(argv=argv, env=env)

        assert cfg.port == 8772
        assert cfg.admin_secret_file == Path("/cli/admin.json")
        assert cfg.tokens_file == Path("/cli/tokens.json")
        assert cfg.allowed_hosts == ("testserver",)

    def test_rejects_public_host_from_env(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            AdminConfig.from_sources(argv=[], env={ENV_ADMIN_HOST: "0.0.0.0"})
