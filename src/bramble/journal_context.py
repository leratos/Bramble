"""Structured read model for curated journal session context."""

from __future__ import annotations

from dataclasses import dataclass

from bramble.journal_entry import JournalEntry


@dataclass(frozen=True, slots=True)
class JournalContext:
    """Deterministic session-start context for one project."""

    project: str
    recent: tuple[JournalEntry, ...]
    open_items: tuple[JournalEntry, ...]
    recent_bugfixes: tuple[JournalEntry, ...]
    recent_decisions: tuple[JournalEntry, ...]
    related_projects: tuple[str, ...]
    suggested_searches: tuple[str, ...]
