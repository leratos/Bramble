"""Runtime configuration for the Bramble admin UI."""

from __future__ import annotations

import argparse
import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from bramble.admin_i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from bramble.admin_time import get_display_timezone
from bramble.server_config import ENV_DB_PATH, ENV_TOKENS_FILE

_DEFAULT_DB_PATH = Path("./data/bramble.db")
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8770
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_ADMIN_SECRET_FILE = Path("./secrets/admin-ui.json")
_DEFAULT_TOKENS_FILE = Path("./secrets/tokens.json")
_DEFAULT_SESSION_IDLE_SECONDS = 30 * 60
_DEFAULT_SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60
_DEFAULT_LOGIN_MAX_ATTEMPTS = 5
_DEFAULT_LOGIN_WINDOW_SECONDS = 5 * 60
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost")
_DEFAULT_DISPLAY_TIMEZONE = "Europe/Berlin"

ENV_ADMIN_HOST = "BRAMBLE_ADMIN_HOST"
ENV_ADMIN_PORT = "BRAMBLE_ADMIN_PORT"
ENV_ADMIN_LOG_LEVEL = "BRAMBLE_ADMIN_LOG_LEVEL"
ENV_ADMIN_SECRET_FILE = "BRAMBLE_ADMIN_SECRET_FILE"
ENV_ADMIN_SESSION_IDLE_SECONDS = "BRAMBLE_ADMIN_SESSION_IDLE_SECONDS"
ENV_ADMIN_SESSION_ABSOLUTE_SECONDS = "BRAMBLE_ADMIN_SESSION_ABSOLUTE_SECONDS"
ENV_ADMIN_LOGIN_MAX_ATTEMPTS = "BRAMBLE_ADMIN_LOGIN_MAX_ATTEMPTS"
ENV_ADMIN_LOGIN_WINDOW_SECONDS = "BRAMBLE_ADMIN_LOGIN_WINDOW_SECONDS"
ENV_ADMIN_COOKIE_SECURE = "BRAMBLE_ADMIN_COOKIE_SECURE"
ENV_ADMIN_ALLOWED_HOSTS = "BRAMBLE_ADMIN_ALLOWED_HOSTS"
ENV_ADMIN_TIME_ZONE = "BRAMBLE_ADMIN_TIME_ZONE"
ENV_ADMIN_LANGUAGE = "BRAMBLE_ADMIN_LANGUAGE"

_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)


