from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService, ImpactCallCheckpointUnavailable
from src.application.documents.impact_config_service import INVOCATION_CONFIG_KEY
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.document_impact import DocumentImpactTaskRequest
from src.schemas.document_impact_calls import (
    DocumentImpactCallInput, DocumentImpactCallSelection, DocumentImpactInvocationPolicy,
    DocumentImpactRangeRead, DocumentImpactUnit,
)
from test_document_impact_analysis_context import configure
from test_document_impact_tasks import evidence, expire, task_environment


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires actual isolated MySQL/PostgreSQL/RustFS, document processes and local HTTP")]


@contextmanager
def provider(*, mode="ok", gated=False, responder=None):
    calls, arrived, release = [], threading.Event(), threading.Event()
    if not gated:
        release.set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            arrived.set()
            if not release.wait(20):
                return
            if mode == "http_failure":
                raw = json.dumps({"error": {"message": "controlled failure"}}).encode()
                self.send_response(500)
            else:
                message = {"role": "assistant", "content": "controlled complete response"}
                if responder is not None:
                    message["content"] = responder(body)
                if mode == "length": message["content"] = "partial"
                if mode == "empty": message["content"] = ""
                if mode == "large": message["content"] = "X" * 20000
                if mode == "tool":
                    message["tool_calls"] = [{"id": "tool-fixture", "type": "function",
                        "function": {"name": "unexpected_execution", "arguments": "{}"}}]
                raw = json.dumps({"id": "impact-fixture-response", "model": body["model"], "choices": [{"index": 0,
                    "message": message, "finish_reason": "length" if mode == "length" else "stop"}],
                    "usage": {"prompt_tokens": 41, "completion_tokens": 13, "total_tokens": 54}}).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try: self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    running = threading.Thread(target=server.serve_forever, daemon=True)
    running.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls, arrived, release
    finally:
        release.set()
        server.shutdown(); server.server_close(); running.join(5)
        assert not running.is_alive()


def call_service(env, tasks, store):
    return DocumentImpactCallService(tasks=tasks, store=store, models=env.impact_runtime,
        objects=env.objects, projects=env.projects)


