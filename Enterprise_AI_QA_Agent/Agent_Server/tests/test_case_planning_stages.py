"""Replay captured production plans, then exercise real staged checkpoints."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_case_effect_evidence import NEW_POLICY, declaration
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import tasks_environment, delivery, expire, live
from src.application.test_cases.planning_stages import (
    build_stage_prompts, merge_evidence_decision, previous_relation_logic_prompt_version,
    seal_logical_decision, validate_relation_logic_finish,
)
from src.application.test_cases.planning_workflow import CasePlanningWorkflow, PlanningCheckpointUnavailable
from src.application.test_runs.preparation_scopes import normalize_preparation_scopes, normalize_relation_scopes
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.schemas.case_planning import CasePlanRecord, CasePlanningPolicy, PlanningDecision
from src.schemas.case_planning_stages import (
    EvidencePlanningDecision, LogicalPlanningDecision, PlanningStageCheckpoint,
    LogicalPlanningDecisionV2, RELATION_STAGE_PROTOCOL, RELATION_PROMPT_VERSION,
    LogicalPlanningDecisionV3,
    STAGE_PROTOCOL, STAGED_PROMPT_VERSION, logical_decision_hash,
)
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskCheckpoint, PlanningTaskSeed
from src.schemas.project import ProjectCreateRequest

ROOT = Path(__file__).resolve().parents[2]
POLICY = NEW_POLICY.model_copy(update={"effect_coverage_protocol": "case-effect-coverage-v1",
    "planning_stage_protocol": STAGE_PROTOCOL})


def test_relation_logic_prompt_projects_out_later_stage_fields_without_weakening_validation():
    prompts = build_stage_prompts(RELATION_STAGE_PROTOCOL)
    schema = json.loads(prompts.logic.split("JSON Schema: ", 1)[1])
    hidden_fields = {"input_bindings", "effect_evidence", "effect_declarations", "effect_schema"}
    for name in ("LogicalFormalCaseItemV2", "LogicalPreparationItemV2"):
        properties = schema["$defs"][name]["properties"]
        assert not hidden_fields.intersection(properties)
    assert any("cleanup_sources" in schema["$defs"][name]["properties"]
        for name in ("LogicalFormalCaseItemV2", "LogicalPreparationItemV2"))
    assert "必须加入dependency" in prompts.logic
    assert "创建用例为dependency" in prompts.logic
    assert RELATION_PROMPT_VERSION == "case-planning-staged-evidence-v8"

    decision = LogicalPlanningDecisionV2.model_validate({"action": "finish", "summary": "使用正式创建前置",
        "items": [
            {"key": "create_order", "kind": "dependency", "reason": "产生查询所需订单",
                "evidence_ids": ["case:create-version"], "case_id": "create-case", "case_version_id": "create-version"},
            {"key": "query_order", "kind": "target", "reason": "符合目标",
                "evidence_ids": ["case:query-version"], "case_id": "query-case", "case_version_id": "query-version",
                "depends_on": ["create_order"], "cleanup_sources": ["create_order"]},
        ]})
    assert decision.items[1].input_bindings == {}
    assert decision.items[1].cleanup_sources == ["create_order"]
    assert decision.model_dump(mode="json")["items"][0]["effect_schema"] == 2

    invalid = decision.model_dump(mode="json")
    invalid["items"][1]["input_bindings"] = {
        "order_id": {"source_item": "create_order", "output_path_tokens": ["order_id"]}}
    with pytest.raises(ValidationError):
        LogicalPlanningDecisionV2.model_validate(invalid)


def test_document_expected_prompt_composes_v3_schema_before_logic_projection():
    prompts = build_stage_prompts(RELATION_STAGE_PROTOCOL, expected_source_protocol="case-effect-document-expected-v1")
    schema = json.loads(prompts.logic.split("JSON Schema: ", 1)[1])
    assert schema["title"] == "LogicalPlanningDecisionV3"
    assert "effect_evidence" in schema["$defs"]["LogicalFormalCaseItemV3"]["properties"]
    assert "cleanup_sources的值必须是本计划items中执行项的key/item_key" in prompts.logic
    assert "没有任何target" in prompts.logic
    assert "不得声称订单已创建" in prompts.logic


def test_actual_agnes_empty_finish_requires_a_gap_only_for_the_new_logic_prompt():
    # Exact structured decision from p2-multi-case-agnes-v2-excerpt-fix-current-20261022-01:
    # the model claimed HTTP 201 had happened, despite returning no plan items or execution receipt.
    response = {
        "action": "finish",
        "summary": "前置用例已创建订单（case_key=dependency-create-order），实际响应为HTTP 201且order.id字面量键已记录。当前查询使用前置执行项输出order_id调用GET /orders/{id}，该输入为正式用例字段而非原始HTTP响应路径。",
        "queries": [],
        "items": [],
        "unresolved": [],
    }
    decision = LogicalPlanningDecisionV3.model_validate(response)
    previous = "case-planning-evidence-references-v13+logic-v7+effect-tasks-v2.r8+linked-navigation-v1"
    current = previous.replace("+logic-v7", "+logic-v8")
    assert previous_relation_logic_prompt_version(current) == previous
    validate_relation_logic_finish(decision, previous)
    with pytest.raises(ValueError, match="unresolved"):
        validate_relation_logic_finish(decision, current)

    response["unresolved"] = [{"condition": "没有匹配的目标用例", "reason": "本轮回放用于验证结构化缺口约束"}]
    validate_relation_logic_finish(LogicalPlanningDecisionV3.model_validate(response), current)


def test_previous_relation_prompt_version_is_preserved_for_frozen_effect_task_seeds():
    current = "case-planning-staged-evidence-v8+effect-tasks-v3.r9+linked-navigation-v1"
    assert previous_relation_logic_prompt_version(current) == (
        "case-planning-staged-evidence-v7+effect-tasks-v3.r9+linked-navigation-v1")


@pytest.mark.asyncio
async def test_frozen_legacy_prompt_seed_can_resume_without_rebuilding_current_prompt():
    raw = json.loads((ROOT / "docs/upgrade_2026-10-03/p4-operation-prefetch-live-final/none/verification.json").read_bytes())
    actual = raw["actual_PG"]
    seed = PlanningTaskSeed.model_validate(actual["seed"])
    checkpoint = PlanningTaskCheckpoint.model_validate(actual["checkpoint"])
    assert seed.plan.prompt_version == "case-planning-declared-operation-prefetch-v7"
    assert "已读正式用例可提供条件，必须加入dependency" not in seed.stage_prompts.logic
    resumed = await CasePlanningWorkflow(None).run(seed, checkpoint,
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert resumed.phase == "finished"
    assert resumed.plan.prompt_version == seed.plan.prompt_version


def test_actual_cleanup_case_key_confusion_is_fixed_using_the_real_rejected_candidate():
    # Minimal exact structure from the actual Agnes response rejected on 2026-10-21:
    # it used the formal case_key "dependency-create-order" as cleanup_sources.
    decision = LogicalPlanningDecisionV2.model_validate({"action": "finish", "summary": "查询订单并清理前置订单",
        "items": [
            {"key": "create-order", "kind": "dependency", "reason": "创建查询所需订单",
                "evidence_ids": ["case:create-version"], "case_id": "create-case", "case_version_id": "create-version"},
            {"key": "query-order", "kind": "target", "reason": "查询订单并清理前置创建项",
                "evidence_ids": ["case:query-version"], "case_id": "query-case", "case_version_id": "query-version",
                "depends_on": ["create-order"], "cleanup_sources": ["dependency-create-order"]},
        ]})
    target = next(item for item in decision.items if item.kind == "target")
    source_key = next(item.key for item in decision.items if item.kind == "dependency")
    assert target.cleanup_sources != [source_key]
    with pytest.raises(ValueError, match="多效果清理来源重复或不存在"):
        normalize_relation_scopes(decision)

    corrected = decision.model_copy(deep=True)
    next(item for item in corrected.items if item.kind == "target").cleanup_sources = [source_key]
    normalize_relation_scopes(corrected)


def captured_plan():
    return CasePlanRecord.model_validate(json.loads((ROOT / "docs/upgrade_2026-10-03/p4-operation-contract-production/all/verification.json").read_bytes())["frozen_plan"])


def logical_only(value):
    value = deepcopy(value)
    for entry in value["items"]:
        entry["input_bindings"] = {}
        entry["effect_evidence"] = None
    return value


def test_saved_v3_seed_and_checkpoint_roundtrip_without_new_fields():
    saved = json.loads((ROOT / "docs/upgrade_2026-10-03/p4-operation-contract-default/PG-planning.json").read_bytes())
    assert PlanningTaskSeed.model_validate(saved["seed"]).model_dump(mode="json") == saved["seed"]
    assert PlanningTaskCheckpoint.model_validate(saved["checkpoint"]).model_dump(mode="json") == saved["checkpoint"]


def test_actual_default_candidate_preserves_illegal_self_scope_and_formal_contract_gap():
    captured = json.loads((ROOT / "docs/upgrade_2026-10-03/p4-staged-planning-default-first/PG-planning.json").read_bytes())
    value = json.loads(captured["checkpoint"]["validation_feedback"]["candidate_response"])
    decision = LogicalPlanningDecision.model_validate(value)
    assert decision.items[0].cleanup_sources == [decision.items[0].key]
    with pytest.raises(ValueError, match="引用自身"):
        normalize_preparation_scopes(decision, "preparation-scope-v1")
    assert decision.items[0].instructions["cleanup_requirement"].endswith("准备项不提前删除。")
    formal = value["items"][1]
    formal["cleanup_sources"] = [value["items"][0]["key"]]
    with pytest.raises(ValidationError, match="Extra inputs"):
        PlanningDecision.model_validate(value)


@pytest.mark.parametrize("field", ["input_bindings", "effect_evidence", "tool_query"])
def test_logic_rejects_technical_fields_from_real_production_decision(field):
    actual = captured_plan().decision.model_dump(mode="json")
    value = logical_only(actual)
    if field == "tool_query":
        value = {"action": "query", "summary": "读取工具", "queries": [{"kind": "tool", "source_id": "api-test-runner"}]}
    else:
        source = next(entry for entry in actual["items"] if entry[field])
        next(entry for entry in value["items"] if entry["key"] == source["key"])[field] = source[field]
    with pytest.raises(ValidationError):
        LogicalPlanningDecision.model_validate(value)


def test_stage_merge_recreates_captured_production_plan_without_business_edits():
    original = captured_plan()
    logic = LogicalPlanningDecision.model_validate(logical_only(original.decision.model_dump(mode="json")))
    fixed = seal_logical_decision(logic)
    before = fixed.model_dump(mode="json")
    bindings = [{"item_key": entry.key, "evidence_ids": entry.evidence_ids,
        "input_bindings": {k: v.model_dump(mode="json") for k, v in entry.input_bindings.items()},
        "effect_evidence": entry.effect_evidence.model_dump(mode="json") if entry.effect_evidence else None}
        for entry in original.decision.items]
    result = merge_evidence_decision(fixed, EvidencePlanningDecision(action="finish", summary="补充证据", bindings=bindings), original.evidence)
    assert result == original.decision
    assert fixed.model_dump(mode="json") == before
    source = next(entry.payload["content"] for entry in original.evidence if entry.kind == "case")
    assert len(source["steps"]) == 8 and source["steps"][-1]["business"] == {"flag": False, "zero": 0, "nullable": None}


@pytest.mark.parametrize("fault", ["unknown_item", "duplicate_item", "unread_source", "modified_logic"])
def test_technical_merge_cannot_replace_fixed_logic_or_read_claims(fault):
    original = captured_plan()
    fixed = seal_logical_decision(LogicalPlanningDecision.model_validate(logical_only(original.decision.model_dump(mode="json"))))
    binding = {"item_key": "target", "evidence_ids": original.decision.items[1].evidence_ids}
    values = [binding]
    if fault == "unknown_item": binding["item_key"] = "foreign"
    elif fault == "duplicate_item": values.append(deepcopy(binding))
    elif fault == "unread_source": binding["evidence_ids"] = ["unread"]
    else: fixed.logical_decision.summary = "改写固定逻辑"
    with pytest.raises(ValueError):
        merge_evidence_decision(fixed, EvidencePlanningDecision(action="finish", summary="声明", bindings=values), original.evidence)


async def setup_actual(env, tmp_path, max_rounds=5, policy=POLICY):
    project = await env.projects.create(ProjectCreateRequest(project_key="staged-plan", name="分阶段真实依据"))
    content = {"objective": "完整查询与必要准备", "steps": [{"order": i + 1, "action": "完整业务步骤"} for i in range(8)],
        "typed": [False, 0, None]}
    case = await active_case(env, project, "staged-target", content)
    source = tmp_path / "staged-requirements.md"
    source.write_text("# 查询要求\n先按项目资料创建记录，再查询其真实ID，最后清理本次创建的数据。\n负向条件保持。\n", encoding="utf-8")
    doc = await env.docs.upload_document(project_id=project.id, filename=source.name,
        content_base64=base64.b64encode(source.read_bytes()).decode())
    assert (await env.objects.read_object_uri(doc.storage_uri))["content"] == source.read_bytes()
    await env.service.save_policy(project.id, policy.model_copy(update={"max_rounds": max_rounds}))
    request = CasePlanningTaskRequest(creation_key="staged", objective=content["objective"], candidate_case_ids=[case.case.id])
    task = await env.tasks.submit(project.id, request)
    return project, case, source, doc, task, request


def logical_finish(context):
    target = item(context, "staged-target", key="target", depends_on=["seed"])
    refs = [*target["evidence_ids"], next(e["id"] for e in context["evidence"] if e["kind"] == "document")]
    return finish({"key": "seed", "kind": "prepare", "reason": "查询必须存在的记录", "evidence_ids": refs,
        "instructions": {"method": "POST", "url": "/orders", "expected": 201}}, target,
        {"key": "cleanup", "kind": "prepare", "reason": "清理本次记录", "evidence_ids": refs,
        "depends_on": ["target"], "cleanup_sources": ["seed"],
        "instructions": {"method": "DELETE", "url": "/orders/{id}", "expected": 200}})


def effects_finish(context, missing=False):
    assert context["planning_stage"] == "effects"
    assert context["logical_hash"] == logical_decision_hash(PlanningDecision.model_validate(context["logical_plan"]))
    ref = next(e["id"] for e in context["evidence"] if e["kind"] == "tool")
    bindings = [{"item_key": "seed", "evidence_ids": [ref], "effect_evidence": declaration("produced")},
        {"item_key": "target", "evidence_ids": [ref], "input_bindings": {"id": {
            "source_item": "seed", "output_path_tokens": ["body", "order.id"]}}}]
    if not missing: bindings.append({"item_key": "cleanup", "evidence_ids": [ref], "effect_evidence": declaration("cleanup")})
    return {"action": "finish", "summary": "固定计划绑定输出证据", "bindings": bindings}


def responses(doc, missing=False):
    return [{"action": "query", "summary": "读取原文", "queries": [{"kind": "document", "source_id": doc.id}]}, logical_finish,
        {"action": "query", "summary": "读取固定原生操作", "queries": [{"kind": "tool", "source_id": "api-test-runner", "operation": "execute_task"}]},
        lambda c: effects_finish(c, missing)]


@pytest.mark.asyncio
@live
async def test_actual_empty_no_target_is_rejected_then_structured_gap_finishes(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="no-target-gap", name="无目标缺口回放"))
        relation_policy = CasePlanningPolicy.model_validate({
            **POLICY.model_dump(mode="json"),
            "preparation_scope_protocol": "preparation-scope-v2",
            "effect_evidence_protocol": "case-effect-evidence-v2",
            "effect_coverage_protocol": "case-effect-coverage-v2",
            "planning_stage_protocol": RELATION_STAGE_PROTOCOL,
        })
        await env.service.save_policy(project.id, relation_policy)
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(
            creation_key="actual-no-target-gap", objective="查询当前项目里不存在的退款记录用例"))
        env.model.replies = [
            {
                "action": "finish",
                "summary": "前置用例已创建订单，实际响应HTTP 201。",
                "items": [],
                "unresolved": [],
            },
            {
                "action": "finish",
                "summary": "没有匹配的正式目标用例。",
                "items": [],
                "unresolved": [{
                    "condition": "当前项目没有覆盖退款记录查询的正式目标用例",
                    "reason": "当前项目已读候选中没有退款记录查询目标，也没有可作为该目标的正式用例。",
                }],
            },
        ]
        await PlanningTaskHandler(env.service, env.jobs, worker_id="no-target-gap")(delivery(task))
        terminal = await env.jobs.get(project.id, task.id)
        plan = await env.service.get(project.id, terminal.plan_id)
        assert terminal.status == "completed" and terminal.round_count == 2
        assert len(env.model.requests) == 2
        feedback = json.loads(env.model.requests[1].messages[0]["content"])["validation_feedback"]
        assert "没有目标用例" in feedback["error"]
        assert plan.status == "no_match"
        assert plan.summary == "未找到符合本次目标的正式用例；本次未创建测试运行。"
        assert plan.decision.unresolved[0].condition == "当前项目没有覆盖退款记录查询的正式目标用例"


async def export(env, project, source, task, name):
    if not os.getenv("QA_STAGED_PLANNING_DIR"): return
    path = Path(os.environ["QA_STAGED_PLANNING_DIR"]) / name
    path.mkdir(parents=True, exist_ok=False)
    (path / source.name).write_bytes(source.read_bytes())
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT seed,checkpoint,status,deadline_at,attempt_no FROM {env.jobs._table} WHERE id=%s", (task.id,))
        row = dict(cur.fetchone())
    row["deadline_at"] = row["deadline_at"].isoformat() if row["deadline_at"] else None
    data = {"actual_PG": row, "actual_MySQL": (await env.service.get_policy(project.id)).model_dump(mode="json"),
        "actual_requests": [r.model_dump(mode="json") for r in env.model.requests], "controlled_model": True,
        "original_object_read_back_verified": True, "business_HTTP": 0, "full_P0_P5_complete": False}
    (path / "verification.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.asyncio
@live
async def test_actual_logic_commit_ack_loss_and_fixed_stage_recovery(tmp_path, monkeypatch):
    async with tasks_environment(tmp_path) as env:
        project, case, source, doc, task, request = await setup_actual(env, tmp_path)
        env.model.replies = responses(doc)
        original = env.jobs.save_checkpoint
        async def lose_ack(claim, checkpoint, *, message):
            saved = await original(claim, checkpoint, message=message)
            if checkpoint.phase == "ready" and checkpoint.stages.stage == "effects" and checkpoint.next_round == 2:
                raise RuntimeError("actual PG stage transition committed; acknowledgement lost")
            return saved
        monkeypatch.setattr(env.jobs, "save_checkpoint", lose_ack)
        with pytest.raises(PlanningCheckpointUnavailable):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="logic-before-loss")(delivery(task))
        before = await env.jobs.get(project.id, task.id)
        assert before.phase == "ready" and before.plan_id is None and len(env.model.requests) == 2
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        def forbidden(*args, **kwargs): raise AssertionError("Current registration must not replace frozen seed")
        monkeypatch.setattr("src.application.test_cases.planning_service.capture_tool_contracts", forbidden)
        monkeypatch.setattr("src.application.test_cases.planning_service.capture_tool_operations", forbidden)
        monkeypatch.setattr(env.jobs, "save_checkpoint", original)
        expire(env, task)
        store = PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service, store, worker_id="effects-recovered")(delivery(task))
        terminal = await store.get(project.id, task.id)
        assert terminal.status == "completed" and terminal.round_count == 4 and terminal.attempt_no == 2
        assert terminal.deadline_at == before.deadline_at and len(env.model.requests) == 4
        plan = await env.service.get(project.id, terminal.plan_id)
        assert plan.status == "planned" and plan.prompt_version == STAGED_PROMPT_VERSION
        assert [i.key for i in plan.decision.items] == ["seed", "target", "cleanup"]
        assert plan.decision.items[1].case_version_id == case.version.id
        assert plan.decision.items[1].input_bindings["id"].output_path_tokens == ["body", "order.id"]
        for call in env.model.requests:
            context = json.loads(call.messages[0]["content"])
            assert next(e["payload"]["content"] for e in context["evidence"] if e["kind"] == "case")["typed"] == [False, 0, None]
            assert "DO-NOT-INJECT-TEMPLATE" not in call.model_dump_json()
            assert ("tool_catalog" in context) == (context["planning_stage"] == "effects")
        await PlanningTaskHandler(env.service, store, worker_id="terminal")(delivery(task))
        assert await env.tasks.submit(project.id, request) == terminal and len(env.model.requests) == 4
        await export(env, project, source, task, "ack-recovery")


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("mode", ["missing_cleanup", "shared_budget", "no_target"])
async def test_actual_final_validation_and_shared_round_budget(tmp_path, mode):
    async with tasks_environment(tmp_path) as env:
        project, _, source, doc, task, _ = await setup_actual(env, tmp_path, max_rounds=4 if mode == "missing_cleanup" else 2)
        env.model.replies = ([{"action": "finish", "summary": "本项目无匹配目标", "items": []}] if mode == "no_target"
            else responses(doc, missing=mode == "missing_cleanup"))
        await PlanningTaskHandler(env.service, env.jobs, worker_id=mode)(delivery(task))
        terminal = await env.jobs.get(project.id, task.id)
        plan = await env.service.get(project.id, terminal.plan_id)
        if mode == "no_target":
            assert terminal.status == "completed" and plan.status == "no_match" and len(env.model.requests) == 1
        else:
            assert terminal.status == "failed" and plan.status == "planning_failed" and plan.decision is None
            assert len(env.model.requests) == (4 if mode == "missing_cleanup" else 2)
            assert ("清理证据声明" in plan.summary) if mode == "missing_cleanup" else ("回合限制" in plan.summary)
        await export(env, project, source, task, mode)


@pytest.mark.asyncio
@live
async def test_actual_PG_rejects_rewritten_logic_even_with_recomputed_hash(tmp_path, monkeypatch):
    async with tasks_environment(tmp_path) as env:
        project, _, source, doc, task, _ = await setup_actual(env, tmp_path)
        env.model.replies = responses(doc)
        original = env.jobs.save_checkpoint
        async def stop_after_logic(claim, checkpoint, *, message):
            saved = await original(claim, checkpoint, message=message)
            if checkpoint.stages.stage == "effects": raise RuntimeError("Stop after actual fixed commit")
            return saved
        monkeypatch.setattr(env.jobs, "save_checkpoint", stop_after_logic)
        with pytest.raises(PlanningCheckpointUnavailable):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="freeze")(delivery(task))
        expire(env, task)
        claim = await env.jobs.claim(project.id, task.id, worker_id="mutation-probe")
        mutated = claim.checkpoint.model_copy(deep=True)
        mutated.stages.logical_decision.summary = "替换逻辑"
        mutated.stages.logical_hash = logical_decision_hash(mutated.stages.logical_decision)
        monkeypatch.setattr(env.jobs, "save_checkpoint", original)
        with pytest.raises(ValueError, match="改写固定逻辑"):
            await env.jobs.save_checkpoint(claim, mutated, message="must reject")
        await export(env, project, source, task, "hash-fence")
