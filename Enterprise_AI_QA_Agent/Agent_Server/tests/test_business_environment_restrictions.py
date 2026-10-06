"""Real documents/decisions -> atomic unexecuted receipts, preserving independent work."""
import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from test_business_applicability import decisions_environment, decision_request, save_decision_evidence, requirement_payload
from test_business_planning import selection
from test_case_planning import SequenceModel, active_case, finish, item
from test_requirement_execution import retention_http
from test_requirement_reviews import LIVE
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.documents.requirement_execution_checks import fixed_result
from src.runtime.postgres_business_planning import PostgresBusinessPlanning
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.application.test_runs.run_service import TestRunService as RunService
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.runtime.tool_runtime_service import ToolRuntimeService, ToolExecutionContext
from src.registry.tools import ToolRegistry
from src.schemas.run_management import RunItemExecuteRequest
from src.schemas.tool_runtime import ModelToolCall
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.test_runs.environment_restrictions import restriction_reviews
from src.schemas.case_planning import CasePlanRecord
from src.schemas.run_management import TestRunItemRecord as RunItemRecord
from src.runtime.postgres_business_applicability_store import fixed_applicability


async def prepared(f, index=1):
    target = PostgresBusinessPlanning(applicability=f.decision_store)
    f.env.service._environments = f.runs._environments = target
    old = f.values[4]
    independent = await active_case(f.env, f.project, "independent-target", {
        "objective": "独立读取相同目标的规则", "steps": [{"action": "读取规则"}],
        "expected": {"code": 200, "days": 9, "expired": False, "expired_count": 0, "deleted_at": None}})
    def plan(context):
        return finish(item(context, old.case.case_key, key="old", kind="dependency"),
            item(context, f.selected.case.case_key, key="dependent", depends_on=["old"]),
            item(context, independent.case.case_key, key="independent"))
    f.env.service._models = SequenceModel(plan)
    frozen = await f.env.service.create(f.project.id, CasePlanningRequest(
        objective="固定目标必要前置与独立目标", environment=selection(f,index),
        candidate_case_ids=[old.case.id, f.selected.case.id, independent.case.id]))
    assert frozen.status == "planned", frozen.summary
    creator = CasePlanRunService(run_store=f.runs, suite_store=f.suites, plan_store=f.env.store,
        case_service=f.env.cases, project_service=f.env.projects)
    return frozen, creator


def runs_service(f):
    return RunService(store=f.runs, project_service=f.env.projects, suite_service=None,
        test_case_service=f.env.cases, tool_job_service=f.jobs)


