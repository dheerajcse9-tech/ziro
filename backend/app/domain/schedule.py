"""Schedule interval logic.

Intervals are half-open [start, end): a drive ending at 12:00 does not clash
with one starting at 12:00. A configurable buffer (minutes) widens each
interval to model travel/turnaround time between drives. All comparisons are
on timezone-aware UTC instants, so drives configured in different timezones
compare correctly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Interval:
    drive_id: int
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Interval bounds must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("Interval end must be after start")


def overlaps(a: Interval, b: Interval, buffer_minutes: int = 0) -> bool:
    pad = timedelta(minutes=max(buffer_minutes, 0))
    return a.start < b.end + pad and b.start < a.end + pad


def overlap_window(a: Interval, b: Interval) -> tuple[datetime, datetime] | None:
    start, end = max(a.start, b.start), min(a.end, b.end)
    return (start, end) if start < end else None


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone '{name}'") from exc


def to_utc(local: datetime, tz_name: str) -> datetime:
    """Interpret a naive local wall-clock time in tz_name; aware values are converted."""
    tz = validate_timezone(tz_name)
    if local.tzinfo is None:
        local = local.replace(tzinfo=tz)
    return local.astimezone(timezone.utc)


def to_local(value: datetime, tz_name: str) -> datetime:
    return value.astimezone(validate_timezone(tz_name))
