"""Audit physical SDK output, graph routing and durable recovery evidence."""
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()}


counts = {}
for name, tests, failures, skipped in [
    ("before", 6, 6, 0), ("verified", 6, 0, 0), ("contract", 93, 0, 0),
    ("limit-before", 5, 5, 0), ("limit-before2", 5, 5, 0), ("tool-before", 2, 2, 0),
    ("final", 248, 0, 48), ("delivery", 252, 0, 48), ("trace-final", 24, 0, 0),
    ("recovery", 3, 0, 0), ("recovery-final", 3, 0, 0), ("storage", 6, 0, 0),
]:
    suite = ET.parse(docs / f"p0-model-stream-terminal-{name}.xml").getroot().find("testsuite")
    assert suite is not None
    assert (int(suite.attrib["tests"]), int(suite.attrib["failures"]), int(suite.attrib["skipped"]),
        int(suite.attrib["errors"])) == (tests, failures, skipped, 0), (name, suite.attrib)
    counts[name] = suite.attrib
assert "compileall_exit=0" in (docs / "p0-model-stream-terminal-compile-final.log").read_text()

stream_facts = []
for folder in sorted((docs / "p0-model-stream-terminal-trace-final").iterdir()):
    response = read(folder / "sdk-result.json")
    saved = read(folder / "facts.json")
    graph = read(folder / "graph-facts.json")
    normal = folder.name.endswith("-tool-complete")
    limited = folder.name.endswith("-budget-limited")
    summary = response["response_summary"]
    status = "tool_calls" if normal else "truncated" if limited else "incomplete"
    assert summary["completion_status"] == status
    assert summary["mode"] == ("ok" if normal else "incomplete_response")
    assert summary["stream_terminal_received"] == (normal or limited)
    assert graph["route"] == ("tool_executor" if normal else "finalizer")
    assert saved["physical_sends"] == graph["physical_sends"] == 1
    assert saved["recovery_new_sends"] == 0 and saved["terminal"]["plan"]["status"] == "planning_failed"
    if not normal:
        assert graph["termination_reason"] == "interrupted"
        event = next(event for event in graph["events"] if event["type"] == "model.invocation_failed")
        assert event["payload"]["completion_status"] == status
        assert not any(event["type"] == "graph.execution_completed" for event in graph["events"])
    if "-tool-" in folder.name:
        assert len(response["tool_calls"]) == len(graph["retained_model_tool_calls"]) == 1
    stream_facts.append({"variant": folder.name, "mode": summary["mode"], "completion_status": status,
        "stream_terminal_received": summary["stream_terminal_received"], "route": graph["route"],
        "physical_sends": 1, "actual_tool_execution_verified": False})
assert len(stream_facts) == 15
for folder in (docs / "p0-model-stream-terminal-limit-before2").iterdir():
    assert read(folder / "graph-facts.json")["termination_reason"] == "assistant_final"
for folder in (docs / "p0-model-stream-terminal-tool-before").iterdir():
    assert read(folder / "graph-facts.json")["route"] == "tool_executor"

controls = []
for folder in (docs / "p0-model-stream-terminal-controls-delivery").iterdir():
    if folder.name == "anthropic-explicit-zero":
        response = read(folder / "sdk-result.json")
        assert response["response_summary"]["usage"]["input_tokens"] == 0
        continue
    facts = read(folder / "result.json")
    summary = facts["response"]["response_summary"]
    limited = folder.name.endswith("-limited")
    streaming = "-stream-" in folder.name
    assert summary["mode"] == ("incomplete_response" if limited else "ok")
    assert summary["completion_status"] == ("truncated" if limited else "complete")
    assert facts["physical_sends"] == 1
    assert ("stream_terminal_received" in summary) == streaming
    if streaming:
        assert summary["stream_terminal_received"] is True
    controls.append(folder.name)
assert len(controls) == 20

recovery = {}
for kind in ("native", "stream", "missing-terminal"):
    facts = read(docs / f"p0-model-stream-terminal-recovery-final/{kind}/verification.json")
    for key in ("response_committed_ack_lost", "recovery_no_new_model_call", "original_deadline_preserved",
                "terminal_no_new_calls", "source_bytes_read_back", "usage_replayed_from_failure"):
        assert facts[key], (kind, key)
    assert facts["physical_http_sends"] == 2 and facts["actual_PG"]["status"] == "failed"
    if kind == "missing-terminal":
        summary = facts["actual_PG"]["checkpoint"]["response_metadata"]
        assert summary["mode"] == "incomplete_response" and summary["stream_terminal_received"] is False
        assert facts["actual_plan"]["rounds"][-1]["response_disposition"] == "incomplete"
    recovery[kind] = {key: value for key, value in facts.items() if not isinstance(value, (dict, list))}

