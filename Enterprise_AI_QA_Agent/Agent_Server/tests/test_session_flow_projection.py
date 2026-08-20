from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from src.api.routes.sessions import router as sessions_router
from src.application.flow.projection_service import (
    FlowProjectionService,
    collect_worker_dispatches,
    pick_snapshot_for_turn,
    project_flow_nodes,
    project_stage_statuses,
    worker_source_stage,
)
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.application.sessions.session_service import SessionService
from src.registry.modes import ModeRegistry
from src.runtime.store import InMemorySessionStore
from src.schemas.session import ExecutionEvent, SessionSnapshot


def _run(awaitable):
    return asyncio.run(awaitable)


def _event(event_type: str, turn_id: str = "t1", phase: str = "", **payload) -> ExecutionEvent:
    data = {"turn_id": turn_id, **payload}
    if phase:
        data["phase"] = phase
    return ExecutionEvent(
        type=event_type,
        session_id="s1",
        timestamp=datetime.now(timezone.utc),
        payload=data,
    )


class _FakeToolJobs:
    def __init__(self, jobs=None, artifacts=None):
        self.jobs = jobs or []
        self.artifacts = artifacts or []

    async def list_jobs(self, session_id=None):
        return list(self.jobs)

    async def list_artifacts(self, session_id=None, tool_job_id=None):
        return list(self.artifacts)


class _FakeSessions:
    def __init__(self, session_id="s1", metadata=None, events=None, snapshots=None):
        self.session_id = session_id
        self.detail = SimpleNamespace(id=session_id, metadata=metadata or {})
        self.events = events or []
        self.snapshots = snapshots or []
        self.event_reads = 0

    async def get_session(self, session_id: str):
        if session_id != self.session_id:
            raise KeyError(session_id)
        return self.detail

    async def list_events(self, session_id: str, limit=None, after_event_id=None):
        if session_id != self.session_id:
            raise KeyError(session_id)
        self.event_reads += 1
        return list(self.events)

    async def list_snapshots(self, session_id: str, limit=None, include_graph_state=False):
        if session_id != self.session_id:
            raise KeyError(session_id)
        return list(self.snapshots)


def _build_app():
    projects = ProjectService(store=InMemoryProjectStore())
    _run(projects.initialize())
    modes = ModeRegistry()
    store = InMemorySessionStore()
    sessions = SessionService(
        store=store,
        input_orchestrator_service=InputOrchestratorService(mode_registry=modes),
        runtime_service=object(),
        mode_registry=modes,
        project_service=projects,
    )
    app = FastAPI()
    app.state.session_service = sessions
    app.state.tool_job_service = _FakeToolJobs()
    app.include_router(sessions_router, prefix="/api/v1")
    return app, store


