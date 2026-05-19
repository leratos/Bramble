"""CLI entry point for the Bramble MCP server.

Wires the building blocks together:

* :class:`bramble.server_config.ServerConfig` resolves CLI / env /
  default values.
* :func:`bramble.logging_setup.configure_logging` installs the JSON
  log handler on stderr.
* :class:`bramble.journal_db.JournalDB` is opened and initialised.
* For the ``http`` transport, :class:`bramble.auth_validator.AuthValidator`
  and :class:`bramble.rate_limiter.RateLimiter` are built and handed
  to the server so every request is gated by a bearer token and a
  rate limit. ``stdio`` is local and runs without that gate.
* :class:`bramble.journal_mcp_server.JournalMCPServer` is constructed
  with that DB and started on the configured transport.

Invoked via ``python -m bramble`` or the ``bramble-server`` console
script defined in :file:`pyproject.toml`.
"""

from __future__ import annotations

import logging

from bramble.auth_validator import AuthValidator
from bramble.journal_db import JournalDB
from bramble.journal_mcp_server import JournalMCPServer
from bramble.logging_setup import configure_logging
from bramble.rate_limiter import RateLimiter
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

    if config.transport == "stdio":
        server = JournalMCPServer(db)
        server.run(transport="stdio")
    else:
        auth_validator = AuthValidator(config.tokens_file)
        rate_limiter = RateLimiter(
            per_token_rpm=config.rate_limit_per_token,
            per_ip_rpm=config.rate_limit_per_ip,
        )
        server = JournalMCPServer(
            db, auth_validator=auth_validator, rate_limiter=rate_limiter
        )
        server.run(transport="http", host=config.host, port=config.port)


if __name__ == "__main__":
    main()
