"""Read-only audit of this batch and prior immutable evidence."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def fingerprint(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


prior = json.loads((docs / "p1-template-fixed-version-app-validation.json").read_text(encoding="utf-8"))
checked = {}
for name, items in (("template_import", prior["artifacts"]),):
    for item in items:
        assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]
    checked[name] = len(items)
perf = json.loads((docs / "p5-perf-evidence-validation.json").read_text(encoding="utf-8"))
for path, digest in perf["artifact_hashes"].items():
    assert fingerprint(root / path)["sha256"] == digest, path
checked["performance"] = len(perf["artifact_hashes"])
for item in prior["protected_files"]:
    assert fingerprint(root / item["path"])["sha256"] == item["sha256"], item["path"]

plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
assert b"\r\r\n" not in plans[0].read_bytes()

tests = {}
for name in ("before", "reproduction", "verified", "regression", "regression-verified", "regression-final"):
    suite = ET.parse(docs / ("p5-security-evidence-" + name + ".xml")).getroot().find("testsuite")
    tests[name] = dict(suite.attrib)
assert tests["verified"]["failures"] == "0" and tests["verified"]["tests"] == "1"
assert tests["regression-final"]["failures"] == "0" and tests["regression-final"]["tests"] == "111"
facts = json.loads((docs / "p5-security-evidence-verified/facts.json").read_text(encoding="utf-8"))
assert facts["requests"] == 2 and facts["downloads"] == 9
cleanup = json.loads((docs / "p5-security-evidence-verified/container-cleanup.json").read_text(encoding="utf-8"))
assert len(cleanup) == 3 and all(x["exit_code"] == 0 and not x["stdout"] for x in cleanup)
downloads = json.loads((docs / "p5-security-evidence-verified/downloads.json").read_text(encoding="utf-8"))
for variant in ("missing", "complete", "unreachable"):
    folder = docs / "p5-security-evidence-verified"
    record = json.loads((folder / (variant + "-record.json")).read_text(encoding="utf-8"))
    saved = json.loads((folder / (variant + "-persisted-job.json")).read_text(encoding="utf-8"))
    assert saved["output_payload"] == {k: v for k, v in record["output"].items()
        if k not in {"download_urls", "download_markdown"}}
    streams = [a for a in saved["artifacts"] if a["metadata"].get("evidence_schema") == "security-command-output-v1"]
    assert len(streams) == 2
for item in downloads:
    path = docs / "p5-security-evidence-verified/download-originals" / item["variant"] / item["filename"]
    actual = fingerprint(path)
    assert actual["bytes"] == item["size_bytes"] and actual["sha256"] == item["sha256"]

artifacts = sorted(p for p in docs.glob("p5-security-evidence-*") if p.is_file() and p.name != "p5-security-evidence-validation.json")
artifacts += sorted(p for folder in docs.glob("p5-security-evidence-*") if folder.is_dir() for p in folder.rglob("*") if p.is_file())
sources = [root / "Agent_Server/src/application/runtime/tool_runtime_service.py", root / "Agent_Server/tests/test_security_evidence_live.py",
    root / "Agent_Server/tests/test_security_execution_environment_upgrade.py", docs / "PLAN.md", docs / "IMPLEMENTATION.md", docs / "P5_SECURITY_EVIDENCE_RESEARCH.md", docs / ".gitattributes"]
result = {"baseline": "106a1114", "tests": tests, "facts": facts, "compileall": "exit0", "plans_equal": 3,
    "previous_artifacts_checked": checked, "protected_files": prior["protected_files"],
    "sources": [fingerprint(p) for p in sources], "artifacts": [fingerprint(p) for p in artifacts],
    "full_goal_complete": False, "default_step_deferred": True,
    "not_proven": ["default Agent and full App security report", "other scanners and timeout/partial/storage failures",
        "performance Agent/capacity/AI streaming", "full recording knowledge/scenes/cleaning/omissions", "production historical migration"]}
(docs / "p5-security-evidence-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "previous_checked": checked, "plans_equal": 3}))
