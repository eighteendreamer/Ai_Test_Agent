"""Split actual captured successful declarations and replay failed old seeds."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from test_planning_evidence_references import actual, reference_wire
from src.application.test_cases.planning_stages import seal_logical_decision, build_stage_prompts, stage_prompt_version
from src.application.test_cases.planning_effect_tasks import (
    DECISIONS, accept_task_decision, aggregate_task_receipts, next_effect_task, check_task_progress,
)
from src.application.runtime.case_effect_evidence import validate_effect_declarations
from src.schemas.case_planning import PlanningDecisionV3, CasePlanningPolicy
from src.schemas.case_planning_stages import PlanningOperationSelection
from src.schemas.case_planning_effect_tasks import EffectTaskCheckpoint
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint

ROOT = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def captured_tasks():
    plan, canonical, catalog = actual()
    logic = deepcopy(plan.rounds[0]["decision"])
    selections = logic.pop("operation_selections")
    fixed = seal_logical_decision(PlanningDecisionV3.model_validate(logic), [PlanningOperationSelection.model_validate(v) for v in selections])
    wire = reference_wire(canonical, catalog)
    tasks = {}
    for name in DECISIONS:
        value = {"task": name, "action": "finish", "summary": canonical["summary"], "bindings": []}
        for source in wire["bindings"]:
            binding = {"item_key": source["item_key"], "evidence_ids": source["evidence_ids"]}
            if name == "bindings":
                binding.update(input_bindings=source.get("input_bindings", {}), declaration_roles=[])
            else:
                declarations = [d for d in source.get("effect_declarations") or [] if d["role"] == name]
                if not declarations:
                    continue
                binding["effect_declarations"] = declarations
            value["bindings"].append(binding)
        tasks[name] = value
    return plan, fixed, catalog, tasks


def test_split_captured_business_declarations_preserves_original_execution():
    plan, fixed, catalog, tasks = captured_tasks()
    before = fixed.model_dump(mode="json")
    state = EffectTaskCheckpoint()
    for name, value in tasks.items():
        assert next_effect_task(fixed, state) == name
        state, merged = accept_task_decision(fixed, state, DECISIONS[name].model_validate(value), catalog, plan.evidence)
    assert next_effect_task(fixed, state) is None
    assert merged == plan.decision
    validate_effect_declarations(merged, "case-effect-evidence-v2", plan.evidence, "case-effect-coverage-v2", "case-effect-document-expected-v1")
    assert fixed.model_dump(mode="json") == before
    assert aggregate_task_receipts(state).bindings[0].effect_declarations


@pytest.mark.parametrize("fault", ["foreign-item", "missing-item", "unknown-e", "role", "wrong-path", "duplicate-id"])
def test_bad_task_cannot_change_saved_previous_receipt(fault):
    plan, fixed, catalog, tasks = captured_tasks()
    state, _ = accept_task_decision(fixed, EffectTaskCheckpoint(), DECISIONS["bindings"].model_validate(tasks["bindings"]), catalog, plan.evidence)
    saved = state.model_dump(mode="json")
    value = deepcopy(tasks["produced"])
    if fault == "foreign-item": value["bindings"][0]["item_key"] = "foreign"
    if fault == "missing-item": value["bindings"] = []
    if fault == "unknown-e": value["bindings"][0]["evidence_ids"] = ["E999"]
    if fault == "role": value["bindings"][0]["effect_declarations"][0]["role"] = "cleanup"
    if fault == "duplicate-id": value["bindings"][0]["effect_declarations"] *= 2
    if fault == "wrong-path":
        check = value["bindings"][0]["effect_declarations"][0]["checks"][0]
        check.pop("expected_source", None); check["expected_path_tokens"] = ["MISSING-PATH"]
    with pytest.raises(ValueError): accept_task_decision(fixed, state, DECISIONS["produced"].model_validate(value), catalog, plan.evidence)
    assert state.model_dump(mode="json") == saved


def test_progress_cannot_rewrite_or_drop_accepted_receipt():
    plan, fixed, catalog, tasks = captured_tasks()
    state, _ = accept_task_decision(fixed, EffectTaskCheckpoint(), DECISIONS["bindings"].model_validate(tasks["bindings"]), catalog, plan.evidence)
    changed = state.model_copy(deep=True); changed.completed[0].summary = "changed"
    with pytest.raises(ValueError): check_task_progress(state, changed)
    with pytest.raises(ValueError): check_task_progress(state, None)


@pytest.mark.parametrize("folder", ["p2-default-current-profile", "p2-linked-context-default"])
def test_actual_old_failed_seed_stays_original_wire(folder):
    raw = json.loads((ROOT / folder / "PG-planning.json").read_bytes())
    assert PlanningTaskSeed.model_validate(raw["seed"]).model_dump(mode="json") == raw["seed"]
    assert PlanningTaskCheckpoint.model_validate(raw["checkpoint"]).model_dump(mode="json") == raw["checkpoint"]


def test_new_strategy_is_explicit_frozen_policy_not_global_model_setting():
    plan, _, _, _ = captured_tasks()
    config = plan.config_snapshot.policy.model_dump(mode="json")
    config["evidence_reference_protocol"] = "case-planning-evidence-references-v1"
    config["planning_stage_protocol"] = "case-planning-staged-evidence-v3"
    policy = CasePlanningPolicy.model_validate(config)
    prompts = build_stage_prompts(policy.planning_stage_protocol, policy.effect_expected_source_protocol,
        policy.operation_prefetch_protocol, policy.source_identity_protocol, policy.linked_source_protocol, policy.evidence_reference_protocol)
    assert set(prompts.effect_tasks) == set(DECISIONS)
    assert stage_prompt_version(policy, prompts).endswith("+effect-tasks-v1")
    assert policy.max_rounds == plan.config_snapshot.policy.max_rounds
    assert policy.timeout_seconds == plan.config_snapshot.policy.timeout_seconds
    config["evidence_reference_protocol"] = None
    with pytest.raises(ValueError): CasePlanningPolicy.model_validate(config)
