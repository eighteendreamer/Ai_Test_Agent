"""Historical wire equivalence and actual document reads under source V2."""
import base64
import json
import os
from pathlib import Path

import pytest

from src.application.recorder.scene_knowledge import scene_knowledge_payload
from src.application.test_cases.planning_stages import build_stage_prompts
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint, CasePlanningTaskRequest
from src.schemas.case_planning_sources import SCENE_LINKED_SOURCE_PROTOCOL
from src.schemas.recording_scene import SceneVersion
from src.schemas.project import ProjectCreateRequest
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.test_cases.planning_evidence_references import evidence_reference_catalog, bind_evidence_references
from src.application.runtime.case_action_binding import _digest
from src.schemas.case_planning import PlanningEvidence
from src.schemas.case_planning_references import ReferencedEvidenceDecision
from src.runtime.planning_task_handler import PlanningTaskHandler
from test_case_planning_tasks import tasks_environment, delivery, live
from test_case_planning import active_case, finish
from test_planning_linked_sources import linked_policy

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("directory", ["p4-item-operation-default-first", "p4-source-identity-default-first", "p4-operation-prefetch-default-first"])
def test_old_scene_unaware_seeds_and_prompts_remain_exact(directory):
    raw = json.loads((ROOT/"docs/upgrade_2026-10-03"/directory/"PG-planning.json").read_bytes())
    assert PlanningTaskSeed.model_validate(raw["seed"]).model_dump(mode="json") == raw["seed"]
    assert PlanningTaskCheckpoint.model_validate(raw["checkpoint"]).model_dump(mode="json") == raw["checkpoint"]
    p = CasePlanningPolicy.model_validate(raw["seed"]["plan"]["config_snapshot"]["policy"])
    assert build_stage_prompts(p.planning_stage_protocol, p.effect_expected_source_protocol,
        p.operation_prefetch_protocol, p.source_identity_protocol, p.linked_source_protocol,
        p.evidence_reference_protocol).model_dump(mode="json") == raw["seed"]["stage_prompts"]


def test_original_scene_tool_projection_is_unchanged():
    old = ROOT/"docs/upgrade_2026-10-03/p5-scene-knowledge-complete"
    version = SceneVersion.model_validate(json.loads((old/"version2.json").read_bytes())["version"])
    knowledge = json.loads((old/"scene-knowledge-registered.json").read_bytes())["output"]["scene_knowledge"]
    assert scene_knowledge_payload(version) == knowledge


def actual_scene_evidence():
    raw = json.loads((ROOT/"docs/upgrade_2026-10-03/p5-scene-planning-final/basis-ack/scene-planning-recovered-pg.json").read_bytes())
    captured = next(e for e in raw["checkpoint"]["plan"]["evidence"] if e["kind"] == "scene")
    value = PlanningEvidence.model_validate(captured)
    assert value.model_dump(mode="json") == captured
    value.payload_hash = _digest(value.payload)
    return value


@pytest.mark.parametrize("mutation", ["projection", "content", "source", "missing-hash"])
def test_real_scene_projection_and_native_asset_hashes_are_separate(mutation):
    captured = actual_scene_evidence()
    catalog = evidence_reference_catalog([captured])
    assert catalog["evidence"]["E1"]["payload_hash"] == captured.payload_hash
    changed = captured.model_copy(deep=True)
    if mutation == "projection":
        changed.payload["aliases"][0]["name"] += "-altered"
    elif mutation == "missing-hash":
        changed.payload_hash = None
    else:
        changed.payload[mutation]["tampered"] = True
        changed.payload_hash = _digest(changed.payload)
    with pytest.raises(ValueError, match="场景原始快照或已读知识投影哈希不一致"):
        evidence_reference_catalog([changed])


def test_real_scene_reference_catalog_detects_projection_drift_and_restores_id():
    captured = actual_scene_evidence()
    catalog = evidence_reference_catalog([captured])
    response = ReferencedEvidenceDecision.model_validate({"action":"finish", "summary":"固定场景",
        "bindings":[{"item_key":"scene-linked-actual", "evidence_ids":["E1"]}]})
    restored = bind_evidence_references(response, catalog, [captured])
    assert restored.bindings[0].evidence_ids == [captured.id]
    changed = captured.model_copy(deep=True)
    changed.payload["aliases"][0]["name"] += "-altered"
    changed.payload_hash = _digest(changed.payload)
    with pytest.raises(ValueError, match="本次调用短引用目录与固定已读证据不同"):
        bind_evidence_references(response, catalog, [changed])


@pytest.mark.asyncio
@live
async def test_linked_source_v2_still_reads_the_actual_authored_document(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="scene-v2-document", name="新来源协议文档兼容"))
        raw = "# 场景辅助资料\n0 false LAST-V2-DOCUMENT 😀".encode("utf-8")
        path = tmp_path/"scene-v2-requirements.md"
        path.write_bytes(raw)
        document = await env.docs.upload_document(project_id=project.id, filename=path.name,
            content_base64=base64.b64encode(path.read_bytes()).decode())
        case = await active_case(env, project, "scene-v2-document", {"objective":"读取项目原件", "extra":[0,False,"😀"]},
            source_refs=[{"source_type":"api_doc", "source_id":document.id, "version":document.current_version_id,
                "metadata":{"content_asset_id":document.original_asset.id,"content_sha256":document.original_asset.sha256}}])
        policy = linked_policy().model_dump(mode="json")
        policy["linked_source_protocol"] = SCENE_LINKED_SOURCE_PROTOCOL
        await env.service.save_policy(project.id, CasePlanningPolicy.model_validate(policy))
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="v2-fixed-document",
            objective="只读检查文档链", candidate_case_ids=[case.case.id]))
        def respond(context):
            entry = context["linked_source_reads"]["entries"][0]
            assert entry["read"] and entry["basis"]["role"] == "authored_snapshot"
            doc = next(e for e in context["evidence"] if e["kind"] == "document")
            assert doc["payload"]["content"] == raw.decode("utf-8")
            assert doc["payload"]["content_sha256"] == document.original_asset.sha256
            return finish()
        env.model.replies = [respond]
        await PlanningTaskHandler(env.service, env.jobs, worker_id="scene-v2-doc")(delivery(task))
        terminal = await env.jobs.get(project.id, task.id)
        plan = await env.service.get(project.id, terminal.plan_id)
        assert terminal.status == "completed" and plan.status == "no_match", plan.summary
        assert plan.prompt_version == "case-planning-linked-scenes-v12+linked-navigation-v1"
        await PlanningTaskHandler(env.service, env.jobs, worker_id="scene-v2-doc-terminal")(delivery(task))
        assert len(env.model.requests) == 1
        if os.getenv("SCENE_PLANNING_DOC_EVIDENCE"):
            out = Path(os.environ["SCENE_PLANNING_DOC_EVIDENCE"])
            out.mkdir(parents=True, exist_ok=False)
            (out/path.name).write_bytes(raw)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT seed,checkpoint FROM {env.jobs._table} WHERE id=%s", (task.id,))
                saved = dict(cur.fetchone())
            (out/"verification.json").write_text(json.dumps({"actual_pg":saved,
                "actual_mysql":(await env.service.get_policy(project.id)).model_dump(mode="json"),
                "requests":[r.model_dump(mode="json") for r in env.model.requests],
                "controlled_response":True,"model_requests":1,"terminal_additional_requests":0},
                ensure_ascii=False,indent=2),encoding="utf-8")
