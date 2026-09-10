from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Awaitable, Callable

from langchain.tools import ToolRuntime
from src.application.model_adapters.message_adapter import to_langchain_tools
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall


class LangChainToolAdapter:
    """Expose selected Registry descriptors and governed callbacks.

    LangChain receives schemas for model binding only.  All actual execution
    continues through the existing ToolRuntimeService, which owns permission,
    approval, audit and artifact behavior.
    """

    def to_schemas(self, tools: Iterable[ToolDescriptor]) -> list[dict[str, Any]]:
        descriptors = list(tools)
        for tool in descriptors:
            if not tool.key.strip():
                raise ValueError("Tool registry descriptor key must not be empty.")
            if not isinstance(tool.input_schema, dict):
                raise ValueError(f"Tool '{tool.key}' input_schema must be an object.")
        return to_langchain_tools(
            [
                {
                    "name": tool.key,
                    "description": tool.description,
                    "input_schema": tool.input_schema or {"type": "object"},
                }
                for tool in descriptors
            ]
        )

    def validate_call(
        self,
        call: ModelToolCall,
        allowed_tools: Iterable[ToolDescriptor],
    ) -> ModelToolCall:
        allowed = {tool.key for tool in allowed_tools}
        if call.name not in allowed:
            raise ValueError(f"Model requested tool '{call.name}' outside the selected Registry tools.")
        if not isinstance(call.arguments, dict):
            raise ValueError(f"Tool '{call.name}' arguments must be an object.")
        return call

    def to_governed_tools(
        self,
        tools: Iterable[ToolDescriptor],
        *,
        invoke: Callable[[ToolDescriptor, str, dict[str, Any]], Awaitable[str]],
    ) -> list[Any]:
        """Build LangChain tools whose execution callback is application-owned.

        The wrapper extracts the framework-provided ``tool_call_id`` from
        ``ToolRuntime`` for Approval/Snapshot correlation. No
        Registry handler is exposed directly to Deep Agents.
        """
        from langchain_core.tools import StructuredTool

        def bind(descriptor):
            async def governed(runtime: ToolRuntime, **arguments):
                return await invoke(descriptor, runtime.tool_call_id, dict(arguments))

            return governed

        result = []
        for descriptor in tools:
            result.append(
                StructuredTool.from_function(
                    coroutine=bind(descriptor),
                    name=descriptor.key,
                    description=descriptor.description,
                    args_schema=descriptor.input_schema or {
                        "type": "object",
                        "properties": {},
                    },
                    infer_schema=False,
                )
            )
        return result
