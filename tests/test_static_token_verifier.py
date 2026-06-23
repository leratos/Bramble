"""Unit tests for :mod:`bramble.static_token_verifier`."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from bramble.auth_validator import AUTH_FAILED_EVENT, AuthValidator
from bramble.static_token_verifier import (
    STATIC_CLIENT_PREFIX,
    StaticTokenVerifier,
)


@pytest.fixture
def validator(tmp_path: Path) -> AuthValidator:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps({"bramble": "tok-bramble", "elder-berry": "tok-elder"}),
        encoding="utf-8",
    )
    return AuthValidator(path)


class TestConstruction:
    def test_rejects_non_validator(self) -> None:
        with pytest.raises(TypeError):
            StaticTokenVerifier(object())  # type: ignore[arg-type]


class TestVerifyToken:
    async def test_valid_token_maps_to_scoped_access(
        self, validator: AuthValidator
    ) -> None:
        verifier = StaticTokenVerifier(validator)
        access = await verifier.verify_token("tok-bramble")
        assert access is not None
        assert access.token == "tok-bramble"
        assert access.client_id == f"{STATIC_CLIENT_PREFIX}bramble"
        assert "journal:read" in access.scopes
        assert "journal:write" in access.scopes  # static tokens are read-write
        assert access.expires_at is None

    async def test_second_project_maps_to_its_own_client_id(
        self, validator: AuthValidator
    ) -> None:
        verifier = StaticTokenVerifier(validator)
        access = await verifier.verify_token("tok-elder")
        assert access is not None
        assert access.client_id == f"{STATIC_CLIENT_PREFIX}elder-berry"

    @pytest.mark.parametrize("token", ["nope", ""])
    async def test_unknown_token_returns_none(
        self, validator: AuthValidator, token: str
    ) -> None:
        verifier = StaticTokenVerifier(validator)
        assert await verifier.verify_token(token) is None

    async def test_custom_scopes_respected(self, validator: AuthValidator) -> None:
        verifier = StaticTokenVerifier(validator, scopes=("journal:read",))
        access = await verifier.verify_token("tok-bramble")
        assert access is not None
        assert access.scopes == ["journal:read"]

    async def test_miss_is_silent_for_fail2ban(
        self, validator: AuthValidator, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A token this map does not know may be a valid OAuth token tried by
        # MultiAuth; declining it must not emit auth_failed.
        verifier = StaticTokenVerifier(validator)
        with caplog.at_level(logging.WARNING, logger="bramble.auth_validator"):
            assert await verifier.verify_token("an-oauth-access-token") is None
        assert not [
            r for r in caplog.records if getattr(r, "event", None) == AUTH_FAILED_EVENT
        ]
