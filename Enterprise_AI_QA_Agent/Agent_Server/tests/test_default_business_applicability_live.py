"""User applicability reviews over real environments plus default-model bound planning."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.schemas.document_impact_views import DocumentImpactSnapshotView
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_BUSINESS_APPLICABILITY") != "1",
    reason="real default model, owned HTTP targets, production API/independent CLIs, isolated stores")]

V1 = """# 订单查询需求

## 查询订单详情
GET /orders/{id} 返回 200，JSON 字段：status（字符串）、total（整数，单位分，商品原价合计）。

## 数据保留
订单记录保留 7 天。
"""

V2 = """# 订单查询需求

## 查询订单详情
GET /orders/{id} 返回 200，JSON 字段：status（字符串）、total（整数，单位分，折后应付合计）、
discount（整数，单位分，优惠总额，无优惠时为 0）。

## 数据保留
订单记录保留 7 天。
"""


class OrderTarget(BaseHTTPRequestHandler):
    """Old deployments keep the original total semantics; new ones apply the change."""

    def _reply(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/app/orders/"):
            self._reply(self.order_payload)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def start_target(payload: dict) -> tuple[str, ThreadingHTTPServer]:
    handler = type("BoundTarget", (OrderTarget,), {"order_payload": payload})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", server


@pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_BUSINESS_APPLICABILITY") != "1", reason="see module gate")
async def test_default_model_environment_applicability_and_bound_planning(tmp_path):
    evidence = Path(os.environ["QA_DEFAULT_BUSINESS_APPLICABILITY_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    old_url, old_server = start_target({"status": "paid", "total": 1000})
    new_url, new_server = start_target({"status": "paid", "total": 900, "discount": 100})
    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"model_id": configured.model_id, "transport": configured.transport,
             "configured_parameters": configured_parameters, "controlled_model": False,
             "applicability": None, "environment_planning": None}
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            # Keep the fixture-registered per-runtime streams (case planning among
            # them) and append this scenario's document streams.
            settings.orchestration.redis_task_outbox_streams = ",".join(
                [settings.orchestration.redis_task_outbox_streams,
                 DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM])
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
            api = processes.start("applicability-api", "-m", "tests.default_model_probe", "uvicorn",
                "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            try:
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

                    async def relay(stream, limit=1):
                        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                            group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                        await queue.connect()
                        try:
                            return await PostgresTaskOutbox(settings).relay_once(queue, limit=limit)
                        finally:
                            await queue.close()

                    async def run_worker(name, module, worker_id, timeout=600):
                        process = processes.start(name, "-m", "tests.default_model_probe", module,
                            "--once", "--worker-id", worker_id)
                        assert await processes.wait(process, timeout=timeout) == 0, (
                            name, (tmp_path / f"{name}.stderr").read_text(encoding="utf-8", errors="replace")[-2000:])

                    # Shared document streams are process-wide constants: purge older
                    # runs' deliveries before this run's one-shot workers start.
                    probe_queue = RedisTaskQueue(settings.database.redis_url, stream=DOCUMENT_PARSE_STREAM,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await probe_queue.connect()
                    try:
                        for stream in (DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM):
                            await probe_queue._client.delete(stream)
                    finally:
                        await probe_queue.close()

                    project = await post("/projects", {"project_key": "applicability-" + uuid4().hex[:10],
                        "name": "默认模型环境适用性验收"}, 201)
                    root = "/projects/" + project["id"]
                    analysis = (await get(root + "/document-impact-analysis-settings"))["payload"]
                    analysis.update(model_key=model.key, analysis_guidance="以真实变更语义为准，引用新版本来源位置")
                    assert (await client.put(root + "/document-impact-analysis-settings", json=analysis)).status_code == 200
                    invocation = (await get(root + "/document-impact-invocation-settings"))["payload"]
                    invocation.update(transport_policy="single_attempt")
                    assert (await client.put(root + "/document-impact-invocation-settings", json=invocation)).status_code == 200

                    document = await post("/registry/api-docs/upload", {"project_id": project["id"],
                        "filename": "requirements.md", "request_id": "applicability-v1",
                        "content_base64": base64.b64encode(V1.encode("utf-8")).decode()})
                    v1_version_id = document["current_version_id"]
                    base = root + f"/documents/{document['id']}"
                    second = await post(f"/registry/api-docs/{document['id']}/versions", {
                        "project_id": project["id"], "filename": "requirements.md", "request_id": "applicability-v2",
                        "expected_parent_version_id": document["current_version_id"],
                        "content_base64": base64.b64encode(V2.encode("utf-8")).decode()})
                    document = second["document"]
                    v2_version_id = document["current_version_id"]

                    views = []
                    for number, version_id in enumerate([v1_version_id, v2_version_id], 1):
                        task = await post(base + "/parse-tasks",
                            {"creation_key": f"parse-v{number}", "version_id": version_id}, 202)
                        assert await relay(DOCUMENT_PARSE_STREAM, 1) == 1
                        await run_worker(f"parse-worker-{number}", "src.cli.run_document_parse_worker", f"parse-v{number}")
                        parsed = await get(base + f"/parse-tasks/{task['id']}")
                        assert parsed["status"] == "completed", parsed
                        views.append(DocumentParseSnapshotView.model_validate(
                            await get(base + f"/parse-snapshots/{parsed['snapshot_id']}")))

                    comparison = await post(base + "/difference-tasks", {"creation_key": "applicability-comparison",
                        "old_snapshot_id": views[0].snapshot.id, "new_snapshot_id": views[1].snapshot.id}, 202)
                    assert await relay(DOCUMENT_DIFFERENCE_STREAM, 1) == 1
                    await run_worker("difference-worker", "src.cli.run_document_difference_worker", "compare")
                    diff = await get(base + f"/difference-tasks/{comparison['id']}")
                    assert diff["status"] == "completed", diff

                    case_payload = {"case_key": "order-discount-check", "title": "核对折后总价与优惠字段",
                        "case_type": "json", "mode_key": "api_testing",
                        "content_json": {"objective": "查询订单详情并核对 total 为折后应付合计、discount 为优惠总额",
                            "steps": [{"order": 1, "action": "GET /orders/{{id}}，核对 status、total 与 discount"}],
                            "expected": {"status_code": 200, "total": 900, "discount": 100}},
                        "execution_notes": "完整读取用例 JSON；total/discount 语义以需求文档 V2 为准",
                        "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                        "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"}}
                    case = CaseBundle.model_validate(await post(root + "/test-cases", case_payload, 201))

                    assert await relay(DOCUMENT_IMPACT_STREAM, 1) == 0
                    submitted = await post(base + "/impact-tasks", {"creation_key": "applicability-impact",
                        "difference_snapshot_id": diff["snapshot_id"], "environment_key": "qa",
                        "target_release": "change-v2"}, 202)
                    assert await relay(DOCUMENT_IMPACT_STREAM, 1) == 1
                    await run_worker("impact-worker", "src.cli.run_document_impact_worker", "impact")
                    task = await get(base + f"/impact-tasks/{submitted['id']}")
                    assert task["status"] == "completed", task
                    view = DocumentImpactSnapshotView.model_validate(await get(base + f"/impact-snapshots/{task['snapshot_id']}"))
                    result = view.result
                    assert result.units and result.rules, result.model_dump(mode="json")
                    save("impact-result.json", view.model_dump(mode="json"))

                    review_request = {"request_id": "applicability-requirement-review", "rule_id": result.rules[0].id,
                        "decision": "accept", "note": "用户业务评审：确认来源与设计关联",
                        "change": "modified", "title": "订单总价口径变更",
                        "statement": "total 调整为折后应付合计，并新增 discount 字段",
                        "points": [{"key": "total-semantics", "description": "验证 total 为折后应付合计"},
                                   {"key": "discount-field", "description": "验证 discount 字段与无优惠为 0"}],
                        "case_links": [{"case_version_id": case.version.id,
                            "point_keys": ["total-semantics", "discount-field"]}]}
                    review = await post(base + f"/impact-snapshots/{task['snapshot_id']}/reviews", review_request, 201)
                    version = review["requirement_version"]

                    # --- User business applicability reviews against real deployments ---
                    from src.application.runtime.tool_job_service import ToolJobService
                    from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
                    from src.registry.tools import ToolRegistry
                    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
                    from src.schemas.tool_runtime import ModelToolCall

                    decisions_sends_before = model_sends()
                    states = []
                    for index, (env_url, payload_label) in enumerate([(old_url, "old"), (new_url, "new")]):
                        root_env = root + "/business-environments"
                        registration = await post(root_env, {"request_id": f"env-{payload_label}",
                            "environment_key": f"target-{index}"}, 201)
                        selected = root_env + "/" + registration["id"]
                        configured_env = await client.put(selected + "/configuration",
                            json={"display_name": f"{'旧' if index == 0 else '新'}语义部署", "base_url": env_url + "/app"})
                        assert configured_env.status_code == 200, configured_env.text
                        env_config = configured_env.json()
                        snapshot = await post(selected + "/snapshots", {
                            "request_id": f"capture-{payload_label}",
                            "expected_config_revision": env_config["revision"],
                            "expected_config_sha256": env_config["sha256"]}, 201)
                        jobs = ToolJobService(PostgresToolJobStore(settings))
                        runtime = ToolRuntimeService(tool_job_service=jobs)
                        tool = ToolRegistry().get("api-test-runner")
                        context = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()),
                            trace_id=str(uuid4()), user_message="正式环境规则观察",
                            normalized_input="正式环境规则观察", context_bundle={"project_id": project["id"]})
                        record = await runtime.execute(tool, ModelToolCall(
                            id=f"observe-{payload_label}", name=tool.key,
                            arguments={"worker_action": "execute_task", "task": {"task_id": f"observe-{payload_label}",
                                "method": "GET", "path": env_url + "/app/orders/1"}}), context)
                        field = "total" if index == 0 else "discount"
                        observation = await post(selected + f"/snapshots/{snapshot['id']}/tool-observations/inspect", {
                            "tool_job_id": record.job_id, "output_path_tokens": ["task_result", "response_body", field]})
                        assert observation["target_status"] == "matched", observation
                        assert observation["value"] == (1000 if index == 0 else 100), observation
                        applicable = index == 1
                        review_payload = {"request_id": f"decision-{payload_label}",
                            "document_id": review["document_id"], "requirement_id": version["requirement_id"],
                            "requirement_version_id": version["id"],
                            "expected_snapshot_sha256": snapshot["sha256"],
                            "expected_requirement_sha256": version["content_sha256"],
                            "coverage_id": "initial", "expected_coverage_sha256": version["content_sha256"],
                            "note": "用户业务评审：只对所选验证点与用例版本下结论",
                            "observations": [{"tool_job_id": record.job_id,
                                "output_path_tokens": observation["output_path_tokens"],
                                "expected_output_sha256": observation["output_sha256"],
                                "expected_value_sha256": observation["value_sha256"]}],
                            "points": [{"point_key": "total-semantics",
                                "status": "effective" if applicable else "not_effective",
                                "note": "对照实际部署响应确认", "observation_indexes": [0]},
                                {"point_key": "discount-field",
                                "status": "effective" if applicable else "not_effective",
                                "note": "对照实际部署响应确认", "observation_indexes": [0]}],
                            "cases": [{"case_version_id": case.version.id,
                                "point_keys": ["total-semantics", "discount-field"],
                                "status": "valid", "note": "新语义部署上该用例版本适用"}] if applicable else []}
                        saved = await post(selected + f"/snapshots/{snapshot['id']}/applicability-reviews", review_payload, 201)
                        assert saved["scope"] == "selected_points_and_case_versions", saved
                        states.append({"path": selected, "snapshot": snapshot, "review": saved,
                            "review_payload": review_payload, "label": payload_label})
                    revoke = {**states[0]["review_payload"], "request_id": "decision-old-revoke", "action": "revoke",
                        "expected_review_id": states[0]["review"]["id"], "points": [], "cases": [],
                        "observations": [], "note": "撤回旧环境结论并保留原确认"}
                    revoked = await post(states[0]["path"] + f"/snapshots/{states[0]['snapshot']['id']}/applicability-reviews",
                        revoke, 201)
                    assert revoked["revision"] == 2, revoked
                    decisions_calls = model_sends() - decisions_sends_before
                    assert decisions_calls == 0, "user business reviews must not call the model"
                    facts["applicability"] = {"environments": 2, "real_observations": 2,
                        "reviews": 2, "revoked_revision": revoked["revision"], "model_http": decisions_calls}
                    save("environment-decisions.json", {"states": states, "revoked": revoked})

                    # --- Default-model planning bound to the new environment snapshot ---
                    await post(f"/test-cases/{case.case.id}/submit-review",
                        {"version_id": case.version.id, "request_id": "bound-planning-review"})
                    await post(f"/test-cases/{case.case.id}/activate",
                        {"version_id": case.version.id, "request_id": "bound-planning-activate"})
                    new_state = states[1]
                    planning_request = {"creation_key": "default-bound-planning",
                        "objective": "在已确认新语义的目标上核对折后总价与优惠字段",
                        "model_key": model.key, "candidate_case_ids": [case.case.id],
                        "environment": {"environment_id": new_state["snapshot"]["environment_id"],
                            "snapshot_id": new_state["snapshot"]["id"],
                            "expected_snapshot_sha256": new_state["snapshot"]["sha256"]}}
                    sends_before = model_sends()
                    planning_task = await post(root + "/case-planning-tasks", planning_request, 202)
                    planning_stream = settings.orchestration.case_planning_task_stream
                    assert await relay(planning_stream, 1) == 1
                    await run_worker("bound-planning-worker", "src.cli.run_case_planning_worker", "bound-planning")
                    done = await get(root + "/case-planning-tasks/" + planning_task["id"])
                    assert done["status"] == "completed", done
                    plan = await get(root + "/case-plans/" + done["plan_id"])
                    save("bound-planning.json", {"task": done, "plan": plan})
                    assert plan["environment"]["snapshot"] == new_state["snapshot"], plan.get("environment")
                    assert plan["environment"]["reviews"] == [new_state["review"]]
                    assert plan["decision"]["items"], plan["decision"]
                    assert any(item.get("case_version_id") == case.version.id
                        for item in plan["decision"]["items"]), plan["decision"]
                    planning_calls = model_sends() - sends_before
                    assert planning_calls >= 1, "environment-bound planning made no model call"
                    detail = await post(root + "/case-plans/" + plan["id"] + "/runs",
                        {"creation_key": "bound-run", "mode_key": "api_testing"}, 201)
                    assert detail["run"]["plan_snapshot"]["plan"]["environment"] == plan["environment"]
                    facts["environment_planning"] = {"model_http": planning_calls,
                        "plan_items": len(plan["decision"]["items"]),
                        "environment_propagated_to_run": True}

                    # --- Execution with environment admission (real default model) ---
                    unknown_request = {**new_state["review_payload"], "request_id": "admission-current-unknown",
                        "expected_review_id": new_state["review"]["id"],
                        "points": [{**p, "status": "unknown"} for p in new_state["review_payload"]["points"]],
                        "cases": [{**c, "status": "unknown"} for c in new_state["review_payload"].get("cases", [])]}
                    unknown = await post(new_state["path"] + f"/snapshots/{new_state['snapshot']['id']}/applicability-reviews",
                        unknown_request, 201)
                    save("admission-unknown-review.json", unknown)
                    run_id = detail["run"]["id"]
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                            ("test_run_dispatch:" + run_id,))
                        delivery = cur.fetchone()["payload"]
                    run_queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.redis_task_stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await run_queue.connect()
                    try:
                        sends_before_execution = model_sends()
                        await run_queue.enqueue(delivery)
                        await run_worker("admission-execution-worker", "src.cli.run_redis_worker", "admission-execute")
                        detail = await get("/runs/" + run_id)
                        for _ in range(6):
                            waiting = next((item for item in detail["items"] if item["status"] == "waiting_approval"), None)
                            if waiting is None:
                                break
                            await post("/run-items/" + waiting["id"] + "/approval",
                                {"approval_id": waiting["approval_id"], "decision": "approved"})
                            detail = await get("/runs/" + run_id)
                        save("admission-execution.json", {"run": detail["run"], "items": detail["items"],
                            "results": detail.get("results", [])})
                        assert detail["run"]["status"] == "completed", detail["run"]
                        assert detail["items"][0]["status"] == "passed", detail["items"]
                        result = detail["results"][0]
                        admission = result["actual"]["environment_admission"]
                        assert admission["reviews"] == [unknown], admission
                        assert admission["decision"] == "no_explicit_invalid", admission
                        original_attempt = next(a for a in detail["attempts"]
                            if a["id"] == admission["execution_attempt_id"])
                        assert original_attempt["environment_admission"] == admission
                        execution_calls = model_sends() - sends_before_execution
                        facts["environment_admission_execution"] = {"run_status": detail["run"]["status"],
                            "item_status": detail["items"][0]["status"], "admission_decision": admission["decision"],
                            "model_http": execution_calls}
                        replay_before = (model_sends(), len(detail["results"]))
                        await run_queue.enqueue(delivery)
                        terminal = processes.start("admission-terminal-worker", "-m", "tests.default_model_probe",
                            "src.cli.run_redis_worker", "--once", "--worker-id", "admission-terminal")
                        assert await processes.wait(terminal, timeout=120) == 0
                        detail_after = await get("/runs/" + run_id)
                        assert detail_after == detail
                        assert (model_sends(), len(detail_after["results"])) == replay_before
                        facts["environment_admission_execution"]["terminal_replay_extra_model_http"] = \
                            model_sends() - sends_before_execution - execution_calls
                    finally:
                        await run_queue.close()
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
        old_server.shutdown()
        new_server.shutdown()
