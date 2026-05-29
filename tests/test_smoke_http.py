"""Unit tests for :mod:`scripts.smoke_http`.

The smoke script is intentionally manual for network checks; these tests
only cover local CLI parsing and mode routing.
"""

from __future__ import annotations

import asyncio

import pytest

from scripts import smoke_http


def test_parse_args_defaults() -> None:
    args = smoke_http.parse_args(["--token", "t"])

    assert args.url == smoke_http.DEFAULT_URL
    assert args.token == "t"
    assert args.project == "bramble"
    assert args.mode == smoke_http.SMOKE_MODE_WRITE_LIGHT


def test_parse_args_read_only_mode() -> None:
    args = smoke_http.parse_args(
        ["--token", "t", "--project", "elder-berry", "--mode", "read-only"]
    )

    assert args.project == "elder-berry"
    assert args.mode == smoke_http.SMOKE_MODE_READ_ONLY


def test_parse_args_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        smoke_http.parse_args(["--token", "t", "--mode", "invalid-mode"])


def test_main_routes_mode_to_run_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def fake_run_smoke(url: str, token: str, project: str, mode: str) -> int:
        captured["url"] = url
        captured["token"] = token
        captured["project"] = project
        captured["mode"] = mode
        return 17

    real_asyncio_run = asyncio.run
    monkeypatch.setattr(smoke_http, "run_smoke", fake_run_smoke)
    monkeypatch.setattr(smoke_http.asyncio, "run", lambda coro: real_asyncio_run(coro))

    rc = smoke_http.main(
        [
            "--url",
            "http://localhost:9999/mcp/",
            "--token",
            "tok",
            "--project",
            "bramble",
            "--mode",
            "read-only",
        ]
    )

    assert rc == 17
    assert captured == {
        "url": "http://localhost:9999/mcp/",
        "token": "tok",
        "project": "bramble",
        "mode": "read-only",
    }


def test_main_returns_2_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_asyncio_run(coro: object) -> int:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise ConnectionRefusedError("offline")

    monkeypatch.setattr(smoke_http.asyncio, "run", fake_asyncio_run)

    rc = smoke_http.main(["--token", "tok"])

    assert rc == 2
