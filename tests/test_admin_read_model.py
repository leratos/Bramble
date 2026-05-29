"""Tests for the admin read model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bramble.admin_read_model import AdminReadModel
from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalEntryLink, JournalStatus


def test_dashboard_stats_count_recent_windows(db: JournalDB) -> None:
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="today",
            timestamp=now - timedelta(hours=1),
        )
    )
    db.append(
        JournalEntry(
            project="elder-berry",
            status=JournalStatus.ABGESCHLOSSEN,
            content="this week",
            timestamp=now - timedelta(days=3),
        )
    )
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.BUGFIX,
            content="older",
            timestamp=now - timedelta(days=40),
        )
    )

    stats = AdminReadModel(db).dashboard_stats(now=now)

    assert stats.project_count == 2
    assert stats.total_entries == 3
    assert stats.entries_last_24h == 1
    assert stats.entries_last_7d == 2
    assert stats.entries_last_30d == 2
    assert stats.digest_7d.counts_by_project == {"bramble": 1, "elder-berry": 1}
    assert stats.digest_7d.counts_by_status == {"abgeschlossen": 1, "notiz": 1}
    assert stats.open_items == ()
    assert [entry.content for entry in stats.recent_entries] == [
        "today",
        "this week",
        "older",
    ]


def test_dashboard_stats_include_open_items_and_decisions(db: JournalDB) -> None:
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.IN_ARBEIT,
            content="open deploy task",
            phase="Phase 4d",
            timestamp=now - timedelta(hours=1),
        )
    )
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            title="Decision: keep dashboard read-only",
            content="decision payload",
            tags=["decision"],
            timestamp=now - timedelta(hours=2),
        )
    )

    stats = AdminReadModel(db).dashboard_stats(now=now)

    assert [entry.content for entry in stats.open_items] == ["open deploy task"]
    assert [entry.title for entry in stats.digest_7d.decisions] == [
        "Decision: keep dashboard read-only"
    ]


def test_projects_include_registered_project_without_entries(db: JournalDB) -> None:
    db.register_project("berry-gym")

    projects = AdminReadModel(db).projects()

    assert [(project.name, project.entry_count) for project in projects] == [
        ("berry-gym", 0)
    ]


def test_dashboard_project_count_uses_registry(db: JournalDB) -> None:
    db.register_project("berry-gym")

    stats = AdminReadModel(db).dashboard_stats(
        now=datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    )

    assert stats.project_count == 1
    assert stats.total_entries == 0


def test_admin_read_model_loads_links_and_backlinks(db: JournalDB) -> None:
    original = db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="original",
        )
    )
    followup = db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.BUGFIX,
            content="follow-up",
            links=[{"to_entry_id": original.id, "relation": "corrects"}],
        )
    )

    entries = {entry.id: entry for entry in AdminReadModel(db).project_entries("bramble")}

    assert entries[followup.id].links == (
        JournalEntryLink(entry_id=original.id, relation="corrects"),
    )
    assert entries[original.id].backlinks == (
        JournalEntryLink(entry_id=followup.id, relation="corrects"),
    )


def test_project_context_returns_local_curated_context(db: JournalDB) -> None:
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.IN_ARBEIT,
            content="open local task",
            phase="Phase 4d",
            timestamp=now,
        )
    )
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.BUGFIX,
            content="local bugfix",
            timestamp=now - timedelta(minutes=1),
        )
    )
    db.append(
        JournalEntry(
            project="elder-berry",
            status=JournalStatus.IN_ARBEIT,
            content="foreign open task",
            timestamp=now - timedelta(minutes=2),
        )
    )

    context = AdminReadModel(db).project_context("bramble", n_recent=5)

    assert [entry.content for entry in context.open_items] == ["open local task"]
    assert [entry.content for entry in context.recent_bugfixes] == ["local bugfix"]
    assert context.related_projects == ()


def test_search_global_filters_by_status_and_since(db: JournalDB) -> None:
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.BUGFIX,
            content="needle new bugfix",
            timestamp=now - timedelta(hours=3),
        )
    )
    db.append(
        JournalEntry(
            project="elder-berry",
            status=JournalStatus.BUGFIX,
            content="needle old bugfix",
            timestamp=now - timedelta(days=50),
        )
    )
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="needle new notiz",
            timestamp=now - timedelta(hours=2),
        )
    )

    hits = AdminReadModel(db).search_global(
        "needle",
        status="bugfix",
        since="7d",
        now=now,
    )

    assert [entry.content for entry in hits] == ["needle new bugfix"]


def test_search_global_filters_by_project_and_tags(db: JournalDB) -> None:
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.BUGFIX,
            content="needle alpha",
            tags=["deployment", "admin-ui"],
            timestamp=now,
        )
    )
    db.append(
        JournalEntry(
            project="elder-berry",
            status=JournalStatus.BUGFIX,
            content="needle beta",
            tags=["deployment"],
            timestamp=now,
        )
    )

    hits = AdminReadModel(db).search_global(
        "needle",
        project="bramble",
        tags=("deployment", "admin-ui"),
        since="all",
        now=now,
    )

    assert [entry.content for entry in hits] == ["needle alpha"]


def test_search_global_filters_by_status_project_and_tags(db: JournalDB) -> None:
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.BUGFIX,
            content="needle alpha",
            tags=["deployment", "admin-ui"],
            timestamp=now,
        )
    )
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="needle beta",
            tags=["deployment", "admin-ui"],
            timestamp=now,
        )
    )
    db.append(
        JournalEntry(
            project="elder-berry",
            status=JournalStatus.BUGFIX,
            content="needle gamma",
            tags=["deployment", "admin-ui"],
            timestamp=now,
        )
    )

    hits = AdminReadModel(db).search_global(
        "needle",
        project="bramble",
        status="bugfix",
        tags=("deployment", "admin-ui"),
        since="all",
        now=now,
    )

    assert [entry.content for entry in hits] == ["needle alpha"]


def test_project_entries_preserve_literal_backslash_n_text(db: JournalDB) -> None:
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            content="first line\\nsecond line",
        )
    )

    entries = AdminReadModel(db).project_entries("bramble")

    assert entries[0].content == "first line\\nsecond line"


def test_search_global_rejects_invalid_since(db: JournalDB) -> None:
    with pytest.raises(ValueError):
        AdminReadModel(db).search_global("needle", since="1y")


def test_workflow_guidance_returns_phase_4e_defaults(db: JournalDB) -> None:
    guidance = AdminReadModel(db).workflow_guidance()

    assert guidance.statuses == ("in_arbeit", "abgeschlossen", "notiz", "bugfix")
    assert "decision" in guidance.suggested_tags
    assert "deployment" in guidance.suggested_tags
    assert any("Append-only" in item for item in guidance.completion_checklist)
