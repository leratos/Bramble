"""Configuration for Bramble's self-hosted OAuth 2.1 Authorization Server.

Phase 6 adds a self-hosted Authorization Server so Bramble can be added
as a *private* custom connector in Claude Web/Mobile (those clients only
support OAuth, never a static bearer token). :class:`OAuthConfig` collects
every knob that the OAuth provider and its SQLite store need into one
immutable object, mirroring :class:`bramble.server_config.ServerConfig`.

Resolution is environment-only (no CLI): the master on/off switch is
:attr:`ServerConfig.enable_oauth`; once on, :mod:`bramble.__main__` builds
this config from the process environment. The confidential static-client
secret is expected to arrive via the ``FASTMCP_ENV_FILE`` env file (mode
600, never committed), so by the time :meth:`OAuthConfig.from_env` runs it
is already present in ``os.environ``.

Design choices (Phase-6 decisions D3/D4, see journal bramble#848):

* Tokens are **opaque** and persisted in a separate ``oauth.db`` (mutable),
  kept apart from the append-only journal DB. No JWT signing key is needed.
* The OAuth path is **read-only** (default scope ``journal:read``); the
  read-only enforcement itself lives in the MCP layer, not here.
* Dynamic Client Registration (RFC 7591) is on by default so Claude can
  self-register; a confidential static client is an optional fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_OAUTH_DB_PATH = Path("./data/oauth.db")
_DEFAULT_SCOPES: tuple[str, ...] = ("journal:read",)
_DEFAULT_ENABLE_DCR = True
_DEFAULT_ACCESS_TOKEN_TTL = 3600  # 1 hour
_DEFAULT_REFRESH_TOKEN_TTL: int | None = 2_592_000  # 30 days; None = never
_DEFAULT_AUTH_CODE_TTL = 300  # 5 minutes (short-lived, single-use)

# Phase-6.6 resource-owner gate on /authorize. The AS must authenticate the
# owner (and take explicit consent) before issuing a code, else any
# self-registered client could mint a read token. Reuses the admin Argon2
# primitives against a DEDICATED secret file (separate from the admin UI).
_DEFAULT_OWNER_SECRET_FILE = Path("./secrets/oauth-owner.json")
_DEFAULT_OWNER_SESSION_IDLE_SECONDS = 900  # 15 min
_DEFAULT_OWNER_SESSION_ABSOLUTE_SECONDS = 28_800  # 8 h
_DEFAULT_OWNER_LOGIN_MAX_ATTEMPTS = 5
_DEFAULT_OWNER_LOGIN_WINDOW_SECONDS = 300  # 5 min
_DEFAULT_OWNER_COOKIE_SECURE = True

# Hosts for which a plain-http base URL is tolerated (local development and
# the in-process test client). Any other host must use https – Claude will
# not talk OAuth to a non-TLS connector, and tokens must not cross the wire
# in clear.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# Env-var names. Kept as module constants so tests, the systemd unit and the
# secrets env file refer to them by name instead of string-typing.
ENV_OAUTH_PUBLIC_BASE_URL = "BRAMBLE_OAUTH_PUBLIC_BASE_URL"
ENV_OAUTH_DB_PATH = "BRAMBLE_OAUTH_DB_PATH"
ENV_OAUTH_SCOPES = "BRAMBLE_OAUTH_SCOPES"
ENV_OAUTH_ENABLE_DCR = "BRAMBLE_OAUTH_ENABLE_DCR"
ENV_OAUTH_ACCESS_TOKEN_TTL = "BRAMBLE_OAUTH_ACCESS_TOKEN_TTL"
ENV_OAUTH_REFRESH_TOKEN_TTL = "BRAMBLE_OAUTH_REFRESH_TOKEN_TTL"
ENV_OAUTH_AUTH_CODE_TTL = "BRAMBLE_OAUTH_AUTH_CODE_TTL"
ENV_OAUTH_STATIC_CLIENT_ID = "BRAMBLE_OAUTH_STATIC_CLIENT_ID"
ENV_OAUTH_STATIC_CLIENT_SECRET = "BRAMBLE_OAUTH_STATIC_CLIENT_SECRET"
ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS = "BRAMBLE_OAUTH_STATIC_CLIENT_REDIRECT_URIS"
ENV_OAUTH_OWNER_SECRET_FILE = "BRAMBLE_OAUTH_OWNER_SECRET_FILE"
ENV_OAUTH_OWNER_SESSION_IDLE_SECONDS = "BRAMBLE_OAUTH_OWNER_SESSION_IDLE_SECONDS"
ENV_OAUTH_OWNER_SESSION_ABSOLUTE_SECONDS = "BRAMBLE_OAUTH_OWNER_SESSION_ABSOLUTE_SECONDS"
ENV_OAUTH_OWNER_LOGIN_MAX_ATTEMPTS = "BRAMBLE_OAUTH_OWNER_LOGIN_MAX_ATTEMPTS"
ENV_OAUTH_OWNER_LOGIN_WINDOW_SECONDS = "BRAMBLE_OAUTH_OWNER_LOGIN_WINDOW_SECONDS"
ENV_OAUTH_OWNER_COOKIE_SECURE = "BRAMBLE_OAUTH_OWNER_COOKIE_SECURE"

_TRUE_TOKENS: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS: frozenset[str] = frozenset({"0", "false", "no", "off"})
# Only explicit words mean "no expiry". Deliberately NOT "0"/"" – an operator
# setting REFRESH_TOKEN_TTL=0 intends a (rejected) zero TTL, not the longest
# possible lifetime, so 0 must fall through to int() and fail validation.
_NONE_TOKENS: frozenset[str] = frozenset({"none", "never"})


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    """Immutable configuration for the OAuth 2.1 Authorization Server.

    Construct directly in tests, or use :meth:`from_env` to resolve from
    the process environment in production.

    Parameters
    ----------
    public_base_url:
        Externally visible base URL of the deployment, e.g.
        ``https://journal.last-strawberry.com``. This becomes the OAuth
        ``issuer`` and the prefix of every advertised endpoint, so it must
        be the public URL the client reaches – never the internal
        ``127.0.0.1:8765`` bind. https is required unless the host is local.
    oauth_db_path:
        Filesystem path to the SQLite database holding the mutable OAuth
        state (clients, auth codes, tokens). Deliberately separate from the
        append-only journal DB.
    scopes:
        Scopes the AS advertises and may grant. Defaults to read-only.
    enable_dcr:
        Whether Dynamic Client Registration (RFC 7591, the ``/register``
        endpoint) is enabled. On by default so Claude can self-register.
    access_token_ttl:
        Lifetime of an issued access token, in seconds.
    refresh_token_ttl:
        Lifetime of an issued refresh token, in seconds, or ``None`` for no
        expiry.
    auth_code_ttl:
        Lifetime of an authorization code, in seconds. Kept short; codes
        are single-use.
    static_client_id / static_client_secret / static_client_redirect_uris:
        Optional confidential static client used as a fallback when DCR is
        not available on the client side. All-or-nothing: a static client
        id requires a secret and at least one redirect URI. Leave unset for
        a DCR-only deployment.
    """

    public_base_url: str
    oauth_db_path: Path = _DEFAULT_OAUTH_DB_PATH
    scopes: tuple[str, ...] = _DEFAULT_SCOPES
    enable_dcr: bool = _DEFAULT_ENABLE_DCR
    access_token_ttl: int = _DEFAULT_ACCESS_TOKEN_TTL
    refresh_token_ttl: int | None = _DEFAULT_REFRESH_TOKEN_TTL
    auth_code_ttl: int = _DEFAULT_AUTH_CODE_TTL
    static_client_id: str | None = None
    static_client_secret: str | None = None
    static_client_redirect_uris: tuple[str, ...] = field(default_factory=tuple)
    owner_secret_file: Path = _DEFAULT_OWNER_SECRET_FILE
    owner_session_idle_seconds: int = _DEFAULT_OWNER_SESSION_IDLE_SECONDS
    owner_session_absolute_seconds: int = _DEFAULT_OWNER_SESSION_ABSOLUTE_SECONDS
    owner_login_max_attempts: int = _DEFAULT_OWNER_LOGIN_MAX_ATTEMPTS
    owner_login_window_seconds: int = _DEFAULT_OWNER_LOGIN_WINDOW_SECONDS
    owner_cookie_secure: bool = _DEFAULT_OWNER_COOKIE_SECURE

    def __post_init__(self) -> None:
        self._validate_public_base_url()
        self._validate_oauth_db_path()
        self._validate_scopes()
        self._validate_enable_dcr()
        self._validate_ttls()
        self._validate_static_client()
        self._validate_owner_gate()

    # ------------------------------------------------------------------
    # Derived accessors
    # ------------------------------------------------------------------
    @property
    def has_static_client(self) -> bool:
        """Whether a confidential static fallback client is configured."""

        return self.static_client_id is not None

    @property
    def resource_url(self) -> str:
        """The protected MCP resource URL (``<base>/mcp``)."""

        return f"{self.public_base_url}/mcp"

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def _validate_public_base_url(self) -> None:
        if not isinstance(self.public_base_url, str):
            raise TypeError("public_base_url must be a string")
        value = self.public_base_url.strip()
        if not value:
            raise ValueError("public_base_url must not be empty")
        if value.endswith("/"):
            raise ValueError(
                "public_base_url must not end with a trailing slash "
                f"(got {self.public_base_url!r})"
            )
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                "public_base_url must be an http(s) URL "
                f"(got scheme {parsed.scheme!r})"
            )
        if not parsed.netloc:
            raise ValueError(
                f"public_base_url {self.public_base_url!r} has no host"
            )
        host = parsed.hostname or ""
        if parsed.scheme == "http" and host not in _LOCAL_HOSTS:
            raise ValueError(
                "public_base_url must use https for non-local hosts "
                f"(got {self.public_base_url!r}); plain http is allowed only "
                f"for {', '.join(sorted(_LOCAL_HOSTS))}"
            )

    def _validate_oauth_db_path(self) -> None:
        if not isinstance(self.oauth_db_path, Path):
            raise TypeError("oauth_db_path must be a pathlib.Path")

    def _validate_scopes(self) -> None:
        if not isinstance(self.scopes, tuple):
            raise TypeError("scopes must be a tuple of strings")
        if not self.scopes:
            raise ValueError("scopes must list at least one scope")
        for scope in self.scopes:
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError("each scope must be a non-empty string")
            if any(ch.isspace() for ch in scope):
                raise ValueError(f"scope {scope!r} must not contain whitespace")

    def _validate_enable_dcr(self) -> None:
        if not isinstance(self.enable_dcr, bool):
            raise TypeError("enable_dcr must be a bool")

    def _validate_ttls(self) -> None:
        for name, value in (
            ("access_token_ttl", self.access_token_ttl),
            ("auth_code_ttl", self.auth_code_ttl),
        ):
            # bool is a subclass of int – exclude it explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.refresh_token_ttl is not None:
            if isinstance(self.refresh_token_ttl, bool) or not isinstance(
                self.refresh_token_ttl, int
            ):
                raise TypeError("refresh_token_ttl must be an int or None")
            if self.refresh_token_ttl <= 0:
                raise ValueError("refresh_token_ttl must be positive or None")

    def _validate_static_client(self) -> None:
        id_set = self.static_client_id is not None
        secret_set = self.static_client_secret is not None
        uris_set = bool(self.static_client_redirect_uris)

        if not (id_set or secret_set or uris_set):
            return  # DCR-only deployment, nothing to validate.

        if not id_set:
            raise ValueError(
                "static_client_secret / static_client_redirect_uris given "
                "without static_client_id"
            )
        if not isinstance(self.static_client_id, str) or not self.static_client_id:
            raise ValueError("static_client_id must be a non-empty string")
        if not secret_set:
            raise ValueError("static_client_id requires static_client_secret")
        if (
            not isinstance(self.static_client_secret, str)
            or not self.static_client_secret
        ):
            raise ValueError("static_client_secret must be a non-empty string")
        if not uris_set:
            raise ValueError(
                "static_client_id requires at least one redirect URI"
            )
        if not isinstance(self.static_client_redirect_uris, tuple):
            raise TypeError("static_client_redirect_uris must be a tuple")
        for uri in self.static_client_redirect_uris:
            self._validate_redirect_uri(uri)

    def _validate_owner_gate(self) -> None:
        if not isinstance(self.owner_secret_file, Path):
            raise TypeError("owner_secret_file must be a pathlib.Path")
        if not isinstance(self.owner_cookie_secure, bool):
            raise TypeError("owner_cookie_secure must be a bool")
        for name, value in (
            ("owner_session_idle_seconds", self.owner_session_idle_seconds),
            ("owner_session_absolute_seconds", self.owner_session_absolute_seconds),
            ("owner_login_max_attempts", self.owner_login_max_attempts),
            ("owner_login_window_seconds", self.owner_login_window_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.owner_session_absolute_seconds < self.owner_session_idle_seconds:
            raise ValueError(
                "owner_session_absolute_seconds must be >= owner_session_idle_seconds"
            )

    @staticmethod
    def _validate_redirect_uri(uri: str) -> None:
        if not isinstance(uri, str) or not uri.strip():
            raise ValueError("each redirect URI must be a non-empty string")
        parsed = urlparse(uri)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"redirect URI {uri!r} must be an http(s) URL"
            )
        host = parsed.hostname or ""
        if parsed.scheme == "http" and host not in _LOCAL_HOSTS:
            raise ValueError(
                f"redirect URI {uri!r} must use https for non-local hosts"
            )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OAuthConfig:
        """Resolve config from environment variables.

        :param env: Mapping to read from. Defaults to :data:`os.environ`.
            Passing an explicit mapping keeps tests hermetic.

        Raises ``ValueError`` if the required public base URL is missing –
        this factory is only called once OAuth is switched on, so the URL
        is mandatory.
        """

        import os

        environ: Mapping[str, str] = os.environ if env is None else env

        public_base_url = environ.get(ENV_OAUTH_PUBLIC_BASE_URL)
        if not public_base_url:
            raise ValueError(
                f"{ENV_OAUTH_PUBLIC_BASE_URL} must be set when OAuth is enabled"
            )

        db_path_value = environ.get(ENV_OAUTH_DB_PATH)
        oauth_db_path = (
            Path(db_path_value) if db_path_value else _DEFAULT_OAUTH_DB_PATH
        )

        scopes = _parse_list(environ.get(ENV_OAUTH_SCOPES))
        scopes_tuple = tuple(scopes) if scopes else _DEFAULT_SCOPES

        enable_dcr = _parse_bool(
            environ.get(ENV_OAUTH_ENABLE_DCR),
            default=_DEFAULT_ENABLE_DCR,
            field_name="enable_dcr",
        )
        access_token_ttl = _parse_int(
            environ.get(ENV_OAUTH_ACCESS_TOKEN_TTL),
            default=_DEFAULT_ACCESS_TOKEN_TTL,
            field_name="access_token_ttl",
        )
        refresh_token_ttl = _parse_int_or_none(
            environ.get(ENV_OAUTH_REFRESH_TOKEN_TTL),
            default=_DEFAULT_REFRESH_TOKEN_TTL,
            field_name="refresh_token_ttl",
        )
        auth_code_ttl = _parse_int(
            environ.get(ENV_OAUTH_AUTH_CODE_TTL),
            default=_DEFAULT_AUTH_CODE_TTL,
            field_name="auth_code_ttl",
        )

        static_client_id = environ.get(ENV_OAUTH_STATIC_CLIENT_ID) or None
        static_client_secret = environ.get(ENV_OAUTH_STATIC_CLIENT_SECRET) or None
        static_client_redirect_uris = tuple(
            _parse_list(environ.get(ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS))
        )

        owner_secret_value = environ.get(ENV_OAUTH_OWNER_SECRET_FILE)
        owner_secret_file = (
            Path(owner_secret_value)
            if owner_secret_value
            else _DEFAULT_OWNER_SECRET_FILE
        )

        return cls(
            public_base_url=public_base_url.strip(),
            oauth_db_path=oauth_db_path,
            scopes=scopes_tuple,
            enable_dcr=enable_dcr,
            access_token_ttl=access_token_ttl,
            refresh_token_ttl=refresh_token_ttl,
            auth_code_ttl=auth_code_ttl,
            static_client_id=static_client_id,
            static_client_secret=static_client_secret,
            static_client_redirect_uris=static_client_redirect_uris,
            owner_secret_file=owner_secret_file,
            owner_session_idle_seconds=_parse_int(
                environ.get(ENV_OAUTH_OWNER_SESSION_IDLE_SECONDS),
                default=_DEFAULT_OWNER_SESSION_IDLE_SECONDS,
                field_name="owner_session_idle_seconds",
            ),
            owner_session_absolute_seconds=_parse_int(
                environ.get(ENV_OAUTH_OWNER_SESSION_ABSOLUTE_SECONDS),
                default=_DEFAULT_OWNER_SESSION_ABSOLUTE_SECONDS,
                field_name="owner_session_absolute_seconds",
            ),
            owner_login_max_attempts=_parse_int(
                environ.get(ENV_OAUTH_OWNER_LOGIN_MAX_ATTEMPTS),
                default=_DEFAULT_OWNER_LOGIN_MAX_ATTEMPTS,
                field_name="owner_login_max_attempts",
            ),
            owner_login_window_seconds=_parse_int(
                environ.get(ENV_OAUTH_OWNER_LOGIN_WINDOW_SECONDS),
                default=_DEFAULT_OWNER_LOGIN_WINDOW_SECONDS,
                field_name="owner_login_window_seconds",
            ),
            owner_cookie_secure=_parse_bool(
                environ.get(ENV_OAUTH_OWNER_COOKIE_SECURE),
                default=_DEFAULT_OWNER_COOKIE_SECURE,
                field_name="owner_cookie_secure",
            ),
        )


# ---------------------------------------------------------------------------
# Env parsing helpers
# ---------------------------------------------------------------------------
def _parse_list(value: str | None) -> list[str]:
    """Split a comma- or whitespace-separated env value into items."""

    if not value:
        return []
    return [item for item in value.replace(",", " ").split() if item]


def _parse_bool(value: str | None, *, default: bool, field_name: str) -> bool:
    if value is None:
        return default
    token = value.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ValueError(
        f"{field_name} env value {value!r} is not a boolean; use one of: "
        f"{', '.join(sorted(_TRUE_TOKENS | _FALSE_TOKENS))}"
    )


def _parse_int(value: str | None, *, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} env value {value!r} is not an integer"
        ) from exc


def _parse_int_or_none(
    value: str | None, *, default: int | None, field_name: str
) -> int | None:
    if value is None:
        return default
    if value.strip().lower() in _NONE_TOKENS:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} env value {value!r} is not an integer or 'none'"
        ) from exc