async def execute_independent(f, claim):
    entry, attempt = claim
    started = await f.runs.start_item(entry.id, attempt.lease_token, datetime.now(timezone.utc))
    context = build_agent_case_context(await f.runs.get_run(entry.run_id), started,
        await f.env.cases.get_version(entry.case_version_id))
    session, turn = str(uuid4()), str(uuid4())
    tc = ToolExecutionContext(session_id=session, turn_id=turn, trace_id=turn,
        user_message=context.objective, normalized_input=context.objective,
        context_bundle={**context.runtime_context(), "attempt_id": attempt.id})
    runtime = ToolRuntimeService(tool_job_service=f.jobs)
    record = await runtime.execute(ToolRegistry().get("api-test-runner"), ModelToolCall(
        id="independent-rule", name="api-test-runner", arguments={"worker_action": "execute_task",
            "task": {"task_id": "independent-rule", "method": "GET",
                "path": context.business_environment.snapshot.configuration.payload.base_url+"/rule"}}), tc)
    checks = [{"name": key, "operator": "equals", "expected_path_tokens": ["expected",key],
        "observations": [{"call_id": record.call_id, "output_path_tokens": ["task_result","response_status"]
            if key == "code" else ["task_result","response_body",key]}]} for key in
        ["code","days","expired_count","expired","deleted_at"]]
    outcome = await AgentCaseAssessmentService(f.jobs).assess(context=context, receipt={"schema_version":1,
        "steps":[{"item_key":"independent","status":"passed","summary":"独立目标实际九天响应","checks":checks}]},
        tool_records=[record], session_id=session, turn_id=turn, attempt_id=attempt.id,
        model_key="controlled-receipt", lease_token=attempt.lease_token)
    result = await runs_service(f).complete_item(entry.id,outcome.completion)
    assert result.status == "passed"
    return result, (await f.jobs.get_job(record.job_id)).model_dump(mode="json")


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_current_invalid_blocks_exact_case_and_dependency_at_creation(tmp_path, retention_http, extension):
    async with decisions_environment(tmp_path, retention_http, extension) as f:
        plan, creator = await prepared(f)
        before = [list(calls) for calls in f.actual_target_calls]
        invalid = await f.decision_client.post(f.decision_routes[1], json=decision_request(f, 1,
            key="invalid-after-planning", status="effective", case_status="invalid"))
        assert invalid.status_code == 201, invalid.text
        detail = await creator.create(f.project.id, plan.id,
            CasePlanRunRequest(creation_key="restrict-creation", mode_key="api_testing"))
        by_key = {entry.plan_item_key: entry for entry in detail.items}
        save_decision_evidence(f, "restriction-create-"+extension, fixed_plan=plan.model_dump(mode="json"),
            run=detail.model_dump(mode="json"), explicit_invalid=invalid.json(),
            restrictions_working=by_key["old"].status == "blocked", no_new_business_HTTP=f.actual_target_calls == before,
            full_environment_product_complete=False)
        # Fails on the actual C1 implementation: it does not load current decisions.
        assert by_key["old"].status == "blocked"
        assert by_key["dependent"].status == "blocked" and by_key["independent"].status == "queued"
        assert detail.run.stats.blocked == 2 and detail.run.stats.queued == 1
        result = fixed_result(next(r for r in detail.results if r.run_item_id == by_key["old"].id))
        assert result.actual["business_execution_started"] is False
        assert result.actual["environment_restriction"]["phase"] == "create"
        assert result.actual["environment_restriction"]["reviews"] == [invalid.json()]
        assert detail.run.plan_snapshot.plan == plan
        assert f.actual_target_calls == before
        claims = await f.runs.claim_items(run_id=detail.run.id,worker_id="independent",limit=1,
            lease_seconds=60,now=datetime.now(timezone.utc))
        assert [entry.plan_item_key for entry,_ in claims] == ["independent"]
        passed, job = await execute_independent(f,claims[0])
        final = await f.runs.get_run(detail.run.id)
        assert final.run.status == "completed" and len(final.results) == 3
        assert not await f.runs.claim_items(run_id=detail.run.id,worker_id="terminal",limit=1,
            lease_seconds=60,now=datetime.now(timezone.utc))
        save_decision_evidence(f,"restriction-independent-"+extension,fixed_plan=plan.model_dump(mode="json"),
            run=final.model_dump(mode="json"), independent_result=passed.model_dump(mode="json"),
            independent_job=job, blocked_new_HTTP=0, actual_execution_is_controlled=True)


def test_actual_captured_failure_selects_only_exact_version_and_keeps_false_zero_null():
    folder = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-environment-restrictions-reproduction-captured/applicability-restriction-create-md"
    state = json.loads((folder/"verification.json").read_bytes())
    plan = CasePlanRecord.model_validate(state["fixed_plan"])
    review = fixed_applicability(state["explicit_invalid"])
    items = [RunItemRecord.model_validate(row) for row in state["run"]["items"]]
    assert state["restrictions_working"] is False and all(row.status == "queued" for row in items)
    assert [row.plan_item_key for row in items if restriction_reviews(plan,row,[review])] == ["old"]
    assert len(state["full_case"]["version"]["content_json"]["steps"]) == 12
    expected = state["full_case"]["version"]["content_json"]["expected"]
    assert expected["expired"] is False and expected["expired_count"] == 0 and expected["deleted_at"] is None


