from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_case_json_templates import _stores
from src.api.routes.case_plans import router
from src.application.documents.api_docs_service import ApiDocsService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_service import TestCaseService as CaseService
from src.application.test_cases.planning_service import CasePlanningService, PLANNING_CONFIG_KEY
from src.application.test_cases.planning_sources import ProjectPlanningSources
from src.application.test_cases.planning_store import InMemoryCasePlanStore, PostgresCasePlanStore
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.models import ModelRegistry
from src.schemas.case_management import TestCaseDraftCreateRequest as DraftRequest
from src.schemas.case_planning import CasePlanningPolicy, CasePlanningRequest, PlanningQuery
from src.schemas.model_config import ModelInvocationResult
from src.schemas.project import ProjectCreateRequest


class SequenceModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def get_default_model_config(self): return SimpleNamespace(key="fixture-model", capabilities=SimpleNamespace(streaming=False))
    def get_model_config(self, key): return SimpleNamespace(key=key, capabilities=SimpleNamespace(streaming=False))

    async def invoke(self, key, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception): raise reply
        if callable(reply):
            reply = reply(json.loads(request.messages[0]["content"]))
            if asyncio.iscoroutine(reply): reply = await reply
        return ModelInvocationResult(text=reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))


@asynccontextmanager
async def environment(tmp_path, live=False, model=None, embeddings=None):
    async with _stores(tmp_path, live) as (settings, project_store, case_store, configs, doc_store, objects):
        projects = ProjectService(store=project_store)
        await projects.initialize()
        cases = CaseService(store=case_store, project_service=projects)
        await cases.initialize()
        docs = ApiDocsService(settings=settings, artifact_storage_service=objects, catalog_store=doc_store, project_service=projects)
        await docs.initialize()
        if live:
            settings.database.postgres_case_plan_table = "up_pl_" + uuid4().hex[:12]
            with postgres_connect(settings) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass(%s) AS existing", (settings.database.postgres_case_plan_table,))
                assert cursor.fetchone()["existing"] is None
            store = PostgresCasePlanStore(settings)
        else:
            store = InMemoryCasePlanStore()
        sources = ProjectPlanningSources(case_service=cases, api_docs_service=docs)
        service = CasePlanningService(store=store, config_store=configs, project_service=projects, sources=sources,
            model_runtime_service=model or SequenceModel(), embedding_service=embeddings)
        await service.initialize()
        app = FastAPI()
        app.state.case_planning_service = service
        app.include_router(router, prefix="/api/v1")
        owned_documents = []
        upload = docs.upload_document
        async def track_upload(**kwargs):
            document = await upload(**kwargs)
            owned_documents.append(document.id)
            return document
        docs.upload_document = track_upload
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
                yield SimpleNamespace(settings=settings, projects=projects, cases=cases, configs=configs,
                    docs=docs, sources=sources, service=service, store=store, client=client, objects=objects)
        finally:
            for doc_id in owned_documents: await docs.delete_document(doc_id)
            if live:
                with postgres_connect(settings) as connection, connection.cursor() as cursor:
                    cursor.execute(f'DROP TABLE "{settings.database.postgres_case_plan_table}"')


