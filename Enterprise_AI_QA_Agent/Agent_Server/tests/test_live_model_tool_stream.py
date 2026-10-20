"""Opt-in current configured model -> production read-only tool -> real SSE.

Business sessions/jobs exist only in memory. Model configuration is read from
MySQL without initializing or writing it. No model/tool/HTTP executor is replaced.
Uses SessionService's no-dispatcher compatibility entry and the formal SSE route.
No durable dispatcher, MCP, memory provider, browser, restart or PostgreSQL
persistence is covered.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from scripts.assistant_stream_browser_probe import _EmptyMCPRuntimeService
from src.api.routes.sessions import router
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.orchestration.input_orchestrator_service import (
    InputOrchestratorService,
)
from src.application.permissions.permission_service import PermissionService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.sessions.session_service import SessionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.core.config import get_settings
from src.graph.builder import build_agent_graph
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.registry.agents import AgentRegistry
from src.registry.models import ModelRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.store import InMemorySessionStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.session import SendMessageRequest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_MODEL_TOOL_TESTS") != "1",
    reason="requires an active configured model and actual provider access; model-tools enables it",
)


def live_application(*, settings=None, session_store=None, job_store=None):
    settings = settings or get_settings().model_copy(deep=True)
    settings.orchestration.session_sse_poll_seconds = 0.03
    registry = ModelRegistry(MySQLModelConfigStore(settings))
    model = registry.get_default_runtime_config()
    assert model.supports_tools, "Current default model must support the tool acceptance contract."
    agents, tools, skills = AgentRegistry(), ToolRegistry(), SkillRegistry()
    control, permissions = RuntimeControlRegistry(), PermissionService()
    store = session_store if session_store is not None else InMemorySessionStore()
    jobs = ToolJobService(job_store if job_store is not None else InMemoryToolJobStore())
    model_runtime = ModelRuntimeService(registry, settings)
    tool_runtime = ToolRuntimeService(settings=settings, session_store=store, tool_job_service=jobs)
    graph = build_agent_graph(
        agent_registry=agents, tool_registry=tools, model_registry=registry,
        skill_registry=skills, skill_runtime_service=SkillRuntimeService(skills),
        mcp_runtime_service=_EmptyMCPRuntimeService(), memory_runtime_service=None,
        test_case_embedding_service=None, permission_service=permissions,
        prompt_assembly_service=PromptAssemblyService(), model_runtime_service=model_runtime,
        tool_runtime_service=tool_runtime, runtime_control=control, tool_job_service=jobs,
    )
    runtime = RuntimeService(
        graph=graph, model_runtime_service=model_runtime, tool_runtime_service=tool_runtime,
        tool_registry=tools, runtime_control=control, permission_service=permissions,
        agent_registry=agents, skill_registry=skills, tool_job_service=jobs, max_iterations=4,
    )
    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), runtime, modes)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.session_service = service
    app.state.tool_job_service = jobs
    app.include_router(router, prefix="/api/v1")
    return app, model.key


@pytest.mark.asyncio
async def test_current_model_executes_read_only_tool_and_stream_matches_final_transcript(tmp_path):
    app, model_key = live_application()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    receiving = None
    try:
        async def started():
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        await asyncio.wait_for(started(), 10)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=180) as client:
            created = await client.post("/sessions", json={
                "title": "Isolated live model/tool/SSE acceptance", "mode_key": "default",
                "preferred_model": model_key, "selected_agent": "coordinator",
            })
            created.raise_for_status()
            session_id = created.json()["id"]
            connected = asyncio.Event()

            async def receive():
                async with (
                    httpx.AsyncClient(timeout=180) as stream_client,
                    stream_client.stream("GET", f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/events") as response,
                ):
                    assert response.status_code == 200
                    connected.set()
                    events = []
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            event = json.loads(line[6:])
                            events.append(event)
                            # Graph completion precedes flushing the text writer.
                            # This event is committed with the final transcript.
                            if event.get("type") in {"turn.completed", "turn.interrupted", "turn.failed"}:
                                return events

            receiving = asyncio.create_task(receive())
            await asyncio.wait_for(connected.wait(), 10)
            result = await app.state.session_service.send_message(session_id, SendMessageRequest(
                content="Call session-history once to read the recent messages of THIS session. Use scope current_session and action history_summary. Then answer briefly with LIVE_MODEL_TOOL_OK. Do not call other tools.",
                agent_key="coordinator", model_key=model_key,
                context={"requested_tool_keys": ["session-history"]},
            ))
            detail = result.session.model_dump(mode="json")
            events = await asyncio.wait_for(receiving, 180)
            trace_event_types = {
                "model.request_prepared", "model.response_received", "model.invocation_failed",
                "tool.execution_started", "tool.execution_completed", "tool.execution_failed",
                "tool.execution_denied", "tool.execution_blocked", "tool.execution_skipped",
            }
            trace_fields = (
                "turn_id", "trace_id", "logical_call_id", "tool_call_ids", "call_id",
                "tool_key", "tool_call_count", "http_request_attempt_count", "status_code",
                "error_type", "cause_error_type", "completion_status",
            )
            model_trace = [
                {"type": event.get("type"), "payload": {
                    key: (event.get("payload") or {})[key]
                    for key in trace_fields if key in (event.get("payload") or {})
                }}
                for event in events if event.get("type") in trace_event_types
            ]
            observations = {
                "status": detail["status"], "model_key": model_key,
                "session_storage": "isolated_memory", "execution_entry": "session_service_compatibility",
                "durable_dispatcher_verified": False, "browser_verified": False, "release_ready": False,
                "trace_events": model_trace,
                "model_logical_call_count": sum(1 for event in model_trace if event["type"] == "model.request_prepared"),
                "http_attempt_count": sum(int(event["payload"].get("http_request_attempt_count") or 0)
                    for event in model_trace if event["type"] in {"model.response_received", "model.invocation_failed"}),
            }
            evidence_dir = Path(os.getenv("QA_TEST_GATE_OUTPUT_DIR") or tmp_path)
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "acceptance.json").write_text(
                json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            assert detail["status"] == "completed", "Actual model turn did not complete; inspect its event evidence."
            persisted_response = await client.get(f"/sessions/{session_id}")
            persisted_response.raise_for_status()
            assert persisted_response.json()["messages"] == detail["messages"]
            jobs_response = await client.get(f"/sessions/{session_id}/tool-jobs")
            jobs_response.raise_for_status()
            jobs = jobs_response.json()
            assert jobs and all(job["tool_key"] == "session-history" and job["status"] == "completed" for job in jobs)
            assistants = [message for message in detail["messages"] if message["role"] == "assistant" and message["content"]]
            assert assistants, "Actual model produced no final assistant text."
            final = assistants[-1]
            deltas = [event["payload"]["delta"] for event in events
                      if event["type"] == "assistant.stream.delta" and event["payload"].get("message_id") == final["id"]]
            assert deltas and "".join(deltas) == final["content"]
            assert any(event["type"] == "assistant.stream.completed" for event in events)
            response_call_ids = {
                call_id for event in model_trace if event["type"] == "model.response_received"
                for call_id in event["payload"].get("tool_call_ids", [])
            }
            executed_call_ids = {
                event["payload"].get("call_id") for event in model_trace
                if event["type"].startswith("tool.execution_") and event["payload"].get("call_id")
            }
            assert response_call_ids == executed_call_ids, model_trace
            observations.update({
                "status": "passed", "completed_tools": len(jobs), "sse_events": len(events),
                "delta_count": len(deltas), "final_chars": len(final["content"]),
                "response_tool_call_ids": sorted(response_call_ids),
                "executed_tool_call_ids": sorted(executed_call_ids),
            })
            (evidence_dir / "acceptance.json").write_text(
                json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8",
            )
    finally:
        if receiving is not None:
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(serving, 15)
        listener.close()
