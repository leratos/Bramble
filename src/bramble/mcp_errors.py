"""Error translation for the Bramble MCP tools.

Per Decision H in the Phase-2 concept document, every tool function
gets the :func:`translate_errors` decorator. The decorator turns
``ValueError`` / ``TypeError`` raised by :class:`JournalDB` (or by
in-tool validation) into :class:`fastmcp.exceptions.ToolError` with a
client-readable message. Anything else is logged with a stack trace
and re-raised as a generic ``RuntimeError`` so we don't leak internal
detail through the MCP transport.

All MCP tools in Bramble are ``async``; the decorator is therefore
async-only and expects to wrap a coroutine function.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def translate_errors(
    func: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap an ``async`` tool function with the standard error policy.

    * ``ValueError`` / ``TypeError`` → :class:`ToolError` with the
      original message (the client sees this).
    * Everything else → ``RuntimeError`` with a generic message; the
      original exception is logged at ``ERROR`` with its stack trace.
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "tool %s rejected input: %s: %s",
                func.__name__,
                type(exc).__name__,
                exc,
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "unhandled error in tool %s",
                func.__name__,
            )
            raise RuntimeError(
                f"internal error in {func.__name__}"
            ) from exc

    return wrapper
