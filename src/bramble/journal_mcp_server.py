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

import logging
from typing import Any

from fastmcp import FastMCP

from bramble.journal_db import JournalDB

logger = logging.getLogger(__name__)


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

        Tools are added in Phase-2 Etappen 4a–4d. The empty body here
        is on purpose – it gives a single place where the four tool
        registrations will live, instead of scattering ``@app.tool``
        decorators across the module.
        """

        # Etappe 4a: journal_read
        # Etappe 4b: journal_append
        # Etappe 4c: journal_search
        # Etappe 4d: journal_list_projects

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
