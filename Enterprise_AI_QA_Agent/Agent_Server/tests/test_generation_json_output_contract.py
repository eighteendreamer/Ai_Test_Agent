"""Regression from a real truncated default-model response, not partial repair."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.application.test_cases.generation_pipeline import ModelTestCaseGenerator, _ModelGeneratedPayload
from src.schemas.case_management import GeneratedTestCaseDraft, TestCaseGenerateRequest as GenerateRequest
from src.schemas.model_config import ModelInvocationResult
from test_document_generation_stream import captured_scope, Models
from test_test_case_generation_pipeline import _ModelRuntime, _SkillRuntime


LEGACY_FIELDS = {"preconditions", "steps", "assertions", "test_data", "cleanup"}


def captured():
    fixture = json.loads((Path(__file__).parent / "fixtures/default_generation_duplicate_truncated.json").read_bytes())
    response = fixture["response"]
    assert hashlib.sha256(response["text"].encode()).hexdigest() == fixture["public_text_sha256"]
    assert response["response_summary"]["finish_reason"] == "length"
    # Extract only a *closed first case* for comparison. The full response is
    # still invalid and must never be accepted by the production parser.
    start = response["text"].index("[") + 1
    case, end = json.JSONDecoder().raw_decode(response["text"], start)
    assert response["text"][end:].startswith(",{\"case_key\":")
    return response, case


@pytest.mark.parametrize("context", [
    {"generation_template": {"payload": {"example_json": {"业务": [False, 0, None]}}}},
    {"incremental_generation": {}},
    {"document_generation": {}, "generation_template": {"payload": {}}},
])
def test_new_json_request_exposes_one_complete_business_content(context):
    request = ModelTestCaseGenerator._build_request(
        GenerateRequest(objective="根据实际文档生成", mode_key="api_testing"), context, ["skill"])
    schema = request.output_format.schema_definition
    prompt_schema, _ = json.JSONDecoder().raw_decode(request.system_prompt.split("JSON Schema: ", 1)[1])
    assert prompt_schema == schema
    draft = schema["$defs"]["GeneratedTestCaseDraft"]
    assert not LEGACY_FIELDS.intersection(draft["properties"])
    assert draft["additionalProperties"] is False
    assert "content_json" in draft["required"]
    assert draft["properties"]["content_json"] == {
        "type": "object", "minProperties": 1, "additionalProperties": {"$ref": "#/$defs/JsonValue"}}
    assert "TestCaseStep" not in schema["$defs"] and "TestCaseAssertion" not in schema["$defs"]
    assert not request.tools and json.loads(request.messages[0]["content"])["evidence_context"] == context
    # Generating the new schema must not mutate the canonical legacy DTO.
    assert LEGACY_FIELDS.issubset(_ModelGeneratedPayload.model_json_schema()["$defs"]["GeneratedTestCaseDraft"]["properties"])


def test_actual_closed_case_projection_preserves_all_executed_content():
    _, case = captured()
    assert LEGACY_FIELDS.issubset(case) and case["content_json"]
    projected = {key: deepcopy(value) for key, value in case.items() if key not in LEGACY_FIELDS}
    before = GeneratedTestCaseDraft.model_validate(case)
    after = GeneratedTestCaseDraft.model_validate(projected)
    assert before.business_content() == after.business_content() == case["content_json"]
    for field in ("case_key", "title", "case_type", "priority", "execution_notes", "generation_metadata"):
        assert getattr(before, field) == getattr(after, field)
    assert len(json.dumps(projected, ensure_ascii=False)) < len(json.dumps(case, ensure_ascii=False))
    # Extension semantics are a separate, explicit test, not claimed as model output.
    projected["content_json"]["业务扩展"] = {"任意嵌套": [False, 0, None, "中文😀", {"steps": []}]}
    parsed = ModelTestCaseGenerator._parse_payload(json.dumps({"cases": [projected]}, ensure_ascii=False))
    assert parsed.cases[0].business_content() == projected["content_json"]
    for invalid in (None, {}):
        with pytest.raises(ValueError):
            GeneratedTestCaseDraft.model_validate({**projected, "content_json": invalid})


@pytest.mark.asyncio
async def test_actual_truncated_response_stays_rejected_and_history_stays_authoritative():
    response, case = captured()
    raw, scope = captured_scope()
    old = scope.modules[0].generation
    models = Models()
    generator = ModelTestCaseGenerator(model_runtime_service=models, skill_runtime_service=None)
    current = await generator.prepare_frozen(request=old.request, authoring=old.authoring,
        context=json.loads(old.invocation.messages[0]["content"])["evidence_context"])
    assert current.prompt_version == "test-case-generation-document-v5"
    assert old.prompt_version == "test-case-generation-document-v1"
    assert scope.model_dump(mode="json") == raw
    for prepared in (old, current):
        with pytest.raises(ValueError, match="截断"):
            generator.parse_frozen(prepared, ModelInvocationResult.model_validate(response))
        complete = ModelInvocationResult(text=json.dumps({"cases": [case]}),
            response_summary={"mode": "ok", "finish_reason": "stop"})
        batch = generator.parse_frozen(prepared, complete)
        assert batch.cases[0].business_content() == case["content_json"]
        assert batch.prompt_version == prepared.prompt_version
    await generator.invoke_frozen(old)
    assert models.calls[0]["request"] == old.invocation
    assert scope.model_dump(mode="json") == raw


@pytest.mark.asyncio
async def test_new_direct_generation_records_prompt_and_legacy_generation_keeps_contract():
    _, case = captured()
    projected = {key: value for key, value in case.items() if key not in LEGACY_FIELDS}
    models = _ModelRuntime(json.dumps({"cases": [projected]}))
    generator = ModelTestCaseGenerator(model_runtime_service=models, skill_runtime_service=_SkillRuntime())
    batch = await generator.generate(request=GenerateRequest(objective="生成草稿", mode_key="api_testing"),
        context={"generation_template": {"payload": {}}})
    assert batch.prompt_version == "test-case-generation-json-v6"
    request = ModelTestCaseGenerator._build_request(
        GenerateRequest(objective="旧格式生成", mode_key="api_testing"), {}, ["skill"])
    assert request.output_format.schema_definition == _ModelGeneratedPayload.model_json_schema()
