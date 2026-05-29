# Bramble Git Guardrails

These hooks are a local safety net for agent-assisted work. They do not replace
`AGENTS.md`; they catch the mistakes that are easiest for an LLM to make:

- committing directly on `main` or an unexpected branch
- committing without a Bramble MCP journal reference
- staging `docs/journal.txt` (historical import source only)
- pushing with failing tests

Enable them once per clone:

```powershell
.\scripts\Install-AgentGuardrails.ps1
```

Disable again:

```powershell
.\scripts\Install-AgentGuardrails.ps1 -Disable
```

Commit messages must contain one of:

```text
Journal: bramble#123
Journal: skipped (short reason)
```

`pre-push` runs `pytest` first and aborts the push if tests fail. It uses the
project venv (`.venv/Scripts/python.exe` on Windows, `.venv/bin/python` on
Linux) and falls back to `python` on `PATH`.

Emergency bypass (commit and push):

```powershell
$env:BRAMBLE_SKIP_GUARDRAILS = "1"
git commit
git push
Remove-Item Env:\BRAMBLE_SKIP_GUARDRAILS
```

Use the bypass only for intentional emergencies or when the Bramble MCP journal
is unavailable.
