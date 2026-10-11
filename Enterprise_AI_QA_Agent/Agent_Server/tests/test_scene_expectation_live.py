"""New authored scene, actual browser ToolRuntime, frozen PG run and receipt."""
import json
import hashlib
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.containers import AppContainer
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.runtime.tool_runtime_service import ToolRuntimeService, ToolExecutionContext
from src.application.runtime.case_action_binding import _digest, CaseActionBinding
from src.schemas.case_execution import AgentCaseExecutionCheckpoint
from src.infrastructure.recording_scene_store import PostgresRecordingSceneStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.registry.tools import ToolRegistry
from src.registry.mcp import MCPRegistry
from src.schemas.case_planning import CasePlanRunRequest
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.run_management import RunItemCompletion
from src.schemas.tool_runtime import ModelToolCall
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import SubmissionModel, delivery
from test_planning_linked_sources import linked_policy
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_EXPECTATION") != "1",
    reason="requires actual recording/API/MySQL/PG/Chromium; controlled planning only")]


@pytest.fixture
def owned_scene_http(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/scene.html":
                self.send_error(404);return
            raw = (tmp_path/"scene.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/scene.html"
    finally:
        server.shutdown();server.server_close();thread.join(5)
        assert not thread.is_alive()


async def test_actual_scene_business_expectation_and_persisted_browser_receipt(tmp_path,owned_scene_http):
    async def probe(*, stage, settings, client, evidence, project_ids, version):
        if stage != "initial":
            return
        def save(name, value):
            (evidence/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        project_id = project_ids[0]
        root = f"/recordings/scenes/projects/{project_id}"
        response = await client.post(root,json={"request_id":str(uuid4()),
            "recording_id":version["version"]["source"]["recording_id"]})
        response.raise_for_status(); scene = response.json()
        content = scene["version"]["content"]
        content["name"] = "业务断言逐项回执实际验收"
        content["assertions"] = [{"kind":"page","target":"#count","operator":"equals","expected":value,
            "description":description} for value,description in [(2,"两次点击实际计数"),(5,"故意错误预期"),
                (0,"零值"),(False,"布尔假值"),(None,"空值")]]
        response = await client.post(root+f"/{scene['scene_id']}/versions",json={"request_id":str(uuid4()),
            "parent_version_id":scene["version"]["id"],"change_reason":"保存明确业务预期","content":content})
        response.raise_for_status(); scene = response.json(); saved = scene["version"]
        save("authored-scene.json",scene)
        container = AppContainer(); container.settings.override(settings)
        model = SubmissionModel(); container.model_runtime_service.override(model)
        container.test_case_embedding_service.override(None)
        sources = container.project_planning_sources()
        sources.set_scene_sources(PostgresRecordingSceneStore(settings))
        project = await container.project_service().get(project_id)
        case = await active_case(SimpleNamespace(settings=settings,cases=container.test_case_service()),project,
            "scene-receipt-actual",{"objective":"点击两次并核对固定场景业务预期", "steps":[{"action":"两次点击#repeat并读取计数"}]},
            mode="ui_testing",source_refs=[{"source_type":"ui_recording_scene","source_id":scene["scene_id"],
                "version":saved["id"],"metadata":{"content_hash":saved["content_hash"]}}])
        chosen = linked_policy().model_dump(mode="json")
        chosen.update(linked_source_protocol="case-planning-linked-sources-v2",scene_receipt_protocol="case-scene-receipt-v1")
        response = await client.put(f"/projects/{project_id}/case-plans/policy",json=chosen)
        response.raise_for_status(); save("mysql-policy.json",response.json())
        planner = container.case_planning_service()
        tasks_store = PostgresPlanningTaskStore(settings,lease_seconds=30)
        tasks = CasePlanningTaskService(store=tasks_store,planning_service=planner,
            project_service=container.project_service(),dispatch_stream="scene-receipt-acceptance")
        await tasks.initialize()
        def logic(context):
            scene_read = next(e for e in context["evidence"] if e["kind"] == "scene")
            return finish(item(context,"scene-receipt-actual",effect_schema=3,
                evidence_ids=[f"case:{case.version.id}",scene_read["id"]]))
        def effects(context):
            scene_read = next(e for e in context["evidence"] if e["kind"] == "scene")
            return {"action":"finish","summary":"固定场景为业务预期来源","bindings":[
                {"item_key":"scene-receipt-actual","evidence_ids":[scene_read["id"]]}]}
        model.replies = [logic,effects]
        task = await tasks.submit(project_id,CasePlanningTaskRequest(creation_key=str(uuid4()),
            objective="核对两次点击",candidate_case_ids=[case.case.id]))
        await PlanningTaskHandler(planner,tasks_store,worker_id="actual-scene-receipt")(delivery(task))
        task = await tasks_store.get(project_id,task.id)
        assert task.status == "completed",task.message
        save("planning-task.json",task.model_dump(mode="json"))
        runs = container.test_run_store()
        creator = CasePlanRunService(run_store=runs,suite_store=container.test_suite_store(),
            plan_store=container.case_plan_store(),case_service=container.test_case_service(),
            project_service=container.project_service())
        request = CasePlanRunRequest(creation_key=str(uuid4()),mode_key="ui_testing")
        detail = await creator.create(project_id,task.plan_id,request)
        now = datetime.now(timezone.utc)
        record, attempt = (await runs.claim_items(run_id=detail.run.id,worker_id="scene-browser",limit=1,
            lease_seconds=300,now=now))[0]
        await runs.start_item(record.id,attempt.lease_token,now)
        detail = await runs.get_run(detail.run.id)
        context = build_agent_case_context(detail,record,await container.test_case_service().get_version(record.case_version_id))
        assert context.scene_receipt_protocol == "case-scene-receipt-v1"
        save("frozen-execution.json",context.execution_json())
        jobs = container.tool_job_service(); await jobs.initialize()
        settings.orchestration.browser_headless = True
        mcp = MCPRuntimeService(MCPRegistry(),settings)
        mcp._artifact_root = evidence/"browser-originals"; mcp._artifact_root.mkdir()
        mcp._playwright_cli._artifact_root = mcp._artifact_root
        objects = container.artifact_storage_service()
        runtime = ToolRuntimeService(settings=settings,mcp_runtime_service=mcp,tool_job_service=jobs,
            artifact_storage_service=objects)
        owned_objects = []
        session,turn = str(uuid4()),str(uuid4())
        checkpoint = AgentCaseExecutionCheckpoint(project_id=context.project_id,run_id=context.run_id,
            run_item_id=context.run_item_id,plan_id=context.plan_id,context_hash=_digest(context.execution_json()),
            execution_attempt_id=attempt.id,session_id=session,turn_id=turn,phase="running")
        await runs.save_checkpoint(record.id,attempt.lease_token,"agent_case_execution",
            checkpoint.model_dump(mode="json"),None,datetime.now(timezone.utc))
        tool_context = ToolExecutionContext(session_id=session,turn_id=turn,trace_id=turn,
            user_message=context.objective,normalized_input=context.objective,
            context_bundle={**context.runtime_context(),"case_execution_managed":True,"attempt_id":attempt.id})
        try:
            tool = ToolRegistry().get("browser-control")
            observed = await runtime.execute(tool,ModelToolCall(id="actual-count",name=tool.key,arguments={
                "action":"evaluate_js","target_url":owned_scene_http,
                "javascript":"(() => { document.querySelector('#repeat').click(); document.querySelector('#repeat').click(); return {count: Number(document.querySelector('#count').textContent), zero: 0, flag: false, nullable: null}; })()",
                "_case_action":{"item_key":context.target_key,"operation_key":"click-twice-and-read"}}),tool_context)
            saved_job = await jobs.get_job_detail(observed.job_id)
            save("actual-browser-job.json",saved_job.model_dump(mode="json"))
            assert saved_job.output_payload["result"]["count"] == 2,saved_job.output_payload
            downloads = []
            originals = evidence/"browser-downloads";originals.mkdir()
            for index,artifact in enumerate(saved_job.artifacts):
                assert artifact.path.startswith("rustfs://"),artifact.path
                owned_objects.append(artifact.path)
                raw = (await objects.read_object_uri(artifact.path))["content"]
                (originals/f"{index}-{artifact.artifact_type}.raw").write_bytes(raw)
                downloads.append({"uri":artifact.path,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
            assert downloads
            save("browser-downloads.json",downloads)
            scene_read = next(e for e in context.evidence if e.kind == "scene")
            def receipt(indices):
                checks = [{"name":"固定场景断言"+str(index),"operator":"equals","expected_source":{
                    "evidence_id":scene_read.id,"content_hash":scene_read.content_hash,
                    "payload_path_tokens":["content","assertions",str(index),"expected"]},
                    "observations":[{"call_id":"actual-count","output_path_tokens":["result",field]}]}
                    for index,field in indices]
                return {"schema_version":2,"steps":[{"item_key":context.target_key,"status":"passed",
                    "summary":"按固定场景核对实际浏览器结果","checks":checks}]}
            assessor = AgentCaseAssessmentService(jobs)
            async def assess(data):
                return await assessor.assess(context=context,receipt=data,tool_records=[observed],session_id=session,
                    turn_id=turn,attempt_id=attempt.id,model_key="controlled-receipt",lease_token=attempt.lease_token)
            wrong = await assess(receipt([(1,"count")]))
            assert wrong.completion.status == "failed"
            save("wrong-expected-result.json",wrong.completion.model_dump(mode="json"))
            good = receipt([(0,"count"),(2,"zero"),(3,"flag"),(4,"nullable")])
            outcome = await assess(good)
            assert outcome.completion.status == "passed",outcome.completion
            save("good-receipt.json",good);save("good-result.json",outcome.completion.model_dump(mode="json"))
            save("verifications.json",[v.model_dump(mode="json") for v in outcome.verification_results])
            # Read-only candidate kernel under a real PG transaction. This does
            # not close consumption, authorize cleanup or complete the RunItem.
            binding = CaseActionBinding.model_validate(saved_job.metadata["case_action_binding"])
            with postgres_connect(settings) as connection,connection.cursor() as cursor:
                candidate = jobs._case_action_admission.consumption._candidate(cursor,context,saved_job,binding,
                    {"reason":"核对场景预期的消费候选","receipt":good},datetime.now(timezone.utc))
            assert candidate["verification"]["status"] == "passed"
            assert candidate["receipt"]["schema_version"] == 2
            assert candidate["run_item_completed"] is False
            save("pg-consumption-candidate.json",candidate)
            prompt = await assessor.build_assessment_request(context=context,tool_records=[observed],session_id=session,
                turn_id=turn,attempt_id=attempt.id)
            assert "case-scene-receipt" in prompt.system_prompt or '"schema_version"' in prompt.system_prompt
            save("assessment-request.json",prompt.model_dump(mode="json"))
            # Change live config and head; persisted execution retains its original authored basis.
            changed = dict(chosen);changed.pop("scene_receipt_protocol")
            (await client.put(f"/projects/{project_id}/case-plans/policy",json=changed)).raise_for_status()
            content["assertions"][0]["expected"] = 99
            response = await client.post(root+f"/{scene['scene_id']}/versions",json={"request_id":str(uuid4()),
                "parent_version_id":saved["id"],"change_reason":"执行后修改当前预期","content":content})
            response.raise_for_status();save("changed-head.json",response.json())
            restored = build_agent_case_context(await runs.get_run(detail.run.id),record,
                await container.test_case_service().get_version(record.case_version_id))
            assert restored.execution_json() == context.execution_json()
            result = await runs.complete_item(record.id,attempt.lease_token,RunItemCompletion.model_validate({
                **outcome.completion.model_dump(exclude={"lease_token"}),"payload_hash":"a"*64}),datetime.now(timezone.utc))
            save("completed-pg-result.json",result.model_dump(mode="json"))
            assert not await runs.claim_items(run_id=detail.run.id,worker_id="terminal",limit=1,
                lease_seconds=300,now=datetime.now(timezone.utc))
            replay = await creator.create(project_id,task.plan_id,request)
            assert replay.run.id == detail.run.id
            save("scene-expectation-facts.json",{"actual_browser_calls":1,"controlled_planning_calls":len(model.requests),
                "actual_count":2,"wrong_expectation":"failed","typed_zero_false_null":"passed",
                "frozen_after_head_change":True,"terminal_reclaim":0,"external_model_calls":0})
        finally:
            await mcp.close()
            assert not mcp._playwright_cli._sessions
            for uri in owned_objects:
                await objects.delete_object_uri(uri)
            await objects.close()
    await actual_recording_scene_management(tmp_path,scene_probe=probe,additional_scenes=1)
