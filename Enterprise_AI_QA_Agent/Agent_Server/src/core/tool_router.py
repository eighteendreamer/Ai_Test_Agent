"""D5 Tool Router: parallel execution for concurrency-safe tools + streaming.

ToolRouter splits tool calls into parallel (concurrency-safe) and serial batches.
ToolStream provides AsyncGenerator-based progress reporting for long-running tools.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Generic, TypeVar

from src.core.tool_definition import ToolContext, ToolDefinition, ToolResult


logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ToolCall:
    """A pending tool invocation."""

    tool_key: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolCallResult:
    """Result of a tool invocation through the router."""

    call_id: str
    tool_key: str
    result: ToolResult | None = None
    error: str | None = None
    execution_time_seconds: float = 0.0


@dataclass
class ToolProgressEvent(Generic[T]):
    """A progress event from a streaming tool."""

    kind: str  # "progress" | "result" | "error"
    data: Any = None


class ToolStream(Generic[T]):
    """AsyncGenerator wrapper for streaming tool output.

    Invariant: yields Progress* events followed by exactly one Terminal (Result | Error).
    """

    def __init__(self, generator: AsyncGenerator[ToolProgressEvent[T], None]) -> None:
        self._generator = generator

    def __aiter__(self) -> AsyncGenerator[ToolProgressEvent[T], None]:
        return self._generator.__aiter__()


class ToolRouter:
    """Route tool calls: parallel for concurrency-safe, serial for the rest.

    Usage:
        router = ToolRouter(tool_registry)
        results = await router.execute_batch(calls, ctx)
    """

    def __init__(
        self,
        tool_definitions: dict[str, ToolDefinition] | None = None,
        max_parallel: int = 5,
    ) -> None:
        self._tool_definitions = tool_definitions or {}
        self._max_parallel = max_parallel

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition."""
        self._tool_definitions[tool.key] = tool

    async def execute_batch(
        self,
        calls: list[ToolCall],
        ctx: ToolContext,
    ) -> list[ToolCallResult]:
        """Execute a batch of tool calls.

        Concurrency-safe tools run in parallel (up to max_parallel).
        Non-concurrency-safe tools run serially with context_modifier between each.
        """
        parallel_calls: list[ToolCall] = []
        serial_calls: list[ToolCall] = []

        for call in calls:
            tool = self._tool_definitions.get(call.tool_key)
            if tool is not None and tool.is_concurrency_safe:
                parallel_calls.append(call)
            else:
                serial_calls.append(call)

        results: list[ToolCallResult] = []

        if parallel_calls:
            parallel_results = await self._execute_parallel(parallel_calls, ctx)
            results.extend(parallel_results)

        for call in serial_calls:
            result = await self._execute_single(call, ctx)
            results.append(result)
            tool = self._tool_definitions.get(call.tool_key)
            if tool is not None and result.result is not None:
                tool.context_modifier(result.result.output, ctx)

        return results

    async def _execute_parallel(
        self,
        calls: list[ToolCall],
        ctx: ToolContext,
    ) -> list[ToolCallResult]:
        """Execute concurrency-safe tools in parallel."""
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def _bounded_execute(call: ToolCall) -> ToolCallResult:
            async with semaphore:
                return await self._execute_single(call, ctx)

        tasks = [asyncio.create_task(_bounded_execute(c)) for c in calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_single(
        self,
        call: ToolCall,
        ctx: ToolContext,
    ) -> ToolCallResult:
        """Execute a single tool call."""
        import time
        start = time.monotonic()

        tool = self._tool_definitions.get(call.tool_key)
        if tool is None:
            return ToolCallResult(
                call_id=call.call_id,
                tool_key=call.tool_key,
                error=f"Unknown tool: {call.tool_key}",
                execution_time_seconds=time.monotonic() - start,
            )

        try:
            validated_input = tool.validate_input(call.args)
            perm = await tool.check_permissions(validated_input, ctx)
            if not perm.allowed:
                return ToolCallResult(
                    call_id=call.call_id,
                    tool_key=call.tool_key,
                    error=f"Permission denied: {perm.reason}",
                    execution_time_seconds=time.monotonic() - start,
                )

            result = await tool.call(validated_input, ctx)
            return ToolCallResult(
                call_id=call.call_id,
                tool_key=call.tool_key,
                result=result,
                execution_time_seconds=time.monotonic() - start,
            )
        except Exception as exc:
            logger.exception("Tool %s execution failed", call.tool_key)
            return ToolCallResult(
                call_id=call.call_id,
                tool_key=call.tool_key,
                error=str(exc),
                execution_time_seconds=time.monotonic() - start,
            )
