from __future__ import annotations

from datetime import datetime


def elapsed_ms(start: datetime | None, end: datetime) -> int:
    """Return a non-negative millisecond duration for timezone-aware timestamps."""
    if start is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def add_active_duration(
    *,
    active_duration_ms: int,
    active_started_at: datetime | None,
    now: datetime,
) -> int:
    return active_duration_ms + elapsed_ms(active_started_at, now)


def add_waiting_duration(
    *,
    waiting_duration_ms: int,
    waiting_started_at: datetime | None,
    now: datetime,
) -> int:
    return waiting_duration_ms + elapsed_ms(waiting_started_at, now)
