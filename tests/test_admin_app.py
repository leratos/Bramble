"""Tests for the Starlette admin UI app."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from starlette.testclient import TestClient

from bramble.admin_app import create_admin_app
from bramble.admin_auth import LoginRateLimiter, SessionStore
from bramble.admin_config import AdminConfig
from bramble.admin_time import format_display_datetime, get_display_timezone
from bramble.journal_db import JournalDB
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.token_store import TokenStore, write_token_map


class _FakeAuthenticator:
    username = "admin"

    def verify(self, username: str, password: str) -> bool:
        return username == "admin" and password == "secret"


@pytest.fixture()
def admin_client(db: JournalDB, tmp_path: Path) -> TestClient:
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
    tokens_file = tmp_path / "secrets" / "tokens.json"
    write_token_map(tokens_file, {"bramble": "tok-bramble"})
    config = AdminConfig(
        db_path=db.db_path,
        tokens_file=tokens_file,
        allowed_hosts=("testserver",),
    )
    app = create_admin_app(
        db,
        _FakeAuthenticator(),  # type: ignore[arg-type]
        config=config,
        sessions=SessionStore(
            idle_seconds=1800,
            absolute_seconds=28800,
            token_factory=lambda: "test-session",
            csrf_token_factory=lambda: "test-csrf",
        ),
        login_limiter=LoginRateLimiter(max_attempts=2, window_seconds=60),
        token_store=TokenStore(tokens_file, token_factory=lambda: "generated-token"),
    )
    return TestClient(app)


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _tokens_file(client: TestClient) -> Path:
    return client.app.state.admin.config.tokens_file


def _csrf(client: TestClient) -> str:
    response = client.get("/tokens")
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


class TestAdminApp:
    def test_dashboard_requires_login(self, admin_client: TestClient) -> None:
        response = admin_client.get("/", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/"

    def test_global_search_requires_login(self, admin_client: TestClient) -> None:
        response = admin_client.get("/search", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/search"

    def test_help_requires_login(self, admin_client: TestClient) -> None:
        response = admin_client.get("/help", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/help"

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

    def test_dashboard_renders_open_items_and_digest_snapshot(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                title="Open item",
                content="open dashboard task",
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="recent bugfix",
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                title="Decision: dashboard metrics",
                content="decision note",
                tags=["decision"],
            )
        )
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert "Neueste offene Arbeitspunkte" in response.text
        assert "open dashboard task" in response.text
        assert "7 Tage Bugfixes" in response.text
        assert "7 Tage Entscheidungen" in response.text

    def test_dashboard_open_metric_uses_total_not_preview_limit(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
        for i in range(12):
            db.append(
                JournalEntry(
                    project="berry-gym",
                    status=JournalStatus.IN_ARBEIT,
                    content=f"dashboard open total task {i}",
                    timestamp=now + timedelta(minutes=i),
                )
            )
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert re.search(
            r"Offene Punkte</span>\s*<strong>12</strong>",
            response.text,
        )

    def test_dashboard_links_help_without_workflow_panel(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert 'href="http://testserver/help">Hilfe</a>' in response.text
        assert "Workflow-Hinweise" not in response.text
        assert "Phase-4e" not in response.text

    def test_help_renders_workflow_guidance(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.get("/help")

        assert response.status_code == 200
        assert "Workflow-Hinweise" in response.text
        assert "Phase-4e" not in response.text
        assert "in_arbeit" in response.text
        assert "decision" in response.text
        assert "Append-only Journal-Eintrag geschrieben" in response.text
        assert "Einträge erstellen" in response.text
        assert "Korrektur-Assistent" in response.text
        assert "Offene Punkte" in response.text
        assert "Suche und Filter" in response.text

    def test_dashboard_formats_timestamps_in_display_timezone(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                title="DST timestamp",
                content="display timezone check",
                timestamp=datetime(2026, 5, 28, 20, 41, 44, 656494, tzinfo=UTC),
            )
        )
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert 'datetime="2026-05-28T20:41:44.656494+00:00"' in response.text
        assert "2026-05-28 22:41 CEST" in response.text
        assert ">2026-05-28T20:41:44.656494+00:00</time>" not in response.text

    def test_dashboard_shows_entry_metadata(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        old = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="old metadata display check",
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                title="Metadata",
                content="metadata display check",
                actor="codex",
                client="codex-desktop",
                source="mcp",
                tags=["admin-ui", "test"],
                links=[{"to_entry_id": old.id, "relation": "corrects"}],
            )
        )
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert "codex" in response.text
        assert "codex-desktop" in response.text
        assert "mcp" in response.text
        assert "admin-ui" in response.text
        assert "test" in response.text
        assert "corrects" in response.text

    def test_dashboard_renders_literal_backslash_n_as_line_break(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="first line\\nsecond line\\n\\nthird line",
            )
        )
        _login(admin_client)

        response = admin_client.get("/")

        assert response.status_code == 200
        assert "first line" in response.text
        assert "second line" in response.text
        assert "third line" in response.text
        assert "\\nsecond line" not in response.text
        assert "\\n\\nthird line" not in response.text

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

    def test_project_view_renders_lifecycle_and_assist_panel(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.get("/projects/bramble?assist=bugfix")

        assert response.status_code == 200
        assert "Projekt-Lifecycle" in response.text
        assert "Lifecycle: active" in response.text
        assert "Korrektur-Assistent" in response.text
        assert "Append-only Journal-Eintrag erstellen." in response.text
        assert "Bugfix erstellen" in response.text
        assert "Korrigierte Eintrags-ID" in response.text
        assert "Bugfix eintragen" in response.text

    def test_project_view_prefills_assist_target_from_entry_action(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        entry = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="target entry",
            )
        )
        _login(admin_client)

        response = admin_client.get(f"/projects/bramble?assist=bugfix&entry_id={entry.id}")

        assert response.status_code == 200
        assert f'value="{entry.id}"' in response.text

    def test_project_assist_creates_append_only_note(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        _login(admin_client)

        response = admin_client.post(
            "/projects/bramble/entries",
            data={
                "csrf_token": _csrf(admin_client),
                "assist": "notiz",
                "title": "Nachtrag mit Umlaut",
                "phase": "Phase 4e",
                "content": "Prüfung abgeschlossen.",
                "tags": "docs, admin-ui",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        created = db.read("bramble", n=1)[0]
        assert created.status is JournalStatus.NOTIZ
        assert created.title == "Nachtrag mit Umlaut"
        assert created.phase == "Phase 4e"
        assert created.content == "Prüfung abgeschlossen."
        assert created.tags == ("admin-ui", "docs")
        assert created.actor == "admin"
        assert created.client == "admin-ui"
        assert created.source == "admin-ui"

        event = admin_client.app.state.admin.audit_log.read_recent()[0]
        assert event.action == "journal.append"
        assert event.result == "success"
        assert event.details["entry_id"] == created.id

    def test_project_assist_creates_linked_bugfix(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        old = db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                content="old work item",
            )
        )
        _login(admin_client)

        response = admin_client.post(
            "/projects/bramble/entries",
            data={
                "csrf_token": _csrf(admin_client),
                "assist": "bugfix",
                "title": "Bugfix Admin-UI",
                "content": "Korrektur eingetragen.",
                "tags": "bugfix",
                "link_entry_id": str(old.id),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        created = db.read("bramble", n=1)[0]
        assert created.status is JournalStatus.BUGFIX
        assert created.links[0].entry_id == old.id
        assert created.links[0].relation.value == "corrects"

    def test_project_assist_rejects_empty_content(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        _login(admin_client)
        before = len(db.read("bramble", n=20))

        response = admin_client.post(
            "/projects/bramble/entries",
            data={
                "csrf_token": _csrf(admin_client),
                "assist": "notiz",
                "title": "Unvollständig",
                "content": "   ",
            },
        )

        assert response.status_code == 400
        assert "Inhalt darf nicht leer sein." in response.text
        assert "Unvollständig" in response.text
        assert len(db.read("bramble", n=20)) == before

    def test_project_status_update_changes_registry_status(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)
        csrf = _csrf(admin_client)

        response = admin_client.post(
            "/projects/bramble/status",
            data={"csrf_token": csrf, "status": "paused"},
            follow_redirects=False,
        )

        assert response.status_code == 303

        refreshed = admin_client.get("/projects/bramble")

        assert refreshed.status_code == 200
        assert "Lifecycle: paused" in refreshed.text

    def test_project_status_update_rejects_invalid_status(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)
        csrf = _csrf(admin_client)

        response = admin_client.post(
            "/projects/bramble/status",
            data={"csrf_token": csrf, "status": "broken"},
        )

        assert response.status_code == 400
        assert "not allowed" in response.text

    def test_global_search_finds_cross_project_hits(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                title="Deploy note",
                content="sharedneedle deployment bramble",
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                content="sharedneedle deployment elder",
            )
        )
        _login(admin_client)

        response = admin_client.get("/search?q=sharedneedle")

        assert response.status_code == 200
        assert "Globale Suche" in response.text
        assert "sharedneedle deployment bramble" in response.text
        assert "sharedneedle deployment elder" in response.text

    def test_global_search_applies_status_and_since_filters(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        now = datetime.now(tz=UTC)
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="filteredterm recent bugfix",
                timestamp=now - timedelta(hours=2),
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                content="filteredterm old bugfix",
                timestamp=now - timedelta(days=40),
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.NOTIZ,
                content="filteredterm recent notiz",
                timestamp=now - timedelta(hours=1),
            )
        )
        _login(admin_client)

        response = admin_client.get(
            "/search?q=filteredterm&status=bugfix&since=7d"
        )

        assert response.status_code == 200
        assert "filteredterm recent bugfix" in response.text
        assert "filteredterm old bugfix" not in response.text
        assert "filteredterm recent notiz" not in response.text

    def test_global_search_rejects_invalid_filter_values(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.get("/search?q=anything&status=bad&since=7d")

        assert response.status_code == 200
        assert "Ungültiger Filterwert." in response.text

    def test_global_search_supports_project_and_tag_filters(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="project tag needle alpha",
                tags=["deployment", "admin-ui"],
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.BUGFIX,
                content="project tag needle beta",
                tags=["deployment"],
            )
        )
        _login(admin_client)

        response = admin_client.get(
            "/search?q=project%20tag%20needle&project=bramble&tags=deployment,admin-ui"
        )

        assert response.status_code == 200
        assert "project tag needle alpha" in response.text
        assert "project tag needle beta" not in response.text

    def test_project_view_renders_context_panel(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.IN_ARBEIT,
                title="Open item",
                content="project context open task",
            )
        )
        db.append(
            JournalEntry(
                project="bramble",
                status=JournalStatus.BUGFIX,
                content="project context bugfix",
            )
        )
        _login(admin_client)

        response = admin_client.get("/projects/bramble")

        assert response.status_code == 200
        assert "Offene Punkte" in response.text
        assert "project context open task" in response.text
        assert "Letzte Bugfixes" in response.text
        assert "Workflow für Eintragsabschluss" in response.text
        assert "Append-only Journal-Eintrag geschrieben" in response.text

    def test_project_open_metric_uses_total_not_preview_limit(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
        for i in range(7):
            db.append(
                JournalEntry(
                    project="berry-gym",
                    status=JournalStatus.IN_ARBEIT,
                    content=f"project open total task {i}",
                    timestamp=now + timedelta(minutes=i),
                )
            )
        _login(admin_client)

        response = admin_client.get("/projects/berry-gym")

        assert response.status_code == 200
        assert re.search(
            r"Offene Punkte</span>\s*<strong>7</strong>",
            response.text,
        )

    def test_project_view_hides_effectively_closed_open_items(
        self, admin_client: TestClient, db: JournalDB
    ) -> None:
        open_item = db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.IN_ARBEIT,
                content="project detail open task",
            )
        )
        db.append(
            JournalEntry(
                project="elder-berry",
                status=JournalStatus.NOTIZ,
                content=f"Open-Items-Abgleich\n- #{open_item.id} -> #999",
            )
        )
        _login(admin_client)

        response = admin_client.get("/projects/elder-berry")

        assert response.status_code == 200
        assert "Keine offenen Punkte." in response.text

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

    def test_logout_requires_csrf(self, admin_client: TestClient) -> None:
        _login(admin_client)

        response = admin_client.post("/logout", follow_redirects=False)

        assert response.status_code == 403

    def test_token_page_lists_status_without_secret(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.get("/tokens")

        assert response.status_code == 200
        assert "Token vorhanden" in response.text
        assert "bramble" in response.text
        assert "elder-berry" in response.text
        assert "tok-bramble" not in response.text

    def test_token_project_without_entries_is_visible(
        self, tmp_path: Path
    ) -> None:
        db = JournalDB(tmp_path / "empty-token-project.db")
        db.initialize()
        tokens_file = tmp_path / "secrets" / "tokens.json"
        write_token_map(tokens_file, {"berry-gym": "tok-berry"})
        app = create_admin_app(
            db,
            _FakeAuthenticator(),  # type: ignore[arg-type]
            config=AdminConfig(
                db_path=db.db_path,
                tokens_file=tokens_file,
                allowed_hosts=("testserver",),
            ),
            sessions=SessionStore(
                idle_seconds=1800,
                absolute_seconds=28800,
                token_factory=lambda: "token-project-session",
                csrf_token_factory=lambda: "test-csrf",
            ),
            token_store=TokenStore(tokens_file),
        )
        client = TestClient(app)
        _login(client)

        dashboard = client.get("/")
        detail = client.get("/projects/berry-gym")

        assert dashboard.status_code == 200
        assert "berry-gym" in dashboard.text
        assert detail.status_code == 200
        assert "0 Einträge" in detail.text
        assert "Noch keine Journal-Einträge." in detail.text

    def test_token_create_requires_csrf(self, admin_client: TestClient) -> None:
        _login(admin_client)

        response = admin_client.post("/tokens", data={"project": "berry-gym"})

        assert response.status_code == 403
        assert json.loads(_tokens_file(admin_client).read_text(encoding="utf-8")) == {
            "bramble": "tok-bramble"
        }

    def test_token_create_writes_file_and_audit(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.post(
            "/tokens",
            data={"project": "berry-gym", "csrf_token": _csrf(admin_client)},
        )

        assert response.status_code == 200
        assert "generated-token" in response.text
        assert "sudo systemctl restart bramble" in response.text
        assert json.loads(_tokens_file(admin_client).read_text(encoding="utf-8")) == {
            "berry-gym": "generated-token",
            "bramble": "tok-bramble",
        }
        event = admin_client.app.state.admin.audit_log.read_recent()[0]
        assert event.action == "token.create"
        assert event.target == "berry-gym"
        assert event.result == "success"
        assert "generated-token" not in str(event.details)
        assert "0 Einträge" in response.text

    def test_token_rotate_replaces_only_target(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.post(
            "/tokens/bramble/rotate",
            data={"csrf_token": _csrf(admin_client)},
        )

        assert response.status_code == 200
        assert "generated-token" in response.text
        assert json.loads(_tokens_file(admin_client).read_text(encoding="utf-8")) == {
            "bramble": "generated-token"
        }
        event = admin_client.app.state.admin.audit_log.read_recent()[0]
        assert event.action == "token.rotate"
        assert event.target == "bramble"

    def test_token_revoke_removes_only_target(
        self, admin_client: TestClient
    ) -> None:
        _login(admin_client)

        response = admin_client.post(
            "/tokens/bramble/revoke",
            data={"csrf_token": _csrf(admin_client)},
        )

        assert response.status_code == 200
        assert json.loads(_tokens_file(admin_client).read_text(encoding="utf-8")) == {}
        event = admin_client.app.state.admin.audit_log.read_recent()[0]
        assert event.action == "token.revoke"
        assert event.target == "bramble"


def test_format_display_datetime_uses_dst_rules() -> None:
    berlin = get_display_timezone("Europe/Berlin")

    summer = format_display_datetime(
        datetime(2026, 5, 28, 20, 41, 44, tzinfo=UTC),
        berlin,
    )
    winter = format_display_datetime(
        datetime(2026, 1, 28, 20, 41, 44, tzinfo=UTC),
        berlin,
    )

    assert summer == "2026-05-28 22:41 CEST"
    assert winter == "2026-01-28 21:41 CET"
