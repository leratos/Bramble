"""Time display helpers for the Bramble admin UI."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ZERO = timedelta(0)
_ONE_HOUR = timedelta(hours=1)
_CET_OFFSET = timedelta(hours=1)
_CEST_OFFSET = timedelta(hours=2)


def get_display_timezone(name: str) -> tzinfo:
    """Return a timezone for admin display timestamps."""

    if name in {"UTC", "Etc/UTC"}:
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Europe/Berlin":
            return _EuropeBerlinFallback()
        raise


def format_display_datetime(value: datetime, display_tz: tzinfo) -> str:
    """Format a journal timestamp for compact local admin display."""

    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    timestamp = value
    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    local_timestamp = timestamp.astimezone(display_tz)
    return local_timestamp.strftime("%Y-%m-%d %H:%M %Z")


class _EuropeBerlinFallback(tzinfo):
    """Minimal Europe/Berlin fallback for Windows without tzdata installed."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return _CEST_OFFSET if self._is_dst_local(dt) else _CET_OFFSET

    def dst(self, dt: datetime | None) -> timedelta:
        return _ONE_HOUR if self._is_dst_local(dt) else _ZERO

    def tzname(self, dt: datetime | None) -> str:
        return "CEST" if self._is_dst_local(dt) else "CET"

    def fromutc(self, dt: datetime) -> datetime:
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")

        utc_naive = dt.replace(tzinfo=None)
        if _berlin_dst_active_utc(utc_naive):
            return (dt + _CEST_OFFSET).replace(tzinfo=self)

        local = (dt + _CET_OFFSET).replace(tzinfo=self)
        dst_end = _berlin_dst_end_utc(utc_naive.year)
        if dst_end <= utc_naive < dst_end + _ONE_HOUR:
            local = local.replace(fold=1)
        return local

    def _is_dst_local(self, dt: datetime | None) -> bool:
        if dt is None:
            return False

        local = dt.replace(tzinfo=None)
        start = datetime(local.year, 3, _last_sunday(local.year, 3), 2)
        end = datetime(local.year, 10, _last_sunday(local.year, 10), 3)
        if not start <= local < end:
            return False
        return not (
            local.month == 10
            and local.day == _last_sunday(local.year, 10)
            and local.hour == 2
            and dt.fold == 1
        )


def _berlin_dst_active_utc(utc_naive: datetime) -> bool:
    return (
        _berlin_dst_start_utc(utc_naive.year)
        <= utc_naive
        < _berlin_dst_end_utc(utc_naive.year)
    )


def _berlin_dst_start_utc(year: int) -> datetime:
    return datetime(year, 3, _last_sunday(year, 3), 1)


def _berlin_dst_end_utc(year: int) -> datetime:
    return datetime(year, 10, _last_sunday(year, 10), 1)


def _last_sunday(year: int, month: int) -> int:
    candidate = datetime(year, month, calendar.monthrange(year, month)[1])
    while candidate.weekday() != 6:
        candidate -= timedelta(days=1)
    return candidate.day
