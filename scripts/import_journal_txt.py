"""Import legacy ``docs/journal.txt`` files into a Bramble SQLite DB.

The importer is deliberately conservative:

* The default mode is a dry-run.
* ``--execute`` is required before anything is written.
* Sections with unclear status/date/content are reported as issues and
  block execution unless ``--allow-warnings`` is supplied.
* Existing entries with the same project/timestamp/status/title/content
  are skipped by default so a repeated import is not destructive.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

# Make ``src/`` importable when running this script directly without
# the package being installed (mirrors scripts/init_db.py).
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bramble.journal_db import JournalDB  # noqa: E402
from bramble.journal_entry import JournalEntry, JournalStatus  # noqa: E402
from bramble.server_config import ENV_DB_PATH  # noqa: E402

DEFAULT_DB_PATH = ROOT / "data" / "bramble.db"
DEFAULT_SOURCE = ROOT / "docs" / "journal.txt"

_KEBAB_CASE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DATE_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?P<clock>\d{2}:\d{2}(?::\d{2})?))?"
)
_PHASE_RE = re.compile(
    r"\bPhase[- ](?P<number>\d+(?:[a-z]|(?:\.[0-9a-z]+))?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedJournalEntry:
    """One importable entry parsed from a legacy text journal."""

    source_line: int
    status: JournalStatus
    timestamp: datetime
    title: str
    phase: str | None
    content: str

    def to_journal_entry(self, project: str) -> JournalEntry:
        """Convert to the persistent Bramble entry model."""

        return JournalEntry(
            project=project,
            status=self.status,
            content=self.content,
            phase=self.phase,
            title=self.title,
            timestamp=self.timestamp,
        )


@dataclass(frozen=True, slots=True)
class ParseIssue:
    """A section that could not be imported without human review."""

    source_line: int
    heading: str
    message: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Parsed entries plus any issues found while reading the source."""

    entries: list[ParsedJournalEntry]
    issues: list[ParseIssue]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Summary of an execute-mode import."""

    imported: int
    skipped_existing: int


@dataclass(frozen=True, slots=True)
class _Section:
    line_no: int
    heading: str
    body_lines: list[str]
    context_timestamp: datetime | None = None


def parse_journal_file(path: Path) -> ParseResult:
    """Read and parse a legacy journal file."""

    return parse_journal_text(path.read_text(encoding="utf-8"))


def parse_journal_text(text: str) -> ParseResult:
    """Parse all ``##`` sections from ``text``."""

    entries: list[ParsedJournalEntry] = []
    issues: list[ParseIssue] = []
    sections = _split_sections(text)
    section_timestamps = [_section_timestamp(section) for section in sections]

    for index, section in enumerate(sections):
        entry, section_issues = _parse_section(
            section,
            fallback_timestamp=_nearest_timestamp(
                index=index,
                sections=sections,
                timestamps=section_timestamps,
            ),
        )
        issues.extend(section_issues)
        if entry is not None:
            entries.append(entry)

    return ParseResult(entries=entries, issues=issues)


def import_entries(
    *,
    db_path: Path,
    project: str,
    entries: Iterable[ParsedJournalEntry],
    allow_duplicates: bool = False,
) -> ImportResult:
    """Write parsed entries to ``db_path`` via :class:`JournalDB`."""

    _require_kebab_case(project)

    db = JournalDB(db_path)
    db.initialize()

    existing = set()
    if not allow_duplicates:
        existing = _existing_signatures(db_path=db_path, project=project)

    imported = 0
    skipped = 0
    for parsed in entries:
        entry = parsed.to_journal_entry(project)
        signature = _entry_signature(entry)
        if not allow_duplicates and signature in existing:
            skipped += 1
            continue
        persisted = db.append(entry)
        existing.add(_entry_signature(persisted))
        imported += 1

    return ImportResult(imported=imported, skipped_existing=skipped)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Import a legacy docs/journal.txt file into Bramble."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"journal.txt path to import (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"SQLite DB path (env: {ENV_DB_PATH}; default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="target project identifier (kebab-case).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and report only; this is the default.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="write parsed entries to the target DB.",
    )
    parser.add_argument(
        "--allow-warnings",
        action="store_true",
        help="allow execute mode even when parse issues were reported.",
    )
    parser.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="write entries even if an identical entry already exists.",
    )
    return parser.parse_args(argv)


