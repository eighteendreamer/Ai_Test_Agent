from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.tool_runtime import ModelToolCall


OPENAI_COMPATIBLE_PROVIDER_ALIASES = {
    "openai": "openai",
    "azure-openai": "openai",
    "azure_openai": "openai",
    "deepseek": "deepseek",
    "qwen": "qwen",
    "dashscope": "qwen",
    "zhipu": "zhipu",
    "glm": "zhipu",
    "moonshot": "moonshot",
    "kimi": "moonshot",
    "groq": "groq",
    "siliconflow": "siliconflow",
    "ollama": "ollama",
    "vllm": "vllm",
    "openrouter": "openrouter",
    "mistral": "mistral",
    "together": "together",
    "xai": "xai",
    "yi": "yi",
    "baichuan": "baichuan",
    "minimax": "minimax",
    "volcengine": "volcengine",
    "doubao": "volcengine",
    "hunyuan": "hunyuan",
    "baidu": "baidu",
    "ernie": "baidu",
}


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    protocol: str
    chat_path: str
    auth_header: str = "authorization"
    auth_prefix: str = "Bearer "
    extra_headers: dict[str, str] = field(default_factory=dict)
    supports_parallel_tool_calls: bool = False


class ModelCompatibilityLayer:
    def resolve_profile(self, config: ModelConfigRecord) -> ProviderProfile:
        provider_key = self._canonical_provider(config.provider)
        if provider_key == "anthropic" or config.transport == "anthropic_messages":
            return ProviderProfile(
                name="anthropic",
                protocol="anthropic_messages",
                chat_path="/v1/messages",
                auth_header="x-api-key",
                auth_prefix="",
                extra_headers={"anthropic-version": "2023-06-01"},
            )

        return ProviderProfile(
            name=provider_key,
            protocol="openai_chat_completions",
            chat_path="/chat/completions",
        )

    def build_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        profile = self.resolve_profile(config)
        if profile.protocol == "anthropic_messages":
            return {
                "model": config.model_id,
                "max_tokens": config.max_tokens,
                "system": request.system_prompt,
                "messages": self._build_anthropic_messages(request.messages),
                **({"temperature": config.temperature} if config.temperature is not None else {}),
                **(
                    {
                        "tools": [
                            {
                                "name": item["name"],
                                "description": item["description"],
                                "input_schema": item["input_schema"],
                            }
                            for item in request.tools
                        ]
                    }
                    if config.supports_tools and request.tools
                    else {}
                ),
            }

        payload: dict[str, Any] = {
            "model": config.model_id,
            "messages": self._build_openai_messages(
                request.system_prompt,
                request.messages,
                tool_name_map=tool_name_map,
            ),
            "max_tokens": config.max_tokens,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.supports_tools and request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": (tool_name_map or {}).get(item["name"], item["name"]),
                        "description": item["description"],
                        "parameters": item["input_schema"],
                    },
                }
                for item in request.tools
            ]
            payload["tool_choice"] = "auto"
            if profile.supports_parallel_tool_calls:
                payload["parallel_tool_calls"] = False
        return payload

    def build_headers(self, config: ModelConfigRecord, api_key: str) -> dict[str, str]:
        profile = self.resolve_profile(config)
        authorization_value = (
            f"{profile.auth_prefix}{api_key}" if profile.auth_prefix else api_key
        )
        return {
            profile.auth_header: authorization_value,
            "content-type": "application/json",
            **profile.extra_headers,
            **config.extra_headers,
        }

    def build_url(self, config: ModelConfigRecord) -> str:
        profile = self.resolve_profile(config)
        base_url = config.api_base_url.rstrip("/")
        if base_url.endswith(profile.chat_path):
            return base_url
        return f"{base_url}{profile.chat_path}"

    def parse_response(
        self,
        config: ModelConfigRecord,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        profile = self.resolve_profile(config)
        if profile.protocol == "anthropic_messages":
            return self._parse_anthropic_response(data)
        return self._parse_openai_compatible_response(data)

    def build_tool_name_map(self, tools: list[dict[str, Any]]) -> dict[str, str]:
        name_map: dict[str, str] = {}
        used: set[str] = set()
        for item in tools:
            original = str(item.get("name") or "").strip()
            if not original:
                continue
            candidate = self._sanitize_tool_name(original)
            suffix = 2
            while candidate in used and name_map.get(original) != candidate:
                candidate = self._sanitize_tool_name(f"{original}_{suffix}")
                suffix += 1
            used.add(candidate)
            name_map[original] = candidate
        return name_map

    def remap_tool_calls(
        self,
        tool_calls: list[ModelToolCall],
        tool_name_map: dict[str, str] | None,
    ) -> list[ModelToolCall]:
        if not tool_name_map:
            return tool_calls
        reverse_map = {sanitized: original for original, sanitized in tool_name_map.items()}
        remapped: list[ModelToolCall] = []
        for item in tool_calls:
            remapped.append(
                ModelToolCall(
                    id=item.id,
                    name=reverse_map.get(item.name, item.name),
                    arguments=item.arguments,
                )
            )
        return remapped

    def _parse_openai_compatible_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", []) or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        text = self._extract_openai_message_text(message)
        tool_calls = self._extract_openai_tool_calls(message)
        return {
            "text": text,
            "tool_calls": tool_calls,
            "finish_reason": choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None,
            "stop_reason": data.get("stop_reason"),
            "usage": data.get("usage", {}),
            "response_id": data.get("id"),
            "raw_response": data,
        }

    def _parse_anthropic_response(self, data: dict[str, Any]) -> dict[str, Any]:
        blocks = data.get("content", []) or []
        text = "\n".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        tool_calls = [
            ModelToolCall(
                id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                arguments=block.get("input", {}) if isinstance(block.get("input"), dict) else {},
            )
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        return {
            "text": text,
            "tool_calls": tool_calls,
            "finish_reason": None,
            "stop_reason": data.get("stop_reason"),
            "usage": data.get("usage", {}),
            "response_id": data.get("id"),
            "raw_response": data,
        }

    def _extract_openai_message_text(self, message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_blocks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_blocks.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("type") in {"text", "output_text"}:
                    text_blocks.append(str(item.get("text") or item.get("content") or ""))
                    continue
                if isinstance(item.get("text"), dict):
                    text_blocks.append(str(item["text"].get("value", "")))
            return "\n".join(block.strip() for block in text_blocks if block.strip()).strip()
        return str(content or "").strip()

    def _extract_openai_tool_calls(self, message: Any) -> list[ModelToolCall]:
        if not isinstance(message, dict):
            return []
        raw_tool_calls = message.get("tool_calls", [])
        if not isinstance(raw_tool_calls, list):
            return []

        tool_calls: list[ModelToolCall] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            function_block = item.get("function", {}) if isinstance(item.get("function"), dict) else {}
            tool_calls.append(
                ModelToolCall(
                    id=str(item.get("id", "")),
                    name=str(function_block.get("name") or item.get("name") or ""),
                    arguments=self._parse_tool_arguments(function_block.get("arguments", item.get("arguments"))),
                )
            )
        return tool_calls

    def _parse_tool_arguments(self, raw_arguments: Any) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str) and raw_arguments.strip():
            try:
                loaded = json.loads(raw_arguments)
                return loaded if isinstance(loaded, dict) else {"value": loaded}
            except json.JSONDecodeError:
                return {"raw_arguments": raw_arguments}
        return {}

    def _build_openai_messages(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")

            if role == "user":
                converted.append({"role": "user", "content": str(content)})
                continue

            if role == "assistant":
                tool_calls = self._normalize_openai_tool_calls(
                    message.get("tool_calls", []),
                    tool_name_map=tool_name_map,
                )
                assistant_message: dict[str, Any] = {"role": "assistant"}
                if tool_calls:
                    assistant_message["content"] = str(content) if str(content).strip() else None
                    assistant_message["tool_calls"] = tool_calls
                else:
                    assistant_message["content"] = str(content)
                converted.append(assistant_message)
                continue

            if role == "tool":
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(message.get("tool_call_id", "")),
                        "content": str(content),
                    }
                )
                continue

            converted.append({"role": role, "content": str(content)})
        return converted

    def _normalize_openai_tool_calls(
        self,
        tool_calls: Any,
        tool_name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(tool_calls, list):
            return normalized

        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue

            if tool_call.get("type") == "function" and isinstance(tool_call.get("function"), dict):
                function_block = tool_call["function"]
                normalized.append(
                    {
                        "id": str(tool_call.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": (tool_name_map or {}).get(
                                str(function_block.get("name", "")),
                                str(function_block.get("name", "")),
                            ),
                            "arguments": self._stringify_tool_arguments(function_block.get("arguments")),
                        },
                    }
                )
                continue

            normalized.append(
                {
                    "id": str(tool_call.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": (tool_name_map or {}).get(
                            str(tool_call.get("name", "")),
                            str(tool_call.get("name", "")),
                        ),
                        "arguments": self._stringify_tool_arguments(tool_call.get("arguments", {})),
                    },
                }
            )

        return normalized

    def _stringify_tool_arguments(self, arguments: Any) -> str:
        if isinstance(arguments, str):
            return arguments
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False)
        return json.dumps({}, ensure_ascii=False)

    def _build_anthropic_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "user":
                converted.append({"role": "user", "content": str(content)})
                continue

            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if str(content).strip():
                    blocks.append({"type": "text", "text": str(content)})
                for tool_call in message.get("tool_calls", []) or []:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.get("id", ""),
                            "name": tool_call.get("name", ""),
                            "input": tool_call.get("arguments", {}),
                        }
                    )
                converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
                continue

            if role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("tool_call_id", ""),
                                "content": str(content),
                            }
                        ],
                    }
                )

        return converted

    def _canonical_provider(self, provider: str) -> str:
        lowered = (provider or "").strip().lower()
        if lowered == "anthropic":
            return "anthropic"
        return OPENAI_COMPATIBLE_PROVIDER_ALIASES.get(lowered, "openai")

    def _sanitize_tool_name(self, name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip())
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        if not sanitized:
            sanitized = "tool"
        if sanitized[0].isdigit():
            sanitized = f"tool_{sanitized}"
        return sanitized[:64]
