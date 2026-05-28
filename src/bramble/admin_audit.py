"""Append-only audit log for Bramble admin UI actions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bramble.journal_db import JournalDB

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS admin_audit_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp    TEXT NOT NULL,
        actor        TEXT NOT NULL,
        action       TEXT NOT NULL,
        target_type  TEXT NOT NULL,
        target       TEXT,
        result       TEXT NOT NULL,
        client_ip    TEXT,
        details_json TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_admin_audit_ts
        ON admin_audit_events(timestamp DESC, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_admin_audit_action_ts
        ON admin_audit_events(action, timestamp DESC)
    """,
)

_SENSITIVE_KEY_PARTS = ("token", "password", "secret", "session")


@dataclass(frozen=True, slots=True)
class AdminAuditEvent:
    """One persisted admin audit event."""

    id: int
    timestamp: datetime
    actor: str
    action: str
    target_type: str
    target: str | None
    result: str
    client_ip: str | None
    details: dict[str, object]


class AdminAuditLog:
    """Small append-only SQLite audit log for admin actions."""

    def __init__(self, db: JournalDB | Path | str) -> None:
        if isinstance(db, JournalDB):
            db_path = db.db_path
        elif isinstance(db, str):
            db_path = Path(db)
        elif isinstance(db, Path):
            db_path = db
        else:
            raise TypeError("db must be a JournalDB, pathlib.Path, or str")
        self._db_path = db_path

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        """Create the audit schema if it does not exist."""

        with self._connect() as conn:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.commit()

    def append(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target: str | None,
        result: str,
        client_ip: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> AdminAuditEvent:
        """Append and return a sanitized audit event."""

        actor = _require_non_empty(actor, "actor")
        action = _require_non_empty(action, "action")
        target_type = _require_non_empty(target_type, "target_type")
        result = _require_non_empty(result, "result")
        if target is not None and not isinstance(target, str):
            raise TypeError("target must be a string or None")
        if client_ip is not None and not isinstance(client_ip, str):
            raise TypeError("client_ip must be a string or None")

        sanitized_details = _sanitize_details(details or {})
        timestamp = datetime.now(UTC)
        params = (
            timestamp.isoformat(),
            actor,
            action,
            target_type,
            target,
            result,
            client_ip,
            json.dumps(sanitized_details, sort_keys=True),
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO admin_audit_events
                    (timestamp, actor, action, target_type, target, result,
                     client_ip, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            conn.commit()
            event_id = cursor.lastrowid

        if event_id is None or event_id <= 0:
            raise RuntimeError("sqlite did not return a lastrowid")
        return AdminAuditEvent(
            id=event_id,
            timestamp=timestamp,
            actor=actor,
            action=action,
            target_type=target_type,
            target=target,
            result=result,
            client_ip=client_ip,
            details=sanitized_details,
        )

    def read_recent(self, limit: int = 20) -> list[AdminAuditEvent]:
        """Return recent audit events, newest first."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an int")
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, actor, action, target_type, target, result,
                       client_ip, details_json
                FROM admin_audit_events
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    @contextmanager
    def _connect(self) -> Any:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


def _row_to_event(row: sqlite3.Row) -> AdminAuditEvent:
    timestamp = datetime.fromisoformat(row["timestamp"])
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    details = json.loads(row["details_json"])
    if not isinstance(details, dict):
        details = {}
    return AdminAuditEvent(
        id=row["id"],
        timestamp=timestamp,
        actor=row["actor"],
        action=row["action"],
        target_type=row["target_type"],
        target=row["target"],
        result=row["result"],
        client_ip=row["client_ip"],
        details=details,
    )


def _require_non_empty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _sanitize_details(details: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, value in details.items():
        if not isinstance(key, str) or not key:
            raise ValueError("details keys must be non-empty strings")
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            sanitized[key] = "[redacted]"
        elif isinstance(value, str | int | float | bool) or value is None:
            sanitized[key] = value
        else:
            sanitized[key] = str(value)
    return sanitized