def resolve_db_path(cli_value: Path | None) -> Path:
    """Resolve DB path using CLI > env > default."""

    if cli_value is not None:
        return cli_value
    import os

    env_value = os.environ.get(ENV_DB_PATH)
    if env_value:
        return Path(env_value)
    return DEFAULT_DB_PATH


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    args = parse_args(argv)

    if not _KEBAB_CASE_RE.match(args.project):
        print(
            f"error: project {args.project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$",
            file=sys.stderr,
        )
        return 2

    if not args.source.exists():
        print(f"error: source file does not exist: {args.source}", file=sys.stderr)
        return 2

    db_path = resolve_db_path(args.db)
    result = parse_journal_file(args.source)
    _print_parse_report(result=result, source=args.source, project=args.project)

    if result.issues and not args.allow_warnings:
        print(
            "error: parse issues found; fix the source or pass --allow-warnings",
            file=sys.stderr,
        )
        return 1

    if not args.execute:
        print("dry-run only; pass --execute to write entries")
        return 0

    import_result = import_entries(
        db_path=db_path,
        project=args.project,
        entries=result.entries,
        allow_duplicates=args.allow_duplicates,
    )
    print(
        "import complete: "
        f"imported={import_result.imported} "
        f"skipped_existing={import_result.skipped_existing} "
        f"db={db_path}"
    )
    return 0


def _split_sections(text: str) -> list[_Section]:
    sections: list[_Section] = []
    current_heading: str | None = None
    current_context_timestamp: datetime | None = None
    pending_context_timestamp: datetime | None = None
    current_line = 0
    body: list[str] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            heading = line[3:].strip()
            if current_heading is not None and _is_metadata_heading(heading):
                body.append(heading)
                continue
            if current_heading is not None:
                sections.append(
                    _Section(
                        line_no=current_line,
                        heading=current_heading,
                        body_lines=body,
                        context_timestamp=current_context_timestamp,
                    )
                )
            current_heading = heading
            current_context_timestamp = pending_context_timestamp
            pending_context_timestamp = None
            current_line = line_no
            body = []
        elif line.startswith("="):
            continue
        elif (context_timestamp := _parse_context_timestamp(line)) is not None:
            pending_context_timestamp = context_timestamp
        elif current_heading is not None:
            body.append(line)

    if current_heading is not None:
        sections.append(
            _Section(
                line_no=current_line,
                heading=current_heading,
                body_lines=body,
                context_timestamp=current_context_timestamp,
            )
        )

    return sections


def _parse_section(
    section: _Section,
    *,
    fallback_timestamp: datetime | None = None,
) -> tuple[ParsedJournalEntry | None, list[ParseIssue]]:
    issues: list[ParseIssue] = []

    heading = _parse_heading(section.heading)
    if heading is None:
        issues.append(
            ParseIssue(
                source_line=section.line_no,
                heading=section.heading,
                message="unknown status in heading",
            )
        )
        return None, issues
    status, title = heading

    date_index, timestamp = _find_timestamp(section.body_lines)
    if timestamp is None:
        timestamp = _timestamp_from_heading(section.heading)
    if timestamp is None:
        timestamp = section.context_timestamp
    if timestamp is None:
        timestamp = fallback_timestamp
    if timestamp is None:
        issues.append(
            ParseIssue(
                source_line=section.line_no,
                heading=section.heading,
                message="missing or invalid Datum line",
            )
        )
        return None, issues

    content_lines = [
        line for index, line in enumerate(section.body_lines) if index != date_index
    ]
    content = "\n".join(content_lines).strip()
    if not content:
        return None, issues

    return (
        ParsedJournalEntry(
            source_line=section.line_no,
            status=status,
            timestamp=timestamp,
            title=_strip_heading_timestamp(title),
            phase=_derive_phase(title),
            content=content,
        ),
        issues,
    )


def _parse_heading(heading: str) -> tuple[JournalStatus, str] | None:
    if ":" in heading:
        raw_status, raw_title = heading.split(":", 1)
        status = _status_from_label(raw_status)
        if status is not None:
            title = raw_title.strip() or heading.strip()
            return status, title

    inferred = _infer_status_from_unprefixed_heading(heading)
    if inferred is not None:
        return inferred, heading.strip()

    return None


def _status_from_label(label: str) -> JournalStatus | None:
    normalised = re.sub(r"[\s_]+", " ", label.strip().casefold())
    return {
        "in arbeit": JournalStatus.IN_ARBEIT,
        "abgeschlossen": JournalStatus.ABGESCHLOSSEN,
        "abschluss": JournalStatus.ABGESCHLOSSEN,
        "bugfix": JournalStatus.BUGFIX,
        "hotfix": JournalStatus.BUGFIX,
        "hotfix-reparatur": JournalStatus.BUGFIX,
        "korrektur": JournalStatus.BUGFIX,
        "notiz": JournalStatus.NOTIZ,
        "nachtrag": JournalStatus.NOTIZ,
        "stand": JournalStatus.NOTIZ,
        "update": JournalStatus.NOTIZ,
        "konzept": JournalStatus.NOTIZ,
    }.get(normalised)


