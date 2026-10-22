"""A real non-default provider through ordinary generation and module recovery.

The test copies a configured model into isolated MySQL, then exercises the
production API, parser worker, generation worker, PostgreSQL receipts, and
RustFS objects. It never activates or edits the user's global model config.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
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


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_ALTERNATE_DOCUMENT_GENERATION") != "1",
        reason="requires an explicitly selected real model and isolated MySQL/PostgreSQL/RustFS/Redis",
    ),
]


def safe_config_identity(config) -> str:
    """Fingerprint credentials for invariance checks without exposing them."""
    payload = {
        "parameters": model_invocation_parameters(config),
        "api_key_sha256": hashlib.sha256((config.api_key or "").encode()).hexdigest(),
        "oauth_refresh_token_sha256": hashlib.sha256(
            (config.oauth_refresh_token or "").encode()
        ).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def test_alternate_model_generates_and_persists_two_document_modules(tmp_path):
    evidence = Path(os.environ["QA_ALTERNATE_DOCUMENT_GENERATION_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    model_name = os.environ["QA_ALTERNATE_DOCUMENT_GENERATION_MODEL_NAME"]
    source_models = MySQLModelConfigStore(get_settings())
    global_default_before = source_models.get_default_active()
    selected = source_models.get_by_name(model_name)
    assert selected.transport == "openai_chat_completions", "this live probe is for the verified Chat transport"
    global_default_identity = safe_config_identity(global_default_before)
    selected_identity = safe_config_identity(selected)

    report = {
        "scope": "real alternate model; two Markdown modules; API restart and independent configuration recovery",
        "full_goal_complete": False,
        "controlled_model": False,
        "model_key": selected.key,
        "provider": selected.provider,
        "transport": selected.transport,
        "model_parameters": model_invocation_parameters(selected),
        "global_default_unchanged": False,
        "actual_sdk_calls": 0,
        "generation_completed": False,
        "independent_recovery_complete": False,
    }

    def save(name, value):
        (evidence / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    physical = evidence / "physical"
    physical.mkdir()
    source = evidence / "alternate-requirements.md"
    source.write_text(
        "# 订单系统需求\n"
        "## 订单查询\n"
        "查询接口只能返回当前用户自己的订单；订单不存在时返回明确错误，且不得创建或修改订单。\n\n"
        "## 订单创建\n"
        "登录用户只能创建归属自己的订单；标题必填，空标题必须拒绝且不得创建订单；成功创建后返回系统生成的订单编号。\n",
        encoding="utf-8",
    )

    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.orchestration.redis_task_outbox_streams = ",".join(
                (DOCUMENT_PARSE_STREAM, GENERATION_STREAM)
            )
            isolated_models = MySQLModelConfigStore(settings)
            isolated_models.initialize()
            clone_request = ModelConfigUpdateRequest(
                model_name=selected.model_id,
                provider=selected.provider,
                transport=selected.transport,
                request_profile=selected.request_profile,
                base_url=selected.api_base_url,
                api_key=selected.api_key,
                auth_type=selected.auth_type,
                oauth_provider=selected.oauth_provider,
                oauth_refresh_token=selected.oauth_refresh_token,
                applications=selected.applications,
                is_active=True,
                use_provider_defaults=False,
                capability_overrides=selected.capability_overrides,
                context_window=selected.context_window,
                max_output_tokens=min(selected.max_output_tokens, 4096),
            )
            isolated_public = isolated_models.upsert(clone_request)
            isolated_model = isolated_models.get_by_name(selected.model_id)
            assert isolated_model.is_active
            assert isolated_model.key == isolated_public.key
            report["isolated_model_key"] = isolated_model.key
            report["isolated_model_parameters"] = model_invocation_parameters(isolated_model)

            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(physical)
            port = free_port()
            api_args = (
                "-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                "--host", "127.0.0.1", "--port", str(port),
            )
            api = processes.start("alternate-generation-api", *api_args)
            queues = {}
            try:
                for stream, suffix in (
                    (DOCUMENT_PARSE_STREAM, "document_parsing"),
                    (GENERATION_STREAM, "case_generation"),
                ):
                    queues[stream] = RedisTaskQueue(
                        settings.database.redis_url,
                        stream=stream,
                        group=settings.orchestration.redis_task_consumer_group + ":" + suffix,
                    )
                    await queues[stream].connect()

                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}/api/v1", timeout=180
                ) as client:
                    await wait_api(client, api)

                    async def call(method, path, payload=None, expected=200):
                        response = await client.request(
                            method, path, **({"json": payload} if payload is not None else {})
                        )
                        if response.status_code != expected:
                            save("last-http-error.json", {
                                "method": method,
                                "path": path,
                                "status": response.status_code,
                                "body_size_bytes": len(response.content),
                                "body_sha256": hashlib.sha256(response.content).hexdigest(),
                                "body_saved": False,
                            })
                        assert response.status_code == expected, (response.status_code, response.text)
                        return response.json()

                    project = await call("POST", "/projects", {
                        "project_key": "alternate-generation-" + uuid4().hex,
                        "name": "替代模型文档生成验收",
                    }, 201)
                    doc = await call("POST", "/registry/api-docs/upload", {
                        "project_id": project["id"],
                        "filename": source.name,
                        "request_id": "alternate-generation-upload",
                        "content_base64": base64.b64encode(source.read_bytes()).decode(),
                    })
                    owned.append(doc["original_asset"]["storage_uri"])
                    doc_base = f"/projects/{project['id']}/documents/{doc['id']}"

                    async def run_worker(name, stream=GENERATION_STREAM):
                        await PostgresTaskOutbox(settings).relay_once(queues[stream])
                        module = (
                            "src.cli.run_document_parse_worker"
                            if stream == DOCUMENT_PARSE_STREAM
                            else "src.cli.run_case_generation_worker"
                        )
                        process = processes.start(
                            name, "-m", "tests.default_model_probe", module,
                            "--once", "--worker-id", name,
                        )
                        assert await processes.wait(process, timeout=150) == 0

                    parse_task = await call("POST", doc_base + "/parse-tasks", {
                        "creation_key": "alternate-generation-parse",
                        "version_id": doc["current_version_id"],
                    }, 202)
                    await run_worker("alternate-document-parser", DOCUMENT_PARSE_STREAM)
                    parse_task = await call("GET", doc_base + "/parse-tasks/" + parse_task["id"])
                    assert parse_task["status"] == "completed", parse_task
                    parsed = await call(
                        "GET", doc_base + "/parse-snapshots/" + parse_task["snapshot_id"]
                    )
                    owned.append(parsed["snapshot"]["storage_uri"])
                    assert "当前用户自己的订单" in parsed["result"]["text"]
                    assert "标题必填" in parsed["result"]["text"]
                    assert not list(physical.glob("*.sdk-create.json")), "parsing must not call the model"

                    settings_path = f"/projects/{project['id']}/case-generation-settings"
                    policy = (await call("GET", settings_path))["payload"]
                    policy.update(transport_policy="single_attempt", call_timeout_seconds=60)
                    await call("PUT", settings_path, policy)

                    request = {
                        "creation_key": "alternate-generation-two-modules",
                        "parse_snapshot_id": parsed["snapshot"]["id"],
                        "objective": (
                            "每个需求模块只生成一条待评审 JSON 用例，覆盖本模块明确的行为及负向条件；"
                            "不编造 URL、状态码或页面元素，不执行测试。"
                        ),
                        "mode_key": "api_testing",
                        "model_key": isolated_model.key,
                        "max_cases_per_module": 1,
                    }
                    group = await call(
                        "POST", doc_base + "/document-case-generations", request, 202
                    )
                    scope = await call(
                        "GET", doc_base + f"/case-generations/{group['id']}/scope"
                    )
                    save("fixed-scope.json", scope)
                    assert scope["authoring"]["model_key"] == isolated_model.key
                    assert len(scope["modules"]) == len(scope["segments"]) == 2
                    assert [segment["title"] for segment in scope["segments"]] == [
                        "订单查询",
                        "订单创建",
                    ]
                    assert scope["parse_snapshot"]["id"] == parsed["snapshot"]["id"]
                    assert not list(physical.glob("*.sdk-create.json")), "submission freezes scope without inference"

                    tasks = []
                    first_versions = None
                    for module_index in range(2):
                        if module_index == 1:
                            # Use the same preflight failure as the default-model
                            # test: change only this fixture's MySQL config.
                            isolated_models.upsert(clone_request.model_copy(update={"max_output_tokens": 1}))
                            await run_worker("alternate-generation-configuration-drift")
                            failed = await call("GET", doc_base + f"/case-generations/{group['id']}")
                            save("generation-configuration-drift.json", failed)
                            assert [item["status"] for item in failed["modules"]] == ["completed", "failed"], failed
                            parent = failed["modules"][1]
                            assert parent["error_code"] == "input_budget_or_configuration_rejected"
                            assert parent["checkpoint"] is None
                            assert failed["modules"][0] == tasks[0]
                            assert len(list(physical.glob("*.sdk-create.json"))) == 1
                            isolated_models.upsert(clone_request)
                            assert model_invocation_parameters(isolated_models.get_by_name(selected.model_id)) == report["isolated_model_parameters"]
                            await processes.stop(api)
                            api = processes.start("alternate-generation-api-restart", *api_args)
                            await wait_api(client, api)
                            assert await call("POST", doc_base + "/document-case-generations", request, 202) == group
                            assert await call("GET", doc_base + f"/case-generations/{group['id']}/scope") == scope
                            retry_path = doc_base + f"/case-generation-tasks/{parent['id']}/retries"
                            retry_request = {"creation_key": "alternate-generation-independent-recovery"}
                            retry = await call("POST", retry_path, retry_request, 202)
                            assert retry["retry"]["strategy"] == "frozen_request"
                            assert retry["retry"]["parent_task_id"] == parent["id"]
                            assert retry["task"]["id"] != parent["id"]
                            assert retry["task"]["module_id"] == parent["module_id"]
                            assert await call("POST", retry_path, retry_request, 202) == retry
                            assert len(list(physical.glob("*.sdk-create.json"))) == 1
                        await run_worker(f"alternate-generation-module-{module_index}")
                        progress = await call(
                            "GET", doc_base + f"/case-generations/{group['id']}"
                        )
                        save(f"generation-progress-{module_index}.json", progress)
                        if module_index == 0:
                            task = progress["modules"][0]
                        else:
                            task = await call("GET", doc_base + f"/case-generation-tasks/{retry['task']['id']}")
                            save("generation-retry.json", {"submission": retry, "terminal": task})
                            assert progress == failed, "Retry must preserve the original failed group and successful sibling"
                        failed_task = task if task["status"] == "failed" else None
                        if failed_task is not None:
                            failed_response = await call(
                                "GET",
                                doc_base
                                + f"/case-generation-tasks/{failed_task['id']}/response",
                            )
                            response_text = str(failed_response.get("text") or "")
                            save(
                                "response-validation-diagnostic.json",
                                {
                                    "task_id": failed_task["id"],
                                    "error_code": failed_task["error_code"],
                                    "response_summary": failed_response.get("response_summary"),
                                    "text_size_bytes": len(response_text.encode("utf-8")),
                                    "text_sha256": hashlib.sha256(
                                        response_text.encode("utf-8")
                                    ).hexdigest(),
                                    "text": response_text,
                                },
                            )
                        assert task["status"] == "completed", task
                        tasks.append(task)
                        if module_index == 0:
                            assert [item["status"] for item in progress["modules"]] == ["completed", "queued"]
                            first_case_id = task["checkpoint"]["receipt"]["versions"][0]["case_id"]
                            first_versions = await call("GET", f"/test-cases/{first_case_id}/versions")
                            save("first-module-before-recovery.json", first_versions)

                    result = await call(
                        "GET", doc_base + f"/case-generations/{group['id']}"
                    )
                    save("generation-result.json", result)
                    assert [item["status"] for item in result["modules"]] == [
                        "completed",
                        "failed",
                    ], result
                    assert result == failed
                    assert await call("GET", f"/test-cases/{first_case_id}/versions") == first_versions
                    replayed_retry = await call("POST", retry_path, retry_request, 202)
                    assert replayed_retry["retry"] == retry["retry"] and replayed_retry["task"] == tasks[1]
                    for task in tasks:
                        response = await call(
                            "GET", doc_base + f"/case-generation-tasks/{task['id']}/response"
                        )
                        assert response["response_summary"]["mode"] == "ok"
                        assert not response["tool_calls"]
                    generation_reviews = []
                    for module, task in zip(scope["modules"], tasks):
                        review = task["checkpoint"]["receipt"]["generation_review"]
                        candidates = json.loads(module["generation"]["invocation"]["messages"][0]["content"])[
                            "evidence_context"]["document_generation"]["coverage_targets"]
                        assert review["schema_version"] == "document-generation-coverage-v2"
                        assert review["review_state"] == "required"
                        assert review["module_id"] == task["module_id"]
                        assert review["parse_snapshot_id"] == parsed["snapshot"]["id"]
                        assert [item["candidate_id"] for item in review["items"]] == [item["id"] for item in candidates]
                        assert review["mapping_state"] in {"complete", "incomplete"}
                        (evidence / f"generation-review-{task['module_id']}.json").write_text(
                            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        generation_reviews.append(review)
                    references = [group["scope"], *[task["checkpoint"]["response"] for task in tasks]]
                    for index, reference in enumerate(references):
                        raw = (await objects.read_object_uri(reference["storage_uri"]))["content"]
                        assert len(raw) == reference["size_bytes"]
                        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
                        parsed_object = json.loads(raw)
                        if "raw_response" in parsed_object:
                            assert not any(
                                key in parsed_object["raw_response"]
                                for key in ("reasoning", "reasoning_content", "choices")
                            )
                        (evidence / f"object-{index}.json").write_bytes(raw)

                    versions = []
                    for module_index, task in enumerate(tasks):
                        receipt_versions = task["checkpoint"]["receipt"]["versions"]
                        assert len(receipt_versions) == 1
                        reference = receipt_versions[0]
                        cases = await call("GET", f"/test-cases/{reference['case_id']}/versions")
                        version = next(item for item in cases if item["id"] == reference["version_id"])
                        case = await call("GET", f"/test-cases/{reference['case_id']}")
                        assert case["lifecycle_status"] == "draft"
                        assert case["active_version_id"] is None
                        assert isinstance(version["content_json"], dict) and version["content_json"]
                        objective = str(version["content_json"].get("objective") or "")
                        expected_terms = ("查询",) if module_index == 0 else ("创建", "新增", "生成")
                        assert any(term in objective for term in expected_terms), objective
                        assert version["source_refs"][0]["source_id"] == parsed["snapshot"]["id"]
                        provenance = version["generation_metadata"]["document_generation"]
                        assert provenance["scope_sha256"] == scope["sha256"]
                        assert provenance["segment"]["module_id"] == task["module_id"]
                        if module_index == 1:
                            assert provenance["retry"] == {
                                "id": retry["retry"]["id"], "sha256": retry["retry"]["sha256"]}
                        versions.append(version)
                        save(f"draft-version-{module_index}.json", version)

                    sdk_files = sorted(physical.glob("*.sdk-create.json"))
                    assert len(sdk_files) == 2, [path.name for path in sdk_files]
                    sdk_records = [json.loads(path.read_bytes()) for path in sdk_files]
                    assert all(record["sdk_create_returned"] is True for record in sdk_records)
                    assert all(len(record["physical_requests"]) == 1 for record in sdk_records), sdk_records
                    for module_index, task in enumerate([*tasks, parent]):
                        await queues[GENERATION_STREAM].enqueue({
                            "schema_version": 1,
                            "task_type": "case_generation",
                            "project_id": project["id"],
                            "task_id": task["id"],
                        })
                        await run_worker(f"alternate-generation-terminal-{module_index}")
                    assert len(list(physical.glob("*.sdk-create.json"))) == 2
                    assert await call("GET", doc_base + f"/case-generations/{group['id']}") == result
                    assert await call("GET", doc_base + f"/case-generation-tasks/{tasks[1]['id']}") == tasks[1]
                    assert await call("GET", f"/test-cases/{first_case_id}/versions") == first_versions
                    report.update(
                        generation_completed=True,
                        independent_recovery_complete=True,
                        recovery_fault="isolated_model_configuration_drift_before_send",
                        api_restarted=True,
                        original_group_statuses=[item["status"] for item in result["modules"]],
                        original_failed_task_preserved=True,
                        successful_sibling_versions_unchanged=True,
                        retry_id=retry["retry"]["id"],
                        retry_task_id=tasks[1]["id"],
                        retry_strategy=retry["retry"]["strategy"],
                        configuration_fault_sdk_calls=0,
                        duplicate_retry_extra_sdk_calls=0,
                        group_id=group["id"],
                        parse_snapshot_id=parsed["snapshot"]["id"],
                        module_count=2,
                        draft_version_ids=[version["id"] for version in versions],
                        generation_reviews=generation_reviews,
                        physical_requests=[
                            request
                            for record in sdk_records
                            for request in record["physical_requests"]
                        ],
                        actual_sdk_calls=len(sdk_files),
                        terminal_replay_model_calls=0,
                    )
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
                            record = row.get("record") or {}
                            reference = record.get("scope") if suffix == "_case_gen_groups" else (row.get("checkpoint") or {}).get("response")
                            if reference and reference["storage_uri"] not in owned:
                                owned.append(reference["storage_uri"])
                            if reference and reference not in references:
                                references.append(reference)
                await processes.close()
    finally:
        global_default_after = MySQLModelConfigStore(get_settings()).get_default_active()
        assert safe_config_identity(global_default_after) == global_default_identity
        assert safe_config_identity(MySQLModelConfigStore(get_settings()).get_by_name(model_name)) == selected_identity
        report["global_default_unchanged"] = True
        report["global_selected_model_unchanged"] = True
        report["actual_sdk_calls"] = len(list(physical.glob("*.sdk-create.json")))
        report["full_goal_complete"] = False
        save("verification.json", report)
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {
                "docker.json",
                "postgres-finally.json",
            }:
                shutil.copyfile(path, evidence / path.name)
