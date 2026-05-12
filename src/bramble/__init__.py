"""Bramble – self-hosted MCP journal server.

Top-level convenience exports. Bramble is a tool package, not a
general-purpose library, so the public surface is intentionally flat:
everything that is reasonable to import from outside is reachable
directly from :mod:`bramble`.

* :class:`JournalEntry`, :class:`JournalStatus` – the entry record
  and its allowed status values.
* :class:`JournalDB` – the SQLite persistence layer.
* :class:`ProjectSummary` – aggregate result type of
  :meth:`JournalDB.project_overview`.
* :class:`ServerConfig` – startup configuration (CLI / env / default
  resolution) for the MCP server.
* :class:`JournalMCPServer` – FastMCP façade exposing the four MCP
  tools (``journal_read``, ``journal_append``, ``journal_search``,
  ``journal_list_projects``).
"""

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.journal_mcp_server import JournalMCPServer
from bramble.project_summary import ProjectSummary
from bramble.server_config import ServerConfig

__all__ = [
    "JournalDB",
    "JournalEntry",
    "JournalMCPServer",
    "JournalStatus",
    "ProjectSummary",
    "ServerConfig",
]
__version__ = "0.1.0"
