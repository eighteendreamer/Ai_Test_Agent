from src.core.request_context import correlation_fields, reset_request_context, set_request_context


def test_context_carries_harness_identifiers():
    token = set_request_context(
        request_id="req-1",
        trace_id="trace-1",
        session_id="session-1",
        turn_id="turn-1",
        run_id="run-1",
        run_item_id="item-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        resource_id="browser-1",
    )
    try:
        assert correlation_fields() == {
            "request_id": "req-1",
            "trace_id": "trace-1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "run_id": "run-1",
            "run_item_id": "item-1",
            "attempt_id": "attempt-1",
            "worker_id": "worker-1",
            "resource_id": "browser-1",
        }
    finally:
        reset_request_context(token)
