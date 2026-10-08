"""Check actual query bodies, original bytes and the scoped source revision."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-knowledge-validation.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_record(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(raw), "size_bytes": len(raw)}


def run():
    frozen = read(DOCS / "p0-recording-knowledge-source-sha.json")
    for name, item in frozen.items():
        raw = (ROOT / name).read_bytes()
        assert digest(raw) == item["sha256"] and len(raw) == item["size"], name
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    plan = plans[0].read_bytes()
    assert all(path.read_bytes() == plan for path in plans) and b"6.77" in plan
    previous = [read(DOCS / name) for name in ("p0-electron-scopes-validation.json", "p0-recording-lifecycle-validation.json")]
    historical = {item["path"]: item for report in previous for item in report["raw_files"]}
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for name, item in historical.items():
            assert file_record(ROOT / name) == item, name
            process.stdin.write(f"HEAD:{ROOT.name}/{name}\n".encode()); process.stdin.flush()
            header = process.stdout.readline().split()
            assert len(header) == 3 and header[1] == b"blob", name
            raw = process.stdout.read(int(header[2])); assert process.stdout.read(1) == b"\n"
            assert len(raw) == item["size_bytes"] and digest(raw) == item["sha256"], name
    finally:
        process.stdin.close(); assert process.wait(timeout=15) == 0
    protected = previous[-1]["unrelated_current_bytes_unchanged"]
    assert all(digest((ROOT / name).read_bytes()) == expected for name, expected in protected.items())
    runs = []
    for path in sorted(DOCS.glob("p0-recording-knowledge-*.xml")):
        xml = ET.parse(path).getroot()
        suites = [xml] if xml.tag == "testsuite" else xml.findall(".//testsuite")
        totals = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
        runs.append({"path": path.name, **totals})
        if path.name == "p0-recording-knowledge-final.xml":
            assert totals == {"tests": 245, "failures": 0, "errors": 0, "skipped": 1}
        if path.name == "p0-recording-knowledge-counterexamples.xml":
            assert totals == {"tests": 2, "failures": 2, "errors": 0, "skipped": 0}
    actual = []
    for driver, root_path, session_file, events_file in (
        ("embedded", DOCS / "p0-recording-knowledge-final-electron", "pg-session-final.json", "pg-events-final.json"),
        ("python", DOCS / "p0-recording-knowledge-final-python", "pg-session.json", "pg-events.json")):
        for path in sorted(root_path.rglob(session_file)):
            directory = path.parent
            session, events = read(path), read(directory / events_file)
            if session["status"] == "completed":
                knowledge = read(directory / "planning-knowledge.json")[0]
                body = knowledge["payload"]["graph"]
                assert body == read(directory / "projection-http.json")
                actions = [node for node in body["nodes"] if node["kind"] == "action"]
                assert [node["event"] for node in actions] == events
                assert body["event_payloads"] == {"protocol": "recording-event-payloads-v1", "status": "complete",
                    "expected_count": len(events), "available_count": len(events), "warnings": []}
                assert digest(json.dumps(knowledge["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) == knowledge["content_hash"]
                assert knowledge["source_id"] == session["id"]
            else:
                assert directory.name in ("finish-timeout", "identity-conflict") and session["status"] == "failed"
            receipt = session["finalize_metrics"].get("finish_receipt")
            if session["status"] == "completed":
                assert receipt["pg_verified"] and receipt["frames_unconfirmed"] == 0
            screenshot = read(directory / "screenshot-storage.json")
            raw = (directory / "screenshot-rustfs.png").read_bytes()
            assert digest(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"]
            actual.append({"driver": driver if driver == "embedded" else directory.parent.name,
                "scenario": directory.name, "status": session["status"], "pg_events": len(events),
                "full_public_and_planning_payload": session["status"] == "completed"})
    assert len(actual) == 35 and sum(item["full_public_and_planning_payload"] for item in actual) == 33
    files = []
    pattern = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")
    for path in sorted(DOCS.glob("p0-recording-knowledge-*")):
        candidates = [path] if path.is_file() else sorted(child for child in path.rglob("*") if child.is_file())
        for candidate in candidates:
            if candidate == OUTPUT: continue
            raw = candidate.read_bytes()
            if candidate.suffix != ".png": assert not pattern.search(raw), candidate.name
            files.append(file_record(candidate))
    result = {"date": "2026-10-08", "baseline": "5711b37e", "plans_byte_identical": True,
        "plan_sha256": digest(plan), "source_files": len(frozen), "xml_runs": runs, "raw_files": files,
        "prior_current_and_HEAD_bytes_exact": len(historical), "unrelated_current_bytes_unchanged": protected,
        "actual_scenarios": actual, "full_recording_queries_checked": 33,
        "current_default_model_test_skipped": True, "controlled_model_request_contract_verified": True,
        "full_upgrade_complete": False, "full_recording_acceptance": False, "manual_deepseek": False,
        "default_AI_use_of_recording": False, "post_action_response_association": False,
        "full_App": False, "production_migration": False,
        "remaining": ["真实默认AI引用知识并操作、完整响应采集/清洗和人工DeepSeek/App/场景报告",
            "Electron实际缓存恢复、并行attach和更多网络/进程故障",
            "Docker真实结果及P0完整HTTP追踪、P1生成导入产品、P2自主执行/准备生命周期/未知副作用",
            "需求业务评审/环境适用及生产迁移、两份旧Worker原汇总字节仍未恢复"]}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"originals": len(files), "prior_exact": len(historical), "full_queries": 33, "plans_equal": True}))


if __name__ == "__main__":
    run()
