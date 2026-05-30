"""Open-item view with resolution provenance and staleness.

The journal is append-only: completing work is recorded as a *new*
entry, never by mutating the original ``in_arbeit`` entry. A naive
``status='in_arbeit'`` filter therefore reports every started entry as
"open" forever. :class:`OpenItemView` is the read-side answer to "is
this still open?": it wraps an ``in_arbeit`` :class:`JournalEntry`
together with *why* the journal believes it is open, resolved, or stale.

The classification is computed by
:meth:`bramble.journal_db.JournalDB.open_items_view` and stays strictly
read-only; no entry is mutated. The view is deliberately small and
project-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass

from bramble.journal_entry import JournalEntry

# Lifecycle states an open-item view can have.
STATE_OPEN = "open"
STATE_STALE = "stale"
STATE_RESOLVED = "resolved"

# Provenance for a ``resolved`` view: how the closure was inferred,
# ordered from highest to lowest confidence.
#
# * ``link``  – an explicit closing link relation (``resolves`` and the
#   legacy ``corrects``/``supersedes``/``implements``).
# * ``text``  – an explicit ``#<open> -> #<new>`` mapping in a later
#   entry's content.
# * ``title`` – a later completing entry shares the normalized title.
# * ``phase`` – a later completing entry shares the normalized phase.
#
# ``link`` and ``text`` are explicit author intent; ``title`` and
# ``phase`` are heuristics and may produce false positives on projects
# that reuse coarse phase buckets.
REASON_LINK = "link"
REASON_TEXT = "text"
REASON_TITLE = "title"
REASON_PHASE = "phase"


@dataclass(frozen=True, slots=True)
class OpenItemView:
    """An ``in_arbeit`` entry with its inferred open/resolved/stale state.

    Parameters
    ----------
    entry:
        The underlying ``in_arbeit`` :class:`JournalEntry`.
    state:
        One of :data:`STATE_OPEN`, :data:`STATE_STALE`,
        :data:`STATE_RESOLVED`.
    resolution_reason:
        For ``resolved`` views, how the closure was inferred (one of the
        ``REASON_*`` constants). ``None`` for open/stale views.
    resolved_by_id:
        For ``resolved`` views, the id of the later entry that closed
        this one, when known. ``None`` otherwise.
    age_days:
        Whole days between the entry timestamp and the reference "now".
        ``None`` only if the age could not be determined.
    """

    entry: JournalEntry
    state: str
    resolution_reason: str | None = None
    resolved_by_id: int | None = None
    age_days: int | None = None

    @property
    def is_resolved(self) -> bool:
        """Whether this view is considered effectively closed."""

        return self.state == STATE_RESOLVED

    @property
    def is_stale(self) -> bool:
        """Whether this view is an unresolved item past the stale cutoff."""

        return self.state == STATE_STALE
