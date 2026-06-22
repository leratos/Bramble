"""Unit tests for :mod:`bramble.consent_store`."""

from __future__ import annotations

import pytest

from bramble.consent_store import ConsentApprovalStore


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TestConsentApprovalStore:
    def test_approve_then_consume_once(self) -> None:
        store = ConsentApprovalStore(ttl_seconds=120, time_source=_Clock())
        store.approve(session_id="s1", fingerprint="fp")
        assert store.consume(session_id="s1", fingerprint="fp") is True
        # Single-use: a second consume fails.
        assert store.consume(session_id="s1", fingerprint="fp") is False

    def test_consume_missing_is_false(self) -> None:
        store = ConsentApprovalStore()
        assert store.consume(session_id="s1", fingerprint="fp") is False

    def test_wrong_fingerprint_or_session_is_false(self) -> None:
        store = ConsentApprovalStore()
        store.approve(session_id="s1", fingerprint="fp")
        assert store.consume(session_id="s1", fingerprint="other") is False
        assert store.consume(session_id="s2", fingerprint="fp") is False
        # The original is still consumable (the wrong ones did not drop it).
        assert store.consume(session_id="s1", fingerprint="fp") is True

    def test_expired_approval_is_false(self) -> None:
        clock = _Clock()
        store = ConsentApprovalStore(ttl_seconds=60, time_source=clock)
        store.approve(session_id="s1", fingerprint="fp")
        clock.advance(61)
        assert store.consume(session_id="s1", fingerprint="fp") is False

    def test_none_session_is_false(self) -> None:
        store = ConsentApprovalStore()
        assert store.consume(session_id=None, fingerprint="fp") is False

    def test_rejects_bad_ttl(self) -> None:
        with pytest.raises(TypeError):
            ConsentApprovalStore(ttl_seconds=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            ConsentApprovalStore(ttl_seconds=0)

    def test_approve_requires_non_empty(self) -> None:
        store = ConsentApprovalStore()
        with pytest.raises(ValueError):
            store.approve(session_id="", fingerprint="fp")
