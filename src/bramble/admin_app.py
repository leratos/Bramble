"""Starlette application factory for the Bramble read-only admin UI."""

from __future__ import annotations

import re
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

from bramble.admin_auth import (
    SESSION_COOKIE_NAME,
    AdminAuthenticator,
    AdminSession,
    LoginRateLimiter,
    SessionStore,
)
from bramble.admin_config import AdminConfig
from bramble.admin_read_model import AdminReadModel
from bramble.journal_db import JournalDB
from bramble.project_summary import ProjectSummary

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
    config: AdminConfig
    authenticator: AdminAuthenticator
    sessions: SessionStore
    login_limiter: LoginRateLimiter
    read_model: AdminReadModel
    templates: Jinja2Templates


def create_admin_app(
    db: JournalDB,
    authenticator: AdminAuthenticator,
    *,
    config: AdminConfig | None = None,
    sessions: SessionStore | None = None,
    login_limiter: LoginRateLimiter | None = None,
    read_model: AdminReadModel | None = None,
    templates_dir: Path | str | None = None,
    static_dir: Path | str | None = None,
) -> Starlette:
    """Build the read-only Starlette app for ``bramble-admin``."""

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
    templates = Jinja2Templates(directory=str(templates_dir))

    routes = [
        Route("/", dashboard, methods=["GET"], name="dashboard"),
        Route("/login", login_form, methods=["GET"], name="login"),
        Route("/login", login_submit, methods=["POST"], name="login_submit"),
        Route("/logout", logout, methods=["POST"], name="logout"),
        Route("/projects", projects_index, methods=["GET"], name="projects"),
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
        config=config,
        authenticator=authenticator,
        sessions=sessions,
        login_limiter=login_limiter,
        read_model=read_model,
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
    ctx.sessions.destroy(request.cookies.get(SESSION_COOKIE_NAME))
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


async def dashboard(request: Request) -> Response:
    session = _current_session(request)
    if session is None:
        return _login_redirect(request)
    ctx = _ctx(request)
    projects = ctx.read_model.projects()
    stats = ctx.read_model.dashboard_stats()
    return _render(
        request,
        "dashboard.html",
        {
            "actor": session.actor,
            "projects": _project_rows(projects),
            "active_project": None,
            "stats": stats,
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

    return _render(
        request,
        "project.html",
        {
            "actor": session.actor,
            "projects": _project_rows(projects),
            "active_project": project,
            "summary": summary,
            "entries": entries,
            "query": query,
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
