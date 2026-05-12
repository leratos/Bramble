"""CLI entry point for the Bramble MCP server.

Wires the four building blocks Phase 2 added together:

* :class:`bramble.server_config.ServerConfig` resolves CLI / env /
  default values.
* :func:`bramble.logging_setup.configure_logging` installs the JSON
  log handler on stderr.
* :class:`bramble.journal_db.JournalDB` is opened and initialised.
* :class:`bramble.journal_mcp_server.JournalMCPServer` is constructed
  with that DB and started on the configured transport.

Invoked via ``python -m bramble`` or the ``bramble-server`` console
script defined in :file:`pyproject.toml`.
"""

from __future__ import annotations

import logging

from bramble.journal_db import JournalDB
from bramble.journal_mcp_server import JournalMCPServer
from bramble.logging_setup import configure_logging
from bramble.server_config import ServerConfig

logger = logging.getLogger(__name__)


def main() -> None:
    """Resolve configuration, prepare the DB, and start serving."""

    config = ServerConfig.from_sources()
    configure_logging(config.log_level)

    db = JournalDB(config.db_path)
    db.initialize()
    logger.info(
        "bramble-server starting",
        extra={
            "db_path": str(config.db_path),
            "transport": config.transport,
        },
    )

    server = JournalMCPServer(db)
    if config.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="http", host=config.host, port=config.port)


if __name__ == "__main__":
    main()
