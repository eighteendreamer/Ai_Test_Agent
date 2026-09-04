"""State object split for D2 Agent Loop upgrade.

The 67-field AgentGraphState is logically split into three objects with different
lifetimes. AgentLoop uses these internally and flattens them into AgentGraphState
before calling the graph, then decomposes the result back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionContext:
    """Session-scoped state that persists across turns.

    These fields are set once when the session starts and remain immutable
    throughout the session lifetime.
    """

    session_id: str
    session_mode: str
    runtime_mode: str
    mode_key: str
    preferred_model: str = ""
    message_count: int = 0


@dataclass
class TurnState:
    """Turn-scoped state that accumulates across loop iterations within a single turn.

    These fields are initialized at turn start and mutated as the loop progresses
    through model invocations and tool executions.
    """

    turn_id: str
    trace_id: str
    user_message: str
    normalized_input: str

    # Agent & model selection (resolved per turn)
    selected_agent_key: str = ""
    selected_agent_name: str = ""
    selected_model_key: str = ""
    selected_model_name: str = ""
    selected_model_provider: str = ""

    # Skills (resolved per turn)
    requested_skill_keys: list[str] = field(default_factory=list)
    resolved_skill_keys: list[str] = field(default_factory=list)
    skill_prompt_blocks: list[str] = field(default_factory=list)

    # Memory & observations (retrieved per turn)
    memory_hits: list[dict[str, Any]] = field(default_factory=list)
    memory_prompt_blocks: list[str] = field(default_factory=list)
    observation_hits: list[dict[str, Any]] = field(default_factory=list)
    observation_prompt_blocks: list[str] = field(default_factory=list)

    # MCP servers (active for this turn)
    active_mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    mcp_prompt_blocks: list[str] = field(default_factory=list)

    # Tool management (evaluated per turn)
    available_tool_keys: list[str] = field(default_factory=list)
    deferred_tool_keys: list[str] = field(default_factory=list)
    model_visible_tool_keys: list[str] = field(default_factory=list)
    allowed_tool_keys: list[str] = field(default_factory=list)
    approval_required_tool_keys: list[str] = field(default_factory=list)
    denied_tool_keys: list[str] = field(default_factory=list)
    permission_decisions: list[dict[str, Any]] = field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)

    # Planning
    plan_steps: list[str] = field(default_factory=list)

    # Prompt assembly (built per turn)
    system_prompt_sections: list[dict[str, Any]] = field(default_factory=list)
    runtime_message_sections: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    runtime_messages: list[dict[str, Any]] = field(default_factory=list)

    # Model invocation (per iteration, but accumulated token usage)
    model_request_payload: dict[str, Any] = field(default_factory=dict)
    model_response_summary: dict[str, Any] = field(default_factory=dict)
    model_response_text: str = ""
    turn_token_usage: dict[str, Any] = field(default_factory=dict)
    model_context_window: int = 0
    assistant_tool_call_message: dict[str, Any] = field(default_factory=dict)
    model_tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # Tool execution (accumulated across iterations)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    tool_messages: list[dict[str, Any]] = field(default_factory=list)
    worker_dispatches: list[dict[str, Any]] = field(default_factory=list)

    # Context & events
    context_bundle: dict[str, Any] = field(default_factory=dict)
    event_log: list[dict[str, Any]] = field(default_factory=list)

    # Final response & control
    final_response: str = ""
    pending_turn: dict[str, Any] = field(default_factory=dict)
    control_state: str = ""
    interrupt_requested: bool = False
    interrupt_reason: str = ""
    loop_iteration: int = 0
    continue_loop: bool = False
    termination_reason: str = ""

    # Internal (not serialized)
    _event_queue: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class LoopConfig:
    """Immutable configuration for the agent loop.

    These fields are set at loop construction time and never change.
    """

    max_iterations: int = 8
    context_budget_ratio: float = 0.9
