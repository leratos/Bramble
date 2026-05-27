"""Tests for the admin read model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bramble.admin_read_model import AdminReadModel
from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus


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
    assert [entry.content for entry in stats.recent_entries] == [
        "today",
        "this week",
        "older",
    ]
