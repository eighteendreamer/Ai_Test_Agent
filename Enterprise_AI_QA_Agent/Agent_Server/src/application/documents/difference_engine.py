"""Deterministic comparison of verified fixed parse snapshots.

No upstream counterpart (checked: parsing_service, reference analyze_document_diff,
Python difflib): snapshot/source DTOs are local; matching uses standard difflib.
Run through DocumentDifferenceProcess outside diagnostics and tests.
"""
from __future__ import annotations

from bisect import bisect_right
from difflib import SequenceMatcher

from src.schemas.document_differences import (
    DocumentCharacterDifference, DocumentDifferencePolicy, DocumentDifferenceRange,
    DocumentDifferenceReference, DocumentDifferenceResult, DocumentDifferenceSource,
    DocumentDifferenceView, DocumentDifferenceWarning, DocumentSectionDifference,
    DocumentTableDifference, DocumentTextDifference,
)
from src.schemas.document_parsing import DocumentParseSnapshotView


def snapshot_reference(view: DocumentParseSnapshotView) -> DocumentDifferenceReference:
    snapshot, result = view.snapshot, view.result
    seed = snapshot.seed
    if (result.source_sha256 != seed.original_asset.sha256 or
        result.source_size_bytes != seed.original_asset.size_bytes or
        result.options != seed.options or result.parser != seed.parser or
        result.parser != snapshot.parser or result.dependency_versions != snapshot.dependency_versions or
        result.status != snapshot.status or result.warnings != snapshot.warnings):
        raise ValueError("比较输入与固定解析依据不一致")
    # Storage bytes/hash verification is performed by DocumentParsingService.read_snapshot.
    return DocumentDifferenceReference(project_id=seed.project_id, document_id=seed.document_id,
        version_id=seed.version_id, snapshot_id=snapshot.id, result_sha256=snapshot.sha256,
        original_sha256=result.source_sha256, parser=result.parser.model_copy(deep=True),
        options=result.options.model_copy(deep=True), dependency_versions=dict(result.dependency_versions))


def _lines(text):
    # Same definition as parser_factory._markdown, including Unicode separators.
    # keepends preserves every original character; normalization only replaces CR/LF.
    lines, ends = text.splitlines(keepends=True), [0]
    for line in lines:
        ends.append(ends[-1] + len(line))
    return lines, ends


def _normalize(text):
    # Boundary offsets, rather than guessed positions after replacing CRLF.
    output, boundaries, index = [], [0], 0
    while index < len(text):
        token = text[index]
        index += 2 if text[index:index + 2] == "\r\n" else 1
        output.append("\n" if token == "\r" else token)
        boundaries.append(index)
    return "".join(output), boundaries


class _TextIndex:
    def __init__(self, result):
        self.result = result
        self.lines, self.offsets = _lines(result.text)
        self.block_ends = [block.end_char for block in result.blocks]

    def span(self, start, end):
        sources = []
        if start < end:
            index = bisect_right(self.block_ends, start)
            for position in range(index, len(self.result.blocks)):
                block = self.result.blocks[position]
                if block.start_char >= end:
                    break
                sources.append(DocumentDifferenceSource(block_id=block.id, source=block.source.model_copy(deep=True)))
        return DocumentDifferenceRange(start_char=start, end_char=end, text=self.result.text[start:end],
            start_line=bisect_right(self.offsets, start) if start < end else None,
            end_line=bisect_right(self.offsets, end - 1) if start < end else None, sources=sources)


class _Budget:
    def __init__(self, maximum):
        self.maximum, self.used = maximum, 0

    def take(self):
        self.used += 1
        if self.used > self.maximum:
            raise ValueError("文档差异数量超过限制，未保存截断结果")


def _changed(old, new):
    # Frequent repeated requirements must remain eligible matching anchors.
    # Trim only exactly equal boundaries; no heuristic discards business content.
    prefix, old_end, new_end = 0, len(old), len(new)
    while prefix < min(old_end, new_end) and old[prefix] == new[prefix]:
        prefix += 1
    while old_end > prefix and new_end > prefix and old[old_end - 1] == new[new_end - 1]:
        old_end -= 1
        new_end -= 1
    if prefix == old_end == new_end:
        return
    for operation, a, b, c, d in SequenceMatcher(None, old[prefix:old_end], new[prefix:new_end], autojunk=False).get_opcodes():
        if operation != "equal":
            yield operation, a + prefix, b + prefix, c + prefix, d + prefix


def _text_view(old, new, kind, policy, budget):
    old_tokens, new_tokens = old.lines, new.lines
    normalized = kind == "normalized"
    if normalized:
        old_tokens = [line.replace("\r\n", "\n").replace("\r", "\n") for line in old_tokens]
        new_tokens = [line.replace("\r\n", "\n").replace("\r", "\n") for line in new_tokens]
    changes = []
    for operation, a, b, c, d in _changed(old_tokens, new_tokens):
        budget.take()
        left = old.span(old.offsets[a], old.offsets[b])
        right = new.span(new.offsets[c], new.offsets[d])
        change = DocumentTextDifference(operation=operation, old=left, new=right)
        changes.append(change)
        left_text, right_text = left.text, right.text
        left_map, right_map = None, None
        if normalized:
            left_text, left_map = _normalize(left_text)
            right_text, right_map = _normalize(right_text)
        if (len(left_text) + len(right_text) > policy.max_character_detail_chars or
            len(left_text) * len(right_text) > policy.max_character_pair_product):
            change.character_status = "omitted"
            continue
        for tag, i, j, k, l in _changed(left_text, right_text):
            budget.take()
            change.characters.append(DocumentCharacterDifference(operation=tag,
                old=old.span(left.start_char + (left_map[i] if left_map is not None else i),
                    left.start_char + (left_map[j] if left_map is not None else j)),
                new=new.span(right.start_char + (right_map[k] if right_map is not None else k),
                    right.start_char + (right_map[l] if right_map is not None else l))))
    return DocumentDifferenceView(kind=kind, normalization="line-endings-v1" if normalized else "none", changes=changes)


