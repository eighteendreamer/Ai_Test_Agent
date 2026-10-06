"""Actual files, independent fixed targets and append-only scoped decisions."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from psycopg.types.json import Jsonb

from src.api.routes.business_applicability import router
from src.api.routes.business_environments import router as environment_router
from src.application.projects.business_applicability_service import BusinessApplicabilityService
from src.application.projects.business_environment_service import BusinessEnvironmentService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.postgres_business_applicability_store import PostgresBusinessApplicabilityStore, fixed_applicability
from src.runtime.postgres_business_environment_store import PostgresBusinessEnvironmentStore, environment_digest
from src.schemas.business_applicability import BusinessApplicabilityRequest
from src.schemas.project import ProjectCreateRequest
from src.schemas.tool_runtime import ModelToolCall
from test_api_http_origin import fixed_target
from test_business_environment import capture_request
from test_requirement_execution import execution_environment, retention_http
from test_requirement_reviews import LIVE, save_evidence
from test_requirement_reviews import payload as requirement_payload


def decision_request(f, index=0, *, key="review", status="not_effective", case_status="valid", previous=None):
    observed = f.observations[index]
    return {"request_id": key, "document_id": f.values[2].id, "requirement_id": f.version["requirement_id"],
        "requirement_version_id": f.version["id"], "expected_requirement_sha256": f.version["content_sha256"],
        "coverage_id": "initial", "expected_coverage_sha256": f.version["content_sha256"],
        "expected_snapshot_sha256": f.snapshots[index]["sha256"], "expected_review_id": previous,
        "note": "明确评审所选保留期点和原七天用例；边界点仍未知",
        "observations": [{"tool_job_id": observed["tool_job_id"], "output_path_tokens": observed["output_path_tokens"],
            "expected_output_sha256": observed["output_sha256"], "expected_value_sha256": observed["value_sha256"]}],
        "points": [{"point_key": "retention", "status": status, "note": "对照九天需求正文和实际响应", "observation_indexes": [0]}],
        "cases": [{"case_version_id": f.values[4].version.id, "point_keys": ["retention"], "status": case_status,
            "note": "只确认这个固定旧版本在选定目标是否适用"}]}


@asynccontextmanager
async def decisions_environment(tmp_path, business_http, extension="md"):
    async with execution_environment(tmp_path, business_http, extension) as f:
        env_store = PostgresBusinessEnvironmentStore(f.env.settings)
        env_service = BusinessEnvironmentService(store=env_store, configs=f.env.configs,
            projects=f.env.projects, tool_jobs=f.jobs._store)
        store = PostgresBusinessApplicabilityStore(requirements=f.req_store, environments=env_store)
        owned = [store.table, *env_store.tables]
        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
            for table in owned:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,)); assert cur.fetchone()["existing"] is None
        await env_service.initialize()
        service = BusinessApplicabilityService(store=store, projects=f.env.projects, environments=env_service)
        await service.initialize()
        app = FastAPI(); app.state.business_environment_service = env_service; app.state.business_applicability_service = service
        app.include_router(environment_router, prefix="/api/v1"); app.include_router(router, prefix="/api/v1")
        try:
            with fixed_target(7) as (old, old_calls, redirects), fixed_target(9) as (new, new_calls, _):
                redirects["/app/cross"] = new + "/app/rule"
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://actual-targets/api/v1") as client:
                    f.decision_client, f.decision_store, f.env_service = client, store, env_service
                    f.env_roots, f.snapshots, f.decision_routes, f.observations, f.origin_jobs = [], [], [], [], []
                    f.actual_target_calls = [old_calls, new_calls]
                    for key, url in [("old", old), ("new", new)]:
                        root = f"/projects/{f.project.id}/business-environments"
                        registration = (await client.post(root, json={"request_id": "register-"+key, "environment_key": key})).json()
                        selected = root+"/"+registration["id"]
                        config = await client.put(selected+"/configuration", json={"display_name": key, "base_url": url+"/app"})
                        assert config.status_code == 200, config.text
                        snapshot = await client.post(selected+"/snapshots", json=capture_request(config.json(), "capture-"+key))
                        assert snapshot.status_code == 201, snapshot.text
                        f.env_roots.append(selected); f.snapshots.append(snapshot.json())
                        f.decision_routes.append(selected+f"/snapshots/{snapshot.json()['id']}/applicability-reviews")
                    runtime = ToolRuntimeService(tool_job_service=f.jobs); tool = ToolRegistry().get("api-test-runner")
                    tc = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()), trace_id=str(uuid4()),
                        user_message="实际规则观察", normalized_input="实际规则观察", context_bundle={"project_id": f.project.id})
                    for index, url in enumerate([old+"/app/rule", new+"/app/rule", old+"/app/cross"]):
                        record = await runtime.execute(tool, ModelToolCall(id="origin-"+str(index), name=tool.key,
                            arguments={"worker_action": "execute_task", "task": {"task_id": "origin-"+str(index), "method": "GET", "path": url}}), tc)
                        f.origin_jobs.append(await f.jobs.get_job(record.job_id))
                        scope = f.env_roots[min(index, 1)] if index < 2 else f.env_roots[0]
                        snap = f.snapshots[min(index, 1)] if index < 2 else f.snapshots[0]
                        reply = await client.post(scope+f"/snapshots/{snap['id']}/tool-observations/inspect", json={
                            "tool_job_id": record.job_id, "output_path_tokens": ["task_result", "response_body", "days"]})
                        assert reply.status_code == 200, reply.text
                        f.observations.append(reply.json())
                    assert [x["value"] for x in f.observations] == [7, 9, 9]
                    assert [x["target_status"] for x in f.observations] == ["matched", "matched", "mismatch"]
                    yield f
        finally:
            with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
                for table in owned: cur.execute(f'DROP TABLE "{table}"')


def save_decision_evidence(f, name, **extra):
    with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {f.decision_store.table} ORDER BY created_at,id")
        rows = [dict(row) for row in cur.fetchall()]
    save_evidence("applicability-"+name, f.paths, snapshots=f.snapshots, observations=f.observations,
        jobs=[job.model_dump(mode="json") for job in f.origin_jobs], records=rows,
        actual_HTTP=f.actual_target_calls, requirement=f.version, coverage=f.coverage,
        original_case=f.values[4].model_dump(mode="json"), full_case=f.selected.model_dump(mode="json"),
        physical_model_requests=f.values[9], explicit_review_is_test_fixture=True, automatic_deployment_inference=False,
        inspection_new_http_calls=0, full_P0_P5_complete=False, **extra)


def test_decision_schema_preserves_scope_and_rejects_inferred_invalid_or_empty_revoke():
    # Run the public schema on actual IDs; malformed decisions must fail before SQL.
    request = {"request_id": "schema", "document_id": str(uuid4()), "requirement_id": str(uuid4()),
        "requirement_version_id": str(uuid4()), "expected_snapshot_sha256": "a"*64,
        "expected_requirement_sha256": "b"*64, "coverage_id": "initial", "expected_coverage_sha256": "b"*64,
        "note": "尚无可靠业务观察", "points": [{"point_key": "retention", "status": "unknown", "note": "缺来源"}],
        "cases": [{"case_version_id": str(uuid4()), "point_keys": ["retention"], "status": "unknown", "note": "待查"}]}
    assert BusinessApplicabilityRequest.model_validate(request).points[0].status == "unknown"
    for changes in [{"action": "revoke"}, {"cases": [{**request["cases"][0], "status": "invalid"}]},
        {"points": [{**request["points"][0], "status": "effective"}]}, {"deployment_status": "effective"},
        {"points": [*request["points"], *request["points"]]}]:
        with pytest.raises(ValidationError): BusinessApplicabilityRequest.model_validate({**request, **changes})


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_versions_targets_partial_scope_revoke_and_immutable_archive(tmp_path, retention_http, monkeypatch, extension):
    async with decisions_environment(tmp_path, retention_http, extension) as f:
        client = f.decision_client
        matrix_before = (await f.client.get(f.stem+"/matrix")).json()
        first_request = decision_request(f)
        first_response = await client.post(f.decision_routes[0], json=first_request)
        assert first_response.status_code == 201, first_response.text
        first = first_response.json()
        second_request = decision_request(f, 1, key="new-effective", status="effective", case_status="invalid")
        second_response = await client.post(f.decision_routes[1], json=second_request)
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()
        assert first["request"]["points"][0]["status"] == "not_effective" and first["cases"][0]["status"] == "valid"
        assert second["request"]["points"][0]["status"] == "effective" and second["cases"][0]["status"] == "invalid"
        assert first["revision"] == second["revision"] == 1
        assert [p["point_key"] for p in second["request"]["points"]] == ["retention"]
        assert second["cases"][0]["version"] == f.values[4].version.model_dump(mode="json")
        assert (await f.client.get(f.stem+"/matrix")).json() == matrix_before
        revoke = {**second_request, "request_id": "revoke", "action": "revoke", "expected_review_id": second["id"],
            "points": [], "cases": [], "observations": [], "note": "业务评审撤回；相关范围恢复未知，旧确认保留"}
        response = await client.post(f.decision_routes[1], json=revoke)
        assert response.status_code == 201, response.text
        revoked = response.json()
        assert revoked["revision"] == 2 and revoked["previous_review_id"] == second["id"] and revoked["cases"] == []
        after_revoke = await client.get(f.decision_routes[1], params={"requirement_version_id": f.version["id"], "limit": 1})
        assert after_revoke.json()["items"] == [revoked] and after_revoke.json()["has_more"]
        assert (await client.get(f.decision_routes[1]+"/"+second["id"])).json() == second
        restored_request = {**second_request, "request_id": "reconfirm", "expected_review_id": revoked["id"]}
        restored = await client.post(f.decision_routes[1], json=restored_request)
        assert restored.status_code == 201 and restored.json()["revision"] == 3
        await client.put(f.env_roots[1]+"/configuration", json={"display_name": "改址", "base_url": "http://127.0.0.1:1"})
        await client.post(f.env_roots[1]+"/archive"); await f.env.projects.archive(f.project.id)
        async def forbidden(*args, **kwargs): raise AssertionError("恢复不允许读当前配置、工具、原件或模型")
        with monkeypatch.context() as patch:
            patch.setattr(f.env.configs, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("current config")))
            patch.setattr(f.jobs, "get_job", forbidden); patch.setattr(f.env.objects, "read_object_uri", forbidden)
            patch.setattr(f.env.impact_runtime, "invoke", forbidden)
            assert (await client.post(f.decision_routes[1], json=second_request)).json() == second
            assert (await client.get(f.decision_routes[1]+"/by-request", params={"request_id": "new-effective"})).json() == second
            assert (await client.post(f.decision_routes[1], json={**second_request, "request_id": "archived-new"})).status_code == 409
        assert [len(x) for x in f.actual_target_calls] == [2, 2]
        save_decision_evidence(f, extension, first=first, second=second, revoked=revoked, reconfirmed=restored.json(),
            matrix_unchanged=matrix_before, recovery_no_network_or_current_config=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_lost_commit_concurrent_first_and_revision_conflicts(tmp_path, retention_http, monkeypatch):
    async with decisions_environment(tmp_path, retention_http) as f:
        request = decision_request(f)
        original = f.decision_store._save
        def lost(*args):
            original(*args); raise ConnectionError("actual committed decision reply lost")
        with monkeypatch.context() as patch:
            patch.setattr(f.decision_store, "_save", lost)
            assert (await f.decision_client.post(f.decision_routes[0], json=request)).status_code == 503
        recovered = await f.decision_client.get(f.decision_routes[0]+"/by-request", params={"request_id": request["request_id"]})
        assert recovered.status_code == 200
        first = recovered.json()
        replies = await asyncio.gather(*[f.decision_client.post(f.decision_routes[0], json=request) for _ in range(4)])
        assert all(r.status_code == 201 and r.json() == first for r in replies)
        other = decision_request(f, 1, key="race", status="effective", case_status="invalid")
        first_race = await asyncio.gather(*[f.decision_client.post(f.decision_routes[1], json=other) for _ in range(4)])
        assert all(r.status_code == 201 and r.json() == first_race[0].json() for r in first_race)
        updates = [{**other, "request_id": "different-"+str(i), "expected_review_id": first_race[0].json()["id"]} for i in range(2)]
        changed = await asyncio.gather(*[f.decision_client.post(f.decision_routes[1], json=body) for body in updates])
        assert sorted(r.status_code for r in changed) == [201, 409]
        assert (await f.decision_client.post(f.decision_routes[0], json={**request, "note": "changed"})).status_code == 409
        save_decision_evidence(f, "races", recovered=first, first_race=[r.json() for r in first_race],
            revision_statuses=[r.status_code for r in changed], actual_committed_reply_lost=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_wrong_basis_scope_source_and_sql_tamper(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        client, route = f.decision_client, f.decision_routes[0]
        request = decision_request(f)
        statuses = {}
        for key in ["expected_snapshot_sha256", "expected_requirement_sha256", "expected_coverage_sha256"]:
            reply = await client.post(route, json={**request, "request_id": key, key: "0"*64})
            assert reply.status_code == 409, reply.text
            statuses[key] = reply.status_code
        for key in ["document_id", "requirement_id", "requirement_version_id"]:
            reply = await client.post(route, json={**request, "request_id": key, key: str(uuid4())})
            assert reply.status_code == 404, reply.text
        wrong_point = deepcopy(request); wrong_point.update(request_id="wrong-point", cases=[])
        wrong_point["points"][0]["point_key"] = "not-in-requirement"
        assert (await client.post(route, json=wrong_point)).status_code == 409
        wrong_case = deepcopy(request); wrong_case["request_id"] = "wrong-case"; wrong_case["cases"][0]["case_version_id"] = f.selected.version.id
        assert (await client.post(route, json=wrong_case)).status_code == 409
        wrong_value = deepcopy(request); wrong_value["request_id"] = "wrong-value"; wrong_value["observations"][0]["expected_value_sha256"] = "0"*64
        assert (await client.post(route, json=wrong_value)).status_code == 409
        cross = deepcopy(request); cross["request_id"] = "cross-origin"; cross["observations"][0].update(
            tool_job_id=f.observations[2]["tool_job_id"], expected_output_sha256=f.observations[2]["output_sha256"],
            expected_value_sha256=f.observations[2]["value_sha256"])
        assert (await client.post(route, json=cross)).status_code == 409
        cross["points"][0]["status"] = "unknown"; cross["cases"][0]["status"] = "unknown"
        saved = await client.post(route, json=cross)
        assert saved.status_code == 201, saved.text
        review = saved.json()
        assert review["observations"][0]["target_status"] == "mismatch"
        assert review["snapshot"]["deployment_status"] == "unknown"
        other = await f.env.projects.create(ProjectCreateRequest(project_key="foreign-decision", name="隔离"))
        assert (await client.get(route.replace(f.project.id, other.id)+"/"+review["id"])).status_code == 404
        altered = deepcopy(review); altered["observations"][0]["value"] = 7
        altered["sha256"] = environment_digest({k: v for k, v in altered.items() if k != "sha256"})
        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {f.decision_store.table} SET record=%s WHERE id=%s", (Jsonb(altered), review["id"]))
        assert (await client.get(route+"/"+review["id"])).status_code == 409
        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {f.decision_store.table} SET record=%s WHERE id=%s", (Jsonb(review), review["id"]))
        assert (await client.get(route+"/"+review["id"])).json() == review
        save_decision_evidence(f, "boundaries", scope_errors=statuses, cross_unknown=review, sql_tamper_rejected=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_new_coverage_requirement_capture_and_replay_do_not_inherit(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        client, route = f.decision_client, f.decision_routes[1]
        request = decision_request(f, 1, key="old-scope", status="effective", case_status="invalid")
        first = await client.post(route, json=request)
        assert first.status_code == 201, first.text
        # Later reviewed coverage explicitly selects the complete twelve-step new case.
        updated = {**request, "request_id": "new-coverage", "coverage_id": f.coverage["id"],
            "expected_coverage_sha256": f.coverage["sha256"], "expected_review_id": first.json()["id"],
            "cases": [{"case_version_id": f.selected.version.id, "point_keys": ["retention"], "status": "valid", "note": "新版本九天断言适用"}]}
        response = await client.post(route, json=updated)
        assert response.status_code == 201, response.text
        second = response.json()
        assert second["coverage"] == f.coverage and len(second["cases"][0]["version"]["content_json"]["steps"]) == 12
        assert (await client.get(route+"/"+first.json()["id"])).json() == first.json()
        review_route = f.base+f"/impact-snapshots/{f.values[7].id}/reviews"
        previous = (await f.client.get(review_route)).json()["items"][0]
        newer = await f.client.post(review_route, json=requirement_payload(f.values[8].rules[0].id, f.values[4].version.id,
            request_id="newer-requirement", expected_review_id=previous["id"], requirement_id=f.version["requirement_id"],
            expected_version_id=f.version["id"], statement="订单保留九天，新增独立边界要求"))
        assert newer.status_code == 201, newer.text
        next_version = newer.json()["requirement_version"]
        page = await client.get(route, params={"requirement_version_id": next_version["id"]})
        assert page.status_code == 200 and page.json()["items"] == []
        # Same environment URL captured again does not inherit older observation/time.
        configuration = (await client.get(f.env_roots[1]+"/configuration")).json()
        snapshot = await client.post(f.env_roots[1]+"/snapshots", json=capture_request(configuration, "later-capture"))
        assert snapshot.status_code == 201
        newer_route = f.env_roots[1]+f"/snapshots/{snapshot.json()['id']}/applicability-reviews"
        stale = deepcopy(request)
        stale.update(request_id="old-observation", expected_snapshot_sha256=snapshot.json()["sha256"])
        assert (await client.post(newer_route, json=stale)).status_code == 409
        stale["points"][0]["status"] = "unknown"; stale["cases"][0]["status"] = "unknown"
        unknown = await client.post(newer_route, json=stale)
        assert unknown.status_code == 201, unknown.text
        assert unknown.json()["observations"][0]["target_status"] == "unknown"
        # An actual completed-request replay produces no new network proof.
        source = f.origin_jobs[1].output_payload["task_result"]
        completed = {k: deepcopy(source[k]) for k in ["status", "response_status", "response_headers", "response_body", "check_results", "duration_ms", "completed_at"]}
        completed.update(http_observation_v1=source["execution_checkpoint"]["http_observation_v1"],
            idempotency_key=f.origin_jobs[1].output_payload["idempotency_key"])
        runtime = ToolRuntimeService(tool_job_service=f.jobs); tool = ToolRegistry().get("api-test-runner")
        tc = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()), trace_id=str(uuid4()),
            user_message="只恢复实际完成请求", normalized_input="只恢复实际完成请求", context_bundle={"project_id": f.project.id})
        replay = await runtime.execute(tool, ModelToolCall(id="replay", name=tool.key, arguments={"worker_action": "execute_task",
            "task": {"task_id": source["task_id"], "method": "GET", "path": source["path"], "execution_checkpoint": {"completed_request": completed}}}), tc)
        replay_job = await f.jobs.get_job(replay.job_id); f.origin_jobs.append(replay_job)
        replay_observation = await client.post(f.env_roots[1]+f"/snapshots/{f.snapshots[1]['id']}/tool-observations/inspect",
            json={"tool_job_id": replay.job_id, "output_path_tokens": ["task_result", "response_body", "days"]})
        assert replay_observation.status_code == 200, replay_observation.text
        observed = replay_observation.json()
        assert observed["target_status"] == "unknown" and observed["http_observation"]["acquisition"] == "completed_request"
        replay_request = deepcopy(updated); replay_request.update(request_id="replayed-source", expected_review_id=second["id"])
        replay_request["observations"] = [{"tool_job_id": observed["tool_job_id"], "output_path_tokens": observed["output_path_tokens"],
            "expected_output_sha256": observed["output_sha256"], "expected_value_sha256": observed["value_sha256"]}]
        replay_rejected = await client.post(route, json=replay_request)
        assert replay_rejected.status_code == 409, replay_rejected.text
        replay_request["points"][0]["status"] = "unknown"; replay_request["cases"][0]["status"] = "unknown"
        replayed = await client.post(route, json=replay_request)
        assert replayed.status_code == 201
        assert [len(x) for x in f.actual_target_calls] == [2, 2]
        save_decision_evidence(f, "new-bases", first=first.json(), new_coverage=second, new_requirement=next_version,
            new_snapshot=snapshot.json(), old_source_unknown=unknown.json(), replay_observation=observed,
            replayed_unknown=replayed.json(), replay_new_HTTP=0)
