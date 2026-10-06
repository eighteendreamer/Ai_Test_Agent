"""Actual authenticated API -> Outbox -> independent planning CLI -> fixed run."""
import json

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox


def planning_response(context):
    cases = [e for e in context["evidence"] if e["kind"] == "case"]
    assert len(cases) == 1
    case = cases[0]
    assert len(case["payload"]["content"]["steps"]) == 12
    assert case["payload"]["content"]["深层扩展"] == {"零": 0, "空": None, "布尔": False, "未知字段": "不能丢失"}
    return json.dumps({"action": "finish", "summary": "按固定七天目标与完整十二步用例规划", "items": [{
        "key": "target", "kind": "target", "case_id": case["source_id"], "case_version_id": case["version"],
        "reason": "明确选择完整用例及服务端固定环境", "evidence_ids": [case["id"]]}]}, ensure_ascii=False)


async def planning_from_production(*, root, state, case, model_key, settings, processes, get, post, client, evidence, sends):
    snapshot = state["targets"][0]["snapshot"]
    await post(f"/test-cases/{case.case.id}/submit-review", {"version_id": case.version.id, "request_id": "planning-review"})
    await post(f"/test-cases/{case.case.id}/activate", {"version_id": case.version.id, "request_id": "planning-activate"})
    request = {"creation_key": "production-fixed-planning", "objective": "在固定七天目标读取完整用例",
        "model_key": model_key, "candidate_case_ids": [case.case.id], "runtime_context": {"flag": False, "empty": None, "count": 0},
        "environment": {"environment_id": snapshot["environment_id"], "snapshot_id": snapshot["id"], "expected_snapshot_sha256": snapshot["sha256"]}}
    route = root+"/case-planning-tasks"
    before = len(sends)
    task = await post(route, request, 202)
    assert len(sends) == before
    assert (await client.post(route, json=request, headers={"Authorization": "Bearer invalid-fixture"})).status_code == 401
    changed = await client.put(state["targets"][0]["environment"]+"/configuration",
        json={"display_name": "提交后改址", "base_url": "http://127.0.0.1:1/different"})
    assert changed.status_code == 200, changed.text
    stream = settings.orchestration.case_planning_task_stream
    queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
    await queue.connect()
    try:
        assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
        child = processes.start("environment-planning-worker", "-m", "src.cli.run_case_planning_worker",
            "--once", "--worker-id", "environment-planning-worker")
        assert await processes.wait(child) == 0
    finally:
        await queue.close()
    done = await get(route+"/"+task["id"])
    (evidence/"environment-planning-attempt.json").write_text(json.dumps({"task": done,
        "request": request, "physical_requests": sends, "expected_planning_calls": 1},
        ensure_ascii=False, indent=2), encoding="utf-8")
    assert done["status"] == "completed", done
    assert len(sends) == before+1
    plan = await get(root+"/case-plans/"+done["plan_id"])
    assert plan["environment"]["snapshot"] == snapshot
    assert plan["environment"]["reviews"] == [state["targets"][0]["review"]]
    assert plan["decision"]["items"][0]["case_version_id"] == case.version.id
    assert len(plan["evidence"][0]["payload"]["content"]["steps"]) == 12
    launch = {"creation_key": "production-bound-run", "mode_key": "api_testing"}
    detail = await post(root+"/case-plans/"+plan["id"]+"/runs", launch, 201)
    assert detail["run"]["plan_snapshot"]["plan"]["environment"] == plan["environment"]
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT seed FROM {settings.database.postgres_case_planning_task_table} WHERE id=%s", (task["id"],))
        seed = cur.fetchone()["seed"]
        cur.execute(f"SELECT record FROM {settings.database.postgres_test_run_table} WHERE id=%s", (detail["run"]["id"],))
        sql_run = cur.fetchone()["record"]
    assert seed["plan"]["environment"] == plan["environment"] == sql_run["plan_snapshot"]["plan"]["environment"]
    value = {"task_path": route, "request": request, "task": done, "seed": seed, "plan": plan,
        "launch": launch, "detail": detail, "sql_run": sql_run, "physical_planning_calls": 1,
        "independent_planning_CLI": True, "claim_restrictions_implemented": False, "autonomous_model": False}
    (evidence/"environment-planning.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value


async def planning_after_restart(*, state, root, get, post, client, evidence, settings, sends):
    before = len(sends)
    assert await get(state["task_path"]+"/by-request", creation_key=state["request"]["creation_key"]) == state["task"]
    # Dispatch disabled and project/environment archived by the preceding scenario:
    # fixed submission and run recovery must happen before new-task guards.
    assert await post(state["task_path"], state["request"], 202) == state["task"]
    assert await get(root+"/case-plans/"+state["plan"]["id"]) == state["plan"]
    assert await post(root+"/case-plans/"+state["plan"]["id"]+"/runs", state["launch"], 201) == state["detail"]
    assert await get("/runs/"+state["detail"]["run"]["id"]) == state["detail"]
    rejected = await client.post(root+"/case-plans/"+state["plan"]["id"]+"/runs",
        json={**state["launch"], "creation_key": "new-after-archive"})
    assert rejected.status_code in {409, 503}
    assert len(sends) == before
    (evidence/"environment-planning-restart.json").write_text(json.dumps({"API_restarted": True,
        "archived_history_preserved": True, "current_config_removed": True, "recovery_new_model_HTTP": 0,
        "creation_hash": state["detail"]["run"]["creation_hash"], "new_run_rejected_status": rejected.status_code,
        "claim_restrictions_implemented": False}, ensure_ascii=False, indent=2), encoding="utf-8")
