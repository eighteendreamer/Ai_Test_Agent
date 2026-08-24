"""回放执行器计划构建单测（P2-1 纯函数部分，不连浏览器）。

覆盖：事件类型过滤（page_scan/未知 skip）、file_change skip（无文件实体）、
脱敏 fill skip（安全红线）、定位策略链顺序（id→testid→role_name→css→xpath
→geometry(bbox+rel_offset)→viewport_point）、geometry 锚点计算、
报告汇总（success_rate/passed/failed/skipped）。
"""

from __future__ import annotations

from src.application.recorder.replay_executor import (
    ReplayReport,
    ReplayStepResult,
    build_replay_plan,
)
from src.schemas.recording import RecorderEvent


def _event(seq: int, etype: str, **overrides) -> RecorderEvent:
    payload = {
        "seq": seq,
        "type": etype,
        "page": {"url": "https://app.example.com/login"},
        "target": None,
        "value": None,
        "page_effect": {},
    }
    payload.update(overrides)
    return RecorderEvent(**payload)


def _full_target() -> dict:
    return {
        "tag": "BUTTON",
        "role": "button",
        "locators": {
            "id": "login-submit",
            "testid": "submit-btn",
            "role_name": {"role": "button", "name": "登 录"},
            "css": "form > button.primary",
            "xpath": "/html/body/form/button[1]",
            "text": "登 录",
        },
    }


# ---------------------------------------------------------------- 类型过滤


def test_page_scan_and_unknown_types_are_skipped() -> None:
    events = [_event(0, "page_scan"), _event(1, "hover_mystery")]
    steps = build_replay_plan(events)
    assert all(s.skip_reason == "not_replayable" for s in steps)
    assert [s.action for s in steps] == ["page_scan", "hover_mystery"]


def test_file_change_skipped_without_file_payload() -> None:
    steps = build_replay_plan([_event(0, "file_change", value=["a.pdf"])])
    assert steps[0].skip_reason == "file_unavailable"


def test_masked_fill_is_skipped_for_security() -> None:
    steps = build_replay_plan(
        [_event(0, "fill", target=_full_target(), value={"length": 9})]
    )
    assert steps[0].skip_reason == "sensitive_value_masked"


def test_plain_fill_is_replayable_with_value() -> None:
    steps = build_replay_plan(
        [_event(0, "fill", target=_full_target(), value="alice")]
    )
    assert steps[0].skip_reason is None
    assert steps[0].detail["value"] == "alice"


# ---------------------------------------------------------------- 策略链


def test_locator_strategy_chain_order() -> None:
    steps = build_replay_plan(
        [
            _event(
                0,
                "click",
                target=_full_target(),
                pixel={
                    "viewport_point": {"x": 100, "y": 200},
                    "bbox": {"x": 80, "y": 190, "w": 40, "h": 20},
                    "rel_offset": {"rx": 0.5, "ry": 0.5},
                },
            )
        ]
    )
    chain = [s["strategy"] for s in steps[0].strategies]
    assert chain == ["id", "testid", "role_name", "css", "xpath", "geometry", "viewport_point"]


def test_partial_locators_yield_only_available_strategies() -> None:
    target = {"tag": "A", "locators": {"testid": "link-1", "text": "详情"}}
    steps = build_replay_plan([_event(0, "click", target=target)])
    chain = [s["strategy"] for s in steps[0].strategies]
    assert chain == ["testid"]  # 无像素 → 无 geometry/viewport_point 兜底


def test_geometry_anchor_uses_bbox_plus_rel_offset() -> None:
    pixel = {
        "bbox": {"x": 640, "y": 488, "w": 144, "h": 30},
        "rel_offset": {"rx": 0.5, "ry": 0.5},
    }
    steps = build_replay_plan([_event(0, "click", target=_full_target(), pixel=pixel)])
    geometry = next(s for s in steps[0].strategies if s["strategy"] == "geometry")
    assert geometry["x"] == 640 + 72
    assert geometry["y"] == 488 + 15


def test_navigate_step_carries_url() -> None:
    steps = build_replay_plan([_event(0, "navigate", page={"url": "https://app.example.com/home"})])
    assert steps[0].detail["url"] == "https://app.example.com/home"
    assert steps[0].skip_reason is None


# ---------------------------------------------------------------- 报告汇总


def test_report_summary_counts_and_success_rate() -> None:
    report = ReplayReport(recording_id="rec-1", entry_url="https://x")
    report.steps = [
        ReplayStepResult(seq=0, action="click", strategy="id", status="passed"),
        ReplayStepResult(seq=1, action="fill", strategy="css", status="passed"),
        ReplayStepResult(seq=2, action="fill", status="skipped", error="sensitive_value_masked"),
        ReplayStepResult(seq=3, action="click", status="failed", error="all strategies exhausted"),
    ]
    assert report.total == 4
    assert (report.passed, report.failed, report.skipped) == (2, 1, 1)
    assert report.success_rate == 0.5
    payload = report.to_dict()
    assert payload["summary"] == {"total": 4, "passed": 2, "failed": 1, "skipped": 1,
                                  "success_rate": 0.5}
    assert len(payload["steps"]) == 4


def test_empty_plan_success_rate_zero_division_safe() -> None:
    report = ReplayReport(recording_id="rec-1", entry_url="https://x")
    assert report.success_rate == 0.0
