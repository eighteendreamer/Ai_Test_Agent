"""Read-only check of report attempts, actual downloads and prior evidence."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


previous = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json", "p5-security-report-validation.json"):
    items = read(docs / name)["artifacts"]
    for item in items:
        assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
    previous[name] = len(items)
perf = read(docs / "p5-perf-evidence-validation.json")
for path, digest in perf["artifact_hashes"].items():
    assert fingerprint(root / path)["sha256"] == digest, path
previous["performance"] = len(perf["artifact_hashes"])
protected = read(docs / "p5-security-evidence-validation.json")["protected_files"]
for item in protected:
    assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
assert b"\r\r\n" not in plans[0].read_bytes()

before = read(docs / "p5-perf-report-before/report-product-observations.json")
assert before[1]["stage"] == "missing" and before[1]["browser_grade"] == "良好"
assert before[1]["expected_job_id"] != before[1]["meta"]["tool_job_id"]
folder = docs / "p5-perf-report-final2"
facts = read(folder / "report-product-facts.json")
assert not facts["unplanned_api_failures"] and not facts["page_errors"]
assert facts["restart_preserved_latest"] and facts["browser_download_metrics_equal"]
assert facts["waiting_cancel_added_engine_runs"] == 0
observed = read(folder / "report-product-observations.json")
assert len(observed) == 9
for item in observed:
    assert item["expected_job_id"] == item["meta"]["tool_job_id"], item["stage"]
    if item["stage"] == "normal":
        assert item["browser_grade"] == "良好" and item["meta"]["verdict"] == "pass"
    elif item["stage"] in {"missing", "malformed", "teardown"}:
        assert item["browser_grade"] == "较差" and not item["meta"]["ready"]
        assert "pass" not in item["browser_body"]
    elif item["stage"] in {"load-inconclusive", "storage-inconclusive", "restarted"}:
        assert item["browser_grade"] == "需关注" and item["meta"]["verdict"] == "inconclusive"
        name = "storage-inconclusive" if item["stage"] == "restarted" else item["stage"]
        record = read(folder / (name + "-tool-record.json"))
        browser = read(folder / (item["stage"] + "-browser-metrics.json"))
        assert browser["metrics"] == record["output"]["metrics"]
        assert browser["verdict"] == record["output"]["verdict"]
engine_facts = read(folder / "facts.json")
assert engine_facts["cross_project_download_status"] == 404 and engine_facts["tampered_same_size_status"] == 409
assert engine_facts["restored_bytes_equal"] and engine_facts["replay_added_engine_runs"] == 0
downloads = read(folder / "downloads.json")
for item in downloads:
    saved = fingerprint(folder / item["saved_relative_path"])
    assert saved["sha256"] == item["sha256"] and saved["bytes"] == item["bytes"]
commands = read(folder / "docker-commands.json")
created = [c["stdout"].strip() for c in commands if c["args"][0] == "create" and c["exit_code"] == 0]
assert len(created) == 8
for identity in created:
    absent = [c for c in commands if c["args"][0] == "ps" and "id=" + identity in c["args"]]
    assert absent and all(c["exit_code"] == 0 and not c["stdout"].strip() for c in absent)
assert "历史执行" in (folder / "last-page.txt").read_text(encoding="utf-8")
build = (folder / "product-build.stdout").read_text(encoding="utf-8")
assert "vue-tsc --noEmit" in build and "built in" in build
security = read(docs / "p5-perf-report-security-regression/report-product-facts.json")
assert security["report_counts"] == {"total_tasks": 3, "completed_tasks": 2, "failed_tasks": 1, "total_findings": 4}
assert not security["page_errors"] and not security["unplanned_api_failures"]

tests = {}
for name in ("before", "verified", "final", "final2", "contract", "regression", "regression-final", "security-regression"):
    suite = ET.parse(docs / ("p5-perf-report-" + name + ".xml")).getroot().find("testsuite")
    tests[name] = dict(suite.attrib)
assert tests["before"]["failures"] == "1" and tests["regression"]["errors"] == "1"
assert tests["final2"]["tests"] == "1" and tests["final2"]["failures"] == "0"
assert tests["regression-final"]["tests"] == "90" and tests["regression-final"]["skipped"] == "6"
assert tests["regression-final"]["failures"] == "0" and tests["security-regression"]["failures"] == "0"
assert "11 passed" in (docs / "p5-perf-report-frontend.log").read_text(encoding="utf-8")
artifacts = sorted(p for p in docs.glob("p5-perf-report-*") if p.is_file() and p.name != "p5-perf-report-validation.json")
artifacts += sorted(p for f in docs.glob("p5-perf-report-*") if f.is_dir() for p in f.rglob("*") if p.is_file())
sources = ["Agent_Server/src/application/report_service.py", "Agent_Server/src/application/runtime/tool_job_service.py",
    "Agent_Server/src/runtime/tool_job_store.py", "Agent_Server/src/runtime/postgres_tool_job_store.py",
    "Agent_Server/tests/test_report_service.py", "Agent_Server/tests/test_performance_evidence_live.py", "Agent_Server/tests/test_performance_report_product.py",
    "agent_web/src/utils/reportPresentation.ts", "agent_web/src/utils/reportPresentation.test.ts", "agent_web/src/views/ReportsView.vue",
    "agent_web/src/locales/zh-CN.ts", "agent_web/src/locales/zh-TW.ts", "agent_web/src/locales/en-US.ts",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/P5_PERFORMANCE_REPORT_PRODUCT_RESEARCH.md"]
result = {"baseline": "653e8e6d", "tests": tests, "frontend": "11 passed in 2 files", "compileall": "exit0",
    "actual_build": True, "facts": facts, "engine_facts": engine_facts, "original_downloads": len(downloads),
    "owned_containers_absent": len(created), "plans_equal": 3, "previous_artifacts_checked": previous,
    "protected_files": protected, "sources": [fingerprint(root / p) for p in sources], "artifacts": [fingerprint(p) for p in artifacts],
    "full_goal_complete": False, "default_step_deferred": True,
    "not_proven": ["default Agent/planning", "capacity lease reuse and AI stream", "other security tools/fault combinations",
        "recording business/knowledge/scenes/aliases/cleaning/omissions", "API renewal", "business quality and production historical migration"],
    "migration_collection_error": "current virtual environment lacks alembic.config; no DDL changed; actual report PG query tested"}
(docs / "p5-perf-report-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "downloads": len(downloads), "previous_checked": previous, "plans_equal": 3}))
