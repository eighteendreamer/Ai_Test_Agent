from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document

from src.application.documents.impact_config_service import CONFIG_KEY, DocumentImpactConfigService
from src.application.documents.impact_sources import DocumentImpactSources
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.case_management import TestCaseDraftCreateRequest as DraftRequest, TestCaseVersionCreateRequest as VersionRequest
from src.schemas.document_impact import DocumentImpactPolicy, DocumentImpactSelection
from src.schemas.document_parsing import DocumentParseSnapshotView, DocumentParseTaskRequest
from src.schemas.project import ProjectCreateRequest
from test_document_difference_tasks import comparison_environment, inputs
from test_case_planning import environment


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_SOURCES") != "1",
    reason="requires isolated real MySQL/PostgreSQL/RustFS and actual document parser/comparison")]


def source(doc, view, *, parsed=True, **changes):
    metadata = {"content_asset_id": view.snapshot.seed.original_asset.id,
        "content_sha256": hashlib.sha256(view.result.text.encode()).hexdigest() if parsed else view.snapshot.seed.original_asset.sha256}
    if parsed:
        metadata["parse_snapshot"] = view.snapshot.model_dump(mode="json")
    return {"source_type": "api_doc", "source_id": doc.id, "version": view.snapshot.seed.version_id,
        "metadata": metadata, **changes}


async def draft(env, project, key, refs, *, mode="api_testing"):
    return await env.cases.create_draft(project_id=project.id, payload=DraftRequest(case_key=key,
        title="验证保留期限", case_type="json", mode_key=mode,
        content_json={"目标": "验证订单保留7天", "steps": [{"操作": f"完整步骤{i}"} for i in range(12)],
            "深层扩展": {"零": 0, "空": None, "布尔": False, "未知字段": "不能丢失"}},
        execution_notes="按照全部业务JSON判断变化", source_refs=refs, model_key="manual",
        prompt_version="manual-v1", skill_versions={"manual": "v1"}))


