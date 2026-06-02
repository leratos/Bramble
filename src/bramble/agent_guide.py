"""Single source of truth for the shared, project-agnostic agent workflow.

This module is THE canonical copy of the Bramble journal working
conventions that every connected project shares. It is served verbatim by
the ``journal_guide`` MCP tool so that projects do not each maintain (and
drift on) their own copy. A project's ``AGENTS.md`` should reference
``journal_guide()`` and only add project-specific details (project name,
tech stack, test runner, repo layout), not re-state these conventions.

Keep this concise and project-agnostic. When a shared convention changes,
change it here and bump :data:`AGENT_GUIDE_VERSION`; every project picks up
the new version on its next ``journal_guide()`` call.
"""

from __future__ import annotations

# ISO date of the last meaningful change to AGENT_GUIDE. Bump on every edit.
AGENT_GUIDE_VERSION = "2026-06-02"

AGENT_GUIDE = """\
# Bramble Journal — Shared Agent Workflow

This is the canonical, project-agnostic working guide for the Bramble MCP
journal. It applies to ALL projects. A project's AGENTS.md references this
guide and only adds project specifics (project name, stack, test runner,
repo layout) — these conventions are NOT repeated there.

## Core principle

The journal is append-only. Entries are never edited or deleted. Corrections
and completions are always NEW entries that reference the old one by link or
reference.

## Session start

1. Always first: `journal_context(project="<project>", n_recent=10)`.
   An empty context is not an error — then, on real work, write a first
   entry, not a smoke entry.
2. For an unclear or cross-project topic:
   `journal_search_all(query="<topic>", limit=20)`.
3. When a new block of work starts: write a clean `in_arbeit` entry with a
   clear scope and next step.

## Entry kinds (status)

The status values are German and stored verbatim (do not translate them):

* `in_arbeit` (in progress): started work with an open next step.
* `abgeschlossen` (done): a finished, verified block of work.
* `notiz` (note): decision, operational event, context (not a bug fix).
* `bugfix`: correction of an error, including a correction to an old entry.

Do not invent new status values (`todo`/`blocked`/`review`): such
information belongs in tags or content.

## Tags

Controlled vocabulary, lowercase-kebab, max. 5 per entry: `decision`,
`deployment`, `security`, `backup`, `import`, `admin-ui`, `test`, `docs`,
`bug`, `token`, `agent`. Tags complement status and phase; they do not
replace them.

## Corrections (append-only)

Never edit an old entry. Instead write a new `bugfix` entry and reference it
via a link `corrects -> <old id>` (or by id/date in the text).

## Open items and completion

"Open" is inferred, not just read off the status. `journal_open_items` and
the `open_items` slice of `journal_context` classify each `in_arbeit` entry
as:

* `resolved` – a later entry marks it done; hidden by default (visible with
  `include_resolved=true`, with `resolution_reason`/`resolved_by_id`).
* `stale` – unresolved and older than `stale_after_days` (default 30);
  shown, but flagged.
* `open` – unresolved and within the window.

How to close an open item cleanly:

1. Easiest: `journal_resolve(project, resolves=[<ids>])`. Writes ONE
   append-only entry with `resolves` links to all ids and reports which were
   closed and which were skipped (missing / other project / not in_arbeit) —
   so the closure is verified.
2. Equivalent manually: a closing entry (`abgeschlossen`/`notiz`/`bugfix`)
   with a link `resolves -> <id of the in_arbeit entry>`.
3. Or explicitly: the exact form `#<open> -> #<new>` in the text of the
   closing entry.
4. Weaker heuristic (automatic): a later `abgeschlossen`/`bugfix` entry with
   the same phase or the same title.

CAUTION — common trap: naming an id only in prose ("#655 is done", "closes
27.2-27.5") does NOT close the item. The inference does not parse free-text
ids — you MUST use `journal_resolve`, a `resolves` link, or the exact
`#<id> -> #<new>` form. After closing, check with `journal_open_items` that
the item is gone.

Important: keep real backlog (follow-up work not yet started) as a lean
`in_arbeit` entry with a next step. What is nowhere recorded as `in_arbeit`
cannot be reported as open by any tool.

## During the work

* Important decisions as a `notiz` with tag `decision`.
* Bug fixes as `bugfix`, never editing the old entry.
* Deployment/backup/token events as `notiz`/`abgeschlossen` with fitting
  tags.

## Session end (Definition of Done)

A work package is only cleanly done when:

1. Code/config is committed.
2. Relevant tests/smoke are run and named in the entry.
3. An append-only journal entry is written.
4. The open follow-up is explicitly documented as the next step.

## Do not

* No `update`/`delete` on the journal; no mutation of old entries.
* No tokens/secrets in the journal, in logs or in the repo.
* `docs/journal.txt` is only a historical import source — no new entries
  there.
"""
