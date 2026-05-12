"""SQLite persistence layer for Bramble journal entries.

The :class:`JournalDB` owns the SQLite connection lifecycle and the
schema. It is intentionally synchronous and uses one short-lived
connection per public method via a context manager. This keeps the
class trivially safe for both threaded and asyncio callers (each call
gets its own connection) at the cost of slightly more overhead per
query. Phase 2 may switch to ``aiosqlite`` behind the same public
interface; no caller should need to change.

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
from pathlib import Path

from bramble.journal_entry import JournalEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS journal_entries (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        project   TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        status    TEXT NOT NULL,
        phase     TEXT,
        title     TEXT,
        content   TEXT NOT NULL
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
        """Create the schema if it does not exist.

        This is idempotent: calling it on an already-initialised
        database is a no-op.

        :raises sqlite3.OperationalError: If the SQLite build lacks
            FTS5 support.
        """

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.commit()
        logger.info("JournalDB initialised at %s", self._db_path)

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
            "(project, timestamp, status, phase, title, content) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        params = (
            entry.project,
            entry.timestamp_iso(),
            entry.status.value,
            entry.phase,
            entry.title,
            entry.content,
        )

        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            new_id = cursor.lastrowid

        if new_id is None or new_id <= 0:
            # This should not happen with AUTOINCREMENT, but be loud
            # if SQLite ever surprises us.
            raise RuntimeError("sqlite did not return a lastrowid")

        return entry.with_id(new_id)

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
            "SELECT id, project, timestamp, status, phase, title, content "
            "FROM journal_entries "
            "WHERE project = ? "
            "ORDER BY timestamp DESC, id DESC "
            "LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (project, n)).fetchall()
        return [self._row_to_entry(row) for row in rows]

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
            "       je.phase, je.title, je.content "
            "FROM journal_fts "
            "JOIN journal_entries je ON je.id = journal_fts.rowid "
            "WHERE journal_fts MATCH ? AND je.project = ? "
            "ORDER BY je.timestamp DESC, je.id DESC "
            "LIMIT ?"
        )
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (query, project, limit)).fetchall()
        except sqlite3.OperationalError as exc:
            # Bad FTS5 syntax from the caller. Don't crash the server.
            logger.warning(
                "FTS5 search failed for project=%r query=%r: %s",
                project,
                query,
                exc,
            )
            return []
        return [self._row_to_entry(row) for row in rows]

    def list_projects(self) -> list[str]:
        """Return all distinct project identifiers, sorted alphabetically."""

        sql = "SELECT DISTINCT project FROM journal_entries ORDER BY project"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [row["project"] for row in rows]

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
