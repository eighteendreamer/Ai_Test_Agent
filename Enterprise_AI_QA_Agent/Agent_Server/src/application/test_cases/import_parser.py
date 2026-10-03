from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError

from src.schemas.case_import import (
    CaseImportCandidate, CaseImportIssue, CaseImportLimits, CaseImportOptions,
    CaseImportPreview, CaseImportSheet,
)
from src.schemas.case_management import TestCaseDraftCreateRequest, TestCaseSourceRef


PARSER_VERSION = "case-import-v1"
# No upstream counterpart (checked: current case generation/xlsx exporter,
# reference document_parser/import_parser): those expose text or API requests,
# not project case drafts. This is the shared preview conversion for both sources.
COLUMN_ALIASES = {
    "case_key": ("用例编号", "case_key", "ID"),
    "title": ("标题", "title", "Title", "用例名称"),
    "case_type": ("类型", "case_type", "Type"),
    "priority": ("优先级", "priority", "Priority"),
    "preconditions": ("前置条件", "preconditions", "Preconditions"),
    "steps": ("步骤", "steps", "Steps"),
    "expected_results": ("预期结果", "expected_results", "Expected Results"),
    "test_data": ("测试数据JSON", "test_data", "Test Data"),
    "cleanup": ("清理", "cleanup", "Cleanup"),
    "execution_notes": ("用例说明", "execution_notes", "Execution Notes"),
    "content_json": ("完整JSON", "content_json", "Content JSON"),
    "order": ("步骤序号", "order", "Order"),
    "action": ("操作", "action", "Action"),
    "expected": ("步骤预期", "expected", "Expected"),
}
ROW_FIELDS = tuple(COLUMN_ALIASES)[:11]
STEP_FIELDS = ("case_key", "order", "action", "expected")


