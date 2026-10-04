from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.documents.impact_checkpoint_io import ImpactCallCheckpointUnavailable
from src.application.documents.impact_response_service import DocumentImpactResponseService
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.application.documents.impact_workflow import DocumentImpactWorkflow, discovered_rules
from src.runtime.document_impact_task_handler import DocumentImpactTaskHandler
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.document_impact import DocumentImpactCommand
from src.schemas.document_impact_calls import DocumentImpactInvocationPolicy
from src.schemas.document_impact_workflow import DocumentImpactCoordinationContext
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.infrastructure.postgres_runtime import postgres_connect
from test_document_impact_sources import draft
from test_document_impact_calls import call_service, prepared, provider
from test_document_impact_responses import finish, read
from test_document_impact_tasks import evidence, expire, task_environment


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS, real files and actual HTTP")]


def workflow(env, tasks, store):
    calls = call_service(env, tasks, store)
    checks = DocumentImpactResponseService(calls=calls, store=store, objects=env.objects)
    return DocumentImpactWorkflow(tasks=tasks, store=store, calls=calls, responses=checks, objects=env.objects)


def responder(holder, views, case, *, missing=False, conflict=False, no_rules=False):
    def respond(body):
        payload = json.loads(body["messages"][-1]["content"])
        context = payload["coordination_context"]
        unit = payload["unit"]
        value = {"protocol_version": "impact-response-v1", "unit": unit,
            "action": "finish", "summary": "固定范围判断", "unit_outcome": "analyzed"}
        if context["stage"] == "discover_rules":
            if no_rules:
                return json.dumps(value, ensure_ascii=False)
            if unit["id"] == holder["first"].id and not payload["additional_fixed_reads"]:
                return json.dumps(read(body), ensure_ascii=False)
            if unit["id"] == holder["first"].id or conflict and unit["kind"] == "difference":
                # An additional source read supplies both original ranges before citing.
                if not payload["additional_fixed_reads"] and unit["kind"] != "segment":
                    return json.dumps(read(body), ensure_ascii=False)
                proposal = finish(holder["first"], holder["manifest"], views, case)["candidates"][0]
                proposal["case_impacts"] = []
                if conflict and unit["kind"] == "difference":
                    proposal["summary"] = "相同原文的另一种待评审解释"
                value["candidates"] = [proposal]
        else:
            assert len(payload["primary"]["case"]["version"]["content_json"]["steps"]) == 12
            holder.setdefault("case_contexts", []).append(context)
            current_case = CaseBundle.model_validate(payload["primary"]["case"])
            impacts = finish(holder["first"], holder["manifest"], views, current_case)["candidates"][0]["case_impacts"]
            value["candidates"] = [{**rule["candidate"], "key": rule["id"], "case_impacts": impacts}
                for rule in context["rules"]]
            if missing:
                value["candidates"] = []
        return json.dumps(value, ensure_ascii=False)
    def wire_response(body):
        text = respond(body)
        if json.loads(body["messages"][-1]["content"])["analysis_protocol"]["version"] == "impact-call-v4":
            from impact_bound_test_support import bound_fixture_response
            return bound_fixture_response(body, json.loads(text))
        return text
    return wire_response


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_actual_files_automatic_read_rules_to_complete_case_and_result(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path, extension) as values:
        env, project, doc, paths, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint, adapter=adapter)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            runner = workflow(env, tasks, store)
            saved = await runner.execute(claim)
            assert saved.status == "completed" and saved.snapshot_id
            snapshot, result = await runner.read_result(project.id, doc.id, saved.id)
            assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
            assert len(result.units) == len(calls.units(manifest))
            assert len(result.rules) == 1 and all(item.status == "analyzed" for item in result.units)
            assert holder["case_contexts"][0]["rules"][0]["candidate"]["summary"] == "订单保留7天调整为9天"
            assert all(item["status"] == "analyzed" for item in holder["case_contexts"][0]["discovery_units"])
            case_result = result.units[-1]
            assert case_result.candidates[0].key == result.rules[0].id
            assert case_result.candidates[0].case_impacts[0].citations[-1].value is None
            assert result.units[0].calls and len(result.units[0].calls) == 2
            assert len(sends) == len(result.units) + 1
            raw = (await env.objects.read_object_uri(snapshot.storage_uri))["content"]
            assert snapshot.sha256 == hashlib.sha256(raw).hexdigest()
            assert (await store.get_snapshot(project.id, doc.id, snapshot.id)) == snapshot
            assert await store.claim(project.id, saved.id, worker_id="terminal") is None
            async def forbidden(*args, **kwargs):
                raise AssertionError("terminal result recovery must not send, rebuild or upload")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                patch.setattr(tasks._sources, "prepare_fixed", forbidden)
                assert await runner.read_result(project.id, doc.id, saved.id) == (snapshot, result)
            with pytest.raises(ContinuationLeaseLostError):
                await store.save_snapshot_checkpoint(claim, snapshot)
            with pytest.raises(KeyError):
                await runner.read_result(str(uuid4()), doc.id, saved.id)
            directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
            if directory:
                target = Path(directory)/"workflow"/f"{extension}-{adapter}"
                target.mkdir(parents=True, exist_ok=True)
                for path in paths: (target/path.name).write_bytes(path.read_bytes())
                (target/"result.json").write_bytes(raw)
            evidence(f"workflow/{extension}-{adapter}/verification.json", snapshot=snapshot.model_dump(mode="json"),
                actual_http_calls=len(sends), case_context=holder["case_contexts"], unit_count=len(result.units),
                automatic_read=True, default_model_judgement=False, independent_process_restart=False)


