"""Real default model drives a full requirement-change closed loop."""
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
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.generation_runtime import GENERATION_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.schemas.document_impact_views import DocumentImpactSnapshotView
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_REQUIREMENT_CHANGE") != "1",
    reason="real default model, production API/independent CLIs, isolated MySQL/PG/Redis/RustFS")]


V1 = """# 订单查询需求

## 查询订单详情
GET /orders/{id} 返回 200，JSON 字段：status（字符串）、total（整数，单位分，商品原价合计）。
不存在或已删除的订单返回 404。
"""


V2 = """# 订单查询需求

## 查询订单详情
GET /orders/{id} 返回 200，JSON 字段：status（字符串）、total（整数，单位分，折后应付合计）、
discount（整数，单位分，优惠总额，无优惠时为 0）。
不存在或已删除的订单返回 404。
"""


@pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_REQUIREMENT_CHANGE") != "1", reason="see module gate")
async def test_default_model_requirement_change_closed_loop(tmp_path):
    evidence = Path(os.environ["QA_DEFAULT_REQUIREMENT_CHANGE_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"model_id": configured.model_id, "transport": configured.transport,
             "configured_parameters": configured_parameters, "controlled_model": False,
             "impact_analysis": None, "generation": None}
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = ",".join([
                DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM, GENERATION_STREAM])
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=configured.model_id, provider=configured.provider, transport=configured.transport,
                request_profile=configured.request_profile, base_url=configured.api_base_url,
                api_key=configured.api_key, auth_type=configured.auth_type,
                oauth_provider=configured.oauth_provider, oauth_refresh_token=configured.oauth_refresh_token,
                applications=configured.applications, is_active=True, use_provider_defaults=False,
                capability_overrides=configured.capability_overrides, context_window=configured.context_window,
                max_output_tokens=configured.max_output_tokens))
            assert model_invocation_parameters(models.get_default_active()) == configured_parameters
            save("model-parameters.json", configured_parameters)
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            api_port = free_port()
            api_args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                        "--host", "127.0.0.1", "--port", str(api_port))
            api = processes.start("requirement-change-api", *api_args)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=300,
                headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                await wait_api(client, api)

                async def post(path, body=None, status=200):
                    response = await client.post(path, json=body)
                    assert response.status_code == status, (path, response.status_code, response.text)
                    return response.json()

                async def get(path):
                    response = await client.get(path)
                    assert response.status_code == 200, (path, response.status_code, response.text)
                    return response.json()

                async def relay(stream, limit):
                    queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await queue.connect()
                    try:
                        return await PostgresTaskOutbox(settings).relay_once(queue, limit=limit)
                    finally:
                        await queue.close()

                async def run_worker(name, module, worker_id, *, timeout=600):
                    process = processes.start(name, "-m", "tests.default_model_probe", module,
                        "--once", "--worker-id", worker_id)
                    assert await processes.wait(process, timeout=timeout) == 0, (
                        name, (tmp_path / f"{name}.stderr").read_text(encoding="utf-8", errors="replace")[-2000:])

                project = await post("/projects", {"project_key": "req-change-" + uuid4().hex[:10],
                    "name": "默认模型需求变更闭环", "base_url": "http://127.0.0.1:1"}, 201)
                root = "/projects/" + project["id"]
                mysql = MySQLApplicationConfigStore(settings)
                analysis = (await get(root + "/document-impact-analysis-settings"))["payload"]
                analysis.update(model_key=model.key, analysis_guidance="以真实变更语义为准，引用新版本来源位置")
                assert (await client.put(root + "/document-impact-analysis-settings", json=analysis)).status_code == 200
                invocation = (await get(root + "/document-impact-invocation-settings"))["payload"]
                invocation.update(transport_policy="single_attempt")
                assert (await client.put(root + "/document-impact-invocation-settings", json=invocation)).status_code == 200
                generation_settings = (await get(root + "/case-generation-settings"))["payload"]
                generation_settings.update(transport_policy="single_attempt", call_timeout_seconds=240)
                assert (await client.put(root + "/case-generation-settings", json=generation_settings)).status_code == 200
                assert mysql.get("document.impact_analysis_settings", scope_key=project["id"]).payload["model_key"] == model.key

                document = await post("/registry/api-docs/upload", {"project_id": project["id"],
                    "filename": "requirements.md", "request_id": "change-v1",
                    "content_base64": base64.b64encode(V1.encode("utf-8")).decode()})
                v1_version_id = document["current_version_id"]
                base = root + f"/documents/{document['id']}"
                second = await post(f"/registry/api-docs/{document['id']}/versions", {
                    "project_id": project["id"], "filename": "requirements.md", "request_id": "change-v2",
                    "expected_parent_version_id": document["current_version_id"],
                    "content_base64": base64.b64encode(V2.encode("utf-8")).decode()})
                document = second["document"]
                v2_version_id = document["current_version_id"]

                views = []
                for number, version_id in enumerate([v1_version_id, v2_version_id], 1):
                    task = await post(base + "/parse-tasks", {"creation_key": f"parse-v{number}", "version_id": version_id}, 202)
                    assert await relay(DOCUMENT_PARSE_STREAM, 1) == 1
                    await run_worker(f"parse-worker-{number}", "src.cli.run_document_parse_worker", f"parse-v{number}")
                    parsed = await get(base + f"/parse-tasks/{task['id']}")
                    assert parsed["status"] == "completed", parsed
                    view = DocumentParseSnapshotView.model_validate(await get(base + f"/parse-snapshots/{parsed['snapshot_id']}"))
                    views.append(view)
                assert views[0].snapshot.id != views[1].snapshot.id
                save("parse-snapshots.json", {"v1": views[0].model_dump(mode="json"), "v2": views[1].model_dump(mode="json")})

                comparison = await post(base + "/difference-tasks", {"creation_key": "change-comparison",
                    "old_snapshot_id": views[0].snapshot.id, "new_snapshot_id": views[1].snapshot.id}, 202)
                assert await relay(DOCUMENT_DIFFERENCE_STREAM, 1) == 1
                await run_worker("difference-worker", "src.cli.run_document_difference_worker", "compare")
                diff = await get(base + f"/difference-tasks/{comparison['id']}")
                assert diff["status"] == "completed", diff
                save("difference.json", diff)

                case_payload = {"case_key": "order-total-check", "title": "核对订单总价", "case_type": "json",
                    "mode_key": "api_testing",
                    "content_json": {"objective": "查询订单详情并核对 total 为商品原价合计",
                        "steps": [{"order": 1, "action": "GET /orders/{{id}}，核对 status 与 total"}],
                        "expected": {"status_code": 200, "total": "商品原价合计"}},
                    "execution_notes": "完整读取用例 JSON；total 语义以需求文档为准",
                    "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                    "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"}}
                case = CaseBundle.model_validate(await post(root + "/test-cases", case_payload, 201))

                request = {"creation_key": "default-impact", "difference_snapshot_id": diff["snapshot_id"],
                    "environment_key": "qa", "target_release": "change-v2"}
                submitted = await post(base + "/impact-tasks", request, 202)
                assert submitted["status"] == "queued"
                assert await relay(DOCUMENT_IMPACT_STREAM, 1) == 1
                await run_worker("impact-worker", "src.cli.run_document_impact_worker", "impact")
                task = await get(base + f"/impact-tasks/{submitted['id']}")
                assert task["status"] == "completed", task
                view = DocumentImpactSnapshotView.model_validate(await get(base + f"/impact-snapshots/{task['snapshot_id']}"))
                result = view.result
                save("impact-result.json", view.model_dump(mode="json"))
                assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
                assert result.units and result.rules, "default model produced no impact rules"
                analyzed = [item for item in result.units if item.status == "analyzed"]
                assert analyzed, result.model_dump(mode="json")
                citations = [citation for item in analyzed for candidate in (item.candidates or [])
                             for impact in candidate.case_impacts for citation in impact.citations]
                assert citations, "no source citations in impact result"
                facts["impact_analysis"] = {"units": len(result.units), "analyzed": len(analyzed),
                    "rules": len(result.rules), "citations": len(citations),
                    "status": result.status, "model_http": model_sends()}

                review_request = {"request_id": "default-requirement-review", "rule_id": result.rules[0].id,
                    "decision": "accept", "note": "确认来源与设计关联；执行与部署另行验证",
                    "change": "modified", "title": "订单总价口径变更",
                    "statement": "total 调整为折后应付合计，并新增 discount 字段",
                    "points": [{"key": "total-semantics", "description": "验证 total 为折后应付合计"},
                               {"key": "discount-field", "description": "验证 discount 字段与无优惠为 0"}],
                    "case_links": [{"case_version_id": case.version.id, "point_keys": ["total-semantics", "discount-field"]}]}
                review = await post(base + f"/impact-snapshots/{task['snapshot_id']}/reviews", review_request, 201)
                requirement_id = review["requirement_version"]["requirement_id"]
                matrix = await get(base + f"/requirements/{requirement_id}/matrix")
                save("requirement-review.json", review)
                save("requirement-matrix.json", matrix)
                assert all(point["design_status"] == "design_covered" for point in matrix["points"]), matrix
                assert matrix["deployment_status"] == "unknown"

                generation_request = {"creation_key": "default-incremental", "requirement_id": requirement_id,
                    "requirement_version_id": review["requirement_version"]["id"],
                    "target_case_version_ids": [case.version.id], "include_new_cases": True,
                    "new_case_mode_key": "api_testing", "model_key": model.key}
                group = await post(base + "/case-generations", generation_request, 202)
                assert await relay(GENERATION_STREAM, 10) == 2
                for index in range(2):
                    await run_worker(f"generation-worker-{index}", "src.cli.run_case_generation_worker",
                        f"generate-{index}")
                generation = await get(base + f"/case-generations/{group['id']}")
                assert all(t["status"] == "completed" for t in generation["modules"]), generation
                save("generation.json", generation)
                update_receipt = created_receipt = None
                for module in generation["modules"]:
                    version_receipt = module["checkpoint"]["receipt"]["versions"][0]
                    if version_receipt["case_id"] == case.case.id:
                        update_receipt = version_receipt
                    else:
                        created_receipt = version_receipt
                assert update_receipt and created_receipt, generation
                updated_versions = await get(f"/test-cases/{case.case.id}/versions")
                draft = next(v for v in updated_versions if v["id"] == update_receipt["version_id"])
                updated_case = await get(f"/test-cases/{case.case.id}")
                new_case = await get(f"/test-cases/{created_receipt['case_id']}")
                assert updated_case["active_version_id"] == case.case.active_version_id
                assert new_case["active_version_id"] is None and new_case["lifecycle_status"] == "draft"
                assert draft["content_json"].get("steps"), draft["content_json"]
                assert draft["generation_metadata"]["incremental_generation"]["requirement_version_id"] == \
                    generation_request["requirement_version_id"]
                facts["generation"] = {"modules": [m["status"] for m in generation["modules"]],
                    "draft_version": update_receipt["version_id"], "new_case_draft": True,
                    "model_http": model_sends()}

                sends_before = model_sends()
                generation_queue = RedisTaskQueue(settings.database.redis_url, stream=GENERATION_STREAM,
                    group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                await generation_queue.connect()
                try:
                    for module in generation["modules"]:
                        await generation_queue.enqueue({"task_type": "case_generation",
                            "project_id": project["id"], "task_id": module["id"]})
                        terminal = processes.start("generation-terminal", "-m", "tests.default_model_probe",
                            "src.cli.run_case_generation_worker", "--once", "--worker-id", "terminal-replay")
                        assert await processes.wait(terminal, timeout=120) == 0
                finally:
                    await generation_queue.close()
                assert await get(base + f"/case-generations/{group['id']}") == generation
                facts["terminal_replay_extra_model_http"] = model_sends() - sends_before
                assert facts["terminal_replay_extra_model_http"] == 0
                facts["model_http_total"] = model_sends()
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"}:
                (evidence / path.name).write_bytes(path.read_bytes())
        facts["global_model_unchanged"] = model_invocation_parameters(
            configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)
