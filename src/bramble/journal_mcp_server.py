"""Bramble's MCP server façade.

The :class:`JournalMCPServer` owns the FastMCP instance and the tool
registrations. It deliberately keeps the FastMCP setup and the
:class:`JournalDB` accessor separate: ``app`` is the thing FastMCP
needs, ``db`` is the thing tools talk to. Tests construct the server
with an in-memory or temp-file ``JournalDB`` and connect via the
FastMCP in-process ``Client``.

Phase-3 hook points (``auth_validator``, ``rate_limiter``) are kept as
optional keyword arguments in the constructor. They are accepted but
not yet consumed; Phase 3 will wire them into the request path without
having to change call sites.

Tool registration happens once in :meth:`__init__` via
:meth:`_register_tools`, which is the file's central index of which
MCP tools exist. Tools themselves come in Etappen 4a–4d.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastmcp import FastMCP

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.mcp_errors import translate_errors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation helpers (Decision E: kebab-case enforced in the MCP layer)
# ---------------------------------------------------------------------------
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _require_kebab_case(project: str) -> None:
    """Reject any project identifier that is not strict kebab-case.

    The pattern matches lowercase letters, digits and hyphens with a
    non-hyphen first character. Whitespace, underscores, mixed case
    and empty strings all fail. The check lives in the MCP layer only
    – :class:`JournalDB` itself remains project-agnostic.
    """

    if not isinstance(project, str):
        raise TypeError("project must be a string")
    if not _KEBAB_CASE_RE.match(project):
        raise ValueError(
            f"project {project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$"
        )


def _entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    """Serialise a :class:`JournalEntry` to a plain MCP-friendly dict."""

    return {
        "id": entry.id,
        "project": entry.project,
        "timestamp": entry.timestamp_iso(),
        "status": entry.status.value,
        "phase": entry.phase,
        "title": entry.title,
        "content": entry.content,
    }


class JournalMCPServer:
    """MCP-facing server that exposes :class:`JournalDB` operations.

    Parameters
    ----------
    db:
        The :class:`JournalDB` instance to read from / write to. Must
        already be initialised (``db.initialize()`` was called).
    auth_validator:
        Phase-3 hook. Currently unused; reserved so Phase-3 wiring
        does not require a constructor signature change.
    rate_limiter:
        Phase-3 hook. Same rationale as ``auth_validator``.
    """

    def __init__(
        self,
        db: JournalDB,
        *,
        auth_validator: Any = None,
        rate_limiter: Any = None,
    ) -> None:
        if not isinstance(db, JournalDB):
            raise TypeError("db must be a JournalDB instance")

        self._db = db
        self._auth_validator = auth_validator
        self._rate_limiter = rate_limiter

        self._app: FastMCP = FastMCP(
            name="bramble",
            instructions=(
                "Shared development journal across projects. "
                "Use journal_append to record new entries, journal_read "
                "to fetch recent entries, journal_search for full-text "
                "search, and journal_list_projects for an overview."
            ),
        )
        self._register_tools()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------
    @property
    def app(self) -> FastMCP:
        """The underlying FastMCP application.

        Used by tests (passed to :class:`fastmcp.Client` for in-process
        calls) and by :mod:`bramble.__main__` to call ``run()``.
        """

        return self._app

    @property
    def db(self) -> JournalDB:
        """The :class:`JournalDB` instance the tools operate on."""

        return self._db

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------
    def _register_tools(self) -> None:
        """Register all MCP tools on :attr:`app`.

        All four tools live in this single method on purpose: the
        ``self`` closure means each tool implicitly carries the
        :class:`JournalDB` it talks to, and keeping the registrations
        together makes the public surface of the server obvious at a
        glance.
        """

        app = self._app
        db = self._db

        @app.tool
        @translate_errors
        async def journal_read(project: str, n: int = 80) -> list[dict[str, Any]]:
            """Return the ``n`` most recent journal entries for ``project``.

            Entries are returned newest first. ``project`` must match
            the kebab-case pattern ``^[a-z0-9][a-z0-9-]*$``.
            """

            _require_kebab_case(project)
            entries = await asyncio.to_thread(db.read, project, n)
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_append(
            project: str,
            status: str,
            content: str,
            phase: str | None = None,
            title: str | None = None,
        ) -> dict[str, Any]:
            """Append a new journal entry and return it with its assigned id.

            ``status`` must be one of: ``in_arbeit``, ``abgeschlossen``,
            ``notiz``, ``bugfix``. The timestamp is set server-side
            (``datetime.now(UTC)``); clients cannot override it.
            """

            _require_kebab_case(project)
            allowed = ", ".join(s.value for s in JournalStatus)
            try:
                status_enum = JournalStatus(status)
            except ValueError as exc:
                raise ValueError(
                    f"status {status!r} is not allowed; must be one of: {allowed}"
                ) from exc

            entry = JournalEntry(
                project=project,
                status=status_enum,
                content=content,
                phase=phase,
                title=title,
            )
            persisted = await asyncio.to_thread(db.append, entry)
            return _entry_to_dict(persisted)

        @app.tool
        @translate_errors
        async def journal_search(
            project: str,
            query: str,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            """Full-text-search ``project``'s entries for ``query``.

            Pass SQLite FTS5 MATCH syntax directly: bare words for AND,
            ``OR`` for alternation, double-quoted strings for phrase
            search, and ``NEAR()`` for proximity. Malformed FTS5
            syntax returns an empty list (not an error) to match the
            Phase-1 ``JournalDB.search`` behaviour.
            """

            _require_kebab_case(project)
            entries = await asyncio.to_thread(db.search, project, query, limit)
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_list_projects() -> list[dict[str, Any]]:
            """List all projects with entry counts and most recent timestamps.

            Returned in descending order of ``last_timestamp`` (most
            recent activity first), with alphabetical tie-break for
            stability.
            """

            summaries = await asyncio.to_thread(db.project_overview)
            return [
                {
                    "project": s.name,
                    "entry_count": s.entry_count,
                    "last_timestamp": s.last_timestamp_iso(),
                }
                for s in summaries
            ]

    # ------------------------------------------------------------------
    # Transport entry point
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        transport: str = "stdio",
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        """Start serving via the requested transport (blocks).

        :param transport: ``"stdio"`` for Claude Desktop / Code, or
            ``"http"`` for the HTTP stub (Phase 3 puts Nginx + auth in
            front).
        :param host: Bind host for ``http`` transport.
        :param port: Bind port for ``http`` transport.
        """

        if transport == "stdio":
            logger.info("starting MCP server on stdio")
            self._app.run(transport="stdio")
        elif transport == "http":
            if host is None or port is None:
                raise ValueError("http transport requires host and port")
            logger.info(
                "starting MCP server on http",
                extra={"host": host, "port": port},
            )
            self._app.run(transport="http", host=host, port=port)
        else:
            raise ValueError(
                f"unsupported transport {transport!r}; expected 'stdio' or 'http'"
            )
