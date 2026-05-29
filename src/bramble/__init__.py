"""Bramble – self-hosted MCP journal server.

Top-level convenience exports. Bramble is a tool package, not a
general-purpose library, so the public surface is intentionally flat:
everything that is reasonable to import from outside is reachable
directly from :mod:`bramble`.

Two import tiers
----------------

* **Eager** (loaded on ``import bramble``): the data and configuration
  classes. These only depend on the Python standard library, so
  importing them is cheap and has no third-party prerequisites.

* **Lazy** (loaded on first attribute access via :pep:`562`):
  :class:`JournalMCPServer`. This class pulls in ``fastmcp``, which
  pulls in a large transitive dependency set. Deferring its import
  keeps the ``scripts/init_db.py`` bootstrap path working in
  environments that have ``sqlite3`` but no MCP-server dependencies.

The public API therefore still works the same:

>>> from bramble import JournalDB           # cheap, stdlib only
>>> from bramble import JournalMCPServer    # triggers fastmcp import

Reported by the codex-connector review on PR for chore/phase-2-cleanup
(otherwise ``init_db.py`` crashes with ``ModuleNotFoundError: fastmcp``
on a fresh checkout before deps are installed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bramble.auth_validator import AuthValidator
from bramble.journal_context import JournalContext
from bramble.journal_db import JournalDB
from bramble.journal_digest import JournalDigest
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.project_summary import ProjectSummary
from bramble.rate_limiter import RateLimiter
from bramble.server_config import ServerConfig

if TYPE_CHECKING:
    # Re-export for type checkers without triggering the runtime
    # import. mypy / pyright follow this branch statically.
    from bramble.journal_mcp_server import JournalMCPServer

__all__ = [
    "AuthValidator",
    "JournalContext",
    "JournalDB",
    "JournalDigest",
    "JournalEntry",
    "JournalMCPServer",
    "JournalStatus",
    "ProjectSummary",
    "RateLimiter",
    "ServerConfig",
]
__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Lazily resolve heavyweight exports on first access.

    Only :class:`JournalMCPServer` is deferred today; everything else
    is imported eagerly above. Falls through to ``AttributeError`` for
    unknown names so typos still fail loudly.
    """

    if name == "JournalMCPServer":
        from bramble.journal_mcp_server import JournalMCPServer

        # Cache on the module so subsequent ``bramble.JournalMCPServer``
        # lookups skip __getattr__ entirely. Without this, every access
        # would re-run the import machinery (cheap once cached in
        # sys.modules, but still noisy).
        import sys

        module = sys.modules[__name__]
        setattr(module, name, JournalMCPServer)
        return JournalMCPServer

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Make :class:`JournalMCPServer` discoverable in ``dir(bramble)``.

    Without this, ``dir(bramble)`` only lists the eagerly imported
    names, and IDEs/REPLs would hide the lazy export.
    """

    return sorted(set(__all__) | set(globals()))
