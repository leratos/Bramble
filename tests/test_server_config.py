"""Unit tests for :mod:`bramble.server_config`."""

from __future__ import annotations

from pathlib import Path

import pytest

from bramble.server_config import (
    ENV_DB_PATH,
    ENV_HOST,
    ENV_LOG_LEVEL,
    ENV_PORT,
    ENV_RATE_LIMIT_PER_IP,
    ENV_RATE_LIMIT_PER_TOKEN,
    ENV_TOKENS_FILE,
    ENV_TRANSPORT,
    ServerConfig,
)


# ---------------------------------------------------------------------------
# Direct construction & field validation
# ---------------------------------------------------------------------------
class TestServerConfigConstruction:
    def _valid_kwargs(self) -> dict:
        return {
            "db_path": Path("./data/bramble.db"),
            "transport": "stdio",
            "host": "127.0.0.1",
            "port": 8765,
            "log_level": "INFO",
        }

    def test_valid_kwargs_construct(self) -> None:
        cfg = ServerConfig(**self._valid_kwargs())
        assert cfg.transport == "stdio"
        assert cfg.port == 8765

    def test_config_is_frozen(self) -> None:
        cfg = ServerConfig(**self._valid_kwargs())
        with pytest.raises(Exception):  # FrozenInstanceError
            cfg.port = 9999  # type: ignore[misc]

    def test_db_path_must_be_path(self) -> None:
        kwargs = self._valid_kwargs() | {"db_path": "./data/bramble.db"}
        with pytest.raises(TypeError):
            ServerConfig(**kwargs)

    def test_transport_rejects_unknown_value(self) -> None:
        kwargs = self._valid_kwargs() | {"transport": "websocket"}
        with pytest.raises(ValueError, match="transport"):
            ServerConfig(**kwargs)

    def test_host_rejects_empty(self) -> None:
        kwargs = self._valid_kwargs() | {"host": "   "}
        with pytest.raises(ValueError):
            ServerConfig(**kwargs)

    @pytest.mark.parametrize("bad_port", [0, -1, 65536, 70000])
    def test_port_must_be_in_range(self, bad_port: int) -> None:
        kwargs = self._valid_kwargs() | {"port": bad_port}
        with pytest.raises(ValueError):
            ServerConfig(**kwargs)

    def test_port_rejects_bool(self) -> None:
        kwargs = self._valid_kwargs() | {"port": True}
        with pytest.raises(TypeError):
            ServerConfig(**kwargs)

    def test_log_level_rejects_unknown(self) -> None:
        kwargs = self._valid_kwargs() | {"log_level": "TRACE"}
        with pytest.raises(ValueError):
            ServerConfig(**kwargs)

    def test_phase_3_fields_have_defaults(self) -> None:
        # The five Phase-2 kwargs alone must still construct: the
        # Phase-3 fields carry defaults.
        cfg = ServerConfig(**self._valid_kwargs())
        assert cfg.tokens_file == Path("./secrets/tokens.json")
        assert cfg.rate_limit_per_token == 60
        assert cfg.rate_limit_per_ip == 120

    def test_tokens_file_must_be_path(self) -> None:
        kwargs = self._valid_kwargs() | {"tokens_file": "./secrets/tokens.json"}
        with pytest.raises(TypeError):
            ServerConfig(**kwargs)

    @pytest.mark.parametrize(
        "field", ["rate_limit_per_token", "rate_limit_per_ip"]
    )
    @pytest.mark.parametrize("bad_value", [0, -1])
    def test_rate_limit_must_be_positive(self, field: str, bad_value: int) -> None:
        kwargs = self._valid_kwargs() | {field: bad_value}
        with pytest.raises(ValueError):
            ServerConfig(**kwargs)

    @pytest.mark.parametrize(
        "field", ["rate_limit_per_token", "rate_limit_per_ip"]
    )
    def test_rate_limit_rejects_bool(self, field: str) -> None:
        kwargs = self._valid_kwargs() | {field: True}
        with pytest.raises(TypeError):
            ServerConfig(**kwargs)


