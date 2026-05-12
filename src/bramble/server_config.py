"""Runtime configuration for the Bramble MCP server.

The :class:`ServerConfig` collects all knobs the server needs at
startup into one immutable object. Values are resolved per Decision D
in the Phase-2 concept document with the priority:

    CLI argument > environment variable > built-in default

The class is also the place where Phase 3 will hook in additional
fields (auth-token path, rate-limit overrides, etc.). Keeping it
isolated in its own module makes that extension a localised change.
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

_VALID_TRANSPORTS: frozenset[str] = frozenset({"stdio", "http"})
_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

# Env-var names. Kept as module constants so callers (tests, Phase-3
# systemd unit) can refer to them by name instead of string-typing.
ENV_DB_PATH = "BRAMBLE_DB_PATH"
ENV_TRANSPORT = "BRAMBLE_TRANSPORT"
ENV_HOST = "BRAMBLE_HOST"
ENV_PORT = "BRAMBLE_PORT"
ENV_LOG_LEVEL = "BRAMBLE_LOG_LEVEL"


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
    """

    db_path: Path
    transport: Literal["stdio", "http"]
    host: str
    port: int
    log_level: str

    def __post_init__(self) -> None:
        self._validate_db_path()
        self._validate_transport()
        self._validate_host()
        self._validate_port()
        self._validate_log_level()

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

        return cls(
            db_path=db_path,
            transport=transport,  # validated in __post_init__
            host=host,
            port=port,
            log_level=log_level,
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
