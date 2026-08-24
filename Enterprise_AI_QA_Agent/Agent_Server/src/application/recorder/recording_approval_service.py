"""UI 录制审批编排（方案 4.2 环节④/⑤，P0-8）。

复用既有审批流（ToolApprovalRequest + session store），审批 metadata 携带
``approval_type="ui_recording"`` 与录制启动载荷；前端 ApprovalPanel 按
pending_approvals 既有链路展示审批卡片。

决策回调（由 SessionService.resolve_approval 的 ui_recording 分支委托）：
- approved → ``RecorderSessionService.launch(...)`` 创建录制会话（launching），
  推送 ``recorder.launch_requested`` 事件（前端据此弹录制窗口，方案 4.2⑤）；
- denied → 推送 ``recorder.approval_declined`` 事件（前端提示降级选项）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.schemas.recording import RecordingCreateRequest
from src.schemas.session import ExecutionEvent, ToolApprovalRequest, ToolApprovalStatus

logger = logging.getLogger(__name__)

APPROVAL_TYPE_UI_RECORDING = "ui_recording"
# 审批挂在公开入口工具名下（mode manifest public_entry_tool_key）
UI_RECORDING_TOOL_KEY = "ui-automation-runner"
UI_RECORDING_TOOL_NAME = "UI自动化模式"


class RecordingApprovalService:
    """ui_recording 审批的创建与决策回调（后端权威，前端按钮只是发令枪）。"""

    def __init__(self, *, recorder_service: Any, session_store: Any) -> None:
        self._recorder_service = recorder_service
        self._session_store = session_store

    # ------------------------------------------------------------ 创建审批

    async def create_approval(
        self,
        *,
        session_id: str,
        turn_id: str,
        request: Any,
        knowledge_gate: dict[str, Any],
    ) -> ToolApprovalRequest:
        """三源不足时创建录制审批并落库；返回审批记录（含 approval_id）。"""
        recording_request = {
            "project_id": str(request.project_id),
            "entry_url": str(request.target_url),
            "session_id": session_id,
            "driver": {"kind": "embedded"},
        }
        reason = (
            "当前项目缺少可复用的 UI 测试资源（用例 / 元素图谱 / 语义记忆均不足），"
            "建议启动浏览器录制以构建元素图谱。"
        )
        approval = ToolApprovalRequest(
            id=str(uuid4()),
            session_id=session_id,
            tool_key=UI_RECORDING_TOOL_KEY,
            tool_name=UI_RECORDING_TOOL_NAME,
            reason=reason,
            created_at=datetime.now(timezone.utc),
            metadata={
                "approval_type": APPROVAL_TYPE_UI_RECORDING,
                "turn_id": turn_id,
                "recording_request": recording_request,
                "knowledge_gate": knowledge_gate,
                "permission_behavior": "ask",
                "permission_source": "ui_automation_recording",
                "permission_reason": reason,
                "permission_visibility": "visible",
                "permission_reason_code": "ui_recording_required",
                "permission_policy_key": "ui_automation.recording",
            },
        )
        await self._session_store.save_approval(session_id, approval)
        await self._append_event(
            session_id,
            "approval.created",
            {
                "approval_id": approval.id,
                "tool_key": approval.tool_key,
                "approval_type": APPROVAL_TYPE_UI_RECORDING,
                "recording_request": recording_request,
                "knowledge_gate": knowledge_gate,
                "message": "UI recording approval created.",
            },
        )
        logger.info(
            "ui recording approval created: session_id=%s approval_id=%s project_id=%s entry_url=%s",
            session_id,
            approval.id,
            recording_request["project_id"],
            recording_request["entry_url"],
        )
        return approval

    # ------------------------------------------------------------ 决策回调

    async def apply_decision(
        self,
        approval: ToolApprovalRequest,
        *,
        decision: ToolApprovalStatus,
        reason: str | None,
    ) -> dict[str, Any]:
        """审批决策落地：approved → launch 录制会话；denied → 记录降级事件。"""
        metadata = approval.metadata or {}
        if metadata.get("approval_type") != APPROVAL_TYPE_UI_RECORDING:
            raise ValueError("approval is not a ui_recording approval")

        if decision is not ToolApprovalStatus.approved:
            logger.info(
                "ui recording approval declined: session_id=%s approval_id=%s",
                approval.session_id,
                approval.id,
            )
            await self._append_event(
                approval.session_id,
                "recorder.approval_declined",
                {
                    "approval_id": approval.id,
                    "decision": decision.value,
                    "reason": reason or "",
                    "message": "UI 录制审批被拒绝，可选择回退 AI 页面探索。",
                },
            )
            return {"status": "declined", "approval_id": approval.id}

        request_payload = dict(metadata.get("recording_request") or {})
        create_request = RecordingCreateRequest(
            project_id=str(request_payload.get("project_id") or ""),
            name=str(request_payload.get("name") or ""),
            entry_url=str(request_payload.get("entry_url") or ""),
            session_id=approval.session_id,
            approval_id=approval.id,
        )
        session = await self._recorder_service.launch(create_request)
        await self._append_event(
            approval.session_id,
            "recorder.launch_requested",
            {
                "approval_id": approval.id,
                "recording_id": session.id,
                "project_id": session.project_id,
                "entry_url": session.entry_url,
                "driver_kind": session.driver_kind.value
                if hasattr(session.driver_kind, "value")
                else str(session.driver_kind),
                "message": "录制会话已创建，等待桌面端打开录制窗口。",
            },
        )
        logger.info(
            "ui recording launched after approval: session_id=%s approval_id=%s recording_id=%s",
            approval.session_id,
            approval.id,
            session.id,
        )
        return {
            "status": "launched",
            "approval_id": approval.id,
            "recording_id": session.id,
        }

    # ------------------------------------------------------------ 内部

    async def _append_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = ExecutionEvent(
            type=event_type,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            payload=payload,
        )
        await self._session_store.append_event(session_id, event)
