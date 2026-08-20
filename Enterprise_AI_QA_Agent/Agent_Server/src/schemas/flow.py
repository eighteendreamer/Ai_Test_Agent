from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.schemas.session import ExecutionEvent
from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord


class FlowNodeStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    waiting_approval = "waiting_approval"


class FlowStageNode(BaseModel):
    id: str
    kind: Literal["stage"] = "stage"
    status: FlowNodeStatus
    phase: str


class FlowWorkerNode(BaseModel):
    id: str
    kind: Literal["worker"] = "worker"
    status: FlowNodeStatus
    source_stage: str
    worker: dict[str, Any] = Field(default_factory=dict)


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: Literal["stage", "spawn"]


class SessionFlowResponse(BaseModel):
    session_id: str
    turn_id: str
    stages: list[FlowStageNode]
    workers: list[FlowWorkerNode]
    edges: list[FlowEdge]
    events: list[ExecutionEvent] = Field(default_factory=list)
    graph_state: dict[str, Any] | None = None
    snapshot_id: str | None = None
    tool_jobs: list[ToolJobRecord] = Field(default_factory=list)
    artifacts: list[ToolArtifactRecord] = Field(default_factory=list)
