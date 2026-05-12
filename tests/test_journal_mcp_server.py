"""Tests for the JournalMCPServer and its tool implementations."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.journal_mcp_server import (
    JournalMCPServer,
    _entry_to_dict,
    _require_kebab_case,
)


@pytest.fixture()
def server(db: JournalDB) -> Iterator[JournalMCPServer]:
    yield JournalMCPServer(db)


# ---------------------------------------------------------------------------
# Construction & DI
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_accepts_journal_db_instance(self, db: JournalDB) -> None:
        srv = JournalMCPServer(db)
        assert srv.db is db

    def test_rejects_non_journal_db(self) -> None:
        with pytest.raises(TypeError, match="JournalDB"):
            JournalMCPServer("not a db")  # type: ignore[arg-type]

    def test_app_property_returns_fastmcp_instance(self, server: JournalMCPServer) -> None:
        assert isinstance(server.app, FastMCP)

    def test_phase_3_hooks_default_to_none(self, db: JournalDB) -> None:
        srv = JournalMCPServer(db)
        # We access private attrs deliberately – this test is the
        # contract that the slots exist and start as None for Phase 3.
        assert srv._auth_validator is None
        assert srv._rate_limiter is None

    def test_phase_3_hooks_accept_arbitrary_objects(self, db: JournalDB) -> None:
        sentinel_auth = object()
        sentinel_rl = object()
        srv = JournalMCPServer(
            db,
            auth_validator=sentinel_auth,
            rate_limiter=sentinel_rl,
        )
        assert srv._auth_validator is sentinel_auth
        assert srv._rate_limiter is sentinel_rl


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
class TestRequireKebabCase:
    @pytest.mark.parametrize(
        "good", ["bramble", "elder-berry", "a", "a1", "1ab", "a-b-c"]
    )
    def test_accepts_kebab_case(self, good: str) -> None:
        _require_kebab_case(good)  # must not raise

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            " ",
            "Bramble",
            "BRAMBLE",
            "with space",
            "snake_case",
            "-leading",
            "with.dot",
            "with/slash",
        ],
    )
    def test_rejects_non_kebab_case(self, bad: str) -> None:
        with pytest.raises(ValueError, match="kebab-case"):
            _require_kebab_case(bad)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            _require_kebab_case(123)  # type: ignore[arg-type]


class TestEntryToDict:
    def test_round_trips_all_fields(self) -> None:
        ts = datetime(2026, 5, 12, 9, 0, tzinfo=UTC)
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.ABGESCHLOSSEN,
            content="body",
            phase="Phase 2",
            title="Title",
            timestamp=ts,
            id=42,
        )
        d = _entry_to_dict(entry)
        assert d == {
            "id": 42,
            "project": "bramble",
            "timestamp": "2026-05-12T09:00:00+00:00",
            "status": "abgeschlossen",
            "phase": "Phase 2",
            "title": "Title",
            "content": "body",
        }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
class TestToolRegistry:
    async def test_expected_tools_are_registered(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        assert names == ["journal_append", "journal_read"]


# ---------------------------------------------------------------------------
# journal_read
# ---------------------------------------------------------------------------
class TestJournalRead:
    async def test_happy_path_returns_dicts_newest_first(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        for i in range(3):
            db.append(
                JournalEntry(
                    project="bramble",
                    status=JournalStatus.NOTIZ,
                    content=f"entry-{i}",
                    timestamp=base + timedelta(minutes=i),
                )
            )

        async with Client(server.app) as client:
            result = await client.call_tool("journal_read", {"project": "bramble"})

        assert isinstance(result.data, list)
        assert [r["content"] for r in result.data] == ["entry-2", "entry-1", "entry-0"]
        for r in result.data:
            assert set(r.keys()) == {
                "id",
                "project",
                "timestamp",
                "status",
                "phase",
                "title",
                "content",
            }

    async def test_respects_n_argument(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        for i in range(5):
            db.append(
                JournalEntry(
                    project="bramble",
                    status=JournalStatus.NOTIZ,
                    content=f"e{i}",
                    timestamp=base + timedelta(minutes=i),
                )
            )
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_read", {"project": "bramble", "n": 2}
            )
        assert len(result.data) == 2

    async def test_rejects_non_kebab_case_project(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool("journal_read", {"project": "Bad Name"})

    async def test_rejects_non_positive_n(self, server: JournalMCPServer) -> None:
        # JournalDB raises ValueError; translate_errors converts it.
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="positive"):
                await client.call_tool("journal_read", {"project": "bramble", "n": 0})

    async def test_returns_empty_list_for_unknown_project(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_read", {"project": "no-such-project"}
            )
        assert result.data == []


# ---------------------------------------------------------------------------
# journal_append
# ---------------------------------------------------------------------------
class TestJournalAppend:
    async def test_happy_path_returns_entry_with_id(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_append",
                {
                    "project": "bramble",
                    "status": "notiz",
                    "content": "kickoff entry",
                },
            )
        assert isinstance(result.data, dict)
        assert isinstance(result.data["id"], int) and result.data["id"] > 0
        assert result.data["project"] == "bramble"
        assert result.data["status"] == "notiz"
        assert result.data["content"] == "kickoff entry"
        assert result.data["phase"] is None
        assert result.data["title"] is None

    async def test_optional_fields_are_persisted(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        async with Client(server.app) as client:
            await client.call_tool(
                "journal_append",
                {
                    "project": "elder-berry",
                    "status": "abgeschlossen",
                    "content": "done",
                    "phase": "Phase 1",
                    "title": "Closeout",
                },
            )
        [stored] = db.read("elder-berry")
        assert stored.phase == "Phase 1"
        assert stored.title == "Closeout"
        assert stored.status is JournalStatus.ABGESCHLOSSEN

    async def test_timestamp_is_server_set(
        self, server: JournalMCPServer
    ) -> None:
        before = datetime.now(tz=UTC)
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_append",
                {"project": "bramble", "status": "notiz", "content": "ts"},
            )
        after = datetime.now(tz=UTC)
        ts = datetime.fromisoformat(result.data["timestamp"])
        assert before - timedelta(seconds=1) <= ts <= after + timedelta(seconds=1)

    async def test_rejects_unknown_status(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="not allowed"):
                await client.call_tool(
                    "journal_append",
                    {
                        "project": "bramble",
                        "status": "in_progress",
                        "content": "x",
                    },
                )

    async def test_rejects_empty_content(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="content"):
                await client.call_tool(
                    "journal_append",
                    {
                        "project": "bramble",
                        "status": "notiz",
                        "content": "   ",
                    },
                )

    async def test_rejects_non_kebab_case_project(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool(
                    "journal_append",
                    {"project": "Bad", "status": "notiz", "content": "x"},
                )


# ---------------------------------------------------------------------------
# Transport pre-validation
# ---------------------------------------------------------------------------
class TestRunPreValidation:
    def test_unknown_transport_raises(self, server: JournalMCPServer) -> None:
        with pytest.raises(ValueError, match="unsupported transport"):
            server.run(transport="websocket")

    def test_http_without_host_raises(self, server: JournalMCPServer) -> None:
        with pytest.raises(ValueError, match="host and port"):
            server.run(transport="http", host=None, port=8765)

    def test_http_without_port_raises(self, server: JournalMCPServer) -> None:
        with pytest.raises(ValueError, match="host and port"):
            server.run(transport="http", host="127.0.0.1", port=None)
