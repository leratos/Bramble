"""Shared pytest fixtures for the Bramble test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bramble.journal_db import JournalDB


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a fresh, isolated SQLite path for each test."""

    return tmp_path / "bramble_test.db"


@pytest.fixture()
def db(db_path: Path) -> Iterator[JournalDB]:
    """Return an initialised :class:`JournalDB` bound to ``db_path``."""

    instance = JournalDB(db_path)
    instance.initialize()
    yield instance
