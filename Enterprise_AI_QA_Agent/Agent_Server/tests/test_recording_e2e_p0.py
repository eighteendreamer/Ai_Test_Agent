"""P0 端到端验收测试（方案 12 章"编排/集成"层，P0-11）。

真实组件全链路串联（仅基础设施 Fake，与 docker-compose 集成环境等价的
内存替身）：

    编排反问项目 → 三源检索不足 → ui_recording 审批落库
    → 审批通过（SessionService.resolve_approval 的委托调用点）
    → RecorderSessionService.launch → 驱动就绪 → start
    → 用户操作事件流（含重复投递）→ 攒批落 PG（幂等去重）
    → stop → RecordingGraphStore 固化 → 图谱写入形状断言
    → 指标对账（action_vertices == PG 事件数，reconciled=True）
    → 固化后再次编排 → task_generation_ready（环节⑤闭环）

拒绝路：apply_decision(denied) → recorder.approval_declined 事件，零录制会话。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.drivers import (
    BrowserDriver,
    DriverRegistry,
    EmbeddedBridge,
    EventChannel,
)
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.application.recorder.recording_approval_service import RecordingApprovalService
from src.application.recorder.ui_resource_assessor import UIResourceAssessor
from src.modes.ui_automation_mode.runtime import UIAutomationModeRuntime
from src.schemas.recording import RecordingControlAction, RecordingEventAck, RecordingStatus
from src.schemas.session import ToolApprovalStatus


# ---------------------------------------------------------------- fakes


@dataclass
class _Context:
    session_id: str = "session-1"
    turn_id: str = "turn-1"
    trace_id: str = "trace-1"
    user_message: str = "测试 https://app.example.com 登录流程"
    normalized_input: str = ""
    context_bundle: dict[str, Any] = field(default_factory=dict)


class _FakeMemgraph:
    """assessor 检索源：计数可控（空图谱 → 录制；富图谱 → 资源充分）。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def initialize(self) -> None: ...

    def execute(self, query: str, parameters: dict | None = None) -> list[dict]:
        return self.rows


class _FakeGraphProvider:
    """graph store 写入源：记录全部 Cypher 与参数（形状/脱敏断言依据）。"""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def initialize(self) -> None: ...

    def execute(self, query: str, parameters: dict | None = None) -> list[dict]:
        return []

    def execute_write(self, query: str, parameters: dict | None = None) -> list[dict]:
        self.writes.append((query, dict(parameters or {})))
        return []


class _FakeAgentSessionStore:
    """RecordingApprovalService 依赖：审批落库 + 事件追加（内存版）。"""

    def __init__(self) -> None:
        self.approvals: dict[str, Any] = {}
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def save_approval(self, session_id: str, approval: Any) -> None:
        self.approvals[approval.id] = approval

    async def append_event(self, session_id: str, event: Any) -> None:
        self.events.append((session_id, event.type, dict(event.payload or {})))


class _FakeDriver(BrowserDriver):
    kind = "embedded"

    def __init__(self) -> None:
        self.opened: list[tuple[str, tuple[int, int]]] = []
        self.capture_calls: list[bool] = []
        self.closed = False
        self.injected = False
        self._ready_event = asyncio.Event()
        self._channel = EventChannel()

    def publish_event(self, payload: dict[str, Any]) -> None:
        self._channel.publish(payload)

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        self.opened.append((url, viewport))

    async def inject_recorder(self, binding_name: str = "__qaRecordEmit") -> None:
        self.injected = True

    def mark_ready(self) -> None:
        self._ready_event.set()

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def on_recorder_event(self) -> AsyncIterator[dict[str, Any]]:
        return self._channel.iterate()

    async def capture_screenshot(self) -> bytes:
        return b""

    async def current_page_info(self) -> dict[str, Any]:
        return {}

    async def set_capture_enabled(self, enabled: bool) -> None:
        self.capture_calls.append(enabled)

    async def close(self) -> None:
        self.closed = True