async def active_case(env, project, key, content, mode="api_testing"):
    file = env.settings.data_dir + "/" + key + ".json"
    from pathlib import Path
    Path(file).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    bundle = await env.cases.create_draft(project_id=project.id, payload=DraftRequest(
        case_key=key, title=content["objective"], case_type="json", mode_key=mode,
        content_json=json.loads(Path(file).read_text(encoding="utf-8")), execution_notes="读取全部步骤；保持负向条件。",
        source_refs=[{"source_type": "manual", "source_id": key}], model_key="manual",
        prompt_version="manual-v1", skill_versions={"manual": "v1"},
        generation_metadata={"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}))
    await env.cases.submit_review(bundle.case.id)
    await env.cases.activate(bundle.case.id)
    return bundle


def item(context, case_key, key=None, kind="target", **extra):
    evidence = next(e for e in context["evidence"] if e["kind"] == "case" and e["payload"]["case_key"] == case_key)
    return {"key": key or case_key, "kind": kind, "case_id": evidence["source_id"], "case_version_id": evidence["version"],
            "evidence_ids": [evidence["id"]], "reason": "依据完整用例规划", **extra}


def finish(*items, **extra): return {"action": "finish", "summary": "已按项目依据规划", "items": list(items), **extra}


LIVE = pytest.param(True, marks=pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_PLANNING_TESTS") != "1", reason="requires isolated real MySQL/PostgreSQL/RustFS"))


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_full_versions_cross_mode_query_dependency_and_immutable_http_plan(tmp_path, live):
    model = SequenceModel(
        {"action": "query", "summary": "查询项目中可创建数据的用例", "queries": [{"kind": "cases", "query": "创建"}]},
        lambda context: finish(item(context, "query", depends_on=["create"], input_bindings={"id": {"source_item": "create", "output_path": "body.id"}}),
                               item(context, "create", kind="dependency")))
    async with environment(tmp_path, live, model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="plan", name="Plan"))
        other = await env.projects.create(ProjectCreateRequest(project_key="other", name="Other"))
        query = await active_case(env, project, "query", {"objective": "查询已有数据", "steps": [{"order": i, "action": "核对步骤"} for i in range(1, 7)] + [{"order": 7, "action": "末尾关键验证😀", "requires": "id"}]})
        dependency = await active_case(env, project, "create", {"objective": "创建测试数据", "operation": "POST /fixtures", "output": "body.id"}, mode="ui_automation")
        await active_case(env, other, "foreign", {"objective": "创建跨项目数据", "operation": "DO-NOT-LEAK"})
        response = await env.client.post(f"/projects/{project.id}/case-plans", json={"objective": "验证查询", "candidate_case_ids": [query.case.id]})
        response.raise_for_status()
        plan = response.json()
        assert plan["status"] == "planned"
        assert [i["key"] for i in plan["decision"]["items"]] == ["create", "query"]
        assert plan["evidence"][0]["payload"]["content"]["steps"][-1]["action"] == "末尾关键验证😀"
        assert plan["decision"]["items"][0]["case_version_id"] == dependency.version.id
        for request in model.requests:
            assert "DO-NOT-INJECT-TEMPLATE" not in request.messages[0]["content"]
            assert "DO-NOT-LEAK" not in request.messages[0]["content"]
            assert request.tools == []
        policy = CasePlanningPolicy(max_items=12)
        await env.service.save_policy(project.id, policy)
        assert (await env.service.get_policy(other.id)).scope_key == "global"
        assert (await env.service.get(project.id, plan["id"])).config_snapshot.policy.max_items == 50
        assert (await env.client.get(f"/projects/{other.id}/case-plans/{plan['id']}")).status_code == 404
        record = await env.service.get(project.id, plan["id"])
        mutated = record.model_copy(update={"summary": "cannot replace"})
        assert (await env.store.save(mutated)).summary == record.summary
        record.summary = "local mutation"
        assert (await env.service.get(project.id, plan["id"])).summary != record.summary
        if live:
            assert (await PostgresCasePlanStore(env.settings).get(project.id, plan["id"])).decision.items[1].case_version_id == query.version.id
            assert MySQLApplicationConfigStore(env.settings).get(PLANNING_CONFIG_KEY, scope_key=project.id).payload["max_items"] == 12
        await env.projects.archive(project.id)
        assert (await env.client.put(f"/projects/{project.id}/case-plans/policy", json=policy.model_dump())).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_actual_document_runtime_preparation_preserves_negative_case(tmp_path, live):
    def plan(context):
        doc = next(e for e in context["evidence"] if e["kind"] == "document")
        return finish(item(context, "positive", depends_on=["seed"], input_bindings={"id": {"source_item": "seed", "output_path": "body.id"}}),
            item(context, "negative"), {"key": "seed", "kind": "prepare", "reason": "文档提供创建方法而无正式创建用例",
                "evidence_ids": [doc["id"]], "instructions": {"action": "POST /fixtures", "output": "body.id"}})
    model = SequenceModel(lambda context: {"action": "query", "summary": "缺少创建依据，读取原文", "queries": [{"kind": "document", "source_id": context["catalog"]["documents"][0]["id"]}]}, plan)
    async with environment(tmp_path, live, model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="docs", name="Docs"))
        positive = await active_case(env, project, "positive", {"objective": "查询已有数据", "requires": "id"})
        negative = await active_case(env, project, "negative", {"objective": "不存在数据应返回404", "id": "absent", "expected": 404})
        file = tmp_path / "requirements.md"
        file.write_text("# 实际准备接口\nPOST /fixtures 创建测试数据，响应 body.id。\nGET /fixtures/{id} 返回内容；不存在返回404。\n", encoding="utf-8")
        document = await env.docs.upload_document(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode(), project_id=project.id)
        result = await env.service.create(project.id, CasePlanningRequest(objective="验证查询正常及不存在", candidate_case_ids=[positive.case.id, negative.case.id]))
        assert result.status == "planned"
        seed = next(i for i in result.decision.items if i.kind == "prepare")
        assert seed.case_id is None and seed.case_version_id is None
        assert next(i for i in result.decision.items if i.key == "negative").depends_on == []
        evidence = next(e for e in result.evidence if e.kind == "document")
        assert evidence.source_id == document.id and evidence.payload["start_line"] == 1
        assert "POST /fixtures" in evidence.payload["content"]
        assert (await env.cases.list_cases(project_id=project.id, status=None, mode_key=None, priority=None, query=None, limit=50, offset=0)).items.__len__() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("defect,expected", [("cycle", "循环"), ("ref", "来源依据"), ("binding", "前置执行项"), ("input", "运行输入"), ("extra", "额外执行项"), ("rewrite", "结构化计划")])
async def test_invalid_model_plans_are_saved_as_failures_without_run(tmp_path, defect, expected):
    def response(context):
        first, second = item(context, "one"), item(context, "two", kind="dependency")
        first["depends_on"] = ["two"]
        if defect == "cycle": second["depends_on"] = ["one"]
        if defect == "ref": first["evidence_ids"] = ["invented"]
        if defect == "binding": second["input_bindings"] = {"id": {"source_item": "one"}}
        if defect == "input": first["input_bindings"] = {"id": {"input_key": "missing"}}
        if defect == "extra": first["depends_on"] = []
        if defect == "rewrite": first["instructions"] = {"expected": "changed"}
        return finish(first, second)
    async with environment(tmp_path, model=SequenceModel(response)) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="invalid", name="Invalid"))
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        await active_case(env, project, "one", {"objective": "目标"})
        await active_case(env, project, "two", {"objective": "前置"})
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planning_failed" and expected in result.summary
        assert result.decision is None
        assert (await env.store.get(project.id, result.id)).status == "planning_failed"


