"""Centralised JSON-logging setup for the Bramble MCP server.

Per Decision I in the Phase-2 concept document, logs are emitted as
JSON from Phase 2 onwards so Phase 3's Fail2Ban filter has a stable
shape to parse.

Module-level loggers (``logging.getLogger(__name__)``) are used
everywhere else in the codebase; this module configures the root
logger and the handler/formatter once at startup.

The handler writes to **stderr**, never stdout: when the MCP server
runs with the ``stdio`` transport, stdout is reserved for the MCP
protocol stream itself. Mixing log output into stdout corrupts the
transport.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

# Public so other modules can refer to it (e.g. when adding their own
# handler in tests). Format includes the standard fields every log
# line should carry; `extra=` keys add tool/project/etc. on top.
_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

# Marker attribute used to detect a handler we installed, so that
# repeated calls to configure_logging() are idempotent without
# clobbering handlers other code may have added (e.g. caplog in tests).
_BRAMBLE_HANDLER_MARKER = "_bramble_json_handler"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger to emit JSON to stderr.

    Idempotent: calling repeatedly with the same level leaves a single
    Bramble handler installed. Calling with a different level updates
    both the root logger and the handler.

    :param level: A standard Python logging level name. Validation is
        done by :class:`bramble.server_config.ServerConfig`; this
        function trusts its caller.
    """

    root = logging.getLogger()
    root.setLevel(level)

    for existing in root.handlers:
        if getattr(existing, _BRAMBLE_HANDLER_MARKER, False):
            existing.setLevel(level)
            return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter(_LOG_FORMAT))
    setattr(handler, _BRAMBLE_HANDLER_MARKER, True)
    root.addHandler(handler)
