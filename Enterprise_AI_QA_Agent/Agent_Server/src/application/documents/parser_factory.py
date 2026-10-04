from __future__ import annotations

import hashlib
import io
import re
import zipfile
from importlib.metadata import version
from pathlib import Path
from typing import Callable

from src.schemas.document_parsing import (
    DocumentParseLimits, DocumentParseOptions, DocumentParseResult, DocumentParserCapability,
    DocumentParseWarning, DocumentSection, DocumentSourceLocation, DocumentTable,
    DocumentTableCell, DocumentTextBlock,
)

Parser = Callable[[str, bytes, DocumentParseOptions, DocumentParseLimits, DocumentParserCapability], DocumentParseResult]
VERSION = "lightweight-v1"


class DocumentParserFactory:
    """Pure conversion; call through DocumentParseProcess outside diagnostics/tests."""

    def __init__(self):
        self._entries: dict[str, tuple[DocumentParserCapability, Parser | None]] = {}
        self.register(DocumentParserCapability(parser_id="markdown", formats=[".md", ".txt"],
            implementation_version=VERSION, sections=True, tables=True, source_positions=["line"]), _markdown)
        self.register(DocumentParserCapability(parser_id="docx", formats=[".docx"],
            implementation_version=VERSION, sections=True, tables=True, source_positions=["docx_body_path"]), _docx)
        self.register(DocumentParserCapability(parser_id="pdf", formats=[".pdf"],
            implementation_version=VERSION, source_positions=["page"]), _pdf)
        self.register(DocumentParserCapability(parser_id="docling_http", formats=[".md", ".docx", ".pdf"],
            implementation_version="unregistered", sections=True, tables=True, source_positions=["provider"],
            transport="http", available=False), None)

    def register(self, capability: DocumentParserCapability, implementation: Parser | None):
        if capability.available != (implementation is not None):
            raise ValueError("解析器能力与实现可用性不一致")
        if capability.parser_id in self._entries and self._entries[capability.parser_id][0].available:
            raise ValueError("已注册解析器不可覆盖，请使用新解析器身份")
        self._entries[capability.parser_id] = (capability.model_copy(deep=True), implementation)

    def capabilities(self) -> list[DocumentParserCapability]:
        return [entry[0].model_copy(deep=True) for entry in self._entries.values()]

    def parse(self, *, filename: str, content: bytes, options: DocumentParseOptions | None = None,
              limits: DocumentParseLimits | None = None) -> DocumentParseResult:
        options = options or DocumentParseOptions()
        limits = limits or DocumentParseLimits()
        if not content or len(content) > limits.max_bytes:
            raise ValueError("文档为空或超过文件大小限制")
        filename = Path(filename.replace("\\", "/")).name
        extension = Path(filename).suffix.lower()
        parser_id = options.parser_id
        if parser_id == "auto":
            parser_id = {".md": "markdown", ".txt": "markdown", ".docx": "docx", ".pdf": "pdf"}.get(extension, "")
        entry = self._entries.get(parser_id)
        if entry is None or entry[1] is None:
            raise ValueError("文档解析器未注册或不可用")
        capability, implementation = entry
        if extension not in capability.formats:
            raise ValueError("解析器不支持此文档格式")
        result = implementation(filename, content, options, limits, capability.model_copy(deep=True))
        return self.convert_result(result, filename=filename, content=content, options=options, capability=capability, limits=limits)

    @staticmethod
    def convert_result(payload, *, filename, content, options, capability, limits) -> DocumentParseResult:
        """Shared result adapter for future registered HTTP implementations."""
        result = DocumentParseResult.model_validate(payload.model_dump() if isinstance(payload, DocumentParseResult) else payload)
        if result.filename != filename or result.source_sha256 != hashlib.sha256(content).hexdigest() or \
            result.source_size_bytes != len(content) or result.options != options or result.parser != capability:
            raise ValueError("解析结果与固定输入或解析器身份不一致")
        if len(result.text) > limits.max_text_chars or len(result.blocks) > limits.max_blocks:
            raise ValueError("文档文本或块数量超过解析限制")
        if sum(len(row) for table in result.tables for row in table.rows) > limits.max_table_cells:
            raise ValueError("文档表格单元格数量超过解析限制")
        return result


