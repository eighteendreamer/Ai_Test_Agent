"""Audit the current import product run without reinterpreting earlier results."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
evidence = root / "p1-case-import-full-product-current2"
facts = json.loads((evidence / "facts.json").read_text(encoding="utf-8"))
assert facts["api_restart_persisted"] is True
assert facts["page_errors"] == []
assert facts["unplanned_api_errors"] == []
assert facts["cross_project_source_status"] == 404
assert len(facts["imports"]) == 4
assert facts["template_change_after_activation"]["case_unchanged"] is True
assert facts["template_change_after_activation"]["versions_unchanged"] is True
hashes = json.loads((evidence / "original-hashes.json").read_text(encoding="utf-8"))
for filename, digest in hashes.items():
    assert hashlib.sha256((evidence / filename).read_bytes()).hexdigest() == digest
files = sorted(path for path in evidence.rglob("*") if path.is_file())
result = {
    "baseline": "05688163",
    "full_goal_complete": False,
    "production_source_changed": False,
    "external_ai_calls": 0,
    "classification": "existing implementation; current full product rerun passed",
    "tests": [
        {"module": "test_case_import_service", "result": "6 passed, 8 deselected in 10.70s", "live": True},
        {"module": "test_case_import_browser", "result": "3 passed in 12.74s", "live": True},
        {"module": "test_case_import_full_product", "result": "1 passed in 66.89s", "live": True},
    ],
    "raw_pytest_log_saved": False,
    "result_source": "terminal tool outputs in this conversation; product raw artifacts saved separately",
    "artifact_hashes": {path.relative_to(evidence).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
}
(root / "p1-current2-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Audited {len(files)} product artifacts; originals, template invariants and restart checked.")
