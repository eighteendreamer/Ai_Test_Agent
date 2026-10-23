"""Real parser output must retain front matter, nested headings, tables and Unicode."""
from collections import Counter

import pytest

from src.application.documents.parser_factory import DocumentParserFactory
from src.application.test_cases.document_generation_sources import document_rule_candidates, document_segments
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


def test_rule_candidates_keep_exact_source_spans_and_inherit_global_statements():
    text = (
        "# 订单系统\n"
        "所有写操作必须登录。未登录必须拒绝且不能增加记录。\n"
        "## 查询\n查询只能返回当前用户的订单。\n"
        "## 创建\n标题必填；空标题必须拒绝。\n"
    )
    result = DocumentParserFactory().parse(filename="actual.md", content=text.encode())
    segments = document_segments(result)
    assert [item.title for item in segments] == ["查询", "创建"]

    query, query_complete = document_rule_candidates(result, segments[0], "snapshot-a")
    create, create_complete = document_rule_candidates(result, segments[1], "snapshot-a")
    assert query_complete and create_complete
    query_text = [item.statement for item in query]
    create_text = [item.statement for item in create]
    assert "所有写操作必须登录。" in query_text and "所有写操作必须登录。" in create_text
    assert "查询只能返回当前用户的订单。" in query_text
    assert "查询只能返回当前用户的订单。" not in create_text
    assert "标题必填；" in create_text and "空标题必须拒绝。" in create_text

    shared = next(item for item in query if item.statement == "所有写操作必须登录。")
    assert shared.scope == "inherited"
    assert result.text[shared.start_char:shared.end_char] == shared.statement
    assert shared.source_block_id in {block.id for block in result.blocks}


@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_rule_candidates_keep_exact_spans_and_native_source_locations(tmp_path, extension):
    path = tmp_path / ("actual." + extension)
    content = actual_docx(path) if extension == "docx" else actual_pdf(path)
    result = DocumentParserFactory().parse(filename=path.name, content=content)
    segment = document_segments(result)[0]

    candidates, complete = document_rule_candidates(result, segment, "snapshot-native-source")

    assert complete and candidates
    block_ids = {block.id for block in result.blocks}
    for candidate in candidates:
        assert result.text[candidate.start_char:candidate.end_char] == candidate.statement
        assert candidate.source_block_id in block_ids
        if extension == "docx":
            assert candidate.source.kind == "docx"
            assert candidate.source.path
        else:
            assert candidate.source.kind == "page"
            assert candidate.source.page in {1, 2}
    if extension == "pdf":
        assert {candidate.source.page for candidate in candidates} == {1, 2}


def test_rule_candidate_limit_is_reported_instead_of_looking_complete():
    result = DocumentParserFactory().parse(filename="actual.md", content="# 查询\n必须查询。也必须校验。\n".encode())
    segment = document_segments(result)[0]
    candidates, complete = document_rule_candidates(result, segment, "snapshot-a", limit=1)
    assert len(candidates) == 1
    assert complete is False
