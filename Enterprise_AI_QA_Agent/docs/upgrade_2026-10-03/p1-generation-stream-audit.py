"""Audit real provider failures, frozen compatibility and owned evidence bytes."""
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_bytes())


def fact(path):
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix(),
        "size_bytes": len(data), "sha256": sha256(data).hexdigest()}


prior = {}
for name in ("p2-default-current", "p2-linked-context", "p2-effect-tasks"):
    previous = read(DOCS / f"{name}-validation.json")
    for item in previous["evidence_files"] + previous["protected_current_files"]:
        path = Path(item["path"])
        if not path.is_absolute():
            path = ROOT / path
        assert fact(path) == item, f"Prior evidence changed: {item['path']}"
    prior[name] = len(previous["evidence_files"])

plans = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
assert len({p.read_bytes() for p in plans}) == 1

expected = {
    "default-current": (1, 1, 0, 0),
    "default-current-verified": (1, 1, 0, 0),
    "default-regression": (27, 0, 0, 15),
    "default-stream": (1, 1, 0, 0),
    "stream-regression": (75, 0, 0, 15),
    "stream-store-regression": (48, 0, 0, 0),
    "stream-final-contract": (7, 0, 0, 0),
    "stream-production": (2, 0, 0, 0),
}
tests = []
for name, counts in expected.items():
    cases = list(ET.parse(DOCS / f"p1-generation-{name}.xml").getroot().iter("testcase"))
    actual = (len(cases), sum(c.find("failure") is not None for c in cases),
        sum(c.find("error") is not None for c in cases), sum(c.find("skipped") is not None for c in cases))
    assert actual == counts, (name, actual)
    tests.append(dict(zip(("batch", "tests", "failures", "errors", "skipped"), (name, *actual))))

trials = []
for name, phase, error in (("default-current-verified", "invoking", "call_outcome_unknown"),
        ("default-stream", "response_saved", "response_validation_rejected")):
    folder = DOCS / f"p1-generation-{name}"
    report = read(folder / "verification.json")
    tasks = read(folder / "PG_case_gen_tasks.json")
    assert sorted(t["status"] for t in tasks) == ["failed", "queued", "queued"]
    failed = next(t for t in tasks if t["status"] == "failed")
    assert failed["checkpoint"]["phase"] == phase and failed["error_code"] == error
    assert not failed["checkpoint"].get("receipt")
    assert report["actual_SDK_calls"] == 1 and report["global_model_unchanged"]
    assert not report["controlled_model"] and not report["module_generation_complete"]
    scope = read(folder / "scope.json")
    assert [s["title"] for s in scope["segments"]] == ["注册", "任务创建", "任务查询"]
    assert "".join(scope["text"][s["start_char"]:s["end_char"]] for s in scope["segments"]) == scope["text"]
    sdk = [read(p) for p in (folder / "physical").glob("*.sdk-create.json")]
    assert len(sdk) == 1
    physical = [p.read_bytes() for p in (folder / "physical").glob("*.request.json")]
    if phase == "invoking":
        assert len(physical) == 2 and physical[0] == physical[1]
        assert sdk[0]["error_type"] == "CancelledError" and not sdk[0]["stream_requested"]
        assert not list((folder / "physical").glob("*.response.json"))
        assert not any("response_transport_protocol" in m["generation"] for m in scope["modules"])
    else:
        assert len(physical) == 1 and sdk[0]["stream_requested"]
        assert all(m["generation"]["response_transport_protocol"] == "case-generation-stream-response-v1" for m in scope["modules"])
        stream = read(next((folder / "physical").glob("*.sdk-stream.json")))
        assert stream["finish_reason"] == "length" and stream["sdk_iteration_finished"]
        assert stream["numeric_usage"]["completion_tokens"] == 8192
        assert stream["field_counts"]["content"]["string_chars"] == 0
        assert not stream["private_contents_saved"] and not stream["response_body_saved"]
        attempt = read(next((folder / "physical").glob("*.attempt.json")))
        assert attempt["response_headers_received"] and attempt["status_code"] == 200
        downloads = read(folder / "downloads.json")
        assert len(downloads) == 2
        for i, ref in enumerate(downloads):
            raw = (folder / f"original-{i}.json").read_bytes()
            assert sha256(raw).hexdigest() == ref["sha256"] and len(raw) == ref["size_bytes"]
        response = read(folder / "original-1.json")
        assert response["text"] == "" and response["response_summary"]["mode"] == "incomplete_response"
        assert not any(k in response["raw_response"] for k in ("reasoning", "reasoning_content", "choices"))
    trials.append({"batch": name, "SDK_calls": 1, "physical_requests": len(physical),
        "phase": phase, "error_code": error, "generated_drafts": 0})

production = []
for relative, streaming in (("", False), ("stream", True)):
    folder = DOCS / "p1-generation-stream-production" / relative
    summary = read(folder / "summary.json")
    assert summary["streaming_transport"] is streaming
    assert [t["status"] for t in summary["report"]["modules"]] == ["completed", "failed", "completed"]
    assert summary["child"]["status"] == "completed" and summary["retry"]["retry"]["strategy"] == "saved_response"
    assert len(summary["physical_http_requests"]) == summary["terminal_replay_model_calls"] == 3
    assert all(bool(r.get("stream")) is streaming for r in summary["physical_http_requests"])
    assert all(v["content_json"]["嵌套"] == {"零": 0, "布尔": False, "空": None} for v in summary["versions"])
    for i, ref in enumerate(summary["downloaded_objects"]):
        raw = (folder / f"object-{i}.json").read_bytes()
        assert sha256(raw).hexdigest() == ref["sha256"] and len(raw) == ref["size_bytes"]
    production.append({"streaming": streaming, "HTTP_calls": 3, "recovery": "saved_response",
        "terminal_extra_HTTP": 0, "draft_versions": len(summary["versions"])})

timeout = DOCS / "p1-generation-stream-timeout"
sdk = [read(p) for p in timeout.glob("*.sdk-create.json")]
attempts = [read(p) for p in timeout.glob("*.attempt.json")]
assert len(sdk) == 1 and len(sdk[0]["physical_requests"]) == len(attempts) == 2
assert sdk[0]["error_type"] == "APITimeoutError"
assert all(a["error_type"] == "ReadTimeout" and not a["response_headers_received"] for a in attempts)
assert not list(timeout.glob("*.response.json"))

paths = []
for pattern in ("p1-generation-default-*", "p1-generation-stream-*"):
    for path in DOCS.glob(pattern):
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(p for p in path.rglob("*") if p.is_file())
paths = sorted(set(p for p in paths if p.name not in {
    "p1-generation-stream-validation.json", "p1-generation-stream-audit.log"}))
result = {"goal_complete": False, "tests": tests, "actual_default_trials": trials,
    "controlled_production": production, "actual_native_timeout_probe": {"SDK_calls": 1, "physical_HTTP": 2, "error": "ReadTimeout"},
    "prior_evidence_unchanged": prior, "protected_current_files": previous["protected_current_files"],
    "plan_copies": [fact(p) for p in plans], "evidence_files": [fact(p) for p in paths],
    "scope": "New frozen document stream and old recovery verified; real default generation still failed; full P0-P5 incomplete"}
(DOCS / "p1-generation-stream-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k not in {"evidence_files", "plan_copies", "protected_current_files"}}, ensure_ascii=False))
print(f"owned evidence files: {len(paths)}")
