"""Actual originals, persistent parsing catalog and project-bound HTTP reads."""
from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.document_parsing import router
from src.schemas.document_parsing import DocumentParseOptions, DocumentParseTaskRequest
from src.schemas.project import ProjectCreateRequest
from test_document_parse_tasks import parsing_environment, upload

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DOCUMENT_PARSERS") != "1", reason="actual isolated PG/MySQL/RustFS parsing catalog")]


async def test_older_submission_finishing_later_cannot_replace_newer_snapshot(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, _, _):
        store.lease_seconds = 30
        project, doc, _ = await upload(env, tmp_path)
        first = await service.submit(project.id, doc.id, DocumentParseTaskRequest(
            creation_key="older-plain", version_id=doc.current_version_id))
        first_claim = await store.claim(project.id, first.id, worker_id="older-worker")
        second = await service.submit(project.id, doc.id, DocumentParseTaskRequest(
            creation_key="newer-layout", version_id=doc.current_version_id,
            options=DocumentParseOptions(pdf_extraction_mode="layout")))
        second_done = await service.execute(await store.claim(project.id, second.id, worker_id="newer-worker"))
        first_done = await service.execute(first_claim)
        old_snapshot, old_result = await service.read_snapshot(project.id, doc.id, first_done.snapshot_id)
        new_snapshot, new_result = await service.read_snapshot(project.id, doc.id, second_done.snapshot_id)
        assert first.created_at < second.created_at
        assert old_snapshot.created_at > new_snapshot.created_at
        assert old_result.options.pdf_extraction_mode == "plain"
        assert new_result.options.pdf_extraction_mode == "layout"
        default = await env.docs.read_document_content(doc.id)
        assert default["parse_snapshot"]["id"] == new_snapshot.id, "late older Worker replaced newer submitted result"
        assert (await service.read_snapshot(project.id, doc.id, old_snapshot.id))[1] == old_result
        listed = await service.list_snapshots(project.id, doc.id, version_id=doc.current_version_id)
        assert [entry.id for entry in listed.items] == [new_snapshot.id, old_snapshot.id]


async def test_http_catalog_paginates_fixed_versions_and_does_not_leak_other_projects(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, _):
        store.lease_seconds = 30
        project, doc, _ = await upload(env, tmp_path)
        other = await env.projects.create(ProjectCreateRequest(project_key="other-catalog", name="其他项目"))
        tasks = []
        for key in ["catalog-one", "catalog-two", "catalog-three"]:
            task = await service.submit(project.id, doc.id, DocumentParseTaskRequest(
                creation_key=key, version_id=doc.current_version_id))
            tasks.append(await service.execute(await store.claim(project.id, task.id, worker_id="catalog-worker")))
        app = FastAPI()
        app.state.document_parsing_service = service
        app.state.document_parser_config_service = configs
        app.include_router(router, prefix="/api/v1")
        base = f"/api/v1/projects/{project.id}/documents/{doc.id}"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://catalog") as client:
            capabilities = await client.get(f"/api/v1/projects/{project.id}/document-parsers")
            assert capabilities.status_code == 200
            assert {item["parser_id"] for item in capabilities.json() if item["available"]} == {"markdown", "docx", "pdf"}
            assert any(item["transport"] == "http" and not item["available"] for item in capabilities.json())
            response = await client.get(base + "/parse-tasks", params={"limit": 2})
            assert response.status_code == 200, response.text
            page = response.json()
            assert [item["id"] for item in page["items"]] == [tasks[2].id, tasks[1].id]
            assert page["has_more"] and page["limit"] == 2 and page["offset"] == 0
            rest = (await client.get(base + "/parse-tasks", params={"limit": 2, "offset": 2})).json()
            assert [item["id"] for item in rest["items"]] == [tasks[0].id] and not rest["has_more"]
            snapshots = await client.get(base + "/parse-snapshots", params={"version_id": doc.current_version_id, "limit": 2})
            assert snapshots.status_code == 200, snapshots.text
            assert [item["id"] for item in snapshots.json()["items"]] == [tasks[2].snapshot_id, tasks[1].snapshot_id]
            assert snapshots.json()["has_more"]
            for suffix in ["/parse-tasks", "/parse-snapshots"]:
                assert (await client.get(base.replace(project.id, other.id) + suffix)).status_code == 404
                assert (await client.get(base + suffix, params={"limit": 0})).status_code == 422
            missing = await client.get(base + "/parse-snapshots", params={"version_id": str(uuid4())})
            assert missing.status_code == 404


async def test_http_failure_diagnostics_read_actual_trace_and_reject_corruption(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, _):
        store.lease_seconds = 30
        project, doc, _ = await upload(env, tmp_path, encrypted=True)
        task = await service.submit(project.id, doc.id, DocumentParseTaskRequest(
            creation_key="encrypted-diagnostic", version_id=doc.current_version_id))
        app = FastAPI()
        app.state.document_parsing_service = service
        app.state.document_parser_config_service = configs
        app.include_router(router, prefix="/api/v1")
        base = f"/api/v1/projects/{project.id}/documents/{doc.id}/parse-tasks/{task.id}/failure"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://diagnostic") as client:
            assert (await client.get(base)).status_code == 409
            completed = await service.execute(await store.claim(project.id, task.id, worker_id="failure-catalog-worker"))
            assert completed.status == "failed" and completed.failure_artifact
            response = await client.get(base)
            assert response.status_code == 200, response.text
            trace = response.json()
            assert trace["task_id"] == task.id and trace["exit_code"] == completed.failure_artifact.exit_code
            # The fixed Worker writes parser errors to its result file; stdout/stderr can honestly be empty.
            assert "加密" in trace["error"] and isinstance(trace["stdout"], str) and isinstance(trace["stderr"], str)
            original_read = env.objects.read_object_uri
            async def corrupt(uri):
                value = await original_read(uri)
                return {**value, "content": value["content"] + b"changed"}
            env.objects.read_object_uri = corrupt
            try:
                rejected = await client.get(base)
                assert rejected.status_code == 409 and "哈希" in rejected.json()["detail"]
            finally:
                env.objects.read_object_uri = original_read
            assert (await service.get(project.id, doc.id, task.id)).status == "failed"
            assert (await service.list_snapshots(project.id, doc.id)).items == []
