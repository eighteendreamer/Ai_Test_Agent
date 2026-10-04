from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.documents.difference_process import DocumentDifferenceProcess
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.document_parsing import DocumentParseSnapshotView, DocumentParseTaskRequest, DocumentParsingSettings
from src.schemas.project import ProjectCreateRequest
from test_document_differences import verify_spans, write_pair
from test_document_parse_tasks import parsing_environment

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_DIFFERENCES") != "1",
    reason="isolated actual MySQL/PostgreSQL/RustFS comparison inputs")]


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_compare_actual_uploaded_versions_and_verified_storage_snapshots(tmp_path, extension):
    async with parsing_environment(tmp_path) as (env, service, store, configs, _):
        store.lease_seconds = 30
        project = await env.projects.create(ProjectCreateRequest(project_key="actual-diff", name="真实文档比较验收"))
        paths = write_pair(tmp_path, extension)
        settings = DocumentParsingSettings()
        settings.policy.timeout_seconds = 65
        config = await configs.save_settings(project.id, settings)
        doc = await env.docs.upload_document(filename=paths[0].name, project_id=project.id, request_id=uuid4().hex,
            content_base64=base64.b64encode(paths[0].read_bytes()).decode())
        views = []
        for number in (1, 2):
            if number == 1:
                version = doc.current_version_id
            else:
                result = await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(project_id=project.id,
                    request_id="actual-v2", expected_parent_version_id=doc.current_version_id, filename=paths[1].name,
                    content_base64=base64.b64encode(paths[1].read_bytes()).decode()))
                version = result.version.id
                settings.policy.timeout_seconds = 80
                await configs.save_settings(project.id, settings)
            task = await service.submit(project.id, doc.id,
                DocumentParseTaskRequest(creation_key=f"actual-v{number}", version_id=version))
            completed = await service.execute(await store.claim(project.id, task.id, worker_id="actual-difference-fixture"))
            assert completed.status == "completed"
            snapshot, extracted = await service.read_snapshot(project.id, doc.id, completed.snapshot_id)
            views.append(DocumentParseSnapshotView(snapshot=snapshot, result=extracted))
        assert views[0].snapshot.seed.config.revision == config.revision
        assert views[0].snapshot.seed.config.payload.policy.timeout_seconds == 65
        assert views[1].snapshot.seed.config.payload.policy.timeout_seconds == 80
        before = []
        for view in views:
            original = await env.objects.read_object_uri(view.snapshot.seed.original_asset.storage_uri)
            raw = (await env.objects.read_object_uri(view.snapshot.storage_uri))["content"]
            assert hashlib.sha256(raw).hexdigest() == view.snapshot.sha256
            assert original["content"] == paths[len(before)].read_bytes()
            before.append(raw)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._snapshots} ORDER BY id")
            metadata_before = cur.fetchall()
        diff = await DocumentDifferenceProcess().compare(old=views[0], new=views[1])
        assert [(c.old.text, c.new.text) for c in diff.raw.changes[0].characters] == [("7", "9")]
        assert diff.old.project_id == diff.new.project_id == project.id
        assert diff.old.document_id == diff.new.document_id == doc.id
        assert diff.old.version_id != diff.new.version_id
        assert diff.old.snapshot_id == views[0].snapshot.id and diff.new.snapshot_id == views[1].snapshot.id
        assert not diff.parsing_basis_changed, "仅任务超时配置变化不能当作解析器/内容变化"
        verify_spans(diff, *views)
        other = await env.projects.create(ProjectCreateRequest(project_key="other-diff", name="其他比较项目"))
        with pytest.raises(KeyError, match="当前项目"):
            await service.read_snapshot(other.id, doc.id, views[0].snapshot.id)
        for index, view in enumerate(views):
            assert (await env.objects.read_object_uri(view.snapshot.storage_uri))["content"] == before[index]
            restored = await service.read_snapshot(project.id, doc.id, view.snapshot.id)
            assert restored == (view.snapshot, view.result)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._snapshots} ORDER BY id")
            assert cur.fetchall() == metadata_before
        evidence = os.getenv("QA_DOCUMENT_DIFFERENCE_STORAGE_EVIDENCE_DIR")
        if evidence:
            directory = Path(evidence) / extension
            directory.mkdir(parents=True, exist_ok=True)
            for path in paths:
                (directory / path.name).write_bytes(path.read_bytes())
            for number, view in enumerate(views, 1):
                (directory / f"snapshot-v{number}.json").write_text(view.model_dump_json(indent=2), encoding="utf-8")
            (directory / "difference.json").write_text(diff.model_dump_json(indent=2), encoding="utf-8")
