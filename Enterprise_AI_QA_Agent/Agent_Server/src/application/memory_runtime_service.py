from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from src.infrastructure.arango_memory_store import ArangoDocumentMemoryStore
from src.runtime.execution_logging import truncate_text
from src.schemas.observation import ObservationRecord
from src.schemas.memory import MemorySearchRequest, MemorySearchResult, MemoryWriteRequest


class MemoryRuntimeService:
    def __init__(
        self,
        memory_store: ArangoDocumentMemoryStore,
        top_k: int = 6,
    ) -> None:
        self._memory_store = memory_store
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

        current_session_request = MemorySearchRequest(
            query=query,
            session_id=session_id,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            top_k=max(self._top_k, 6),
            tags=self._derive_read_tags(context or {}),
            day_window=0,
        )
        historical_request = MemorySearchRequest(
            query=query,
            session_id=None,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            top_k=max(self._top_k, 8),
            tags=self._derive_read_tags(context or {}),
            day_window=0,
        )
        global_request = MemorySearchRequest(
            query=query,
            session_id=None,
            trace_id=trace_id,
            scopes=["global"],
            top_k=max(self._top_k, 6),
            tags=self._derive_read_tags(context or {}),
            day_window=0,
        )
        current_session_hits = await self._memory_store.search(current_session_request)
        historical_hits = await self._memory_store.search(historical_request)
        global_hits = await self._memory_store.search(global_request)
        total_current_session_docs = await self._memory_store.count_documents(current_session_request)
        total_historical_docs = await self._memory_store.count_documents(historical_request)
        total_global_docs = await self._memory_store.count_documents(global_request)
        hits = self._merge_ranked_hits(
            current_session_hits,
            historical_hits,
            global_hits,
            self._top_k,
        )
        prompt_blocks = [
            (
                "Memory inventory summary: "
                f"current_session docs={total_current_session_docs}, "
                f"all_history session/page/artifact docs={total_historical_docs}, "
                f"global long-term docs={total_global_docs}, "
                f"retrieved_hits={len(hits)}."
            ),
            *[
                (
                    f"- [{hit.kind}] {hit.summary or truncate_text(hit.content, 140)} "
                    f"(scope={hit.scope}, source={hit.source or 'memory'}, score={hit.score or 0:.3f}, stale={hit.stale})"
                )
                for hit in hits
            ],
        ]
        return MemorySearchResult(
            query=query,
            hits=hits,
            prompt_blocks=prompt_blocks,
            source_count=len(hits),
            total_session_docs=total_historical_docs,
            total_global_docs=total_global_docs,
            total_docs=total_historical_docs + total_global_docs,
            backend=self.backend,
        )

    async def retrieve_observation_context(
        self,
        session_id: str | None,
        trace_id: str,
        query: str,
        context: dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> MemorySearchResult:
        if not query.strip():
            return MemorySearchResult(query=query, backend=self.backend)

        current_session_request = MemorySearchRequest(
            query=query,
            session_id=session_id,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            kinds=["observation"],
            top_k=max(top_k, 4),
            tags=self._derive_read_tags(context or {}),
            day_window=0,
        )
        historical_request = MemorySearchRequest(
            query=query,
            session_id=None,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            kinds=["observation"],
            top_k=max(top_k, 6),
            tags=self._derive_read_tags(context or {}),
            day_window=0,
        )
        current_hits = await self._memory_store.search(current_session_request)
        historical_hits = await self._memory_store.search(historical_request)
        total_current_docs = await self._memory_store.count_documents(current_session_request)
        total_historical_docs = await self._memory_store.count_documents(historical_request)
        hits = self._merge_ranked_hits(current_hits, historical_hits, [], top_k)
        prompt_blocks = []
        if hits:
            prompt_blocks = [
                (
                    "Historical testing observations: "
                    f"current_session={total_current_docs}, "
                    f"all_history={total_historical_docs}, "
                    f"retrieved_hits={len(hits)}."
                ),
                *[
                    self._format_observation_prompt_block(hit)
                    for hit in hits
                ],
            ]
        return MemorySearchResult(
            query=query,
            hits=hits,
            prompt_blocks=prompt_blocks,
            source_count=len(hits),
            total_session_docs=total_current_docs,
            total_global_docs=0,
            total_docs=total_historical_docs,
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
            point = await self._memory_store.write(request)
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
            content_parts.append(
                "assertions="
                + ", ".join(str(item.get("type", "assert")) for item in assertions[:12])
            )
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
        point = await self._memory_store.write(request)
        return point.id if point is not None else None

    async def write_observations(self, observations: Iterable[ObservationRecord]) -> list[str]:
        write_ids: list[str] = []
        for observation in observations:
            point = await self._memory_store.write(
                MemoryWriteRequest(
                    scope=observation.scope,
                    kind="observation",
                    content=observation.content,
                    summary=observation.summary,
                    tags=observation.tags,
                    session_id=observation.session_id,
                    turn_id=observation.turn_id,
                    trace_id=observation.trace_id,
                    source=observation.source,
                    metadata={
                        "memory_id": observation.id,
                        "observation_id": observation.id,
                        "observation_title": observation.title,
                        "observation_category": observation.category,
                        "tool_key": observation.tool_key,
                        "status": observation.status,
                        **observation.metadata,
                    },
                )
            )
            if point is not None:
                write_ids.append(point.id)
        return write_ids

    async def list_session_observations(
        self,
        session_id: str,
        top_k: int = 100,
    ) -> list[ObservationRecord]:
        points = await self._memory_store.list_points(
            MemorySearchRequest(
                query="",
                session_id=session_id,
                scopes=["session", "page", "artifact"],
                kinds=["observation"],
                top_k=top_k,
                day_window=0,
            )
        )
        observations: list[ObservationRecord] = []
        for point in points:
            metadata = point.metadata or {}
            observations.append(
                ObservationRecord(
                    id=str(metadata.get("observation_id") or point.id),
                    session_id=point.session_id or session_id,
                    turn_id=point.turn_id or "",
                    trace_id=point.trace_id or "",
                    tool_key=str(metadata.get("tool_key") or "tool"),
                    status=str(metadata.get("status") or "completed"),
                    scope=point.scope,
                    category=str(metadata.get("observation_category") or "tool_execution"),
                    title=str(metadata.get("observation_title") or point.summary or point.source or point.id),
                    summary=point.summary,
                    content=point.content,
                    source=point.source,
                    tags=point.tags,
                    metadata=metadata,
                    created_at=point.created_at,
                )
            )
        return observations

    def _format_observation_prompt_block(self, hit) -> str:
        metadata = hit.metadata or {}
        category = str(metadata.get("observation_category") or "tool_execution")
        tool_key = str(metadata.get("tool_key") or "tool")
        title = str(metadata.get("observation_title") or hit.summary or hit.source or hit.id)
        source = hit.source or "memory"
        return (
            f"- [{category}] {title} "
            f"(tool={tool_key}, scope={hit.scope}, source={source}, score={hit.score or 0:.3f})"
        )

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
            MemoryWriteRequest(
                scope="global",
                kind="semantic",
                content=(
                    f"Conversation turn summary.\n"
                    f"User message: {user_message.strip()}\n"
                    f"Assistant response: {assistant_message.strip()}"
                ),
                summary=truncate_text(f"{user_message.strip()} -> {assistant_message.strip()}", 180),
                tags=["long_term", "conversation", "semantic"],
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                source="conversation.long_term",
                metadata={
                    "memory_id": self._stable_long_term_memory_id(session_id, turn_id, user_message),
                    "memory_level": "long_term",
                    "context_keys": sorted(context_bundle.keys()),
                },
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

    def _merge_ranked_hits(
        self,
        current_session_hits: list,
        historical_hits: list,
        global_hits: list,
        top_k: int,
    ) -> list:
        merged: dict[str, Any] = {}
        for hit in global_hits:
            merged[hit.id] = hit
        for hit in historical_hits:
            existing = merged.get(hit.id)
            if existing is None or (hit.score or 0.0) > (existing.score or 0.0):
                merged[hit.id] = hit
        for hit in current_session_hits:
            existing = merged.get(hit.id)
            if existing is None or (hit.score or 0.0) >= (existing.score or 0.0):
                merged[hit.id] = hit
        ranked = list(merged.values())
        ranked.sort(
            key=lambda item: (
                1 if item.session_id else 0,
                1 if item.scope == "session" else 0,
                item.score or 0.0,
                item.updated_at,
            ),
            reverse=True,
        )
        return ranked[:top_k]

    def _stable_long_term_memory_id(self, session_id: str, turn_id: str, user_message: str) -> str:
        digest = hashlib.sha1(f"{session_id}:{turn_id}:{user_message.strip()}".encode("utf-8")).hexdigest()
        return f"ltm-{digest[:20]}"
