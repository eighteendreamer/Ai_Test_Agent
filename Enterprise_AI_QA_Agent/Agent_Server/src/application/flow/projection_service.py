from __future__ import annotations

from datetime import datetime
from typing import Any

from src.application.runtime.tool_job_service import ToolJobService
from src.application.sessions.session_service import SessionService
from src.schemas.flow import (
    FlowEdge,
    FlowNodeStatus,
    FlowStageNode,
    FlowWorkerNode,
    SessionFlowResponse,
)
from src.schemas.session import ExecutionEvent, SessionSnapshot


# Aligned with agent_web/src/features/flow/stages.ts and workers.ts.
FLOW_STAGES = (
    "context_builder",
    "router",
    "planner",
    "permission_gate",
    "prompt_assembler",
    "model_invoker",
    "tool_executor",
    "finalizer",
    "responder",
)
OPTIONAL_STAGES = frozenset({"tool_executor"})
STAGE_SET = frozenset(FLOW_STAGES)
WORKER_NODE_PREFIX = "worker:"

RUNNING_TYPES = frozenset(
    {
        "graph.execution_started",
        "model.request_prepared",
        "model.tool_calls_received",
        "tool.execution_started",
        "graph.loop_continuing",
        "graph.loop_prepared",
    }
)
WAITING_TYPES = frozenset({"graph.waiting_for_approval"})
FAILED_TYPES = frozenset(
    {
        "tool.execution_failed",
        "tool.execution_denied",
        "tool.execution_blocked",
        "turn.failed",
        "graph.context_budget_exhausted",
        "graph.max_iterations_reached",
    }
)
DONE_TYPES = frozenset(
    {
        "graph.context_built",
        "graph.route_selected",
        "graph.plan_built",
        "graph.permission_evaluated",
        "graph.prompt_assembled",
        "model.response_received",
        "tool.execution_completed",
        "tool.execution_skipped",
        "graph.execution_completed",
        "graph.response_ready",
        "runtime.turn_completed",
        "turn.completed",
    }
)
STAGE_EDGES = (
    ("e-context-router", "context_builder", "router"),
    ("e-router-planner", "router", "planner"),
    ("e-planner-permission", "planner", "permission_gate"),
    ("e-permission-prompt", "permission_gate", "prompt_assembler"),
    ("e-prompt-model", "prompt_assembler", "model_invoker"),
    ("e-model-tool", "model_invoker", "tool_executor"),
    ("e-model-finalizer", "model_invoker", "finalizer"),
    ("e-tool-finalizer", "tool_executor", "finalizer"),
    ("e-finalizer-responder", "finalizer", "responder"),
)


def _payload_text(event: ExecutionEvent, key: str) -> str:
    value = (event.payload or {}).get(key)
    return "" if value is None else str(value).strip()


def _event_timestamp(event: ExecutionEvent) -> float:
    stamp = event.timestamp
    if isinstance(stamp, datetime):
        return stamp.timestamp()
    parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return parsed.timestamp()


def _event_step(event: ExecutionEvent) -> float:
    raw = (event.payload or {}).get("step")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def resolve_latest_turn_id(events: list[ExecutionEvent]) -> str:
    for event in reversed(events):
        turn_id = _payload_text(event, "turn_id")
        if turn_id:
            return turn_id
    return ""


def select_turn_events(events: list[ExecutionEvent], turn_id: str = "") -> list[ExecutionEvent]:
    target = turn_id or resolve_latest_turn_id(events)
    if not target:
        return list(events)
    return [
        event
        for event in events
        if not _payload_text(event, "turn_id") or _payload_text(event, "turn_id") == target
    ]


def status_from_event_type(event_type: str) -> FlowNodeStatus | None:
    if event_type in WAITING_TYPES:
        return FlowNodeStatus.waiting_approval
    if event_type in FAILED_TYPES:
        return FlowNodeStatus.failed
    if event_type in RUNNING_TYPES:
        return FlowNodeStatus.running
    if event_type in DONE_TYPES:
        return FlowNodeStatus.done
    return None


def _empty_statuses() -> dict[str, FlowNodeStatus]:
    return {stage: FlowNodeStatus.pending for stage in FLOW_STAGES}


def _mark_prior_stages_done(statuses: dict[str, FlowNodeStatus], current: str) -> None:
    current_index = FLOW_STAGES.index(current)
    for stage in FLOW_STAGES[:current_index]:
        if stage in OPTIONAL_STAGES:
            continue
        if statuses[stage] is FlowNodeStatus.pending:
            statuses[stage] = FlowNodeStatus.done


def project_stage_statuses(
    events: list[ExecutionEvent],
    turn_id: str = "",
) -> dict[str, FlowNodeStatus]:
    statuses = _empty_statuses()
    ordered = sorted(select_turn_events(events, turn_id), key=lambda item: (_event_timestamp(item), _event_step(item)))

    for event in ordered:
        status = status_from_event_type(event.type)
        phase = _payload_text(event, "phase")
        if status is not None and phase in STAGE_SET:
            statuses[phase] = status
            if status is not FlowNodeStatus.pending:
                _mark_prior_stages_done(statuses, phase)
            continue

        if event.type == "runtime.turn_started" and statuses["context_builder"] is FlowNodeStatus.pending:
            statuses["context_builder"] = FlowNodeStatus.running
            continue

        if event.type in {"runtime.turn_completed", "turn.completed"}:
            for stage in FLOW_STAGES:
                if statuses[stage] in {FlowNodeStatus.running, FlowNodeStatus.waiting_approval}:
                    statuses[stage] = FlowNodeStatus.done
            if statuses["responder"] is FlowNodeStatus.pending and statuses["finalizer"] is FlowNodeStatus.done:
                statuses["responder"] = FlowNodeStatus.done
            continue

        if event.type == "turn.failed":
            for stage in FLOW_STAGES:
                if statuses[stage] in {FlowNodeStatus.running, FlowNodeStatus.waiting_approval}:
                    statuses[stage] = FlowNodeStatus.failed

    return statuses


