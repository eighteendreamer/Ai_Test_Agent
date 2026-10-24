"""Real default model discovers an execution gap, cites evidence and submits a durable revision."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import test_execution_replanning as replanning_module
from src.application.models.model_runtime_service import ModelRuntimeService
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import model_invocation_parameters
from test_agent_case_session import ControlledAgent
from test_case_planning_tasks import delivery
from test_execution_replanning import (
    TOOL_KEY, candidate_replies, execution_environment, gap_http,
)
from test_model_completion_http import Registry

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_EXECUTION_REPLANNING") != "1",
    reason="real default model execution agent over isolated actual stores")]


class RealModelGapAgent(ControlledAgent):
    """The controlled planning models stay untouched; the execution agent is
    the real default model routed through the actual runtime services."""

    def __init__(self, url, config, runtime):
        super().__init__()
        self.url = url
        self.config = config
        self.runtime = runtime
        self.invocations = []

    def get_model_config(self, key):
        return self.config

    def get_invocation_runtime_parameters(self, key, **kwargs):
        return self.runtime.get_invocation_runtime_parameters(key, **kwargs)

    def stream_handler(self, handler):
        return self.runtime.stream_handler(handler)

    async def invoke(self, key, request, **kwargs):
        self.invocations.append(request)
        return await self.runtime.invoke(key, request, **kwargs)


async def test_default_model_gap_read_verify_and_durable_revision(tmp_path, gap_http, monkeypatch):
    settings = get_settings().model_copy(deep=True)
    configured_store = MySQLModelConfigStore(settings)
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    assert configured.transport == "openai_chat_completions"
    # The planned session's preferred model key is the controlled alias; serve
    # the real default config under that key so model identity checks hold
    # while every parameter, credential and endpoint stays the real default's.
    alias_config = configured.model_copy(update={"key": "controlled-case-agent"})
    real_runtime = ModelRuntimeService(Registry(alias_config), settings)

    class Factory(ControlledAgent):
        last = None

        def __new__(cls, url):
            instance = RealModelGapAgent(url, alias_config, real_runtime)
            Factory.last = instance
            return instance

    # The execution environment resolves GapAgent from its module namespace:
    # inject the real-model agent before the environment context is entered.
    monkeypatch.setattr(replanning_module, "GapAgent", Factory)

    evidence = {}
    facts = {"model_id": configured.model_id, "transport": configured.transport,
             "configured_parameters": configured_parameters, "controlled_model": False,
             "planning_model": "controlled (fixture_plan + candidate_replies)",
             "execution_subject": "real default model gap discovery, read-only verification and revision submission"}
    try:
        # The real model's rounds take far longer than the scripted agent's
        # 15s default turn lease; extend it for this live acceptance. The
        # document spells the full gap URL — a real project document names
        # concrete endpoints, and the scripted agent's relative paths plus
        # an injected base URL are fixture shortcuts the model cannot see.
        manual_document = (
            "# 执行补充依据\n"
            "实际响应required_case=item-4时，left需要先执行项目正式用例item-4。\n"
            "当前已领取项仍保持原步骤与版本；right无需新增准备。\n"
            f"缺口只读验证：GET {gap_http[0]} 返回的 required_case 字段即为缺口证据。\n")
        async with execution_environment(tmp_path, gap_http,
                turn_execution_lease_seconds=300, document_text=manual_document) as f:
            try:
                before = await f.runs.get_run(f.detail.run.id)
                outcome = await f.adapter.execute(case=await f.env.cases.get_case(f.entry.case_id),
                    version=f.version, run=before.run, item=f.entry, attempt_id=f.attempt.id)
                while hasattr(outcome, "approval_id"):
                    from src.schemas.session import ApprovalDecisionRequest, ToolApprovalStatus
                    current = await f.args()
                    approval = next(a for a in await f.adapter._store.list_approvals(outcome.session_id)
                        if a.id == outcome.approval_id)
                    await f.run_service.mark_waiting_approval(current["item"].id,
                        replanning_module.RunItemApprovalWaitRequest(
                            lease_token=current["item"].lease_token, approval_id=outcome.approval_id,
                            tool_job_id=outcome.tool_job_id, reason=outcome.summary,
                            approval_scope_hash=approval.metadata["approval_scope_hash"]))
                    resumed = await f.run_service.resume_waiting_approval(current["item"].id, outcome.approval_id)
                    await f.run_service.start_item(resumed.id,
                        replanning_module.RunItemLeaseRequest(lease_token=resumed.lease_token))
                    current = await f.args()
                    outcome = await f.adapter.resume_approval(**current, approval_id=outcome.approval_id,
                        payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
                assert outcome.completion.status == "blocked", outcome.completion
                jobs = await f.jobs.list_jobs(f.execution.session_id)
                submit_job = next(job for job in jobs
                    if job.tool_key == TOOL_KEY and job.input_payload["action"] == "submit")
                assert submit_job.status.value == "completed", submit_job.output_payload
                evidence["submit_job"] = {"id": str(submit_job.id), "input": submit_job.input_payload}
                task_id = submit_job.output_payload["task_id"]
                task = await f.tasks.get(f.plan.project_id, task_id)
                assert task.status == "queued" and task.run_revision.status == "pending", task
                unchanged = await f.runs.get_run(f.detail.run.id)
                assert unchanged.run.plan_revision == 0
                facts["gap_http_calls"] = list(gap_http[1])
                facts["model_invocations"] = len(Factory.last.invocations)
                facts["revision_task"] = {"id": str(task.id), "status": task.status,
                    "revision_status": task.run_revision.status}
                assert any(path == "/execution-gap" for path in gap_http[1]), gap_http[1]

                # Re-plan the revision with the deterministic candidate replies and
                # assert the durable revision applies to unclaimed items only.
                f.env.service._models = replanning_module.SubmissionModel()
                f.env.service._models.replies = candidate_replies(f)
                handler = replanning_module.PlanningTaskHandler(f.env.service, f.tasks,
                    worker_id="default-replanning-worker", revision_finalizer=f.revisions)
                await handler(delivery(task))
                saved = await f.tasks.get(f.plan.project_id, task.id)
                assert saved.run_revision.status == "applied", saved
                current = await f.runs.get_run(f.detail.run.id)
                assert current.run.plan_revision == 1 and len(current.items) == len(before.items) + 1
                assert current.attempts == unchanged.attempts and current.results == unchanged.results
            except Exception:
                # Forensics: persist the real model's actual requests and executed
                # tool jobs so a failed choreography is attributable after the fact.
                try:
                    agent = Factory.last
                    if agent is not None:
                        evidence["model_requests_tail"] = [
                            {"tools": [t.get("function", {}).get("name") or t.get("name")
                                for t in (request.tools or [])],
                             "tail_messages": request.messages[-4:]}
                            for request in agent.invocations[-3:]]
                    evidence["session_jobs"] = [
                        {"tool_key": job.tool_key, "status": job.status.value,
                         "input": job.input_payload, "output_summary": str(job.output_payload)[:600]}
                        for job in await f.jobs.list_jobs(f.execution.session_id)]
                except Exception:
                    pass
                raise
    finally:
        output = os.getenv("QA_DEFAULT_EXECUTION_REPLANNING_DIR")
        if output:
            path = Path(output)
            path.mkdir(parents=True, exist_ok=True)
            (path / "verification.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            (path / "evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
