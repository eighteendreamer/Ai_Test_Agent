"""Fixed execution associations through an independent authenticated production API."""
import json
from types import SimpleNamespace

from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.test_cases.case_service import TestCaseService
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_cases.planning_service import CasePlanningService
from src.application.test_cases.planning_store import PostgresCasePlanStore
from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_execution_store import fixed_execution
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.case_management import TestCaseBundle
from test_requirement_execution import actual_run, association


async def prepare_execution_fixture(*, base, root, review, settings, post, business_http, source_ref):
    assert review
    projects = ProjectService(store=PostgresProjectStore(settings))
    cases = TestCaseService(store=PostgresTestCaseStore(settings), project_service=projects)
    plans = PostgresCasePlanStore(settings)
    planning = CasePlanningService(store=plans, config_store=MySQLApplicationConfigStore(settings), project_service=projects,
        sources=None, model_runtime_service=None)
    selected = TestCaseBundle.model_validate(await post(root+"/test-cases", {
        "case_key": "execution-evidence", "title": "核对九天保留边界", "case_type": "json", "mode_key": "api_testing",
        "content_json": {"expected": {"code": 200, "days": 9, "expired_count": 0, "expired": False, "deleted_at": None},
            "steps": [{"action": f"核对完整操作{i}"} for i in range(12)]},
        "execution_notes": "全部正文与False/0/null保留；准备及清理分别统计。", "source_refs": [source_ref],
        "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"}}, 201))
    await cases.submit_review(selected.case.id); await cases.activate(selected.case.id)
    selected.case = await cases.get_case(selected.case.id)
    version = review["requirement_version"]; stem = base+f"/requirements/{version['requirement_id']}"
    coverage = await post(stem+f"/versions/{version['id']}/coverage-reviews", {
        "request_id": "production-execution-coverage", "note": "明确关联正文实际保留期检查",
        "case_links": [{"case_version_id": selected.version.id, "content_hash": selected.version.content_hash, "point_keys": ["retention"]}]}, 201)
    jobs = ToolJobService(PostgresToolJobStore(settings))
    f = SimpleNamespace(env=SimpleNamespace(settings=settings, projects=projects, cases=cases, store=plans, service=planning),
        project=await projects.get(review["project_id"]), selected=selected, coverage=coverage,
        jobs=jobs, runs=PostgresTestRunStore(settings), suites=PostgresTestSuiteStore(settings), http=business_http,
        results=[], outcomes=[], tool_records=[])
    path = stem+f"/versions/{version['id']}/execution-reviews"
    return f, path, stem, version


async def execution_from_production(*, base, root, review, settings, get, post, client, evidence, sends, business_http, source_ref):
    f, path, stem, version = await prepare_execution_fixture(base=base, root=root, review=review, settings=settings,
        post=post, business_http=business_http, source_ref=source_ref)
    selected, coverage = f.selected, f.coverage
    before = len(sends)
    failed = await actual_run(f, days=7)
    first_payload = association(f, failed, request_id="production-first-execution")
    first = await post(path, first_payload, 201)
    assert first["checks"][0]["check"]["status"] == "failed"
    first_matrix = await get(stem+"/matrix")
    passed = await actual_run(f, parent=failed)
    second_payload = association(f, passed, request_id="production-regression-execution", parent=first["id"], indexes=(1, 2, 3, 4))
    second = await post(path, second_payload, 201)
    assert second["checks"][0]["regression_source_result_id"] == failed.id
    matrix = await get(stem+"/matrix")
    assert matrix["points"][0]["execution_status"] == "passed" and matrix["deployment_status"] == "unknown"
    assert matrix["points"][0]["execution_scope"] == "selected_checks"
    assert await get(stem+"/matrix", coverage_id=coverage["id"], execution_id=first["id"]) == first_matrix
    candidates = await get(path.replace("execution-reviews", "execution-candidates"), coverage_id=coverage["id"], limit=1)
    assert candidates["has_more"] and candidates["items"][0]["result_id"] == passed.id
    state = {"path": path, "stem": stem, "base": base, "first_payload": first_payload, "first": first,
        "second_payload": second_payload, "second": second, "first_matrix": first_matrix, "matrix": matrix,
        "coverage": coverage, "candidates": candidates, "results": [r.model_dump(mode="json") for r in f.results],
        "tool_jobs": [(await f.jobs.get_job_detail(r.job_id)).model_dump(mode="json") for r in f.tool_records],
        "business_http": business_http[1], "selected_case": selected.model_dump(mode="json"),
        "execution_model_http_calls": len(sends)-before, "external_default_model": False,
        "actual_production_api": True, "autonomous_agent_model_execution": False}
    assert len(sends) == before and len(business_http[1]) == 6
    (evidence/"requirement-execution.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


async def execution_after_restart(*, state, get, post, client, settings, evidence, project_id, sends):
    before = len(sends)
    for label in ["first", "second"]:
        assert await post(state["path"], state[label+"_payload"], 201) == state[label]
        assert await get(state["base"]+"/requirement-execution-reviews/by-request",
            request_id=state[label+"_payload"]["request_id"]) == state[label]
    assert await get(state["stem"]+"/matrix") == state["matrix"]
    await post(f"/projects/{project_id}/archive", {})
    assert await post(state["path"], state["first_payload"], 201) == state["first"]
    assert await get(state["stem"]+"/matrix", execution_id=state["first"]["id"]) == state["first_matrix"]
    rejected = await client.post(state["path"], json={**state["second_payload"], "request_id": "new-after-archive"})
    assert rejected.status_code == 409
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_requirement_execution ORDER BY revision")
        records = [row["record"] for row in cur.fetchall()]
    assert records == [state["first"], state["second"]]
    assert all(fixed_execution(record).model_dump(mode="json") == record for record in records)
    assert len(sends) == before
    (evidence/"requirement-execution-restart.json").write_text(json.dumps({"records": records,
        "api_restarted": True, "dispatch_disabled": True, "archived_history_preserved": True,
        "new_association_rejected": True, "recovery_model_http_calls": len(sends)-before}, ensure_ascii=False, indent=2), encoding="utf-8")
