"""Actual production worker bootstrap reads the API-created fixed scene."""
import json
import os

import pytest

from src.runtime.planning_worker_runtime import planning_worker_runtime
from src.schemas.case_planning_sources import SceneContentBasis
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_PLANNING") != "1",
    reason="actual main/CDP/worker bootstrap/MySQL/PG/Redis")]


async def test_production_worker_bootstrap_uses_the_fixed_scene_store(tmp_path):
    observations = []

    async def probe(*, stage, settings, client, evidence, project_ids, version):
        if stage not in {"initial", "restart"}:
            return
        stream = settings.orchestration.case_planning_task_stream
        settings.orchestration.redis_task_outbox_streams = stream
        original = version["version"]
        basis = SceneContentBasis(project_id=project_ids[0], scene_id=version["scene_id"],
            version_id=original["id"], content_hash=original["content_hash"])
        async with planning_worker_runtime(settings) as runtime:
            entries, metadata = await runtime.planner._sources.read_scene_basis(project_ids[0], basis)
            scene = entries[0]
            assert scene.payload["content"] == original["content"]
            assert scene.payload["source"] == original["source"]
            assert scene.version == original["id"] and scene.content_hash == original["content_hash"]
            assert scene.payload["verification_status"] == "not_executed"
            assert metadata == {"has_more":False}
            observations.append({"stage":stage, "evidence":scene.model_dump(mode="json"),
                "production_bootstrap":True, "model_invocations":0})
            (evidence/"scene-planning-worker-reads.json").write_text(
                json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")

    await actual_recording_scene_management(tmp_path, scene_probe=probe)
    assert [item["stage"] for item in observations] == ["initial", "restart"]
