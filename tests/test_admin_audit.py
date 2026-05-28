"""Tests for :mod:`bramble.admin_audit`."""

from __future__ import annotations

from bramble.admin_audit import AdminAuditLog
from bramble.journal_db import JournalDB


def test_appends_and_reads_recent_events(db: JournalDB) -> None:
    audit = AdminAuditLog(db)
    audit.initialize()

    event = audit.append(
        actor="admin",
        action="token.rotate",
        target_type="token",
        target="bramble",
        result="success",
        client_ip="127.0.0.1",
        details={"mutation": "rotated"},
    )

    assert event.id == 1
    assert event.action == "token.rotate"
    assert event.details == {"mutation": "rotated"}
    assert audit.read_recent()[0] == event


def test_redacts_sensitive_detail_keys(db: JournalDB) -> None:
    audit = AdminAuditLog(db)
    audit.initialize()

    event = audit.append(
        actor="admin",
        action="token.create",
        target_type="token",
        target="bramble",
        result="success",
        details={"token": "secret-token", "safe": "kept"},
    )

    assert event.details == {"safe": "kept", "token": "[redacted]"}
    assert "secret-token" not in str(audit.read_recent()[0].details)