@dataclass(frozen=True, slots=True)
class AdminConfig:
    """Immutable startup configuration for the separate admin server."""

    db_path: Path
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    log_level: str = _DEFAULT_LOG_LEVEL
    admin_secret_file: Path = _DEFAULT_ADMIN_SECRET_FILE
    tokens_file: Path = _DEFAULT_TOKENS_FILE
    session_idle_seconds: int = _DEFAULT_SESSION_IDLE_SECONDS
    session_absolute_seconds: int = _DEFAULT_SESSION_ABSOLUTE_SECONDS
    login_max_attempts: int = _DEFAULT_LOGIN_MAX_ATTEMPTS
    login_window_seconds: int = _DEFAULT_LOGIN_WINDOW_SECONDS
    cookie_secure: bool = False
    allowed_hosts: tuple[str, ...] = _DEFAULT_ALLOWED_HOSTS
    display_timezone: str = _DEFAULT_DISPLAY_TIMEZONE
    language: str = DEFAULT_LANGUAGE

    def __post_init__(self) -> None:
        self._validate_db_path()
        self._validate_host()
        self._validate_port()
        self._validate_log_level()
        self._validate_admin_secret_file()
        self._validate_tokens_file()
        self._validate_positive_seconds("session_idle_seconds")
        self._validate_positive_seconds("session_absolute_seconds")
        self._validate_positive_seconds("login_window_seconds")
        self._validate_login_max_attempts()
        self._validate_cookie_secure()
        self._validate_allowed_hosts()
        self._validate_display_timezone()
        self._validate_language()
        if self.session_absolute_seconds < self.session_idle_seconds:
            raise ValueError(
                "session_absolute_seconds must be >= session_idle_seconds"
            )

    def _validate_db_path(self) -> None:
        if not isinstance(self.db_path, Path):
            raise TypeError("db_path must be a pathlib.Path")

    def _validate_host(self) -> None:
        if not isinstance(self.host, str):
            raise TypeError("host must be a string")
        if not _is_loopback_host(self.host):
            raise ValueError(
                "admin host must be loopback-only; use 127.0.0.1 and an SSH tunnel"
            )

    def _validate_port(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("port must be an int")
        if not (1 <= self.port <= 65535):
            raise ValueError("port must be in [1, 65535]")

    def _validate_log_level(self) -> None:
        if not isinstance(self.log_level, str):
            raise TypeError("log_level must be a string")
        if self.log_level not in _VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(_VALID_LOG_LEVELS))
            raise ValueError(
                f"log_level {self.log_level!r} is not allowed; "
                f"must be one of: {allowed}"
            )

    def _validate_admin_secret_file(self) -> None:
        if not isinstance(self.admin_secret_file, Path):
            raise TypeError("admin_secret_file must be a pathlib.Path")

    def _validate_tokens_file(self) -> None:
        if not isinstance(self.tokens_file, Path):
            raise TypeError("tokens_file must be a pathlib.Path")

    def _validate_positive_seconds(self, field_name: str) -> None:
        value = getattr(self, field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} must be an int")
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")

    def _validate_login_max_attempts(self) -> None:
        if isinstance(self.login_max_attempts, bool) or not isinstance(
            self.login_max_attempts, int
        ):
            raise TypeError("login_max_attempts must be an int")
        if self.login_max_attempts <= 0:
            raise ValueError("login_max_attempts must be positive")

    def _validate_cookie_secure(self) -> None:
        if not isinstance(self.cookie_secure, bool):
            raise TypeError("cookie_secure must be a bool")

    def _validate_allowed_hosts(self) -> None:
        if not isinstance(self.allowed_hosts, tuple):
            raise TypeError("allowed_hosts must be a tuple")
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must not be empty")
        for host in self.allowed_hosts:
            if not isinstance(host, str) or not host.strip():
                raise ValueError("allowed_hosts must contain non-empty strings")

    def _validate_display_timezone(self) -> None:
        if not isinstance(self.display_timezone, str):
            raise TypeError("display_timezone must be a string")
        if not self.display_timezone.strip():
            raise ValueError("display_timezone must not be empty")
        try:
            get_display_timezone(self.display_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"display_timezone {self.display_timezone!r} is not a valid "
                "or supported timezone"
            ) from exc

    def _validate_language(self) -> None:
        if not isinstance(self.language, str):
            raise TypeError("language must be a string")
        if self.language not in SUPPORTED_LANGUAGES:
            allowed = ", ".join(SUPPORTED_LANGUAGES)
            raise ValueError(
                f"language {self.language!r} is not allowed; must be one of: {allowed}"
            )

    @classmethod
    def from_sources(
        cls,
        argv: list[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> AdminConfig:
        """Resolve admin config from CLI args, env vars, and defaults."""

        environ: Mapping[str, str] = os.environ if env is None else env
        parser = _build_parser()
        ns = parser.parse_args(argv)

        return cls(
            db_path=_resolve_path(
                cli=ns.db,
                env_value=environ.get(ENV_DB_PATH),
                default=_DEFAULT_DB_PATH,
            ),
            host=_resolve_str(
                cli=ns.host,
                env_value=environ.get(ENV_ADMIN_HOST),
                default=_DEFAULT_HOST,
            ),
            port=_resolve_int(
                cli=ns.port,
                env_value=environ.get(ENV_ADMIN_PORT),
                default=_DEFAULT_PORT,
                field_name="port",
            ),
            log_level=_resolve_str(
                cli=ns.log_level,
                env_value=environ.get(ENV_ADMIN_LOG_LEVEL),
                default=_DEFAULT_LOG_LEVEL,
            ).upper(),
            admin_secret_file=_resolve_path(
                cli=ns.admin_secret_file,
                env_value=environ.get(ENV_ADMIN_SECRET_FILE),
                default=_DEFAULT_ADMIN_SECRET_FILE,
            ),
            tokens_file=_resolve_path(
                cli=ns.tokens_file,
                env_value=environ.get(ENV_TOKENS_FILE),
                default=_DEFAULT_TOKENS_FILE,
            ),
            session_idle_seconds=_resolve_int(
                cli=ns.session_idle_seconds,
                env_value=environ.get(ENV_ADMIN_SESSION_IDLE_SECONDS),
                default=_DEFAULT_SESSION_IDLE_SECONDS,
                field_name="session_idle_seconds",
            ),
            session_absolute_seconds=_resolve_int(
                cli=ns.session_absolute_seconds,
                env_value=environ.get(ENV_ADMIN_SESSION_ABSOLUTE_SECONDS),
                default=_DEFAULT_SESSION_ABSOLUTE_SECONDS,
                field_name="session_absolute_seconds",
            ),
            login_max_attempts=_resolve_int(
                cli=ns.login_max_attempts,
                env_value=environ.get(ENV_ADMIN_LOGIN_MAX_ATTEMPTS),
                default=_DEFAULT_LOGIN_MAX_ATTEMPTS,
                field_name="login_max_attempts",
            ),
            login_window_seconds=_resolve_int(
                cli=ns.login_window_seconds,
                env_value=environ.get(ENV_ADMIN_LOGIN_WINDOW_SECONDS),
                default=_DEFAULT_LOGIN_WINDOW_SECONDS,
                field_name="login_window_seconds",
            ),
            cookie_secure=_resolve_bool(
                cli=ns.cookie_secure,
                env_value=environ.get(ENV_ADMIN_COOKIE_SECURE),
                default=False,
                field_name="cookie_secure",
            ),
            allowed_hosts=_resolve_hosts(
                cli=ns.allowed_hosts,
                env_value=environ.get(ENV_ADMIN_ALLOWED_HOSTS),
                default=_DEFAULT_ALLOWED_HOSTS,
            ),
            display_timezone=_resolve_str(
                cli=ns.time_zone,
                env_value=environ.get(ENV_ADMIN_TIME_ZONE),
                default=_DEFAULT_DISPLAY_TIMEZONE,
            ),
            language=_resolve_str(
                cli=ns.language,
                env_value=environ.get(ENV_ADMIN_LANGUAGE),
                default=DEFAULT_LANGUAGE,
            ).strip().lower(),
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bramble-admin",
        description="Run the Bramble admin UI.",
    )
    parser.add_argument("--db", help=f"Path to the SQLite database (env: {ENV_DB_PATH}).")
    parser.add_argument(
        "--host",
        help=f"Admin bind host. Must be loopback-only (env: {ENV_ADMIN_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"Admin bind port (env: {ENV_ADMIN_PORT}).",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        help=f"Python logging level name (env: {ENV_ADMIN_LOG_LEVEL}).",
    )
    parser.add_argument(
        "--admin-secret-file",
        dest="admin_secret_file",
        help=f"Path to admin-ui.json (env: {ENV_ADMIN_SECRET_FILE}).",
    )
    parser.add_argument(
        "--tokens-file",
        dest="tokens_file",
        help=f"Path to tokens.json for token admin actions (env: {ENV_TOKENS_FILE}).",
    )
    parser.add_argument(
        "--session-idle-seconds",
        dest="session_idle_seconds",
        type=int,
        help=f"Idle timeout for admin sessions (env: {ENV_ADMIN_SESSION_IDLE_SECONDS}).",
    )
    parser.add_argument(
        "--session-absolute-seconds",
        dest="session_absolute_seconds",
        type=int,
        help=(
            "Absolute timeout for admin sessions "
            f"(env: {ENV_ADMIN_SESSION_ABSOLUTE_SECONDS})."
        ),
    )
    parser.add_argument(
        "--login-max-attempts",
        dest="login_max_attempts",
        type=int,
        help=f"Failed login attempts per window (env: {ENV_ADMIN_LOGIN_MAX_ATTEMPTS}).",
    )
    parser.add_argument(
        "--login-window-seconds",
        dest="login_window_seconds",
        type=int,
        help=f"Login rate-limit window (env: {ENV_ADMIN_LOGIN_WINDOW_SECONDS}).",
    )
    parser.add_argument(
        "--secure-cookie",
        dest="cookie_secure",
        action="store_true",
        default=None,
        help=f"Mark the session cookie Secure (env: {ENV_ADMIN_COOKIE_SECURE}).",
    )
    parser.add_argument(
        "--allowed-host",
        dest="allowed_hosts",
        action="append",
        help=(
            "Allowed Host header. Repeatable; defaults to 127.0.0.1 and localhost "
            f"(env: {ENV_ADMIN_ALLOWED_HOSTS}, comma-separated)."
        ),
    )
    parser.add_argument(
        "--time-zone",
        dest="time_zone",
        help=(
            "IANA timezone for admin timestamp display "
            f"(env: {ENV_ADMIN_TIME_ZONE}; default: {_DEFAULT_DISPLAY_TIMEZONE})."
        ),
    )
    parser.add_argument(
        "--language",
        dest="language",
        choices=SUPPORTED_LANGUAGES,
        help=(
            "Admin UI language "
            f"(env: {ENV_ADMIN_LANGUAGE}; default: {DEFAULT_LANGUAGE})."
        ),
    )
    return parser


def _is_loopback_host(host: str) -> bool:
    stripped = host.strip().strip("[]")
    if stripped == "localhost":
        return True
    try:
        return ipaddress.ip_address(stripped).is_loopback
    except ValueError:
        return False


def _resolve_path(*, cli: str | None, env_value: str | None, default: Path) -> Path:
    if cli is not None:
        return Path(cli)
    if env_value is not None:
        return Path(env_value)
    return default


def _resolve_str(*, cli: str | None, env_value: str | None, default: str) -> str:
    if cli is not None:
        return cli
    if env_value is not None:
        return env_value
    return default


def _resolve_int(
    *,
    cli: int | None,
    env_value: str | None,
    default: int,
    field_name: str,
) -> int:
    if cli is not None:
        return cli
    if env_value is not None:
        try:
            return int(env_value)
        except ValueError as exc:
            raise ValueError(
                f"{field_name} env value {env_value!r} is not an integer"
            ) from exc
    return default


def _resolve_bool(
    *,
    cli: bool | None,
    env_value: str | None,
    default: bool,
    field_name: str,
) -> bool:
    if cli is not None:
        return cli
    if env_value is None:
        return default

    normalised = env_value.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} env value {env_value!r} is not a bool")


def _resolve_hosts(
    *,
    cli: list[str] | None,
    env_value: str | None,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if cli:
        return tuple(host.strip() for host in cli if host.strip())
    if env_value is not None:
        return tuple(host.strip() for host in env_value.split(",") if host.strip())
    return default
