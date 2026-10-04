from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.documents.impact_batches import DocumentImpactBatches
from src.application.documents.impact_config_service import BATCH_CONFIG_KEY
from src.application.documents.impact_sources import impact_record_hash
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.document_impact import DocumentImpactTaskRequest
from src.schemas.document_impact_batches import (
    DocumentImpactBatchManifest, DocumentImpactBatchPolicy, DocumentImpactCaseCitation, DocumentImpactTextCitation,
)
from test_document_impact_tasks import evidence, expire, task_environment
from test_document_impact_sources import draft


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires actual isolated MySQL/PostgreSQL/RustFS and real document parsing/comparison")]


def archive_files(paths, name):
    directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
    if directory:
        output = Path(directory) / name
        output.mkdir(parents=True, exist_ok=True)
        for path in paths:
            (output / path.name).write_bytes(path.read_bytes())


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_real_files_manifest_complete_retrieval_and_citations(tmp_path, extension):
    async with task_environment(tmp_path, extension) as values:
        env, project, doc, paths, views, diff_id, _, configs, case, store, service = values
        content = {**case.version.content_json, "a.b": {"01": [False, None, 0]}}
        original = case.version.model_dump(include={"execution_notes", "source_refs", "model_key",
            "prompt_version", "skill_versions"})
        extra = await env.cases.create_version(case.case.id, VersionRequest(content_json=content, **original))
        manual = await draft(env, project, "missing-source", [{"source_type": "manual", "source_id": "human-notes"}])
        settings = await configs.save_batch_settings(project.id, DocumentImpactBatchPolicy(
            max_segment_chars=16, boundary_context_chars=5))
        assert env.configs.get(BATCH_CONFIG_KEY, scope_key=project.id).payload == settings.payload.model_dump(mode="json")
        task = await service.submit(project.id, doc.id,
            DocumentImpactTaskRequest(creation_key="fixed-scope", difference_snapshot_id=diff_id))
        claim = await store.claim(project.id, task.id, worker_id="manifest-worker")
        assert claim.seed.batch_settings == settings
        await service.prepare_input(claim)
        input_checkpoint, envelope = await service.read_input(project.id, doc.id, task.id)
        checkpoint = await service.prepare_manifest(claim)
        reread, manifest = await service.read_manifest(project.id, doc.id, task.id)
        assert checkpoint == reread and manifest.analysis_status == "not_started"
        assert len(manifest.sources) == 2  # old and original share the exact same snapshot
        old = next(source for source in manifest.sources if "old" in source.roles)
        assert len(old.roles) == 2 and old.snapshot_id == views[0].snapshot.id
        assert {unit.version_id for unit in manifest.cases} == {case.version.id, extra.id, manual.version.id}
        assert manifest.source_gaps[0].case_version_id == manual.version.id
        assert manifest.source_gaps[0].status == "missing" and manifest.source_gaps[0].codes == ["non_document_source"]
        range_evidence = []
        for source in manifest.sources:
            expected = views[0].result.text if "old" in source.roles else views[1].result.text
            reconstructed = []
            for segment in (item for item in manifest.segments if item.source_id == source.id):
                value = DocumentImpactBatches.read_segment(claim.seed, envelope, manifest, segment.id)
                assert value["text"] == expected[segment.read_start_char:segment.read_end_char]
                assert 0 <= segment.end_char - segment.start_char <= 16
                reconstructed.append(expected[segment.start_char:segment.end_char])
                assert segment.text_sha256 == hashlib.sha256(reconstructed[-1].encode()).hexdigest()
                assert all(block["source"]["kind"] == {"md": "line", "docx": "docx", "pdf": "page"}[extension]
                    for block in value["blocks"])
                range_evidence.append(value)
            assert "".join(reconstructed) == expected  # includes separators and trailing text
            assert source.text_sha256 == hashlib.sha256(expected.encode()).hexdigest()
            citation = DocumentImpactTextCitation(source_id=source.id, text_sha256=source.text_sha256,
                start_char=0, end_char=min(20, len(expected)), quote=expected[:20])
            DocumentImpactBatches.validate_text_citation(claim.seed, envelope, manifest, citation)
            with pytest.raises(ValueError, match="原文不一致"):
                DocumentImpactBatches.validate_text_citation(claim.seed, envelope, manifest,
                    citation.model_copy(update={"quote": "fabricated"}))
        for kind, entries in DocumentImpactBatches._differences(envelope.input).items():
            assert len([unit for unit in manifest.differences if unit.kind == kind]) == len(entries)
            for index, entry in enumerate(entries):
                assert DocumentImpactBatches.read_difference(claim.seed, envelope, manifest, kind, index)[
                    "difference"] == entry.model_dump(mode="json")
        complete = DocumentImpactBatches.read_case(claim.seed, envelope, manifest, extra.id)
        assert complete["case"]["version"]["content_json"] == content
        assert len(complete["case"]["version"]["content_json"]["steps"]) == 12
        assert complete["source_links"]
        for index, value in enumerate([False, None, 0]):
            citation = DocumentImpactCaseCitation(version_id=extra.id, version_record_sha256=impact_record_hash(extra),
                path=["a.b", "01", index], value=value)
            DocumentImpactBatches.validate_case_citation(claim.seed, envelope, manifest, citation)
            with pytest.raises(ValueError):
                DocumentImpactBatches.validate_case_citation(claim.seed, envelope, manifest,
                    citation.model_copy(update={"path": ["a", "b", "01", index]}))
        bool_citation = DocumentImpactCaseCitation(version_id=extra.id, version_record_sha256=impact_record_hash(extra),
            path=["a.b", "01", 0], value=0)
        with pytest.raises(ValueError, match="引用值"):
            DocumentImpactBatches.validate_case_citation(claim.seed, envelope, manifest, bool_citation)
        for bad_path in (["a.b", "01", -1], ["a.b", "01", "0"]):
            with pytest.raises(ValueError, match="路径不存在"):
                DocumentImpactBatches.validate_case_citation(claim.seed, envelope, manifest,
                    bool_citation.model_copy(update={"path": bad_path}))
        with pytest.raises(ValueError):
            DocumentImpactCaseCitation(version_id=extra.id, version_record_sha256=impact_record_hash(extra),
                path=[True], value=False)
        with pytest.raises(ValueError, match="范围无效"):
            DocumentImpactBatches.read_range(claim.seed, envelope, manifest, old.id, -1, 2)
        with pytest.raises(ValueError, match="没有对应"):
            DocumentImpactBatches.read_case(claim.seed, envelope, manifest, str(uuid4()))
        truncated = manifest.model_copy(update={"cases": manifest.cases[:-1]})
        with pytest.raises(ValueError, match="清单与固定输入"):
            DocumentImpactBatches.validate(claim.seed, envelope, truncated)
        with pytest.raises(ValueError, match="遗漏|完整分段"):
            DocumentImpactBatchManifest.model_validate(manifest.model_dump() | {"segments": manifest.segments[:-1]})
        raw = (await env.objects.read_object_uri(checkpoint.storage_uri))["content"]
        assert hashlib.sha256(raw).hexdigest() == checkpoint.sha256
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._manifests} WHERE task_id=%s", (task.id,))
            assert cur.fetchone()["record"] == checkpoint.model_dump(mode="json")
        assert (await service.get(project.id, doc.id, task.id)).status == "running"
        assert input_checkpoint.input_sha256 == checkpoint.input_sha256
        archive_files(paths, "manifest/" + extension)
        evidence("manifest/" + extension + "/scope.json", manifest=manifest.model_dump(mode="json"),
            checkpoint=checkpoint.model_dump(mode="json"), retrievals=range_evidence,
            complete_case=complete, model_calls=0, source_reconstruction_verified=True)