@pytest.mark.asyncio
@LIVE
async def test_confirmation_after_create_is_checked_before_parallel_claim_limit(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="before-review",mode_key="api_testing"))
        invalid = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,
            status="effective",case_status="invalid"))
        assert invalid.status_code == 201
        claims = await asyncio.gather(*[f.runs.claim_items(run_id=detail.run.id,worker_id="race-"+str(n),limit=1,
            lease_seconds=60,now=datetime.now(timezone.utc)) for n in range(4)])
        selected = [claim for group in claims for claim in group]
        assert [entry.plan_item_key for entry,_ in selected] == ["independent"]
        result,job = await execute_independent(f,selected[0])
        saved = await f.runs.get_run(detail.run.id)
        assert saved.run.status == "completed" and len(saved.results) == 3
        blocked = next(r for r in saved.results if r.actual.get("environment_restriction"))
        assert blocked.actual["environment_restriction"]["phase"] == "claim"
        save_decision_evidence(f,"restriction-claim",run=saved.model_dump(mode="json"),independent_job=job,
            fixed_plan=plan.model_dump(mode="json"),claimed_count=len(selected),explicit_invalid=invalid.json())


@pytest.mark.asyncio
@LIVE
async def test_confirmation_after_claim_stops_execute_and_recovers_actual_lost_reply(tmp_path,retention_http,monkeypatch):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="claimed",mode_key="api_testing"))
        claim = (await f.runs.claim_items(run_id=detail.run.id,worker_id="claimed",limit=1,
            lease_seconds=60,now=datetime.now(timezone.utc)))[0]
        invalid = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,
            status="effective",case_status="invalid"))
        assert invalid.status_code == 201
        before = [list(calls) for calls in f.actual_target_calls]
        class NoBusiness:
            def __getattr__(self,key): raise AssertionError("blocked item must not read cases or invoke a runner")
        service = ExecutionService(run_service=runs_service(f),test_case_service=NoBusiness(),adapter=NoBusiness())
        command = RunItemExecuteRequest(lease_token=claim[1].lease_token)
        original = f.runs._start_item_sync
        def lost(*args): original(*args); raise ConnectionError("actual committed blocked reply lost")
        with monkeypatch.context() as patch:
            patch.setattr(f.runs,"_start_item_sync",lost)
            with pytest.raises(ConnectionError): await service.execute_item(claim[0].id,command)
        result = await service.execute_item(claim[0].id,command)
        assert result.status == "blocked" and result.attempt_id == claim[1].id
        assert result.actual["environment_restriction"]["phase"] == "start"
        assert (await service.execute_item(claim[0].id,command)) == result
        with pytest.raises(ValueError):
            await service.execute_item(claim[0].id,RunItemExecuteRequest(lease_token=str(uuid4())))
        assert f.actual_target_calls == before
        saved = await f.runs.get_run(detail.run.id)
        assert len(saved.results) == 2 and len(saved.attempts) == 2
        assert saved.run.stats.blocked == 2 and saved.run.stats.queued == 1
        save_decision_evidence(f,"restriction-start",run=saved.model_dump(mode="json"),explicit_invalid=invalid.json(),
            fixed_plan=plan.model_dump(mode="json"),actual_committed_reply_lost=True,blocked_new_HTTP=0)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("condition",["revoke","unknown","other-target"])
