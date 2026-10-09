"""Read-only verification of actual report snapshots and immutable evidence."""
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


checked = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json"):
    saved = read(docs / name)
    for item in saved["artifacts"]:
        assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
    checked[name] = len(saved["artifacts"])
perf = read(docs / "p5-perf-evidence-validation.json")
for path, digest in perf["artifact_hashes"].items():
    assert fingerprint(root / path)["sha256"] == digest, path
checked["performance"] = len(perf["artifact_hashes"])
protected = read(docs / "p5-security-evidence-validation.json")["protected_files"]
for item in protected:
    assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
assert b"\r\r\n" not in plans[0].read_bytes()

folder = docs / "p5-security-report-final3"
facts = read(folder / "report-product-facts.json")
assert facts["report_counts"] == {"total_tasks": 3, "completed_tasks": 2, "failed_tasks": 1, "total_findings": 4}
assert facts["report_status"] == "partial" and facts["real_target_gets"] == 2
assert not facts["unplanned_api_failures"] and not facts["page_errors"]
assert facts["api_restart_report_equal"] and facts["browser_download_final_fields_equal"]
assert facts["mode_filter_before_paging"] and facts["pending_report_grade"] == "进行中"
report = read(folder / "report-record.json")["output"]["report"]
browser_report = read(folder / "browser-report-download.json")
original = next(p for p in (folder / "report-originals").glob("*.json"))
saved = read(original)
for field in (*facts["report_counts"], "settlement", "verification_result", "evaluation_result"):
    assert saved[field] == report[field] == browser_report[field], field
assert "部分完成" in (folder / "last-page.txt").read_text(encoding="utf-8")
pages = read(folder / "report-pages.json")
assert pages[0]["has_more"] and not pages[1]["has_more"]
assert {item["session"]["mode_key"] for page in pages for item in page["items"]} == {"code_review", "security_testing", "performance_testing"}
cleanup = read(folder / "container-cleanup.json")
assert len(cleanup) == 3 and all(item["exit_code"] == 0 and not item["stdout"] for item in cleanup)
for item in read(folder / "downloads.json"):
    path = folder / "download-originals" / item["variant"] / item["filename"]
    actual = fingerprint(path)
    assert actual["bytes"] == item["size_bytes"] and actual["sha256"] == item["sha256"]

tests = {}
for name in ("before", "verified", "final", "final2", "final3", "regression", "regression-final", "projection-final", "projection-verified"):
    suite = ET.parse(docs / ("p5-security-report-" + name + ".xml")).getroot().find("testsuite")
    tests[name] = dict(suite.attrib)
assert tests["final3"]["tests"] == "1" and tests["final3"]["failures"] == "0"
assert tests["regression-final"]["tests"] == "183" and tests["regression-final"]["skipped"] == "33" and tests["regression-final"]["failures"] == "0"
assert tests["projection-verified"]["tests"] == "5" and tests["projection-verified"]["failures"] == "0"
assert "14 passed" in (docs / "p5-security-report-frontend-verified.log").read_text(encoding="utf-8")
build = (folder / "product-build.stdout").read_text(encoding="utf-8")
assert "vue-tsc --noEmit" in build and "built in" in build

artifacts = sorted(p for p in docs.glob("p5-security-report-*") if p.is_file() and p.name != "p5-security-report-validation.json")
artifacts += sorted(p for f in docs.glob("p5-security-report-*") if f.is_dir() for p in f.rglob("*") if p.is_file())
sources = ["Agent_Server/src/application/report_service.py", "Agent_Server/src/runtime/store.py", "Agent_Server/src/runtime/postgres_session_store.py",
    "Agent_Server/src/modes/security_testing_mode/runtime.py", "Agent_Server/tests/test_report_service.py", "Agent_Server/tests/test_security_evidence_live.py",
    "Agent_Server/tests/test_security_report_product.py", "agent_web/src/utils/reportPresentation.ts", "agent_web/src/utils/reportPresentation.test.ts",
    "agent_web/src/views/ReportsView.vue", "agent_web/src/locales/zh-CN.ts", "agent_web/src/locales/zh-TW.ts", "agent_web/src/locales/en-US.ts",
    "docs/upgrade_2026-10-03/PLAN.md", "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/.gitattributes", "docs/upgrade_2026-10-03/P5_SECURITY_REPORT_PRODUCT_RESEARCH.md"]
result = {"baseline": "2f404e68", "tests": tests, "frontend": "14 passed", "compileall": "exit0", "actual_build": True,
    "facts": facts, "plans_equal": 3, "previous_artifacts_checked": checked, "protected_files": protected,
    "sources": [fingerprint(root / p) for p in sources], "artifacts": [fingerprint(p) for p in artifacts],
    "full_goal_complete": False, "default_step_deferred": True,
    "not_proven": ["default security Agent/planning and other scanners", "real performance full App/capacity/AI stream",
        "recording knowledge/scenes/aliases/cleaning/omissions", "business quality and production historical migration"]}
(docs / "p5-security-report-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "previous_checked": checked, "plans_equal": 3}))
