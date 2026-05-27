"""Read-only query model for the Bramble admin UI."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry
from bramble.project_summary import ProjectSummary


@dataclass(frozen=True, slots=True)
class DashboardStats:
    """Aggregated read-only numbers for the admin dashboard."""

    project_count: int
    total_entries: int
    entries_last_24h: int
    entries_last_7d: int
    entries_last_30d: int
    recent_entries: tuple[JournalEntry, ...]


class AdminReadModel:
    """Small read-only facade over :class:`JournalDB` and dashboard SQL."""

    def __init__(self, db: JournalDB) -> None:
        if not isinstance(db, JournalDB):
            raise TypeError("db must be a JournalDB")
        self._db = db

    def projects(self) -> list[ProjectSummary]:
        sql = (
            "SELECT project, COUNT(*) AS entry_count, "
            "       MAX(timestamp) AS last_ts "
            "FROM journal_entries "
            "GROUP BY project "
            "ORDER BY last_ts DESC, project ASC"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql).fetchall()

        projects: list[ProjectSummary] = []
        for row in rows:
            ts = datetime.fromisoformat(row["last_ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            projects.append(
                ProjectSummary(
                    name=row["project"],
                    entry_count=row["entry_count"],
                    last_timestamp=ts,
                )
            )
        return projects

    def project_entries(self, project: str, *, limit: int = 80) -> list[JournalEntry]:
        sql = (
            "SELECT id, project, timestamp, status, phase, title, content "
            "FROM journal_entries "
            "WHERE project = ? "
            "ORDER BY timestamp DESC, id DESC "
            "LIMIT ?"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, (project, limit)).fetchall()
        return [_row_to_entry(row) for row in rows]

    def search_project(
        self,
        project: str,
        query: str,
        *,
        limit: int = 80,
    ) -> list[JournalEntry]:
        sql = (
            "SELECT je.id, je.project, je.timestamp, je.status, "
            "       je.phase, je.title, je.content "
            "FROM journal_fts "
            "JOIN journal_entries je ON je.id = journal_fts.rowid "
            "WHERE journal_fts MATCH ? AND je.project = ? "
            "ORDER BY je.timestamp DESC, je.id DESC "
            "LIMIT ?"
        )
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, (query, project, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_row_to_entry(row) for row in rows]

    def dashboard_stats(self, *, now: datetime | None = None) -> DashboardStats:
        if now is None:
            now = datetime.now(tz=UTC)
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)

        projects = self.projects()
        with closing(self._connect()) as conn:
            total_entries = _count_all_entries(conn)
            entries_last_24h = _count_entries_since(conn, now - timedelta(hours=24))
            entries_last_7d = _count_entries_since(conn, now - timedelta(days=7))
            entries_last_30d = _count_entries_since(conn, now - timedelta(days=30))
            recent_entries = tuple(_recent_entries(conn, limit=10))

        return DashboardStats(
            project_count=len(projects),
            total_entries=total_entries,
            entries_last_24h=entries_last_24h,
            entries_last_7d=entries_last_7d,
            entries_last_30d=entries_last_30d,
            recent_entries=recent_entries,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn


def _count_all_entries(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0])


def _count_entries_since(conn: sqlite3.Connection, since: datetime) -> int:
    sql = "SELECT COUNT(*) FROM journal_entries WHERE timestamp >= ?"
    return int(conn.execute(sql, (since.astimezone(UTC).isoformat(),)).fetchone()[0])


def _recent_entries(conn: sqlite3.Connection, *, limit: int) -> list[JournalEntry]:
    sql = (
        "SELECT id, project, timestamp, status, phase, title, content "
        "FROM journal_entries "
        "ORDER BY timestamp DESC, id DESC "
        "LIMIT ?"
    )
    rows = conn.execute(sql, (limit,)).fetchall()
    return [_row_to_entry(row) for row in rows]


def _row_to_entry(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry.from_row(
        id=row["id"],
        project=row["project"],
        timestamp=row["timestamp"],
        status=row["status"],
        phase=row["phase"],
        title=row["title"],
        content=row["content"],
    )
