"""New actual-provider diagnostic through the existing streaming context only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import monotonic

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_response_service import DocumentImpactResponseService
from src.application.documents.impact_workflow import discovered_rules, DocumentImpactWorkflow
from src.application.model_clients.openai_client import OpenAIChatClient
from src.schemas.document_impact import DocumentImpactTaskRequest
from src.schemas.document_impact_calls import DocumentImpactCallSelection
from src.schemas.document_impact_workflow import DocumentImpactCoordinationContext, DocumentImpactUnitResult
from test_document_impact_default_model import real_scenario, write_json


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL") != "1",
    reason="requires actual default provider and isolated actual MySQL/PG/RustFS",
)]


async def test_actual_default_stream_complete_fixed_evidence(tmp_path, monkeypatch):
    directory = Path(os.environ.get("QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR", str(tmp_path / "evidence")))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "objects").mkdir()
    wire, deltas = [], []
    async with real_scenario(tmp_path, directory) as (env, project, doc, diff_id, cases, store, tasks, models):
        default = models.get_default_model_config()
        assert default.transport == "openai_chat_completions" and default.capabilities.streaming
        inspection = OpenAIChatClient()._make_client(default, "unused-type-inspection")
        transport_class = type(inspection._client)
        original_send = transport_class.send
        await inspection.close()
        write_json(directory / "http-transport.json", {
            "class": transport_class.__module__ + "." + transport_class.__name__,
            "instrumentation": "actual SDK send unchanged; no headers or endpoint stored",
        })

        async def trace(client, request, *args, **kwargs):
            raw = request.content
            body = json.loads(raw)
            entry = {"attempt": len(wire) + 1, "method": request.method,
                "request_sha256": hashlib.sha256(raw).hexdigest(), "request_bytes": len(raw),
                "stream": body.get("stream"), "stream_options": body.get("stream_options"),
                "message_count": len(body["messages"])}
            (directory / f"http-request-{entry['attempt']}.json").write_bytes(raw)
            wire.append(entry)
            write_json(directory / "physical-http.json", wire)
            print("impact_stream_http_started " + json.dumps(entry), flush=True)
            started = monotonic()
            try:
                response = await original_send(client, request, *args, **kwargs)
                entry["status_code"] = response.status_code
                return response
            except asyncio.CancelledError:
                entry["outcome"] = "cancelled_before_headers"
                raise
            except Exception as exc:
                entry["error_type"] = type(exc).__name__
                raise
            finally:
                # SDK stream send returns at headers, before complete text consumption.
                entry["send_elapsed_seconds"] = round(monotonic() - started, 3)
                write_json(directory / "physical-http.json", wire)

        task = await tasks.submit(project.id, doc.id, DocumentImpactTaskRequest(
            creation_key="new-stream-complete-evidence-diagnostic", difference_snapshot_id=diff_id,
            environment_key="声明环境", target_release="尚无部署证据"))
        claim = await store.claim(project.id, task.id, worker_id="stream-diagnostic")
        await tasks.prepare_input(claim)
        await tasks.prepare_manifest(claim)
        _, manifest = await tasks.read_manifest(project.id, doc.id, task.id)
        calls = DocumentImpactCallService(tasks=tasks, store=store, models=models, objects=env.objects, projects=env.projects)
        checks = DocumentImpactResponseService(calls=calls, store=store, objects=env.objects)
        units = calls.units(manifest)
        unit = next(item for item in units if item.kind == "difference")
        selection = DocumentImpactCallSelection(unit=unit, segment_ids=[item.id for item in manifest.segments],
            case_version_ids=[case.version.id for case in cases],
            difference_ids=[item.id for item in units if item.kind == "difference" and item != unit])
        record = await calls.prepare(claim, call_key="stream-complete-evidence", selection=selection)
        _, request = await calls.read_request(project.id, doc.id, task.id, record.id)
        write_json(directory / "seed.json", claim.seed.model_dump(mode="json"))
        write_json(directory / "manifest.json", manifest.model_dump(mode="json"))
        write_json(directory / "request.json", request.model_dump(mode="json"))
        start = monotonic()

        async def observe(delta):
            deltas.append({"index": len(deltas) + 1, "elapsed_seconds": round(monotonic() - start, 3), "text": delta})

        try:
            with monkeypatch.context() as patch:
                patch.setattr(transport_class, "send", trace)
                async with models.stream_handler(observe):
                    record = await calls.invoke(claim, record.id)
        finally:
            saved = await store.calls.get(project.id, doc.id, task.id, call_id=record.id)
            write_json(directory / "call.json", saved.model_dump(mode="json"))
            write_json(directory / "text-deltas.json", deltas)
            write_json(directory / "observation.json", {
                "elapsed_seconds": round(monotonic() - start, 3), "physical_sends": len(wire),
                "first_text_seconds": deltas[0]["elapsed_seconds"] if deltas else None,
                "received_text_chars": sum(len(item["text"]) for item in deltas),
                "phase": saved.phase, "outcome": saved.outcome_code,
                "full_automatic_workflow": False, "source_default_configuration_changed": False,
                "transport_only_causal_experiment": False, "semantic_approval": False,
            })
        assert saved.phase == "response_saved", {"phase": saved.phase, "outcome": saved.outcome_code}
        _, response = await calls.read_response(project.id, doc.id, task.id, saved.id)
        write_json(directory / "response.json", response.model_dump(mode="json"))
        checkpoint, assessment = await checks.assess(claim, saved.id)
        write_json(directory / "assessment.json", {
            "checkpoint": checkpoint.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json")})
        assert wire and all(item["stream"] is True for item in wire)
        assert "".join(item["text"] for item in deltas).strip() == response.text
        assert response.metadata["mode"] == "ok" and response.metadata["finish_reason"] == "stop"
        assert assessment.status == "unit_finished", {"status": assessment.status,
            "issues": [issue.code for issue in assessment.issues]}
        assert assessment.semantic_status == "pending_review" and assessment.deployment_status == "unknown"
        assert {candidate.kind for candidate in assessment.decision.candidates} >= {"modified", "removed", "added"}
        assert any(candidate.case_impacts for candidate in assessment.decision.candidates)
        assert (await checks.read(project.id, doc.id, task.id, saved.id))[1] == assessment
        assert (await calls.read_response(project.id, doc.id, task.id, saved.id))[1] == response


async def test_actual_default_stream_discovery_then_complete_cases(tmp_path, monkeypatch):
    directory = Path(os.environ.get("QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR", str(tmp_path / "evidence")))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "objects").mkdir()
    wire, observations = [], []
    active = {}
    async with real_scenario(tmp_path, directory) as (env, project, doc, diff_id, cases, store, tasks, models):
        default = models.get_default_model_config()
        assert default.transport == "openai_chat_completions" and default.capabilities.streaming
        effort = os.getenv("QA_IMPACT_DIAGNOSTIC_REASONING_EFFORT")
        if effort is not None:
            assert effort in {"low", "medium", "high"}
            # Diagnostic-only parameter, explicitly separate from the production request contract.
            # Official Step 5 Chat Completions docs and the installed SDK define this field.
            await asyncio.to_thread(env.configs.save, "qa.impact_diagnostic_settings",
                {"reasoning_effort": effort}, scope_key=project.id)
            setting = await asyncio.to_thread(env.configs.get, "qa.impact_diagnostic_settings", scope_key=project.id)
            effort = setting.payload["reasoning_effort"]
            write_json(directory / "diagnostic-wire-setting.json", {"key": "qa.impact_diagnostic_settings",
                "scope_key": setting.scope_key, "revision": setting.revision, "payload": setting.payload,
                "production_seed_includes_parameter": False, "source_model_configuration_changed": False})
        original_params = OpenAIChatClient._build_params
        def diagnostic_params(client, config, request, tool_name_map):
            result = original_params(client, config, request, tool_name_map)
            if effort is not None:
                assert config.key == default.key
                result["reasoning_effort"] = effort
            return result
        inspection = OpenAIChatClient()._make_client(default, "unused-type-inspection")
        transport_class = type(inspection._client)
        original_send = transport_class.send
        await inspection.close()
        write_json(directory / "http-transport.json", {
            "class": transport_class.__module__ + "." + transport_class.__name__,
            "instrumentation": "actual send and response.aiter_bytes unchanged; no headers or endpoint stored",
        })

        async def trace(client, request, *args, **kwargs):
            raw = request.content
            body = json.loads(raw)
            entry = {"attempt": len(wire) + 1, "logical_call_id": active["id"], "stage": active["stage"],
                "request_sha256": hashlib.sha256(raw).hexdigest(), "request_bytes": len(raw),
                "stream": body.get("stream"), "stream_options": body.get("stream_options"),
                "diagnostic_reasoning_effort": body.get("reasoning_effort")}
            (directory / f"http-request-{entry['attempt']}.json").write_bytes(raw)
            wire.append(entry)
            write_json(directory / "physical-http.json", wire)
            started = monotonic()
            try:
                response = await original_send(client, request, *args, **kwargs)
                entry["status_code"] = response.status_code
                original_iter = response.aiter_bytes

                async def observed_bytes(*iter_args, **iter_kwargs):
                    chunks = []
                    with (directory / f"http-response-{entry['attempt']}.sse").open("wb") as stream_file:
                        try:
                            async for chunk in original_iter(*iter_args, **iter_kwargs):
                                chunks.append({"index": len(chunks) + 1,
                                    "elapsed_seconds": round(monotonic() - started, 3), "size_bytes": len(chunk),
                                    "sha256": hashlib.sha256(chunk).hexdigest()})
                                stream_file.write(chunk)
                                stream_file.flush()
                                yield chunk
                        finally:
                            write_json(directory / f"http-response-{entry['attempt']}-chunks.json", chunks)
                response.aiter_bytes = observed_bytes
                return response
            except asyncio.CancelledError:
                entry["outcome"] = "cancelled_before_headers"
                raise
            except Exception as exc:
                entry["error_type"] = type(exc).__name__
                raise
            finally:
                entry["send_elapsed_seconds"] = round(monotonic() - started, 3)
                write_json(directory / "physical-http.json", wire)

        task = await tasks.submit(project.id, doc.id, DocumentImpactTaskRequest(
            creation_key="new-stream-stage-diagnostic", difference_snapshot_id=diff_id,
            environment_key="声明环境", target_release="尚无部署证据"))
        claim = await store.claim(project.id, task.id, worker_id="stream-stage-diagnostic")
        await tasks.prepare_input(claim)
        await tasks.prepare_manifest(claim)
        _, manifest = await tasks.read_manifest(project.id, doc.id, task.id)
        calls = DocumentImpactCallService(tasks=tasks, store=store, models=models, objects=env.objects, projects=env.projects)
        checks = DocumentImpactResponseService(calls=calls, store=store, objects=env.objects)
        units = calls.units(manifest)
        write_json(directory / "seed.json", claim.seed.model_dump(mode="json"))
        write_json(directory / "manifest.json", manifest.model_dump(mode="json"))

        async def invoke_fixed(selection, context):
            index = len(observations) + 1
            record = await calls.prepare(claim, call_key=f"stream-stage-{index}", selection=selection,
                coordination_context=context)
            _, request = await calls.read_request(project.id, doc.id, task.id, record.id)
            write_json(directory / f"logical-request-{index}.json", request.model_dump(mode="json"))
            active.update(id=record.id, stage=context.stage)
            start, deltas = monotonic(), []
            async def observe(delta):
                deltas.append({"index": len(deltas) + 1, "elapsed_seconds": round(monotonic() - start, 3), "text": delta})
            try:
                async with models.stream_handler(observe):
                    record = await calls.invoke(claim, record.id)
            finally:
                saved = await store.calls.get(project.id, doc.id, task.id, call_id=record.id)
                write_json(directory / f"logical-call-{index}.json", saved.model_dump(mode="json"))
                write_json(directory / f"text-deltas-{index}.json", deltas)
                observation = {"logical_call_id": record.id, "stage": context.stage,
                    "elapsed_seconds": round(monotonic() - start, 3), "phase": saved.phase,
                    "outcome": saved.outcome_code, "first_text_seconds": deltas[0]["elapsed_seconds"] if deltas else None,
                    "received_text_chars": sum(len(item["text"]) for item in deltas)}
                observations.append(observation)
                write_json(directory / "observations.json", observations)
                print("impact_stream_stage_observation " + json.dumps(observation), flush=True)
            assert saved.phase == "response_saved", {"phase": saved.phase, "outcome": saved.outcome_code}
            _, response = await calls.read_response(project.id, doc.id, task.id, record.id)
            write_json(directory / f"logical-response-{index}.json", response.model_dump(mode="json"))
            checkpoint, assessment = await checks.assess(claim, saved.id)
            write_json(directory / f"logical-assessment-{index}.json", {
                "checkpoint": checkpoint.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json")})
            assert "".join(item["text"] for item in deltas).strip() == response.text
            assert response.metadata["mode"] == "ok" and response.metadata["finish_reason"] == "stop"
            assert assessment.status == "unit_finished", {"status": assessment.status,
                "issues": [issue.code for issue in assessment.issues]}
            return DocumentImpactUnitResult(unit=selection.unit, status=assessment.decision.unit_outcome,
                calls=[record.id], final_check=checkpoint, candidates=assessment.decision.candidates,
                unresolved=assessment.decision.unresolved)

        unit = next(item for item in units if item.kind == "difference")
        context = DocumentImpactCoordinationContext(protocol=claim.seed.workflow_protocol, stage="discover_rules")
        selection = DocumentImpactCallSelection(unit=unit, segment_ids=[item.id for item in manifest.segments],
            difference_ids=[item.id for item in units if item.kind == "difference" and item != unit])
        with monkeypatch.context() as patch:
            patch.setattr(transport_class, "send", trace)
            if effort is not None:
                patch.setattr(OpenAIChatClient, "_build_params", diagnostic_params)
            discovery = await invoke_fixed(selection, context)
            rules = discovered_rules([discovery])
            write_json(directory / "discovered-rules.json", [rule.model_dump(mode="json") for rule in rules])
            assert {rule.candidate.kind for rule in rules} >= {"modified", "removed", "added"}
            assert all(not rule.candidate.case_impacts for rule in rules)
            context = DocumentImpactCoordinationContext(protocol=claim.seed.workflow_protocol, stage="case_impacts",
                rules=rules, discovery_units=[discovery])
            results = []
            for unit in [item for item in units if item.kind == "case"]:
                selection = DocumentImpactCallSelection(unit=unit, segment_ids=[item.id for item in manifest.segments],
                    ranges=DocumentImpactWorkflow._rule_reads(rules))
                result = await invoke_fixed(selection, context)
                results.append(result)
                write_json(directory / "case-results.json", [item.model_dump(mode="json") for item in results])
                assert {candidate.key for candidate in result.candidates} == {rule.id for rule in rules}
                assert all([impact.version_id for impact in candidate.case_impacts] == [unit.id] for candidate in result.candidates)
        assert len(results) == len(cases) and all(item.status == "analyzed" for item in results)
        write_json(directory / "acceptance.json", {"diagnostic_stages_returned": True,
            "full_automatic_workflow": False, "semantic_approval": False, "deployment_status": "unknown",
            "diagnostic_reasoning_effort": effort, "production_seed_includes_parameter": False})