def worker_node_id(worker: dict[str, Any], index: int = 0) -> str:
    task_id = str(worker.get("task_id") or "").strip()
    if task_id:
        return f"{WORKER_NODE_PREFIX}{task_id}"
    child_id = str(worker.get("child_session_id") or "").strip()
    if child_id:
        return f"{WORKER_NODE_PREFIX}{child_id}"
    return f"{WORKER_NODE_PREFIX}anonymous-{index}"


def worker_flow_status(status: Any) -> FlowNodeStatus:
    normalized = str(status or "").strip().lower()
    if normalized in {"running", "in_progress"}:
        return FlowNodeStatus.running
    if normalized in {"waiting_approval", "waiting"}:
        return FlowNodeStatus.waiting_approval
    if normalized in {"completed", "success", "done"}:
        return FlowNodeStatus.done
    if normalized in {"failed", "error", "denied", "cancelled"}:
        return FlowNodeStatus.failed
    return FlowNodeStatus.pending


def worker_source_stage(worker: dict[str, Any]) -> str:
    source = str(worker.get("source_stage") or "").strip()
    return source if source in STAGE_SET else "tool_executor"


def _as_record_array(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def collect_worker_dispatches(
    metadata: dict[str, Any] | None,
    graph_state: dict[str, Any] | None,
    turn_id: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    groups = [
        _as_record_array((metadata or {}).get("worker_dispatches")),
        _as_record_array((graph_state or {}).get("worker_dispatches")),
    ]
    for group in groups:
        for index, worker in enumerate(group):
            key = worker_node_id(worker, index)
            current = merged.get(key)
            if current is None:
                merged[key] = dict(worker)
                ordered.append(merged[key])
                continue
            current.update(worker)
            at = next((pos for pos, item in enumerate(ordered) if worker_node_id(item) == key), -1)
            if at >= 0:
                ordered[at] = current
    if not turn_id:
        return ordered
    kept: list[dict[str, Any]] = []
    for worker in ordered:
        parent_turn_id = str(worker.get("parent_turn_id") or "").strip()
        if not parent_turn_id or parent_turn_id == turn_id:
            kept.append(worker)
    return kept


def pick_snapshot_for_turn(snapshots: list[SessionSnapshot], turn_id: str) -> SessionSnapshot | None:
    if not snapshots:
        return None
    ranked = sorted(
        snapshots,
        key=lambda item: (int(item.version or 0), str(item.created_at or "")),
        reverse=True,
    )
    if not turn_id:
        return ranked[0]
    for snapshot in ranked:
        graph_state = snapshot.graph_state if isinstance(snapshot.graph_state, dict) else {}
        if str(graph_state.get("turn_id") or "").strip() == turn_id:
            return snapshot
    return None


class FlowProjectionService:
    """Read-only projection of an existing session turn. Does not write events or graph state."""

    def __init__(
        self,
        session_service: SessionService,
        tool_job_service: ToolJobService,
    ) -> None:
        self._session_service = session_service
        self._tool_job_service = tool_job_service

    async def get_flow(self, session_id: str, turn_id: str | None = None) -> SessionFlowResponse:
        detail = await self._session_service.get_session(session_id)
        events = await self._session_service.list_events(session_id, limit=500)
        snapshots = await self._session_service.list_snapshots(
            session_id,
            limit=20,
            include_graph_state=True,
        )
        jobs = await self._tool_job_service.list_jobs(session_id=session_id)
        artifacts = await self._tool_job_service.list_artifacts(session_id=session_id)

        requested_turn = str(turn_id or "").strip()
        resolved_turn = requested_turn or resolve_latest_turn_id(events)
        snapshot = pick_snapshot_for_turn(snapshots, resolved_turn)
        graph_state = snapshot.graph_state if snapshot and isinstance(snapshot.graph_state, dict) else None
        metadata = detail.metadata if isinstance(getattr(detail, "metadata", None), dict) else {}
        workers = collect_worker_dispatches(metadata, graph_state, resolved_turn)
        statuses = project_stage_statuses(events, resolved_turn)

        stage_nodes = [
            FlowStageNode(id=stage, status=statuses[stage])
            for stage in FLOW_STAGES
        ]
        worker_nodes = [
            FlowWorkerNode(
                id=worker_node_id(worker, index),
                status=worker_flow_status(worker.get("status")),
                source_stage=worker_source_stage(worker),
                worker=dict(worker),
            )
            for index, worker in enumerate(workers)
        ]
        edges = [
            FlowEdge(id=edge_id, source=source, target=target, kind="stage")
            for edge_id, source, target in STAGE_EDGES
        ]
        edges.extend(
            FlowEdge(
                id=f"e-{node.source_stage}-{node.id}",
                source=node.source_stage,
                target=node.id,
                kind="spawn",
            )
            for node in worker_nodes
        )
        return SessionFlowResponse(
            session_id=session_id,
            turn_id=resolved_turn,
            stages=stage_nodes,
            workers=worker_nodes,
            edges=edges,
            events=events,
            graph_state=graph_state,
            snapshot_id=snapshot.id if snapshot else None,
            tool_jobs=list(jobs),
            artifacts=list(artifacts),
        )
