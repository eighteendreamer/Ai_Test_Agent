"""Recorded scene navigation and frozen historical planning contracts."""
import json
from pathlib import Path

import pytest

from src.schemas.case_planning import PlanningQuery
from src.schemas.case_planning_scenes import ScenePlanningQuery, scene_decision_types
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint
from src.application.test_cases.planning_stages import build_stage_prompts
from src.application.test_cases.planning_evidence_references import evidence_reference_catalog, bind_evidence_references
from src.schemas.case_planning import PlanningEvidence
from src.schemas.case_planning_scenes import SceneReferencedEvidenceDecision, SceneEvidencePlanningDecision

ROOT = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03"


def actual_read_query():
    nav = json.loads((ROOT/"p5-scene-query-before/scene-query-before.json").read_bytes())["actual_pg_catalog"]["items"][0]
    return {"kind":"scene", "source_id":nav["scene_id"], "version_id":nav["version_id"], "content_hash":nav["content_hash"]}


@pytest.mark.parametrize("omitted", ["source_id","version_id","content_hash"])
def test_recorded_navigation_needs_all_fixed_scene_identity_fields(omitted):
    value = actual_read_query(); value.pop(omitted)
    with pytest.raises(ValueError, match="场景正文读取需要"):
        ScenePlanningQuery.model_validate(value)


@pytest.mark.parametrize("item_operations,references", [(False,False),(False,True),(True,False),(True,True)])
def test_scene_query_wire_is_available_in_each_supported_stage(item_operations,references):
    logic, effects = scene_decision_types(item_operations,references)
    for model in (logic,effects):
        decision = model.model_validate({"action":"query","summary":"实际场景导航","queries":[actual_read_query()]})
        assert decision.queries[0].version_id == actual_read_query()["version_id"]
    with pytest.raises(ValueError): PlanningQuery.model_validate(actual_read_query())


@pytest.mark.parametrize("folder", ["basis-ack","read-ack","basis-ack-refs","read-ack-refs"])
def test_actual_v12_seed_checkpoint_and_prompts_are_unchanged(folder):
    raw = json.loads((ROOT/"p5-scene-planning-reference-complete"/folder/"scene-planning-recovered-pg.json").read_bytes())
    seed = PlanningTaskSeed.model_validate(raw["seed"])
    assert seed.model_dump(mode="json") == raw["seed"]
    assert PlanningTaskCheckpoint.model_validate(raw["checkpoint"]).model_dump(mode="json") == raw["checkpoint"]
    p = seed.plan.config_snapshot.policy
    prompts = build_stage_prompts(p.planning_stage_protocol,p.effect_expected_source_protocol,p.operation_prefetch_protocol,
        p.source_identity_protocol,p.linked_source_protocol,p.evidence_reference_protocol)
    assert prompts.model_dump(mode="json") == raw["seed"]["stage_prompts"]
    assert "scene_query_protocol" not in seed.model_dump(mode="json")["plan"]["config_snapshot"]["policy"]


def test_reference_query_keeps_the_recorded_scene_identity_until_query_consumption():
    raw = json.loads((ROOT/"p5-scene-planning-reference-complete/basis-ack-refs/scene-planning-recovered-pg.json").read_bytes())
    evidence = [PlanningEvidence.model_validate(e) for e in raw["checkpoint"]["plan"]["evidence"]]
    catalog = evidence_reference_catalog(evidence)
    wire = SceneReferencedEvidenceDecision.model_validate({"action":"query","summary":"补读实际场景", "queries":[actual_read_query()]})
    result = bind_evidence_references(wire,catalog,evidence,query_decision_type=SceneEvidencePlanningDecision)
    assert result.queries[0].model_dump(mode="json") == wire.queries[0].model_dump(mode="json")
