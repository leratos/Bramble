"""Runtime configuration for the Bramble MCP server.

The :class:`ServerConfig` collects all knobs the server needs at
startup into one immutable object. Values are resolved per Decision D
in the Phase-2 concept document with the priority:

    CLI argument > environment variable > built-in default

Phase 3 added the auth-token path and the rate-limit knobs as three
further fields with the same resolution rule. Keeping the class
isolated in its own module made that extension a localised change.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Defaults and allowed values
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = Path("./data/bramble.db")
_DEFAULT_TRANSPORT: Literal["stdio", "http"] = "stdio"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_DEFAULT_LOG_LEVEL = "INFO"

# Phase-3 defaults. The token file lives outside the repo
# (``.gitignore`` ignores ``secrets/``); the systemd unit overrides
# the path to ``/opt/bramble/secrets/tokens.json`` via the env var.
# Rate-limit values come straight from Decision E in the Phase-3
# concept document.
_DEFAULT_TOKENS_FILE = Path("./secrets/tokens.json")
_DEFAULT_RATE_LIMIT_PER_TOKEN = 60
_DEFAULT_RATE_LIMIT_PER_IP = 120

# Phase-6 master switch. The self-hosted OAuth 2.1 Authorization Server is
# off by default; turning it on is what makes ``__main__`` build the
# OAuth provider + static-token verifier and hand them to FastMCP. With it
# off the ``http`` transport behaves exactly as in Phase 3 (static bearer
# only), so the existing local bearer path cannot regress. The detailed
# OAuth knobs live in :class:`bramble.oauth_config.OAuthConfig`.
_DEFAULT_ENABLE_OAUTH = False

_VALID_TRANSPORTS: frozenset[str] = frozenset({"stdio", "http"})
_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

# Env-var names. Kept as module constants so callers (tests, Phase-3
# systemd unit, scripts/gen_token.py) can refer to them by name
# instead of string-typing.
ENV_DB_PATH = "BRAMBLE_DB_PATH"
ENV_TRANSPORT = "BRAMBLE_TRANSPORT"
ENV_HOST = "BRAMBLE_HOST"
ENV_PORT = "BRAMBLE_PORT"
ENV_LOG_LEVEL = "BRAMBLE_LOG_LEVEL"
ENV_TOKENS_FILE = "BRAMBLE_TOKENS_FILE"
ENV_RATE_LIMIT_PER_TOKEN = "BRAMBLE_RATE_LIMIT_PER_TOKEN"
ENV_RATE_LIMIT_PER_IP = "BRAMBLE_RATE_LIMIT_PER_IP"
ENV_ENABLE_OAUTH = "BRAMBLE_ENABLE_OAUTH"


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Immutable startup configuration for :class:`JournalMCPServer`.

    Construct directly when wiring tests, or use :meth:`from_sources`
    to resolve CLI / env / defaults in production.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database.
    transport:
        ``"stdio"`` (default, used by Claude Desktop) or ``"http"``
        (the HTTP stub – Phase 3 wires auth on top).
    host:
        Bind host for ``http`` transport. Ignored for ``stdio``.
    port:
        Bind port for ``http`` transport. Ignored for ``stdio``.
    log_level:
        Standard Python logging level name, upper-case
        (``"DEBUG"`` … ``"CRITICAL"``).
    tokens_file:
        Path to the JSON bearer-token map. Only consulted for the
        ``http`` transport, where every request must carry a valid
        token; ignored for ``stdio``.
    rate_limit_per_token:
        Allowed requests per minute for a single token. Doubles as the
        token-bucket capacity (Decision E).
    rate_limit_per_ip:
        Allowed requests per minute for a single client IP – the
        backstop applied before a token is known.
    enable_oauth:
        Master switch for the Phase-6 self-hosted OAuth 2.1 Authorization
        Server. ``False`` (default) keeps the ``http`` transport on the
        Phase-3 static-bearer path unchanged; ``True`` makes ``__main__``
        wire the OAuth provider + static-token verifier. Only consulted
        for the ``http`` transport. The OAuth-specific knobs live in
        :class:`bramble.oauth_config.OAuthConfig`.
    """

    db_path: Path
    transport: Literal["stdio", "http"]
    host: str
    port: int
    log_level: str
    tokens_file: Path = _DEFAULT_TOKENS_FILE
    rate_limit_per_token: int = _DEFAULT_RATE_LIMIT_PER_TOKEN
    rate_limit_per_ip: int = _DEFAULT_RATE_LIMIT_PER_IP
    enable_oauth: bool = _DEFAULT_ENABLE_OAUTH

    def __post_init__(self) -> None:
        self._validate_db_path()
        self._validate_transport()
        self._validate_host()
        self._validate_port()
        self._validate_log_level()
        self._validate_tokens_file()
        self._validate_rate_limits()
        self._validate_enable_oauth()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def _validate_db_path(self) -> None:
        if not isinstance(self.db_path, Path):
            raise TypeError("db_path must be a pathlib.Path")

    def _validate_transport(self) -> None:
        if self.transport not in _VALID_TRANSPORTS:
            allowed = ", ".join(sorted(_VALID_TRANSPORTS))
            raise ValueError(
                f"transport {self.transport!r} is not allowed; "
                f"must be one of: {allowed}"
            )

    def _validate_host(self) -> None:
        if not isinstance(self.host, str):
            raise TypeError("host must be a string")
        if not self.host.strip():
            raise ValueError("host must not be empty")

    def _validate_port(self) -> None:
        # bool is a subclass of int – exclude it explicitly.
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

    def _validate_tokens_file(self) -> None:
        if not isinstance(self.tokens_file, Path):
            raise TypeError("tokens_file must be a pathlib.Path")

    def _validate_rate_limits(self) -> None:
        # bool is a subclass of int – exclude it explicitly.
        for name, value in (
            ("rate_limit_per_token", self.rate_limit_per_token),
            ("rate_limit_per_ip", self.rate_limit_per_ip),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    def _validate_enable_oauth(self) -> None:
        if not isinstance(self.enable_oauth, bool):
            raise TypeError("enable_oauth must be a bool")

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_sources(
        cls,
        argv: list[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ServerConfig:
        """Resolve config from CLI args, env vars, and defaults.

        :param argv: Argument list (without the program name). ``None``
            falls back to ``sys.argv[1:]`` via ``argparse``.
        :param env: Mapping to read env vars from. Defaults to
            :data:`os.environ`. Passing an explicit mapping keeps tests
            hermetic.
        """

        environ: Mapping[str, str] = os.environ if env is None else env
        parser = _build_parser()
        ns = parser.parse_args(argv)

        db_path = _resolve_path(
            cli=ns.db,
            env_value=environ.get(ENV_DB_PATH),
            default=_DEFAULT_DB_PATH,
        )
        transport = _resolve_str(
            cli=ns.transport,
            env_value=environ.get(ENV_TRANSPORT),
            default=_DEFAULT_TRANSPORT,
        )
        host = _resolve_str(
            cli=ns.host,
            env_value=environ.get(ENV_HOST),
            default=_DEFAULT_HOST,
        )
        port = _resolve_int(
            cli=ns.port,
            env_value=environ.get(ENV_PORT),
            default=_DEFAULT_PORT,
            field_name="port",
        )
        log_level = _resolve_str(
            cli=ns.log_level,
            env_value=environ.get(ENV_LOG_LEVEL),
            default=_DEFAULT_LOG_LEVEL,
        ).upper()
        tokens_file = _resolve_path(
            cli=ns.tokens_file,
            env_value=environ.get(ENV_TOKENS_FILE),
            default=_DEFAULT_TOKENS_FILE,
        )
        rate_limit_per_token = _resolve_int(
            cli=ns.rate_limit_per_token,
            env_value=environ.get(ENV_RATE_LIMIT_PER_TOKEN),
            default=_DEFAULT_RATE_LIMIT_PER_TOKEN,
            field_name="rate_limit_per_token",
        )
        rate_limit_per_ip = _resolve_int(
            cli=ns.rate_limit_per_ip,
            env_value=environ.get(ENV_RATE_LIMIT_PER_IP),
            default=_DEFAULT_RATE_LIMIT_PER_IP,
            field_name="rate_limit_per_ip",
        )
        enable_oauth = _resolve_bool(
            cli=ns.enable_oauth,
            env_value=environ.get(ENV_ENABLE_OAUTH),
            default=_DEFAULT_ENABLE_OAUTH,
            field_name="enable_oauth",
        )

        return cls(
            db_path=db_path,
            transport=transport,  # validated in __post_init__
            host=host,
            port=port,
            log_level=log_level,
            tokens_file=tokens_file,
            rate_limit_per_token=rate_limit_per_token,
            rate_limit_per_ip=rate_limit_per_ip,
            enable_oauth=enable_oauth,
        )


# ---------------------------------------------------------------------------
# Argparse + resolution helpers
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bramble-server",
        description="Run the Bramble journal MCP server.",
    )
    parser.add_argument(
        "--db",
        help=f"Path to the SQLite database (env: {ENV_DB_PATH}).",
    )
    parser.add_argument(
        "--transport",
        choices=sorted(_VALID_TRANSPORTS),
        help=f"MCP transport to use (env: {ENV_TRANSPORT}).",
    )
    parser.add_argument(
        "--host",
        help=f"HTTP bind host, only used for --transport http (env: {ENV_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"HTTP bind port, only used for --transport http (env: {ENV_PORT}).",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        help=f"Python logging level name (env: {ENV_LOG_LEVEL}).",
    )
    parser.add_argument(
        "--tokens-file",
        dest="tokens_file",
        help=(
            "Path to the JSON bearer-token map, used by the http "
            f"transport (env: {ENV_TOKENS_FILE})."
        ),
    )
    parser.add_argument(
        "--rate-limit-per-token",
        dest="rate_limit_per_token",
        type=int,
        help=(
            "Requests per minute allowed for a single token "
            f"(env: {ENV_RATE_LIMIT_PER_TOKEN})."
        ),
    )
    parser.add_argument(
        "--rate-limit-per-ip",
        dest="rate_limit_per_ip",
        type=int,
        help=(
            "Requests per minute allowed for a single client IP "
            f"(env: {ENV_RATE_LIMIT_PER_IP})."
        ),
    )
    # store_const with default None keeps the CLI > env > default
    # precedence: an absent flag is None ("not set"), present is True.
    # There is intentionally no CLI way to force False – use the env var
    # (BRAMBLE_ENABLE_OAUTH=false) or the built-in default for that.
    parser.add_argument(
        "--enable-oauth",
        dest="enable_oauth",
        action="store_const",
        const=True,
        default=None,
        help=(
            "Enable the self-hosted OAuth 2.1 Authorization Server on the "
            f"http transport (env: {ENV_ENABLE_OAUTH})."
        ),
    )
    return parser


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


_TRUE_TOKENS: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS: frozenset[str] = frozenset({"0", "false", "no", "off"})


def _resolve_bool(
    *,
    cli: bool | None,
    env_value: str | None,
    default: bool,
    field_name: str,
) -> bool:
    if cli is not None:
        return cli
    if env_value is not None:
        token = env_value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        raise ValueError(
            f"{field_name} env value {env_value!r} is not a boolean; "
            f"use one of: {', '.join(sorted(_TRUE_TOKENS | _FALSE_TOKENS))}"
        )
    return default
