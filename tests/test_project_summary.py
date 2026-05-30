"""Unit tests for :mod:`bramble.project_summary`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from bramble.project_summary import ProjectSummary


# ---------------------------------------------------------------------------
# Construction & happy path
# ---------------------------------------------------------------------------
class TestProjectSummaryConstruction:
    def test_minimal_summary_stores_fields(self) -> None:
        ts = datetime(2026, 5, 12, 9, 0, tzinfo=UTC)
        summary = ProjectSummary(
            name="bramble",
            entry_count=3,
            last_timestamp=ts,
        )
        assert summary.name == "bramble"
        assert summary.entry_count == 3
        assert summary.last_timestamp == ts

    def test_name_is_stripped(self) -> None:
        summary = ProjectSummary(
            name="  bramble  ",
            entry_count=1,
            last_timestamp=datetime.now(tz=UTC),
        )
        assert summary.name == "bramble"

    def test_non_utc_timestamp_is_normalised_to_utc(self) -> None:
        cet = timezone(timedelta(hours=2))
        ts_cet = datetime(2026, 5, 12, 10, 0, tzinfo=cet)
        summary = ProjectSummary(
            name="bramble",
            entry_count=1,
            last_timestamp=ts_cet,
        )
        assert summary.last_timestamp.utcoffset() == timedelta(0)
        assert summary.last_timestamp == ts_cet  # same instant

    def test_last_timestamp_iso_includes_utc_offset(self) -> None:
        ts = datetime(2026, 5, 12, 9, 0, tzinfo=UTC)
        summary = ProjectSummary(name="b", entry_count=1, last_timestamp=ts)
        assert summary.last_timestamp_iso() == "2026-05-12T09:00:00+00:00"

    def test_empty_project_allows_zero_count_and_no_timestamp(self) -> None:
        summary = ProjectSummary(name="empty-project", entry_count=0)
        assert summary.entry_count == 0
        assert summary.last_timestamp is None
        assert summary.last_timestamp_iso() is None

    def test_summary_is_frozen(self) -> None:
        summary = ProjectSummary(
            name="bramble",
            entry_count=1,
            last_timestamp=datetime.now(tz=UTC),
        )
        with pytest.raises(FrozenInstanceError):
            summary.entry_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestProjectSummaryValidation:
    def test_rejects_non_string_name(self) -> None:
        with pytest.raises(TypeError):
            ProjectSummary(
                name=123,  # type: ignore[arg-type]
                entry_count=1,
                last_timestamp=datetime.now(tz=UTC),
            )

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_rejects_blank_name(self, blank: str) -> None:
        with pytest.raises(ValueError):
            ProjectSummary(
                name=blank,
                entry_count=1,
                last_timestamp=datetime.now(tz=UTC),
            )

    def test_rejects_non_int_entry_count(self) -> None:
        with pytest.raises(TypeError):
            ProjectSummary(
                name="b",
                entry_count="3",  # type: ignore[arg-type]
                last_timestamp=datetime.now(tz=UTC),
            )

    def test_rejects_bool_entry_count(self) -> None:
        # bool is a subclass of int; do not silently accept ``True`` as ``1``.
        with pytest.raises(TypeError):
            ProjectSummary(
                name="b",
                entry_count=True,  # type: ignore[arg-type]
                last_timestamp=datetime.now(tz=UTC),
            )

    def test_rejects_negative_entry_count(self) -> None:
        with pytest.raises(ValueError):
            ProjectSummary(
                name="b",
                entry_count=-1,
                last_timestamp=datetime.now(tz=UTC),
            )

    def test_rejects_non_datetime_timestamp(self) -> None:
        with pytest.raises(TypeError):
            ProjectSummary(
                name="b",
                entry_count=1,
                last_timestamp="2026-05-12T09:00:00+00:00",  # type: ignore[arg-type]
            )

    def test_rejects_naive_timestamp(self) -> None:
        with pytest.raises(ValueError):
            ProjectSummary(
                name="b",
                entry_count=1,
                last_timestamp=datetime(2026, 5, 12, 9, 0),
            )
