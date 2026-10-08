"""断言建议生成器测试（P2-3 纯函数）。

覆盖：navigated_to equals（high）、旧dom_mutation_count不推导操作响应、
实际page_response及同文档关联提示（low）、末页 title contains（medium）、page_scan
interactive_count（low）、confidence 排序与截断、空事件流空建议，
以及 P2-2 集成（草稿断言 = 基线 + 建议）。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.application.recorder.assertion_suggester import suggest_assertions
from src.application.recorder.recording_case_draft_service import build_recording_draft_payload
from src.schemas.recording import RecorderEvent, RecordingSession


def _event(seq: int, etype: str, **overrides: Any) -> RecorderEvent:
    payload: dict[str, Any] = {
        "seq": seq,
        "type": etype,
        "page": {"url": "https://app.example.com/login", "title": ""},
        "target": None,
        "value": None,
        "page_effect": {},
    }
    payload.update(overrides)
    return RecorderEvent(**payload)


def _click_target() -> dict[str, Any]:
    return {"tag": "BUTTON", "locators": {"id": "submit", "text": "登 录"}}


# ---------------------------------------------------------------- 规则


def test_navigated_to_yields_high_confidence_url_assertion() -> None:
    events = [
        _event(0, "click", target=_click_target(),
               page_effect={"navigated_to": "https://app.example.com/home"})
    ]
    suggestions = suggest_assertions(events)
    assert suggestions
    first = suggestions[0]
    assert first.kind == "ui_url" and first.operator == "equals"
    assert first.expected == "https://app.example.com/home"
    assert first.description.startswith("[high]")
    assert "登 录" in first.description


def test_legacy_pre_action_mutation_count_does_not_prove_response() -> None:
    events = [
        _event(0, "click", target=_click_target(),
               page_effect={"dom_mutation_count": 12})
    ]
    suggestions = suggest_assertions(events)
    dom = [s for s in suggestions if s.kind == "ui_dom_changed"]
    assert not dom


def test_dom_mutation_below_threshold_not_suggested() -> None:
    events = [
        _event(0, "click", target=_click_target(),
               page_effect={"dom_mutation_count": 1})  # 低于阈值：可能是轮询噪声
    ]
    suggestions = suggest_assertions(events)
    assert all(s.kind != "ui_dom_changed" for s in suggestions)


def test_last_page_title_suggested_as_medium() -> None:
    events = [_event(0, "click", page={"url": "https://x/home", "title": "工作台"})]
    suggestions = suggest_assertions(events)
    titles = [s for s in suggestions if s.kind == "ui_title"]
    assert titles and titles[0].expected == "工作台"
    assert titles[0].description.startswith("[medium]")


def test_interactive_count_suggested_as_low() -> None:
    events = [
        _event(0, "page_scan", page={"url": "https://x/home", "title": "工作台"},
               page_effect={"interactive_count": 42, "dom_hash": "h"}),
    ]
    suggestions = suggest_assertions(events)
    counts = [s for s in suggestions if s.kind == "ui_element_count"]
    assert counts and counts[0].expected == 42
    assert counts[0].description.startswith("[low]")


# ---------------------------------------------------------------- 排序与边界


def test_suggestions_sorted_by_confidence_and_capped() -> None:
    # Historical pre-action counters cannot establish an operation's response.
    events = [
        _event(0, "click", target=_click_target(),
               page_effect={"navigated_to": "https://x/a", "dom_mutation_count": 5}),
        _event(1, "click", page={"url": "https://x/a", "title": "完成"},
               page_effect={"interactive_count": 9}),
    ]
    suggestions = suggest_assertions(events)
    assert [s.kind for s in suggestions] == ["ui_url", "ui_title", "ui_element_count"]
    assert [s.description.split("]")[0] + "]" for s in suggestions] == [
        "[high]", "[medium]", "[low]",
    ]

    # Six URL observations exceed the existing five-suggestion limit.
    long_events = [
        _event(i, "click", target=_click_target(),
               page_effect={"navigated_to": f"https://x/{c}", "dom_mutation_count": 5})
        for i, c in enumerate("abcdef")
    ]
    capped = suggest_assertions(long_events)
    assert len(capped) == 5
    assert all(item.kind == "ui_url" for item in capped)


def test_empty_events_yield_no_suggestions() -> None:
    assert suggest_assertions([]) == []


# ---------------------------------------------------------------- P2-2 集成


def test_draft_payload_merges_baseline_and_suggestions() -> None:
    session = RecordingSession(
        id=str(uuid4()), project_id="proj-1", name="登录",
        entry_url="https://app.example.com/login",
    )
    events = [
        _event(0, "click", target=_click_target(),
               page_effect={"navigated_to": "https://app.example.com/home",
                            "dom_mutation_count": 15}),
    ]
    payload = build_recording_draft_payload(session, events)
    kinds = [a.kind for a in payload.assertions]
    assert kinds[0] == "ui_url"  # 基线（必选）
    assert "ui_url" in kinds[1:] and "ui_dom_changed" not in kinds[1:]


def test_actual_recorded_response_yields_review_hint_with_same_document_actions():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-recording-response-python-fixed/cdp-attach/response-dom/pg-events.json"
    events = [RecorderEvent.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    hints = [item for item in suggest_assertions(events) if item.kind == "ui_dom_changed"]
    assert len(hints) == 1 and hints[0].description.startswith("[low]")
    assert "仅为时间关联" in hints[0].description
    response = next(item for item in events if item.type == "page_response")
    response.page["recording_scope"]["document_id"] = "another-document"
    assert not any(item.kind == "ui_dom_changed" for item in suggest_assertions(events))
