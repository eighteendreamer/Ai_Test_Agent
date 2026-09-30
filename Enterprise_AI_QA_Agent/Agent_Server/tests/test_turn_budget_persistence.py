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

    restored["model_tool_calls"] = [{"id": "tool-1", "name": "demo", "arguments": {}}]
    restored["continue_loop"] = True
    await build_finalizer_node(_ModelRuntime())(restored)
    assert restored["termination_reason"] == "context_budget_exhausted"
    assert restored["continue_loop"] is False
