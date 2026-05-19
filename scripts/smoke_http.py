"""Smoke test the Bramble MCP server over its authenticated HTTP transport.

This script is for *manual* end-to-end verification. It is intentionally
not part of the pytest suite – the suite uses the in-process FastMCP
client and does not need a running server. Use this when you want to
poke a real, network-bound instance and watch the JSON logs on the
server side.

Since Phase 3 the HTTP transport requires a bearer token on every tool
call, so a token is mandatory:

Workflow
--------
Terminal 1 (server)::

    bramble-server --transport http --host 127.0.0.1 --port 8765 \\
        --tokens-file ./secrets/tokens.json --log-level DEBUG

Terminal 2 (this script)::

    python scripts/smoke_http.py --token <bramble-token>
    # or, against the deployed endpoint:
    python scripts/smoke_http.py \\
        --url https://journal.last-strawberry.com/mcp/ \\
        --token <bramble-token> --project bramble

The ``--token`` must belong to ``--project`` (default ``bramble``):
every write the script makes goes into that project.

What it does
------------
1. Connects with the token, lists tools, checks all four are present.
2. Verifies a tokenless request is rejected (auth gate is on).
3. Appends two journal entries to ``--project``.
4. Reads them back via ``journal_read``.
5. Searches for a keyword only one entry contains.
6. Verifies a write into a *foreign* project is rejected (Decision B).
7. Calls ``journal_list_projects`` and prints the aggregate view.
8. Issues two deliberately bad calls (unknown status, non-kebab
   project) to verify clean ``ToolError`` translation.

Exit codes
----------
* ``0`` – every check passed.
* ``1`` – an expected check failed (real bug).
* ``2`` – the server was not reachable.
* ``3`` – something else blew up; full traceback is printed.

The script writes to the real database the server points at, so the
entries accumulate across runs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

# fastmcp is a runtime dependency of bramble; importing it here is fine.
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError


DEFAULT_URL = "http://127.0.0.1:8765/mcp/"

# A kebab-case project the token is guaranteed not to own, used for the
# write-scope rejection check.
FOREIGN_PROJECT = "smoke-foreign-project"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def info(msg: str) -> None:
    print(f"-> {msg}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Client / result helpers
# ---------------------------------------------------------------------------
def make_client(url: str, token: str | None) -> Client:
    """Build an MCP client, optionally carrying a bearer token."""

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return Client(StreamableHttpTransport(url, headers=headers))


def unwrap(result: Any) -> Any:
    """Extract the structured payload from a FastMCP call_tool result.

    Recent fastmcp exposes the parsed value as ``.data``; older
    versions only expose ``.content`` (TextContent whose ``.text``
    holds JSON). Try the rich path first, then fall back.
    """

    if hasattr(result, "data") and result.data is not None:
        return result.data

    content = getattr(result, "content", None)
    if content:
        text = getattr(content[0], "text", None)
        if text is not None:
            import json

            return json.loads(text)

    return result


# ---------------------------------------------------------------------------
# Main smoke flow
# ---------------------------------------------------------------------------
EXPECTED_TOOLS = {
    "journal_read",
    "journal_append",
    "journal_search",
    "journal_list_projects",
}


async def run_smoke(url: str, token: str, project: str) -> int:
    info(f"connecting to {url} as project {project!r}")

    async with make_client(url, token) as client:
        # ------------------------------------------------------------------
        # 1. Tool discovery
        # ------------------------------------------------------------------
        section("tool discovery")
        tool_names = {t.name for t in await client.list_tools()}
        print(f"  registered tools: {sorted(tool_names)}")
        missing = EXPECTED_TOOLS - tool_names
        if missing:
            fail(f"missing expected tools: {sorted(missing)}")
            return 1
        ok("all four expected tools are registered")

        # ------------------------------------------------------------------
        # 2. The auth gate rejects a tokenless request
        # ------------------------------------------------------------------
        section("auth gate (request without a token)")
        async with make_client(url, None) as anon:
            try:
                await anon.call_tool("journal_list_projects", {})
            except ToolError as exc:
                ok(f"tokenless call rejected as expected: {exc}")
            except Exception as exc:  # noqa: BLE001
                fail(f"expected ToolError, got {type(exc).__name__}: {exc}")
                return 1
            else:
                fail("server accepted a tool call without any token")
                return 1

        # ------------------------------------------------------------------
        # 3. Append two entries to the token's project
        # ------------------------------------------------------------------
        section(f"appending entries to {project!r}")
        run_marker = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        needle = f"smokeneedle{run_marker[-6:]}"

        result = await client.call_tool(
            "journal_append",
            {
                "project": project,
                "status": "notiz",
                "content": f"smoke run {run_marker}: alpha entry",
                "phase": "Phase 3",
                "title": "Smoke alpha",
            },
        )
        alpha = unwrap(result)
        print(f"  appended id={alpha['id']} status={alpha['status']!r}")

        result = await client.call_tool(
            "journal_append",
            {
                "project": project,
                "status": "bugfix",
                "content": f"smoke run {run_marker}: beta entry with keyword {needle}",
                "title": "Smoke beta",
            },
        )
        beta = unwrap(result)
        print(f"  appended id={beta['id']} status={beta['status']!r}")
        ok("two append calls succeeded")

        # ------------------------------------------------------------------
        # 4. Read back
        # ------------------------------------------------------------------
        section(f"journal_read {project} (n=10)")
        result = await client.call_tool(
            "journal_read", {"project": project, "n": 10}
        )
        entries = unwrap(result)
        print(f"  {len(entries)} entries returned (newest first)")
        if len(entries) < 2:
            fail("expected at least the two entries we just wrote")
            return 1
        if entries[0]["id"] != beta["id"]:
            fail(
                f"expected newest-first: top id should be {beta['id']}, "
                f"got {entries[0]['id']}"
            )
            return 1
        ok("read returned entries newest-first")

        # ------------------------------------------------------------------
        # 5. Search
        # ------------------------------------------------------------------
        section(f"journal_search {project} for {needle!r}")
        result = await client.call_tool(
            "journal_search",
            {"project": project, "query": needle, "limit": 5},
        )
        hits = unwrap(result)
        print(f"  {len(hits)} hit(s)")
        if len(hits) != 1 or hits[0]["id"] != beta["id"]:
            fail(
                f"expected exactly one hit (id={beta['id']}); "
                f"got {[h['id'] for h in hits]}"
            )
            return 1
        ok("FTS5 search found exactly the beta entry")

        # ------------------------------------------------------------------
        # 6. Write-scope binding: a foreign project is refused
        # ------------------------------------------------------------------
        section(f"write-scope check (append into {FOREIGN_PROJECT!r})")
        try:
            await client.call_tool(
                "journal_append",
                {
                    "project": FOREIGN_PROJECT,
                    "status": "notiz",
                    "content": "this should be rejected by the scope check",
                },
            )
        except ToolError as exc:
            ok(f"foreign-project write rejected as expected: {exc}")
        except Exception as exc:  # noqa: BLE001
            fail(f"expected ToolError, got {type(exc).__name__}: {exc}")
            return 1
        else:
            fail("server accepted a write into a foreign project")
            return 1

        # ------------------------------------------------------------------
        # 7. Overview after writes
        # ------------------------------------------------------------------
        section("journal_list_projects (after writes)")
        result = await client.call_tool("journal_list_projects", {})
        projects_after = unwrap(result)
        for p in projects_after:
            print(
                f"  {p['project']:20s} "
                f"count={p['entry_count']:3d}  last={p['last_timestamp']}"
            )
        if project not in {p["project"] for p in projects_after}:
            fail(f"expected {project!r} in the overview")
            return 1
        ok(f"{project!r} appears in the overview")

        # ------------------------------------------------------------------
        # 8. Negative: unknown status
        # ------------------------------------------------------------------
        section("negative test: unknown status")
        try:
            await client.call_tool(
                "journal_append",
                {"project": project, "status": "erfunden", "content": "x"},
            )
        except ToolError as exc:
            ok(f"rejected as expected: {exc}")
        except Exception as exc:  # noqa: BLE001
            fail(f"expected ToolError, got {type(exc).__name__}: {exc}")
            return 1
        else:
            fail("server accepted an unknown status value")
            return 1

        # ------------------------------------------------------------------
        # 9. Negative: non-kebab project name
        # ------------------------------------------------------------------
        section("negative test: non-kebab project name")
        try:
            await client.call_tool("journal_read", {"project": "Bad_Name", "n": 5})
        except ToolError as exc:
            ok(f"rejected as expected: {exc}")
        except Exception as exc:  # noqa: BLE001
            fail(f"expected ToolError, got {type(exc).__name__}: {exc}")
            return 1
        else:
            fail("server accepted a non-kebab project name")
            return 1

        print("\nAll smoke checks passed.")
        return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test the Bramble MCP HTTP server."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"MCP HTTP endpoint URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="bearer token; must belong to --project.",
    )
    parser.add_argument(
        "--project",
        default="bramble",
        help="project the token owns and the script writes to (default: bramble).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run_smoke(args.url, args.token, args.project))
    except (ConnectionRefusedError, ConnectionError) as exc:
        fail(f"connection failed: {exc}")
        fail("is the server running on the URL above?")
        fail(
            "start it with: bramble-server --transport http "
            "--host 127.0.0.1 --port 8765 --tokens-file ./secrets/tokens.json"
        )
        return 2
    except KeyboardInterrupt:
        fail("aborted by user")
        return 130
    except Exception:
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
