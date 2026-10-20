"""Project actual requests, stream counters and terminal PG evidence without secrets."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[1]


def read(path):
    return json.loads(path.read_bytes())


def summarize(name):
    directory = EVIDENCE / name
    verification = read(directory / "verification.json")
    task = read(directory / "planning-task.json")
    pg = read(directory / "PG-planning.json")
    assert task["status"] in {"completed", "failed", "cancelled"}
    rounds = pg["checkpoint"]["plan"]["rounds"]
    requests = []
    for path in sorted((directory / "physical").glob("*.request.json")):
        payload = read(path)
        messages = payload.get("messages", [])
        context = json.loads(messages[-1]["content"]) if messages else {}
        stream_path = path.with_name(path.name.replace(".request.json", ".sdk-stream.json"))
        stream = read(stream_path) if stream_path.exists() else None
        entry = {"request_file": path.name, "round": context.get("round"),
                 "stage": context.get("planning_stage"), "task": context.get("evidence_task"),
                 "max_tokens": payload.get("max_tokens"),
                 "format": payload.get("response_format", {}).get("type"),
                 "reasoning_effort": payload.get("reasoning_effort")}
        if stream:
            assert stream["private_contents_saved"] is False
            assert stream["response_body_saved"] is False
            entry.update(finish_reason=stream.get("finish_reason"), usage=stream.get("numeric_usage"),
                         elapsed_seconds=stream.get("elapsed_seconds"),
                         content_chars=stream["field_counts"]["content"]["string_chars"],
                         reasoning_chars=stream["field_counts"]["reasoning"]["string_chars"])
        requests.append(entry)
    prompts = pg["seed"]["stage_prompts"]
    return {
        "task_status": task["status"], "task_message": task["message"],
        "planning_completed": verification["planning_completed"],
        "business_execution_completed": verification["business_execution_completed"],
        "actual_business_HTTP": verification["actual_business_HTTP"],
        "isolated_profile_override": verification["isolated_profile_override"],
        "isolated_max_output_tokens_override": verification["isolated_max_output_tokens_override"],
        "task_prompt_revision": prompts["task_prompt_revision"],
        "binding_prompt_chars": len(prompts["effect_tasks"]["bindings"]),
        "logical_items": [{key: item.get(key) for key in ("key", "kind", "case_id", "depends_on")}
                          for item in pg["checkpoint"]["stages"]["logical_decision"]["items"]],
        "accepted_bindings": [item for receipt in pg["checkpoint"]["effect_tasks"]["completed"]
                              if receipt["task"] == "bindings" for item in receipt["bindings"]],
        "rounds": [{key: item.get(key) for key in
                    ("round", "status", "planning_stage", "evidence_task", "validation_error")}
                   for item in rounds],
        "requests": sorted(requests, key=lambda item: (item["round"] or 100, item["request_file"])),
        "physical_request_count": len(list((directory / "physical").glob("*.send.json"))),
        "artifacts": {str(path.relative_to(EVIDENCE)).replace("\\", "/"):
                      hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(directory.rglob("*")) if path.is_file()},
    }


def main():
    facts = {"full_P0_P5_complete": False, "runs": {name: summarize(name) for name in (
        "p2-default-multi-case-native-schema-low-corrected-20261020",
        "p2-default-multi-case-r4-20261020")}}
    facts["source_schema_probe"] = read(EVIDENCE / "p2-binding-source-schema-probe-20261020" / "verification.json")
    facts["embedding_probe"] = read(EVIDENCE / "p2-embedding-account-recheck-20261020.json")
    facts["tests"] = {}
    for stem in ("p2-native-schema-contract-20261020", "p2-binding-responsibility-r4-contract-20261020",
                 "p2-binding-responsibility-r4-storage-20261020", "p2-binding-responsibility-r4-dependency-20261020",
                 "p2-binding-responsibility-r4-probe-contract-20261020",
                 "p2-default-multi-case-native-schema-low-corrected-20261020", "p2-default-multi-case-r4-20261020"):
        suite = ET.parse(EVIDENCE / (stem + ".xml")).getroot().find("testsuite")
        facts["tests"][stem] = {key: suite.attrib.get(key) for key in
                               ("tests", "failures", "errors", "skipped", "time")}
    new_run = facts["runs"]["p2-default-multi-case-r4-20261020"]
    assert new_run["task_prompt_revision"] == 4
    assert new_run["isolated_profile_override"] is False
    assert new_run["isolated_max_output_tokens_override"] is False
    facts["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "Agent_Server/src/application/test_cases/planning_effect_tasks.py",
        "Agent_Server/src/application/test_cases/planning_stages.py",
        "Agent_Server/src/schemas/case_planning_stages.py",
        "Agent_Server/tests/test_default_coverage_production.py",
        "Agent_Server/tests/test_planning_effect_tasks.py")}
    output = EVIDENCE / "p2-binding-responsibility-audit-20261020.json"
    # This derived summary is regenerable; original runtime artifacts stay read-only.
    with output.open("w", encoding="utf-8") as file:
        json.dump(facts, file, ensure_ascii=False, indent=2)
    print(json.dumps({name: {key: item[key] for key in
          ("task_status", "planning_completed", "business_execution_completed", "physical_request_count")}
          for name, item in facts["runs"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
