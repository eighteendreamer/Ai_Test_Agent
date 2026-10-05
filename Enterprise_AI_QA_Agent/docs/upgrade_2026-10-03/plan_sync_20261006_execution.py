"""Synchronize the requested plan and check existing evidence; no business writes."""
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
PLANS = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
OLD_SHA = "0f3ace496b5bcf9ca2ad043028ad3ea1d8432970d1f0004c1313d41a42b4d657"
IGNORE_SHA = "d604cb2eb1e5da2b92ec113f2e14d05889963ae0fe9492760670f3a1c5c8b241"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify():
    payloads = [path.read_bytes() for path in PLANS]
    assert payloads[0] == payloads[1] == payloads[2], "方案副本内容不同"
    assert sha((ROOT / ".gitignore").read_bytes()) == IGNORE_SHA
    links = re.findall(r"\]\((G:/[^)]+)\)", payloads[0].decode("utf-8"))
    for link in links:
        assert Path(re.sub(r":\d+$", "", link)).is_file(), link
    previous = json.loads((DOCS / "p4-generation-pool-validation.json").read_text("utf-8"))
    reports = list(previous["previous_raw_evidence_verified"])
    reports.append("p4-generation-pool-validation.json")
    counts = {}
    for name in reports:
        report = json.loads((DOCS / name).read_text("utf-8"))
        for entry in report["raw_files"]:
            data = (ROOT / entry["path"]).read_bytes()
            assert len(data) == entry["size_bytes"], entry["path"]
            assert sha(data) == entry["sha256"], entry["path"]
        counts[name] = len(report["raw_files"])
    suites = {}
    for filename in ("p4-generation-pool-components-verified.xml",
                     "p4-generation-pool-live-delivery.xml",
                     "p4-generation-pool-app-navigation.xml",
                     "p4-generation-pool-app-compatibility.xml"):
        root = ET.parse(DOCS / filename).getroot()
        nodes = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        counts_xml = {key: sum(int(node.get(key, "0")) for node in nodes)
                      for key in ("tests", "failures", "errors", "skipped")}
        assert counts_xml["failures"] == counts_xml["errors"] == counts_xml["skipped"] == 0
        suites[filename] = counts_xml
    schema = (ROOT / "Agent_Server/src/schemas/requirements.py").read_text("utf-8")
    assert 'execution_status: Literal["not_assessed"]' in schema
    assert 'deployment_status: Literal["unknown"]' in schema
    routes = (ROOT / "Agent_Server/src/api/routes/requirements.py").read_text("utf-8")
    assert "execution-reviews" not in routes
    return {"research_baseline": "12d2ebd4", "scope": "方案正文及副本更新；未实施执行证据接口",
        "plans_byte_identical": True, "plan_sha256": sha(payloads[0]),
        "local_link_occurrences": len(links), "gitignore_unchanged": True,
        "previous_raw_evidence_verified": counts, "raw_evidence_count": sum(counts.values()),
        "historical_xml_counts": suites, "historical_tests_rerun": False,
        "current_execution_not_assessed_verified": True, "execution_routes_not_connected": True,
        "business_tests_run": False, "production_migration_run": False,
        "full_P0_P5_complete": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    if args.sync:
        data = PLANS[0].read_bytes()
        accepted = {OLD_SHA, sha(data)}
        prior_audit = DOCS / "p4-plan-execution-sync-validation.json"
        if prior_audit.exists():
            accepted.add(json.loads(prior_audit.read_text("utf-8"))["plan_sha256"])
        for target in PLANS[1:]:
            assert sha(target.read_bytes()) in accepted, "旧副本已改变，不能覆盖"
        for target in PLANS[1:]:
            target.write_bytes(data)
    result = verify()
    target = DOCS / "p4-plan-execution-sync-validation.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"plans_byte_identical": True, "raw_evidence_count": result["raw_evidence_count"],
        "historical_xml_counts": result["historical_xml_counts"], "audit": str(target)}, ensure_ascii=False))
