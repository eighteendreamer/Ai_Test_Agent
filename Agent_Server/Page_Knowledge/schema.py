"""
页面知识结构模型

三层知识体系：
  1. 页面级知识（URL 级）
  2. 功能级知识（登录模块等）
  3. 系统级知识（整站结构）

存储"抽象能力"，而不是原始 HTML
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional, Any


@dataclass
class FormField:
    """表单字段"""
    name: str
    field_type: str  # text / password / email / select / checkbox / radio / textarea / file ...
    label: str = ""
    required: bool = False
    placeholder: str = ""
    options: List[str] = field(default_factory=list)  # select/radio 的选项

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "FormField":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FormCapability:
    """表单能力"""
    name: str
    fields: List[FormField] = field(default_factory=list)
    submit_button: str = ""
    method: str = ""  # GET / POST
    action: str = ""  # form action URL

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["fields"] = [f.to_dict() for f in self.fields]
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "FormCapability":
        fields = [FormField.from_dict(f) for f in d.get("fields", [])]
        return cls(
            name=d.get("name", ""),
            fields=fields,
            submit_button=d.get("submit_button", ""),
            method=d.get("method", ""),
            action=d.get("action", ""),
        )


@dataclass
class TableCapability:
    """表格能力"""
    name: str = ""
    columns: List[str] = field(default_factory=list)
    has_pagination: bool = False
    has_search: bool = False
    has_sort: bool = False
    row_actions: List[str] = field(default_factory=list)  # 编辑/删除/查看 等

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "TableCapability":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PageKnowledge:
    """
    页面知识（核心数据结构）

    它代表系统对一个页面的完整"理解"。
    """
    # ── 基础标识 ──
    url: str
    page_title: str = ""
    page_type: str = ""            # login / list / detail / form / dashboard / mixed
    summary: str = ""              # 一句话描述页面能力
    description: str = ""          # 更详细的语义描述（用于 Embedding）

    # ── 能力结构 ──
    forms: List[FormCapability] = field(default_factory=list)
    tables: List[TableCapability] = field(default_factory=list)
    buttons: List[str] = field(default_factory=list)           # 独立按钮文本列表
    links: List[str] = field(default_factory=list)             # 重要链接/导航
    dynamic_elements: List[str] = field(default_factory=list)  # toast / modal / dropdown 等
    page_sections: List[str] = field(default_factory=list)     # 功能区域描述

    # ── 交互地图（递归探索产物）──
    interactions: List[Dict] = field(default_factory=list)     # [{name, element_type, selector, page_url, effects: [{effect_type, target_url, description}]}]
    parent_page: str = ""                                      # 从哪个页面导航过来的
    navigation_path: List[str] = field(default_factory=list)   # 从入口到当前页的导航路径
    child_pages: List[str] = field(default_factory=list)       # 可导航到的子页面 URL

    # ── 属性标记 ──
    planned_tasks: List[Dict] = field(default_factory=list)
    task_graph: Dict[str, Any] = field(default_factory=dict)
    task_summary: str = ""
    auth_required: bool = True
    has_file_upload: bool = False
    has_export: bool = False
    has_import: bool = False
    has_search: bool = False
    has_pagination: bool = False
    roles: List[str] = field(default_factory=list)
    security_surface: List[str] = field(default_factory=list)  # 安全攻击面

    # ── 版本控制 ──
    hash_signature: str = ""
    version: int = 1
    last_updated: str = ""
    last_accessed: str = ""

    # ── 元数据 ──
    domain: str = ""               # 站点域名
    module_name: str = ""          # 所属功能模块
    tags: List[str] = field(default_factory=list)

    def compute_hash(self) -> str:
        """
        计算页面结构签名

        基于：表单字段数、按钮数、表格列数、页面类型、标题
        不使用动态内容，变更敏感度适中
        """
        sig_parts = [
            self.page_type,
            self.page_title,
            str(len(self.forms)),
            str(sum(len(f.fields) for f in self.forms)),
            str(len(self.buttons)),
            str(len(self.tables)),
            str(sum(len(t.columns) for t in self.tables)),
            str(self.auth_required),
            str(self.has_file_upload),
            str(self.has_export),
            ",".join(sorted(str(b) for b in self.buttons[:20])),
            ",".join(sorted(str(s) for s in self.page_sections[:10])),
        ]
        raw = "|".join(sig_parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def refresh_hash(self):
        self.hash_signature = self.compute_hash()
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def build_embedding_text(self) -> str:
        """
        生成用于 Embedding 的语义摘要文本

        不要向量化整个 DOM —— 只向量化结构化描述
        """
        parts = []
        if self.page_title:
            parts.append(f"页面标题：{self.page_title}")
        if self.summary:
            parts.append(self.summary)
        if self.page_type:
            parts.append(f"页面类型：{self.page_type}")
        if self.forms:
            form_desc = ", ".join(
                f"{f.name}({len(f.fields)}个字段)" for f in self.forms
            )
            parts.append(f"表单：{form_desc}")
        if self.tables:
            table_desc = ", ".join(
                f"{t.name}({len(t.columns)}列)" for t in self.tables
            )
            parts.append(f"表格：{table_desc}")
        if self.buttons:
            btn_strs = [str(b) for b in self.buttons[:15]]
            parts.append(f"按钮：{', '.join(btn_strs)}")
        if self.auth_required:
            parts.append("需要登录认证")
        if self.has_file_upload:
            parts.append("支持文件上传")
        if self.has_export:
            parts.append("支持数据导出")
        if self.security_surface:
            parts.append(f"安全面：{', '.join(self.security_surface[:5])}")
        if self.description:
            parts.append(self.description)
        if self.interactions:
            interaction_names = [i.get("name", "") for i in self.interactions[:15] if isinstance(i, dict)]
            if interaction_names:
                parts.append(f"交互项：{', '.join(interaction_names)}")
        if self.planned_tasks:
            task_titles = [t.get("title", "") for t in self.planned_tasks[:12] if isinstance(t, dict)]
            if task_titles:
                parts.append(f"任务流：{', '.join(task_titles)}")
        if self.task_summary:
            parts.append(f"任务摘要：{self.task_summary}")
        return "；".join(parts)

    def to_dict(self) -> Dict:
        d = {
            "url": self.url,
            "page_title": self.page_title,
            "page_type": self.page_type,
            "summary": self.summary,
            "description": self.description,
            "forms": [f.to_dict() for f in self.forms],
            "tables": [t.to_dict() for t in self.tables],
            "buttons": self.buttons,
            "links": self.links,
            "dynamic_elements": self.dynamic_elements,
            "page_sections": self.page_sections,
            "interactions": self.interactions,
            "parent_page": self.parent_page,
            "navigation_path": self.navigation_path,
            "child_pages": self.child_pages,
            "planned_tasks": self.planned_tasks,
            "task_graph": self.task_graph,
            "task_summary": self.task_summary,
            "auth_required": self.auth_required,
            "has_file_upload": self.has_file_upload,
            "has_export": self.has_export,
            "has_import": self.has_import,
            "has_search": self.has_search,
            "has_pagination": self.has_pagination,
            "roles": self.roles,
            "security_surface": self.security_surface,
            "hash_signature": self.hash_signature,
            "version": self.version,
            "last_updated": self.last_updated,
            "last_accessed": self.last_accessed,
            "domain": self.domain,
            "module_name": self.module_name,
            "tags": self.tags,
        }
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "PageKnowledge":
        forms = [FormCapability.from_dict(f) for f in d.get("forms", [])]
        tables = [TableCapability.from_dict(t) for t in d.get("tables", [])]

        pk = cls(
            url=d.get("url", ""),
            page_title=d.get("page_title", ""),
            page_type=d.get("page_type", ""),
            summary=d.get("summary", ""),
            description=d.get("description", ""),
            forms=forms,
            tables=tables,
            buttons=d.get("buttons", []),
            links=d.get("links", []),
            dynamic_elements=d.get("dynamic_elements", []),
            page_sections=d.get("page_sections", []),
            interactions=d.get("interactions", []),
            parent_page=d.get("parent_page", ""),
            navigation_path=d.get("navigation_path", []),
            child_pages=d.get("child_pages", []),
            planned_tasks=d.get("planned_tasks", []),
            task_graph=d.get("task_graph", {}),
            task_summary=d.get("task_summary", ""),
            auth_required=d.get("auth_required", True),
            has_file_upload=d.get("has_file_upload", False),
            has_export=d.get("has_export", False),
            has_import=d.get("has_import", False),
            has_search=d.get("has_search", False),
            has_pagination=d.get("has_pagination", False),
            roles=d.get("roles", []),
            security_surface=d.get("security_surface", []),
            hash_signature=d.get("hash_signature", ""),
            version=d.get("version", 1),
            last_updated=d.get("last_updated", ""),
            last_accessed=d.get("last_accessed", ""),
            domain=d.get("domain", ""),
            module_name=d.get("module_name", ""),
            tags=d.get("tags", []),
        )
        return pk

    @classmethod
    def from_capabilities(cls, url: str, capabilities: Dict) -> "PageKnowledge":
        """
        从 OneClick_Test 已有的 page_capabilities（LLM 生成）构建

        兼容已有的页面能力抽象结果
        """
        forms = []
        for f in capabilities.get("forms", []):
            fields = []
            for fd in f.get("fields", []):
                fields.append(FormField(
                    name=fd.get("name", ""),
                    field_type=fd.get("type", fd.get("field_type", "text")),
                    label=fd.get("label", ""),
                    required=fd.get("required", False),
                    placeholder=fd.get("placeholder", ""),
                    options=fd.get("options", []),
                ))
            forms.append(FormCapability(
                name=f.get("name", ""),
                fields=fields,
                submit_button=f.get("submit_button", ""),
            ))

        tables = []
        for t in capabilities.get("tables", []):
            tables.append(TableCapability(
                name=t.get("name", ""),
                columns=t.get("columns", []),
                has_pagination=t.get("has_pagination", False),
                has_search=t.get("has_search", False),
                has_sort=t.get("has_sort", False),
                row_actions=t.get("row_actions", []),
            ))

        # 提取域名
        domain = ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc
        except Exception:
            pass

        # 处理 buttons：兼容字符串列表和字典列表
        buttons_raw = capabilities.get("buttons", [])
        buttons = []
        for btn in buttons_raw:
            if isinstance(btn, dict):
                # 字典格式：提取 name 字段
                buttons.append(btn.get("name", str(btn)))
            elif isinstance(btn, str):
                # 字符串格式：直接使用
                buttons.append(btn)
            else:
                # 其他类型：转为字符串
                buttons.append(str(btn))

        # 处理 page_sections：兼容字符串列表和字典列表
        sections_raw = capabilities.get("page_sections", [])
        page_sections = []
        for sec in sections_raw:
            if isinstance(sec, dict):
                # 字典格式：提取 section_name 或 name 字段
                page_sections.append(sec.get("section_name", sec.get("name", str(sec))))
            elif isinstance(sec, str):
                # 字符串格式：直接使用
                page_sections.append(sec)
            else:
                # 其他类型：转为字符串
                page_sections.append(str(sec))

        pk = cls(
            url=url,
            page_title=capabilities.get("page_title", ""),
            page_type=capabilities.get("page_type", "mixed"),
            summary=capabilities.get("summary", ""),
            description=capabilities.get("description", ""),
            forms=forms,
            tables=tables,
            buttons=buttons,
            links=capabilities.get("links", []),
            dynamic_elements=capabilities.get("dynamic_elements", []),
            page_sections=page_sections,
            planned_tasks=capabilities.get("planned_tasks", []),
            task_graph=capabilities.get("task_graph", {}),
            task_summary=capabilities.get("task_summary", ""),
            auth_required=capabilities.get("auth_required", True),
            has_file_upload=capabilities.get("has_file_upload", False),
            has_export=capabilities.get("has_export", False),
            has_import=capabilities.get("has_import", False),
            has_search=capabilities.get("has_search", False),
            has_pagination=capabilities.get("has_pagination", False),
            roles=capabilities.get("roles", []),
            security_surface=capabilities.get("security_surface", []),
            domain=domain,
            module_name=capabilities.get("module_name", ""),
            tags=capabilities.get("tags", []),
        )
        pk.refresh_hash()
        return pk

    @classmethod
    def from_interaction_map(cls, page_map) -> "PageKnowledge":
        """
        从 PageInteractionMap（递归探索产物）构建 PageKnowledge。

        Args:
            page_map: UI_Exploration.interaction_map.PageInteractionMap 实例
        """
        from urllib.parse import urlparse

        url = page_map.page_url
        domain = ""
        try:
            domain = urlparse(url).netloc
        except Exception:
            pass

        # 将 forms 从 raw dict 转换为 FormCapability
        forms = []
        for f in page_map.forms:
            fields = []
            for fd in f.get("fields", []):
                fields.append(FormField(
                    name=fd.get("name", ""),
                    field_type=fd.get("type", fd.get("field_type", "text")),
                    label=fd.get("label", ""),
                    required=fd.get("required", False),
                    placeholder=fd.get("placeholder", ""),
                    options=[opt.get("text", str(opt)) for opt in fd.get("options", [])],
                ))
            forms.append(FormCapability(
                name=f.get("name", f.get("id", "")),
                fields=fields,
                submit_button="",
                method=f.get("method", ""),
                action=f.get("action", ""),
            ))

        # 将 tables 从 raw dict 转换为 TableCapability
        tables = []
        for t in page_map.tables:
            tables.append(TableCapability(
                name=t.get("name", ""),
                columns=t.get("columns", []),
                has_pagination=t.get("has_pagination", False),
            ))

        # 提取按钮名称列表（从 interactions 中筛选 button 类型）
        buttons = [
            i.name for i in page_map.interactions
            if i.element_type == "button" and i.name
        ]

        # 提取链接
        links = [
            i.name for i in page_map.interactions
            if i.element_type == "link" and i.name
        ]

        # 将 interactions 序列化为 dict 列表
        interactions = [i.to_dict() for i in page_map.interactions]

        pk = cls(
            url=url,
            page_title=page_map.page_title,
            page_type=page_map.page_type,
            summary=page_map.summary,
            description=page_map.summary,
            forms=forms,
            tables=tables,
            buttons=buttons,
            links=links,
            interactions=interactions,
            parent_page=page_map.parent_page,
            navigation_path=page_map.navigation_path,
            child_pages=page_map.child_pages,
            planned_tasks=getattr(page_map, "planned_tasks", []),
            task_graph=getattr(page_map, "task_graph", {}),
            task_summary=getattr(page_map, "task_summary", ""),
            domain=domain,
        )
        pk.refresh_hash()
        return pk
