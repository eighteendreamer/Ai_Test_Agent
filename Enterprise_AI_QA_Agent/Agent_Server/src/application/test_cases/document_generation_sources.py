"""Capture ordinary document modules without manufacturing requirement reviews."""
import hashlib
import json
import logging
from collections import Counter
from uuid import uuid4

from src.schemas.case_generation import IncrementalGenerationModule, fixed_generation_snapshot
from src.schemas.case_management import TestCaseGenerateRequest, TestCaseSourceRef
from src.schemas.document_case_generation import DocumentGenerationScope, DocumentGenerationSegment
from src.schemas.generation_coverage import DocumentGenerationCoverageCandidate

logger = logging.getLogger(__name__)

_MAX_RULE_CANDIDATES_PER_MODULE = 1000


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


def _statement_spans(text):
    """Split parser text into source-exact review candidates, not approved rules."""
    start = 0
    for index, character in enumerate(text):
        boundary = character in "。！？!?；;\r\n"
        if character == "." and (index + 1 == len(text) or text[index + 1].isspace()):
            boundary = index == 0 or not text[index - 1].isdigit()
        if not boundary:
            continue
        end = index + 1
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            yield left, right
        start = end
    left, right = start, len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if right > left:
        yield left, right


def document_rule_candidates(result, segment, parse_snapshot_id, *, limit=_MAX_RULE_CANDIDATES_PER_MODULE):
    """Freeze source-exact statement candidates and flag an inventory cap explicitly.

    # No upstream counterpart (checked: DocumentParserFactory, DocumentImpactWorkflow,
    # RequirementVersion.points, and the local generation/governance course): ordinary
    # first-pass document generation has no accepted requirement-point inventory. This
    # creates provisional parser-grounded candidates for human review; it does not infer
    # approved requirements or claim semantic completeness.
    """
    sections = {section.id: section for section in result.sections}
    selected_section = sections.get(segment.section_id) if segment.section_id else None
    if segment.section_id and selected_section is None:
        raise ValueError("生成模块引用了不存在的解析章节")

    ancestors = set()
    cursor = selected_section
    while cursor is not None:
        ancestors.add(cursor.id)
        cursor = sections.get(cursor.parent_id) if cursor.parent_id else None

    descendants = set()
    if selected_section is not None:
        for section in result.sections:
            cursor = section
            while cursor is not None:
                if cursor.id == selected_section.id:
                    descendants.add(section.id)
                    break
                cursor = sections.get(cursor.parent_id) if cursor.parent_id else None

    def owner_for(position):
        owners = [section for section in result.sections
            if section.start_char <= position < section.end_char]
        return max(owners, key=lambda item: (item.level, item.start_char), default=None)

    candidates = []
    for block in result.blocks:
        if block.kind not in {"text", "table", "code"}:
            continue
        for local_start, local_end in _statement_spans(block.text):
            start_char = block.start_char + local_start
            end_char = block.start_char + local_end
            owner = owner_for(start_char)
            is_local = segment.start_char <= start_char < segment.end_char
            is_unscoped = owner is None
            is_inherited = owner is not None and owner.id in ancestors and owner.id != segment.section_id
            is_descendant = owner is not None and owner.id in descendants
            if selected_section is not None and not (is_local or is_unscoped or is_inherited or is_descendant):
                continue
            if selected_section is None and not is_local:
                continue
            statement = block.text[local_start:local_end]
            identity = "\0".join((parse_snapshot_id, block.id, str(start_char), str(end_char), statement))
            candidates.append(DocumentGenerationCoverageCandidate(
                id=hashlib.sha256(identity.encode("utf-8")).hexdigest(), statement=statement,
                source_block_id=block.id, start_char=start_char, end_char=end_char,
                source=block.source, scope="unscoped" if is_unscoped else "inherited" if is_inherited else "local",
                section_id=owner.id if owner else None))

    candidates.sort(key=lambda item: (item.start_char, item.end_char, item.id))
    complete = len(candidates) <= limit
    return candidates[:limit], complete


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
            rule_candidates, inventory_complete = document_rule_candidates(result, segment, snapshot.id)
            candidate_warnings = [] if inventory_complete else [
                f"模块规则候选超过{_MAX_RULE_CANDIDATES_PER_MODULE}条上限，清单已截断；覆盖核对不完整"
            ]
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
                    "segment": segment.model_dump(mode="json"), "basis_limitations": limitations,
                    "coverage_targets": [item.model_dump(mode="json") for item in rule_candidates],
                    "coverage_inventory_complete": inventory_complete,
                    "coverage_inventory_warnings": candidate_warnings}}
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
            "parse_snapshot_id": snapshot.id, "scope_sha256": scope.sha256, "module_count": len(modules),
            "text_chars": len(result.text), "coverage_candidate_counts": [
                len(json.loads(module.generation.invocation.messages[0]["content"])["evidence_context"]["document_generation"]["coverage_targets"])
                for module in modules]})
        return scope
