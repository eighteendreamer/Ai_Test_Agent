from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PromptSectionCacheScope = Literal["static", "dynamic", "ephemeral"]
PromptSectionChannel = Literal["system", "runtime_message"]


class PromptSection(BaseModel):
    key: str
    content: str
    source: str
    title: str | None = None
    channel: PromptSectionChannel = "system"
    cache_scope: PromptSectionCacheScope = "dynamic"
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def render(self) -> str:
        body = self.content.strip()
        if not body:
            return ""
        if self.title:
            return f"# {self.title}\n{body}"
        return body


class PromptAssemblyResult(BaseModel):
    system_sections: list[PromptSection] = Field(default_factory=list)
    runtime_message_sections: list[PromptSection] = Field(default_factory=list)
    system_prompt: str = ""
    runtime_messages: list[dict[str, Any]] = Field(default_factory=list)
