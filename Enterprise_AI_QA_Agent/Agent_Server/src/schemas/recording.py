from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class RecordingStatus(str, Enum):
    """录制会话权威状态机（方案 4.1 / 4.2）。

    迁移合法性由 RecorderSessionService 校验；此处仅定义取值。
    """

    launching = "launching"        # 会话已创建，等待驱动就绪
    ready = "ready"                # 驱动就绪，采集未开始
    active = "active"              # 采集中
    paused = "paused"              # 暂停（事件不入库）
    finalizing = "finalizing"      # 停止后固化中（PG 事件 → Memgraph）
    completed = "completed"        # 固化完成
    discarded = "discarded"        # 销毁：丢弃数据，不写图谱
    failed = "failed"              # 启动/固化失败


class RecordingControlAction(str, Enum):
    """控制条四按钮（方案 4.2 环节⑥）。"""

    start = "start"
    pause = "pause"
    resume = "resume"
    stop = "stop"
    destroy = "destroy"


class RecordingDriverKind(str, Enum):
    """浏览器接入层驱动类型（方案 5.1）。"""

    embedded = "embedded"                  # Electron 内嵌 WebContentsView
    cdp_attach = "cdp-attach"              # attach 外部 Chromium 系浏览器
    playwright_managed = "playwright-managed"  # 服务端自启（Playwright）


# recorder.js 采集的已知事件类型（方案 6.1）；type 字段不强制枚举，
# 未知类型按原样入库（前向兼容），固化端按需忽略。
RECORDING_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "click",
        "dblclick",
        "fill",          # input/change debounce 500ms 合并后的最终值
        "key",           # 功能键 / 快捷键组合
        "submit",
        "scroll",        # 300ms 节流
        "navigate",      # from/to URL
        "file_change",   # input[type=file]，只记文件名
    }
)

# 事件 identity：入库幂等键（PG 联合唯一约束 ui_recording_event(recording_id, seq)）。
RecordingEventIdentity = tuple[str, int]


def event_identity(recording_id: str, seq: int) -> RecordingEventIdentity:
    """事件的幂等标识：网络重试、重复批次按此键去重（方案 7.2 关口②）。"""
    return (recording_id, seq)


def mask_sensitive_input(value: str) -> dict[str, int]:
    """敏感输入脱敏（安全红线）：只保留长度，不保留明文。"""
    return {"length": len(value)}


class RecorderEvent(BaseModel):
    """录制事件流水（方案 6.2 双重定位契约）。

    追加写语义：事件是不可变流水，只 insert 不 update；同一
    (recording_id, seq) 重复提交由存储层按唯一约束收敛。
    """

    seq: int = Field(ge=0, description="会话内单调递增序号，与 recording_id 构成幂等键")
    type: str = Field(description="click/dblclick/fill/key/submit/scroll/navigate/...")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    page: dict[str, Any] = Field(default_factory=dict, description="url/title/viewport/dpr")
    target: dict[str, Any] | None = Field(default=None, description="DOM 定位链 locators/tag/role/attributes")
    pixel: dict[str, Any] | None = Field(default=None, description="viewport_point/bbox/rel_offset 像素三件套")
    value: Any = Field(default=None, description="输入值（敏感字段采集端已脱敏为 {length:n}）")
    page_effect: dict[str, Any] = Field(default_factory=dict, description="navigated_to/dom_mutation_count")
    screenshot_ref: str | None = Field(default=None, description="截图产物引用（MinIO/本地产物目录）")

    @field_validator("type")
    @classmethod
    def _validate_type_not_blank(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("recorder event type must not be blank")
        return normalized

    def identity(self, recording_id: str) -> RecordingEventIdentity:
        """本事件的幂等键（与 PG 联合唯一约束语义一致）。"""
        return event_identity(recording_id, self.seq)


def dedupe_event_batch(events: list[RecorderEvent]) -> list[RecorderEvent]:
    """批内按 (seq) 幂等去重：保留首次出现，丢弃重复（方案 7.2 关口②）。

    同一 recording_id 内 seq 即唯一；调用方保证传入事件属于同一录制会话。
    """
    seen: set[int] = set()
    deduped: list[RecorderEvent] = []
    for event in events:
        if event.seq in seen:
            continue
        seen.add(event.seq)
        deduped.append(event)
    return deduped


class RecordingSession(BaseModel):
    """录制会话元数据（PG ui_recording 表行）。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = Field(description="正式 project_id（禁止自由文本冒充）")
    name: str = ""
    entry_url: str
    driver_kind: RecordingDriverKind = RecordingDriverKind.embedded
    status: RecordingStatus = RecordingStatus.launching
    session_id: str | None = Field(default=None, description="触发的 agent 会话")
    approval_id: str | None = Field(default=None, description="来源审批 ui_recording")
    step_count: int = Field(default=0, ge=0, description="已入库事件数（对账用）")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    finalize_metrics: dict[str, Any] = Field(default_factory=dict, description="固化指标：图谱写入计数/对账/degraded 标记")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecordingDriverConfig(BaseModel):
    """驱动配置：桌面端 embedded；cdp-attach 需 endpoint。"""

    kind: RecordingDriverKind = RecordingDriverKind.embedded
    endpoint: str | None = Field(default=None, description="cdp-attach 的 http://host:port/json")
    viewport: tuple[int, int] = (1440, 960)


class RecordingCreateRequest(BaseModel):
    """POST /api/v1/recordings 请求体。"""

    project_id: str
    name: str = ""
    entry_url: str
    driver: RecordingDriverConfig = Field(default_factory=RecordingDriverConfig)
    session_id: str | None = None
    approval_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("project_id", "entry_url")
    @classmethod
    def _validate_not_blank(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("project_id and entry_url must not be blank")
        return normalized


class RecordingEventBatchRequest(BaseModel):
    """POST /api/v1/recordings/{id}/events:batch 请求体。"""

    events: list[RecorderEvent] = Field(default_factory=list)
    client_batch_id: str | None = Field(default=None, description="客户端批次号（日志追踪）")


class RecordingControlRequest(BaseModel):
    """POST /api/v1/recordings/{id}/control 请求体。"""

    action: RecordingControlAction
    reason: str | None = None


class RecordingPublic(BaseModel):
    """对外投影：列表/详情通用，不含事件流。"""

    id: str
    project_id: str
    name: str
    entry_url: str
    driver_kind: RecordingDriverKind
    status: RecordingStatus
    session_id: str | None = None
    approval_id: str | None = None
    step_count: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    finalize_metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_session(cls, session: RecordingSession) -> RecordingPublic:
        return cls(
            id=session.id,
            project_id=session.project_id,
            name=session.name,
            entry_url=session.entry_url,
            driver_kind=session.driver_kind,
            status=session.status,
            session_id=session.session_id,
            approval_id=session.approval_id,
            step_count=session.step_count,
            created_at=session.created_at,
            updated_at=session.updated_at,
            started_at=session.started_at,
            ended_at=session.ended_at,
            finalize_metrics=session.finalize_metrics,
            metadata=session.metadata,
        )


class RecordingDetail(RecordingPublic):
    """详情：会话 + 事件流 + 固化指标。"""

    events: list[RecorderEvent] = Field(default_factory=list)


class RecordingEventAck(BaseModel):
    """批量事件写入回执：去重后入库数 + 丢弃重复数（幂等语义可见）。"""

    accepted: int
    duplicates: int
