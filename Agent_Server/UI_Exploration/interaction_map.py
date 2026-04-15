"""
交互地图数据结构

定义页面级递归探索的核心数据模型：
  - InteractionEffect: 单个交互效果
  - InteractionItem:   单个可交互元素及其效果
  - PageInteractionMap: 单页交互地图
  - SiteExplorationResult: 整站探索结果

所有数据按「页面 → 交互项 → 效果」三层组织。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional, Any


# ═══════════════════════════════════════════════════════════════════
# 交互效果
# ═══════════════════════════════════════════════════════════════════

@dataclass
class InteractionEffect:
    """交互操作产生的效果"""
    effect_type: str             # navigate / modal / toast / content_change / dropdown / tab_switch / download / none / error
    target_url: str = ""         # navigate 类型时的目标 URL
    description: str = ""        # 效果描述
    new_elements: List[str] = field(default_factory=list)  # 效果触发后出现的新元素

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "InteractionEffect":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ═══════════════════════════════════════════════════════════════════
# 交互项
# ═══════════════════════════════════════════════════════════════════

@dataclass
class InteractionItem:
    """单个可交互元素"""
    name: str                              # 元素显示文本
    element_type: str                      # button / link / tab / menu_item / icon / input / select
    selector: str                          # CSS/XPath 选择器
    page_url: str                          # 所在页面 URL
    location: str = ""                     # 元素在页面中的位置描述（如"顶部导航栏"）
    is_navigation: bool = False            # 是否为导航型元素（会跳转页面）
    is_dangerous: bool = False             # 是否为危险操作（删除/提交等）
    effects: List[InteractionEffect] = field(default_factory=list)
    probed: bool = False                   # 是否已探测过效果

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["effects"] = [e.to_dict() for e in self.effects]
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "InteractionItem":
        effects = [InteractionEffect.from_dict(e) for e in d.get("effects", [])]
        return cls(
            name=d.get("name", ""),
            element_type=d.get("element_type", ""),
            selector=d.get("selector", ""),
            page_url=d.get("page_url", ""),
            location=d.get("location", ""),
            is_navigation=d.get("is_navigation", False),
            is_dangerous=d.get("is_dangerous", False),
            effects=effects,
            probed=d.get("probed", False),
        )


# ═══════════════════════════════════════════════════════════════════
# 单页交互地图
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PageInteractionMap:
    """单页交互地图：一个 URL 的完整交互信息"""
    page_url: str
    page_title: str = ""
    page_type: str = ""                    # login / list / detail / form / dashboard / mixed
    parent_page: str = ""                  # 从哪个页面导航过来的
    navigation_path: List[str] = field(default_factory=list)  # 从入口到当前页的导航路径
    child_pages: List[str] = field(default_factory=list)       # 可导航到的子页面 URL
    interactions: List[InteractionItem] = field(default_factory=list)
    forms: List[Dict] = field(default_factory=list)            # 表单能力（复用现有结构）
    tables: List[Dict] = field(default_factory=list)           # 表格能力
    summary: str = ""
    explored_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def interaction_count(self) -> int:
        return len(self.interactions)

    @property
    def navigation_items(self) -> List[InteractionItem]:
        return [i for i in self.interactions if i.is_navigation]

    @property
    def action_items(self) -> List[InteractionItem]:
        return [i for i in self.interactions if not i.is_navigation and not i.is_dangerous]

    def to_dict(self) -> Dict:
        return {
            "page_url": self.page_url,
            "page_title": self.page_title,
            "page_type": self.page_type,
            "parent_page": self.parent_page,
            "navigation_path": self.navigation_path,
            "child_pages": self.child_pages,
            "interactions": [i.to_dict() for i in self.interactions],
            "forms": self.forms,
            "tables": self.tables,
            "summary": self.summary,
            "explored_at": self.explored_at,
            "interaction_count": self.interaction_count,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "PageInteractionMap":
        interactions = [InteractionItem.from_dict(i) for i in d.get("interactions", [])]
        return cls(
            page_url=d.get("page_url", ""),
            page_title=d.get("page_title", ""),
            page_type=d.get("page_type", ""),
            parent_page=d.get("parent_page", ""),
            navigation_path=d.get("navigation_path", []),
            child_pages=d.get("child_pages", []),
            interactions=interactions,
            forms=d.get("forms", []),
            tables=d.get("tables", []),
            summary=d.get("summary", ""),
            explored_at=d.get("explored_at", ""),
        )


# ═══════════════════════════════════════════════════════════════════
# 整站探索结果
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SiteExplorationResult:
    """整站探索最终产物"""
    entry_url: str
    pages: List[PageInteractionMap] = field(default_factory=list)
    navigation_graph: Dict[str, List[str]] = field(default_factory=dict)
    total_pages: int = 0
    total_interactions: int = 0
    duration_seconds: float = 0
    summary: str = ""

    def build_navigation_graph(self):
        """从页面列表构建导航关系图"""
        self.navigation_graph = {}
        for page in self.pages:
            self.navigation_graph[page.page_url] = page.child_pages
        self.total_pages = len(self.pages)
        self.total_interactions = sum(p.interaction_count for p in self.pages)

    def to_dict(self) -> Dict:
        return {
            "entry_url": self.entry_url,
            "pages": [p.to_dict() for p in self.pages],
            "navigation_graph": self.navigation_graph,
            "total_pages": self.total_pages,
            "total_interactions": self.total_interactions,
            "duration_seconds": round(self.duration_seconds, 1),
            "summary": self.summary,
        }

    def to_legacy_format(self) -> Dict:
        """
        转换为旧版 explorer.py 的输出格式，保持下游兼容。

        旧格式：
          { page_url, page_title, page_type, modules, navigation, forms, tables,
            key_interactions, pages_explored, summary }
        """
        if not self.pages:
            return {
                "page_url": self.entry_url,
                "page_title": "",
                "page_type": "unknown",
                "modules": [],
                "navigation": {},
                "forms": [],
                "tables": [],
                "key_interactions": [],
                "pages_explored": [],
                "summary": self.summary or "无探索结果",
            }

        # 将每个页面映射为一个 module
        modules = []
        all_forms = []
        all_tables = []
        key_interactions = []
        pages_explored = []

        for page in self.pages:
            pages_explored.append(page.page_url)

            # 每页 → 一个 module
            elements = []
            for item in page.interactions:
                effect_desc = ""
                if item.effects:
                    effect_parts = []
                    for eff in item.effects:
                        if eff.effect_type == "navigate":
                            effect_parts.append(f"导航到 {eff.target_url}")
                        elif eff.effect_type != "none":
                            effect_parts.append(eff.description or eff.effect_type)
                    effect_desc = "; ".join(effect_parts) if effect_parts else "无明显效果"
                else:
                    effect_desc = "未探测"

                elements.append({
                    "name": item.name,
                    "type": item.element_type,
                    "selector": item.selector,
                    "attributes": {
                        "is_navigation": item.is_navigation,
                        "is_dangerous": item.is_dangerous,
                        "location": item.location,
                    },
                    "behavior": {
                        "action": "click" if item.element_type in ("button", "link", "tab", "menu_item") else "input",
                        "effect": effect_desc,
                    },
                })

                # 有实际效果的交互记入 key_interactions
                if item.probed and item.effects:
                    for eff in item.effects:
                        if eff.effect_type != "none":
                            key_interactions.append({
                                "step": f"在 {page.page_title or page.page_url} 点击 {item.name}",
                                "purpose": f"触发 {item.element_type} 交互",
                                "result": eff.description or eff.effect_type,
                            })

            modules.append({
                "module_name": page.page_title or page.page_url,
                "page_url": page.page_url,
                "description": page.summary,
                "elements": elements,
            })

            all_forms.extend(page.forms)
            all_tables.extend(page.tables)

        first_page = self.pages[0]
        return {
            "page_url": self.entry_url,
            "page_title": first_page.page_title,
            "page_type": first_page.page_type or "mixed",
            "modules": modules,
            "navigation": self.navigation_graph,
            "forms": all_forms,
            "tables": all_tables,
            "key_interactions": key_interactions[:30],
            "pages_explored": pages_explored,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "SiteExplorationResult":
        pages = [PageInteractionMap.from_dict(p) for p in d.get("pages", [])]
        result = cls(
            entry_url=d.get("entry_url", ""),
            pages=pages,
            navigation_graph=d.get("navigation_graph", {}),
            total_pages=d.get("total_pages", len(pages)),
            total_interactions=d.get("total_interactions", 0),
            duration_seconds=d.get("duration_seconds", 0),
            summary=d.get("summary", ""),
        )
        return result
