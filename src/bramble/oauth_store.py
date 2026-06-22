"""SQLite persistence for the OAuth 2.1 Authorization Server state.

:class:`OAuthStore` owns the *mutable* OAuth state — registered clients,
authorization codes, access tokens and refresh tokens — in a database
file kept deliberately separate from the append-only journal DB
(Phase-6 decision D4, journal bramble#848). Mixing mutable, short-lived
token rows into the append-only journal would undermine its core
invariant and bloat its Borg snapshots with auth churn.

The four record types are FastMCP/MCP pydantic models, so each row stores
the model's JSON (faithful, forward-compatible round-trip) plus a few
projected columns (``client_id``, ``expires_at``) for indexed lookup and
expiry purging. ``expires_at`` is epoch seconds; a ``NULL`` means "never
expires" (access/refresh tokens may have no expiry).

This class is a pure persistence layer: ``get_*`` returns whatever is
stored without applying expiry semantics. Expiry enforcement and token
rotation are the provider's job (:class:`bramble.oauth_provider`), which
holds the clock; this class only offers :meth:`purge_expired` so the
provider can sweep stale rows. Like :class:`bramble.journal_db.JournalDB`
it is synchronous and uses one short-lived connection per public method;
async callers wrap calls in :func:`asyncio.to_thread` at the provider
layer.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastmcp.server.auth.auth import (
    AccessToken,
    AuthorizationCode,
    OAuthClientInformationFull,
    RefreshToken,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS oauth_clients (
        client_id   TEXT PRIMARY KEY,
        data        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_auth_codes (
        code        TEXT PRIMARY KEY,
        client_id   TEXT NOT NULL,
        expires_at  REAL NOT NULL,
        data        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_access_tokens (
        token       TEXT PRIMARY KEY,
        client_id   TEXT NOT NULL,
        expires_at  REAL,
        data        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
        token          TEXT PRIMARY KEY,
        client_id      TEXT NOT NULL,
        expires_at     REAL,
        access_token   TEXT,
        data           TEXT NOT NULL,
        created_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auth_codes_expiry "
    "ON oauth_auth_codes(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_access_tokens_expiry "
    "ON oauth_access_tokens(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expiry "
    "ON oauth_refresh_tokens(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_access "
    "ON oauth_refresh_tokens(access_token)",
)


class OAuthStore:
    """Durable store for OAuth clients, codes and tokens.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database file. Created (with parent
        directories) on :meth:`initialize`.
    """

    def __init__(self, db_path: Path | str) -> None:
        if isinstance(db_path, str):
            db_path = Path(db_path)
        if not isinstance(db_path, Path):
            raise TypeError("db_path must be a pathlib.Path or str")
        self._db_path: Path = db_path

    @property
    def db_path(self) -> Path:
        """Path of the SQLite database file."""

        return self._db_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Create the schema if absent and enable WAL mode. Idempotent."""

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                logger.warning(
                    "could not enable WAL mode on oauth db; journal_mode is %r",
                    mode,
                )
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.commit()

    # ------------------------------------------------------------------
    # Clients (RFC 7591 dynamic registration + static fallback)
    # ------------------------------------------------------------------
    def save_client(self, client: OAuthClientInformationFull) -> None:
        """Insert or replace a registered client keyed by ``client_id``."""

        if not isinstance(client, OAuthClientInformationFull):
            raise TypeError("client must be an OAuthClientInformationFull")
        if not client.client_id:
            raise ValueError("client.client_id must be set before saving")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_clients "
                "(client_id, data, created_at) VALUES (?, ?, ?)",
                (client.client_id, client.model_dump_json(), _now_iso()),
            )
            conn.commit()

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Return the client with ``client_id`` or ``None``."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM oauth_clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["data"])

    # ------------------------------------------------------------------
    # Authorization codes (short-lived, single-use)
    # ------------------------------------------------------------------
    def save_auth_code(self, auth_code: AuthorizationCode) -> None:
        if not isinstance(auth_code, AuthorizationCode):
            raise TypeError("auth_code must be an AuthorizationCode")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_auth_codes "
                "(code, client_id, expires_at, data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    auth_code.code,
                    auth_code.client_id,
                    float(auth_code.expires_at),
                    auth_code.model_dump_json(),
                    _now_iso(),
                ),
            )
            conn.commit()

    def get_auth_code(self, code: str) -> AuthorizationCode | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM oauth_auth_codes WHERE code = ?", (code,)
            ).fetchone()
        if row is None:
            return None
        return AuthorizationCode.model_validate_json(row["data"])

    def delete_auth_code(self, code: str) -> bool:
        """Delete an auth code (consumed on exchange). Returns whether a row went."""

        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM oauth_auth_codes WHERE code = ?", (code,)
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Access tokens
    # ------------------------------------------------------------------
    def save_access_token(self, token: AccessToken) -> None:
        if not isinstance(token, AccessToken):
            raise TypeError("token must be an AccessToken")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_access_tokens "
                "(token, client_id, expires_at, data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    token.token,
                    token.client_id,
                    _as_real(token.expires_at),
                    token.model_dump_json(),
                    _now_iso(),
                ),
            )
            conn.commit()

    def get_access_token(self, token: str) -> AccessToken | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM oauth_access_tokens WHERE token = ?", (token,)
            ).fetchone()
        if row is None:
            return None
        return AccessToken.model_validate_json(row["data"])

    def delete_access_token(self, token: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM oauth_access_tokens WHERE token = ?", (token,)
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Refresh tokens
    # ------------------------------------------------------------------
    def save_refresh_token(
        self, token: RefreshToken, *, access_token: str | None = None
    ) -> None:
        """Persist a refresh token, optionally pairing it to an access token.

        The pairing column lets the provider rotate the pair on a refresh
        grant and cascade a revocation between the two tokens, mirroring the
        access<->refresh bookkeeping of the in-memory reference provider.
        """

        if not isinstance(token, RefreshToken):
            raise TypeError("token must be a RefreshToken")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_refresh_tokens "
                "(token, client_id, expires_at, access_token, data, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    token.token,
                    token.client_id,
                    _as_real(token.expires_at),
                    access_token,
                    token.model_dump_json(),
                    _now_iso(),
                ),
            )
            conn.commit()

    def get_refresh_token(self, token: str) -> RefreshToken | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM oauth_refresh_tokens WHERE token = ?", (token,)
            ).fetchone()
        if row is None:
            return None
        return RefreshToken.model_validate_json(row["data"])

    def delete_refresh_token(self, token: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM oauth_refresh_tokens WHERE token = ?", (token,)
            )
            conn.commit()
            return cur.rowcount > 0

    def get_paired_access_token(self, refresh_token: str) -> str | None:
        """Return the access token issued alongside ``refresh_token``."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT access_token FROM oauth_refresh_tokens WHERE token = ?",
                (refresh_token,),
            ).fetchone()
        if row is None:
            return None
        return row["access_token"]

    def get_refresh_for_access(self, access_token: str) -> str | None:
        """Return the refresh token paired with ``access_token`` (reverse lookup)."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT token FROM oauth_refresh_tokens WHERE access_token = ?",
                (access_token,),
            ).fetchone()
        if row is None:
            return None
        return row["token"]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def purge_expired(self, now: float) -> dict[str, int]:
        """Delete every code/token whose ``expires_at`` is at or before ``now``.

        ``now`` is epoch seconds, injected so the sweep is deterministic in
        tests. Rows with a ``NULL`` ``expires_at`` (never-expiring tokens)
        are left untouched. Returns the per-table delete counts.
        """

        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise TypeError("now must be epoch seconds (int or float)")
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for table in (
                "oauth_auth_codes",
                "oauth_access_tokens",
                "oauth_refresh_tokens",
            ):
                cur = conn.execute(
                    f"DELETE FROM {table} "
                    "WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (now,),
                )
                counts[table] = cur.rowcount
            conn.commit()
        return counts

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection with sensible defaults."""

        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _as_real(expires_at: int | None) -> float | None:
    """Project an ``int | None`` expiry to the REAL column (``None`` -> NULL)."""

    return None if expires_at is None else float(expires_at)
