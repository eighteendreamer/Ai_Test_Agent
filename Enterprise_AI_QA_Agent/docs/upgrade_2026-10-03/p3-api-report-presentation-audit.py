"""Audit API report projection, redaction and frontend build evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[2]
directory = Path(__file__).resolve().parent
repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True).strip())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


suite = ET.parse(directory / "p3-api-report-presentation-backend-final.xml").getroot().find("testsuite")
assert (int(suite.get("tests", "0")), int(suite.get("failures", "0")), int(suite.get("skipped", "0"))) == (23, 0, 0)
web_log = (directory / "p3-api-report-presentation-web-test.log").read_text(encoding="utf8")
assert "Test Files  1 passed" in web_log and "Tests  8 passed" in web_log
build_log = (directory / "p3-api-report-presentation-web-build.log").read_text(encoding="utf8")
assert "✓ built in" in build_log

previous = {}
for name in subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "537a4aba"], cwd=root, text=True).splitlines():
    if "/p3-api-unknown-reconciliation-" not in name:
        continue
    current = (repo / name).read_bytes()
    assert current == subprocess.check_output(["git", "show", f"537a4aba:{name}"], cwd=root), name
    previous[name] = hashlib.sha256(current).hexdigest()
assert len(previous) > 50

plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({digest(path) for path in plans}) == 1
artifacts = [p for p in directory.glob("p3-api-report-presentation-*") if p.is_file() and p.name != "p3-api-report-presentation-validation.json"]
artifacts += [directory / "P3_API_REPORT_PRESENTATION_RESEARCH.md", root / "Agent_Server/src/application/report_service.py",
              root / "Agent_Server/src/modes/api_testing_mode/report_builder.py", root / "agent_web/src/views/ReportsView.vue"]
report = {"baseline": "537a4aba", "full_goal_complete": False, "backend_tests": {"tests": 23, "failures": 0, "skipped": 0},
    "frontend_tests": {"tests": 8, "failures": 0}, "frontend_build": True, "api_report_mode_projected": True,
    "request_response_redaction_verified": True, "live_pg_rustfs_browser_report_page_verified": False,
    "external_ai_calls": 0, "three_plans_equal": True, "plan_sha256": digest(plans[0]),
    "previous_reconciliation_originals_unchanged": previous,
    "artifact_hashes": {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in artifacts}}
(directory / "p3-api-report-presentation-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
print("API report projection and frontend build verified; prior reconciliation originals unchanged; full goal remains active.")