@pytest.mark.asyncio
async def test_retrieval_failure_no_match_and_unresolved_are_distinct(tmp_path):
    class BrokenEmbedding:
        async def retrieve(self, **kwargs): raise ConnectionError("fixture transport failed")
    model = SequenceModel(finish())
    async with environment(tmp_path, model=model, embeddings=BrokenEmbedding()) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="empty", name="Empty"))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "retrieval_failed" and model.requests == []
        env.service._embeddings = None
        result = await env.service.create(project.id, CasePlanningRequest(objective="不匹配"))
        assert result.status == "no_match" and result.decision.items == []
        await active_case(env, project, "one", {"objective": "目标", "requires": "未知条件"})
        model.replies.append(lambda context: finish(item(context, "one"), unresolved=[{"item_key": "one", "condition": "未知条件", "reason": "缺少项目依据"}]))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "blocked" and result.decision.unresolved[0].item_key == "one"


@pytest.mark.asyncio
async def test_actual_reference_copy_error_gets_bounded_feedback_without_silent_replacement(tmp_path):
    from pathlib import Path
    actual = json.loads((Path(__file__).resolve().parents[2] /
        "docs/upgrade_2026-10-03/p2-rejected-evidence-reference.json").read_text(encoding="utf-8"))
    available = {e["id"] for e in actual["evidence"]}
    wrong = actual["rounds"][-1]["decision"]["items"][0]["evidence_ids"][-1]
    assert wrong not in available and wrong[:-1] in available
    def rejected(context):
        selected = item(context, "one")
        selected["evidence_ids"][0] += wrong[-1]
        return finish(selected)
    def corrected(context):
        feedback = context["validation_feedback"]
        assert "来源依据" in feedback["error"]
        assert feedback["available_evidence_ids"] == [context["evidence"][0]["id"]]
        assert json.loads(feedback["candidate_response"])["items"][0]["evidence_ids"][0] not in feedback["available_evidence_ids"]
        return finish(item(context, "one"))
    model = SequenceModel(rejected, corrected)
    async with environment(tmp_path, model=model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="repair", name="Repair"))
        await active_case(env, project, "one", {"objective": "目标"})
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=2))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planned" and len(model.requests) == 2
        assert result.rounds[0]["status"] == "validation_rejected"
        assert result.rounds[1]["status"] == "parsed"
        assert result.decision.items[0].evidence_ids == [result.evidence[0].id]
        model.replies.extend([rejected, rejected])
        exhausted = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert exhausted.status == "planning_failed" and exhausted.decision is None
        assert len(exhausted.rounds) == 2 and "来源依据" in exhausted.summary


