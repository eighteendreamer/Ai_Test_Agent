from __future__ import annotations

from typing import Any, TypedDict


class AgentGraphState(TypedDict):
    session_id: str
    turn_id: str
    trace_id: str
    user_message: str
    normalized_input: str
    session_mode: str
    runtime_mode: str
    message_count: int
    preferred_model: str
    selected_agent_key: str
    selected_agent_name: str
    selected_model_key: str
    selected_model_name: str
    selected_model_provider: str
    requested_skill_keys: list[str]
    resolved_skill_keys: list[str]
    skill_prompt_blocks: list[str]
    memory_hits: list[dict[str, Any]]
    memory_prompt_blocks: list[str]
    active_mcp_servers: list[dict[str, Any]]
    mcp_prompt_blocks: list[str]
    available_tool_keys: list[str]
    allowed_tool_keys: list[str]
    approval_required_tool_keys: list[str]
    pending_approvals: list[dict[str, Any]]
    plan_steps: list[str]
    system_prompt: str
    runtime_messages: list[dict[str, Any]]
    model_request_payload: dict[str, Any]
    model_response_text: str
    assistant_tool_call_message: dict[str, Any]
    model_tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    tool_messages: list[dict[str, Any]]
    worker_dispatches: list[dict[str, Any]]
    context_bundle: dict[str, Any]
    event_log: list[dict[str, Any]]
    final_response: str
    pending_turn: dict[str, Any]
    loop_iteration: int
    max_iterations: int
    continue_loop: bool
    termination_reason: str
