"""Bearer-token authentication for the Bramble MCP server.

Per Phase-3 Decision A every project gets its own bearer token. The
operator keeps a cleartext ``project -> token`` map in a JSON file
(default ``./secrets/tokens.json``, never committed). Cleartext is a
deliberate choice: the same token strings have to be pasted into the
MCP configuration of the consuming AI tools.

:class:`AuthValidator` loads that file once at construction and hashes
every token (SHA-256) into a ``hash -> project`` lookup map. An
incoming token is hashed and looked up – there is no character-by-
character comparison, so token verification carries no timing side
channel.

The module depends only on the standard library; it never imports
``fastmcp``. The request path translates its result into an MCP error,
but the auth logic itself stays transport-agnostic so it can be
unit-tested in-process (a Phase-3 end-of-phase criterion).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Fail2Ban contract: every failed authentication emits a log record
# carrying exactly this event name. The jail filter
# (deploy/fail2ban/bramble-filter.conf) matches on it, so this string
# must not change without updating that filter in lock-step.
AUTH_FAILED_EVENT = "auth_failed"


class AuthValidator:
    """Resolve bearer tokens to the project they belong to.

    Parameters
    ----------
    tokens_file:
        Path to the JSON token map (``{project: token}``). Read once at
        construction time; rotating or revoking a token means editing
        the file and restarting the service.
    """

    def __init__(self, tokens_file: Path | str) -> None:
        if isinstance(tokens_file, str):
            tokens_file = Path(tokens_file)
        if not isinstance(tokens_file, Path):
            raise TypeError("tokens_file must be a pathlib.Path or str")

        self._tokens_file: Path = tokens_file
        self._project_by_hash: dict[str, str] = self._load(tokens_file)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------
    @property
    def tokens_file(self) -> Path:
        """Path the token map was loaded from."""

        return self._tokens_file

    @property
    def project_count(self) -> int:
        """Number of distinct projects that have a token."""

        return len(self._project_by_hash)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def authenticate(self, token: str | None, *, client_ip: str) -> str | None:
        """Return the project a token belongs to, or ``None`` on a miss.

        A miss – no token supplied, or a token that is not in the map –
        emits an ``auth_failed`` JSON log event carrying ``client_ip``
        so Fail2Ban can act on it. A hit is silent.
        """

        project = self.resolve_project(token)
        if project is None:
            logger.warning(
                "authentication failed",
                extra={"event": AUTH_FAILED_EVENT, "client_ip": client_ip},
            )
        return project

    def resolve_project(self, token: str | None) -> str | None:
        """Resolve a token to its project without logging (silent lookup).

        Unlike :meth:`authenticate`, a miss is *not* logged as
        ``auth_failed``. This is for the Phase-6 OAuth ``MultiAuth`` path:
        a token this static map does not recognise may still be a valid
        OAuth access token that another verifier accepts, so logging it as
        a failure here would mislabel legitimate OAuth tokens and could
        trip Fail2Ban (maxretry) against real users. The genuine
        all-verifiers-failed signal is handled at the auth-middleware /
        proxy layer instead.
        """

        if token:
            return self._project_by_hash.get(_hash_token(token))
        return None

    # ------------------------------------------------------------------
    # Token-file loading
    # ------------------------------------------------------------------
    @staticmethod
    def _load(path: Path) -> dict[str, str]:
        """Parse the token file into a ``token-hash -> project`` map."""

        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"token file {path} does not exist; create it with "
                "scripts/gen_token.py"
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"token file {path} is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"token file {path} must contain a JSON object")

        by_hash: dict[str, str] = {}
        for project, token in data.items():
            if not isinstance(project, str) or not project:
                raise ValueError(
                    f"token file {path}: project keys must be non-empty strings"
                )
            if not isinstance(token, str) or not token:
                raise ValueError(
                    f"token file {path}: token for project {project!r} "
                    "must be a non-empty string"
                )
            token_hash = _hash_token(token)
            if token_hash in by_hash:
                raise ValueError(
                    f"token file {path}: projects {by_hash[token_hash]!r} and "
                    f"{project!r} share the same token"
                )
            by_hash[token_hash] = project

        logger.info("loaded %d project token(s) from %s", len(by_hash), path)
        return by_hash


def _hash_token(token: str) -> str:
    """Return the hex SHA-256 digest of ``token``."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
