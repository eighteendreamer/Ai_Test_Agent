"""
页面结构对比引擎（Diff Engine）

功能：
  1. 计算新旧页面结构的差异
  2. 变更类型分类：字段新增 / 删除 / 修改
  3. 变更日志生成
  4. 自动回归测试推荐

如果页面新增字段 → 自动建议新的边界测试子任务
"""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from Page_Knowledge.schema import PageKnowledge

logger = logging.getLogger(__name__)


class ChangeType:
    """变更类型"""
    FIELD_ADDED = "field_added"
    FIELD_REMOVED = "field_removed"
    FIELD_MODIFIED = "field_modified"
    BUTTON_ADDED = "button_added"
    BUTTON_REMOVED = "button_removed"
    TABLE_ADDED = "table_added"
    TABLE_REMOVED = "table_removed"
    TABLE_COLUMN_CHANGED = "table_column_changed"
    FORM_ADDED = "form_added"
    FORM_REMOVED = "form_removed"
    SECTION_ADDED = "section_added"
    SECTION_REMOVED = "section_removed"
    PROPERTY_CHANGED = "property_changed"


class DiffResult:
    """对比结果"""

    def __init__(self):
        self.changes: List[Dict] = []
        self.has_changes: bool = False
        self.severity: str = "none"       # none / low / medium / high
        self.summary: str = ""
        self.regression_hints: List[str] = []  # 自动回归测试推荐

    def add_change(self, change_type: str, description: str, detail: Any = None):
        self.changes.append({
            "type": change_type,
            "description": description,
            "detail": detail,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.has_changes = True

    def compute_severity(self):
        """根据变更量和类型计算严重度"""
        if not self.changes:
            self.severity = "none"
            return

        critical_types = {
            ChangeType.FORM_ADDED, ChangeType.FORM_REMOVED,
            ChangeType.TABLE_ADDED, ChangeType.TABLE_REMOVED,
        }
        medium_types = {
            ChangeType.FIELD_ADDED, ChangeType.FIELD_REMOVED,
            ChangeType.FIELD_MODIFIED, ChangeType.TABLE_COLUMN_CHANGED,
        }

        critical_count = sum(1 for c in self.changes if c["type"] in critical_types)
        medium_count = sum(1 for c in self.changes if c["type"] in medium_types)

        if critical_count > 0:
            self.severity = "high"
        elif medium_count >= 3:
            self.severity = "high"
        elif medium_count >= 1 or len(self.changes) >= 3:
            self.severity = "medium"
        else:
            self.severity = "low"

    def build_summary(self):
        """生成变更摘要"""
        if not self.changes:
            self.summary = "页面结构无变化"
            return

        type_counts = {}
        for c in self.changes:
            t = c["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        parts = []
        for t, cnt in type_counts.items():
            type_label = {
                ChangeType.FIELD_ADDED: "新增字段",
                ChangeType.FIELD_REMOVED: "删除字段",
                ChangeType.FIELD_MODIFIED: "修改字段",
                ChangeType.BUTTON_ADDED: "新增按钮",
                ChangeType.BUTTON_REMOVED: "删除按钮",
                ChangeType.TABLE_ADDED: "新增表格",
                ChangeType.TABLE_REMOVED: "删除表格",
                ChangeType.TABLE_COLUMN_CHANGED: "表格列变更",
                ChangeType.FORM_ADDED: "新增表单",
                ChangeType.FORM_REMOVED: "删除表单",
                ChangeType.SECTION_ADDED: "新增功能区域",
                ChangeType.SECTION_REMOVED: "删除功能区域",
                ChangeType.PROPERTY_CHANGED: "属性变更",
            }.get(t, t)
            parts.append(f"{type_label}×{cnt}")

        self.summary = f"检测到 {len(self.changes)} 项变更：{', '.join(parts)}"

    def to_dict(self) -> Dict:
        return {
            "has_changes": self.has_changes,
            "severity": self.severity,
            "summary": self.summary,
            "change_count": len(self.changes),
            "changes": self.changes,
            "regression_hints": self.regression_hints,
        }


class DiffEngine:
    """页面结构对比引擎"""

    @staticmethod
    def compute_diff(old: PageKnowledge, new: PageKnowledge) -> DiffResult:
        """
        对比两个版本的页面知识，生成差异报告

        Args:
            old: 知识库中已有的页面知识
            new: 最新探索得到的页面知识

        Returns:
            DiffResult 包含所有变更信息
        """
        result = DiffResult()

        # 1. 对比表单
        DiffEngine._diff_forms(old, new, result)

        # 2. 对比按钮
        DiffEngine._diff_buttons(old, new, result)

        # 3. 对比表格
        DiffEngine._diff_tables(old, new, result)

        # 4. 对比功能区域
        DiffEngine._diff_sections(old, new, result)

        # 5. 对比属性标记
        DiffEngine._diff_properties(old, new, result)

        # 计算严重度和摘要
        result.compute_severity()
        result.build_summary()

        # 生成回归测试推荐
        DiffEngine._generate_regression_hints(result)

        return result

    @staticmethod
    def _diff_forms(old: PageKnowledge, new: PageKnowledge, result: DiffResult):
        """对比表单"""
        old_forms = {f.name: f for f in old.forms}
        new_forms = {f.name: f for f in new.forms}

        # 新增表单
        for name in new_forms:
            if name not in old_forms:
                result.add_change(
                    ChangeType.FORM_ADDED,
                    f"新增表单: {name}（{len(new_forms[name].fields)}个字段）",
                    {"form_name": name, "fields": [f.name for f in new_forms[name].fields]}
                )

        # 删除表单
        for name in old_forms:
            if name not in new_forms:
                result.add_change(
                    ChangeType.FORM_REMOVED,
                    f"删除表单: {name}",
                    {"form_name": name}
                )

        # 已有表单的字段变化
        for name in old_forms:
            if name not in new_forms:
                continue
            old_fields = {f.name: f for f in old_forms[name].fields}
            new_fields = {f.name: f for f in new_forms[name].fields}

            for fn in new_fields:
                if fn not in old_fields:
                    result.add_change(
                        ChangeType.FIELD_ADDED,
                        f"表单 {name} 新增字段: {fn} ({new_fields[fn].field_type})",
                        {"form_name": name, "field_name": fn, "field_type": new_fields[fn].field_type}
                    )
            for fn in old_fields:
                if fn not in new_fields:
                    result.add_change(
                        ChangeType.FIELD_REMOVED,
                        f"表单 {name} 删除字段: {fn}",
                        {"form_name": name, "field_name": fn}
                    )
            for fn in old_fields:
                if fn in new_fields:
                    of = old_fields[fn]
                    nf = new_fields[fn]
                    if of.field_type != nf.field_type or of.required != nf.required:
                        result.add_change(
                            ChangeType.FIELD_MODIFIED,
                            f"表单 {name} 字段 {fn} 变更: "
                            f"类型 {of.field_type}→{nf.field_type}, "
                            f"必填 {of.required}→{nf.required}",
                            {"form_name": name, "field_name": fn,
                             "old_type": of.field_type, "new_type": nf.field_type}
                        )

    @staticmethod
    def _diff_buttons(old: PageKnowledge, new: PageKnowledge, result: DiffResult):
        """对比按钮"""
        # 处理 buttons 可能包含 dict 的情况（兼容旧数据）
        def normalize_buttons(buttons):
            normalized = []
            for btn in buttons:
                if isinstance(btn, dict):
                    normalized.append(btn.get("name", str(btn)))
                elif isinstance(btn, str):
                    normalized.append(btn)
                else:
                    normalized.append(str(btn))
            return normalized
        
        old_buttons = normalize_buttons(old.buttons)
        new_buttons = normalize_buttons(new.buttons)
        
        old_set = set(old_buttons)
        new_set = set(new_buttons)

        for b in new_set - old_set:
            result.add_change(ChangeType.BUTTON_ADDED, f"新增按钮: {b}", {"button": b})
        for b in old_set - new_set:
            result.add_change(ChangeType.BUTTON_REMOVED, f"删除按钮: {b}", {"button": b})

    @staticmethod
    def _diff_tables(old: PageKnowledge, new: PageKnowledge, result: DiffResult):
        """对比表格"""
        old_tables = {t.name: t for t in old.tables}
        new_tables = {t.name: t for t in new.tables}

        for name in new_tables:
            if name not in old_tables:
                result.add_change(
                    ChangeType.TABLE_ADDED,
                    f"新增表格: {name}（{len(new_tables[name].columns)}列）",
                    {"table_name": name, "columns": new_tables[name].columns}
                )

        for name in old_tables:
            if name not in new_tables:
                result.add_change(
                    ChangeType.TABLE_REMOVED,
                    f"删除表格: {name}",
                    {"table_name": name}
                )

        for name in old_tables:
            if name not in new_tables:
                continue
            old_cols = set(old_tables[name].columns)
            new_cols = set(new_tables[name].columns)
            if old_cols != new_cols:
                added_cols = new_cols - old_cols
                removed_cols = old_cols - new_cols
                changes = []
                if added_cols:
                    changes.append(f"+{','.join(added_cols)}")
                if removed_cols:
                    changes.append(f"-{','.join(removed_cols)}")
                result.add_change(
                    ChangeType.TABLE_COLUMN_CHANGED,
                    f"表格 {name} 列变更: {'; '.join(changes)}",
                    {"table_name": name, "added": list(added_cols), "removed": list(removed_cols)}
                )

    @staticmethod
    def _diff_sections(old: PageKnowledge, new: PageKnowledge, result: DiffResult):
        """对比功能区域"""
        old_set = set(old.page_sections)
        new_set = set(new.page_sections)

        for s in new_set - old_set:
            result.add_change(ChangeType.SECTION_ADDED, f"新增功能区域: {s}", {"section": s})
        for s in old_set - new_set:
            result.add_change(ChangeType.SECTION_REMOVED, f"删除功能区域: {s}", {"section": s})

    @staticmethod
    def _diff_properties(old: PageKnowledge, new: PageKnowledge, result: DiffResult):
        """对比属性标记"""
        props = [
            ("auth_required", "需要登录"),
            ("has_file_upload", "文件上传"),
            ("has_export", "数据导出"),
            ("has_import", "数据导入"),
            ("has_search", "搜索功能"),
            ("has_pagination", "分页功能"),
        ]
        for attr, label in props:
            old_val = getattr(old, attr, None)
            new_val = getattr(new, attr, None)
            if old_val != new_val:
                result.add_change(
                    ChangeType.PROPERTY_CHANGED,
                    f"{label}: {old_val} → {new_val}",
                    {"property": attr, "old": old_val, "new": new_val}
                )

    @staticmethod
    def _generate_regression_hints(result: DiffResult):
        """
        根据变更自动生成回归测试建议

        如果页面新增字段 → 自动建议边界测试子任务
        """
        for change in result.changes:
            ct = change["type"]
            detail = change.get("detail", {})

            if ct == ChangeType.FIELD_ADDED:
                field_type = detail.get("field_type", "text")
                field_name = detail.get("field_name", "")
                form_name = detail.get("form_name", "")
                result.regression_hints.append(
                    f"新增字段 {form_name}.{field_name}({field_type})："
                    f"建议添加必填校验、边界值、特殊字符测试"
                )

            elif ct == ChangeType.FORM_ADDED:
                form_name = detail.get("form_name", "")
                result.regression_hints.append(
                    f"新增表单 {form_name}：建议添加完整 CRUD 测试和字段级边界测试"
                )

            elif ct == ChangeType.BUTTON_ADDED:
                btn = detail.get("button", "")
                result.regression_hints.append(
                    f"新增按钮 [{btn}]：建议添加点击功能验证和连续操作测试"
                )

            elif ct == ChangeType.TABLE_ADDED:
                table_name = detail.get("table_name", "")
                result.regression_hints.append(
                    f"新增表格 {table_name}：建议添加分页、排序、搜索和空数据测试"
                )

            elif ct == ChangeType.TABLE_COLUMN_CHANGED:
                table_name = detail.get("table_name", "")
                added = detail.get("added", [])
                if added:
                    result.regression_hints.append(
                        f"表格 {table_name} 新增列 {','.join(added)}：建议验证数据展示和排序"
                    )

            elif ct == ChangeType.PROPERTY_CHANGED:
                prop = detail.get("property", "")
                new_val = detail.get("new")
                if prop == "has_file_upload" and new_val:
                    result.regression_hints.append(
                        "新增文件上传能力：建议添加文件类型验证、大小限制、恶意文件测试"
                    )
                elif prop == "auth_required" and not new_val:
                    result.regression_hints.append(
                        "取消登录要求：建议验证匿名访问安全性"
                    )
