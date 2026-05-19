"""End-to-end smoke tests for the ``bramble-server`` entry point.

Asserts that ``python -m bramble`` resolves the config, opens and
initialises the DB, and dispatches to the right transport. The actual
``FastMCP.run`` call is patched out – the test does not start a real
stdio/HTTP server, which would block forever.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bramble import __main__ as cli
from bramble.journal_mcp_server import JournalMCPServer
from bramble.server_config import (
    ENV_DB_PATH,
    ENV_HOST,
    ENV_LOG_LEVEL,
    ENV_PORT,
    ENV_RATE_LIMIT_PER_IP,
    ENV_RATE_LIMIT_PER_TOKEN,
    ENV_TOKENS_FILE,
    ENV_TRANSPORT,
)


def _clear_bramble_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any BRAMBLE_* vars that might leak from the test runner."""

    for var in (
        ENV_DB_PATH,
        ENV_TRANSPORT,
        ENV_HOST,
        ENV_PORT,
        ENV_LOG_LEVEL,
        ENV_TOKENS_FILE,
        ENV_RATE_LIMIT_PER_TOKEN,
        ENV_RATE_LIMIT_PER_IP,
    ):
        monkeypatch.delenv(var, raising=False)


def _write_tokens_file(tmp_path: Path) -> Path:
    """Write a minimal token file so the http path can build AuthValidator."""

    path = tmp_path / "tokens.json"
    path.write_text('{"bramble": "tok-bramble"}', encoding="utf-8")
    return path


class TestMainWiring:
    def test_initializes_db_and_starts_stdio(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _clear_bramble_env(monkeypatch)
        db_path = tmp_path / "bramble.db"
        monkeypatch.setattr(
            sys,
            "argv",
            ["bramble-server", "--db", str(db_path)],
        )

        captured: dict = {}

        def fake_run(self: JournalMCPServer, **kwargs: object) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(JournalMCPServer, "run", fake_run)

        cli.main()

        assert db_path.exists(), "main() must create and initialise the DB"
        assert captured == {"transport": "stdio"}

    def test_uses_http_when_configured_via_cli(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _clear_bramble_env(monkeypatch)
        db_path = tmp_path / "bramble.db"
        tokens_file = _write_tokens_file(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bramble-server",
                "--db",
                str(db_path),
                "--transport",
                "http",
                "--host",
                "127.0.0.1",
                "--port",
                "9100",
                "--tokens-file",
                str(tokens_file),
            ],
        )

        captured: dict = {}

        def fake_run(self: JournalMCPServer, **kwargs: object) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(JournalMCPServer, "run", fake_run)

        cli.main()

        assert captured == {
            "transport": "http",
            "host": "127.0.0.1",
            "port": 9100,
        }

    def test_http_wires_auth_and_rate_limiter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # The http transport must hand both Phase-3 hooks to the server.
        _clear_bramble_env(monkeypatch)
        db_path = tmp_path / "bramble.db"
        tokens_file = _write_tokens_file(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bramble-server",
                "--db",
                str(db_path),
                "--transport",
                "http",
                "--host",
                "127.0.0.1",
                "--port",
                "9100",
                "--tokens-file",
                str(tokens_file),
            ],
        )

        built: dict = {}

        def fake_run(self: JournalMCPServer, **kwargs: object) -> None:
            built["auth_validator"] = self._auth_validator
            built["rate_limiter"] = self._rate_limiter

        monkeypatch.setattr(JournalMCPServer, "run", fake_run)

        cli.main()

        assert built["auth_validator"] is not None
        assert built["rate_limiter"] is not None

    def test_env_var_path_is_honoured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _clear_bramble_env(monkeypatch)
        db_path = tmp_path / "from-env.db"
        monkeypatch.setenv(ENV_DB_PATH, str(db_path))
        monkeypatch.setattr(sys, "argv", ["bramble-server"])
        monkeypatch.setattr(JournalMCPServer, "run", lambda self, **kw: None)

        cli.main()

        assert db_path.exists()


class TestCliHelp:
    def test_help_exits_cleanly(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "bramble", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "bramble-server" in result.stdout
        assert "--transport" in result.stdout
        assert "--db" in result.stdout
