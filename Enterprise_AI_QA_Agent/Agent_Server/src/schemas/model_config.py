from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.schemas.prompting import PromptSection
from src.schemas.tool_runtime import ModelToolCall


ModelTransport = Literal[
    "anthropic_messages",
    "openai_chat_completions",
    "google_gemini_generate_content",
]


ContentPartType = Literal[
    "text",
    "image_url",
    "image_base64",
    "file",
    "tool_result",
]


class ContentPart(BaseModel):
    type: ContentPartType
    text: str | None = None
    url: str | None = None
    mime_type: str | None = None
    data_base64: str | None = None
    file_name: str | None = None
    tool_name: str | None = None
    payload: dict[str, Any] | None = None


class UnifiedMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    tool_call_id: str | None = None
    parts: list[ContentPart] = Field(default_factory=list)


class ModelCapabilities(BaseModel):
    text_input: bool = True
    text_output: bool = True
    tool_calling: bool = True
    vision: bool = False
    multi_image: bool = False
    file_input: bool = False
    pdf_input: bool = False
    reasoning: bool = False
    json_mode: bool = False
    streaming: bool = True
    parallel_tool_calls: bool = False
    image_url_input: bool = True
    image_base64_input: bool = True


class ModelCapabilitiesOverride(BaseModel):
    text_input: bool | None = None
    text_output: bool | None = None
    tool_calling: bool | None = None
    vision: bool | None = None
    multi_image: bool | None = None
    file_input: bool | None = None
    pdf_input: bool | None = None
    reasoning: bool | None = None
    json_mode: bool | None = None
    streaming: bool | None = None
    parallel_tool_calls: bool | None = None
    image_url_input: bool | None = None
    image_base64_input: bool | None = None

    def has_values(self) -> bool:
        return bool(self.model_dump(exclude_none=True))

    def apply_to(self, base: ModelCapabilities) -> ModelCapabilities:
        return base.model_copy(update=self.model_dump(exclude_none=True))


class ModelConfigRecord(BaseModel):
    id: int | None = None
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
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    capability_overrides: ModelCapabilitiesOverride = Field(default_factory=ModelCapabilitiesOverride)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _sync_capabilities(self):
        self.capabilities.tool_calling = bool(self.supports_tools)
        self.capabilities.vision = bool(self.supports_vision)
        self.capabilities.reasoning = bool(self.supports_reasoning)
        return self


class ModelConfigPublic(BaseModel):
    id: int | None = None
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
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    capability_overrides: ModelCapabilitiesOverride = Field(default_factory=ModelCapabilitiesOverride)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ModelInvocationRequest(BaseModel):
    system_prompt: str
    messages: list[dict[str, Any]]
    structured_messages: list[UnifiedMessage] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt_sections: list[PromptSection] = Field(default_factory=list)
    runtime_message_sections: list[PromptSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_messages(self):
        if self.structured_messages:
            return self
        self.structured_messages = [
            UnifiedMessage(
                role=self._coerce_role(item.get("role")),
                tool_call_id=str(item.get("tool_call_id") or "") or None,
                parts=self._coerce_parts_from_message(item),
            )
            for item in self.messages
            if isinstance(item, dict)
        ]
        return self

    @staticmethod
    def _coerce_role(value: Any) -> Literal["system", "user", "assistant", "tool"]:
        role = str(value or "user").strip().lower()
        if role in {"system", "user", "assistant", "tool"}:
            return role  # type: ignore[return-value]
        return "user"

    @classmethod
    def _coerce_parts_from_message(cls, item: dict[str, Any]) -> list[ContentPart]:
        role = cls._coerce_role(item.get("role"))
        if role == "tool":
            content = item.get("content", "")
            payload = content if isinstance(content, dict) else None
            text = content if isinstance(content, str) else None
            return [
                ContentPart(
                    type="tool_result",
                    text=text,
                    tool_name=str(item.get("name") or "") or None,
                    payload=payload,
                )
            ]
        return cls._coerce_parts(item.get("content"))

    @classmethod
    def _coerce_parts(cls, content: Any) -> list[ContentPart]:
        if isinstance(content, str):
            return [ContentPart(type="text", text=content)]
        if isinstance(content, list):
            parts: list[ContentPart] = []
            for entry in content:
                part = cls._coerce_part(entry)
                if part is not None:
                    parts.append(part)
            return parts or [ContentPart(type="text", text="")]
        if isinstance(content, dict):
            part = cls._coerce_part(content)
            if part is not None:
                return [part]
        return [ContentPart(type="text", text=str(content or ""))]

    @classmethod
    def _coerce_part(cls, entry: Any) -> ContentPart | None:
        if isinstance(entry, str):
            return ContentPart(type="text", text=entry)
        if not isinstance(entry, dict):
            return None

        entry_type = str(entry.get("type") or "").strip().lower()
        if entry_type in {"text", "output_text"}:
            text_value = entry.get("text")
            if isinstance(text_value, dict):
                text_value = text_value.get("value", "")
            return ContentPart(type="text", text=str(text_value or entry.get("content") or ""))

        if entry_type in {"image_url", "input_image"}:
            image_value = entry.get("image_url")
            if isinstance(image_value, dict):
                image_value = image_value.get("url")
            image_value = image_value or entry.get("url")
            if isinstance(image_value, str):
                if image_value.startswith("data:") and ";base64," in image_value:
                    prefix, data_base64 = image_value.split(";base64,", 1)
                    mime_type = prefix.split("data:", 1)[-1] or None
                    return ContentPart(
                        type="image_base64",
                        mime_type=mime_type,
                        data_base64=data_base64,
                    )
                return ContentPart(type="image_url", url=image_value)

        if entry_type == "image":
            source = entry.get("source")
            if isinstance(source, dict):
                if source.get("type") == "base64":
                    return ContentPart(
                        type="image_base64",
                        mime_type=str(source.get("media_type") or "") or None,
                        data_base64=str(source.get("data") or "") or None,
                    )
                if source.get("type") == "url":
                    return ContentPart(type="image_url", url=str(source.get("url") or "") or None)

        if entry_type == "file":
            return ContentPart(
                type="file",
                file_name=str(entry.get("file_name") or entry.get("name") or "") or None,
                mime_type=str(entry.get("mime_type") or "") or None,
                url=str(entry.get("url") or "") or None,
                data_base64=str(entry.get("data_base64") or "") or None,
            )

        if entry_type == "tool_result":
            payload = entry.get("payload")
            text = entry.get("content") if isinstance(entry.get("content"), str) else None
            return ContentPart(
                type="tool_result",
                tool_name=str(entry.get("tool_name") or "") or None,
                text=text,
                payload=payload if isinstance(payload, dict) else None,
            )

        if "text" in entry:
            text_value = entry.get("text")
            if isinstance(text_value, dict):
                text_value = text_value.get("value", "")
            return ContentPart(type="text", text=str(text_value or ""))

        return None


class ModelInvocationResult(BaseModel):
    text: str
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