async def test_unknown_revoked_or_other_target_does_not_block_new_items(tmp_path,retention_http,condition):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f,0 if condition == "other-target" else 1)
        request = decision_request(f,1,status="effective",case_status="invalid")
        reply = await f.decision_client.post(f.decision_routes[1],json=request)
        assert reply.status_code == 201
        if condition != "other-target":
            request.update(request_id="changed",expected_review_id=reply.json()["id"])
            if condition == "revoke": request.update(action="revoke",points=[],cases=[],observations=[])
            else:
                request["points"][0]["status"] = "unknown"
                request["cases"][0]["status"] = "unknown"
            reply = await f.decision_client.post(f.decision_routes[1],json=request)
            assert reply.status_code == 201,reply.text
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key=condition,mode_key="api_testing"))
        assert not detail.results and all(item.status == "queued" for item in detail.items)
        claim = (await f.runs.claim_items(run_id=detail.run.id,worker_id="allowed",limit=1,
            lease_seconds=60,now=datetime.now(timezone.utc)))[0]
        started = await f.runs.start_item(claim[0].id,claim[1].lease_token,datetime.now(timezone.utc))
        assert started.status == "running" and started.plan_item_key == "old"
        save_decision_evidence(f,"restriction-"+condition,run=(await f.runs.get_run(detail.run.id)).model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),decision=reply.json(),unknown_not_hard_blocked=True)


@pytest.mark.asyncio
@LIVE
async def test_started_checkpoint_survives_invalid_confirmation_without_new_http(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="started",mode_key="api_testing"))
        now = datetime.now(timezone.utc)
        entry,attempt = (await f.runs.claim_items(run_id=detail.run.id,worker_id="started",limit=1,
            lease_seconds=60,now=now))[0]
        await f.runs.start_item(entry.id,attempt.lease_token,now)
        tc = ToolExecutionContext(session_id=str(uuid4()),turn_id=str(uuid4()),trace_id=str(uuid4()),
            user_message="保存已发送请求的原检查点",normalized_input="保存已发送请求的原检查点",
            context_bundle={"project_id":f.project.id,"attempt_id":attempt.id})
        sent = await ToolRuntimeService(tool_job_service=f.jobs).execute(ToolRegistry().get("api-test-runner"),
            ModelToolCall(id="started-post",name="api-test-runner",arguments={"worker_action":"execute_task",
                "task":{"task_id":"started-post","method":"POST",
                    "path":plan.environment.snapshot.configuration.payload.base_url+"/rule"}}),tc)
        original_job = await f.jobs.get_job(sent.job_id)
        assert original_job.output_payload["task_result"]["response_status"] == 200
        checkpoint = {"tool_job_id":sent.job_id,"business_effect_status":"unknown",
            "HTTP_received":True,"business_state_change_verified":False}
        await f.runs.save_checkpoint(entry.id,attempt.lease_token,"original-request",checkpoint,1,now)
        later = now+timedelta(seconds=91)
        assert await f.runs.recover_expired(detail.run.id,later) == 1
        expired = await f.runs.get_run(detail.run.id)
        original_attempt = next(row for row in expired.attempts if row.id == attempt.id)
        assert original_attempt.started_at is not None and original_attempt.checkpoint_payload == checkpoint
        invalid = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,
            status="effective",case_status="invalid"))
        assert invalid.status_code == 201,invalid.text
        before = [list(calls) for calls in f.actual_target_calls]
        resumed,resumed_attempt = (await f.runs.claim_items(run_id=detail.run.id,worker_id="recover",limit=1,
            lease_seconds=60,now=later))[0]
        assert resumed.id == entry.id and resumed_attempt.checkpoint_payload == checkpoint
        started = await f.runs.start_item(resumed.id,resumed_attempt.lease_token,later)
        final = await f.runs.get_run(detail.run.id)
        assert started.status == "running" and not final.results and f.actual_target_calls == before
        assert next(row for row in final.attempts if row.id == attempt.id) == original_attempt
        save_decision_evidence(f,"restriction-started-recovery",run=final.model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),explicit_invalid=invalid.json(),original_checkpoint=checkpoint,
            original_started_attempt=original_attempt.model_dump(mode="json"),original_job=original_job.model_dump(mode="json"),
            new_recovery_HTTP=0,business_state_change_verified=False,autonomous_continuation_invoked=False)


