"""Check original lifecycle evidence, fixed source and exact historical bytes."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-lifecycle-validation.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_record(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw), "size_bytes": len(raw)}


def run():
    frozen = read(DOCS / "p0-recording-lifecycle-final-source-sha.json")
    for name, record in frozen.items():
        raw = (ROOT / name).read_bytes()
        assert sha(raw) == record["sha256"] and len(raw) == record["size"], name
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    plan = plans[0].read_bytes()
    assert all(path.read_bytes() == plan for path in plans)
    assert b"6.76" in plan and b"P0" in plan and b"P5" in plan
    prior = read(DOCS / "p0-electron-scopes-validation.json")
    for name, expected in prior["unrelated_current_bytes_unchanged"].items():
        assert sha((ROOT / name).read_bytes()) == expected, name
    historical = prior["raw_files"]
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for item in historical:
            current = file_record(ROOT / item["path"])
            assert current == item, item["path"]
            process.stdin.write(f'HEAD:{ROOT.name}/{item["path"]}\n'.encode())
            process.stdin.flush()
            header = process.stdout.readline().split()
            assert len(header) == 3 and header[1] == b"blob", item["path"]
            original = process.stdout.read(int(header[2]))
            assert process.stdout.read(1) == b"\n"
            assert len(original) == item["size_bytes"] and sha(original) == item["sha256"], item["path"]
    finally:
        process.stdin.close()
        assert process.wait(timeout=15) == 0

    xml_runs = []
    for path in sorted(DOCS.glob("p0-recording-lifecycle-*.xml")):
        element = ET.parse(path).getroot()
        suites = element.findall(".//testsuite") if element.tag != "testsuite" else [element]
        totals = {name: sum(int(suite.attrib.get(name, 0)) for suite in suites)
            for name in ("tests", "failures", "errors", "skipped")}
        xml_runs.append({"path": path.name, **totals})
        if path.name == "p0-recording-lifecycle-final.xml":
            assert totals == {"tests": 211, "failures": 0, "errors": 0, "skipped": 0}
        if path.name == "p0-recording-lifecycle-web-verified.xml":
            assert totals == {"tests": 36, "failures": 0, "errors": 0, "skipped": 0}
    assert any(row["failures"] for row in xml_runs), "original failures must remain"
    assert "built in" in (DOCS / "p0-recording-lifecycle-build.log").read_text(encoding="utf-8")

    actual = []
    for directory in sorted((DOCS / "p0-recording-lifecycle-final-electron").iterdir()):
        session = read(directory / "pg-session-final.json")
        events = read(directory / "pg-events-final.json")
        expected_status = "failed" if directory.name in ("identity-conflict", "finish-timeout") else "completed"
        assert session["status"] == expected_status
        if expected_status == "completed":
            receipt = session["finalize_metrics"]["finish_receipt"]
            assert receipt["pg_verified"] and receipt["frames_unconfirmed"] == 0 and receipt["transport_dropped"] == 0
            assert [event["seq"] for event in events] == list(range(len(events)))
            bindings = [json.loads(line) for line in (directory / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
            native_contexts = [json.loads(line) for line in (directory / "native-contexts.jsonl").read_text(encoding="utf-8").splitlines()]
            native_headers = [json.loads(line) for line in (directory / "native-binding.jsonl").read_text(encoding="utf-8").splitlines()]
            native_frames = {(row["web_contents_id"], row["session_id"], row["params"]["context"]["id"]): row["params"]["context"]["auxData"]["frameId"]
                for row in native_contexts if row["method"] == "Runtime.executionContextCreated" and row["params"]["context"].get("auxData", {}).get("isDefault")}
            for event in events:
                scope = event["page"]["recording_scope"]
                original = next(row for row in bindings if row["recording_document_id"] == scope["document_id"] and row["seq"] == scope["local_seq"])
                assert {key: value for key, value in event["page"].items() if key != "recording_scope"} == original["page"]
                target = original["target"]
                if target and scope["parent_frame_id"]:
                    target = {**target, "in_iframe": True}
                assert event["target"] == target
                for field in ("type", "pixel", "value", "page_effect"):
                    assert event[field] == original[field]
                header = next(row for row in native_headers if json.loads(row["envelope"]["payload"]).get("recording_document_id") == scope["document_id"]
                    and json.loads(row["envelope"]["payload"]).get("seq") == scope["local_seq"])
                frame = native_frames[(header["web_contents_id"], header["session_id"], header["execution_context_id"])]
                assert scope["frame_id"].endswith(":" + frame)
            assert all(row["confirmed"] and row["pg_verified"] and row["received_count"] == row["next_seq"] for row in receipt["scopes"])
            for target in read(directory / "scope-targets.json")["expected"]:
                assert any((event["target"] or {}).get("locators", {}).get("id") == target for event in events)
            if directory.name == "frame-remove":
                assert any(event["type"] == "fill" and event["value"] == "跨来源嵌套最后输入" for event in events)
            if directory.name == "popup-close":
                assert any(event["type"] == "fill" and event["value"] == "弹窗最后输入" for event in events)
        screenshot = read(directory / "screenshot-storage.json")
        raw = (directory / "screenshot-rustfs.png").read_bytes()
        assert sha(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"]
        assert screenshot["uri"].startswith("rustfs://")
        actual.append({"driver": "embedded", "scenario": directory.name, "status": expected_status, "pg_events": len(events)})
    assert len(actual) == 15

    python_count = 0
    for path in sorted((DOCS / "p0-recording-lifecycle-final-python").rglob("pg-session.json")):
        directory = path.parent
        session, events, receipt = read(path), read(directory / "pg-events.json"), read(directory / "receipt.json")
        assert session["status"] == "completed" and receipt["pg_verified"] and receipt["frames_unconfirmed"] == 0
        assert [event["seq"] for event in events] == list(range(len(events)))
        verification = read(directory / "verification.json")
        assert all(verification["actual_target_ids"].count(target) >= verification["expected_target_ids"].count(target) for target in verification["expected_target_ids"])
        assert all(value in verification["actual_values"] for value in verification["expected_values"])
        assert all(scope["pg_verified"] and scope["received_count"] == scope["next_seq"] for scope in receipt["scopes"])
        cache_restored = directory.name.startswith("cache-back")
        if cache_restored:
            states = read(directory / "states.json")
            assert any(row["type"] == "pageshow" and row["persisted"] is True for row in states[-1]["lifecycle"])
            root_ids = {event["page"]["recording_scope"]["document_id"] for event in events if (event["target"] or {}).get("locators", {}).get("id") == "root"}
            assert len(root_ids) == 1
        if directory.name == "ready-start-gap":
            gap = read(directory / "ready-start-gap.json")
            assert gap == {"pg_status": "ready", "runtime_status": "launching", "http_start_done_before_ready_reply": False}
            assert read(directory / "ready-start-response.json")["status"] == 200
        screenshot = read(directory / "screenshot-storage.json")
        raw = (directory / "screenshot-rustfs.png").read_bytes()
        assert sha(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"]
        actual.append({"driver": directory.parent.name, "scenario": directory.name, "status": "completed", "pg_events": len(events), "actual_BFCache": cache_restored})
        python_count += 1
    assert python_count == 20
    assert sum(row.get("actual_BFCache", False) for row in actual) == 2

    raw_files = []
    credential_pattern = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")
    for path in sorted(DOCS.glob("p0-recording-lifecycle-*")):
        candidates = [path] if path.is_file() else sorted(child for child in path.rglob("*") if child.is_file())
        for candidate in candidates:
            if candidate == OUTPUT:
                continue
            raw = candidate.read_bytes()
            if candidate.suffix != ".png":
                assert not credential_pattern.search(raw), candidate.name
            raw_files.append(file_record(candidate))
    result = {"date": "2026-10-08", "baseline": "77ab0e0", "plans_byte_identical": True,
        "plan_sha256": sha(plan), "frozen_source_files": len(frozen), "raw_files": raw_files, "xml_runs": xml_runs,
        "prior_original_HEAD_and_current_bytes_exact": len(historical),
        "unrelated_current_bytes_unchanged": prior["unrelated_current_bytes_unchanged"], "actual_scenarios": actual,
        "actual_Electron_scenarios": 15, "actual_Python_storage_scenarios": python_count,
        "native_source_and_business_fields_checked": True, "full_Chromium_BFCache_storage": True,
        "ready_commit_control_serialization": True, "full_upgrade_complete": False,
        "full_recording_acceptance": False, "manual_deepseek": False, "model_use": False,
        "full_App": False, "full_knowledge_originals_for_AI": False,
        "remaining": ["Electron真实缓存恢复、并行attach及更多网络/进程故障", "完整响应关联、清洗/知识原文、AI使用、人工DeepSeek/完整App/场景报告", "Docker真实性能/安全与P0完整HTTP追踪", "P1生成/导入产品、P2自主执行与未知状态、业务评审/环境证据", "生产历史迁移及两份旧Worker汇总原字节未恢复"]}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"raw_files": len(raw_files), "prior_exact": len(historical), "scenarios": len(actual), "plans_equal": True}, ensure_ascii=True))


if __name__ == "__main__":
    run()