async def test_manifest_commit_lost_reply_and_restart_preserve_fixed_scope(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, configs, _, store, service = values
        await configs.save_batch_settings(project.id, DocumentImpactBatchPolicy(max_segment_chars=15))
        task = await service.submit(project.id, doc.id,
            DocumentImpactTaskRequest(creation_key="lost-manifest-reply", difference_snapshot_id=diff_id))
        first = await store.claim(project.id, task.id, worker_id="first")
        await service.prepare_input(first)
        save = store.save_manifest_checkpoint
        async def lost_reply(*args, **kwargs):
            await save(*args, **kwargs)
            raise ConnectionError("manifest PG commit succeeded, reply was lost")
        with monkeypatch.context() as patch:
            patch.setattr(store, "save_manifest_checkpoint", lost_reply)
            with pytest.raises(ConnectionError):
                await service.prepare_manifest(first)
        checkpoint, manifest = await service.read_manifest(project.id, doc.id, task.id)
        await configs.save_batch_settings(project.id, DocumentImpactBatchPolicy(max_segment_chars=1, max_retrieval_bytes=1))
        env.impact_skill_file.unlink()
        expire(store, task.id)
        restarted = PostgresDocumentImpactStore(env.settings)
        recovery = await restarted.claim(project.id, task.id, worker_id="restarted")
        assert recovery.task.phase == "analyzing" and recovery.task.deadline_at == first.task.deadline_at
        second_service = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
            projects=env.projects, objects=env.objects, stream=None)
        async def forbidden(*args, **kwargs):
            raise AssertionError("saved manifest must not read current configuration, reprepare evidence, or upload")
        with monkeypatch.context() as patch:
            patch.setattr(configs, "get_batch_settings", forbidden)
            patch.setattr(sources, "prepare_fixed", forbidden)
            patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
            assert await second_service.prepare_manifest(recovery) == checkpoint
            assert await second_service.read_manifest(project.id, doc.id, task.id) == (checkpoint, manifest)
            assert (await second_service.submit(project.id, doc.id,
                DocumentImpactTaskRequest(creation_key="lost-manifest-reply", difference_snapshot_id=diff_id))).id == task.id
        results = await asyncio.gather(*(restarted.save_manifest_checkpoint(recovery, checkpoint) for _ in range(5)))
        assert results == [checkpoint] * 5
        with pytest.raises(ContinuationLeaseLostError):
            await store.save_manifest_checkpoint(first, checkpoint)
        with pytest.raises(ValueError, match="不能改写"):
            await restarted.save_manifest_checkpoint(recovery, checkpoint.model_copy(update={"sha256": "0" * 64}))
        with pytest.raises(ValueError, match="不能替换"):
            await restarted.save_manifest_checkpoint(recovery, checkpoint.model_copy(update={"input_sha256": "0" * 64}))
        await restarted.finish_failed(recovery, error_code="fixture_analysis_not_started", message="fixture scope only")
        assert await restarted.claim(project.id, task.id, worker_id="terminal") is None
        assert await second_service.read_manifest(project.id, doc.id, task.id) == (checkpoint, manifest)
        evidence("manifest/recovery.json", task_id=task.id, checkpoint=checkpoint.model_dump(mode="json"),
            original_deadline_preserved=True, objects_reuploaded=0, model_calls=0, independent_process_restart=False)


