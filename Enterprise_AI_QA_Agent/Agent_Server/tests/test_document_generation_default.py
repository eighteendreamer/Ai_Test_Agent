"""Real default model, ordinary modules and independent configuration recovery."""
import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.generation_runtime import GENERATION_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_DOCUMENT_GENERATION") != "1",
    reason="actual default provider and isolated production API/CLI/MySQL/PG/RustFS/Redis")
async def test_default_model_ordinary_modules_and_independent_configuration_recovery(tmp_path):
    evidence = Path(os.environ["QA_DEFAULT_DOCUMENT_GENERATION_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    original = MySQLModelConfigStore(get_settings()).get_default_active()
    assert original is not None and original.transport == "openai_chat_completions", "SDK probe scope is OpenAI-compatible"
    parameters = model_invocation_parameters(original)
    report = {"scope": "real default model; ordinary Markdown; independent workers; no business execution",
        "full_goal_complete": False, "model_parameters": parameters, "controlled_model": False,
        "module_generation_complete": False, "independent_recovery_complete": False}

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def sdk_calls():
        return len(list((evidence / "physical").glob("*.sdk-create.json")))

    source = evidence / "requirements.md"
    source.write_text("# 任务系统业务需求\n所有写操作要求登录；未登录必须拒绝，不能增加记录。未指定具体接口路径、状态码或页面元素，不得编造。\n\n"
        "## 注册\n邮箱必须唯一。首次注册成功后可以登录；重复邮箱注册必须拒绝，已有账号不变。\n\n"
        "## 任务创建\n标题必填。合法创建后返回任务，初始业务JSON包含{\"done\":false,\"retry_count\":0,\"deleted_at\":null}。"
        "空标题必须拒绝，不增加任务；测试结束清理本次创建的任务。\n\n"
        "## 任务查询\n只返回当前用户自己的任务。分页每页最多20条，超出上限必须拒绝。"
        "不能泄露其他用户的任务，空结果是合法状态。\n", encoding="utf-8")
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.orchestration.redis_task_outbox_streams = ",".join((DOCUMENT_PARSE_STREAM, GENERATION_STREAM))
            models = MySQLModelConfigStore(settings)
            models.initialize()
            clone = ModelConfigUpdateRequest(model_name=original.model_id, provider=original.provider,
                transport=original.transport, request_profile=original.request_profile,
                base_url=original.api_base_url, api_key=original.api_key,
                auth_type=original.auth_type, oauth_provider=original.oauth_provider,
                oauth_refresh_token=original.oauth_refresh_token, applications=original.applications,
                is_active=True, use_provider_defaults=False, capability_overrides=original.capability_overrides,
                context_window=original.context_window, max_output_tokens=original.max_output_tokens)
            models.upsert(clone)
            assert model_invocation_parameters(models.get_default_active()) == parameters
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            port = free_port()
            args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("generation-default-api", *args)
            queues = {}
            try:
                for stream, suffix in ((DOCUMENT_PARSE_STREAM, "document_parsing"), (GENERATION_STREAM, "case_generation")):
                    queues[stream] = RedisTaskQueue(settings.database.redis_url, stream=stream,
                        group=settings.orchestration.redis_task_consumer_group + ":" + suffix)
                    await queues[stream].connect()
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=180) as client:
                    await wait_api(client, api)

                    async def call(method, path, data=None, status=200):
                        response = await client.request(method, path, **({"json": data} if data is not None else {}))
                        assert response.status_code == status, (path, response.status_code, response.text)
                        return response.json()

                    async def worker(name, stream=GENERATION_STREAM):
                        await PostgresTaskOutbox(settings).relay_once(queues[stream])
                        module = "src.cli.run_document_parse_worker" if stream == DOCUMENT_PARSE_STREAM else "src.cli.run_case_generation_worker"
                        child = processes.start(name, "-m", "tests.default_model_probe", module, "--once", "--worker-id", name)
                        assert await processes.wait(child, timeout=150) == 0

                    project = await call("POST", "/projects", {"project_key": "default-gen-" + uuid4().hex, "name": "默认模型普通需求全文生成"}, 201)
                    root = f"/projects/{project['id']}"
                    doc = await call("POST", "/registry/api-docs/upload", {"project_id": project["id"], "filename": source.name,
                        "request_id": "default-generation-source", "content_base64": base64.b64encode(source.read_bytes()).decode()})
                    owned.append(doc["original_asset"]["storage_uri"])
                    assert (await objects.read_object_uri(owned[-1]))["content"] == source.read_bytes()
                    save("document.json", doc)
                    base = root + f"/documents/{doc['id']}"
                    parsed = await call("POST", base + "/parse-tasks", {"creation_key": "default-generation-parse", "version_id": doc["current_version_id"]}, 202)
                    await worker("generation-default-parser", DOCUMENT_PARSE_STREAM)
                    parsed = await call("GET", base + "/parse-tasks/" + parsed["id"])
                    assert parsed["status"] == "completed"
                    snapshot = await call("GET", base + "/parse-snapshots/" + parsed["snapshot_id"])
                    owned.append(snapshot["snapshot"]["storage_uri"])
                    save("parse.json", snapshot)
                    save("generation-config.json", await call("GET", root + "/case-generation-settings"))
                    request = {"creation_key": "default-generation", "parse_snapshot_id": parsed["snapshot_id"],
                        "objective": "为全部章节生成可评审用例。每模块最多3条，覆盖本模块正常、异常和明确边界，保留全局负向条件及false/0/null。未提供的技术细节写警告。",
                        "mode_key": "api_testing", "max_cases_per_module": 3}
                    group = await call("POST", base + "/document-case-generations", request, 202)
                    scope = await call("GET", base + f"/case-generations/{group['id']}/scope")
                    save("scope.json", scope)
                    assert scope["authoring"]["model_parameters"] == parameters
                    assert all(m["generation"].get("response_transport_protocol") == "case-generation-stream-response-v1" for m in scope["modules"])
                    assert [s["title"] for s in scope["segments"]] == ["注册", "任务创建", "任务查询"]
                    assert "".join(scope["text"][s["start_char"]:s["end_char"]] for s in scope["segments"]) == snapshot["result"]["text"]
                    assert sdk_calls() == 0
                    await worker("generation-default-module-0")
                    progress = await call("GET", base + f"/case-generations/{group['id']}")
                    save("progress-first.json", progress)
                    assert [m["status"] for m in progress["modules"]] == ["completed", "queued", "queued"], progress
                    assert sdk_calls() == 1
                    # Configuration drift is an explicit preflight fault, not a
                    # provider/process crash. No extra HTTP may be sent here.
                    models.upsert(clone.model_copy(update={"max_output_tokens": 1}))
                    await worker("generation-default-drift")
                    failed = await call("GET", base + f"/case-generations/{group['id']}")
                    save("progress-drift.json", failed)
                    assert [m["status"] for m in failed["modules"]] == ["completed", "failed", "queued"]
                    assert failed["modules"][1]["error_code"] == "input_budget_or_configuration_rejected" and sdk_calls() == 1
                    models.upsert(clone)
                    assert model_invocation_parameters(models.get_default_active()) == parameters
                    await processes.stop(api)
                    api = processes.start("generation-default-api-restart", *args)
                    await wait_api(client, api)
                    assert await call("POST", base + "/document-case-generations", request, 202) == group
                    await worker("generation-default-module-2")
                    final = await call("GET", base + f"/case-generations/{group['id']}")
                    save("progress-final-original.json", final)
                    assert [m["status"] for m in final["modules"]] == ["completed", "failed", "completed"] and sdk_calls() == 2
                    retry = await call("POST", base + f"/case-generation-tasks/{failed['modules'][1]['id']}/retries",
                        {"creation_key": "default-independent-recovery"}, 202)
                    assert retry["retry"]["strategy"] == "frozen_request"
                    await worker("generation-default-retry")
                    recovered = await call("GET", base + "/case-generation-tasks/" + retry["task"]["id"])
                    save("retry.json", {"submission": retry, "terminal": recovered})
                    assert recovered["status"] == "completed" and sdk_calls() == 3
                    assert await call("GET", base + f"/case-generations/{group['id']}") == final
                    versions = []
                    for task in (final["modules"][0], recovered, final["modules"][2]):
                        response = await call("GET", base + f"/case-generation-tasks/{task['id']}/response")
                        save(f"response-public-{task['module_id']}.json", {k: response[k] for k in ("text", "response_summary", "tool_calls")})
                        assert response["response_summary"]["mode"] == "ok" and not response["tool_calls"]
                        for ref in task["checkpoint"]["receipt"]["versions"]:
                            values = await call("GET", f"/test-cases/{ref['case_id']}/versions")
                            version = next(v for v in values if v["id"] == ref["version_id"])
                            bundle = await call("GET", f"/test-cases/{ref['case_id']}")
                            assert bundle["case"]["lifecycle_status"] == "draft" and bundle["case"]["active_version_id"] is None
                            assert version["content_json"] and version["source_refs"][0]["source_id"] == parsed["snapshot_id"]
                            provenance = version["generation_metadata"]["document_generation"]
                            assert provenance["scope_sha256"] == scope["sha256"] and provenance["segment"]["module_id"] == task["module_id"]
                            versions.append(version)
                    save("versions.json", versions)
                    for task in (*final["modules"], recovered):
                        await queues[GENERATION_STREAM].enqueue({"schema_version": 1, "task_type": "case_generation",
                            "project_id": project["id"], "task_id": task["id"]})
                        await worker("generation-default-terminal-" + task["id"][:6])
                    assert sdk_calls() == 3
                    foreign = await call("POST", "/projects", {"project_key": "foreign-gen-" + uuid4().hex, "name": "隔离验证"}, 201)
                    assert (await client.get(f"/projects/{foreign['id']}/documents/{doc['id']}/case-generations/{group['id']}/scope")).status_code == 404
                    report.update(module_generation_complete=True, independent_recovery_complete=True,
                        generated_drafts=len(versions), terminal_extra_sdk_calls=0)
            finally:
                for queue in queues.values():
                    await queue.close()
                references = []
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    for suffix in ("_case_gen_groups", "_case_gen_tasks", "_case_gen_retries"):
                        table = settings.database.postgres_api_doc_table + suffix
                        cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                        if not cur.fetchone()["existing"]:
                            continue
                        cur.execute(f"SELECT * FROM {table}")
                        rows = [dict(row) for row in cur.fetchall()]
                        save("PG" + suffix + ".json", rows)
                        for row in rows:
                            ref = row["record"].get("scope") if suffix == "_case_gen_groups" else (row.get("checkpoint") or {}).get("response")
                            if ref and ref["storage_uri"] not in owned:
                                owned.append(ref["storage_uri"])
                            if ref and ref not in references:
                                references.append(ref)
                downloads = []
                for index, ref in enumerate(references):
                    raw = (await objects.read_object_uri(ref["storage_uri"]))["content"]
                    assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                    value = json.loads(raw)
                    # Stream results contain public text and aggregate metadata;
                    # no provider's private reasoning body is exported.
                    if "raw_response" in value:
                        assert not any(key in value["raw_response"] for key in ("reasoning", "reasoning_content", "choices"))
                    (evidence / f"original-{index}.json").write_bytes(raw)
                    downloads.append(ref)
                save("downloads.json", downloads)
                await processes.close()
    finally:
        assert MySQLModelConfigStore(get_settings()).get_default_active() == original, "Global default model changed"
        report.update(global_model_unchanged=True, actual_SDK_calls=sdk_calls())
        save("verification.json", report)
        for path in tmp_path.glob("generation-default-*.std*"):
            (evidence / path.name).write_bytes(path.read_bytes())
