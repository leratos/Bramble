# Contributing to Bramble

Thanks for your interest! Bramble is a self-hosted MCP server for a
cross-project development journal. Before contributing, please read the
[security model](SECURITY.md) — in particular that reading is cross-project
and there is no tenant isolation.

## Development environment

Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

Enable the versioned git hooks (branch policy + pytest before push):

```bash
git config core.hooksPath .githooks
```

## Tests

- Runner: `pytest` from the project root.
- `asyncio_mode = "auto"` (see `pyproject.toml`).
- Tests live flat in `tests/`, named `test_{module}.py`.
- Cover every change with tests: happy path, the most important error cases,
  relevant edge cases. Prefer `unittest.mock` for mocks.
- The full suite must be green before a PR; CI runs it too.

## Branches and commits

- No direct commits to `main` (the pre-commit hook refuses them).
- Feature work: `feature/short-description`; bugfix: `fix/...`;
  tooling/docs-only changes: `chore/...` or `docs/...`. Branch names are
  lowercase with hyphens.
- Meaningful commit messages (Conventional Commits style welcome:
  `feat(...)`, `fix(...)`, `docs(...)`, `chore(...)`).

## Code style

- OOP: one class per file (`snake_case` filename), communication through
  clear interfaces, dependency injection through the constructor.
- **Append-only invariant**: no `update`/`delete` tools or paths on the
  journal. Corrections are new entries.
- Linting: ruff (`line-length = 100`, rules `E,F,W,I,B,UP,SIM` in
  `pyproject.toml`). Please run `ruff check .` locally before a PR.
- Log via `logging.getLogger(__name__)`, no `print()` for errors/warnings;
  catch specific exceptions, never a bare `except`.
- New runtime dependencies only with justification in the PR.

## Pull requests

1. Branch off `main`, implement the change plus tests.
2. Full suite green, ruff clean.
3. Open a PR against `main`; CI must pass.
4. Contributions are accepted under the [Apache-2.0 license](LICENSE).

## Note for AI agents

For LLM-assisted contributions there is also [AGENTS.md](AGENTS.md). The
maintainer keeps the running project memory in the Bramble MCP journal;
external contributors do not need a token for that.
