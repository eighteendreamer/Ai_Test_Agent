from datetime import datetime, timedelta, timezone

from src.application.test_runs.timing import (
    add_active_duration,
    add_waiting_duration,
    elapsed_ms,
)
from src.schemas.run_management import (
    TestRunAttemptRecord as RunAttemptRecord,
    TestRunItemRecord as RunItemRecord,
)


def test_elapsed_ms_is_non_negative_and_millisecond_precise() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert elapsed_ms(start, start + timedelta(seconds=1, milliseconds=250)) == 1250
    assert elapsed_ms(start, start - timedelta(seconds=1)) == 0
    assert elapsed_ms(None, start) == 0


def test_timing_fields_are_available_on_persisted_run_records() -> None:
    now = datetime.now(timezone.utc)
    item = RunItemRecord(
        id="item",
        run_id="run",
        case_id="case",
        case_version_id="version",
        position=1,
        created_at=now,
        updated_at=now,
        active_started_at=now,
    )
    attempt = RunAttemptRecord(
        id="attempt",
        run_id="run",
        run_item_id="item",
        attempt_no=1,
        worker_id="worker",
        lease_token="lease",
        claimed_at=now,
        active_started_at=now,
    )
    assert add_active_duration(
        active_duration_ms=item.active_duration_ms,
        active_started_at=item.active_started_at,
        now=now + timedelta(seconds=2),
    ) == 2000
    assert add_waiting_duration(
        waiting_duration_ms=attempt.waiting_duration_ms,
        waiting_started_at=now,
        now=now + timedelta(seconds=3),
    ) == 3000
