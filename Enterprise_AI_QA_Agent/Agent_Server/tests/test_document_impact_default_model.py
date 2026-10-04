"""Real default-provider decisions, with immutable files and actual storage.

The first probe is one declared unit with complete fixed reads, not whole-workflow
or semantic approval. Never replace the provider or change the global model.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from src.application.documents.impact_analysis_context import DocumentImpactAnalysisContexts
from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_config_service import DocumentImpactConfigService
from src.application.documents.impact_response_service import DocumentImpactResponseService
from src.application.documents.impact_sources import DocumentImpactSources
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.case_management import TestCaseDraftCreateRequest as DraftRequest
from src.schemas.document_differences import DocumentDifferenceTaskRequest
from src.schemas.document_impact import DocumentImpactPolicy, DocumentImpactTaskRequest
from src.schemas.document_impact_calls import DocumentImpactCallSelection
from src.schemas.document_parsing import DocumentParseSnapshotView, DocumentParseTaskRequest
from src.schemas.model_config import model_invocation_parameters
from src.schemas.project import ProjectCreateRequest
from test_document_difference_tasks import comparison_environment
from test_document_impact_sources import source


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL") != "1",
    reason="requires the actual default model, isolated MySQL/PG and real RustFS",
)]


OLD = (
    "# 订单服务需求\n\n"
    "## 订单保留\n已支付订单保留7天，超过保留期查询返回410。\n\n"
    "## 优惠码\n提交有效优惠码PROMO5时订单金额减少5元。\n\n"
    "## 管理鉴权\n未登录访问GET /admin必须返回401，不得创建登录前置。\n"
)
NEW = (
    "# 订单服务需求\n\n"
    "## 订单保留\n已支付订单保留9天，超过保留期查询返回410。\n\n"
    "## 管理鉴权\n未登录访问GET /admin必须返回401，不得创建登录前置。\n\n"
    "## 订单导出\n新增GET /orders/export，导出订单按创建时间倒序排列。\n"
)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


@asynccontextmanager
async def real_scenario(tmp_path, directory):
    async with comparison_environment(tmp_path) as (env, diffs, diff_store, _, parsing, parse_store):
        project = await env.projects.create(ProjectCreateRequest(project_key="real-impact", name="真实模型影响样本"))
        views, paths = [], []
        doc = None
        for number, text in enumerate((OLD, NEW), 1):
            path = tmp_path / f"requirements-v{number}.md"
            path.write_text(text, encoding="utf-8")
            paths.append(path)
            (directory / path.name).write_bytes(path.read_bytes())
            arguments = dict(filename=path.name, content_base64=base64.b64encode(path.read_bytes()).decode(),
                project_id=project.id, request_id=f"upload-v{number}")
            if doc is None:
                doc = await env.docs.upload_document(**arguments)
                version_id = doc.current_version_id
            else:
                saved = await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(**arguments,
                    expected_parent_version_id=doc.current_version_id))
                doc, version_id = saved.document, saved.version.id
            task = await parsing.submit(project.id, doc.id, DocumentParseTaskRequest(
                creation_key=f"parse-v{number}", version_id=version_id))
            completed = await parsing.execute(await parse_store.claim(project.id, task.id, worker_id="real-model-parse"))
            assert completed.status == "completed"
            snapshot, result = await parsing.read_snapshot(project.id, doc.id, completed.snapshot_id)
            views.append(DocumentParseSnapshotView(snapshot=snapshot, result=result))
            write_json(directory / f"parse-v{number}.json", views[-1].model_dump(mode="json"))
        task = await diffs.submit(project.id, doc.id, DocumentDifferenceTaskRequest(creation_key="compare",
            old_snapshot_id=views[0].snapshot.id, new_snapshot_id=views[1].snapshot.id))
        compared = await diffs.execute(await diff_store.claim(project.id, task.id, worker_id="real-model-diff"))
        assert compared.status == "completed"
        _, difference = await diffs.read_snapshot(project.id, doc.id, compared.snapshot_id)
        write_json(directory / "difference.json", difference.model_dump(mode="json"))
        cases = []
        for key, objective, operation, expected in [
            ("retention", "验证已支付订单超过7天不可查", "查询8天前已支付订单", {"status": 410, "days": 7}),
            ("unauth", "验证未登录拒绝管理访问", "保持未登录，请求GET /admin", {"status": 401, "login": False}),
            ("promotion", "验证有效优惠码减5元", "使用PROMO5创建订单", {"discount": 5}),
        ]:
            bundle = await env.cases.create_draft(project_id=project.id, payload=DraftRequest(case_key=key,
                title=objective, case_type="json", mode_key="ui_automation" if key == "promotion" else "api_testing",
                content_json={"objective": objective, "steps": [
                    {"action": f"记录受控环境信息{i}"} for i in range(1, 8)
                ] + [{"action": operation, "expected": expected}], "extension": {"zero": 0, "null": None, "false": False}},
                execution_notes="阅读所有步骤，保留刻意缺失的负向条件。", source_refs=[source(doc, views[0])],
                model_key="manual", prompt_version="manual-v1", skill_versions={"manual": "v1"}))
            cases.append(bundle)
        write_json(directory / "cases.json", [case.model_dump(mode="json") for case in cases])
        write_json(directory / "semantic-baseline.json", {
            "modified": "已支付订单保留7天改为9天：retention需要复核",
            "removed": "优惠码减5元规则删除：promotion提出复核/停用候选，不能直接停用",
            "added": "导出按创建时间倒序：当前三个用例没有相应验证点",
            "unchanged": "未登录401：unauth继续保留负向条件，不补登录",
            "deployment": "unknown", "full_workflow_coverage": False,
        })
        configs = DocumentImpactConfigService(config_store=env.configs, project_service=env.projects)
        await configs.initialize()
        await configs.save_settings(project.id, DocumentImpactPolicy(timeout_seconds=600))
        models = ModelRuntimeService(ModelRegistry(MySQLModelConfigStore(get_settings())), env.settings)
        default = models.get_default_model_config()
        assert default is not None
        original_parameters = model_invocation_parameters(default)
        write_json(directory / "model-provenance.json", {"model": original_parameters,
            "runtime": models.get_invocation_runtime_parameters(default.key), "source_model_configuration_read_only": True})
        sources = DocumentImpactSources(documents=env.docs, parsing=parsing, differences=diffs, cases=env.cases, configs=configs)
        skill_root = Path(__file__).resolve().parents[1] / "src" / "SKILLS"
        contexts = DocumentImpactAnalysisContexts(models=models, skills=SkillRuntimeService(SkillRegistry(skill_root)), configs=configs)
        store = PostgresDocumentImpactStore(env.settings, lease_seconds=600)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in store.tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        tasks = DocumentImpactTaskService(store=store, sources=sources, documents=env.docs, projects=env.projects,
            objects=env.objects, stream="isolated:real-impact:" + uuid4().hex, analysis_contexts=contexts)
        owned, references = [], []
        original_upload = env.objects.store_uploaded_bytes
        async def tracked(**kwargs):
            saved = await original_upload(**kwargs)
            if kwargs["object_prefix"].startswith("document-impacts/"):
                owned.append(saved["uri"])
                raw = (await env.objects.read_object_uri(saved["uri"]))["content"]
                assert raw == kwargs["content"]
                digest = hashlib.sha256(raw).hexdigest()
                (directory / "objects" / (digest + ".object")).write_bytes(raw)
                references.append({"storage_uri": saved["uri"], "sha256": digest, "size_bytes": len(raw), "name": kwargs["filename"]})
            return saved
        env.objects.store_uploaded_bytes = tracked
        try:
            await tasks.initialize()
            yield env, project, doc, compared.snapshot_id, cases, store, tasks, models
        finally:
            write_json(directory / "object-references.json", references)
            write_json(directory / "source-model-after.json", model_invocation_parameters(models.get_default_model_config()))
            assert model_invocation_parameters(models.get_default_model_config()) == original_parameters
            for uri in set(owned):
                await env.objects.delete_object_uri(uri)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in store.tables:
                    cur.execute(f'DROP TABLE "{table}"')


async def test_default_model_real_multirule_evidence_and_complete_cases(tmp_path, monkeypatch):
    directory = Path(os.environ.get("QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR", str(tmp_path / "evidence")))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "objects").mkdir()
    wire = []
    send = None
    async def trace(client, request, *args, **kwargs):
        entry = {"attempt": len(wire) + 1, "method": request.method}
        raw = request.content
        (directory / f"http-request-{entry['attempt']}.json").write_bytes(raw)
        entry["request_sha256"] = hashlib.sha256(raw).hexdigest()
        wire.append(entry)
        start = monotonic()
        print("default_model_http_started " + json.dumps(entry), flush=True)
        try:
            result = await send(client, request, *args, **kwargs)
            entry["status_code"] = result.status_code
            return result
        except asyncio.CancelledError:
            entry["outcome"] = "cancelled_without_response"
            raise
        except Exception as exc:
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            entry["elapsed_seconds"] = round(monotonic() - start, 3)
            write_json(directory / "physical-http.json", wire)
    async with real_scenario(tmp_path, directory) as (env, project, doc, diff_id, cases, store, tasks, models):
        # Inspect the actual SDK factory, rather than assume it uses httpx.
        # OpenAI 3.14.0 uses httpx2 in this environment. This client never sends.
        default = models.get_default_model_config()
        assert default.transport == "openai_chat_completions"
        inspection = OpenAIChatClient()._make_client(default, "unused-type-inspection")
        transport_class = type(inspection._client)
        send = transport_class.send
        write_json(directory / "http-transport.json", {
            "class": transport_class.__module__ + "." + transport_class.__name__,
            "mro": [cls.__module__ + "." + cls.__name__ for cls in transport_class.__mro__],
            "instrumentation": "actual SDK client send; no headers or endpoint stored",
        })
        await inspection.close()
        task = await tasks.submit(project.id, doc.id, DocumentImpactTaskRequest(creation_key="actual-default-unit",
            difference_snapshot_id=diff_id, environment_key="声明环境", target_release="尚无部署证据"))
        claim = await store.claim(project.id, task.id, worker_id="default-model-probe")
        await tasks.prepare_input(claim)
        await tasks.prepare_manifest(claim)
        _, manifest = await tasks.read_manifest(project.id, doc.id, task.id)
        calls = DocumentImpactCallService(tasks=tasks, store=store, models=models, objects=env.objects, projects=env.projects)
        checks = DocumentImpactResponseService(calls=calls, store=store, objects=env.objects)
        write_json(directory / "seed.json", claim.seed.model_dump(mode="json"))
        write_json(directory / "manifest.json", manifest.model_dump(mode="json"))
        unit = next(unit for unit in calls.units(manifest) if unit.kind == "difference")
        selection = DocumentImpactCallSelection(unit=unit, segment_ids=[item.id for item in manifest.segments],
            case_version_ids=[case.version.id for case in cases])
        record = await calls.prepare(claim, call_key="real-complete-evidence", selection=selection)
        _, request = await calls.read_request(project.id, doc.id, task.id, record.id)
        assert all(case.version_record_sha256 in request.request.messages[-1]["content"] for case in manifest.cases)
        write_json(directory / "request.json", request.model_dump(mode="json"))
        with monkeypatch.context() as patch:
            patch.setattr(transport_class, "send", trace)
            record = await calls.invoke(claim, record.id)
        write_json(directory / "call.json", record.model_dump(mode="json"))
        write_json(directory / "task.json", (await tasks.get(project.id, doc.id, task.id)).model_dump(mode="json"))
        assert record.phase == "response_saved", {"phase": record.phase, "outcome": record.outcome_code, "physical_sends": len(wire)}
        _, response = await calls.read_response(project.id, doc.id, task.id, record.id)
        write_json(directory / "response.json", response.model_dump(mode="json"))
        _, assessment = await checks.assess(claim, record.id)
        write_json(directory / "assessment.json", assessment.model_dump(mode="json"))
        print("default_model_unit_assessment " + json.dumps({"disposition": record.response_disposition,
            "status": assessment.status, "issues": [issue.code for issue in assessment.issues], "physical_sends": len(wire)}), flush=True)
        assert assessment.status == "unit_finished", {"status": assessment.status, "issues": [issue.code for issue in assessment.issues]}
        assert assessment.semantic_status == "pending_review" and assessment.deployment_status == "unknown"
        assert {candidate.kind for candidate in assessment.decision.candidates} >= {"modified", "removed", "added"}
        assert any(candidate.case_impacts for candidate in assessment.decision.candidates)
        # Read persisted results without another HTTP call; this does not prove semantics.
        assert (await checks.read(project.id, doc.id, task.id, record.id))[1] == assessment
        assert (await calls.read_response(project.id, doc.id, task.id, record.id))[1] == response
