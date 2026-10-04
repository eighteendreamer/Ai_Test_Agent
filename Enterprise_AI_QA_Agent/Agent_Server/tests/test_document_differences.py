from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.application.documents.difference_engine import compare_snapshots
from src.application.documents.difference_process import DocumentDifferenceProcess
from src.application.documents.parser_factory import DocumentParserFactory
from src.application.documents.parser_process import DocumentParseProcess
from src.runtime import limited_parser_process
from src.runtime.limited_parser_process import ParserProcessError
from src.schemas.api_docs import ApiDocumentAsset
from src.schemas.document_differences import DocumentDifferencePolicy, DocumentDifferenceResult
from src.schemas.document_parsing import (
    DocumentParseSeed, DocumentParseSnapshot, DocumentParseSnapshotView,
    DocumentParseWarning, DocumentParsingSettings, DocumentParsingSettingsRecord,
)


PROJECT, DOCUMENT = str(uuid4()), str(uuid4())


def fixed_view(result, *, version=None):
    now = datetime.now(timezone.utc)
    original = ApiDocumentAsset(id=str(uuid4()), kind="original", filename=result.filename,
        content_type="application/octet-stream", size_bytes=result.source_size_bytes, sha256=result.source_sha256,
        storage_uri="fixture://original", created_at=now, reason="actual file comparison fixture")
    seed = DocumentParseSeed(project_id=PROJECT, document_id=DOCUMENT, version_id=version or str(uuid4()),
        original_asset=original, config=DocumentParsingSettingsRecord(scope_key="project:" + PROJECT, revision=1,
            payload=DocumentParsingSettings()), options=result.options, parser=result.parser)
    encoded = result.model_dump_json().encode("utf-8")
    snapshot = DocumentParseSnapshot(id=str(uuid4()), task_id=str(uuid4()), seed=seed,
        storage_uri="fixture://result", sha256=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded),
        status=result.status, parser=result.parser, dependency_versions=result.dependency_versions,
        warnings=result.warnings, created_at=now)
    return DocumentParseSnapshotView(snapshot=snapshot, result=result)


def markdown(text, *, version=None):
    return fixed_view(DocumentParserFactory().parse(filename="requirements.md", content=text.encode("utf-8-sig")), version=version)


def verify_spans(diff, old, new):
    for view in (diff.raw, diff.normalized):
        normalize = (lambda value: value.replace("\r\n", "\n").replace("\r", "\n")) if view.kind == "normalized" else (lambda value: value)
        rebuilt, cursor = [], 0
        for change in view.changes:
            assert change.old.start_char >= cursor
            rebuilt.extend((normalize(old.result.text[cursor:change.old.start_char]), normalize(change.new.text)))
            cursor = change.old.end_char
            if change.character_status == "complete":
                fragments, position = [], change.old.start_char
                for detail in change.characters:
                    assert change.old.start_char <= position <= detail.old.start_char <= detail.old.end_char <= change.old.end_char
                    assert change.new.start_char <= detail.new.start_char <= detail.new.end_char <= change.new.end_char
                    fragments.extend((normalize(old.result.text[position:detail.old.start_char]), normalize(detail.new.text)))
                    position = detail.old.end_char
                fragments.append(normalize(old.result.text[position:change.old.end_char]))
                assert "".join(fragments) == normalize(change.new.text)
            for item in [change, *change.characters]:
                for side, source in ((item.old, old.result), (item.new, new.result)):
                    assert side.text == source.text[side.start_char:side.end_char]
                    if side.start_char == side.end_char:
                        assert not side.sources and side.start_line is None and side.end_line is None
                    for evidence in side.sources:
                        block = next(b for b in source.blocks if b.id == evidence.block_id)
                        assert block.source == evidence.source
                        assert block.start_char < side.end_char and block.end_char > side.start_char
        rebuilt.append(normalize(old.result.text[cursor:]))
        assert "".join(rebuilt) == normalize(new.result.text), "完整差异必须能还原新文本，不能只证明返回了JSON"


