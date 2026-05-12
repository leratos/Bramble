"""Tests for the JournalMCPServer scaffolding (Etappe 3).

Tools themselves are added in later etappen; these tests verify
construction, DI, the public surface, and transport pre-validation.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastmcp import Client, FastMCP

from bramble.journal_db import JournalDB
from bramble.journal_mcp_server import JournalMCPServer


@pytest.fixture()
def server(db: JournalDB) -> Iterator[JournalMCPServer]:
    yield JournalMCPServer(db)


# ---------------------------------------------------------------------------
# Construction & DI
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_accepts_journal_db_instance(self, db: JournalDB) -> None:
        srv = JournalMCPServer(db)
        assert srv.db is db

    def test_rejects_non_journal_db(self) -> None:
        with pytest.raises(TypeError, match="JournalDB"):
            JournalMCPServer("not a db")  # type: ignore[arg-type]

    def test_app_property_returns_fastmcp_instance(self, server: JournalMCPServer) -> None:
        assert isinstance(server.app, FastMCP)

    def test_phase_3_hooks_default_to_none(self, db: JournalDB) -> None:
        srv = JournalMCPServer(db)
        # We access private attrs deliberately – this test is the
        # contract that the slots exist and start as None for Phase 3.
        assert srv._auth_validator is None
        assert srv._rate_limiter is None

    def test_phase_3_hooks_accept_arbitrary_objects(self, db: JournalDB) -> None:
        sentinel_auth = object()
        sentinel_rl = object()
        srv = JournalMCPServer(
            db,
            auth_validator=sentinel_auth,
            rate_limiter=sentinel_rl,
        )
        assert srv._auth_validator is sentinel_auth
        assert srv._rate_limiter is sentinel_rl


# ---------------------------------------------------------------------------
# Tool registry (state at end of Etappe 3)
# ---------------------------------------------------------------------------
class TestToolRegistry:
    async def test_no_tools_registered_yet(self, server: JournalMCPServer) -> None:
        # Etappe 3 only wires the scaffolding. Tools are added in 4a-d.
        async with Client(server.app) as client:
            tools = await client.list_tools()
        assert tools == []


# ---------------------------------------------------------------------------
# Transport pre-validation
# ---------------------------------------------------------------------------
class TestRunPreValidation:
    def test_unknown_transport_raises(self, server: JournalMCPServer) -> None:
        with pytest.raises(ValueError, match="unsupported transport"):
            server.run(transport="websocket")

    def test_http_without_host_raises(self, server: JournalMCPServer) -> None:
        with pytest.raises(ValueError, match="host and port"):
            server.run(transport="http", host=None, port=8765)

    def test_http_without_port_raises(self, server: JournalMCPServer) -> None:
        with pytest.raises(ValueError, match="host and port"):
            server.run(transport="http", host="127.0.0.1", port=None)
