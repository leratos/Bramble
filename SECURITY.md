# Security Policy

## Security model — read this first

Bramble is designed as a **single-owner tool**: one operator runs the
journal for their own projects. This model has a deliberate but, for
outsiders, surprising property:

> **Reading and searching are cross-project. Any valid token can read the
> entries of ALL projects.** Only *writing* (`journal_append`) is bound to
> the token's project.

Concretely:

- A token effectively grants **read access to the entire journal**.
- Bramble provides **no tenant isolation** and is **not** suitable for
  serving mutually distrusting users on the same instance.
- Only issue tokens to parties you trust with read access to *all* projects
  on that instance (e.g. your own agents/projects).
- If you need tenant isolation, run **separate instances** with separate
  databases.

If you want to run Bramble as a hosted multi-user service, that is **not**
safe without deep changes (real per-tenant read isolation).

## Append-only and personal data

The data model is **append-only**: there are deliberately no update/delete
tools; corrections are new entries. That is an advantage for a development
journal, but it conflicts with deletion obligations (e.g. the GDPR right to
erasure). So do not store personal data — or anything else you may later be
required to delete — that you must be able to remove. For a public service
you would need to add your own deletion/purge strategy.

## Operational recommendations

- The HTTP transport is authenticated (bearer token) — **always** run it
  behind TLS and a reverse proxy, never expose it unprotected.
- Keep the rate limit (per token/IP) and Fail2Ban enabled; see `deploy/`.
- Bind the admin UI to localhost only (default `127.0.0.1`) and reach it
  exclusively through an SSH tunnel — **not** via a public path.
- Tokens and the Argon2id admin secret live outside the repo (`secrets/`,
  excluded via `.gitignore`). Never commit them or write them to
  logs/chat transcripts.
- After rotating a token, restart the MCP service (the token file is read at
  startup).
- Writing admin actions are CSRF-protected and logged append-only to
  `admin_audit_events`.

## Supported versions

Only the latest state on `main` currently receives fixes.

## Reporting a vulnerability

Please do **not** open public issues for security vulnerabilities. Use
GitHub's private reporting instead ("Report a vulnerability" in the
repository's *Security* tab). Describe the reproduction, the affected
version/commit and the potential impact. We acknowledge receipt and
coordinate a fix before disclosure.