def _section_keys(sections):
    by_id = {section.id: section for section in sections}
    keys = []
    for section in sections:
        ancestors, seen, parent_id = [], {section.id}, section.parent_id
        while parent_id:
            if parent_id in seen:
                raise ValueError("文档章节父引用存在循环")
            seen.add(parent_id)
            parent = by_id[parent_id]
            ancestors.append((parent.level, parent.title))
            parent_id = parent.parent_id
        keys.append((tuple(reversed(ancestors)), section.level, section.title))
    return keys


def compare_snapshots(*, old: DocumentParseSnapshotView, new: DocumentParseSnapshotView,
                      policy: DocumentDifferencePolicy | None = None) -> DocumentDifferenceResult:
    policy = policy or DocumentDifferencePolicy()
    left_ref, right_ref = snapshot_reference(old), snapshot_reference(new)
    if left_ref.project_id != right_ref.project_id or left_ref.document_id != right_ref.document_id:
        raise ValueError("比较快照必须属于同一项目和文档")
    for view in (old, new):
        result = view.result
        if result.status == "unsupported":
            raise ValueError("不支持解析的快照不能产生无差异成功结果")
        if len(result.text) > policy.max_text_chars:
            raise ValueError("比较文本超过字符数量限制")
        count = len(result.blocks) + len(result.sections) + len(result.tables)
        count += sum(len(row) for table in result.tables for row in table.rows)
        if count > policy.max_structure_items:
            raise ValueError("比较结构超过数量限制")
    left, right = _TextIndex(old.result), _TextIndex(new.result)
    if max(len(left.lines), len(right.lines)) > policy.max_lines:
        raise ValueError("比较文本超过行数量限制")
    basis_changed = (left_ref.parser != right_ref.parser or left_ref.options != right_ref.options or
        left_ref.dependency_versions != right_ref.dependency_versions)
    budget = _Budget(policy.max_changes)
    result = DocumentDifferenceResult(old=left_ref, new=right_ref, policy=policy.model_copy(deep=True),
        originals_equal=left_ref.original_sha256 == right_ref.original_sha256, parsing_basis_changed=basis_changed,
        raw=_text_view(left, right, "raw", policy, budget),
        normalized=_text_view(left, right, "normalized", policy, budget))
    # IDs are snapshot-local; match structure/content, never ordinal block/section IDs.
    for tag, a, b, c, d in _changed(_section_keys(old.result.sections), _section_keys(new.result.sections)):
        budget.take()
        before, after = old.result.sections[a:b], new.result.sections[c:d]
        result.sections.append(DocumentSectionDifference(operation=tag, old=before, new=after,
            old_ranges=[left.span(item.start_char, item.end_char) for item in before],
            new_ranges=[right.span(item.start_char, item.end_char) for item in after]))
    table_key = lambda table: tuple(tuple(cell.text for cell in row) for row in table.rows)
    old_blocks = {block.id: block for block in old.result.blocks}
    new_blocks = {block.id: block for block in new.result.blocks}
    for tag, a, b, c, d in _changed([table_key(t) for t in old.result.tables], [table_key(t) for t in new.result.tables]):
        budget.take()
        before, after = old.result.tables[a:b], new.result.tables[c:d]
        result.tables.append(DocumentTableDifference(operation=tag, old=before, new=after,
            old_ranges=[left.span(old_blocks[t.block_id].start_char, old_blocks[t.block_id].end_char) for t in before],
            new_ranges=[right.span(new_blocks[t.block_id].start_char, new_blocks[t.block_id].end_char) for t in after]))
    result.warnings.append(DocumentDifferenceWarning(code="extracted_text_positions",
        message="字符位置属于解析抽取文本；源行、DOCX路径及PDF页码沿用解析器，不表示原件字符坐标或完整格式差异"))
    for side, view in (("old", old), ("new", new)):
        result.warnings.extend(DocumentDifferenceWarning(code=w.code, message=w.message, side=side, source=w.source)
            for w in view.result.warnings)
        if view.result.status == "partial":
            result.warnings.append(DocumentDifferenceWarning(code="partial_parse", side=side,
                message="此侧快照为部分解析，未提取内容不能由比较证明未变化"))
    if basis_changed:
        result.warnings.append(DocumentDifferenceWarning(code="parsing_basis_changed",
            message="两侧解析器、选项或依赖不同；抽取差异不能直接认定为需求变化"))
    if any(change.character_status == "omitted" for view in (result.raw, result.normalized) for change in view.changes):
        result.warnings.append(DocumentDifferenceWarning(code="character_detail_omitted",
            message="部分变更块超过字符细化预算；完整行差异保留，未提供字符细化"))
    # Copy nested parse structures as well: callers cannot mutate the fixed input through the result.
    return result.model_copy(deep=True)
