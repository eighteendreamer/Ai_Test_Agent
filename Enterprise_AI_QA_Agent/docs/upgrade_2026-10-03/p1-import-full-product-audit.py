"""Validate actual import product evidence while preserving previous deliveries."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    history = {}
    protected = {}
    for name in ("p0-receipt-transport-validation.json", "plan-receipt-current-validation.json"):
        prior = json.loads((DOCS / name).read_bytes())
        for relative, expected in prior["artifact_hashes"].items():
            assert sha(ROOT / relative) == expected, relative
        history[name] = len(prior["artifact_hashes"])
        for relative, expected in prior.get("protected_files_sha256", {}).items():
            assert sha(ROOT / relative) == expected, relative
            protected[relative] = expected
        if name == "p0-receipt-transport-validation.json":
            for relative, expected in prior.get("current_source_hashes", {}).items():
                assert sha(ROOT / relative) == expected, relative
    plans = [DOCS / "PLAN.md",
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
    assert "### 6.87 " in plans[0].read_text(encoding="utf-8")
    tests = {}
    for suffix in ("first", "second", "third", "verified", "final", "complete", "delivery"):
        suite = ET.parse(DOCS / f"p1-import-full-product-{suffix}.xml").getroot().find("testsuite")
        failed = int(suite.get("failures")) + int(suite.get("errors"))
        skipped = int(suite.get("skipped"))
        passed = int(suite.get("tests")) - failed - skipped
        assert (passed, failed, skipped) == ((1, 0, 0) if suffix == "delivery" else (0, 1, 0))
        tests[suffix] = {"passed": passed, "failed": failed, "skipped": skipped, "seconds": float(suite.get("time"))}
    evidence = DOCS / "p1-import-full-product-delivery"
    facts = json.loads((evidence / "facts.json").read_bytes())
    assert facts["api_restart_persisted"] and not facts["page_errors"] and not facts["unplanned_api_errors"]
    assert facts["model_execution_requested"] is False and facts["cross_project_source_status"] == 404
    assert facts["template_saved"]["scope_key"] == facts["project"]["id"]
    before, after = facts["active_before"], facts["case_after_new_draft"]
    assert before["active_version_id"] == after["active_version_id"] and after["latest_version"] == 2
    versions = facts["versions_after"]
    assert all(old in versions for old in facts["versions_before"])
    assert all(version["content_json"]["test_data"] == {"count": 0, "flag": False, "nullable": None} for version in versions)
    assert facts["conflict"]["conflicting_case_keys"] == ["product-rows"]
    assert len(facts["imports"]) == 4
    assert [item["committed"]["batch"]["status"] for item in facts["imports"][:3]] == ["committed"] * 3
    invalid = facts["imports"][-1]["preview"]
    assert len(invalid["batch"]["preview"]["candidates"]) == 1 and not invalid["conflicting_case_keys"]
    assert any(issue["row"] == 2 and issue["severity"] == "error" for issue in invalid["batch"]["preview"]["issues"])
    database = json.loads((evidence / "postgres-finally.json").read_bytes())
    assert len(database["postgres_test_case_table"]) == 3
    assert len(database["postgres_test_case_version_table"]) == 4
    hashes = json.loads((evidence / "original-hashes.json").read_bytes())
    for relative, expected in hashes.items():
        assert sha(evidence / relative) == expected
    artifacts = sorted(path for path in DOCS.rglob("*") if path.is_file()
        and path.relative_to(DOCS).parts[0].startswith("p1-import-full-product-")
        and path.name not in {"p1-import-full-product-audit.py", "p1-import-full-product-validation.json"})
    result = {"baseline": "97ac33b6", "scope": "built product template, Excel/JSON import and version governance",
        "full_P0_P5_complete": False, "real_model_acceptance_deferred": True,
        "tests": tests, "plans_equal": True, "plan_sha256": sha(plans[0]),
        "historical_artifacts_unchanged": history, "protected_files_sha256": protected,
        "current_source_hashes": {"Agent_Server/tests/test_case_import_full_product.py": sha(ROOT / "Agent_Server/tests/test_case_import_full_product.py")},
        "artifact_hashes": {path.relative_to(ROOT).as_posix(): sha(path) for path in artifacts}}
    (DOCS / "p1-import-full-product-validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": tests, "artifacts": len(artifacts), "historical": history, "plans_equal": True}))


if __name__ == "__main__":
    main()
