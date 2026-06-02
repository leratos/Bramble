# Bramble AI Client Setup

Working guide for Codex, Claude and other MCP-capable AI clients.

## Goal

AI clients should use Bramble as a shared development journal:

* read old entries,
* search per-project or cross-project,
* write new entries,
* add corrections in a traceable way.

Bramble is deliberately **append-only**. There is no update/delete tool. To
correct an entry, write a new `bugfix` or `notiz` entry that references the
old one by `id`, title or date.

For Bramble's own repo, the binding agent rules are in
[`AGENTS.md`](../AGENTS.md). `docs/journal.txt` is only a historical import
source; new entries are written exclusively through the MCP journal.

The status values (`in_arbeit`, `abgeschlossen`, `notiz`, `bugfix`) are
German and are part of the data contract — they are stored verbatim and not
translated (in-progress, done, note, bugfix).

## Connection

HTTP MCP endpoint:

```text
https://journal.last-strawberry.com/mcp/
```

Every HTTP request needs the header:

```text
Authorization: Bearer <project-token>
```

For Bramble itself the project is:

```text
bramble
```

The token lives only on the host in:

```text
/opt/bramble/secrets/tokens.json
```

Tokens are not written to the repo and not copied into chat transcripts.

## Expected tools

After a successful connection the client should see these tools. The
authoritative, always-current reference for workflow and conventions is
`journal_guide()` (single source of truth, call at session start); the table
below is just an overview.

| Tool | Use |
| --- | --- |
| `journal_guide()` | Canonical, cross-project working conventions (call first) |
| `journal_read(project, n=80)` | Read the latest entries of a project |
| `journal_append(project, status, content, phase=None, title=None)` | Write a new entry |
| `journal_search(project, query, limit=20)` | Full-text search within a project |
| `journal_search_all(...)` | Full-text search across all projects with optional filters |
| `journal_context(project, n_recent=10, include_cross_project=True)` | Curated session-start context for a project |
| `journal_digest(...)` | Time-range digest with counts, open items, bugfixes and decisions |
| `journal_open_items(project=None, limit=50)` | Open work items, newest first, optionally filtered per project |
| `journal_resolve(project, resolves=[ids])` | Close open `in_arbeit` entries via `resolves` links; reports closed/skipped ids |
| `journal_list_projects()` | List projects with counts and last activity |

`journal_append` is bound to the token's project. A `bramble` token may
therefore only write to `project="bramble"`. Reading and searching stay
cross-project.

## Working rules for AI agents

At the start of a Bramble session:

0. Call `journal_guide()` once and follow it. It provides the binding,
  cross-project conventions (statuses, tags, correction/`resolves` model,
  open-item semantics, DoD). The steps below are the Bramble-specific
  application of it.
1. Prefer `journal_context(project="bramble", n_recent=10)`. Fallback:
  `journal_read(project="bramble", n=20)`.
2. For an unclear history, search specifically, e.g.
   `journal_search(project="bramble", query="Phase 4", limit=10)`. If the
   relevant project is unclear, use `journal_search_all(...)`.
3. When a new block of work starts: create a clear `in_arbeit` entry early,
  with scope and next step.
4. Take the entries you read into account when planning and answering about
  status.

During the work:

* Document relevant decisions, completed work packages, bugs and operational
  events as new entries.
* In Bramble's own repo, do not write new entries to `docs/journal.txt`.
* Do not journal trivial intermediate updates; Bramble is a development
  journal, not a token-by-token log.
* Set the `phase` value when it naturally fits, e.g. `Phase 4`.
* Use short, concrete `title` values.

At the end of substantial work:

* Write a completion or progress entry.
* Mention tests, host commands and open next steps in the `content`.
* Meet these DoD checks before completion:
  1. Code/config committed.
  2. Relevant tests or smoke checks run.
  3. Append-only journal entry written.
  4. Next step explicitly documented.

## Status values

| Status | Meaning |
| --- | --- |
| `in_arbeit` | Work started, still open |
| `abgeschlossen` | Work package completed |
| `notiz` | Operational note, decision, context |
| `bugfix` | Bug fixed, or a correction to an older entry |

## Corrections and adjustments

Existing entries are never overwritten. Instead:

```text
status: bugfix
title: Correction to entry <id or title>
content:
Corrects the entry "<old title>" from <date>.

Old:
<short description of the wrong statement>

New:
<corrected statement>

Impact:
<if relevant: what follows from it>
```

For smaller additions without an error:

```text
status: notiz
title: Addendum to <topic>
content:
Adds to the entry "<old title>" ...
```

## Example: writing an entry

```json
{
  "project": "bramble",
  "status": "abgeschlossen",
  "phase": "Phase 4",
  "title": "AI client setup documented",
  "content": "Documented Bramble for MCP-capable AI clients. At the start agents read journal_read(...), record progress via journal_append(...), and correct append-only via bugfix/notiz follow-ups."
}
```

## Minimal system-prompt snippet

```text
Use Bramble as a project-scoped development journal.

Project: <project>

At the start of every session, call journal_guide() and follow the
conventions it describes (statuses, tags, append-only corrections,
open-item/resolves semantics, DoD). Then read
journal_context(project="<project>", n_recent=10) (fallback: journal_read).
At the end of substantial work, write a journal_append entry.
```

## Project AGENTS.md template

Other projects should **not** copy the shared conventions into their
`AGENTS.md`; they should reference `journal_guide()` instead. That keeps one
source and avoids copy-paste drift. Minimal block for another project's
`AGENTS.md`:

```markdown
## Project memory

Project: <project-kebab>

The active project memory is the Bramble MCP journal
(https://journal.last-strawberry.com/mcp/, project-scoped token).

At the start of every session:
1. Call `journal_guide()` and follow it — the canonical, shared journal
   conventions (statuses, tags, correction/`resolves` model, open-item
   semantics, session start/end, DoD). Do not repeat them here.
2. Read `journal_context(project="<project-kebab>", n_recent=10)`.

This document only adds project specifics (tech stack, test runner, repo
layout, branch conventions).
```

## Verifying a new client

1. Check the tool list: all ten Bramble tools must be visible.
1. Fetch the conventions:

```text
journal_guide()
```

1. Test reading:

```text
journal_read(project="bramble", n=5)
```

1. Test search:

```text
journal_search(project="bramble", query="Backup", limit=5)
```

1. Test cross-project search:

```text
journal_search_all(query="Backup", limit=5)
```

1. Test the digest:

```text
journal_digest(project="bramble", since="7d")
```

1. Test session context:

```text
journal_context(project="bramble", n_recent=10)
```

1. Test open items:

```text
journal_open_items(project="bramble", limit=10)
```

1. Run the write test only as a real journal entry, not as an arbitrary
  smoke entry. Example: `title="Client <name> connected"`.

If a write test fails, check first:

* Is the Authorization header set?
* Does the token belong to project `bramble`?
* Is the client really writing to `project="bramble"`?
* Is the client possibly blocked by Fail2Ban?
