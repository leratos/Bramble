"""Unit tests for :mod:`scripts.smoke_http`.

The smoke script is intentionally manual for network checks; these tests
only cover local CLI parsing and mode routing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from scripts import smoke_http


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _ClientContext:
    def __init__(self, client: "_ScriptedClient") -> None:
        self._client = client

    async def __aenter__(self) -> "_ScriptedClient":
        return self._client

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _ScriptedClient:
    def __init__(self, calls: Sequence[tuple[str, Any]]) -> None:
        self._calls = list(calls)
        self.called_tools: list[str] = []

    async def list_tools(self) -> list[_Tool]:
        return [_Tool(name) for name in smoke_http.EXPECTED_TOOLS]

    async def call_tool(self, name: str, args: dict[str, Any]) -> _Result:
        self.called_tools.append(name)
        if not self._calls:
            raise AssertionError(f"unexpected call: {name} {args}")

        expected_name, payload = self._calls.pop(0)
        if name != expected_name:
            raise AssertionError(f"expected call {expected_name}, got {name}")

        if isinstance(payload, Exception):
            raise payload
        return _Result(payload)

    def assert_exhausted(self) -> None:
        assert self._calls == []


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


def test_run_smoke_read_only_success(monkeypatch: pytest.MonkeyPatch) -> None:
    authed = _ScriptedClient(
        [
            ("journal_read", []),
            (
                "journal_context",
                {
                    "project": "bramble",
                    "recent": [],
                    "open_items": [],
                    "recent_bugfixes": [],
                    "recent_decisions": [],
                    "related_projects": [],
                    "suggested_searches": [],
                },
            ),
            ("journal_search", []),
            ("journal_search_all", []),
            (
                "journal_digest",
                {
                    "range": {"since": "x", "until": "y"},
                    "projects": ["bramble"],
                    "counts_by_project": {"bramble": 0},
                    "counts_by_status": {},
                    "entries": [],
                    "open_items": [],
                    "bugfixes": [],
                    "decisions": [],
                },
            ),
            ("journal_open_items", []),
            ("journal_list_projects", [{"project": "bramble", "entry_count": 0, "last_timestamp": "2026-05-29T00:00:00+00:00"}]),
            (
                "journal_read",
                smoke_http.ToolError("project must be kebab-case"),
            ),
        ]
    )
    anon = _ScriptedClient(
        [
            (
                "journal_list_projects",
                smoke_http.ToolError("missing or invalid bearer token"),
            )
        ]
    )

    def fake_make_client(url: str, token: str | None) -> _ClientContext:
        _ = url
        return _ClientContext(anon if token is None else authed)

    monkeypatch.setattr(smoke_http, "make_client", fake_make_client)

    rc = asyncio.run(
        smoke_http.run_smoke(
            url="http://localhost:8765/mcp/",
            token="tok",
            project="bramble",
            mode=smoke_http.SMOKE_MODE_READ_ONLY,
        )
    )

    assert rc == 0
    assert "journal_append" not in authed.called_tools
    authed.assert_exhausted()
    anon.assert_exhausted()


def test_run_smoke_write_light_success(monkeypatch: pytest.MonkeyPatch) -> None:
    alpha = {"id": 101, "status": "in_arbeit"}
    beta = {"id": 102, "status": "bugfix"}

    authed = _ScriptedClient(
        [
            ("journal_append", alpha),
            ("journal_append", beta),
            ("journal_read", [{"id": beta["id"]}, {"id": alpha["id"]}]),
            ("journal_search", [{"id": beta["id"]}]),
            (
                "journal_context",
                {
                    "project": "bramble",
                    "recent": [],
                    "open_items": [],
                    "recent_bugfixes": [],
                    "recent_decisions": [],
                    "related_projects": [],
                    "suggested_searches": [],
                },
            ),
            ("journal_search_all", [{"id": beta["id"]}]),
            (
                "journal_digest",
                {
                    "range": {"since": "x", "until": "y"},
                    "projects": ["bramble"],
                    "counts_by_project": {"bramble": 2},
                    "counts_by_status": {"in_arbeit": 1, "bugfix": 1},
                    "entries": [{"id": alpha["id"]}, {"id": beta["id"]}],
                    "open_items": [{"id": alpha["id"]}],
                    "bugfixes": [{"id": beta["id"]}],
                    "decisions": [],
                },
            ),
            (
                "journal_open_items",
                [{"id": alpha["id"], "status": "in_arbeit", "open_state": "open"}],
            ),
            (
                "journal_append",
                smoke_http.ToolError("token is bound to another project"),
            ),
            (
                "journal_list_projects",
                [
                    {
                        "project": "bramble",
                        "entry_count": 2,
                        "last_timestamp": "2026-05-29T00:00:00+00:00",
                    }
                ],
            ),
            ("journal_append", smoke_http.ToolError("unknown status")),
            (
                "journal_read",
                smoke_http.ToolError("project must be kebab-case"),
            ),
        ]
    )
    anon = _ScriptedClient(
        [
            (
                "journal_list_projects",
                smoke_http.ToolError("missing or invalid bearer token"),
            )
        ]
    )

    def fake_make_client(url: str, token: str | None) -> _ClientContext:
        _ = url
        return _ClientContext(anon if token is None else authed)

    monkeypatch.setattr(smoke_http, "make_client", fake_make_client)

    rc = asyncio.run(
        smoke_http.run_smoke(
            url="http://localhost:8765/mcp/",
            token="tok",
            project="bramble",
            mode=smoke_http.SMOKE_MODE_WRITE_LIGHT,
        )
    )

    assert rc == 0
    assert authed.called_tools.count("journal_append") == 4
    authed.assert_exhausted()
    anon.assert_exhausted()
