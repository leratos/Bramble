"""Unit tests for :class:`bramble.journal_db.JournalDB`."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bramble.journal_context import JournalContext
from bramble.journal_db import JournalDB
from bramble.journal_digest import JournalDigest
from bramble.journal_entry import JournalEntry, JournalEntryLink, JournalStatus
from bramble.open_item import (
    REASON_LINK,
    REASON_PHASE,
    REASON_TEXT,
    STATE_OPEN,
    STATE_RESOLVED,
    STATE_STALE,
)
from bramble.project_summary import ProjectSummary


def _entry(
    project: str = "bramble",
    *,
    content: str = "hello",
    status: JournalStatus = JournalStatus.NOTIZ,
    phase: str | None = None,
    title: str | None = None,
    timestamp: datetime | None = None,
) -> JournalEntry:
    return JournalEntry(
        project=project,
        status=status,
        content=content,
        phase=phase,
        title=title,
        timestamp=timestamp or datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# Construction & initialisation
# ---------------------------------------------------------------------------
class TestJournalDBInit:
    def test_initialize_is_idempotent(self, db_path: Path) -> None:
        db = JournalDB(db_path)
        db.initialize()
        db.initialize()  # must not raise

    def test_initialize_migrates_entry_links_relation_check(
        self,
        db_path: Path,
    ) -> None:
        # Simulate a pre-Phase-4f database whose journal_entry_links CHECK
        # constraint predates the 'resolves' relation.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE journal_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL, timestamp TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT, title TEXT,
                    content TEXT NOT NULL, actor TEXT, client TEXT, source TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE journal_entry_links (
                    from_entry_id INTEGER NOT NULL
                        REFERENCES journal_entries(id) ON DELETE CASCADE,
                    to_entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
                    relation TEXT NOT NULL CHECK (
                        relation IN (
                            'corrects', 'adds_context_to', 'supersedes',
                            'implements', 'relates_to'
                        )
                    ),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (from_entry_id, to_entry_id, relation)
                )
                """
            )
            conn.execute(
                "INSERT INTO journal_entries (project, timestamp, status, content) "
                "VALUES ('bramble', '2026-05-01T12:00:00+00:00', 'in_arbeit', 'open')"
            )
            conn.execute(
                "INSERT INTO journal_entries (project, timestamp, status, content) "
                "VALUES ('bramble', '2026-05-02T12:00:00+00:00', 'abgeschlossen', 'done')"
            )
            conn.execute(
                "INSERT INTO journal_entry_links "
                "(from_entry_id, to_entry_id, relation, created_at) "
                "VALUES (2, 1, 'supersedes', '2026-05-02T12:00:00+00:00')"
            )
            # The pre-migration CHECK must reject 'resolves'.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO journal_entry_links "
                    "(from_entry_id, to_entry_id, relation, created_at) "
                    "VALUES (2, 1, 'resolves', 'x')"
                )
            conn.commit()
        finally:
            conn.close()

        db = JournalDB(db_path)
        db.initialize()

        # The existing link row survives the table rebuild.
        with sqlite3.connect(db_path) as verify:
            rows = verify.execute(
                "SELECT from_entry_id, to_entry_id, relation "
                "FROM journal_entry_links"
            ).fetchall()
        assert (2, 1, "supersedes") in rows

        # 'resolves' is now accepted through the public append path.
        resolved = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.ABGESCHLOSSEN,
                content="resolve the open entry",
                links=[{"to_entry_id": 1, "relation": "resolves"}],
            )
        )
        assert resolved.id is not None

    def test_initialize_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "deeper" / "bramble.db"
        db = JournalDB(nested)
        db.initialize()
        assert nested.exists()

    def test_db_path_accepts_str(self, tmp_path: Path) -> None:
        str_path = str(tmp_path / "via-str.db")
        db = JournalDB(str_path)
        db.initialize()
        assert db.db_path == Path(str_path)

    def test_constructor_rejects_non_path(self) -> None:
        with pytest.raises(TypeError):
            JournalDB(123)  # type: ignore[arg-type]

    def test_initialize_enables_wal_mode(self, db_path: Path) -> None:
        JournalDB(db_path).initialize()
        # WAL is persisted in the file header, so a fresh connection
        # sees it (Phase-3 Decision I).
        with sqlite3.connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"

    def test_initialize_wal_survives_reinitialise(self, db_path: Path) -> None:
        JournalDB(db_path).initialize()
        JournalDB(db_path).initialize()  # idempotent, still WAL
        with sqlite3.connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"

    def test_initialize_migrates_existing_entry_projects_to_registry(
        self, db_path: Path
    ) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE journal_entries (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    project   TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status    TEXT NOT NULL,
                    phase     TEXT,
                    title     TEXT,
                    content   TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO journal_entries
                    (project, timestamp, status, phase, title, content)
                VALUES
                    ('bramble', '2026-05-12T08:00:00+00:00', 'notiz', NULL, NULL, 'b1'),
                    ('bramble', '2026-05-12T08:05:00+00:00', 'notiz', NULL, NULL, 'b2'),
                    ('elder-berry', '2026-05-12T08:10:00+00:00', 'notiz', NULL, NULL, 'e1')
                """
            )
            conn.commit()

        db = JournalDB(db_path)
        db.initialize()

        overview = {summary.name: summary for summary in db.project_overview()}
        assert set(overview) == {"bramble", "elder-berry"}
        assert overview["bramble"].entry_count == 2
        assert overview["bramble"].last_timestamp == datetime(
            2026, 5, 12, 8, 5, tzinfo=UTC
        )
        assert overview["elder-berry"].entry_count == 1

    def test_initialize_adds_metadata_columns_to_legacy_entries_table(
        self, db_path: Path
    ) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE journal_entries (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    project   TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status    TEXT NOT NULL,
                    phase     TEXT,
                    title     TEXT,
                    content   TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO journal_entries
                    (project, timestamp, status, phase, title, content)
                VALUES
                    ('bramble', '2026-05-12T08:00:00+00:00', 'notiz', NULL, NULL, 'legacy')
                """
            )
            conn.commit()

        db = JournalDB(db_path)
        db.initialize()

        with sqlite3.connect(db_path) as conn:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(journal_entries)")
            }
        [entry] = db.read("bramble")
        assert {"actor", "client", "source"} <= columns
        assert entry.actor is None
        assert entry.client is None
        assert entry.source is None

    def test_initialize_creates_tag_tables(self, db: JournalDB) -> None:
        with sqlite3.connect(db.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert {"journal_tags", "journal_entry_tags"} <= tables

    def test_initialize_creates_link_table(self, db: JournalDB) -> None:
        with sqlite3.connect(db.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert "journal_entry_links" in tables

    def test_initialize_creates_open_items_status_index(self, db: JournalDB) -> None:
        with sqlite3.connect(db.db_path) as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
        assert "idx_status_ts" in indexes


# ---------------------------------------------------------------------------
# append()
# ---------------------------------------------------------------------------
class TestAppend:
    def test_append_returns_entry_with_id(self, db: JournalDB) -> None:
        result = db.append(_entry())
        assert result.id is not None
        assert result.id > 0

    def test_ids_are_monotonically_increasing(self, db: JournalDB) -> None:
        first = db.append(_entry(content="a"))
        second = db.append(_entry(content="b"))
        assert second.id is not None and first.id is not None
        assert second.id > first.id

    def test_append_rejects_entry_with_existing_id(self, db: JournalDB) -> None:
        persisted = db.append(_entry())
        with pytest.raises(ValueError, match="append-only"):
            db.append(persisted)

    def test_append_rejects_non_entry(self, db: JournalDB) -> None:
        with pytest.raises(TypeError):
            db.append("not an entry")  # type: ignore[arg-type]

    def test_append_registers_project(self, db: JournalDB) -> None:
        db.append(_entry(project="berry-gym"))
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT name, status FROM projects WHERE name = 'berry-gym'"
            ).fetchone()
        assert row == ("berry-gym", "active")


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------
class TestRead:
    def test_read_empty_project_returns_empty_list(self, db: JournalDB) -> None:
        assert db.read("nope") == []

    def test_read_returns_newest_first(self, db: JournalDB) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        for i in range(3):
            db.append(
                _entry(
                    content=f"entry-{i}",
                    timestamp=base + timedelta(minutes=i),
                )
            )
        rows = db.read("bramble")
        assert [r.content for r in rows] == ["entry-2", "entry-1", "entry-0"]

    def test_read_respects_n(self, db: JournalDB) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        for i in range(5):
            db.append(
                _entry(
                    content=f"e{i}",
                    timestamp=base + timedelta(minutes=i),
                )
            )
        assert len(db.read("bramble", n=2)) == 2

    def test_read_isolates_by_project(self, db: JournalDB) -> None:
        db.append(_entry(project="bramble", content="b"))
        db.append(_entry(project="elder-berry", content="e"))
        assert [r.content for r in db.read("bramble")] == ["b"]
        assert [r.content for r in db.read("elder-berry")] == ["e"]

    def test_read_breaks_timestamp_ties_by_id(self, db: JournalDB) -> None:
        same_ts = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        first = db.append(_entry(content="first", timestamp=same_ts))
        second = db.append(_entry(content="second", timestamp=same_ts))
        rows = db.read("bramble")
        assert rows[0].id == second.id
        assert rows[1].id == first.id

    @pytest.mark.parametrize("bad_n", [0, -1])
    def test_read_rejects_non_positive_n(self, db: JournalDB, bad_n: int) -> None:
        with pytest.raises(ValueError):
            db.read("bramble", n=bad_n)

    def test_read_rejects_non_int_n(self, db: JournalDB) -> None:
        with pytest.raises(TypeError):
            db.read("bramble", n="10")  # type: ignore[arg-type]

    def test_read_rejects_bool_n(self, db: JournalDB) -> None:
        # bool is a subclass of int; the API should not silently
        # accept ``True`` as ``1``.
        with pytest.raises(TypeError):
            db.read("bramble", n=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------
class TestSearch:
    def test_search_finds_word_in_content(self, db: JournalDB) -> None:
        db.append(_entry(content="we fixed a flaky test today"))
        db.append(_entry(content="unrelated content"))
        hits = db.search("bramble", "flaky")
        assert len(hits) == 1
        assert "flaky" in hits[0].content

    def test_search_finds_word_in_title(self, db: JournalDB) -> None:
        db.append(_entry(title="Deployment Notes", content="body without keyword"))
        db.append(_entry(title="Other", content="other body"))
        hits = db.search("bramble", "Deployment")
        assert len(hits) == 1
        assert hits[0].title == "Deployment Notes"

    def test_search_respects_project_isolation(self, db: JournalDB) -> None:
        db.append(_entry(project="bramble", content="needle here"))
        db.append(_entry(project="elder-berry", content="needle here too"))
        hits = db.search("bramble", "needle")
        assert len(hits) == 1
        assert hits[0].project == "bramble"

    def test_search_respects_limit(self, db: JournalDB) -> None:
        for i in range(5):
            db.append(_entry(content=f"keyword variant {i}"))
        assert len(db.search("bramble", "keyword", limit=2)) == 2

    def test_search_returns_empty_on_bad_fts_syntax(self, db: JournalDB) -> None:
        db.append(_entry(content="something"))
        # Unbalanced quote is invalid FTS5 syntax.
        assert db.search("bramble", '"open quote') == []

    def test_search_returns_empty_when_no_match(self, db: JournalDB) -> None:
        db.append(_entry(content="only this"))
        assert db.search("bramble", "absent") == []

    def test_search_rejects_empty_query(self, db: JournalDB) -> None:
        with pytest.raises(ValueError):
            db.search("bramble", "   ")

    def test_search_reflects_delete_via_trigger(self, db: JournalDB) -> None:
        # Bramble is append-only via API, but the delete trigger
        # exists. Verify it keeps the FTS index in sync if a row is
        # deleted directly.
        persisted = db.append(_entry(content="ephemeral entry"))
        import sqlite3

        with sqlite3.connect(db.db_path) as conn:
            conn.execute("DELETE FROM journal_entries WHERE id = ?", (persisted.id,))
            conn.commit()
        assert db.search("bramble", "ephemeral") == []


# ---------------------------------------------------------------------------
# search_all()
# ---------------------------------------------------------------------------
class TestSearchAll:
    def test_search_all_finds_matches_across_projects(self, db: JournalDB) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(
            _entry(
                project="bramble",
                content="deployment keyword in bramble",
                timestamp=base,
            )
        )
        db.append(
            _entry(
                project="elder-berry",
                content="deployment keyword in elder",
                timestamp=base + timedelta(minutes=1),
            )
        )
        db.append(_entry(project="berry-gym", content="unrelated"))

        hits = db.search_all("deployment")

        assert [entry.project for entry in hits] == ["elder-berry", "bramble"]

    def test_search_all_filters_projects(self, db: JournalDB) -> None:
        db.append(_entry(project="bramble", content="needle in bramble"))
        db.append(_entry(project="elder-berry", content="needle in elder"))

        hits = db.search_all("needle", projects=["bramble"])

        assert [entry.project for entry in hits] == ["bramble"]

    def test_search_all_filters_statuses_and_tags(self, db: JournalDB) -> None:
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

        hits = db.search_all(
            "release",
            statuses=[JournalStatus.BUGFIX],
            tags=["Deploy", "hotfix"],
        )

        assert len(hits) == 1
        assert hits[0].project == "elder-berry"
        assert hits[0].tags == ("deploy", "hotfix")

    def test_search_all_respects_limit(self, db: JournalDB) -> None:
        for i in range(5):
            db.append(_entry(project=f"project-{i}", content=f"keyword {i}"))

        assert len(db.search_all("keyword", limit=2)) == 2

    def test_search_all_caps_limit(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            db.search_all("keyword", limit=101)

    def test_search_all_returns_empty_on_bad_fts_syntax(self, db: JournalDB) -> None:
        db.append(_entry(content="something"))

        assert db.search_all('"open quote') == []

    def test_search_all_rejects_empty_query(self, db: JournalDB) -> None:
        with pytest.raises(ValueError):
            db.search_all("   ")

    def test_search_all_rejects_invalid_status_filter(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="status"):
            db.search_all("keyword", statuses=["done"])


# ---------------------------------------------------------------------------
# digest()
# ---------------------------------------------------------------------------
class TestDigest:
    def test_digest_aggregates_range_counts_and_categories(
        self, db: JournalDB
    ) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        decision = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                title="Decision: keep FTS",
                content="decision payload",
                tags=["decision"],
                timestamp=now - timedelta(hours=1),
            )
        )
        bugfix = db.append(
            _entry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                content="bug fixed",
                timestamp=now - timedelta(hours=2),
            )
        )
        open_item = db.append(
            _entry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="open work",
                timestamp=now - timedelta(hours=3),
            )
        )
        db.append(
            _entry(
                project="berry-gym",
                content="old work",
                timestamp=now - timedelta(days=40),
            )
        )

        digest = db.digest(since="7d", now=now)

        assert isinstance(digest, JournalDigest)
        assert digest.range_since == now - timedelta(days=7)
        assert digest.range_until == now
        assert digest.projects == ("bramble", "elder-berry")
        assert digest.counts_by_project == {"bramble": 2, "elder-berry": 1}
        assert digest.counts_by_status == {
            "bugfix": 1,
            "in_arbeit": 1,
            "notiz": 1,
        }
        assert [entry.id for entry in digest.entries] == [
            decision.id,
            bugfix.id,
            open_item.id,
        ]
        assert [entry.id for entry in digest.open_items] == [open_item.id]
        assert [entry.id for entry in digest.bugfixes] == [bugfix.id]
        assert [entry.id for entry in digest.decisions] == [decision.id]

    def test_digest_filters_project_and_tags(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="deploy payload",
                tags=["deploy", "decision"],
                timestamp=now - timedelta(hours=1),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="untagged payload",
                timestamp=now - timedelta(hours=2),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content="deploy elsewhere",
                tags=["deploy"],
                timestamp=now - timedelta(hours=3),
            )
        )

        digest = db.digest(project="bramble", since="24h", tags=["Deploy"], now=now)

        assert [entry.content for entry in digest.entries] == ["deploy payload"]
        assert digest.counts_by_project == {"bramble": 1}

    def test_digest_accepts_iso_range(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        db.append(_entry(content="inside", timestamp=now - timedelta(minutes=30)))
        db.append(_entry(content="outside", timestamp=now - timedelta(hours=3)))

        digest = db.digest(
            since="2026-05-29T11:00:00+00:00",
            until="2026-05-29T12:00:00+00:00",
            now=now,
        )

        assert [entry.content for entry in digest.entries] == ["inside"]

    def test_digest_respects_limit(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        for i in range(5):
            db.append(
                _entry(
                    content=f"entry-{i}",
                    timestamp=now - timedelta(minutes=i),
                )
            )

        digest = db.digest(since="24h", limit=2, now=now)

        assert len(digest.entries) == 2
        assert digest.counts_by_project == {"bramble": 5}

    def test_digest_rejects_invalid_since(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="since"):
            db.digest(since="yesterday")

    def test_digest_rejects_until_before_since(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="until"):
            db.digest(
                since="2026-05-29T12:00:00+00:00",
                until="2026-05-29T11:00:00+00:00",
            )

    def test_digest_caps_limit(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            db.digest(limit=101)


# ---------------------------------------------------------------------------
# open_items()
# ---------------------------------------------------------------------------
class TestOpenItems:
    def test_open_items_returns_newest_in_arbeit_entries(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="open-old",
                timestamp=now - timedelta(hours=2),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="closed",
                timestamp=now - timedelta(hours=1),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="open-new",
                timestamp=now,
            )
        )

        result = db.open_items(limit=10)

        assert [entry.content for entry in result] == ["open-new", "open-old"]
        assert all(entry.status is JournalStatus.IN_ARBEIT for entry in result)

    def test_open_items_filters_by_project(self, db: JournalDB) -> None:
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

        result = db.open_items(project="bramble", limit=10)

        assert [entry.content for entry in result] == ["open bramble"]

    def test_open_items_respects_limit(self, db: JournalDB) -> None:
        for i in range(5):
            db.append(
                JournalEntry(
                    project="bramble",
                    status=JournalStatus.IN_ARBEIT,
                    content=f"open-{i}",
                    timestamp=datetime(2026, 5, 29, 12, i, tzinfo=UTC),
                )
            )

        result = db.open_items(limit=2)

        assert len(result) == 2
        assert [entry.content for entry in result] == ["open-4", "open-3"]

    def test_open_item_count_is_not_limited(self, db: JournalDB) -> None:
        for i in range(12):
            db.append(
                JournalEntry(
                    project="berry-gym",
                    status=JournalStatus.IN_ARBEIT,
                    content=f"open-{i}",
                    timestamp=datetime(2026, 5, 29, 12, i, tzinfo=UTC),
                )
            )

        result = db.open_items(project="berry-gym", limit=10)

        assert len(result) == 10
        assert db.open_item_count(project="berry-gym") == 12

    def test_open_items_returns_empty_for_no_open_entries(self, db: JournalDB) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="note",
            )
        )

        assert db.open_items(limit=10) == []

    def test_open_items_breaks_timestamp_ties_by_id(self, db: JournalDB) -> None:
        same_ts = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        first = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="first",
                timestamp=same_ts,
            )
        )
        second = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="second",
                timestamp=same_ts,
            )
        )

        rows = db.open_items(limit=10)

        assert rows[0].id == second.id
        assert rows[1].id == first.id

    def test_open_items_rejects_limit_above_cap(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            db.open_items(limit=101)

    def test_open_items_rejects_non_positive_limit(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="positive"):
            db.open_items(limit=0)

    def test_open_items_excludes_entries_closed_via_link_relation(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="work in progress",
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                content="closed by explicit link",
                links=(
                    JournalEntryLink(
                        entry_id=open_entry.id,
                        relation="supersedes",
                    ),
                ),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert result == []

    def test_open_items_excludes_entries_closed_via_resolves_link(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="work in progress",
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                content="closed by explicit resolves link",
                links=(
                    JournalEntryLink(
                        entry_id=open_entry.id,
                        relation="resolves",
                    ),
                ),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert result == []

    def test_open_items_excludes_entries_closed_via_textual_id_mapping(
        self,
        db: JournalDB,
    ) -> None:
        open_a = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="slice A",
            )
        )
        open_b = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="slice B",
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content=(
                    "Open-Items-Abgleich\n"
                    f"- #{open_a.id} -> #999\n"
                    "- #12345 -> #12346"
                ),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert [entry.id for entry in result] == [open_b.id]

    def test_open_items_applies_limit_after_closure_filtering(
        self,
        db: JournalDB,
    ) -> None:
        open_entries = [
            db.append(
                JournalEntry(
                    project="bramble",
                    status=JournalStatus.IN_ARBEIT,
                    content=f"open-{index}",
                    timestamp=datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
                    - timedelta(minutes=index),
                )
            )
            for index in range(11)
        ]
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content=(
                    "Open-Items-Abgleich\n"
                    f"- #{open_entries[0].id} -> #999\n"
                    f"- #{open_entries[1].id} -> #999"
                ),
            )
        )

        result = db.open_items(project="bramble", limit=10)

        assert len(result) == 9
        assert [entry.content for entry in result] == [
            "open-2",
            "open-3",
            "open-4",
            "open-5",
            "open-6",
            "open-7",
            "open-8",
            "open-9",
            "open-10",
        ]

    def test_open_items_excludes_entries_closed_by_newer_same_phase_entry(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                phase="phase-95",
                content="E4 Start: Handler-Gates vorbereiten",
                timestamp=datetime(2026, 5, 29, 9, 59, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                phase="Phase 95",
                content="Phase 95 ist formal abgeschlossen",
                timestamp=datetime(2026, 5, 29, 15, 3, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert result == []
        assert open_entry.id is not None

    def test_open_items_keeps_entries_open_for_different_phase(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                phase="phase-95",
                content="open phase 95 task",
                timestamp=datetime(2026, 5, 29, 10, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                phase="phase-96",
                content="phase 96 abgeschlossen",
                timestamp=datetime(2026, 5, 29, 15, 0, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert [entry.id for entry in result] == [open_entry.id]

    def test_open_items_excludes_entries_closed_by_newer_same_title_entry(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                title="Konzept-Phase Note-Nextcloud-Replace",
                content="start",
                timestamp=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                title="Konzept-Phase Note-Nextcloud-Replace",
                content="abgeschlossen",
                timestamp=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert result == []
        assert open_entry.id is not None

    def test_open_items_keeps_entries_open_for_different_title(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                title="Konzept-Phase Note-Nextcloud-Replace",
                content="start",
                timestamp=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                title="Andere Abschluss-Phase",
                content="abgeschlossen",
                timestamp=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert [entry.id for entry in result] == [open_entry.id]

    def test_open_items_excludes_entries_closed_by_title_with_punctuation_variants(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                title="Hotfix -- Tower-Update + Self-Respawn",
                content="start",
                timestamp=datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                title="Hotfix Tower-Update + Self-Respawn",
                content="done",
                timestamp=datetime(2026, 5, 29, 12, 1, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert result == []
        assert open_entry.id is not None

    def test_open_items_excludes_entries_closed_by_base_title(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                title="Bugfix RPi5-Display-Rotation -- Saleria steht auf Kopf",
                content="start",
                timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                title="Bugfix RPi5-Display-Rotation",
                content="done",
                timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert result == []
        assert open_entry.id is not None

    def test_open_items_keeps_entries_open_for_generic_base_title(
        self,
        db: JournalDB,
    ) -> None:
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                title="Hotfix -- Tower-Update + Self-Respawn",
                content="start",
                timestamp=datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                title="Hotfix",
                content="done",
                timestamp=datetime(2026, 5, 29, 12, 1, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert [entry.id for entry in result] == [open_entry.id]


# ---------------------------------------------------------------------------
# open_items_view()
# ---------------------------------------------------------------------------
class TestOpenItemsView:
    def test_classifies_open_and_stale_by_cutoff(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        recent = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="recent",
                timestamp=now - timedelta(days=3),
            )
        )
        old = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="old",
                timestamp=now - timedelta(days=40),
            )
        )

        views = db.open_items_view(project="bramble", now=now)
        by_id = {view.entry.id: view for view in views}

        assert by_id[recent.id].state == STATE_OPEN
        assert by_id[recent.id].age_days == 3
        assert by_id[old.id].state == STATE_STALE
        assert by_id[old.id].age_days == 40

        tighter = db.open_items_view(project="bramble", stale_after_days=2, now=now)
        assert {v.entry.id: v.state for v in tighter} == {
            recent.id: STATE_STALE,
            old.id: STATE_STALE,
        }

    def test_resolved_via_link_carries_provenance(self, db: JournalDB) -> None:
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

        # Resolved items are hidden by default.
        assert db.open_items_view(project="elder-berry") == []

        views = db.open_items_view(project="elder-berry", include_resolved=True)
        assert len(views) == 1
        view = views[0]
        assert view.entry.id == open_entry.id
        assert view.state == STATE_RESOLVED
        assert view.resolution_reason == REASON_LINK
        assert view.resolved_by_id == closer.id

    def test_resolved_via_text_mapping_carries_provenance(
        self,
        db: JournalDB,
    ) -> None:
        open_a = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="slice A",
            )
        )
        mapper = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content=f"Open-Items-Abgleich\n- #{open_a.id} -> #999",
            )
        )

        views = db.open_items_view(project="elder-berry", include_resolved=True)
        view = next(v for v in views if v.entry.id == open_a.id)
        assert view.state == STATE_RESOLVED
        assert view.resolution_reason == REASON_TEXT
        assert view.resolved_by_id == mapper.id

    def test_phase_heuristic_ignores_notiz_but_honors_abgeschlossen(
        self,
        db: JournalDB,
    ) -> None:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                phase="Phase 9",
                content="start",
                timestamp=now - timedelta(hours=2),
            )
        )
        # A notiz sharing the phase must NOT close the item.
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                phase="Phase 9",
                content="just a note",
                timestamp=now - timedelta(hours=1),
            )
        )

        views = db.open_items_view(
            project="elder-berry", include_resolved=True, now=now
        )
        assert [v.state for v in views] == [STATE_OPEN]

        # An abgeschlossen sharing the phase DOES close it, with provenance.
        closer = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                phase="Phase 9",
                content="phase done",
                timestamp=now,
            )
        )
        view = db.open_items_view(
            project="elder-berry", include_resolved=True, now=now
        )[0]
        assert view.state == STATE_RESOLVED
        assert view.resolution_reason == REASON_PHASE
        assert view.resolved_by_id == closer.id

    def test_explicit_link_reason_beats_phase_heuristic(
        self,
        db: JournalDB,
    ) -> None:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                phase="Phase 12",
                content="start",
                timestamp=now - timedelta(hours=2),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                phase="Phase 12",
                content="phase done",
                timestamp=now - timedelta(hours=1),
            )
        )
        resolver = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.ABGESCHLOSSEN,
                content="explicit resolve",
                links=(
                    JournalEntryLink(entry_id=open_entry.id, relation="resolves"),
                ),
                timestamp=now,
            )
        )

        view = db.open_items_view(
            project="elder-berry", include_resolved=True, now=now
        )[0]
        assert view.resolution_reason == REASON_LINK
        assert view.resolved_by_id == resolver.id

    def test_open_items_keeps_item_when_only_notiz_shares_phase(
        self,
        db: JournalDB,
    ) -> None:
        # Regression for the heuristic tightening: a notiz no longer closes
        # work via the phase path, so the legacy open_items() keeps the item.
        open_entry = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                phase="Phase 50",
                content="start",
                timestamp=datetime(2026, 5, 29, 10, 0, tzinfo=UTC),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                phase="Phase 50",
                content="note in same phase",
                timestamp=datetime(2026, 5, 29, 11, 0, tzinfo=UTC),
            )
        )

        result = db.open_items(project="elder-berry", limit=10)

        assert [entry.id for entry in result] == [open_entry.id]

    def test_rejects_non_positive_stale_after_days(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="positive"):
            db.open_items_view(stale_after_days=0)

    def test_rejects_non_bool_include_resolved(self, db: JournalDB) -> None:
        with pytest.raises(TypeError):
            db.open_items_view(include_resolved="yes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# context()
# ---------------------------------------------------------------------------
class TestContext:
    def test_context_returns_curated_structure(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
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

        context = db.context("bramble", n_recent=2)

        assert isinstance(context, JournalContext)
        assert context.project == "bramble"
        assert len(context.recent) == 2
        assert [view.entry.status.value for view in context.open_items] == [
            "in_arbeit"
        ]
        assert [entry.status.value for entry in context.recent_bugfixes] == ["bugfix"]
        assert [entry.title for entry in context.recent_decisions] == [
            "Decision: keep context deterministic"
        ]
        assert "elder-berry" in context.related_projects
        assert "Phase 4d" in context.suggested_searches
        assert "deployment" in context.suggested_searches

    def test_context_open_items_use_closure_inference_not_30d_window(
        self,
        db: JournalDB,
    ) -> None:
        # The old context slice used digest.open_items (raw in_arbeit within
        # the 30-day window): it over-reported resolved items inside the
        # window and dropped genuinely-open items older than 30 days. The
        # unified slice fixes both.
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        started = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                phase="Phase 1",
                content="phase 1 start (resolved, but inside 30d window)",
                timestamp=now - timedelta(days=10),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.ABGESCHLOSSEN,
                phase="Phase 1",
                content="phase 1 done",
                timestamp=now - timedelta(days=9),
            )
        )
        genuinely_open = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                phase="Phase 2",
                content="phase 2 still open, older than 30 days",
                timestamp=now - timedelta(days=60),
            )
        )

        context = db.context("bramble", n_recent=10, include_cross_project=False)
        ids = [view.entry.id for view in context.open_items]

        assert started.id not in ids  # resolved -> excluded
        assert genuinely_open.id in ids  # >30d but unresolved -> still shown

    def test_context_empty_project_returns_empty_lists(self, db: JournalDB) -> None:
        context = db.context("berry-gym")

        assert context == JournalContext(
            project="berry-gym",
            recent=(),
            open_items=(),
            recent_bugfixes=(),
            recent_decisions=(),
            related_projects=(),
            suggested_searches=(),
        )

    def test_context_can_disable_cross_project_lookup(self, db: JournalDB) -> None:
        now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
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

        context = db.context("bramble", include_cross_project=False)

        assert context.related_projects == ()
        assert context.suggested_searches == ("Phase 4d", "deployment")

    def test_context_rejects_non_positive_n_recent(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="positive"):
            db.context("bramble", n_recent=0)

    def test_context_rejects_n_recent_above_cap(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            db.context("bramble", n_recent=101)

    def test_context_rejects_non_bool_include_cross_project(
        self, db: JournalDB
    ) -> None:
        with pytest.raises(TypeError, match="include_cross_project"):
            db.context("bramble", include_cross_project=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# project_overview()
# ---------------------------------------------------------------------------
class TestProjectOverview:
    def test_empty_db_returns_empty_list(self, db: JournalDB) -> None:
        assert db.project_overview() == []

    def test_registered_project_without_entries_is_listed(self, db: JournalDB) -> None:
        db.register_project("berry-gym")

        [summary] = db.project_overview()
        assert summary.name == "berry-gym"
        assert summary.entry_count == 0
        assert summary.last_timestamp is None
        assert summary.last_timestamp_iso() is None

    def test_project_status_can_be_read_and_updated(self, db: JournalDB) -> None:
        db.register_project("berry-gym")

        assert db.project_status("berry-gym") == "active"

        db.set_project_status("berry-gym", "paused")

        assert db.project_status("berry-gym") == "paused"
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT status, archived_at FROM projects WHERE name = 'berry-gym'"
            ).fetchone()
        assert row == ("paused", None)

        db.set_project_status("berry-gym", "archived")

        assert db.project_status("berry-gym") == "archived"
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT status, archived_at FROM projects WHERE name = 'berry-gym'"
            ).fetchone()
        assert row[0] == "archived"
        assert row[1] is not None

    def test_counts_and_last_timestamp_per_project(self, db: JournalDB) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(_entry(project="bramble", content="b1", timestamp=base))
        db.append(
            _entry(
                project="bramble",
                content="b2",
                timestamp=base + timedelta(minutes=5),
            )
        )
        db.append(
            _entry(
                project="elder-berry",
                content="e1",
                timestamp=base + timedelta(minutes=10),
            )
        )

        overview = {s.name: s for s in db.project_overview()}
        assert overview["bramble"].entry_count == 2
        assert overview["bramble"].last_timestamp == base + timedelta(minutes=5)
        assert overview["elder-berry"].entry_count == 1
        assert overview["elder-berry"].last_timestamp == base + timedelta(minutes=10)

    def test_sorted_by_last_timestamp_desc(self, db: JournalDB) -> None:
        base = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(_entry(project="oldest", content="o", timestamp=base))
        db.append(
            _entry(
                project="middle",
                content="m",
                timestamp=base + timedelta(hours=1),
            )
        )
        db.append(
            _entry(
                project="newest",
                content="n",
                timestamp=base + timedelta(hours=2),
            )
        )
        names = [s.name for s in db.project_overview()]
        assert names == ["newest", "middle", "oldest"]

    def test_ties_broken_alphabetically(self, db: JournalDB) -> None:
        same_ts = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(_entry(project="charlie", content="c", timestamp=same_ts))
        db.append(_entry(project="alpha", content="a", timestamp=same_ts))
        db.append(_entry(project="bravo", content="b", timestamp=same_ts))
        names = [s.name for s in db.project_overview()]
        assert names == ["alpha", "bravo", "charlie"]

    def test_timestamps_are_utc_datetimes(self, db: JournalDB) -> None:
        ts = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        db.append(_entry(project="bramble", content="x", timestamp=ts))
        [summary] = db.project_overview()
        assert isinstance(summary, ProjectSummary)
        assert summary.last_timestamp is not None
        assert summary.last_timestamp.tzinfo is not None
        assert summary.last_timestamp.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------