async def prepared(values, endpoint, *, policy=None, adapter=False):
    env, project, doc, _, _, diff_id, _, configs, case, store, tasks = values
    env.settings.model.langchain_model_adapter_enabled = adapter
    await configure(env, endpoint)
    if policy:
        await configs.save_invocation_settings(project.id, policy)
    task = await tasks.submit(project.id, doc.id, DocumentImpactTaskRequest(
        creation_key="fixed-calls", difference_snapshot_id=diff_id))
    claim = await store.claim(project.id, task.id, worker_id="first-call-worker")
    await tasks.prepare_input(claim)
    await tasks.prepare_manifest(claim)
    _, manifest = await tasks.read_manifest(project.id, doc.id, task.id)
    selection = DocumentImpactCallSelection(unit=DocumentImpactUnit(kind="case", id=case.version.id),
        segment_ids=[item.id for item in manifest.segments],
        difference_ids=[f"{item.kind}:{item.index}" for item in manifest.differences])
    return claim, manifest, selection, call_service(env, tasks, store)


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_real_files_actual_model_request_and_saved_response_recovery(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path, extension) as values:
        env, project, doc, paths, views, _, sources, configs, case, store, tasks = values
        with provider() as (endpoint, calls, _, _):
            claim, manifest, selection, service = await prepared(values, endpoint, adapter=adapter)
            record = await service.prepare(claim, call_key="case-complete", selection=selection)
            assert record.phase == "ready" and calls == []
            assert env.configs.get(INVOCATION_CONFIG_KEY).payload["max_logical_calls"] == 200
            dependencies = claim.seed.invocation_snapshot.runtime_parameters["dependency_versions"]
            assert set(dependencies) == ({"openai", "langchain-openai", "langchain-core"} if adapter else {"openai"})
            request_record, payload = await service.read_request(project.id, doc.id, claim.task.id, record.id)
            decoded = json.loads(payload.request.messages[-1]["content"])
            assert decoded["primary"]["case"]["version"]["content_json"] == case.version.content_json
            assert len(decoded["primary"]["case"]["version"]["content_json"]["steps"]) == 12
            assert decoded["environment"]["deployment_status"] == "unknown"
            assert payload.budget.method == "utf8_bytes_estimate" and payload.budget.provider_token_measurement == "estimate"
            assert payload.budget.reserved_estimated_tokens == payload.budget.input_estimated_tokens + 1024
            assert payload.budget.input_estimated_tokens > 0
            dispatched = await service.invoke(claim, record.id)
            assert dispatched.phase == "response_saved" and dispatched.response_disposition == "received"
            saved, response = await service.read_response(project.id, doc.id, claim.task.id, record.id)
            assert response.text == "controlled complete response" and len(calls) == 1
            assert calls[0]["messages"][0]["content"] == payload.request.system_prompt
            assert calls[0]["messages"][-1]["content"] == payload.request.messages[-1]["content"]
            assert response.metadata["usage"]  # Provider measurements and the reservation are different facts.
            raw = (await env.objects.read_object_uri(saved.response.storage_uri))["content"]
            assert saved.response.sha256 == hashlib.sha256(raw).hexdigest()
            assert endpoint.encode() not in raw and b"local-fixture-marker" not in raw
            assert await store.calls.save_response(claim, record.id, saved.response, "received") == saved
            with pytest.raises(ValueError, match="不能改写"):
                await store.calls.save_response(claim, record.id, saved.response.model_copy(update={"sha256": "0" * 64}), "received")
            await configs.save_invocation_settings(project.id, DocumentImpactInvocationPolicy(
                max_logical_calls=1, max_reserved_estimated_tokens=1))
            env.impact_skill_file.unlink()
            expire(store, claim.task.id)
            restarted = PostgresDocumentImpactStore(env.settings)
            recovered = await restarted.claim(project.id, claim.task.id, worker_id="response-recovery")
            second_tasks = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
                projects=env.projects, objects=env.objects, stream=None)
            recovery = call_service(env, second_tasks, restarted)
            async def forbidden(*args, **kwargs):
                raise AssertionError("saved call recovery must not invoke, build sources, upload or read current configuration")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                patch.setattr(configs, "get_invocation_settings", forbidden)
                patch.setattr(sources, "prepare_fixed", forbidden)
                assert await recovery.prepare(recovered, call_key="case-complete", selection=selection) == saved
                assert await recovery.invoke(recovered, record.id) == saved
                assert await recovery.read_response(project.id, doc.id, claim.task.id, record.id) == (saved, response)
            assert len(calls) == 1 and recovered.task.deadline_at == claim.task.deadline_at
            assert await restarted.calls.get(str(uuid4()), doc.id, claim.task.id, call_id=record.id) is None
            with pytest.raises(ContinuationLeaseLostError):
                await store.calls.save_response(claim, record.id, saved.response, "received")
            with pytest.raises(ValueError, match="当前调用attempt"):
                await restarted.calls.save_response(recovered, record.id, saved.response, "received")
            await restarted.finish_failed(recovered, error_code="fixture_candidates_not_started", message="fixture response only")
            assert await recovery.invoke(recovered, record.id) == saved
            assert await restarted.claim(project.id, claim.task.id, worker_id="terminal") is None
            directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
            if directory:
                target = Path(directory)/"calls"/f"{extension}-{adapter}"
                target.mkdir(parents=True,exist_ok=True)
                for path in paths: (target/path.name).write_bytes(path.read_bytes())
                (target/"response.json").write_bytes(raw)
            evidence(f"calls/{extension}-{adapter}/verification.json", request=payload.model_dump(mode="json"),
                saved=saved.model_dump(mode="json"), actual_http_calls=len(calls), model_adapter=adapter,
                recovered_without_http=True, default_model_judgement=False, independent_process_restart=False)


