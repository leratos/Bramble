"""Bramble's MCP server façade.

The :class:`JournalMCPServer` owns the FastMCP instance and the tool
registrations. It deliberately keeps the FastMCP setup and the
:class:`JournalDB` accessor separate: ``app`` is the thing FastMCP
needs, ``db`` is the thing tools talk to. Tests construct the server
with an in-memory or temp-file ``JournalDB`` and connect via the
FastMCP in-process ``Client``.

The ``auth_validator`` and ``rate_limiter`` constructor arguments are
consumed by :class:`_AuthRateLimitMiddleware`, which FastMCP runs
before every tool call. They are supplied together for the
authenticated ``http`` transport and left unset for local ``stdio``
use, where no token gate applies.

Tool registration happens once in :meth:`__init__` via
:meth:`_register_tools`, which is the file's central index of which
MCP tools exist.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from bramble.agent_guide import AGENT_GUIDE, AGENT_GUIDE_VERSION
from bramble.auth_validator import AuthValidator
from bramble.journal_db import JournalDB
from bramble.journal_context import JournalContext
from bramble.journal_digest import JournalDigest
from bramble.journal_entry import JournalEntry, JournalStatus
from bramble.mcp_errors import translate_errors
from bramble.open_item import OpenItemView
from bramble.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Set by :class:`_AuthRateLimitMiddleware` to the project a request's
# bearer token belongs to, so ``journal_append`` can enforce the
# write-scope binding (Phase-3 Decision B). It stays ``None`` for the
# stdio transport and for any server built without an auth validator,
# which is exactly when no scope binding should apply.
_token_project: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bramble_token_project", default=None
)

# Peers from which an ``X-Forwarded-For`` header may be trusted. Plesk's
# Nginx terminates TLS on the same host and proxies to loopback, so a
# loopback peer is our own proxy. Any other peer could forge the header.
_TRUSTED_PROXY_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})


# ---------------------------------------------------------------------------
# Validation helpers (Decision E: kebab-case enforced in the MCP layer)
# ---------------------------------------------------------------------------
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _require_kebab_case(project: str) -> None:
    """Reject any project identifier that is not strict kebab-case.

    The pattern matches lowercase letters, digits and hyphens with a
    non-hyphen first character. Whitespace, underscores, mixed case
    and empty strings all fail. The check lives in the MCP layer only
    – :class:`JournalDB` itself remains project-agnostic.
    """

    if not isinstance(project, str):
        raise TypeError("project must be a string")
    if not _KEBAB_CASE_RE.match(project):
        raise ValueError(
            f"project {project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$"
        )


def _require_kebab_case_filters(projects: list[str] | None) -> None:
    if projects is None:
        return
    if isinstance(projects, (str, bytes)):
        raise TypeError("projects must be an iterable of project strings")
    for project in projects:
        _require_kebab_case(project)


def _entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    """Serialise a :class:`JournalEntry` to a plain MCP-friendly dict."""

    return {
        "id": entry.id,
        "project": entry.project,
        "timestamp": entry.timestamp_iso(),
        "status": entry.status.value,
        "phase": entry.phase,
        "title": entry.title,
        "content": entry.content,
        "actor": entry.actor,
        "client": entry.client,
        "source": entry.source,
        "tags": list(entry.tags),
        "links": [
            {"to_entry_id": link.entry_id, "relation": link.relation.value}
            for link in entry.links
        ],
        "backlinks": [
            {"from_entry_id": link.entry_id, "relation": link.relation.value}
            for link in entry.backlinks
        ],
    }


def _open_item_to_dict(view: OpenItemView) -> dict[str, Any]:
    """Serialise an :class:`OpenItemView` to an MCP-friendly dict.

    The underlying entry fields are kept at the top level (backward
    compatible with the pre-Phase-4f entry shape) and augmented with the
    open-item lifecycle annotations.
    """

    data = _entry_to_dict(view.entry)
    data["open_state"] = view.state
    data["resolution_reason"] = view.resolution_reason
    data["resolved_by_id"] = view.resolved_by_id
    data["age_days"] = view.age_days
    return data


def _digest_to_dict(digest: JournalDigest) -> dict[str, Any]:
    return {
        "range": {
            "since": digest.range_since.isoformat(),
            "until": digest.range_until.isoformat(),
        },
        "projects": list(digest.projects),
        "counts_by_project": dict(digest.counts_by_project),
        "counts_by_status": dict(digest.counts_by_status),
        "entries": [_entry_to_dict(entry) for entry in digest.entries],
        "open_items": [_entry_to_dict(entry) for entry in digest.open_items],
        "bugfixes": [_entry_to_dict(entry) for entry in digest.bugfixes],
        "decisions": [_entry_to_dict(entry) for entry in digest.decisions],
    }


def _context_to_dict(context: JournalContext) -> dict[str, Any]:
    return {
        "project": context.project,
        "recent": [_entry_to_dict(entry) for entry in context.recent],
        "open_items": [_open_item_to_dict(view) for view in context.open_items],
        "recent_bugfixes": [
            _entry_to_dict(entry) for entry in context.recent_bugfixes
        ],
        "recent_decisions": [
            _entry_to_dict(entry) for entry in context.recent_decisions
        ],
        "related_projects": list(context.related_projects),
        "suggested_searches": list(context.suggested_searches),
    }


def _mcp_source(source: str | None) -> str:
    if source is None or not source.strip():
        return "mcp"
    return source


def _enforce_project_scope(project: str) -> None:
    """Reject a write whose project does not match the bearer token.

    :class:`_AuthRateLimitMiddleware` stashes the token's project in
    :data:`_token_project` before the tool runs. When that is unset –
    stdio transport, or a server built without an auth validator – no
    binding applies and any project may be written (Decision B).
    """

    token_project = _token_project.get()
    if token_project is not None and project != token_project:
        raise ValueError(
            f"token is scoped to project {token_project!r}; "
            f"it cannot append to {project!r}"
        )


# ---------------------------------------------------------------------------
# Request-credential extraction (Phase-3 auth)
# ---------------------------------------------------------------------------
def _bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    Returns ``None`` for a missing header, a non-Bearer scheme, or an
    empty token – every one of which is an authentication failure.
    """

    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _resolve_client_ip(request: Any) -> str:
    """Return the real client IP for ``request``.

    ``X-Forwarded-For`` is trusted only when the immediate peer is a
    local proxy (:data:`_TRUSTED_PROXY_HOSTS`); a request from any
    other peer uses its peer address verbatim. This is the
    ``X-Forwarded-For`` spoofing mitigation from the Phase-3 risk list:
    without it an attacker could forge the header and dodge Fail2Ban.
    """

    peer = request.client.host if request.client else "unknown"
    if peer in _TRUSTED_PROXY_HOSTS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Plesk-Nginx appends, so the left-most entry is the
            # originating client.
            original = forwarded.split(",")[0].strip()
            if original:
                return original
    return peer


