"""Structured read model for curated journal session context."""

from __future__ import annotations

from dataclasses import dataclass

from bramble.journal_entry import JournalEntry
from bramble.open_item import OpenItemView


@dataclass(frozen=True, slots=True)
class JournalContext:
    """Deterministic session-start context for one project.

    ``open_items`` carries :class:`OpenItemView` values (not bare entries):
    the session-start context that agents read first uses the same
    append-only closure inference as ``journal_open_items``, so an item
    that a later entry marked done is not reported as open here either.
    Effectively-closed items are excluded; ``stale`` items are kept and
    flagged.
    """

    project: str
    recent: tuple[JournalEntry, ...]
    open_items: tuple[OpenItemView, ...]
    recent_bugfixes: tuple[JournalEntry, ...]
    recent_decisions: tuple[JournalEntry, ...]
    related_projects: tuple[str, ...]
    suggested_searches: tuple[str, ...]
