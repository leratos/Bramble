"""Unit tests for :class:`bramble.journal_db.JournalDB`."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus
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
