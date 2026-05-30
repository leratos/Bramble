"""Tests for the JournalMCPServer and its tool implementations."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from bramble.auth_validator import AuthValidator
from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalEntryLink, JournalStatus
from bramble.journal_mcp_server import (
    JournalMCPServer,
    _AuthRateLimitMiddleware,
    _bearer_token,
    _enforce_project_scope,
    _entry_to_dict,
    _require_kebab_case,
    _resolve_client_ip,
    _token_project,
)
from bramble.rate_limiter import RateLimiter


@pytest.fixture()
def server(db: JournalDB) -> Iterator[JournalMCPServer]:
    yield JournalMCPServer(db)


@pytest.fixture()
def auth_validator(tmp_path: Path) -> AuthValidator:
    """An AuthValidator backed by a two-project token file."""

    path = tmp_path / "tokens.json"
    path.write_text(
        '{"bramble": "tok-bramble", "elder-berry": "tok-elder"}', encoding="utf-8"
    )
    return AuthValidator(path)


@pytest.fixture()
def rate_limiter() -> RateLimiter:
    return RateLimiter(per_token_rpm=60, per_ip_rpm=120)


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

    def test_hooks_default_to_none(self, db: JournalDB) -> None:
        srv = JournalMCPServer(db)
        # Accessing private attrs deliberately: the contract is that a
        # stdio server runs without an auth/rate-limit gate.
        assert srv._auth_validator is None
        assert srv._rate_limiter is None

    def test_hooks_are_stored_when_provided(
        self, db: JournalDB, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        srv = JournalMCPServer(
            db, auth_validator=auth_validator, rate_limiter=rate_limiter
        )
        assert srv._auth_validator is auth_validator
        assert srv._rate_limiter is rate_limiter

    def test_requires_both_hooks_or_neither(
        self, db: JournalDB, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        with pytest.raises(ValueError, match="together"):
            JournalMCPServer(db, auth_validator=auth_validator)
        with pytest.raises(ValueError, match="together"):
            JournalMCPServer(db, rate_limiter=rate_limiter)


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
            "actor": None,
            "client": None,
            "source": None,
            "tags": [],
            "links": [],
            "backlinks": [],
        }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
class TestToolRegistry:
    async def test_expected_tools_are_registered(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        assert names == [
            "journal_append",
            "journal_context",
            "journal_digest",
            "journal_list_projects",
            "journal_open_items",
            "journal_read",
            "journal_search",
            "journal_search_all",
        ]


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
                "actor",
                "client",
                "source",
                "tags",
                "links",
                "backlinks",
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
        assert result.data["actor"] is None
        assert result.data["client"] is None
        assert result.data["source"] == "mcp"
        assert result.data["tags"] == []
        assert result.data["links"] == []
        assert result.data["backlinks"] == []

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

    async def test_metadata_fields_are_persisted(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_append",
                {
                    "project": "bramble",
                    "status": "notiz",
                    "content": "metadata",
                    "actor": "codex",
                    "client": "codex-desktop",
                    "source": "agent",
                },
            )

        [stored] = db.read("bramble")
        assert stored.actor == "codex"
        assert stored.client == "codex-desktop"
        assert stored.source == "agent"
        assert result.data["actor"] == "codex"
        assert result.data["client"] == "codex-desktop"
        assert result.data["source"] == "agent"

    async def test_tags_are_persisted(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_append",
                {
                    "project": "bramble",
                    "status": "notiz",
                    "content": "tagged",
                    "tags": ["test", "Admin-UI", "test"],
                },
            )

        [stored] = db.read("bramble")
        assert stored.tags == ("admin-ui", "test")
        assert result.data["tags"] == ["admin-ui", "test"]

    async def test_rejects_invalid_tags(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool(
                    "journal_append",
                    {
                        "project": "bramble",
                        "status": "notiz",
                        "content": "bad tag",
                        "tags": ["bad_tag"],
                    },
                )

    async def test_links_are_persisted(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        old = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="old",
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_append",
                {
                    "project": "bramble",
                    "status": "bugfix",
                    "content": "fix",
                    "links": [{"to_entry_id": old.id, "relation": "corrects"}],
                },
            )

        [new_entry, old_entry] = db.read("bramble")
        assert new_entry.links[0].entry_id == old.id
        assert new_entry.links[0].relation == "corrects"
        assert old_entry.backlinks[0].entry_id == new_entry.id
        assert result.data["links"] == [
            {"to_entry_id": old.id, "relation": "corrects"}
        ]

    async def test_rejects_missing_link_target(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="does not exist"):
                await client.call_tool(
                    "journal_append",
                    {
                        "project": "bramble",
                        "status": "bugfix",
                        "content": "bad link",
                        "links": [{"to_entry_id": 999, "relation": "corrects"}],
                    },
                )

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
# journal_search
# ---------------------------------------------------------------------------
class TestJournalSearch:
    async def test_happy_path_finds_word_in_content(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="we fixed a flaky test today",
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="unrelated content",
            )
        )
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_search", {"project": "bramble", "query": "flaky"}
            )
        assert len(result.data) == 1
        assert "flaky" in result.data[0]["content"]

    async def test_respects_limit(self, server: JournalMCPServer, db: JournalDB) -> None:
        for i in range(5):
            db.append(
                JournalEntry(
                    project="bramble",
                    status=JournalStatus.NOTIZ,
                    content=f"keyword variant {i}",
                )
            )
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_search",
                {"project": "bramble", "query": "keyword", "limit": 2},
            )
        assert len(result.data) == 2

    async def test_malformed_fts5_returns_empty_list(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble", status=JournalStatus.NOTIZ, content="something"
            )
        )
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_search",
                {"project": "bramble", "query": '"open quote'},
            )
        assert result.data == []

    async def test_rejects_empty_query(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="query"):
                await client.call_tool(
                    "journal_search", {"project": "bramble", "query": "   "}
                )

    async def test_rejects_non_kebab_case_project(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool(
                    "journal_search", {"project": "Bad", "query": "x"}
                )

    async def test_rejects_non_positive_limit(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="positive"):
                await client.call_tool(
                    "journal_search",
                    {"project": "bramble", "query": "x", "limit": 0},
                )


# ---------------------------------------------------------------------------
# journal_search_all
# ---------------------------------------------------------------------------
class TestJournalSearchAll:
    async def test_happy_path_finds_matches_across_projects(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="deployment keyword in bramble",
                timestamp=base,
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                content="deployment keyword in elder",
                timestamp=base + timedelta(minutes=1),
                tags=["deploy"],
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_search_all",
                {"query": "deployment"},
            )

        assert [row["project"] for row in result.data] == ["elder-berry", "bramble"]
        assert set(result.data[0]) == {
            "id",
            "project",
            "timestamp",
            "status",
            "phase",
            "title",
            "content",
            "actor",
            "client",
            "source",
            "tags",
            "links",
            "backlinks",
        }

    async def test_filters_projects_statuses_and_tags(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="release keyword",
                tags=["deploy"],
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                content="release keyword",
                tags=["deploy", "hotfix"],
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_search_all",
                {
                    "query": "release",
                    "projects": ["elder-berry"],
                    "statuses": ["bugfix"],
                    "tags": ["Deploy", "hotfix"],
                },
            )

        assert len(result.data) == 1
        assert result.data[0]["project"] == "elder-berry"
        assert result.data[0]["tags"] == ["deploy", "hotfix"]

    async def test_malformed_fts5_returns_empty_list(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble", status=JournalStatus.NOTIZ, content="something"
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_search_all",
                {"query": '"open quote'},
            )

        assert result.data == []

    async def test_rejects_non_kebab_case_project_filter(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool(
                    "journal_search_all",
                    {"query": "x", "projects": ["Bad"]},
                )

    async def test_rejects_limit_above_cap(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="at most 100"):
                await client.call_tool(
                    "journal_search_all",
                    {"query": "x", "limit": 101},
                )


# ---------------------------------------------------------------------------
# journal_digest
# ---------------------------------------------------------------------------
class TestJournalDigest:
    async def test_happy_path_returns_structured_digest(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="open digest work",
                timestamp=now - timedelta(hours=1),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                title="Decision: digest shape",
                content="bugfix digest work",
                tags=["decision"],
                timestamp=now - timedelta(hours=2),
            )
        )
        db.append(
            JournalEntry(
                project="berry-gym",
                status=JournalStatus.NOTIZ,
                content="old digest work",
                timestamp=now - timedelta(days=40),
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool("journal_digest", {"since": "24h"})

        assert result.data["projects"] == ["bramble", "elder-berry"]
        assert result.data["counts_by_project"] == {
            "bramble": 1,
            "elder-berry": 1,
        }
        assert result.data["counts_by_status"] == {"bugfix": 1, "in_arbeit": 1}
        assert [row["content"] for row in result.data["entries"]] == [
            "open digest work",
            "bugfix digest work",
        ]
        assert [row["content"] for row in result.data["open_items"]] == [
            "open digest work"
        ]
        assert [row["content"] for row in result.data["bugfixes"]] == [
            "bugfix digest work"
        ]
        assert [row["content"] for row in result.data["decisions"]] == [
            "bugfix digest work"
        ]
        assert set(result.data["range"]) == {"since", "until"}

    async def test_filters_project_and_tags(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="deploy digest",
                tags=["deploy"],
                timestamp=now - timedelta(hours=1),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content="deploy other",
                tags=["deploy"],
                timestamp=now - timedelta(hours=2),
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_digest",
                {"project": "bramble", "since": "24h", "tags": ["Deploy"]},
            )

        assert [row["content"] for row in result.data["entries"]] == [
            "deploy digest"
        ]
        assert result.data["counts_by_project"] == {"bramble": 1}

    async def test_rejects_non_kebab_case_project(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool(
                    "journal_digest",
                    {"project": "Bad", "since": "24h"},
                )

    async def test_rejects_invalid_range(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="since"):
                await client.call_tool(
                    "journal_digest",
                    {"since": "yesterday"},
                )


# ---------------------------------------------------------------------------
# journal_context
# ---------------------------------------------------------------------------
class TestJournalContext:
    async def test_happy_path_returns_curated_session_start_structure(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="deployment prep in progress",
                title="Phase 4d prep",
                phase="Phase 4d",
                tags=["deploy"],
                timestamp=now - timedelta(hours=1),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="fixed digest edge case",
                timestamp=now - timedelta(hours=2),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                title="Decision: keep context deterministic",
                content="decision payload",
                tags=["decision"],
                timestamp=now - timedelta(hours=3),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content="deployment notes from sibling project",
                timestamp=now - timedelta(hours=4),
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_context",
                {"project": "bramble", "n_recent": 2},
            )

        assert result.data["project"] == "bramble"
        assert len(result.data["recent"]) == 2
        assert [row["status"] for row in result.data["open_items"]] == ["in_arbeit"]
        assert [row["status"] for row in result.data["recent_bugfixes"]] == ["bugfix"]
        assert [row["title"] for row in result.data["recent_decisions"]] == [
            "Decision: keep context deterministic"
        ]
        assert "elder-berry" in result.data["related_projects"]
        assert "Phase 4d" in result.data["suggested_searches"]
        assert "deployment" in result.data["suggested_searches"]

    async def test_empty_project_returns_empty_lists(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_context", {"project": "berry-gym"}
            )

        assert result.data == {
            "project": "berry-gym",
            "recent": [],
            "open_items": [],
            "recent_bugfixes": [],
            "recent_decisions": [],
            "related_projects": [],
            "suggested_searches": [],
        }

    async def test_can_disable_cross_project_lookup(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="deployment prep",
                phase="Phase 4d",
                timestamp=now,
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content="deployment in other project",
                timestamp=now - timedelta(minutes=1),
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_context",
                {
                    "project": "bramble",
                    "include_cross_project": False,
                },
            )

        assert result.data["related_projects"] == []
        assert result.data["suggested_searches"] == ["Phase 4d", "deployment"]

    async def test_session_start_flow_context_then_global_search(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="deployment checklist for upcoming host update",
                phase="Phase 4d",
                timestamp=now,
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content="deployment notes mirrored in sibling project",
                timestamp=now - timedelta(minutes=1),
            )
        )

        async with Client(server.app) as client:
            context_result = await client.call_tool(
                "journal_context",
                {"project": "bramble", "n_recent": 5},
            )
            assert "deployment" in context_result.data["suggested_searches"]

            search_result = await client.call_tool(
                "journal_search_all",
                {
                    "query": "deployment",
                    "projects": ["bramble"],
                    "limit": 5,
                },
            )

        assert search_result.data
        assert search_result.data[0]["project"] == "bramble"
        assert "deployment" in search_result.data[0]["content"]

    async def test_rejects_non_kebab_case_project(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool("journal_context", {"project": "Bad Name"})

    async def test_rejects_non_positive_n_recent(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="positive"):
                await client.call_tool(
                    "journal_context",
                    {"project": "bramble", "n_recent": 0},
                )


# ---------------------------------------------------------------------------
# journal_open_items
# ---------------------------------------------------------------------------
class TestJournalOpenItems:
    async def test_happy_path_returns_newest_open_items(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="open older",
                timestamp=now - timedelta(minutes=2),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="not open",
                timestamp=now - timedelta(minutes=1),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="open newer",
                timestamp=now,
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool("journal_open_items", {"limit": 10})

        assert [row["content"] for row in result.data] == [
            "open newer",
            "open older",
        ]
        assert [row["status"] for row in result.data] == ["in_arbeit", "in_arbeit"]

    async def test_filters_by_project(self, server: JournalMCPServer, db: JournalDB) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="open bramble",
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="open elder",
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_open_items", {"project": "bramble", "limit": 10}
            )

        assert [row["content"] for row in result.data] == ["open bramble"]

    async def test_rejects_non_kebab_case_project(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="kebab-case"):
                await client.call_tool(
                    "journal_open_items", {"project": "Bad Name"}
                )

    async def test_rejects_limit_above_cap(self, server: JournalMCPServer) -> None:
        async with Client(server.app) as client:
            with pytest.raises(ToolError, match="at most 100"):
                await client.call_tool(
                    "journal_open_items", {"limit": 101}
                )

    async def test_rows_carry_open_state_annotations(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="open work",
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool(
                "journal_open_items", {"project": "bramble", "limit": 10}
            )

        row = result.data[0]
        assert row["open_state"] in ("open", "stale")
        assert row["resolution_reason"] is None
        assert row["resolved_by_id"] is None
        assert isinstance(row["age_days"], int)

    async def test_resolved_hidden_by_default_shown_with_flag(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="wip",
            )
        )
        closer = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                content="done",
                links=(
                    JournalEntryLink(entry_id=open_entry.id, relation="resolves"),
                ),
            )
        )

        async with Client(server.app) as client:
            default = await client.call_tool(
                "journal_open_items", {"project": "elder-berry"}
            )
            with_resolved = await client.call_tool(
                "journal_open_items",
                {"project": "elder-berry", "include_resolved": True},
            )

        assert default.data == []
        assert len(with_resolved.data) == 1
        row = with_resolved.data[0]
        assert row["id"] == open_entry.id
        assert row["open_state"] == "resolved"
        assert row["resolution_reason"] == "link"
        assert row["resolved_by_id"] == closer.id


# ---------------------------------------------------------------------------
# journal_list_projects
# ---------------------------------------------------------------------------
class TestJournalListProjects:
    async def test_empty_db_returns_empty_list(
        self, server: JournalMCPServer
    ) -> None:
        async with Client(server.app) as client:
            result = await client.call_tool("journal_list_projects", {})
        assert result.data == []

    async def test_happy_path_returns_counts_and_timestamps(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="b1",
                timestamp=base,
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="b2",
                timestamp=base + timedelta(minutes=5),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content="e1",
                timestamp=base + timedelta(minutes=10),
            )
        )

        async with Client(server.app) as client:
            result = await client.call_tool("journal_list_projects", {})

        # Most recent activity first.
        names = [row["project"] for row in result.data]
        assert names == ["elder-berry", "bramble"]
        by_name = {row["project"]: row for row in result.data}
        assert by_name["bramble"]["entry_count"] == 2
        assert by_name["bramble"]["last_timestamp"] == "2026-05-12T08:05:00+00:00"
        assert by_name["elder-berry"]["entry_count"] == 1
        assert by_name["elder-berry"]["last_timestamp"] == "2026-05-12T08:10:00+00:00"

    async def test_registered_empty_project_has_null_timestamp(
        self, server: JournalMCPServer, db: JournalDB
    ) -> None:
        db.register_project("berry-gym")

        async with Client(server.app) as client:
            result = await client.call_tool("journal_list_projects", {})

        assert result.data == [
            {
                "project": "berry-gym",
                "entry_count": 0,
                "last_timestamp": None,
            }
        ]


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


# ---------------------------------------------------------------------------
# Phase-3 auth: bearer-token parsing
# ---------------------------------------------------------------------------
class TestBearerToken:
    @pytest.mark.parametrize(
        "header,expected",
        [
            ("Bearer abc123", "abc123"),
            ("bearer abc123", "abc123"),  # scheme is case-insensitive
            ("BEARER abc123", "abc123"),
            ("Bearer   spaced", "spaced"),  # extra whitespace stripped
            (None, None),
            ("", None),
            ("abc123", None),  # no scheme
            ("Basic abc123", None),  # wrong scheme
            ("Bearer ", None),  # empty token
            ("Bearer", None),  # scheme only
        ],
    )
    def test_parses_header(self, header: str | None, expected: str | None) -> None:
        assert _bearer_token(header) == expected


# ---------------------------------------------------------------------------
# Phase-3 auth: client-IP resolution (X-Forwarded-For mitigation)
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in for a Starlette request: peer + headers."""

    def __init__(self, peer: str | None, headers: dict[str, str] | None = None) -> None:
        self.client = _FakeClient(peer) if peer is not None else None
        self.headers = headers or {}


