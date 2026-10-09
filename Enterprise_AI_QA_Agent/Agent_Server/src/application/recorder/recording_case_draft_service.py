"""录制事件流转用例草稿（P2-2，进既有「评审 → 固定版本 → 套件冻结」链路）。

转换规则（AGENTS 第七章治理要求）：
- 一次录制 → 一条用例草稿（draft 起步，走既有 submit_review → activate
  → 套件冻结链路，不新增状态机分支）；
- steps：可回放动作序列，action 为人话描述（「点击「登 录」按钮」），
  data 携带机器可回放载荷（event 原文 + 定位链摘要），评审可读、回放可执行；
- assertions：基线断言取末次导航目标（page_effect.navigated_to equals）；
  无导航时退化为「页面 title 包含」断言；再无则占位 manual_review 断言
  （评审必改，宁缺毋假）；
- source_refs：source_type=ui_recording + recording_id（可追溯硬要求）；
- model_key/prompt_version/skill_versions：规则转换非模型生成，记录规则
  引擎版本标识（生成来源证据三件套语义等价）。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from src.application.recorder.assertion_suggester import suggest_assertions
from src.application.recorder.replay_executor import _is_masked_value
from src.application.test_cases.case_store import DuplicateCaseKeyError
from src.application.test_cases.version_content import version_content_hash
from src.schemas.case_management import (
    TestCaseAssertion,
    TestCaseDraftCreateRequest,
    TestCaseSourceRef,
    TestCaseStep,
)
from src.schemas.recording import RecorderEvent, RecordingSession, masked_recording_event_payload

logger = logging.getLogger(__name__)

CONVERSION_RULE_KEY = "rule-based-recorder-conversion"
CONVERSION_RULE_VERSION = "p2-2.v2"
MODE_KEY_UI_AUTOMATION = "ui_automation"
CASE_TYPE_UI_RECORDED = "ui_recorded"

_REPLAYABLE = {"click", "dblclick", "fill", "key", "submit", "scroll", "navigate", "file_change"}


def _element_label(target: dict[str, Any] | None) -> str:
    """元素语义名：locators.text → role_name.name → attributes.placeholder → tag。"""
    if not isinstance(target, dict):
        return "页面"
    locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
    role_name = locators.get("role_name") if isinstance(locators.get("role_name"), dict) else {}
    attributes = target.get("attributes") if isinstance(target.get("attributes"), dict) else {}
    for candidate in (
        locators.get("text"),
        role_name.get("name"),
        attributes.get("placeholder"),
        attributes.get("aria-label"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return str(target.get("tag") or "元素").lower()


def _step_action_text(event: RecorderEvent) -> str:
    label = _element_label(event.target)
    if event.type in {"click", "dblclick"}:
        verb = "点击" if event.type == "click" else "双击"
        return f"{verb}「{label}」"
    if event.type == "fill":
        if _is_masked_value(event.value):
            return f"在「{label}」输入密码（已脱敏，评审时补充占位符）"
        return f"在「{label}」输入 {str(event.value)[:60]}"
    if event.type == "key":
        return f"按下按键 {str(event.value or '')[:20]}"
    if event.type == "submit":
        return f"提交表单「{label}」"
    if event.type == "scroll":
        return "滚动页面"
    if event.type == "navigate":
        return f"打开 {str((event.page or {}).get('url') or '')[:80]}"
    if event.type == "file_change":
        return f"在「{label}」选择文件（文件名仅记录，评审时确认）"
    return event.type


def _locator_summary(event: RecorderEvent) -> dict[str, Any]:
    """保留原定位摘要兼容，并携带完整脱敏事件供执行和评审。"""
    target = event.target if isinstance(event.target, dict) else {}
    locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
    return {
        "seq": event.seq,
        "recorder_type": event.type,
        "locators": {
            key: locators.get(key)
            for key in ("id", "testid", "role_name", "css", "xpath", "text")
            if locators.get(key)
        },
        "pixel": event.pixel,
        "sensitive": _is_masked_value(event.value),
        "event": event.model_dump(mode="json"),
    }


def _baseline_assertion(events: list[RecorderEvent]) -> TestCaseAssertion:
    """基线断言：末次 navigated_to equals > 末页 title contains > manual_review 占位。"""
    for event in reversed(events):
        effect = event.page_effect if isinstance(event.page_effect, dict) else {}
        navigated_to = str(effect.get("navigated_to") or "").strip()
        if navigated_to:
            return TestCaseAssertion(
                kind="ui_url",
                target="page.url",
                operator="equals",
                expected=navigated_to,
                description="录制结束时最后一次导航目标地址",
            )
    for event in reversed(events):
        title = str((event.page or {}).get("title") or "").strip()
        if title:
            return TestCaseAssertion(
                kind="ui_title",
                target="page.title",
                operator="contains",
                expected=title,
                description="录制末页标题（无导航目标时的退化基线）",
            )
    return TestCaseAssertion(
        kind="manual_review",
        target="page",
        operator="manual_review",
        expected=None,
        description="录制未捕获可自动化的断言依据，评审时必须补充",
    )


def build_recording_draft_payload(
    session: RecordingSession,
    events: list[RecorderEvent],
) -> TestCaseDraftCreateRequest:
    """录制会话+事件流 → 用例草稿载荷（纯函数）。

    空事件流/纯采集副产物（无 page_scan 之外可回放动作）由调用方前置校验。
    """
    steps: list[TestCaseStep] = []
    # The same write-side mask is used by graph knowledge and case conversion.
    # Action labels are only summaries; never make them the execution evidence.
    safe_events = [RecorderEvent.model_validate(masked_recording_event_payload(event)) for event in events]
    for event in safe_events:
        if event.type not in _REPLAYABLE:
            continue  # page_scan 等采集副产物不进步骤
        steps.append(
            TestCaseStep(
                order=len(steps) + 1,
                action=_step_action_text(event),
                expected=None,
                kind="ui_action",
                data=_locator_summary(event),
            )
        )
    return TestCaseDraftCreateRequest(
        case_key=f"ui_rec_{session.id[:12].replace('-', '_')}",
        title=session.name or f"录制用例 {session.entry_url[:60]}",
        mode_key=MODE_KEY_UI_AUTOMATION,
        case_type=CASE_TYPE_UI_RECORDED,
        priority="P1",
        preconditions=[f"入口地址 {session.entry_url}", "目标系统可访问且登录态可用"],
        steps=steps,
        # 断言 = 基线（必选，三级退化）+ 规则建议（P2-3，带 confidence 供评审筛选）
        assertions=[_baseline_assertion(safe_events), *suggest_assertions(safe_events)],
        execution_notes=("步骤 action 是显示摘要，执行时读取 data.event 的完整输入、定位和文档范围。"
            "test_data.recording_observations 是采集观察，不执行为操作；截断、无变化或未采集不能证明业务通过。"
            "脱敏输入与仅有文件名的记录需要可靠测试数据，不能恢复或猜测原值。"),
        test_data={"recording_id": session.id, "driver_kind": str(session.driver_kind),
            "recording_observations": [event.model_dump(mode="json") for event in safe_events if event.type not in _REPLAYABLE]},
        cleanup=[],
        source_refs=[
            TestCaseSourceRef(
                source_type="ui_recording",
                source_id=session.id,
                version=CONVERSION_RULE_VERSION,
                label=f"UI 录制 {session.name or session.entry_url[:80]}",
                metadata={"event_count": len(events), "step_count": len(steps),
                    "finalize_metrics": deepcopy(session.finalize_metrics)},
            )
        ],
        model_key=CONVERSION_RULE_KEY,
        prompt_version=CONVERSION_RULE_VERSION,
        skill_versions={"recorder": CONVERSION_RULE_VERSION},
    )


class RecordingCaseDraftService:
    """录制 → 用例草稿编排：取事件流 → 构造载荷 → 既有 create_draft 链路。"""

    def __init__(self, *, recorder_service: Any, test_case_service: Any) -> None:
        self._recorder_service = recorder_service
        self._test_case_service = test_case_service

    async def get_created_draft(self, recording_id: str, *, project_id: str | None = None) -> dict[str, Any] | None:
        """只读恢复原始转换版本；不读取当前模板或重建已保存内容。"""
        session = await self._recorder_service.get_session(recording_id)
        if session is None:
            raise ValueError(f"recording not found: {recording_id}")
        if project_id is not None and session.project_id != project_id:
            raise ValueError("recording does not belong to the given project")
        case_key = f"ui_rec_{session.id[:12].replace('-', '_')}"
        case = await self._test_case_service.get_case_by_key(session.project_id, case_key)
        if case is None:
            return None
        versions = await self._test_case_service.list_versions(case.id)
        original = next((version for version in versions if version.version == 1), None)
        if (original is None or case.project_id != session.project_id
            or original.case_id != case.id or original.test_data.get("recording_id") != recording_id
            or original.model_key != CONVERSION_RULE_KEY
            or not any(ref.source_type == "ui_recording" and ref.source_id == recording_id for ref in original.source_refs)
            or original.content_hash != version_content_hash(original)):
            logger.warning("recording_case_draft_recovery_rejected", extra={"recording_id": recording_id,
                "project_id": session.project_id, "case_id": case.id})
            raise ValueError("existing case key does not identify the original recording conversion")
        logger.info("recording_case_draft_recovered", extra={"recording_id": recording_id,
            "project_id": session.project_id, "case_id": case.id, "version_id": original.id})
        return {"case": case, "version": original, "recording_id": recording_id, "step_count": len(original.steps)}

    async def create_draft_from_recording(
        self,
        recording_id: str,
        *,
        project_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """按 recording_id 生成草稿；会话须为 completed（固化完成才有完整事件流）。"""
        existing = await self.get_created_draft(recording_id, project_id=project_id)
        if existing is not None:
            return existing
        session = await self._recorder_service.get_session(recording_id)
        if session is None:
            raise ValueError(f"recording not found: {recording_id}")
        if str(session.status) != "completed" and getattr(session.status, "value", session.status) != "completed":
            raise ValueError(
                f"recording not finalized (status={getattr(session.status, 'value', session.status)}); "
                "only completed recordings can convert to case drafts"
            )
        if project_id and session.project_id != project_id:
            raise ValueError("recording does not belong to the given project")

        events = await self._recorder_service.get_events(recording_id)
        replayable = [e for e in events if e.type in _REPLAYABLE]
        if not replayable:
            raise ValueError("recording has no replayable actions to convert")

        payload = build_recording_draft_payload(session, events)
        try:
            bundle = await self._test_case_service.create_draft(
                project_id=session.project_id, payload=payload, created_by=created_by,
            )
        except DuplicateCaseKeyError:
            # The existing project/key unique constraint elects the concurrent winner.
            existing = await self.get_created_draft(recording_id, project_id=project_id)
            if existing is None:
                raise
            return existing
        logger.info("recording_case_draft_created", extra={"recording_id": recording_id,
            "project_id": session.project_id, "case_id": bundle.case.id, "step_count": len(payload.steps),
            "observation_count": len(payload.test_data["recording_observations"]),
            "conversion_version": CONVERSION_RULE_VERSION})
        return {
            "case": bundle.case,
            "version": bundle.version,
            "recording_id": recording_id,
            "step_count": len(payload.steps),
        }
