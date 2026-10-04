"""Deterministic scope and exact retrieval over a saved impact input.

Use parser boundaries and existing character offsets, never model summaries.
This catalog does not claim that any model batch has run or covered the scope.
"""
from __future__ import annotations

import hashlib
import json
import logging
from bisect import bisect_left, bisect_right

from src.application.documents.impact_sources import DocumentImpactSources, impact_record_hash
from src.schemas.document_impact_batches import (
    DocumentImpactBatchManifest, DocumentImpactCaseUnit, DocumentImpactDifferenceUnit,
    DocumentImpactSegment, DocumentImpactSourceGap, DocumentImpactTextSource,
)

logger = logging.getLogger(__name__)


def _hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _overlaps(item, start, end):
    return item.start_char < end and start < item.end_char


class DocumentImpactBatches:
    # No upstream counterpart (checked: document_parsing.py, impact_sources.py,
    # planning_workflow.py, local Agent trajectory course): the fixed impact
    # scope catalog is new domain data; offsets, hashes and leases reuse existing contracts.
    @staticmethod
    def _sources(payload):
        items = {}

        def add(role, document_id, version_id, asset, text, view):
            key = f"parse:{view.snapshot.id}" if view else f"asset:{document_id}:{version_id}:{asset.id}"
            source = DocumentImpactTextSource(id=key, roles=[role], document_id=document_id,
                version_id=version_id, asset_id=asset.id, original_sha256=asset.sha256,
                snapshot_id=view.snapshot.id if view else None, text_sha256=_hash_text(text),
                length_chars=len(text), status=view.result.status if view else "unparsed",
                sections=view.result.sections if view else [], warnings=view.result.warnings if view else [])
            if key in items:
                prior, prior_text, prior_view = items[key]
                if (prior.model_copy(update={"roles": [role]}) != source or prior_text != text or prior_view != view):
                    raise ValueError("相同固定来源身份的正文或解析依据不同")
                prior.roles.append(role)
            else:
                items[key] = source, text, view

        for role, view in (("old", payload.old), ("new", payload.new)):
            fixed = view.snapshot.seed
            add(role, fixed.document_id, fixed.version_id, fixed.original_asset, view.result.text, view)
        for original in payload.originals:
            add("original:" + original.id, original.document_id, original.version_id,
                original.asset, original.text, original.parse)
        return items

    @staticmethod
    def _segment(source, text, view, policy):
        boundaries = sorted({len(text), *([section.start_char for section in view.result.sections] +
            [block.end_char for block in view.result.blocks] if view else [])})
        start = 0
        segments = []
        blocks = view.result.blocks if view else []
        block_starts = [block.start_char for block in blocks]
        block_ends = [block.end_char for block in blocks]
        sections = sorted(source.sections, key=lambda section: section.start_char)
        active_sections, section_index = [], 0
        while start < len(text) or not segments:
            if len(segments) >= policy.max_segments:
                raise ValueError("完整影响分段数量超过固定策略，未省略来源")
            maximum = min(len(text), start + policy.max_segment_chars)
            index = bisect_right(boundaries, maximum) - 1
            end = boundaries[index] if index >= 0 and boundaries[index] > start else maximum
            read_start = max(0, start - policy.boundary_context_chars)
            read_end = min(len(text), end + policy.boundary_context_chars)
            selected_blocks = blocks[bisect_right(block_ends, start):bisect_left(block_starts, end)]
            active_sections = [section for section in active_sections if section.end_char > start]
            while section_index < len(sections) and sections[section_index].start_char < end:
                section = sections[section_index]
                if section.end_char > start:
                    active_sections.append(section)
                section_index += 1
            segments.append(DocumentImpactSegment(id=f"{source.id}:{start}:{end}", source_id=source.id,
                start_char=start, end_char=end, read_start_char=read_start, read_end_char=read_end,
                text_sha256=_hash_text(text[start:end]), block_ids=[block.id for block in selected_blocks],
                section_ids=[section.id for section in active_sections],
                split_block_ids=[block.id for block in selected_blocks if block.start_char < start or block.end_char > end]))
            start = end
        return segments

    @staticmethod
    def _differences(payload):
        return {"raw": payload.difference.raw.changes, "normalized": payload.difference.normalized.changes,
            "section": payload.difference.sections, "table": payload.difference.tables}

    @classmethod
    def build(cls, seed, envelope):
        DocumentImpactSources.validate_fixed_input(seed, envelope)
        if seed.batch_settings is None:
            raise ValueError("历史影响任务未冻结分批策略，请创建新任务")
        catalog = cls._sources(envelope.input)
        segments = []
        for source, text, view in catalog.values():
            segments.extend(cls._segment(source, text, view, seed.batch_settings.payload))
            if len(segments) > seed.batch_settings.payload.max_segments:
                raise ValueError("完整影响分段数量超过固定策略，未省略来源")
        manifest = DocumentImpactBatchManifest(project_id=seed.project_id, document_id=seed.document_id,
            input_sha256=envelope.input_sha256, config=seed.batch_settings,
            sources=[item[0] for item in catalog.values()],
            segments=segments,
            cases=[DocumentImpactCaseUnit(version_id=bundle.version.id, case_id=bundle.case.id,
                content_hash=bundle.version.content_hash, version_record_sha256=impact_record_hash(bundle.version))
                for bundle in envelope.input.cases],
            differences=[DocumentImpactDifferenceUnit(kind=kind, index=index, sha256=impact_record_hash(change))
                for kind, changes in cls._differences(envelope.input).items() for index, change in enumerate(changes)],
            source_gaps=[DocumentImpactSourceGap(**link.model_dump(include={"case_version_id", "source_index",
                "evidence_id", "status", "codes"})) for link in envelope.input.source_links if link.status != "available"])
        logger.info("document_impact_manifest_built", extra={"project_id": seed.project_id,
            "input_sha256": envelope.input_sha256, "source_count": len(manifest.sources),
            "segment_count": len(manifest.segments), "case_count": len(manifest.cases),
            "difference_count": len(manifest.differences), "source_gap_count": len(manifest.source_gaps)})
        return manifest

    @classmethod
    def validate(cls, seed, envelope, manifest):
        if manifest != cls.build(seed, envelope):
            raise ValueError("影响批次清单与固定输入或策略不同")

    @classmethod
    def _catalog(cls, seed, envelope, manifest):
        DocumentImpactSources.validate_fixed_input(seed, envelope)
        if (manifest.input_sha256 != envelope.input_sha256 or manifest.project_id != seed.project_id or
            manifest.document_id != seed.document_id or manifest.config != seed.batch_settings):
            raise ValueError("影响读取不能替换项目、输入或分批策略")
        return cls._sources(envelope.input)

    @classmethod
    def _source(cls, seed, envelope, manifest, source_id):
        found = cls._catalog(seed, envelope, manifest).get(source_id)
        recorded = next((source for source in manifest.sources if source.id == source_id), None)
        if found is None or found[0] != recorded:
            raise ValueError("影响读取没有对应的固定来源")
        return found

    @staticmethod
    def _within_budget(manifest, value):
        size = len(_json_bytes(value))
        if size > manifest.config.payload.max_retrieval_bytes:
            logger.warning("document_impact_retrieval_rejected", extra={"project_id": manifest.project_id,
                "input_sha256": manifest.input_sha256, "size_bytes": size,
                "max_retrieval_bytes": manifest.config.payload.max_retrieval_bytes})
            raise ValueError("完整影响读取超过字节限制，未截断；请选择更小范围或创建新任务调整策略")
        return value

    @classmethod
    def read_range(cls, seed, envelope, manifest, source_id, start_char, end_char):
        source, text, view = cls._source(seed, envelope, manifest, source_id)
        # bool is not an integer position, and Python's negative indexing is not a source location.
        if type(start_char) is not int or type(end_char) is not int or not 0 <= start_char <= end_char <= len(text):
            raise ValueError("影响原文读取范围无效")
        blocks = [block for block in view.result.blocks if _overlaps(block, start_char, end_char)] if view else []
        block_ids = {block.id for block in blocks}
        return cls._within_budget(manifest, {"source": source.model_dump(mode="json", exclude={"sections"}),
            "start_char": start_char, "end_char": end_char, "text": text[start_char:end_char],
            "sections": [section.model_dump(mode="json") for section in source.sections
                if _overlaps(section, start_char, end_char)],
            "blocks": [block.model_dump(mode="json", exclude={"text"}) for block in blocks],
            "tables": [table.model_dump(mode="json") for table in view.result.tables
                if table.block_id in block_ids] if view else []})

    @classmethod
    def read_segment(cls, seed, envelope, manifest, segment_id):
        segment = next((item for item in manifest.segments if item.id == segment_id), None)
        if segment is None:
            raise ValueError("影响分段不存在")
        source, text, _ = cls._source(seed, envelope, manifest, segment.source_id)
        policy = manifest.config.payload
        if (segment.id != f"{source.id}:{segment.start_char}:{segment.end_char}" or
            not 0 <= segment.start_char <= segment.end_char <= len(text) or
            segment.end_char - segment.start_char > policy.max_segment_chars or
            segment.read_start_char != max(0, segment.start_char - policy.boundary_context_chars) or
            segment.read_end_char != min(len(text), segment.end_char + policy.boundary_context_chars) or
            segment.text_sha256 != _hash_text(text[segment.start_char:segment.end_char])):
            raise ValueError("影响分段与固定原文不一致")
        value = cls.read_range(seed, envelope, manifest, segment.source_id, segment.read_start_char, segment.read_end_char)
        value["segment"] = segment.model_dump(mode="json")
        return cls._within_budget(manifest, value)

    @classmethod
    def _case(cls, seed, envelope, manifest, version_id):
        cls._catalog(seed, envelope, manifest)
        bundle = next((item for item in envelope.input.cases if item.version.id == version_id), None)
        recorded = next((item for item in manifest.cases if item.version_id == version_id), None)
        if (bundle is None or recorded is None or recorded.case_id != bundle.case.id or
            recorded.content_hash != bundle.version.content_hash or
            recorded.version_record_sha256 != impact_record_hash(bundle.version)):
            raise ValueError("影响用例没有对应的固定完整版本")
        return bundle

    @classmethod
    def read_case(cls, seed, envelope, manifest, version_id):
        bundle = cls._case(seed, envelope, manifest, version_id)
        return cls._within_budget(manifest, {"case": bundle.model_dump(mode="json"), "source_links": [
            link.model_dump(mode="json") for link in envelope.input.source_links if link.case_version_id == version_id]})

    @classmethod
    def read_difference(cls, seed, envelope, manifest, kind, index):
        cls._catalog(seed, envelope, manifest)
        changes = cls._differences(envelope.input).get(kind)
        if changes is None or type(index) is not int or not 0 <= index < len(changes):
            raise ValueError("影响差异读取范围无效")
        recorded = next((unit for unit in manifest.differences if unit.kind == kind and unit.index == index), None)
        if recorded is None or recorded.sha256 != impact_record_hash(changes[index]):
            raise ValueError("影响差异与固定清单不同")
        return cls._within_budget(manifest, {"kind": kind, "index": index,
            "difference": changes[index].model_dump(mode="json"),
            "old_source_id": next(source.id for source in manifest.sources if "old" in source.roles),
            "new_source_id": next(source.id for source in manifest.sources if "new" in source.roles)})

    @classmethod
    def validate_text_citation(cls, seed, envelope, manifest, citation):
        source, text, _ = cls._source(seed, envelope, manifest, citation.source_id)
        if (source.text_sha256 != citation.text_sha256 or
            not 0 <= citation.start_char < citation.end_char <= len(text) or
            text[citation.start_char:citation.end_char] != citation.quote):
            raise ValueError("影响原文引用的身份、范围或原文不一致")

    @classmethod
    def validate_case_citation(cls, seed, envelope, manifest, citation):
        bundle = cls._case(seed, envelope, manifest, citation.version_id)
        if citation.version_record_sha256 != impact_record_hash(bundle.version):
            raise ValueError("影响用例引用版本哈希不同")
        value = getattr(bundle.version, citation.root)
        for token in citation.path:
            if isinstance(value, dict) and type(token) is str and token in value:
                value = value[token]
            elif isinstance(value, list) and type(token) is int and 0 <= token < len(value):
                value = value[token]
            else:
                raise ValueError("影响用例引用路径不存在")
        if _json_bytes(value) != _json_bytes(citation.value):
            raise ValueError("影响用例引用值与完整版本不同")