async def test_concurrent_invoke_saves_one_intent_and_continuation_keeps_exact_history(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, _, _, _, store, tasks = values
        with provider() as (endpoint, calls, _, _):
            claim, manifest, selection, service = await prepared(values, endpoint)
            record = await service.prepare(claim, call_key="concurrent", selection=selection)
            results = await asyncio.gather(*(service.invoke(claim, record.id) for _ in range(6)))
            assert len(calls) == 1 and any(item.phase == "response_saved" for item in results)
            source = next(item for item in manifest.sources if "new" in item.roles)
            next_selection = DocumentImpactCallSelection(unit=selection.unit, previous_call_id=record.id,
                ranges=[DocumentImpactRangeRead(source_id=source.id, start_char=0, end_char=source.length_chars)])
            followup = await service.prepare(claim, call_key="cross-section-continue", selection=next_selection)
            _, first_input = await service.read_request(project.id, doc.id, claim.task.id, record.id)
            _, continued = await service.read_request(project.id, doc.id, claim.task.id, followup.id)
            assert continued.request.messages[:-2] == first_input.request.messages
            assert continued.request.messages[-2] == {"role": "assistant", "content": "controlled complete response"}
            assert "9" in json.loads(continued.request.messages[-1]["content"])["additional_fixed_reads"][0]["text"]
            await service.invoke(claim, followup.id)
            assert len(calls) == 2
            with pytest.raises(ValueError, match="已有调用"):
                await service.prepare(claim, call_key="silent-duplicate", selection=selection)
            with pytest.raises(ValueError, match="同一影响调用身份"):
                await service.prepare(claim, call_key="concurrent", selection=next_selection)
            evidence("calls/concurrency.json", http_calls=len(calls), concurrent_coroutines=6,
                continuation=continued.model_dump(mode="json"), duplicate_unit_rejected=True)


async def test_unknown_http_outcome_is_not_resent_and_explicit_retry_uses_remaining_budget(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, sources, _, _, store, tasks = values
        with provider(gated=True) as (endpoint, calls, arrived, release):
            claim, _, selection, service = await prepared(values, endpoint,
                policy=DocumentImpactInvocationPolicy(max_logical_calls=2))
            record = await service.prepare(claim, call_key="unknown", selection=selection)
            executing = asyncio.create_task(service.invoke(claim, record.id))
            assert await asyncio.to_thread(arrived.wait, 5)
            executing.cancel()
            with pytest.raises(asyncio.CancelledError): await executing
            assert (await store.calls.get(project.id, doc.id, claim.task.id, call_id=record.id)).phase == "invoking"
            release.set()
            expire(store, claim.task.id)
            restarted = PostgresDocumentImpactStore(env.settings)
            recovered = await restarted.claim(project.id, claim.task.id, worker_id="unknown-recovery")
            second_tasks = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
                projects=env.projects, objects=env.objects, stream=None)
            recovery = call_service(env, second_tasks, restarted)
            unknown = await recovery.invoke(recovered, record.id)
            assert unknown.phase == "outcome_unknown" and unknown.outcome_code == "recovered_without_response"
            assert await recovery.invoke(recovered, record.id) == unknown and len(calls) == 1
            retry = await recovery.prepare(recovered, call_key="explicit-retry",
                selection=selection.model_copy(update={"retry_of_call_id": record.id}))
            assert retry.id != record.id and retry.budget == record.budget
            await recovery.invoke(recovered, retry.id)
            assert len(calls) == 2
            with pytest.raises(ValueError, match="调用额度"):
                await recovery.prepare(recovered, call_key="third", selection=DocumentImpactCallSelection(
                    unit=DocumentImpactUnit(kind="segment", id=selection.segment_ids[0])))
            records = await restarted.calls.list(project.id, doc.id, claim.task.id)
            assert len(records) == 2 and sum(item.budget.reserved_estimated_tokens for item in records) == 2 * record.budget.reserved_estimated_tokens
            evidence("calls/unknown.json", records=[item.model_dump(mode="json") for item in records],
                http_calls=len(calls), no_implicit_retry=True, original_deadline_preserved=recovered.task.deadline_at==claim.task.deadline_at)


@pytest.mark.parametrize("stage", ["prepare", "intent", "response_checkpoint"])
async def test_lost_pg_replies_do_not_grant_another_send(tmp_path, monkeypatch, stage):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, sources, _, _, store, tasks = values
        with provider() as (endpoint, calls, _, _):
            claim, _, selection, service = await prepared(values, endpoint)
            method = {"prepare": "prepare", "intent": "start", "response_checkpoint": "save_response"}[stage]
            original = getattr(store.calls, method)
            async def lost_reply(*args, **kwargs):
                await original(*args, **kwargs)
                raise ConnectionError("actual PG commit succeeded, reply lost")
            if stage == "prepare":
                with monkeypatch.context() as patch:
                    patch.setattr(store.calls, method, lost_reply)
                    with pytest.raises(ImpactCallCheckpointUnavailable):
                        await service.prepare(claim, call_key="lost", selection=selection)
                record = await service.prepare(claim, call_key="lost", selection=selection)
                assert calls == [] and record.phase == "ready"
                await service.invoke(claim, record.id)
            else:
                record = await service.prepare(claim, call_key="lost", selection=selection)
                with monkeypatch.context() as patch:
                    patch.setattr(store.calls, method, lost_reply)
                    with pytest.raises(ImpactCallCheckpointUnavailable): await service.invoke(claim, record.id)
            expire(store, claim.task.id)
            restarted = PostgresDocumentImpactStore(env.settings)
            recovered = await restarted.claim(project.id, claim.task.id, worker_id="lost-reply-recovery")
            second_tasks = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
                projects=env.projects, objects=env.objects, stream=None)
            recovery = call_service(env, second_tasks, restarted)
            result = await recovery.invoke(recovered, record.id)
            assert len(calls) == (0 if stage == "intent" else 1)
            assert result.phase == ("outcome_unknown" if stage == "intent" else "response_saved")
            evidence(f"calls/lost-{stage}.json", saved=result.model_dump(mode="json"), http_calls=len(calls), repeated_send=False)


