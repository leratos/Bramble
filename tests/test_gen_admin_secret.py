"""Tests for :mod:`scripts.gen_admin_secret`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bramble.admin_auth import AdminAuthenticator
from scripts import gen_admin_secret


def test_generates_argon2_secret_file(tmp_path: Path) -> None:
    pytest.importorskip("argon2")
    path = tmp_path / "admin-ui.json"
    passwords = iter(["secret", "secret"])

    rc = gen_admin_secret.main(
        ["--output", str(path), "--username", "admin"],
        password_reader=lambda _prompt: next(passwords),
    )

    assert rc == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["username"] == "admin"
    assert payload["password_hash"].startswith("$argon2id$")
    assert AdminAuthenticator(path).verify("admin", "secret") is True


def test_refuses_to_replace_existing_file_without_force(tmp_path: Path) -> None:
    path = tmp_path / "admin-ui.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        gen_admin_secret.main(
            ["--output", str(path)],
            password_reader=lambda _prompt: "secret",
        )
