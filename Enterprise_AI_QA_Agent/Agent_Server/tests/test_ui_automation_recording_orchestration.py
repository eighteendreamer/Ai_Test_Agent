"""UIAutomationModeRuntime 录制编排分支单测（方案 4.1 状态机，P0-8）。

覆盖：缺 project_id 反问（附候选项目）、三源充分 → task_generation_ready、
三源不足 → 发起录制审批（awaiting_recording_approval）、审批服务未注入 →
降级既有 AI 探索分支（awaiting_exploration_selection）、用户显式选方向时
直走探索（不破坏既有交互）、assessor 未注入时回退 memory-only 评估。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.application.recorder.ui_resource_assessor import UIResourceAssessor
from src.modes.ui_automation_mode.runtime import UIAutomationModeRuntime


@dataclass
class _Context:
    session_id: str = "session-1"
    turn_id: str = "turn-1"
    trace_id: str = "trace-1"
    user_message: str = "测试 https://example.com 登录流程"
    normalized_input: str = ""
    context_bundle: dict[str, Any] = field(default_factory=dict)


class _FakeMemgraph:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def initialize(self) -> None: ...

    def execute(self, query: str, parameters: dict | None = None) -> list[dict]:
        return self.rows


class _FakeApprovalService:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []

    async def create_approval(self, *, session_id: str, turn_id: str, request, knowledge_gate):
        self.create_calls.append(
            {"session_id": session_id, "turn_id": turn_id, "request": request, "knowledge_gate": knowledge_gate}
        )
        from src.schemas.session import ToolApprovalRequest
        from datetime import datetime, timezone

        return ToolApprovalRequest(
            id="appr-1",
            session_id=session_id,
            tool_key="ui-automation-runner",
            tool_name="UI自动化模式",
            reason="r",
            created_at=datetime.now(timezone.utc),
            metadata={
                "approval_type": "ui_recording",
                "recording_request": {"project_id": request.project_id, "entry_url": request.target_url},
            },
        )


def _runtime(
    *,
    graph_rows: list[dict] | None = None,
    approval_service: Any | None = None,
    project_catalog: Any | None = None,
) -> UIAutomationModeRuntime:
    assessor = UIResourceAssessor(
        memgraph_provider=_FakeMemgraph(graph_rows or [{"page_count": 0, "element_count": 0, "action_count": 0}]),
        test_case_service=None,
        memory_runtime_service=None,
    )
    runtime = UIAutomationModeRuntime(
        resource_assessor=assessor,
        recording_approval_service=approval_service,
        project_catalog_provider=project_catalog,
    )
    return runtime


def _handle(runtime: UIAutomationModeRuntime, arguments: dict | None = None) -> dict:
    return asyncio.run(runtime.handle(arguments or {}, _Context()))


EMPTY_GRAPH = [{"page_count": 0, "element_count": 0, "action_count": 0}]
RICH_GRAPH = [{"page_count": 3, "element_count": 50, "action_count": 8}]


# ------------------------------------------------------- 环节② 项目反问


def test_missing_project_id_asks_for_project_with_candidates():
    async def catalog() -> list[dict]:
        return [{"project_id": "proj-1", "name": "示例项目"}, {"project_id": "proj-2", "name": "商城"}]

    runtime = _runtime(project_catalog=catalog)

    result = _handle(runtime, {"target_url": "https://example.com"})

    state = result["ui_automation_state"]
    assert state["phase"] == "awaiting_project_selection"
    candidates = state["project_candidates"]
    assert [c["project_id"] for c in candidates] == ["proj-1", "proj-2"]
    assert state["knowledge_gate"]["reason"] == "project_id_required"


def test_missing_project_id_without_catalog_returns_empty_candidates():
    runtime = _runtime()

    result = _handle(runtime, {"target_url": "https://example.com", "project_id": ""})

    assert result["ui_automation_state"]["phase"] == "awaiting_project_selection"
    assert result["ui_automation_state"]["project_candidates"] == []


# ------------------------------------------------------- 环节③ 三源检索


def test_sufficient_resources_short_circuits_to_task_generation_ready():
    runtime = _runtime(graph_rows=RICH_GRAPH)

    result = _handle(runtime, {"project_id": "proj-1", "target_url": "https://example.com"})

    assert result["ui_automation_state"]["phase"] == "task_generation_ready"
    # 三源判定理由保留（审计可见），不再是硬编码 knowledge_sufficient
    assert result["ui_automation_state"]["knowledge_gate"]["reason"] == "graph_coverage_sufficient"


def test_insufficient_resources_creates_recording_approval():
    approvals = _FakeApprovalService()
    runtime = _runtime(graph_rows=EMPTY_GRAPH, approval_service=approvals)

    result = _handle(runtime, {"project_id": "proj-1", "target_url": "https://example.com"})

    state = result["ui_automation_state"]
    assert state["phase"] == "awaiting_recording_approval"
    assert state["approval_id"] == "appr-1"
    assert state["approval_type"] == "ui_recording"
    assert state["recording_request"]["entry_url"] == "https://example.com"
    assert len(approvals.create_calls) == 1
    assert approvals.create_calls[0]["session_id"] == "session-1"
    # 三源审计快照随审批落库
    assert approvals.create_calls[0]["knowledge_gate"]["decision"] == "need_recording"


def test_insufficient_without_approval_service_falls_back_to_exploration():
    runtime = _runtime(graph_rows=EMPTY_GRAPH, approval_service=None)

    result = _handle(runtime, {"project_id": "proj-1", "target_url": "https://example.com"})

    state = result["ui_automation_state"]
    assert state["phase"] == "awaiting_exploration_selection"
    assert "降级" in result["summary"]


def test_explicit_direction_choice_skips_recording_approval():
    approvals = _FakeApprovalService()
    runtime = _runtime(graph_rows=EMPTY_GRAPH, approval_service=approvals)

    # 用户显式选择浏览器/信息探索（direction+subdirection 齐备）→ 直接走 worker
    result = _handle(
        runtime,
        {
            "project_id": "proj-1",
            "target_url": "https://example.com",
            "direction": "browser",
            "subdirection": "information_exploration",
        },
    )

    assert approvals.create_calls == []  # 未发起录制审批
    state = result["ui_automation_state"]
    # 无探索服务配置 → worker 内部占位（direction_not_supported 之外的分支）
    assert state["phase"] in {"exploration_completed", "exploration_failed", "awaiting_input"}


def test_assessor_missing_falls_back_to_legacy_memory_gate():
    # 未注入 assessor：既有 memory-only 评估（memory 服务缺失 → need_exploration）
    runtime = UIAutomationModeRuntime()
    result = _handle(runtime, {"project_id": "proj-1", "target_url": "https://example.com"})

    state = result["ui_automation_state"]
    assert state["phase"] == "awaiting_exploration_selection"
    assert state["knowledge_gate"]["reason"] == "memory_service_unavailable"


def test_missing_target_info_still_asks_input_after_project_given():
    runtime = _runtime()

    # user_message 不带 URL（_extract_url 提取不到）且 objective 过短
    result = asyncio.run(
        runtime.handle(
            {"project_id": "proj-1", "target_url": "", "objective": "短"},
            _Context(user_message="随便看看", normalized_input="随便看看"),
        )
    )

    assert result["ui_automation_state"]["phase"] == "awaiting_input"
    assert result["ui_automation_state"]["knowledge_gate"]["decision"] == "missing_target_info"