class _FakePGStore:
    """内存版 PostgresRecordingStore。

    幂等语义与真实链路等价：bridge 预收敛（批内去重 + seen_seqs）+
    PG ``(recording_id, seq)`` 唯一约束 → 逐条增量去重，重复只留首份。
    """

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.events: dict[str, list[Any]] = {}

    async def create_session(self, session: Any) -> Any:
        self.sessions[session.id] = session
        return session

    async def append_events(self, recording_id: str, events: list[Any]) -> RecordingEventAck:
        known = self.events.setdefault(recording_id, [])
        existing = {e.seq for e in known}
        accepted = 0
        for event in events:
            if event.seq in existing:
                continue
            known.append(event)
            existing.add(event.seq)
            accepted += 1
        # 真实 store 契约：append 后同步 step_count = 落库事件总数
        session = self.sessions.get(recording_id)
        if session is not None:
            session.step_count = len(known)
            session.updated_at = datetime.now(timezone.utc)
        return RecordingEventAck(accepted=accepted, duplicates=len(events) - accepted)

    async def update_status(
        self,
        recording_id: str,
        status: RecordingStatus,
        *,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        finalize_metrics: dict[str, Any] | None = None,
    ) -> Any | None:
        session = self.sessions.get(recording_id)
        if session is None:
            return None
        session.status = status
        if started_at:
            session.started_at = started_at
        if ended_at:
            session.ended_at = ended_at
        if finalize_metrics is not None:
            session.finalize_metrics = finalize_metrics
        session.updated_at = datetime.now(timezone.utc)
        return session

    async def get_session(self, recording_id: str) -> Any | None:
        return self.sessions.get(recording_id)

    async def list_sessions(self, project_id=None, limit=50, offset=0):  # noqa: ANN001
        values = list(self.sessions.values())
        if project_id:
            values = [s for s in values if s.project_id == project_id]
        return values[offset : offset + limit]

    async def get_events(self, recording_id: str) -> list[Any]:
        return list(self.events.get(recording_id, []))

    async def discard_session(self, recording_id: str) -> Any | None:
        session = self.sessions.get(recording_id)
        if session is None:
            return None
        session.status = RecordingStatus.discarded
        session.ended_at = datetime.now(timezone.utc)
        return session


# ---------------------------------------------------------------- 装配


def _graph_store(provider: _FakeGraphProvider) -> RecordingGraphStore:
    store = object.__new__(RecordingGraphStore)
    store._settings = None  # type: ignore[assignment]
    store._provider = provider  # type: ignore[assignment]
    return store


