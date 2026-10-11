"""Project actual captured bases without changing their evidence or replay."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.application.test_cases.planning_linked_sources import (
    LINKED_CONTEXT_PROTOCOL, linked_source_context, linked_context_prompt_version,
)
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint

ROOT = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


@pytest.mark.parametrize("folder", ["p2-default-current-staged", "p2-default-current-profile"])
def test_actual_failed_task_full_basis_is_retained_but_not_repeated(folder):
    raw = json.loads((ROOT / folder / "PG-planning.json").read_bytes())
    seed = PlanningTaskSeed.model_validate(raw["seed"])
    cp = PlanningTaskCheckpoint.model_validate(raw["checkpoint"])
    assert seed.model_dump(mode="json") == raw["seed"]
    assert cp.model_dump(mode="json") == raw["checkpoint"]
    before = cp.model_dump(mode="json")
    assert linked_source_context(cp.linked_sources, None) == before["linked_sources"]
    nav = linked_source_context(cp.linked_sources, LINKED_CONTEXT_PROTOCOL)
    assert nav["protocol"] == LINKED_CONTEXT_PROTOCOL
    original = before["linked_sources"]["entries"][0]
    entry = nav["entries"][0]
    for field in ("case_evidence_ids", "evidence_ids", "read", "error", "source"):
        assert entry[field] == original[field]
    basis = original["basis"]
    assert entry["basis"]["project_id"] == basis["project_id"]
    assert entry["basis"]["version_id"] == basis["version"]["id"]
    assert entry["basis"]["asset_sha256"] == basis["asset"]["sha256"]
    assert entry["basis"]["role"] == basis["role"]
    assert entry["basis"]["warnings"] == basis["warnings"]
    serialized = json.dumps(nav, ensure_ascii=False)
    assert "preview_text" not in serialized and "storage_uri" not in serialized
    assert len(serialized) < len(json.dumps(before["linked_sources"], ensure_ascii=False))
    assert cp.model_dump(mode="json") == before
    assert [e.model_dump(mode="json") for e in cp.plan.evidence] == before["plan"]["evidence"]


def test_projection_keeps_actual_scene_identity_and_old_prompt_version():
    path = ROOT / "p5-scene-planning-reference-complete/basis-ack-refs/scene-planning-recovered-pg.json"
    raw = json.loads(path.read_bytes())
    seed = PlanningTaskSeed.model_validate(raw["seed"])
    cp = PlanningTaskCheckpoint.model_validate(raw["checkpoint"])
    assert seed.model_dump(mode="json") == raw["seed"]
    nav = linked_source_context(cp.linked_sources, LINKED_CONTEXT_PROTOCOL)
    for before, after in zip(raw["checkpoint"]["linked_sources"]["entries"], nav["entries"]):
        if before["source"]["source_type"] == "ui_recording_scene":
            assert before == after
    assert linked_context_prompt_version(seed.plan.prompt_version, None) == seed.plan.prompt_version


def test_unknown_projection_is_not_silently_used():
    raw = json.loads((ROOT / "p2-default-current-staged/PG-planning.json").read_bytes())
    cp = PlanningTaskCheckpoint.model_validate(raw["checkpoint"])
    with pytest.raises(ValueError, match="来源上下文"):
        linked_source_context(cp.linked_sources, "unknown")
    nav = linked_source_context(cp.linked_sources, LINKED_CONTEXT_PROTOCOL)
    changed = deepcopy(nav)
    changed["entries"][0]["basis"]["warnings"].append("local-change")
    assert changed != linked_source_context(cp.linked_sources, LINKED_CONTEXT_PROTOCOL)
