"""Tests for Bramble admin authentication helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bramble.admin_auth import (
    AdminAuthenticator,
    AdminSecret,
    LoginRateLimiter,
    SessionStore,
    hash_admin_password,
)


class TestAdminSecret:
    def test_loads_valid_secret(self, tmp_path: Path) -> None:
        path = tmp_path / "admin-ui.json"
        path.write_text(
            json.dumps(
                {
                    "username": "admin",
                    "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$salt$hash",
                }
            ),
            encoding="utf-8",
        )

        secret = AdminSecret.load(path)

        assert secret.username == "admin"
        assert secret.password_hash.startswith("$argon2id$")

    def test_rejects_missing_hash(self, tmp_path: Path) -> None:
        path = tmp_path / "admin-ui.json"
        path.write_text('{"username": "admin"}', encoding="utf-8")

        with pytest.raises(ValueError, match="password_hash"):
            AdminSecret.load(path)


class TestAdminAuthenticator:
    def test_verifies_argon2id_password(self, tmp_path: Path) -> None:
        pytest.importorskip("argon2")
        password_hash = hash_admin_password("correct horse battery staple")
        path = tmp_path / "admin-ui.json"
        path.write_text(
            json.dumps({"username": "admin", "password_hash": password_hash}),
            encoding="utf-8",
        )

        authenticator = AdminAuthenticator(path)

        assert authenticator.verify("admin", "correct horse battery staple") is True
        assert authenticator.verify("admin", "wrong") is False
        assert authenticator.verify("root", "correct horse battery staple") is False


class TestSessionStore:
    def test_session_expires_on_idle_timeout(self) -> None:
        now = 1000.0

        def time_source() -> float:
            return now

        store = SessionStore(
            idle_seconds=10,
            absolute_seconds=100,
            time_source=time_source,
            token_factory=lambda: "sid",
        )
        session_id = store.create("admin")

        assert store.get(session_id) is not None
        now = 1011.0
        assert store.get(session_id) is None

    def test_session_expires_on_absolute_timeout(self) -> None:
        now = 1000.0

        def time_source() -> float:
            return now

        store = SessionStore(
            idle_seconds=100,
            absolute_seconds=100,
            time_source=time_source,
            token_factory=lambda: "sid",
        )
        session_id = store.create("admin")

        now = 1090.0
        assert store.get(session_id) is not None
        now = 1101.0
        assert store.get(session_id) is None

    def test_session_stores_distinct_csrf_token(self) -> None:
        store = SessionStore(
            idle_seconds=10,
            absolute_seconds=100,
            token_factory=lambda: "sid",
            csrf_token_factory=lambda: "csrf",
        )

        session_id = store.create("admin")

        session = store.get(session_id)
        assert session is not None
        assert session.csrf_token == "csrf"


class TestLoginRateLimiter:
    def test_blocks_after_failed_attempt_budget(self) -> None:
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)

        assert limiter.allow("127.0.0.1") is True
        limiter.record_failure("127.0.0.1")
        assert limiter.allow("127.0.0.1") is True
        limiter.record_failure("127.0.0.1")
        assert limiter.allow("127.0.0.1") is False

    def test_success_resets_budget(self) -> None:
        limiter = LoginRateLimiter(max_attempts=1, window_seconds=60)
        limiter.record_failure("127.0.0.1")
        assert limiter.allow("127.0.0.1") is False

        limiter.record_success("127.0.0.1")

        assert limiter.allow("127.0.0.1") is True
