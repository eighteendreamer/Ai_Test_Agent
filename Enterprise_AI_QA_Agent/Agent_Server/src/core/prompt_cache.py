"""D4 Prompt Cache: stable prefix + cache breakpoints for LLM API cache hits.

The key insight: LLM APIs (Anthropic, OpenAI) cache prompt prefixes. If the
prefix is stable across turns, subsequent calls hit the cache and save tokens.

Strategy:
  1. Build a stable prefix (identity + contract + mode) that rarely changes
  2. Build a variable suffix (tool results + dynamic context) that changes each turn
  3. Mark cache breakpoints at the boundary so the API knows where to split

Tool definitions are sorted by key (alphabetical) to ensure prefix stability
when tools are added/removed (new tools append to the end, not middle).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptPayload:
    """A prompt payload with cache breakpoint information."""

    system: str
    messages: list[dict[str, Any]]
    cache_breakpoints: list[int] = field(default_factory=list)
    estimated_prefix_tokens: int = 0

    @property
    def stable_prefix(self) -> str:
        """The cacheable prefix portion of the system prompt."""
        if not self.cache_breakpoints:
            return self.system
        first_break = self.cache_breakpoints[0]
        return self.system[:first_break]

    @property
    def variable_suffix(self) -> str:
        """The non-cacheable suffix that changes each turn."""
        if not self.cache_breakpoints:
            return ""
        first_break = self.cache_breakpoints[0]
        return self.system[first_break:]


class PromptCacheBuilder:
    """Build prompt payloads with cache-optimized structure.

    Usage:
        builder = PromptCacheBuilder()
        builder.set_stable_prefix(identity_block, contract_block, mode_block)
        builder.set_variable_suffix(dynamic_tools, memory, user_message)
        payload = builder.build()
    """

    def __init__(self) -> None:
        self._stable_parts: list[str] = []
        self._variable_parts: list[str] = []
        self._tool_definitions: list[dict[str, Any]] = []

    def set_stable_prefix(self, *parts: str) -> None:
        """Set the stable prefix parts (identity, contract, mode, etc.)."""
        self._stable_parts = [p for p in parts if p]

    def add_variable_part(self, content: str) -> None:
        """Add a variable part (dynamic context, tool results, etc.)."""
        if content:
            self._variable_parts.append(content)

    def set_tool_definitions(self, tools: list[dict[str, Any]]) -> None:
        """Set tool definitions, sorted by key for prefix stability."""
        self._tool_definitions = sorted(tools, key=lambda t: t.get("name", t.get("key", "")))

    def build(self) -> PromptPayload:
        """Build the final prompt payload with cache breakpoints."""
        stable_prefix = "\n\n".join(self._stable_parts)
        tool_block = self._render_tools()
        variable_suffix = "\n\n".join(self._variable_parts)

        system_parts = [stable_prefix]
        if tool_block:
            system_parts.append(tool_block)
        if variable_suffix:
            system_parts.append(variable_suffix)

        system = "\n\n---\n\n".join(system_parts)

        breakpoint_pos = len(stable_prefix) + 5
        if tool_block:
            breakpoint_pos += len(tool_block) + 10

        return PromptPayload(
            system=system,
            messages=[],
            cache_breakpoints=[breakpoint_pos] if stable_prefix else [],
            estimated_prefix_tokens=self._estimate_tokens(stable_prefix),
        )

    def _render_tools(self) -> str:
        """Render tool definitions in a stable order."""
        if not self._tool_definitions:
            return ""
        lines = ["Available tools:"]
        for tool in self._tool_definitions:
            name = tool.get("name", tool.get("key", "unknown"))
            desc = tool.get("description", "")
            lines.append(f"- {name}: {desc[:120]}")
        return "\n".join(lines)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count estimate (4 chars per token for English)."""
        return len(text) // 4


def sort_tools_stable(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort tool definitions by key for prompt cache stability.

    New tools append to the end, preserving the existing prefix.
    """
    return sorted(tools, key=lambda t: t.get("name", t.get("key", "")))
