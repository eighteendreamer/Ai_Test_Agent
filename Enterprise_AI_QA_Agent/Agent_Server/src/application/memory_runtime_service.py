from __future__ import annotations

from typing import Any

from src.application.embedding_service import EmbeddingService
from src.infrastructure.qdrant_memory_store import QdrantMemoryStore
from src.runtime.execution_logging import truncate_text
from src.schemas.memory import MemorySearchRequest, MemorySearchResult, MemoryWriteRequest


class MemoryRuntimeService:
    def __init__(
        self,
        memory_store: QdrantMemoryStore,
        embedding_service: EmbeddingService,
        top_k: int = 6,
    ) -> None:
        self._memory_store = memory_store
        self._embedding_service = embedding_service
        self._top_k = top_k

    async def initialize(self) -> None:
        await self._memory_store.initialize()

    @property
    def backend(self) -> str:
        return self._memory_store.backend

    async def refresh_backend_status(self) -> str:
        return await self._memory_store.refresh_connection_status()

    async def retrieve_for_turn(
        self,
        session_id: str,
        trace_id: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> MemorySearchResult:
        if not query.strip():
            return MemorySearchResult(query=query, backend=self.backend)

        request = MemorySearchRequest(
            query=query,
            session_id=session_id,
            trace_id=trace_id,
            top_k=self._top_k,
            tags=self._derive_read_tags(context or {}),
        )
        vector = await self._embedding_service.embed_text(query)
        hits = await self._memory_store.search(request, vector)
        prompt_blocks = [
            (
                f"- [{hit.kind}] {hit.summary or truncate_text(hit.content, 140)} "
                f"(source={hit.source or 'memory'}, score={hit.score or 0:.3f}, stale={hit.stale})"
            )
            for hit in hits
        ]
        return MemorySearchResult(
            query=query,
            hits=hits,
            prompt_blocks=prompt_blocks,
            source_count=len(hits),
            backend=self.backend,
        )

    async def write_turn_memory(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        user_message: str,
        assistant_message: str,
        tool_results: list[dict[str, Any]],
        context_bundle: dict[str, Any],
    ) -> list[str]:
        write_ids: list[str] = []
        requests = self._build_turn_write_policy(
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            user_message=user_message,
            assistant_message=assistant_message,
            tool_results=tool_results,
            context_bundle=context_bundle,
        )
        for request in requests:
            vector = await self._embedding_service.embed_text(request.content)
            point = await self._memory_store.write(request, vector)
            if point is not None:
                write_ids.append(point.id)
        return write_ids

    async def write_page_memory(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        title: str,
        current_url: str,
        summary: str,
        selectors: list[str] | None = None,
        assertions: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> str | None:
        content_parts = [
            f"title={title or 'n/a'}",
            f"url={current_url or 'n/a'}",
            f"summary={summary or 'n/a'}",
        ]
        if selectors:
            content_parts.append("selectors=" + ", ".join(selectors[:12]))
        if assertions:
            content_parts.append("assertions=" + ", ".join(str(item.get("type", "assert")) for item in assertions[:12]))
        request = MemoryWriteRequest(
            scope="page",
            kind="page_knowledge",
            content="\n".join(content_parts),
            summary=truncate_text(summary or title or current_url, 160),
            tags=["page", "browser", "knowledge"],
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            source=current_url,
            metadata={
                "title": title,
                "url": current_url,
                "assertion_count": len(assertions or []),
                "artifact_count": len(artifacts or []),
            },
        )
        vector = await self._embedding_service.embed_text(request.content)
        point = await self._memory_store.write(request, vector)
        return point.id if point is not None else None

    def _build_turn_write_policy(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        user_message: str,
        assistant_message: str,
        tool_results: list[dict[str, Any]],
        context_bundle: dict[str, Any],
    ) -> list[MemoryWriteRequest]:
        requests = [
            MemoryWriteRequest(
                scope="session",
                kind="episodic",
                content=f"User goal: {user_message.strip()}",
                summary=truncate_text(user_message.strip(), 140),
                tags=["user_goal", "turn_input"],
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                source="session.user",
                metadata={"context_keys": sorted(context_bundle.keys())},
            ),
            MemoryWriteRequest(
                scope="session",
                kind="semantic",
                content=f"Assistant outcome: {assistant_message.strip()}",
                summary=truncate_text(assistant_message.strip(), 160),
                tags=["assistant_summary", "turn_output"],
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                source="session.assistant",
            ),
        ]
        for tool_result in tool_results:
            if str(tool_result.get("status")) != "completed":
                continue
            summary = str(tool_result.get("summary") or "").strip()
            output = tool_result.get("output") or {}
            content = (
                f"Tool {tool_result.get('tool_key', 'unknown')} completed. "
                f"Summary: {summary}. Output excerpt: {truncate_text(str(output), 220)}"
            )
            requests.append(
                MemoryWriteRequest(
                    scope="session",
                    kind="verification" if "assert" in str(output).lower() else "episodic",
                    content=content,
                    summary=truncate_text(summary or content, 160),
                    tags=["tool_result", str(tool_result.get("tool_key") or "tool")],
                    session_id=session_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    source=f"tool.{tool_result.get('tool_key', 'unknown')}",
                    metadata={
                        "tool_key": tool_result.get("tool_key"),
                        "artifact_count": len((output or {}).get("artifacts", []))
                        if isinstance(output, dict)
                        else 0,
                    },
                )
            )
        return requests

    def _derive_read_tags(self, context: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        target_url = str(context.get("target_url") or "")
        if target_url:
            tags.extend(["page", "browser"])
        if context.get("verification_mode"):
            tags.append("verification")
        return tags
