from __future__ import annotations

import time

from fastapi.testclient import TestClient

from scripts.assistant_stream_browser_probe import (
    ASSISTANT_MESSAGE_ID,
    EXPECTED_ANSWER,
    SESSION_ID,
    TOOL_KEY,
    create_app,
)


def test_probe_runs_model_tool_loop_and_persists_stream_events() -> None:
    with TestClient(create_app()) as client:
        started = client.post("/api/v1/__test__/assistant-stream/start")
        assert started.status_code == 202
        assert started.json() == {"status": "started"}

        deadline = time.monotonic() + 10
        health = client.get("/api/v1/__test__/health").json()
        while health["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.03)
            health = client.get("/api/v1/__test__/health").json()

        assert health["status"] == "completed", health
        assert health["model_invocation_count"] == "2"
        assert health["model_received_completed_tool_result"] == "true"
        assert health["tool_invocation_count"] == "1"
        assert health["tool_query"] == "assistant-stream-browser-probe"
        assert health["tool_result_status"] == "completed"
        assert health["tool_job_status"] == "completed"

        session = client.get(f"/api/v1/sessions/{SESSION_ID}").json()
        assert session["status"] == "completed"
        assistant_messages = [
            message
            for message in session["messages"]
            if message["id"] == ASSISTANT_MESSAGE_ID
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["content"] == EXPECTED_ANSWER

        events = client.get(
            f"/api/v1/sessions/{SESSION_ID}/events/history?limit=500"
        ).json()
        assert any(
            event["type"] == "tool.execution_completed"
            and event["payload"].get("tool_key") == TOOL_KEY
            for event in events
        )
        deltas = [
            event["payload"]["delta"]
            for event in events
            if event["type"] == "assistant.stream.delta"
            and event["payload"].get("message_id") == ASSISTANT_MESSAGE_ID
        ]
        assert "".join(deltas) == EXPECTED_ANSWER

        jobs = client.get(f"/api/v1/sessions/{SESSION_ID}/tool-jobs").json()
        assert len(jobs) == 1
        assert jobs[0]["tool_key"] == TOOL_KEY
        assert jobs[0]["status"] == "completed"
