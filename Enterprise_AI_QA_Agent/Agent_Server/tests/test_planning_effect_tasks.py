"""Split actual captured successful declarations and replay failed old seeds."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from test_planning_evidence_references import actual, reference_wire
from src.application.test_cases.planning_stages import seal_logical_decision, build_stage_prompts, stage_prompt_version
from src.application.test_cases.planning_effect_tasks import (
    DECISIONS, _prompt_schema, accept_task_decision, aggregate_task_receipts, effect_task_prompts,
    DECISIONS_V2, next_effect_task, check_task_progress, effect_task_output_format, effect_task_schema,
)
from src.application.runtime.case_effect_evidence import validate_effect_declarations
from src.schemas.case_planning import PlanningDecisionV3, CasePlanningPolicy
from src.schemas.case_planning_stages import PlanningOperationSelection, PlanningStagePrompts
from src.schemas.case_planning_effect_tasks import BindingTaskDecision, ProducedTaskDecision, EffectTaskCheckpoint
from src.schemas.case_planning_references import REFERENCE_PROTOCOL_V2, ReferencedDocumentExpectedV2
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint

ROOT = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def test_prompt_schemas_omit_only_non_validation_title_annotations():
    prompts = effect_task_prompts()
    for task, model in DECISIONS.items():
        original = effect_task_schema(task)
        prompt_schema = json.loads(prompts[task].split("\nJSON Schema: ", 1)[1])
        assert prompt_schema == _prompt_schema(original)
        assert "title" in original
        assert _prompt_schema(prompt_schema) == prompt_schema


def test_each_effect_task_uses_its_typed_optional_json_schema():
    for task, model in DECISIONS.items():
        output_format = effect_task_output_format(task)
        assert output_format.kind == "json_schema"
        assert output_format.schema_name == f"case_planning_{task}"
        assert output_format.schema_definition == effect_task_schema(task)
        assert output_format.strict is True
        assert output_format.required is False
    with pytest.raises(ValueError, match="未知的规划声明任务"):
        effect_task_output_format("unknown")


def test_v2_effect_schema_uses_single_document_reference_alias():
    schema = effect_task_schema("produced", REFERENCE_PROTOCOL_V2)
    for name in ("ReferencedDocumentValueV2", "ReferencedDocumentTextV2", "ReferencedTextExpectedV2"):
        expected = schema["$defs"][name]
        assert set(expected["required"]) == set(expected["properties"])
        assert "reference" in expected["properties"]
        assert not {"evidence_ref", "text_ref"} & set(expected["properties"])
    for name in ("ReferencedEffectCheckV2", "ReferencedOperationSelectorV2"):
        definition = schema["$defs"][name]
        assert {"expected_path_tokens", "expected_source"} <= set(definition["required"])
    prompt_schema = json.loads(effect_task_prompts(REFERENCE_PROTOCOL_V2)["produced"].split("\nJSON Schema: ", 1)[1])
    assert prompt_schema == _prompt_schema(schema)
    assert 'expected_source={reference:"T3"' in effect_task_prompts(REFERENCE_PROTOCOL_V2)["produced"]


def test_v2_effect_wire_rejects_v1_dual_reference_fields():
    with pytest.raises(ValueError):
        TypeAdapter(ReferencedDocumentExpectedV2).validate_python({
            "reference": "E1", "evidence_ref": "E1", "text_ref": "T1", "payload_path_tokens": [],
        })


def test_strict_effect_schema_requires_both_nullable_expected_source_fields():
    schema = effect_task_schema("produced")
    for name in ("ReferencedEffectCheck", "ReferencedOperationSelector"):
        definition = schema["$defs"][name]
        assert {"expected_path_tokens", "expected_source"} <= set(definition["required"])
    expected = schema["$defs"]["ReferencedDocumentExpected"]
    assert set(expected["properties"]) <= set(expected["required"])
    assert "expected_path_tokens" in effect_task_prompts()["produced"]
    assert "未使用的一项写null" in effect_task_prompts()["produced"]


def test_effect_task_prompt_disambiguates_case_paths_document_refs_and_roles():
    prompts = effect_task_prompts()
    common = prompts["produced"]
    assert 'expected_path_tokens=["期望","create_code"]' in common
    assert "路径相对content_json对象本身" in common
    assert 'text_ref:"T3"' in common
    assert "E和T是二选一的文档短引用" in common
    assert "用例E证据的预期只能走expected_path_tokens" in common
    assert "条件全部同时匹配" in common
    assert "不得把模板当作实际path预期" in common
    assert "validation_feedback中的case:/document:等ID仅用于定位" in common
    assert "不能在produced任务里添加role=cleanup" in prompts["produced"]
    assert "只返回role=cleanup" in prompts["cleanup"]


def test_binding_task_uses_item_sources_without_native_effect_path_instructions():
    prompts = effect_task_prompts(REFERENCE_PROTOCOL_V2)
    binding_prompt = prompts["bindings"].split("\nJSON Schema: ", 1)[0]
    assert "runtime_context为空时不能引用任何input_key" in binding_prompt
    assert "前置执行项整理后的输出" in binding_prompt
    assert "不能只修改summary" in binding_prompt
    assert "checks.output_path_tokens" not in binding_prompt
    assert "select_when" not in binding_prompt
    assert "object_path_tokens" not in binding_prompt
    for task in ("produced", "cleanup"):
        assert "相对持久化的原始工具输出根" in prompts[task]


def test_effect_task_wire_schema_rejects_canonical_ids_instead_of_short_refs():
    for task, binding_name in (("bindings", "TaskBinding"), ("produced", "TaskDeclarations")):
        schema = effect_task_schema(task)
        reference_schema = schema["$defs"][binding_name]["properties"]["evidence_ids"]["items"]
        assert reference_schema["pattern"] == r"^E[1-9][0-9]*$"
    with pytest.raises(ValueError, match="String should match pattern"):
        BindingTaskDecision.model_validate({"task": "bindings", "action": "finish", "summary": "test",
            "bindings": [{"item_key": "target", "evidence_ids": ["case:version-id"]}]})
    with pytest.raises(ValueError, match="String should match pattern"):
        ProducedTaskDecision.model_validate({"task": "produced", "action": "finish", "summary": "test",
            "bindings": [{"item_key": "target", "evidence_ids": ["document:version:hash"],
                "effect_declarations": []}]})


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


def test_v2_task_receipts_bind_e_and_t_aliases_to_canonical_evidence():
    plan, fixed, catalog, tasks = captured_tasks()
    state = EffectTaskCheckpoint()
    merged = None
    for name, source in tasks.items():
        value = deepcopy(source)
        if name != "bindings":
            for binding in value["bindings"]:
                for declaration in binding["effect_declarations"]:
                    for item in declaration["checks"] + declaration["select_when"]:
                        reference = item.get("expected_source")
                        item.setdefault("expected_path_tokens", None)
                        if reference is None:
                            item["expected_source"] = None
                        else:
                            alias = reference.get("text_ref") or reference.get("evidence_ref")
                            item["expected_source"] = {"reference": alias,
                                **{key: part for key, part in reference.items()
                                    if key not in {"text_ref", "evidence_ref"}}}
        wire = DECISIONS_V2[name].model_validate(value)
        state, merged = accept_task_decision(fixed, state, wire, catalog, plan.evidence, REFERENCE_PROTOCOL_V2)
    assert next_effect_task(fixed, state) is None
    assert merged == plan.decision


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


def test_actual_rejected_r3_prompts_restore_without_rebuilding_as_r4():
    raw = json.loads((ROOT / "p2-bindings-r3-prompts-20261020.json").read_bytes())["stage_prompts"]
    restored = PlanningStagePrompts.model_validate(raw)
    assert restored.task_prompt_revision == 3
    assert restored.model_dump(mode="json") == raw
    assert restored.effect_tasks["bindings"] != effect_task_prompts(REFERENCE_PROTOCOL_V2)["bindings"]


def test_new_strategy_is_explicit_frozen_policy_not_global_model_setting():
    plan, _, _, _ = captured_tasks()
    config = plan.config_snapshot.policy.model_dump(mode="json")
    config["evidence_reference_protocol"] = "case-planning-evidence-references-v1"
    config["planning_stage_protocol"] = "case-planning-staged-evidence-v3"
    policy = CasePlanningPolicy.model_validate(config)
    prompts = build_stage_prompts(policy.planning_stage_protocol, policy.effect_expected_source_protocol,
        policy.operation_prefetch_protocol, policy.source_identity_protocol, policy.linked_source_protocol, policy.evidence_reference_protocol)
    assert set(prompts.effect_tasks) == set(DECISIONS)
    assert stage_prompt_version(policy, prompts).endswith("+effect-tasks-v1.r4")
    assert prompts.task_prompt_revision == 4
    legacy = prompts.model_dump(mode="json")
    legacy.pop("task_prompt_revision")
    restored = PlanningStagePrompts.model_validate(legacy)
    assert restored.model_dump(mode="json") == legacy
    assert stage_prompt_version(policy, restored).endswith("+effect-tasks-v1")
    assert policy.max_rounds == plan.config_snapshot.policy.max_rounds
    assert policy.timeout_seconds == plan.config_snapshot.policy.timeout_seconds
    config["evidence_reference_protocol"] = None
    with pytest.raises(ValueError): CasePlanningPolicy.model_validate(config)
