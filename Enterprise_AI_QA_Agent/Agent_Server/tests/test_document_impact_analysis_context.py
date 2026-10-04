from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.application.documents.impact_config_service import ANALYSIS_CONFIG_KEY
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.schemas.document_impact import DocumentImpactTaskRequest
from src.schemas.document_impact_analysis import DocumentImpactAnalysisPolicy
from src.schemas.model_config import ModelCapabilitiesOverride, model_invocation_parameters
from src.schemas.project import ProjectUpdateRequest
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_impact_tasks import evidence, expire, task_environment


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires actual isolated MySQL/PostgreSQL/RustFS and document processes")]


@contextmanager
def provider():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            payload = json.dumps({"id": "fixture-only", "model": body["model"], "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "controlled transport response"},
                 "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        serving.join(5)
        assert not serving.is_alive()


async def configure(env, endpoint, **changes):
    values = dict(model_name="impact-fixture", provider="openai", transport="openai_chat_completions",
        base_url=endpoint, api_key="local-fixture-marker", is_active=True, use_provider_defaults=False,
        capability_overrides=ModelCapabilitiesOverride(streaming=False), context_window=128000, max_output_tokens=1024)
    await asyncio.to_thread(env.impact_models.upsert, ModelConfigUpdateRequest(**{**values, **changes}))


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_frozen_context_real_files_mysql_changes_and_actual_http_request(tmp_path, monkeypatch, extension):
    async with task_environment(tmp_path, extension) as values:
        env, project, doc, paths, views, diff_id, sources, configs, case, store, service = values
        env.settings.model.langchain_model_adapter_enabled = False
        with provider() as (endpoint, calls):
            await configure(env, endpoint)
            await configs.save_analysis_settings(project.id, DocumentImpactAnalysisPolicy(
                analysis_guidance="原分析指导：必须核对第12步与原始保留期限。", max_input_bytes=180000))
            request = DocumentImpactTaskRequest(creation_key="frozen-analysis", difference_snapshot_id=diff_id,
                environment_key="staging", target_release="next-release")
            task = await service.submit(project.id, doc.id, request)
            seed = await store.get_seed(project.id, doc.id, task.id)
            context = seed.analysis_context
            assert calls == [] and context is not None
            assert context.model.model_dump(mode="json") == model_invocation_parameters(env.impact_runtime.get_default_model_config())
            assert context.environment.deployment_status == "unknown"
            assert context.environment.environment_key == "staging" and context.environment.target_release == "next-release"
            assert context.config.revision == 1 and context.config.scope_key == project.id
            assert context.token_measurement == "not_available"
            assert context.prompt.skills[0].sha256 == hashlib.sha256(context.prompt.skills[0].prompt_block.encode()).hexdigest()
            assert "原分析指导" in context.prompt.system_prompt
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT seed FROM {store._tasks} WHERE id=%s", (task.id,))
                stored = cur.fetchone()["seed"]["analysis_context"]
            assert stored == context.model_dump(mode="json")
            serialized = json.dumps(stored, ensure_ascii=False)
            assert "local-fixture-marker" not in serialized and endpoint not in serialized
            assert "api_key" not in stored["model"] and "extra_headers" not in stored["model"]
            assert env.configs.get(ANALYSIS_CONFIG_KEY, scope_key=project.id).payload["max_input_bytes"] == 180000
            assert env.configs.get(ANALYSIS_CONFIG_KEY).payload["analysis_guidance"] == ""
            assert context.project.id == project.id
            claimed = await store.claim(project.id, task.id, worker_id="analysis-context-worker")
            assert (await service.prepare_input(claimed)).phase == "input_saved"
            frozen, model_request = await service.prepare_analysis_request(project.id, doc.id, task.id)
            decoded = json.loads(model_request.messages[0]["content"])
            full = decoded["fixed_evidence"]["cases"][0]["version"]
            assert len(full["content_json"]["steps"]) == 12
            assert full["content_json"]["深层扩展"]["布尔"] is False
            assert decoded["fixed_evidence"]["old"]["snapshot"]["id"] == views[0].snapshot.id
            assert decoded["fixed_evidence"]["originals"][0]["parse"]["snapshot"]["id"] == views[0].snapshot.id
            result = await env.impact_runtime.invoke(context.model.key, model_request,
                expected_parameters=context.model.model_dump(mode="json"))
            assert result.text == "controlled transport response" and len(calls) == 1
            assert calls[0]["messages"][0]["content"] == context.prompt.system_prompt
            assert calls[0]["messages"][-1]["content"] == model_request.messages[0]["content"]
            assert calls[0]["max_tokens"] == context.model.max_output_tokens
            # Rotating the credential alone is supported and never copied into PG.
            await configure(env, endpoint, api_key="rotated-local-marker")
            assert model_invocation_parameters(env.impact_runtime.get_model_config(context.model.key)) == context.model.model_dump(mode="json")
            await configs.save_analysis_settings(project.id, DocumentImpactAnalysisPolicy(
                analysis_guidance="新配置不得替换旧任务", max_input_bytes=1))
            env.impact_skill_file.write_text("---\nname: changed-skill\n---\n新Skill不得替换旧任务。", encoding="utf-8")
            env.impact_skill_file.unlink()
            await env.projects.update(project.id, ProjectUpdateRequest(name="变更后的项目", base_url="http://later.invalid"))
            await configure(env, endpoint, max_output_tokens=128)
            expire(store, task.id)
            restarted = PostgresDocumentImpactStore(env.settings)
            recovery = await restarted.claim(project.id, task.id, worker_id="analysis-context-recovery")
            assert recovery.seed.analysis_context == context
            recovered_service = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
                projects=env.projects, objects=env.objects, stream=None)
            async def forbidden(*args, **kwargs):
                raise AssertionError("saved analysis recovery must not read current configuration or sources")
            with monkeypatch.context() as patch:
                patch.setattr(configs, "get_analysis_settings", forbidden)
                patch.setattr(sources, "prepare_fixed", forbidden)
                recovered_context, recovered_request = await recovered_service.prepare_analysis_request(project.id, doc.id, task.id)
                assert recovered_context == frozen and recovered_request == model_request
                assert (await recovered_service.submit(project.id, doc.id, request)).id == task.id
            with pytest.raises(ValueError, match="模型参数已变化"):
                await env.impact_runtime.invoke(context.model.key, recovered_request,
                    expected_parameters=context.model.model_dump(mode="json"))
            assert len(calls) == 1
            assert (await service.get(project.id, doc.id, task.id)).status == "running"
            assert (await service.get(project.id, doc.id, task.id)).snapshot_id is None
            for field, value in (("model_key", "explicit-different-model"), ("environment_key", "prod"), ("target_release", "later-release")):
                with pytest.raises(ValueError, match="请求身份"):
                    await recovered_service.submit(project.id, doc.id, request.model_copy(update={field: value}))
            evidence("analysis-context/" + extension + ".json", context=context.model_dump(mode="json"),
                input_sha256=decoded["input_sha256"], model_request=model_request.model_dump(mode="json"),
                actual_http_calls=len(calls), changed_parameters_calls=0, independent_process_restart=False,
                controlled_provider=True, ai_judgement_verified=False)


async def test_legacy_seed_hash_input_and_replay_do_not_gain_current_model(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, _, _, store, service = values
        request = DocumentImpactTaskRequest(creation_key="legacy-impact", difference_snapshot_id=diff_id)
        legacy_body = {"case_version_ids": [], "difference_snapshot_id": diff_id}
        legacy_hash = hashlib.sha256(json.dumps(legacy_body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        assert store.request_hash(request) == legacy_hash
        seed = await sources.capture(project.id, doc.id, diff_id, request)
        assert seed.analysis_context is None
        task = await store.submit(seed, creation_key=request.creation_key, request_hash=legacy_hash, stream=service._stream)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._tasks} SET seed=seed-'analysis_context' WHERE id=%s", (task.id,))
        legacy_service = DocumentImpactTaskService(store=store, sources=sources, documents=env.docs,
            projects=env.projects, objects=env.objects, stream=None)
        assert (await legacy_service.submit(project.id, doc.id, request)).id == task.id
        claim = await store.claim(project.id, task.id, worker_id="legacy-reader")
        assert claim.seed.analysis_context is None
        await legacy_service.prepare_input(claim)
        with pytest.raises(ValueError, match="历史影响任务未冻结"):
            await legacy_service.prepare_analysis_request(project.id, doc.id, task.id)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT seed FROM {store._tasks} WHERE id=%s", (task.id,))
            assert "analysis_context" not in cur.fetchone()["seed"]
        checkpoint, envelope = await legacy_service.read_input(project.id, doc.id, task.id)
        assert checkpoint.input_sha256 == envelope.input_sha256
        evidence("analysis-context/legacy.json", task_id=task.id, request_hash=legacy_hash,
            analysis_context=None, rewritten_seed=False, input_sha256=envelope.input_sha256)


async def test_capture_rejections_do_not_create_tasks_and_input_budget_does_not_truncate(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, configs, _, store, service = values
        request = DocumentImpactTaskRequest(creation_key="capture-invalid", difference_snapshot_id=diff_id, model_key="missing-model")
        with pytest.raises(ValueError, match="没有可用"):
            await service.submit(project.id, doc.id, request)
        assert await store.get_by_request(project.id, doc.id, request.creation_key) is None
        request.model_key = None
        await configure(env, "http://127.0.0.1:9/v1", context_window=1024, max_output_tokens=1024)
        with pytest.raises(ValueError, match="输出预算必须小于上下文窗口"):
            await service.submit(project.id, doc.id, request)
        assert await store.get_by_request(project.id, doc.id, request.creation_key) is None
        await configure(env, "http://127.0.0.1:9/v1")
        unconfigured = DocumentImpactTaskService(store=store, sources=sources, documents=env.docs,
            projects=env.projects, objects=env.objects, stream=service._stream)
        with pytest.raises(RuntimeError, match="依据服务尚未注入"):
            await unconfigured.submit(project.id, doc.id, request)
        env.impact_skill_file.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Skill正文为空"):
            await service.submit(project.id, doc.id, request)
        assert await store.get_by_request(project.id, doc.id, request.creation_key) is None
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {store._tasks}")
            assert cur.fetchone()["n"] == 0
        env.impact_skill_file.write_text("---\nname: analyze-document-impact\n---\n读取固定用例与原文，不执行测试。", encoding="utf-8")
        await configs.save_analysis_settings(project.id, DocumentImpactAnalysisPolicy(max_input_bytes=1))
        task = await service.submit(project.id, doc.id, request)
        claim = await store.claim(project.id, task.id, worker_id="small-model-input")
        prepared = await service.prepare_input(claim)
        assert prepared.phase == "input_saved"
        with pytest.raises(ValueError, match="字节预算.*未截断"):
            await service.prepare_analysis_request(project.id, doc.id, task.id)
        _, complete = await service.read_input(project.id, doc.id, task.id)
        assert len(complete.input.cases[0].version.content_json["steps"]) == 12
        assert (await service.get(project.id, doc.id, task.id)).status == "running"
        # Frozen prompt/Skill integrity is checked when reading PG metadata.
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._tasks} SET seed=jsonb_set(seed,'{{analysis_context,prompt,system_prompt}}','\"changed\"'::jsonb) WHERE id=%s", (task.id,))
        with pytest.raises(ValueError, match="Prompt正文与哈希"):
            await store.get_seed(project.id, doc.id, task.id)