async def test_long_actual_markdown_cross_section_read_and_limits_do_not_omit_scope(tmp_path, monkeypatch):
    import test_document_difference_tasks
    def long_pair(directory, extension):
        assert extension == "md"
        paths = [directory / f"long-v{number}.md" for number in (1, 2)]
        for days, path in zip((7, 9), paths):
            parts = [f"# Retention\r\nRetention: {days} days. See section Final.\r\n\r\n"]
            parts += [f"## Section {index}\r\n" + "中文😀正文。" * 30 + "\r\n\r\n" for index in range(110)]
            parts += ["## Final\r\n", "X" * 2000 + "\r\nFinal rule is authoritative.\r\n"]
            path.write_bytes("".join(parts).encode("utf-8"))
        return paths
    monkeypatch.setattr(test_document_difference_tasks, "write_pair", long_pair)
    async with task_environment(tmp_path) as values:
        env, project, doc, paths, views, diff_id, _, configs, _, store, service = values
        await configs.save_batch_settings(project.id, DocumentImpactBatchPolicy(
            max_segment_chars=650, boundary_context_chars=20, max_retrieval_bytes=150000))
        task = await service.submit(project.id, doc.id,
            DocumentImpactTaskRequest(creation_key="long-catalog", difference_snapshot_id=diff_id))
        claim = await store.claim(project.id, task.id, worker_id="long-manifest")
        await service.prepare_input(claim)
        _, envelope = await service.read_input(project.id, doc.id, task.id)
        await service.prepare_manifest(claim)
        _, manifest = await service.read_manifest(project.id, doc.id, task.id)
        assert len(manifest.segments) > 40 and any(item.split_block_ids for item in manifest.segments)
        old = next(source for source in manifest.sources if "old" in source.roles)
        final = next(section for section in old.sections if section.title == "Final")
        retrieved = DocumentImpactBatches.read_range(claim.seed, envelope, manifest, old.id, final.start_char, final.end_char)
        assert "Final rule is authoritative." in retrieved["text"]
        assert retrieved["text"] == views[0].result.text[final.start_char:final.end_char]
        assert "".join(views[0].result.text[s.start_char:s.end_char]
            for s in manifest.segments if s.source_id == old.id) == views[0].result.text
        limited_seed = claim.seed.model_copy(deep=True)
        limited_seed.batch_settings.payload.max_segments = 1
        with pytest.raises(ValueError, match="分段数量"):
            DocumentImpactBatches.build(limited_seed, envelope)
        limited_seed.batch_settings.payload.max_segments = 10000
        limited_seed.batch_settings.payload.max_retrieval_bytes = 1
        limited = DocumentImpactBatches.build(limited_seed, envelope)
        with pytest.raises(ValueError, match="未截断"):
            DocumentImpactBatches.read_case(limited_seed, envelope, limited, limited.cases[0].version_id)
        with pytest.raises(ValueError, match="未截断"):
            DocumentImpactBatches.read_range(limited_seed, envelope, limited, old.id, final.start_char, final.end_char)
        archive_files(paths, "manifest/long-md")
        evidence("manifest/long-md/result.json", manifest=manifest.model_dump(mode="json"),
            source_chars=len(views[0].result.text), cross_section_retrieval=retrieved,
            no_omission=True, model_calls=0, token_measurement="not_available")


