from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.mcp_runtime_service import MCPRuntimeService
from src.application.tool_job_service import ToolJobService
from src.core.config import Settings
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


@dataclass
class ToolExecutionContext:
    session_id: str
    turn_id: str
    trace_id: str
    user_message: str
    normalized_input: str
    context_bundle: dict[str, Any]
    selected_agent_key: str = ""
    selected_model_key: str = ""
    tool_job_id: str = ""


class ToolRuntimeService:
    def __init__(
        self,
        request_timeout_seconds: int = 20,
        settings: Settings | None = None,
        mcp_runtime_service: MCPRuntimeService | None = None,
        memory_runtime_service: MemoryRuntimeService | None = None,
        tool_job_service: ToolJobService | None = None,
        coordinator_runtime_service=None,
    ) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._settings = settings
        self._docs_dir = Path(__file__).resolve().parents[3] / "docs"
        self._mcp_runtime_service = mcp_runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._tool_job_service = tool_job_service
        self._coordinator_runtime_service = coordinator_runtime_service
        self._handlers = {
            "workflow-router": self._run_workflow_router,
            "subagent-dispatch": self._run_subagent_dispatch,
            "knowledge-rag": self._run_knowledge_rag,
            "dom-inspector": self._run_dom_inspector,
            "browser-automation": self._run_browser_automation,
            "file-artifact-manager": self._run_file_artifact_manager,
        }

    def set_coordinator_runtime_service(self, coordinator_runtime_service) -> None:
        self._coordinator_runtime_service = coordinator_runtime_service

    def set_memory_runtime_service(self, memory_runtime_service: MemoryRuntimeService) -> None:
        self._memory_runtime_service = memory_runtime_service

    def set_tool_job_service(self, tool_job_service: ToolJobService) -> None:
        self._tool_job_service = tool_job_service

    def has_handler(self, tool_key: str) -> bool:
        return tool_key in self._handlers

    async def execute(
        self,
        tool: ToolDescriptor,
        call: ModelToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionRecord:
        started_at = datetime.utcnow()
        job = None
        handler = self._handlers.get(tool.key)
        if handler is None:
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="failed",
                summary=f"No runtime handler is registered for tool '{tool.key}'.",
                trace_id=context.trace_id,
                input=call.arguments,
                output={},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        try:
            job_context = context
            if self._tool_job_service is not None:
                job = await self._tool_job_service.create_job(
                    tool=tool,
                    call_id=call.id,
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    input_payload=call.arguments,
                    metadata={
                        "selected_agent_key": context.selected_agent_key,
                        "selected_model_key": context.selected_model_key,
                    },
                )
                await self._tool_job_service.mark_running(job)
                job_context = ToolExecutionContext(
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    user_message=context.user_message,
                    normalized_input=context.normalized_input,
                    context_bundle=context.context_bundle,
                    selected_agent_key=context.selected_agent_key,
                    selected_model_key=context.selected_model_key,
                    tool_job_id=job.id,
                )

            result = await handler(call.arguments, job_context)
            resolved_status = self._resolve_result_status(result)
            summary = str(result.get("summary", f"Tool '{tool.key}' completed."))
            if job is not None and self._tool_job_service is not None:
                if resolved_status == "failed":
                    await self._tool_job_service.mark_failed(
                        job.id,
                        summary=summary,
                        error_message=str(result.get("error") or summary),
                        output_payload=result,
                    )
                elif resolved_status == "partial":
                    await self._tool_job_service.mark_partial(
                        job.id,
                        summary=summary,
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
                elif resolved_status == "waiting_approval":
                    await self._tool_job_service.mark_waiting_approval(
                        job.id,
                        summary=summary,
                        metadata={"output_payload": result},
                    )
                elif resolved_status == "denied":
                    await self._tool_job_service.mark_denied(
                        job.id,
                        summary=summary,
                        output_payload=result,
                    )
                else:
                    await self._tool_job_service.mark_completed(
                        job.id,
                        summary=summary,
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status=resolved_status,
                summary=summary,
                trace_id=str(result.get("trace_id") or context.trace_id or ""),
                job_id=job.id if job is not None else None,
                input=call.arguments,
                output=result,
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        except Exception as exc:
            if job is not None and self._tool_job_service is not None:
                await self._tool_job_service.mark_failed(
                    job.id,
                    summary=f"Tool '{tool.key}' failed.",
                    error_message=str(exc),
                    output_payload={"error": str(exc)},
                )
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="failed",
                summary=f"Tool '{tool.key}' failed: {exc}",
                trace_id=context.trace_id,
                job_id=job.id if job is not None else None,
                input=call.arguments,
                output={"error": str(exc)},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

    def _resolve_result_status(self, result: dict[str, Any]) -> str:
        explicit_status = str(result.get("status") or "").strip().lower()
        if explicit_status in {"completed", "partial", "failed", "waiting_approval", "denied"}:
            return explicit_status
        if result.get("ok") is False:
            workers = result.get("workers")
            if isinstance(workers, list):
                running_count = sum(1 for item in workers if isinstance(item, dict) and item.get("status") == "running")
                failed_count = sum(1 for item in workers if isinstance(item, dict) and item.get("status") == "failed")
                if running_count > 0 and failed_count > 0:
                    return "partial"
            return "failed"
        return "completed"

    async def _run_knowledge_rag(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.normalized_input).strip()
        top_k = int(arguments.get("top_k") or 3)
        if not query:
            return {
                "summary": "No query was provided for knowledge retrieval.",
                "chunks": [],
            }

        memory_matches: list[dict[str, Any]] = []
        if self._memory_runtime_service is not None:
            memory_result = await self._memory_runtime_service.retrieve_for_turn(
                session_id=context.session_id,
                trace_id=context.trace_id,
                query=query,
                context=context.context_bundle,
            )
            memory_matches = [
                {
                    "source": item.source or "arangodb",
                    "score": item.score or 0.0,
                    "excerpt": item.summary or item.content,
                    "kind": item.kind,
                }
                for item in memory_result.hits
            ]

        tokens = [token.lower() for token in re.split(r"\W+", query) if token.strip()]
        matches: list[dict[str, Any]] = []

        if self._docs_dir.exists():
            for file_path in sorted(self._docs_dir.glob("*.md")):
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                lowered = text.lower()
                filename_score = sum(file_path.name.lower().count(token) for token in tokens)
                score = (sum(lowered.count(token) for token in tokens) + filename_score) or 0
                if score <= 0:
                    continue
                excerpt = _build_excerpt(text, tokens)
                matches.append(
                    {
                        "source": file_path.name,
                        "score": score,
                        "excerpt": excerpt,
                    }
                )

        matches.sort(key=lambda item: item["score"], reverse=True)
        selected = [*memory_matches, *matches][:top_k]
        return {
            "summary": f"Retrieved {len(selected)} knowledge chunks for query '{query}'.",
            "chunks": selected,
            "query": query,
        }

    async def _run_subagent_dispatch(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        coordinator_runtime = self._require_coordinator_runtime()
        return await coordinator_runtime.dispatch(
            payload=arguments,
            context=asdict(context),
        )

    async def _run_workflow_router(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.user_message).lower()
        route = "coordinator"
        rationale = "Defaulted to coordinator for orchestration."
        if any(token in query for token in ["browser", "page", "ui", "selenium", "playwright"]):
            route = "ui-executor"
            rationale = "Detected browser or UI execution intent."
        elif any(token in query for token in ["api", "payload", "response", "request"]):
            route = "api-verifier"
            rationale = "Detected API verification intent."
        elif any(token in query for token in ["report", "summary", "结论", "报告"]):
            route = "report-analyst"
            rationale = "Detected reporting intent."
        elif any(token in query for token in ["plan", "case", "scenario", "用例", "测试点"]):
            route = "qa-planner"
            rationale = "Detected planning or case design intent."

        return {
            "summary": f"Recommended execution route: {route}.",
            "route": route,
            "rationale": rationale,
        }

    async def _run_dom_inspector(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "inspect-page",
            arguments,
            asdict(context),
        )

    async def _run_browser_automation(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "browser-automation",
            arguments,
            asdict(context),
        )

    async def _run_file_artifact_manager(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "filesystem-mcp",
            "write-artifact",
            arguments,
            asdict(context),
        )

    def _require_mcp_runtime(self) -> MCPRuntimeService:
        if self._mcp_runtime_service is None:
            raise RuntimeError("MCP runtime service is not configured.")
        return self._mcp_runtime_service

    def _require_coordinator_runtime(self):
        if self._coordinator_runtime_service is None:
            raise RuntimeError("Coordinator runtime service is not configured.")
        return self._coordinator_runtime_service


def _build_excerpt(text: str, tokens: list[str], radius: int = 140) -> str:
    lowered = text.lower()
    pivot = min(
        (lowered.find(token) for token in tokens if lowered.find(token) >= 0),
        default=0,
    )
    start = max(0, pivot - radius)
    end = min(len(text), pivot + radius)
    return " ".join(text[start:end].split())
