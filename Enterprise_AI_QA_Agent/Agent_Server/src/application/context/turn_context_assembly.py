"""Shared, fixed context preparation; never a model loop or task scheduler."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
import logging
from typing import Any


logger = logging.getLogger(__name__)
ContextNode = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class TurnContextAssembly:
    # Composition owns the existing nodes. Legacy registers them in its graph;
    # a new framework Turn runs only this preparation, not the legacy graph.
    context_builder: ContextNode
    router: ContextNode
    permission_gate: ContextNode
    prompt_assembler: ContextNode

    async def prepare(self, state: dict[str, Any]) -> dict[str, Any]:
        model_key = state["selected_model_key"]
        phase = "context_builder"
        logger.info("turn_context_assembly_started", extra={"session_id": state["session_id"],
            "turn_id": state["turn_id"], "trace_id": state["trace_id"]})
        try:
            for phase, node in (("context_builder", self.context_builder), ("router", self.router),
                ("permission_gate", self.permission_gate), ("prompt_assembler", self.prompt_assembler)):
                result = node(state)
                state = await result if inspect.isawaitable(result) else result
                if phase == "router" and state["selected_model_key"] != model_key:
                    raise ValueError("Context assembly cannot replace the bound model selection")
        except Exception as exc:
            logger.warning("turn_context_assembly_failed", extra={"session_id": state["session_id"],
                "turn_id": state["turn_id"], "trace_id": state["trace_id"], "phase": phase, "error_type": type(exc).__name__})
            raise
        logger.info("turn_context_assembled", extra={"session_id": state["session_id"],
            "turn_id": state["turn_id"], "trace_id": state["trace_id"], "system_section_count": len(state["system_prompt_sections"]),
            "visible_tool_count": len(state["model_visible_tool_keys"]),
            "selected_skill_count": len(state["resolved_skill_keys"])})
        return state