@pytest.mark.parametrize("mode,disposition", [("length","truncated"),("empty","empty"),("tool","tool_calls"),
    ("http_failure","provider_failure"),("large",None)])
async def test_failure_and_partial_responses_are_saved_without_success_or_reexecution(tmp_path, mode, disposition):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, _, _, _, store, tasks = values
        with provider(mode=mode) as (endpoint, calls, _, _):
            claim, _, selection, service = await prepared(values, endpoint,
                policy=DocumentImpactInvocationPolicy(max_response_bytes=4000) if mode == "large" else None)
            record = await service.prepare(claim, call_key=mode, selection=selection)
            saved = await service.invoke(claim, record.id)
            if mode == "large":
                assert saved.phase == "response_unavailable" and saved.response is None
                assert saved.outcome_code == "response_over_size_limit"
            else:
                assert saved.phase == "response_saved" and saved.response_disposition == disposition
                _, response = await service.read_response(project.id, doc.id, claim.task.id, record.id)
                if mode == "http_failure": assert "diagnostics_omitted" in response.raw_response
            sent = len(calls)
            assert await service.invoke(claim, record.id) == saved and len(calls) == sent
            assert (await tasks.get(project.id, doc.id, claim.task.id)).status == "running"
            if mode == "http_failure": assert sent == 3  # Existing official SDK retry behavior, one logical intent.
            else: assert sent == 1
            evidence(f"calls/response-{mode}.json", record=saved.model_dump(mode="json"),
                logical_calls=1, actual_http_attempts=sent, model_candidates_validated=False)


async def test_token_counts_size_total_budget_runtime_changes_and_expired_time(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, _, configs, _, store, tasks = values
        with provider() as (endpoint, calls, _, _):
            policy = DocumentImpactInvocationPolicy(token_method="tiktoken_content", encoding_name="cl100k_base",
                max_logical_calls=2, max_reserved_estimated_tokens=100000)
            claim, manifest, selection, service = await prepared(values, endpoint, policy=policy)
            record = await service.prepare(claim, call_key="budget", selection=selection)
            _, payload = await service.read_request(project.id, doc.id, claim.task.id, record.id)
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            actual_content = sum(len(enc.encode_ordinary(text)) for text in [payload.request.system_prompt,
                *[message["content"] for message in payload.request.messages]])
            assert record.budget.content_tokens_or_estimate == actual_content
            assert record.budget.provider_token_measurement == "estimate" and calls == []
            saved_runtime = claim.seed.invocation_snapshot.runtime_parameters
            version = __import__("importlib.metadata", fromlist=["version"]).version
            with monkeypatch.context() as patch:
                patch.setattr("src.application.models.model_runtime_service.importlib.metadata.version",
                    lambda name: "unrelated-sdk-changed" if name == "google-genai" else version(name))
                assert env.impact_runtime.get_invocation_runtime_parameters(claim.seed.analysis_context.model.key) == saved_runtime
            env.settings.model.langchain_model_adapter_enabled = True
            with pytest.raises(ValueError, match="适配器"):
                await service.invoke(claim, record.id)
            with pytest.raises(ValueError, match="适配器"):
                await env.impact_runtime.invoke(claim.seed.analysis_context.model.key, payload.request,
                    expected_runtime_parameters=saved_runtime)
            env.settings.model.langchain_model_adapter_enabled = False
            await configure(env, endpoint, max_output_tokens=512)
            with pytest.raises(ValueError, match="模型参数"):
                await service.invoke(claim, record.id)
            await configure(env, endpoint)
            assert (await store.calls.get(project.id,doc.id,claim.task.id,call_id=record.id)).phase == "ready" and calls == []
            limited = claim.seed.model_copy(deep=True)
            limited.analysis_context.config.payload.max_input_bytes = 1
            with pytest.raises(ValueError, match="字节预算"):
                service.estimate(limited, payload.request)
            limited.analysis_context.config.payload.max_input_bytes = 256000
            limited.analysis_context.model.context_window = limited.analysis_context.model.max_output_tokens + 1
            with pytest.raises(ValueError, match="上下文窗口"):
                service.estimate(limited, payload.request)
            with monkeypatch.context() as patch:
                patch.setattr("src.application.documents.impact_calls.importlib.metadata.version", lambda _: "changed-version")
                with pytest.raises(ValueError, match="Tokenizer版本"):
                    service.estimate(claim.seed, payload.request)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {store._tasks} SET deadline_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s",
                    (claim.task.id,))
            with pytest.raises(TimeoutError, match="原任务预算"):
                await service.invoke(claim, record.id)
            assert calls == []
            evidence("calls/budget.json", request=payload.model_dump(mode="json"),
                content_encoding_verified=True, framing_is_estimate=True, runtime=saved_runtime, actual_http_calls=0)


