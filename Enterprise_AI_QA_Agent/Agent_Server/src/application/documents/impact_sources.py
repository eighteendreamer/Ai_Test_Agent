"""Read all three fixed evidence sides before any impact judgement or draft mutation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from uuid import UUID

from src.schemas.case_management import TestCaseBundle
from src.schemas.document_impact import (
    DocumentImpactInput, DocumentImpactInputEnvelope, DocumentImpactOriginalEvidence, DocumentImpactSourceLink,
)
from src.schemas.document_parsing import DocumentParseSnapshot, DocumentParseSnapshotView

logger = logging.getLogger(__name__)


class _SourceGap(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class DocumentImpactSources:
    def __init__(self, *, documents, parsing, differences, cases, configs):
        self._docs, self._parsing, self._differences = documents, parsing, differences
        self._cases, self._configs = cases, configs

    async def prepare(self, project_id, document_id, difference_snapshot_id, selection):
        config = await self._configs.get_settings(project_id)
        try:
            async with asyncio.timeout(config.payload.timeout_seconds):
                result = await self._prepare(project_id, document_id, difference_snapshot_id, selection, config)
        except TimeoutError:
            logger.warning("document_impact_input_timeout", extra={"project_id": project_id,
                "document_id": document_id, "difference_snapshot_id": difference_snapshot_id})
            raise
        except (ValueError, KeyError, RuntimeError) as exc:
            logger.warning("document_impact_input_rejected", extra={"project_id": project_id,
                "document_id": document_id, "difference_snapshot_id": difference_snapshot_id,
                "error_type": type(exc).__name__})
            raise
        logger.info("document_impact_input_prepared", extra={"project_id": project_id,
            "document_id": document_id, "difference_snapshot_id": difference_snapshot_id,
            "case_version_count": len(result.input.cases), "original_count": len(result.input.originals),
            "source_gap_count": sum(link.status != "available" for link in result.input.source_links),
            "input_sha256": result.input_sha256})
        return result

    async def _prepare(self, project_id, document_id, difference_snapshot_id, selection, config):
        snapshot, difference = await self._differences.read_snapshot(project_id, document_id, difference_snapshot_id)
        views = []
        for fixed in (snapshot.seed.old_snapshot, snapshot.seed.new_snapshot):
            actual, result = await self._parsing.read_snapshot(project_id, document_id, fixed.id)
            if actual != fixed:
                raise ValueError("文档影响输入与比较固定解析快照不一致")
            views.append(DocumentParseSnapshotView(snapshot=actual, result=result))
        ids = list(dict.fromkeys(selection.case_version_ids))
        if ids:
            if len(ids) > config.payload.max_case_versions:
                raise ValueError("文档影响用例版本数超过配置限制，未截断输入")
            versions = await self._cases.get_versions(ids)
            cases = await self._cases.get_cases(list(dict.fromkeys(versions[id].case_id for id in ids)))
            pairs = [(cases[versions[id].case_id], versions[id]) for id in ids]
        else:
            pairs, has_more = await self._cases.list_project_current_versions(project_id, config.payload.max_case_versions)
            if has_more:
                raise ValueError("项目当前用例版本数超过影响配置限制，请明确选择范围或调整配置；未截断输入")
        for case, version in pairs:
            if case.project_id != project_id or version.case_id != case.id:
                raise KeyError("文档影响输入的用例版本不属于当前项目")
        bundles = [TestCaseBundle(case=case, version=version) for case, version in pairs]
        # Lower bound before reading more objects; final canonical size is also checked.
        context_chars = sum(len(view.result.text) for view in views) + sum(
            len(bundle.version.model_dump_json()) for bundle in bundles)
        self._check_chars(context_chars, config.payload.max_context_chars)
        originals, links, cache = {}, [], {}
        for bundle in bundles:
            for index, source in enumerate(bundle.version.source_refs):
                key = json.dumps(source.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False)
                if key not in cache:
                    cache[key] = await self._source(project_id, bundle.version.id, source)
                evidence, codes = cache[key]
                if evidence:
                    if evidence.id not in originals:
                        context_chars += len(evidence.text)
                        self._check_chars(context_chars, config.payload.max_context_chars)
                    originals[evidence.id] = evidence
                links.append(DocumentImpactSourceLink(case_version_id=bundle.version.id, source_index=index,
                    source_ref=source, evidence_id=evidence.id if evidence else None,
                    status="missing" if evidence is None else "partial" if codes else "available", codes=codes))
        payload = DocumentImpactInput(project_id=project_id, document_id=document_id,
            selection="explicit" if ids else "project_current", config=config,
            difference_snapshot=snapshot, difference=difference, old=views[0], new=views[1],
            cases=bundles, originals=list(originals.values()), source_links=links)
        canonical = await asyncio.to_thread(lambda: json.dumps(payload.model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
        self._check_chars(len(canonical), config.payload.max_context_chars)
        return DocumentImpactInputEnvelope(input_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(), input=payload)

    @staticmethod
    def _check_chars(count, maximum):
        if count > maximum:
            raise ValueError("完整文档影响输入超过配置限制，未截断正文或用例")

    async def _source(self, project_id, case_version_id, source):
        try:
            if source.source_type != "api_doc":
                raise _SourceGap("non_document_source")
            try:
                UUID(source.source_id)
                UUID(source.version or "")
            except (ValueError, TypeError, AttributeError):
                raise _SourceGap("missing_version_identity")
            document = await self._docs.get_document(source.source_id)
            if document.project_id != project_id:
                raise _SourceGap("source_project_mismatch")
            version = await self._docs.get_version(source.source_id, source.version)
            metadata = source.metadata
            codes = ["original_context_truncated"] if metadata.get("truncated") else []
            parsed = metadata.get("parse_snapshot")
            parse = None
            if parsed:
                expected = DocumentParseSnapshot.model_validate(parsed)
                if (expected.seed.project_id != project_id or expected.seed.document_id != source.source_id or
                    expected.seed.version_id != version.id):
                    raise _SourceGap("source_snapshot_identity_mismatch")
                actual, result = await self._parsing.read_snapshot(project_id, source.source_id, expected.id)
                if actual != expected:
                    raise _SourceGap("source_snapshot_metadata_mismatch")
                if result.status == "unsupported":
                    raise _SourceGap("source_parse_unsupported")
                if result.status == "partial":
                    codes.append("source_parse_partial")
                parse = DocumentParseSnapshotView(snapshot=actual, result=result)
                asset, text = actual.seed.original_asset, result.text
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            else:
                if Path(version.snapshot.filename).suffix.lower() in {".pdf", ".docx"}:
                    raise _SourceGap("missing_parse_snapshot_identity")
                asset_id = metadata.get("content_asset_id")
                if not isinstance(asset_id, str) or not asset_id:
                    raise _SourceGap("missing_content_asset_identity")
                asset, text = await self._docs.read_text_asset(source.source_id, asset_id)
                if asset.version_id != version.id:
                    raise _SourceGap("source_asset_version_mismatch")
                content_hash = asset.sha256
            if metadata.get("content_asset_id") and asset.id != metadata["content_asset_id"]:
                raise _SourceGap("source_asset_identity_mismatch")
            if metadata.get("content_sha256"):
                if content_hash != metadata["content_sha256"]:
                    raise _SourceGap("source_content_hash_mismatch")
            else:
                codes.append("missing_original_content_hash")
            identity = f"{source.source_id}:{version.id}:{asset.id}:{parse.snapshot.id if parse else 'text'}"
            evidence = DocumentImpactOriginalEvidence(id=identity, document_id=source.source_id,
                version_id=version.id, asset=asset, text=text, parse=parse)
            return evidence, codes
        except _SourceGap as exc:
            code = exc.code
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            # Validation/storage errors may embed input text; log identities and type only.
            code = "source_read_failed"
            logger.warning("document_impact_original_read_failed", extra={"project_id": project_id,
                "case_version_id": case_version_id, "source_id": source.source_id, "error_type": type(exc).__name__})
        logger.warning("document_impact_original_gap", extra={"project_id": project_id,
            "case_version_id": case_version_id, "source_id": source.source_id, "code": code})
        return None, [code]
