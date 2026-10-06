"""Full fixed cases and real effects through main/API/independent CLIs/SDK."""
import base64
from copy import deepcopy
from html import unescape
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from test_agent_case_execution import regression_business_http
from test_case_effect_evidence import NEW_POLICY, declaration
from test_case_planning import finish, item
from test_document_impact_calls import provider
from test_document_impact_production import collect_objects
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from src.schemas.run_management import TestRunRecord as RunRecord


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_EXECUTION_CLI_TESTS") != "1", reason="actual main/API/CLIs/MySQL/PG/RustFS/Redis")]
ROOT = Path(__file__).resolve().parents[2]


def contexts(body):
    values = []
    for message in body["messages"]:
        content = message.get("content", "")
        candidates = re.findall(r'<untrusted_content[^>]*>\s*(.*?)\s*</untrusted_content>', content, re.S)
        for raw in candidates or [content]:
            try:
                value = json.loads(unescape(raw))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                values.append(value)
    return values


def respond(body, business, clear_all):
    values = contexts(body)
    frozen = next((v for v in values if v.get("effect_coverage_protocol")), None)
    assert "DO-NOT-INJECT-TEMPLATE" not in json.dumps(body, ensure_ascii=False)
    if frozen is None:
        context = next(v for v in values if "catalog" in v)
        cases = [e for e in context["evidence"] if e["kind"] == "case"]
        assert len(cases) == 1 and len(cases[0]["payload"]["content"]["steps"]) == 8
        assert cases[0]["payload"]["content"]["steps"][-1]["business"] == {"flag": False, "zero": 0, "nullable": None}
        contract = next((e for e in context["evidence"] if e["kind"] == "tool"), None)
        if contract is None:
            data = {"action": "query", "summary": "读取固定原生工具接口", "queries": [{"kind": "tool", "source_id": "api-test-runner"}]}
        else:
            assert "response_status" in contract["payload"]["output_variants"][0]["schema"]["properties"]
            target = item(context, "production-coverage", key="target", depends_on=["seed"],
                input_bindings={"id": {"source_item": "seed", "output_path_tokens": ["body", "order.id"]}})
            refs = [*target["evidence_ids"], contract["id"]]
            data = finish({"key": "seed", "kind": "prepare", "reason": "完整用例要求准备两条记录", "evidence_ids": refs,
                "instructions": {"method": "POST", "url": business + "/orders", "expected": 201, "count": 2},
                "effect_evidence": declaration("produced")}, target,
                {"key": "cleanup", "kind": "prepare", "reason": "清理实际准备的全部记录", "evidence_ids": refs,
                 "depends_on": ["target"], "cleanup_sources": ["seed"],
                 "instructions": {"method": "DELETE", "url": business + "/orders/{id}", "expected": 200},
                 "effect_evidence": declaration("cleanup")})
        return {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}
    assert frozen["effect_coverage_protocol"] == "case-effect-coverage-v1"
    assert frozen["effect_owner_item_ids"]["target"] == frozen["run_item_id"]
    source = next(s["content_json"] for s in frozen["steps"] if s["phase"] == "case")
    assert len(source["steps"]) == 8 and source["steps"][-1]["action"] == "LAST-COVERAGE-STEP"
    previous = [json.loads(m["content"]) for m in body["messages"] if m.get("role") == "tool"]
    sequence = [("create-first", "seed", "POST"), ("create-alias", "seed", "POST"),
        ("create-second", "seed", "POST"), ("target", "target", "GET"), ("cleanup-first", "cleanup", "DELETE")]
    if clear_all:
        sequence.append(("cleanup-second", "cleanup", "DELETE"))
    if len(previous) < len(sequence):
        call, key, method = sequence[len(previous)]
        url = business + "/orders"
        if method != "POST":
            index = 2 if call == "cleanup-second" else 0
            url += "/" + previous[index]["output"]["task_result"]["response_body"]["order.id"]
        operation = "create-first" if call == "create-alias" else call
        arguments = {"_case_action": {"item_key": key, "operation_key": operation}, "worker_action": "execute_task",
            "task": {"task_id": operation, "method": method, "path": url, "full_url": url, "timeout_seconds": 5}}
        tool = next(t["function"] for t in body["tools"] if t["function"]["name"] == "api-test-runner")
        assert "_case_action" in tool["parameters"]["required"]
        return {"role": "assistant", "content": "按完整固定用例执行下一项", "tool_calls": [{"id": call, "type": "function",
            "function": {"name": "api-test-runner", "arguments": json.dumps(arguments, ensure_ascii=False)}}]}
    calls = {"seed": "create-first", "target": "target", "cleanup": "cleanup-first"}
    receipt = {"schema_version": 1, "steps": [{"item_key": step["item"]["key"], "status": "passed",
        "summary": "核对本项实际响应", "checks": [{"name": "固定状态码", "operator": "equals",
            "expected_path_tokens": ["expected"] if step["phase"] != "case" else ["期望", "code"],
            "observations": [{"call_id": calls[step["item"]["key"]], "output_path_tokens": ["task_result", "response_status"]}]}],
        "outputs": {"body": {"call_id": calls[step["item"]["key"]], "output_path_tokens": ["task_result", "response_body"]}}}
        for step in frozen["steps"]]}
    for field in ("flag", "count", "nullable"):
        next(s for s in receipt["steps"] if s["item_key"] == "target")["checks"].append({
            "name": field, "operator": "equals", "expected_path_tokens": ["期望", field],
            "observations": [{"call_id": "target", "output_path_tokens": ["task_result", "response_body", field]}]})
    return {"role": "assistant", "content": json.dumps(receipt, ensure_ascii=False)}


