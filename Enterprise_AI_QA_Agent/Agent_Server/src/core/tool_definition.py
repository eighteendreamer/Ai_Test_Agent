"""D5 Generic Tool Definition: typed input/output with safety declarations.

ToolDefinition replaces the dict-based ToolDescriptor with:
  - Generic[TInput, TOutput] for compile-time type safety
  - Pydantic models for input/output schema validation
  - Safety declarations (concurrency_safe, read_only, destructive)
  - Context modifiers for non-concurrent tools
  - Result budget controls

Reference: Claude Code tool definitions + Grok ToolRegistryBuilder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel


logger = logging.getLogger(__name__)

TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


@dataclass(frozen=True)
class ToolResult(Generic[TOutput]):
    """Typed result from a tool execution."""

    output: TOutput
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True)
class PermissionResult:
    """Result of a permission check."""

    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    approval_prompt: str = ""


@dataclass
class ToolContext:
    """Runtime context available during tool execution."""

    session_id: str = ""
    turn_id: str = ""
    agent_key: str = ""
    mode_key: str = ""
    state: dict[str, Any] = field(default_factory=dict)


class ToolDefinition(Generic[TInput, TOutput]):
    """Base class for typed tool definitions.

    Subclass this to define tools with:
      - input_schema: Pydantic model for input validation
      - output_schema: Pydantic model for output validation
      - Safety declarations: is_concurrency_safe, is_read_only, is_destructive
      - Execution: call() method
      - Permission: check_permissions() method
      - Context modification: context_modifier() for non-concurrent tools
    """

    key: str = ""
    name: str = ""
    description: str = ""
    input_schema: type[TInput] | None = None
    output_schema: type[TOutput] | None = None

    # Safety declarations
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_destructive: bool = False

    # Budget
    max_result_size_chars: int = 24_000

    async def call(self, args: TInput, ctx: ToolContext) -> ToolResult[TOutput]:
        """Execute the tool with validated input. Override in subclass."""
        raise NotImplementedError

    async def check_permissions(
        self, args: TInput, ctx: ToolContext
    ) -> PermissionResult:
        """Check if the tool call is permitted. Override in subclass."""
        return PermissionResult(allowed=True)

    def context_modifier(self, result: TOutput, ctx: ToolContext) -> None:
        """Modify context after execution (for non-concurrent tools).

        Override to update ctx.state based on the tool result.
        This is called between serial tool executions.
        """
        pass

    def validate_input(self, raw: dict[str, Any]) -> TInput:
        """Validate and parse raw input dict into the typed schema."""
        if self.input_schema is None:
            raise ValueError(f"Tool {self.key} has no input_schema")
        return self.input_schema.model_validate(raw)

    def truncate_output(self, output: TOutput) -> tuple[TOutput, bool]:
        """Truncate output if it exceeds max_result_size_chars."""
        output_str = str(output)
        if len(output_str) <= self.max_result_size_chars:
            return output, False
        truncated_str = output_str[: self.max_result_size_chars]
        logger.warning(
            "Tool %s output truncated from %d to %d chars",
            self.key,
            len(output_str),
            self.max_result_size_chars,
        )
        return output, True

    def to_descriptor_dict(self) -> dict[str, Any]:
        """Convert to a dict compatible with the existing ToolDescriptor format."""
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "is_concurrency_safe": self.is_concurrency_safe,
            "is_read_only": self.is_read_only,
            "is_destructive": self.is_destructive,
            "max_result_size_chars": self.max_result_size_chars,
        }