generation_folder = docs / "p0-model-stream-terminal-generation/guards"
generation = read(generation_folder / "verification.json")
assert generation["actual_generation_http_calls"] == 1 and generation["guard_send_count"] == 0
assert not generation["drafts_committed"]
for name, expected in generation["files"].items():
    actual = digest(generation_folder / name)
    assert actual["sha256"] == expected["sha256"] and actual["bytes"] == expected["size_bytes"]
impact = {}
for name, status in [("length", "truncated"), ("empty", "empty"), ("tool", "tool_calls"),
                     ("http_failure", "provider_failure"), ("large", None)]:
    facts = read(docs / f"p0-model-stream-terminal-impact/calls/response-{name}.json")
    assert facts["record"]["response_disposition"] == status
    assert facts["logical_calls"] == 1 and not facts["model_candidates_validated"]
    assert facts["actual_http_attempts"] == (3 if name == "http_failure" else 1)
    impact[name] = {"status": status, "logical_calls": 1, "actual_http_attempts": facts["actual_http_attempts"]}

plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
assert b"\r\r\n" not in plans[0].read_bytes()
previous = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json",
             "p5-security-report-validation.json", "p5-perf-report-validation.json",
             "p5-recording-knowledge-validation.json", "p5-recording-overview-validation.json",
             "p0-model-completion-validation.json"):
    items = read(docs / name)["artifacts"]
    for item in items:
        assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]
    previous[name] = len(items)
perf = read(docs / "p5-perf-evidence-validation.json")
for path, expected in perf["artifact_hashes"].items():
    assert digest(root / path)["sha256"] == expected, path
previous["p5-perf-evidence-validation.json"] = len(perf["artifact_hashes"])
protected = read(docs / "p0-model-completion-validation.json")["protected_files"]
for item in protected:
    assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]

artifacts = sorted({path for item in docs.glob("p0-model-stream-terminal-*")
    for path in ([item] if item.is_file() else item.rglob("*"))
    if path.is_file() and path.name != "p0-model-stream-terminal-validation.json"})
sources = ["Agent_Server/src/application/model_clients/openai_client.py",
    "Agent_Server/src/application/model_clients/openai_responses_client.py",
    "Agent_Server/src/application/model_clients/anthropic_client.py", "Agent_Server/src/application/model_clients/google_client.py",
    "Agent_Server/src/application/model_clients/completion.py", "Agent_Server/src/application/models/model_runtime_service.py",
    "Agent_Server/src/application/model_adapters/langchain_model_adapter.py",
    "Agent_Server/src/application/model_adapters/legacy_model_adapter.py",
    "Agent_Server/src/application/model_adapters/message_adapter.py", "Agent_Server/src/schemas/model_config.py",
    "Agent_Server/src/graph/nodes/model_invoker.py", "Agent_Server/src/application/documents/impact_calls.py",
    "Agent_Server/src/application/test_cases/planning_workflow.py", "Agent_Server/src/application/test_cases/generation_pipeline.py",
    "Agent_Server/src/application/test_runs/agent_case_assessment.py", "Agent_Server/tests/test_model_completion_http.py",
    "Agent_Server/tests/test_model_stream_terminal_http.py", "Agent_Server/tests/test_planning_response_completion.py",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P0_MODEL_STREAM_TERMINAL_RESEARCH.md", "docs/upgrade_2026-10-03/.gitattributes"]
patterns = [re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"Authorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._-]{20,}", re.I)]
for path in artifacts + [root / name for name in sources]:
    assert not any(pattern.search(path.read_bytes()) for pattern in patterns), path
result = {"baseline": "443f5af7", "junit": counts, "actual_streams": stream_facts,
    "actual_sync_stream_controls": controls, "recovery": recovery, "impact": impact,
    "generation_guard_send_count": generation["guard_send_count"], "compileall": "exit0",
    "plans_equal": 3, "previous_artifacts_checked": previous, "protected_files": protected,
    "sources": [digest(root / name) for name in sources], "artifacts": [digest(path) for path in artifacts],
    "secret_pattern_file_hits": 0, "full_goal_complete": False,
    "not_proven": ["request profile/JSON/reasoning/media capability governance", "external multi-model services",
        "default Agent/full App", "new unknown terminal semantics/Deep Agents pilot", "actual control tool execution",
        "actual process kill", "manual DeepSeek/recording business management/cleaning omissions",
        "API renewal/performance capacity and AI stream/other security tools", "preparations/unknown effects/revisions",
        "business requirement review/production history migration/all-phase physical HTTP"]}
(docs / "p0-model-stream-terminal-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "delivery": counts["delivery"], "trace_final": counts["trace-final"],
    "plans_equal": 3, "previous_artifacts_checked": previous}))
