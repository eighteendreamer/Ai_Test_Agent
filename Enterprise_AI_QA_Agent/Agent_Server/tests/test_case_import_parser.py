from __future__ import annotations

import io
import json
import zipfile

import pytest
from openpyxl import load_workbook

from src.application.test_cases.import_parser import CaseImportParser, build_import_template
from src.schemas.case_import import CaseImportLimits, CaseImportOptions


def _file(tmp_path, *, layout="rows", cases=None, steps=None, custom_headers=None):
    workbook = load_workbook(io.BytesIO(build_import_template(layout)))
    sheet = workbook["用例"]
    if custom_headers:
        for index, header in enumerate(custom_headers, 1):
            sheet.cell(1, index, header)
    headers = [cell.value for cell in sheet[1]]
    for case in cases or []:
        sheet.append([case.get(header) for header in headers])
    if layout == "split":
        step_sheet = workbook["步骤"]
        for step in steps or []:
            step_sheet.append([step.get(cell.value) for cell in step_sheet[1]])
    path = tmp_path / "订单测试.xlsx"
    workbook.save(path)
    workbook.close()
    return path


def _parse(path, **changes):
    return CaseImportParser().parse(filename=path.name, content=path.read_bytes(), options=CaseImportOptions(mode_key="api_testing", **changes))


def test_row_template_real_xlsx_preserves_steps_negative_conditions_and_source(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "ORDER-001", "标题": "未登录查询应拒绝", "前置条件": "保持未登录",
        "步骤": "打开详情\n查询不存在订单", "预期结果": "拒绝访问\n返回拒绝响应", "测试数据JSON": '{"id":0,"flag":false}',
        "用例说明": "不要补登录或创建订单"}])
    preview = _parse(path)
    assert preview.issues == []
    draft = preview.candidates[0].draft
    assert draft.case_key == "order-001"
    assert draft.steps == []  # Business content is not legacy mode parameters.
    assert draft.content_json["preconditions"] == ["保持未登录"]
    assert [step["action"] for step in draft.content_json["steps"]] == ["打开详情", "查询不存在订单"]
    assert draft.content_json["test_data"] == {"id": 0, "flag": False}
    assert draft.execution_notes == "不要补登录或创建订单"
    source = draft.source_refs[0]
    assert source.metadata["row"] == 2 and source.metadata["sheet"] == "用例"
    assert source.metadata["columns"]["case_key"] == "用例编号"
    assert source.source_id == preview.file_hash
    assert CaseImportParser().inspect(filename=path.name, content=path.read_bytes())[0].suggested_columns["title"] == "标题"


def test_split_template_real_xlsx_sorts_steps_and_keeps_row_provenance(tmp_path):
    path = _file(tmp_path, layout="split", cases=[{"用例编号": "order-detail", "标题": "查询详情"}], steps=[
        {"用例编号": "order-detail", "步骤序号": 2, "操作": "查询", "步骤预期": "详情正确"},
        {"用例编号": "order-detail", "步骤序号": 1, "操作": "使用项目已有订单", "步骤预期": "获得编号"},
    ])
    preview = _parse(path, layout="split", case_sheet="用例", step_sheet="步骤")
    assert not preview.issues
    candidate = preview.candidates[0]
    assert [step["order"] for step in candidate.draft.content_json["steps"]] == [1, 2]
    assert candidate.step_rows == [2, 3]
    assert candidate.draft.source_refs[0].metadata["step_sheet"] == "步骤"
    assert candidate.draft.content_json["steps"][0]["source_columns"]["操作"] == "使用项目已有订单"


