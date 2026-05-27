"""Generate the Argon2id admin secret file for bramble-admin."""

from __future__ import annotations

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bramble.admin_auth import hash_admin_password  # noqa: E402


def main(
    argv: list[str] | None = None,
    *,
    password_reader: Callable[[str], str] = getpass,
) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)

    username = ns.username.strip()
    if not username:
        parser.error("--username must not be empty")

    path = Path(ns.output)
    if path.exists() and not ns.force:
        parser.error(f"{path} already exists; pass --force to replace it")

    password = password_reader("Admin password: ")
    confirmation = password_reader("Repeat admin password: ")
    if not password:
        parser.error("password must not be empty")
    if password != confirmation:
        parser.error("passwords do not match")

    payload = {
        "username": username,
        "password_hash": hash_admin_password(password),
    }
    _write_secret(path, payload)
    print(f"Wrote admin secret to {path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen_admin_secret.py",
        description="Create an admin-ui.json file for bramble-admin.",
    )
    parser.add_argument(
        "--output",
        default="./secrets/admin-ui.json",
        help="Secret file path to write.",
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="Admin username to store in the secret file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing secret file.",
    )
    return parser


def _write_secret(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)


if __name__ == "__main__":
    raise SystemExit(main())
