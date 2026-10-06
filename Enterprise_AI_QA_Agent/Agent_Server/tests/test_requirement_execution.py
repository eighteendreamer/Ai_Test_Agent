"""Real documents, business HTTP, stored ToolJobs and fixed point associations."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.application.runtime.tool_job_service import ToolJobService
from src.application.documents.requirement_execution_checks import body_checks, fixed_result
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.test_cases.version_content import version_content_hash
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.plan_mapping import build_plan_run, validate_frozen_cases, validate_regression_parent
from src.application.test_runs.run_service import TestRunService as RunService
from src.application.test_runs.run_store import PostgresTestRunStore as RunStore
from src.application.test_suites.suite_store import PostgresTestSuiteStore as SuiteStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.postgres_requirement_execution_store import PostgresRequirementExecutionStore, fixed_execution
from src.runtime.postgres_requirement_store import PostgresRequirementStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.case_management import TestCaseDraftCreateRequest as DraftRequest
from src.schemas.case_planning import CasePlanRecord, CasePlanRunRequest, RegressionPlanSource
from src.schemas.project import ProjectCreateRequest
from src.schemas.requirement_execution import RequirementExecutionRequest
from src.schemas.tool_runtime import ModelToolCall
from test_requirement_reviews import environment, payload, LIVE, save_evidence


@pytest.fixture
def retention_http():
    calls = []
    actual_id = "actual-" + uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self): self.reply(201)
        def do_GET(self): self.reply(200 if urlparse(self.path).path == "/orders/"+actual_id else 404)
        def do_DELETE(self): self.reply(500 if "fail=1" in self.path else 200)
        def reply(self, status):
            calls.append({"method": self.command, "path": self.path, "status": status})
            days = int(parse_qs(urlparse(self.path).query).get("days", ["9"])[0])
            raw = json.dumps({"order.id": actual_id, "days": days, "expired_count": 0,
                "expired": False, "deleted_at": None}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, actual_id
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        assert not thread.is_alive()


@asynccontextmanager
async def execution_environment(tmp_path, retention_http, extension="md"):
    async with environment(tmp_path, extension) as values:
        env, project, doc, paths, case, req_store, requirements, snapshot, impact, sends, client, base = values
        fields = ["postgres_test_case_result_table", "postgres_test_run_attempt_table", "postgres_test_run_item_table",
            "postgres_test_run_table", "postgres_test_suite_item_table", "postgres_test_suite_table",
            "postgres_tool_artifact_table", "postgres_tool_job_table"]
        tables = ["up_rx_"+str(i)+"_"+uuid4().hex[:12] for i in range(len(fields))]
        for field, table in zip(fields, tables): setattr(env.settings.database, field, table)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        req_store.execution = PostgresRequirementExecutionStore(req_store)
        jobs = ToolJobService(PostgresToolJobStore(env.settings)); await jobs.initialize()
        suites = SuiteStore(env.settings); await suites.initialize()
        runs = RunStore(env.settings); await runs.initialize()
        try:
            content = {"objective": "订单保留九天及边界", "expected": {"code": 200, "days": 9,
                "expired_count": 0, "expired": False, "deleted_at": None},
                "steps": [{"action": f"核对完整业务步骤{i}"} for i in range(12)]}
            original = tmp_path / "execution-case.json"
            original.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            selected = await env.cases.create_draft(project_id=project.id, payload=DraftRequest(
                case_key="execution-retention", title=content["objective"], case_type="json", mode_key="api_testing",
                content_json=json.loads(original.read_text("utf-8")), execution_notes="读取十二步与九天边界，保持False/0/null。",
                source_refs=case.version.source_refs, model_key="manual", prompt_version="manual-v1", skill_versions={"manual": "v1"}))
            await env.cases.submit_review(selected.case.id); await env.cases.activate(selected.case.id)
            selected.case = await env.cases.get_case(selected.case.id)
            response = await client.post(f"{base}/impact-snapshots/{snapshot.id}/reviews", json=payload(impact.rules[0].id, case.version.id))
            assert response.status_code == 201, response.text
            version = response.json()["requirement_version"]; rid = version["requirement_id"]
            stem = f"{base}/requirements/{rid}"
            coverage_response = await client.post(f"{stem}/versions/{version['id']}/coverage-reviews", json={
                "request_id": "execution-coverage", "note": "已核对完整九天/边界用例",
                "case_links": [{"case_version_id": selected.version.id, "content_hash": selected.version.content_hash,
                    "point_keys": ["retention", "boundary"]}]})
            assert coverage_response.status_code == 201, coverage_response.text
            coverage = coverage_response.json()
            fixture = SimpleNamespace(values=values, env=env, project=project, paths=[*paths, original],
                req_store=req_store, requirements=requirements, client=client, base=base, stem=stem,
                version=version, coverage=coverage, selected=selected, jobs=jobs, suites=suites, runs=runs,
                http=retention_http, results=[], outcomes=[], tool_records=[])
            fixture.route = f"{stem}/versions/{version['id']}/execution-reviews"
            yield fixture
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in tables: cur.execute(f'DROP TABLE "{table}"')


def observation(key, *tokens):
    return {"call_id": key, "output_path_tokens": list(tokens)}


async def actual_run(f, *, days=9, cleanup_fail=False, parent=None, without_checks=False):
    now = datetime.now(timezone.utc); env = f.env; case = f.selected
    evidence_id = "case:"+case.version.id
    plan = CasePlanRecord(id=str(uuid4()), project_id=f.project.id,
        request={"objective": "验证九天保留规则及边界"}, status="planned", summary="固定完整用例及准备/清理",
        config_snapshot=await env.service.get_policy(f.project.id), created_at=now,
        evidence=[{"id": evidence_id, "kind": "case", "source_id": case.case.id, "version": case.version.id,
            "content_hash": case.version.content_hash, "payload": {"content": case.version.business_content(),
                "execution_notes": case.version.execution_notes, "source_refs": [ref.model_dump(mode="json") for ref in case.version.source_refs]}}],
        decision={"action": "finish", "summary": "准备→正文→清理", "items": [
            {"key": "seed", "kind": "prepare", "reason": "创建本次订单", "evidence_ids": [evidence_id], "instructions": {"expected": 201}},
            {"key": "target", "kind": "target", "case_id": case.case.id, "case_version_id": case.version.id,
                "reason": "核对保留期", "evidence_ids": [evidence_id], "depends_on": ["seed"]},
            {"key": "cleanup", "kind": "prepare", "reason": "删除本次订单", "evidence_ids": [evidence_id],
                "depends_on": ["target"], "instructions": {"expected": 200}}]})
    validate_frozen_cases(plan, {case.case.id: case.case}, {case.version.id: case.version})
    await env.store.save(plan)
    regression = RegressionPlanSource(parent_run_id=parent.run_id,
        source_plan_id=(await f.runs.get_run(parent.run_id)).run.plan_snapshot.plan.id,
        source_results={"target": parent.id}) if parent else None
    suite, run, items = build_plan_run(plan, CasePlanRunRequest(creation_key=uuid4().hex, mode_key="api_testing"), now=now, regression=regression)
    if parent:
        validate_regression_parent(run, (await f.runs.get_run(parent.run_id)).run, {parent.id: parent})
    await f.suites.create(suite.suite, suite.items); await f.runs.create_run(run, items)
    claimed, attempt = (await f.runs.claim_items(run_id=run.id, worker_id="actual-execution", limit=1, lease_seconds=60, now=now))[0]
    await f.runs.start_item(claimed.id, attempt.lease_token, now)
    detail = await f.runs.get_run(run.id)
    context = build_agent_case_context(detail, detail.items[0], case.version)
    session, turn = str(uuid4()), str(uuid4())
    tc = ToolExecutionContext(session_id=session, turn_id=turn, trace_id=turn,
        user_message=context.objective, normalized_input=context.objective,
        context_bundle={**context.runtime_context(), "attempt_id": attempt.id})
    runtime = ToolRuntimeService(tool_job_service=f.jobs); tool = ToolRegistry().get("api-test-runner")
    records, steps = [], []
    url, _, identity = f.http
    for key, method in [("seed", "POST"), ("target", "GET"), ("cleanup", "DELETE")]:
        path = "/orders" if key == "seed" else "/orders/"+identity
        if key == "target": path += f"?days={days}"
        if key == "cleanup" and cleanup_fail: path += "?fail=1"
        records.append(await runtime.execute(tool, ModelToolCall(id=key, name=tool.key, arguments={"worker_action": "execute_task",
            "task": {"task_id": key, "method": method, "path": url+path, "full_url": url+path, "timeout_seconds": 5}}), tc))
        checks = [{"name": "HTTP状态", "expected_path_tokens": ["expected", "code"] if key == "target" else ["expected"],
            "operator": "equals", "observations": [observation(key, "task_result", "response_status")]}]
        if key == "target":
            checks.extend({"name": field, "expected_path_tokens": ["expected", field], "operator": "equals",
                "observations": [observation(key, "task_result", "response_body", field)]}
                for field in ["days", "expired_count", "expired", "deleted_at"])
            if without_checks: checks = []
        steps.append({"item_key": key, "status": "passed", "summary": key, "checks": checks,
            "outputs": {"body": observation(key, "task_result", "response_body")}})
    outcome = await AgentCaseAssessmentService(f.jobs).assess(context=context, receipt={"schema_version": 1, "steps": steps},
        tool_records=records, session_id=session, turn_id=turn, attempt_id=attempt.id, model_key="controlled-receipt",
        lease_token=attempt.lease_token)
    service = RunService(store=f.runs, project_service=env.projects, suite_service=None, test_case_service=env.cases,
        tool_job_service=f.jobs, clock=lambda: now)
    result = await service.complete_item(claimed.id, outcome.completion)
    assert not await f.runs.claim_items(run_id=run.id, worker_id="no-reclaim", limit=1, lease_seconds=60, now=now)
    f.results.append(result); f.outcomes.append(outcome); f.tool_records.extend(records)
    return result


def association(f, result, *, request_id="associate", parent=None, indexes=(1,), points=("retention",)):
    verification = next(v for v in result.actual["verification_results"] if v["metadata"]["phase"] == "case")
    return {"request_id": request_id, "coverage_id": f.coverage["id"], "expected_execution_id": parent,
        "note": "对照原工具输出与固定预期，关联正文核验", "checks": [
            {"result_id": result.id, "verification_id": verification["id"], "check_index": i, "point_keys": list(points)} for i in indexes]}


async def evidence(f, name, **extra):
    tools = [await f.jobs.get_job_detail(record.job_id) for record in f.tool_records]
    with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {f.req_store.execution.table} ORDER BY revision")
        execution = [row["record"] for row in cur.fetchall()]
    save_evidence("execution-"+name, f.paths, results=[r.model_dump(mode="json") for r in f.results],
        tool_jobs=[t.model_dump(mode="json") for t in tools], execution_sql=execution,
        mysql_policy=(await f.env.service.get_policy(f.project.id)).model_dump(mode="json"),
        business_http=f.http[1], physical_model_requests=f.values[9], selected_case=f.selected.model_dump(mode="json"),
        version=f.version, coverage=f.coverage, external_default_model=False, **extra)


def test_request_rejects_duplicate_refs_points_and_client_verdict():
    ref = {"result_id": str(uuid4()), "verification_id": str(uuid4()), "check_index": 0, "point_keys": ["retention"]}
    for checks in [[ref, ref], [{**ref, "point_keys": ["retention", "retention"]}], [{**ref, "status": "passed"}]]:
        with pytest.raises(ValidationError):
            RequirementExecutionRequest(request_id="original", coverage_id="initial", note="核对", checks=checks)


def test_original_saved_business_failure_and_cleanup_failure_remain_separate():
    root = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-requirement-execution-live-fixed"
    for name, check_status in [("execution-md", "passed"), ("execution-regression", "failed")]:
        saved = json.loads((root/name/"verification.json").read_text("utf-8"))
        result = fixed_result(saved["results"][0])
        checks = body_checks(result, "target", saved["selected_case"]["case"]["project_id"])
        assert len(checks) == 5 and list(checks.values())[1]["status"] == check_status
        assert result.status == "failed"
        bad = result.model_copy(deep=True)
        verification = next(v for v in bad.actual["verification_results"] if v["metadata"]["phase"] == "case")
        verification["metadata"]["checks"][1]["operator"] = "invented"
        with pytest.raises(ValidationError): body_checks(bad, "target", saved["selected_case"]["case"]["project_id"])


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_real_fixed_body_checks_partial_association_history_and_archive(tmp_path, retention_http, monkeypatch, extension):
    async with execution_environment(tmp_path, retention_http, extension) as f:
        result = await actual_run(f, cleanup_fail=True)
        assert result.status == "failed" and result.actual["case_status"] == "passed"
        request = association(f, result)
        response = await f.client.post(f.route, json=request); assert response.status_code == 201, response.text
        saved = response.json(); fixed_execution(saved)
        matrix = (await f.client.get(f.stem+"/matrix")).json()
        assert [point["execution_status"] for point in matrix["points"]] == ["passed", "not_assessed"]
        assert matrix["points"][0]["execution_scope"] == "selected_checks"
        assert matrix["points"][0]["execution_checks"][0]["result_status"] == "failed"
        assert matrix["deployment_status"] == "unknown"
        all_request = association(f, result, request_id="all-literal-values", parent=saved["id"], indexes=(2, 3, 4), points=("boundary",))
        all_response = await f.client.post(f.route, json=all_request); assert all_response.status_code == 201, all_response.text
        all_saved = all_response.json()
        assert [check["check"]["actual"] for check in all_saved["checks"]] == [0, False, None]
        initial = (await f.client.get(f.stem+"/matrix", params={"execution_id": "initial"})).json()
        assert initial["execution_review"] is None
        historical = (await f.client.get(f.stem+"/matrix", params={"execution_id": saved["id"]})).json()
        assert historical["execution_review"] == saved
        clear_request = {**request, "request_id": "clear", "expected_execution_id": all_saved["id"], "checks": []}
        clear_response = await f.client.post(f.route, json=clear_request); assert clear_response.status_code == 201, clear_response.text
        page = (await f.client.get(f.route, params={"limit": 1})).json(); assert page["has_more"] and len(page["items"]) == 1
        await f.env.projects.archive(f.project.id)
        async def forbidden(*args, **kwargs): raise AssertionError("历史恢复不得读取当前工具/结果或调用模型")
        with monkeypatch.context() as patch:
            patch.setattr(f.req_store.execution, "_result", lambda *a: (_ for _ in ()).throw(AssertionError("current result read")))
            patch.setattr(f.env.impact_runtime, "invoke", forbidden)
            assert (await f.client.post(f.route, json=request)).json() == saved
            restored = await f.client.get(f.base+"/requirement-execution-reviews/by-request", params={"request_id": request["request_id"]})
            assert restored.json() == saved
            fresh = PostgresRequirementStore(f.env.settings)
            assert (await fresh.execution.get_by_request(f.project.id, request["request_id"])).model_dump(mode="json") == saved
            assert (await f.client.post(f.route, json={**clear_request, "request_id": "archived-new"})).status_code == 409
        assert len(f.values[9]) == 2 and len(f.http[1]) == 3
        await evidence(f, extension, association=saved, literals=all_saved, matrix=matrix, clear=clear_response.json())


@pytest.mark.asyncio
@LIVE
async def test_lost_commit_reply_race_and_candidate_pagination(tmp_path, retention_http, monkeypatch):
    async with execution_environment(tmp_path, retention_http) as f:
        result = await actual_run(f); request = association(f, result)
        original = f.req_store.execution._save
        def lost(*args, **kwargs):
            original(*args, **kwargs); raise ConnectionError("actual execution commit reply lost")
        with monkeypatch.context() as patch:
            patch.setattr(f.req_store.execution, "_save", lost)
            assert (await f.client.post(f.route, json=request)).status_code == 503
        replies = await asyncio.gather(*(f.client.post(f.route, json=request) for _ in range(4)))
        assert all(r.status_code == 201 for r in replies)
        saved = replies[0].json(); assert all(r.json() == saved for r in replies)
        assert (await f.client.post(f.route, json={**request, "note": "different"})).status_code == 409
        race = await asyncio.gather(*(f.client.post(f.route, json={**request, "request_id": "race-"+str(i),
            "expected_execution_id": saved["id"]}) for i in range(2)))
        assert sorted(r.status_code for r in race) == [201, 409]
        second = await actual_run(f)
        candidates = await f.client.get(f.route.replace("execution-reviews", "execution-candidates"),
            params={"coverage_id": f.coverage["id"], "limit": 1})
        assert candidates.status_code == 200, candidates.text
        assert candidates.json()["has_more"] and candidates.json()["items"][0]["result_id"] == second.id
        page2 = (await f.client.get(f.route.replace("execution-reviews", "execution-candidates"),
            params={"coverage_id": f.coverage["id"], "limit": 1, "offset": 1})).json()
        assert page2["items"][0]["result_id"] == result.id and not page2["has_more"]
        assert len(page2["items"][0]["checks"]) == 5
        await evidence(f, "recovery", original=saved, concurrent=[r.json() for r in race], candidates=candidates.json(), page2=page2)


@pytest.mark.asyncio
@LIVE
async def test_failed_business_then_new_regression_preserves_original(tmp_path, retention_http):
    async with execution_environment(tmp_path, retention_http) as f:
        failed = await actual_run(f, days=7); assert failed.status == "failed"
        first = await f.client.post(f.route, json=association(f, failed)); assert first.status_code == 201, first.text
        assert (await f.client.get(f.stem+"/matrix")).json()["points"][0]["execution_status"] == "failed"
        passed = await actual_run(f, parent=failed); assert passed.status == "passed"
        response = await f.client.post(f.route, json=association(f, passed, request_id="regression", parent=first.json()["id"]))
        assert response.status_code == 201, response.text
        check = response.json()["checks"][0]
        assert check["run_kind"] == "regression" and check["regression_source_result_id"] == failed.id
        assert (await f.runs.get_result(failed.id)) == failed
        old = (await f.client.get(f.stem+"/matrix", params={"execution_id": first.json()["id"]})).json()
        assert old["points"][0]["execution_status"] == "failed"
        await evidence(f, "regression", failed_association=first.json(), passed_association=response.json(), old_matrix=old)


@pytest.mark.asyncio
@LIVE
async def test_wrong_refs_foreign_project_coverage_and_actual_tool_corruption(tmp_path, retention_http):
    async with execution_environment(tmp_path, retention_http) as f:
        result = await actual_run(f); request = association(f, result)
        pre = result.actual["verification_results"][0]["id"]
        variants = [{**request, "coverage_id": "initial"},
            {**request, "checks": [{**request["checks"][0], "verification_id": pre}]},
            {**request, "checks": [{**request["checks"][0], "check_index": 199}]},
            {**request, "checks": [{**request["checks"][0], "point_keys": ["unknown"]}]}]
        for i, variant in enumerate(variants):
            reply = await f.client.post(f.route, json={**variant, "request_id": "bad-"+str(i)})
            assert reply.status_code == 409, reply.text
        foreign = await f.env.projects.create(ProjectCreateRequest(project_key="foreign-execution", name="隔离"))
        assert (await f.client.post(f.route.replace(f.project.id, foreign.id), json=request)).status_code == 404
        jobs = f.jobs._store
        actual = await jobs.get_job(f.tool_records[1].job_id)
        changed = actual.model_copy(deep=True); changed.output_payload["task_result"]["response_body"]["days"] = 7
        await jobs.save_job(changed)
        try:
            reply = await f.client.post(f.route, json=request); assert reply.status_code == 409, reply.text
        finally:
            await jobs.save_job(actual)
        reply = await f.client.post(f.route, json=request); assert reply.status_code == 201, reply.text
        saved = reply.json()
        corrupt = deepcopy(saved); corrupt["checks"][0]["check"]["actual"] = 7
        with pytest.raises(ValueError): fixed_execution(corrupt)
        await evidence(f, "boundaries", original=saved, rejected_count=len(variants)+2)


@pytest.mark.asyncio
@LIVE
async def test_missing_body_checks_are_visible_without_inferred_success(tmp_path, retention_http):
    async with execution_environment(tmp_path, retention_http) as f:
        result = await actual_run(f, without_checks=True)
        assert result.status == "blocked"
        candidates = await f.client.get(f.route.replace("execution-reviews", "execution-candidates"),
            params={"coverage_id": f.coverage["id"]})
        assert candidates.status_code == 200, candidates.text
        item = candidates.json()["items"][0]
        assert item["checks"] == [] and item["warnings"]
        response = await f.client.post(f.route, json=association(f, result))
        assert response.status_code == 409
        matrix = (await f.client.get(f.stem+"/matrix")).json()
        assert all(point["execution_status"] == "not_assessed" for point in matrix["points"])
        await evidence(f, "missing", candidates=candidates.json(), matrix=matrix)


@pytest.mark.asyncio
@LIVE
async def test_new_coverage_and_new_requirement_do_not_inherit_old_success(tmp_path, retention_http):
    async with execution_environment(tmp_path, retention_http) as f:
        result = await actual_run(f)
        response = await f.client.post(f.route, json=association(f, result)); assert response.status_code == 201, response.text
        saved = response.json()
        newer = await f.client.post(f.route.replace("execution-reviews", "coverage-reviews"), json={
            "request_id": "coverage-changed", "expected_coverage_id": f.coverage["id"], "note": "撤销设计关联", "case_links": []})
        assert newer.status_code == 201, newer.text
        matrix = (await f.client.get(f.stem+"/matrix")).json()
        assert matrix["execution_review"] is None and all(p["execution_status"] == "not_assessed" for p in matrix["points"])
        old = (await f.client.get(f.stem+"/matrix", params={"coverage_id": f.coverage["id"], "execution_id": saved["id"]})).json()
        assert old["execution_review"] == saved
        assert (await f.client.get(f.stem+"/matrix", params={"execution_id": saved["id"]})).status_code == 404
        impact = f.values[8]; original_case = f.values[4]
        change = payload(impact.rules[0].id, original_case.version.id, request_id="changed-requirement",
            expected_review_id=f.version["review_id"], requirement_id=f.version["requirement_id"],
            expected_version_id=f.version["id"], statement="订单保留九天，并明确新边界", case_links=[])
        newer_req = await f.client.post(f.base+f"/impact-snapshots/{f.values[7].id}/reviews", json=change)
        assert newer_req.status_code == 201, newer_req.text
        current = (await f.client.get(f.stem+"/matrix")).json()
        assert current["version"]["id"] != f.version["id"] and current["execution_review"] is None
        assert all(point["execution_status"] == "not_assessed" for point in current["points"])
        await evidence(f, "new-versions", original=saved, old_matrix=old, new_coverage=matrix, current_matrix=current)


@pytest.mark.asyncio
@LIVE
async def test_uuid_case_variants_keep_same_fixed_execution_identity(tmp_path, retention_http):
    async with execution_environment(tmp_path, retention_http) as f:
        result = await actual_run(f)
        request = association(f, result)
        request["coverage_id"] = request["coverage_id"].upper()
        for check in request["checks"]:
            check["result_id"] = check["result_id"].upper()
            check["verification_id"] = check["verification_id"].upper()
        reply = await f.client.post(f.route, json=request); assert reply.status_code == 201, reply.text
        matrix = (await f.client.get(f.stem+"/matrix")).json()
        assert matrix["execution_review"] == reply.json()
        assert reply.json()["request"]["coverage_id"] == f.coverage["id"]
        assert reply.json()["checks"][0]["result_id"] == result.id
        await evidence(f, "uuid-identity", original=reply.json(), matrix=matrix)
