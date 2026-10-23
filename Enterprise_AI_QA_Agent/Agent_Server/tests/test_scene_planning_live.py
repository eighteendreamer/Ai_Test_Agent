"""New actual capture and scene, authored source pre-read and PG recovery."""
import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.containers import AppContainer
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_scene_store import PostgresRecordingSceneStore
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
from src.application.test_cases.planning_evidence_references import evidence_reference_catalog
from src.application.runtime.case_action_binding import _digest
from src.schemas.case_planning import PlanningEvidence
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.case_planning_task import PlanningTaskCheckpoint
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import SubmissionModel, delivery, expire
from test_planning_linked_sources import linked_policy
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_PLANNING") != "1",
    reason="new Chromium/CDP/main/App/MySQL/PG/Redis")]


@pytest.mark.parametrize("fault", ["basis-ack", "read-ack"])
@pytest.mark.parametrize("reference", [False, True])
async def test_actual_scene_source_before_planning_and_fixed_recovery(tmp_path, monkeypatch, fault, reference):
    monkeypatch.setenv("RECORDING_SCENE_EVIDENCE", str(Path(os.environ["RECORDING_SCENE_EVIDENCE"])/(fault + ("-refs" if reference else ""))))
    ack_index = 0 if fault == "basis-ack" else 1
    state = {}

    async def probe(*, stage, settings, client, evidence, project_ids, version):
        def save(name, value):
            (evidence/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        container = AppContainer()
        container.settings.override(settings)
        model = SubmissionModel()
        container.model_runtime_service.override(model)
        container.test_case_embedding_service.override(None)
        sources = container.project_planning_sources()
        if hasattr(sources, "set_scene_sources"):
            sources.set_scene_sources(PostgresRecordingSceneStore(settings))
        planner = container.case_planning_service()
        jobs = PostgresPlanningTaskStore(settings, lease_seconds=30)
        tasks = CasePlanningTaskService(store=jobs, planning_service=planner,
            project_service=container.project_service(), dispatch_stream="scene-planning-acceptance")
        await tasks.initialize()
        env = SimpleNamespace(settings=settings, cases=container.test_case_service(), jobs=jobs)
        if stage == "initial":
            source = {"source_type":"ui_recording_scene", "source_id":version["scene_id"],
                "version":version["version"]["id"], "metadata":{"content_hash":version["version"]["content_hash"]}}
            project = await container.project_service().get(project_ids[0])
            case = await active_case(env, project, "scene-linked-actual", {
                "objective":"按已录制计数页面验证两次点击", "steps":version["version"]["source"]["original_draft"]["steps"],
                "未知嵌套":{"保留":[0,False,"中文😀"]}}, mode="ui_testing", source_refs=[source])
            chosen = linked_policy().model_dump(mode="json")
            chosen["linked_source_protocol"] = os.getenv("SCENE_PLANNING_PROTOCOL", "case-planning-linked-sources-v2")
            if reference:
                chosen["evidence_reference_protocol"] = "case-planning-evidence-references-v1"
            response = await client.put(f"/projects/{project.id}/case-plans/policy", json=chosen)
            response.raise_for_status()
            save("scene-planning-policy.json", response.json())
            task = await tasks.submit(project.id, CasePlanningTaskRequest(creation_key=str(uuid4()),
                objective="按项目固定用例规划", candidate_case_ids=[case.case.id]))
            state.update(task=task, case=case, source=source, version=version)
            original = jobs.save_checkpoint
            async def lose_ack(claim, checkpoint, *, message):
                await original(claim, checkpoint, message=message)
                p = checkpoint.linked_sources
                if p and p.entries[0].basis is not None and p.next_index == ack_index:
                    state["claim"] = claim
                    raise RuntimeError("Actual committed scene checkpoint acknowledgement lost")
            if chosen["linked_source_protocol"].endswith("v2"):
                monkeypatch.setattr(jobs, "save_checkpoint", lose_ack)
                with pytest.raises(PlanningCheckpointUnavailable):
                    await PlanningTaskHandler(planner, jobs, worker_id="scene-first")(delivery(task))
                assert model.requests == []
            else:
                def require_scene(context):
                    assert context["linked_source_reads"]["entries"][0]["read"], context["linked_source_reads"]
                    return finish()
                model.replies = [require_scene]
                await PlanningTaskHandler(planner, jobs, worker_id="scene-before")(delivery(task))
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT seed,checkpoint FROM {jobs._table} WHERE id=%s", (task.id,))
                raw = dict(cur.fetchone())
            save("scene-planning-initial-pg.json", raw)
            assert raw["checkpoint"]["linked_sources"]["entries"][0]["basis"] is not None, raw["checkpoint"]["linked_sources"]
            if chosen["linked_source_protocol"].endswith("v2"):
                rejected = []
                for fault in ("changed-hash", "missing-progress"):
                    changed = PlanningTaskCheckpoint.model_validate(raw["checkpoint"])
                    if fault == "changed-hash": changed.linked_sources.entries[0].basis.content_hash = "0" * 64
                    else: changed.linked_sources = None
                    with pytest.raises(ValueError):
                        await original(state["claim"], changed, message="reject altered scene basis")
                    rejected.append(fault)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT seed,checkpoint FROM {jobs._table} WHERE id=%s", (task.id,))
                    assert dict(cur.fetchone()) == raw
                save("scene-planning-checkpoint-rejections.json", rejected)
        elif stage == "restart":
            task = state["task"]
            # Change current MySQL policy; the PG seed remains the original V2.
            changed = linked_policy().model_dump(mode="json")
            changed.update(linked_source_protocol="case-planning-linked-documents-v1", max_rounds=1)
            response = await client.put(f"/projects/{project_ids[0]}/case-plans/policy", json=changed)
            response.raise_for_status()
            def logic(context):
                entry = context["linked_source_reads"]["entries"][0]
                assert entry["read"] and not entry["error"]
                scene = next(e for e in context["evidence"] if e["kind"] == "scene")
                expected = state["version"]["version"]
                assert scene["version"] == expected["id"] and scene["content_hash"] == expected["content_hash"]
                assert scene["payload"]["content"] == expected["content"]
                assert scene["payload"]["source"] == expected["source"]
                assert scene["payload"]["verification_status"] == "not_executed"
                captured = PlanningEvidence.model_validate(scene)
                catalog = evidence_reference_catalog([captured])
                assert catalog["evidence"]["E1"]["content_hash"] == expected["content_hash"]
                rejected_hashes = []
                for mutation in ("alias-projection", "native-content", "native-source"):
                    altered = captured.model_copy(deep=True)
                    if mutation == "alias-projection":
                        altered.payload["aliases"][0]["name"] += "-altered"
                    else:
                        altered.payload["content" if mutation == "native-content" else "source"]["tampered"] = True
                        altered.payload_hash = _digest(altered.payload)
                    with pytest.raises(ValueError, match="场景原始快照或已读知识投影哈希不一致"):
                        evidence_reference_catalog([altered])
                    rejected_hashes.append(mutation)
                save("scene-planning-hash-rejections.json", rejected_hashes)
                return finish(item(context, "scene-linked-actual", effect_schema=3,
                    evidence_ids=[f"case:{state['case'].version.id}", scene["id"]]))
            def effects(context):
                scene = next(e for e in context["evidence"] if e["kind"] == "scene")
                key = scene["id"]
                if reference:
                    key = next(k for k,v in context["evidence_reference_catalog"]["evidence"].items() if v["evidence_id"] == scene["id"])
                return {"action":"finish", "summary":"固定来源保持", "bindings":[{"item_key":"scene-linked-actual", "evidence_ids":[key]}]}
            model.replies = [logic, effects]
            expire(env, task)
            await PlanningTaskHandler(planner, jobs, worker_id="scene-restarted")(delivery(task))
            completed = await jobs.get(project_ids[0], task.id)
            save("scene-planning-completed.json", completed.model_dump(mode="json"))
            assert completed.status == "completed", completed.message
            before = len(model.requests)
            await PlanningTaskHandler(planner, jobs, worker_id="scene-terminal")(delivery(task))
            assert len(model.requests) == before == 2
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT seed,checkpoint FROM {jobs._table} WHERE id=%s", (task.id,))
                raw = dict(cur.fetchone())
            save("scene-planning-recovered-pg.json", raw)
            assert raw["seed"] == json.loads((evidence/"scene-planning-initial-pg.json").read_bytes())["seed"]
            scene = next(e for e in raw["checkpoint"]["plan"]["evidence"] if e["kind"] == "scene")
            assert scene["id"] in raw["checkpoint"]["plan"]["decision"]["items"][0]["evidence_ids"]
            failures = []
            for fault in ("missing-version", "missing-hash", "wrong-hash", "foreign-project"):
                source = deepcopy(state["source"])
                if fault == "missing-version": source.pop("version")
                if fault == "missing-hash": source["metadata"].pop("content_hash")
                if fault == "wrong-hash": source["metadata"]["content_hash"] = "0" * 64
                owner = project_ids[1] if fault == "foreign-project" else project_ids[0]
                project = await container.project_service().get(owner)
                case = await active_case(env, project, "scene-source-" + fault,
                    {"objective":"校验固定来源拒绝", "steps":state["version"]["version"]["source"]["original_draft"]["steps"]},
                    mode="ui_testing", source_refs=[source])
                chosen = linked_policy().model_dump(mode="json")
                chosen["linked_source_protocol"] = "case-planning-linked-sources-v2"
                response = await client.put(f"/projects/{owner}/case-plans/policy", json=chosen)
                response.raise_for_status()
                failed_task = await tasks.submit(owner, CasePlanningTaskRequest(creation_key=str(uuid4()),
                    objective="只读检查错误来源", candidate_case_ids=[case.case.id]))
                def rejected(context):
                    entry = context["linked_source_reads"]["entries"][0]
                    assert entry["error"] and not entry["read"] and entry["basis"] is None
                    assert not any(e["kind"] == "scene" for e in context["evidence"])
                    return finish(unresolved=[{"condition":"没有匹配的正式目标用例",
                        "reason":"当前只读检查固定来源错误，不是用例正文中的业务目标。"}])
                model.replies = [rejected]
                await PlanningTaskHandler(planner, jobs, worker_id="scene-" + fault)(delivery(failed_task))
                result = await jobs.get(owner, failed_task.id)
                assert result.status == "completed", result.message
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT seed,checkpoint FROM {jobs._table} WHERE id=%s", (failed_task.id,))
                    record = dict(cur.fetchone())
                    assert record["checkpoint"]["plan"]["status"] == "no_match"
                    failures.append({"fault":fault, **record})
            save("scene-planning-source-rejections.json", failures)

    await actual_recording_scene_management(tmp_path, scene_probe=probe)
