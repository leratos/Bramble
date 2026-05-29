"""Aggregate summary for a single project.

The :class:`ProjectSummary` is the return type of
:meth:`bramble.journal_db.JournalDB.project_overview`. It carries the
pieces of information the ``journal_list_projects`` MCP tool needs to
answer *"what projects exist, what has been written where, and how
recently?"* without forcing callers to issue a separate query per
project.

Design notes:

* The class mirrors :class:`bramble.journal_entry.JournalEntry` in
  spirit: ``frozen`` to prevent accidental mutation, with eager
  validation in ``__post_init__``.
* ``last_timestamp`` is a timezone-aware ``datetime`` in UTC when a
  project has entries, and ``None`` for registered projects that are
  still empty.
* ``entry_count`` may be zero for projects that exist in the registry
  before their first journal entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    """Aggregate counts and recency for one project.

    Parameters
    ----------
    name:
        The project identifier as stored in the database. Non-empty
        after stripping whitespace.
    entry_count:
        Number of journal entries for this project. Must be ``>= 0``.
    last_timestamp:
        Timestamp of the most recent entry, as a timezone-aware UTC
        ``datetime``. ``None`` when the project has no entries yet.
    """

    name: str
    entry_count: int
    last_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        self._validate_name()
        self._validate_entry_count()
        self._validate_timestamp()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def _validate_name(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        stripped = self.name.strip()
        if not stripped:
            raise ValueError("name must not be empty")
        if stripped != self.name:
            object.__setattr__(self, "name", stripped)

    def _validate_entry_count(self) -> None:
        # bool is a subclass of int; exclude it explicitly.
        if isinstance(self.entry_count, bool) or not isinstance(self.entry_count, int):
            raise TypeError("entry_count must be an int")
        if self.entry_count < 0:
            raise ValueError("entry_count must be >= 0")

    def _validate_timestamp(self) -> None:
        if self.last_timestamp is None:
            return
        if not isinstance(self.last_timestamp, datetime):
            raise TypeError("last_timestamp must be a datetime or None")
        if self.last_timestamp.tzinfo is None or self.last_timestamp.tzinfo.utcoffset(
            self.last_timestamp
        ) is None:
            raise ValueError(
                "last_timestamp must be timezone-aware; pass datetime in UTC"
            )
        if self.last_timestamp.utcoffset() != UTC.utcoffset(self.last_timestamp):
            object.__setattr__(
                self,
                "last_timestamp",
                self.last_timestamp.astimezone(UTC),
            )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------
    def last_timestamp_iso(self) -> str | None:
        """Return :attr:`last_timestamp` as an ISO-8601 string (UTC)."""

        if self.last_timestamp is None:
            return None
        return self.last_timestamp.isoformat()
