from __future__ import annotations

import pytest

from src.application.model_adapters.tool_adapter import LangChainToolAdapter
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall


def _tool(key: str = "read_only_probe") -> ToolDescriptor:
    return ToolDescriptor(
        key=key,
        name="Read-only probe",
        description="Read-only test tool",
        category="test",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    )


def test_registry_descriptor_maps_to_langchain_schema_without_executor():
    schemas = LangChainToolAdapter().to_schemas([_tool()])

    assert schemas == [
        {
            "name": "read_only_probe",
            "description": "Read-only test tool",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        }
    ]


def test_model_tool_call_must_be_in_selected_registry_tools():
    call = ModelToolCall(id="call-1", name="read_only_probe", arguments={"target": "x"})

    assert LangChainToolAdapter().validate_call(call, [_tool()]) == call

    with pytest.raises(ValueError, match="outside the selected Registry"):
        LangChainToolAdapter().validate_call(
            call.model_copy(update={"name": "not-registered"}),
            [_tool()],
        )
