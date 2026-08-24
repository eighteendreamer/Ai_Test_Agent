"""断言建议生成器（P2-3）：基于 page_effect 与元素语义的规则建议。

建议规则（confidence 递减，评审按需采纳）：
- navigated_to equals（high）：动作触发页面跳转——最强可验证效果；
- 操作后 DOM 变更（medium）：page_effect.dom_mutation_count > 0，页面有响应；
- 末页 title contains（medium）：弱于 URL 但稳定性好；
- page_scan interactive_count（low）：页面应保持 N 个可交互元素（易脆，仅参考）。

每条建议的 description 携带 [confidence] 与依据说明（评审可读可筛）；
纯函数无副作用，供 P2-2 草稿增强与后续 Agent 评审消费。
"""

from __future__ import annotations

from typing import Any

from src.schemas.case_management import TestCaseAssertion
from src.schemas.recording import RecorderEvent

# 单次录制最多采纳的建议数（防长录制断言爆炸，评审负担可控）
_MAX_SUGGESTIONS = 5

# dom_mutation_count 达到该值才建议 DOM 响应断言（微小变更可能是轮询噪声）
_DOM_MUTATION_THRESHOLD = 3


def _action_label(event: RecorderEvent) -> str:
    target = event.target if isinstance(event.target, dict) else {}
    locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
    role_name = locators.get("role_name") if isinstance(locators.get("role_name"), dict) else {}
    for candidate in (locators.get("text"), role_name.get("name")):
        text = str(candidate or "").strip()
        if text:
            return text
    return str(target.get("tag") or "元素").lower()


def suggest_assertions(events: list[RecorderEvent]) -> list[TestCaseAssertion]:
    """录制事件流 → 断言建议列表（纯函数，按置信度排序，截断 _MAX_SUGGESTIONS）。"""
    suggestions: list[tuple[int, TestCaseAssertion]] = []  # (优先级, 断言)

    for event in events:
        effect: dict[str, Any] = event.page_effect if isinstance(event.page_effect, dict) else {}
        navigated_to = str(effect.get("navigated_to") or "").strip()
        if navigated_to:
            label = _action_label(event) if event.target else "导航"
            suggestions.append(
                (
                    0,
                    TestCaseAssertion(
                        kind="ui_url",
                        target="page.url",
                        operator="equals",
                        expected=navigated_to,
                        description=f"[high] {event.type}「{label}」后页面跳转到该地址（录制实测依据）",
                    ),
                )
            )

        mutation = int(effect.get("dom_mutation_count") or 0)
        if mutation >= _DOM_MUTATION_THRESHOLD and event.target is not None:
            label = _action_label(event)
            suggestions.append(
                (
                    1,
                    TestCaseAssertion(
                        kind="ui_dom_changed",
                        target="page.dom",
                        operator="changed",
                        expected=True,
                        description=(
                            f"[medium] {event.type}「{label}」后页面 DOM 发生 {mutation} 处变更"
                            "（操作产生了可见响应）"
                        ),
                    ),
                )
            )

    for event in reversed(events):
        title = str((event.page or {}).get("title") or "").strip()
        if title:
            suggestions.append(
                (
                    2,
                    TestCaseAssertion(
                        kind="ui_title",
                        target="page.title",
                        operator="contains",
                        expected=title,
                        description=f"[medium] 录制末页标题包含「{title}」（弱于 URL 断言）",
                    ),
                )
            )
            break

    for event in reversed(events):
        effect = event.page_effect if isinstance(event.page_effect, dict) else {}
        count = int(effect.get("interactive_count") or 0)
        if count > 0:
            suggestions.append(
                (
                    3,
                    TestCaseAssertion(
                        kind="ui_element_count",
                        target="page.interactive_elements",
                        operator="gte",
                        expected=count,
                        description=(
                            f"[low] 页面扫描捕获 {count} 个可交互元素（易脆，页面改版即失效，仅参考）"
                        ),
                    ),
                )
            )
            break

    suggestions.sort(key=lambda pair: pair[0])
    return [assertion for _, assertion in suggestions[:_MAX_SUGGESTIONS]]
