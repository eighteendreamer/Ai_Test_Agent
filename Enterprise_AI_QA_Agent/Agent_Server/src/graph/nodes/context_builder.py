from __future__ import annotations

from datetime import datetime
import logging

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.projects.project_service import ProjectService
from src.graph.state import AgentGraphState
from src.runtime.execution_logging import append_graph_event_durable, truncate_text
from src.schemas.framework_transcript import uses_turn_context_assembly


logger = logging.getLogger(__name__)


def build_context_builder_node(
    memory_runtime_service: MemoryRuntimeService | None = None,
    *, project_service: ProjectService | None = None,
):
    async def context_builder(state: AgentGraphState) -> AgentGraphState:
        context = dict(state["context_bundle"])
        if uses_turn_context_assembly(context):
            # New preparation owns this source. Frozen graph-input recovery
            # consumes its original state, not this mutable project lookup.
            context.pop("project_context", None)
            state["context_bundle"] = context
            project_id = context.get("project_id")
            if project_id:
                try:
                    if project_service is None:
                        raise RuntimeError("Project service is not composed")
                    project = await project_service.require_active(project_id)
                    if project.id != project_id:
                        raise ValueError("Project source returned another identity")
                    context["project_context"] = project.model_dump(mode="json", include={
                        "id", "project_key", "name", "description", "base_url", "graph_scope_key",
                        "status", "created_at", "updated_at", "archived_at"})
                except Exception as exc:
                    logger.warning("framework_project_context_unavailable", extra={
                        "session_id": state["session_id"], "turn_id": state["turn_id"],
                        "project_id": project_id, "error_type": type(exc).__name__})
                    raise RuntimeError("Framework project context is unavailable") from None
                logger.info("framework_project_context_loaded", extra={
                    "session_id": state["session_id"], "turn_id": state["turn_id"], "project_id": project_id})
        state["observation_hits"] = []
        state["observation_prompt_blocks"] = []

        if memory_runtime_service is not None:
            observation_result = await memory_runtime_service.retrieve_observation_context(
                session_id=state["session_id"],
                trace_id=state["trace_id"],
                query=state["normalized_input"] or state["user_message"],
                context=context,
                top_k=5,
            )
            state["observation_hits"] = [
                item.model_dump(mode="python")
                for item in observation_result.hits
            ]
            state["observation_prompt_blocks"] = _build_observation_timeline_blocks(
                state["observation_hits"],
                total_current_docs=observation_result.total_session_docs,
                total_historical_docs=observation_result.total_docs,
            )

        harness_flags = [
            str(item).strip()
            for item in context.get("harness_flags", [])
            if str(item).strip()
        ] if isinstance(context.get("harness_flags"), list) else []
        for item in [
            "event_sourcing",
            "permission_gate",
            "checkpoint_ready",
            "registry_driven",
            "recursive_tool_loop",
            "observation_context",
            f"mode:{state['mode_key']}",
        ]:
            if item not in harness_flags:
                harness_flags.append(item)

        context.update(
            {
                "message_count": state["message_count"],
                "session_mode": state["session_mode"],
                "runtime_mode": state["runtime_mode"],
                "mode_key": state["mode_key"],
                "preferred_model": state["preferred_model"] or "auto",
                "loop_iteration": state["loop_iteration"],
                "observation_hit_count": len(state["observation_hits"]),
                "observation_timeline_range": _timeline_range(state["observation_hits"]),
                "observation_categories": sorted(
                    {
                        str((item.get("metadata") or {}).get("observation_category") or "")
                        for item in state["observation_hits"]
                        if isinstance(item, dict)
                    }
                    - {""}
                ),
                "harness_flags": harness_flags,
            }
        )
        state["context_bundle"] = context
        await append_graph_event_durable(
            state,
            "graph.context_built",
            "context_builder",
            "Runtime context bundle has been prepared for this turn.",
            message_count=state["message_count"],
            session_mode=state["session_mode"],
            runtime_mode=state["runtime_mode"],
            preferred_model=state["preferred_model"] or "auto",
            loop_iteration=state["loop_iteration"],
            **({} if uses_turn_context_assembly(context) else {
                "user_message_preview": truncate_text(state["user_message"], 160)}),
            observation_hit_count=len(state["observation_hits"]),
            context_keys=",".join(sorted(context.keys())),
        )
        return state

    return context_builder


def _build_observation_timeline_blocks(
    observation_hits: list[dict],
    total_current_docs: int,
    total_historical_docs: int,
) -> list[str]:
    if not observation_hits:
        return []

    sorted_hits = sorted(
        observation_hits,
        key=lambda item: _parse_hit_datetime(item) or datetime.min,
    )
    timeline_lines = [
        (
            "Historical testing timeline: "
            f"matched_observations={len(sorted_hits)}, "
            f"current_session_matches={total_current_docs}, "
            f"all_history_matches={total_historical_docs}."
        )
    ]

    current_day = ""
    for hit in sorted_hits:
        hit_dt = _parse_hit_datetime(hit)
        day_label = hit_dt.strftime("%Y-%m-%d") if hit_dt is not None else "unknown-day"
        time_label = hit_dt.strftime("%H:%M:%S") if hit_dt is not None else "--:--:--"
        if day_label != current_day:
            current_day = day_label
            timeline_lines.append(f"{day_label}:")
        timeline_lines.append(_format_timeline_entry(time_label, hit))

    timeline_lines.append(
        "Use this timeline as prior testing evidence: prefer repeated failure modes, known selectors, API patterns, and report artifacts over re-discovering them."
    )
    return timeline_lines


def _format_timeline_entry(time_label: str, hit: dict) -> str:
    metadata = hit.get("metadata") or {}
    category = str(metadata.get("observation_category") or "tool_execution")
    tool_key = str(metadata.get("tool_key") or "tool")
    title = str(metadata.get("observation_title") or hit.get("summary") or hit.get("source") or "observation")
    summary = str(hit.get("summary") or "").strip()
    source = str(hit.get("source") or "").strip()
    fragments = [f"- {time_label} [{category}] {title}", f"tool={tool_key}", f"scope={hit.get('scope', 'session')}"]
    if source:
        fragments.append(f"source={source}")
    if summary and summary != title:
        fragments.append(f"summary={truncate_text(summary, 140)}")
    return " | ".join(fragments)


def _timeline_range(observation_hits: list[dict]) -> str:
    timestamps = [dt for dt in (_parse_hit_datetime(item) for item in observation_hits) if dt is not None]
    if not timestamps:
        return ""
    start = min(timestamps).strftime("%Y-%m-%d %H:%M:%S")
    end = max(timestamps).strftime("%Y-%m-%d %H:%M:%S")
    return f"{start} -> {end}"


def _parse_hit_datetime(hit: dict) -> datetime | None:
    raw = hit.get("created_at") or hit.get("updated_at")
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
