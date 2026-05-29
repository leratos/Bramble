"""Starlette application factory for the Bramble admin UI."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from bramble.admin_audit import AdminAuditLog
from bramble.admin_auth import (
    SESSION_COOKIE_NAME,
    AdminAuthenticator,
    AdminSession,
    LoginRateLimiter,
    SessionStore,
)
from bramble.admin_config import AdminConfig
from bramble.admin_read_model import AdminReadModel
from bramble.admin_time import format_display_datetime, get_display_timezone
from bramble.journal_db import JournalDB
from bramble.project_summary import ProjectSummary
from bramble.token_store import TokenMutation, TokenStore

_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MAX_FORM_BYTES = 8 * 1024
_MAX_SEARCH_CHARS = 200


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach conservative security headers to every admin response."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "base-uri 'none'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "img-src 'self' data:; "
                "script-src 'none'; "
                "style-src 'self'"
            ),
        )
        return response


@dataclass(frozen=True, slots=True)
class _AdminContext:
    db: JournalDB
    config: AdminConfig
    authenticator: AdminAuthenticator
    sessions: SessionStore
    login_limiter: LoginRateLimiter
    read_model: AdminReadModel
    token_store: TokenStore
    audit_log: AdminAuditLog
    templates: Jinja2Templates


def create_admin_app(
    db: JournalDB,
    authenticator: AdminAuthenticator,
    *,
    config: AdminConfig | None = None,
    sessions: SessionStore | None = None,
    login_limiter: LoginRateLimiter | None = None,
    read_model: AdminReadModel | None = None,
    token_store: TokenStore | None = None,
    audit_log: AdminAuditLog | None = None,
    templates_dir: Path | str | None = None,
    static_dir: Path | str | None = None,
) -> Starlette:
    """Build the Starlette app for ``bramble-admin``."""

    if not isinstance(db, JournalDB):
        raise TypeError("db must be a JournalDB")
    if config is None:
        config = AdminConfig(db_path=db.db_path)
    if templates_dir is None:
        templates_dir = Path(__file__).parent / "templates" / "admin"
    if static_dir is None:
        static_dir = Path(__file__).parent / "static" / "admin"

    sessions = sessions or SessionStore(
        idle_seconds=config.session_idle_seconds,
        absolute_seconds=config.session_absolute_seconds,
    )
    login_limiter = login_limiter or LoginRateLimiter(
        max_attempts=config.login_max_attempts,
        window_seconds=config.login_window_seconds,
    )
    read_model = read_model or AdminReadModel(db)
    token_store = token_store or TokenStore(config.tokens_file)
    _sync_token_projects(db, token_store)
    audit_log = audit_log or AdminAuditLog(db)
    audit_log.initialize()
    templates = Jinja2Templates(directory=str(templates_dir))
    display_tz = get_display_timezone(config.display_timezone)
    templates.env.filters["admin_datetime"] = (
        lambda value: format_display_datetime(value, display_tz)
    )

    routes = [
        Route("/", dashboard, methods=["GET"], name="dashboard"),
        Route("/login", login_form, methods=["GET"], name="login"),
        Route("/login", login_submit, methods=["POST"], name="login_submit"),
        Route("/logout", logout, methods=["POST"], name="logout"),
        Route("/projects", projects_index, methods=["GET"], name="projects"),
        Route("/search", global_search, methods=["GET"], name="global_search"),
        Route("/tokens", tokens_index, methods=["GET"], name="tokens"),
        Route("/tokens", token_create, methods=["POST"], name="token_create"),
        Route(
            "/tokens/{project:str}/rotate",
            token_rotate,
            methods=["POST"],
            name="token_rotate",
        ),
        Route(
            "/tokens/{project:str}/revoke",
            token_revoke,
            methods=["POST"],
            name="token_revoke",
        ),
        Route(
            "/projects/{project:str}",
            project_detail,
            methods=["GET"],
            name="project_detail",
        ),
        Mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        ),
    ]
    app = Starlette(debug=False, routes=routes)
    app.state.admin = _AdminContext(
        db=db,
        config=config,
        authenticator=authenticator,
        sessions=sessions,
        login_limiter=login_limiter,
        read_model=read_model,
        token_store=token_store,
        audit_log=audit_log,
        templates=templates,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(config.allowed_hosts),
    )
    app.add_middleware(SecurityHeadersMiddleware)

    return app


async def login_form(request: Request) -> Response:
    if _current_session(request) is not None:
        return RedirectResponse(
            url=_safe_next(request.query_params.get("next")),
            status_code=303,
        )
    return _render(
        request,
        "login.html",
        {
            "next_url": _safe_next(request.query_params.get("next")),
            "error": None,
            "actor": None,
        },
        status_code=200,
    )


async def login_submit(request: Request) -> Response:
    ctx = _ctx(request)
    client_ip = _client_ip(request)
    if not ctx.login_limiter.allow(client_ip):
        return _render(
            request,
            "login.html",
            {
                "next_url": _safe_next(request.query_params.get("next")),
                "error": "Zu viele Login-Versuche. Bitte spaeter erneut versuchen.",
                "actor": None,
            },
            status_code=429,
        )

    form = await _read_urlencoded_form(request)
    username = form.get("username", "")
    password = form.get("password", "")
    if ctx.authenticator.verify(username, password):
        ctx.login_limiter.record_success(client_ip)
        session_id = ctx.sessions.create(ctx.authenticator.username)
        response = RedirectResponse(
            url=_safe_next(form.get("next") or request.query_params.get("next")),
            status_code=303,
        )
        _set_session_cookie(response, session_id, ctx.config)
        return response

    ctx.login_limiter.record_failure(client_ip)
    return _render(
        request,
        "login.html",
        {
            "next_url": _safe_next(form.get("next") or request.query_params.get("next")),
            "error": "Login fehlgeschlagen.",
            "actor": None,
        },
        status_code=401,
    )


async def logout(request: Request) -> Response:
    ctx = _ctx(request)
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    form = await _read_urlencoded_form(request)
    if not _csrf_is_valid(session, form):
        _audit(
            request,
            session,
            action="csrf.denied",
            target_type="session",
            target=None,
            result="denied",
        )
        return PlainTextResponse("forbidden", status_code=403)
    ctx.sessions.destroy(request.cookies.get(SESSION_COOKIE_NAME))
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


async def tokens_index(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    return _render_tokens(request, session)


async def token_create(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    form = await _read_urlencoded_form(request)
    if not _csrf_is_valid(session, form):
        _audit(
            request,
            session,
            action="csrf.denied",
            target_type="token",
            target=form.get("project"),
            result="denied",
        )
        return PlainTextResponse("forbidden", status_code=403)

    project = form.get("project", "")
    try:
        _ctx(request).db.register_project(project)
        mutation = _ctx(request).token_store.create(project)
    except (OSError, TypeError, ValueError) as exc:
        _audit(
            request,
            session,
            action="token.create",
            target_type="token",
            target=project.strip() or None,
            result="denied",
            details={"reason": str(exc)},
        )
        return _render_tokens(request, session, error=str(exc), status_code=400)

    _audit_token_mutation(request, session, "token.create", mutation)
    return _render_tokens(request, session, mutation=mutation)


async def token_rotate(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    project = request.path_params["project"]
    if not _PROJECT_RE.fullmatch(project):
        return PlainTextResponse("unknown project", status_code=404)

    form = await _read_urlencoded_form(request)
    if not _csrf_is_valid(session, form):
        _audit(
            request,
            session,
            action="csrf.denied",
            target_type="token",
            target=project,
            result="denied",
        )
        return PlainTextResponse("forbidden", status_code=403)

    try:
        mutation = _ctx(request).token_store.rotate(project)
        _ctx(request).db.register_project(project)
    except (OSError, ValueError) as exc:
        _audit(
            request,
            session,
            action="token.rotate",
            target_type="token",
            target=project,
            result="denied",
            details={"reason": str(exc)},
        )
        return _render_tokens(request, session, error=str(exc), status_code=400)

    _audit_token_mutation(request, session, "token.rotate", mutation)
    return _render_tokens(request, session, mutation=mutation)


async def token_revoke(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    project = request.path_params["project"]
    if not _PROJECT_RE.fullmatch(project):
        return PlainTextResponse("unknown project", status_code=404)

    form = await _read_urlencoded_form(request)
    if not _csrf_is_valid(session, form):
        _audit(
            request,
            session,
            action="csrf.denied",
            target_type="token",
            target=project,
            result="denied",
        )
        return PlainTextResponse("forbidden", status_code=403)

    try:
        mutation = _ctx(request).token_store.revoke(project)
    except (OSError, ValueError) as exc:
        _audit(
            request,
            session,
            action="token.revoke",
            target_type="token",
            target=project,
            result="denied",
            details={"reason": str(exc)},
        )
        return _render_tokens(request, session, error=str(exc), status_code=400)

    _audit_token_mutation(request, session, "token.revoke", mutation)
    return _render_tokens(request, session, mutation=mutation)


async def dashboard(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    ctx = _ctx(request)
    projects = ctx.read_model.projects()
    stats = ctx.read_model.dashboard_stats()
    workflow = ctx.read_model.workflow_guidance()
    return _render(
        request,
        "dashboard.html",
        {
            "actor": session.actor,
            "projects": _project_rows(projects),
            "active_project": None,
            "csrf_token": session.csrf_token,
            "stats": stats,
            "workflow": workflow,
        },
    )


async def projects_index(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    projects = _ctx(request).read_model.projects()
    if projects:
        return RedirectResponse(
            url=f"/projects/{projects[0].name}",
            status_code=303,
        )
    return RedirectResponse(url="/", status_code=303)


async def project_detail(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)

    project = request.path_params["project"]
    if not _PROJECT_RE.fullmatch(project):
        return PlainTextResponse("unknown project", status_code=404)

    ctx = _ctx(request)
    projects = ctx.read_model.projects()
    summaries = {summary.name: summary for summary in projects}
    summary = summaries.get(project)
    if summary is None:
        return PlainTextResponse("unknown project", status_code=404)

    query = request.query_params.get("q", "").strip()
    search_error = None
    if query:
        if len(query) > _MAX_SEARCH_CHARS:
            entries = []
            search_error = "Die Suche ist zu lang."
        else:
            entries = ctx.read_model.search_project(project, query=query)
    else:
        entries = ctx.read_model.project_entries(project)
    project_context = ctx.read_model.project_context(project)
    workflow = ctx.read_model.workflow_guidance()

    return _render(
        request,
        "project.html",
        {
            "actor": session.actor,
            "projects": _project_rows(projects),
            "active_project": project,
            "csrf_token": session.csrf_token,
            "summary": summary,
            "project_context": project_context,
            "entries": entries,
            "query": query,
            "search_error": search_error,
            "workflow": workflow,
        },
    )


async def global_search(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)

    ctx = _ctx(request)
    projects = ctx.read_model.projects()
    query = request.query_params.get("q", "").strip()
    status_filter = request.query_params.get("status", "all").strip() or "all"
    since_filter = request.query_params.get("since", "30d").strip() or "30d"

    entries = []
    search_error: str | None = None
    if query:
        if len(query) > _MAX_SEARCH_CHARS:
            search_error = "Die Suche ist zu lang."
        else:
            try:
                entries = ctx.read_model.search_global(
                    query,
                    status=status_filter,
                    since=since_filter,
                    limit=80,
                )
            except ValueError:
                search_error = "Ungueltiger Filterwert."

    return _render(
        request,
        "search.html",
        {
            "actor": session.actor,
            "projects": _project_rows(projects),
            "active_project": None,
            "csrf_token": session.csrf_token,
            "entries": entries,
            "query": query,
            "status_filter": status_filter,
            "since_filter": since_filter,
            "search_error": search_error,
        },
    )


def _ctx(request: Request) -> _AdminContext:
    return request.app.state.admin


def _current_session(request: Request) -> AdminSession | None:
    ctx = _ctx(request)
    return ctx.sessions.get(request.cookies.get(SESSION_COOKIE_NAME))


def _login_redirect(request: Request) -> RedirectResponse:
    next_url = _safe_next(str(request.url.path))
    if request.url.query:
        next_url = _safe_next(f"{next_url}?{request.url.query}")
    return RedirectResponse(
        url=f"/login?next={quote(next_url, safe='/')}",
        status_code=303,
    )


def _render_tokens(
    request: Request,
    session: AdminSession,
    *,
    mutation: TokenMutation | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    ctx = _ctx(request)
    projects = ctx.read_model.projects()
    token_status_code = status_code
    try:
        token_summaries = ctx.token_store.list_tokens()
    except ValueError as exc:
        token_summaries = []
        error = error or f"Token-Datei konnte nicht gelesen werden: {exc}"
        token_status_code = max(status_code, 500)
    token_projects = {summary.project for summary in token_summaries}
    journal_projects = {project.name for project in projects}
    missing_token_projects = sorted(journal_projects - token_projects)
    return _render(
        request,
        "tokens.html",
        {
            "actor": session.actor,
            "csrf_token": session.csrf_token,
            "projects": _project_rows(projects),
            "active_project": None,
            "token_summaries": token_summaries,
            "missing_token_projects": missing_token_projects,
            "mutation": mutation,
            "error": error,
            "restart_command": "sudo systemctl restart bramble",
        },
        status_code=token_status_code,
    )


def _sync_token_projects(db: JournalDB, token_store: TokenStore) -> None:
    try:
        token_projects = [summary.project for summary in token_store.list_tokens()]
    except (OSError, ValueError):
        return
    db.register_projects(token_projects)


def _render(
    request: Request,
    template_name: str,
    context: dict[str, object],
    *,
    status_code: int = 200,
) -> Response:
    return _ctx(request).templates.TemplateResponse(
        request,
        template_name,
        context,
        status_code=status_code,
    )


def _csrf_is_valid(session: AdminSession, form: dict[str, str]) -> bool:
    token = form.get("csrf_token", "")
    return secrets.compare_digest(token, session.csrf_token)


def _audit_token_mutation(
    request: Request,
    session: AdminSession,
    action: str,
    mutation: TokenMutation,
) -> None:
    _audit(
        request,
        session,
        action=action,
        target_type="token",
        target=mutation.project,
        result="success",
        details={"mutation": mutation.action},
    )


def _audit(
    request: Request,
    session: AdminSession,
    *,
    action: str,
    target_type: str,
    target: str | None,
    result: str,
    details: dict[str, object] | None = None,
) -> None:
    _ctx(request).audit_log.append(
        actor=session.actor,
        action=action,
        target_type=target_type,
        target=target,
        result=result,
        client_ip=_client_ip(request),
        details=details,
    )


async def _read_urlencoded_form(request: Request) -> dict[str, str]:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > _MAX_FORM_BYTES:
                return {}
        except ValueError:
            return {}
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        return {}
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    parsed = parse_qs(decoded, keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _client_ip(request: Request) -> str:
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\r" in value or "\n" in value:
        return "/"
    return value


def _set_session_cookie(
    response: Response,
    session_id: str,
    config: AdminConfig,
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=config.session_idle_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="strict",
        path="/",
    )


def _project_rows(projects: list[ProjectSummary]) -> list[dict[str, object]]:
    return [
        {
            "name": project.name,
            "entry_count": project.entry_count,
            "last_timestamp": project.last_timestamp,
        }
        for project in projects
    ]
