"""Actual files -> fixed impact -> explicit API reviews -> PG versions/matrix."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from src.api.routes.requirements import router
from src.application.documents.requirement_service import RequirementService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_store import PostgresRequirementStore, fixed_requirement_version
from src.schemas.project import ProjectCreateRequest
from src.schemas.requirements import RequirementReviewRequest
from test_document_impact_calls import prepared, provider
from test_document_impact_grouped_workflow import grouped_responder
from test_document_impact_tasks import task_environment
from test_document_impact_workflow import workflow

LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires isolated actual MySQL/PG/RustFS, MD/DOCX/PDF processes and HTTP")


def payload(rule_id, case_version_id, *, request_id="review-1", **changes):
    value = dict(request_id=request_id, rule_id=rule_id, decision="accept", note="已对照新旧原文及完整用例",
        change="modified", title="订单保留期", statement="订单保留九天",
        points=[{"key": "retention", "description": "验证九天保留期"},
            {"key": "boundary", "description": "验证保留期边界"}],
        case_links=[{"case_version_id": case_version_id, "point_keys": ["retention"]}])
    return {**value, **changes}


@asynccontextmanager
async def environment(tmp_path, extension="md"):
    async with task_environment(tmp_path, extension, protocol_version="impact-call-v7") as values:
        env, project, doc, paths, views, _, _, _, case, impact_store, tasks = values
        holder = {}
        with provider(responder=grouped_responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            runner = workflow(env, tasks, impact_store)
            saved = await runner.execute(claim)
            snapshot, result = await runner.read_result(project.id, doc.id, saved.id)
            assert len(sends) == 2 and len(result.rules) == 1
            store = PostgresRequirementStore(env.settings)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in store.tables:
                    cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                    assert cur.fetchone()["existing"] is None
            service = RequirementService(store=store, workflow=runner, projects=env.projects, documents=env.docs)
            await service.initialize()
            app = FastAPI()
            app.state.requirement_service = service
            app.include_router(router, prefix="/api/v1")
            base = f"/api/v1/projects/{project.id}/documents/{doc.id}"
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://actual-api") as client:
                    yield env, project, doc, paths, case, store, service, snapshot, result, sends, client, base
            finally:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    for table in store.tables:
                        cur.execute(f'DROP TABLE "{table}"')


def save_evidence(name, paths, **values):
    if directory := os.getenv("QA_REQUIREMENT_EVIDENCE_DIR"):
        target = Path(directory) / name
        target.mkdir(parents=True, exist_ok=True)
        for path in paths:
            (target/path.name).write_bytes(path.read_bytes())
        (target/"verification.json").write_text(json.dumps(values, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_api_reviews_versions_points_and_historical_matrix(tmp_path, monkeypatch, extension):
    async with environment(tmp_path, extension) as values:
        env, project, doc, paths, case, store, service, snapshot, result, sends, client, base = values
        before = (await env.objects.read_object_uri(snapshot.storage_uri))["content"]
        route = f"{base}/impact-snapshots/{snapshot.id}/reviews"
        request = payload(result.rules[0].id, case.version.id)
        response = await client.post(route, json=request)
        assert response.status_code == 201, response.text
        first = response.json(); v1 = first["requirement_version"]; rid = v1["requirement_id"]
        assert first["source"]["rule"] == result.rules[0].model_dump(mode="json")
        assert first["source"]["case_checkpoints"] and first["source"]["deployment_status"] == "unknown"
        assert first["source"]["basis_limitations"] == result.basis_limitations
        assert first["source"]["case_assessments"] == [result.units[-1].candidates[0].model_dump(mode="json")]
        matrix1 = (await client.get(f"{base}/requirements/{rid}/matrix")).json()
        assert [p["design_status"] for p in matrix1["points"]] == ["design_covered", "uncovered"]
        assert all(p["execution_status"] == "not_assessed" for p in matrix1["points"])
        request2 = payload(result.rules[0].id, case.version.id, request_id="review-2", expected_review_id=first["id"],
            requirement_id=rid, expected_version_id=v1["id"], statement="订单保留九天，明确边界", case_links=[])
        response2 = await client.post(route, json=request2)
        assert response2.status_code == 201, response2.text
        second = response2.json(); v2 = second["requirement_version"]
        assert v2["version"] == 2 and v2["parent_version_id"] == v1["id"]
        assert [p["id"] for p in v2["points"]] == [p["id"] for p in v1["points"]]
        matrix2 = (await client.get(f"{base}/requirements/{rid}/matrix")).json()
        assert [p["design_status"] for p in matrix2["points"]] == ["needs_review", "uncovered"]
        assert matrix2["points"][0]["previous_case_links"] == v1["case_links"]
        old = (await client.get(f"{base}/requirements/{rid}/matrix", params={"version_id": v1["id"]})).json()
        assert old["version"] == v1 and old["points"] == matrix1["points"]
        stale = await client.post(route, json={**request2, "request_id": "stale", "expected_review_id": second["id"]})
        assert stale.status_code == 409 and "父版本" in stale.text
        delete = await client.post(route, json=payload(result.rules[0].id, case.version.id, request_id="removed",
            expected_review_id=second["id"], requirement_id=rid, expected_version_id=v2["id"], change="removed",
            title="订单保留期移除候选已确认", statement="确认此需求移除；用例停用另行评审", points=[], case_links=[]))
        assert delete.status_code == 201, delete.text
        removed = delete.json()
        assert (await client.get(f"{base}/requirements/{rid}/matrix")).json()["removed"]
        versions = (await client.get(f"{base}/requirements/{rid}/versions")).json()
        assert len(versions) == 3 and versions[0] == v1 and versions[1] == v2
        current_case = await env.cases.get_case(case.case.id)
        assert current_case.active_version_id == case.version.id and current_case.lifecycle_status == "active"
        assert await env.cases.get_version(case.version.id) == case.version
        assert (await env.objects.read_object_uri(snapshot.storage_uri))["content"] == before
        assert len(sends) == 2 and await env.projects.archive(project.id)
        async def forbidden(*args, **kwargs):
            raise AssertionError("existing review recovery cannot read objects, analyze, build or upload")
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", forbidden)
            patch.setattr(env.impact_runtime, "invoke", forbidden)
            patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
            assert (await client.post(route, json=request)).json() == first
            assert (await client.get(f"{base}/requirement-reviews/by-request", params={"request_id": "review-1"})).json() == first
            archived_old = (await client.get(f"{base}/requirements/{rid}/matrix", params={"version_id": v1["id"]})).json()
            assert archived_old["version"] == old["version"] and archived_old["points"] == old["points"]
            assert archived_old["requirement"]["current_version_id"] == removed["requirement_version"]["id"]
            assert (await client.post(route, json={**request, "request_id": "after-archive"})).status_code == 409
        page = (await client.get(f"{base}/requirements")).json()
        assert len(page["items"]) == 1 and page["items"][0]["version"] == removed["requirement_version"]
        reviews = (await client.get(route, params={"limit": 1})).json()
        assert len(reviews["items"]) == 1 and reviews["has_more"]
        save_evidence(extension, paths, source_snapshot=snapshot.model_dump(mode="json"), source_result=result.model_dump(mode="json"),
            source_sha256=hashlib.sha256(before).hexdigest(), first=first, second=second, removed=removed,
            versions=versions, matrix_v1=matrix1, matrix_v2=matrix2, physical_requests=sends,
            full_case=case.model_dump(mode="json"), actual_http_calls=len(sends), semantic_review_is_test_fixture=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_lost_commit_reply_concurrent_recovery_and_conflicts(tmp_path, monkeypatch):
    async with environment(tmp_path) as values:
        env, project, doc, paths, case, store, service, snapshot, result, sends, client, base = values
        route = f"{base}/impact-snapshots/{snapshot.id}/reviews"
        request = payload(result.rules[0].id, case.version.id)
        original = store._save_review
        def lost_reply(*args, **kwargs):
            saved = original(*args, **kwargs)
            raise ConnectionError("actual committed review reply lost")
        with monkeypatch.context() as patch:
            patch.setattr(store, "_save_review", lost_reply)
            response = await client.post(route, json=request)
            assert response.status_code == 503
        async def forbidden(*args, **kwargs): raise AssertionError("recovery must not read impact or send")
        with monkeypatch.context() as patch:
            patch.setattr(service._workflow, "read_snapshot", forbidden)
            replies = await asyncio.gather(*(client.post(route, json=request) for _ in range(4)))
            assert all(reply.status_code == 201 for reply in replies)
            first = replies[0].json()
            assert all(reply.json() == first for reply in replies)
            assert (await client.post(route, json={**request, "note": "changed"})).status_code == 409
        race = {**request, "expected_review_id": first["id"], "requirement_id": first["requirement_version"]["requirement_id"],
            "expected_version_id": first["requirement_version"]["id"]}
        replies2 = await asyncio.gather(*(client.post(route, json={**race, "request_id": f"race-{i}"}) for i in range(2)))
        assert sorted(reply.status_code for reply in replies2) == [201, 409]
        foreign = await env.projects.create(ProjectCreateRequest(project_key="foreign", name="其他项目"))
        assert (await client.get(base.replace(project.id, foreign.id)+"/requirements")).status_code == 404
        assert (await client.post(route, json={**request, "request_id": "unknown-rule", "rule_id": "0"*64})).status_code == 409
        assert (await client.post(route, json={**request, "request_id": "foreign-case", "expected_review_id":
            next(reply.json()["id"] for reply in replies2 if reply.status_code == 201),
            "case_links": [{"case_version_id": str(uuid4()), "point_keys": ["retention"]}]})).status_code == 409
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            counts = {}
            for table in store.tables:
                cur.execute(f'SELECT COUNT(*) AS count FROM "{table}"')
                counts[table] = cur.fetchone()["count"]
            assert counts[store._reviews] == counts[store._versions] == 2 and counts[store._requirements] == 1
        save_evidence("recovery", paths, saved=first, racing_responses=[reply.json() for reply in replies2],
            counts=counts, actual_http_calls=len(sends), physical_requests=sends,
            actual_pg_commit_reply_lost=True, original_impact_unchanged=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_reject_defer_fresh_concurrency_and_restored_point_identity(tmp_path):
    async with environment(tmp_path) as values:
        env, project, doc, paths, case, store, service, snapshot, result, sends, client, base = values
        route = f"{base}/impact-snapshots/{snapshot.id}/reviews"
        rejection = {"request_id": "reject", "rule_id": result.rules[0].id, "decision": "reject", "note": "暂不采纳此建议"}
        rejected = await client.post(route, json=rejection)
        assert rejected.status_code == 201 and rejected.json()["requirement_version"] is None
        deferred = await client.post(route, json={**rejection, "request_id": "defer", "decision": "defer",
            "expected_review_id": rejected.json()["id"], "note": "等待产品确认"})
        assert deferred.status_code == 201 and deferred.json()["requirement_version"] is None
        assert (await client.get(f"{base}/requirements")).json()["items"] == []
        request = payload(result.rules[0].id, case.version.id, request_id="fresh-race", expected_review_id=deferred.json()["id"])
        concurrent = await asyncio.gather(*(client.post(route, json=request) for _ in range(4)))
        assert all(response.status_code == 201 for response in concurrent)
        accepted = concurrent[0].json()
        assert all(response.json() == accepted for response in concurrent)
        v1 = accepted["requirement_version"]; rid = v1["requirement_id"]
        removed = await client.post(route, json=payload(result.rules[0].id, case.version.id, request_id="remove",
            expected_review_id=accepted["id"], requirement_id=rid, expected_version_id=v1["id"],
            change="removed", points=[], case_links=[]))
        assert removed.status_code == 201
        restored = await client.post(route, json=payload(result.rules[0].id, case.version.id, request_id="restore",
            expected_review_id=removed.json()["id"], requirement_id=rid,
            expected_version_id=removed.json()["requirement_version"]["id"], case_links=[]))
        assert restored.status_code == 201
        v3 = restored.json()["requirement_version"]
        assert [point["id"] for point in v3["points"]] == [point["id"] for point in v1["points"]]
        matrix = (await client.get(f"{base}/requirements/{rid}/matrix")).json()
        assert [point["design_status"] for point in matrix["points"]] == ["needs_review", "uncovered"]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS count FROM {store._versions}")
            assert cur.fetchone()["count"] == 3
            cur.execute(f"SELECT record FROM {store._versions} WHERE id=%s", (v1["id"],))
            raw_v1 = cur.fetchone()["record"]
            cur.execute(f"UPDATE {store._versions} SET record=jsonb_set(record,'{{statement}}','\"corrupted\"'::jsonb) WHERE id=%s", (v1["id"],))
        try:
            corruption = await client.get(f"{base}/requirements/{rid}/matrix", params={"version_id": v1["id"]})
            assert corruption.status_code == 409 and "哈希" in corruption.text
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {store._versions} SET record=%s::jsonb WHERE id=%s", (json.dumps(raw_v1,ensure_ascii=False),v1["id"]))
        assert (await client.get(f"{base}/requirements/{rid}/matrix", params={"version_id":v1["id"]})).json()["version"] == v1
        save_evidence("decisions", paths, rejected=rejected.json(), deferred=deferred.json(), accepted=accepted,
            removed=removed.json(), restored=restored.json(), matrix=matrix, actual_http_calls=len(sends),
            physical_requests=sends, corruption_detected_and_original_restored=True)


@pytest.mark.parametrize("changes", [{"points": []}, {"decision": "reject"},
    {"requirement_id": str(uuid4())}, {"case_links": [{"case_version_id": str(uuid4()), "point_keys": ["unknown"]}]}])
def test_incoherent_explicit_review_requests_are_rejected(changes):
    with pytest.raises(ValueError):
        RequirementReviewRequest.model_validate(payload("1"*64, str(uuid4()), **changes))