async def test_manifest_corruption_legacy_policy_and_project_scope(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, _, _, store, service = values
        request = DocumentImpactTaskRequest(creation_key="legacy-batch", difference_snapshot_id=diff_id)
        legacy = await sources.capture(project.id, doc.id, diff_id, request)
        task = await store.submit(legacy, creation_key=request.creation_key, request_hash=store.request_hash(request),
            stream=service._stream)
        claim = await store.claim(project.id, task.id, worker_id="legacy")
        await service.prepare_input(claim)
        with pytest.raises(ValueError, match="历史影响任务未冻结分批"):
            await service.prepare_manifest(claim)
        assert await store.get_manifest_checkpoint(project.id, doc.id, task.id) is None
        new = await service.submit(project.id, doc.id, request.model_copy(update={"creation_key": "corrupt-manifest"}))
        claim = await store.claim(project.id, new.id, worker_id="corruption")
        await service.prepare_input(claim)
        checkpoint = await service.prepare_manifest(claim)
        read = env.objects.read_object_uri
        async def corrupt(uri, **kwargs):
            value = await read(uri, **kwargs)
            return {**value, "content": value["content"] + b" "} if uri == checkpoint.storage_uri else value
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", corrupt)
            with pytest.raises(ValueError, match="对象大小或哈希"):
                await service.read_manifest(project.id, doc.id, new.id)
        assert await store.get_manifest_checkpoint(str(uuid4()), doc.id, new.id) is None
        assert await store.get_manifest_checkpoint(project.id, str(uuid4()), new.id) is None
        deadline_task = await service.submit(project.id, doc.id,
            request.model_copy(update={"creation_key": "expired-original-budget"}))
        deadline_claim = await store.claim(project.id, deadline_task.id, worker_id="deadline")
        await service.prepare_input(deadline_claim)
        # Original absolute deadline is enforced even with a still-live lease.
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._tasks} SET deadline_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s",
                (deadline_task.id,))
        with pytest.raises(TimeoutError, match="原预算已耗尽"):
            await service.prepare_manifest(deadline_claim)
        assert await store.get_manifest_checkpoint(project.id, doc.id, deadline_task.id) is None
        evidence("manifest/boundaries.json", legacy_config_not_invented=True, corrupted_object_rejected=True,
            cross_project_read_rejected=True, model_calls=0)
