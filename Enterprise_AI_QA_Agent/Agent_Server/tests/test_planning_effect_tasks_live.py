"""Actual task receipts, lost acknowledgements and fixed original budgets."""
import base64
import json
import os
from pathlib import Path

import pytest
from test_case_planning_tasks import tasks_environment, delivery, expire, live
from test_planning_linked_sources import linked_case, linked_policy
from test_planning_evidence_references import reference_wire
from test_case_effect_relations import document_declaration
from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
from src.schemas.project import ProjectCreateRequest
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskCheckpoint
from src.schemas.case_planning_references import REFERENCE_PROTOCOL, REFERENCE_PROTOCOL_V2
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.infrastructure.postgres_runtime import postgres_connect

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("reference_protocol", [REFERENCE_PROTOCOL, REFERENCE_PROTOCOL_V2])
@pytest.mark.parametrize("fault", ["none", "bindings-ack", "produced-ack", "cleanup-response-ack", "budget", "unknown-reference", "produced-query"])
async def test_actual_separate_receipts_restore_without_resending_completed_tasks(tmp_path, monkeypatch, fault, reference_protocol):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="tasks-"+fault, name="分任务证据"))
        raw = (ROOT / "docs/upgrade_2026-10-03/p4-linked-source-default-fixed/requirements.md").read_bytes()
        document = await env.docs.upload_document(project_id=project.id, filename="requirements.md", content_base64=base64.b64encode(raw).decode())
        case, content = await linked_case(env, project, [{"source_type": "api_doc", "source_id": document.id}])
        policy = CasePlanningPolicy.model_validate({**linked_policy().model_dump(mode="json"),
            "evidence_reference_protocol": reference_protocol, "planning_stage_protocol": "case-planning-staged-evidence-v3",
            "max_rounds": 3 if fault == "budget" else 5})
        await env.service.save_policy(project.id, policy)
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="original", objective=content["objective"], candidate_case_ids=[case.case.id]))

        produced_reply = []
        def respond(context):
            assert next(e["payload"]["content"] for e in context["evidence"] if e["kind"] == "case") == content
            doc = next(e for e in context["evidence"] if e["kind"] == "document")
            assert doc["version"] == document.current_version_id
            if context["planning_stage"] == "logic":
                return {"action": "finish", "summary": "完整正式项自行产生及清理", "items": [{"key": "target", "kind": "target", "effect_schema": 3,
                    "case_id": case.case.id, "case_version_id": case.version.id, "reason": "完整业务用例", "cleanup_sources": ["target"],
                    "evidence_ids": [e["id"] for e in context["evidence"]]}], "operation_selections": [{"item_key": "target",
                    "tool_key": "api-test-runner", "operation": "execute_task", "evidence_ids": [doc["id"]]}]}
            task_name = context["evidence_task"]["task"]
            if task_name == "produced" and not produced_reply:
                produced_reply.append(True)
                if fault == "produced-query":
                    return {"task": task_name, "action": "query", "summary": "补查固定完整工具契约",
                        "queries": [{"kind": "tool", "source_id": "api-test-runner", "operation": "execute_task"}]}
            assert context["evidence_task"]["scope"] == ["target"]
            assert [r["task"] for r in context["completed_tasks"]["completed"]] == ["bindings", "produced"][:["bindings", "produced", "cleanup"].index(task_name)]
            tool = next(e for e in context["evidence"] if e["kind"] == "tool")
            binding = {"item_key": "target", "evidence_ids": [tool["id"], doc["id"]], "effect_declarations": None}
            if task_name != "bindings": binding["effect_declarations"] = [document_declaration(task_name, "target", doc)]
            value = reference_wire({"action": "finish", "summary": "固定"+task_name+"回执", "bindings": [binding]}, context["evidence_reference_catalog"], reference_protocol)
            if task_name == "bindings":
                value["bindings"][0].pop("effect_declarations")
                value["bindings"][0]["declaration_roles"] = []
            if fault == "unknown-reference" and task_name == "produced" and len(produced_reply) == 1:
                produced_reply.append(True)
                value["bindings"][0]["evidence_ids"] = ["E999"]
            return {**value, "task": task_name}

        env.model.replies = [respond] * 5
        original = env.jobs.save_checkpoint
        claims = []
        async def lose_ack(claim, checkpoint, *, message):
            await original(claim, checkpoint, message=message)
            completed = checkpoint.effect_tasks.completed if checkpoint.effect_tasks else []
            matched = (fault == "cleanup-response-ack" and checkpoint.phase == "response" and checkpoint.plan.rounds[-1].get("evidence_task", {}).get("task") == "cleanup"
                or fault in {"bindings-ack", "produced-ack"} and checkpoint.phase == "ready" and completed and completed[-1].task+"-ack" == fault)
            if matched and not claims:
                claims.append(claim); raise RuntimeError("Actual task receipt committed; acknowledgement lost")
        before = None
        rejections = []
        if "ack" in fault:
            monkeypatch.setattr(env.jobs, "save_checkpoint", lose_ack)
            with pytest.raises(PlanningCheckpointUnavailable): await PlanningTaskHandler(env.service, env.jobs, worker_id="first")(delivery(task))
            before = await env.jobs.get(project.id, task.id)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT checkpoint FROM {env.jobs._table} WHERE id=%s", (task.id,)); saved = cur.fetchone()["checkpoint"]
            changed = PlanningTaskCheckpoint.model_validate(saved)
            changed.effect_tasks.completed[0].summary = "FORBIDDEN-REWRITE"
            with pytest.raises(ValueError, match="回退或改写"): await original(claims[0], changed, message="必须保持已保存回执")
            rejections.append("completed_receipt_rewrite")
            await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
            await env.docs.upload_version(document.id, ApiDocVersionUploadRequest(project_id=project.id, request_id="later", expected_parent_version_id=document.current_version_id,
                filename="requirements.md", content_base64=base64.b64encode(b"NEW-DOCUMENT-DO-NOT-MIX").decode()))
            monkeypatch.setattr(env.jobs, "save_checkpoint", original)
            expire(env, task)
        restored = PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service, restored, worker_id="resume")(delivery(task))
        terminal = await restored.get(project.id, task.id)
        plan = await env.service.get(project.id, terminal.plan_id)
        assert terminal.status == ("failed" if fault == "budget" else "completed"), terminal
        assert plan.status == ("planning_failed" if fault == "budget" else "planned"), plan.summary
        calls = 3 if fault == "budget" else 5 if fault in {"unknown-reference", "produced-query"} else 4
        assert len(env.model.requests) == calls
        if before: assert terminal.attempt_no == 2 and terminal.deadline_at == before.deadline_at
        await PlanningTaskHandler(env.service, restored, worker_id="terminal")(delivery(task))
        assert len(env.model.requests) == calls
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT seed,checkpoint,status,attempt_no,deadline_at FROM {env.jobs._table} WHERE id=%s", (task.id,)); final = dict(cur.fetchone())
        final["deadline_at"] = final["deadline_at"].isoformat()
        assert final["seed"]["plan"]["config_snapshot"]["policy"]["planning_stage_protocol"] == "case-planning-staged-evidence-v3"
        assert len(final["checkpoint"]["effect_tasks"]["completed"]) == (2 if fault == "budget" else 3)
        if os.getenv("QA_EFFECT_TASK_DIR"):
            out = Path(os.environ["QA_EFFECT_TASK_DIR"]) / reference_protocol / fault; out.mkdir(parents=True, exist_ok=False)
            (out / "requirements.md").write_bytes(raw)
            (out / "verification.json").write_text(json.dumps({"actual_PG": final, "actual_requests": [r.model_dump(mode="json") for r in env.model.requests],
                "actual_current_MySQL": (await env.service.get_policy(project.id)).model_dump(mode="json"), "actual_SQL_rejections": rejections,
                "controlled_model": True, "business_HTTP": 0, "terminal_extra_model_calls": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
