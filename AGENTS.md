# Bramble Agent Instructions

When working in this repository, use the Bramble MCP journal as project
memory.

Project:

```text
bramble
```

At the start of a work session:

- Prefer a curated start with
  `journal_context(project="bramble", n_recent=10)`.
- Fallback for raw history: `journal_read(project="bramble", n=20)`.
- If the context is unclear, search with
  `journal_search(project="bramble", query=..., limit=...)` or
  project-overgreifend with `journal_search_all(query=..., limit=...)`.

During work:

- Treat the Bramble MCP journal as the authoritative project memory.
- Do not write new entries to `docs/journal.txt`; it is a legacy import
  source only.
- Do not modify or delete existing journal entries.
- Corrections are append-only: add a new `bugfix` or `notiz` entry that
  references the old entry by id, title, or date.
- Use only these status values: `in_arbeit`, `abgeschlossen`, `notiz`,
  `bugfix`.
- Prefer a small stable tag set when relevant:
  `decision`, `deployment`, `security`, `backup`, `admin-ui`, `test`,
  `docs`, `token`.

At the end of substantial work:

- Add a new entry with `journal_append(project="bramble", ...)`.
- Use one of these statuses: `in_arbeit`, `abgeschlossen`, `notiz`,
  `bugfix`.
- Include the relevant phase when known, for example `Phase 4`.
- Mention important tests, host commands, decisions, and open follow-up
  work in the entry content.
- Apply this completion checklist before closing:
  1) code/config committed,
  2) relevant tests/smoke checks executed,
  3) append-only journal entry written,
  4) next step documented explicitly.
