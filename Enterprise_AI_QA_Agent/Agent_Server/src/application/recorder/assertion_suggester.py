"""断言建议生成器（P2-3）：基于 page_effect 与元素语义的规则建议。

建议规则（confidence 递减，评审按需采纳）：
- navigated_to equals（high）：动作触发页面跳转——最强可验证效果；
- DOM 观察（low）：固定响应协议记录了操作之后的变化，仅供评审；
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

        # The historical counter contains changes before this event. It cannot
        # establish a response to this operation. Never rewrite old drafts.
        response = effect.get("response")
        if (event.type == "page_response" and isinstance(response, dict)
                and response.get("protocol") == "recording-dom-response-v1"
                and response.get("association") == "temporal_after_actions"
                and response.get("observation_status") == "observed_changes"
                and type(response.get("mutation_count")) is int and response["mutation_count"] > 0
                and isinstance(response.get("elements"), list) and response["elements"]):
            scope = event.page.get("recording_scope", {})
            document_id = scope.get("document_id") if isinstance(scope, dict) else None
            local_seqs = response.get("action_local_seqs")
            if (not document_id or not isinstance(local_seqs, list) or not local_seqs
                    or any(type(seq) is not int or seq < 0 for seq in local_seqs)):
                continue
            actions = [item for item in events if item.type in {"click", "dblclick", "fill", "key", "submit", "scroll", "navigate", "file_change"}
                and isinstance(item.page.get("recording_scope"), dict)
                and item.page["recording_scope"].get("document_id") == document_id
                and item.page["recording_scope"].get("local_seq") in local_seqs and item.seq < event.seq]
            if len(actions) != len(set(local_seqs)):
                continue
            suggestions.append(
                (
                    3,
                    TestCaseAssertion(
                        kind="ui_dom_changed",
                        target="page.dom",
                        operator="changed",
                        expected=True,
                        description=(
                            f"[low] 录制条目{[item.seq for item in actions]}之后的观察窗口记录"
                            f"{response['mutation_count']}条DOM变更；仅为时间关联，需评审业务含义"
                            + ("，采集范围存在截断" if response.get("truncated") else "")
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