@pytest.mark.asyncio
async def test_changed_version_and_foreign_read_are_not_frozen(tmp_path):
    async with environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="scope", name="Scope"))
        other = await env.projects.create(ProjectCreateRequest(project_key="foreign", name="Foreign"))
        target = await active_case(env, project, "one", {"objective": "目标"})
        foreign = await active_case(env, other, "foreign", {"objective": "外部"})
        model = SequenceModel()
        env.service._models = model
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标", candidate_case_ids=[foreign.case.id]))
        assert result.status == "retrieval_failed" and not result.evidence and model.requests == []
        async def archive(context):
            await env.cases.archive(target.case.id)
            return finish(item(context, "one"))
        model.replies.append(archive)
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标", candidate_case_ids=[target.case.id]))
        assert result.status == "planning_failed" and result.decision is None


@pytest.mark.asyncio
async def test_context_budget_timeout_and_round_limit_do_not_publish_plan(tmp_path):
    async with environment(tmp_path, model=SequenceModel()) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="budget", name="Budget"))
        await active_case(env, project, "large", {"objective": "大用例", "full": "内容" * 10000})
        await env.service.save_policy(project.id, CasePlanningPolicy(max_context_chars=10000))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planning_failed" and "未截断用例" in result.summary
        assert not env.service._models.requests
        async def wait(context): await asyncio.sleep(1); return finish()
        env.service._models = SequenceModel(wait)
        await env.service.save_policy(project.id, CasePlanningPolicy(timeout_seconds=.02))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planning_failed" and "超时" in result.summary
        env.service._models = SequenceModel({"action": "query", "summary": "继续找依据", "queries": [{"kind": "cases", "query": "缺失"}]})
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planning_failed" and "回合限制" in result.summary


@pytest.mark.asyncio
async def test_cleanup_after_target_and_stream_metrics_use_existing_model_runtime(tmp_path):
    class StreamingModel(SequenceModel):
        def get_default_model_config(self): return SimpleNamespace(key="fixture-model", capabilities=SimpleNamespace(streaming=True))
        @asynccontextmanager
        async def stream_handler(self, callback):
            self.callback = callback
            try: yield
            finally: self.callback = None
        async def invoke(self, key, request):
            await self.callback("partial")
            await self.callback("response")
            return await super().invoke(key, request)
    def response(context):
        return finish(item(context, "one"), {"key": "cleanup", "kind": "prepare", "instructions": {"action": "移除本次数据"},
            "reason": "按用例清理要求在验证后执行", "evidence_ids": [context["evidence"][0]["id"]], "depends_on": ["one"]})
    model = StreamingModel(response)
    async with environment(tmp_path, model=model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="stream", name="Stream"))
        await active_case(env, project, "one", {"objective": "目标", "cleanup": "验证后移除本次数据"})
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planned" and [i.key for i in result.decision.items] == ["one", "cleanup"]
        assert result.rounds[0]["received_text_chars"] == 15
        assert result.rounds[0]["first_text_seconds"] >= 0
        assert model.callback is None


