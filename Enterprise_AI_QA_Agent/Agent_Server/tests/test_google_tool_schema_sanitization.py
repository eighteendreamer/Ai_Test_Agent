from __future__ import annotations

import pytest
from google.genai import types as genai_types
from pydantic import ValidationError

from src.application.model_clients.base import ProviderClientError
from src.application.model_clients.google_client import GoogleGenAIClient
from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.registry.tools import ToolRegistry


def test_google_schema_conversion_preserves_supported_constraints() -> None:
    schema = {
        "type": "object",
        "required": ["external_scanners", "profile", "missing"],
        "properties": {
            "unconstrained": {},
            "external_scanners": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "Scanner names to run.",
            },
            "kind": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                    {"type": "null"},
                ]
            },
            "optional_tag": {"type": ["string", "null"]},
            "profile": {"$ref": "#/$defs/Profile"},
        },
        "$defs": {
            "Profile": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }

    converted = GoogleGenAIClient()._sanitize_function_parameters(schema)

    assert converted["required"] == [
        "external_scanners",
        "profile",
        "missing",
    ]
    assert converted["properties"]["unconstrained"] == {}
    assert converted["additionalProperties"] is False
    assert converted["defs"]["Profile"]["required"] == ["name"]
    assert converted["defs"]["Profile"]["additionalProperties"] is False
    assert converted["properties"]["profile"]["ref"] == "#/defs/Profile"
    assert converted["properties"]["external_scanners"]["anyOf"] == [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ]
    assert converted["properties"]["kind"] == {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
            {"type": "null"},
        ]
    }
    assert converted["properties"]["optional_tag"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }
    genai_types.Schema.model_validate(converted)


@pytest.mark.parametrize(
    ("schema", "keyword"),
    [
        ({"allOf": [{"type": "string"}, {"minLength": 2}]}, "allOf"),
        (
            {"oneOf": [{"type": "number"}, {"type": "integer"}]},
            "oneOf",
        ),
        (
            {"type": "object", "patternProperties": {"^x": {"type": "string"}}},
            "patternProperties",
        ),
        ({"type": []}, "type"),
        ({"type": ["string", 42]}, "type"),
    ],
)
def test_google_schema_conversion_rejects_unrepresentable_constraints(
    schema: dict,
    keyword: str,
) -> None:
    with pytest.raises(ProviderClientError, match=keyword):
        GoogleGenAIClient()._sanitize_function_parameters(schema)


def test_registered_tool_schemas_validate_as_google_function_parameters() -> None:
    client = GoogleGenAIClient()

    for tool in ToolRegistry().list():
        schema = client._sanitize_function_parameters(tool.input_schema)
        genai_types.FunctionDeclaration(
            name="schema_probe",
            parameters=schema,
        )


def test_api_timeout_schema_maps_for_google_without_accepting_zero_at_runtime() -> None:
    tool = ToolRegistry().get("api-test-runner")
    timeout_schema = tool.input_schema["properties"]["task"]["properties"][
        "timeout_seconds"
    ]
    converted = GoogleGenAIClient()._sanitize_function_parameters(timeout_schema)
    timeout_branch = next(
        branch for branch in converted["anyOf"] if branch.get("type") == "number"
    )

    assert timeout_branch["minimum"] == 0
    assert "Must be greater than 0." in timeout_branch["description"]
    genai_types.Schema.model_validate(converted)

    with pytest.raises(ValidationError):
        ApiTestTask(task_id="timeout-boundary", method="GET", path="/", timeout_seconds=0)


def test_google_schema_conversion_preserves_empty_nested_schema() -> None:
    converted = GoogleGenAIClient()._sanitize_function_parameters(
        {"type": "object", "properties": {"payload": {}}}
    )

    assert converted["properties"]["payload"] == {}


def test_google_schema_conversion_rejects_non_string_required_entries() -> None:
    with pytest.raises(ProviderClientError, match="required.*strings"):
        GoogleGenAIClient()._sanitize_function_parameters(
            {"type": "object", "required": ["name", 1]}
        )


def test_google_schema_conversion_does_not_ignore_constraints_on_null_branches() -> None:
    with pytest.raises(ProviderClientError, match="allOf"):
        GoogleGenAIClient()._sanitize_function_parameters(
            {
                "anyOf": [
                    {"type": "null", "allOf": [{"type": "object"}]},
                    {"type": "string"},
                ]
            }
        )


def test_google_schema_conversion_merges_definition_vocabularies() -> None:
    converted = GoogleGenAIClient()._sanitize_function_parameters(
        {
            "type": "object",
            "$defs": {"Profile": {"type": "string"}},
            "definitions": {"LegacyId": {"type": "integer"}},
            "properties": {
                "profile": {"$ref": "#/$defs/Profile"},
                "legacy_id": {"$ref": "#/definitions/LegacyId"},
            },
        }
    )

    assert set(converted["defs"]) == {"Profile", "LegacyId"}
    assert converted["properties"]["profile"]["ref"] == "#/defs/Profile"
    assert converted["properties"]["legacy_id"]["ref"] == "#/defs/LegacyId"


def test_google_schema_conversion_rejects_colliding_definition_names() -> None:
    with pytest.raises(ProviderClientError, match="definition names collide"):
        GoogleGenAIClient()._sanitize_function_parameters(
            {
                "$defs": {"Record": {"type": "string"}},
                "definitions": {"Record": {"type": "integer"}},
            }
        )


def test_google_schema_conversion_rejects_nested_defs() -> None:
    with pytest.raises(ProviderClientError, match="only at the root"):
        GoogleGenAIClient()._sanitize_function_parameters(
            {
                "type": "object",
                "properties": {
                    "record": {"defs": {"Record": {"type": "string"}}}
                },
            }
        )
