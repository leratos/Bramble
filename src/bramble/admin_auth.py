"""Authentication primitives for the Bramble admin UI."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised in environments with argon2-cffi installed.
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
    from argon2.low_level import Type
except ModuleNotFoundError as exc:  # pragma: no cover - tested by startup failure path.
    PasswordHasher = None  # type: ignore[assignment]
    InvalidHashError = VerificationError = VerifyMismatchError = Exception
    Type = None  # type: ignore[assignment]
    _ARGON2_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    _ARGON2_IMPORT_ERROR = None

SESSION_COOKIE_NAME = "bramble_admin_session"


@dataclass(frozen=True, slots=True)
class AdminSecret:
    """Loaded admin password verifier metadata."""

    username: str
    password_hash: str

    @classmethod
    def load(cls, path: Path | str) -> AdminSecret:
        if isinstance(path, str):
            path = Path(path)
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path or str")

        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"admin secret file {path} does not exist; create admin-ui.json "
                "with an Argon2id password_hash before starting bramble-admin"
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"admin secret file {path} is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"admin secret file {path} must contain a JSON object")

        username = data.get("username")
        password_hash = data.get("password_hash")
        if not isinstance(username, str) or not username.strip():
            raise ValueError("admin secret username must be a non-empty string")
        if not isinstance(password_hash, str) or not password_hash.startswith(
            "$argon2id$"
        ):
            raise ValueError("admin secret password_hash must be an Argon2id hash")

        return cls(username=username.strip(), password_hash=password_hash)


class AdminAuthenticator:
    """Verify one local admin user against an Argon2id hash file."""

    def __init__(self, secret_file: Path | str) -> None:
        self._secret_file = Path(secret_file)
        self._secret = AdminSecret.load(self._secret_file)
        self._hasher = _password_hasher()
        self._dummy_hash = self._hasher.hash("bramble-admin-dummy-password")

    @property
    def username(self) -> str:
        return self._secret.username

    @property
    def secret_file(self) -> Path:
        return self._secret_file

    def verify(self, username: str, password: str) -> bool:
        """Return ``True`` only when both username and password match."""

        if not isinstance(username, str) or not isinstance(password, str):
            return False

        username_matches = secrets.compare_digest(username, self._secret.username)
        password_hash = self._secret.password_hash if username_matches else self._dummy_hash
        try:
            self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False
        return username_matches


@dataclass(frozen=True, slots=True)
class AdminSession:
    """Server-side admin session metadata."""

    actor: str
    created_at: float
    last_seen: float


class SessionStore:
    """In-memory store for opaque admin session identifiers."""

    def __init__(
        self,
        *,
        idle_seconds: int,
        absolute_seconds: int,
        time_source: Any = time.time,
        token_factory: Any = None,
    ) -> None:
        _validate_positive_int(idle_seconds, "idle_seconds")
        _validate_positive_int(absolute_seconds, "absolute_seconds")
        if absolute_seconds < idle_seconds:
            raise ValueError("absolute_seconds must be >= idle_seconds")
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds
        self._now = time_source
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._sessions: dict[str, AdminSession] = {}

    def create(self, actor: str) -> str:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        now = float(self._now())
        session_id = self._token_factory()
        self._sessions[session_id] = AdminSession(
            actor=actor.strip(),
            created_at=now,
            last_seen=now,
        )
        return session_id

    def get(self, session_id: str | None) -> AdminSession | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = float(self._now())
        if self._is_expired(session, now):
            self.destroy(session_id)
            return None
        refreshed = replace(session, last_seen=now)
        self._sessions[session_id] = refreshed
        return refreshed

    def destroy(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def invalidate_all(self) -> None:
        self._sessions.clear()

    def _is_expired(self, session: AdminSession, now: float) -> bool:
        return (
            now - session.last_seen > self._idle_seconds
            or now - session.created_at > self._absolute_seconds
        )


@dataclass(slots=True)
class _AttemptWindow:
    count: int
    started_at: float


class LoginRateLimiter:
    """Fixed-window limiter for failed admin login attempts per IP."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        time_source: Any = time.time,
    ) -> None:
        _validate_positive_int(max_attempts, "max_attempts")
        _validate_positive_int(window_seconds, "window_seconds")
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._now = time_source
        self._windows: dict[str, _AttemptWindow] = {}

    def allow(self, client_ip: str) -> bool:
        window = self._current_window(client_ip)
        return window.count < self._max_attempts

    def record_failure(self, client_ip: str) -> None:
        window = self._current_window(client_ip)
        window.count += 1

    def record_success(self, client_ip: str) -> None:
        self._windows.pop(client_ip, None)

    def _current_window(self, client_ip: str) -> _AttemptWindow:
        now = float(self._now())
        window = self._windows.get(client_ip)
        if window is None or now - window.started_at >= self._window_seconds:
            window = _AttemptWindow(count=0, started_at=now)
            self._windows[client_ip] = window
        return window


def hash_admin_password(password: str) -> str:
    """Return an Argon2id hash suitable for admin-ui.json."""

    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return _password_hasher().hash(password)


def _password_hasher() -> Any:
    if PasswordHasher is None or Type is None:
        raise RuntimeError("argon2-cffi is required for the admin UI") from (
            _ARGON2_IMPORT_ERROR
        )
    return PasswordHasher(type=Type.ID)


def _validate_positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