@pytest.mark.asyncio
async def test_provider_error_is_not_misreported_as_json_error(tmp_path):
    model = SequenceModel()
    async def fail(key, request):
        return ModelInvocationResult(text="provider unavailable", response_summary={"mode": "http_error", "error_type": "ProviderClientError", "status_code": 503, "response_body": "DO-NOT-LOG"})
    model.invoke = fail
    async with environment(tmp_path, model=model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="provider", name="Provider"))
        result = await env.service.create(project.id, CasePlanningRequest(objective="目标"))
        assert result.status == "planning_failed" and "模型调用失败（http_error）" in result.summary
        assert result.rounds[0]["status_code"] == 503
        assert "DO-NOT-LOG" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_semantic_recall_does_not_filter_mode_or_execute_a_summary(tmp_path):
    async with environment(tmp_path, model=SequenceModel(lambda c: finish(item(c, "one")))) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="semantic", name="Semantic"))
        case = await active_case(env, project, "one", {"objective": "验证", "steps": [{"action": "摘要以外的真实操作"}]}, mode="ui_automation")
        class Embedding:
            async def retrieve(self, **kwargs):
                assert kwargs["project_id"] == project.id and kwargs["mode_key"] is None
                return SimpleNamespace(hits=[SimpleNamespace(case=case.case)], prompt_blocks=["DO-NOT-EXECUTE-SUMMARY"])
        env.service._embeddings = Embedding()
        result = await env.service.create(project.id, CasePlanningRequest(objective="验证"))
        assert result.status == "planned"
        assert result.evidence[0].payload["content"]["steps"][0]["action"] == "摘要以外的真实操作"
        assert "DO-NOT-EXECUTE-SUMMARY" not in env.service._models.requests[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_project_catalog_exposes_formal_cases_and_supports_paging_by_identity(tmp_path):
    async with environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="catalog", name="Catalog"))
        other = await env.projects.create(ProjectCreateRequest(project_key="not-catalog", name="Other"))
        one = await active_case(env, project, "one", {"objective": "生成业务标识", "outputs": "order_id"})
        two = await active_case(env, project, "two", {"objective": "目标"})
        foreign = await active_case(env, other, "foreign", {"objective": "外部"})
        first = await env.sources.catalog(project.id, 1)
        second = await env.sources.catalog(project.id, 1, offset=1)
        assert first["cases_has_more"] and not second["cases_has_more"]
        ids = {c["id"] for c in first["cases"] + second["cases"]}
        assert ids == {one.case.id, two.case.id} and foreign.case.id not in ids
        full, _ = await env.sources.query(project.id, PlanningQuery(kind="cases", source_id=one.case.id), 1)
        assert full[0].payload["content"]["outputs"] == "order_id"


def test_actual_rejected_output_and_advertised_schema_agree_on_formal_instructions():
    from pathlib import Path
    from src.schemas.case_planning import PlanningDecision
    actual = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p2-rejected-formal-instructions.json"
    payload = json.loads(actual.read_text(encoding="utf-8"))
    assert any(item.get("instructions") for item in payload["items"] if item["kind"] != "prepare")
    with pytest.raises(ValueError, match="instructions"):
        PlanningDecision.model_validate(payload)
    definitions = PlanningDecision.model_json_schema()["$defs"]
    assert "instructions" not in definitions["FormalCasePlanItem"]["properties"]
    assert definitions["FormalCasePlanItem"]["additionalProperties"] is False
    assert "instructions" in definitions["PreparationPlanItem"]["required"]


def test_actual_model_json_with_trailing_text_keeps_preparation_and_fixed_references():
    from pathlib import Path
    actual = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p2-model-output-with-trailing-text.txt"
    text = actual.read_text(encoding="utf-8")
    with pytest.raises(json.JSONDecodeError, match="Extra data"):
        json.loads(text)
    first, end = json.JSONDecoder().raw_decode(text)
    assert text[end:].strip()
    plan = CasePlanningService._parse(text)
    assert plan.action == "finish" and not plan.unresolved
    assert plan.items[0].kind == "prepare" and plan.items[0].case_id is None
    assert len(plan.items[0].instructions["steps"]) == 2
    assert plan.items[1].case_version_id == first["items"][1]["case_version_id"]
    assert plan.items[1].input_bindings["order_id"].source_item == plan.items[0].key
    # Surrounding prose never permits rewriting the fixed formal case.
    first["items"][1]["instructions"] = {"rewrite": "forbidden"}
    with pytest.raises(ValueError, match="结构化计划"):
        CasePlanningService._parse(json.dumps(first) + "\ntrailing fixture text")


