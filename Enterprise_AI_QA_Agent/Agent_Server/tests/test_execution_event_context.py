from datetime import datetime, timezone

from src.schemas.session import ExecutionEvent
from src.runtime.streaming import format_sse


def test_execution_event_serializes_harness_context_for_sse():
    event = ExecutionEvent(
        type="run_item.started",
        session_id="session-1",
        timestamp=datetime.now(timezone.utc),
        request_id="req-1",
        trace_id="trace-1",
        turn_id="turn-1",
        run_id="run-1",
        run_item_id="item-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        resource_id="browser-1",
    )
    rendered = format_sse(event)
    assert '"request_id": "req-1"' in rendered
    assert '"resource_id": "browser-1"' in rendered