def _extract_credentials() -> tuple[str | None, str]:
    """Pull the bearer token and client IP out of the current request.

    The middleware is mounted only for the ``http`` transport, so an
    HTTP request is normally present. If it is missing the request
    cannot be authenticated, so this returns no token and the caller
    fails closed.
    """

    try:
        request = get_http_request()
    except RuntimeError:
        return None, "unknown"
    headers = get_http_headers(include={"authorization"})
    return _bearer_token(headers.get("authorization")), _resolve_client_ip(request)


class _AuthRateLimitMiddleware(Middleware):
    """FastMCP middleware enforcing bearer-token auth and rate limits.

    Runs before every tool call on the ``http`` transport. This class
    is only the wiring that bridges FastMCP's hook to Bramble's own
    :class:`~bramble.auth_validator.AuthValidator` and
    :class:`~bramble.rate_limiter.RateLimiter`; the auth and
    rate-limit logic itself lives in those two classes.
    """

    def __init__(
        self, auth_validator: AuthValidator, rate_limiter: RateLimiter
    ) -> None:
        self._auth_validator = auth_validator
        self._rate_limiter = rate_limiter

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        token, client_ip = _extract_credentials()
        project = self._authorize(token=token, client_ip=client_ip)
        # Hand the resolved project to journal_append's scope check.
        reset = _token_project.set(project)
        try:
            return await call_next(context)
        finally:
            _token_project.reset(reset)

    def _authorize(self, *, token: str | None, client_ip: str) -> str:
        """Decide a single request: return the token's project or raise.

        Kept free of FastMCP types – it takes the already-extracted
        token and client IP – so it is unit-testable in-process. The
        order matters: the per-IP limit is a backstop that applies
        before the token is known, then auth, then the per-token limit.
        """

        if not self._rate_limiter.allow_ip(client_ip):
            raise ToolError("rate limit exceeded; slow down")
        project = self._auth_validator.authenticate(token, client_ip=client_ip)
        if project is None:
            raise ToolError(
                "authentication required: missing or invalid bearer token"
            )
        if not self._rate_limiter.allow_project(project):
            raise ToolError("rate limit exceeded; slow down")
        return project


