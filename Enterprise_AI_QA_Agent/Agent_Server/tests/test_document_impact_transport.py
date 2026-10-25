"""Actual SDK attempts, frozen policy, full files/cases and ambiguous transport."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from src.application.model_clients.registry import resolve_client
from src.application.models.model_runtime_service import ModelRuntimeService
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.schemas.document_impact import DocumentImpactSourceSeed
from src.schemas.document_impact_calls import DocumentImpactCallInput, DocumentImpactInvocationPolicy
from src.application.documents.impact_sources import canonical_impact_json
from src.schemas.model_config import ModelTransportOptions
from test_document_impact_calls import prepared, provider, call_service
from test_document_impact_grouped_workflow import grouped_responder
from test_document_impact_tasks import task_environment, evidence, expire
from test_document_impact_workflow import workflow
from test_model_clients import _config


LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires actual MySQL/PG/RustFS, parsers and HTTP")


def test_saved_grouped_requests_keep_exact_hash_and_old_transport_policy():
    base = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-default-model-grouped-v6-first"
    seed = DocumentImpactSourceSeed.model_validate_json((base/"seed.json").read_bytes())
    assert seed.invocation_snapshot.config.payload.transport_policy == "provider_defaults"
    assert seed.invocation_snapshot.config.payload.transport_options is None
    verified = 0
    for item in json.loads((base/"object-references.json").read_text(encoding="utf-8")):
        if item["name"] != "request.json": continue
        raw = (base/"objects"/f"{item['sha256']}.object").read_bytes()
        parsed = DocumentImpactCallInput.model_validate_json(raw)
        assert hashlib.sha256(canonical_impact_json(parsed)).hexdigest() == item["sha256"]
        verified += 1
    assert verified == 4


@pytest.mark.parametrize("transport", ["openai_chat_completions", "openai_responses", "anthropic_messages"])
def test_existing_factories_receive_supported_explicit_options(transport):
    config = _config(transport=transport)
    client = resolve_client(config, timeout_seconds=120, max_retries=0)
    assert client._timeout_seconds == 120 and client._max_retries == 0
    assert resolve_client(config)._max_retries == 2


def test_google_transport_records_verified_explicit_options():
    config = _config(transport="google_gemini_generate_content")
    service = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config),
        SimpleNamespace(model=SimpleNamespace(langchain_model_adapter_enabled=False,llm_request_timeout_seconds=60)))
    parameters = service.get_invocation_runtime_parameters(config.key,
        transport_options=ModelTransportOptions(timeout_seconds=120,max_retries=0))
    assert parameters["sdk_retry_policy"] == "explicit_transport_options"
    assert parameters["transport_options"] == {"timeout_seconds": 120, "max_retries": 0}
    assert resolve_client(config, max_retries=0)._max_retries == 0


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_actual_slow_complete_group_uses_one_attempt_per_call(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path,extension,protocol_version="impact-call-v6") as values:
        env, project, doc, paths, views, _, _, configs, case, store, tasks = values
        env.settings.model.llm_request_timeout_seconds = .25
        holder, observed = {}, []
        original = openai.AsyncOpenAI.__init__
        def capture(client,*args,**kwargs):
            original(client,*args,**kwargs)
            observed.append({"timeout":client.timeout,"max_retries":client.max_retries})
        respond = grouped_responder(holder,views,case)
        def slow(body):
            time.sleep(.7)  # Longer than the configured provider default, within the fixed call budget.
            return respond(body)
        with provider(responder=slow) as (endpoint,sends,_,_):
            policy = DocumentImpactInvocationPolicy(call_timeout_seconds=4,transport_policy="single_attempt")
            claim, manifest, _, calls = await prepared(values,endpoint,policy=policy,adapter=adapter)
            frozen = claim.seed.invocation_snapshot
            assert frozen.runtime_parameters["transport_options"] == {"timeout_seconds":4.0,"max_retries":0}
            holder.update(first=calls.units(manifest)[0],manifest=manifest)
            # Current MySQL config changes must not change already frozen SDK options.
            await configs.save_invocation_settings(project.id,DocumentImpactInvocationPolicy(call_timeout_seconds=1))
            runner=workflow(env,tasks,store)
            with monkeypatch.context() as patch:
                patch.setattr(openai.AsyncOpenAI,"__init__",capture)
                saved=await runner.execute(claim)
            snapshot,result=await runner.read_result(project.id,doc.id,saved.id)
            assert all(item.status=="analyzed" for item in result.units)
            assert len(sends)==2 and len(observed)==2
            assert all(item=={"timeout":4.0,"max_retries":0} for item in observed)
            assert env.settings.model.llm_request_timeout_seconds==.25
            assert len(result.rules)==1 and len(holder["case_payloads"])==1
            assert await store.claim(project.id,saved.id,worker_id="terminal")==None
            evidence(f"transport/{extension}-{adapter}/verification.json",snapshot=snapshot.model_dump(mode="json"),
                result=result.model_dump(mode="json"),frozen_invocation=frozen.model_dump(mode="json"),
                actual_sdk_options=observed,actual_http_calls=len(sends),physical_requests=sends,
                provider_default_timeout=.25,response_delay_seconds=.7,default_model_judgement=False)
            if directory:=os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR"):
                target=Path(directory)/"transport"/f"{extension}-{adapter}"
                for path in paths:(target/path.name).write_bytes(path.read_bytes())


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("adapter", [False, True])
@pytest.mark.parametrize("scenario", ["http_failure", "connection_closed", "deadline"])
async def test_actual_failure_single_attempt_and_unknown_recovery(tmp_path, monkeypatch, adapter, scenario):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, _, _, configs, _, store, tasks = values
        with provider(mode=scenario if scenario!="deadline" else "ok",gated=scenario=="deadline") as (endpoint,sends,_,_):
            claim, _, selection, service=await prepared(values,endpoint,adapter=adapter,
                policy=DocumentImpactInvocationPolicy(call_timeout_seconds=.3 if scenario=="deadline" else 4,
                    transport_policy="single_attempt"))
            record=await service.prepare(claim,call_key="single-transport-boundary",selection=selection)
            saved=await service.invoke(claim,record.id)
            assert len(sends)==1
            if scenario=="http_failure":
                assert saved.phase=="response_saved" and saved.response_disposition=="provider_failure"
                _,response=await service.read_response(project.id,doc.id,claim.task.id,saved.id)
                assert response.metadata["status_code"]==500
            else:
                assert saved.phase=="outcome_unknown"
                assert saved.response is None
            await configs.save_invocation_settings(project.id,DocumentImpactInvocationPolicy())
            expire(store,claim.task.id)
            restarted=PostgresDocumentImpactStore(env.settings)
            recovered=await restarted.claim(project.id,claim.task.id,worker_id="transport-recovery")
            recovery=call_service(env,tasks,restarted)
            async def forbidden(*args,**kwargs):raise AssertionError("unknown or saved reply cannot dispatch again")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime,"invoke",forbidden)
                assert await recovery.invoke(recovered,record.id)==saved
            assert len(sends)==1
            evidence(f"transport/{scenario}-{adapter}.json",record=saved.model_dump(mode="json"),
                actual_http_calls=len(sends),physical_requests=sends,original_request_not_resent=True,
                fixed_invocation=claim.seed.invocation_snapshot.model_dump(mode="json"),independent_process_restart=False)
