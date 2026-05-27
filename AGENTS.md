# Bramble Agent Instructions

When working in this repository, use the Bramble MCP journal as project
memory.

Project:

```text
bramble
```

At the start of a work session:

- Read recent context with `journal_read(project="bramble", n=20)`.
- If the context is unclear, search with
  `journal_search(project="bramble", query=..., limit=...)`.

During work:

- Treat the Bramble MCP journal as the authoritative project memory.
- Do not write new entries to `docs/journal.txt`; it is a legacy import
  source only.
- Do not modify or delete existing journal entries.
- Corrections are append-only: add a new `bugfix` or `notiz` entry that
  references the old entry by id, title, or date.

At the end of substantial work:

- Add a new entry with `journal_append(project="bramble", ...)`.
- Use one of these statuses: `in_arbeit`, `abgeschlossen`, `notiz`,
  `bugfix`.
- Include the relevant phase when known, for example `Phase 4`.
- Mention important tests, host commands, decisions, and open follow-up
  work in the entry content.