class _Harness:
    """全真实服务 + Fake 基础设施的一次性装配。"""

    def __init__(self, graph_rows: list[dict] | None = None) -> None:
        self.memgraph = _FakeMemgraph(
            graph_rows or [{"page_count": 0, "element_count": 0, "action_count": 0}]
        )
        self.pg = _FakePGStore()
        self.graph_provider = _FakeGraphProvider()
        self.session_store = _FakeAgentSessionStore()
        self.drivers: dict[str, _FakeDriver] = {}

        registry = DriverRegistry()

        def _factory(_config, *, recording_id: str, **_ctx) -> _FakeDriver:
            driver = _FakeDriver()
            self.drivers[recording_id] = driver
            return driver

        registry.register("embedded", _factory)
        self.recorder = RecorderSessionService(
            settings=None,  # type: ignore[arg-type]
            store=self.pg,  # type: ignore[arg-type]
            graph_store=_graph_store(self.graph_provider),  # type: ignore[arg-type]
            registry=registry,
            bridge=EmbeddedBridge(),
        )
        self.approval_service = RecordingApprovalService(
            recorder_service=self.recorder, session_store=self.session_store
        )
        assessor = UIResourceAssessor(
            memgraph_provider=self.memgraph,  # type: ignore[arg-type]
            test_case_service=None,
            memory_runtime_service=None,
        )
        self.runtime = UIAutomationModeRuntime(
            resource_assessor=assessor,
            recording_approval_service=self.approval_service,
            project_catalog_provider=None,
        )

    async def handle(self, arguments: dict | None = None) -> dict:
        return await self.runtime.handle(arguments or {}, _Context())

    async def wait_status(self, recording_id: str, target: RecordingStatus, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self.recorder.runtime_status(recording_id) is target:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(
            f"recording {recording_id} did not reach {target.value}: "
            f"{self.recorder.runtime_status(recording_id)}"
        )

    async def wait_event_count(self, recording_id: str, expected: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if len(self.pg.events.get(recording_id, [])) >= expected:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(
            f"expected {expected} persisted events, got {len(self.pg.events.get(recording_id, []))}"
        )


# ------------------------------------------------------------- 事件脚本


_LOGIN_PAGE = {"url": "https://app.example.com/login", "title": "登录"}
_HOME_PAGE = {"url": "https://app.example.com/home", "title": "工作台"}


def _locators(name: str, *, css: str) -> dict[str, Any]:
    return {
        "id": None,
        "testid": None,
        "role_name": {"role": "button", "name": name},
        "css": css,
        "xpath": None,
        "text": name,
    }


def _event_payload(seq: int, etype: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "seq": seq,
        "type": etype,
        "timestamp": "2026-08-24T12:00:00Z",
        "page": dict(_LOGIN_PAGE),
        "target": None,
        "value": None,
        "page_effect": {},
    }
    payload.update(overrides)
    return payload


def _login_script() -> list[dict[str, Any]]:
    """登录 + 表单提交脚本：9 个唯一动作（seq 0..8，与 recorder.js 从 0 计数一致）。"""
    return [
        _event_payload(0, "navigate", page=_LOGIN_PAGE),
        _event_payload(
            1,
            "fill",
            target={"tag": "INPUT", "role": "textbox", "locators": _locators("用户名", css="input#username")},
            value="alice",
        ),
        _event_payload(
            2,
            "fill",
            target={
                "tag": "INPUT",
                "role": "textbox",
                "locators": _locators("密码", css="input#password"),
                "attributes": {"type": "password"},
            },
            value="s3cret-pw",  # 密码字段：固化端必须兜底脱敏
        ),
        _event_payload(
            3,
            "click",
            target={"tag": "BUTTON", "role": "button", "locators": _locators("登 录", css="form>button.primary")},
        ),
        _event_payload(4, "navigate", page=_HOME_PAGE, page_effect={"navigated_to": "https://app.example.com/home"}),
        _event_payload(
            5,
            "click",
            target={
                "tag": "A",
                "role": "link",
                "locators": _locators("新建工单", css="nav>a.new-ticket"),
                "attributes": {"href": "/tickets/new"},
            },
        ),
        _event_payload(
            6,
            "fill",
            target={"tag": "INPUT", "role": "textbox", "locators": _locators("工单标题", css="input#ticket-title")},
            value="无法登录",
        ),
        _event_payload(
            7,
            "click",
            target={"tag": "BUTTON", "role": "button", "locators": _locators("提 交", css="form>button.submit")},
        ),
        _event_payload(8, "submit", target={"tag": "FORM", "role": "form", "locators": _locators("新建工单表单", css="form#new-ticket")}),
    ]


# ---------------------------------------------------------------- 主路径


def test_p0_e2e_approve_record_finalize_reconcile_and_ready() -> None:
    """全链路一次跑通：反问 → 审批 → 录制 → 固化 → 对账一致 → 任务生成就绪。"""

    async def _scenario() -> None:
        harness = _Harness()

        # --- 环节②：缺 project_id → 反问项目（带决策依据，不冒充正式项目）
        result = await harness.handle({"target_url": "https://app.example.com"})
        state = result["ui_automation_state"]
        assert state["phase"] == "awaiting_project_selection"

        # --- 环节③④：三源不足 → 发起 ui_recording 审批（落库 + 事件）
        result = await harness.handle(
            {"project_id": "proj-1", "target_url": "https://app.example.com"}
        )
        state = result["ui_automation_state"]
        assert state["phase"] == "awaiting_recording_approval"
        approval_id = state["approval_id"]
        approval = harness.session_store.approvals[approval_id]
        assert approval.metadata["approval_type"] == "ui_recording"
        assert approval.metadata["knowledge_gate"]["decision"] == "need_recording"
        event_types = [e[1] for e in harness.session_store.events]
        assert "approval.created" in event_types

        # --- 环节④ 决策：approved → launch（SessionService.resolve_approval 的委托调用点）
        decision = await harness.approval_service.apply_decision(
            approval, decision=ToolApprovalStatus.approved, reason=None
        )
        assert decision["status"] == "launched"
        recording_id = decision["recording_id"]
        launch_events = [e for e in harness.session_store.events if e[1] == "recorder.launch_requested"]
        assert len(launch_events) == 1
        assert launch_events[0][2]["recording_id"] == recording_id

        # --- 驱动就绪 → ready（Electron 登记握手）
        driver = harness.drivers[recording_id]
        assert driver.opened[0][0] == "https://app.example.com"
        assert driver.injected is True
        driver.mark_ready()
        await harness.wait_status(recording_id, RecordingStatus.ready)

        # --- 控制：start → active，注入登录表单操作脚本（含一次重复投递）
        await harness.recorder.control(recording_id, RecordingControlAction.start)
        assert harness.recorder.runtime_status(recording_id) is RecordingStatus.active

        script = _login_script()
        for payload in script:
            driver.publish_event(payload)
        driver.publish_event(script[4])  # seq=4 重复投递（网络重试等价）→ 幂等去重

        await harness.wait_event_count(recording_id, expected=9)
        persisted = harness.pg.events[recording_id]
        assert len(persisted) == 9, "重复 seq 必须被幂等去重"
        assert sorted(e.seq for e in persisted) == list(range(9))

        # --- 控制：stop → 固化 → completed
        completed = await harness.recorder.control(recording_id, RecordingControlAction.stop)
        assert completed.status is RecordingStatus.completed
        assert driver.closed is True

        # --- 对账（方案 12 章集成验收）：图谱指标 == PG 事件数
        metrics = completed.finalize_metrics
        finalize = metrics["finalize"]
        integrity = metrics["integrity"]
        assert integrity["pg_event_count"] == 9
        assert finalize["action_vertices"] == 9, "Action 流水不去重"
        assert integrity["reconciled"] is True
        assert integrity["degraded"] is False
        assert integrity["seq_gaps"] == []
        assert finalize["page_vertices"] >= 2, "登录页 + 工作台两页"
        assert finalize["element_vertices"] >= 1
        assert finalize["has_step_edges"] == 9

        # --- 图谱写入形状：MERGE 幂等语句与边类型齐全
        writes = harness.graph_provider.writes
        queries = [q for q, _ in writes]
        assert sum("MERGE (n:Recording" in q for q in queries) == 1
        assert sum("MERGE (n:Action" in q for q in queries) == 9
        assert sum("MERGE (n:Page" in q for q in queries) == finalize["page_vertices"]
        assert sum("HAS_STEP" in q for q in queries) == 9
        assert sum("TARGETS" in q for q in queries) == finalize["targets_edges"]
        assert sum("ON_PAGE" in q for q in queries) == finalize["on_page_edges"]

        # --- 安全红线：明文密码不得进入任何图谱写入参数
        for _, params in writes:
            assert "s3cret-pw" not in json.dumps(params, ensure_ascii=False, default=str)

        # --- 环节⑤：固化完成后再编排 → 图谱覆盖充分 → task_generation_ready
        harness.memgraph.rows = [{"page_count": 2, "element_count": 12, "action_count": 9}]
        result = await harness.handle(
            {"project_id": "proj-1", "target_url": "https://app.example.com"}
        )
        state = result["ui_automation_state"]
        assert state["phase"] == "task_generation_ready"
        assert state["knowledge_gate"]["reason"] == "graph_coverage_sufficient"

    asyncio.run(_scenario())


def test_p0_e2e_denied_declines_without_launching_recording() -> None:
    """审批拒绝：降级事件可见，零录制会话产生（未审批不得启动驱动）。"""

    async def _scenario() -> None:
        harness = _Harness()

        result = await harness.handle(
            {"project_id": "proj-1", "target_url": "https://app.example.com"}
        )
        approval_id = result["ui_automation_state"]["approval_id"]
        approval = harness.session_store.approvals[approval_id]

        decision = await harness.approval_service.apply_decision(
            approval, decision=ToolApprovalStatus.denied, reason="不需要录制"
        )
        assert decision["status"] == "declined"
        declined_events = [
            e for e in harness.session_store.events if e[1] == "recorder.approval_declined"
        ]
        assert len(declined_events) == 1
        assert declined_events[0][2]["approval_id"] == approval_id
        assert harness.pg.sessions == {}, "拒绝后不得创建任何录制会话"
        assert harness.drivers == {}, "拒绝后不得启动任何驱动"

    asyncio.run(_scenario())
