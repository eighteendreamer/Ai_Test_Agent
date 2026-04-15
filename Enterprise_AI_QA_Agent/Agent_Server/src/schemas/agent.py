from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PermissionLevel = Literal["safe", "ask", "restricted"]


class ToolDescriptor(BaseModel):
    key: str
    name: str
    description: str
    category: str
    permission_level: PermissionLevel = "safe"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    supports_streaming: bool = False
    enabled_by_default: bool = True
    tags: list[str] = Field(default_factory=list)


class ModelDescriptor(BaseModel):
    key: str
    name: str
    provider: str
    summary: str
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = True
    tags: list[str] = Field(default_factory=list)


class SkillDescriptor(BaseModel):
    key: str
    name: str
    summary: str
    description: str
    recommended_agents: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class MCPServerDescriptor(BaseModel):
    key: str
    name: str
    summary: str
    transport: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True


class AgentDescriptor(BaseModel):
    key: str
    name: str
    role: str
    summary: str
    description: str
    supported_tools: list[str] = Field(default_factory=list)
    supported_skills: list[str] = Field(default_factory=list)
    supported_models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    tags: list[str] = Field(default_factory=list)
