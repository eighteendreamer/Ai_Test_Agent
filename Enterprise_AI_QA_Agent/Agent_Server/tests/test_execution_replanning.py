"""Actual execution tools, frozen evidence and durable unclaimed Run revision."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_agent_case_session import ControlledAgent, AgentRegistry
from test_agent_case_execution import approval_continuation
from test_business_environment_admission import agent_runtime
from test_case_plan_run_creation import planned, fixture_plan
from test_case_planning import SequenceModel, item
from test_case_planning_tasks import SubmissionModel, delivery
from src.application.capabilities.tool_exposure_policy import ToolExposurePolicy
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.execution_replanning import (
    ExecutionReplanningRequest, ExecutionReplanningService, TOOL_KEY,
)
from src.application.test_runs.revision_planning_service import RunRevisionPlanningService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.postgres_case_action_admission import PostgresCaseActionAdmission
from src.schemas.case_planning import CasePlanningPolicy, CasePlanningRequest, CasePlanRunRequest
from src.schemas.model_config import ModelInvocationResult
from src.schemas.run_management import RunItemApprovalWaitRequest, RunItemLeaseRequest
from src.schemas.tool_runtime import ModelToolCall

LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_EXECUTION_REPLANNING") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS; no container or external model")
POLICY = CasePlanningPolicy(action_binding_protocol="case-action-binding-v1",
    action_admission_protocol="case-action-admission-v1", operation_protocol="case-operation-v1")


@pytest.fixture
def gap_http():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            body = json.dumps({"required_case": "item-4", "scope": "left", "ready": False}).encode()
            self.send_response(409)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}/execution-gap", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def call(key, action, **values):
    return ModelToolCall(id=key, name=TOOL_KEY, arguments={"action": action,
        "_case_action": {"item_key": "independent", "operation_key": key}, **values})


class GapAgent(ControlledAgent):
    def __init__(self, url):
        super().__init__()
        self.url = url
    async def invoke(self, key, request):
        self.requests.append(request)
        visible = {entry["name"] for entry in request.tools}
        assert TOOL_KEY in visible
        assert "execution_replanning" in {entry.key for entry in request.system_prompt_sections}
        previous = [json.loads(entry["content"]) for entry in request.messages if entry.get("role") == "tool"]
        if not previous:
            next_call = ModelToolCall(id="actual-gap", name="api-test-runner", arguments={
                "_case_action": {"item_key": "independent", "operation_key": "gap.read"},
                "worker_action": "execute_task", "task": {"task_id": "read-gap", "method": "GET", "path": self.url}})
        elif len(previous) == 1:
            next_call = call("inspect", "inspect")
        elif len(previous) == 2:
            assert previous[0]["output"]["task_result"]["response_body"]["required_case"] == "item-4"
            next_call = call("replan", "submit", expected_revision=previous[1]["output"]["revision"],
                reason="实际只读响应指出left缺少item-4，保留当前已领取项并补齐未领取部分",
                evidence=[{"job_id": previous[0]["job_id"], "output_path_tokens": [
                    "task_result", "response_body", "required_case"]}])
        else:
            assert previous[-1]["output"]["run_revision"]["status"] == "pending"
            return ModelInvocationResult(text=json.dumps({"schema_version": 1, "steps": [{
                "item_key": "independent", "status": "blocked", "summary": "实际发现新缺口，已提交持久重规划；未证明目标通过",
                "checks": []}]}), response_summary={"mode": "ok", "finish_reason": "stop"})
        return ModelInvocationResult(text="根据实际证据推进原运行", tool_calls=[next_call],
            response_summary={"mode": "ok", "finish_reason": "tool_calls"})


@asynccontextmanager
async def execution_environment(tmp_path, gap_http, *, turn_execution_lease_seconds=15,
        document_text=None):
    async with planned(tmp_path, True) as (env, store, suites, creator, old, now, outbox):
        project_id = old.project_id
        await env.service.save_policy(project_id, POLICY)
        env.service._models = SequenceModel(fixture_plan)
        plan = await env.service.create(project_id, CasePlanningRequest(objective="执行中新缺口重规划"))
        detail = await creator.create(project_id, plan.id,
            CasePlanRunRequest(creation_key="execution-replanning-original", mode_key="api_testing"))
        # The real planner's prepare_task path requires the same typed model
        # configuration used by the existing durable planning-worker tests.
        env.service._models = SubmissionModel()
        source = tmp_path / "execution-replanning.md"
        default_text = ("# 执行补充依据\n实际响应required_case=item-4时，left需要先执行项目正式用例item-4。\n"
            "当前已领取项仍保持原步骤与版本；right无需新增准备。\n")
        source.write_text(document_text or default_text, encoding="utf-8")
        document = await env.docs.upload_document(project_id=project_id, filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        suffix = uuid4().hex[:12]
        env.settings.database.postgres_tool_job_table = "up_er_job_" + suffix
        env.settings.database.postgres_tool_artifact_table = "up_er_art_" + suffix
        env.settings.database.postgres_case_planning_task_table = "up_er_task_" + suffix
        tables = [env.settings.database.postgres_tool_job_table, env.settings.database.postgres_tool_artifact_table,
            env.settings.database.postgres_case_planning_task_table]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for name in tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (name,))
                assert cur.fetchone()["existing"] is None
        jobs = ToolJobService(PostgresToolJobStore(env.settings))
        jobs._case_action_admission = PostgresCaseActionAdmission(runs=store, jobs=jobs._store)
        await jobs.initialize()
        tables += [jobs._case_action_admission.operations.table,
            jobs._case_action_admission.consumption.table, jobs._store.effect_evidence.table]
        tasks = PostgresPlanningTaskStore(env.settings, lease_seconds=30)
        await tasks.initialize()
        revisions = RunRevisionPlanningService(tasks=tasks, planner=env.service, runs=store,
            projects=env.projects, revision_service=creator, dispatch_stream="isolated:execution-replanning")
        bridge = ExecutionReplanningService(planning=revisions, tasks=tasks, runs=store, jobs=jobs)
        f = SimpleNamespace(env=env, runs=store, jobs=jobs, tasks=tasks, revisions=revisions, bridge=bridge,
            plan=plan, detail=detail, document=document, creator=creator, suites=suites, target_calls=gap_http[1])
        try:
            async with agent_runtime(f, GapAgent(gap_http[0]), action_admission=True,
                turn_execution_lease_seconds=turn_execution_lease_seconds) as (runs, adapter, reader, clock):
                claims = await store.claim_items(run_id=detail.run.id, worker_id="execution-replanner",
                    limit=2, lease_seconds=600, now=clock[0])
                entry, attempt = next(row for row in claims if row[0].plan_item_key == "independent")
                await runs.start_item(entry.id, RunItemLeaseRequest(lease_token=attempt.lease_token))
                f.entry, f.attempt = await runs.get_item(entry.id), attempt
                f.adapter, f.run_service = adapter, runs
                f.runtime = adapter._sessions._runtime_service._tool_runtime_service
                f.runtime._execution_replanning = bridge
                f.version = await env.cases.get_version(entry.case_version_id)
                context, checkpoint, attempt = await adapter._load(run=await runs.get_record(detail.run.id),
                    item=f.entry, version=f.version, attempt_id=f.attempt.id)
                f.execution = ToolExecutionContext(session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
                    trace_id="execution-replanning", user_message=context.objective, normalized_input=context.objective,
                    context_bundle={**context.runtime_context(), "case_execution_managed": True,
                        "attempt_id": checkpoint.execution_attempt_id})
                f.agent = adapter._sessions._runtime_service._model_runtime_service
                async def args():
                    current_item = await runs.get_item(f.entry.id)
                    current_attempt = await runs.get_latest_attempt(f.entry.id)
                    current_run = await runs.get_record(detail.run.id)
                    return {"case": await env.cases.get_case(current_item.case_id),
                        "version": await env.cases.get_version(current_item.case_version_id),
                        "item": current_item, "run": current_run,
                        "attempt_id": current_attempt.id}
                f.args = args
                yield f
        finally:
            output = os.getenv("QA_EXECUTION_REPLANNING_DIR")
            if output:
                from pathlib import Path
                path = Path(output)
                path.mkdir(parents=True, exist_ok=True)
                (path / (tmp_path.name + "-allocations.json")).write_text(json.dumps({"tables": tables,
                    "project_id": project_id, "document_uri": document.storage_uri}), encoding="utf-8")
            try:
                await env.docs._artifact_storage_service.delete_object_uri(document.storage_uri)
            finally:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    for name in reversed(tables): cur.execute(f'DROP TABLE "{name}"')


async def gap_evidence(f, url):
    return await f.runtime.execute(ToolRegistry().get("api-test-runner"), ModelToolCall(id="observed", name="api-test-runner",
        arguments={"_case_action": {"item_key": "independent", "operation_key": "gap.read"},
            "worker_action": "execute_task", "task": {"task_id": "gap-read", "method": "GET", "path": url}}), f.execution)


def candidate_replies(f):
    def decide(context):
        trigger = context["constraints"]["run_revision"]["execution_trigger"]
        assert trigger["evidence"][0]["output"] == "item-4"
        assert trigger["evidence"][0]["output_path_tokens"] == [
            "task_result", "response_body", "required_case"]
        assert "response_body" not in trigger["evidence"][0]["output"]
        assert trigger["binding"]["run_item_id"] == f.entry.id
        result = fixture_plan(context)
        result["items"].append(item(context, "item-4", key="extra", kind="dependency"))
        next(node for node in result["items"] if node["key"] == "left")["depends_on"].append("extra")
        return result
    return [{"action": "query", "summary": "读取补充文档与正式前置用例", "queries": [
        {"kind": "document", "source_id": f.document.id}, {"kind": "cases", "query": "item-4"}]}, decide]


def save_evidence(tmp_path, name, data):
    output = os.getenv("QA_EXECUTION_REPLANNING_DIR")
    if output:
        from pathlib import Path
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        (path / (tmp_path.name + "-" + name + ".json")).write_text(
            json.dumps(data, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


@pytest.mark.asyncio
@LIVE
async def test_execution_graph_discovers_gap_submits_and_applies_to_unclaimed_items(tmp_path, gap_http):
    async with execution_environment(tmp_path, gap_http) as f:
        before = await f.runs.get_run(f.detail.run.id)
        outcome = await f.adapter.execute(case=await f.env.cases.get_case(f.entry.case_id), version=f.version,
            run=before.run, item=f.entry, attempt_id=f.attempt.id)
        while hasattr(outcome, "approval_id"):
            from src.schemas.session import ApprovalDecisionRequest, ToolApprovalStatus
            current = await f.args()
            approval = next(a for a in await f.adapter._store.list_approvals(outcome.session_id)
                if a.id == outcome.approval_id)
            await f.run_service.mark_waiting_approval(current["item"].id,
                RunItemApprovalWaitRequest(
                    lease_token=current["item"].lease_token, approval_id=outcome.approval_id,
                    tool_job_id=outcome.tool_job_id, reason=outcome.summary,
                    approval_scope_hash=approval.metadata["approval_scope_hash"]))
            resumed = await f.run_service.resume_waiting_approval(current["item"].id, outcome.approval_id)
            await f.run_service.start_item(resumed.id, RunItemLeaseRequest(lease_token=resumed.lease_token))
            current = await f.args()
            outcome = await f.adapter.resume_approval(**current, approval_id=outcome.approval_id,
                payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
        assert outcome.completion.status == "blocked"
        jobs = await f.jobs.list_jobs(f.execution.session_id)
        submit_job = next(job for job in jobs if job.tool_key == TOOL_KEY and job.input_payload["action"] == "submit")
        assert submit_job.status.value == "completed", submit_job.output_payload
        task_id = submit_job.output_payload["task_id"]
        task = await f.tasks.get(f.plan.project_id, task_id)
        assert task.status == "queued" and task.run_revision.status == "pending"
        assert gap_http[1] == ["/execution-gap"]
        unchanged = await f.runs.get_run(f.detail.run.id)
        assert unchanged.run.plan_revision == 0
        f.env.service._models = SubmissionModel()
        f.env.service._models.replies = candidate_replies(f)
        handler = PlanningTaskHandler(f.env.service, f.tasks, worker_id="execution-replanning-worker", revision_finalizer=f.revisions)
        await handler(delivery(task))
        saved = await f.tasks.get(f.plan.project_id, task.id)
        assert saved.run_revision.status == "applied", saved
        current = await f.runs.get_run(f.detail.run.id)
        assert current.run.plan_revision == 1 and len(current.items) == len(before.items) + 1
        assert current.attempts == unchanged.attempts and current.results == unchanged.results
        old = await f.runs.get_execution_detail(current.run.id, f.entry.id, f.attempt.id)
        assert old.run.plan_snapshot == before.run.plan_snapshot
        left = next(entry for entry in current.items if entry.plan_item_key == "left")
        extra = next(entry for entry in current.items if entry.plan_item_key == "extra")
        assert extra.id in {dependency.item_id for dependency in left.dependencies}
        status = await f.runtime.execute(ToolRegistry().get(TOOL_KEY),
            call("status-after-apply", "status", submission_operation_key="replan"), f.execution)
        assert status.output["run_revision"]["status"] == "applied"
        await handler(delivery(task))
        assert len(f.env.service._models.requests) == 2
        assert len(await f.creator.list_revisions(f.plan.project_id, current.run.id)) == 1
        save_evidence(tmp_path, "graph", {"task": saved.model_dump(mode="json"),
            "tool_jobs": [job.model_dump(mode="json") for job in jobs], "model_calls": len(f.agent.requests),
            "actual_http": gap_http[1], "status": status.model_dump(mode="json"),
            "attempts_preserved": current.attempts == unchanged.attempts,
            "current_outcome": outcome.completion.model_dump(mode="json")})


@pytest.mark.asyncio
@LIVE
async def test_lost_submit_return_is_found_by_original_operation_without_repeating(tmp_path, gap_http, monkeypatch):
    class Interrupted(BaseException): pass
    async with execution_environment(tmp_path, gap_http) as f:
        evidence = await gap_evidence(f, gap_http[0])
        submit = call("original-submit", "submit", expected_revision=0, reason="实际响应缺少必要前置",
            evidence=[{"job_id": evidence.job_id, "output_path_tokens": [
                "task_result", "response_body", "required_case"]}])
        original_submit = f.revisions.submit
        async def lost_return(*args, **kwargs):
            await original_submit(*args, **kwargs)
            raise Interrupted("committed durable submission, return lost")
        monkeypatch.setattr(f.revisions, "submit", lost_return)
        with pytest.raises(Interrupted):
            await f.runtime.execute(ToolRegistry().get(TOOL_KEY), submit, f.execution)
        origin = next(job for job in await f.jobs.list_jobs(f.execution.session_id) if job.call_id == submit.id)
        assert origin.status.value == "running"
        replay = await f.runtime.execute(ToolRegistry().get(TOOL_KEY), submit.model_copy(update={"id": "new-provider-id"}), f.execution)
        assert replay.output["error"] == "case_operation_outcome_unknown"
        status = await f.runtime.execute(ToolRegistry().get(TOOL_KEY),
            call("find-original", "status", submission_operation_key="original-submit"), f.execution)
        assert status.output["submission_found"] is True and status.output["run_revision"]["status"] == "pending"
        assert (await f.tasks.list(f.plan.project_id, limit=20, offset=0)).items.__len__() == 1
        assert await f.jobs.get_job(origin.id) == origin
        assert gap_http[1] == ["/execution-gap"]
        save_evidence(tmp_path, "lost-return", {"original": origin.model_dump(mode="json"),
            "replay": replay.model_dump(mode="json"), "status": status.model_dump(mode="json"),
            "actual_http": gap_http[1]})


@pytest.mark.asyncio
@LIVE
async def test_foreign_evidence_and_stale_revision_cannot_create_planning_task(tmp_path, gap_http):
    async with execution_environment(tmp_path, gap_http) as f:
        evidence = await gap_evidence(f, gap_http[0])
        source = await f.jobs.get_job(evidence.job_id)
        foreign = source.model_copy(deep=True, update={"id": str(uuid4()), "turn_id": "different-turn"})
        await f.jobs._store.save_job(foreign)
        records = []
        references = lambda job_id, path=("task_result", "response_body", "required_case"): [
            {"job_id": job_id, "output_path_tokens": list(path)}]
        for key, revision, evidence_refs in [
            ("foreign", 0, references(foreign.id)),
            ("stale", 1, references(source.id)),
            ("missing", 0, references(str(uuid4()))),
            ("missing-path", 0, references(source.id, ("task_result", "response_body", "absent"))),
        ]:
            record = await f.runtime.execute(ToolRegistry().get(TOOL_KEY), call(key, "submit",
                expected_revision=revision, reason="具体执行缺口", evidence=evidence_refs), f.execution)
            assert record.status == "failed" and record.output["error"] == "execution_replanning_rejected"
            records.append(record.model_dump(mode="json"))
        assert not (await f.tasks.list(f.plan.project_id, limit=20, offset=0)).items
        assert (await f.runs.get_run(f.detail.run.id)).run.plan_revision == 0
        save_evidence(tmp_path, "rejections", {"records": records, "actual_http": gap_http[1]})


def test_execution_replanning_evidence_is_bounded_by_serialized_selected_values():
    from src.application.test_runs.execution_replanning import (
        MAX_EXECUTION_TRIGGER_EVIDENCE_BYTES, ExecutionReplanningService,
    )
    assert ExecutionReplanningService._bounded_evidence_bytes([{"output": "ok"}]) < 32_768
    with pytest.raises(ValueError, match="超过总大小限制"):
        ExecutionReplanningService._bounded_evidence_bytes([
            {"output": "x" * MAX_EXECUTION_TRIGGER_EVIDENCE_BYTES}])


def test_only_managed_operation_context_exposes_platform_tool_and_validates_action():
    tool, policy, agent = ToolRegistry().get(TOOL_KEY), ToolExposurePolicy(), AgentRegistry().get("coordinator")
    managed = {"case_execution_managed": True, "case_execution": {
        "action_admission_protocol": "case-action-admission-v1", "operation_protocol": "case-operation-v1"}}
    for bundle in ({}, {"case_execution": managed["case_execution"]}, {"case_execution_managed": True}):
        assert not policy.filter_supported(tools=[tool], agent=agent, context_bundle=bundle)
    assert policy.filter_supported(tools=[tool], agent=agent, context_bundle=managed) == [tool]
    for payload in ({"action": "submit", "reason": "gap"}, {"action": "status"},
        {"action": "inspect", "reason": "extra"}, {"action": "submit", "expected_revision": 0,
        "reason": " ", "evidence": [{"job_id": "job", "output_path_tokens": ["a"]}]},
        {"action": "submit", "expected_revision": 0, "reason": "gap",
            "evidence": [{"job_id": "job", "output_path_tokens": []}]},
        {"action": "status", "submission_operation_key": "x", "project_id": "forged"}):
        with pytest.raises(ValueError): ExecutionReplanningRequest.model_validate(payload)