def test_explicit_mapping_and_full_json_keep_unknown_fields(tmp_path):
    headers = ["编号自定", "名称自定", "类型", "优先级", "前置条件", "步骤", "预期结果", "测试数据JSON", "清理", "用例说明", "完整JSON"]
    content = {"任意结构": {"数组": [0, False, None, "中文😀"]}, "instructions": "完整读取"}
    path = _file(tmp_path, custom_headers=headers, cases=[{"编号自定": "custom", "名称自定": "自定义", "完整JSON": json.dumps(content)}])
    with pytest.raises(ValueError, match="明确映射"):
        _parse(path)
    preview = _parse(path, columns={"case_key": "编号自定", "title": "名称自定"})
    assert preview.candidates[0].draft.content_json == content
    assert preview.resolved_columns["title"] == "名称自定"
    assert preview.candidates[0].draft.source_refs[0].metadata["source_columns"]["完整JSON"] == json.dumps(content)


def test_explicit_unmapping_does_not_restore_an_auto_detected_column(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "one", "标题": "忽略该列", "前置条件": "此列不应进入业务内容", "步骤": "查询", "预期结果": "成功"}])
    preview = _parse(path, columns={"preconditions": ""})
    assert "preconditions" not in preview.resolved_columns
    assert preview.candidates[0].draft.content_json["preconditions"] == []
    assert preview.candidates[0].draft.source_refs[0].metadata["source_columns"]["前置条件"] == "此列不应进入业务内容"
    with pytest.raises(ValueError, match="明确映射字段：title"):
        _parse(path, columns={"title": ""})


def test_duplicate_and_invalid_rows_are_visible_without_discarding_valid_rows(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "same", "标题": "第一条", "完整JSON": '{"goal":"first"}'},
        {"用例编号": "SAME", "标题": "重复", "完整JSON": '{"goal":"second"}'},
        {"用例编号": "invalid key", "标题": "错误编号", "完整JSON": '{"goal":"bad"}'},
        {"用例编号": "bad-json", "标题": "错误JSON", "完整JSON": '{"goal":'},
    ])
    preview = _parse(path)
    assert [candidate.draft.case_key for candidate in preview.candidates] == ["same"]
    assert [issue.row for issue in preview.issues] == [3, 4, 5]
    assert preview.issues[0].code == "duplicate_case_key"
    assert all(issue.severity == "error" for issue in preview.issues)


@pytest.mark.parametrize("bad_order", ["bad", 1.5, 0])
def test_invalid_split_step_invalidates_its_case_instead_of_omitting_step(tmp_path, bad_order):
    path = _file(tmp_path, layout="split", cases=[{"用例编号": "one", "标题": "完整步骤"}], steps=[
        {"用例编号": "one", "步骤序号": 1, "操作": "第一步", "步骤预期": "完成"},
        {"用例编号": "one", "步骤序号": bad_order, "操作": "不能遗漏的第二步"},
    ])
    preview = _parse(path, layout="split", step_sheet="步骤")
    assert preview.candidates == []
    assert {issue.code for issue in preview.issues} == {"invalid_step", "invalid_case"}


def test_orphan_and_duplicate_steps_have_precise_rows(tmp_path):
    path = _file(tmp_path, layout="split", cases=[{"用例编号": "one", "标题": "重复步骤"}], steps=[
        {"用例编号": "one", "步骤序号": 1, "操作": "a"}, {"用例编号": "one", "步骤序号": 1, "操作": "b"},
        {"用例编号": "missing", "步骤序号": 1, "操作": "孤立步骤"}])
    preview = _parse(path, layout="split", step_sheet="步骤")
    assert preview.candidates == []
    assert next(issue for issue in preview.issues if issue.code == "orphan_step").row == 4


def test_formula_is_literal_and_aggregated_expected_results_are_not_lost(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "formula", "标题": "保持原文", "步骤": '=CONCAT("a","b")\n第二步', "预期结果": "整体预期"}])
    preview = _parse(path)
    assert preview.candidates[0].draft.content_json["steps"][0]["action"] == '=CONCAT("a","b")'
    assert preview.candidates[0].draft.content_json["expected_results"] == ["整体预期"]
    assert any(issue.code == "formula_literal" for issue in preview.issues)


