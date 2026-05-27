"""Tests for the Starlette admin UI app."""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from starlette.testclient import TestClient

from bramble.admin_app import create_admin_app
from bramble.admin_auth import LoginRateLimiter, SessionStore
from bramble.admin_config import AdminConfig
from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus


class _FakeAuthenticator:
    username = "admin"

    def verify(self, username: str, password: str) -> bool:
        return username == "admin" and password == "secret"


@pytest.fixture()
def admin_client(db: JournalDB) -> TestClient:
    db.append(
        JournalEntry(
            project="bramble",
            status=JournalStatus.NOTIZ,
            title="Admin UI",
            content="read-only dashboard entry",
        )
    )
    db.append(
        JournalEntry(
            project="elder-berry",
            status=JournalStatus.ABGESCHLOSSEN,
            content="connector setup",
        )
    )
    config = AdminConfig(db_path=db.db_path, allowed_hosts=("testserver",))
    app = create_admin_app(
        db,
        _FakeAuthenticator(),  # type: ignore[arg-type]
        config=config,
        sessions=SessionStore(
            idle_seconds=1800,
            absolute_seconds=28800,
            token_factory=lambda: "test-session",
        ),
        login_limiter=LoginRateLimiter(max_attempts=2, window_seconds=60),
    )
    return TestClient(app)


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303


class TestAdminApp:
    def test_dashboard_requires_login(self, admin_client: TestClient) -> None:
        response = admin_client.get("/", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/"

    def test_successful_login_sets_hardened_cookie(
        self, admin_client: TestClient
    ) -> None:
        response = admin_client.post(
            "/login",
            data={"username": "admin", "password": "secret", "next": "/"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        cookie = response.headers["set-cookie"]
        assert "bramble_admin_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie

    def test_dashboard_lists_read_only_stats(self, admin_client: TestClient) -> None:
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert "Dashboard" in response.text
        assert "bramble" in response.text
        assert "elder-berry" in response.text
        assert "read-only dashboard entry" in response.text

    def test_project_view_searches_without_writing(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        _login(admin_client)
        before = len(db.read("bramble", n=10))

        response = admin_client.get("/projects/bramble?q=dashboard")

        assert response.status_code == 200
        assert "Suchergebnisse" in response.text
        assert "read-only dashboard entry" in response.text
        assert len(db.read("bramble", n=10)) == before

    def test_invalid_project_is_404(self, admin_client: TestClient) -> None:
        _login(admin_client)

        response = admin_client.get("/projects/Bad_Project")

        assert response.status_code == 404

    def test_failed_logins_are_rate_limited(self, admin_client: TestClient) -> None:
        for _ in range(2):
            response = admin_client.post(
                "/login",
                data={"username": "admin", "password": "wrong"},
            )
            assert response.status_code == 401

        response = admin_client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
        )

        assert response.status_code == 429

    def test_security_headers_are_set(self, admin_client: TestClient) -> None:
        response = admin_client.get("/login")

        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "script-src 'none'" in response.headers["content-security-policy"]
