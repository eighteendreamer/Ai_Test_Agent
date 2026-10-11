"""Discover an actual scene without an authored case source reference."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.containers import AppContainer
from src.schemas.case_planning_scenes import ScenePlanningQuery, SCENE_QUERY_PROTOCOL
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_scene_store import PostgresRecordingSceneStore
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
from src.application.test_cases.planning_sources import FULL_SOURCE_IDENTITY_PROTOCOL
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import SubmissionModel, delivery, expire
from test_planning_linked_sources import linked_policy
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_QUERY") != "1",
    reason="actual main/CDP/MySQL/PG/Redis planning queries")]


@pytest.mark.parametrize("query_stage,fault", [("logic","pending-read"),("logic","read-ack"),
    ("effects","pending-read"),("effects","read-ack")])
async def test_actual_unlinked_scene_query(tmp_path, monkeypatch, query_stage, fault):
    monkeypatch.setenv("RECORDING_SCENE_EVIDENCE", str(Path(os.environ["RECORDING_SCENE_EVIDENCE"])/(query_stage+"-"+fault)))
    state = {"read_calls":[],"model_calls":0}
    async def probe(*, stage, settings, client, evidence, project_ids, version):
        if stage not in {"initial","restart"}:
            return
        def save(name, value):
            (evidence/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        container = AppContainer(); container.settings.override(settings)
        model = SubmissionModel(); container.model_runtime_service.override(model)
        container.test_case_embedding_service.override(None)
        sources = container.project_planning_sources()
        sources.set_scene_sources(PostgresRecordingSceneStore(settings))
        original_read = sources.read_scene_basis
        async def observe_read(project, basis):
            state["read_calls"].append(basis.model_dump(mode="json"))
            return await original_read(project, basis)
        monkeypatch.setattr(sources,"read_scene_basis",observe_read)
        planner = container.case_planning_service()
        jobs = PostgresPlanningTaskStore(settings,lease_seconds=30)
        tasks = CasePlanningTaskService(store=jobs,planning_service=planner,
            project_service=container.project_service(),dispatch_stream="scene-query-acceptance")
        await tasks.initialize()
        env = SimpleNamespace(settings=settings,cases=container.test_case_service(),jobs=jobs)
        if stage == "initial":
            response = await client.post(f"/recordings/scenes/projects/{project_ids[0]}",json={
                "request_id":str(uuid4()),"recording_id":version["version"]["source"]["recording_id"]})
            response.raise_for_status()
            state["extra_scene"] = response.json()
            project = await container.project_service().get(project_ids[0])
            case = await active_case(env,project,"unlinked-scene-target",{
                "objective":"核对重复点击计数", "steps":[{"action":"按场景资料检查两次点击", "expected":"计数增加"}],
                "未知嵌套":{"输入":[0,False,"😀"]}},mode="ui_testing")
            assert all(ref.source_type != "ui_recording_scene" for ref in case.version.source_refs)
            chosen = linked_policy().model_dump(mode="json")
            chosen.pop("linked_source_protocol")
            chosen.update(scene_query_protocol=SCENE_QUERY_PROTOCOL,max_candidates=1,max_rounds=6)
            if query_stage == "effects": chosen["evidence_reference_protocol"] = "case-planning-evidence-references-v1"
            response = await client.put(f"/projects/{project.id}/case-plans/policy",json=chosen)
            response.raise_for_status(); save("scene-query-policy.json",response.json())
            task = await tasks.submit(project.id,CasePlanningTaskRequest(creation_key=str(uuid4()),
                objective="通过项目场景资料规划",candidate_case_ids=[case.case.id]))
            state.update(task=task,case=case,version=version)
            def logic_finish(context):
                return finish(item(context,"unlinked-scene-target",effect_schema=3))
            def search(context):
                assert context["catalog"]["scene_catalog"]["has_more"]
                assert not any(e["kind"] == "scene" for e in context["evidence"])
                return {"action":"query","summary":"寻找场景资料","queries":[{"kind":"scenes","offset":0}]}
            def next_page(context):
                catalog = context["query_results"][0]["scene_catalog"]
                assert catalog["has_more"] and len(catalog["items"]) == 1
                assert not any(e["kind"] == "scene" for e in context["evidence"])
                return {"action":"query","summary":"继续场景目录","queries":[{"kind":"scenes","offset":1}]}
            def read(context):
                catalog = context["query_results"][0]["scene_catalog"]
                assert not catalog["has_more"]
                found = catalog["items"][0]
                assert found["scene_id"] == version["scene_id"]
                assert found["version_id"] == version["version"]["id"]
                return {"action":"query","summary":"读取精确场景正文","queries":[{"kind":"scene",
                    "source_id":found["scene_id"],"version_id":found["version_id"],"content_hash":found["content_hash"]}]}
            model.replies = ([logic_finish] if query_stage == "effects" else []) + [search,next_page,read]
            original_save = jobs.save_checkpoint
            async def lose_ack(claim, checkpoint, *, message):
                await original_save(claim,checkpoint,message=message)
                pending = checkpoint.pending_decision
                if (checkpoint.phase == "query" and pending and pending.queries[0].kind == "scene"
                    and checkpoint.query_index == (0 if fault == "pending-read" else 1)):
                    raise RuntimeError("Actual scene query checkpoint acknowledgement lost")
            monkeypatch.setattr(jobs,"save_checkpoint",lose_ack)
            with pytest.raises(PlanningCheckpointUnavailable):
                await PlanningTaskHandler(planner,jobs,worker_id="query-first")(delivery(task))
            state["model_calls"] = len(model.requests)
            assert state["model_calls"] == (3 if query_stage == "logic" else 4)
            with postgres_connect(settings) as conn,conn.cursor() as cur:
                cur.execute(f"SELECT seed,checkpoint FROM {jobs._table} WHERE id=%s",(task.id,))
                state["initial"] = dict(cur.fetchone())
            save("scene-query-initial-pg.json",state["initial"])
            assert len(state["read_calls"]) == (0 if fault == "pending-read" else 1)
        else:
            response = await client.put(f"/projects/{project_ids[0]}/case-plans/policy",json=linked_policy().model_dump(mode="json"))
            response.raise_for_status()
            def check_scene(context):
                scene = next(e for e in context["evidence"] if e["kind"] == "scene")
                original = state["version"]["version"]
                assert scene["version"] == original["id"] and scene["content_hash"] == original["content_hash"]
                assert scene["payload"]["content"] == original["content"]
                assert scene["payload"]["source"] == original["source"]
                assert scene["payload"]["verification_status"] == "not_executed"
                assert context["query_results"][0]["evidence_ids"] == [scene["id"]]
                return scene
            def logic(context):
                scene = check_scene(context)
                return finish(item(context,"unlinked-scene-target",effect_schema=3,
                    evidence_ids=[f"case:{state['case'].version.id}",scene["id"]]))
            def effects(context):
                scene = (check_scene(context) if query_stage == "effects" else next(e for e in context["evidence"] if e["kind"] == "scene"))
                key = scene["id"]
                if query_stage == "effects":
                    key = next(k for k,v in context["evidence_reference_catalog"]["evidence"].items() if v["evidence_id"] == key)
                return {"action":"finish","summary":"场景资料已经固定","bindings":[{"item_key":"unlinked-scene-target","evidence_ids":[key]}]}
            model.replies = ([logic] if query_stage == "logic" else []) + [effects]
            expire(env,state["task"])
            await PlanningTaskHandler(planner,jobs,worker_id="query-restarted")(delivery(state["task"]))
            result = await jobs.get(project_ids[0],state["task"].id)
            save("scene-query-completed.json",result.model_dump(mode="json"))
            assert result.status == "completed",result.message
            calls = len(model.requests)
            await PlanningTaskHandler(planner,jobs,worker_id="query-terminal")(delivery(state["task"]))
            assert len(model.requests) == calls and state["model_calls"]+calls == 5
            assert len(state["read_calls"]) == 1
            with postgres_connect(settings) as conn,conn.cursor() as cur:
                cur.execute(f"SELECT seed,checkpoint FROM {jobs._table} WHERE id=%s",(state["task"].id,))
                raw = dict(cur.fetchone())
            save("scene-query-recovered-pg.json",raw)
            assert raw["seed"] == state["initial"]["seed"]
            assert raw["checkpoint"]["plan"]["status"] == "planned"
            scene = next(e for e in raw["checkpoint"]["plan"]["evidence"] if e["kind"] == "scene")
            assert scene["id"] in raw["checkpoint"]["plan"]["decision"]["items"][0]["evidence_ids"]
            save("scene-query-execution-facts.json",{"fixed_read_calls":state["read_calls"],"controlled_model_calls":5,
                "terminal_additional_calls":0,"no_authored_scene_reference":True,"query_stage":query_stage,"fault":fault})
            request = ScenePlanningQuery(kind="scene",source_id=state["version"]["scene_id"],
                version_id=state["version"]["version"]["id"],content_hash=state["version"]["version"]["content_hash"])
            # Use a fresh source reader, preserving the positive execution count.
            negative = container.project_planning_sources()
            monkeypatch.setattr(negative,"read_scene_basis",original_read)
            rejected = []
            for owner,query,protocol,name in [(project_ids[1],request,SCENE_QUERY_PROTOCOL,"foreign-project"),
                (project_ids[0],request.model_copy(update={"content_hash":"0"*64}),SCENE_QUERY_PROTOCOL,"wrong-hash"),
                (project_ids[0],request,None,"disabled-policy")]:
                with pytest.raises((KeyError,ValueError)):
                    await negative.query(owner,query,1,identity_protocol=FULL_SOURCE_IDENTITY_PROTOCOL,scene_query_protocol=protocol)
                rejected.append(name)
            assert (await negative.scene_catalog(project_ids[1],"",1,0))["items"] == []
            save("scene-query-rejections.json",rejected)
    await actual_recording_scene_management(tmp_path, scene_probe=probe, additional_scenes=1)