def test_json_file_keeps_custom_content_and_reports_rows(tmp_path):
    path = tmp_path / "cases.json"
    business = {"扩展": [0, False, None], "condition": "刻意不存在"}
    path.write_text(json.dumps({"cases": [{"case_key": "one", "title": "自定义", "content_json": business},
        {"case_key": "two", "title": "原格式", "custom": {"deep": True}}, "invalid"]}, ensure_ascii=False), encoding="utf-8-sig")
    preview = _parse(path, layout="json")
    assert preview.candidates[0].draft.content_json == business
    assert preview.candidates[1].draft.content_json["custom"] == {"deep": True}
    assert preview.issues[0].row == 3
    assert preview.sheets == []


def test_wrong_dimensions_do_not_silently_hide_final_step(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "one", "标题": "尾部", "步骤": "最后一步", "预期结果": "保存成功"}])
    stream = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stream, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b'<dimension ref="A1:K2"/>', b'<dimension ref="A1:A1"/>')
            target.writestr(item, content)
    path.write_bytes(stream.getvalue())
    preview = _parse(path)
    assert preview.candidates[0].draft.content_json["steps"][0]["action"] == "最后一步"


def test_empty_missing_headers_and_oversized_inputs_fail_explicitly(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "one", "标题": "内容", "步骤": "一步", "预期结果": "成功"}])
    with pytest.raises(ValueError, match="文件大小限制"):
        CaseImportParser(CaseImportLimits(max_bytes=1)).parse(filename=path.name, content=path.read_bytes(), options=CaseImportOptions(mode_key="api_testing"))
    with pytest.raises(ValueError, match="解压内容"):
        CaseImportParser(CaseImportLimits(max_expanded_bytes=1)).inspect(filename=path.name, content=path.read_bytes())
    with pytest.raises(ValueError, match="旧 .xls"):
        CaseImportParser().parse(filename="old.xls", content=b"data", options=CaseImportOptions(mode_key="api_testing"))
    with pytest.raises(ValueError, match="工作表不存在"):
        _parse(path, case_sheet="不存在")
    workbook = load_workbook(path)
    workbook["用例"].cell(1, 3).value = None
    workbook["用例"].cell(2, 3).value = "无表头但有信息"
    workbook.save(path)
    workbook.close()
    preview = _parse(path)
    assert preview.candidates == []
    assert preview.issues[0].code == "missing_header"


def test_invalid_json_does_not_accept_non_json_constants(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('[{"case_key":"one","title":"bad","number":NaN}]', encoding="utf-8")
    with pytest.raises(ValueError, match="合法的 UTF-8 JSON"):
        _parse(path, layout="json")


def test_actual_rows_not_cached_dimensions_enforce_limits(tmp_path):
    path = _file(tmp_path, cases=[{"用例编号": "one", "标题": "第一条"}, {"用例编号": "two", "标题": "第二条"}])
    with pytest.raises(ValueError, match="工作表行数"):
        CaseImportParser(CaseImportLimits(max_rows=1)).parse(filename=path.name, content=path.read_bytes(), options=CaseImportOptions(mode_key="api_testing"))
    with pytest.raises(ValueError, match="用例数量"):
        CaseImportParser(CaseImportLimits(max_cases=1)).parse(filename=path.name, content=path.read_bytes(), options=CaseImportOptions(mode_key="api_testing"))


@pytest.mark.parametrize("damaged_part", ["xl/workbook.xml", "xl/worksheets/sheet1.xml"])
def test_malformed_xlsx_xml_and_macro_payload_are_visible_errors(tmp_path, damaged_part):
    path = _file(tmp_path)
    stream = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stream, "w") as target:
        for item in source.infolist():
            content = b"<invalid" if item.filename == damaged_part else source.read(item.filename)
            target.writestr(item, content)
    with pytest.raises(ValueError, match="无法读取 Excel 文件结构"):
        CaseImportParser().inspect(filename=path.name, content=stream.getvalue())
    stream = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stream, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("xl/vbaProject.bin", b"macro-fixture")
    with pytest.raises(ValueError, match="不接受宏"):
        CaseImportParser().inspect(filename=path.name, content=stream.getvalue())
