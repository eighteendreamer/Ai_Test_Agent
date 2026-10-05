"""Actual public generation routes over fixed files and real stores/HTTP."""
from fastapi import FastAPI
import httpx
import pytest

from src.api.routes.case_generation_tasks import router
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.project import ProjectCreateRequest
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_generation_tasks import generation_environment, task_request, evidence
from test_incremental_generation_sources import accepted, responder
from test_requirement_reviews import LIVE


def app_for(service, config):
    app = FastAPI()
    app.state.generation_task_service = service
    app.state.generation_task_config = config
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_public_settings_submission_fixed_report_and_archived_history(tmp_path, extension):
    async with generation_environment(tmp_path, extension) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        base = f"/api/v1/projects/{project.id}/documents/{doc.id}"
        settings_path = f"/api/v1/projects/{project.id}/case-generation-settings"
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service, config)), base_url="http://actual-api") as client:
                settings = await client.get(settings_path)
                assert settings.status_code == 200
                policy = {**settings.json()["payload"], "max_input_bytes": 300000}
                saved_policy = await client.put(settings_path, json=policy)
                assert saved_policy.status_code == 200
                assert env.configs.get("test_case.incremental_generation_settings", scope_key=project.id).payload == policy
                payload = task_request(review, case, include_new_cases=True, new_case_mode_key="api_testing").model_dump(mode="json")
                reply = await client.post(base+"/case-generations", json=payload)
                assert reply.status_code == 202, reply.text
                group = reply.json()
                assert (await client.post(base+"/case-generations", json=payload)).json() == group
                assert (await client.get(base+"/case-generations/by-request", params={"creation_key": payload["creation_key"]})).json() == group
                assert (await client.post(base+"/case-generations", json={**payload,"max_new_cases":2})).status_code == 409
                assert (await client.post(base+"/case-generations", json={**payload,"lease_token":"forbidden"})).status_code == 422
                assert (await client.get(base+"/case-generations", params={"limit":0})).status_code == 422
                assert (await client.get(base+"/case-generations/by-request", params={"creation_key":"missing"})).status_code == 404
                report = (await client.get(base+f"/case-generations/{group['id']}")).json()
                assert [task["status"] for task in report["modules"]] == ["queued", "queued"]
                first_task = report["modules"][0]["id"]
                assert (await client.get(base+f"/case-generation-tasks/{first_task}/response")).status_code == 404
                fixed_scope = (await client.get(base+f"/case-generations/{group['id']}/scope")).json()
                assert fixed_scope["sha256"] == group["scope_sha256"]
                later = await client.post(base+"/case-generations", json={**payload,"creation_key":"page-2","target_case_version_ids":[]})
                assert later.status_code == 202
                page = (await client.get(base+"/case-generations", params={"limit":1})).json()
                assert page["has_more"] and page["items"][0] == later.json()
                assert (await client.get(base+"/case-generations", params={"limit":1,"offset":1})).json()["items"] == [group]
                other = await env.projects.create(ProjectCreateRequest(project_key="foreign-entry",name="其他项目"))
                foreign = base.replace(project.id, other.id)
                for suffix in ["/case-generations", "/case-generations/by-request?creation_key=incremental-original",
                    f"/case-generations/{group['id']}", f"/case-generations/{group['id']}/scope", f"/case-generation-tasks/{first_task}"]:
                    assert (await client.get(foreign+suffix)).status_code == 404
                for task in report["modules"]:
                    final = await service.execute(await store.claim(project.id, task["id"], worker_id="api-functional"))
                    assert final.status == "completed"
                report = (await client.get(base+f"/case-generations/{group['id']}")).json()
                assert all(task["status"] == "completed" for task in report["modules"]) and len(sends) == 2
                response = await client.get(base+f"/case-generation-tasks/{first_task}/response")
                assert response.status_code == 200 and "九天" in response.json()["text"]
                assert (await client.get(base+f"/case-generation-tasks/{first_task}")).json() == report["modules"][0]
                assert (await env.cases.get_case(case.case.id)).active_version_id == case.version.id
                await env.projects.archive(project.id)
                assert (await client.post(base+"/case-generations", json=payload)).json() == group
                assert (await client.get(base+f"/case-generations/{group['id']}/scope")).json() == fixed_scope
                assert (await client.get(base+f"/case-generations/{group['id']}")).json() == report
                assert (await client.post(base+"/case-generations", json={**payload,"creation_key":"archived-new"})).status_code == 409
                assert (await client.put(settings_path, json=policy)).status_code == 409
                original = await service.get_by_request(project.id, doc.id, payload["creation_key"])
                await evidence("api-"+extension, values, service, original, physical_requests=sends,
                    actual_generation_http_calls=2, actual_public_api=True, independent_worker_process=False,
                    public_report=report, public_scope=fixed_scope, mysql_policy=saved_policy.json(), archived_original_readable=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_public_unknown_submission_reply_recovers_original_group(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        base = f"/api/v1/projects/{project.id}/documents/{doc.id}"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service, config)),base_url="http://actual-api") as client:
            payload = task_request(review, case).model_dump(mode="json")
            submit = store._submit_group
            def lose_reply(*args, **kwargs):
                submit(*args, **kwargs)
                raise ConnectionError("actual committed API group reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(store,"_submit_group",lose_reply)
                reply = await client.post(base+"/case-generations",json=payload)
                assert reply.status_code == 503
            async def forbidden(*args, **kwargs): raise AssertionError("API recovery must not recapture")
            with monkeypatch.context() as patch:
                patch.setattr(service._sources,"capture",forbidden)
                patch.setattr(config,"get",forbidden)
                found = await client.get(base+"/case-generations/by-request",params={"creation_key":payload["creation_key"]})
                assert found.status_code == 200
                assert (await client.post(base+"/case-generations",json=payload)).json() == found.json()
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS count FROM {store._groups}")
                assert cur.fetchone()["count"] == 1
            await evidence("api-lost-reply",values,service,await service.get_by_request(project.id,doc.id,payload["creation_key"]),
                actual_generation_http_calls=0, actual_public_api=True, actual_pg_commit_reply_lost=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_public_retirement_receipt_preserves_active_case(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        removed = await accepted(values, change="removed", points=[], case_links=[])
        base = f"/api/v1/projects/{project.id}/documents/{doc.id}"
        async def forbidden(*args, **kwargs):
            raise AssertionError("retirement candidate must not invoke generation")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service, config)), base_url="http://actual-api") as client:
            payload = task_request(removed, case).model_dump(mode="json")
            reply = await client.post(base+"/case-generations", json=payload)
            assert reply.status_code == 202
            group = reply.json()
            fixed = (await client.get(base+f"/case-generations/{group['id']}/scope")).json()
            assert fixed["template"] is None and fixed["authoring"] is None
            report = (await client.get(base+f"/case-generations/{group['id']}")).json()
            task_id = report["modules"][0]["id"]
            with monkeypatch.context() as patch:
                patch.setattr(service._generator, "invoke_frozen", forbidden)
                await service.execute(await store.claim(project.id, task_id, worker_id="public-retirement"))
            report = (await client.get(base+f"/case-generations/{group['id']}")).json()
            task = report["modules"][0]
            receipt = task["checkpoint"]["receipt"]
            assert task["status"] == "completed" and receipt["kind"] == "retirement_candidate"
            assert receipt["versions"] == [] and receipt["response"] is None
            assert receipt["retirement_target"]["case"]["id"] == case.case.id
            assert (await client.get(base+f"/case-generation-tasks/{task_id}/response")).status_code == 404
            current = await env.cases.get_case(case.case.id)
            assert current.lifecycle_status == "active" and current.active_version_id == case.version.id
            assert current.latest_version == 1 and await store.claim(project.id, task_id, worker_id="terminal") is None
            await evidence("api-retirement", values, service,
                await service.get_by_request(project.id, doc.id, payload["creation_key"]),
                actual_generation_http_calls=0, actual_public_api=True, public_report=report,
                public_scope=fixed, active_case_not_disabled=True)