class _ResultBuilder:
    def __init__(self, filename, content, options, limits, capability):
        self.limits = limits
        self.result = DocumentParseResult(filename=filename, source_sha256=hashlib.sha256(content).hexdigest(),
            source_size_bytes=len(content), parser=capability, options=options, text="")
        self.headings: list[tuple[DocumentTextBlock, str]] = []
        self.chunks: list[str] = []
        self.length = 0

    def block(self, text, source, kind="text", level=None, title=None, start=None, end=None):
        if len(self.result.blocks) >= self.limits.max_blocks:
            raise ValueError("文档块数量超过解析限制")
        if start is None:
            start = self.length
            self.chunks.append(text)
            end = start + len(text)
            self.chunks.append("\n")
            self.length = end + 1
        if end > self.limits.max_text_chars:
            raise ValueError("文档文本超过解析限制")
        block = DocumentTextBlock(id=f"block-{len(self.result.blocks) + 1}", kind=kind, text=text,
            source=source, start_char=start, end_char=end, heading_level=level)
        self.result.blocks.append(block)
        if level:
            self.headings.append((block, title if title is not None else text.strip()))
        return block

    def warn(self, code, message, source=None):
        self.result.warnings.append(DocumentParseWarning(code=code, message=message, source=source))

    def finish(self, text=None):
        self.result.text = "".join(self.chunks) if text is None else text
        stack = []
        for block, title in self.headings:
            while stack and stack[-1].level >= block.heading_level:
                stack.pop().end_char = block.start_char
            section = DocumentSection(id=f"section-{len(self.result.sections) + 1}", heading_block_id=block.id,
                title=title, level=block.heading_level, parent_id=stack[-1].id if stack else None,
                start_char=block.start_char, end_char=len(self.result.text))
            self.result.sections.append(section)
            stack.append(section)
        return self.result


def _line_source(start, end=None):
    return DocumentSourceLocation(kind="line", start_line=start, end_line=end or start)


def _table_values(line):
    # No upstream counterpart (checked: reference document_parser/import_parser):
    # preserve source lines; recognize a small pipe-table subset, not rendered HTML.
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and len(re.search(r"\\*$", line[:-1]).group()) % 2 == 0:
        line = line[:-1]
    values, current, ticks, index = [], [], 0, 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line) and line[index + 1] in {"|", "\\", "`"}:
            current.append(line[index + 1])
            index += 2
            continue
        if char == "`":
            end = index + 1
            while end < len(line) and line[end] == "`":
                end += 1
            count = end - index
            if not ticks:
                ticks = count
            elif ticks == count:
                ticks = 0
            current.append(line[index:end])
            index = end
            continue
        if char == "|" and not ticks:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    values.append("".join(current).strip())
    return values


def _markdown(filename, content, options, limits, capability):
    try:
        text = content.decode(options.encoding)
    except UnicodeError as exc:
        raise ValueError("文档无法按指定编码读取，请明确选择编码") from exc
    if len(text) > limits.max_text_chars:
        raise ValueError("文档文本超过解析限制")
    builder = _ResultBuilder(filename, content, options, limits, capability)
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    index, cells = 0, 0
    while index < len(lines):
        start = index
        line = lines[index].rstrip("\r\n")
        if not line.strip():
            index += 1
            continue
        kind, level, title = "text", None, None
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        heading = re.match(r"^ {0,3}(#{1,6})(?:\s+|$)(.*)", line)
        rows = None
        if fence:
            kind = "code"
            marker = fence.group(1)
            index += 1
            while index < len(lines):
                closing = re.fullmatch(r" {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", lines[index].rstrip("\r\n"))
                index += 1
                if closing:
                    break
        elif heading:
            kind, level = "heading", len(heading.group(1))
            title = re.sub(r"\s+#+\s*$", "", heading.group(2)).strip()
            index += 1
        elif index + 1 < len(lines) and re.fullmatch(r" {0,3}(=+|-+)\s*", lines[index + 1].rstrip("\r\n")):
            kind, level, title = "heading", 1 if "=" in lines[index + 1] else 2, line.strip()
            index += 2
        elif index + 1 < len(lines) and "|" in line and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in _table_values(lines[index + 1])):
            kind, rows = "table", []
            header = _table_values(line)
            rows.append([DocumentTableCell(text=value, source=_line_source(index + 1)) for value in header])
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                row = _table_values(lines[index])
                if len(row) != len(header):
                    builder.warn("markdown_table_shape", "表格行列数不一致，原始行完整保留。", _line_source(index + 1))
                rows.append([DocumentTableCell(text=value, source=_line_source(index + 1)) for value in row])
                index += 1
            cells += sum(len(row) for row in rows)
            if cells > limits.max_table_cells:
                raise ValueError("文档表格单元格数量超过解析限制")
        else:
            index += 1
        end = offsets[index] if index < len(offsets) else len(text)
        block = builder.block(text[offsets[start]:end], _line_source(start + 1, index), kind,
            level, title, start=offsets[start], end=end)
        if rows:
            builder.result.tables.append(DocumentTable(block_id=block.id, rows=rows))
    builder.result.metadata = {"encoding": options.encoding, "line_count": len(lines)}
    return builder.finish(text)


