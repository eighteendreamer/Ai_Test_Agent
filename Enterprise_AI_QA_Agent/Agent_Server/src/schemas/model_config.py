from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.schemas.prompting import PromptSection
from src.schemas.tool_runtime import ModelToolCall


ModelTransport = Literal["anthropic_messages", "openai_chat_completions"]


class ModelConfigRecord(BaseModel):
    key: str
    name: str
    provider: str
    transport: ModelTransport
    model_id: str
    api_base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    description: str = ""
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = True
    is_active: bool = False
    is_default: bool = False
    temperature: float | None = None
    max_tokens: int = 4096
    extra_headers: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ModelConfigPublic(BaseModel):
    key: str
    name: str
    provider: str
    transport: ModelTransport
    model_id: str
    api_base_url: str
    description: str = ""
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = True
    is_active: bool = False
    is_default: bool = False
    temperature: float | None = None
    max_tokens: int = 4096
    has_secret: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ModelInvocationRequest(BaseModel):
    system_prompt: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt_sections: list[PromptSection] = Field(default_factory=list)
    runtime_message_sections: list[PromptSection] = Field(default_factory=list)


class ModelInvocationResult(BaseModel):
    text: str
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
