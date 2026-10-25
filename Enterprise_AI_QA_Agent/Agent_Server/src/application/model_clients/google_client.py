from __future__ import annotations

from src.application.model_clients.request_mapping import map_model_request

import json
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from src.application.model_clients.base import (
    ProviderClientError,
    TextDeltaHandler,
    build_tool_name_map,
    remap_tool_calls,
    await_provider_io,
    provider_stream_events,
    response_to_dict,
)
from src.application.model_clients.http_observation import create_observed_http_client
from src.core.request_context import new_id
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall

_SCHEMA_ALLOWED_KEYS = {
    "additionalProperties",
    "anyOf",
    "default",
    "defs",
    "description",
    "enum",
    "example",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "nullable",
    "pattern",
    "properties",
    "propertyOrdering",
    "ref",
    "required",
    "type",
    "title",
}
_SCHEMA_TYPES = {
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
}
_SCHEMA_IGNORED_KEYS = {
    "$comment",
    "$id",
    "$schema",
    "deprecated",
    "examples",
    "externalDocs",
    "readOnly",
    "writeOnly",
    "xml",
}


class GoogleGenAIClient:
    """generateContent client backed by the official google-genai SDK."""

    client_key = "google_gemini_generate_content"

    def __init__(self, *, timeout_seconds: float = 300.0, max_retries: int | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]:
        tool_name_map = build_tool_name_map(request.tools, allow_dash=False)
        contents = self._build_contents(request, tool_name_map)
        generate_config = self._build_generate_config(config, request, tool_name_map)
        http_client = create_observed_http_client()
        try:
            client = (self._make_client(config, api_key) if http_client is None else
                self._make_client(config, api_key, http_client=http_client))
            response = await await_provider_io(client.aio.models.generate_content(
                model=config.model_id,
                contents=contents,
                config=generate_config,
            ), transport=self.client_key)
        except genai_errors.APIError as exc:
            raise self._wrap_api_error(exc) from exc
        finally:
            if http_client is not None:
                await http_client.aclose()
        parsed = self._parse_response(response)
        parsed["tool_calls"] = remap_tool_calls(parsed["tool_calls"], tool_name_map)
        return parsed

    async def stream(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
        on_text_delta: TextDeltaHandler,
    ) -> dict[str, Any]:
        tool_name_map = build_tool_name_map(request.tools, allow_dash=False)
        contents = self._build_contents(request, tool_name_map)
        generate_config = self._build_generate_config(config, request, tool_name_map)
        http_client = create_observed_http_client()

        response_id = ""
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        text_parts: list[str] = []
        tool_calls: list[ModelToolCall] = []

        try:
            client = (self._make_client(config, api_key) if http_client is None else
                self._make_client(config, api_key, http_client=http_client))
            stream = await await_provider_io(client.aio.models.generate_content_stream(
                model=config.model_id,
                contents=contents,
                config=generate_config,
            ), transport=self.client_key)
            async for chunk in provider_stream_events(stream, transport=self.client_key):
                chunk_usage = self._extract_usage(chunk)
                if chunk_usage:
                    usage = chunk_usage
                chunk_response_id = getattr(chunk, "response_id", None)
                if chunk_response_id:
                    response_id = str(chunk_response_id)
                candidates = getattr(chunk, "candidates", None) or []
                if not candidates:
                    continue
                first = candidates[0]
                finish_reason = self._enum_to_str(getattr(first, "finish_reason", None)) or finish_reason
                content = getattr(first, "content", None)
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    text = getattr(part, "text", None)
                    if text:
                        text_parts.append(str(text))
                        await on_text_delta(str(text))
                    function_call = getattr(part, "function_call", None)
                    if function_call is not None and getattr(function_call, "name", None):
                        tool_calls.append(
                            ModelToolCall(
                                id=str(getattr(function_call, "id", None) or new_id("gemini_call")),
                                name=str(function_call.name),
                                arguments=dict(getattr(function_call, "args", None) or {}),
                            )
                        )
        except genai_errors.APIError as exc:
            raise self._wrap_api_error(exc) from exc
        finally:
            if http_client is not None:
                await http_client.aclose()

        return {
            "text": "".join(text_parts).strip(),
            "tool_calls": remap_tool_calls(tool_calls, tool_name_map),
            "stream_terminal_received": bool(finish_reason) and finish_reason != "FINISH_REASON_UNSPECIFIED",
            "finish_reason": finish_reason,
            "stop_reason": finish_reason,
            "usage": usage,
            "response_id": response_id,
            "raw_response": {
                "mode": "stream",
                "provider": config.provider,
                "stream_terminal_received": bool(finish_reason) and finish_reason != "FINISH_REASON_UNSPECIFIED",
                "finish_reason": finish_reason,
                "usage": usage,
            },
        }

    def _make_client(self, config: ModelConfigRecord, api_key: str, *, http_client=None) -> genai.Client:
        headers: dict[str, str] = {**config.extra_headers}
        if config.auth_type == "oauth2":
            # The SDK still requires api_key for construction; the Bearer token
            # header takes precedence on Google's side for OAuth-based access.
            headers["Authorization"] = f"Bearer {api_key}"
        options = genai_types.HttpOptions(
            base_url=self._normalize_base_url(config.api_base_url),
            headers=headers or None,
            timeout=int(self._timeout_seconds * 1000),
            httpx_async_client=http_client,
            # Official HttpRetryOptions counts the original request. Preserve
            # the historical SDK default when the caller supplies no policy.
            retry_options=(genai_types.HttpRetryOptions(attempts=self._max_retries + 1)
                if self._max_retries is not None else None),
        )
        return genai.Client(
            api_key=api_key,
            http_options=options,
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        # The SDK appends its own /v1beta version path.
        cleaned = str(base_url or "").rstrip("/")
        for suffix in ("/v1beta", "/v1"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break
        return cleaned

    def _wrap_api_error(self, exc: genai_errors.APIError) -> ProviderClientError:
        body = ""
        details = getattr(exc, "details", None)
        if details:
            try:
                body = json.dumps(details, ensure_ascii=False)
            except Exception:
                body = str(details)
        return ProviderClientError(
            str(exc),
            status_code=getattr(exc, "code", None),
            response_body=body,
        )

    def _build_generate_config(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None,
    ) -> dict[str, Any]:
        generate_config: dict[str, Any] = {
            "max_output_tokens": config.max_output_tokens,
        }
        if request.system_prompt:
            generate_config["system_instruction"] = request.system_prompt
        if config.temperature is not None:
            generate_config["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            generate_config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": (tool_name_map or {}).get(item["name"], item["name"]),
                            "description": item["description"],
                            "parameters": self._sanitize_function_parameters(item["input_schema"]),
                        }
                        for item in request.tools
                    ]
                }
            ]
        generate_config.update(map_model_request(config, request).parameters)
        return generate_config

    def _parse_response(self, response: Any) -> dict[str, Any]:
        candidates = getattr(response, "candidates", None) or []
        first = candidates[0] if candidates else None
        content = getattr(first, "content", None)
        parts = getattr(content, "parts", None) or []

        text_blocks: list[str] = []
        tool_calls: list[ModelToolCall] = []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                text_blocks.append(str(text))
            function_call = getattr(part, "function_call", None)
            if function_call is not None and getattr(function_call, "name", None):
                tool_calls.append(
                    ModelToolCall(
                        id=str(getattr(function_call, "id", None) or new_id("gemini_call")),
                        name=str(function_call.name or ""),
                        arguments=dict(getattr(function_call, "args", None) or {}),
                    )
                )

        finish_reason = self._enum_to_str(getattr(first, "finish_reason", None))
        return {
            "text": "\n".join(block.strip() for block in text_blocks if block.strip()).strip(),
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "stop_reason": finish_reason,
            "usage": self._extract_usage(response),
            "response_id": getattr(response, "response_id", None),
            "raw_response": response_to_dict(response),
        }

    def _extract_usage(self, response: Any) -> dict[str, Any]:
        """Keep the legacy camelCase usage keys so token accounting stays intact."""
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata is None:
            return {}
        usage: dict[str, Any] = {}
        for attr, key in (
            ("prompt_token_count", "promptTokenCount"),
            ("candidates_token_count", "candidatesTokenCount"),
            ("total_token_count", "totalTokenCount"),
            ("cached_content_token_count", "cachedContentTokenCount"),
            ("thoughts_token_count", "thoughtsTokenCount"),
        ):
            value = getattr(usage_metadata, attr, None)
            if value is not None:
                usage[key] = int(value)
        return usage

    @staticmethod
    def _enum_to_str(value: Any) -> str | None:
        if value is None:
            return None
        return str(getattr(value, "value", None) or getattr(value, "name", None) or value)

    def _build_contents(
        self,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for message in request.structured_messages:
            if message.role == "system":
                continue
            role = "model" if message.role == "assistant" else "user"
            parts = self._serialize_parts(message, tool_name_map)
            if message.role == "assistant" and message.tool_calls:
                for tool_call in message.tool_calls:
                    parts.append(
                        {
                            "function_call": {
                                "id": tool_call.id,
                                "name": (tool_name_map or {}).get(tool_call.name, tool_call.name),
                                "args": tool_call.arguments or {},
                            }
                        }
                    )
            contents.append({"role": role, "parts": parts})
        return contents

    def _serialize_parts(
        self,
        message: UnifiedMessage,
        tool_name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for part in message.parts:
            serialized = self._serialize_part(
                part,
                tool_name_map,
                tool_call_id=message.tool_call_id,
            )
            if serialized is not None:
                parts.append(serialized)
        return parts or [{"text": ""}]

    def _serialize_part(
        self,
        part: ContentPart,
        tool_name_map: dict[str, str] | None = None,
        *,
        tool_call_id: str | None = None,
    ) -> dict[str, Any] | None:
        if part.type == "text":
            return {"text": part.text or ""}
        if part.type == "image_base64" and part.data_base64:
            return {
                "inline_data": {
                    "mime_type": part.mime_type or "image/jpeg",
                    "data": part.data_base64,
                }
            }
        if part.type == "image_url" and part.url:
            if part.url.startswith("data:") and ";base64," in part.url:
                prefix, data_base64 = part.url.split(";base64,", 1)
                mime_type = prefix.split("data:", 1)[-1] or "image/jpeg"
                return {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": data_base64,
                    }
                }
            return {"text": f"Image URL: {part.url}"}
        if part.type == "tool_result":
            payload = part.payload if isinstance(part.payload, dict) else {}
            tool_name = part.tool_name or "tool_result"
            function_response = {
                "name": (tool_name_map or {}).get(tool_name, tool_name),
                "response": payload or {"content": part.text or ""},
            }
            if tool_call_id:
                function_response["id"] = tool_call_id
            return {
                "function_response": function_response,
            }
        if part.type == "file":
            label = part.file_name or "file"
            return {"text": f"[file:{label}]"}
        return None

    def _sanitize_function_parameters(
        self,
        schema: Any,
        *,
        is_root: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(schema, dict):
            if is_root:
                return {"type": "object", "properties": {}}
            raise ProviderClientError(
                "Gemini nested function parameter schemas must be objects."
            )

        working = dict(schema)
        exclusive_minimum = working.pop("exclusiveMinimum", None)
        if exclusive_minimum is not None:
            if isinstance(exclusive_minimum, bool) or not isinstance(
                exclusive_minimum, (int, float)
            ):
                raise ProviderClientError(
                    "Gemini function parameter schema 'exclusiveMinimum' must be numeric."
                )
            minimum = working.get("minimum")
            if minimum is None or minimum < exclusive_minimum:
                working["minimum"] = exclusive_minimum
            strict_note = f"Must be greater than {exclusive_minimum}."
            description = str(working.get("description") or "").strip()
            working["description"] = f"{description} {strict_note}".strip()
        if "allOf" in working:
            raise ProviderClientError(
                "Gemini function parameter schema uses unsupported 'allOf'; "
                "the constraint cannot be represented without changing its meaning."
            )
        if "oneOf" in working:
            if "anyOf" in working or not self._one_of_branches_are_disjoint(
                working["oneOf"]
            ):
                raise ProviderClientError(
                    "Gemini function parameter schema uses 'oneOf' branches that "
                    "cannot be represented losslessly."
                )
            working["anyOf"] = working.pop("oneOf")

        raw_type = working.get("type")
        if isinstance(raw_type, list):
            if not raw_type or any(
                not isinstance(item, str) or item not in _SCHEMA_TYPES
                for item in raw_type
            ):
                raise ProviderClientError(
                    "Gemini function parameter schema 'type' must contain supported type names."
                )
            if len(set(raw_type)) != len(raw_type):
                raise ProviderClientError(
                    "Gemini function parameter schema 'type' must not contain duplicate types."
                )
            if len(raw_type) == 1:
                working["type"] = raw_type[0]
            else:
                if "anyOf" in working:
                    raise ProviderClientError(
                        "Gemini function parameter schema combines a multi-type "
                        "'type' list with 'anyOf', which cannot be represented losslessly."
                    )
                working.pop("type", None)
                working["anyOf"] = [{"type": item} for item in raw_type]

        sanitized: dict[str, Any] = {}
        raw_required = working.get("required")
        for key, value in working.items():
            if key in _SCHEMA_IGNORED_KEYS or key.startswith("x-"):
                continue
            if key in {"$defs", "definitions"}:
                key = "defs"
            elif key == "$ref":
                key = "ref"
            elif key not in _SCHEMA_ALLOWED_KEYS:
                raise ProviderClientError(
                    f"Gemini function parameter schema contains unsupported keyword '{key}'."
                )

            if key == "properties":
                if not isinstance(value, dict):
                    raise ProviderClientError(
                        "Gemini function parameter schema 'properties' must be an object."
                    )
                sanitized["properties"] = {
                    str(name): self._sanitize_function_parameters(
                        child_schema,
                        is_root=False,
                    )
                    for name, child_schema in value.items()
                    if isinstance(name, str)
                }
                continue
            if key == "items":
                sanitized["items"] = self._sanitize_function_parameters(
                    value,
                    is_root=False,
                )
                continue
            if key == "required":
                if not isinstance(value, list):
                    raise ProviderClientError(
                        "Gemini function parameter schema 'required' must be an array."
                    )
                if any(not isinstance(item, str) for item in value):
                    raise ProviderClientError(
                        "Gemini function parameter schema 'required' entries must be strings."
                    )
                continue
            if key == "anyOf":
                sanitized["anyOf"] = self._sanitize_union_branches(value)
                continue
            if key == "defs":
                if not is_root:
                    raise ProviderClientError(
                        "Gemini function parameter schema permits 'defs' only at the root."
                    )
                if not isinstance(value, dict):
                    raise ProviderClientError(
                        "Gemini function parameter schema 'defs' must be an object."
                    )
                converted_defs = {
                    str(name): self._sanitize_function_parameters(
                        definition,
                        is_root=False,
                    )
                    for name, definition in value.items()
                    if isinstance(name, str)
                }
                existing_defs = sanitized.get("defs", {})
                if set(existing_defs).intersection(converted_defs):
                    raise ProviderClientError(
                        "Gemini function parameter schema definition names collide "
                        "after normalizing '$defs' and 'definitions'."
                    )
                sanitized["defs"] = {**existing_defs, **converted_defs}
                continue
            if key == "ref":
                if not isinstance(value, str):
                    raise ProviderClientError(
                        "Gemini function parameter schema 'ref' must be a string."
                    )
                sanitized["ref"] = self._normalize_schema_ref(value)
                continue
            if key == "additionalProperties" and isinstance(value, dict):
                sanitized[key] = self._sanitize_function_parameters(
                    value,
                    is_root=False,
                )
                continue
            if key == "additionalProperties":
                if not isinstance(value, bool):
                    raise ProviderClientError(
                        "Gemini function parameter schema 'additionalProperties' "
                        "must be a boolean or schema object."
                    )
                sanitized[key] = value
                continue
            if key == "type":
                if not isinstance(value, str) or value not in _SCHEMA_TYPES:
                    raise ProviderClientError(
                        "Gemini function parameter schema 'type' must be a supported type name."
                    )
                sanitized["type"] = value
                continue
            if key in _SCHEMA_ALLOWED_KEYS:
                sanitized[key] = value

        if isinstance(raw_required, list):
            sanitized["required"] = list(raw_required)

        if sanitized.get("type") == "object":
            sanitized.setdefault("properties", {})
        if sanitized or not is_root:
            return sanitized
        return {"type": "object", "properties": {}}

    def _sanitize_union_branches(
        self,
        value: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ProviderClientError(
                "Gemini function parameter schema union must contain at least one branch."
            )

        branches: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ProviderClientError(
                    "Gemini function parameter schema union branches must be objects."
                )
            branches.append(
                self._sanitize_function_parameters(item, is_root=False)
            )
        return branches

    @staticmethod
    def _one_of_branches_are_disjoint(value: Any) -> bool:
        if not isinstance(value, list) or len(value) < 2:
            return False
        used_types: set[str] = set()
        for branch in value:
            if not isinstance(branch, dict):
                return False
            branch_type = branch.get("type")
            if isinstance(branch_type, str):
                branch_types = {branch_type}
            elif isinstance(branch_type, list) and all(
                isinstance(item, str) for item in branch_type
            ):
                branch_types = set(branch_type)
            else:
                return False
            if "number" in branch_types:
                branch_types.add("integer")
            if used_types.intersection(branch_types):
                return False
            used_types.update(branch_types)
        return True

    @staticmethod
    def _normalize_schema_ref(value: str) -> str:
        return (
            value.replace("#/$defs/", "#/defs/")
            .replace("#/definitions/", "#/defs/")
        )
