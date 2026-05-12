"""Smoke test the Bramble MCP server over its HTTP transport.

This script is for *manual* end-to-end verification. It is intentionally
not part of the pytest suite – the suite uses the in-process FastMCP
client and does not need a running server. Use this when you want to
poke a real, network-bound instance and watch the JSON logs on the
server side.

Workflow
--------
Terminal 1 (server)::

    bramble-server --transport http --host 127.0.0.1 --port 8765 \\
        --log-level DEBUG

Terminal 2 (this script)::

    python scripts/smoke_http.py
    # or, against a different endpoint:
    python scripts/smoke_http.py --url http://127.0.0.1:9000/mcp/

What it does
------------
1. Connects to the MCP endpoint, lists the registered tools, and
   verifies that all four Bramble tools are present.
2. Appends three journal entries (two for ``bramble``, one for
   ``elder-berry``) so the read / search / list paths have data.
3. Reads them back via ``journal_read``.
4. Searches for a keyword that only one entry contains.
5. Calls ``journal_list_projects`` and prints the aggregate view.
6. Issues two deliberately bad calls (unknown status, non-kebab
   project) to verify that :func:`bramble.mcp_errors.translate_errors`
   produces a clean ``ToolError`` instead of a 500.

Exit codes
----------
* ``0`` – every check passed.
* ``1`` – an expected check failed (real bug).
* ``2`` – the server was not reachable.
* ``3`` – something else blew up; full traceback is printed.

The script writes to the real database the server points at. By
default that is ``./data/bramble.db`` next to the project root, so
the entries will accumulate across runs. Wipe the file if you want a
clean slate.
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
from fastmcp.exceptions import ToolError


DEFAULT_URL = "http://127.0.0.1:8765/mcp/"


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
# Result unwrapping
# ---------------------------------------------------------------------------
def unwrap(result: Any) -> Any:
    """Extract the structured payload from a FastMCP call_tool result.

    FastMCP's ``Client.call_tool`` returns a ``CallToolResult`` object.
    Recent versions expose the parsed value as ``.data``. Older
    versions only expose ``.content`` (a list of ``TextContent``
    objects whose ``.text`` holds JSON). Try the rich path first; fall
    back to parsing JSON from the first text content so this script
    works across a wider range of fastmcp releases.
    """

    if hasattr(result, "data") and result.data is not None:
        return result.data

    content = getattr(result, "content", None)
    if content:
        first = content[0]
        text = getattr(first, "text", None)
        if text is not None:
            import json

            return json.loads(text)

    # Fall-through: return as-is and let the caller cope.
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


async def run_smoke(url: str) -> int:
    info(f"connecting to {url}")

    async with Client(url) as client:
        # ------------------------------------------------------------------
        # 1. Tool discovery
        # ------------------------------------------------------------------
        section("tool discovery")
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        print(f"  registered tools: {sorted(tool_names)}")

        missing = EXPECTED_TOOLS - tool_names
        if missing:
            fail(f"missing expected tools: {sorted(missing)}")
            return 1
        ok("all four expected tools are registered")

        # ------------------------------------------------------------------
        # 2. Initial state
        # ------------------------------------------------------------------
        section("initial project overview")
        result = await client.call_tool("journal_list_projects", {})
        projects_before = unwrap(result)
        print(f"  {len(projects_before)} project(s) before writes")
        for p in projects_before:
            print(
                f"    {p['project']:20s} "
                f"count={p['entry_count']:3d}  "
                f"last={p['last_timestamp']}"
            )

        # ------------------------------------------------------------------
        # 3. Append three entries
        # ------------------------------------------------------------------
        section("appending entries")
        run_marker = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")

        result = await client.call_tool(
            "journal_append",
            {
                "project": "bramble",
                "status": "notiz",
                "content": f"smoke run {run_marker}: alpha entry",
                "phase": "Phase 2",
                "title": "Smoke alpha",
            },
        )
        alpha = unwrap(result)
        print(f"  appended id={alpha['id']} project={alpha['project']!r} "
              f"status={alpha['status']!r}")

        result = await client.call_tool(
            "journal_append",
            {
                "project": "bramble",
                "status": "bugfix",
                "content": (
                    f"smoke run {run_marker}: beta entry with keyword "
                    f"smokeneedle{run_marker[-6:]}"
                ),
                "title": "Smoke beta",
            },
        )
        beta = unwrap(result)
        print(f"  appended id={beta['id']} status={beta['status']!r}")

        result = await client.call_tool(
            "journal_append",
            {
                "project": "elder-berry",
                "status": "notiz",
                "content": f"smoke run {run_marker}: isolation check",
            },
        )
        gamma = unwrap(result)
        print(f"  appended id={gamma['id']} project={gamma['project']!r}")

        ok("three append calls succeeded")

        # ------------------------------------------------------------------
        # 4. Read back
        # ------------------------------------------------------------------
        section("journal_read bramble (n=10)")
        result = await client.call_tool("journal_read", {"project": "bramble", "n": 10})
        entries = unwrap(result)
        print(f"  {len(entries)} entries returned (newest first)")
        for e in entries[:5]:
            title = e["title"] or "-"
            print(f"    [{e['id']:>4}] {e['status']:14s} {title}")
        if len(entries) < 2:
            fail("expected at least the two bramble entries we just wrote")
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
        needle = f"smokeneedle{run_marker[-6:]}"
        section(f"journal_search bramble for {needle!r}")
        result = await client.call_tool(
            "journal_search",
            {"project": "bramble", "query": needle, "limit": 5},
        )
        hits = unwrap(result)
        print(f"  {len(hits)} hit(s)")
        for h in hits:
            print(f"    [{h['id']:>4}] {h['content'][:70]}")
        if len(hits) != 1 or hits[0]["id"] != beta["id"]:
            fail(
                f"expected exactly one hit (id={beta['id']}); "
                f"got {[h['id'] for h in hits]}"
            )
            return 1
        ok("FTS5 search found exactly the beta entry")

        # ------------------------------------------------------------------
        # 6. Project isolation
        # ------------------------------------------------------------------
        section("project isolation check (search bramble keyword in elder-berry)")
        result = await client.call_tool(
            "journal_search",
            {"project": "elder-berry", "query": needle, "limit": 5},
        )
        cross_hits = unwrap(result)
        if cross_hits:
            fail(f"search leaked across projects: {cross_hits}")
            return 1
        ok("no cross-project leakage")

        # ------------------------------------------------------------------
        # 7. Overview after writes
        # ------------------------------------------------------------------
        section("journal_list_projects (after writes)")
        result = await client.call_tool("journal_list_projects", {})
        projects_after = unwrap(result)
        for p in projects_after:
            print(
                f"  {p['project']:20s} "
                f"count={p['entry_count']:3d}  "
                f"last={p['last_timestamp']}"
            )

        names = {p["project"] for p in projects_after}
        if not {"bramble", "elder-berry"} <= names:
            fail(f"expected bramble and elder-berry in overview, got {names}")
            return 1
        ok("both projects appear in the overview")

        # ------------------------------------------------------------------
        # 8. Negative: unknown status
        # ------------------------------------------------------------------
        section("negative test: unknown status")
        try:
            await client.call_tool(
                "journal_append",
                {
                    "project": "bramble",
                    "status": "erfunden",
                    "content": "this should be rejected",
                },
            )
        except ToolError as exc:
            ok(f"rejected as expected: ToolError: {exc}")
        except Exception as exc:  # noqa: BLE001 – we want to see *anything* else
            fail(
                f"expected ToolError, got {type(exc).__name__}: {exc}"
            )
            return 1
        else:
            fail("server accepted an unknown status value")
            return 1

        # ------------------------------------------------------------------
        # 9. Negative: non-kebab project name
        # ------------------------------------------------------------------
        section("negative test: non-kebab project name")
        try:
            await client.call_tool(
                "journal_read",
                {"project": "Bad_Name", "n": 5},
            )
        except ToolError as exc:
            ok(f"rejected as expected: ToolError: {exc}")
        except Exception as exc:  # noqa: BLE001
            fail(
                f"expected ToolError, got {type(exc).__name__}: {exc}"
            )
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run_smoke(args.url))
    except (ConnectionRefusedError, ConnectionError) as exc:
        fail(f"connection failed: {exc}")
        fail("is the server running on the URL above?")
        fail(
            "start it with: "
            "bramble-server --transport http --host 127.0.0.1 --port 8765"
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
