"""Unit tests for :mod:`bramble.journal_entry`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from bramble.journal_entry import JournalEntry, JournalStatus


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestJournalEntryConstruction:
    def test_minimal_entry_uses_defaults(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="hello world",
        )

        assert entry.project == "bramble"
        assert entry.status is JournalStatus.NOTIZ
        assert entry.content == "hello world"
        assert entry.phase is None
        assert entry.title is None
        assert entry.id is None
        assert entry.timestamp.tzinfo is not None
        assert entry.timestamp.utcoffset() == timedelta(0)

    def test_full_entry_round_trips_values(self) -> None:
        ts = datetime(2026, 5, 12, 8, 30, tzinfo=UTC)
        entry = JournalEntry(
            project="elder-berry",
            status=JournalStatus.IN_ARBEIT,
            content="kickoff",
            phase="Phase 1",
            title="Start",
            actor="codex",
            client="codex-desktop",
            source="mcp",
            timestamp=ts,
        )

        assert entry.project == "elder-berry"
        assert entry.phase == "Phase 1"
        assert entry.title == "Start"
        assert entry.actor == "codex"
        assert entry.client == "codex-desktop"
        assert entry.source == "mcp"
        assert entry.timestamp == ts
        assert entry.timestamp_iso() == "2026-05-12T08:30:00+00:00"

    def test_status_accepts_string_and_normalises(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status="bugfix",
            content="fix typo",
        )
        assert entry.status is JournalStatus.BUGFIX

    def test_entry_is_frozen(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="immutable",
        )
        with pytest.raises(Exception):  # FrozenInstanceError is a subclass
            entry.project = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestJournalEntryValidation:
    @pytest.mark.parametrize("bad_project", ["", "   ", "\t\n"])
    def test_empty_project_is_rejected(self, bad_project: str) -> None:
        with pytest.raises(ValueError, match="project must not be empty"):
            JournalEntry(
                project=bad_project,
                status=JournalStatus.NOTIZ,
                content="x",
            )

    def test_project_is_stripped(self) -> None:
        entry = JournalEntry(
            project="  bramble  ",
            status=JournalStatus.NOTIZ,
            content="x",
        )
        assert entry.project == "bramble"

    def test_non_string_project_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            JournalEntry(
                project=123,  # type: ignore[arg-type]
                status=JournalStatus.NOTIZ,
                content="x",
            )

    def test_unknown_status_string_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            JournalEntry(
                project="bramble",
                status="erfunden",
                content="x",
            )

    def test_status_wrong_type_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            JournalEntry(
                project="bramble",
                status=42,  # type: ignore[arg-type]
                content="x",
            )

    @pytest.mark.parametrize("bad_content", ["", "   ", "\n\t"])
    def test_empty_content_is_rejected(self, bad_content: str) -> None:
        with pytest.raises(ValueError, match="content must not be empty"):
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content=bad_content,
            )

    def test_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="x",
                timestamp=datetime(2026, 5, 12, 8, 0),  # naive
            )

    def test_non_utc_timestamp_is_normalised_to_utc(self) -> None:
        cet = timezone(timedelta(hours=2))
        ts = datetime(2026, 5, 12, 10, 0, tzinfo=cet)  # 08:00 UTC
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="x",
            timestamp=ts,
        )
        assert entry.timestamp.utcoffset() == timedelta(0)
        assert entry.timestamp == ts  # equal across timezones
        assert entry.timestamp_iso().endswith("+00:00")

    def test_whitespace_only_phase_becomes_none(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="x",
            phase="   ",
        )
        assert entry.phase is None

    def test_phase_is_stripped(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="x",
            phase="  Phase 1  ",
        )
        assert entry.phase == "Phase 1"

    def test_metadata_is_stripped_and_empty_metadata_becomes_none(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="x",
            actor="  codex  ",
            client="   ",
            source="\n",
        )
        assert entry.actor == "codex"
        assert entry.client is None
        assert entry.source is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class TestJournalEntryHelpers:
    def test_with_id_returns_new_instance(self) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="x",
        )
        persisted = entry.with_id(7)

        assert persisted is not entry
        assert persisted.id == 7
        assert entry.id is None  # original untouched

    @pytest.mark.parametrize("bad_id", [0, -1, "1", None, 1.5])
    def test_with_id_rejects_non_positive_int(self, bad_id: object) -> None:
        entry = JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="x",
        )
        with pytest.raises((TypeError, ValueError)):
            entry.with_id(bad_id)  # type: ignore[arg-type]

    def test_from_row_parses_iso_timestamp(self) -> None:
        entry = JournalEntry.from_row(
            id=1,
            project="bramble",
            timestamp="2026-05-12T08:30:00+00:00",
            status="notiz",
            phase=None,
            title=None,
            content="x",
            actor="codex",
            client="codex-desktop",
            source="mcp",
        )
        assert entry.id == 1
        assert entry.status is JournalStatus.NOTIZ
        assert entry.actor == "codex"
        assert entry.client == "codex-desktop"
        assert entry.source == "mcp"
        assert entry.timestamp == datetime(2026, 5, 12, 8, 30, tzinfo=UTC)

    def test_from_row_assumes_utc_for_naive_legacy_rows(self) -> None:
        # Defensive: legacy rows might be missing the offset.
        entry = JournalEntry.from_row(
            id=2,
            project="bramble",
            timestamp="2026-05-12T08:30:00",
            status="notiz",
            phase=None,
            title=None,
            content="x",
            actor=None,
            client=None,
            source=None,
        )
        assert entry.timestamp.tzinfo is not None
        assert entry.timestamp.utcoffset() == timedelta(0)
