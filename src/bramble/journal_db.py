"""SQLite persistence layer for Bramble journal entries.

The :class:`JournalDB` owns the SQLite connection lifecycle and the
schema. It is intentionally synchronous and uses one short-lived
connection per public method via a context manager. This keeps the
class trivially safe for both threaded and asyncio callers (each call
gets its own connection) at the cost of slightly more overhead per
query. Async callers wrap method invocations in
:func:`asyncio.to_thread` at the MCP layer (see
:class:`bramble.journal_mcp_server.JournalMCPServer`); this class
deliberately stays out of the async machinery so its Phase-1 tests
remain valid.

The schema follows the spec in the project root README, plus a few
defensive additions:

* The FTS5 index covers both ``content`` and ``title`` so that titles
  are searchable without having to be repeated in the body.
* Three triggers (insert / update / delete) keep the FTS table in
  sync with the base table. The journal is append-only by API
  contract, but the triggers exist anyway in case a future migration
  needs them or a row is fixed up manually.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from bramble.journal_entry import JournalEntry
from bramble.project_summary import ProjectSummary

logger = logging.getLogger(__name__)

_PROJECT_STATUSES = {"active", "paused", "archived"}


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        name          TEXT PRIMARY KEY,
        display_name  TEXT,
        description   TEXT,
        status        TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'paused', 'archived')),
        default_phase TEXT,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        archived_at   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_entries (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        project   TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        status    TEXT NOT NULL,
        phase     TEXT,
        title     TEXT,
        content   TEXT NOT NULL,
        actor     TEXT,
        client    TEXT,
        source    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_tags (
        name        TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_entry_tags (
        entry_id  INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
        tag       TEXT NOT NULL REFERENCES journal_tags(name),
        PRIMARY KEY (entry_id, tag)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_project_ts
        ON journal_entries(project, timestamp DESC)
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts
        USING fts5(
            content,
            title,
            content='journal_entries',
            content_rowid='id'
        )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_entries_ai
        AFTER INSERT ON journal_entries
    BEGIN
        INSERT INTO journal_fts(rowid, content, title)
        VALUES (new.id, new.content, new.title);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_entries_ad
        AFTER DELETE ON journal_entries
    BEGIN
        INSERT INTO journal_fts(journal_fts, rowid, content, title)
        VALUES ('delete', old.id, old.content, old.title);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_entries_au
        AFTER UPDATE ON journal_entries
    BEGIN
        INSERT INTO journal_fts(journal_fts, rowid, content, title)
        VALUES ('delete', old.id, old.content, old.title);
        INSERT INTO journal_fts(rowid, content, title)
        VALUES (new.id, new.content, new.title);
    END
    """,
)