@pytest.mark.parametrize("scenario", ["missing", "conflict", "no_rules", "budget"])
async def test_incomplete_coverage_overlap_no_rules_and_frozen_budget(tmp_path, scenario):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case, missing=scenario=="missing",
            conflict=scenario=="conflict", no_rules=scenario=="no_rules")) as (endpoint, sends, _, _):
            policy = DocumentImpactInvocationPolicy(max_logical_calls=1) if scenario == "budget" else None
            claim, manifest, _, calls = await prepared(values, endpoint, policy=policy)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            saved = await workflow(env, tasks, store).execute(claim)
            _, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            if scenario == "missing":
                assert result.status == "partial" and result.units[-1].status == "incomplete"
                assert "rule_case_coverage_incomplete" in result.units[-1].reason_codes
            elif scenario == "conflict":
                assert len(result.rules) == 2 and len(result.overlaps) == 1
                assert len(result.rules[1].origins) > 1  # Only exact duplicate bodies grouped.
            elif scenario == "no_rules":
                assert not result.rules and not result.overlaps
                assert result.units[-1].status == "analyzed" and holder["case_contexts"]
            else:
                assert len(sends) == 1 and result.status == "partial"
                assert all(item.status == "unprocessed" for item in result.units)
                assert all("call_budget_logical_calls" in item.reason_codes for item in result.units)
                assert len(await store.calls.list(project.id, doc.id, saved.id)) == 1
            evidence(f"workflow/{scenario}.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends))


async def test_actual_result_checkpoint_commit_lost_reply_recovers_without_http(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, sources, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            original = store.save_snapshot_checkpoint
            async def lost_reply(*args, **kwargs):
                await original(*args, **kwargs)
                raise OSError("actual commit reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(store, "save_snapshot_checkpoint", lost_reply)
                with pytest.raises(ImpactCallCheckpointUnavailable):
                    await workflow(env, tasks, store).execute(claim)
            checkpoint = await store.get_result_checkpoint(project.id, doc.id, claim.task.id)
            assert checkpoint and (await tasks.get(project.id, doc.id, claim.task.id)).phase == "result_saved"
            sent = len(sends)
            expire(store, claim.task.id)
            restarted = PostgresDocumentImpactStore(env.settings)
            recovered = await restarted.claim(project.id, claim.task.id, worker_id="workflow-recovered")
            assert recovered.snapshot == checkpoint
            second_tasks = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
                projects=env.projects, objects=env.objects, stream=None)
            async def forbidden(*args, **kwargs):
                raise AssertionError("saved result publication must not invoke or upload")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                saved = await workflow(env, second_tasks, restarted).execute(recovered)
            assert saved.status == "completed" and len(sends) == sent
            assert recovered.task.deadline_at == claim.task.deadline_at
            evidence("workflow/result-recovery.json", snapshot=checkpoint.model_dump(mode="json"),
                http_calls=sent, recovered_without_http=True, independent_process_restart=False)


async def test_handler_uses_shared_lease_and_terminal_redelivery(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            expire(store, claim.task.id)
            handler = DocumentImpactTaskHandler(workflow(env, tasks, store), store, worker_id="workflow-handler")
            delivery = SimpleNamespace(payload=DocumentImpactCommand(project_id=project.id, task_id=claim.task.id).model_dump(mode="json"))
            await handler(delivery)
            sent = len(sends)
            await handler(delivery)
            assert (await tasks.get(project.id, doc.id, claim.task.id)).status == "completed" and len(sends) == sent
            evidence("workflow/handler.json", shared_handler=True, terminal_redelivery_http=0, actual_http_calls=sent)


@pytest.mark.parametrize("mode", ["length", "empty", "tool"])
async def test_actual_incomplete_provider_responses_never_count_as_analyzed(tmp_path, mode):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(mode=mode, responder=responder(holder, views, case, no_rules=True)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            saved = await workflow(env, tasks, store).execute(claim)
            _, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            assert result.status == "partial" and not result.rules
            assert all(item.status == "ineligible" for item in result.units)
            assert len(sends) == len(result.units)
            evidence(f"workflow/ineligible-{mode}.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends))


async def test_actual_source_gap_remains_partial_even_when_model_claims_analyzed(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        extra = await draft(env, project, "manual-origin-gap", [{"source_type": "manual", "source_id": "human"}])
        await env.cases.submit_review(extra.case.id)
        await env.cases.activate(extra.case.id)
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            saved = await workflow(env, tasks, store).execute(claim)
            _, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            assert result.status == "partial" and all(item.status == "analyzed" for item in result.units)
            assert any("case_source_missing:" in reason for reason in result.basis_limitations)
            assert len(holder["case_contexts"]) == 2
            evidence("workflow/source-gap.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends))


async def test_cancelled_actual_http_becomes_unknown_without_automatic_resend(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(gated=True, responder=responder(holder, views, case)) as (endpoint, sends, arrived, release):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            execution = asyncio.create_task(workflow(env, tasks, store).execute(claim))
            assert await asyncio.to_thread(arrived.wait, 10)
            execution.cancel()
            with pytest.raises(asyncio.CancelledError): await execution
            assert len(sends) == 1
            old = (await store.calls.list(project.id, doc.id, claim.task.id))[0]
            assert old.phase == "invoking"
            release.set()
            expire(store, claim.task.id)
            recovered = await store.claim(project.id, claim.task.id, worker_id="unknown-workflow-recovery")
            saved = await workflow(env, tasks, store).execute(recovered)
            _, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            assert result.status == "partial" and result.units[0].status == "unknown"
            assert result.units[0].calls == [old.id]
            assert len(sends) == len(result.units)
            evidence("workflow/unknown-http.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends),
                original_request_not_resent=True, independent_process_restart=False)


async def test_saved_unit_results_survive_deadline_and_actual_object_reply_loss(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            original = env.objects.store_uploaded_bytes
            orphan = []
            async def lost_reply(**kwargs):
                saved = await original(**kwargs)
                if kwargs["filename"] == "workflow-result.json":
                    orphan.append(saved["uri"])
                    raise OSError("object saved, reply lost")
                return saved
            with monkeypatch.context() as patch:
                patch.setattr(env.objects, "store_uploaded_bytes", lost_reply)
                with pytest.raises(ImpactCallCheckpointUnavailable):
                    await workflow(env, tasks, store).execute(claim)
            assert orphan and await store.get_result_checkpoint(project.id, doc.id, claim.task.id) is None
            sent = len(sends)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {store._tasks} SET deadline_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (claim.task.id,))
            expire(store, claim.task.id)
            recovered = await store.claim(project.id, claim.task.id, worker_id="expired-result-recovery")
            assert recovered.remaining_seconds < 0
            async def forbidden(*args, **kwargs):
                raise AssertionError("saved calls after deadline must not invoke or rebuild input")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(tasks._sources, "prepare_fixed", forbidden)
                saved = await workflow(env, tasks, store).execute(recovered)
            snapshot, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            assert saved.status == "completed" and len(sends) == sent
            assert all(item.status == "analyzed" for item in result.units)
            assert (await env.objects.read_object_uri(orphan[0]))["content"]
            with pytest.raises(ValueError, match="大小或哈希"):
                await workflow(env, tasks, store)._read_result(snapshot.model_copy(update={"sha256":"0"*64}))
            evidence("workflow/object-deadline-recovery.json", result=result.model_dump(mode="json"),
                actual_http_calls=sent, expired_deadline=True, unknown_object_retained=True)


@pytest.mark.parametrize("lose_lease", [False, True])
async def test_shared_handler_actual_heartbeat_renewal_or_expiry_cancellation(tmp_path, monkeypatch, lose_lease):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(gated=True, responder=responder(holder, views, case)) as (endpoint, sends, arrived, release):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            store.lease_seconds = 3
            expire(store, claim.task.id)
            renewals = []
            original = store.heartbeat
            async def heartbeat(owner):
                await original(owner)
                renewals.append(owner.attempt_id)
            monkeypatch.setattr(store, "heartbeat", heartbeat)
            handler = DocumentImpactTaskHandler(workflow(env, tasks, store), store, worker_id="heartbeat-workflow")
            delivery = SimpleNamespace(payload=DocumentImpactCommand(project_id=project.id, task_id=claim.task.id).model_dump(mode="json"))
            execution = asyncio.create_task(handler(delivery))
            assert await asyncio.to_thread(arrived.wait, 10)
            if lose_lease:
                expire(store, claim.task.id)
                with pytest.raises(ContinuationLeaseLostError): await asyncio.wait_for(execution, 10)
                assert await store.get_result_checkpoint(project.id, doc.id, claim.task.id) is None
                assert (await tasks.get(project.id, doc.id, claim.task.id)).status == "running"
                assert len(sends) == 1
                release.set()
            else:
                await asyncio.sleep(4)
                assert len(renewals) >= 3
                assert await store.claim(project.id, claim.task.id, worker_id="competing-worker") is None
                release.set()
                await asyncio.wait_for(execution, 30)
                assert (await tasks.get(project.id, doc.id, claim.task.id)).status == "completed"
            evidence(f"workflow/heartbeat-{lose_lease}.json", actual_renewals=len(renewals), actual_http_calls=len(sends),
                cancelled_on_expiry=lose_lease, result_published=not lose_lease)


@pytest.mark.parametrize("fault", ["foreign_checkpoint", "changed_candidate"])
async def test_rule_handoff_checks_actual_saved_origin_before_http(tmp_path, fault):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, selection, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            runner = workflow(env, tasks, store)
            discovery = await runner._unit(claim, holder["first"], DocumentImpactCoordinationContext(
                protocol=claim.seed.workflow_protocol, stage="discover_rules"))
            assert discovery.status == "analyzed" and len(sends) == 2
            rules = discovered_rules([discovery])
            if fault == "foreign_checkpoint":
                rules[0].origins[0].checkpoint = rules[0].origins[0].checkpoint.model_copy(update={"task_id":str(uuid4())})
            else:
                rules[0].candidate = rules[0].candidate.model_copy(update={"summary":"未保存的变化候选"})
            context = DocumentImpactCoordinationContext(protocol=claim.seed.workflow_protocol, stage="case_impacts",
                rules=rules, discovery_units=[discovery])
            with pytest.raises(ValueError, match="协调规则"):
                await calls.prepare(claim, call_key="invalid-rule-handoff", selection=selection, coordination_context=context)
            assert len(sends) == 2
            assert await store.calls.get(project.id, doc.id, claim.task.id, call_key="invalid-rule-handoff") is None
            evidence(f"workflow/rejected-origin-{fault}.json", actual_http_calls=len(sends), handoff_http_calls=0)