@pytest.mark.asyncio
async def test_recording_success_contract_and_document_preview_error_are_visible(tmp_path):
    async with environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="record", name="Record"))
        from datetime import datetime, timezone
        recording = SimpleNamespace(id="r1", project_id=project.id, status=SimpleNamespace(value="completed"),
            name="记录", updated_at=datetime.now(timezone.utc), finalize_metrics={"actions": 1})
        class Recordings:
            async def get_session(self, key): return recording
        class Graphs:
            async def get_recording_subgraph(self, key, *, project_id): return {"status": "success", "recording_id": key, "nodes": [{"id": "action", "label": "点击"}], "edges": []}
        env.sources.set_recording_sources(recording_store=Recordings(), recording_graph_store=Graphs())
        evidence, _ = await env.sources.query(project.id, PlanningQuery(kind="recording", source_id="r1"), 20)
        assert evidence[0].payload["graph"]["status"] == "success"
        doc = await env.docs.upload_document(filename="actual.md", content_base64=base64.b64encode(b"actual").decode(), project_id=project.id)
        original_read = env.docs.read_document_content
        async def unavailable(key): return {"content": "stale preview", "read_error": "fixture object missing", "truncated": False}
        env.docs.read_document_content = unavailable
        with pytest.raises(ValueError, match="旧预览"):
            await env.sources.query(project.id, PlanningQuery(kind="document", source_id=doc.id), 20)
        env.docs.read_document_content = original_read


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_PLANNING_MODEL_TESTS") != "1", reason="requires current configured model and real isolated MySQL/PostgreSQL/RustFS")
async def test_actual_model_selects_dependencies_preserves_negative_and_uses_existing_data(tmp_path):
    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="actual-planning", name="真实模型规划验证"))
        other = await env.projects.create(ProjectCreateRequest(project_key="actual-other", name="隔离项目"))
        file = tmp_path / "order-requirements.md"
        file.write_text(
            "# 受控测试系统需求\n"
            "POST /actors 注册测试操作者，不需要已有账号，响应 JSON body.actor_id。\n"
            "POST /orders 创建订单，需要 actor_id，金额固定36，响应 JSON body.order_id。\n"
            "GET /orders/{order_id} 查询详情，已有订单返回200与金额36，不存在返回404。订单查询不需要登录。\n"
            "GET /admin 是管理页，未登录应返回401。验证未登录拒绝时必须保持未登录，不创建登录前置。\n"
            "已有订单信息可以直接使用；不能把不存在订单的负向用例改成先创建同编号订单。\n",
            encoding="utf-8")
        document = await env.docs.upload_document(filename=file.name, content_base64=base64.b64encode(file.read_bytes()).decode(), project_id=project.id)
        query = await active_case(env, project, "order-detail", {"objective": "查询已有订单并验证金额", "steps": [
            {"order": i, "action": "记录本次环境与可用信息"} for i in range(1, 7)] + [
            {"order": 7, "action": "使用实际已存在的 order_id 请求 GET /orders/{order_id}", "expected": {"status": 200, "body.amount": 36}, "requires": ["order_id"]}]})
        missing = await active_case(env, project, "order-missing", {"objective": "不存在订单返回404", "operation": "GET /orders/not-present", "expected": 404, "precondition": "not-present 不存在，保持此条件"})
        unauth = await active_case(env, project, "admin-unauth", {"objective": "未登录管理页访问被拒绝", "operation": "GET /admin", "precondition": "未登录", "expected": 401})
        create = await active_case(env, project, "order-create", {"objective": "创建测试订单", "requires": ["actor_id"], "operation": "POST /orders", "input": {"actor_id": "实际操作者ID", "amount": 36}, "expected": 201, "output": "body.order_id"}, mode="ui_automation")
        actor = await active_case(env, project, "actor-register", {"objective": "注册测试操作者", "operation": "POST /actors", "requires": [], "expected": 201, "output": "body.actor_id"})
        await active_case(env, other, "foreign-order", {"objective": "创建测试订单", "operation": "DO-NOT-LEAK"})
        runtime = ModelRuntimeService(model_registry=ModelRegistry(MySQLModelConfigStore(env.settings)), settings=env.settings)
        requests = []
        invoke = runtime.invoke
        calls = []
        async def track(key, request):
            from time import perf_counter
            requests.append(request)
            started = perf_counter()
            result = await invoke(key, request)
            call = {"call": len(requests), "elapsed_seconds": round(perf_counter() - started, 3),
                    "response_mode": result.response_summary.get("mode"), "text_chars": len(result.text)}
            calls.append(call)
            print("actual_planning_call " + json.dumps(call), flush=True)
            (tmp_path / f"model-call-{len(requests)}.txt").write_text(result.text, encoding="utf-8")
            if call["response_mode"] == "ok":
                try:
                    CasePlanningService._parse(result.text)
                except ValueError:
                    diagnostic = tmp_path / f"model-output-{len(requests)}.txt"
                    diagnostic.write_text(result.text, encoding="utf-8")
                    print("invalid_fixture_model_output " + str(diagnostic), flush=True)
            return result
        runtime.invoke = track
        env.service._models = runtime
        # This complex scenario can require five serial model calls. Exercise the
        # actual project policy in MySQL; do not mutate the global/default model.
        await env.service.save_policy(project.id, CasePlanningPolicy(timeout_seconds=480))
        plan = await env.service.create(project.id, CasePlanningRequest(
            objective="验证订单详情正常查询、不存在订单返回404，以及未登录管理页返回401；没有已有业务数据。只执行这些验证目标及它们必要的前置。",
            candidate_case_ids=[query.case.id, missing.case.id, unauth.case.id]))
        (tmp_path / "first-plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print("actual_fixture_plan " + str(tmp_path / "first-plan.json"), flush=True)
        assert plan.status == "planned", {"summary": plan.summary, "rounds": plan.rounds}
        target_ids = {i.case_id for i in plan.decision.items if i.kind == "target"}
        assert target_ids == {query.case.id, missing.case.id, unauth.case.id}
        dependency_ids = {i.case_id for i in plan.decision.items if i.kind == "dependency"}
        assert dependency_ids == {create.case.id, actor.case.id}
        assert not [i for i in plan.decision.items if i.kind == "prepare"]
        ordered = [i.case_id for i in plan.decision.items]
        assert ordered.index(actor.case.id) < ordered.index(create.case.id) < ordered.index(query.case.id)
        for case_id in (missing.case.id, unauth.case.id):
            assert next(i for i in plan.decision.items if i.case_id == case_id).depends_on == []
        assert next(i for i in plan.decision.items if i.case_id == query.case.id).input_bindings
        assert next(i for i in plan.decision.items if i.case_id == create.case.id).input_bindings
        existing = await env.service.create(project.id, CasePlanningRequest(
            objective="验证已有订单详情；已有 order_id 输入可直接使用，不需要创建订单或操作者。",
            candidate_case_ids=[query.case.id], runtime_context={"order_id": "owned-existing-order"}))
        assert existing.status == "planned", existing.summary
        assert len(existing.decision.items) == 1 and existing.decision.items[0].case_id == query.case.id
        assert existing.decision.items[0].depends_on == []
        assert any(binding.input_key == "order_id" for binding in existing.decision.items[0].input_bindings.values())
        # Without formal creation/registration cases, the same actual document is
        # sufficient to propose run-local preparation, without enabling new cases.
        await env.cases.archive(create.case.id)
        await env.cases.archive(actor.case.id)
        preparation = await env.service.create(project.id, CasePlanningRequest(
            objective="验证已有订单详情，没有已有业务数据。按项目原文补足必要准备；项目不存在可执行的正式创建订单和注册用例。",
            candidate_case_ids=[query.case.id]))
        assert preparation.status == "planned", preparation.summary
        prepares = [i for i in preparation.decision.items if i.kind == "prepare"]
        assert prepares and {i.case_id for i in preparation.decision.items if i.kind == "target"} == {query.case.id}
        assert not [i for i in preparation.decision.items if i.kind == "dependency"]
        doc_evidence = {e.id for e in preparation.evidence if e.kind == "document" and e.source_id == document.id}
        assert doc_evidence and all(set(i.evidence_ids) & doc_evidence for i in prepares)
        text = json.dumps([i.instructions for i in prepares], ensure_ascii=False)
        assert "/actors" in text and "/orders" in text
        for request in requests:
            assert "DO-NOT-INJECT-TEMPLATE" not in request.messages[0]["content"]
            assert "DO-NOT-LEAK" not in request.messages[0]["content"]
        assert (await PostgresCasePlanStore(env.settings).get(project.id, preparation.id)).decision == preparation.decision
        from pathlib import Path
        (Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p2-model-planning.json").write_text(json.dumps({
            "scope": "real model plans only; governed Agent execution not covered", "calls": calls,
            "formal_dependency_plan": {"status": plan.status, "target_count": len(target_ids), "dependency_count": len(dependency_ids), "rounds": len(plan.rounds)},
            "existing_data_plan": {"status": existing.status, "item_count": len(existing.decision.items)},
            "run_local_preparation": {"status": preparation.status, "prepare_count": len(prepares), "document_read": bool(doc_evidence)},
            "negative_conditions_preserved": True, "templates_not_injected": True, "project_isolation": True,
            "configuration_in_mysql": True, "immutable_plan_in_postgres": True, "original_document_in_rustfs": True,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
