"""Explicit full draft reviews and durable coverage through the production API."""
import asyncio
import json

from src.application.documents.impact_sources import impact_record_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_coverage_store import fixed_coverage
from src.schemas.case_management import TestCaseVersionRecord


async def coverage_from_production(*, base, review, generation, retry_state, get, post, client, settings, evidence, sends):
    requirement = review["requirement_version"]
    stem = base+f"/requirements/{requirement['requirement_id']}"
    path = stem+f"/versions/{requirement['id']}/coverage-reviews"
    initial = await get(stem+"/matrix")
    assert initial["coverage_review"] is None and initial["points"][1]["design_status"] == "uncovered"
    references = []
    count = len(sends)
    for task, key in zip(generation["modules"], ["retention", "boundary"]):
        ref = task["checkpoint"]["receipt"]["versions"][0]
        version = next(item for item in await get(f"/test-cases/{ref['case_id']}/versions") if item["id"] == ref["version_id"])
        case = await get(f"/test-cases/{ref['case_id']}")
        pending = await post(f"/test-cases/{ref['case_id']}/submit-review", {"version_id":version["id"], "request_id":"production-coverage-review"})
        assert pending["version_reviews"][version["id"]]["status"] == "pending_review"
        approved = await post(f"/test-cases/{ref['case_id']}/activate", {"version_id":version["id"],
            "expected_active_version_id":case["active_version_id"], "request_id":"production-coverage-activate"})
        assert approved["active_version_id"] == version["id"] and approved["version_reviews"][version["id"]]["status"] == "approved"
        assert version["content_hash"] == ref["content_hash"]
        references.append({"link":{"case_version_id":version["id"], "content_hash":version["content_hash"],
            "generation_task_id":task["id"], "point_keys":[key]}, "version":version, "approved":approved})
    first_payload = {"request_id":"production-coverage-first", "note":"已逐字段核对更新用例，边界尚未确认", "case_links":[references[0]["link"]]}
    first = await post(path,first_payload,201)
    first_matrix = await get(stem+"/matrix")
    assert [p["design_status"] for p in first_matrix["points"]] == ["design_covered", "uncovered"]
    second_payload = {"request_id":"production-coverage-second", "expected_coverage_id":first["id"],
        "note":"明确确认更新与新建用例各自的验证点", "case_links":[item["link"] for item in references]}
    second = await post(path,second_payload,201)
    current = await get(stem+"/matrix")
    assert [p["design_status"] for p in current["points"]] == ["design_covered"]*2
    assert current["version"] == requirement and current["coverage_review"] == second
    assert all(p["execution_status"] == "not_assessed" for p in current["points"]) and current["deployment_status"] == "unknown"
    assert await get(stem+"/matrix",coverage_id=first["id"]) == first_matrix
    assert await get(stem+"/matrix",coverage_id="initial") == initial
    assert (await get(path,limit=1))["items"] == [second] and (await get(path,limit=1))["has_more"]
    assert (await client.post(path,json={**second_payload,"note":"changed"})).status_code == 409
    for item, link in zip(references,second["case_links"]):
        assert link["version_record_sha256"] == impact_record_hash(TestCaseVersionRecord.model_validate(item["version"]))
        assert link["review_status"] == "approved"
    retry = None
    if retry_state:
        item = retry_state["items"][-1]
        task, version = item["view"]["task"], item["draft"]
        retry_payload = {"request_id":"production-coverage-retry", "expected_coverage_id":second["id"],
            "note":"确认实际原响应重试草稿；不替换原生成与失败记录", "case_links":[
                {"case_version_id":version["id"],"content_hash":version["content_hash"],"generation_task_id":task["id"],"point_keys":["retention"]},
                references[1]["link"]]}
        retry = await post(path,retry_payload,201)
        assert retry["case_links"][0]["generation"]["retry"] == {"id":item["view"]["retry"]["id"],"sha256":item["view"]["retry"]["sha256"]}
        assert retry["case_links"][0]["review_status"] == "draft"
        assert retry["case_links"][0]["active_version_id"] == references[0]["version"]["id"]
        assert await get(base+f"/case-generation-tasks/{item['view']['retry']['parent_task_id']}") == item["parent_report"]["modules"][0]
        current = await get(stem+"/matrix")
    with postgres_connect(settings) as conn,conn.cursor() as cur:
        docs = settings.database.postgres_api_doc_table
        cur.execute(f"SELECT record FROM {docs}_requirement_coverage ORDER BY revision")
        stored = [row["record"] for row in cur.fetchall()]
        cur.execute(f"SELECT record FROM {docs}_requirement_versions WHERE id=%s",(requirement["id"],))
        assert cur.fetchone()["record"] == requirement
    assert stored == [first,second,*([retry] if retry else [])]
    assert all(fixed_coverage(record).model_dump(mode="json") == record for record in stored)
    assert len(sends) == count
    state = dict(path=path,stem=stem,first=first,first_payload=first_payload,first_matrix=first_matrix,initial=initial,
        second=second,second_payload=second_payload,matrix=current,retry=retry,references=references,pg_coverage=stored,
        actual_production_api=True,independent_generation_workers=True,coverage_model_http_calls=0,
        full_business_review=False,formal_coverage_page=False)
    (evidence/"requirement-coverage.json").write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    return state


async def coverage_after_restart(*,state,get,post,client,evidence,project_id):
    for label in ["first","second"]:
        record, payload = state[label],state[label+"_payload"]
        replies = await asyncio.gather(*(post(state["path"],payload,201) for _ in range(4)))
        assert all(reply == record for reply in replies)
        base = state["stem"].rsplit("/requirements/",1)[0]
        assert await get(base+"/requirement-coverage-reviews/by-request",request_id=payload["request_id"]) == record
    assert await get(state["stem"]+"/matrix") == state["matrix"]
    assert await get(state["stem"]+"/matrix",coverage_id=state["first"]["id"]) == state["first_matrix"]
    assert await get(state["stem"]+"/matrix",coverage_id="initial") == state["initial"]
    await post(f"/projects/{project_id}/archive",{})
    assert await post(state["path"],state["second_payload"],201) == state["second"]
    assert (await client.post(state["path"],json={**state["second_payload"],"request_id":"archived-new"})).status_code == 409
    assert await get(state["stem"]+"/matrix") == state["matrix"]
    state["actual_api_restart_disabled_dispatch_and_archive_recovered"] = True
    (evidence/"requirement-coverage.json").write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