def _request(app, method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return _run(send())


def test_project_stage_statuses_follow_phase_and_turn_started():
    events = [
        _event("runtime.turn_started", phase=""),
        _event("graph.context_built", phase="context_builder"),
        _event("graph.execution_started", phase="router"),
    ]
    statuses = project_stage_statuses(events, "t1")
    assert statuses["context_builder"].value == "done"
    assert statuses["router"].value == "running"
    assert statuses["tool_executor"].value == "pending"


def test_project_flow_nodes_only_contains_observed_phases_and_edges():
    events = [
        _event("runtime.turn_started"),
        _event("graph.context_built", phase="context_builder"),
        _event("graph.execution_started", phase="router"),
        _event("graph.prompt_assembled", phase="prompt_assembler"),
    ]

    stages, edges = project_flow_nodes(events, "t1")

    assert [stage.phase for stage in stages] == [
        "context_builder",
        "router",
        "prompt_assembler",
    ]
    assert [stage.id for stage in stages] == [
        "context_builder",
        "router",
        "prompt_assembler",
    ]
    assert [edge.model_dump() for edge in edges] == [
        {"id": "e-context_builder-router", "source": "context_builder", "target": "router", "kind": "stage"},
        {"id": "e-router-prompt_assembler", "source": "router", "target": "prompt_assembler", "kind": "stage"},
    ]
    assert "planner" not in {stage.phase for stage in stages}


def test_collect_workers_keeps_missing_parent_turn_and_filters_other_turns():
    workers = collect_worker_dispatches(
        {
            "worker_dispatches": [
                {"task_id": "keep-absent", "agent_key": "a", "description": "x", "status": "running"},
                {
                    "task_id": "keep-match",
                    "agent_key": "a",
                    "description": "x",
                    "status": "running",
                    "parent_turn_id": "t1",
                },
                {
                    "task_id": "drop-other",
                    "agent_key": "a",
                    "description": "x",
                    "status": "running",
                    "parent_turn_id": "t2",
                },
            ]
        },
        None,
        "t1",
    )
    assert [item["task_id"] for item in workers] == ["keep-absent", "keep-match"]


def test_worker_source_stage_preserves_dynamic_source_phase():
    assert worker_source_stage({"source_stage": "independent_findings"}) == "independent_findings"
    assert worker_source_stage({"source_stage": "planner"}) == "planner"


def test_pick_snapshot_does_not_fall_back_to_another_turn():
    matching = SessionSnapshot(
        id="snap-t1",
        session_id="s1",
        version=1,
        stage="prompt_assembler",
        created_at=datetime.now(timezone.utc),
        graph_state={"turn_id": "t1", "system_prompt": "this-turn"},
    )
    other = SessionSnapshot(
        id="snap-t0",
        session_id="s1",
        version=2,
        stage="prompt_assembler",
        created_at=datetime.now(timezone.utc),
        graph_state={"turn_id": "t0", "system_prompt": "other-turn"},
    )
    assert pick_snapshot_for_turn([other], "t1") is None
    picked = pick_snapshot_for_turn([other, matching], "t1")
    assert picked is not None
    assert picked.id == "snap-t1"
    assert picked.graph_state["system_prompt"] == "this-turn"


def test_get_flow_is_read_only_and_projects_workers():
    sessions = _FakeSessions(
        metadata={
            "worker_dispatches": [
                {
                    "task_id": "w1",
                    "child_session_id": "child-1",
                    "agent_key": "api-doc-analyst",
                    "description": "分析文档",
                    "status": "running",
                    "parent_turn_id": "t1",
                    "source_stage": "tool_executor",
                }
            ]
        },
        events=[
            _event("runtime.turn_started"),
            _event("graph.prompt_assembled", phase="prompt_assembler"),
        ],
        snapshots=[
            SessionSnapshot(
                id="snap-1",
                session_id="s1",
                version=3,
                stage="prompt_assembler",
                created_at=datetime.now(timezone.utc),
                graph_state={"turn_id": "t1", "system_prompt": "full-prompt"},
            )
        ],
    )
    service = FlowProjectionService(sessions, _FakeToolJobs())
    first = _run(service.get_flow("s1", turn_id="t1"))
    second = _run(service.get_flow("s1", turn_id="t1"))

    assert sessions.event_reads == 2
    assert len(sessions.events) == 2
    assert first.turn_id == "t1"
    assert first.graph_state["system_prompt"] == "full-prompt"
    assert [stage.id for stage in first.stages] == [
        "context_builder",
        "prompt_assembler",
        "tool_executor",
    ]
    assert first.stages[1].status.value == "done"
    assert "planner" not in {stage.id for stage in first.stages}
    assert first.workers[0].id == "worker:w1"
    assert first.workers[0].worker["child_session_id"] == "child-1"
    assert any(edge.kind == "spawn" and edge.target == "worker:w1" for edge in first.edges)
    assert second.snapshot_id == first.snapshot_id


def test_flow_route_returns_404_for_missing_session():
    app, _ = _build_app()
    response = _request(app, "GET", "/api/v1/sessions/missing/flow")
    assert response.status_code == 404


def test_flow_route_aggregates_existing_session_without_replay():
    app, store = _build_app()
    created = _request(app, "POST", "/api/v1/sessions", json={"title": "Flow session"})
    session_id = created.json()["id"]
    _run(
        store.append_event(
            session_id,
            ExecutionEvent(
                type="runtime.turn_started",
                session_id=session_id,
                timestamp=datetime.now(timezone.utc),
                payload={"turn_id": "turn-a"},
            ),
        )
    )
    session = _run(store.get_session(session_id))
    session.metadata["worker_dispatches"] = [
        {
            "task_id": "task-a",
            "agent_key": "api-suite-planner",
            "description": "规划套件",
            "status": "completed",
            "child_session_id": "child-a",
        }
    ]
    _run(store.save_session(session))
    before = _run(store.list_events(session_id))

    response = _request(app, "GET", f"/api/v1/sessions/{session_id}/flow")
    after = _run(store.list_events(session_id))

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["turn_id"] == "turn-a"
    assert body["stages"][0]["status"] == "running"
    assert body["workers"][0]["worker"]["task_id"] == "task-a"
    assert [event.type for event in after] == [event.type for event in before]
    assert "session.replay_requested" not in {event.type for event in after}
