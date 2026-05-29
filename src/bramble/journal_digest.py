"""Structured read model for journal digests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bramble.journal_entry import JournalEntry


@dataclass(frozen=True, slots=True)
class JournalDigest:
    """Deterministic aggregation over a journal time range."""

    range_since: datetime
    range_until: datetime
    projects: tuple[str, ...]
    counts_by_project: dict[str, int]
    counts_by_status: dict[str, int]
    entries: tuple[JournalEntry, ...]
    open_items: tuple[JournalEntry, ...]
    bugfixes: tuple[JournalEntry, ...]
    decisions: tuple[JournalEntry, ...]
