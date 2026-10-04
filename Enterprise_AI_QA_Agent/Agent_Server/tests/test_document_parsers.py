from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.application.documents.parser_factory import DocumentParserFactory
from src.schemas.document_parsing import DocumentParseOptions, DocumentParseLimits


def actual_docx(path):
    doc = Document()
    doc.add_heading("订单查询", 1)
    doc.add_paragraph("订单详情需要有效订单 ID。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "约束"
    table.cell(1, 0).text = "有效期"
    table.cell(1, 1).text = "7 天"
    doc.add_heading("失败场景", 2)
    doc.add_paragraph("无效订单应返回明确错误。")
    doc.save(path)
    return path.read_bytes()


def actual_pdf(path, *, blank=False, encrypted=False):
    writer = PdfWriter()
    for text in ([] if blank else ["Order retention: 7 days.", "Invalid ID returns an error."]):
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
    if blank:
        writer.add_blank_page(width=600, height=800)
    if encrypted:
        writer.encrypt("controlled-test-value")
    writer.write(path)
    return path.read_bytes()


def parse(path, **options):
    return DocumentParserFactory().parse(filename=path.name, content=path.read_bytes(), options=DocumentParseOptions(**options))


def test_markdown_preserves_raw_text_line_ranges_headings_and_table(tmp_path):
    path = tmp_path / "实际需求.md"
    original = "# 订单查询\r\n条件说明。\r\n\r\n| 字段 | 约束 |\r\n| --- | --- |\r\n| 有效期 | 7 天 |\r\n\r\n```text\r\n# 不是标题\r\n```\r\n\r\n失败场景\r\n========\r\n无效 ID 应失败。\r\n"
    path.write_bytes(original.encode("utf-8-sig"))
    result = parse(path)
    assert result.text == original
    assert result.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert [section.title for section in result.sections] == ["订单查询", "失败场景"]
    assert result.tables[0].rows[1][1].text == "7 天"
    assert result.tables[0].rows[1][1].source.start_line == 6
    assert any(block.kind == "code" and "# 不是标题" in block.text for block in result.blocks)
    assert all(result.text[block.start_char:block.end_char] == block.text for block in result.blocks)


def test_real_docx_keeps_table_between_paragraphs_and_locations(tmp_path):
    path = tmp_path / "需求.docx"
    actual_docx(path)
    result = parse(path)
    assert result.text.index("订单详情") < result.text.index("有效期") < result.text.index("失败场景")
    assert [block.kind for block in result.blocks] == ["heading", "text", "table", "heading", "text"]
    assert result.tables[0].rows[1][1].text == "7 天"
    assert result.tables[0].rows[1][1].source.path == [2, 1, 1]
    assert result.tables[0].rows[1][1].source.page is None
    assert result.sections[1].parent_id == result.sections[0].id
    assert result.dependency_versions["python-docx"] == "1.2.0"


def test_actual_pdf_extracts_ordered_pages_and_reports_structure_limits(tmp_path):
    path = tmp_path / "rules.pdf"
    actual_pdf(path)
    result = parse(path)
    assert "Order retention: 7 days." in result.text and "Invalid ID returns an error." in result.text
    assert [block.source.page for block in result.blocks] == [1, 2]
    assert not result.parser.tables and not result.tables
    assert any(w.code == "pdf_structure_unavailable" for w in result.warnings)
    assert result.dependency_versions["pypdf"] == "6.19.0"
    layout = parse(path, pdf_extraction_mode="layout")
    assert "7 days" in layout.text and layout.options.pdf_extraction_mode == "layout"


def test_no_text_pdf_never_claims_complete_parse(tmp_path):
    path = tmp_path / "no-text.pdf"
    actual_pdf(path, blank=True)
    result = parse(path)
    assert result.status == "unsupported" and not result.text.strip()
    assert any(w.code == "pdf_no_text" and w.source.page == 1 for w in result.warnings)


@pytest.mark.parametrize("header_only", [False, True])
def test_actual_docx_without_body_text_is_not_reported_complete(tmp_path, header_only):
    path = tmp_path / "without-body.docx"
    doc = Document()
    if header_only:
        doc.sections[0].header.paragraphs[0].text = "only in header"
    doc.save(path)
    result = parse(path)
    assert result.status == "unsupported" and not result.text.strip()
    assert any(w.code == "docx_no_body_text" for w in result.warnings)


def test_encrypted_and_damaged_pdf_fail_explicitly(tmp_path):
    path = tmp_path / "encrypted.pdf"
    actual_pdf(path, encrypted=True)
    with pytest.raises(ValueError, match="加密"):
        parse(path)
    path.write_bytes(b"%PDF-1.7\ninvalid")
    with pytest.raises(ValueError, match="PDF"):
        parse(path)


def test_factory_rejects_unavailable_or_mismatched_parser_and_unsafe_encoding(tmp_path):
    path = tmp_path / "requirements.md"
    path.write_bytes(b"# Real requirements")
    with pytest.raises(ValueError, match="未注册|不可用"):
        parse(path, parser_id="docling_http")
    with pytest.raises(ValueError, match="格式"):
        parse(path, parser_id="docx")
    path.write_bytes(b"\xffinvalid")
    with pytest.raises(ValueError, match="编码"):
        parse(path)


@pytest.mark.parametrize("changes", [{"max_bytes": 1}, {"max_text_chars": 1}, {"max_blocks": 1}])
def test_actual_markdown_limits_are_errors_not_truncated_success(tmp_path, changes):
    path = tmp_path / "limits.md"
    path.write_bytes(b"# Heading\nsecond line\n")
    with pytest.raises(ValueError, match="限制"):
        DocumentParserFactory().parse(filename=path.name, content=path.read_bytes(), limits=DocumentParseLimits(**changes))


def test_docx_expansion_and_pdf_page_limits(tmp_path):
    docx = tmp_path / "limits.docx"
    actual_docx(docx)
    with pytest.raises(ValueError, match="限制"):
        DocumentParserFactory().parse(filename=docx.name, content=docx.read_bytes(), limits=DocumentParseLimits(max_expanded_bytes=1))
    pdf = tmp_path / "limits.pdf"
    actual_pdf(pdf)
    with pytest.raises(ValueError, match="限制"):
        DocumentParserFactory().parse(filename=pdf.name, content=pdf.read_bytes(), limits=DocumentParseLimits(max_pages=1))


def test_docx_nested_and_merged_table_content_and_revision_warning(tmp_path):
    path = tmp_path / "complex.docx"
    doc = Document()
    doc.add_paragraph("before table")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "merged header"
    nested = table.cell(1, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "nested actual content"
    revised = OxmlElement("w:ins")
    p = OxmlElement("w:p")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "tracked text"
    r.append(t)
    p.append(r)
    revised.append(p)
    doc.element.body.insert(0, revised)
    doc.save(path)
    result = parse(path)
    assert "nested actual content" in result.text and "merged header" in result.text
    assert result.status == "partial"
    assert {"docx_nested_table", "docx_merged_cells", "docx_unextracted_elements"} <= {w.code for w in result.warnings}


def test_escaped_and_code_pipes_keep_correct_table_cell_boundaries(tmp_path):
    path = tmp_path / "pipe.md"
    path.write_text("| name | value |\n| --- | --- |\n| escaped | left\\|right |\n| code | ``x|y`` |\n| slash | C:\\path |\n", encoding="utf-8")
    result = parse(path)
    assert [row[1].text for row in result.tables[0].rows] == ["value", "left|right", "``x|y``", "C:\\path"]
    assert all(len(row) == 2 for row in result.tables[0].rows)


def test_registration_reserves_http_extension_but_rejects_mismatched_result(tmp_path):
    path = tmp_path / "extension.md"
    path.write_bytes(b"# registered source")
    baseline = parse(path)
    factory = DocumentParserFactory()
    capability = next(item for item in factory.capabilities() if item.parser_id == "docling_http")
    assert not capability.available and capability.transport == "http"
    capability.available = True
    capability.implementation_version = "registered-fixture-v1"
    def implementation(filename, content, options, limits, declared):
        result = baseline.model_copy(deep=True)
        result.options = options
        result.parser = declared
        return result
    factory.register(capability, implementation)
    options = DocumentParseOptions(parser_id="docling_http")
    result = factory.parse(filename=path.name, content=path.read_bytes(), options=options)
    assert result.parser == capability
    bad = result.model_copy(deep=True)
    bad.source_sha256 = "0" * 64
    with pytest.raises(ValueError, match="固定输入"):
        factory.convert_result(bad, filename=path.name, content=path.read_bytes(), options=options,
            capability=capability, limits=DocumentParseLimits())
    bad = result.model_copy(deep=True)
    bad.blocks[0].end_char -= 1
    with pytest.raises(ValueError, match="全文"):
        factory.convert_result(bad, filename=path.name, content=path.read_bytes(), options=options,
            capability=capability, limits=DocumentParseLimits())


def test_evidence_files_are_actual_documents(tmp_path):
    output = os.getenv("QA_DOCUMENT_PARSER_EVIDENCE_DIR")
    if not output:
        pytest.skip("explicit evidence directory not configured")
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    paths = [evidence / "requirements.md", evidence / "requirements.docx", evidence / "requirements.pdf"]
    paths[0].write_bytes("# 订单查询\r\n有效期：7 天。\r\n".encode("utf-8-sig"))
    actual_docx(paths[1])
    actual_pdf(paths[2])
    for path in paths:
        result = parse(path)
        path.with_suffix(path.suffix + ".result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        assert result.source_size_bytes == path.stat().st_size
