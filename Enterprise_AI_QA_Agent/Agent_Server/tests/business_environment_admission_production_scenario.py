"""Independent API/CLI/SDK Agent execution, preserving first-start confirmation."""
import json
import re
from html import unescape

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.run_management import TestRunRecord as RunRecord


def save(evidence,name,value):
    (evidence/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")


def admission_message(body):
    contexts = []
    for message in body["messages"]:
        if message.get("role") != "system":
            continue
        for raw in re.findall(r'<untrusted_content[^>]*>\s*(.*?)\s*</untrusted_content>',message["content"],re.S):
            try: value = json.loads(unescape(raw))
            except json.JSONDecodeError: continue
            if isinstance(value,dict) and value.get("environment_admission") and "target_key" in value:
                contexts.append(value)
    if not contexts:
        return None
    assert len(contexts) == 1
    context = contexts[0]
    assert context["environment_admission"]["decision"] == "no_explicit_invalid"
    assert "first start" in "\n".join(m["content"] for m in body["messages"] if m.get("role") == "system")
    content = next(s["content_json"] for s in context["steps"] if s["item"]["key"] == context["target_key"])
    assert len(content["steps"]) == 12 and content["expected"]["deleted_at"] is None
    if not any(m.get("role") == "tool" for m in body["messages"]):
        path = context["business_environment"]["snapshot"]["configuration"]["payload"]["base_url"]+"/rule"
        args = {"worker_action":"execute_task","task":{"task_id":"admission-rule","method":"GET","path":path}}
        return {"role":"assistant","content":"读取固定目标规则","tool_calls":[{"id":"admission-rule","type":"function",
            "function":{"name":"api-test-runner","arguments":json.dumps(args,ensure_ascii=False)}}]}
    checks = [{"name":field,"operator":"equals","expected_path_tokens":["expected",field],
        "observations":[{"call_id":"admission-rule","output_path_tokens":["task_result","response_status"]
            if field == "code" else ["task_result","response_body",field]}]}
        for field in ["code","days","expired_count","expired","deleted_at"]]
    receipt = {"schema_version":1,"steps":[{"item_key":context["target_key"],"status":"passed",
        "summary":"按实际工具证据核验固定五项预期","checks":checks}]}
    return {"role":"assistant","content":json.dumps(receipt,ensure_ascii=False)}


async def admission_from_production(*,root,state,case,model_key,settings,processes,get,post,client,evidence,sends):
    target = state["targets"][0]
    snapshot = target["snapshot"]
    content = {"expected":{"code":200,"days":7,"expired_count":0,"expired":False,"deleted_at":None},
        "深层扩展":{"零":0,"空":None,"布尔":False,"未知字段":"不能丢失"},
        "steps":[{"order":i+1,"action":action} for i,action in enumerate([
            "使用固定环境地址","GET /rule 一次","核对状态码200","核对JSON响应",
            "保留期限为7天","过期数为0","expired为False","deleted_at为null",
            "记录实际响应来源","保持固定版本","unknown不等于已生效","保存工具证据和逐字段结论"])]}
    created = await post(root+"/test-cases",{"case_key":"admission-real-rule","title":"实际七天规则与空值验证",
        "mode_key":"api_testing","case_type":"json","content_json":content,
        "execution_notes":"只读取固定环境的/rule，用已有api-test-runner；逐字段对照expected并保留真实证据。",
        "source_refs":[ref.model_dump(mode="json") for ref in case.version.source_refs],
        "model_key":model_key,"prompt_version":"admission-fixture-v1","skill_versions":{"manual":"v1"}},201)
    cid,vid = created["case"]["id"],created["version"]["id"]
    await post(f"/test-cases/{cid}/submit-review",{"version_id":vid,"request_id":"admission-review"})
    await post(f"/test-cases/{cid}/activate",{"version_id":vid,"request_id":"admission-activate"})
    command = {"creation_key":"production-admission-planning","objective":"读取固定七天规则并验证实际返回",
        "model_key":model_key,"candidate_case_ids":[cid],"environment":{"environment_id":snapshot["environment_id"],
            "snapshot_id":snapshot["id"],"expected_snapshot_sha256":snapshot["sha256"]}}
    route = root+"/case-planning-tasks"
    before = len(sends)
    task = await post(route,command,202)
    queue = RedisTaskQueue(settings.database.redis_url,stream=settings.orchestration.case_planning_task_stream,
        group=settings.orchestration.redis_task_consumer_group,socket_timeout_seconds=1)
    await queue.connect()
    try:
        assert await PostgresTaskOutbox(settings).relay_once(queue,limit=1) == 1
        child = processes.start("admission-planning-worker","-m","src.cli.run_case_planning_worker",
            "--once","--worker-id","admission-planning-worker")
        assert await processes.wait(child) == 0
    finally: await queue.close()
    done = await get(route+"/"+task["id"])
    assert done["status"] == "completed" and len(sends) == before+1
    plan = await get(root+"/case-plans/"+done["plan_id"])
    assert plan["environment"]["reviews"] == [target["review"]]
    launch = {"creation_key":"production-admission-run","mode_key":"api_testing"}
    detail = await post(root+"/case-plans/"+plan["id"]+"/runs",launch,201)
    request = {**target["request"],"request_id":"admission-current-unknown","expected_review_id":target["review"]["id"],
        "points":[{**p,"status":"unknown"} for p in target["request"]["points"]],
        "cases":[{**c,"status":"unknown"} for c in target["request"]["cases"]]}
    unknown = await post(target["path"],request,201)
    with postgres_connect(settings) as conn,conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("test_run_dispatch:"+detail["run"]["id"],))
        delivery = cur.fetchone()["payload"]
    queue = RedisTaskQueue(settings.database.redis_url,stream=settings.orchestration.redis_task_stream,
        group=settings.orchestration.redis_task_consumer_group,socket_timeout_seconds=1)
    await queue.connect()
    try:
        await queue.enqueue(delivery)
        child = processes.start("admission-execution-worker","-m","src.cli.run_redis_worker",
            "--once","--worker-id","admission-execution-worker")
        assert await processes.wait(child) == 0
        detail = await get("/runs/"+detail["run"]["id"])
        save(evidence,"environment-admission-worker-attempt.json",{"detail":detail,"physical_requests":sends})
        if detail["items"][0]["status"] == "waiting_approval":
            item = detail["items"][0]
            await post("/run-items/"+item["id"]+"/approval",{"approval_id":item["approval_id"],"decision":"approved"})
            detail = await get("/runs/"+detail["run"]["id"])
        save(evidence,"environment-admission-execution-attempt.json",{"detail":detail,"physical_requests":sends})
        assert detail["run"]["status"] == "completed" and detail["items"][0]["status"] == "passed"
        assert len(sends) == before+3 and [len(t["HTTP"]) for t in state["targets"]] == [2,1]
        result = detail["results"][0]
        receipt = result["actual"]["environment_admission"]
        assert receipt["reviews"] == [unknown] and receipt["decision"] == "no_explicit_invalid"
        original = next(a for a in detail["attempts"] if a["id"] == receipt["execution_attempt_id"])
        assert original["environment_admission"] == receipt and original["started_at"] == receipt["checked_at"]
        with postgres_connect(settings) as conn,conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {settings.database.postgres_test_run_table} WHERE id=%s",(detail["run"]["id"],))
            sql_run = cur.fetchone()["record"]
        assert RunRecord.model_validate(sql_run).model_dump(mode="json") == detail["run"]
        # Replay the actual terminal delivery; the original graph/result cannot run again.
        await queue.enqueue(delivery)
        child = processes.start("admission-terminal-worker","-m","src.cli.run_redis_worker",
            "--once","--worker-id","admission-terminal-worker")
        assert await processes.wait(child) == 0
        assert await get("/runs/"+detail["run"]["id"]) == detail and len(sends) == before+3
    finally: await queue.close()
    value = {"task_path":route,"request":command,"task":done,"plan":plan,"launch":launch,"detail":detail,
        "additional_reviews":[unknown],"physical_model_calls":3,"actual_HTTP_counts":[2,1],
        "case":created,"SQL_run":sql_run,"physical_requests":sends,"actual_terminal_dispatch":delivery,
        "independent_planning_CLI":True,"independent_execution_CLI":True,"actual_SDK_HTTP":True,
        "controlled_model":True,"autonomous_external_model_verified":False,"terminal_replay_new_HTTP":0}
    save(evidence,"environment-admission.json",value)
    return value


async def admission_after_restart(*,state,root,get,post,evidence,sends):
    before = len(sends)
    assert await post(state["task_path"],state["request"],202) == state["task"]
    assert await get(root+"/case-plans/"+state["plan"]["id"]) == state["plan"]
    assert await post(root+"/case-plans/"+state["plan"]["id"]+"/runs",state["launch"],201) == state["detail"]
    assert await get("/runs/"+state["detail"]["run"]["id"]) == state["detail"]
    assert len(sends) == before
    save(evidence,"environment-admission-restart.json",{"API_restarted":True,"archived_history_preserved":True,
        "current_config_removed":True,"new_model_HTTP":0,"result_id":state["detail"]["results"][0]["id"]})
