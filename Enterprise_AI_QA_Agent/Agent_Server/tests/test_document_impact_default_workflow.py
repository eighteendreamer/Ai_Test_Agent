"""Actual default-provider automatic workflow; preserve partial and failed evidence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_config_service import DocumentImpactConfigService
from src.application.documents.impact_response_service import DocumentImpactResponseService
from src.application.documents.impact_workflow import DocumentImpactWorkflow
from src.application.model_clients.openai_client import OpenAIChatClient
from src.runtime.document_impact_task_handler import DocumentImpactTaskHandler
from src.schemas.document_impact import DocumentImpactCommand, DocumentImpactTaskRequest
from src.schemas.document_impact_calls import DocumentImpactInvocationPolicy
from test_document_impact_default_model import real_scenario, write_json


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL") != "1",
    reason="requires actual default model and isolated actual MySQL/PG/RustFS",
)]


async def test_default_model_automatic_fixed_scope_and_terminal_history(tmp_path, monkeypatch):
    directory = Path(os.environ.get("QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR", str(tmp_path / "evidence")))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "objects").mkdir()
    wire = []
    async with real_scenario(tmp_path, directory) as (env, project, doc, diff_id, cases, store, tasks, models):
        single_attempt = os.getenv("QA_DOCUMENT_IMPACT_SINGLE_ATTEMPT") == "1"
        if single_attempt:
            configs = DocumentImpactConfigService(config_store=env.configs, project_service=env.projects)
            configured = await configs.save_invocation_settings(project.id,
                DocumentImpactInvocationPolicy(transport_policy="single_attempt"))
            write_json(directory / "transport-policy.json", configured.model_dump(mode="json"))
        default = models.get_default_model_config()
        assert default.transport == "openai_chat_completions"
        inspection = OpenAIChatClient()._make_client(default, "unused-type-inspection")
        transport_class = type(inspection._client)
        original_send = transport_class.send
        write_json(directory / "http-transport.json", {
            "class": transport_class.__module__ + "." + transport_class.__name__,
            "instrumentation": "actual SDK send unchanged; no headers or endpoint stored",
        })
        await inspection.close()

        async def trace(client, request, *args, **kwargs):
            raw = request.content
            body = json.loads(raw)
            payload = json.loads(body["messages"][-1]["content"])
            entry = {"attempt": len(wire) + 1, "method": request.method,
                "request_sha256": hashlib.sha256(raw).hexdigest(), "request_bytes": len(raw),
                "unit": payload["unit"], "stage": payload["coordination_context"]["stage"],
                "message_count": len(body["messages"])}
            entry["sdk_timeouts"] = request.extensions.get("timeout")
            (directory / f"http-request-{entry['attempt']}.json").write_bytes(raw)
            wire.append(entry)
            write_json(directory / "physical-http.json", wire)
            print("default_workflow_http_started " + json.dumps(entry), flush=True)
            start = monotonic()
            try:
                response = await original_send(client, request, *args, **kwargs)
                entry["status_code"] = response.status_code
                return response
            except asyncio.CancelledError:
                entry["outcome"] = "cancelled_without_response"
                raise
            except Exception as exc:
                entry["error_type"] = type(exc).__name__
                raise
            finally:
                entry["elapsed_seconds"] = round(monotonic() - start, 3)
                write_json(directory / "physical-http.json", wire)
                print("default_workflow_http_finished " + json.dumps(entry), flush=True)

        task = await tasks.submit(project.id, doc.id, DocumentImpactTaskRequest(
            creation_key="actual-default-automatic-workflow", difference_snapshot_id=diff_id,
            environment_key="声明环境", target_release="尚无部署证据"))
        write_json(directory / "seed.json", (await store.get_seed(project.id, doc.id, task.id)).model_dump(mode="json"))
        calls = DocumentImpactCallService(tasks=tasks, store=store, models=models, objects=env.objects, projects=env.projects)
        checks = DocumentImpactResponseService(calls=calls, store=store, objects=env.objects)
        workflow = DocumentImpactWorkflow(tasks=tasks, store=store, calls=calls, responses=checks, objects=env.objects)
        handler = DocumentImpactTaskHandler(workflow, store, worker_id="default-model-workflow")
        delivery = SimpleNamespace(payload=DocumentImpactCommand(project_id=project.id, task_id=task.id).model_dump(mode="json"))
        start = monotonic()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(transport_class, "send", trace)
                await handler(delivery)
        finally:
            saved = await tasks.get(project.id, doc.id, task.id)
            write_json(directory / "task.json", saved.model_dump(mode="json"))
            records = await store.calls.list(project.id, doc.id, task.id)
            write_json(directory / "calls.json", [record.model_dump(mode="json") for record in records])
            for index, record in enumerate(records, 1):
                _, request = await calls.read_request(project.id, doc.id, task.id, record.id)
                write_json(directory / f"logical-request-{index}.json", request.model_dump(mode="json"))
                if record.phase == "response_saved":
                    _, response = await calls.read_response(project.id, doc.id, task.id, record.id)
                    write_json(directory / f"logical-response-{index}.json", response.model_dump(mode="json"))
                    checkpoint, assessment = await checks.read(project.id, doc.id, task.id, record.id)
                    write_json(directory / f"logical-assessment-{index}.json", {
                        "checkpoint": checkpoint.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json")})
            write_json(directory / "observation.json", {
                "elapsed_seconds": round(monotonic() - start, 3), "logical_calls": len(records),
                "physical_sends": len(wire), "task_status": saved.status,
                "independent_worker_process": False, "production_historical_migration": False,
                "source_default_configuration_changed": False,
            })

        assert saved.status == "completed", {"status": saved.status, "error_code": saved.error_code}
        snapshot, result = await workflow.read_result(project.id, doc.id, task.id)
        _, manifest = await tasks.read_manifest(project.id, doc.id, task.id)
        write_json(directory / "manifest.json", manifest.model_dump(mode="json"))
        write_json(directory / "snapshot.json", snapshot.model_dump(mode="json"))
        write_json(directory / "result.json", result.model_dump(mode="json"))
        sent = len(wire)
        async def forbidden(*args, **kwargs):
            raise AssertionError("terminal history must not send, rebuild or upload")
        with monkeypatch.context() as patch:
            patch.setattr(models, "invoke", forbidden)
            patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
            patch.setattr(tasks._sources, "prepare_fixed", forbidden)
            await handler(delivery)
            assert await workflow.read_result(project.id, doc.id, task.id) == (snapshot, result)
        assert len(wire) == sent
        if single_attempt:
            assert len(wire) == len(records), "single-attempt SDK must not repeat a logical request"
        assert await store.claim(project.id, task.id, worker_id="terminal") is None
        assert [item.unit for item in result.units] == calls.units(manifest)
        assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
        write_json(directory / "acceptance.json", {
            "all_fixed_units_retained": True, "terminal_redelivery_without_http": True,
            "terminal_unclaimable": True, "coverage_status": result.status,
            "unit_statuses": [item.status for item in result.units],
            "semantic_approval": False, "deployment_status": result.deployment_status,
        })
        # Save the actual partial outcome first; do not fabricate an accepted response.
        assert all(item.status == "analyzed" and not item.reason_codes and not item.unresolved for item in result.units), {
            "units": [{"unit": item.unit.model_dump(), "status": item.status,
                "reason_codes": item.reason_codes, "unresolved": item.unresolved} for item in result.units]}
        assert {rule.candidate.kind for rule in result.rules} >= {"modified", "removed", "added"}
        assert len([item for item in result.units if item.unit.kind == "case"]) == len(cases)
