"""Independent production API lifespan/restart, real stores, no target probes."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.runtime.postgres_business_environment_store import PostgresBusinessEnvironmentStore
from src.schemas.business_environment import TARGET_CONFIG_KEY
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api


@contextmanager
def target_sentinel():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self):
            calls.append([self.command, self.path])
            self.send_response(500)
            self.end_headers()
        do_GET = reply
        do_POST = reply
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_BUSINESS_ENVIRONMENT_PRODUCTION") != "1",
    reason="independent production API, real isolated MySQL/PG/Redis/RustFS")
async def test_actual_production_registration_configuration_capture_restart_and_archive(tmp_path):
    output = Path(os.getenv("QA_ENVIRONMENT_PRODUCTION_EVIDENCE_DIR", str(tmp_path / "evidence")))
    output.mkdir(parents=True, exist_ok=True)
    with target_sentinel() as (target, target_calls):
        try:
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.api_auth_token = SecretStr(uuid4().hex)
                models = MySQLModelConfigStore(settings)
                models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name="no-invocation-environment-fixture", provider="openai",
                    transport="openai_chat_completions", base_url=target + "/v1", api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False),
                    context_window=128000, max_output_tokens=1024))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("business-environment-api-first", *args)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=30,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)
                    async def post(path, body, expected=201):
                        result = await client.post(path, json=body)
                        if result.is_success:
                            payload = result.json()
                            if isinstance(payload, dict) and payload.get("original_asset"):
                                for uri in [payload["original_asset"]["storage_uri"], payload["storage_uri"]]:
                                    if uri not in owned:
                                        owned.append(uri)
                        assert result.status_code == expected, result.text
                        return result.json()
                    project = await post("/projects", {"project_key": "environment-production", "name": "正式环境API验收"})
                    other = await post("/projects", {"project_key": "other", "name": "另一个项目"})
                    original = b"# Target evidence\n\nRetention: 7 days.\n"
                    source_path = output / "requirements.md"
                    source_path.write_bytes(original)
                    document = await post("/registry/api-docs/upload", {"request_id": "original-doc", "project_id": project["id"],
                        "filename": "requirements.md", "content_base64": base64.b64encode(original).decode()}, expected=200)
                    root = f"/projects/{project['id']}/business-environments"
                    assert (await client.get(root, headers={"Authorization": "Bearer invalid-fixture"})).status_code == 401
                    registration_request = {"request_id": "production-registration", "environment_key": "stage"}
                    registered = await post(root, registration_request)
                    selected = root + "/" + registered["id"]
                    missing = await client.get(selected + "/configuration")
                    assert missing.status_code == 409
                    payload = {"display_name": "正式目标配置", "base_url": target + "/application", "description": "尚无部署证据"}
                    configured = await client.put(selected + "/configuration", json=payload)
                    assert configured.status_code == 200
                    config = configured.json()
                    request = {"request_id": "production-capture", "expected_config_revision": config["revision"],
                        "expected_config_sha256": config["sha256"]}
                    frozen = await post(selected + "/snapshots", request)
                    assert frozen["deployment_status"] == "unknown"
                    changed = await client.put(selected + "/configuration", json={**payload, "base_url": target + "/other-target"})
                    assert changed.status_code == 200 and changed.json()["revision"] == 2
                    assert (await client.post(selected + "/snapshots", json={**request, "request_id": "stale-new"})).status_code == 409
                    assert (await client.get(selected.replace(project["id"], other["id"]) + f"/snapshots/{frozen['id']}")).status_code == 404
                    store = PostgresBusinessEnvironmentStore(settings)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT record FROM {store._snapshots}")
                        rows = [dict(row) for row in cur.fetchall()]
                        assert rows == [{"record": frozen}]
                    with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT scope_key,payload_json,revision FROM `{settings.database.application_config_table}` WHERE config_key=%s", (TARGET_CONFIG_KEY,))
                        mysql_rows = [dict(row) for row in cur.fetchall()]
                        assert len(mysql_rows) == 1 and mysql_rows[0]["revision"] == 2
                    await processes.stop(api)
                    settings.orchestration.redis_task_dispatch_enabled = False
                    processes.env = child_environment(settings)
                    api = processes.start("business-environment-api-restarted", *args)
                    await wait_api(client, api)
                    assert (await client.get(selected + "/snapshots/by-request", params={"request_id": request["request_id"]})).json() == frozen
                    assert (await client.post(selected + "/snapshots", json=request)).json() == frozen
                    assert (await client.post(selected + "/archive")).status_code == 200
                    assert (await client.post(f"/projects/{project['id']}/archive")).status_code == 200
                    with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"DELETE FROM `{settings.database.application_config_table}` WHERE config_key=%s", (TARGET_CONFIG_KEY,))
                        conn.commit()
                    assert (await client.get(selected + f"/snapshots/{frozen['id']}")).json() == frozen
                    assert (await client.post(selected + "/snapshots", json=request)).json() == frozen
                    assert (await client.post(root, json=registration_request)).json()["id"] == registered["id"]
                    assert (await client.post(selected + "/snapshots", json={**request, "request_id": "new-after-archive"})).status_code == 409
                    assert (await client.put(selected + "/configuration", json=payload)).status_code == 409
                    assert (await objects.read_object_uri(document["original_asset"]["storage_uri"]))["content"] == original
                    assert target_calls == []
                    (output / "verified.json").write_text(json.dumps({"registration": registered, "configuration": config,
                        "later_configuration": changed.json(), "fixed_snapshot": frozen, "pg_snapshots": rows,
                        "mysql_current_before_removal": mysql_rows, "document": document,
                        "source_sha256": hashlib.sha256(original).hexdigest(), "target_or_model_requests": target_calls,
                        "production_API_restart": True, "current_config_removed": True, "deployment_status": "unknown",
                        "environment_bound_to_plan_or_run": False, "autonomous_agent_execution": False,
                        "full_P0_P5_complete": False}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        finally:
            for path in tmp_path.iterdir():
                if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                    shutil.copyfile(path, output/path.name)
