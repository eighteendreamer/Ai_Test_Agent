from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Support both:
# 1. `python Agent_Server/src/main.py`
# 2. `uvicorn src.main:app --reload`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.routes.health import router as health_router
from src.api.routes.registry import router as registry_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.settings import router as settings_router
from src.application.coordinator_runtime_service import CoordinatorRuntimeService
from src.application.embedding_service import EmbeddingService
from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.mcp_runtime_service import MCPRuntimeService
from src.application.model_runtime_service import ModelRuntimeService
from src.application.permission_service import PermissionService
from src.application.prompt_service import PromptSubmissionService
from src.application.registry_service import RegistryService
from src.application.runtime_service import RuntimeService
from src.application.session_service import SessionService
from src.application.skill_runtime_service import SkillRuntimeService
from src.application.settings_service import SettingsService
from src.application.tool_job_service import ToolJobService
from src.application.tool_runtime_service import ToolRuntimeService
from src.core.config import get_settings
from src.graph.builder import build_agent_graph
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.qdrant_memory_store import QdrantMemoryStore
from src.registry.agents import AgentRegistry
from src.registry.mcp import MCPRegistry
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.store import MySQLSessionStore
from src.runtime.tool_job_store import MySQLToolJobStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = MySQLSessionStore(settings)
    await store.initialize()
    agent_registry = AgentRegistry()
    tool_registry = ToolRegistry()
    model_config_store = MySQLModelConfigStore(settings)
    model_config_store.initialize()
    email_config_store = MySQLEmailConfigStore(settings)
    email_config_store.initialize()
    model_registry = ModelRegistry(model_config_store)
    skill_registry = SkillRegistry()
    mcp_registry = MCPRegistry()
    skill_runtime_service = SkillRuntimeService(skill_registry=skill_registry)
    mcp_runtime_service = MCPRuntimeService(mcp_registry=mcp_registry, settings=settings)
    embedding_service = EmbeddingService(settings=settings)
    memory_store = QdrantMemoryStore(settings=settings)
    memory_runtime_service = MemoryRuntimeService(
        memory_store=memory_store,
        embedding_service=embedding_service,
        top_k=settings.memory_top_k,
    )
    await memory_runtime_service.initialize()
    tool_job_store = MySQLToolJobStore(settings=settings)
    tool_job_service = ToolJobService(
        store=tool_job_store,
        heartbeat_timeout_seconds=settings.tool_job_heartbeat_timeout_seconds,
    )
    await tool_job_service.initialize()
    permission_service = PermissionService()
    prompt_service = PromptSubmissionService()
    model_runtime_service = ModelRuntimeService(model_registry=model_registry, settings=settings)
    tool_runtime_service = ToolRuntimeService(
        request_timeout_seconds=settings.llm_request_timeout_seconds,
        settings=settings,
        mcp_runtime_service=mcp_runtime_service,
        memory_runtime_service=memory_runtime_service,
        tool_job_service=tool_job_service,
    )
    graph = build_agent_graph(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        model_registry=model_registry,
        skill_registry=skill_registry,
        skill_runtime_service=skill_runtime_service,
        mcp_runtime_service=mcp_runtime_service,
        memory_runtime_service=memory_runtime_service,
        permission_service=permission_service,
        model_runtime_service=model_runtime_service,
        tool_runtime_service=tool_runtime_service,
        tool_job_service=tool_job_service,
    )
    runtime_service = RuntimeService(
        graph=graph,
        model_runtime_service=model_runtime_service,
        tool_runtime_service=tool_runtime_service,
        tool_registry=tool_registry,
        max_iterations=settings.runtime_max_iterations,
    )

    app.state.settings = settings
    app.state.store = store
    app.state.agent_registry = agent_registry
    app.state.tool_registry = tool_registry
    app.state.model_config_store = model_config_store
    app.state.email_config_store = email_config_store
    app.state.model_registry = model_registry
    app.state.skill_registry = skill_registry
    app.state.mcp_registry = mcp_registry
    app.state.skill_runtime_service = skill_runtime_service
    app.state.mcp_runtime_service = mcp_runtime_service
    app.state.embedding_service = embedding_service
    app.state.memory_store = memory_store
    app.state.memory_runtime_service = memory_runtime_service
    app.state.tool_job_store = tool_job_store
    app.state.tool_job_service = tool_job_service
    app.state.memory_backend = memory_runtime_service.backend
    app.state.permission_service = permission_service
    app.state.prompt_service = prompt_service
    app.state.graph = graph
    app.state.model_runtime_service = model_runtime_service
    app.state.tool_runtime_service = tool_runtime_service
    app.state.runtime_service = runtime_service
    session_service = SessionService(
        store=store,
        prompt_service=prompt_service,
        runtime_service=runtime_service,
        memory_runtime_service=memory_runtime_service,
    )
    coordinator_runtime_service = CoordinatorRuntimeService(
        settings=settings,
        store=store,
        session_service=session_service,
        agent_registry=agent_registry,
    )
    tool_runtime_service.set_coordinator_runtime_service(coordinator_runtime_service)
    app.state.coordinator_runtime_service = coordinator_runtime_service
    app.state.session_service = session_service
    app.state.registry_service = RegistryService(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        model_registry=model_registry,
        skill_registry=skill_registry,
        mcp_registry=mcp_registry,
    )
    app.state.settings_service = SettingsService(
        model_config_store=model_config_store,
        email_config_store=email_config_store,
    )
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix=settings.api_v1_prefix)
app.include_router(registry_router, prefix=settings.api_v1_prefix)
app.include_router(sessions_router, prefix=settings.api_v1_prefix)
app.include_router(settings_router, prefix=settings.api_v1_prefix)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=False)