def write_pair(tmp_path, extension):
    paths = [tmp_path / f"requirements-v{number}.{extension}" for number in (1, 2)]
    for path, days in zip(paths, (7, 9)):
        if extension == "md":
            path.write_bytes(f"# 订单查询\r\n有效期：{days} 天。  \r\n".encode("utf-8-sig"))
        elif extension == "docx":
            doc = Document()
            doc.add_heading("订单查询", 1)
            doc.add_paragraph("订单详情需要有效订单 ID。")
            table = doc.add_table(rows=2, cols=2)
            table.cell(0, 0).text, table.cell(0, 1).text = "字段", "约束"
            table.cell(1, 0).text, table.cell(1, 1).text = "有效期", f"{days} 天"
            doc.save(path)
        else:
            writer = PdfWriter()
            for text in (f"Order retention: {days} days.", "Invalid ID returns an error."):
                page = writer.add_blank_page(width=600, height=800)
                font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica")})
                page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
                stream = DecodedStreamObject()
                stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
                page[NameObject("/Contents")] = stream
            writer.write(path)
    return paths


def test_normalized_crlf_maps_back_to_original_without_removing_whitespace():
    old = markdown("# 需求\r\n保留  7 天。\r\n    code\r\n")
    new = markdown("# 需求\n保留  9 天。\n    code\n")
    result = compare_snapshots(old=old, new=new)
    assert len(result.normalized.changes) == 1
    assert [(c.old.text, c.new.text) for c in result.normalized.changes[0].characters] == [("7", "9")]
    verify_spans(result, old, new)
    endings = compare_snapshots(old=old, new=markdown(old.result.text.replace("\r\n", "\n")))
    assert endings.raw.changes and not endings.normalized.changes and not endings.originals_equal
    whitespace = compare_snapshots(old=old, new=markdown(old.result.text.replace("    code", "  code")))
    assert whitespace.normalized.changes, "缩进不能被归一规则抹去"
    unicode_change = compare_snapshots(old=markdown("value\u2028content\n"), new=markdown("value\u2028changed\n")).raw.changes[0]
    assert unicode_change.old.start_line == unicode_change.old.end_line == 2
    assert unicode_change.old.sources[0].source.start_line == 2


@pytest.mark.parametrize("left,right,operation", [
    ("", "new\n", "insert"), ("old\n", "", "delete"), ("x", "x\n", "replace"),
    ("# A\nvalue\n# B\nother\n", "# B\nother\n# A\nvalue\n", None),
])
def test_empty_side_eof_and_section_move_preserve_exact_text(left, right, operation):
    # Complete empty extracted text is valid; an unsupported scan is handled separately.
    old, new = markdown(left or "\n"), markdown(right or "\n")
    if not left:
        old.result.text, old.result.blocks = "", []
        old = fixed_view(old.result)
    if not right:
        new.result.text, new.result.blocks = "", []
        new = fixed_view(new.result)
    result = compare_snapshots(old=old, new=new)
    assert result.raw.changes
    if operation:
        assert result.raw.changes[0].operation == operation
    else:
        assert result.sections
    verify_spans(result, old, new)


def test_repeated_lines_over_200_are_not_discarded_as_matching_anchors():
    old = markdown("# 需求\n" + "重复业务条件\n" * 1000 + "保留 7 天\n" + "重复业务条件\n" * 1000)
    new = markdown(old.result.text.replace("7 天", "9 天"))
    diff = compare_snapshots(old=old, new=new)
    assert len(diff.raw.changes) == 1
    assert diff.raw.changes[0].old.text == "保留 7 天\n"
    assert diff.raw.changes[0].old.start_line == 1002
    assert diff.raw.changes[0].old.start_char > 6000, "不能沿用参考实现的6000字前缀截断"
    assert diff.raw.changes[0].characters[0].old.text == "7"


@pytest.mark.asyncio
async def test_large_identical_repeated_content_compares_in_actual_process():
    old = markdown("# 需求\n" + "重复业务条件\n" * 20_000)
    new = fixed_view(old.result.model_copy(deep=True))
    diff = await DocumentDifferenceProcess().compare(old=old, new=new)
    assert diff.originals_equal and not diff.raw.changes and not diff.normalized.changes