class CaseImportParser:
    """Pure conversion. Does not execute cells, enable cases or write databases."""

    def __init__(self, limits: CaseImportLimits | None = None) -> None:
        self._limits = limits or CaseImportLimits()

    def inspect(self, *, filename: str, content: bytes, header_row: int = 1) -> list[CaseImportSheet]:
        if not content or len(content) > self._limits.max_bytes:
            raise ValueError("导入文件为空或超过文件大小限制")
        if Path(filename).suffix.lower() == ".json":
            return []
        if Path(filename).suffix.lower() != ".xlsx":
            raise ValueError("用例导入仅支持 .xlsx 和 .json")
        if not 1 <= header_row <= 50:
            raise ValueError("表头行必须在 1 到 50 之间")
        with self._workbook(content) as workbook:
            return [CaseImportSheet(name=sheet.title, headers=headers, suggested_columns=self._suggest(headers))
                    for sheet in workbook.worksheets if (headers := self._headers(sheet, header_row)) is not None]

    def parse(self, *, filename: str, content: bytes, options: CaseImportOptions) -> CaseImportPreview:
        extension = Path(filename).suffix.lower()
        if extension not in {".xlsx", ".json"}:
            raise ValueError("用例导入仅支持 .xlsx 和 .json，旧 .xls 不在首期范围内")
        if not content or len(content) > self._limits.max_bytes:
            raise ValueError("导入文件为空或超过文件大小限制")
        result = CaseImportPreview(filename=Path(filename).name, file_hash=hashlib.sha256(content).hexdigest(), options=options)
        if extension == ".json":
            if options.layout != "json":
                raise ValueError("JSON 文件请选择 json 导入布局")
            self._parse_json(content, result)
        else:
            if options.layout == "json":
                raise ValueError("Excel 文件请选择 rows 或 split 导入布局")
            with self._workbook(content) as workbook:
                for sheet in workbook.worksheets:
                    headers = self._headers(sheet, options.header_row)
                    result.sheets.append(CaseImportSheet(name=sheet.title, headers=headers, suggested_columns=self._suggest(headers)))
                self._parse_excel(workbook, result)
        return result

    @contextmanager
    def _workbook(self, content: bytes):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > self._limits.max_zip_entries or sum(item.file_size for item in entries) > self._limits.max_expanded_bytes:
                    raise ValueError("Excel 解压内容超过导入限制")
                if any(item.filename.lower().endswith("vbaproject.bin") for item in entries):
                    raise ValueError("用例导入不接受宏内容")
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        except (zipfile.BadZipFile, KeyError, OSError, ParseError, InvalidFileException) as exc:
            raise ValueError("无法读取 Excel 文件结构") from exc
        try:
            if not workbook.worksheets:
                raise ValueError("Excel 文件没有可导入的工作表")
            if len(workbook.worksheets) > self._limits.max_sheets:
                raise ValueError("Excel 工作表数量超过导入限制")
            # Do not trust cached dimensions: an incorrect A1:A1 must not hide
            # trailing steps. Actual rows/columns are bounded during iteration.
            for sheet in workbook.worksheets:
                sheet.reset_dimensions()
            yield workbook
        finally:
            workbook.close()

    def _headers(self, sheet, row_number: int) -> list[str]:
        rows = sheet.iter_rows(min_row=row_number, max_row=row_number)
        row = next(rows, ())
        try:
            if len(row) > self._limits.max_columns:
                raise ValueError("Excel 表头列数超过导入限制")
            return [str(cell.value).strip() if cell.value is not None else "" for cell in row]
        finally:
            rows.close()

    def _suggest(self, headers: list[str]) -> dict[str, str]:
        return {field: matches[0] for field, aliases in COLUMN_ALIASES.items()
                if len(matches := [header for header in headers if header.casefold() in {alias.casefold() for alias in aliases}]) == 1}

    def _mapping(self, headers: list[str], explicit: dict[str, str], fields: tuple[str, ...]) -> dict[str, str]:
        nonempty = [header for header in headers if header]
        if len(nonempty) != len(set(nonempty)):
            raise ValueError("工作表存在重复表头，请修正后导入")
        mapping = {key: value for key, value in self._suggest(headers).items() if key in fields}
        for field, header in explicit.items():
            if field in fields and header == "":
                mapping.pop(field, None)
                continue
            if field not in fields or header not in nonempty:
                raise ValueError(f"无效列映射：{field}")
            mapping[field] = header
        for field in ("case_key", "title") if "title" in fields else ("case_key", "action"):
            if field not in mapping:
                raise ValueError(f"需要明确映射字段：{field}")
        return mapping

    def _rows(self, sheet, headers: list[str], start: int, result: CaseImportPreview):
        rows = sheet.iter_rows(min_row=start)
        try:
            for number, cells in enumerate(rows, start):
                if number - start >= self._limits.max_rows:
                    raise ValueError("工作表行数超过导入限制，未截断后继续报告成功")
                if len(cells) > self._limits.max_columns:
                    raise ValueError("工作表实际列数超过导入限制")
                if not any(cell.value is not None for cell in cells):
                    continue
                if any(cell.value is not None for cell in cells[len(headers):]) or any(
                    not header and cell.value is not None for header, cell in zip(headers, cells)
                ):
                    self._issue(result, "missing_header", "有内容的列缺少表头", sheet.title, number)
                    continue
                values = {}
                for header, cell in zip(headers, cells):
                    if not header:
                        continue
                    if len(str(cell.value or "")) > self._limits.max_cell_chars:
                        raise ValueError("单元格内容超过导入限制")
                    if cell.data_type == "f":
                        self._issue(result, "formula_literal", "公式仅保留原文，不计算或使用缓存结果", sheet.title, number, header, "warning")
                    values[header] = _json_value(cell.value)
                yield number, values
        finally:
            rows.close()

    def _parse_excel(self, workbook, result: CaseImportPreview) -> None:
        options = result.options
        name = options.case_sheet or workbook.worksheets[0].title
        if name not in {sheet.name for sheet in result.sheets}:
            raise ValueError("指定的用例工作表不存在")
        case_sheet = workbook[name]
        headers = next(sheet.headers for sheet in result.sheets if sheet.name == name)
        result.resolved_columns = self._mapping(headers, options.columns, ROW_FIELDS)
        step_groups, invalid_step_keys = self._step_groups(workbook, result) if options.layout == "split" else ({}, set())
        seen: set[str] = set()
        for number, raw in self._rows(case_sheet, headers, options.header_row + 1, result):
            mapped = {field: raw.get(header) for field, header in result.resolved_columns.items()}
            key = _text(mapped.get("case_key")).strip().lower()
            if key in seen:
                self._issue(result, "duplicate_case_key", "文件内存在重复用例编号", name, number, "case_key")
                continue
            seen.add(key)
            if len(seen) > self._limits.max_cases:
                raise ValueError("用例数量超过导入限制")
            step_rows = []
            try:
                if key in invalid_step_keys:
                    raise ValueError("该用例关联的步骤存在错误，不能遗漏后继续导入")
                if mapped.get("content_json") not in (None, ""):
                    business = _object_json(mapped["content_json"])
                else:
                    actions = _lines(mapped.get("steps"))
                    expected = _lines(mapped.get("expected_results"))
                    steps = [{"order": index, "action": action,
                              **({"expected": expected[index - 1]} if len(expected) == len(actions) else {})}
                             for index, action in enumerate(actions, 1)]
                    if options.layout == "split":
                        group = step_groups.get(key, [])
                        if not group:
                            raise ValueError("该用例没有可关联的步骤")
                        orders = [step["order"] for _, step in group]
                        if len(orders) != len(set(orders)):
                            raise ValueError("同一用例的步骤序号重复")
                        steps = [step for _, step in sorted(group, key=lambda item: item[1]["order"])]
                        step_rows = [row for row, _ in group]
                    business = {"objective": mapped.get("title"), "preconditions": _lines(mapped.get("preconditions")),
                                "steps": steps, "expected_results": expected,
                                "test_data": _object_json(mapped.get("test_data") or "{}"), "cleanup": _lines(mapped.get("cleanup"))}
                    if not steps or not (expected or any(step.get("expected") for step in steps)):
                        self._issue(result, "review_business_content", "操作或预期不完整，草稿需要评审补充", name, number, severity="warning")
                self._candidate(result, number, name, mapped, business, raw, step_rows)
            except (ValueError, TypeError) as exc:
                self._issue(result, "invalid_case", _validation_message(exc), name, number)
        for key, group in step_groups.items():
            if key not in seen:
                for number, _ in group:
                    self._issue(result, "orphan_step", "步骤找不到当前用例表中的编号", options.step_sheet, number, "case_key")

    def _step_groups(self, workbook, result: CaseImportPreview) -> tuple[dict[str, list[tuple[int, dict[str, Any]]]], set[str]]:
        options = result.options
        if not options.step_sheet or options.step_sheet not in {sheet.name for sheet in result.sheets} or options.step_sheet == (options.case_sheet or workbook.worksheets[0].title):
            raise ValueError("分表布局需要选择独立且存在的步骤工作表")
        headers = next(sheet.headers for sheet in result.sheets if sheet.name == options.step_sheet)
        result.resolved_step_columns = self._mapping(headers, options.step_columns, STEP_FIELDS)
        groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        invalid_keys: set[str] = set()
        for number, raw in self._rows(workbook[options.step_sheet], headers, options.header_row + 1, result):
            mapped = {field: raw.get(header) for field, header in result.resolved_step_columns.items()}
            key = _text(mapped.get("case_key")).strip().lower()
            try:
                order_raw = mapped.get("order")
                order = len(groups.get(key, [])) + 1 if order_raw in (None, "") else int(order_raw)
                if order < 1 or (order_raw not in (None, "") and float(order_raw) != order) or not key or not _text(mapped.get("action")).strip():
                    raise ValueError("步骤编号、序号或操作无效")
                groups.setdefault(key, []).append((number, {"order": order, "action": mapped["action"], "expected": mapped.get("expected"), "source_columns": raw}))
            except (ValueError, TypeError, OverflowError):
                invalid_keys.add(key)
                self._issue(result, "invalid_step", "步骤编号、序号或操作无效", options.step_sheet, number)
        return groups, invalid_keys

    def _parse_json(self, content: bytes, result: CaseImportPreview) -> None:
        try:
            items = _strict_json(content.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("JSON 文件不是合法的 UTF-8 JSON") from exc
        if isinstance(items, dict) and isinstance(items.get("cases"), list):
            items = items["cases"]
        if not isinstance(items, list) or len(items) > self._limits.max_cases:
            raise ValueError("JSON 导入需要用例数组，且不能超过用例数量限制")
        seen: set[str] = set()
        for row, item in enumerate(items, 1):
            if not isinstance(item, dict):
                self._issue(result, "invalid_case", "用例必须是 JSON 对象", row=row)
                continue
            key = _text(item.get("case_key")).strip().lower()
            if key in seen:
                self._issue(result, "duplicate_case_key", "文件内存在重复用例编号", row=row, field="case_key")
                continue
            seen.add(key)
            try:
                business = item.get("content_json", item)
                if not isinstance(business, dict) or not business:
                    raise ValueError("用例业务内容必须是非空 JSON 对象")
                self._candidate(result, row, None, item, business, item, [])
            except (ValueError, TypeError) as exc:
                self._issue(result, "invalid_case", _validation_message(exc), row=row)

    def _candidate(self, result, row, sheet, mapped, business, raw, step_rows):
        if len(result.candidates) >= self._limits.max_cases:
            raise ValueError("用例数量超过导入限制")
        provenance = {"filename": result.filename, "file_hash": result.file_hash, "sheet": sheet, "row": row,
                      "step_sheet": result.options.step_sheet, "step_rows": step_rows,
                      "columns": result.resolved_columns, "step_columns": result.resolved_step_columns,
                      "parser_version": PARSER_VERSION, "source_columns": raw}
        draft = TestCaseDraftCreateRequest(
            case_key=_text(mapped.get("case_key")).strip().lower(), title=_text(mapped.get("title")).strip(),
            mode_key=result.options.mode_key, case_type=_text(mapped.get("case_type") or "imported"),
            priority=_text(mapped.get("priority") or "P1"), content_json=business,
            execution_notes=_text(mapped.get("execution_notes")),
            source_refs=[TestCaseSourceRef(source_type="case_import", source_id=result.file_hash, label=result.filename, metadata=provenance)],
            model_key="manual-import", prompt_version="not-used-manual-import", skill_versions={"manual-import": "not-used"},
            generation_metadata={"import": {"parser_version": PARSER_VERSION, "file_hash": result.file_hash}},
        )
        result.candidates.append(CaseImportCandidate(row=row, sheet=sheet, step_rows=step_rows, draft=draft))

    @staticmethod
    def _issue(result, code, message, sheet=None, row=None, field=None, severity="error"):
        result.issues.append(CaseImportIssue(code=code, message=message, sheet=sheet, row=row, field=field, severity=severity))


def build_import_template(layout: str) -> bytes:
    if layout not in {"rows", "split"}:
        raise ValueError("Excel 模板布局必须为 rows 或 split")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用例"
    fields = ROW_FIELDS if layout == "rows" else tuple(field for field in ROW_FIELDS if field not in {"steps", "expected_results"})
    sheet.append([COLUMN_ALIASES[field][0] for field in fields])
    if layout == "split":
        workbook.create_sheet("步骤").append([COLUMN_ALIASES[field][0] for field in STEP_FIELDS])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _strict_json(value: str):
    def invalid_constant(_value):
        raise ValueError("JSON 不允许 NaN 或 Infinity")
    return json.loads(value, parse_constant=invalid_constant)


def _object_json(value: Any) -> dict[str, Any]:
    parsed = value if isinstance(value, dict) else _strict_json(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("JSON 单元格必须是对象")
    return parsed


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _lines(value: Any) -> list[str]:
    return [line.strip() for line in _text(value).splitlines() if line.strip()]


def _json_value(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Excel 单元格含非有限数值")
    return value


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors(include_input=False, include_url=False))
    return str(exc)