async def test_total_budget_corrupt_objects_and_legacy_invocation_snapshot(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, _, configs, _, store, tasks = values
        with provider() as (endpoint, calls, _, _):
            claim, _, selection, service = await prepared(values, endpoint,
                policy=DocumentImpactInvocationPolicy(max_reserved_estimated_tokens=1))
            with pytest.raises(ValueError, match="累计估算预算"):
                await service.prepare(claim, call_key="too-expensive", selection=selection)
            assert await store.calls.list(project.id, doc.id, claim.task.id) == [] and calls == []
            # A separate task is required to use the changed frozen policy.
            await configs.save_invocation_settings(project.id, DocumentImpactInvocationPolicy())
            task = await tasks.submit(project.id,doc.id,DocumentImpactTaskRequest(creation_key="corruption",difference_snapshot_id=diff_id))
            other = await store.claim(project.id,task.id,worker_id="corruption")
            await tasks.prepare_input(other); await tasks.prepare_manifest(other)
            record = await service.prepare(other,call_key="corrupt",selection=selection)
            read=env.objects.read_object_uri
            async def corrupted(uri, **kwargs):
                result=await read(uri,**kwargs)
                return {**result,"content": result["content"]+b" "} if uri==record.request.storage_uri else result
            with monkeypatch.context() as patch:
                patch.setattr(env.objects,"read_object_uri",corrupted)
                with pytest.raises(ValueError, match="对象大小或哈希"):
                    await service.invoke(other,record.id)
            assert calls == []
            with pytest.raises(ValueError,match="历史影响任务"):
                service.estimate(other.seed.model_copy(update={"invocation_snapshot":None}),
                    (await service.read_request(project.id,doc.id,task.id,record.id))[1].request)
            evidence("calls/boundaries.json", total_budget_rejected=True, corruption_no_http=True,
                legacy_policy_not_invented=True, actual_http_calls=0)


async def test_response_object_write_failure_preserves_intent_and_does_not_repeat_http(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, sources, _, _, store, tasks = values
        with provider() as (endpoint, calls, _, _):
            claim, _, selection, service = await prepared(values, endpoint)
            record = await service.prepare(claim, call_key="response-object-failure", selection=selection)
            async def unavailable(**kwargs):
                assert kwargs["filename"] == "response.json"
                raise ConnectionError("response object write cannot be confirmed")
            with monkeypatch.context() as patch:
                patch.setattr(env.objects, "store_uploaded_bytes", unavailable)
                with pytest.raises(ImpactCallCheckpointUnavailable):
                    await service.invoke(claim, record.id)
            assert len(calls) == 1
            assert (await store.calls.get(project.id,doc.id,claim.task.id,call_id=record.id)).phase == "invoking"
            expire(store,claim.task.id)
            restarted=PostgresDocumentImpactStore(env.settings)
            recovered=await restarted.claim(project.id,claim.task.id,worker_id="object-recovery")
            second_tasks=DocumentImpactTaskService(store=restarted,sources=sources,documents=env.docs,
                projects=env.projects,objects=env.objects,stream=None)
            recovery=call_service(env,second_tasks,restarted)
            unknown=await recovery.invoke(recovered,record.id)
            assert unknown.phase == "outcome_unknown" and len(calls)==1
            evidence("calls/response-object-failure.json",call=unknown.model_dump(mode="json"),
                actual_http_calls=1, repeated_send=False, no_response_to_validate=True)
