"""Real parser output must retain front matter, nested headings, tables and Unicode."""
from collections import Counter

import pytest

from src.application.documents.parser_factory import DocumentParserFactory
from src.application.test_cases.document_generation_sources import document_segments
from test_document_parsers import actual_docx, actual_pdf


@pytest.mark.parametrize("text,titles", [
    ("前言 😀\n# 商城\n## 登录\n规则\n### 失败\n失败条件\n## 订单\n| 字段 | 约束 |\n| --- | --- |\n| 金额 | 大于0 |\n## 查询\n分页", ["登录", "订单", "查询"]),
    ("# A\n## A1\n## A2\n# B\n## B1\n文本", ["A", "B"]),
    ("前言\n没有标题，不能丢失", ["actual.md"]),
    ("# 只有一个标题\n后文", ["actual.md"]),
])
def test_actual_markdown_ranges_cover_every_character_once(text, titles):
    result = DocumentParserFactory().parse(filename="actual.md", content=text.encode())
    segments = document_segments(result)
    assert [s.title for s in segments] == titles
    assert "".join(result.text[s.start_char:s.end_char] for s in segments) == result.text
    assert Counter(b for s in segments for b in s.block_ids) == Counter(b.id for b in result.blocks)
    assert [s.index for s in segments] == list(range(len(segments)))


@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_actual_docx_pdf_no_inferred_module_or_lost_table_and_page(tmp_path, extension):
    path = tmp_path / ("actual." + extension)
    content = actual_docx(path) if extension == "docx" else actual_pdf(path)
    result = DocumentParserFactory().parse(filename=path.name, content=content)
    segments = document_segments(result)
    assert len(segments) == 1
    assert result.text[segments[0].start_char:segments[0].end_char] == result.text
    assert segments[0].block_ids == [b.id for b in result.blocks]


def test_empty_actual_document_cannot_be_reported_as_generated():
    result = DocumentParserFactory().parse(filename="empty.md", content=b"   \n")
    with pytest.raises(ValueError, match="没有可生成"): document_segments(result)