class TestRoundTrip:
    def test_all_fields_survive_round_trip(self, db: JournalDB) -> None:
        ts = datetime(2026, 5, 12, 9, 15, tzinfo=UTC)
        original = _entry(
            project="elder-berry",
            content="full payload",
            status=JournalStatus.ABGESCHLOSSEN,
            phase="Phase 2",
            title="Done",
            timestamp=ts,
        )
        persisted = db.append(original)
        [restored] = db.read("elder-berry")

        assert restored.id == persisted.id
        assert restored.project == "elder-berry"
        assert restored.content == "full payload"
        assert restored.status is JournalStatus.ABGESCHLOSSEN
        assert restored.phase == "Phase 2"
        assert restored.title == "Done"
        assert restored.timestamp == ts

    def test_metadata_fields_survive_round_trip(self, db: JournalDB) -> None:
        original = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="metadata payload",
            actor="codex",
            client="codex-desktop",
            source="mcp",
        )
        db.append(original)

        [restored] = db.read("bramble")

        assert restored.actor == "codex"
        assert restored.client == "codex-desktop"
        assert restored.source == "mcp"

    def test_tags_survive_round_trip(self, db: JournalDB) -> None:
        original = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="tagged payload",
            tags=["test", "admin-ui", "test"],
        )
        persisted = db.append(original)

        [restored] = db.read("bramble")
        hits = db.search("bramble", "tagged")

        assert restored.id == persisted.id
        assert restored.tags == ("admin-ui", "test")
        assert hits[0].tags == ("admin-ui", "test")
        with sqlite3.connect(db.db_path) as conn:
            stored_tags = {
                row[0]
                for row in conn.execute("SELECT name FROM journal_tags")
            }
        assert stored_tags == {"admin-ui", "test"}

    def test_links_create_outgoing_and_incoming_views(self, db: JournalDB) -> None:
        original = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="old context",
            )
        )
        followup = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="corrected context",
                links=[{"to_entry_id": original.id, "relation": "corrects"}],
            )
        )

        entries = {entry.id: entry for entry in db.read("bramble")}

        assert entries[followup.id].links == (
            JournalEntryLink(entry_id=original.id, relation="corrects"),
        )
        assert entries[original.id].backlinks == (
            JournalEntryLink(entry_id=followup.id, relation="corrects"),
        )

    def test_link_target_must_exist_and_insert_rolls_back(self, db: JournalDB) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            db.append(
                JournalEntry(
                    project="bramble",
                    status=JournalStatus.BUGFIX,
                    content="broken link",
                    links=[{"to_entry_id": 999, "relation": "corrects"}],
                )
            )

        assert db.read("bramble") == []
