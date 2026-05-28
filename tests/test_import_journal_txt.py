"""Unit tests for the ``scripts/import_journal_txt.py`` helper."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalStatus

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import_journal_txt.py"
_spec = importlib.util.spec_from_file_location("import_journal_txt", _SCRIPT)
assert _spec is not None and _spec.loader is not None
import_journal_txt = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = import_journal_txt
_spec.loader.exec_module(import_journal_txt)


SAMPLE = """# Legacy Journal

Intro text ignored by the importer.

## In Arbeit: Phase 1 - Repo-Setup
Datum: 2026-05-12 (UTC)
Branch: feature/phase-1

Implemented the first thing.

## Bugfix: Lazy Import
Datum: 2026-05-13 09:30 (UTC)

Fixed the import path.
"""


def test_parse_journal_text_extracts_entries_and_dates() -> None:
    result = import_journal_txt.parse_journal_text(SAMPLE)

    assert result.issues == []
    assert len(result.entries) == 2

    first = result.entries[0]
    assert first.status is JournalStatus.IN_ARBEIT
    assert first.title == "Phase 1 - Repo-Setup"
    assert first.phase == "Phase 1"
    assert first.timestamp == datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    assert "Datum:" not in first.content
    assert "Branch: feature/phase-1" in first.content

    second = result.entries[1]
    assert second.status is JournalStatus.BUGFIX
    assert second.timestamp == datetime(2026, 5, 13, 9, 30, tzinfo=UTC)


def test_parse_accepts_date_in_heading_and_removes_it_from_title() -> None:
    text = """## Abgeschlossen: Phase 75b -- Repo-Hygiene (2026-05-01)
- Branch: feature/phase-75b

Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.status is JournalStatus.ABGESCHLOSSEN
    assert entry.title == "Phase 75b -- Repo-Hygiene"
    assert entry.phase == "Phase 75b"
    assert entry.timestamp == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def test_parse_accepts_bulleted_date_line() -> None:
    text = """## Abgeschlossen: Konzept-Phase 92 -- Multi-Stop-Routing

- Datum: 2026-05-13
- Branch: feature/phase-92

Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert result.entries[0].timestamp == datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    assert "Datum:" not in result.entries[0].content


def test_parse_accepts_elder_berry_status_labels() -> None:
    text = """## Korrektur: Phase 80 Etappe 2 -- CodeQL-Findings (2026-05-09)
Body.

## Nachtrag: Phase-91-PR -- Review-Anmerkungen (2026-05-18)
Body.

## Stand: Hotfix Tower-Update -- Code geschrieben (2026-05-05)
Body.

## Abschluss: Phase 76b komplett auf main (2026-05-05)
Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert [entry.status for entry in result.entries] == [
        JournalStatus.BUGFIX,
        JournalStatus.NOTIZ,
        JournalStatus.NOTIZ,
        JournalStatus.ABGESCHLOSSEN,
    ]


def test_parse_merges_metadata_headings_into_current_section() -> None:
    text = """================================================
2026-05-13 Phase 92 -- Multi-Stop-Routing
================================================

## In Arbeit: Konzept-Phase 92 -- Multi-Stop-Routing
## Naechster Schritt: docs/concepts/phase-92.md
## Branch: feature/phase-92

Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.timestamp == datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    assert "Naechster Schritt: docs/concepts/phase-92.md" in entry.content
    assert "Branch: feature/phase-92" in entry.content


def test_parse_skips_empty_heading_only_sections() -> None:
    text = """## Abgeschlossen: Duplicate Heading (2026-05-10)

## Abgeschlossen: Real Heading (2026-05-10)
Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert len(result.entries) == 1
    assert result.entries[0].title == "Real Heading"


def test_parse_uses_nearest_dated_section_for_undated_notes() -> None:
    text = """## Abgeschlossen: Phase 80 Etappe 1 (2026-05-08)
Body.

## Hinweis fuer Etappe 2 (Phase 80, separate Session)
Follow-up.

## In Arbeit: Phase 80 Etappe 2 (2026-05-08)
Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert result.entries[1].status is JournalStatus.NOTIZ
    assert result.entries[1].timestamp == datetime(2026, 5, 8, 12, 0, tzinfo=UTC)


def test_parse_infers_in_progress_for_phase_heading_with_open_work() -> None:
    text = """## Phase 3 (Deployment): Code umgesetzt, Host-Deploy offen
Datum: 2026-05-19 (UTC)

Deployment code exists, host deploy still open.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.issues == []
    assert result.entries[0].status is JournalStatus.IN_ARBEIT
    assert result.entries[0].title == "Phase 3 (Deployment): Code umgesetzt, Host-Deploy offen"
    assert result.entries[0].phase == "Phase 3"


def test_parse_reports_unknown_status() -> None:
    text = """## Vielleicht: Something unclear
Datum: 2026-05-12 (UTC)

Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.entries == []
    assert len(result.issues) == 1
    assert result.issues[0].message == "unknown status in heading"


def test_parse_reports_missing_date() -> None:
    text = """## Notiz: Undated

Body.
"""
    result = import_journal_txt.parse_journal_text(text)

    assert result.entries == []
    assert len(result.issues) == 1
    assert result.issues[0].message == "missing or invalid Datum line"


def test_real_bramble_journal_parses_without_issues() -> None:
    source = Path(__file__).resolve().parent.parent / "docs" / "journal.txt"
    result = import_journal_txt.parse_journal_file(source)

    assert result.issues == []
    assert len(result.entries) >= 12


def test_dry_run_does_not_create_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "journal.txt"
    source.write_text(SAMPLE, encoding="utf-8")
    db_path = tmp_path / "bramble.db"

    rc = import_journal_txt.main(
        [
            "--source",
            str(source),
            "--db",
            str(db_path),
            "--project",
            "bramble",
        ]
    )

    assert rc == 0
    assert not db_path.exists()
    assert "dry-run only" in capsys.readouterr().out


def test_execute_imports_entries_and_skips_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "journal.txt"
    source.write_text(SAMPLE, encoding="utf-8")
    db_path = tmp_path / "bramble.db"

    first_rc = import_journal_txt.main(
        [
            "--source",
            str(source),
            "--db",
            str(db_path),
            "--project",
            "bramble",
            "--execute",
        ]
    )
    second_rc = import_journal_txt.main(
        [
            "--source",
            str(source),
            "--db",
            str(db_path),
            "--project",
            "bramble",
            "--execute",
        ]
    )

    assert first_rc == 0
    assert second_rc == 0

    db = JournalDB(db_path)
    entries = db.read("bramble", n=10)
    assert len(entries) == 2
    assert {entry.title for entry in entries} == {
        "Phase 1 - Repo-Setup",
        "Lazy Import",
    }


def test_bad_project_returns_error(tmp_path: Path) -> None:
    source = tmp_path / "journal.txt"
    source.write_text(SAMPLE, encoding="utf-8")

    rc = import_journal_txt.main(
        ["--source", str(source), "--project", "Bad_Name"]
    )

    assert rc == 2
