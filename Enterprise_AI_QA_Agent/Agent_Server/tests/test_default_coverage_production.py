"""Actual configured default model plans and executes full fixed JSON cases."""
import base64
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from default_model_probe import install
from test_agent_case_execution import regression_business_http
from test_case_effect_evidence import NEW_POLICY
from test_case_cleanup_coverage_production import contexts
from test_document_impact_calls import provider
from test_document_impact_production import collect_objects
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api
from src.application.model_clients.openai_client import OpenAIChatClient
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelCapabilitiesOverride, ModelTransportOptions, model_invocation_parameters
from src.schemas.case_planning_task import PLANNING_RESPONSE_PROTOCOL
from src.schemas.case_planning_stages import STAGE_PROTOCOL
from src.schemas.settings import ModelConfigUpdateRequest
from src.schemas.model_request_options import ModelRequestProfile


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_actual_SDK_event_hooks_preserve_full_stream_and_do_not_save_auth(tmp_path):
    original = install(tmp_path)
    try:
        with provider(message_responder=lambda body: {"role": "assistant", "content": "actual-stream-tail"}) as (url, sends, _, _):
            config = ModelConfigRecord(key="probe", name="probe", provider="openai", transport="openai_chat_completions",
                model_id="probe", api_base_url=url, max_output_tokens=80)
            secret = uuid4().hex
            deltas = []
            async def receive(delta):
                deltas.append(delta)
            result = await OpenAIChatClient(timeout_seconds=5, max_retries=0).stream(config, secret,
                ModelInvocationRequest(system_prompt="Trace fixture", messages=[{"role": "user", "content": "trace fixture"}]), receive)
            assert result["text"] == "actual-stream-tail" == "".join(deltas)
            requests = list(tmp_path.glob("*.request.json"))
            assert len(requests) == len(sends) == 1
            assert json.loads(requests[0].read_bytes()) == sends[0]
            assert secret not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
            response = json.loads(next(tmp_path.glob("*.response.json")).read_text(encoding="utf-8"))
            assert response["status_code"] == 200 and response["body_read_by_probe"] is False
    finally:
        OpenAIChatClient._make_client = original


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_COVERAGE_PRODUCTION") != "1", reason="actual current model and isolated main/API/CLIs/stores")
async def test_actual_default_model_plans_and_executes_complete_case(tmp_path, regression_business_http):
    global_settings = get_settings().model_copy(deep=True)
    original_model = MySQLModelConfigStore(global_settings).get_default_active()
    parameters = model_invocation_parameters(original_model)
    invocation_model = original_model.model_copy(deep=True)
    profile_path = os.getenv("QA_DEFAULT_REQUEST_PROFILE")
    if profile_path:
        invocation_model.request_profile = ModelRequestProfile.model_validate_json(Path(profile_path).read_bytes())
        invocation_model = ModelConfigRecord.model_validate(invocation_model.model_dump())
    invocation_parameters = model_invocation_parameters(invocation_model)
    assert original_model.transport == "openai_chat_completions", "Probe currently targets the actual OpenAI-compatible SDK"
    endpoint, effects, _ = regression_business_http[0]
    orders, created = regression_business_http[1:]
    directory = Path(os.environ["QA_DEFAULT_COVERAGE_DIR"])
    directory.mkdir(parents=True, exist_ok=False)
    report = {"baseline": "c7d20303 plus staged planning worktree" if os.getenv("QA_STAGED_PLANNING") == "1" else "eae3393a plus frozen operation contracts v3", "configured_model_parameters": parameters, "controlled_model": False,
        "planning_completed": False, "business_execution_completed": False, "full_P0_P5_complete": False}
    report["isolated_invocation_parameters"] = invocation_parameters
    report["isolated_profile_override"] = bool(profile_path)
    executor_only = os.getenv("QA_RECEIPT_EXECUTOR_ONLY") == "1"
    report["controlled_planning"] = executor_only
    planning_providers = ExitStack()
    if os.getenv("QA_FORMAL_EFFECT_RELATIONS") == "1":
        report["baseline"] = "d81b25e8 plus formal effect relations v2"
    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned):
        settings.api_auth_token = SecretStr(uuid4().hex)
        settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
        models = MySQLModelConfigStore(settings)
        models.initialize()
        execution_model = ModelConfigUpdateRequest(model_name=original_model.model_id, provider=original_model.provider,
            transport=original_model.transport, request_profile=invocation_model.request_profile,
            base_url=original_model.api_base_url, api_key=original_model.api_key,
            auth_type=original_model.auth_type, oauth_provider=original_model.oauth_provider,
            oauth_refresh_token=original_model.oauth_refresh_token, applications=original_model.applications, is_active=True,
            use_provider_defaults=False, capability_overrides=original_model.capability_overrides,
            context_window=original_model.context_window, max_output_tokens=original_model.max_output_tokens)
        model = models.upsert(execution_model)
        assert model_invocation_parameters(models.get_default_active()) == invocation_parameters
        processes.env = child_environment(settings)
        processes.env["QA_DEFAULT_PROBE_DIR"] = str(directory / "physical")
        port = free_port()
        args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
        api = processes.start("default-api", *args)
        project = task = plan = detail = None
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=240,
                headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                await wait_api(client, api)
                async def post(path, data, status=200):
                    r = await client.post(path, json=data)
                    assert r.status_code == status, (r.status_code, r.text)
                    return r.json()
                async def get(path):
                    r = await client.get(path)
                    assert r.status_code == 200, (r.status_code, r.text)
                    return r.json()
                project = await post("/projects", {"project_key": "default-" + uuid4().hex, "name": "默认Agent真实完整用例", "base_url": endpoint}, 201)
                root = "/projects/" + project["id"]
                source = directory / "requirements.md"
                source.write_text(f"# 实际查询及准备清理\nPOST {endpoint}/orders 不需要鉴权和请求体，每次创建一个订单，返回201，JSON键order.id是完整键（含点号）。\n"
                    "本次需要准备两条订单，第二条作为未查询的独立记录，两个ID均要保存。第一条count=1，第二条count=0；flag=false、nullable=null。\n"
                    f"GET {endpoint}/orders/{{id}} 返回200及该订单完整JSON。DELETE同一路径返回200及刚删除的订单JSON。\n"
                    "查询第一条并验证其返回；最后逐条清理本次两个订单。没有已存在ID，需依据本文件准备，不能猜ID。原生工具api-test-runner的execute_task可调用这些接口。\n", encoding="utf-8")
                doc = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": source.name,
                    "content_base64": base64.b64encode(source.read_bytes()).decode()})
                owned.append(doc["storage_uri"])
                assert (await objects.read_object_uri(doc["storage_uri"]))["content"] == source.read_bytes()
                save("document.json", doc)
                content = {"objective": "查询第一条实际订单，保留未查询订单并在结束时清理全部本次记录", "requires": ["actual_order_id"],
                    "期望": {"code": 200, "flag": False, "count": 1, "nullable": None},
                    "steps": [{"order": i+1, "action": action} for i, action in enumerate([
                        "读取项目需求和实际ID来源；没有ID需要先准备", "确认准备两条记录且记录两个实际ID", "GET第一条/orders/{id}",
                        "核对状态码200", "核对flag为False", "核对count为1", "核对nullable为null并清理所有本次记录", "LAST-DEFAULT-COVERAGE-STEP"])],
                    "negative_condition": "没有订单ID时不得凭空推断，必须取得实际返回",
                    "operations": {"query": endpoint + "/orders/{id}"}}
                content["steps"][-1]["business"] = {"false": False, "zero": 0, "nullable": None}
                save("full-case.json", content)
                bundle = await post(root + "/test-cases", {"case_key": "default-query", "title": "完整八步已有订单查询",
                    "mode_key": "api_testing", "case_type": "json", "content_json": content,
                    "execution_notes": "按完整JSON和项目文档完成，必要准备和清理沿运行计划；未知字段仍保留。",
                    "source_refs": [{"source_type": "api_doc", "source_id": doc["id"]}], "model_key": "manual", "prompt_version": "manual-v1",
                    "skill_versions": {"manual": "v1"}, "generation_metadata": {"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}}, 201)
                save("case.json", bundle)
                cid = bundle["case"]["id"]
                await post(f"/test-cases/{cid}/submit-review", {})
                await post(f"/test-cases/{cid}/activate", {})
                policy = NEW_POLICY.model_copy(update={"effect_coverage_protocol": "case-effect-coverage-v1", "effect_coverage_max_bytes": 1024000})
                if os.getenv("QA_STAGED_PLANNING") == "1":
                    policy = policy.model_copy(update={"planning_stage_protocol": STAGE_PROTOCOL})
                if os.getenv("QA_FORMAL_EFFECT_RELATIONS") == "1":
                    from src.schemas.case_planning import CasePlanningPolicy
                    policy = CasePlanningPolicy.model_validate({**policy.model_dump(mode="json"),
                        "preparation_scope_protocol": "preparation-scope-v2", "effect_evidence_protocol": "case-effect-evidence-v2",
                        "effect_coverage_protocol": "case-effect-coverage-v2", "planning_stage_protocol": "case-planning-staged-evidence-v2"})
                if os.getenv("QA_OPERATION_PREFETCH") == "1":
                    from src.schemas.case_planning import CasePlanningPolicy
                    policy = CasePlanningPolicy.model_validate({**policy.model_dump(mode="json"),
                        "preparation_scope_protocol": "preparation-scope-v2", "effect_evidence_protocol": "case-effect-evidence-v2",
                        "effect_coverage_protocol": "case-effect-coverage-v2", "planning_stage_protocol": "case-planning-staged-evidence-v2",
                        "effect_expected_source_protocol": "case-effect-document-expected-v1",
                        "operation_prefetch_protocol": "case-planning-declared-operation-prefetch-v1"})
                    report["baseline"] = "e4d268e8 plus declared operation prefetch v7"
                if os.getenv("QA_SOURCE_IDENTITY") == "1":
                    from src.schemas.case_planning import CasePlanningPolicy
                    policy = CasePlanningPolicy.model_validate({**policy.model_dump(mode="json"),
                        "preparation_scope_protocol": "preparation-scope-v2", "effect_evidence_protocol": "case-effect-evidence-v2",
                        "effect_coverage_protocol": "case-effect-coverage-v2", "planning_stage_protocol": "case-planning-staged-evidence-v2",
                        "effect_expected_source_protocol": "case-effect-document-expected-v1",
                        "source_identity_protocol": "case-planning-source-full-hash-v1"})
                    report["baseline"] = "5b5490ac plus source identity v8"
                if os.getenv("QA_ITEM_OPERATION_SELECTION") == "1":
                    from src.schemas.case_planning import CasePlanningPolicy
                    policy = CasePlanningPolicy.model_validate({**policy.model_dump(mode="json"),
                        "preparation_scope_protocol": "preparation-scope-v2", "effect_evidence_protocol": "case-effect-evidence-v2",
                        "effect_coverage_protocol": "case-effect-coverage-v2", "planning_stage_protocol": "case-planning-staged-evidence-v2",
                        "effect_expected_source_protocol": "case-effect-document-expected-v1",
                        "source_identity_protocol": "case-planning-source-full-hash-v1",
                        "operation_prefetch_protocol": "case-planning-item-operation-prefetch-v2"})
                    report["baseline"] = "07275986 plus item operation selection v9"
                if os.getenv("QA_LINKED_SOURCES") == "1":
                    policy.linked_source_protocol = "case-planning-linked-documents-v1"
                    report["baseline"] = "af790e15 plus fixed linked sources v10"
                if os.getenv("QA_EVIDENCE_REFERENCES") == "1":
                    policy.evidence_reference_protocol = "case-planning-evidence-references-v1"
                    report["baseline"] = "43f1700b plus evidence references v11"
                if os.getenv("QA_EFFECT_TASKS") == "1":
                    policy.planning_stage_protocol = "case-planning-staged-evidence-v3"
                    report["baseline"] = "28fc566c plus separate evidence tasks"
                if os.getenv("QA_RECEIPT_CORRECTION") == "1":
                    policy.receipt_correction_protocol = "case-receipt-correction-v1"
                    report["isolated_receipt_correction_enabled"] = True
                    transport = os.getenv("QA_RECEIPT_TRANSPORT_OPTIONS")
                    if transport:
                        policy.receipt_correction_transport_options = ModelTransportOptions.model_validate_json(transport)
                if executor_only:
                    # Diagnose the existing execution adapter using a real HTTP
                    # planning fixture, not a second external planning attempt.
                    # This path is explicitly NOT a default-model planning pass.
                    from test_case_planning import item as plan_item, finish as plan_finish
                    policy = NEW_POLICY.model_copy(update={"effect_coverage_protocol": "case-effect-coverage-v1",
                        "effect_coverage_max_bytes": 1024000,
                        "receipt_correction_protocol": policy.receipt_correction_protocol,
                        "receipt_correction_transport_options": policy.receipt_correction_transport_options})
                    def receipt_diagnostic_plan(body):
                        context = json.loads(body["messages"][-1]["content"])
                        documents = [e for e in context["evidence"] if e["kind"] == "document"]
                        if not documents:
                            return json.dumps({"action": "query", "summary": "读取真实项目文档用于执行诊断",
                                "queries": [{"kind": "document", "source_id": doc["id"]}]}, ensure_ascii=False)
                        target = plan_item(context, "default-query", key="target_order_query")
                        target["evidence_ids"].extend(e["id"] for e in documents)
                        return json.dumps(plan_finish(target), ensure_ascii=False)
                    planning_endpoint, planning_calls, _, _ = planning_providers.enter_context(
                        provider(responder=receipt_diagnostic_plan))
                    planning_model = models.upsert(ModelConfigUpdateRequest(model_name="receipt-diagnostic-controlled-plan",
                        provider="openai", transport="openai_chat_completions", base_url=planning_endpoint,
                        api_key=uuid4().hex, is_active=True, use_provider_defaults=False,
                        capability_overrides=ModelCapabilitiesOverride(streaming=False),
                        context_window=128000, max_output_tokens=4096))
                    report["baseline"] = "ef28497f; controlled HTTP planning; real default Agent execution only"
                r = await client.put(root + "/case-plans/policy", json=policy.model_dump(mode="json"))
                assert r.status_code == 200, r.text
                request = {"creation_key": "default-complete-plan", "objective": content["objective"], "model_key": model.key, "candidate_case_ids": [cid]}
                if executor_only:
                    request["model_key"] = planning_model.key
                task = await post(root + "/case-planning-tasks", request, 202)
                save("submission.json", task)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT seed FROM {settings.database.postgres_case_planning_task_table} WHERE id=%s", (task["id"],))
                    frozen = cur.fetchone()["seed"]
                assert frozen["response_protocol"] == PLANNING_RESPONSE_PROTOCOL
                report["frozen_response_protocol"] = frozen["response_protocol"]
                report["frozen_linked_source_context_protocol"] = frozen.get("linked_source_context_protocol")
                if os.getenv("QA_STAGED_PLANNING") == "1" or os.getenv("QA_FORMAL_EFFECT_RELATIONS") == "1" or os.getenv("QA_OPERATION_PREFETCH") == "1" or os.getenv("QA_SOURCE_IDENTITY") == "1" or os.getenv("QA_ITEM_OPERATION_SELECTION") == "1":
                    assert frozen["stage_prompts"]["protocol"] == policy.planning_stage_protocol
                    report["frozen_stage_protocol"] = frozen["stage_prompts"]["protocol"]
                planning_queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.case_planning_task_stream,
                    group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                await planning_queue.connect()
                try:
                    assert await PostgresTaskOutbox(settings).relay_once(planning_queue, limit=1) == 1
                    worker = processes.start("default-planning-worker", "-m", "tests.default_model_probe", "src.cli.run_case_planning_worker",
                        "--once", "--worker-id", "default-plan")
                    code = await processes.wait(worker, timeout=220)
                finally:
                    await planning_queue.close()
                task = await get(root + "/case-planning-tasks/" + task["id"])
                save("planning-task.json", task)
                print("default_planning_terminal " + json.dumps({"status": task["status"], "worker_exit": code}), flush=True)
                assert code == 0 and task["status"] == "completed", task
                plan = await get(root + "/case-plans/" + task["plan_id"])
                save("plan.json", plan)
                report["planning_completed"] = True
                report["default_model_planning_completed"] = not executor_only
                if executor_only:
                    save("controlled-planning-requests.json", planning_calls)
                    models.upsert(execution_model)
                    assert model_invocation_parameters(models.get_default_active()) == invocation_parameters
                assert plan["config_snapshot"]["policy"]["effect_coverage_protocol"] == policy.effect_coverage_protocol
                detail = await post(root + "/case-plans/" + plan["id"] + "/runs", {"creation_key": "default-complete-run", "mode_key": "api_testing"}, 201)
                rid = detail["run"]["id"]
                assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                worker = processes.start("default-execution-worker", "-m", "tests.default_model_probe", "src.cli.run_redis_worker",
                    "--once", "--worker-id", "default-execute")
                assert await processes.wait(worker, timeout=420) == 0
                for index in range(12):
                    detail = await get("/runs/" + rid)
                    save(f"run-{index}.json", detail)
                    waiting = next((i for i in detail["items"] if i["status"] == "waiting_approval"), None)
                    if waiting is None:
                        break
                    await post("/run-items/" + waiting["id"] + "/approval", {"approval_id": waiting["approval_id"], "decision": "approved"})
                detail = await get("/runs/" + rid)
                save("final-run.json", detail)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT id,tool_key,status,metadata,input_payload,output_payload FROM {settings.database.postgres_tool_job_table} WHERE metadata->>'run_id'=%s", (rid,))
                    jobs = [dict(row) for row in cur.fetchall()]
                save("PG-tool-jobs.json", jobs)
                if executor_only:
                    # Redeliver the actual owned terminal dispatch. Never reset
                    # a RunItem or infer a new task payload.
                    assert all(i["status"] in {"passed", "failed", "error", "blocked", "skipped"} for i in detail["items"])
                    sends_before = len(list((directory / "physical").glob("*.send.json")))
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE payload->>'run_id'=%s", (rid,))
                        dispatches = [row["payload"] for row in cur.fetchall()]
                    assert len(dispatches) == 1
                    await queue.enqueue(dispatches[0])
                    worker = processes.start("default-execution-terminal", "-m", "tests.default_model_probe",
                        "src.cli.run_redis_worker", "--once", "--worker-id", "default-terminal")
                    assert await processes.wait(worker, timeout=60) == 0
                    assert len(list((directory / "physical").glob("*.send.json"))) == sends_before
                    assert await get("/runs/" + rid) == detail
                    report["terminal_replay_extra_physical_requests"] = 0
                assert all(i["status"] == "passed" for i in detail["items"]), detail
                assert len(created) == 2 and not orders, (effects, orders)
                assert [method for method, _ in effects].count("POST") == 2
                assert [method for method, _ in effects].count("GET") >= 1
                assert [method for method, _ in effects].count("DELETE") == 2
                target = next(r for r in detail["results"] if r["case_id"] == cid)
                assert target["actual"]["case_status"] == "passed"
                coverages = [c for r in detail["results"] for c in r["actual"].get("effect_cleanup_coverage", {}).get("cleanups", {}).values()]
                if any(i.get("cleanup_sources") for i in plan["decision"]["items"]):
                    assert coverages and all(c["status"] == "passed" for c in coverages)
                    assert sum(c["known_object_count"] for c in coverages) >= 2
                # A complete formal case can do its own preparation and cleanup.
                # Prove every actual object against reliable PG task returns even
                # when there is no separate prepare item or coverage declaration.
                native = [j for j in jobs if j["tool_key"] == "api-test-runner"
                    and j["output_payload"].get("task_result")
                    and not j["metadata"].get("case_operation", {}).get("decision") == "replayed"]
                produced = [j["output_payload"]["task_result"]["response_body"]["order.id"]
                    for j in native if j["input_payload"].get("task", {}).get("method") == "POST"]
                removed = [j["output_payload"]["task_result"]["response_body"]["order.id"]
                    for j in native if j["input_payload"].get("task", {}).get("method") == "DELETE"]
                assert set(produced) == set(removed) == set(created) and len(produced) == len(removed) == 2
                assert all(j["status"] == "completed" for j in native)
                report.update(declared_cleanup_scope=bool(coverages), actual_PG_object_cleanup_verified=True,
                    case_internal_cleanup_server_coverage_verified=bool(coverages))
                report["business_execution_completed"] = True
                for p in (directory / "physical").glob("*.request.json"):
                    body = json.loads(p.read_bytes())
                    assert "DO-NOT-INJECT-TEMPLATE" not in json.dumps(body, ensure_ascii=False)
                    for frozen in (c for c in contexts(body) if c.get("effect_coverage_protocol")):
                        assert any(s["content_json"] == content for s in frozen["steps"] if s["phase"] == "case")
        finally:
            report.update(project_id=project["id"] if project else None, task=task, plan=plan, actual_business_HTTP=effects,
                actual_created_ids=created, remaining_orders=deepcopy(orders))
            if task:
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT seed,checkpoint,status FROM {settings.database.postgres_case_planning_task_table} WHERE id=%s", (task["id"],))
                    row = cur.fetchone()
                if row:
                    save("PG-planning.json", dict(row))
            if detail:
                downloaded = []
                for attempt in detail["attempts"]:
                    correction = (attempt.get("checkpoint_payload") or {}).get("receipt_correction")
                    if not correction:
                        continue
                    for key in ("request", "response"):
                        ref = correction.get(key)
                        if not ref:
                            continue
                        owned.append(ref["uri"])
                        raw = (await objects.read_object_uri(ref["uri"]))["content"]
                        assert len(raw) == ref["size_bytes"]
                        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
                        name = "receipt-" + correction["call_id"] + "-" + key + ".json"
                        (directory / name).write_bytes(raw)
                        downloaded.append({"file": name, **ref, "download_verified": True})
                save("receipt-original-downloads.json", downloaded)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT id,tool_key,status,metadata,input_payload,output_payload FROM {settings.database.postgres_tool_job_table} WHERE metadata->>'run_id'=%s", (detail["run"]["id"],))
                    save("PG-tool-jobs.json", [dict(row) for row in cur.fetchall()])
                    sessions = {(i.get("execution_checkpoint") or {}).get("session_id") for i in detail["items"]}
                    sessions.update(((r.get("actual") or {}).get("execution_checkpoint") or {}).get("session_id") for r in detail["results"])
                    sessions.update((a.get("checkpoint_payload") or {}).get("session_id") for a in detail["attempts"])
                    sessions.discard(None)
                    if sessions:
                        # Public model response and numeric/state identity only;
                        # never export the whole graph or private reasoning.
                        cur.execute(f"""SELECT id,session_id,version,stage,
                            graph_state->'model_response_text' AS model_response_text,
                            graph_state->'final_response' AS final_response,
                            graph_state->'context_bundle'->'case_execution' AS case_execution,
                            graph_state->'model_response_summary' AS model_response_summary,
                            graph_state->'model_call_count' AS model_call_count,
                            graph_state->'max_iterations' AS max_iterations
                            FROM {settings.database.postgres_snapshot_table} WHERE session_id=ANY(%s) ORDER BY session_id,version""", (list(sessions),))
                        save("PG-public-receipt-snapshots.json", [dict(row) for row in cur.fetchall()])
                    else:
                        report["public_receipt_snapshot_session_identity_missing"] = True
            collect_objects(settings, owned)
            await processes.close()
            planning_providers.close()
            for p in tmp_path.iterdir():
                if p.suffix in {".stdout", ".stderr"}:
                    shutil.copyfile(p, directory / p.name)
            report['SDK_create_observations'] = [json.loads(p.read_bytes()) for p in sorted((directory/'physical').glob('*.sdk-create.json'))]
            report['SDK_stream_observations'] = [json.loads(p.read_bytes()) for p in sorted((directory/'physical').glob('*.sdk-stream.json'))]
            assert model_invocation_parameters(MySQLModelConfigStore(global_settings).get_default_active()) == parameters
            save("verification.json", report)
