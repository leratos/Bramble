"""Bramble – self-hosted MCP journal server.

Phase 1 exports the core data classes. The MCP server (Phase 2) will be
added in :mod:`bramble.journal_mcp_server` and import from here.
"""

from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus

__all__ = ["JournalDB", "JournalEntry", "JournalStatus"]
__version__ = "0.1.0"
