"""Read-only evidence audit; writes only this batch's audit projection."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

root = Path(__file__).resolve().parents[2]
docs = root / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()}


def junit(prefix, tests, failures=0, skipped=0):
    suite = ET.parse(docs / f"{prefix}.xml").getroot().find("testsuite")
    assert suite is not None
    assert int(suite.attrib["tests"]) == tests, (prefix, suite.attrib)
    assert int(suite.attrib["failures"]) == failures, (prefix, suite.attrib)
    assert int(suite.attrib["skipped"]) == skipped, (prefix, suite.attrib)
    assert int(suite.attrib["errors"]) == 0
    return suite.attrib


counts = {}
for name, tests, failures, skipped in [
    ("before", 20, 6, 0), ("before2", 20, 4, 0), ("verified", 20, 0, 0),
    ("regression", 206, 0, 48), ("live", 8, 0, 0), ("storage-final", 6, 0, 0),
    ("final", 77, 2, 0), ("usage-before", 2, 2, 0), ("delivery", 78, 0, 0),
]:
    prefix = f"p0-model-completion-{name}"
    counts[prefix] = junit(prefix, tests, failures, skipped)
counts["p2-default-coverage-current"] = junit("p2-default-coverage-current", 1, 1)
counts["p2-json-mode-contract"] = junit("p2-json-mode-contract", 41, 0, 7)
assert "compileall_exit=0" in (docs / "p0-model-completion-compile.log").read_text()

matrix = []
for folder in sorted((docs / "p0-model-completion-delivery").iterdir()):
    if folder.name == "anthropic-explicit-zero":
        result = read(folder / "sdk-result.json")
        assert result["response_summary"]["usage"]["input_tokens"] == 0
        assert result["response_summary"]["usage"]["output_tokens"] == 3
        continue
    facts = read(folder / "result.json")
    summary = facts["response"]["response_summary"]
    limited = folder.name.endswith("-limited")
    assert summary["completion_status"] == ("truncated" if limited else "complete")
    assert facts["terminal"]["plan"]["status"] == ("planning_failed" if limited else "no_match")
    assert facts["physical_sends"] == 1 and not facts["real_provider"]
    usage = summary["usage"]
    assert (usage.get("prompt_tokens", usage.get("input_tokens", usage.get("promptTokenCount")))) == 7
    assert (usage.get("completion_tokens", usage.get("output_tokens", usage.get("candidatesTokenCount")))) == 3
    request = read(folder / "physical-request.json")["body"]
    if folder.name.startswith("google"):
        assert request["generationConfig"]["maxOutputTokens"] == 512
    else:
        key = ("max_output_tokens" if folder.name.startswith("openai_responses") else
            "max_completion_tokens" if folder.name.startswith("langchain") else "max_tokens")
        assert request[key] == 512
    matrix.append({"variant": folder.name, "status": summary["completion_status"],
        "terminal": facts["terminal"]["plan"]["status"], "physical_sends": 1})
assert len(matrix) == 20
for kind in ("complete", "limited"):
    old = read(docs / f"p0-model-completion-usage-before/anthropic_messages-stream-{kind}/sdk-result.json")
    assert old["response_summary"]["usage"]["input_tokens"] is None

recovery = {}
for kind in ("native", "stream"):
    facts = read(docs / f"p0-model-completion-recovery/{kind}/verification.json")
    for key in ("response_committed_ack_lost", "recovery_no_new_model_call", "original_deadline_preserved",
                "terminal_no_new_calls", "source_bytes_read_back", "usage_replayed_from_failure"):
        assert facts[key], (kind, key)
    assert facts["physical_http_sends"] == 2
    recovery[kind] = {key: value for key, value in facts.items() if not isinstance(value, (dict, list))}

generation_folder = docs / "p0-model-completion-generation-final/guards"
generation = read(generation_folder / "verification.json")
assert generation["guard_send_count"] == 0 and generation["actual_generation_http_calls"] == 1
assert generation["pg_corruption_detected_and_restored"]
assert not generation["drafts_committed"] and not generation["durable_generation_task_connected"]
for name, expected in generation["files"].items():
    actual = digest(generation_folder / name)
    assert actual["sha256"] == expected["sha256"] and actual["bytes"] == expected["size_bytes"]

impact = {}
for name, disposition in [("length", "truncated"), ("empty", "empty"), ("tool", "tool_calls"),
                          ("http_failure", "provider_failure"), ("large", None)]:
    facts = read(docs / f"p0-model-completion-live-final/calls/response-{name}.json")
    assert facts["record"]["response_disposition"] == disposition
    assert facts["logical_calls"] == 1 and not facts["model_candidates_validated"]
    assert facts["actual_http_attempts"] == (3 if name == "http_failure" else 1)
    impact[name] = {"response_disposition": disposition, "logical_calls": 1,
        "physical_attempts": facts["actual_http_attempts"]}

default = read(docs / "p2-default-coverage-current/verification.json")
assert default["task"]["status"] == "failed" and default["plan"] is None
assert not default["actual_business_HTTP"] and not default["actual_created_ids"]
assert len(default["SDK_create_observations"]) == len(default["SDK_stream_observations"]) == 2
assert default["SDK_stream_observations"][1]["field_counts"]["content"]["string_chars"] == 0
http = list((docs / "p2-default-coverage-current/physical").glob("*.response.json"))
assert len(http) == 2 and all(read(path)["status_code"] == 200 for path in http)

plans = [docs / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(path.read_bytes() == plans[0].read_bytes() for path in plans)
assert b"\r\r\n" not in plans[0].read_bytes()

previous = {}
for name in ("p1-template-fixed-version-app-validation.json", "p5-security-evidence-validation.json",
             "p5-security-report-validation.json", "p5-perf-report-validation.json",
             "p5-recording-knowledge-validation.json", "p5-recording-overview-validation.json"):
    items = read(docs / name)["artifacts"]
    for item in items:
        assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]
    previous[name] = len(items)
perf = read(docs / "p5-perf-evidence-validation.json")
for path, expected in perf["artifact_hashes"].items():
    assert digest(root / path)["sha256"] == expected, path
previous["p5-perf-evidence-validation.json"] = len(perf["artifact_hashes"])
protected = read(docs / "p5-security-evidence-validation.json")["protected_files"]
for item in protected:
    assert digest(root / item["path"])["sha256"] == item["sha256"], item["path"]

artifacts = sorted({path for prefix in ("p0-model-completion-", "p2-default-coverage-current", "p2-json-mode-contract")
    for item in docs.glob(prefix + "*") for path in ([item] if item.is_file() else item.rglob("*"))
    if path.is_file() and path.name != "p0-model-completion-validation.json"})
sources = ["Agent_Server/src/application/model_clients/completion.py",
    "Agent_Server/src/application/model_clients/anthropic_client.py",
    "Agent_Server/src/application/model_adapters/langchain_model_adapter.py",
    "Agent_Server/src/application/model_adapters/legacy_model_adapter.py",
    "Agent_Server/src/application/models/model_runtime_service.py",
    "Agent_Server/src/application/test_cases/planning_workflow.py",
    "Agent_Server/src/application/test_cases/generation_pipeline.py",
    "Agent_Server/src/application/documents/impact_calls.py",
    "Agent_Server/src/application/test_runs/agent_case_assessment.py",
    "Agent_Server/src/application/test_runs/agent_case_receipt.py",
    "Agent_Server/tests/test_model_completion_http.py", "Agent_Server/tests/test_model_completion_contract.py",
    "Agent_Server/tests/test_incremental_generation_sources.py", "docs/upgrade_2026-10-03/PLAN.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md", "docs/upgrade_2026-10-03/P0_MODEL_ADAPTATION_RESEARCH.md",
    "docs/upgrade_2026-10-03/.gitattributes"]
result = {"baseline": "3a11b05b", "junit": counts, "matrix": matrix, "recovery": recovery,
    "generation_guard_sends": generation["guard_send_count"], "impact": impact,
    "default_step": {"physical_HTTP_200": 2, "status": "failed", "business_HTTP": 0,
        "strategy": "old single stage", "new_staged_strategy_verified": False},
    "compileall": "exit0", "plans_equal": 3, "previous_artifacts_checked": previous,
    "protected_files": protected, "sources": [digest(root / path) for path in sources],
    "artifacts": [digest(path) for path in artifacts], "full_goal_complete": False,
    "not_proven": ["provider request profile/JSON/reasoning/media capabilities", "multiple external providers",
        "default autonomous planning/execution and full App", "manual DeepSeek and recording business management",
        "API renewal", "performance capacity/AI stream", "other security tools/faults",
        "preparations/unknown effects/revisions/other modes", "business requirement review/production migration"]}
(docs / "p0-model-completion-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"artifacts": len(artifacts), "delivery": counts["p0-model-completion-delivery"],
    "plans_equal": 3, "previous_artifacts_checked": previous}))
