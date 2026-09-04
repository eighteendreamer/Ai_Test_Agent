"""D2 Agent Loop: extracted from RuntimeService._run_until_settled.

AgentLoop owns the main execution loop that drives the LangGraph graph.
It uses three state objects (SessionContext, TurnState, LoopConfig) internally
and flattens them into AgentGraphState before calling graph.ainvoke().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Awaitable, Callable

from src.application.runtime.error_recovery import ErrorRecoveryCascade
from src.application.runtime.events import LoopEvent, LoopEventType
from src.application.runtime.state_types import LoopConfig, SessionContext, TurnState
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_logging import append_graph_event


logger = logging.getLogger(__name__)


class AgentLoop:
    """AsyncGenerator-ready agent loop that drives the LangGraph graph.

    Phase 1: run_turn() returns a dict (drop-in replacement for _run_until_settled).
    Phase 2: stream_turn() yields LoopEvent objects for real-time streaming.
    """

    def __init__(
        self,
        graph: Any,
        runtime_control: RuntimeControlRegistry,
        max_iterations: int = 8,
        error_recovery: ErrorRecoveryCascade | None = None,
    ) -> None:
        self._graph = graph
        self._runtime_control = runtime_control
        self._max_iterations = max_iterations
        self._error_recovery = error_recovery

    async def run_turn(
        self,
        state: dict[str, Any],
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Execute the agent loop until settled. Drop-in for _run_until_settled.

        The state dict is in AgentGraphState format (flat dict). Internally we
        decompose it into SessionContext/TurnState/LoopConfig for clarity, then
        recompose before each graph.ainvoke() call.
        """
        session, turn, config = self._decompose_graph_state(state)

        current_state = state
        while True:
            self._apply_interrupt_state(current_state)
            if current_state["interrupt_requested"]:
                return self._interrupt_result(current_state)

            try:
                result = await self._graph.ainvoke(current_state)
            except Exception as exc:
                if self._error_recovery is not None:
                    recovery = await self._error_recovery.attempt_recovery(exc, current_state)
                    if recovery.action == "retry":
                        if recovery.retry_delay_seconds > 0:
                            await asyncio.sleep(recovery.retry_delay_seconds)
                        current_state = recovery.state or current_state
                        continue
                    elif recovery.action == "terminate":
                        return self._interrupt_result(recovery.state or current_state)
                raise

            self._apply_interrupt_state(result)
            if result["interrupt_requested"]:
                return self._interrupt_result(result)

            if not result["continue_loop"]:
                if self._error_recovery is not None:
                    self._error_recovery.clear_retry_count(str(result.get("session_id", "")))
                return result

            append_graph_event(
                result,
                "runtime.loop_reenter",
                "runtime",
                "Runtime is re-entering the recursive model loop for the same turn.",
                next_iteration=result["loop_iteration"] + 1,
                max_iterations=result["max_iterations"],
            )
            result["loop_iteration"] += 1
            current_state = result

    async def stream_turn(
        self,
        state: dict[str, Any],
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Execute the agent loop, yielding events as it progresses.

        Phase 2 of D2 upgrade: AsyncGenerator event stream for real-time visibility.
        Events are yielded between graph invocations, not from within nodes.
        """
        yield LoopEvent(
            type=LoopEventType.TURN_STARTED,
            iteration=state.get("loop_iteration", 0),
            payload={
                "session_id": state.get("session_id", ""),
                "turn_id": state.get("turn_id", ""),
            },
        )

        current_state = state
        iteration = 0
        while True:
            self._apply_interrupt_state(current_state)
            if current_state["interrupt_requested"]:
                result = self._interrupt_result(current_state)
                yield LoopEvent(
                    type=LoopEventType.TURN_INTERRUPT,
                    iteration=iteration,
                    payload={
                        "reason": result.get("interrupt_reason", ""),
                        "loop_iteration": result.get("loop_iteration", 0),
                    },
                )
                yield LoopEvent(
                    type=LoopEventType.TURN_COMPLETED,
                    iteration=iteration,
                    payload={
                        "termination_reason": "interrupted",
                        "final_response_preview": (result.get("final_response") or "")[:160],
                    },
                )
                return

            result = await self._graph.ainvoke(current_state)

            self._apply_interrupt_state(result)
            if result["interrupt_requested"]:
                result = self._interrupt_result(result)
                yield LoopEvent(
                    type=LoopEventType.TURN_INTERRUPT,
                    iteration=iteration,
                    payload={
                        "reason": result.get("interrupt_reason", ""),
                        "loop_iteration": result.get("loop_iteration", 0),
                    },
                )
                yield LoopEvent(
                    type=LoopEventType.TURN_COMPLETED,
                    iteration=iteration,
                    payload={
                        "termination_reason": "interrupted",
                        "final_response_preview": (result.get("final_response") or "")[:160],
                    },
                )
                return

            if result.get("model_tool_calls"):
                yield LoopEvent(
                    type=LoopEventType.MODEL_RESPONSE,
                    iteration=iteration,
                    payload={
                        "tool_call_count": len(result["model_tool_calls"]),
                        "response_text_preview": (result.get("model_response_text") or "")[:160],
                    },
                )

            if result.get("tool_results"):
                yield LoopEvent(
                    type=LoopEventType.TOOL_EXECUTED,
                    iteration=iteration,
                    payload={
                        "tool_result_count": len(result["tool_results"]),
                        "pending_approval_count": len(result.get("pending_approvals") or []),
                    },
                )

            if not result["continue_loop"]:
                yield LoopEvent(
                    type=LoopEventType.TURN_COMPLETED,
                    iteration=iteration,
                    payload={
                        "termination_reason": result.get("termination_reason", ""),
                        "final_response_preview": (result.get("final_response") or "")[:160],
                        "tool_result_count": len(result.get("tool_results") or []),
                    },
                )
                return

            append_graph_event(
                result,
                "runtime.loop_reenter",
                "runtime",
                "Runtime is re-entering the recursive model loop for the same turn.",
                next_iteration=result["loop_iteration"] + 1,
                max_iterations=result["max_iterations"],
            )
            result["loop_iteration"] += 1
            iteration += 1

            yield LoopEvent(
                type=LoopEventType.LOOP_REENTER,
                iteration=iteration,
                payload={
                    "next_iteration": result["loop_iteration"],
                    "max_iterations": result["max_iterations"],
                },
            )
            current_state = result

    def _apply_interrupt_state(self, state: dict[str, Any]) -> None:
        """Check if interrupt was requested and update state accordingly."""
        reason = self._runtime_control.get_interrupt_reason(str(state["session_id"]))
        state["interrupt_requested"] = bool(reason)
        state["interrupt_reason"] = reason

    def _interrupt_result(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build a terminal state for an interrupted turn."""
        if state["termination_reason"] != "interrupted":
            append_graph_event(
                state,
                "runtime.interrupt_requested",
                "runtime",
                "Interrupt was requested and will stop execution at this safe boundary.",
                interrupt_reason=state.get("interrupt_reason", ""),
                loop_iteration=state["loop_iteration"],
            )
        state["continue_loop"] = False
        state["termination_reason"] = "interrupted"
        state["control_state"] = "interrupted"
        state["pending_turn"] = self._build_pending_turn(state, stage="interrupted")
        return state

    def _build_pending_turn(
        self,
        state: dict[str, Any],
        stage: str = "interrupted",
    ) -> dict[str, Any]:
        """Serialize the current turn state for later resume."""
        return {
            "stage": stage,
            "turn_id": state.get("turn_id", ""),
            "trace_id": state.get("trace_id", ""),
            "user_message": state.get("user_message", ""),
            "normalized_input": state.get("normalized_input", ""),
            "session_mode": state.get("session_mode", ""),
            "runtime_mode": state.get("runtime_mode", ""),
            "mode_key": state.get("mode_key", ""),
            "selected_agent_key": state.get("selected_agent_key", ""),
            "selected_model_key": state.get("selected_model_key", ""),
            "runtime_messages": list(state.get("runtime_messages") or []),
            "tool_results": list(state.get("tool_results") or []),
            "tool_messages": list(state.get("tool_messages") or []),
            "pending_approvals": list(state.get("pending_approvals") or []),
            "loop_iteration": state.get("loop_iteration", 0),
            "termination_reason": state.get("termination_reason", ""),
        }

    def _decompose_graph_state(
        self, state: dict[str, Any]
    ) -> tuple[SessionContext, TurnState, LoopConfig]:
        """Decompose flat AgentGraphState dict into three typed objects.

        This is used internally for clarity. The graph still receives the flat dict.
        """
        session = SessionContext(
            session_id=state.get("session_id", ""),
            session_mode=state.get("session_mode", ""),
            runtime_mode=state.get("runtime_mode", ""),
            mode_key=state.get("mode_key", ""),
            preferred_model=state.get("preferred_model", ""),
            message_count=state.get("message_count", 0),
        )

        turn = TurnState(
            turn_id=state.get("turn_id", ""),
            trace_id=state.get("trace_id", ""),
            user_message=state.get("user_message", ""),
            normalized_input=state.get("normalized_input", ""),
            selected_agent_key=state.get("selected_agent_key", ""),
            selected_agent_name=state.get("selected_agent_name", ""),
            selected_model_key=state.get("selected_model_key", ""),
            selected_model_name=state.get("selected_model_name", ""),
            selected_model_provider=state.get("selected_model_provider", ""),
            requested_skill_keys=list(state.get("requested_skill_keys") or []),
            resolved_skill_keys=list(state.get("resolved_skill_keys") or []),
            skill_prompt_blocks=list(state.get("skill_prompt_blocks") or []),
            memory_hits=list(state.get("memory_hits") or []),
            memory_prompt_blocks=list(state.get("memory_prompt_blocks") or []),
            observation_hits=list(state.get("observation_hits") or []),
            observation_prompt_blocks=list(state.get("observation_prompt_blocks") or []),
            active_mcp_servers=list(state.get("active_mcp_servers") or []),
            mcp_prompt_blocks=list(state.get("mcp_prompt_blocks") or []),
            available_tool_keys=list(state.get("available_tool_keys") or []),
            deferred_tool_keys=list(state.get("deferred_tool_keys") or []),
            model_visible_tool_keys=list(state.get("model_visible_tool_keys") or []),
            allowed_tool_keys=list(state.get("allowed_tool_keys") or []),
            approval_required_tool_keys=list(state.get("approval_required_tool_keys") or []),
            denied_tool_keys=list(state.get("denied_tool_keys") or []),
            permission_decisions=list(state.get("permission_decisions") or []),
            pending_approvals=list(state.get("pending_approvals") or []),
            plan_steps=list(state.get("plan_steps") or []),
            system_prompt_sections=list(state.get("system_prompt_sections") or []),
            runtime_message_sections=list(state.get("runtime_message_sections") or []),
            system_prompt=state.get("system_prompt", ""),
            runtime_messages=list(state.get("runtime_messages") or []),
            model_request_payload=dict(state.get("model_request_payload") or {}),
            model_response_summary=dict(state.get("model_response_summary") or {}),
            model_response_text=state.get("model_response_text", ""),
            turn_token_usage=dict(state.get("turn_token_usage") or {}),
            model_context_window=state.get("model_context_window", 0),
            assistant_tool_call_message=dict(state.get("assistant_tool_call_message") or {}),
            model_tool_calls=list(state.get("model_tool_calls") or []),
            tool_results=list(state.get("tool_results") or []),
            tool_messages=list(state.get("tool_messages") or []),
            worker_dispatches=list(state.get("worker_dispatches") or []),
            context_bundle=dict(state.get("context_bundle") or {}),
            event_log=list(state.get("event_log") or []),
            final_response=state.get("final_response", ""),
            pending_turn=dict(state.get("pending_turn") or {}),
            control_state=state.get("control_state", ""),
            interrupt_requested=bool(state.get("interrupt_requested", False)),
            interrupt_reason=state.get("interrupt_reason", ""),
            loop_iteration=state.get("loop_iteration", 0),
            continue_loop=bool(state.get("continue_loop", False)),
            termination_reason=state.get("termination_reason", ""),
            _event_queue=state.get("_event_queue"),
        )

        config = LoopConfig(
            max_iterations=state.get("max_iterations", self._max_iterations),
        )

        return session, turn, config

    def _compose_graph_state(
        self,
        session: SessionContext,
        turn: TurnState,
        config: LoopConfig,
    ) -> dict[str, Any]:
        """Compose three typed objects back into flat AgentGraphState dict.

        This is the inverse of _decompose_graph_state. Used when we want to
        rebuild the state dict from the typed objects (e.g., after modifications).
        """
        return {
            "session_id": session.session_id,
            "session_mode": session.session_mode,
            "runtime_mode": session.runtime_mode,
            "mode_key": session.mode_key,
            "preferred_model": session.preferred_model,
            "message_count": session.message_count,
            "turn_id": turn.turn_id,
            "trace_id": turn.trace_id,
            "user_message": turn.user_message,
            "normalized_input": turn.normalized_input,
            "selected_agent_key": turn.selected_agent_key,
            "selected_agent_name": turn.selected_agent_name,
            "selected_model_key": turn.selected_model_key,
            "selected_model_name": turn.selected_model_name,
            "selected_model_provider": turn.selected_model_provider,
            "requested_skill_keys": turn.requested_skill_keys,
            "resolved_skill_keys": turn.resolved_skill_keys,
            "skill_prompt_blocks": turn.skill_prompt_blocks,
            "memory_hits": turn.memory_hits,
            "memory_prompt_blocks": turn.memory_prompt_blocks,
            "observation_hits": turn.observation_hits,
            "observation_prompt_blocks": turn.observation_prompt_blocks,
            "active_mcp_servers": turn.active_mcp_servers,
            "mcp_prompt_blocks": turn.mcp_prompt_blocks,
            "available_tool_keys": turn.available_tool_keys,
            "deferred_tool_keys": turn.deferred_tool_keys,
            "model_visible_tool_keys": turn.model_visible_tool_keys,
            "allowed_tool_keys": turn.allowed_tool_keys,
            "approval_required_tool_keys": turn.approval_required_tool_keys,
            "denied_tool_keys": turn.denied_tool_keys,
            "permission_decisions": turn.permission_decisions,
            "pending_approvals": turn.pending_approvals,
            "plan_steps": turn.plan_steps,
            "system_prompt_sections": turn.system_prompt_sections,
            "runtime_message_sections": turn.runtime_message_sections,
            "system_prompt": turn.system_prompt,
            "runtime_messages": turn.runtime_messages,
            "model_request_payload": turn.model_request_payload,
            "model_response_summary": turn.model_response_summary,
            "model_response_text": turn.model_response_text,
            "turn_token_usage": turn.turn_token_usage,
            "model_context_window": turn.model_context_window,
            "assistant_tool_call_message": turn.assistant_tool_call_message,
            "model_tool_calls": turn.model_tool_calls,
            "tool_results": turn.tool_results,
            "tool_messages": turn.tool_messages,
            "worker_dispatches": turn.worker_dispatches,
            "context_bundle": turn.context_bundle,
            "event_log": turn.event_log,
            "final_response": turn.final_response,
            "pending_turn": turn.pending_turn,
            "control_state": turn.control_state,
            "interrupt_requested": turn.interrupt_requested,
            "interrupt_reason": turn.interrupt_reason,
            "loop_iteration": turn.loop_iteration,
            "max_iterations": config.max_iterations,
            "continue_loop": turn.continue_loop,
            "termination_reason": turn.termination_reason,
            "_event_queue": turn._event_queue,
        }
