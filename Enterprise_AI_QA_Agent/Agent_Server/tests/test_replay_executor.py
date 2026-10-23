"""回放执行器计划构建单测（P2-1 纯函数部分，不连浏览器）。

覆盖：事件类型过滤（page_scan/未知 skip）、file_change skip（无文件实体）、
脱敏 fill skip（安全红线）、普通 DOM 定位策略链（id→testid→role_name→css→xpath
→geometry）、Canvas 坐标兜底、geometry 锚点计算、
报告汇总（success_rate/passed/failed/skipped）。
"""

from __future__ import annotations

from src.application.recorder.replay_executor import (
    RecordingReplayExecutor,
    ReplayReport,
    ReplayStep,
    ReplayStepResult,
    build_replay_plan,
)
import asyncio
import pytest
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
    assert chain == ["id", "testid", "role_name", "css", "xpath", "geometry"]


def test_viewport_point_is_reserved_for_canvas_targets() -> None:
    target = {"tag": "CANVAS", "locators": {}, "attributes": {}}
    step = build_replay_plan([_event(0, "click", target=target,
        pixel={"viewport_point": {"x": 100, "y": 200}})])[0]
    assert [item["strategy"] for item in step.strategies] == ["viewport_point"]


def test_role_without_name_is_not_a_stable_identity() -> None:
    class Handle:
        async def evaluate(self, _expression):
            return {"tag": "BUTTON", "role": "button", "name": "", "text": "", "attributes": {}}

    async def scenario():
        executor = RecordingReplayExecutor()
        with pytest.raises(RuntimeError, match="does not match recorded target semantics"):
            await executor._verify_candidate(Handle(), {
                "tag": "BUTTON", "role": "button",
                "locators": {"role_name": {"role": "button", "name": None}, "text": None},
                "attributes": {},
            }, "role_name")

    asyncio.run(scenario())


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


def test_recorded_scroll_coordinate_names_are_executed() -> None:
    class Page:
        argument = None

        async def evaluate(self, _script, argument):
            self.argument = argument

    async def scenario():
        page = Page()
        result = await RecordingReplayExecutor()._run_step(page, ReplayStep(
            seq=7, action="scroll", detail={"value": {"scroll_top": 321, "scroll_left": 45}}))
        assert result.status == "passed" and result.strategy == "scroll_to"
        assert page.argument == {"top": 321, "left": 45}

    asyncio.run(scenario())


def test_checkpoint_resume_requires_read_only_proof_for_business_actions() -> None:
    class Page:
        url = "https://example.test/"

        async def evaluate(self, _script, _argument=None):
            return {"top": 0, "left": 0}

    async def scenario():
        executor = RecordingReplayExecutor()
        page = Page()
        click = ReplayStep(seq=4, action="click", strategies=[])
        assert await executor._resume_decision(page, click, {"status": "passed"}) == "unknown"

        scroll = ReplayStep(seq=5, action="scroll", detail={"value": {"top": 0, "left": 0}})
        assert await executor._resume_decision(page, scroll, {"status": "passed"}) == "verified"

    asyncio.run(scenario())


def test_checkpoint_payload_marks_action_before_and_after_execution() -> None:
    class Page:
        url = "https://example.test/"

        async def goto(self, url, wait_until=None):
            self.url = url

        async def evaluate(self, _script, argument=None):
            return None

    class Browser:
        async def new_context(self):
            return self

        async def new_page(self):
            return Page()

        async def close(self):
            return None

    class Chromium:
        async def launch(self, headless=True):
            return Browser()

    class Playwright:
        chromium = Chromium()

        async def stop(self):
            return None

    async def scenario():
        executor = RecordingReplayExecutor()
        executor._start_playwright = lambda: _resolved(Playwright())
        checkpoints = []

        async def save(value):
            checkpoints.append(value)

        result = await executor.execute_with_checkpoint(
            recording_id="rec-1", entry_url="https://example.test/",
            events=[_event(0, "scroll", value={"top": 20})],
            checkpoint=None, checkpoint_callback=save,
        )
        assert result.steps[0].status == "passed"
        assert checkpoints[0]["in_progress"] == {"seq": 0, "action": "scroll"}
        assert checkpoints[-1]["in_progress"] is None
        assert checkpoints[-1]["steps"]["0"]["status"] == "passed"

        recovered = RecordingReplayExecutor()
        recovered._start_playwright = lambda: _resolved(Playwright())
        result = await recovered.execute_with_checkpoint(
            recording_id="rec-2", entry_url="https://example.test/",
            events=[_event(0, "click", target={"tag": "BUTTON", "locators": {"id": "save"}})],
            checkpoint={"version": 1, "steps": {}, "in_progress": {"seq": 0, "action": "click"}},
            checkpoint_callback=save,
        )
        assert result.recovery_required["reason"] == "recovery_unknown_side_effect"
        assert result.steps[0].status == "failed"

    async def _run():
        await scenario()

    asyncio.run(_run())


async def _resolved(value):
    return value