@pytest.mark.asyncio
async def test_actual_no_text_pdf_cannot_be_reported_as_no_change_success(tmp_path):
    path = tmp_path / "no-text.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(path)
    parsed = await DocumentParseProcess().parse(filename=path.name, content=path.read_bytes())
    assert parsed.status == "unsupported"
    with pytest.raises(ParserProcessError, match="不支持解析") as caught:
        await DocumentDifferenceProcess().compare(old=markdown("# 需求\n7天\n"), new=fixed_view(parsed))
    assert caught.value.exit_code == 2


def test_structure_ordinal_ids_do_not_become_cross_version_identity():
    old = markdown("# Existing\ntext\n## Child\nvalue\n")
    new = markdown("# Added\nnew\n# Existing\ntext\n## Child\nvalue\n")
    diff = compare_snapshots(old=old, new=new)
    assert len(diff.sections) == 1
    assert diff.sections[0].operation == "insert"
    assert [section.title for section in diff.sections[0].new] == ["Added"]
    assert old.result.sections[0].id == new.result.sections[0].id  # same ordinal, different content


def test_fixed_inputs_are_immutable_and_parser_change_is_visible():
    old = markdown("# Requirement\n7 days\n")
    newer_result = old.result.model_copy(deep=True)
    newer_result.options.pdf_extraction_mode = "layout"
    newer_result.dependency_versions["comparison-fixture"] = "v2"
    new = fixed_view(newer_result, version=old.snapshot.seed.version_id)
    before = (old.model_dump_json(), new.model_dump_json())
    result = compare_snapshots(old=old, new=new)
    assert result.parsing_basis_changed and result.originals_equal and not result.raw.changes
    assert any(w.code == "parsing_basis_changed" for w in result.warnings)
    result.old.options.encoding = "utf-16"
    assert before == (old.model_dump_json(), new.model_dump_json())


def test_partial_unsupported_project_document_and_seed_boundaries():
    old = markdown("# Requirement\n7 days\n")
    partial = old.result.model_copy(deep=True)
    partial.status = "partial"
    partial.warnings.append(DocumentParseWarning(code="actual_missing_content", message="部分提取缺失"))
    result = compare_snapshots(old=old, new=fixed_view(partial))
    assert {(w.code, w.side) for w in result.warnings} >= {("partial_parse", "new"), ("actual_missing_content", "new")}
    unsupported = partial.model_copy(deep=True)
    unsupported.status = "unsupported"
    with pytest.raises(ValueError, match="不支持解析"):
        compare_snapshots(old=old, new=fixed_view(unsupported))
    for field in ("project_id", "document_id"):
        wrong = old.model_copy(deep=True)
        setattr(wrong.snapshot.seed, field, str(uuid4()))
        with pytest.raises(ValueError, match="同一项目和文档"):
            compare_snapshots(old=old, new=wrong)
    wrong = old.model_copy(deep=True)
    wrong.snapshot.seed.original_asset.sha256 = "0" * 64
    with pytest.raises(ValueError, match="固定解析依据"):
        compare_snapshots(old=old, new=wrong)


def test_expensive_character_details_are_explicitly_omitted_without_losing_lines():
    old, new = markdown("# Requirement\n" + "a" * 500 + "\n"), markdown("# Requirement\n" + "b" * 500 + "\n")
    result = compare_snapshots(old=old, new=new, policy=DocumentDifferencePolicy(max_character_pair_product=100))
    assert result.raw.changes[0].old.text == "a" * 500 + "\n"
    assert result.raw.changes[0].new.text == "b" * 500 + "\n"
    assert result.raw.changes[0].character_status == "omitted" and not result.raw.changes[0].characters
    assert result.policy.max_character_pair_product == 100
    assert any(w.code == "character_detail_omitted" for w in result.warnings)


