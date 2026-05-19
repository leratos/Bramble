"""Unit tests for :class:`bramble.rate_limiter.RateLimiter`.

A fake clock is injected so the refill behaviour can be exercised
deterministically without ever sleeping.
"""

from __future__ import annotations

import logging

import pytest

from bramble.rate_limiter import RateLimiter


class FakeClock:
    """Monotonic clock whose advance is fully under test control."""

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


# ---------------------------------------------------------------------------
# Construction & validation
# ---------------------------------------------------------------------------
class TestConstruction:
    @pytest.mark.parametrize("field", ["per_token_rpm", "per_ip_rpm"])
    @pytest.mark.parametrize("bad", [0, -1])
    def test_rejects_non_positive_rpm(self, field: str, bad: int) -> None:
        with pytest.raises(ValueError):
            RateLimiter(**{field: bad})  # type: ignore[arg-type]

    def test_rejects_bool_rpm(self) -> None:
        with pytest.raises(TypeError):
            RateLimiter(per_ip_rpm=True)  # type: ignore[arg-type]

    def test_rejects_non_int_rpm(self) -> None:
        with pytest.raises(TypeError):
            RateLimiter(per_token_rpm=1.5)  # type: ignore[arg-type]

    def test_default_rpm_values(self) -> None:
        # Decision E: 60 per token, 120 per IP. A fresh bucket allows a
        # full-capacity burst, so capacity is observable via behaviour.
        rl = RateLimiter(time_source=FakeClock())
        assert all(rl.allow_project("p") for _ in range(60))
        assert rl.allow_project("p") is False
        assert all(rl.allow_ip("1.2.3.4") for _ in range(120))
        assert rl.allow_ip("1.2.3.4") is False


# ---------------------------------------------------------------------------
# Burst / exhaustion
# ---------------------------------------------------------------------------
class TestBurst:
    def test_burst_up_to_capacity_then_denied(self) -> None:
        rl = RateLimiter(per_token_rpm=3, per_ip_rpm=99, time_source=FakeClock())
        assert [rl.allow_project("bramble") for _ in range(3)] == [True, True, True]
        assert rl.allow_project("bramble") is False

    def test_distinct_projects_have_independent_buckets(self) -> None:
        rl = RateLimiter(per_token_rpm=2, per_ip_rpm=99, time_source=FakeClock())
        assert rl.allow_project("a") and rl.allow_project("a")
        assert rl.allow_project("a") is False
        assert rl.allow_project("b") is True  # untouched

    def test_ip_and_project_pools_are_separate(self) -> None:
        rl = RateLimiter(per_token_rpm=1, per_ip_rpm=1, time_source=FakeClock())
        assert rl.allow_project("x") is True
        assert rl.allow_project("x") is False
        # The same key string addresses a different, fresh IP bucket.
        assert rl.allow_ip("x") is True
        assert rl.allow_ip("x") is False


# ---------------------------------------------------------------------------
# Refill over time
# ---------------------------------------------------------------------------
class TestRefill:
    def test_refills_one_token_per_second_at_60_rpm(self) -> None:
        clock = FakeClock()
        rl = RateLimiter(per_token_rpm=60, per_ip_rpm=99, time_source=clock)
        for _ in range(60):
            assert rl.allow_project("p")
        assert rl.allow_project("p") is False

        clock.advance(1.0)  # 60/60 = 1 token refilled
        assert rl.allow_project("p") is True
        assert rl.allow_project("p") is False

    def test_partial_refill_below_one_token_is_denied(self) -> None:
        clock = FakeClock()
        rl = RateLimiter(per_token_rpm=60, per_ip_rpm=99, time_source=clock)
        for _ in range(60):
            rl.allow_project("p")

        clock.advance(0.5)  # only half a token back
        assert rl.allow_project("p") is False
        clock.advance(0.5)  # now a full token has accrued
        assert rl.allow_project("p") is True

    def test_refill_is_capped_at_capacity(self) -> None:
        clock = FakeClock()
        rl = RateLimiter(per_token_rpm=3, per_ip_rpm=99, time_source=clock)
        for _ in range(3):
            rl.allow_project("p")

        clock.advance(10_000)  # far more than enough to overfill
        assert [rl.allow_project("p") for _ in range(3)] == [True, True, True]
        assert rl.allow_project("p") is False  # capped, not unbounded


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class TestLogging:
    def test_denied_request_logs_rate_limited_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        rl = RateLimiter(per_token_rpm=1, per_ip_rpm=99, time_source=FakeClock())
        rl.allow_project("bramble")  # consumes the only token
        with caplog.at_level(logging.WARNING, logger="bramble.rate_limiter"):
            assert rl.allow_project("bramble") is False

        events = [
            r for r in caplog.records if getattr(r, "event", None) == "rate_limited"
        ]
        assert len(events) == 1
        assert events[0].rate_limit_key == "bramble"
