"""Audit the expanded live split-receipt planning evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
root = directory.parents[1]
repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True).strip())

suite = ET.parse(directory / "p4-effect-task-live-current.xml").getroot().find("testsuite")
assert (int(suite.get("tests", "0")), int(suite.get("failures", "0")),
        int(suite.get("skipped", "0"))) == (7, 0, 0)

expected = {"none", "bindings-ack", "produced-ack", "cleanup-response-ack", "budget",
            "unknown-reference", "produced-query"}
observations = {}
for folder in sorted((directory / "p4-effect-task-live-current").iterdir()):
    if not folder.is_dir():
        continue
    assert folder.name in expected, folder.name
    payload = json.loads((folder / "verification.json").read_text(encoding="utf8"))
    assert payload["controlled_model"] is True
    assert payload["business_HTTP"] == 0
    assert payload["terminal_extra_model_calls"] == 0
    observations[folder.name] = {
        "planning_status": payload["actual_PG"]["status"],
        "attempt_no": payload["actual_PG"]["attempt_no"],
        "actual_model_requests": len(payload["actual_requests"]),
        "terminal_extra_model_calls": payload["terminal_extra_model_calls"],
        "business_HTTP": payload["business_HTTP"],
        "sql_rejections": payload["actual_SQL_rejections"],
    }
assert set(observations) == expected

artifacts = [directory / "p4-effect-task-live-current.xml", directory / "p4-effect-task-live-current.log",
             directory / "p4-effect-task-live-current-audit.py"]
artifacts += list((directory / "p4-effect-task-live-current").rglob("*"))
artifacts = [path for path in artifacts if path.is_file()]
validation = {"baseline": "ba7676ce", "full_goal_complete": False,
    "tests": {"total": 7, "passed": 7, "failed": 0, "skipped": 0},
    "scenarios": observations, "isolated_mysql_postgres_rustfs": True,
    "controlled_model": True, "business_http": 0, "external_ai_calls": 0,
    "default_ai_quality_verified": False, "formal_run_execution_verified": False,
    "artifact_hashes": {str(path.relative_to(repo)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in artifacts}}
(directory / "p4-effect-task-live-current-validation.json").write_text(
    json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf8")
print("Expanded seven-way split receipt planning recovery verified; business execution and default AI remain outside scope.")