class JournalMCPServer:
    """MCP-facing server that exposes :class:`JournalDB` operations.

    Parameters
    ----------
    db:
        The :class:`JournalDB` instance to read from / write to. Must
        already be initialised (``db.initialize()`` was called).
    auth_validator:
        Resolves bearer tokens to projects. Supply together with
        ``rate_limiter`` to gate the ``http`` transport; leave unset
        for ``stdio``. Providing only one of the two raises.
    rate_limiter:
        Token-bucket request limiter. See ``auth_validator``.
    """

    def __init__(
        self,
        db: JournalDB,
        *,
        auth_validator: AuthValidator | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        if not isinstance(db, JournalDB):
            raise TypeError("db must be a JournalDB instance")
        if (auth_validator is None) != (rate_limiter is None):
            raise ValueError(
                "auth_validator and rate_limiter must be provided together: "
                "both for the authenticated http transport, or neither"
            )

        self._db = db
        self._auth_validator = auth_validator
        self._rate_limiter = rate_limiter

        self._app: FastMCP = FastMCP(
            name="bramble",
            instructions=(
                "Shared development journal across projects. "
                "At session start, first call journal_guide for the "
                "canonical, project-agnostic working conventions "
                "(append-only model, statuses, tags, corrections, "
                "open-item/resolves semantics, DoD) and follow them. "
                "Use journal_append to record new entries, journal_read "
                "to fetch recent entries, journal_search or "
                "journal_search_all for full-text search, journal_context "
                "for curated session-start context, journal_digest for "
                "period summaries, journal_open_items for open-task "
                "snapshots, and journal_list_projects for an overview."
            ),
        )
        self._register_tools()
        if auth_validator is not None and rate_limiter is not None:
            self._app.add_middleware(
                _AuthRateLimitMiddleware(auth_validator, rate_limiter)
            )

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------
    @property
    def app(self) -> FastMCP:
        """The underlying FastMCP application.

        Used by tests (passed to :class:`fastmcp.Client` for in-process
        calls) and by :mod:`bramble.__main__` to call ``run()``.
        """

        return self._app

    @property
    def db(self) -> JournalDB:
        """The :class:`JournalDB` instance the tools operate on."""

        return self._db

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------
    def _register_tools(self) -> None:
        """Register all MCP tools on :attr:`app`.

        All tools live in this single method on purpose: the
        ``self`` closure means each tool implicitly carries the
        :class:`JournalDB` it talks to, and keeping the registrations
        together makes the public surface of the server obvious at a
        glance.
        """

        app = self._app
        db = self._db

        @app.tool
        @translate_errors
        async def journal_read(project: str, n: int = 80) -> list[dict[str, Any]]:
            """Return the ``n`` most recent journal entries for ``project``.

            Entries are returned newest first. ``project`` must match
            the kebab-case pattern ``^[a-z0-9][a-z0-9-]*$``.
            """

            _require_kebab_case(project)
            entries = await asyncio.to_thread(db.read, project, n)
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_append(
            project: str,
            status: str,
            content: str,
            phase: str | None = None,
            title: str | None = None,
            tags: list[str] | None = None,
            links: list[dict[str, Any]] | None = None,
            actor: str | None = None,
            client: str | None = None,
            source: str | None = None,
        ) -> dict[str, Any]:
            """Append a new journal entry and return it with its assigned id.

            ``status`` must be one of: ``in_arbeit``, ``abgeschlossen``,
            ``notiz``, ``bugfix``. The timestamp is set server-side
            (``datetime.now(UTC)``); clients cannot override it.

            On the authenticated ``http`` transport the bearer token is
            bound to one project: appending to a different project is
            rejected (Phase-3 Decision B).
            """

            _require_kebab_case(project)
            _enforce_project_scope(project)
            allowed = ", ".join(s.value for s in JournalStatus)
            try:
                status_enum = JournalStatus(status)
            except ValueError as exc:
                raise ValueError(
                    f"status {status!r} is not allowed; must be one of: {allowed}"
                ) from exc

            entry = JournalEntry(
                project=project,
                status=status_enum,
                content=content,
                phase=phase,
                title=title,
                actor=actor,
                client=client,
                source=_mcp_source(source),
                tags=tags or (),
                links=links or (),
            )
            persisted = await asyncio.to_thread(db.append, entry)
            return _entry_to_dict(persisted)

        @app.tool
        @translate_errors
        async def journal_search(
            project: str,
            query: str,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            """Full-text-search ``project``'s entries for ``query``.

            Pass SQLite FTS5 MATCH syntax directly: bare words for AND,
            ``OR`` for alternation, double-quoted strings for phrase
            search, and ``NEAR()`` for proximity. Malformed FTS5
            syntax returns an empty list (not an error) to match the
            Phase-1 ``JournalDB.search`` behaviour.
            """

            _require_kebab_case(project)
            entries = await asyncio.to_thread(db.search, project, query, limit)
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_search_all(
            query: str,
            limit: int = 20,
            projects: list[str] | None = None,
            statuses: list[str] | None = None,
            tags: list[str] | None = None,
        ) -> list[dict[str, Any]]:
            """Full-text-search journal entries across projects.

            Pass SQLite FTS5 MATCH syntax directly. Optional filters
            narrow results by ``projects``, ``statuses`` and ``tags``;
            multiple tags use AND semantics. At most 100 results can be
            requested. Malformed FTS5 syntax returns an empty list.
            """

            _require_kebab_case_filters(projects)
            entries = await asyncio.to_thread(
                db.search_all,
                query,
                limit,
                projects=projects,
                statuses=statuses,
                tags=tags,
            )
            return [_entry_to_dict(e) for e in entries]

        @app.tool
        @translate_errors
        async def journal_digest(
            project: str | None = None,
            since: str = "7d",
            until: str | None = None,
            tags: list[str] | None = None,
            limit: int = 80,
        ) -> dict[str, Any]:
            """Return a structured journal digest for a time range.

            ``since`` accepts ``24h``, ``7d``, ``30d`` or an ISO
            timestamp. ``until`` accepts an ISO timestamp and defaults
            to now. The tool is read-only and returns deterministic
            counts plus capped entry lists.
            """

            if project is not None:
                _require_kebab_case(project)
            digest = await asyncio.to_thread(
                db.digest,
                project=project,
                since=since,
                until=until,
                tags=tags,
                limit=limit,
            )
            return _digest_to_dict(digest)

        @app.tool
        @translate_errors
        async def journal_context(
            project: str,
            n_recent: int = 10,
            include_cross_project: bool = True,
        ) -> dict[str, Any]:
            """Return curated session-start context for one project.

            The output combines project-local recency with deterministic
            slices (open items, bugfixes, decisions) and optional
            cross-project related-project hints. The tool is strictly
            read-only and backward compatible with sparse metadata.
            """

            _require_kebab_case(project)
            context = await asyncio.to_thread(
                db.context,
                project=project,
                n_recent=n_recent,
                include_cross_project=include_cross_project,
            )
            return _context_to_dict(context)

        @app.tool
        @translate_errors
        async def journal_open_items(
            project: str | None = None,
            limit: int = 50,
            include_resolved: bool = False,
            stale_after_days: int = 30,
        ) -> list[dict[str, Any]]:
            """Return newest open work items with resolution + staleness.

            Base set is ``status='in_arbeit'``. The journal is append-only,
            so completion is a *new* entry; this tool infers which started
            items are effectively done instead of reporting every one as
            open. Each item carries ``open_state`` (``open`` | ``stale`` |
            ``resolved``), ``resolution_reason`` (``link`` | ``text`` |
            ``title`` | ``phase`` | ``null``), ``resolved_by_id`` and
            ``age_days``.

            * ``resolved`` items are inferred-closed and hidden unless
              ``include_resolved`` is true; the reason/resolver make the
              suppression auditable.
            * ``stale`` items are unresolved but older than
              ``stale_after_days`` (default 30); they are still returned,
              just flagged.
            * the most reliable close signal is an explicit ``resolves``
              link from the completing entry to the open one.

            Optional ``project`` narrows to one project; otherwise the
            result is cross-project. At most 100 rows can be requested.
            """

            if project is not None:
                _require_kebab_case(project)
            views = await asyncio.to_thread(
                db.open_items_view,
                project=project,
                limit=limit,
                include_resolved=include_resolved,
                stale_after_days=stale_after_days,
            )
            return [_open_item_to_dict(view) for view in views]

        @app.tool
        @translate_errors
        async def journal_list_projects() -> list[dict[str, Any]]:
            """List all projects with entry counts and most recent timestamps.

            Returned in descending order of ``last_timestamp`` (most
            recent activity first), with alphabetical tie-break for
            stability.
            """

            summaries = await asyncio.to_thread(db.project_overview)
            return [
                {
                    "project": s.name,
                    "entry_count": s.entry_count,
                    "last_timestamp": s.last_timestamp_iso(),
                }
                for s in summaries
            ]

        @app.tool
        @translate_errors
        async def journal_guide() -> dict[str, Any]:
            """Return the canonical shared agent working conventions.

            This is the single source of truth for the project-agnostic
            Bramble journal workflow (append-only model, statuses, tags,
            corrections, open-item/resolves semantics, session start/end
            and Definition of Done). Projects reference this guide instead
            of copying the conventions into each project's instructions, so
            there is one place to maintain and no drift.

            Call this at session start and follow it. ``version`` is the ISO
            date of the last change, so a caller can tell whether the guide
            moved since it last read it.
            """

            return {"version": AGENT_GUIDE_VERSION, "guide": AGENT_GUIDE}

    # ------------------------------------------------------------------
    # Transport entry point
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        transport: str = "stdio",
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        """Start serving via the requested transport (blocks).

        :param transport: ``"stdio"`` for Claude Desktop / Code, or
            ``"http"`` for the HTTP stub (Phase 3 puts Nginx + auth in
            front).
        :param host: Bind host for ``http`` transport.
        :param port: Bind port for ``http`` transport.
        """

        if transport == "stdio":
            logger.info("starting MCP server on stdio")
            self._app.run(transport="stdio")
        elif transport == "http":
            if host is None or port is None:
                raise ValueError("http transport requires host and port")
            logger.info(
                "starting MCP server on http",
                extra={"host": host, "port": port},
            )
            self._app.run(transport="http", host=host, port=port)
        else:
            raise ValueError(
                f"unsupported transport {transport!r}; expected 'stdio' or 'http'"
            )
