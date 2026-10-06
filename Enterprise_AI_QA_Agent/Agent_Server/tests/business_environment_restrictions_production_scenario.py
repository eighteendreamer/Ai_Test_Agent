"""Independent production processes, exact restriction and immutable replay."""
import json

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.application.documents.requirement_execution_checks import fixed_result
from src.schemas.run_management import TestRunRecord as RunRecord


def save(evidence,name,value):
    (evidence/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")


async def restrictions_from_production(*,root,state,case,model_key,settings,processes,get,post,client,evidence,sends):
    target = state["targets"][1]
    request = {**target["request"],"request_id":"restriction-confirm-again",
        "expected_review_id":state["revoked"]["id"]}
    invalid = await post(target["path"],request,201)
    snapshot = target["snapshot"]
    command = {"creation_key":"production-restriction-planning","objective":"在固定九天目标复核旧七天完整用例",
        "model_key":model_key,"candidate_case_ids":[case.case.id],
        "environment":{"environment_id":snapshot["environment_id"],"snapshot_id":snapshot["id"],
            "expected_snapshot_sha256":snapshot["sha256"]}}
    route = root+"/case-planning-tasks"
    before = len(sends)
    task = await post(route,command,202)
    queue = RedisTaskQueue(settings.database.redis_url,stream=settings.orchestration.case_planning_task_stream,
        group=settings.orchestration.redis_task_consumer_group,socket_timeout_seconds=1)
    await queue.connect()
    try:
        assert await PostgresTaskOutbox(settings).relay_once(queue,limit=1) == 1
        child = processes.start("restriction-planning-worker","-m","src.cli.run_case_planning_worker",
            "--once","--worker-id","restriction-planning-worker")
        assert await processes.wait(child) == 0
    finally:
        await queue.close()
    done = await get(route+"/"+task["id"])
    save(evidence,"environment-restriction-planning-attempt.json",{"task":done,"request":command,
        "physical_requests":sends,"expected_planning_calls":1})
    assert done["status"] == "completed" and len(sends) == before+1
    plan = await get(root+"/case-plans/"+done["plan_id"])
    assert plan["environment"]["snapshot"] == snapshot and plan["environment"]["reviews"] == [invalid]
    launch = {"creation_key":"production-restriction-run","mode_key":"api_testing"}
    detail = await post(root+"/case-plans/"+plan["id"]+"/runs",launch,201)
    assert detail["run"]["status"] == "completed" and detail["run"]["stats"]["blocked"] == 1
    assert detail["items"][0]["case_version_id"] == case.version.id and detail["items"][0]["status"] == "blocked"
    result = fixed_result(detail["results"][0])
    assert result.actual["business_execution_started"] is False
    assert result.actual["environment_restriction"]["reviews"] == [invalid]
    assert result.actual["environment_restriction"]["current_reviews"] == [invalid]
    with postgres_connect(settings) as conn,conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {settings.database.postgres_test_run_table} WHERE id=%s",(detail["run"]["id"],))
        sql_run = cur.fetchone()["record"]
        save(evidence,"environment-restriction-run-captured.json",{"sql_run":sql_run,"detail":detail,"plan":plan})
        assert RunRecord.model_validate(sql_run).model_dump(mode="json") == detail["run"]
        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("test_run_dispatch:"+detail["run"]["id"],))
        delivery = cur.fetchone()["payload"]
    # Deliver only the actual terminal run. The preceding C1 run is still queued.
    queue = RedisTaskQueue(settings.database.redis_url,stream=settings.orchestration.redis_task_stream,
        group=settings.orchestration.redis_task_consumer_group,socket_timeout_seconds=1)
    await queue.connect()
    try:
        await queue.enqueue(delivery)
        child = processes.start("restriction-terminal-worker","-m","src.cli.run_redis_worker",
            "--once","--worker-id","restriction-terminal-worker")
        assert await processes.wait(child) == 0
    finally:
        await queue.close()
    assert await get("/runs/"+detail["run"]["id"]) == detail and len(sends) == before+1
    assert all(len(t["HTTP"]) == 1 for t in state["targets"])
    revoke = {**request,"request_id":"restriction-revoke-after-block","expected_review_id":invalid["id"],
        "action":"revoke","points":[],"cases":[],"observations":[]}
    revoked = await post(target["path"],revoke,201)
    assert await post(root+"/case-plans/"+plan["id"]+"/runs",launch,201) == detail
    value = {"task_path":route,"request":command,"task":done,"plan":plan,"launch":launch,"detail":detail,
        "additional_reviews":[invalid,revoked],"physical_planning_calls":1,"business_HTTP_during_restriction":0,
        "actual_terminal_dispatch":delivery,"sql_run":sql_run,"independent_planning_CLI":True,"independent_execution_CLI":True,
        "autonomous_model":False,"allowed_decision_receipt_implemented":False}
    save(evidence,"environment-restriction.json",value)
    return value


async def restrictions_after_restart(*,state,root,get,post,evidence,sends):
    before = len(sends)
    assert await post(state["task_path"],state["request"],202) == state["task"]
    assert await get(root+"/case-plans/"+state["plan"]["id"]) == state["plan"]
    assert await post(root+"/case-plans/"+state["plan"]["id"]+"/runs",state["launch"],201) == state["detail"]
    assert await get("/runs/"+state["detail"]["run"]["id"]) == state["detail"]
    assert len(sends) == before
    save(evidence,"environment-restriction-restart.json",{"API_restarted":True,"archived_history_preserved":True,
        "revoked_blocked_result_preserved":True,"current_config_removed":True,"new_model_HTTP":0,
        "creation_hash":state["detail"]["run"]["creation_hash"],"result_id":state["detail"]["results"][0]["id"]})