@pytest.mark.parametrize("changes,reason", [
    ({"max_text_chars": 1}, "字符数量"), ({"max_lines": 1}, "行数量"),
    ({"max_structure_items": 1}, "结构"), ({"max_changes": 1}, "差异数量"),
])
def test_limit_rejection_does_not_return_prefix_success(changes, reason):
    with pytest.raises(ValueError, match=reason):
        compare_snapshots(old=markdown("# 需求\n7天\n"), new=markdown("# 需求\n9天\n"), policy=DocumentDifferencePolicy(**changes))


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_originals_parse_and_compare_in_real_processes(tmp_path, extension):
    paths = write_pair(tmp_path, extension)
    views = []
    for path in paths:
        views.append(fixed_view(await DocumentParseProcess().parse(filename=path.name, content=path.read_bytes())))
    task = asyncio.create_task(DocumentDifferenceProcess().compare(old=views[0], new=views[1]))
    ticks = 0
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.002)
    diff = await task
    evidence = os.getenv("QA_DOCUMENT_DIFFERENCE_EVIDENCE_DIR")
    if evidence:
        directory = Path(evidence) / extension
        directory.mkdir(parents=True, exist_ok=True)
        for path in paths:
            (directory / path.name).write_bytes(path.read_bytes())
        for number, view in enumerate(views, 1):
            (directory / f"snapshot-v{number}.json").write_text(view.model_dump_json(indent=2), encoding="utf-8")
        (directory / "difference.json").write_text(diff.model_dump_json(indent=2), encoding="utf-8")
    assert ticks > 1 and len(diff.raw.changes) == 1 and len(diff.normalized.changes) == 1
    change = diff.raw.changes[0]
    assert [(c.old.text, c.new.text) for c in change.characters] == [("7", "9")]
    verify_spans(diff, *views)
    assert change.old.sources[0].source.kind == {"md": "line", "docx": "docx", "pdf": "page"}[extension]
    if extension == "docx":
        assert diff.tables[0].old[0].rows[1][1].text == "7 天"
        assert diff.tables[0].new[0].rows[1][1].text == "9 天"
        # parser_factory.table_rows uses [body index, row index, column index].
        assert diff.tables[0].old[0].rows[1][1].source.path == [2, 1, 1]
    elif extension == "pdf":
        assert change.old.sources[0].source.page == 1
        assert len(views[0].result.blocks) == 2
    assert DocumentDifferenceResult.model_validate_json(diff.model_dump_json()) == diff


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"timeout_seconds": 0.000001}, "时间限制"), ({"max_memory_bytes": 1}, "内存限制"),
    ({"max_cpu_seconds": 0.000001}, "CPU 限制"), ({"max_output_bytes": 1}, "大小限制"),
])
async def test_real_comparison_resource_rejection_reaps_process_and_temp_files(monkeypatch, changes, reason):
    original, observed = subprocess.Popen, []
    def start(command, **kwargs):
        process = original(command, **kwargs)
        observed.append((process, psutil.Process(process.pid), Path(command[-2]).parent))
        return process
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", start)
    with pytest.raises(ParserProcessError, match=reason) as caught:
        await DocumentDifferenceProcess().compare(old=markdown("7 days\n"), new=markdown("9 days\n"), policy=DocumentDifferencePolicy(**changes))
    assert caught.value.exit_code is not None
    process, handle, directory = observed[0]
    assert process.poll() is not None and not handle.is_running() and not directory.exists()


@pytest.mark.asyncio
async def test_actual_comparison_cancellation_and_input_rejection(monkeypatch):
    original, observed = subprocess.Popen, []
    def start(command, **kwargs):
        process = original(command, **kwargs)
        observed.append((process, psutil.Process(process.pid), Path(command[-2]).parent))
        return process
    monkeypatch.setattr(limited_parser_process.subprocess, "Popen", start)
    old, new = markdown("7 days\n"), markdown("9 days\n")
    with pytest.raises(ValueError, match="输入超过大小"):
        await DocumentDifferenceProcess().compare(old=old, new=new, policy=DocumentDifferencePolicy(max_input_bytes=1))
    assert not observed
    task = asyncio.create_task(DocumentDifferenceProcess().compare(old=old, new=new))
    async with asyncio.timeout(5):
        while not observed:
            await asyncio.sleep(0.002)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process, handle, directory = observed[0]
    assert process.poll() is not None and not handle.is_running() and not directory.exists()
