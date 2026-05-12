"""Guards for :mod:`bramble`'s top-level import surface.

These tests protect a subtle but important invariant: importing the
``bramble`` package must not eagerly pull in ``fastmcp``. The
``scripts/init_db.py`` bootstrap is explicitly designed to run on a
fresh checkout with only ``sqlite3`` available, and adds ``src/`` to
``sys.path`` before importing :class:`bramble.journal_db.JournalDB`.
That import path executes ``bramble/__init__.py`` first; any
unconditional import of :mod:`bramble.journal_mcp_server` (or
anything that drags in ``fastmcp``) would break the bootstrap.

Each check runs in a **child Python process** because the live pytest
session has already imported ``fastmcp`` via other tests, so
``sys.modules`` inside this test process is contaminated and useless
for the assertion.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh Python interpreter and return the result."""

    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestPackageImportSurface:
    def test_import_bramble_does_not_load_fastmcp(self) -> None:
        result = _run(
            """
            import sys
            import bramble  # noqa: F401
            assert "fastmcp" not in sys.modules, (
                "fastmcp was imported eagerly via bramble/__init__.py; "
                "init_db.py bootstrap is broken. "
                f"Loaded fastmcp-ish modules: "
                f"{[m for m in sys.modules if 'fastmcp' in m]}"
            )
            """
        )
        assert result.returncode == 0, result.stderr

    def test_import_journal_db_does_not_load_fastmcp(self) -> None:
        # This is the exact path scripts/init_db.py exercises.
        result = _run(
            """
            import sys
            from bramble.journal_db import JournalDB  # noqa: F401
            assert "fastmcp" not in sys.modules, (
                "fastmcp leaked into the JournalDB import path"
            )
            """
        )
        assert result.returncode == 0, result.stderr

    def test_journalmcpserver_is_still_accessible_via_package(self) -> None:
        # Lazy export must remain reachable via the documented API.
        result = _run(
            """
            from bramble import JournalMCPServer
            assert JournalMCPServer.__name__ == "JournalMCPServer"
            print("ok")
            """
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_journalmcpserver_access_triggers_fastmcp_load(self) -> None:
        # Sanity: lazy means lazy, not "never". Once accessed, fastmcp
        # must be in sys.modules.
        result = _run(
            """
            import sys
            import bramble
            assert "fastmcp" not in sys.modules
            _ = bramble.JournalMCPServer
            assert "fastmcp" in sys.modules
            print("ok")
            """
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_unknown_attribute_still_raises_attribute_error(self) -> None:
        # __getattr__ must not silently swallow typos.
        result = _run(
            """
            import bramble
            try:
                bramble.JornalMCPServer  # typo on purpose
            except AttributeError:
                print("ok")
            else:
                raise AssertionError("typo did not raise AttributeError")
            """
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_journalmcpserver_listed_in_dir(self) -> None:
        # IDE discovery: dir(bramble) must include the lazy export.
        result = _run(
            """
            import bramble
            assert "JournalMCPServer" in dir(bramble), dir(bramble)
            print("ok")
            """
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout
