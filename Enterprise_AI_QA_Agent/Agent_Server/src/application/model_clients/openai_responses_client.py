from __future__ import annotations

from src.application.model_clients.request_mapping import map_model_request

import json
from typing import Any

import openai
from openai import AsyncOpenAI

from src.application.model_clients.base import (
    ProviderClientError,
    TextDeltaHandler,
    build_tool_name_map,
    parse_tool_arguments,
    remap_tool_calls,
    response_to_dict,
    serialize_tool_result_text,
)
from src.schemas.model_config import ContentPart, ModelConfigRecord, ModelInvocationRequest, UnifiedMessage
from src.schemas.tool_runtime import ModelToolCall


class OpenAIResponsesClient:
    """Responses API client (`/v1/responses`) backed by the official openai SDK.

    Request mapping (see openai/resources/responses/responses.py):
    - system prompt   -> `instructions`
    - history         -> `input` item list (message / function_call /
                         function_call_output items)
    - tools           -> flat function tools ({"type": "function", "name", ...})
    - token limit     -> `max_output_tokens`
    Responses are stateless for this runtime, so `store=False` is always sent.
    """

    client_key = "openai_responses"

    def __init__(self, *, timeout_seconds: float = 300.0, max_retries: int = 2) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> dict[str, Any]:
        tool_name_map = build_tool_name_map(request.tools)
        params = self._build_params(config, request, tool_name_map)
        try:
            async with self._make_client(config, api_key) as client:
                response = await client.responses.create(**params)
        except openai.APIStatusError as exc:
            raise self._wrap_status_error(exc) from exc
        except openai.APIError as exc:
            raise ProviderClientError(str(exc)) from exc
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
        tool_name_map = build_tool_name_map(request.tools)
        params = self._build_params(config, request, tool_name_map)
        params["stream"] = True

        final_response: Any = None
        text_parts: list[str] = []

        try:
            async with self._make_client(config, api_key) as client:
                stream = await client.responses.create(**params)
                async for event in stream:
                    event_type = str(getattr(event, "type", "") or "")
                    # Text deltas arrive via response.output_text.delta; the
                    # completed/failed/incomplete events carry the final
                    # Response object with the full output list and usage.
                    if event_type == "response.output_text.delta":
                        delta = str(getattr(event, "delta", "") or "")
                        if delta:
                            text_parts.append(delta)
                            await on_text_delta(delta)
                        continue
                    if event_type in {
                        "response.completed",
                        "response.incomplete",
                        "response.failed",
                    }:
                        final_response = getattr(event, "response", None)
        except openai.APIStatusError as exc:
            raise self._wrap_status_error(exc) from exc
        except openai.APIError as exc:
            raise ProviderClientError(str(exc)) from exc

        if final_response is not None:
            parsed = self._parse_response(final_response)
        else:
            parsed = {
                "text": "".join(text_parts).strip(),
                "tool_calls": [],
                "finish_reason": None,
                "stop_reason": None,
                "usage": {},
                "response_id": "",
                "raw_response": {},
            }
        parsed["tool_calls"] = remap_tool_calls(parsed["tool_calls"], tool_name_map)
        parsed["raw_response"] = {
            "mode": "stream",
            "provider": config.provider,
            "stream_terminal_received": final_response is not None,
            "finish_reason": parsed["finish_reason"],
            "usage": parsed["usage"],
        }
        parsed["stream_terminal_received"] = final_response is not None
        return parsed

    def _make_client(self, config: ModelConfigRecord, api_key: str) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key,
            base_url=config.api_base_url,
            default_headers=dict(config.extra_headers) or None,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )

    def _wrap_status_error(self, exc: openai.APIStatusError) -> ProviderClientError:
        body = ""
        try:
            body = exc.response.text or ""
        except Exception:
            body = ""
        return ProviderClientError(
            str(exc),
            status_code=exc.status_code,
            response_body=body,
        )

    def _build_params(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": config.model_id,
            "input": self._build_input_items(request, tool_name_map),
            "max_output_tokens": config.max_output_tokens,
            "store": False,
        }
        if request.system_prompt:
            params["instructions"] = request.system_prompt
        if config.temperature is not None:
            params["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": (tool_name_map or {}).get(item["name"], item["name"]),
                    "description": item["description"],
                    "parameters": item["input_schema"],
                    "strict": False,
                }
                for item in request.tools
            ]
            params["tool_choice"] = "auto"
            if config.capabilities.parallel_tool_calls:
                params["parallel_tool_calls"] = True
        params.update(map_model_request(config, request).parameters)
        return params

    def _parse_response(self, response: Any) -> dict[str, Any]:
        text_blocks: list[str] = []
        tool_calls: list[ModelToolCall] = []
        for index, item in enumerate(getattr(response, "output", None) or []):
            item_type = str(getattr(item, "type", "") or "")
            if item_type == "message":
                for content in getattr(item, "content", None) or []:
                    if str(getattr(content, "type", "") or "") == "output_text":
                        text_blocks.append(str(getattr(content, "text", "") or ""))
                continue
            if item_type == "function_call":
                tool_calls.append(
                    ModelToolCall(
                        id=str(getattr(item, "call_id", "") or f"call_{index}"),
                        name=str(getattr(item, "name", "") or ""),
                        arguments=parse_tool_arguments(getattr(item, "arguments", "")),
                    )
                )

        status = str(getattr(response, "status", "") or "") or None
        incomplete_details = getattr(response, "incomplete_details", None)
        stop_reason = str(getattr(incomplete_details, "reason", "") or "") or status

        usage_obj = getattr(response, "usage", None)
        usage: dict[str, Any] = {}
        if usage_obj is not None:
            for attr in ("input_tokens", "output_tokens", "total_tokens"):
                value = getattr(usage_obj, attr, None)
                if value is not None:
                    usage[attr] = int(value)

        return {
            "text": "\n".join(block.strip() for block in text_blocks if block.strip()).strip(),
            "tool_calls": tool_calls,
            "finish_reason": status,
            "stop_reason": stop_reason,
            "usage": usage,
            "response_id": getattr(response, "id", None),
            "raw_response": response_to_dict(response),
        }

    def _build_input_items(
        self,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in request.structured_messages:
            role = message.role
            if role == "system":
                continue
            if role == "tool":
                tool_part = next((part for part in message.parts if part.type == "tool_result"), None)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id or "",
                        "output": serialize_tool_result_text(tool_part),
                    }
                )
                continue

            content = self._serialize_parts(message)
            if content:
                items.append({"role": role, "content": content})
            if role == "assistant" and message.tool_calls:
                for tool_call in message.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.id,
                            "name": (tool_name_map or {}).get(tool_call.name, tool_call.name),
                            "arguments": json.dumps(tool_call.arguments or {}, ensure_ascii=False),
                        }
                    )
        return items

    def _serialize_parts(self, message: UnifiedMessage) -> str | list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in message.parts:
            block = self._serialize_part(part)
            if block is not None:
                blocks.append(block)
        if not blocks:
            # Assistant history rows that only carried tool calls have no
            # content; the caller emits the function_call items separately.
            return "" if message.role != "assistant" else []
        if len(blocks) == 1 and blocks[0].get("type") == "input_text":
            return str(blocks[0].get("text") or "")
        return blocks

    def _serialize_part(self, part: ContentPart) -> dict[str, Any] | None:
        if part.type == "text":
            return {"type": "input_text", "text": part.text or ""}
        if part.type == "image_url" and part.url:
            return {"type": "input_image", "image_url": part.url, "detail": "auto"}
        if part.type == "image_base64" and part.data_base64:
            mime_type = part.mime_type or "image/jpeg"
            return {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{part.data_base64}",
                "detail": "auto",
            }
        if part.type == "file":
            label = part.file_name or "file"
            return {"type": "input_text", "text": f"[file:{label}]"}
        if part.type == "tool_result":
            return {"type": "input_text", "text": serialize_tool_result_text(part)}
        return None
