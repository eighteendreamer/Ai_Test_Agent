from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from typing import Any

from src.contracts.memory_store import MemoryStoreProtocol
from src.application.context.embedding_runtime_service import EmbeddingRuntimeService
from src.runtime.execution_logging import truncate_text
from src.schemas.observation import ObservationRecord
from src.schemas.memory import MemorySearchRequest, MemorySearchResult, MemoryWriteRequest


class MemoryRuntimeService:
    def __init__(
        self,
        memory_store: MemoryStoreProtocol,
        top_k: int = 6,
        embedding_runtime_service: EmbeddingRuntimeService | None = None,
    ) -> None:
        self._memory_store = memory_store
        self._top_k = top_k
        self._embedding_runtime_service = embedding_runtime_service

    async def initialize(self) -> None:
        await self._memory_store.initialize()

    @property
    def backend(self) -> str:
        return self._memory_store.backend

    async def refresh_backend_status(self) -> str:
        return await self._memory_store.refresh_connection_status()

    async def backfill_missing_embeddings(
        self,
        *,
        limit: int = 1000,
        batch_size: int = 32,
        execute: bool = False,
    ) -> dict[str, Any]:
        missing_total = await self._memory_store.count_missing_embeddings()
        requested = min(max(int(limit), 1), missing_total)
        if not execute or requested == 0:
            return {
                "execute": False,
                "missing_total": missing_total,
                "requested": requested,
                "updated": 0,
            }
        if self._embedding_runtime_service is None:
            raise RuntimeError("Embedding runtime service is not configured.")
        points = await self._memory_store.list_missing_embeddings(requested)
        updated = 0
        size = max(1, min(int(batch_size), 100))
        for offset in range(0, len(points), size):
            batch = points[offset : offset + size]
            texts = [
                "\n".join(part for part in (point.summary, point.content) if part)
                for point in batch
            ]
            result = await self._embedding_runtime_service.embed_texts(texts)
            metadata = {
                "embedding_model": result.model_name,
                "embedding_provider": result.provider,
                "embedding_adapter": result.adapter,
                "embedding_source_dimension": result.original_dimension,
            }
            updated += await self._memory_store.update_embeddings(
                [
                    (point.id, vector, metadata)
                    for point, vector in zip(batch, result.vectors)
                ]
            )
        return {
            "execute": True,
            "missing_total": missing_total,
            "requested": len(points),
            "updated": updated,
        }

    async def retrieve_for_turn(
        self,
        session_id: str,
        trace_id: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> MemorySearchResult:
        if not query.strip():
            return MemorySearchResult(query=query, backend=self.backend)

        context = context or {}
        memory_filters = await self._build_search_filters(query, context)
        if self._is_security_session_isolated(context):
            request = MemorySearchRequest(
                query=query,
                session_id=session_id,
                trace_id=trace_id,
                scopes=["session", "page", "artifact"],
                top_k=max(self._top_k, 6),
                tags=self._derive_read_tags(context),
                day_window=0,
                metadata_filters=memory_filters,
            )
            hits, total_docs = await asyncio.gather(
                self._memory_store.search(request),
                self._memory_store.count_documents(request),
            )
            prompt_blocks = [
                (
                    "Memory inventory summary: "
                    f"current_session docs={total_docs}, "
                    "cross_session session/page/artifact docs=0, "
                    "global long-term docs=0, "
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
                total_session_docs=total_docs,
                total_global_docs=0,
                total_docs=total_docs,
                backend=self.backend,
            )

        current_session_request = MemorySearchRequest(
            query=query,
            session_id=session_id,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            top_k=max(self._top_k, 6),
            tags=self._derive_read_tags(context),
            day_window=0,
            metadata_filters=memory_filters,
        )
        historical_request = MemorySearchRequest(
            query=query,
            session_id=None,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            top_k=max(self._top_k, 8),
            tags=self._derive_read_tags(context),
            day_window=0,
            metadata_filters=memory_filters,
        )
        global_request = MemorySearchRequest(
            query=query,
            session_id=None,
            trace_id=trace_id,
            scopes=["global"],
            top_k=max(self._top_k, 6),
            tags=self._derive_read_tags(context),
            day_window=0,
            metadata_filters=memory_filters,
        )
        (
            current_session_hits,
            historical_hits,
            global_hits,
            total_current_session_docs,
            total_historical_docs,
            total_global_docs,
        ) = await asyncio.gather(
            self._memory_store.search(current_session_request),
            self._memory_store.search(historical_request),
            self._memory_store.search(global_request),
            self._memory_store.count_documents(current_session_request),
            self._memory_store.count_documents(historical_request),
            self._memory_store.count_documents(global_request),
        )
        hits = self._merge_ranked_hits(
            current_session_hits,
            historical_hits,
            global_hits,
            self._top_k,
            preferred_session_id=session_id,
        )
        cross_session_docs = max(total_historical_docs - total_current_session_docs, 0)
        prompt_blocks = [
            (
                "Memory inventory summary: "
                f"current_session docs={total_current_session_docs}, "
                f"cross_session session/page/artifact docs={cross_session_docs}, "
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

        context = context or {}
        memory_filters = await self._build_search_filters(query, context)
        if self._is_security_session_isolated(context):
            request = MemorySearchRequest(
                query=query,
                session_id=session_id,
                trace_id=trace_id,
                scopes=["session", "page", "artifact"],
                kinds=["observation"],
                top_k=max(top_k, 4),
                tags=self._derive_read_tags(context),
                day_window=0,
                metadata_filters=memory_filters,
            )
            hits, total_docs = await asyncio.gather(
                self._memory_store.search(request),
                self._memory_store.count_documents(request),
            )
            prompt_blocks: list[str] = []
            if hits:
                prompt_blocks = [
                    (
                        "Historical testing observations: "
                        f"current_session={total_docs}, "
                        "cross_session=0, "
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
                total_session_docs=total_docs,
                total_global_docs=0,
                total_docs=total_docs,
                backend=self.backend,
            )

        current_session_request = MemorySearchRequest(
            query=query,
            session_id=session_id,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            kinds=["observation"],
            top_k=max(top_k, 4),
            tags=self._derive_read_tags(context),
            day_window=0,
            metadata_filters=memory_filters,
        )
        historical_request = MemorySearchRequest(
            query=query,
            session_id=None,
            trace_id=trace_id,
            scopes=["session", "page", "artifact"],
            kinds=["observation"],
            top_k=max(top_k, 6),
            tags=self._derive_read_tags(context),
            day_window=0,
            metadata_filters=memory_filters,
        )
        current_hits, historical_hits, total_current_docs, total_historical_docs = (
            await asyncio.gather(
                self._memory_store.search(current_session_request),
                self._memory_store.search(historical_request),
                self._memory_store.count_documents(current_session_request),
                self._memory_store.count_documents(historical_request),
            )
        )
        hits = self._merge_ranked_hits(
            current_hits,
            historical_hits,
            [],
            top_k,
            preferred_session_id=session_id,
        )
        cross_session_docs = max(total_historical_docs - total_current_docs, 0)
        prompt_blocks = []
        if hits:
            prompt_blocks = [
                (
                    "Historical testing observations: "
                    f"current_session={total_current_docs}, "
                    f"cross_session={cross_session_docs}, "
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
        turn_lease_token: str | None = None,
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
        requests = await self._attach_embeddings(requests)
        if turn_lease_token:
            requests = [item.model_copy(update={"turn_lease_token": turn_lease_token}) for item in requests]
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
        request = (await self._attach_embeddings([request]))[0]
        point = await self._memory_store.write(request)
        return point.id if point is not None else None

    async def write_observations(
        self,
        observations: Iterable[ObservationRecord],
        turn_lease_token: str | None = None,
    ) -> list[str]:
        write_ids: list[str] = []
        requests: list[MemoryWriteRequest] = []
        for observation in observations:
            requests.append(
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
        for request in await self._attach_embeddings(requests):
            if turn_lease_token:
                request = request.model_copy(update={"turn_lease_token": turn_lease_token})
            point = await self._memory_store.write(request)
            if point is not None:
                write_ids.append(point.id)
        return write_ids

    async def _build_search_filters(
        self,
        query: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        filters = self._derive_memory_filters(context)
        if self._embedding_runtime_service is None:
            return filters
        try:
            result = await self._embedding_runtime_service.embed_texts([query])
        except Exception:
            return filters
        return {
            **filters,
            "__query_embedding": result.vectors[0],
            "__embedding_model": result.model_name,
            "__embedding_provider": result.provider,
        }

    async def _attach_embeddings(
        self,
        requests: list[MemoryWriteRequest],
    ) -> list[MemoryWriteRequest]:
        if not requests or self._embedding_runtime_service is None:
            return requests
        texts = [
            "\n".join(part for part in (request.summary, request.content) if part)
            for request in requests
        ]
        try:
            result = await self._embedding_runtime_service.embed_texts(texts)
        except Exception:
            return requests
        return [
            request.model_copy(
                update={
                    "metadata": {
                        **request.metadata,
                        "embedding": vector,
                        "embedding_model": result.model_name,
                        "embedding_provider": result.provider,
                        "embedding_adapter": result.adapter,
                        "embedding_source_dimension": result.original_dimension,
                    }
                }
            )
            for request, vector in zip(requests, result.vectors)
        ]

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
        mode_key = str(context_bundle.get("mode_key") or "default").strip() or "default"
        metadata = self._build_common_metadata(context_bundle)
        requests = [
            MemoryWriteRequest(
                scope="session",
                kind="episodic",
                content=f"User goal: {user_message.strip()}",
                summary=truncate_text(user_message.strip(), 140),
                tags=["user_goal", "turn_input", f"mode:{mode_key}"],
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                source="session.user",
                metadata={
                    **metadata,
                    "context_keys": sorted(context_bundle.keys()),
                    "mode_key": mode_key,
                },
            ),
            MemoryWriteRequest(
                scope="session",
                kind="semantic",
                content=f"Assistant outcome: {assistant_message.strip()}",
                summary=truncate_text(assistant_message.strip(), 160),
                tags=["assistant_summary", "turn_output", f"mode:{mode_key}"],
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                source="session.assistant",
                metadata={
                    **metadata,
                    "mode_key": mode_key,
                },
            ),
        ]
        if not self._is_security_session_isolated(context_bundle):
            requests.append(
                MemoryWriteRequest(
                    scope="global",
                    kind="semantic",
                    content=(
                        f"Conversation turn summary.\n"
                        f"User message: {user_message.strip()}\n"
                        f"Assistant response: {assistant_message.strip()}"
                    ),
                    summary=truncate_text(f"{user_message.strip()} -> {assistant_message.strip()}", 180),
                    tags=["long_term", "conversation", "semantic", f"mode:{mode_key}"],
                    session_id=session_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    source="conversation.long_term",
                    metadata={
                        **metadata,
                        "memory_id": self._stable_long_term_memory_id(session_id, turn_id, user_message),
                        "memory_level": "long_term",
                        "mode_key": mode_key,
                        "context_keys": sorted(context_bundle.keys()),
                    },
                )
            )
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
                    tags=["tool_result", str(tool_result.get("tool_key") or "tool"), f"mode:{mode_key}"],
                    session_id=session_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    source=f"tool.{tool_result.get('tool_key', 'unknown')}",
                    metadata={
                        **metadata,
                        "tool_key": tool_result.get("tool_key"),
                        "mode_key": mode_key,
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

    def _derive_memory_filters(self, context: dict[str, Any]) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        mode_key = str(context.get("mode_key") or "").strip()
        if mode_key:
            filters["mode_key"] = mode_key
        target_fingerprint = str(context.get("target_fingerprint") or "").strip()
        if target_fingerprint:
            filters["target_fingerprint"] = target_fingerprint
        campaign_id = str(context.get("campaign_id") or "").strip()
        if campaign_id:
            filters["campaign_id"] = campaign_id
        return filters

    def _build_common_metadata(self, context: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in ("mode_key", "target_fingerprint", "campaign_id", "platform_label"):
            value = str(context.get(key) or "").strip()
            if value:
                metadata[key] = value
        return metadata

    def _is_security_session_isolated(self, context: dict[str, Any]) -> bool:
        mode_key = str(context.get("mode_key") or "").strip().lower()
        scope = str(context.get("security_memory_scope") or "").strip().lower()
        allow_cross_session = bool(context.get("allow_cross_session_memory"))
        return mode_key == "security_testing" and scope != "shared" and not allow_cross_session

    def _merge_ranked_hits(
        self,
        current_session_hits: list,
        historical_hits: list,
        global_hits: list,
        top_k: int,
        preferred_session_id: str | None = None,
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
                1 if preferred_session_id and item.session_id == preferred_session_id else 0,
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
