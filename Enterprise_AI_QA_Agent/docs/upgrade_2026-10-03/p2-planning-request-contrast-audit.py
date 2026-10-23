"""Verify single-factor physical requests and source immutability; no network."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent
source = root / "p2-default-coverage-live18d"
source_request = source / "physical/21b265d4eb1f42b899dc82d9f2104e00.request.json"
source_bytes = source_request.read_bytes()
wire = json.loads(source_bytes)
audited = []
for name, changed_key in (
    ("p2-produced-native-schema-current2", "response_format"),
    ("p2-produced-path-roots-current", "messages"),
):
    directory = root / name
    origin = json.loads((directory / "source-verification.json").read_bytes())
    assert origin["request_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert origin["checkpoint_sha256"] == hashlib.sha256((source / "PG-planning.json").read_bytes()).hexdigest()
    requests = list((directory / "physical").glob("*.request.json"))
    sdk = list((directory / "physical").glob("*.sdk-create.json"))
    stream = list((directory / "physical").glob("*.sdk-stream.json"))
    assert len(requests) == len(sdk) == len(stream) == 1
    actual = json.loads(requests[0].read_bytes())
    changed = {key for key in wire.keys() | actual.keys() if wire.get(key) != actual.get(key)}
    assert changed == {changed_key}, changed
    assert actual["messages"][1:] == wire["messages"][1:]
    if changed_key == "messages":
        old_system = wire["messages"][0]["content"]
        new_system = actual["messages"][0]["content"]
        prefix, schema = old_system.split("\nJSON Schema: ", 1)
        assert new_system.startswith(prefix + "路径根说明：")
        assert new_system.split("\nJSON Schema: ", 1)[1] == schema
    else:
        assert actual["response_format"]["type"] == "json_schema"
        assert actual["response_format"]["json_schema"]["schema"] == json.loads(
            wire["messages"][0]["content"].split("\nJSON Schema: ", 1)[1])
    public = json.loads((directory / "verification.json").read_bytes())
    observation = json.loads(stream[0].read_bytes())
    response = json.loads(next((directory / "physical").glob("*.response.json")).read_bytes())
    assert response["status_code"] == 200
    assert public["global_model_unchanged"] and public["business_HTTP"] == 0
    assert public["physical_requests"] == 1 and public["text_chars"] == 0
    assert not public["canonical_accepted"] and not public["response_received"]
    assert observation["finish_reason"] is None and observation["error_type"] == "CancelledError"
    assert observation["field_counts"]["content"]["nonempty_events"] == 0
    assert observation["private_contents_saved"] is False
    suites = ET.parse(root / (name + ".xml")).getroot()
    suite = next(suites.iter("testsuite"))
    assert int(suite.attrib["tests"]) == int(suite.attrib["failures"]) == 1
    audited.append({"directory": name, "changed_fields": sorted(changed),
        "source_sha256": origin["request_sha256"], "result": public,
        "observed_usage": observation["numeric_usage"],
        "file_hashes": {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}})

result = {"audited": audited, "status": "verified_failed_contrasts", "production_change": False}
target = root / "p2-planning-request-contrast-audit.json"
with target.open("x", encoding="utf-8") as output:
    json.dump(result, output, ensure_ascii=False, indent=2)
print("Verified two single-factor failed contrasts; source unchanged; model/business replay absent.")
