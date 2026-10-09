"""Capture ordinary document modules without manufacturing requirement reviews."""
import logging
from collections import Counter
from uuid import uuid4

from src.schemas.case_generation import IncrementalGenerationModule, fixed_generation_snapshot
from src.schemas.case_management import TestCaseGenerateRequest, TestCaseSourceRef
from src.schemas.document_case_generation import DocumentGenerationScope, DocumentGenerationSegment

logger = logging.getLogger(__name__)


def document_segments(result):
    """Disjoint character ranges; full source accompanies each module request."""
    # No upstream counterpart (checked: parser sections, incremental sources,
    # reference ai_case_generator): freeze native disjoint ranges without AI inference.
    if not result.text.strip():
        raise ValueError("解析快照没有可生成用例的文本")
    counts = Counter(section.level for section in result.sections)
    levels = [level for level, count in counts.items() if count > 1]
    sections = sorted((s for s in result.sections if levels and s.level == min(levels)), key=lambda s: s.start_char)
    if not sections:
        sections = [None]
    segments = []
    for index, section in enumerate(sections):
        start = section.start_char if index else 0
        end = sections[index+1].start_char if index+1 < len(sections) else len(result.text)
        segments.append(DocumentGenerationSegment(module_id=str(uuid4()), index=index,
            title=section.title if section else result.filename, section_id=section.id if section else None,
            start_char=start, end_char=end, block_ids=[b.id for b in result.blocks if b.start_char < end and b.end_char > start]))
    return segments


class DocumentCaseGenerationSources:
    def __init__(self, *, parsing, projects, templates, generator):
        self._parsing, self._projects, self._templates, self._generator = parsing, projects, templates, generator

    async def capture(self, project_id, document_id, request, *, transport_options=None):
        await self._projects.require_active(project_id)
        snapshot, result = await self._parsing.read_snapshot(project_id, document_id, request.parse_snapshot_id)
        if result.status != "complete":
            raise ValueError("全量生成需完整解析快照；请先解决解析警告或保留为人工评审依据")
        segments = document_segments(result)
        template = await self._templates.get(project_id)
        authoring = await self._generator.capture_authoring(request.model_key, transport_options=transport_options)
        limitations = ["章节范围覆盖不等于生成用例覆盖充分；草稿需逐版本评审", "环境部署状态尚未验证"]
        limitations.extend(w.message for w in result.warnings)
        modules = []
        for segment in segments:
            reference = TestCaseSourceRef(source_type="document_parse_snapshot", source_id=snapshot.id,
                version=snapshot.seed.version_id, label=segment.title[:240], uri=snapshot.storage_uri,
                metadata={"document_id": document_id, "parse_sha256": snapshot.sha256,
                    "original_sha256": snapshot.seed.original_asset.sha256, "parser": snapshot.parser.model_dump(mode="json"),
                    "segment": segment.model_dump(mode="json")})
            context = {"generation_template": template.model_dump(mode="json"),
                "source_refs": [reference.model_dump(mode="json")],
                "document_generation": {"parse_snapshot": snapshot.model_dump(mode="json"), "text": result.text,
                    "sections": [s.model_dump(mode="json") for s in result.sections],
                    "tables": [t.model_dump(mode="json") for t in result.tables],
                    "segment": segment.model_dump(mode="json"), "basis_limitations": limitations}}
            generation = await self._generator.prepare_frozen(request=TestCaseGenerateRequest(
                objective=request.objective, mode_key=request.mode_key, model_key=authoring.model_key,
                api_doc_ids=[document_id], include_history=False, include_knowledge_graph=False,
                max_cases=request.max_cases_per_module), context=context, authoring=authoring)
            modules.append(fixed_generation_snapshot(IncrementalGenerationModule, id=segment.module_id,
                kind="create", target=None, expected_latest_version=None, generation=generation))
        scope = fixed_generation_snapshot(DocumentGenerationScope, project_id=project_id, document_id=document_id,
            request=request, parse_snapshot=snapshot, text=result.text, segments=segments, template=template,
            authoring=authoring, modules=modules, basis_limitations=limitations)
        logger.info("document_generation_scope_captured", extra={"project_id": project_id, "document_id": document_id,
            "parse_snapshot_id": snapshot.id, "scope_sha256": scope.sha256, "module_count": len(modules), "text_chars": len(result.text)})
        return scope
