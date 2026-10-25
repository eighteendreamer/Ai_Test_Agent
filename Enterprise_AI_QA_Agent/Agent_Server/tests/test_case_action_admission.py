"""Actual pending owned action must respect later explicit applicability review."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from test_business_applicability import decisions_environment, decision_request, save_decision_evidence
from test_business_environment_admission import agent_runtime
from test_case_action_binding import owned_plan, OwnedRuleAgent
from test_requirement_execution import retention_http
from test_requirement_reviews import LIVE
from src.application.test_runs.agent_case_execution import AgentCaseApprovalWait
from src.schemas.run_management import RunItemLeaseRequest, RunItemApprovalWaitRequest
from src.schemas.session import ApprovalDecisionRequest, ToolApprovalStatus
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.tool_runtime import ModelToolCall


def selected_review(f, *, index=1, key="selected-invalid", status="effective", case_status="invalid", previous=None):
    body = decision_request(f, index, key=key, status=status, case_status=case_status, previous=previous)
    body.update(coverage_id=f.coverage["id"], expected_coverage_sha256=f.coverage["sha256"])
    body["cases"][0]["case_version_id"] = f.selected.version.id
    return body


@asynccontextmanager
async def active_actions(f, *, multiple=False, environment=True):
    plan, detail = await owned_plan(f, multiple, admission=True, environment=environment)
    async with agent_runtime(f, OwnedRuleAgent(), action_admission=True) as (runs, adapter, reader, clock):
        entry, attempt = (await f.runs.claim_items(run_id=detail.run.id, worker_id="action-protocol",
            limit=1, lease_seconds=120, now=clock[0]))[0]
        await runs.start_item(entry.id, RunItemLeaseRequest(lease_token=attempt.lease_token))
        entry = await runs.get_item(entry.id)
        context, checkpoint, attempt = await adapter._load(run=await runs.get_record(detail.run.id), item=entry,
            version=await f.env.cases.get_version(entry.case_version_id), attempt_id=attempt.id)
        await adapter._save(entry, attempt, checkpoint.model_copy(update={"phase":"running"}))
        execution = ToolExecutionContext(session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
            trace_id="actual-action-admission", user_message="核对原固定动作", normalized_input="核对原固定动作",
            context_bundle={**context.runtime_context(), "case_execution_managed":True,
                "attempt_id":checkpoint.execution_attempt_id})
        runtime = ToolRuntimeService(tool_job_service=f.jobs)
        def call(key="target", identity="action-call"):
            return ModelToolCall(id=identity, name="api-test-runner", arguments={ARGUMENT:{"item_key":key},
                "worker_action":"execute_task", "task":{"task_id":identity,"method":"GET",
                    "path":f.snapshots[1]["configuration"]["payload"]["base_url"]+"/rule"}})
        yield plan, entry, attempt, execution, runtime, call


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_owned_pending_action_stops_after_explicit_invalid(tmp_path, retention_http, extension):
    async with decisions_environment(tmp_path, retention_http, extension) as f:
        plan, detail = await owned_plan(f, False, admission=True)
        agent = OwnedRuleAgent()
        async with agent_runtime(f, agent, action_admission=True) as (runs, adapter, reader, clock):
            entry, attempt = (await f.runs.claim_items(run_id=detail.run.id, worker_id="action-admission",
                limit=1, lease_seconds=120, now=clock[0]))[0]
            await runs.start_item(entry.id, RunItemLeaseRequest(lease_token=attempt.lease_token))

            async def args():
                return {"run": await runs.get_record(detail.run.id), "item": await runs.get_item(entry.id),
                    "case": await f.env.cases.get_case(entry.case_id),
                    "version": await f.env.cases.get_version(entry.case_version_id),
                    "attempt_id": (await runs.get_latest_attempt(entry.id)).id}

            before = [len(calls) for calls in f.actual_target_calls]
            waiting = await adapter.execute(**await args())
            assert isinstance(waiting, AgentCaseApprovalWait)
            approval = next(a for a in await reader.list_approvals(waiting.session_id) if a.id == waiting.approval_id)
            await runs.mark_waiting_approval(entry.id, RunItemApprovalWaitRequest(lease_token=attempt.lease_token,
                approval_id=approval.id, tool_job_id=waiting.tool_job_id, reason=waiting.summary,
                approval_scope_hash=approval.metadata["approval_scope_hash"]))
            pending = await f.jobs.get_job(waiting.tool_job_id)
            assert pending.status.value == "waiting_approval"
            assert pending.metadata["case_action_binding"]["item_key"] == "target"
            assert [len(calls) for calls in f.actual_target_calls] == before
            review_request = decision_request(f, 1,
                key="owned-invalid-while-pending", status="effective", case_status="invalid")
            review_request.update(coverage_id=f.coverage["id"], expected_coverage_sha256=f.coverage["sha256"])
            review_request["cases"][0]["case_version_id"] = entry.case_version_id
            assert entry.case_version_id == f.selected.version.id
            invalid = await f.decision_client.post(f.decision_routes[1], json=review_request)
            assert invalid.status_code == 201, invalid.text
            assert invalid.json()["cases"][0]["case_version_id"] == entry.case_version_id
            save_decision_evidence(f, "owned-pending-before-"+extension, fixed_plan=plan.model_dump(mode="json"),
                pending_job=pending.model_dump(mode="json"), approval=approval.model_dump(mode="json"),
                current_review=invalid.json(), actual_HTTP_before=before,
                model_requests=[r.model_dump(mode="json") for r in agent.requests])
            resumed = await runs.resume_waiting_approval(entry.id, approval.id)
            await runs.start_item(entry.id, RunItemLeaseRequest(lease_token=resumed.lease_token))
            outcome = await adapter.resume_approval(**await args(), approval_id=approval.id,
                payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
            job = await f.jobs.get_job(waiting.tool_job_id)
            after = [len(calls) for calls in f.actual_target_calls]
            save_decision_evidence(f, "owned-pending-after-"+extension, fixed_plan=plan.model_dump(mode="json"),
                pending_job=pending.model_dump(mode="json"), actual_job=job.model_dump(mode="json"),
                approval=approval.model_dump(mode="json"), current_review=invalid.json(),
                actual_result=outcome.completion.model_dump(mode="json"), actual_HTTP_before=before,
                actual_HTTP_after=after, new_pending_action_HTTP=after[1]-before[1],
                model_requests=[r.model_dump(mode="json") for r in agent.requests],
                controlled_model=True, autonomous_external_model_verified=False,
                sole_case_without_preparation_or_cleanup=True)
            assert after == before, "owned pending action still sent HTTP after exact explicit invalid"
            assert job.status.value == "denied" and job.started_at is None
            assert job.metadata["case_action_admission"]["decision"] == "explicit_invalid"
            assert job.metadata["case_action_admission"]["restricted_review_ids"] == [invalid.json()["id"]]


def test_old_policy_wire_and_new_strategy_requires_owned_actions():
    assert "action_admission_protocol" not in CasePlanningPolicy().model_dump(mode="json")
    with pytest.raises(ValueError, match="固定执行项"):
        CasePlanningPolicy(action_admission_protocol="case-action-admission-v1")


@pytest.mark.asyncio
@LIVE
async def test_actual_before_case_denied_after_preserved_and_terminal_replay(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f, multiple=True) as (plan, entry, attempt, execution, runtime, call):
            review = await f.decision_client.post(f.decision_routes[1], json=selected_review(f))
            assert review.status_code == 201, review.text
            await f.env.service.save_policy(f.project.id, CasePlanningPolicy())
            before = [len(calls) for calls in f.actual_target_calls]
            saved, records = [], []
            for key in ("seed", "target", "after_read"):
                record = await runtime.execute(ToolRegistry().get("api-test-runner"), call(key,key), execution)
                assert record.status == ("completed" if key == "after_read" else "denied")
                job = await f.jobs.get_job(record.job_id)
                receipt = job.metadata["case_action_admission"]
                assert receipt["decision"] == ("after_not_restricted" if key == "after_read" else "explicit_invalid")
                assert receipt["restricted_review_ids"] == [review.json()["id"]]
                assert not receipt["after_phase_is_cleanup_evidence"]
                repeated = await runtime.execute(ToolRegistry().get("api-test-runner"), call(key,key),
                    replace(execution, tool_job_id=job.id))
                assert repeated.job_id == job.id and repeated.status == record.status
                assert await f.jobs.get_job(job.id) == job
                saved.append(job.model_dump(mode="json"))
                records.append(record)
            assert [len(calls) for calls in f.actual_target_calls] == [before[0], before[1]+1]
            assert plan.config_snapshot.policy.action_admission_protocol == "case-action-admission-v1"
            from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
            from src.schemas.case_execution import AgentCaseExecutionContext
            manifest = await AgentCaseAssessmentService(f.jobs).evidence_manifest(
                context=AgentCaseExecutionContext.model_validate(execution.context_bundle["case_execution"]),
                tool_records=records, session_id=execution.session_id, turn_id=execution.turn_id,
                attempt_id=execution.context_bundle["attempt_id"])
            assert [r["case_action_admission"] for r in manifest] == [r["metadata"]["case_action_admission"] for r in saved]
            save_decision_evidence(f, "action-admission-phases", fixed_plan=plan.model_dump(mode="json"),
                actual_owned_jobs=saved, actual_evidence_manifest=manifest, actual_HTTP_before=before,
                actual_HTTP_after=[len(calls) for calls in f.actual_target_calls],
                actual_stateful_cleanup_verified=False, terminal_replay_new_HTTP=0)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("scope", ["unknown", "other_version", "other_target", "revoked"])
async def test_actual_nonrestricting_reviews_do_not_block_owned_target(tmp_path, retention_http, scope):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            index = 0 if scope == "other_target" else 1
            body = (decision_request(f, index, status="effective",case_status="invalid") if scope == "other_version"
                else selected_review(f, index=index, status="unknown" if scope == "unknown" else "effective",
                    case_status="unknown" if scope == "unknown" else "invalid"))
            review = await f.decision_client.post(f.decision_routes[index], json=body)
            assert review.status_code == 201, review.text
            if scope == "revoked":
                body = selected_review(f, key="revoked-selected", previous=review.json()["id"])
                body.update(action="revoke", note="明确撤销当前固定确认", points=[], cases=[], observations=[])
                reply = await f.decision_client.post(f.decision_routes[1], json=body)
                assert reply.status_code == 201, reply.text
            before = [len(calls) for calls in f.actual_target_calls]
            record = await runtime.execute(ToolRegistry().get("api-test-runner"), call(), execution)
            assert record.status == "completed"
            job = await f.jobs.get_job(record.job_id)
            assert job.metadata["case_action_admission"]["decision"] == "no_explicit_invalid"
            assert [len(calls) for calls in f.actual_target_calls] == [before[0],before[1]+1]
            save_decision_evidence(f, "action-admission-scope-"+scope, fixed_plan=plan.model_dump(mode="json"),
                actual_job=job.model_dump(mode="json"), actual_HTTP_before=before,
                actual_HTTP_after=[len(calls) for calls in f.actual_target_calls])


@pytest.mark.asyncio
@LIVE
async def test_actual_plan_without_fixed_target_records_missing_environment_without_guessing(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f, environment=False) as (plan, entry, attempt, execution, runtime, call):
            before = [len(calls) for calls in f.actual_target_calls]
            record = await runtime.execute(ToolRegistry().get("api-test-runner"), call(), execution)
            job = await f.jobs.get_job(record.job_id)
            assert record.status == "completed" and plan.environment is None
            assert job.metadata["case_action_admission"]["decision"] == "no_fixed_environment"
            assert job.metadata["case_action_admission"]["reviews"] == []
            assert [len(calls) for calls in f.actual_target_calls] == [before[0],before[1]+1]
            save_decision_evidence(f, "action-admission-no-fixed-target", fixed_plan=plan.model_dump(mode="json"),
                actual_job=job.model_dump(mode="json"), actual_HTTP_before=before,
                actual_HTTP_after=[len(calls) for calls in f.actual_target_calls],
                no_environment_or_deployment_inferred=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_lost_claim_commit_reply_stays_unknown_without_resend(tmp_path, retention_http, monkeypatch):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            original = f.jobs._case_action_admission._claim
            tool = ToolRegistry().get("api-test-runner")
            request = call()
            execution = execution.bind_call(request)
            job = await f.jobs.create_job(tool=tool, call_id=request.id, session_id=execution.session_id,
                turn_id=execution.turn_id, trace_id=execution.trace_id, input_payload=request.arguments,
                metadata=execution.job_metadata(), once_per_call=True)
            execution = replace(execution, tool_job_id=job.id)
            before = [len(calls) for calls in f.actual_target_calls]
            def lost(*args):
                original(*args)
                raise ConnectionError("actual PG action claim commit reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(f.jobs._case_action_admission, "_claim", lost)
                with pytest.raises(CaseActionClaimUnavailable):
                    await runtime.execute(tool, request, execution)
            persisted = await f.jobs.get_job(job.id)
            assert persisted.status.value == "running" and persisted.started_at is not None
            assert persisted.metadata["case_action_admission"]["decision"] == "no_explicit_invalid"
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool, request, execution)
            assert await f.jobs.get_job(job.id) == persisted
            assert [len(calls) for calls in f.actual_target_calls] == before
            save_decision_evidence(f, "action-admission-lost-claim", fixed_plan=plan.model_dump(mode="json"),
                actual_job=persisted.model_dump(mode="json"), actual_HTTP_before=before, actual_HTTP_after=before,
                actual_claim_commit_reply_lost=True, original_job_not_resent=True,
                logical_operation_identity_implemented=False)


@pytest.mark.asyncio
@LIVE
async def test_actual_return_lost_after_http_preserves_started_unknown_without_second_send(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            request = call()
            execution = execution.bind_call(request)
            tool = ToolRegistry().get("api-test-runner")
            job = await f.jobs.create_job(tool=tool, call_id=request.id, session_id=execution.session_id,
                turn_id=execution.turn_id, trace_id=execution.trace_id, input_payload=request.arguments,
                metadata=execution.job_metadata(), once_per_call=True)
            execution = replace(execution, tool_job_id=job.id)
            before = [len(calls) for calls in f.actual_target_calls]
            original = runtime._handlers[tool.key]
            async def lose_response(arguments, context):
                await original(arguments, context)
                raise ConnectionError("actual HTTP returned but handler reply unavailable")
            runtime._handlers[tool.key] = lose_response
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool, request, execution)
            persisted = await f.jobs.get_job(job.id)
            assert persisted.status.value == "running" and "tool_output_observation" not in persisted.metadata
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool, request, execution)
            assert await f.jobs.get_job(job.id) == persisted
            assert [len(calls) for calls in f.actual_target_calls] == [before[0],before[1]+1]
            save_decision_evidence(f, "action-admission-lost-http-return", fixed_plan=plan.model_dump(mode="json"),
                actual_job=persisted.model_dump(mode="json"), actual_HTTP_before=before,
                actual_HTTP_after=[len(calls) for calls in f.actual_target_calls],
                actual_HTTP_return_lost=True, original_job_not_resent=True,
                logical_operation_identity_implemented=False)


@pytest.mark.asyncio
@LIVE
async def test_actual_saved_http_return_recovers_delivery_before_later_invalid_without_resend(tmp_path, retention_http, monkeypatch):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            request = call()
            execution = execution.bind_call(request)
            tool = ToolRegistry().get("api-test-runner")
            job = await f.jobs.create_job(tool=tool, call_id=request.id, session_id=execution.session_id,
                turn_id=execution.turn_id, trace_id=execution.trace_id, input_payload=request.arguments,
                metadata=execution.job_metadata(), once_per_call=True)
            execution = replace(execution, tool_job_id=job.id)
            before = [len(calls) for calls in f.actual_target_calls]
            async def interrupted(*args, **kwargs):
                raise ConnectionError("actual stored response settlement interrupted")
            with monkeypatch.context() as patch:
                patch.setattr(f.jobs,"finish_output_processing",interrupted)
                patch.setattr(f.jobs,"recover_observed_output",interrupted)
                with pytest.raises(ConnectionError):
                    await runtime.execute(tool, request, execution)
            observed = await f.jobs.get_job(job.id)
            assert observed.status.value == "running" and "tool_output_observation" in observed.metadata
            assert observed.output_payload["task_result"]["response_body"]["expired"] is False
            reply = await f.decision_client.post(f.decision_routes[1], json=selected_review(f))
            assert reply.status_code == 201, reply.text
            repeated = await runtime.execute(tool, request, execution)
            recovered = await f.jobs.get_job(job.id)
            assert repeated.status == recovered.status.value == "partial"
            assert recovered.output_payload["output_delivery"]["status"] == "interrupted"
            assert recovered.output_payload["task_result"] == observed.output_payload["task_result"]
            assert recovered.metadata["case_action_admission"] == observed.metadata["case_action_admission"]
            assert [len(calls) for calls in f.actual_target_calls] == [before[0],before[1]+1]
            save_decision_evidence(f, "action-admission-observed-return", fixed_plan=plan.model_dump(mode="json"),
                observed_job=observed.model_dump(mode="json"), actual_job=recovered.model_dump(mode="json"),
                later_review=reply.json(), actual_HTTP_before=before,
                actual_HTTP_after=[len(calls) for calls in f.actual_target_calls],
                reliable_original_return_preserved=True, recovery_new_HTTP=0,
                partial_is_not_business_pass=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_concurrent_claim_is_single_and_later_review_preserves_claimed_effect(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            request = call()
            execution = execution.bind_call(request)
            tool = ToolRegistry().get("api-test-runner")
            job = await f.jobs.create_job(tool=tool, call_id=request.id, session_id=execution.session_id,
                turn_id=execution.turn_id, trace_id=execution.trace_id, input_payload=request.arguments,
                metadata=execution.job_metadata(), once_per_call=True)
            execution = replace(execution, tool_job_id=job.id)
            entered, release = asyncio.Event(), asyncio.Event()
            original = runtime._handlers[tool.key]
            async def held(arguments, context):
                entered.set()
                await release.wait()
                return await original(arguments, context)
            runtime._handlers[tool.key] = held
            before = [len(calls) for calls in f.actual_target_calls]
            tasks = [asyncio.create_task(runtime.execute(tool, request, execution)) for _ in range(4)]
            try:
                await asyncio.wait_for(entered.wait(), 5)
                done, pending = await asyncio.wait(tasks, timeout=2)
                assert len(done) == 3 and len(pending) == 1
                assert all(isinstance(t.exception(), CaseActionClaimUnavailable) for t in done)
                claimed = await f.jobs.get_job(job.id)
                assert claimed.status.value == "running"
                review = await f.decision_client.post(f.decision_routes[1], json=selected_review(f))
                assert review.status_code == 201, review.text
                assert [len(calls) for calls in f.actual_target_calls] == before
            finally:
                release.set()
                outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            completed = await f.jobs.get_job(job.id)
            assert completed.status.value == "completed"
            assert completed.metadata["case_action_admission"] == claimed.metadata["case_action_admission"]
            assert [len(calls) for calls in f.actual_target_calls] == [before[0],before[1]+1]
            save_decision_evidence(f, "action-admission-concurrent", fixed_plan=plan.model_dump(mode="json"),
                claim=claimed.model_dump(mode="json"), actual_job=completed.model_dump(mode="json"),
                later_review=review.json(), actual_HTTP_before=before,
                actual_HTTP_after=[len(calls) for calls in f.actual_target_calls],
                claimants=4, business_sends=1, claim_is_linearization_boundary=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_governance_pending_job_denial_and_replay_use_same_receipt(tmp_path, retention_http):
    from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
    from src.application.runtime.tool_governance_service import GovernedToolCallContext, ToolGovernanceService
    from src.schemas.session import SessionMode, RuntimeMode, MessageKind
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            registry = ToolRegistry()
            governance = ToolGovernanceService(tool_registry=registry, permission_service=PermissionService(),
                tool_runtime_service=runtime, tool_job_service=f.jobs)
            context = GovernedToolCallContext(execution=execution,
                policy=PermissionPolicyContext(session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
                    selected_agent_key="coordinator", message_kind=MessageKind.user_input,
                    submit_mode="immediate", execution_lane="conversation_turn"),
                active_mode_key="api_testing", available_tool_keys=frozenset({"api-test-runner"}))
            request = call()
            before = [len(calls) for calls in f.actual_target_calls]
            pending = await governance.execute(request, context, prepare_approval=True)
            assert pending.record.status == "waiting_approval"
            reply = await f.decision_client.post(f.decision_routes[1], json=selected_review(f))
            assert reply.status_code == 201, reply.text
            approval = {**pending.approval, "status":"approved"}
            denied = await governance.execute(request, context, approval=approval)
            saved = await f.jobs.get_job(denied.record.job_id)
            repeated = await governance.execute(request, context, approval=approval)
            assert denied.record.status == repeated.record.status == "denied" and repeated.replayed
            assert await f.jobs.get_job(saved.id) == saved
            assert saved.started_at is None and [len(calls) for calls in f.actual_target_calls] == before
            save_decision_evidence(f, "action-admission-governance", fixed_plan=plan.model_dump(mode="json"),
                approval=approval, actual_job=saved.model_dump(mode="json"), actual_HTTP_before=before,
                actual_HTTP_after=before, original_terminal_replayed=True,
                full_deep_agent_model_harness_verified=False)


@pytest.mark.asyncio
@LIVE
async def test_actual_edited_context_parameters_and_expired_lease_preserve_pending_job(tmp_path, retention_http):
    from copy import deepcopy
    from src.infrastructure.postgres_runtime import postgres_connect
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            request = call()
            execution = execution.bind_call(request)
            tool = ToolRegistry().get("api-test-runner")
            job = await f.jobs.create_job(tool=tool, call_id=request.id, session_id=execution.session_id,
                turn_id=execution.turn_id, trace_id=execution.trace_id, input_payload=request.arguments,
                metadata=execution.job_metadata(), once_per_call=True)
            execution = replace(execution, tool_job_id=job.id)
            before = [len(calls) for calls in f.actual_target_calls]
            edited = deepcopy(execution.context_bundle)
            edited["case_execution"]["objective"] = "改变原完整上下文"
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool, request, replace(execution, context_bundle=edited))
            edited_request = request.model_copy(deep=True)
            edited_request.arguments["task"]["method"] = "POST"
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool, edited_request, execution)
            with pytest.raises(ValueError):
                await runtime.execute(tool, request, replace(execution,
                    context_bundle={**execution.context_bundle,"project_id":"foreign"}))
            with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
                # Admission compares the database clock. Build and verify its
                # expired input on that same clock, independent of host skew.
                cur.execute("SELECT clock_timestamp()-INTERVAL '1 second' AS expired")
                expired = cur.fetchone()["expired"]
                f.runs._write_item(cur, entry.model_copy(update={"lease_expires_at": expired}))
                cur.execute(f"SELECT lease_expires_at <= clock_timestamp() AS expired FROM {f.runs._item_table} WHERE id=%s",
                    (entry.id,))
                assert cur.fetchone()["expired"]
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool, request, execution)
            assert await f.jobs.get_job(job.id) == job
            assert [len(calls) for calls in f.actual_target_calls] == before
            save_decision_evidence(f, "action-admission-identity", fixed_plan=plan.model_dump(mode="json"),
                original_job=job.model_dump(mode="json"), actual_HTTP_before=before, actual_HTTP_after=before,
                original_arguments_and_context_and_lease_checked=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_confirmation_commit_lock_precedes_pending_job_claim(tmp_path, retention_http, monkeypatch):
    from threading import Event
    from src.infrastructure.postgres_runtime import postgres_connect
    async with decisions_environment(tmp_path, retention_http) as f:
        async with active_actions(f) as (plan, entry, attempt, execution, runtime, call):
            locked, release = asyncio.Event(), Event()
            loop = asyncio.get_running_loop()
            env = f.decision_store._environments
            original = env._project
            def hold(cur, project_id):
                result = original(cur, project_id)
                if not locked.is_set():
                    loop.call_soon_threadsafe(locked.set)
                    if not release.wait(10):
                        raise TimeoutError("actual confirmation project lock not released")
                return result
            before = [len(calls) for calls in f.actual_target_calls]
            with monkeypatch.context() as patch:
                patch.setattr(env, "_project", hold)
                review_task = asyncio.create_task(f.decision_client.post(f.decision_routes[1], json=selected_review(f)))
                claim_task = None
                try:
                    await asyncio.wait_for(locked.wait(), 5)
                    claim_task = asyncio.create_task(runtime.execute(ToolRegistry().get("api-test-runner"), call(), execution))
                    for _ in range(50):
                        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
                            cur.execute("SELECT pid,wait_event_type FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s",
                                ("%"+f.env.settings.database.postgres_project_table+"%",))
                            blocked = [dict(row) for row in cur.fetchall()]
                        if blocked:
                            break
                        await asyncio.sleep(0.02)
                    assert blocked and not claim_task.done()
                    assert [len(calls) for calls in f.actual_target_calls] == before
                finally:
                    release.set()
                    reply = await review_task
                    record = await claim_task if claim_task else None
            assert reply.status_code == 201 and record.status == "denied"
            job = await f.jobs.get_job(record.job_id)
            assert job.metadata["case_action_admission"]["restricted_review_ids"] == [reply.json()["id"]]
            assert [len(calls) for calls in f.actual_target_calls] == before
            save_decision_evidence(f, "action-admission-confirmation-lock", fixed_plan=plan.model_dump(mode="json"),
                actual_job=job.model_dump(mode="json"), current_review=reply.json(), PG_lock_wait=blocked,
                actual_HTTP_before=before, actual_HTTP_after=before, actual_confirmation_transaction_precedes_claim=True)
