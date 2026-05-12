"""Unit tests for :mod:`bramble.mcp_errors`."""

from __future__ import annotations

import logging

import pytest
from fastmcp.exceptions import ToolError

from bramble.mcp_errors import translate_errors


class TestTranslateErrors:
    async def test_returns_value_on_happy_path(self) -> None:
        @translate_errors
        async def tool(x: int) -> int:
            return x * 2

        assert await tool(21) == 42

    async def test_value_error_becomes_tool_error(self) -> None:
        @translate_errors
        async def tool() -> None:
            raise ValueError("project must not be empty")

        with pytest.raises(ToolError, match="project must not be empty"):
            await tool()

    async def test_type_error_becomes_tool_error(self) -> None:
        @translate_errors
        async def tool() -> None:
            raise TypeError("n must be an int")

        with pytest.raises(ToolError, match="n must be an int"):
            await tool()

    async def test_tool_error_preserves_original_cause(self) -> None:
        original = ValueError("status xyz is not allowed")

        @translate_errors
        async def tool() -> None:
            raise original

        with pytest.raises(ToolError) as info:
            await tool()
        assert info.value.__cause__ is original

    async def test_unknown_error_becomes_runtime_error_and_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @translate_errors
        async def tool() -> None:
            raise KeyError("missing key")

        with caplog.at_level(logging.ERROR, logger="bramble.mcp_errors"):
            with pytest.raises(RuntimeError, match="internal error in tool"):
                await tool()

        # The original KeyError must appear in the logs (via exc_info)
        # so the operator can diagnose it, but never in the message
        # returned to the client.
        with_exc = [r for r in caplog.records if r.exc_info and r.exc_info[0] is KeyError]
        assert with_exc, "expected an ERROR record carrying the KeyError"

    async def test_preserves_function_name(self) -> None:
        @translate_errors
        async def journal_read_demo() -> int:
            return 0

        assert journal_read_demo.__name__ == "journal_read_demo"

    async def test_value_error_is_logged_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @translate_errors
        async def tool() -> None:
            raise ValueError("bad input")

        with caplog.at_level(logging.WARNING, logger="bramble.mcp_errors"):
            with pytest.raises(ToolError):
                await tool()

        assert any("rejected input" in rec.message for rec in caplog.records)
