"""Tests for :mod:`bramble.token_store`."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from bramble.token_store import TokenStore, load_token_map, write_token_map


def test_missing_token_file_lists_no_projects(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "tokens.json")

    assert store.list_tokens() == []


def test_create_writes_token_once_and_lists_only_metadata(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "tokens.json"
    store = TokenStore(path, token_factory=lambda: "new-token")

    mutation = store.create("bramble")

    assert mutation.project == "bramble"
    assert mutation.action == "created"
    assert mutation.token == "new-token"
    assert json.loads(path.read_text(encoding="utf-8")) == {"bramble": "new-token"}
    assert store.list_tokens()[0].project == "bramble"


def test_rotate_keeps_other_projects(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    write_token_map(path, {"bramble": "old", "elder-berry": "elder"})
    store = TokenStore(path, token_factory=lambda: "rotated")

    mutation = store.rotate("bramble")

    assert mutation.token == "rotated"
    assert load_token_map(path) == {"bramble": "rotated", "elder-berry": "elder"}


def test_revoke_removes_only_target_project(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    write_token_map(path, {"bramble": "old", "elder-berry": "elder"})
    store = TokenStore(path)

    mutation = store.revoke("bramble")

    assert mutation.action == "revoked"
    assert mutation.token is None
    assert load_token_map(path) == {"elder-berry": "elder"}


def test_rejects_non_kebab_project(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "tokens.json")

    with pytest.raises(ValueError, match="kebab-case"):
        store.create("Bad_Name")


def test_rejects_duplicate_existing_tokens(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps({"bramble": "shared", "elder-berry": "shared"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="share the same token"):
        load_token_map(path)


def test_token_file_is_owner_only_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX owner-only mode bits are not reliable on Windows")
    path = tmp_path / "tokens.json"

    write_token_map(path, {"bramble": "token"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
