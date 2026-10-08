"""Audit preserved originals, full response reads and frozen project policy."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-response-validation.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw), "size_bytes": len(raw)}


def run():
    frozen = read(DOCS / "p0-recording-response-source-sha.json")
    for name, item in frozen.items():
        raw = (ROOT / name).read_bytes()
        assert len(raw) == item["size"] and sha(raw) == item["sha256"], name
    initial = read(DOCS / "p0-recording-response-initial-source-sha.json")
    original = (DOCS / "p0-recording-response-before-frame-annotation.js").read_bytes()
    assert sha(original) == initial["Agent_Server/src/application/recorder/assets/recorder.js"]["sha256"]
    plan = (DOCS / "PLAN.md").read_bytes()
    for path in [Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
        Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]:
        assert path.read_bytes() == plan
    assert b"6.78" in plan
    previous = [read(DOCS / name) for name in ["p0-electron-scopes-validation.json", "p0-recording-lifecycle-validation.json", "p0-recording-knowledge-validation.json"]]
    historical = {item["path"]: item for report in previous for item in report["raw_files"]}
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for name, item in historical.items():
            assert record(ROOT / name) == item, name
            process.stdin.write(f"HEAD:{ROOT.name}/{name}\n".encode()); process.stdin.flush()
            header = process.stdout.readline().split()
            assert len(header) == 3 and header[1] == b"blob", name
            raw = process.stdout.read(int(header[2])); assert process.stdout.read(1) == b"\n"
            assert len(raw) == item["size_bytes"] and sha(raw) == item["sha256"], name
    finally:
        process.stdin.close(); assert process.wait(timeout=15) == 0
    protected = previous[-1]["unrelated_current_bytes_unchanged"]
    assert all(sha((ROOT / name).read_bytes()) == expected for name, expected in protected.items())
    results = []
    for path in sorted(DOCS.glob("p0-recording-response-*.xml")):
        xml = ET.parse(path).getroot()
        suites = [xml] if xml.tag == "testsuite" else xml.findall(".//testsuite")
        totals = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
        results.append({"file": path.name, **totals})
        if path.name in ("p0-recording-response-final.xml", "p0-recording-response-verified.xml"):
            assert totals["failures"] == totals["errors"] == 0 and totals["skipped"] == 1
        if path.name in ("p0-recording-response-js-final.xml", "p0-recording-response-js-verified.xml"):
            assert totals == {"tests": 46, "failures": 0, "errors": 0, "skipped": 0}
        if path.name == "p0-recording-response-reproduction.xml":
            assert totals == {"tests": 2, "failures": 2, "errors": 0, "skipped": 0}
    actual, responses = [], []
    for driver, folder, session_name, event_name in [
        ("embedded", "p0-recording-response-verified-electron", "pg-session-final.json", "pg-events-final.json"),
        ("python", "p0-recording-response-verified-python", "pg-session.json", "pg-events.json")]:
        for path in sorted((DOCS / folder).rglob(session_name)):
            directory = path.parent
            session, events = read(path), read(directory / event_name)
            assert "capture_settings" in session["metadata"]
            if session["status"] == "completed":
                knowledge = read(directory / "planning-knowledge.json")[0]
                body = knowledge["payload"]["graph"]
                assert body == read(directory / "projection-http.json")
                actions = [node for node in body["nodes"] if node["kind"] == "action"]
                assert [node["event"] for node in actions] == events
                assert body["event_payloads"]["status"] == "complete"
                assert sha(json.dumps(knowledge["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) == knowledge["content_hash"]
                receipt = session["finalize_metrics"]["finish_receipt"]
                assert receipt["pg_verified"] and receipt["frames_unconfirmed"] == 0
            else:
                assert directory.name in ("finish-timeout", "identity-conflict") and session["status"] == "failed"
            for event in events:
                if event["type"] != "page_response": continue
                effect = event["page_effect"]["response"]
                scope = event["page"]["recording_scope"]
                related = [item for item in events if item["page"]["recording_scope"]["document_id"] == scope["document_id"]
                    and item["page"]["recording_scope"]["local_seq"] in effect["action_local_seqs"]]
                assert len(related) == len(set(effect["action_local_seqs"]))
                assert related and all(item["seq"] < event["seq"] for item in related)
                assert effect["association"] == "temporal_after_actions" and effect["scope"] == "document_light_dom"
                assert len(effect["elements"]) <= effect["max_nodes"]
                assert all(item["target"]["in_iframe"] == bool(scope["frame_path"]) for item in effect["elements"] if "target" in item)
                assert all(len(item.get("text", "")) <= effect["max_text_chars"] for item in effect["elements"])
                assert effect["truncated"] == bool(effect["truncation_reasons"])
                responses.append({"driver": driver if driver == "embedded" else directory.parent.name,
                    "scenario": directory.name, "seq": event["seq"], "scope": scope,
                    "actions": [item["seq"] for item in related], "action_types": [item["type"] for item in related],
                    "status": effect["observation_status"], "reason": effect["reason"], "truncated": effect["truncated"]})
            screenshot = read(directory / "screenshot-storage.json")
            raw = (directory / "screenshot-rustfs.png").read_bytes()
            assert sha(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"]
            actual.append({"driver": driver if driver == "embedded" else directory.parent.name,
                "scenario": directory.name, "status": session["status"], "pg_events": len(events),
                "full_public_and_planning_payload": session["status"] == "completed"})
    assert len(actual) == 46 and sum(item["full_public_and_planning_payload"] for item in actual) == 44
    for driver in ["cdp-attach", "playwright-managed"]:
        assert any(row["driver"] == driver and row["scenario"] == "response-input" and row["action_types"] == ["fill"] for row in responses)
        assert any(row["driver"] == driver and row["scenario"] == "response-stop" and row["reason"] == "stop" for row in responses)
        assert any(row["driver"] == driver and row["scenario"] == "response-limit" and row["truncated"] for row in responses)
        assert any(row["driver"] == driver and row["scenario"] == "response-none" and row["status"] == "no_changes_observed" for row in responses)
        assert any(row["driver"] == driver and len(row["scope"]["frame_path"]) == 2 for row in responses)
    assert any(row["driver"] == "embedded" and row["scenario"] == "response-dom" for row in responses)
    assert any(row["driver"] == "embedded" and len(row["scope"]["frame_path"]) == 2 for row in responses)
    for mode in ["live", "offline"]:
        policy = read(DOCS / "p0-recording-response-verified-policy" / mode / "verification.json")
        assert policy["original_session"]["metadata"]["capture_settings"] == policy["pg_readback"]["metadata"]["capture_settings"]
        requests = policy["script_requests"]
        assert requests[0] == requests[-1] and requests[0]["payload"] != requests[1]["payload"]
        assert policy["actual_mysql_postgres"] == (mode == "live")
    files = []
    pattern = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")
    for path in sorted(DOCS.glob("p0-recording-response-*")):
        candidates = [path] if path.is_file() else sorted(child for child in path.rglob("*") if child.is_file())
        for candidate in candidates:
            if candidate == OUTPUT: continue
            raw = candidate.read_bytes()
            if candidate.suffix != ".png": assert not pattern.search(raw), candidate.name
            files.append(record(candidate))
    result = {"date": "2026-10-08", "baseline": "091e4200", "plans_byte_identical": True, "plan_sha256": sha(plan),
        "source_files": len(frozen), "xml_runs": results, "raw_files": files,
        "prior_current_and_HEAD_bytes_exact": len(historical), "unrelated_current_bytes_unchanged": protected,
        "actual_scenarios": actual, "full_recording_queries_checked": 44, "response_observations": responses,
        "mysql_policy_pg_freeze_and_script_isolation": True, "full_upgrade_complete": False,
        "first_electron_connection_failure_root_cause": "not established; first failure retained; traced rerun and full regressions separately recorded",
        "production_startup": "failed before recorder initialization: consumption FK references run_items before table creation; needs separate initialization-order repair",
        "full_recording_acceptance": False, "manual_deepseek": False, "default_AI_use_of_recording": False,
        "full_App": False, "production_migration": False,
        "remaining": ["默认AI实际引用响应并操作、清洗和人工DeepSeek/App/场景报告", "网络/跨文档响应、Shadow DOM及Electron真实缓存/并行/故障",
            "Docker真实结果、P0完整HTTP追踪、P1生成导入产品、P2自主执行/准备生命周期/未知副作用", "需求业务评审/环境适用及生产迁移、旧两Worker原汇总字节恢复"]}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"originals": len(files), "prior_exact": len(historical), "full_queries": 44, "responses": len(responses), "plans_equal": True}))


if __name__ == "__main__":
    run()