# ---------------------------------------------------------------------------
# from_sources(): priority CLI > Env > Default
# ---------------------------------------------------------------------------
class TestFromSources:
    def test_all_defaults_when_no_cli_no_env(self) -> None:
        cfg = ServerConfig.from_sources(argv=[], env={})
        assert cfg.db_path == Path("./data/bramble.db")
        assert cfg.transport == "stdio"
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8765
        assert cfg.log_level == "INFO"
        assert cfg.tokens_file == Path("./secrets/tokens.json")
        assert cfg.rate_limit_per_token == 60
        assert cfg.rate_limit_per_ip == 120

    def test_phase_3_env_overrides_default(self) -> None:
        env = {
            ENV_TOKENS_FILE: "/opt/bramble/secrets/tokens.json",
            ENV_RATE_LIMIT_PER_TOKEN: "30",
            ENV_RATE_LIMIT_PER_IP: "200",
        }
        cfg = ServerConfig.from_sources(argv=[], env=env)
        assert cfg.tokens_file == Path("/opt/bramble/secrets/tokens.json")
        assert cfg.rate_limit_per_token == 30
        assert cfg.rate_limit_per_ip == 200

    def test_phase_3_cli_overrides_env(self) -> None:
        env = {ENV_TOKENS_FILE: "/env/tokens.json", ENV_RATE_LIMIT_PER_TOKEN: "10"}
        argv = [
            "--tokens-file",
            "/cli/tokens.json",
            "--rate-limit-per-token",
            "90",
            "--rate-limit-per-ip",
            "150",
        ]
        cfg = ServerConfig.from_sources(argv=argv, env=env)
        assert cfg.tokens_file == Path("/cli/tokens.json")
        assert cfg.rate_limit_per_token == 90
        assert cfg.rate_limit_per_ip == 150

    def test_non_integer_rate_limit_env_raises(self) -> None:
        with pytest.raises(ValueError, match="rate_limit_per_token"):
            ServerConfig.from_sources(
                argv=[], env={ENV_RATE_LIMIT_PER_TOKEN: "lots"}
            )

    def test_env_overrides_default(self) -> None:
        env = {
            ENV_DB_PATH: "/tmp/from-env.db",
            ENV_TRANSPORT: "http",
            ENV_HOST: "0.0.0.0",
            ENV_PORT: "9000",
            ENV_LOG_LEVEL: "DEBUG",
        }
        cfg = ServerConfig.from_sources(argv=[], env=env)
        assert cfg.db_path == Path("/tmp/from-env.db")
        assert cfg.transport == "http"
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9000
        assert cfg.log_level == "DEBUG"

    def test_cli_overrides_env(self) -> None:
        env = {
            ENV_DB_PATH: "/tmp/env.db",
            ENV_TRANSPORT: "stdio",
            ENV_PORT: "1000",
        }
        argv = [
            "--db",
            "/tmp/cli.db",
            "--transport",
            "http",
            "--port",
            "9100",
        ]
        cfg = ServerConfig.from_sources(argv=argv, env=env)
        assert cfg.db_path == Path("/tmp/cli.db")
        assert cfg.transport == "http"
        assert cfg.port == 9100

    def test_log_level_is_uppercased(self) -> None:
        cfg = ServerConfig.from_sources(argv=["--log-level", "debug"], env={})
        assert cfg.log_level == "DEBUG"

    def test_partial_env_falls_back_to_defaults(self) -> None:
        cfg = ServerConfig.from_sources(argv=[], env={ENV_TRANSPORT: "http"})
        assert cfg.transport == "http"
        assert cfg.host == "127.0.0.1"  # default
        assert cfg.port == 8765  # default

    def test_non_integer_port_env_raises(self) -> None:
        with pytest.raises(ValueError, match="port"):
            ServerConfig.from_sources(argv=[], env={ENV_PORT: "abc"})

    def test_invalid_transport_via_cli_is_caught_by_argparse(self) -> None:
        # argparse rejects unknown choices before construction
        with pytest.raises(SystemExit):
            ServerConfig.from_sources(argv=["--transport", "websocket"], env={})

    def test_invalid_transport_via_env_caught_by_validation(self) -> None:
        with pytest.raises(ValueError, match="transport"):
            ServerConfig.from_sources(argv=[], env={ENV_TRANSPORT: "websocket"})
