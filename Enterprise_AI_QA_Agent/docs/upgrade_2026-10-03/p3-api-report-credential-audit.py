"""Audit saved observations; does not rerun or substitute for product tests."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf8"))


expected = {
    "p3-api-report-credential-before.xml": (5, 5, 0),
    "p3-api-report-credential-final.xml": (17, 0, 1),
    "p3-api-cookie-report-product.xml": (1, 1, 0),
    "p3-api-cookie-report-product-verified.xml": (1, 1, 0),
    "p3-api-cookie-report-product-final.xml": (1, 0, 0),
    "p3-api-cookie-report-product-restart.xml": (1, 0, 0),
    "p3-api-report-credential-regression.xml": (350, 1, 8),
    "p3-api-report-credential-regression-final.xml": (350, 0, 8),
    "p3-api-report-credential-finalization-before.xml": (8, 2, 0),
    "p3-api-report-credential-delivery.xml": (350, 0, 8),
    "p3-api-cookie-report-product-delivery.xml": (1, 0, 0),
}
test_runs = {}
for filename, counts in expected.items():
    suite = ET.parse(root/filename).getroot().find("testsuite")
    actual = tuple(int(suite.get(field, "0")) for field in ("tests", "failures", "skipped"))
    assert actual == counts and int(suite.get("errors", "0")) == 0, filename
    test_runs[filename] = {"tests": actual[0], "failures": actual[1], "skipped": actual[2],
        "passed": actual[0]-actual[1]-actual[2], "time_seconds": float(suite.get("time")),
        "skipped_nodes": [node.get("classname")+"::"+node.get("name")
            for node in suite.findall("testcase") if node.find("skipped") is not None]}

for path in sorted((root/"p3-api-report-credential-delivery").glob("runtime-*.json")):
    facts = read_json(path)
    assert all(facts[key] for key in ("report_safe", "artifacts_safe", "markdown_safe",
        "raw_execution_response_preserved", "execution_objects_unchanged", "final_evaluation_safe"))
    assert facts["report_extra_http"] == 0 and len(facts["events"]) == 4
for limit in (500, 2000, 4000):
    facts = read_json(root/"p3-api-report-credential-delivery"/f"failed-preview-{limit}.json")
    assert facts["public_formats_safe_including_partial_preview"] and facts["original_execution_unchanged"]

product = root/"p3-api-cookie-report-product-delivery"
facts = read_json(product/"verification.json")
assert facts["report_counts"] == {"total": 3, "passed": 2, "failed": 1}
assert all(facts[key] for key in ("public_job_and_artifacts_safe", "download_matches_stored_bytes",
    "restart_report_entry_and_download_equal"))
assert facts["page_errors"] == [] and facts["terminal_replay_extra_http"] == 0 and len(facts["events"]) == 4
start, end = facts["planned_restart_network_range"]
assert facts["unplanned_api_failures"] == []
assert not [request for index, request in enumerate(facts["network"])
    if request["status"] >= 400 and not start <= index < end]
for artifact in facts["artifacts"]:
    content = (product/artifact["saved_filename"]).read_bytes()
    assert len(content) == artifact["size_bytes"] and hashlib.sha256(content).hexdigest() == artifact["sha256"]
    assert artifact["uri"] == "inline://artifact"
    if artifact["type"] == "api_test_report_json":
        assert content == (product/"browser-report-download.json").read_bytes()
report = read_json(product/"api_test_report_json.json")
profile = next(task for task in report["tasks"] if task["task_id"] == "profile")
assert profile["status"] == "failed" and profile["response"]["body"]["opaque_echo"] == "[REDACTED]"
assert profile["checks"][0]["actual"] == 200 and profile["checks"][0]["expected"] == 201
storage = read_json(root/"p3-api-report-credential-delivery-storage"/"verification.json")
assert storage["public_output_no_plain_cookie_values"] and storage["encrypted_rows_without_plain_values"]
assert storage["terminal_extra_http"] == 0
paths = [root/filename for filename in expected]
paths += [root/filename.replace(".xml", ".log") for filename in expected]
paths += [path for directory in ("p3-api-report-credential-before", "p3-api-report-credential-final",
    "p3-api-cookie-report-product-final", "p3-api-cookie-report-product-restart",
    "p3-api-report-credential-cookie-storage", "p3-api-report-credential-finalization-before",
    "p3-api-report-credential-delivery", "p3-api-report-credential-delivery-storage",
    "p3-api-cookie-report-product-delivery")
    for path in (root/directory).rglob("*") if path.is_file()]
manifest = [{"path": str(path.relative_to(root)).replace("\\", "/"), "bytes": path.stat().st_size,
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(paths)]
result = {"test_runs": test_runs, "evidence_files": manifest,
    "scope": "saved HTTP/store/browser/download/restart observations; generated credentials intentionally not exported",
    "full_P0_P5_complete": False}
(root/"p3-api-report-credential-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(f"Evidence audit passed: {len(test_runs)} test runs, {len(manifest)} files; full goal remains incomplete.")