class TestResolveClientIp:
    def test_uses_peer_when_no_forwarded_header(self) -> None:
        request = _FakeRequest("198.51.100.5")
        assert _resolve_client_ip(request) == "198.51.100.5"

    def test_trusts_forwarded_header_from_loopback(self) -> None:
        request = _FakeRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})
        assert _resolve_client_ip(request) == "203.0.113.9"

    def test_takes_leftmost_forwarded_entry(self) -> None:
        request = _FakeRequest(
            "127.0.0.1", {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
        )
        assert _resolve_client_ip(request) == "203.0.113.9"

    def test_ignores_forwarded_header_from_untrusted_peer(self) -> None:
        # Spoofing mitigation: a non-loopback peer cannot forge its IP.
        request = _FakeRequest(
            "198.51.100.5", {"x-forwarded-for": "203.0.113.9"}
        )
        assert _resolve_client_ip(request) == "198.51.100.5"

    def test_falls_back_to_peer_when_forwarded_is_blank(self) -> None:
        request = _FakeRequest("127.0.0.1", {"x-forwarded-for": "   "})
        assert _resolve_client_ip(request) == "127.0.0.1"

    def test_handles_missing_client(self) -> None:
        assert _resolve_client_ip(_FakeRequest(None)) == "unknown"


# ---------------------------------------------------------------------------
# Phase-3 auth: the middleware authorisation decision
# ---------------------------------------------------------------------------
class TestAuthorize:
    def test_valid_token_returns_project(
        self, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        middleware = _AuthRateLimitMiddleware(auth_validator, rate_limiter)
        assert (
            middleware._authorize(token="tok-bramble", client_ip="1.2.3.4")
            == "bramble"
        )

    def test_missing_token_raises(
        self, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        middleware = _AuthRateLimitMiddleware(auth_validator, rate_limiter)
        with pytest.raises(ToolError, match="authentication"):
            middleware._authorize(token=None, client_ip="1.2.3.4")

    def test_invalid_token_raises(
        self, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        middleware = _AuthRateLimitMiddleware(auth_validator, rate_limiter)
        with pytest.raises(ToolError, match="authentication"):
            middleware._authorize(token="not-a-real-token", client_ip="1.2.3.4")

    def test_exhausted_ip_budget_raises(self, auth_validator: AuthValidator) -> None:
        # per_ip_rpm=1: the second request from the same IP is refused
        # before the token is even looked at.
        limiter = RateLimiter(per_token_rpm=99, per_ip_rpm=1)
        middleware = _AuthRateLimitMiddleware(auth_validator, limiter)
        middleware._authorize(token="tok-bramble", client_ip="1.2.3.4")
        with pytest.raises(ToolError, match="rate limit"):
            middleware._authorize(token="tok-bramble", client_ip="1.2.3.4")

    def test_exhausted_token_budget_raises(
        self, auth_validator: AuthValidator
    ) -> None:
        # per_token_rpm=1: the IP still has budget, the project does not.
        limiter = RateLimiter(per_token_rpm=1, per_ip_rpm=99)
        middleware = _AuthRateLimitMiddleware(auth_validator, limiter)
        middleware._authorize(token="tok-bramble", client_ip="1.2.3.4")
        with pytest.raises(ToolError, match="rate limit"):
            middleware._authorize(token="tok-bramble", client_ip="1.2.3.4")


# ---------------------------------------------------------------------------
# Phase-3 Decision B: journal_append write-scope binding
# ---------------------------------------------------------------------------
class TestProjectScope:
    def test_no_binding_when_context_unset(self) -> None:
        # stdio / no-auth: any project may be written.
        _enforce_project_scope("bramble")  # must not raise

    def test_allows_matching_project(self) -> None:
        reset = _token_project.set("bramble")
        try:
            _enforce_project_scope("bramble")  # must not raise
        finally:
            _token_project.reset(reset)

    def test_rejects_mismatching_project(self) -> None:
        reset = _token_project.set("elder-berry")
        try:
            with pytest.raises(ValueError, match="scoped to project"):
                _enforce_project_scope("bramble")
        finally:
            _token_project.reset(reset)

    async def test_append_rejected_for_foreign_project(
        self, server: JournalMCPServer
    ) -> None:
        reset = _token_project.set("elder-berry")
        try:
            async with Client(server.app) as client:
                with pytest.raises(ToolError, match="scoped to project"):
                    await client.call_tool(
                        "journal_append",
                        {
                            "project": "bramble",
                            "status": "notiz",
                            "content": "should be blocked",
                        },
                    )
        finally:
            _token_project.reset(reset)

    async def test_append_allowed_for_own_project(
        self, server: JournalMCPServer
    ) -> None:
        reset = _token_project.set("bramble")
        try:
            async with Client(server.app) as client:
                result = await client.call_tool(
                    "journal_append",
                    {
                        "project": "bramble",
                        "status": "notiz",
                        "content": "own-project write",
                    },
                )
        finally:
            _token_project.reset(reset)
        assert result.data["project"] == "bramble"


# ---------------------------------------------------------------------------
# Phase-3 auth: the wired-up server
# ---------------------------------------------------------------------------
class TestAuthenticatedServer:
    async def test_blocks_calls_without_a_token(
        self,
        db: JournalDB,
        auth_validator: AuthValidator,
        rate_limiter: RateLimiter,
    ) -> None:
        # A server built with the hooks gates every tool call. The
        # in-process client carries no HTTP request, so it has no
        # token and is refused – proof the middleware is wired in.
        srv = JournalMCPServer(
            db, auth_validator=auth_validator, rate_limiter=rate_limiter
        )
        async with Client(srv.app) as client:
            with pytest.raises(ToolError, match="authentication"):
                await client.call_tool("journal_read", {"project": "bramble"})

    async def test_unauthenticated_server_runs_without_a_gate(
        self, server: JournalMCPServer
    ) -> None:
        # No hooks (stdio): tool calls work without any token.
        async with Client(server.app) as client:
            result = await client.call_tool("journal_list_projects", {})
        assert result.data == []
