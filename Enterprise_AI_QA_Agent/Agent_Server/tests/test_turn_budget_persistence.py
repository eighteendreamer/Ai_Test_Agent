from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.application.runtime.runtime_service import RuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.finalizer import build_finalizer_node
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.schemas.session import (
    ExecutionRequest,
    RuntimeMode,
    SessionMode,
    SessionStatus,
)


class _ModelRuntime:
    def get_model_config(self, model_key):
        return None

    def get_default_model_config(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint", ["snapshot", "runtime_pending", "loop_interrupt"])
async def test_turn_budget_survives_checkpoint_and_blocks_loop_reentry(
    checkpoint: str,
) -> None:
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-budget-resume",
        title="Budget resume",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=now,
        updated_at=now,
    )
    request = ExecutionRequest(
        turn_id="turn-budget-resume",
        session_id=session.id,
        user_message="Continue after the tool call",
        normalized_input="Continue after the tool call",
        mode_key="default",
    )
    runtime = RuntimeService(
        graph=None,
        model_runtime_service=_ModelRuntime(),
        tool_runtime_service=None,
        tool_registry=ToolRegistry(),
        runtime_control=RuntimeControlRegistry(),
    )
    state = runtime._build_initial_state(session, request)
    state["model_call_count"] = 3
    state["max_iterations"] = 5
    state["turn_token_usage"] = {
        "prompt_tokens": 950,
        "completion_tokens": 20,
        "total_tokens": 970,
    }
    state["model_context_window"] = 1000

    if checkpoint == "snapshot":
        snapshot = runtime._build_snapshot(session, state, 1)
        restored = runtime._state_from_snapshot(session, snapshot)
    elif checkpoint == "runtime_pending":
        pending = runtime._build_pending_turn(state, stage="interrupted")
        restored = runtime._state_from_pending_turn(session, pending)
    else:
        pending = runtime._agent_loop._interrupt_result(state)["pending_turn"]
        restored = runtime._state_from_pending_turn(session, pending)

    assert restored["turn_token_usage"] == state["turn_token_usage"]
    assert restored["model_context_window"] == 1000
    assert restored["model_call_count"] == 3
    assert restored["max_iterations"] == 5

    restored["model_tool_calls"] = [{"id": "tool-1", "name": "demo", "arguments": {}}]
    restored["continue_loop"] = True
    await build_finalizer_node(_ModelRuntime())(restored)
    assert restored["termination_reason"] == "context_budget_exhausted"
    assert restored["continue_loop"] is False


def test_historical_budget_serialization_does_not_add_new_counter():
    from src.application.runtime.state_persistence import persisted_turn_budget
    from src.application.runtime.state_types import SessionContext, TurnState, LoopConfig
    from src.application.runtime.agent_loop import AgentLoop
    old = {"turn_token_usage": {"total_tokens": 10}, "model_context_window": 1000}
    assert persisted_turn_budget(old) == old
    loop = AgentLoop(None, RuntimeControlRegistry())
    composed = loop._compose_graph_state(SessionContext("old", "normal", "interactive", "default"),
        TurnState("turn", "trace", "request", "request"), LoopConfig())
    assert "model_call_count" not in composed
    composed["model_call_count"] = 4
    session, turn, config = loop._decompose_graph_state(composed)
    assert loop._compose_graph_state(session, turn, config)["model_call_count"] == 4
