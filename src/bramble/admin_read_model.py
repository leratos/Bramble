"""Read-only query model for the Bramble admin UI."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bramble.journal_context import JournalContext
from bramble.journal_db import JournalDB
from bramble.journal_digest import JournalDigest
from bramble.journal_entry import JournalEntry, JournalEntryLink
from bramble.project_summary import ProjectSummary


@dataclass(frozen=True, slots=True)
class DashboardStats:
    """Aggregated read-only numbers for the admin dashboard."""

    project_count: int
    total_entries: int
    entries_last_24h: int
    entries_last_7d: int
    entries_last_30d: int
    digest_7d: JournalDigest
    open_items: tuple[JournalEntry, ...]
    recent_entries: tuple[JournalEntry, ...]


class AdminReadModel:
    """Small read-only facade over :class:`JournalDB` and dashboard SQL."""

    def __init__(self, db: JournalDB) -> None:
        if not isinstance(db, JournalDB):
            raise TypeError("db must be a JournalDB")
        self._db = db

    def projects(self) -> list[ProjectSummary]:
        sql = (
            "WITH entry_stats AS ("
            "    SELECT project, COUNT(*) AS entry_count, MAX(timestamp) AS last_ts "
            "    FROM journal_entries "
            "    GROUP BY project "
            ") "
            "SELECT p.name AS project, "
            "       COALESCE(es.entry_count, 0) AS entry_count, "
            "       es.last_ts AS last_ts "
            "FROM projects p "
            "LEFT JOIN entry_stats es ON es.project = p.name "
            "ORDER BY es.last_ts IS NULL ASC, es.last_ts DESC, p.name ASC"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql).fetchall()

        projects: list[ProjectSummary] = []
        for row in rows:
            ts = _parse_optional_timestamp(row["last_ts"])
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
            "SELECT id, project, timestamp, status, phase, title, content, "
            "       actor, client, source "
            "FROM journal_entries "
            "WHERE project = ? "
            "ORDER BY timestamp DESC, id DESC "
            "LIMIT ?"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, (project, limit)).fetchall()
            return _rows_to_entries(conn, rows)

    def search_project(
        self,
        project: str,
        query: str,
        *,
        limit: int = 80,
    ) -> list[JournalEntry]:
        sql = (
            "SELECT je.id, je.project, je.timestamp, je.status, "
            "       je.phase, je.title, je.content, "
            "       je.actor, je.client, je.source "
            "FROM journal_fts "
            "JOIN journal_entries je ON je.id = journal_fts.rowid "
            "WHERE journal_fts MATCH ? AND je.project = ? "
            "ORDER BY je.timestamp DESC, je.id DESC "
            "LIMIT ?"
        )
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, (query, project, limit)).fetchall()
                return _rows_to_entries(conn, rows)
        except sqlite3.OperationalError:
            return []

    def project_context(
        self,
        project: str,
        *,
        n_recent: int = 5,
    ) -> JournalContext:
        return self._db.context(
            project,
            n_recent=n_recent,
            include_cross_project=False,
        )

    def dashboard_stats(self, *, now: datetime | None = None) -> DashboardStats:
        if now is None:
            now = datetime.now(tz=UTC)
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)

        projects = self.projects()
        digest_7d = self._db.digest(since="7d", now=now)
        open_items = tuple(self._db.open_items(limit=10))
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
            digest_7d=digest_7d,
            open_items=open_items,
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
        "SELECT id, project, timestamp, status, phase, title, content, "
        "       actor, client, source "
        "FROM journal_entries "
        "ORDER BY timestamp DESC, id DESC "
        "LIMIT ?"
    )
    rows = conn.execute(sql, (limit,)).fetchall()
    return _rows_to_entries(conn, rows)


def _rows_to_entries(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> list[JournalEntry]:
    entry_ids = [row["id"] for row in rows]
    tags_by_entry_id = _tags_by_entry_id(conn, entry_ids)
    links_by_entry_id = _outgoing_links_by_entry_id(conn, entry_ids)
    backlinks_by_entry_id = _incoming_links_by_entry_id(conn, entry_ids)
    return [
        _row_to_entry(
            row,
            tags=tags_by_entry_id.get(row["id"], ()),
            links=links_by_entry_id.get(row["id"], ()),
            backlinks=backlinks_by_entry_id.get(row["id"], ()),
        )
        for row in rows
    ]


def _row_to_entry(
    row: sqlite3.Row,
    *,
    tags: tuple[str, ...] = (),
    links: tuple[JournalEntryLink, ...] = (),
    backlinks: tuple[JournalEntryLink, ...] = (),
) -> JournalEntry:
    return JournalEntry.from_row(
        id=row["id"],
        project=row["project"],
        timestamp=row["timestamp"],
        status=row["status"],
        phase=row["phase"],
        title=row["title"],
        content=row["content"],
        actor=row["actor"],
        client=row["client"],
        source=row["source"],
        tags=tags,
        links=links,
        backlinks=backlinks,
    )


def _tags_by_entry_id(
    conn: sqlite3.Connection,
    entry_ids: list[int],
) -> dict[int, tuple[str, ...]]:
    if not entry_ids:
        return {}
    placeholders = ",".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"""
        SELECT entry_id, tag
        FROM journal_entry_tags
        WHERE entry_id IN ({placeholders})
        ORDER BY tag ASC
        """,
        entry_ids,
    ).fetchall()
    tag_map: dict[int, list[str]] = {}
    for row in rows:
        tag_map.setdefault(row["entry_id"], []).append(row["tag"])
    return {entry_id: tuple(tags) for entry_id, tags in tag_map.items()}


def _outgoing_links_by_entry_id(
    conn: sqlite3.Connection,
    entry_ids: list[int],
) -> dict[int, tuple[JournalEntryLink, ...]]:
    if not entry_ids:
        return {}
    placeholders = ",".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"""
        SELECT from_entry_id, to_entry_id, relation
        FROM journal_entry_links
        WHERE from_entry_id IN ({placeholders})
        ORDER BY relation ASC, to_entry_id ASC
        """,
        entry_ids,
    ).fetchall()
    link_map: dict[int, list[JournalEntryLink]] = {}
    for row in rows:
        link_map.setdefault(row["from_entry_id"], []).append(
            JournalEntryLink(entry_id=row["to_entry_id"], relation=row["relation"])
        )
    return {entry_id: tuple(links) for entry_id, links in link_map.items()}


def _incoming_links_by_entry_id(
    conn: sqlite3.Connection,
    entry_ids: list[int],
) -> dict[int, tuple[JournalEntryLink, ...]]:
    if not entry_ids:
        return {}
    placeholders = ",".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"""
        SELECT to_entry_id, from_entry_id, relation
        FROM journal_entry_links
        WHERE to_entry_id IN ({placeholders})
        ORDER BY relation ASC, from_entry_id ASC
        """,
        entry_ids,
    ).fetchall()
    link_map: dict[int, list[JournalEntryLink]] = {}
    for row in rows:
        link_map.setdefault(row["to_entry_id"], []).append(
            JournalEntryLink(entry_id=row["from_entry_id"], relation=row["relation"])
        )
    return {entry_id: tuple(links) for entry_id, links in link_map.items()}


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts
