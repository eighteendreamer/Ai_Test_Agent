"""Opt-in HTTP acceptance with the database model and actual PostgreSQL/RustFS.

Run in the isolated C4 environment with RUN_LIVE_DEEP_AGENTS_HITL=1.
Creates labelled acceptance sessions and keeps all evidence; never deletes data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEEP_AGENTS_HITL") != "1",
    reason="set RUN_LIVE_DEEP_AGENTS_HITL=1 for actual model/HTTP/PostgreSQL acceptance",
)


@pytest.fixture
def live_server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ)
    env.update({
        "DEEP_AGENTS__ENABLED": "true",
        "DEEP_AGENTS__GOVERNED_TOOLS_ENABLED": "true",
        "DEEP_AGENTS__CHECKPOINT_ENABLED": "true",
        "DEEP_AGENTS__READ_ONLY_FILESYSTEM_ENABLED": "false",
        "DEEP_AGENTS__COGNITIVE_SUBAGENTS_ENABLED": "false",
        "DEEP_AGENTS__COGNITIVE_PLANNING_ENABLED": "false",
        "DEEP_AGENTS__PILOT_MODE_KEYS": '["code_review"]',
    })
    processes = []
    handles = []
    logs = []
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=120)

    def start():
        log = tmp_path / f"server-{len(processes)}.log"
        handle = log.open("wb")
        handles.append(handle)
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=Path(__file__).resolve().parents[1], env=env,
            stdout=handle, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        processes.append(process)
        for _ in range(90):
            if process.poll() is not None:
                pytest.fail(log.read_text(encoding="utf-8", errors="replace"))
            try:
                response = client.get("/health", timeout=2)
                if response.status_code == 200:
                    assert response.json()["postgres_ok"]
                    return process
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        pytest.fail("Uvicorn did not become healthy")

    def restart():
        processes[-1].terminate()
        processes[-1].wait(timeout=15)
        return start()

    try:
        start()
        yield client, restart
    except BaseException:
        for log in logs:
            print(log.read_text(encoding="utf-8", errors="replace"))
        raise
    finally:
        client.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        for handle in handles:
            handle.close()


def _get(client, path):
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _post(client, path, payload):
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


@pytest.mark.parametrize("decision", ["approved", "denied"])
def test_live_official_hitl_survives_process_restart(live_server, decision):
    client, restart = live_server
    marker = "DA_E4_DURABLE_HTTP_OK"
    session = _post(client, "/sessions", {
        "title": f"DA-E4 official HITL {decision} restart acceptance",
        "mode_key": "code_review", "selected_agent": "coordinator",
    })
    sid = session["id"]
    pending = _post(client, f"/sessions/{sid}/messages", {
        "content": (
            f"For this acceptance test call cli-executor exactly once with command "
            f"'Write-Output {marker}' and shell 'powershell'. "
            "Use only that tool. After its result, summarize briefly; do not retry a rejected call."
        ),
        "agent_key": "coordinator", "mode_key": "code_review",
        "context": {"requested_tool_keys": ["cli-executor"]},
    })
    assert pending["session"]["status"] == "waiting_approval", sid
    approvals = pending["session"]["pending_approvals"]
    assert len(approvals) == 1, sid
    approval = approvals[0]
    assert approval["tool_key"] == "cli-executor"
    assert approval["metadata"]["arguments"]["command"] == f"Write-Output {marker}"
    assert approval["metadata"]["deep_agent_interrupt_id"]
    jobs = _get(client, f"/sessions/{sid}/tool-jobs")
    assert len(jobs) == 1 and jobs[0]["status"] == "waiting_approval"
    assert jobs[0]["started_at"] is None
    restart()
    assert _get(client, f"/sessions/{sid}")["status"] == "waiting_approval"
    _post(client, f"/sessions/{sid}/approvals/{approval['id']}", {
        "decision": decision, "reason": "Acceptance decision after process restart.",
    })
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        detail = _get(client, f"/sessions/{sid}")
        if detail["status"] not in {"running", "waiting_approval"}:
            break
        time.sleep(0.5)
    assert detail["status"] == "completed", (sid, detail["control_state"])
    jobs = _get(client, f"/sessions/{sid}/tool-jobs")
    assert len(jobs) == 1 and jobs[0]["id"] == approval["metadata"]["tool_job_id"]
    assert jobs[0]["status"] == ("completed" if decision == "approved" else "denied")
    if decision == "approved":
        assert jobs[0]["output_payload"]["stdout"].strip() == marker
        assert jobs[0]["output_payload"]["exit_code"] == 0
    else:
        assert jobs[0]["started_at"] is None
    artifacts = _get(client, f"/sessions/{sid}/artifacts")
    assert len(artifacts) == (1 if decision == "approved" else 0)
    snapshots = _get(client, f"/sessions/{sid}/snapshots")
    assert [item["stage"] for item in snapshots] == ["waiting_approval", "completed"]
    events = _get(client, f"/sessions/{sid}/events/history")
    correlations = [item["payload"]["correlation_id"] for item in events if item["payload"].get("correlation_id")]
    assert len(correlations) == len(set(correlations)), sid
    _post(client, f"/sessions/{sid}/approvals/{approval['id']}", {
        "decision": decision, "reason": "Repeat the same decision without re-execution.",
    })
    assert _get(client, f"/sessions/{sid}")["status"] == "completed"
    assert len(_get(client, f"/sessions/{sid}/tool-jobs")) == 1
    print(json.dumps({
        "session_id": sid, "model": next(
            (item["metadata"].get("model_key") for item in reversed(detail["messages"])
             if item["role"] == "assistant"), None,
        ),
        "decision": decision, "status": detail["status"], "job_id": jobs[0]["id"],
        "artifacts": len(artifacts), "events": len(events),
        "trace_runs": [item["payload"]["run_id"] for item in events if item["type"] == "observability.trace_linked"],
    }, ensure_ascii=False))


def test_live_safe_business_tool_completes_without_approval(live_server):
    client, _restart = live_server
    session = _post(client, "/sessions", {
        "title": "DA-E4 safe tool acceptance", "mode_key": "code_review",
        "selected_agent": "coordinator",
    })
    sid = session["id"]
    result = _post(client, f"/sessions/{sid}/messages", {
        "content": "Call session-history once to retrieve the recent messages of THIS session. Then summarize briefly. Do not call other tools.",
        "agent_key": "coordinator", "mode_key": "code_review",
        "context": {"requested_tool_keys": ["session-history"]},
    })
    assert result["session"]["status"] == "completed", sid
    assert result["session"]["pending_approvals"] == []
    jobs = _get(client, f"/sessions/{sid}/tool-jobs")
    # The provider can request list_questions and history_summary separately.
    # Check execution identity, not a natural-language promise of one model call.
    assert jobs and all(item["tool_key"] == "session-history" for item in jobs)
    assert all(item["status"] == "completed" for item in jobs)
    assert len({item["call_id"] for item in jobs}) == len(jobs)
    assert any(item["role"] == "tool" for item in result["session"]["messages"])
    print(json.dumps({"safe_session_id": sid, "status": "completed", "job_ids": [item["id"] for item in jobs]}))
