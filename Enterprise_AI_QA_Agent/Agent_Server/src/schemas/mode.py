from __future__ import annotations

from pydantic import BaseModel, Field

from src.schemas.intent import ActivationPolicy


class ModeDescriptor(BaseModel):
    key: str
    name: str
    summary: str
    description: str
    category: str = "general"
    is_test_mode: bool = False
    default_agent_key: str
    allowed_agent_keys: list[str] = Field(default_factory=list)
    default_skill_keys: list[str] = Field(default_factory=list)
    on_demand_skill_keys: list[str] = Field(default_factory=list)
    registered_tool_keys: list[str] = Field(default_factory=list)
    harness_key: str = "base_conversation"
    activation_policy: ActivationPolicy = "explicit_only"
    minimum_authorization: str = "none"
    allowed_environments: list[str] = Field(default_factory=list)
    blocked_environments: list[str] = Field(default_factory=list)
    maximum_auto_risk_level: str = "low"
    core_capability_keys: list[str] = Field(default_factory=list)
    on_demand_capability_keys: list[str] = Field(default_factory=list)
    denied_capability_keys: list[str] = Field(default_factory=list)
    public_entry_tool_key: str | None = None
    allow_subworkflows: bool = True
    allowed_subworkflow_keys: list[str] = Field(default_factory=list)
    placeholder: bool = False
    tags: list[str] = Field(default_factory=list)
