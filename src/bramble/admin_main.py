"""CLI entry point for the separate Bramble admin UI server."""

from __future__ import annotations

import logging

import uvicorn

from bramble.admin_app import create_admin_app
from bramble.admin_auth import AdminAuthenticator
from bramble.admin_config import AdminConfig
from bramble.journal_db import JournalDB
from bramble.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Resolve admin config, prepare dependencies, and run uvicorn."""

    config = AdminConfig.from_sources()
    configure_logging(config.log_level)

    if not config.db_path.exists():
        raise FileNotFoundError(
            f"admin database {config.db_path} does not exist; "
            "initialise it with bramble-server or scripts/init_db.py first"
        )

    db = JournalDB(config.db_path)
    db.initialize()
    authenticator = AdminAuthenticator(config.admin_secret_file)
    app = create_admin_app(db, authenticator, config=config)

    logger.info(
        "bramble-admin starting",
        extra={
            "db_path": str(config.db_path),
            "host": config.host,
            "port": config.port,
        },
    )
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