class JournalDB:
    """SQLite-backed storage for :class:`JournalEntry` records.

    The class is responsible for connection lifecycle, schema
    management and query execution. It deliberately does **not**
    expose the SQLite connection.
    """

    def __init__(self, db_path: Path | str) -> None:
        if isinstance(db_path, str):
            db_path = Path(db_path)
        if not isinstance(db_path, Path):
            raise TypeError("db_path must be a pathlib.Path or str")
        self._db_path: Path = db_path

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        """The path to the underlying SQLite file."""

        return self._db_path

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Create the schema if it does not exist and enable WAL mode.

        This is idempotent: calling it on an already-initialised
        database is a no-op.

        :raises sqlite3.OperationalError: If the SQLite build lacks
            FTS5 support.
        """

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # WAL lets multiple readers run alongside a single writer
            # and cuts down SQLITE_BUSY errors. It matters here because
            # the MCP tools reach SQLite from several threads via
            # asyncio.to_thread. The mode is stored in the database
            # header, so this is a one-time switch (Phase-3 Decision I).
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                logger.warning(
                    "could not enable WAL mode; journal_mode is %r", mode
                )
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            _migrate_journal_entry_metadata_columns(conn)
            _migrate_projects_from_entries(conn)
            conn.commit()
        logger.info("JournalDB initialised at %s (journal_mode=%s)", self._db_path, mode)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------
    def append(self, entry: JournalEntry) -> JournalEntry:
        """Insert ``entry`` and return a copy with the assigned id.

        :raises ValueError: If ``entry.id`` is already set (suggesting
            the caller is trying to re-insert a persisted row).
        """

        if not isinstance(entry, JournalEntry):
            raise TypeError("entry must be a JournalEntry")
        if entry.id is not None:
            raise ValueError(
                "entry.id is already set; refusing to insert. "
                "Bramble is append-only."
            )

        sql = (
            "INSERT INTO journal_entries "
            "(project, timestamp, status, phase, title, content, actor, client, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            entry.project,
            entry.timestamp_iso(),
            entry.status.value,
            entry.phase,
            entry.title,
            entry.content,
            entry.actor,
            entry.client,
            entry.source,
        )

        with self._connect() as conn:
            _upsert_project_for_entry(conn, entry.project, entry.timestamp_iso())
            cursor = conn.execute(sql, params)
            new_id = cursor.lastrowid
            if new_id is None or new_id <= 0:
                # This should not happen with AUTOINCREMENT, but be loud
                # if SQLite ever surprises us.
                raise RuntimeError("sqlite did not return a lastrowid")
            _insert_entry_tags(conn, new_id, entry.tags, entry.timestamp_iso())
            conn.commit()

        return entry.with_id(new_id)

    def register_project(
        self,
        name: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        status: str = "active",
        default_phase: str | None = None,
    ) -> None:
        """Ensure a project exists in the registry without adding entries."""

        self._validate_project_arg(name)
        name = name.strip()
        display_name = _normalise_optional_text(display_name, "display_name")
        description = _normalise_optional_text(description, "description")
        default_phase = _normalise_optional_text(default_phase, "default_phase")
        if status not in _PROJECT_STATUSES:
            allowed = ", ".join(sorted(_PROJECT_STATUSES))
            raise ValueError(
                f"status {status!r} is not allowed; must be one of: {allowed}"
            )

        now = datetime.now(tz=UTC).isoformat()
        archived_at = now if status == "archived" else None
        sql = (
            "INSERT OR IGNORE INTO projects "
            "(name, display_name, description, status, default_phase, "
            " created_at, updated_at, archived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            name,
            display_name,
            description,
            status,
            default_phase,
            now,
            now,
            archived_at,
        )
        with self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def register_projects(self, names: object) -> None:
        """Ensure multiple project names exist in the registry."""

        if isinstance(names, (str, bytes)):
            raise TypeError("names must be an iterable of project strings")
        try:
            iterator = iter(names)  # type: ignore[arg-type]
        except TypeError as exc:
            raise TypeError("names must be an iterable of project strings") from exc

        project_names: list[str] = []
        for name in iterator:
            self._validate_project_arg(name)
            project_names.append(name.strip())
        if not project_names:
            return

        now = datetime.now(tz=UTC).isoformat()
        rows = [(name, now, now) for name in sorted(set(project_names))]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO projects "
                "(name, created_at, updated_at) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def read(self, project: str, n: int = 80) -> list[JournalEntry]:
        """Return the ``n`` most recent entries for ``project``.

        Newest entries come first.
        """

        self._validate_project_arg(project)
        self._validate_limit_arg(n, name="n")

        sql = (
            "SELECT id, project, timestamp, status, phase, title, content, "
            "       actor, client, source "
            "FROM journal_entries "
            "WHERE project = ? "
            "ORDER BY timestamp DESC, id DESC "
            "LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (project, n)).fetchall()
            return self._rows_to_entries(conn, rows)

    def search(
        self,
        project: str,
        query: str,
        limit: int = 20,
    ) -> list[JournalEntry]:
        """Full-text-search ``project``'s entries for ``query``.

        The ``query`` is passed as-is to SQLite FTS5 ``MATCH``. See
        https://sqlite.org/fts5.html for the supported syntax.

        Returns an empty list if the query string is malformed (an
        ``OperationalError`` from the FTS5 parser is caught and
        logged).
        """

        self._validate_project_arg(project)
        self._validate_limit_arg(limit, name="limit")
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not query.strip():
            raise ValueError("query must not be empty")

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
            with self._connect() as conn:
                rows = conn.execute(sql, (query, project, limit)).fetchall()
                return self._rows_to_entries(conn, rows)
        except sqlite3.OperationalError as exc:
            # Bad FTS5 syntax from the caller. Don't crash the server.
            logger.warning(
                "FTS5 search failed for project=%r query=%r: %s",
                project,
                query,
                exc,
            )
            return []

    def project_overview(self) -> list[ProjectSummary]:
        """Return one :class:`ProjectSummary` per project, newest activity first.

        For each project in the registry, the summary contains the
        entry count and the most recent timestamp. Projects with zero
        entries are listed with ``last_timestamp=None``.

        Ordering: descending by ``last_timestamp``. Ties – which can
        legitimately occur when two entries share a timestamp string –
        are broken alphabetically by project name to keep the output
        stable across calls.
        """

        # ISO-8601 strings with the same UTC offset compare
        # lexicographically the same way the underlying datetimes do.
        # ``JournalEntry`` enforces UTC, so this MAX/ORDER BY is safe.
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
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()

        summaries: list[ProjectSummary] = []
        for row in rows:
            ts = _parse_optional_timestamp(row["last_ts"])
            summaries.append(
                ProjectSummary(
                    name=row["project"],
                    entry_count=row["entry_count"],
                    last_timestamp=ts,
                )
            )
        return summaries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection with sensible defaults."""

        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            # Foreign keys are off in Bramble (no FKs in the schema),
            # but enable them anyway in case a future migration adds
            # one and forgets.
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _rows_to_entries(
        conn: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> list[JournalEntry]:
        tags_by_entry_id = _tags_by_entry_id(conn, [row["id"] for row in rows])
        return [
            JournalDB._row_to_entry(row, tags=tags_by_entry_id.get(row["id"], ()))
            for row in rows
        ]

    @staticmethod
    def _row_to_entry(
        row: sqlite3.Row,
        *,
        tags: tuple[str, ...] = (),
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
        )

    @staticmethod
    def _validate_project_arg(project: object) -> None:
        if not isinstance(project, str):
            raise TypeError("project must be a string")
        if not project.strip():
            raise ValueError("project must not be empty")

    @staticmethod
    def _validate_limit_arg(value: object, *, name: str) -> None:
        # bool is a subclass of int – exclude it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an int")
        if value <= 0:
            raise ValueError(f"{name} must be positive")


def _migrate_projects_from_entries(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO projects (name, created_at, updated_at)
        SELECT project, MIN(timestamp), MAX(timestamp)
        FROM journal_entries
        GROUP BY project
        """
    )


def _migrate_journal_entry_metadata_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(journal_entries)").fetchall()
    }
    for column in ("actor", "client", "source"):
        if column not in columns:
            conn.execute(f"ALTER TABLE journal_entries ADD COLUMN {column} TEXT")


def _insert_entry_tags(
    conn: sqlite3.Connection,
    entry_id: int,
    tags: tuple[str, ...],
    timestamp: str,
) -> None:
    if not tags:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO journal_tags (name, created_at) VALUES (?, ?)",
        [(tag, timestamp) for tag in tags],
    )
    conn.executemany(
        "INSERT INTO journal_entry_tags (entry_id, tag) VALUES (?, ?)",
        [(entry_id, tag) for tag in tags],
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


def _upsert_project_for_entry(
    conn: sqlite3.Connection,
    project: str,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO projects (name, created_at, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            created_at = CASE
                WHEN excluded.created_at < projects.created_at
                THEN excluded.created_at
                ELSE projects.created_at
            END,
            updated_at = CASE
                WHEN excluded.updated_at > projects.updated_at
                THEN excluded.updated_at
                ELSE projects.updated_at
            END
        """,
        (project, timestamp, timestamp),
    )


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def _normalise_optional_text(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    value = value.strip()
    return value or None
