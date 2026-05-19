"""Unit tests for :class:`bramble.auth_validator.AuthValidator`.

These run fully in-process – no network, no MCP server – per the
Phase-3 end-of-phase criterion that the auth logic is hermetically
testable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from bramble.auth_validator import AUTH_FAILED_EVENT, AuthValidator, _hash_token


def write_token_file(path: Path, mapping: dict[str, str]) -> Path:
    """Write ``mapping`` as a JSON token file and return ``path``."""

    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


@pytest.fixture()
def tokens_file(tmp_path: Path) -> Path:
    return write_token_file(
        tmp_path / "tokens.json",
        {"bramble": "tok-bramble", "elder-berry": "tok-elder"},
    )


# ---------------------------------------------------------------------------
# Construction & token-file loading
# ---------------------------------------------------------------------------
class TestLoading:
    def test_loads_valid_file(self, tokens_file: Path) -> None:
        validator = AuthValidator(tokens_file)
        assert validator.tokens_file == tokens_file
        assert validator.project_count == 2

    def test_accepts_str_path(self, tokens_file: Path) -> None:
        validator = AuthValidator(str(tokens_file))
        assert validator.project_count == 2

    def test_rejects_non_path(self) -> None:
        with pytest.raises(TypeError):
            AuthValidator(123)  # type: ignore[arg-type]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="gen_token"):
            AuthValidator(tmp_path / "absent.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "tokens.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            AuthValidator(bad)

    def test_non_object_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "tokens.json"
        bad.write_text('["a", "b"]', encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            AuthValidator(bad)

    def test_empty_token_value_raises(self, tmp_path: Path) -> None:
        path = write_token_file(tmp_path / "tokens.json", {"bramble": ""})
        with pytest.raises(ValueError, match="non-empty string"):
            AuthValidator(path)

    def test_non_string_token_value_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        path.write_text(json.dumps({"bramble": 123}), encoding="utf-8")
        with pytest.raises(ValueError, match="non-empty string"):
            AuthValidator(path)

    def test_duplicate_token_across_projects_raises(self, tmp_path: Path) -> None:
        path = write_token_file(
            tmp_path / "tokens.json",
            {"bramble": "shared", "elder-berry": "shared"},
        )
        with pytest.raises(ValueError, match="share the same token"):
            AuthValidator(path)

    def test_empty_map_is_allowed(self, tmp_path: Path) -> None:
        # A server with no tokens is misconfigured, not corrupt: it
        # starts, but nothing can authenticate.
        path = write_token_file(tmp_path / "tokens.json", {})
        validator = AuthValidator(path)
        assert validator.project_count == 0


# ---------------------------------------------------------------------------
# authenticate()
# ---------------------------------------------------------------------------
class TestAuthenticate:
    def test_valid_token_resolves_project(self, tokens_file: Path) -> None:
        validator = AuthValidator(tokens_file)
        assert validator.authenticate("tok-bramble", client_ip="1.2.3.4") == "bramble"
        assert (
            validator.authenticate("tok-elder", client_ip="1.2.3.4") == "elder-berry"
        )

    def test_unknown_token_returns_none(self, tokens_file: Path) -> None:
        validator = AuthValidator(tokens_file)
        assert validator.authenticate("wrong", client_ip="1.2.3.4") is None

    def test_none_token_returns_none(self, tokens_file: Path) -> None:
        validator = AuthValidator(tokens_file)
        assert validator.authenticate(None, client_ip="1.2.3.4") is None

    def test_empty_token_returns_none(self, tokens_file: Path) -> None:
        validator = AuthValidator(tokens_file)
        assert validator.authenticate("", client_ip="1.2.3.4") is None

    def test_valid_token_does_not_log_failure(
        self, tokens_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        validator = AuthValidator(tokens_file)
        with caplog.at_level(logging.WARNING, logger="bramble.auth_validator"):
            validator.authenticate("tok-bramble", client_ip="1.2.3.4")
        assert not [r for r in caplog.records if getattr(r, "event", None)]

    def test_failed_auth_logs_auth_failed_event_with_ip(
        self, tokens_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        validator = AuthValidator(tokens_file)
        with caplog.at_level(logging.WARNING, logger="bramble.auth_validator"):
            validator.authenticate("wrong", client_ip="203.0.113.7")

        events = [r for r in caplog.records if getattr(r, "event", None)]
        assert len(events) == 1
        assert events[0].event == AUTH_FAILED_EVENT
        assert events[0].client_ip == "203.0.113.7"


# ---------------------------------------------------------------------------
# Token hashing
# ---------------------------------------------------------------------------
class TestHashing:
    def test_hash_is_stable_and_distinct(self) -> None:
        assert _hash_token("abc") == _hash_token("abc")
        assert _hash_token("abc") != _hash_token("abd")

    def test_cleartext_token_is_not_stored(self, tokens_file: Path) -> None:
        # The validator must keep only hashes, never the raw tokens.
        validator = AuthValidator(tokens_file)
        stored = validator._project_by_hash
        assert "tok-bramble" not in stored
        assert _hash_token("tok-bramble") in stored