@pytest.mark.asyncio
@LIVE
async def test_project_lock_orders_real_claim_and_append_before_start(tmp_path,retention_http,monkeypatch):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="lock-race",mode_key="api_testing"))
        held,released = Event(),Event()
        original = f.runs._environments.current_reviews_in_cursor
        def hold(cur,fixed_plan):
            reviews = original(cur,fixed_plan)
            held.set()
            assert released.wait(15),"test must release actual PG project lock"
            return reviews
        def blocked_sessions():
            with postgres_connect(f.env.settings) as conn,conn.cursor() as cur:
                cur.execute("SELECT pid,state,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers "
                    "FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND datname=current_database() "
                    "AND wait_event_type='Lock' AND query LIKE %s",("%"+f.env.settings.database.postgres_project_table+"%",))
                return [dict(row) for row in cur.fetchall()]
        with monkeypatch.context() as patch:
            patch.setattr(f.runs._environments,"current_reviews_in_cursor",hold)
            claim_task = asyncio.create_task(f.runs.claim_items(run_id=detail.run.id,worker_id="lock-first",limit=1,
                lease_seconds=60,now=datetime.now(timezone.utc)))
            review_task = None
            try:
                assert await asyncio.to_thread(held.wait,5)
                review_task = asyncio.create_task(f.decision_client.post(f.decision_routes[1],
                    json=decision_request(f,1,status="effective",case_status="invalid")))
                waits = []
                for _ in range(100):
                    waits = await asyncio.to_thread(blocked_sessions)
                    if waits: break
                    await asyncio.sleep(.05)
                assert waits and all(row["blockers"] for row in waits),"actual append must wait on project lock"
                assert not review_task.done()
            finally:
                released.set()
                claims = await asyncio.wait_for(claim_task,15)
                invalid = await asyncio.wait_for(review_task,15) if review_task else None
        assert invalid.status_code == 201,invalid.text
        assert claims[0][0].plan_item_key == "old"
        started = await f.runs.start_item(claims[0][0].id,claims[0][1].lease_token,datetime.now(timezone.utc))
        assert started.status == "blocked"
        save_decision_evidence(f,"restriction-project-lock",run=(await f.runs.get_run(detail.run.id)).model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),explicit_invalid=invalid.json(),actual_lock_waits=waits,
            claim_precedes_append=True,start_sees_committed_review=True)


@pytest.mark.asyncio
@LIVE
async def test_conflicting_requirement_version_decisions_do_not_fabricate_invalid_consensus(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        invalid = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,
            status="effective",case_status="invalid"))
        assert invalid.status_code == 201,invalid.text
        route = f.base+f"/impact-snapshots/{f.values[7].id}/reviews"
        previous = (await f.client.get(route)).json()["items"][0]
        newer = await f.client.post(route,json=requirement_payload(f.values[8].rules[0].id,f.values[4].version.id,
            request_id="conflicting-version",expected_review_id=previous["id"],requirement_id=f.version["requirement_id"],
            expected_version_id=f.version["id"],statement="订单保留九天，明确环境适用复核"))
        assert newer.status_code == 201,newer.text
        version = newer.json()["requirement_version"]
        request = decision_request(f,1,key="valid-other-version",status="effective",case_status="valid")
        request.update(requirement_version_id=version["id"],expected_requirement_sha256=version["content_sha256"],
            expected_coverage_sha256=version["content_sha256"])
        valid = await f.decision_client.post(f.decision_routes[1],json=request)
        assert valid.status_code == 201,valid.text
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="conflict",mode_key="api_testing"))
        assert not detail.results and all(row.status == "queued" for row in detail.items)
        save_decision_evidence(f,"restriction-conflict",run=detail.model_dump(mode="json"),fixed_plan=plan.model_dump(mode="json"),
            explicit_invalid=invalid.json(),explicit_valid=valid.json(),new_requirement_version=version,
            conflicting_point_not_hard_blocked=True)
