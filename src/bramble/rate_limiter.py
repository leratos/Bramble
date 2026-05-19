"""In-memory request rate limiting for the Bramble MCP server.

Per Phase-3 Decision E the server throttles requests with a
token-bucket algorithm held entirely in memory. In-memory is a
deliberate choice: Bramble is a single-process service, so a restart
resetting every bucket is acceptable.

:class:`RateLimiter` keeps two independent bucket pools:

* **per project** – one bucket per project, fed by that project's
  bearer token. Each project has exactly one token, so "per project"
  and "per token" are the same thing here. Default 60 requests/minute.
* **per client IP** – a coarse backstop, applied before the token is
  known. Default 120 requests/minute.

A bucket's capacity equals its requests-per-minute value and it
refills at ``rpm / 60`` units per second, so a fresh bucket allows a
burst of ``rpm`` requests and then settles to the steady rate.

The module depends only on the standard library and never imports
``fastmcp``; the request path turns a refusal into an MCP error, but
the limiter itself stays transport-agnostic and unit-testable
in-process (a Phase-3 end-of-phase criterion).

Concurrency: the limiter is consumed from the FastMCP middleware,
which runs on the single asyncio event-loop thread. :meth:`allow_ip`
and :meth:`allow_project` are fully synchronous, so they execute
atomically with respect to other coroutines – no lock is required.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60.0


@dataclass(slots=True)
class _Bucket:
    """Mutable token-bucket state: current level and last refill time."""

    tokens: float
    last_refill: float


class RateLimiter:
    """Token-bucket rate limiter with per-project and per-IP pools.

    Parameters
    ----------
    per_token_rpm:
        Requests per minute allowed for a single project/token. Also
        the per-project bucket capacity.
    per_ip_rpm:
        Requests per minute allowed for a single client IP. Also the
        per-IP bucket capacity.
    time_source:
        Callable returning a monotonically increasing seconds value.
        Defaults to :func:`time.monotonic`; tests inject a fake clock
        so they never have to sleep.
    """

    def __init__(
        self,
        *,
        per_token_rpm: int = 60,
        per_ip_rpm: int = 120,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_capacity = float(_validate_rpm(per_token_rpm, "per_token_rpm"))
        self._ip_capacity = float(_validate_rpm(per_ip_rpm, "per_ip_rpm"))
        self._token_refill = self._token_capacity / _SECONDS_PER_MINUTE
        self._ip_refill = self._ip_capacity / _SECONDS_PER_MINUTE
        self._now = time_source

        self._token_buckets: dict[str, _Bucket] = {}
        self._ip_buckets: dict[str, _Bucket] = {}

    # ------------------------------------------------------------------
    # Public checks
    # ------------------------------------------------------------------
    def allow_project(self, project: str) -> bool:
        """Consume one request from ``project``'s bucket.

        Returns ``True`` if the request fits within budget, ``False``
        if the project is currently rate-limited.
        """

        return self._consume(
            self._token_buckets, project, self._token_capacity, self._token_refill
        )

    def allow_ip(self, client_ip: str) -> bool:
        """Consume one request from ``client_ip``'s bucket.

        Returns ``True`` if the request fits within budget, ``False``
        if the IP is currently rate-limited.
        """

        return self._consume(
            self._ip_buckets, client_ip, self._ip_capacity, self._ip_refill
        )

    # ------------------------------------------------------------------
    # Core algorithm
    # ------------------------------------------------------------------
    def _consume(
        self,
        buckets: dict[str, _Bucket],
        key: str,
        capacity: float,
        refill_per_second: float,
    ) -> bool:
        """Refill ``key``'s bucket for elapsed time, then take one token."""

        now = self._now()
        bucket = buckets.get(key)
        if bucket is None:
            # A previously unseen key starts with a full bucket.
            bucket = _Bucket(tokens=capacity, last_refill=now)
            buckets[key] = bucket

        elapsed = now - bucket.last_refill
        bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_per_second)
        bucket.last_refill = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True

        logger.warning(
            "rate limit exceeded",
            extra={"event": "rate_limited", "rate_limit_key": key},
        )
        return False


def _validate_rpm(value: int, name: str) -> int:
    """Validate a requests-per-minute argument: positive, non-bool int."""

    # bool is a subclass of int – exclude it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
