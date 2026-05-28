"""Token-file management for Bramble project bearer tokens."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TOKEN_NBYTES = 32


@dataclass(frozen=True, slots=True)
class TokenSummary:
    """Read-safe token metadata for the admin UI."""

    project: str


@dataclass(frozen=True, slots=True)
class TokenMutation:
    """Result of a token create, rotation, or revoke operation."""

    project: str
    action: str
    token: str | None = None


class TokenStore:
    """Manage the cleartext ``project -> token`` map atomically.

    The existing MCP server reads cleartext bearer tokens from
    ``tokens.json`` at startup. This class centralises that file format
    for both the CLI helper and the admin UI while ensuring callers do
    not need to expose existing token values.
    """

    def __init__(
        self,
        tokens_file: Path | str,
        *,
        token_factory: Any = None,
    ) -> None:
        if isinstance(tokens_file, str):
            tokens_file = Path(tokens_file)
        if not isinstance(tokens_file, Path):
            raise TypeError("tokens_file must be a pathlib.Path or str")
        self._tokens_file = tokens_file
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(_TOKEN_NBYTES)
        )

    @property
    def tokens_file(self) -> Path:
        """Path to the managed token map."""

        return self._tokens_file

    def list_tokens(self) -> list[TokenSummary]:
        """Return one read-safe summary per project with a token."""

        tokens = load_token_map(self._tokens_file)
        return [TokenSummary(project=project) for project in sorted(tokens)]

    def create(self, project: str) -> TokenMutation:
        """Create a new project token.

        :raises ValueError: If ``project`` is invalid or already has a token.
        """

        project = validate_project(project)
        tokens = load_token_map(self._tokens_file)
        if project in tokens:
            raise ValueError(f"project {project!r} already has a token")
        token = self._new_token()
        tokens[project] = token
        write_token_map(self._tokens_file, tokens)
        return TokenMutation(project=project, action="created", token=token)

    def rotate(self, project: str) -> TokenMutation:
        """Replace an existing project token and return the new value once."""

        project = validate_project(project)
        tokens = load_token_map(self._tokens_file)
        if project not in tokens:
            raise ValueError(f"project {project!r} has no token to rotate")
        token = self._new_token()
        tokens[project] = token
        write_token_map(self._tokens_file, tokens)
        return TokenMutation(project=project, action="rotated", token=token)

    def revoke(self, project: str) -> TokenMutation:
        """Remove an existing project token."""

        project = validate_project(project)
        tokens = load_token_map(self._tokens_file)
        if project not in tokens:
            raise ValueError(f"project {project!r} has no token to revoke")
        del tokens[project]
        write_token_map(self._tokens_file, tokens)
        return TokenMutation(project=project, action="revoked", token=None)

    def upsert(self, project: str) -> TokenMutation:
        """Create or rotate a token, matching the historical CLI behavior."""

        project = validate_project(project)
        tokens = load_token_map(self._tokens_file)
        action = "rotated" if project in tokens else "created"
        token = self._new_token()
        tokens[project] = token
        write_token_map(self._tokens_file, tokens)
        return TokenMutation(project=project, action=action, token=token)

    def _new_token(self) -> str:
        token = self._token_factory()
        if not isinstance(token, str) or not token:
            raise ValueError("token_factory must return a non-empty string")
        return token


def validate_project(project: str) -> str:
    """Validate and normalise a project identifier."""

    if not isinstance(project, str):
        raise TypeError("project must be a string")
    project = project.strip()
    if not _PROJECT_RE.fullmatch(project):
        raise ValueError(
            f"project {project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$"
        )
    return project


def load_token_map(path: Path | str) -> dict[str, str]:
    """Return the existing token map, or an empty map when absent."""

    if isinstance(path, str):
        path = Path(path)
    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path or str")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"token file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"token file {path} must contain a JSON object")
    return _validate_token_map(path, data)


def write_token_map(path: Path | str, tokens: dict[str, str]) -> None:
    """Atomically write ``tokens`` with owner-only file permissions."""

    if isinstance(path, str):
        path = Path(path)
    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path or str")

    tokens = _validate_token_map(path, tokens)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(tokens, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)


def _validate_token_map(path: Path, data: dict[object, object]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    seen_values: dict[str, str] = {}
    for project, token in data.items():
        if not isinstance(project, str) or not project:
            raise ValueError(
                f"token file {path}: project keys must be non-empty strings"
            )
        project = validate_project(project)
        if project in tokens:
            raise ValueError(
                f"token file {path}: project {project!r} appears more than once"
            )
        if not isinstance(token, str) or not token:
            raise ValueError(
                f"token file {path}: token for project {project!r} "
                "must be a non-empty string"
            )
        if token in seen_values:
            raise ValueError(
                f"token file {path}: projects {seen_values[token]!r} and "
                f"{project!r} share the same token"
            )
        seen_values[token] = project
        tokens[project] = token
    return tokens
