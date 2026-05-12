"""Initialise (or migrate) a Bramble SQLite database.

Usage
-----

    python scripts/init_db.py [path/to/bramble.db]

If no path is given, the script defaults to ``./data/bramble.db`` next
to the project root. The script is idempotent: it can safely be run
against an existing database.

The script also performs a quick FTS5 availability check. If the
underlying SQLite build is missing FTS5, the script exits with code
2 and prints a helpful error message – this is the most common
deployment failure on minimal Linux images.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Make ``src/`` importable when running this script directly without
# the package being installed (e.g. fresh clone, no ``pip install -e``).
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bramble.journal_db import JournalDB  # noqa: E402  (sys.path setup above)


DEFAULT_DB_PATH = ROOT / "data" / "bramble.db"


def check_fts5_available() -> None:
    """Raise :class:`SystemExit` if the SQLite build lacks FTS5."""

    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
    except sqlite3.OperationalError as exc:
        print(
            "ERROR: This Python's sqlite3 module was built without FTS5 "
            "support. Bramble requires FTS5 for full-text search.\n"
            f"  sqlite version: {sqlite3.sqlite_version}\n"
            f"  underlying error: {exc}\n"
            "  fix: use the official python.org build (or a distro "
            "package that ships SQLite with FTS5 enabled).",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"path to the SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    check_fts5_available()

    db = JournalDB(args.db_path)
    db.initialize()
    print(f"Bramble DB initialised at {args.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
