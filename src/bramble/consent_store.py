"""One-time, request-bound consent approvals for the OAuth owner gate.

After the resource owner approves an authorization on the consent screen,
the gate records a short-lived approval keyed by ``(session_id,
fingerprint)`` where the fingerprint binds the approval to that exact
``/authorize`` request (client, redirect, scope, PKCE challenge). The
subsequent ``GET /authorize`` consumes it exactly once before delegating
to the framework's code issuance. This keeps a stale or cross-request
approval from auto-authorizing a different client.

In-memory is fine: Bramble is a single-process service, and an approval is
only valid for the few seconds between the consent redirect and the
authorize call.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class ConsentApprovalStore:
    """Short-TTL, single-use store of approved ``(session_id, fingerprint)``.

    Parameters
    ----------
    ttl_seconds:
        How long an approval stays valid after :meth:`approve`. Kept small;
        it only has to survive the consent->authorize redirect.
    time_source:
        Callable returning epoch seconds. Injected for deterministic tests.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 120,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise TypeError("ttl_seconds must be an int")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._now = time_source
        self._approvals: dict[tuple[str, str], float] = {}

    def approve(self, *, session_id: str, fingerprint: str) -> None:
        if not session_id or not fingerprint:
            raise ValueError("session_id and fingerprint must be non-empty")
        self._approvals[(session_id, fingerprint)] = float(self._now()) + self._ttl

    def consume(self, *, session_id: str | None, fingerprint: str) -> bool:
        """Return ``True`` once for a valid, unexpired approval, then drop it."""

        if not session_id or not fingerprint:
            return False
        key = (session_id, fingerprint)
        expires_at = self._approvals.pop(key, None)
        if expires_at is None:
            return False
        return float(self._now()) <= expires_at
