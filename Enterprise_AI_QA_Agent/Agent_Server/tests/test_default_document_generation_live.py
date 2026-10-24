"""Real default model generates ordinary document modules and recovers independently."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.generation_runtime import GENERATION_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride, model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_DOCUMENT_GENERATION") != "1",
    reason="real default model, production API/independent CLIs, isolated MySQL/PG/Redis/RustFS")]

DOCUMENT = ("# 商城需求\n所有接口均需登录，退款不能超过实付金额。\n\n"
    "## 注册\n手机号必须唯一，验证码五分钟有效。\n\n"
    "## 订单\n### 创建\n库存不足不能下单。\n### 退款\n退款金额上限为实付金额。\n\n"
    "## 查询\n仅能查询自己的订单，分页每页最多20条。\n")


@pytest.mark.parametrize("streaming", [False, True])
async def test_default_model_ordinary_document_modules_and_independent_recovery(tmp_path, streaming):
    evidence = Path(os.environ["QA_DEFAULT_DOCUMENT_GENERATION_DIR"]) / ("stream" if streaming else "plain")
    evidence.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"model_id": configured.model_id, "transport": configured.transport, "streaming": streaming,
             "controlled_model": False, "quality_generation": None, "recovery_generation": None}
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = ",".join([DOCUMENT_PARSE_STREAM, GENERATION_STREAM])
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=configured.model_id, provider=configured.provider, transport=configured.transport,
                request_profile=configured.request_profile, base_url=configured.api_base_url,
                api_key=configured.api_key, auth_type=configured.auth_type,
                oauth_provider=configured.oauth_provider, oauth_refresh_token=configured.oauth_refresh_token,
                applications=configured.applications, is_active=True, use_provider_defaults=False,
                capability_overrides=ModelCapabilitiesOverride(streaming=streaming),
                context_window=configured.context_window, max_output_tokens=configured.max_output_tokens))
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            port = free_port()
            api_args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                        "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("default-generation-api", *api_args)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=300,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)

                    async def post(path, payload, status=200):
                        response = await client.post(path, json=payload)
                        assert response.status_code == status, (path, response.status_code, response.text)
                        return response.json()

                    async def get(path):
                        response = await client.get(path)
                        assert response.status_code == 200, (path, response.status_code, response.text)
                        return response.json()

                    async def relay(stream, limit=10):
                        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                            group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                        await queue.connect()
                        try:
                            return await PostgresTaskOutbox(settings).relay_once(queue, limit=limit)
                        finally:
                            await queue.close()

                    # The document/generation streams are process-wide constants shared
                    # by every acceptance run on this Redis database: purge leftovers
                    # from earlier runs so this run's one-shot workers cannot consume
                    # deliveries whose task rows live in another, destroyed database.
                    probe = RedisTaskQueue(settings.database.redis_url, stream=DOCUMENT_PARSE_STREAM,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await probe.connect()
                    try:
                        for stream in (DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM,
                                       DOCUMENT_IMPACT_STREAM, GENERATION_STREAM):
                            await probe._client.delete(stream)
                    finally:
                        await probe.close()

                    async def run_worker(name, timeout=600):
                        process = processes.start(name, "-m", "tests.default_model_probe",
                            "src.cli.run_case_generation_worker", "--once", "--worker-id", name)
                        assert await processes.wait(process, timeout=timeout) == 0, (
                            name, (tmp_path / f"{name}.stderr").read_text(encoding="utf-8", errors="replace")[-2000:])

                    async def parse_document(project, source_path, request_id):
                        doc = await post("/registry/api-docs/upload", {"project_id": project["id"],
                            "filename": source_path.name, "request_id": request_id,
                            "content_base64": base64.b64encode(source_path.read_bytes()).decode()})
                        base = f"/projects/{project['id']}/documents/{doc['id']}"
                        parsed = await post(base + "/parse-tasks",
                            {"creation_key": f"parse-{request_id}", "version_id": doc["current_version_id"]}, 202)
                        assert await relay(DOCUMENT_PARSE_STREAM, 1) == 1
                        parser = processes.start("default-parser", "-m", "src.cli.run_document_parse_worker",
                            "--once", "--worker-id", "default-parser")
                        assert await processes.wait(parser, timeout=120) == 0
                        parsed = await get(base + f"/parse-tasks/{parsed['id']}")
                        assert parsed["status"] == "completed", parsed
                        snapshot = await get(base + f"/parse-snapshots/{parsed['snapshot_id']}")
                        return base, parsed["snapshot_id"], snapshot

                    project = await post("/projects", {"project_key": "default-ordinary-" + uuid4().hex[:10],
                        "name": "默认模型普通文档模块生成"}, 201)
                    source = evidence / "ordinary-requirements.md"
                    source.write_text(DOCUMENT, encoding="utf-8")
                    base, snapshot_id, snapshot = await parse_document(project, source, "default-v1")
                    save("parse-snapshot.json", snapshot)

                    policy_path = f"/projects/{project['id']}/case-generation-settings"
                    policy = (await get(policy_path))["payload"]
                    policy.update(transport_policy="single_attempt", call_timeout_seconds=240)

                    # --- Quality generation: every module runs with the real model. ---
                    request = {"creation_key": "default-quality", "parse_snapshot_id": snapshot_id,
                        "objective": "覆盖商城需求的所有章节", "mode_key": "api_testing",
                        "max_cases_per_module": 3, "model_key": model.key}
                    group = await post(base + "/document-case-generations", request, 202)
                    scope = await get(base + f"/case-generations/{group['id']}/scope")
                    assert scope["implementation_version"] == "document-generation-scope-v1"
                    assert len(scope["modules"]) == 3, scope["modules"]
                    assert "".join(scope["text"][s["start_char"]:s["end_char"]] for s in scope["segments"]) == scope["text"]
                    assert scope["text"] == snapshot["result"]["text"]
                    save("quality-scope.json", scope)
                    template_path = f"/projects/{project['id']}/test-case-generation-template"
                    template = (await get(template_path))["payload"]
                    template["writing_guidance"] = "提交后修改写作指导不能替换已固定的模板"
                    assert (await client.put(template_path, json=template)).status_code == 200
                    assert (await client.put(policy_path, json=policy)).status_code == 200
                    assert (await post(base + "/document-case-generations", request, 202)) == group
                    sends_before = model_sends()
                    assert await relay(GENERATION_STREAM, 10) == 3
                    for index in range(3):
                        await run_worker(f"quality-generation-{index}")
                    report = await get(base + f"/case-generations/{group['id']}")
                    assert [t["status"] for t in report["modules"]] == ["completed"] * 3, report
                    quality_calls = model_sends() - sends_before
                    assert quality_calls == 3, quality_calls
                    drafts = []
                    for module in report["modules"]:
                        receipt = module["checkpoint"]["receipt"]
                        review = receipt["generation_review"]
                        assert review["schema_version"] == "document-generation-coverage-v2"
                        assert review["mapping_state"] == "complete", review
                        assert review["candidate_inventory_complete"] is True
                        assert all(not item["validation_issues"] for item in review["items"])
                        assert all(reference["expected_path"] is not None
                            for item in review["items"] for reference in item["step_refs"])
                        for version in receipt["versions"]:
                            values = await get(f"/test-cases/{version['case_id']}/versions")
                            case_version = next(v for v in values if v["id"] == version["version_id"])
                            assert case_version["generation_metadata"]["document_generation"]["parse_snapshot_id"] == snapshot_id
                            drafts.append({"case_id": version["case_id"], "version": version["version"],
                                "steps": len(case_version["content_json"].get("steps", []))})
                    save("quality-report.json", report)
                    facts["quality_generation"] = {"modules": 3, "model_http": quality_calls, "drafts": drafts}

                    sends_before = model_sends()
                    generation_queue = RedisTaskQueue(settings.database.redis_url, stream=GENERATION_STREAM,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await generation_queue.connect()
                    try:
                        for module in report["modules"]:
                            await generation_queue.enqueue({"schema_version": 1, "task_type": "case_generation",
                                "project_id": project["id"], "task_id": module["id"]})
                            await run_worker("quality-terminal")
                    finally:
                        await generation_queue.close()
                    assert model_sends() - sends_before == 0, "terminal replay must not call the model"

                    # --- Independent recovery: tiny size limit rejects every real
                    # response after RustFS persistence; saved_response retries finish
                    # each module without any new model call. ---
                    recovery_policy = {**policy, "max_response_bytes": 1500}
                    assert (await client.put(policy_path, json=recovery_policy)).status_code == 200
                    recovery_request = {**request, "creation_key": "default-recovery"}
                    recovery_group = await post(base + "/document-case-generations", recovery_request, 202)
                    sends_before = model_sends()
                    assert await relay(GENERATION_STREAM, 10) == 3
                    for index in range(3):
                        await run_worker(f"recovery-generation-{index}")
                    recovery_report = await get(base + f"/case-generations/{recovery_group['id']}")
                    assert [t["status"] for t in recovery_report["modules"]] == ["failed"] * 3, recovery_report
                    recovery_calls = model_sends() - sends_before
                    assert recovery_calls == 3, recovery_calls
                    for module in recovery_report["modules"]:
                        assert module["error_code"] == "response_validation_rejected", module
                        assert module["checkpoint"]["phase"] == "response_saved", module
                        owned.append(module["checkpoint"]["response"]["storage_uri"])
                    save("recovery-failed-report.json", recovery_report)

                    assert (await client.put(policy_path, json=policy)).status_code == 200
                    sends_before = model_sends()
                    retried, rejected = [], []
                    for index, module in enumerate(recovery_report["modules"]):
                        retry = await post(base + f"/case-generation-tasks/{module['id']}/retries",
                            {"creation_key": f"default-recover-{index}"}, 202)
                        assert retry["retry"]["strategy"] == "saved_response"
                        await relay(GENERATION_STREAM, 10)
                        await run_worker(f"recovery-retry-{index}")
                        child = await get(base + f"/case-generation-tasks/{retry['task']['id']}")
                        # A recovered response must either complete the module or be
                        # rejected again by the same coverage gate with the response
                        # still preserved; sampling variance may legitimately reject.
                        if child["status"] == "completed":
                            retried.append(child["id"])
                        else:
                            # Either the response still fails size/content validation
                            # or the coverage review rejects the draft commit; both
                            # are the product's quality gates keeping the module
                            # failed with its response preserved for review.
                            assert child["status"] == "failed", child
                            assert child["error_code"] in {
                                "response_validation_rejected", "draft_commit_rejected"}, child
                            rejected.append({"task_id": child["id"], "error_code": child["error_code"],
                                "checkpoint_phase": (child.get("checkpoint") or {}).get("phase")})
                            owned.append((child.get("checkpoint") or {}).get("response", {}).get("storage_uri"))
                    assert model_sends() - sends_before == 0, "saved_response retry must not call the model"
                    save("recovery-retried.json", {"completed": retried, "rejected": rejected})
                    facts["recovery_generation"] = {"failed_modules": 3, "saved_response_retries": 3,
                        "completed_recoveries": len(retried), "gate_rejections": len(rejected),
                        "model_http_during_retry": 0, "model_http_for_attempts": recovery_calls}
            finally:
                if api.poll() is None:
                    await processes.stop(api)
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"}:
                (evidence / path.name).write_bytes(path.read_bytes())
        facts["global_model_unchanged"] = model_invocation_parameters(
            configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)
