"""Independent authenticated production API over real documents and ToolJobs."""
import json
from uuid import uuid4

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.tools import ToolRegistry
from src.runtime.postgres_business_applicability_store import PostgresBusinessApplicabilityStore, fixed_applicability
from src.runtime.postgres_business_environment_store import PostgresBusinessEnvironmentStore
from src.runtime.postgres_requirement_store import PostgresRequirementStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.tool_runtime import ModelToolCall
from test_business_environment import capture_request


async def decisions_from_production(*, root, review, get, post, client, settings, evidence, targets, sends):
    version = review["requirement_version"]
    before = len(sends)
    jobs = ToolJobService(PostgresToolJobStore(settings)); runtime = ToolRuntimeService(tool_job_service=jobs)
    tool = ToolRegistry().get("api-test-runner")
    context = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()), trace_id=str(uuid4()),
        user_message="正式环境规则观察", normalized_input="正式环境规则观察", context_bundle={"project_id": review["project_id"]})
    states = []
    for index, (url, calls, _) in enumerate(targets):
        root_env = root+"/business-environments"
        registration = await post(root_env, {"request_id": "production-env-"+str(index), "environment_key": "target-"+str(index)}, 201)
        selected = root_env+"/"+registration["id"]
        configured = await client.put(selected+"/configuration", json={"display_name": "实际目标"+str(index), "base_url": url+"/app"})
        assert configured.status_code == 200, configured.text
        snapshot = await post(selected+"/snapshots", capture_request(configured.json(), "production-capture-"+str(index)), 201)
        record = await runtime.execute(tool, ModelToolCall(id="rule-"+str(index), name=tool.key,
            arguments={"worker_action": "execute_task", "task": {"task_id": "rule-"+str(index), "method": "GET", "path": url+"/app/rule"}}), context)
        observation = await post(selected+f"/snapshots/{snapshot['id']}/tool-observations/inspect", {
            "tool_job_id": record.job_id, "output_path_tokens": ["task_result", "response_body", "days"]})
        assert observation["target_status"] == "matched" and observation["value"] == (7 if index == 0 else 9)
        path = selected+f"/snapshots/{snapshot['id']}/applicability-reviews"
        request = {"request_id": "production-decision-"+str(index), "document_id": review["document_id"],
            "requirement_id": version["requirement_id"], "requirement_version_id": version["id"],
            "expected_snapshot_sha256": snapshot["sha256"], "expected_requirement_sha256": version["content_sha256"],
            "coverage_id": "initial", "expected_coverage_sha256": version["content_sha256"],
            "note": "只明确所选保留期规则及完整固定七天用例；其他范围不推断",
            "observations": [{"tool_job_id": record.job_id, "output_path_tokens": observation["output_path_tokens"],
                "expected_output_sha256": observation["output_sha256"], "expected_value_sha256": observation["value_sha256"]}],
            "points": [{"point_key": "retention", "status": "not_effective" if index == 0 else "effective",
                "note": "对照实际规则及原文确认", "observation_indexes": [0]}],
            "cases": [{"case_version_id": version["case_links"][0]["case_version_id"], "point_keys": ["retention"],
                "status": "valid" if index == 0 else "invalid", "note": "旧版本七天断言适用性"}]}
        saved = await post(path, request, 201)
        assert saved["scope"] == "selected_points_and_case_versions" and len(saved["cases"][0]["version"]["content_json"]["steps"]) == 12
        assert fixed_applicability(saved).model_dump(mode="json") == saved
        assert (await get(path, requirement_version_id=version["id"], limit=1))["items"] == [saved]
        assert (await client.post(path, json=request, headers={"Authorization": "Bearer invalid-fixture"})).status_code == 401
        states.append({"path": path, "environment": selected, "snapshot": snapshot, "request": request,
            "review": saved, "job": (await jobs.get_job(record.job_id)).model_dump(mode="json"), "HTTP": calls})
    revoke = {**states[1]["request"], "request_id": "production-revoke", "action": "revoke",
        "expected_review_id": states[1]["review"]["id"], "points": [], "cases": [], "observations": [], "note": "撤回并保留原确认"}
    revoked = await post(states[1]["path"], revoke, 201)
    assert revoked["revision"] == 2
    assert (await get(states[1]["path"], requirement_version_id=version["id"], limit=1))["items"] == [revoked]
    assert len(sends) == before and all(len(target[1]) == 1 for target in targets)
    state = {"targets": states, "revoke_request": revoke, "revoked": revoked,
        "model_calls_during_decision": 0, "explicit_reviews_are_fixture": True, "run_restrictions_implemented": False}
    (evidence/"environment-decisions.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


async def decisions_after_restart(*, state, get, post, client, settings, evidence, root, sends):
    before = len(sends)
    for target in state["targets"]:
        assert await post(target["path"], target["request"], 201) == target["review"]
        assert await get(target["path"]+"/by-request", request_id=target["request"]["request_id"]) == target["review"]
        await post(target["environment"]+"/archive", {})
    await post(root+"/archive", {})
    with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
        for target in state["targets"]:
            cur.execute(f"DELETE FROM `{settings.database.application_config_table}` WHERE config_key=%s AND scope_key=%s",
                ("project.environment_target", target["snapshot"]["environment_id"]))
        conn.commit()
    for target in state["targets"]:
        assert await post(target["path"], target["request"], 201) == target["review"]
        assert await get(target["path"]+"/"+target["review"]["id"]) == target["review"]
        rejected = await client.post(target["path"], json={**target["request"], "request_id": "new-archived"})
        assert rejected.status_code == 409
        assert len(target["HTTP"]) == 1
    assert await post(state["targets"][1]["path"], state["revoke_request"], 201) == state["revoked"]
    store = PostgresBusinessApplicabilityStore(requirements=PostgresRequirementStore(settings), environments=PostgresBusinessEnvironmentStore(settings))
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {store.table} ORDER BY created_at,id")
        records = [row["record"] for row in cur.fetchall()]
    assert records == [*[t["review"] for t in state["targets"]], state["revoked"]]
    assert all(fixed_applicability(r).model_dump(mode="json") == r for r in records)
    assert len(sends) == before
    (evidence/"environment-decisions-restart.json").write_text(json.dumps({"records": records,
        "API_restarted": True, "archived_history_preserved": True, "current_target_configuration_removed": True,
        "recovery_new_HTTP": 0, "default_model_executed": False}, ensure_ascii=False, indent=2), encoding="utf-8")
