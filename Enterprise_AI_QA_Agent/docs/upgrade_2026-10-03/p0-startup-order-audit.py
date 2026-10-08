"""Check cold-start evidence, real originals and preserved historical bytes."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Agent_Server"))
from src.schemas.document_parsing import DocumentParseResult


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw), "size_bytes": len(raw)}


def check_original(directory, asset):
    raw = (directory / asset["filename"]).read_bytes()
    assert sha(raw) == asset["sha256"] and len(raw) == asset["size_bytes"]


def run():
    frozen = read(DOCS / "p0-startup-order-source-sha.json")
    for name, item in frozen.items():
        raw = (ROOT / name).read_bytes()
        assert sha(raw) == item["sha256"] and len(raw) == item["size"], name
    plan = (DOCS / "PLAN.md").read_bytes()
    for path in [Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
        Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]:
        assert path.read_bytes() == plan
    assert b"6.79" in plan
    protected = read(DOCS / "p0-recording-response-validation.json")["unrelated_current_bytes_unchanged"]
    assert all(sha((ROOT / name).read_bytes()) == expected for name, expected in protected.items())
    reports = [read(DOCS / name) for name in ["p0-electron-scopes-validation.json",
        "p0-recording-lifecycle-validation.json", "p0-recording-knowledge-validation.json",
        "p0-recording-response-validation.json"]]
    historical = {item["path"]: item for report in reports for item in report["raw_files"]}
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for name, item in historical.items():
            assert record(ROOT / name) == item, name
            process.stdin.write(f"HEAD:{ROOT.name}/{name}\n".encode()); process.stdin.flush()
            header = process.stdout.readline().split()
            assert len(header) == 3 and header[1] == b"blob", name
            raw = process.stdout.read(int(header[2])); assert process.stdout.read(1) == b"\n"
            assert sha(raw) == item["sha256"] and len(raw) == item["size_bytes"], name
    finally:
        process.stdin.close(); assert process.wait(timeout=15) == 0
    expected_runs = {
        "production-first": (1, 1, 0), "production-fixed": (1, 0, 0),
        "execution-first": (1, 0, 0), "regression-first": (80, 24, 2),
        "regression-configured": (65, 0, 0), "claim-fixed": (15, 0, 0),
    }
    runs = []
    for suffix, expected in expected_runs.items():
        suite = ET.parse(DOCS / f"p0-startup-order-{suffix}.xml").getroot().find("testsuite")
        totals = {key: int(suite.attrib.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}
        assert (totals["tests"], totals["failures"], totals["skipped"]) == expected
        assert totals["errors"] == 0
        runs.append({"run": suffix, **totals, "junit_seconds": float(suite.attrib["time"])})
    folder = DOCS / "p0-startup-order-production-fixed"
    startup = read(folder / "startup-prerequisites.json")
    assert startup["tables_absent_before_api"] and startup["restart_preserved_prerequisites"]
    assert startup["first"] == startup["restarted"]
    keys = startup["first"]["foreign_keys"]
    assert {k["referenced_table"] for k in keys} == {"agent_test_run_items", "agent_tool_jobs"}
    assert all(k["convalidated"] for k in keys)
    assert startup["first"]["capture_config"]["payload"]["protocol"] == "recording-dom-response-v1"
    for name in ("document-api-first.stderr", "document-api-restarted.stderr"):
        log = (folder / name).read_text(encoding="utf-8")
        assert log.index("lifespan_run_schema_initialized") < log.index("lifespan_tool_jobs_initialized")
        assert "Application startup complete" in log
    parsed = read(folder / "summary.json")
    assert parsed["task"]["status"] == "completed" and parsed["model_calls"] == 0
    assert parsed["terminal_replay_no_reparse"] and parsed["request_replayed_same_task"]
    snap = parsed["snapshot"]["snapshot"]
    check_original(folder, snap["seed"]["original_asset"])
    raw_result = DocumentParseResult.model_validate(parsed["snapshot"]["result"]).model_dump_json().encode("utf-8")
    assert len(raw_result) == snap["size_bytes"] and sha(raw_result) == snap["sha256"]
    # This is a reconstruction checked against the actual stored object hash;
    # its raw bytes were not separately captured during the live run.
    assert parsed["frozen_timeout"] != parsed["current_timeout"]
    execution = read(DOCS / "p0-startup-order-execution-first/p2-execution-worker-cli.json")
    http = read(DOCS / "p0-startup-order-execution-first/http.json")
    assert execution["actual_effects"] == http["effects"]
    assert [e[0] for e in http["effects"]] == ["POST", "GET", "DELETE"]
    assert len(http["calls"]) == 5 and not http["errors"]
    assert execution["terminal_replay_effects"] == execution["business_records_remaining"] == 0
    assert execution["approval_survived_api_and_worker_restart"] and execution["confirmed_create_survived_api_restart"]
    phase = []
    for path in sorted((DOCS / "p0-startup-order-consumption-phase").rglob("verification.json")):
        item = read(path)
        check_original(path.parent, item["original"]["original_asset"])
        phase.append({"scenario": item["scenario"], "path": path.relative_to(ROOT).as_posix()})
    assert len(phase) == 15
    external = []
    for directory in sorted((DOCS / "p0-startup-order-consumption-external").iterdir()):
        path = directory / "final.json"
        if not path.exists():
            # An explicit passing dependency intentionally stops before the
            # second consumer when the first failed; this is not missing output.
            path = directory / "left-result.json"
            assert directory.name in {"formal-bound-failed", "formal-explicit-failed", "legacy-formal-failed"}
        item = read(path)
        for source in item["originals"]:
            check_original(path.parent, source["original_asset"])
        assert item["controlled_agent"] and not item["default_autonomous_model"]
        if path.name == "left-result.json":
            assert item["right_claim_count"] == 0 and not item["closures"]
            assert not any(method == "DELETE" for method, _ in item["actual_HTTP"])
        external.append({"scenario": item["scenario"], "path": path.relative_to(ROOT).as_posix()})
    assert len(external) == 9
    files = sorted(p for p in DOCS.glob("p0-startup-order-*") if p.is_file() and p.name != "p0-startup-order-validation.json")
    files += sorted(p for directory in DOCS.glob("p0-startup-order-*") if directory.is_dir() for p in directory.rglob("*") if p.is_file())
    suspicious = []
    for path in files:
        if path.suffix not in {".json", ".log", ".stdout", ".stderr", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r'\bsk-[A-Za-z0-9_-]{16,}|"api_key"\s*:\s*"(?!\*|<|redacted|masked)[^"]{16,}"', text):
            suspicious.append(path.name)
    assert not suspicious, suspicious
    report = {"date": "2026-10-08", "baseline": "7497b469", "plans_byte_identical": True,
        "plan_sha256": sha(plan), "source_files": frozen, "xml_runs": runs,
        "raw_files": [record(p) for p in files], "prior_current_and_HEAD_bytes_exact": len(historical),
        "unrelated_current_bytes_unchanged": protected, "validated_startup": startup,
        "actual_docx_source_and_snapshot_hash_checked": True,
        "snapshot_result_raw_not_separately_captured": True, "actual_execution_HTTP_calls": len(http["calls"]),
        "phase_scenarios": phase, "external_scenarios": external, "default_autonomous_model": False,
        "full_upgrade_complete": False, "frontend_retested": False,
        "remaining": ["录制AI实际使用、清洗、人工DeepSeek及完整App", "Docker性能/安全及其余P0—P5",
            "生产历史迁移、两份旧Worker汇总原字节恢复"]}
    (DOCS / "p0-startup-order-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"new_raw_files": len(files), "historical_byte_checks": len(historical),
        "phase_scenarios": len(phase), "external_scenarios": len(external), "plans_equal": True}))


if __name__ == "__main__":
    run()