def _docx(filename, content, options, limits, capability):
    from docx import Document
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_zip_entries or sum(entry.file_size for entry in entries) > limits.max_expanded_bytes:
                raise ValueError("DOCX 解压内容超过解析限制")
        document = Document(io.BytesIO(content))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("无法读取 DOCX 文档结构") from exc
    builder = _ResultBuilder(filename, content, options, limits, capability)
    builder.result.dependency_versions = {"python-docx": version("python-docx"), "lxml": version("lxml")}
    cells = 0

    def source(path):
        return DocumentSourceLocation(kind="docx", part="word/document.xml", path=path)

    def cell_text(cell, path):
        parts = []
        for inner_index, item in enumerate(cell.iter_inner_content()):
            if isinstance(item, Paragraph):
                parts.append(item.text)
            else:
                nested_rows = table_rows(item, [*path, inner_index])
                parts.append("\n".join("\t".join(value.text for value in row) for row in nested_rows))
                builder.warn("docx_nested_table", "嵌套表格文本保留在所在单元格，独立表格仅列出正文顶层表格。", source(path))
        return "\n".join(parts)

    def table_rows(table, path):
        nonlocal cells
        rows = []
        for row_index, row in enumerate(table.rows):
            values = []
            if row.grid_cols_before or row.grid_cols_after:
                builder.warn("docx_incomplete_grid", "表格行有未占用网格列；单元格按实际列位置记录，不填造缺失单元格。", source([*path, row_index]))
            for column_index, cell in enumerate(row.cells):
                cells += 1
                if cells > limits.max_table_cells:
                    raise ValueError("文档表格单元格数量超过解析限制")
                location = [*path, row_index, column_index + row.grid_cols_before]
                if cell.grid_span > 1:
                    builder.warn("docx_merged_cells", "表格含合并单元格；网格位置可能引用相同文本，合并布局未还原。", source(location))
                values.append(DocumentTableCell(text=cell_text(cell, location), source=source(location)))
            rows.append(values)
        return rows

    for body_index, item in enumerate(document.iter_inner_content()):
        if isinstance(item, Paragraph):
            name = item.style.name if item.style else ""
            heading = re.fullmatch(r"Heading ([1-9])", name)
            level = int(heading.group(1)) if heading else None
            builder.block(item.text, source([body_index]), "heading" if level else "text", level)
        elif isinstance(item, Table):
            rows = table_rows(item, [body_index])
            block = builder.block("\n".join("\t".join(cell.text for cell in row) for row in rows), source([body_index]), "table")
            builder.result.tables.append(DocumentTable(block_id=block.id, rows=rows))
    if document.element.xpath(".//w:ins | .//w:del | .//w:sdt | .//w:txbxContent"):
        builder.warn("docx_unextracted_elements", "文档含修订、内容控件或文本框，轻量接口无法完整提取其中内容。")
        builder.result.status = "partial"
    if document.inline_shapes:
        builder.warn("docx_images_not_extracted", "文档含图像；图像中的文字未做 OCR。")
        builder.result.status = "partial"
    builder.warn("docx_layout_scope", "仅提取正文段落与表格；页码、页眉页脚、脚注、批注及视觉布局不在本解析器能力内。")
    builder.result.metadata = {"body_block_count": len(builder.result.blocks)}
    result = builder.finish()
    if not result.text.strip():
        builder.warn("docx_no_body_text", "正文未提取到文本；文档可能为空或内容位于本解析器不支持的位置。")
        result.status = "unsupported"
    return result


def _pdf(filename, content, options, limits, capability):
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    builder = _ResultBuilder(filename, content, options, limits, capability)
    builder.result.dependency_versions = {"pypdf": version("pypdf")}
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("加密 PDF 首期不支持解析，请提供未加密原件")
        if len(reader.pages) > limits.max_pages:
            raise ValueError("PDF 页数超过解析限制")
        empty = 0
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text(extraction_mode=options.pdf_extraction_mode) or ""
            location = DocumentSourceLocation(kind="page", page=page_number)
            if not text.strip():
                empty += 1
                builder.warn("pdf_no_text", "本页未提取到文本；可能为空白、图像或扫描页，首期不提供 OCR。", location)
            builder.block(text, location)
        builder.result.status = "unsupported" if empty == len(reader.pages) else "partial" if empty else "complete"
        builder.result.metadata = {"page_count": len(reader.pages), "empty_text_pages": empty}
    except (PyPdfError, OSError) as exc:
        raise ValueError("无法解析 PDF 文档结构") from exc
    builder.warn("pdf_structure_unavailable", "仅提取分页文本；表格、标题结构及复杂布局无法可靠还原。")
    return builder.finish()
