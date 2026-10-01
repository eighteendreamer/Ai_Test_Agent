"""Local-only deterministic backend for the assistant stream browser probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, HTTPException

from src.api.routes.sessions import router as sessions_router
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
from src.domain.models import SessionRecord
from src.graph.builder import build_agent_graph
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.store import InMemorySessionStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ModelDescriptor
from src.schemas.model_config import (
    ModelConfigRecord,
    ModelInvocationRequest,
    ModelInvocationResult,
)
from src.schemas.session import (
    ChatMessage,
    MessageRole,
    RuntimeMode,
    SendMessageRequest,
    SessionMode,
    SessionStatus,
)
from src.schemas.tool_runtime import ModelToolCall

logger = logging.getLogger("assistant_stream_browser_probe")
SESSION_ID = "assistant-stream-browser-probe"
TURN_ID = "turn-assistant-stream-browser-probe"
ASSISTANT_MESSAGE_ID = "assistant-stream-browser-answer"
USER_TEXT = "验证后端 AI 分片到前端的传输。"
MODEL_KEY = "deterministic-assistant-stream-probe"
TOOL_KEY = "knowledge-rag"
TOOL_QUERY = "assistant-stream-browser-probe"
TOOL_EVIDENCE = "本地知识工具已实际执行。"
CHUNKS = [
    "这是本地确定性模型桩生成的回答：\n\n",
    "1. 后端逐片发送 AI 文本。\n",
    "2. SSE 实时送达浏览器。\n\n",
    "最终正文从持久化 transcript 对账。",
]
EXPECTED_ANSWER = "".join(CHUNKS)


class _ProbeModelRegistry:
    """Supply one local model configuration without contacting a provider."""

    def __init__(self) -> None:
        self._config = ModelConfigRecord(
            key=MODEL_KEY,
            name="Deterministic assistant stream probe",
            provider="local-probe",
            transport="openai_chat_completions",
            model_id="deterministic-probe",
            api_base_url="http://127.0.0.1",
            is_active=True,
        )

    def get_runtime_config(self, key: str) -> ModelConfigRecord:
        if key != MODEL_KEY:
            raise KeyError(key)
        return self._config

    def get_default_runtime_config(self) -> ModelConfigRecord:
        return self._config

    def resolve_for_agent(
        self,
        requested_key: str | None,
        supported_model_keys: list[str],
    ) -> ModelDescriptor:
        del requested_key, supported_model_keys
        return ModelDescriptor(
            key=MODEL_KEY,
            name=self._config.name,
            provider=self._config.provider,
            summary="Deterministic local test model; no external provider is used.",
            supports_tools=True,
            supports_reasoning=False,
        )


class _EmptyMCPRuntimeService:
    def list_active_servers(self) -> list[dict]:
        return []

    def build_prompt_blocks(self, _active_servers: list[dict] | None = None) -> list[str]:
        return []


class _DeterministicProbeModelRuntimeService(ModelRuntimeService):
    """Use the production stream-handler context with deterministic responses."""

    def __init__(self, model_registry: _ProbeModelRegistry, settings) -> None:
        super().__init__(model_registry, settings)
        self.invocation_count = 0
        self.saw_completed_tool_result = False

    async def invoke(
        self,
        model_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        if model_key != MODEL_KEY:
            raise RuntimeError(f"Probe received unexpected model key: {model_key}")
        self.invocation_count += 1
        if self.invocation_count == 1:
            available_tools = {str(item.get("name") or "") for item in request.tools}
            if TOOL_KEY not in available_tools:
                raise RuntimeError(
                    f"Probe model was not offered tool '{TOOL_KEY}'; "
                    f"available tools: {sorted(available_tools)}."
                )
            return ModelInvocationResult(
                text="",
                tool_calls=[ModelToolCall(
                    id="assistant-stream-probe-call",
                    name=TOOL_KEY,
                    arguments={"query": TOOL_QUERY, "top_k": 1},
                )],
                response_summary={"mode": "ok", "finish_reason": "tool_calls"},
            )

        if self.invocation_count != 2:
            raise RuntimeError("Probe model received more than one follow-up invocation.")

        for message in request.messages:
            if message.get("role") != "tool" or message.get("name") != TOOL_KEY:
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            output = payload.get("output") if isinstance(payload, dict) else None
            if (
                isinstance(payload, dict)
                and payload.get("status") == "completed"
                and isinstance(output, dict)
                and output.get("answer") == TOOL_EVIDENCE
            ):
                self.saw_completed_tool_result = True
                break
        if not self.saw_completed_tool_result:
            raise RuntimeError("Probe model did not receive the completed tool result.")

        for chunk in CHUNKS:
            await self._emit_stream_chunk(chunk)
            await asyncio.sleep(0.18)
        return ModelInvocationResult(
            text=EXPECTED_ANSWER,
            response_summary={"mode": "ok", "finish_reason": "stop"},
        )


def _build_probe_runtime(settings, tool_job_service: ToolJobService):
    agent_registry = AgentRegistry()
    model_registry = _ProbeModelRegistry()
    skill_registry = SkillRegistry()
    tool_registry = ToolRegistry()
    permission_service = PermissionService()
    model_runtime_service = _DeterministicProbeModelRuntimeService(model_registry, settings)
    runtime_control = RuntimeControlRegistry()
    tool_runtime_service = ToolRuntimeService(
        settings=settings,
        tool_job_service=tool_job_service,
    )
    tool_invocations: list[dict[str, str]] = []

    async def run_probe_knowledge(arguments: dict, context) -> dict:
        tool_invocations.append({
            "tool_key": context.tool_key,
            "call_id": context.call_id,
            "query": str(arguments.get("query") or ""),
        })
        return {
            "status": "completed",
            "ok": True,
            "summary": "Deterministic local knowledge lookup completed.",
            "answer": TOOL_EVIDENCE,
            "chunks": [{"content": TOOL_EVIDENCE, "source": "local-probe"}],
        }

    # Only the external knowledge provider is replaced. The registered tool,
    # permission gate, LangGraph tool node, ToolRuntimeService dispatcher, and
    # ToolJobService lifecycle remain the production implementations.
    tool_runtime_service._handlers[TOOL_KEY] = run_probe_knowledge
    graph = build_agent_graph(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        model_registry=model_registry,
        skill_registry=skill_registry,
        skill_runtime_service=SkillRuntimeService(skill_registry),
        mcp_runtime_service=_EmptyMCPRuntimeService(),
        memory_runtime_service=None,
        test_case_embedding_service=None,
        permission_service=permission_service,
        prompt_assembly_service=PromptAssemblyService(),
        model_runtime_service=model_runtime_service,
        tool_runtime_service=tool_runtime_service,
        runtime_control=runtime_control,
        tool_job_service=tool_job_service,
    )
    runtime_service = RuntimeService(
        graph=graph,
        model_runtime_service=model_runtime_service,
        tool_runtime_service=tool_runtime_service,
        tool_registry=tool_registry,
        runtime_control=runtime_control,
        permission_service=permission_service,
        agent_registry=agent_registry,
        skill_registry=skill_registry,
        tool_job_service=tool_job_service,
        max_iterations=4,
    )
    return runtime_service, model_runtime_service, tool_invocations


def create_app() -> FastAPI:
    settings = get_settings()
    settings.orchestration.session_sse_poll_seconds = 0.02
    store = InMemorySessionStore()
    tool_job_service = ToolJobService(InMemoryToolJobStore())
    runtime_service, model_runtime_service, tool_invocations = _build_probe_runtime(
        settings,
        tool_job_service,
    )
    mode_registry = ModeRegistry()
    input_orchestrator_service = InputOrchestratorService(mode_registry)
    service = SessionService(
        store,
        input_orchestrator_service,
        runtime_service,
        mode_registry,
    )
    start_lock = asyncio.Lock()
    started = False
    active_tasks: set[asyncio.Task[None]] = set()
    probe_result: dict[str, str] = {"status": "ready"}

    async def seed_probe_session() -> None:
        now = datetime.now(UTC)
        await store.save_session(SessionRecord(
            id=SESSION_ID,
            title="Assistant stream browser probe",
            status=SessionStatus.running,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=now,
            updated_at=now,
            messages=[ChatMessage(
                id="assistant-stream-browser-user",
                role=MessageRole.user,
                content=USER_TEXT,
                created_at=now,
                metadata={"turn_id": TURN_ID},
            )],
        ))

    async def cancel_stream_tasks() -> None:
        for task in tuple(active_tasks):
            task.cancel()
        if active_tasks:
            await asyncio.gather(*tuple(active_tasks), return_exceptions=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await seed_probe_session()
        try:
            yield
        finally:
            await cancel_stream_tasks()

    app = FastAPI(title="Assistant stream browser probe", lifespan=lifespan)
    app.state.session_service = service
    app.state.tool_job_service = tool_job_service
    app.state.settings = settings
    app.include_router(sessions_router, prefix="/api/v1")

    async def stream_deterministic_answer() -> None:
        probe_result["status"] = "running"
        try:
            session = await store.get_session(SESSION_ID)
            if session is None:
                raise RuntimeError(f"Probe session disappeared: {SESSION_ID}")
            request = input_orchestrator_service.orchestrate(
                session,
                SendMessageRequest(
                    content=USER_TEXT,
                    agent_key="coordinator",
                    model_key=MODEL_KEY,
                ),
            )
            request = request.model_copy(update={"turn_id": TURN_ID})
            on_text_delta = service._build_stream_chunk_handler(
                SESSION_ID,
                TURN_ID,
                ASSISTANT_MESSAGE_ID,
            )
            runtime_result = await on_text_delta.run(
                service._event_sink(SESSION_ID).run,
                runtime_service.execute_turn,
                session,
                request,
                on_model_chunk=on_text_delta,
            )

            completed_tools = [
                item
                for item in runtime_result.state.get("tool_results", [])
                if item.get("tool_key") == TOOL_KEY and item.get("status") == "completed"
            ]
            if len(completed_tools) != 1 or len(tool_invocations) != 1:
                raise RuntimeError(
                    "Probe did not complete exactly one knowledge tool execution."
                )
            if not model_runtime_service.saw_completed_tool_result:
                raise RuntimeError("Probe final model response did not consume tool output.")
            if runtime_result.output_text != EXPECTED_ANSWER:
                raise RuntimeError("Probe final model response differed from streamed output.")

            await service._finalize_runtime_result(
                session,
                runtime_result,
                ASSISTANT_MESSAGE_ID,
                USER_TEXT,
            )
            probe_result.update({
                "status": "completed",
                "tool_result_status": str(completed_tools[0].get("status") or ""),
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            probe_result.update({
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            })
            logger.exception("assistant_stream_browser_probe_failed")

    @app.post("/api/v1/__test__/assistant-stream/start", status_code=202)
    async def start_stream() -> dict[str, str]:
        nonlocal started
        async with start_lock:
            if started:
                return {"status": "already_started"}
            started = True
            probe_result["status"] = "running"
            task = asyncio.create_task(stream_deterministic_answer())
            active_tasks.add(task)

            def task_finished(finished: asyncio.Task[None]) -> None:
                active_tasks.discard(finished)
                if finished.cancelled():
                    return
                error = finished.exception()
                if error is not None:
                    logger.error(
                        "assistant_stream_browser_probe_failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )

            task.add_done_callback(task_finished)
        return {"status": "started"}

    @app.get("/api/v1/__test__/health")
    async def probe_health() -> dict[str, str]:
        if await store.get_session(SESSION_ID) is None:
            raise HTTPException(status_code=503, detail="Probe session is not initialized")
        jobs = await tool_job_service.list_jobs(session_id=SESSION_ID)
        tool_job_status = jobs[0].status.value if jobs else ""
        return {
            "status": probe_result["status"],
            "session_id": SESSION_ID,
            "model_invocation_count": str(model_runtime_service.invocation_count),
            "model_received_completed_tool_result": str(
                model_runtime_service.saw_completed_tool_result
            ).lower(),
            "tool_invocation_count": str(len(tool_invocations)),
            "tool_query": tool_invocations[0]["query"] if tool_invocations else "",
            "tool_result_status": probe_result.get("tool_result_status", ""),
            "tool_job_status": tool_job_status,
            "error_type": probe_result.get("error_type", ""),
            "error": probe_result.get("error", ""),
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=13032)
    args = parser.parse_args()
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