async def setup(env, service, store, configs, parsing, parse_store, tmp_path, extension="md"):
    project, doc, paths, views, request = await inputs(env, parsing, parse_store, tmp_path, extension)
    task = await service.submit(project.id, doc.id, request)
    result = await service.execute(await store.claim(project.id, task.id, worker_id="impact-input-comparison"))
    assert result.status == "completed"
    impact_configs = DocumentImpactConfigService(config_store=env.configs, project_service=env.projects)
    await impact_configs.initialize()
    sources = DocumentImpactSources(documents=env.docs, parsing=parsing, differences=service,
        cases=env.cases, configs=impact_configs)
    return project, doc, paths, views, result.snapshot_id, sources, impact_configs


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_fixed_three_sides_current_and_draft_cross_mode_and_reparse(tmp_path, monkeypatch, extension):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, paths, views, diff_id, sources, impact_configs = await setup(
            env, service, store, configs, parsing, parse_store, tmp_path, extension)
        first = await draft(env, project, "retention", [source(doc, views[0])])
        await env.cases.submit_review(first.case.id)
        await env.cases.activate(first.case.id)
        second = await env.cases.create_version(first.case.id, VersionRequest(**first.version.model_dump(
            include={"content_json", "execution_notes", "source_refs", "model_key", "prompt_version", "skill_versions"})))
        current_case = await env.cases.get_case(first.case.id)
        active_page = await env.cases.list_cases(project_id=project.id, status="active", mode_key=None,
            priority=None, query=None, limit=50, offset=0)
        lifecycle_gap = {"old_active_version_id": first.version.id, "current": current_case.model_dump(mode="json"),
            "active_candidate_case_ids": [case.id for case in active_page.items]}
        assert current_case.active_version_id == first.version.id
        assert current_case.lifecycle_status == "draft"
        assert first.case.id not in lifecycle_gap["active_candidate_case_ids"]
        unrelated = await draft(env, project, "manual-ui", [{"source_type": "manual", "source_id": "human-notes"}], mode="ui_testing")
        archived = await draft(env, project, "archived", [source(doc, views[0])])
        await env.cases.archive(archived.case.id)
        other = await env.projects.create(ProjectCreateRequest(project_key="other", name="其他项目"))
        foreign = await draft(env, other, "retention", [source(doc, views[0])])
        # A newer parse of V1 must never replace the original generation snapshot.
        task = await parsing.submit(project.id, doc.id, DocumentParseTaskRequest(
            creation_key="reparse-old-v1", version_id=views[0].snapshot.seed.version_id))
        reparsed = await parsing.execute(await parse_store.claim(project.id, task.id, worker_id="impact-reparse"))
        assert reparsed.status == "completed" and reparsed.snapshot_id != views[0].snapshot.id
        # Move current document to V3 while retaining the V1 -> V2 comparison input.
        current = await env.docs.get_document(doc.id)
        await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(project_id=project.id,
            request_id="restore-as-v3", expected_parent_version_id=current.current_version_id,
            filename=paths[0].name, content_base64=base64.b64encode(paths[0].read_bytes()).decode()))
        async def forbidden(*args, **kwargs):
            raise AssertionError("影响来源不可读取当前全文或latest_snapshot")
        monkeypatch.setattr(env.docs, "read_document_content", forbidden)
        monkeypatch.setattr(env.docs, "read_version_content", forbidden)
        monkeypatch.setattr(parsing, "latest_snapshot", forbidden)
        result = await sources.prepare(project.id, doc.id, diff_id, DocumentImpactSelection())
        ids = {bundle.version.id for bundle in result.input.cases}
        assert ids == {first.version.id, second.id, unrelated.version.id}
        assert foreign.version.id not in ids and archived.version.id not in ids
        assert result.input.old.snapshot == views[0].snapshot and result.input.new.snapshot == views[1].snapshot
        assert result.input.difference.raw.changes[0].characters[0].old.text == "7"
        assert result.input.difference.raw.changes[0].characters[0].new.text == "9"
        assert len(result.input.originals) == 1
        assert result.input.originals[0].parse.snapshot.id == views[0].snapshot.id
        assert "7" in result.input.originals[0].text
        for bundle in result.input.cases:
            assert bundle.version.content_json["steps"][11]["操作"] == "完整步骤11"
            assert bundle.version.content_json["深层扩展"]["布尔"] is False
        assert next(link for link in result.input.source_links if link.case_version_id == unrelated.version.id).codes == ["non_document_source"]
        canonical = json.dumps(result.input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False)
        assert result.input_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
        assert (await sources.prepare(project.id, doc.id, diff_id, DocumentImpactSelection())).input_sha256 == result.input_sha256
        policy = DocumentImpactPolicy(max_case_versions=1)
        await impact_configs.save_settings(project.id, policy)
        assert MySQLApplicationConfigStore(env.settings).get(CONFIG_KEY, scope_key=project.id).payload["max_case_versions"] == 1
        assert result.input.config.payload.max_case_versions == 500
        with pytest.raises(ValueError, match="未截断输入"):
            await sources.prepare(project.id, doc.id, diff_id, DocumentImpactSelection())
        # Historical/archived versions are still explicitly inspectable.
        selected = await sources.prepare(project.id, doc.id, diff_id,
            DocumentImpactSelection(case_version_ids=[archived.version.id, archived.version.id]))
        assert [bundle.version.id for bundle in selected.input.cases] == [archived.version.id]
        with pytest.raises(KeyError, match="不属于当前项目"):
            await sources.prepare(project.id, doc.id, diff_id, DocumentImpactSelection(case_version_ids=[foreign.version.id]))
        folder = Path(os.environ.get("QA_DOCUMENT_IMPACT_EVIDENCE_DIR", str(tmp_path))) / extension
        folder.mkdir(parents=True, exist_ok=True)
        (folder/"input.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        (folder/"existing-lifecycle-gap.json").write_text(json.dumps(lifecycle_gap, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder/("v1."+extension)).write_bytes(paths[0].read_bytes())
        (folder/("v2."+extension)).write_bytes(paths[1].read_bytes())


async def test_exact_text_asset_gaps_corruption_scope_and_budgets(tmp_path, monkeypatch):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, paths, views, diff_id, sources, impact_configs = await setup(
            env, service, store, configs, parsing, parse_store, tmp_path)
        plain = source(doc, views[0], parsed=False)
        broken = source(doc, views[0], version="legacy-timestamp")
        mismatch = source(doc, views[0], parsed=False)
        mismatch["metadata"]["content_sha256"] = "a" * 64
        truncated = source(doc, views[0])
        truncated["metadata"]["truncated"] = True
        other = await env.projects.create(ProjectCreateRequest(project_key="foreign-doc", name="其他文档项目"))
        other_doc = await env.docs.upload_document(filename="foreign.md", project_id=other.id,
            content_base64=base64.b64encode(b"foreign content must not be read").decode())
        foreign = {**plain, "source_id": other_doc.id, "version": other_doc.current_version_id}
        wrong_asset = source(doc, views[1], parsed=False)
        wrong_asset["metadata"]["content_asset_id"] = views[0].snapshot.seed.original_asset.id
        wrong_snapshot = source(doc, views[0], version=views[1].snapshot.seed.version_id)
        bundle = await draft(env, project, "gaps", [plain, broken, mismatch, truncated, foreign, wrong_asset, wrong_snapshot])
        selection = DocumentImpactSelection(case_version_ids=[bundle.version.id])
        result = await sources.prepare(project.id, doc.id, diff_id, selection)
        assert result.input.source_links[0].status == "available"
        assert result.input.originals[0].parse is None
        assert result.input.originals[0].asset.sha256 == hashlib.sha256(paths[0].read_bytes()).hexdigest()
        assert [link.codes for link in result.input.source_links] == [[], ["missing_version_identity"],
            ["source_content_hash_mismatch"], ["original_context_truncated"], ["source_project_mismatch"],
            ["source_asset_version_mismatch"], ["source_snapshot_identity_mismatch"]]
        assert not any("foreign content" in evidence.text for evidence in result.input.originals)
        # Corrupt original bytes are observed as a gap, never replaced by current text.
        read = env.objects.read_object_uri
        async def corrupt(uri):
            if uri == views[0].snapshot.seed.original_asset.storage_uri:
                return {"content": b"wrong original bytes"}
            return await read(uri)
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", corrupt)
            damaged = await sources.prepare(project.id, doc.id, diff_id, selection)
            assert damaged.input.source_links[0].codes == ["source_read_failed"]
            assert damaged.input.source_links[3].status == "partial"
        await impact_configs.save_settings(project.id, DocumentImpactPolicy(max_context_chars=1))
        with pytest.raises(ValueError, match="未截断正文或用例"):
            await sources.prepare(project.id, doc.id, diff_id, selection)
        await impact_configs.save_settings(project.id, DocumentImpactPolicy(timeout_seconds=.05))
        original_read = env.docs.read_text_asset
        async def slow(*args, **kwargs):
            await asyncio.sleep(.5)
            return await original_read(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(env.docs, "read_text_asset", slow)
            with pytest.raises(TimeoutError):
                await sources.prepare(project.id, doc.id, diff_id, selection)
        with pytest.raises(KeyError):
            await sources.prepare(other.id, doc.id, diff_id, selection)


async def test_actual_partial_docx_and_missing_binary_parse_are_distinct_gaps(tmp_path):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, _, _, diff_id, sources, _ = await setup(
            env, service, store, configs, parsing, parse_store, tmp_path)
        image = tmp_path / "unread-image.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII="))
        file = tmp_path / "partial.docx"
        document = Document()
        document.add_paragraph("保留期限7天；图片中的需求首期无法提取。")
        document.add_picture(str(image))
        document.save(file)
        uploaded = await env.docs.upload_document(filename=file.name, project_id=project.id,
            content_base64=base64.b64encode(file.read_bytes()).decode())
        task = await parsing.submit(project.id, uploaded.id, DocumentParseTaskRequest(
            creation_key="partial-picture", version_id=uploaded.current_version_id))
        completed = await parsing.execute(await parse_store.claim(project.id, task.id, worker_id="partial-docx"))
        snapshot, parsed = await parsing.read_snapshot(project.id, uploaded.id, completed.snapshot_id)
        view = DocumentParseSnapshotView(snapshot=snapshot, result=parsed)
        assert parsed.status == "partial"
        bundle = await draft(env, project, "partial-source", [source(uploaded, view), source(uploaded, view, parsed=False)])
        result = await sources.prepare(project.id, doc.id, diff_id, DocumentImpactSelection(case_version_ids=[bundle.version.id]))
        assert [(link.status, link.codes) for link in result.input.source_links] == [
            ("partial", ["source_parse_partial"]), ("missing", ["missing_parse_snapshot_identity"])]
        assert result.input.originals[0].parse.result.status == "partial"
        assert result.input.originals[0].parse.result.warnings
        folder = Path(os.environ.get("QA_DOCUMENT_IMPACT_EVIDENCE_DIR", str(tmp_path))) / "partial-docx"
        folder.mkdir(parents=True, exist_ok=True)
        (folder/"input.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        (folder/"source.docx").write_bytes(file.read_bytes())


@pytest.mark.parametrize("live", [False, True])
async def test_current_query_keeps_both_versions_and_returns_independent_fixed_records(tmp_path, live):
    async with environment(tmp_path, live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="current-versions", name="版本查询"))
        first = await draft(env, project, "version-one", [{"source_type": "manual", "source_id": "notes"}])
        await env.cases.submit_review(first.case.id)
        await env.cases.activate(first.case.id)
        second = await env.cases.create_version(first.case.id, VersionRequest(**first.version.model_dump(
            include={"content_json", "execution_notes", "source_refs", "model_key", "prompt_version", "skill_versions"})))
        pairs, more = await env.cases.list_project_current_versions(project.id, 1)
        assert more and len(pairs) == 1
        pairs, more = await env.cases.list_project_current_versions(project.id, 2)
        assert not more and [version.id for _, version in pairs] == [first.version.id, second.id]
        # Current pointers can move after this statement, without changing captured inputs.
        captured = pairs[1][0].model_dump(mode="json")
        await env.cases.submit_review(first.case.id)
        await env.cases.activate(first.case.id)
        assert pairs[1][0].model_dump(mode="json") == captured
        pairs[0][1].content_json["目标"] = "调用方修改"
        assert (await env.cases.get_version(first.version.id)).content_json["目标"] == "验证订单保留7天"
