from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from test_case_planning import SequenceModel, active_case, environment, finish, item
from src.application.test_cases.planning_service import PROMPT_VERSION
from src.application.test_cases.planning_store import PostgresCasePlanStore
from src.application.test_runs.dependencies import output_at_path
from src.schemas.case_planning import CasePlanningRequest, PlanInputBinding
from src.schemas.project import ProjectCreateRequest


@pytest.mark.parametrize("tokens,value", [
    (["body", "order.id"], "literal-id"),
    (["body", ""], 0),
    (["body", "values", "-1"], False),
    ([], {"body": {"order.id": "literal-id", "": 0, "values": [False]}}),
])
def test_literal_binding_paths_preserve_json_keys_and_values(tokens, value):
    binding = PlanInputBinding(source_item="create", output_path_tokens=tokens)
    actual = {"body": {"order.id": "literal-id", "": 0, "values": [False]}}
    assert binding.path_tokens == tokens
    assert output_at_path(actual, binding.path_tokens) == value
    restored = PlanInputBinding.model_validate_json(binding.model_dump_json())
    assert restored.path_tokens == tokens


def test_existing_dotted_paths_remain_readable_but_conflicting_paths_are_rejected():
    binding = PlanInputBinding.model_validate({"source_item": "create", "output_path": "body.id", "required": True})
    assert binding.path_tokens == ["body", "id"]
    assert PlanInputBinding(input_key="existing").path_tokens == []
    with pytest.raises(ValueError, match="路径"):
        PlanInputBinding(source_item="create", output_path="body.id", output_path_tokens=["body", "order.id"])


@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.getenv("RUN_LIVE_PLAN_BINDING_PATHS") != "1", reason="requires isolated MySQL/PostgreSQL/RustFS"))])
@pytest.mark.asyncio
async def test_saved_plan_binding_gates_claim_using_actual_saved_result(tmp_path, live):
    from test_run_item_dependencies import setup, finish as complete
    from src.schemas.run_management import RunItemDependency
    async with setup(tmp_path, live) as (env, store, run, items, now):
        env.service._models = SequenceModel(lambda context: finish(
            item(context, "item-0", kind="dependency"), item(context, "item-1", depends_on=["item-0"],
                input_bindings={"id": {"source_item": "item-0", "output_path_tokens": ["body", "order.id"]},
                                "enabled": {"source_item": "item-0", "output_path_tokens": ["body", "enabled"]}})))
        plan = await env.service.create(run.project_id, CasePlanningRequest(objective="验证数据引用与领取",
            candidate_case_ids=[i.case_id for i in items[:2]]))
        assert plan.status == "planned", plan.summary
        bindings = plan.decision.items[-1].input_bindings
        # This test explicitly maps the plan; production plan-to-run service is still pending.
        items[1].dependencies = [RunItemDependency(item_id=items[0].id,
            required_output_paths=[b.path_tokens for b in bindings.values()])]
        await store.create_run(run, items)
        claims = await store.claim_items(run_id=run.id, worker_id="literal-source", limit=5, lease_seconds=60, now=now)
        assert items[1].id not in {i.id for i, _ in claims}
        source_result = None
        for claim in claims:
            result = await complete(store, claim, now, {"body": {"order.id": "saved-literal-id", "enabled": False}})
            if result.run_item_id == items[0].id:
                source_result = result
        assert source_result is not None
        saved_result = await store.get_result(source_result.id)
        assert saved_result is not None
        assert output_at_path(saved_result.actual, bindings["id"].path_tokens) == "saved-literal-id"
        assert output_at_path(saved_result.actual, bindings["enabled"].path_tokens) is False
        assert output_at_path(saved_result.actual, ["body", "order", "id"]) is None
        claims = await store.claim_items(run_id=run.id, worker_id="literal-consumer", limit=5, lease_seconds=60, now=now)
        assert [i.id for i, _ in claims] == [items[1].id]
        await complete(store, claims[0], now, {"bound_inputs": {
            key: output_at_path(saved_result.actual, binding.path_tokens) for key, binding in bindings.items()}})
        assert (await store.get_run(run.id)).run.status == "completed"


