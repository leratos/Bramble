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
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bramble.journal_context import JournalContext
from bramble.journal_entry import JournalEntry, JournalEntryLink, JournalStatus
from bramble.journal_digest import JournalDigest
from bramble.project_summary import ProjectSummary

logger = logging.getLogger(__name__)

_PROJECT_STATUSES = {"active", "paused", "archived"}
_SEARCH_ALL_LIMIT_MAX = 100
_DIGEST_LIMIT_MAX = 100
_OPEN_ITEMS_LIMIT_MAX = 100
_CONTEXT_N_RECENT_MAX = 100
_CONTEXT_RELATED_PROJECTS_MAX = 5
_CONTEXT_RELATED_SEARCH_LIMIT = 20
_CONTEXT_SUGGESTIONS_MAX = 8
_DIGEST_RELATIVE_RANGES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
_OPEN_ITEMS_CLOSING_RE = re.compile(r"#(?P<open_id>\d+)\s*->\s*#\d+", re.IGNORECASE)
_DECISION_RE = re.compile(r"\b(decision|entscheidung|festgelegt)\b", re.IGNORECASE)
_TAG_FILTER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CONTEXT_SUGGESTION_TERMS: tuple[str, ...] = (
    "deployment",
    "backup",
    "restore",
    "bugfix",
    "decision",
    "admin",
    "token",
    "mcp",
    "import",
    "matrix",
    "ssh",
    "host",
)


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
    CREATE TABLE IF NOT EXISTS journal_entry_links (
        from_entry_id  INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
        to_entry_id    INTEGER NOT NULL REFERENCES journal_entries(id),
        relation       TEXT NOT NULL CHECK (
            relation IN (
                'corrects',
                'adds_context_to',
                'supersedes',
                'implements',
                'relates_to'
            )
        ),
        created_at     TEXT NOT NULL,
        PRIMARY KEY (from_entry_id, to_entry_id, relation)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_entry_links_to
        ON journal_entry_links(to_entry_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_project_ts
        ON journal_entries(project, timestamp DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_status_ts
        ON journal_entries(status, timestamp DESC, id DESC)
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
            _insert_entry_links(conn, new_id, entry.links, entry.timestamp_iso())
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

    def project_status(self, name: str) -> str | None:
        """Return the lifecycle status for a registered project."""

        self._validate_project_arg(name)
        sql = "SELECT status FROM projects WHERE name = ?"
        with self._connect() as conn:
            row = conn.execute(sql, (name.strip(),)).fetchone()
        if row is None:
            return None
        return str(row[0])

    def set_project_status(self, name: str, status: str) -> None:
        """Update the lifecycle status for an existing project."""

        self._validate_project_arg(name)
        name = name.strip()
        if status not in _PROJECT_STATUSES:
            allowed = ", ".join(sorted(_PROJECT_STATUSES))
            raise ValueError(
                f"status {status!r} is not allowed; must be one of: {allowed}"
            )

        now = datetime.now(tz=UTC).isoformat()
        archived_at = now if status == "archived" else None
        sql = (
            "UPDATE projects "
            "SET status = ?, updated_at = ?, archived_at = ? "
            "WHERE name = ?"
        )
        with self._connect() as conn:
            cursor = conn.execute(sql, (status, now, archived_at, name))
            if cursor.rowcount == 0:
                raise ValueError(f"unknown project {name!r}")
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

    def search_all(
        self,
        query: str,
        limit: int = 20,
        *,
        projects: object = None,
        statuses: object = None,
        tags: object = None,
    ) -> list[JournalEntry]:
        """Full-text-search entries across projects.

        Optional filters narrow the result set by project, status and
        tags. Multiple tags use AND semantics: every requested tag must
        be present on a hit. Malformed FTS5 syntax returns an empty
        result list, matching :meth:`search`.
        """

        self._validate_search_all_limit_arg(limit)
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not query.strip():
            raise ValueError("query must not be empty")

        project_filter = _normalise_text_filter_values(projects, name="projects")
        status_filter = _normalise_status_filter_values(statuses)
        tag_filter = _normalise_tag_filter_values(tags)

        where = ["journal_fts MATCH ?"]
        params: list[object] = [query]
        if project_filter:
            placeholders = ",".join("?" for _ in project_filter)
            where.append(f"je.project IN ({placeholders})")
            params.extend(project_filter)
        if status_filter:
            placeholders = ",".join("?" for _ in status_filter)
            where.append(f"je.status IN ({placeholders})")
            params.extend(status_filter)
        for index, tag in enumerate(tag_filter):
            where.append(
                "EXISTS ("
                f"SELECT 1 FROM journal_entry_tags jet{index} "
                f"WHERE jet{index}.entry_id = je.id AND jet{index}.tag = ?"
                ")"
            )
            params.append(tag)

        sql = (
            "SELECT je.id, je.project, je.timestamp, je.status, "
            "       je.phase, je.title, je.content, "
            "       je.actor, je.client, je.source "
            "FROM journal_fts "
            "JOIN journal_entries je ON je.id = journal_fts.rowid "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY je.timestamp DESC, je.id DESC "
            "LIMIT ?"
        )
        params.append(limit)
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
                return self._rows_to_entries(conn, rows)
        except sqlite3.OperationalError as exc:
            logger.warning(
                "FTS5 search_all failed for query=%r: %s",
                query,
                exc,
            )
            return []

    def digest(
        self,
        *,
        project: str | None = None,
        since: str = "7d",
        until: str | None = None,
        tags: object = None,
        limit: int = 80,
        now: datetime | None = None,
    ) -> JournalDigest:
        """Return a structured digest over a time range.

        Counts cover the full filtered range. Entry lists are capped by
        ``limit`` and returned newest first.
        """

        self._validate_digest_limit_arg(limit)
        if project is not None:
            self._validate_project_arg(project)
            project = project.strip()
        range_since, range_until = _resolve_digest_range(
            since=since,
            until=until,
            now=now,
        )
        tag_filter = _normalise_tag_filter_values(tags)
        where, params = _digest_where_clause(
            project=project,
            range_since=range_since,
            range_until=range_until,
            tags=tag_filter,
        )
        count_sql = (
            "SELECT je.project, je.status, COUNT(*) AS entry_count "
            "FROM journal_entries je "
            f"WHERE {' AND '.join(where)} "
            "GROUP BY je.project, je.status "
            "ORDER BY je.project ASC, je.status ASC"
        )
        entry_sql = (
            "SELECT je.id, je.project, je.timestamp, je.status, "
            "       je.phase, je.title, je.content, "
            "       je.actor, je.client, je.source "
            "FROM journal_entries je "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY je.timestamp DESC, je.id DESC "
            "LIMIT ?"
        )
        with self._connect() as conn:
            count_rows = conn.execute(count_sql, params).fetchall()
            rows = conn.execute(entry_sql, [*params, limit]).fetchall()
            entries = tuple(self._rows_to_entries(conn, rows))

        counts_by_project: dict[str, int] = {}
        counts_by_status: dict[str, int] = {}
        for row in count_rows:
            count = int(row["entry_count"])
            counts_by_project[row["project"]] = (
                counts_by_project.get(row["project"], 0) + count
            )
            counts_by_status[row["status"]] = counts_by_status.get(row["status"], 0) + count

        return JournalDigest(
            range_since=range_since,
            range_until=range_until,
            projects=tuple(sorted(counts_by_project)),
            counts_by_project=counts_by_project,
            counts_by_status=counts_by_status,
            entries=entries,
            open_items=tuple(
                entry for entry in entries if entry.status is JournalStatus.IN_ARBEIT
            ),
            bugfixes=tuple(
                entry for entry in entries if entry.status is JournalStatus.BUGFIX
            ),
            decisions=tuple(entry for entry in entries if _entry_is_decision(entry)),
        )

    def open_items(
        self,
        *,
        project: str | None = None,
        limit: int = 50,
    ) -> list[JournalEntry]:
        """Return newest actionable open work items.

        Base set is ``status='in_arbeit'``. Entries are suppressed when a
        newer non-``in_arbeit`` entry in the same project clearly marks them
        as closed via either:

        * an incoming closing link relation (``corrects``, ``supersedes``,
          ``implements``), or
        * a textual mapping in the newer entry content like ``#123 -> #124``.
        """

        self._validate_open_items_limit_arg(limit)
        if project is not None:
            self._validate_project_arg(project)
            project = project.strip()

        where = ["status = ?"]
        params: list[object] = [JournalStatus.IN_ARBEIT.value]
        if project is not None:
            where.append("project = ?")
            params.append(project)

        sql = (
            "SELECT id, project, timestamp, status, phase, title, content, "
            "       actor, client, source "
            "FROM journal_entries "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY timestamp DESC, id DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            entries = _filter_effectively_closed_open_items(
                conn,
                self._rows_to_entries(conn, rows),
            )
            return entries[:limit]

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

    def context(
        self,
        project: str,
        *,
        n_recent: int = 10,
        include_cross_project: bool = True,
    ) -> JournalContext:
        """Return curated session-start context for ``project``."""

        self._validate_project_arg(project)
        project = project.strip()
        self._validate_context_n_recent_arg(n_recent)
        if not isinstance(include_cross_project, bool):
            raise TypeError("include_cross_project must be a bool")

        recent = tuple(self.read(project, n_recent))
        digest = self.digest(project=project, since="30d", limit=100)
        suggested_searches = _context_suggested_searches(
            recent=recent,
            digest=digest,
        )
        related_projects: tuple[str, ...] = ()
        if include_cross_project and suggested_searches:
            related_projects = _context_related_projects(
                db=self,
                project=project,
                suggested_searches=suggested_searches,
            )

        return JournalContext(
            project=project,
            recent=recent,
            open_items=tuple(digest.open_items[:n_recent]),
            recent_bugfixes=tuple(digest.bugfixes[:n_recent]),
            recent_decisions=tuple(digest.decisions[:n_recent]),
            related_projects=related_projects,
            suggested_searches=suggested_searches,
        )

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
        entry_ids = [row["id"] for row in rows]
        tags_by_entry_id = _tags_by_entry_id(conn, entry_ids)
        links_by_entry_id = _outgoing_links_by_entry_id(conn, entry_ids)
        backlinks_by_entry_id = _incoming_links_by_entry_id(conn, entry_ids)
        return [
            JournalDB._row_to_entry(
                row,
                tags=tags_by_entry_id.get(row["id"], ()),
                links=links_by_entry_id.get(row["id"], ()),
                backlinks=backlinks_by_entry_id.get(row["id"], ()),
            )
            for row in rows
        ]

    @staticmethod
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

    @staticmethod
    def _validate_search_all_limit_arg(value: object) -> None:
        JournalDB._validate_limit_arg(value, name="limit")
        if value > _SEARCH_ALL_LIMIT_MAX:
            raise ValueError(f"limit must be at most {_SEARCH_ALL_LIMIT_MAX}")

    @staticmethod
    def _validate_digest_limit_arg(value: object) -> None:
        JournalDB._validate_limit_arg(value, name="limit")
        if value > _DIGEST_LIMIT_MAX:
            raise ValueError(f"limit must be at most {_DIGEST_LIMIT_MAX}")

    @staticmethod
    def _validate_context_n_recent_arg(value: object) -> None:
        JournalDB._validate_limit_arg(value, name="n_recent")
        if value > _CONTEXT_N_RECENT_MAX:
            raise ValueError(f"n_recent must be at most {_CONTEXT_N_RECENT_MAX}")

    @staticmethod
    def _validate_open_items_limit_arg(value: object) -> None:
        JournalDB._validate_limit_arg(value, name="limit")
        if value > _OPEN_ITEMS_LIMIT_MAX:
            raise ValueError(f"limit must be at most {_OPEN_ITEMS_LIMIT_MAX}")


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


def _insert_entry_links(
    conn: sqlite3.Connection,
    entry_id: int,
    links: tuple[JournalEntryLink, ...],
    timestamp: str,
) -> None:
    if not links:
        return
    if any(link.entry_id == entry_id for link in links):
        raise ValueError("journal entry cannot link to itself")

    target_ids = sorted({link.entry_id for link in links})
    placeholders = ",".join("?" for _ in target_ids)
    existing_ids = {
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM journal_entries WHERE id IN ({placeholders})",
            target_ids,
        ).fetchall()
    }
    missing_ids = [target_id for target_id in target_ids if target_id not in existing_ids]
    if missing_ids:
        missing = ", ".join(str(target_id) for target_id in missing_ids)
        raise ValueError(f"link target entry id does not exist: {missing}")

    conn.executemany(
        """
        INSERT INTO journal_entry_links
            (from_entry_id, to_entry_id, relation, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (entry_id, link.entry_id, link.relation.value, timestamp)
            for link in links
        ],
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


def _normalise_text_filter_values(values: object, *, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings, not a string")
    try:
        iterator = iter(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable of strings") from exc

    normalised: set[str] = set()
    for value in iterator:
        if not isinstance(value, str):
            raise TypeError(f"{name} must contain only strings")
        value = value.strip()
        if not value:
            raise ValueError(f"{name} must not contain empty values")
        normalised.add(value)
    return tuple(sorted(normalised))


def _normalise_status_filter_values(values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise TypeError("statuses must be an iterable of strings, not a string")
    try:
        iterator = iter(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("statuses must be an iterable of strings") from exc

    normalised: set[str] = set()
    for value in iterator:
        if isinstance(value, JournalStatus):
            normalised.add(value.value)
            continue
        if not isinstance(value, str):
            raise TypeError("statuses must contain only strings or JournalStatus")
        try:
            normalised.add(JournalStatus(value.strip()).value)
        except ValueError as exc:
            allowed = ", ".join(status.value for status in JournalStatus)
            raise ValueError(
                f"status {value!r} is not allowed; must be one of: {allowed}"
            ) from exc
    return tuple(sorted(normalised))


def _normalise_tag_filter_values(values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise TypeError("tags must be an iterable of strings, not a string")
    try:
        iterator = iter(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("tags must be an iterable of strings") from exc

    normalised: set[str] = set()
    for value in iterator:
        if not isinstance(value, str):
            raise TypeError("tags must contain only strings")
        tag = value.strip().lower()
        if not tag:
            raise ValueError("tags must not contain empty values")
        if not _TAG_FILTER_RE.fullmatch(tag):
            raise ValueError(
                f"tag {tag!r} must match kebab-case pattern "
                "^[a-z0-9][a-z0-9-]*$"
            )
        normalised.add(tag)
    return tuple(sorted(normalised))


def _resolve_digest_range(
    *,
    since: str,
    until: str | None,
    now: datetime | None,
) -> tuple[datetime, datetime]:
    if now is None:
        range_until = datetime.now(tz=UTC)
    else:
        range_until = _normalise_digest_datetime(now, name="now")
    if until is not None:
        range_until = _parse_digest_timestamp(until, name="until")
    range_since = _parse_digest_since(since, range_until=range_until)
    if range_until <= range_since:
        raise ValueError("until must be after since")
    return range_since, range_until


def _parse_digest_since(value: str, *, range_until: datetime) -> datetime:
    if not isinstance(value, str):
        raise TypeError("since must be a string")
    value = value.strip()
    if not value:
        raise ValueError("since must not be empty")
    if value in _DIGEST_RELATIVE_RANGES:
        return range_until - _DIGEST_RELATIVE_RANGES[value]
    try:
        return _parse_digest_timestamp(value, name="since")
    except ValueError as exc:
        allowed = ", ".join(_DIGEST_RELATIVE_RANGES)
        raise ValueError(
            f"since must be one of {allowed} or an ISO timestamp"
        ) from exc


def _parse_digest_timestamp(value: str, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    return _normalise_digest_datetime(timestamp, name=name)


def _normalise_digest_datetime(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _digest_where_clause(
    *,
    project: str | None,
    range_since: datetime,
    range_until: datetime,
    tags: tuple[str, ...],
) -> tuple[list[str], list[object]]:
    where = ["je.timestamp >= ?", "je.timestamp <= ?"]
    params: list[object] = [range_since.isoformat(), range_until.isoformat()]
    if project is not None:
        where.append("je.project = ?")
        params.append(project)
    for index, tag in enumerate(tags):
        where.append(
            "EXISTS ("
            f"SELECT 1 FROM journal_entry_tags jet{index} "
            f"WHERE jet{index}.entry_id = je.id AND jet{index}.tag = ?"
            ")"
        )
        params.append(tag)
    return where, params


def _entry_is_decision(entry: JournalEntry) -> bool:
    if "decision" in entry.tags:
        return True
    return bool(
        _DECISION_RE.search(entry.title or "")
        or _DECISION_RE.search(entry.content)
    )


def _filter_effectively_closed_open_items(
    conn: sqlite3.Connection,
    entries: list[JournalEntry],
) -> list[JournalEntry]:
    if not entries:
        return entries

    by_project: dict[str, list[JournalEntry]] = {}
    for entry in entries:
        by_project.setdefault(entry.project, []).append(entry)

    filtered: list[JournalEntry] = []
    for project, project_entries in by_project.items():
        closed_ids = _infer_closed_open_item_ids(
            conn,
            project=project,
            open_item_ids=[entry.id for entry in project_entries if entry.id is not None],
        )
        filtered.extend(
            entry
            for entry in project_entries
            if entry.id is None or entry.id not in closed_ids
        )

    filtered.sort(key=lambda entry: (entry.timestamp, entry.id or 0), reverse=True)
    return filtered


def _infer_closed_open_item_ids(
    conn: sqlite3.Connection,
    *,
    project: str,
    open_item_ids: list[int],
) -> set[int]:
    if not open_item_ids:
        return set()

    placeholders = ",".join("?" for _ in open_item_ids)
    closing_statuses = (
        JournalStatus.ABGESCHLOSSEN.value,
        JournalStatus.BUGFIX.value,
        JournalStatus.NOTIZ.value,
    )
    status_placeholders = ",".join("?" for _ in closing_statuses)
    rows = conn.execute(
        f"""
        SELECT id, content
        FROM journal_entries
        WHERE project = ?
          AND status IN ({status_placeholders})
          AND (
                content LIKE '%#% -> #%'
                OR EXISTS (
                    SELECT 1
                    FROM journal_entry_links l
                    WHERE l.from_entry_id = journal_entries.id
                      AND l.relation IN ('corrects', 'supersedes', 'implements')
                      AND l.to_entry_id IN ({placeholders})
                )
          )
        """,
        [project, *closing_statuses, *open_item_ids],
    ).fetchall()

    closed_ids: set[int] = set()
    for row in rows:
        content = row["content"] or ""
        for match in _OPEN_ITEMS_CLOSING_RE.finditer(content):
            open_id = int(match.group("open_id"))
            if open_id in open_item_ids:
                closed_ids.add(open_id)

    if rows:
        link_rows = conn.execute(
            f"""
            SELECT l.to_entry_id
            FROM journal_entry_links l
            JOIN journal_entries je ON je.id = l.from_entry_id
            WHERE je.project = ?
              AND je.status IN ({status_placeholders})
              AND l.relation IN ('corrects', 'supersedes', 'implements')
              AND l.to_entry_id IN ({placeholders})
            """,
            [project, *closing_statuses, *open_item_ids],
        ).fetchall()
        closed_ids.update(int(row["to_entry_id"]) for row in link_rows)

    return closed_ids


def _context_suggested_searches(
    *,
    recent: tuple[JournalEntry, ...],
    digest: JournalDigest,
) -> tuple[str, ...]:
    suggestions: list[str] = []
    seen: set[str] = set()

    for entry in recent:
        if entry.phase and entry.phase not in seen:
            suggestions.append(entry.phase)
            seen.add(entry.phase)

    lowered_text = "\n".join(
        (
            f"{entry.title or ''}\n{entry.content}\n{' '.join(entry.tags)}"
            for entry in digest.entries
        )
    ).lower()
    for term in _CONTEXT_SUGGESTION_TERMS:
        if term == "bugfix":
            has_term = bool(digest.bugfixes)
        elif term == "decision":
            has_term = bool(digest.decisions)
        else:
            has_term = term in lowered_text
        if has_term and term not in seen:
            suggestions.append(term)
            seen.add(term)
    return tuple(suggestions[:_CONTEXT_SUGGESTIONS_MAX])


def _context_related_projects(
    *,
    db: JournalDB,
    project: str,
    suggested_searches: tuple[str, ...],
) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for query in suggested_searches:
        for hit in db.search_all(query, limit=_CONTEXT_RELATED_SEARCH_LIMIT):
            if hit.project == project:
                continue
            counts[hit.project] = counts.get(hit.project, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        name for name, _ in ranked[:_CONTEXT_RELATED_PROJECTS_MAX]
    )
