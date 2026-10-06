"""Independent main API reads real ToolRuntime origins from PostgreSQL."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.tools import ToolRegistry
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from src.schemas.tool_runtime import ModelToolCall
from test_api_http_origin import fixed_target
from test_business_environment import capture_request
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_HTTP_ORIGIN_PRODUCTION") != "1",
    reason="independent main API, real MySQL/PG/Redis/RustFS and actual target HTTP")
async def test_production_inspects_actual_fixed_target_and_cross_target_after_restart(tmp_path):
    output = Path(os.getenv("QA_HTTP_ORIGIN_PRODUCTION_DIR", str(tmp_path / "evidence")))
    output.mkdir(parents=True, exist_ok=True)
    with fixed_target(7) as (old, old_calls, redirects), fixed_target(9) as (new, new_calls, _):
        redirects["/app/cross"] = new + "/app/rule"
        try:
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.api_auth_token = SecretStr(uuid4().hex)
                models = MySQLModelConfigStore(settings); models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="no-model-origin-fixture", provider="openai",
                    transport="openai_chat_completions", base_url=new + "/v1", api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False),
                    context_window=128000, max_output_tokens=1024))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("http-origin-api-first", *args)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=30,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)
                    async def post(path, body, status=201):
                        result = await client.post(path, json=body)
                        if result.is_success and isinstance(result.json(), dict) and result.json().get("original_asset"):
                            for uri in [result.json()["original_asset"]["storage_uri"], result.json()["storage_uri"]]:
                                if uri not in owned: owned.append(uri)
                        assert result.status_code == status, result.text
                        return result.json()
                    project = await post("/projects", {"project_key": "http-origin", "name": "实际HTTP来源验收"})
                    original = b"# Retention\n\nNew requirement: 9 days.\n"
                    (output / "requirements.md").write_bytes(original)
                    document = await post("/registry/api-docs/upload", {"request_id": "origin-document", "project_id": project["id"],
                        "filename": "requirements.md", "content_base64": base64.b64encode(original).decode()}, 200)
                    root = f"/projects/{project['id']}/business-environments"
                    registration = await post(root, {"request_id": "origin-registration", "environment_key": "old"})
                    selected = root + "/" + registration["id"]
                    response = await client.put(selected + "/configuration", json={"display_name": "实际七天目标", "base_url": old + "/app"})
                    assert response.status_code == 200
                    config = response.json()
                    snapshot = await post(selected + "/snapshots", capture_request(config))
                    jobs = ToolJobService(PostgresToolJobStore(settings))
                    runtime = ToolRuntimeService(tool_job_service=jobs)
                    context = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()), trace_id=str(uuid4()),
                        user_message="读取已固定目标", normalized_input="读取已固定目标", context_bundle={"project_id": project["id"]})
                    tool = ToolRegistry().get("api-test-runner")
                    calls = []
                    for key, path in [("direct", "/app/rule"), ("cross", "/app/cross")]:
                        calls.append(await runtime.execute(tool, ModelToolCall(id=key, name=tool.key, arguments={"worker_action": "execute_task",
                            "task": {"task_id": key, "method": "POST", "path": old + path, "timeout_seconds": 5}}), context))
                    inspect_path = selected + f"/snapshots/{snapshot['id']}/tool-observations/inspect"
                    requests = [{"tool_job_id": c.job_id, "output_path_tokens": ["task_result", "response_body", "days"]} for c in calls]
                    observations = [await post(inspect_path, body, 200) for body in requests]
                    assert [v["target_status"] for v in observations] == ["matched", "mismatch"]
                    assert [v["value"] for v in observations] == [7, 9]
                    assert (await client.post(inspect_path, json=requests[0], headers={"Authorization": "Bearer invalid-fixture"})).status_code == 401
                    assert [hop["method"] for hop in observations[1]["http_observation"]["hops"]] == ["POST", "GET"]
                    await processes.stop(api)
                    settings.orchestration.redis_task_dispatch_enabled = False
                    processes.env = child_environment(settings)
                    api = processes.start("http-origin-api-restarted", *args)
                    await wait_api(client, api)
                    assert [await post(inspect_path, body, 200) for body in requests] == observations
                    await post(selected + "/archive", {}, 200)
                    await post(f"/projects/{project['id']}/archive", {}, 200)
                    with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"DELETE FROM `{settings.database.application_config_table}` WHERE config_key=%s AND scope_key=%s",
                            ("project.environment_target", registration["id"]))
                        conn.commit()
                    assert [await post(inspect_path, body, 200) for body in requests] == observations
                    assert len(old_calls) == 2 and len(new_calls) == 1
                    assert (await objects.read_object_uri(document["original_asset"]["storage_uri"]))["content"] == original
                    job_records = [(await jobs.get_job(c.job_id)).model_dump(mode="json") for c in calls]
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT input_payload,output_payload,metadata FROM {settings.database.postgres_tool_job_table} ORDER BY created_at")
                        pg_jobs = [dict(row) for row in cur.fetchall()]
                    assert len(pg_jobs) == 2
                    (output / "verified.json").write_text(json.dumps({"registration": registration, "snapshot": snapshot,
                        "document": document, "source_sha256": hashlib.sha256(original).hexdigest(), "requests": requests,
                        "observations": observations, "jobs": job_records, "pg_jobs": pg_jobs,
                        "business_http_old": old_calls, "business_http_new": new_calls, "API_restart": True,
                        "archived_history_preserved": True, "current_config_removed": True, "inspection_new_http_calls": 0,
                        "external_default_model": False, "deployment_status": "unknown", "full_P0_P5_complete": False},
                        ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        finally:
            for path in tmp_path.iterdir():
                if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                    shutil.copyfile(path, output/path.name)
