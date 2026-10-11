"""Verify task receipts, preserved failures and byte identities; no model calls."""
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_bytes())


def fact(path):
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix(),
            "size_bytes": len(data), "sha256": sha256(data).hexdigest()}


def unchanged(item):
    path = Path(item["path"])
    if not path.is_absolute():
        path = ROOT / path
    assert fact(path) == item, f"Prior original changed: {item['path']}"


prior_counts = {}
for name in ("p2-default-current", "p2-linked-context"):
    previous = read(DOCS / f"{name}-validation.json")
    for item in previous["evidence_files"] + previous["protected_current_files"]:
        unchanged(item)
    prior_counts[name] = len(previous["evidence_files"])

plans = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
assert len({p.read_bytes() for p in plans}) == 1, "Plan copies differ"

expected_tests = {
    "contract": (29, 0, 0, 5), "default": (1, 1, 0, 0), "default-lifecycle": (1, 1, 0, 0),
    "delivery": (38, 0, 0, 9), "expanded": (132, 1, 0, 53), "expanded-final": (132, 0, 0, 53),
    "legacy-live": (70, 17, 0, 0), "legacy-final": (70, 0, 0, 0),
    "live": (5, 0, 0, 0), "live-final": (15, 0, 0, 0), "live-query-final": (18, 0, 0, 0),
}
tests = []
for name, expected in expected_tests.items():
    cases = list(ET.parse(DOCS / f"p2-effect-tasks-{name}.xml").getroot().iter("testcase"))
    counts = (len(cases), sum(c.find("failure") is not None for c in cases),
        sum(c.find("error") is not None for c in cases), sum(c.find("skipped") is not None for c in cases))
    assert counts == expected, (name, counts)
    tests.append(dict(zip(("batch", "tests", "failures", "errors", "skipped"), (name, *counts))))

controlled = []
for path in sorted((DOCS / "p2-effect-tasks-live-query-final").glob("*/verification.json")):
    raw = read(path)
    pg = raw["actual_PG"]
    checkpoint = pg["checkpoint"]
    original = checkpoint["plan"]["config_snapshot"]["policy"]
    assert original["planning_stage_protocol"] == "case-planning-staged-evidence-v3"
    assert pg["seed"]["stage_prompts"]["protocol"] == original["planning_stage_protocol"]
    assert len(pg["seed"]["stage_prompts"]["effect_tasks"]) == 3
    assert raw["controlled_model"] and raw["terminal_extra_model_calls"] == 0 and raw["business_HTTP"] == 0
    receipts = [r["task"] for r in checkpoint["effect_tasks"]["completed"]]
    name = path.parent.name
    expected_calls = 3 if name == "budget" else 5 if name in {"produced-query", "unknown-reference"} else 4
    assert len(raw["actual_requests"]) == expected_calls
    assert receipts == (["bindings", "produced"] if name == "budget" else ["bindings", "produced", "cleanup"])
    assert pg["status"] == ("failed" if name == "budget" else "completed")
    if name == "budget":
        assert original["max_rounds"] == 3 and checkpoint["next_round"] == 3
    if name in {"bindings-ack", "produced-ack", "cleanup-response-ack"}:
        assert raw["actual_current_MySQL"]["policy"]["max_rounds"] == 1
        assert original["max_rounds"] == 5 and "completed_receipt_rewrite" in raw["actual_SQL_rejections"]
    controlled.append({"fault": name, "requests": expected_calls, "status": pg["status"],
        "accepted_tasks": receipts, "terminal_extra_model_calls": 0})
assert len(controlled) == 7

default_trials = []
for name, sdk_count in (("default", 6), ("default-lifecycle", 3)):
    folder = DOCS / f"p2-effect-tasks-{name}"
    raw = read(folder / "verification.json")
    pg = read(folder / "PG-planning.json")
    assert not raw["controlled_model"] and not raw["business_execution_completed"] and not raw["full_P0_P5_complete"]
    assert len(raw["SDK_create_observations"]) == sdk_count
    assert all(len(x["physical_requests"]) == 1 for x in raw["SDK_create_observations"])
    responses = [read(p) for p in (folder / "physical").glob("*.response.json")]
    assert len(responses) == sdk_count and all(r["status_code"] == 200 for r in responses)
    assert [r["task"] for r in pg["checkpoint"]["effect_tasks"]["completed"]] == ["bindings"]
    if name == "default":
        assert raw["planning_completed"] and pg["status"] == "completed"
        assert [x[0] for x in raw["actual_business_HTTP"]] == ["POST", "POST", "GET", "DELETE", "DELETE"]
        assert len(raw["actual_created_ids"]) == 2 and raw["remaining_orders"] == {}
        run = read(folder / "final-run.json")
        assert run["results"] and all(r["status"] == "error" for r in run["results"])
        verdict = "planning omitted effect roles; actual business cleaned both records; formal receipt error"
    else:
        assert not raw["planning_completed"] and raw["actual_business_HTTP"] == [] and pg["status"] == "failed"
        assert pg["checkpoint"]["plan"]["rounds"][-1]["evidence_task"]["task"] == "produced"
        assert pg["checkpoint"]["plan"]["rounds"][-1]["status"] == "interrupted"
        streams = raw["SDK_stream_observations"]
        interrupted = [s for s in streams if s.get("finish_reason") is None]
        assert len(interrupted) == 1 and interrupted[0]["field_counts"]["content"]["string_chars"] == 0
        assert interrupted[0]["numeric_usage"]["completion_tokens"] == 6621
        assert not (folder / "final-run.json").exists()
        verdict = "original total deadline interrupted produced task; binding preserved; no Run or business HTTP"
    default_trials.append({"batch": name, "SDK_calls": sdk_count, "physical_HTTP": sdk_count,
        "HTTP_status": 200, "business_HTTP": len(raw["actual_business_HTTP"]), "verdict": verdict})

paths = sorted(p for p in DOCS.glob("p2-effect-tasks-*") if p.is_file())
paths += sorted(p for d in DOCS.glob("p2-effect-tasks-*") if d.is_dir() for p in d.rglob("*") if p.is_file())
paths = [p for p in paths if p.name != "p2-effect-tasks-validation.json"]
result = {"goal_complete": False, "tests": tests, "controlled_actual_store_tests": controlled,
    "actual_default_trials": default_trials, "prior_evidence_unchanged": prior_counts,
    "protected_current_files": previous["protected_current_files"], "plan_copies": [fact(p) for p in plans],
    "evidence_files": [fact(p) for p in paths],
    "scope": "optional frozen task protocol and actual-store recovery verified; both real Step trials failed; full P0-P5 incomplete"}
(DOCS / "p2-effect-tasks-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"evidence_files": len(paths), "prior_evidence_unchanged": prior_counts,
    "controlled_actual_store_tests": controlled, "actual_default_trials": default_trials}, ensure_ascii=False))
