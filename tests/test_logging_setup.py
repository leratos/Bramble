"""Unit tests for :mod:`bramble.logging_setup`."""

from __future__ import annotations

import io
import json
import logging

import pytest

from bramble.logging_setup import _BRAMBLE_HANDLER_MARKER, configure_logging


@pytest.fixture(autouse=True)
def _reset_root_logger() -> None:
    """Snapshot and restore the root logger around each test."""

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    # Drop any Bramble handler from a previous test so configure_logging
    # actually adds one in this test.
    root.handlers = [
        h for h in original_handlers if not getattr(h, _BRAMBLE_HANDLER_MARKER, False)
    ]
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def _bramble_handlers() -> list[logging.Handler]:
    return [
        h
        for h in logging.getLogger().handlers
        if getattr(h, _BRAMBLE_HANDLER_MARKER, False)
    ]


class TestConfigureLogging:
    def test_installs_exactly_one_bramble_handler(self) -> None:
        configure_logging("INFO")
        assert len(_bramble_handlers()) == 1

    def test_is_idempotent(self) -> None:
        configure_logging("INFO")
        configure_logging("INFO")
        configure_logging("INFO")
        assert len(_bramble_handlers()) == 1

    def test_level_can_be_updated_in_place(self) -> None:
        configure_logging("INFO")
        configure_logging("DEBUG")
        [handler] = _bramble_handlers()
        assert handler.level == logging.DEBUG
        assert logging.getLogger().level == logging.DEBUG

    def test_handler_writes_to_stderr(self) -> None:
        import sys

        configure_logging("INFO")
        [handler] = _bramble_handlers()
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr

    def test_output_is_valid_json_with_expected_fields(self) -> None:
        configure_logging("INFO")
        [handler] = _bramble_handlers()

        # Redirect this single handler at a buffer so we can inspect output.
        buffer = io.StringIO()
        original_stream = handler.stream
        handler.stream = buffer
        try:
            logger = logging.getLogger("bramble.test")
            logger.info("hello", extra={"project": "bramble", "tool": "x"})
            handler.flush()
        finally:
            handler.stream = original_stream

        payload = json.loads(buffer.getvalue())
        assert payload["message"] == "hello"
        assert payload["levelname"] == "INFO"
        assert payload["name"] == "bramble.test"
        assert payload["project"] == "bramble"
        assert payload["tool"] == "x"