@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.getenv("RUN_LIVE_PLAN_BINDING_PATHS") != "1", reason="requires isolated MySQL/PostgreSQL/RustFS"))])
@pytest.mark.asyncio
async def test_actual_json_file_http_plan_keeps_literal_paths_and_old_plan(tmp_path, live):
    def response(context):
        return finish(item(context, "create", kind="dependency"), item(context, "query", depends_on=["create"],
            input_bindings={"id": {"source_item": "create", "output_path_tokens": ["body", "order.id"]},
                            "flags": {"input_key": "settings", "output_path_tokens": ["", "enabled"]}}))
    async with environment(tmp_path, live=live, model=SequenceModel(response, response)) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="literal", name="字面量输出路径"))
        source = tmp_path / "literal-output-requirements.json"
        source.write_text(json.dumps({"objective": "创建业务标识", "output": {"body": {"order.id": "本次创建的实际ID"}}},
            ensure_ascii=False), encoding="utf-8")
        document = await env.docs.upload_document(filename=source.name, project_id=project.id,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        stored_document = await env.docs.read_document_content(document.id)
        assert json.loads(stored_document["content"]) == json.loads(source.read_text(encoding="utf-8"))
        await active_case(env, project, "create", json.loads(source.read_text(encoding="utf-8")))
        await active_case(env, project, "query", {"objective": "查询已有数据", "requires": ["id", "flags"]})
        request = {"objective": "查询创建后的数据", "runtime_context": {"settings": {"": {"enabled": False}}}}
        response = await env.client.post(f"/projects/{project.id}/case-plans", json=request)
        response.raise_for_status()
        assert response.json()["status"] == "planned", response.json()["summary"]
        record = await env.service.get(project.id, response.json()["id"])
        binding = record.decision.items[-1].input_bindings["id"]
        assert binding.path_tokens == ["body", "order.id"]
        assert record.prompt_version == PROMPT_VERSION
        assert output_at_path({"body": {"order.id": "real-result"}}, binding.path_tokens) == "real-result"
        flags = record.decision.items[-1].input_bindings["flags"]
        assert output_at_path(record.request.runtime_context[flags.input_key], flags.path_tokens) is False
        if live:
            reread = await PostgresCasePlanStore(env.settings).get(project.id, record.id)
            assert reread.decision == record.decision
        # Pre-upgrade records have no token-array field; keep their stored prompt and path.
        old = record.model_dump(mode="json")
        from uuid import uuid4
        old["id"] = str(uuid4())
        old["prompt_version"] = "case-planning-v2"
        old["decision"]["items"][-1]["input_bindings"]["id"] = {
            "source_item": "create", "input_key": None, "output_path": "body.id", "required": True}
        from src.schemas.case_planning import CasePlanRecord
        await env.store.save(CasePlanRecord.model_validate(old))
        assert (await env.service.get(project.id, old["id"])).decision.items[-1].input_bindings["id"].path_tokens == ["body", "id"]
        assert (await env.service.get(project.id, old["id"])).prompt_version == "case-planning-v2"


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PLAN_BINDING_MODEL") != "1", reason="requires current actual model and real storage")
async def test_actual_model_uses_literal_path_from_full_case(tmp_path):
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.registry.models import ModelRegistry
    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="literal-model", name="模型输出路径验收"))
        await active_case(env, project, "create", {"objective": "创建订单", "operation": "POST /fixtures",
            "expected": 201, "actual_output_shape": {"body": {"order.id": "真实订单ID"}},
            "output_notes": "order.id 是一个完整的JSON键，不是嵌套的order和id。"})
        query = await active_case(env, project, "query", {"objective": "查询已有订单", "operation": "GET /fixtures/{id}",
            "requires": ["id"], "expected": 200})
        env.service._models = ModelRuntimeService(model_registry=ModelRegistry(MySQLModelConfigStore(env.settings)), settings=env.settings)
        record = await env.service.create(project.id, CasePlanningRequest(
            objective="查询创建后的订单。先从正式创建用例取得ID；响应body内的order.id是一个完整键名，必须用字面量路径保存数据来源。",
            candidate_case_ids=[query.case.id]))
        assert record.status == "planned", record.summary
        target = next(i for i in record.decision.items if i.kind == "target")
        binding = target.input_bindings["id"]
        assert binding.output_path_tokens == ["body", "order.id"]
        assert binding.source_item in target.depends_on
        assert (await PostgresCasePlanStore(env.settings).get(project.id, record.id)).decision == record.decision
        (Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p2-binding-model.json").write_text(json.dumps({
            "scope": "actual model planning and persisted path; business execution not covered",
            "model_key": record.model_key, "prompt_version": record.prompt_version, "status": record.status,
            "rounds": len(record.rounds), "output_path_tokens": binding.output_path_tokens,
            "formal_dependency_count": sum(i.kind == "dependency" for i in record.decision.items),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
