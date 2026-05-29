"""Journal entry data class with validation.

The :class:`JournalEntry` represents a single, immutable record in the
Bramble journal. The class is deliberately small and project-agnostic:
it knows about its own fields and how to validate them, but it does
**not** know how to persist itself. Persistence is the responsibility
of :class:`bramble.journal_db.JournalDB`.

Design notes (Phase 1, see ``docs/journal.txt``):

* ``id`` is ``None`` for entries that have not been written to the
  database yet. ``JournalDB.append()`` returns a new entry with the
  assigned row id.
* ``timestamp`` is a :class:`datetime.datetime` with timezone ``UTC``.
  Naive datetimes are rejected to prevent silent timezone bugs. The
  database stores the value as an ISO-8601 string.
* ``status`` is a :class:`JournalStatus` (``StrEnum``). Callers may
  pass either the enum member or the underlying string – the
  constructor normalises both to the enum.
* The class is ``frozen`` to make accidental mutation of journal
  entries impossible. Bramble is append-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MAX_TAGS = 5


class JournalStatus(StrEnum):
    """Allowed values for the ``status`` column of ``journal_entries``."""

    IN_ARBEIT = "in_arbeit"
    ABGESCHLOSSEN = "abgeschlossen"
    NOTIZ = "notiz"
    BUGFIX = "bugfix"


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""

    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """A single, immutable journal record.

    Parameters
    ----------
    project:
        Short identifier of the project the entry belongs to. Must be
        non-empty after stripping whitespace. Convention: lowercase
        ``kebab-case`` (e.g. ``"elder-berry"``).
    status:
        One of :class:`JournalStatus`. Strings are accepted and
        normalised to the corresponding enum member.
    content:
        The free-text body of the entry. Must be non-empty after
        stripping whitespace.
    phase:
        Optional phase label (e.g. ``"Phase 1"``).
    title:
        Optional short title.
    actor:
        Optional human or agent acting on the work, for audit context.
    client:
        Optional technical client identifier.
    source:
        Optional broad origin such as ``"mcp"``, ``"admin-ui"``, or
        ``"import"``. This is metadata only, not an auth source.
    tags:
        Optional lowercase kebab-case labels. Duplicates are removed
        and at most five tags are allowed.
    timestamp:
        Timezone-aware ``datetime`` in UTC. Defaults to "now".
    id:
        Database row id. ``None`` until the entry has been persisted.
    """

    project: str
    status: JournalStatus
    content: str
    phase: str | None = None
    title: str | None = None
    actor: str | None = None
    client: str | None = None
    source: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    timestamp: datetime = field(default_factory=_utc_now)
    id: int | None = None

    def __post_init__(self) -> None:
        # ``frozen=True`` means we use ``object.__setattr__`` to
        # normalise fields. This is the documented pattern.
        self._validate_project()
        self._validate_status()
        self._validate_content()
        self._validate_optional_text("phase")
        self._validate_optional_text("title")
        self._validate_optional_text("actor")
        self._validate_optional_text("client")
        self._validate_optional_text("source")
        self._validate_tags()
        self._validate_timestamp()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def _validate_project(self) -> None:
        if not isinstance(self.project, str):
            raise TypeError("project must be a string")
        stripped = self.project.strip()
        if not stripped:
            raise ValueError("project must not be empty")
        if stripped != self.project:
            object.__setattr__(self, "project", stripped)

    def _validate_status(self) -> None:
        # Accept both enum members and raw strings for caller
        # convenience, but always store the enum.
        if isinstance(self.status, JournalStatus):
            return
        if isinstance(self.status, str):
            try:
                normalised = JournalStatus(self.status)
            except ValueError as exc:
                allowed = ", ".join(s.value for s in JournalStatus)
                raise ValueError(
                    f"status {self.status!r} is not allowed; "
                    f"must be one of: {allowed}"
                ) from exc
            object.__setattr__(self, "status", normalised)
            return
        raise TypeError(
            "status must be a JournalStatus or a string, "
            f"got {type(self.status).__name__}"
        )

    def _validate_content(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        if not self.content.strip():
            raise ValueError("content must not be empty")

    def _validate_optional_text(self, attr: str) -> None:
        value = getattr(self, attr)
        if value is None:
            return
        if not isinstance(value, str):
            raise TypeError(f"{attr} must be a string or None")
        stripped = value.strip()
        if not stripped:
            # Empty / whitespace-only strings are normalised to None to
            # keep the DB clean.
            object.__setattr__(self, attr, None)
            return
        if stripped != value:
            object.__setattr__(self, attr, stripped)

    def _validate_tags(self) -> None:
        if self.tags is None:
            object.__setattr__(self, "tags", ())
            return
        if isinstance(self.tags, (str, bytes)):
            raise TypeError("tags must be an iterable of strings, not a string")

        try:
            iterator = iter(self.tags)
        except TypeError as exc:
            raise TypeError("tags must be an iterable of strings") from exc

        normalised: set[str] = set()
        for tag in iterator:
            if not isinstance(tag, str):
                raise TypeError("tags must contain only strings")
            tag = tag.strip().lower()
            if not tag:
                raise ValueError("tags must not contain empty values")
            if not _TAG_RE.fullmatch(tag):
                raise ValueError(
                    f"tag {tag!r} must match kebab-case pattern "
                    "^[a-z0-9][a-z0-9-]*$"
                )
            normalised.add(tag)

        if len(normalised) > _MAX_TAGS:
            raise ValueError(f"entries may have at most {_MAX_TAGS} tags")
        object.__setattr__(self, "tags", tuple(sorted(normalised)))

    def _validate_timestamp(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.tzinfo.utcoffset(
            self.timestamp
        ) is None:
            raise ValueError(
                "timestamp must be timezone-aware; pass datetime.now(tz=UTC)"
            )
        # Normalise to UTC so persisted strings always have the +00:00
        # offset. We intentionally do not strip the tzinfo; ISO-8601
        # strings include the offset.
        if self.timestamp.utcoffset() != UTC.utcoffset(self.timestamp):
            object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))

    # ------------------------------------------------------------------
    # Serialisation helpers (used by JournalDB)
    # ------------------------------------------------------------------
    def timestamp_iso(self) -> str:
        """Return the timestamp as an ISO-8601 string (UTC)."""

        return self.timestamp.isoformat()

    def with_id(self, new_id: int) -> JournalEntry:
        """Return a copy of this entry with ``id`` set to ``new_id``.

        Used by :class:`JournalDB` after a successful insert.
        """

        if not isinstance(new_id, int) or new_id <= 0:
            raise ValueError("new_id must be a positive int")
        return JournalEntry(
            project=self.project,
            status=self.status,
            content=self.content,
            phase=self.phase,
            title=self.title,
            actor=self.actor,
            client=self.client,
            source=self.source,
            tags=self.tags,
            timestamp=self.timestamp,
            id=new_id,
        )

    @classmethod
    def from_row(
        cls,
        *,
        id: int,
        project: str,
        timestamp: str,
        status: str,
        phase: str | None,
        title: str | None,
        content: str,
        actor: str | None = None,
        client: str | None = None,
        source: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> JournalEntry:
        """Build an entry from a DB row.

        The DB stores ``timestamp`` as an ISO-8601 string; this method
        parses it back into a timezone-aware ``datetime``.
        """

        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            # Defensive: legacy rows might be naive. Treat as UTC.
            ts = ts.replace(tzinfo=UTC)
        return cls(
            project=project,
            status=status,
            content=content,
            phase=phase,
            title=title,
            actor=actor,
            client=client,
            source=source,
            tags=tags,
            timestamp=ts,
            id=id,
        )
