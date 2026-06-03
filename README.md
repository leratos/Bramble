# Bramble

[![CI](https://github.com/leratos/Bramble/actions/workflows/ci.yml/badge.svg)](https://github.com/leratos/Bramble/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> 🌐 **Deutsche Version: [README.de.md](README.de.md).** English is the
> authoritative version of this README.

Self-hosted MCP server for a cross-project development journal.

Bramble is the central "bramble bush" that all the berry projects
(Elder-Berry, Bull-Berry, Berry-Gym, Last-Strawberry, …) hang on. Instead
of keeping a separate `docs/journal.txt` in every repo, all projects write
their journal entries over the Model Context Protocol to one shared server,
which stores the data in a SQLite database and makes it searchable across
projects.

> **Security model (read this first):** Bramble is a single-owner tool.
> Reading and searching are cross-project — **any valid token reads the
> entries of all projects**; only writing is project-bound. There is **no
> tenant isolation**, and the tool is not meant to serve mutually distrusting
> users on one instance. Append-only also means no deletion. See
> [SECURITY.md](SECURITY.md) for details and operational guidance.

## Status

Actively developed and running in production. Bramble runs as a systemd
service behind a reverse proxy with bearer-token auth, per-token/per-IP rate
limiting, Fail2Ban, and WAL-mode SQLite; the Borg backup including a restore
test is verified. The journal itself is Bramble's active project memory —
`docs/journal.txt` remains only as a historical import source and is no
longer used for new entries.

## Architecture

- Python 3.12, SQLite (FTS5 for full-text search), FastMCP 3.x
- OOP, one class per file
- Dependency injection through the constructor
- Append-only: no `update`/`delete` tools — corrections are new entries

### Data model

```sql
CREATE TABLE journal_entries (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project   TEXT NOT NULL,
    timestamp TEXT NOT NULL,        -- ISO 8601, UTC
    status    TEXT NOT NULL,        -- in_arbeit | abgeschlossen | notiz | bugfix
    phase     TEXT,
    title     TEXT,
    content   TEXT NOT NULL
);
```

Plus an `idx_project_ts` index for fast `read` queries and an FTS5 table
`journal_fts` (indexing `content` and `title`), kept in sync by three
triggers (insert/update/delete). Tags, links and actor/client/source
metadata live in additional tables (see `src/bramble/journal_db.py`).

> Note: the status values are stored verbatim as `in_arbeit`,
> `abgeschlossen`, `notiz`, `bugfix` (German: in-progress, done, note,
> bugfix). They are part of the data contract and are not translated.

### MCP tools

Ten tools on the `JournalMCPServer`, each with `ToolError`-conformant error
translation:

| Tool | Purpose |
| --- | --- |
| `journal_guide()` | Canonical, project-agnostic working conventions (single source of truth, call at session start) |
| `journal_read(project, n=80)` | Newest `n` entries for a project, newest first |
| `journal_append(project, status, content, phase=None, title=None)` | Write a new entry; the timestamp is set server-side |
| `journal_search(project, query, limit=20)` | FTS5 full-text search, MATCH syntax passed through |
| `journal_search_all(...)` | Cross-project FTS5 search with optional filters, at most 100 hits |
| `journal_context(project, n_recent=10, include_cross_project=True, full=False)` | Curated session-start context (open items, bugfixes, decisions, related projects); `content` is previewed (truncated) by default (+ `content_chars`/`content_truncated`), `full=True` for the full body |
| `journal_digest(...)` | Structured time-range digest with counts and curated entry lists |
| `journal_open_items(project=None, limit=50, include_resolved=False, stale_after_days=30)` | Open work items with append-only closure inference; per item `open_state` (`open`/`stale`/`resolved`), `resolution_reason`, `resolved_by_id`, `age_days`. Resolved items hidden by default |
| `journal_resolve(project, resolves=[ids], title=None, content=None)` | Close open `in_arbeit` entries: writes one append-only entry with `resolves` links to the ids and reports `resolved`/`skipped` (missing / other project / not in_arbeit / already resolved) |
| `journal_list_projects()` | `(project, entry_count, last_timestamp)` per project, most recent activity first |

### Open items in the append-only model

The journal never edits entries; a completion is a new entry. A raw
`status="in_arbeit"` filter would therefore report every started entry as
open forever. `journal_open_items` and the `open_items` slice of
`journal_context` instead infer which items are effectively done, and
return the reasoning:

- The most reliable close signal is an explicit `resolves -> <open entry>`
  link from the closing entry (relation `resolves`). `corrects` /
  `supersedes` / `implements` and a `#<open> -> #<new>` text reference also
  close explicitly. The `journal_resolve` tool is the easiest way to write
  these links.
- Without an explicit reference, a conservative heuristic applies (a later
  `abgeschlossen`/`bugfix` entry with the same phase or title).
- Unresolved items older than `stale_after_days` are flagged `stale` (not
  hidden).

Details and decisions: `docs/concepts/phase-4f-open-items-resolution.md`
(internal, German).

Project identifiers must be kebab-case at the MCP layer
(`^[a-z0-9][a-z0-9-]*$`). `JournalDB` itself stays project-agnostic.

## Setup

```bash
# Linux/macOS
cd /opt/bramble
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

```powershell
# Windows
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Running the server

After `pip install -e .` the console-script entry point `bramble-server`
is available.

### stdio transport (for Claude Desktop / Claude Code)

```bash
bramble-server --transport stdio
```

### HTTP transport (authenticated)

```bash
bramble-server --transport http --host 127.0.0.1 --port 8765 \
    --tokens-file ./secrets/tokens.json --log-level INFO
```

The HTTP transport is authenticated: every tool call needs an
`Authorization: Bearer <token>` header. Tokens are created with
`scripts/gen_token.py <project>`. `journal_append` is bound to the token's
project; reading and searching remain cross-project.

### Configuration

Priority: CLI argument > environment variable > default.

| CLI | Env | Default |
| --- | --- | --- |
| `--db PATH` | `BRAMBLE_DB_PATH` | `./data/bramble.db` |
| `--transport stdio\|http` | `BRAMBLE_TRANSPORT` | `stdio` |
| `--host HOST` | `BRAMBLE_HOST` | `127.0.0.1` |
| `--port PORT` | `BRAMBLE_PORT` | `8765` |
| `--log-level LEVEL` | `BRAMBLE_LOG_LEVEL` | `INFO` |
| `--tokens-file PATH` | `BRAMBLE_TOKENS_FILE` | `./secrets/tokens.json` |
| `--rate-limit-per-token N` | `BRAMBLE_RATE_LIMIT_PER_TOKEN` | `60` |
| `--rate-limit-per-ip N` | `BRAMBLE_RATE_LIMIT_PER_IP` | `120` |

Logs are written as JSON to stderr (stdout is reserved for the MCP protocol
on the stdio transport).

### Admin UI

The separate admin server renders Starlette/Jinja2 views and binds by
default only to `127.0.0.1:8770`. Access is meant to go through an SSH
tunnel for operations, **not** through a public Nginx/Plesk path.

An Argon2id secret must exist before starting. There is deliberately no
default password:

```bash
python scripts/gen_admin_secret.py --output ./secrets/admin-ui.json
```

Then:

```bash
bramble-admin --db ./data/bramble.db \
    --admin-secret-file ./secrets/admin-ui.json \
    --tokens-file ./secrets/tokens.json
```

The UI shows a dashboard, project list, project view and project search.
Timestamps stay UTC in the database but are formatted in the admin UI with
the display time zone (`--time-zone`, env `BRAMBLE_ADMIN_TIME_ZONE`, default
`Europe/Berlin`), without seconds. The UI is English by default; pass
`--language de` (env `BRAMBLE_ADMIN_LANGUAGE`) for German. It can also
create, rotate and remove
project tokens. Existing token values are never shown; new or rotated tokens
appear only directly in that action's response. After token changes,
`bramble.service` must be restarted because the MCP server reads the token
file at startup.

Writing admin actions are CSRF-protected and logged to the append-only
`admin_audit_events` table. Login sessions are kept server-side, the cookie
is `HttpOnly` and `SameSite=Strict`; missing or invalid secrets abort
startup.

### Preparing the DB without a server

```bash
python scripts/init_db.py                # default ./data/bramble.db
python scripts/init_db.py /tmp/test.db   # explicit
BRAMBLE_DB_PATH=/tmp/test.db python scripts/init_db.py  # via env
```

`init_db.py` is idempotent. The server calls `db.initialize()` on startup
anyway; the script is only for setups where the DB is created separately
from the server lifecycle.

## Manual end-to-end smoke testing

`scripts/smoke_http.py` checks the MCP tools against a real running HTTP
server (not part of the pytest suite).

```bash
# Terminal 1
bramble-server --transport http --host 127.0.0.1 --port 8765 \
    --tokens-file ./secrets/tokens.json --log-level INFO

# Terminal 2
python scripts/smoke_http.py --token <bramble-token>
# or against a different endpoint:
python scripts/smoke_http.py --url http://127.0.0.1:9000/mcp/ \
    --token <bramble-token>
# read-only variant without a test append:
python scripts/smoke_http.py --token <bramble-token> --mode read-only
```

By default (`--mode write-light`) the smoke test writes two entries to the
real DB, reads them back, searches via FTS5, checks
`journal_context`/`journal_digest`/`journal_open_items`, the auth gate and
token scope, and fires negative tests (unknown status, non-kebab project
name). With `--mode read-only` only read checks run, without a test append.

## Importing legacy journals

`docs/journal.txt` is already imported for Bramble itself and is no longer
maintained. This section only covers importing old text journals from
Bramble or other projects.

`scripts/import_journal_txt.py` imports existing `docs/journal.txt` files
straight into the SQLite DB. The default is a dry run; it only writes with
`--execute`.

```bash
python scripts/import_journal_txt.py \
    --project bramble \
    --source docs/journal.txt \
    --db data/bramble.db \
    --execute
```

The import preserves the journal date where it is unambiguously parseable.
Date entries without a time are set to `12:00:00+00:00`. Identical entries
are skipped in execute mode.

## Connecting AI clients

MCP-capable AI clients connect over the HTTP endpoint and a project-scoped
bearer token. Working rules, the correction model and a system-prompt
snippet are in [docs/ai-client-setup.md](docs/ai-client-setup.md).

For this repo, the operational agent rules also live in
[AGENTS.md](AGENTS.md).

## Repository layout

```text
Bramble/
├── docs/
│   ├── concepts/        # phase concepts (internal, German)
│   ├── ai-client-setup.md
│   └── journal.txt      # legacy import source; no new entries
├── deploy/              # example systemd/Nginx/Fail2Ban configs (adapt to your host)
├── scripts/
│   ├── gen_admin_secret.py
│   ├── gen_token.py     # create/rotate project tokens
│   ├── import_journal_txt.py
│   ├── init_db.py       # migration / DB bootstrap
│   └── smoke_http.py    # manual HTTP smoke script
├── src/bramble/         # source (one class per file)
└── tests/               # pytest tests
```

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Contributions are
accepted under the same terms.

Note on `deploy/`: the systemd units, Nginx directives and Fail2Ban rules
there are **examples** for the reference host (domain, paths like
`/opt/bramble`). Adapt them to your environment.
