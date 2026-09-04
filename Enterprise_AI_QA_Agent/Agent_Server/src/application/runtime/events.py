"""Loop events for D2 Agent Loop streaming.

LoopEvent is yielded by AgentLoop.stream_turn() to provide real-time
visibility into the agent loop progression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LoopEventType(str, Enum):
    """Event types emitted by the agent loop."""

    TURN_STARTED = "turn.started"
    MODEL_RESPONSE = "model.response_received"
    TOOL_EXECUTED = "tool.execution_completed"
    LOOP_REENTER = "runtime.loop_reenter"
    TURN_COMPLETED = "runtime.turn_completed"
    TURN_INTERRUPT = "turn.interrupted"
    CONTEXT_COMPACTED = "runtime.context_compacted"
    APPROVAL_REQUIRED = "turn.approval_required"


@dataclass(frozen=True)
class LoopEvent:
    """A structured event emitted during agent loop execution.

    Events are yielded by AgentLoop.stream_turn() between graph invocations,
    providing real-time visibility into the loop progression.
    """

    type: LoopEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    iteration: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "iteration": self.iteration,
            "payload": self.payload,
        }