def _infer_status_from_unprefixed_heading(heading: str) -> JournalStatus | None:
    lower = heading.casefold()
    if lower.startswith("phase ") and (
        "offen" in lower or "code umgesetzt" in lower or "in arbeit" in lower
    ):
        return JournalStatus.IN_ARBEIT
    if lower.startswith("hinweis"):
        return JournalStatus.NOTIZ
    if "merged in main" in lower or "gemerged in main" in lower:
        return JournalStatus.ABGESCHLOSSEN
    if "hotfix" in lower or "pr-review-fix" in lower or "review-fix" in lower:
        return JournalStatus.BUGFIX
    return None


def _find_timestamp(body_lines: list[str]) -> tuple[int | None, datetime | None]:
    for index, line in enumerate(body_lines):
        normalised = line.strip()
        if normalised.startswith("- "):
            normalised = normalised[2:].strip()
        if normalised.startswith("Datum:"):
            return index, _parse_timestamp(normalised)
    return None, None


def _parse_timestamp(line: str) -> datetime | None:
    match = _DATE_RE.search(line)
    if not match:
        return None

    date_part = match.group("date")
    clock = match.group("clock")
    try:
        if clock is None:
            date_value = datetime.fromisoformat(date_part).date()
            return datetime.combine(date_value, time(hour=12), tzinfo=UTC)
        if clock.count(":") == 1:
            clock = f"{clock}:00"
        return datetime.fromisoformat(f"{date_part}T{clock}").replace(tzinfo=UTC)
    except ValueError:
        return None


def _timestamp_from_heading(heading: str) -> datetime | None:
    return _parse_timestamp(heading)


def _parse_context_timestamp(line: str) -> datetime | None:
    stripped = line.strip()
    if not _DATE_RE.match(stripped):
        return None
    return _parse_timestamp(stripped)


def _section_timestamp(section: _Section) -> datetime | None:
    _, body_timestamp = _find_timestamp(section.body_lines)
    if body_timestamp is not None:
        return body_timestamp
    heading_timestamp = _timestamp_from_heading(section.heading)
    if heading_timestamp is not None:
        return heading_timestamp
    return section.context_timestamp


def _nearest_timestamp(
    *,
    index: int,
    sections: list[_Section],
    timestamps: list[datetime | None],
) -> datetime | None:
    nearest: tuple[int, datetime] | None = None
    source_line = sections[index].line_no
    for other_index, timestamp in enumerate(timestamps):
        if timestamp is None or other_index == index:
            continue
        distance = abs(sections[other_index].line_no - source_line)
        if nearest is None or distance < nearest[0]:
            nearest = (distance, timestamp)
    if nearest is None:
        return None
    return nearest[1]


def _strip_heading_timestamp(title: str) -> str:
    stripped = re.sub(
        r"\s*\(\s*\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\s*\)\s*$",
        "",
        title,
    ).strip()
    return stripped or title.strip()


def _is_metadata_heading(heading: str) -> bool:
    prefix = heading.split(":", 1)[0].strip().casefold()
    return prefix in {"branch", "naechster schritt"}


def _derive_phase(title: str) -> str | None:
    match = _PHASE_RE.search(title)
    if not match:
        return None
    return f"Phase {match.group('number')}"


def _existing_signatures(*, db_path: Path, project: str) -> set[tuple[str, ...]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT project, timestamp, status, phase, title, content "
            "FROM journal_entries WHERE project = ?",
            (project,),
        ).fetchall()
    return {
        (
            row["project"],
            row["timestamp"],
            row["status"],
            row["phase"] or "",
            row["title"] or "",
            row["content"],
        )
        for row in rows
    }


def _entry_signature(entry: JournalEntry) -> tuple[str, ...]:
    return (
        entry.project,
        entry.timestamp_iso(),
        entry.status.value,
        entry.phase or "",
        entry.title or "",
        entry.content,
    )


def _require_kebab_case(project: str) -> None:
    if not _KEBAB_CASE_RE.match(project):
        raise ValueError(
            f"project {project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$"
        )


def _print_parse_report(
    *, result: ParseResult, source: Path, project: str
) -> None:
    print(f"source: {source}")
    print(f"project: {project}")
    print(f"entries: {len(result.entries)}")
    print(f"issues: {len(result.issues)}")

    for index, entry in enumerate(result.entries, start=1):
        print(
            f"  {index:03d} line={entry.source_line} "
            f"date={entry.timestamp.isoformat()} "
            f"status={entry.status.value} "
            f"title={entry.title!r}"
        )

    for issue in result.issues:
        print(
            f"  issue line={issue.source_line} "
            f"heading={issue.heading!r}: {issue.message}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
