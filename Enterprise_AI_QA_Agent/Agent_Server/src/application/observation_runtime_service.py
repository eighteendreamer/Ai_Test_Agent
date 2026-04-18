from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from src.runtime.execution_logging import truncate_text
from src.schemas.observation import ObservationCategory, ObservationRecord, ObservationScope


class ObservationRuntimeService:
    def build_tool_observations(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_results: list[dict[str, Any]],
        context_bundle: dict[str, Any] | None = None,
    ) -> list[ObservationRecord]:
        observations: list[ObservationRecord] = []
        context_bundle = context_bundle or {}
        for tool_result in tool_results:
            status = str(tool_result.get("status") or "").strip().lower()
            if status not in {"completed", "partial"}:
                continue

            tool_key = str(tool_result.get("tool_key") or "tool")
            output = tool_result.get("output")
            if not isinstance(output, dict):
                output = {}
            summary = str(tool_result.get("summary") or "").strip()
            category = self._category_for_tool(tool_key)
            scope = self._scope_for_tool(tool_key)
            source = self._resolve_source(tool_key, output)
            title = self._build_title(tool_key, category, summary, source)
            content = self._build_content(tool_key, summary, output, context_bundle)
            metadata = {
                "category": category,
                "tool_key": tool_key,
                "status": status,
                "source": source,
                "artifact_count": len(output.get("artifacts", [])) if isinstance(output.get("artifacts"), list) else 0,
                "job_id": tool_result.get("job_id"),
                "call_id": tool_result.get("call_id"),
                "context_keys": sorted(context_bundle.keys()),
            }
            observations.append(
                ObservationRecord(
                    id=str(uuid4()),
                    session_id=session_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    tool_key=tool_key,
                    status=status,
                    scope=scope,
                    category=category,
                    title=title,
                    summary=truncate_text(summary or title, 180),
                    content=content,
                    source=source,
                    tags=self._build_tags(tool_key, category, scope, source),
                    metadata=metadata,
                )
            )
        return observations

    def _category_for_tool(self, tool_key: str) -> ObservationCategory:
        if tool_key in {"browser-automation", "browser-control", "dom-inspector"}:
            return "page_state"
        if tool_key == "api-tester":
            return "api_assertion"
        if tool_key == "cli-executor":
            return "cli_execution"
        if tool_key == "report-writer":
            return "report_artifact"
        if tool_key == "knowledge-rag":
            return "knowledge_hit"
        if tool_key == "session-history":
            return "history_fact"
        return "tool_execution"

    def _scope_for_tool(self, tool_key: str) -> ObservationScope:
        if tool_key in {"browser-automation", "browser-control", "dom-inspector"}:
            return "page"
        if tool_key in {"api-tester", "report-writer", "file-artifact-manager"}:
            return "artifact"
        return "session"

    def _resolve_source(self, tool_key: str, output: dict[str, Any]) -> str | None:
        if tool_key in {"browser-automation", "browser-control", "dom-inspector"}:
            return str(output.get("current_url") or output.get("url") or "").strip() or None
        if tool_key == "api-tester":
            endpoint = str(output.get("endpoint") or output.get("url") or "").strip()
            return endpoint or None
        artifact_items = output.get("artifacts")
        if isinstance(artifact_items, list) and artifact_items:
            first_artifact = artifact_items[0]
            if isinstance(first_artifact, dict):
                path = str(first_artifact.get("path") or first_artifact.get("uri") or "").strip()
                if path:
                    return path
        return None

    def _build_title(
        self,
        tool_key: str,
        category: ObservationCategory,
        summary: str,
        source: str | None,
    ) -> str:
        if summary:
            return truncate_text(summary, 120)
        if source:
            return f"{tool_key} observation from {source}"
        return f"{tool_key} {category.replace('_', ' ')}"

    def _build_content(
        self,
        tool_key: str,
        summary: str,
        output: dict[str, Any],
        context_bundle: dict[str, Any],
    ) -> str:
        content_parts = [f"tool_key={tool_key}"]
        if summary:
            content_parts.append(f"summary={summary}")
        if output:
            output_excerpt = truncate_text(json.dumps(output, ensure_ascii=True, default=str), 600)
            content_parts.append(f"output={output_excerpt}")
        if context_bundle:
            context_excerpt = truncate_text(json.dumps(context_bundle, ensure_ascii=True, default=str), 240)
            content_parts.append(f"context={context_excerpt}")
        return "\n".join(content_parts)

    def _build_tags(
        self,
        tool_key: str,
        category: ObservationCategory,
        scope: ObservationScope,
        source: str | None,
    ) -> list[str]:
        tags = ["observation", tool_key, category, scope]
        if source:
            if source.startswith("http"):
                tags.append("url_source")
            elif "." in source:
                tags.append("artifact_source")
        return list(dict.fromkeys(tags))
