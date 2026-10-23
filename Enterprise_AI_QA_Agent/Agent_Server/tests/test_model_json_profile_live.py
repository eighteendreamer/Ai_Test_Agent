"""Reload actual MySQL declarations and inspect each native SDK's HTTP request."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_model_completion_http import provider
from src.application.models.model_runtime_service import ModelRuntimeService
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.models import ModelRegistry
from src.schemas.model_config import ModelCapabilitiesOverride, ModelInvocationRequest, model_invocation_parameters
from src.schemas.model_request_options import ModelOutputFormat, ModelRequestProfile
from src.schemas.settings import ModelConfigUpdateRequest


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_MODEL_REQUEST_PROFILE") != "1",
    reason="requires actual MySQL; native SDKs send HTTP only to a controlled local server")]


@pytest.mark.parametrize("transport", ["openai_chat_completions", "openai_responses",
    "anthropic_messages", "google_gemini_generate_content"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_mysql_json_declarations_drive_native_http_after_reload(tmp_path, transport, streaming):
    evidence = Path(os.getenv("QA_MODEL_JSON_PROFILE_DIR", str(tmp_path / "evidence"))) / (
        transport + ("-stream" if streaming else "-sync"))
    evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings().model_copy(deep=True)
    settings.model.langchain_model_adapter_enabled = False
    table = "up_json_profile_" + uuid4().hex[:12]
    settings.database.llm_model_table = table
    with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (table,))
        assert cursor.fetchone() is None
    expected = {"title": "订单不存在😀", "status": 404, "created": False, "data": None}
    schema = {"type": "object", "properties": {"title": {"type": "string"},
        "status": {"type": "integer"}, "created": {"type": "boolean"}, "data": {"type": "null"}},
        "required": list(expected), "additionalProperties": False}
    results = {}
    cleanup = {"owned_mysql_table": table, "table_removed": False, "created_containers": 0}
    try:
        with provider(transport, False, content_supplier=lambda: expected) as (url, sends, _):
            store = MySQLModelConfigStore(settings)
            store.initialize()
            payload = {"model_name": "owned-json-profile", "provider": "anthropic" if transport == "anthropic_messages"
                else "google" if transport == "google_gemini_generate_content" else "openai",
                "transport": transport, "base_url": url, "is_active": True, "max_output_tokens": 2048}
            profile = ModelRequestProfile(output_formats=["json_schema"])
            store.upsert(ModelConfigUpdateRequest(**payload, api_key="local-fixture-marker",
                use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(json_mode=False),
                request_profile=profile))
            # Rebuild both Store and runtime: the request must use persisted declarations.
            reloaded = MySQLModelConfigStore(settings)
            record = reloaded.get_by_name(payload["model_name"])
            assert record.request_profile == profile and record.capabilities.json_mode is False
            with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT request_profile FROM `{table}` WHERE model_name=%s", (payload["model_name"],))
                assert json.loads(cursor.fetchone()["request_profile"]) == profile.model_dump(mode="json")
            runtime = ModelRuntimeService(ModelRegistry(reloaded), settings)
            request = ModelInvocationRequest(system_prompt="Return the requested JSON object.",
                messages=[{"role": "user", "content": json.dumps(expected, ensure_ascii=False)}],
                output_format=ModelOutputFormat(kind="json_schema", schema_definition=schema))
            deltas = []
            async def on_delta(text):
                deltas.append(text)
            async def invoke():
                async with runtime.stream_handler(on_delta if streaming else None):
                    return await runtime.invoke(record.key, request)
            result = await invoke()
            results["declared_schema"] = result.model_dump(mode="json")
            assert result.response_summary["mode"] == "ok", result.response_summary
            assert json.loads(result.text) == expected and len(sends) == 1
            if streaming:
                assert json.loads("".join(deltas)) == expected
            body, path = sends[0]["body"], sends[0]["path"]
            if transport == "openai_chat_completions":
                assert path == "/v1/chat/completions"
                assert body["response_format"]["json_schema"]["schema"] == schema
                assert "text" not in body and "output_config" not in body
            elif transport == "openai_responses":
                assert path == "/v1/responses"
                assert body["text"]["format"]["schema"] == schema
                assert "response_format" not in body and "output_config" not in body
            elif transport == "anthropic_messages":
                assert path == "/v1/messages"
                assert body["output_config"]["format"] == {"type": "json_schema", "schema": schema}
                assert "response_format" not in body and "text" not in body
            else:
                assert ":streamGenerateContent" in path if streaming else ":generateContent" in path
                assert body["generationConfig"]["responseMimeType"] == "application/json"
                assert body["generationConfig"]["responseJsonSchema"] == schema
                assert "response_format" not in body and "output_config" not in body
            # A legacy bool cannot declare which protocol/schema fields a model accepts.
            store.upsert(ModelConfigUpdateRequest(**payload, use_provider_defaults=False,
                capability_overrides=ModelCapabilitiesOverride(json_mode=True), request_profile=None))
            cleared = MySQLModelConfigStore(settings).get_by_name(payload["model_name"])
            assert cleared.capabilities.json_mode is True and cleared.request_profile is None
            rejected = await invoke()
            results["required_undeclared"] = rejected.model_dump(mode="json")
            assert rejected.response_summary["mode"] == "unsupported_request"
            assert rejected.response_summary["reason"] == "format_not_declared" and len(sends) == 1
            request.output_format.required = False
            fallback = await invoke()
            results["optional_fallback"] = fallback.model_dump(mode="json")
            assert fallback.response_summary["mode"] == "ok" and len(sends) == 2
            assert json.loads(fallback.text) == expected
            assert {"option": "output_format", "action": "prompt_fallback", "reason": "format_not_declared"} in (
                fallback.response_summary["request_mapping"]["decisions"])
            assert not {"response_format", "output_config", "text"}.intersection(sends[-1]["body"])
            assert "responseMimeType" not in sends[-1]["body"].get("generationConfig", {})
            (evidence / "verification.json").write_text(json.dumps({"transport": transport,
                "streaming": streaming, "persisted_model": model_invocation_parameters(record),
                "requests": sends, "results": results, "controlled_model": True,
                "external_provider_verified": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()
            cursor.execute("SHOW TABLES LIKE %s", (table,))
            cleanup["table_removed"] = cursor.fetchone() is None
        (evidence / "cleanup.json").write_text(json.dumps(cleanup, indent=2), encoding="utf-8")
        assert cleanup["table_removed"]