@pytest.mark.parametrize("clear_all", [False, True])
async def test_actual_production_all_original_objects_and_terminal_recovery(tmp_path, regression_business_http, clear_all):
    business, orders, created = regression_business_http
    endpoint, effects, _ = business
    directory = Path(os.getenv("QA_COVERAGE_PRODUCTION_DIR", str(tmp_path / "evidence"))) / ("all" if clear_all else "partial")
    directory.mkdir(parents=True, exist_ok=False)
    errors = []
    def response(body):
        try:
            return respond(body, endpoint, clear_all)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            raise
    with provider(message_responder=response) as (url, sends, _, _):
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned):
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
            settings.api_auth_token = SecretStr(uuid4().hex)
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(model_name="controlled-coverage", provider="openai",
                transport="openai_chat_completions", base_url=url, api_key=uuid4().hex, is_active=True,
                use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False), max_output_tokens=4096))
            processes.env = child_environment(settings)
            port = free_port()
            args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
            api = processes.start("coverage-api", *args)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)
                    async def post(path, data, status=200):
                        r = await client.post(path, json=data)
                        assert r.status_code == status, (r.status_code, r.text, errors)
                        return r.json()
                    async def get(path):
                        r = await client.get(path)
                        assert r.status_code == 200, (r.status_code, r.text)
                        return r.json()
                    project = await post("/projects", {"project_key": "coverage-" + uuid4().hex, "name": "原对象覆盖生产实测", "base_url": endpoint}, 201)
                    root = "/projects/" + project["id"]
                    assert (await client.get(root + "/case-plans/policy", headers={"Authorization": "Bearer invalid-fixture"})).status_code == 401
                    source = directory / "requirements.md"
                    source.write_text(f"# 完整查询与清理\n准备两条记录：POST {endpoint}/orders，每次返回201及order.id。\n"
                        f"GET {endpoint}/orders/{{id}}返回200、flag=false、count=1、nullable=null。\n"
                        f"DELETE {endpoint}/orders/{{id}}返回200，清理本次创建的全部记录。\n", encoding="utf-8")
                    doc = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": source.name,
                        "content_base64": base64.b64encode(source.read_bytes()).decode()})
                    owned.append(doc["storage_uri"])
                    assert (await objects.read_object_uri(doc["storage_uri"]))["content"] == source.read_bytes()
                    content = {"objective": "查询真实第一条记录并清理全部准备记录", "期望": {"code": 200, "flag": False, "count": 1, "nullable": None},
                        "steps": [{"order": i + 1, "action": action} for i, action in enumerate([
                            "POST /orders创建第一条返回201及order.id", "再创建第二条，保留两个实际ID", "GET第一条/orders/{id}",
                            "核对状态码200", "核对flag为False", "核对count为1及nullable为null", "逐条DELETE全部本次记录", "LAST-COVERAGE-STEP"])],
                        "operations": {"create": endpoint + "/orders", "query": endpoint + "/orders/{id}", "cleanup": endpoint + "/orders/{id}"}}
                    content["steps"][-1]["business"] = {"flag": False, "zero": 0, "nullable": None}
                    (directory / "full-case.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
                    bundle = await post(root + "/test-cases", {"case_key": "production-coverage", "title": "完整两对象查询清理", "mode_key": "api_testing",
                        "case_type": "json", "content_json": content, "execution_notes": "按完整八步执行，两个实际创建对象都要清理。",
                        "source_refs": [{"source_type": "api_doc", "source_id": doc["id"]}], "model_key": "manual", "prompt_version": "manual-v1",
                        "skill_versions": {"manual": "v1"}, "generation_metadata": {"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}}, 201)
                    cid = bundle["case"]["id"]
                    await post(f"/test-cases/{cid}/submit-review", {})
                    await post(f"/test-cases/{cid}/activate", {})
                    policy = NEW_POLICY.model_copy(update={"effect_coverage_protocol": "case-effect-coverage-v1", "effect_coverage_max_bytes": 1024000})
                    configured = await client.put(root + "/case-plans/policy", json=policy.model_dump(mode="json"))
                    assert configured.status_code == 200, configured.text
                    request = {"creation_key": "coverage-task", "objective": content["objective"], "model_key": model.key, "candidate_case_ids": [cid]}
                    task = await post(root + "/case-planning-tasks", request, 202)
                    disabled = await client.put(root + "/case-plans/policy", json=CasePlanningPolicy().model_dump(mode="json"))
                    assert disabled.status_code == 200 and not sends
                    planning_queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.case_planning_task_stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await planning_queue.connect()
                    try:
                        assert await PostgresTaskOutbox(settings).relay_once(planning_queue, limit=1) == 1
                        child = processes.start("coverage-planning-worker", "-m", "src.cli.run_case_planning_worker", "--once", "--worker-id", "coverage-plan")
                        assert await processes.wait(child) == 0
                    finally:
                        await planning_queue.close()
                    task = await get(root + "/case-planning-tasks/" + task["id"])
                    assert task["status"] == "completed", task
                    plan = await get(root + "/case-plans/" + task["plan_id"])
                    assert plan["config_snapshot"]["policy"]["effect_coverage_protocol"] == "case-effect-coverage-v1"
                    assert len(sends) == 2
                    with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload_json,revision FROM `{settings.database.application_config_table}` WHERE scope_key=%s AND config_key=%s",
                            (project["id"], "test_case.planning_policy"))
                        configured_row = dict(cur.fetchone())
                    current_policy = json.loads(configured_row["payload_json"])
                    assert "effect_coverage_protocol" not in current_policy
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT seed FROM {settings.database.postgres_case_planning_task_table} WHERE id=%s", (task["id"],))
                        seed = cur.fetchone()["seed"]
                    assert seed["plan"]["config_snapshot"]["policy"]["effect_coverage_protocol"] == "case-effect-coverage-v1"
                    assert "api-test-runner" in seed["tool_contracts"]
                    path, launch = root + "/case-plans/" + plan["id"] + "/runs", {"creation_key": "coverage-run", "mode_key": "api_testing"}
                    detail = await post(path, launch, 201)
                    rid, iid = detail["run"]["id"], detail["items"][0]["id"]
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s", ("test_run_dispatch:" + rid,))
                        delivery = cur.fetchone()["payload"]
                    assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                    child = processes.start("coverage-execution-worker", "-m", "src.cli.run_redis_worker", "--once", "--worker-id", "coverage-execute")
                    assert await processes.wait(child) == 0
                    detail = await get("/runs/" + rid)
                    assert detail["items"][0]["status"] == "waiting_approval" and not effects
                    restarted = False
                    for _ in range(8):
                        current = detail["items"][0]
                        if current["status"] != "waiting_approval":
                            break
                        await post("/run-items/" + iid + "/approval", {"approval_id": current["approval_id"], "decision": "approved"})
                        detail = await get("/runs/" + rid)
                        if len(effects) == 1 and not restarted:
                            count = len(sends)
                            await processes.stop(api)
                            api = processes.start("coverage-api-restarted", *args)
                            await wait_api(client, api)
                            assert await get("/runs/" + rid) == detail
                            assert len(sends) == count and len(effects) == 1
                            assert await post(path, launch, 201) == detail
                            restarted = True
                    (directory / "attempt.json").write_text(json.dumps({"detail": detail, "physical_requests": sends, "business_HTTP": effects, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
                    assert restarted and detail["items"][0]["status"] == ("passed" if clear_all else "blocked"), detail
                    result = detail["results"][0]
                    assert result["actual"]["preparations"]["seed"]["output"]["body"]["order.id"] == created[0]
                    assert result["actual"]["case_output"]["body"]["order.id"] == created[0]
                    coverage = result["actual"]["effect_cleanup_coverage"]["cleanups"]["cleanup"]
                    assert coverage["known_object_count"] == 2 and coverage["covered_object_count"] == (2 if clear_all else 1)
                    assert len(orders) == (0 if clear_all else 1) and len(created) == 2
                    assert [method for method, _ in effects] == ["POST", "POST", "GET", "DELETE"] + (["DELETE"] if clear_all else [])
                    assert result["actual"]["case_status"] == "passed" and not errors
                    assert len(sends) == (9 if clear_all else 8)
                    inventory = result["actual"]["effect_inventory"]
                    assert len(inventory) == len(effects)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT id,status,metadata,input_payload,output_payload FROM {settings.database.postgres_tool_job_table} WHERE metadata->>'run_id'=%s", (rid,))
                        jobs = [dict(row) for row in cur.fetchall()]
                        cur.execute(f"SELECT record FROM {settings.database.postgres_test_run_table} WHERE id=%s", (rid,))
                        sql_run = cur.fetchone()["record"]
                    assert RunRecord.model_validate(sql_run).model_dump(mode="json") == detail["run"]
                    alias = next(j for j in jobs if j["metadata"]["case_operation"]["decision"] == "replayed")
                    assert alias["metadata"]["case_operation"]["action_sent"] is False
                    assert alias["id"] not in {row["origin_job_id"] for row in inventory}
                    known = coverage["covered"] + coverage["uncovered"]
                    assert {row["value"] for row in known} == set(created)
                    count = len(sends)
                    await queue.enqueue(delivery)
                    child = processes.start("coverage-terminal-worker", "-m", "src.cli.run_redis_worker", "--once", "--worker-id", "coverage-terminal")
                    assert await processes.wait(child) == 0
                    assert await get("/runs/" + rid) == detail and len(sends) == count
                    (directory / "verification.json").write_text(json.dumps({"baseline": "56af7313", "controlled_model": True,
                        "default_autonomous_Agent_verified": False, "main_API_independent_CLIs_actual_SDK": True, "original_read_back_RustFS": True,
                        "current_policy_disabled_after_submission": disabled.json(), "frozen_plan": plan, "planning_task": task, "case": bundle,
                        "actual_MySQL_policy": {"policy": current_policy, "revision": configured_row["revision"]}, "PG_seed": seed,
                        "PG_run": sql_run, "authenticated_API": True,
                        "detail": detail, "PG_jobs": jobs, "business_HTTP": effects, "created_ids": created, "remaining_orders": deepcopy(orders),
                        "terminal_replay_new_model_HTTP": 0, "consumption_completion_verified": False, "full_P0_P5_complete": False}, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                collect_objects(settings, owned)
                await processes.close()
                for p in tmp_path.iterdir():
                    if p.suffix in {".stdout", ".stderr"}:
                        shutil.copyfile(p, directory / p.name)
                (directory / "physical-requests.json").write_text(json.dumps(sends, ensure_ascii=False, indent=2), encoding="utf-8")
                (directory / "business-HTTP.json").write_text(json.dumps(effects, ensure_ascii=False, indent=2), encoding="utf-8")
