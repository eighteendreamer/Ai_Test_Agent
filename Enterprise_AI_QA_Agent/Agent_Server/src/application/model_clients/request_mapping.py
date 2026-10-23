"""Map explicit internal options to existing provider protocols, before any HTTP send."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


class UnsupportedModelRequest(ValueError):
    def __init__(self, option: str, reason: str):
        self.option, self.reason = option, reason
        super().__init__(f"Unsupported model request option={option}: {reason}")


@dataclass
class MappedModelRequest:
    parameters: dict[str, Any]
    decisions: list[dict[str, str]]


def map_model_request(config: ModelConfigRecord, request: ModelInvocationRequest) -> MappedModelRequest:
    profile, transport = config.request_profile, config.transport
    parameters: dict[str, Any] = {}
    decisions: list[dict[str, str]] = []

    image_count = 0
    for message in request.structured_messages:
        for part in message.parts:
            if part.type == "image_base64":
                image_count += 1
                if not config.capabilities.vision:
                    raise UnsupportedModelRequest("vision", "vision_not_enabled")
                if not config.capabilities.image_base64_input:
                    raise UnsupportedModelRequest("image_base64_input", "base64_image_not_enabled")
            elif part.type == "image_url" and part.url:
                image_count += 1
                is_data_url = part.url.startswith("data:") and ";base64," in part.url
                if not config.capabilities.vision:
                    raise UnsupportedModelRequest("vision", "vision_not_enabled")
                if is_data_url and not config.capabilities.image_base64_input:
                    raise UnsupportedModelRequest("image_base64_input", "base64_image_not_enabled")
                if not is_data_url and not config.capabilities.image_url_input:
                    raise UnsupportedModelRequest("image_url_input", "image_url_not_enabled")
            elif part.type == "file":
                mime_type = str(part.mime_type or "").lower()
                if mime_type == "application/pdf" and not config.capabilities.pdf_input:
                    raise UnsupportedModelRequest("pdf_input", "pdf_input_not_enabled")
                if not config.capabilities.file_input:
                    raise UnsupportedModelRequest("file_input", "file_input_not_enabled")
                raise UnsupportedModelRequest("file_input", "file_transport_not_mapped")
    if image_count > 1 and not config.capabilities.multi_image:
        raise UnsupportedModelRequest("multi_image", "multi_image_not_enabled")

    def unsupported(option, required, reason):
        if required:
            raise UnsupportedModelRequest(option, reason)
        decisions.append({"option": option, "action": "prompt_fallback", "reason": reason})

    output = request.output_format
    if output is not None:
        kind = output.kind
        if profile is None or kind not in profile.output_formats:
            # Optional Schema requests may use explicitly declared JSON mode;
            # the business validator still enforces the original Schema.
            if (not output.required and kind == "json_schema" and profile is not None
                and "json_object" in profile.output_formats):
                kind = "json_object"
                decisions.append({"option": "output_format", "action": "json_object_fallback", "reason": "schema_not_declared"})
            else:
                unsupported("output_format", output.required, "format_not_declared")
                kind = None
        if kind is not None and output.strict is not None and transport not in {"openai_chat_completions", "openai_responses"}:
            unsupported("output_format", output.required, "strict_flag_not_mapped")
            kind = None
        if kind is not None:
            schema = {"name": output.schema_name, "schema": output.schema_definition}
            if output.strict is not None:
                schema["strict"] = output.strict
            if transport == "openai_chat_completions":
                parameters["response_format"] = ({"type": kind, "json_schema": schema}
                    if kind == "json_schema" else {"type": kind})
            elif transport == "openai_responses":
                parameters["text"] = {"format": {"type": kind, **(schema if kind == "json_schema" else {})}}
            elif transport == "anthropic_messages":
                parameters["output_config"] = {"format": {"type": "json_schema", "schema": output.schema_definition}}
            else:
                parameters["response_mime_type"] = "application/json"
                if kind == "json_schema":
                    parameters["response_json_schema"] = output.schema_definition
            decisions.append({"option": "output_format", "action": "mapped", "format": kind})

    reasoning = request.reasoning or (profile.default_reasoning if profile else None)
    if reasoning is not None:
        if reasoning.effort is not None:
            if profile is None or profile.reasoning_style != "effort" or reasoning.effort not in profile.reasoning_efforts:
                unsupported("reasoning", reasoning.required, "effort_not_declared")
            else:
                if transport == "openai_chat_completions":
                    parameters["reasoning_effort"] = reasoning.effort
                else:
                    parameters["reasoning"] = {"effort": reasoning.effort}
                decisions.append({"option": "reasoning", "action": "mapped", "style": "effort"})
        else:
            budget = reasoning.budget_tokens
            invalid = (profile is None or profile.reasoning_style != "budget" or
                budget < profile.min_reasoning_budget or
                (profile.max_reasoning_budget is not None and budget > profile.max_reasoning_budget))
            if invalid:
                unsupported("reasoning", reasoning.required, "budget_not_declared")
            elif transport == "anthropic_messages" and budget != 0 and (budget < 1024 or budget >= config.max_output_tokens):
                unsupported("reasoning", reasoning.required, "anthropic_budget_out_of_range")
            elif transport == "anthropic_messages" and budget > 0 and config.temperature not in {None, 1.0}:
                unsupported("reasoning", reasoning.required, "anthropic_thinking_temperature_conflict")
            else:
                if transport == "anthropic_messages":
                    parameters["thinking"] = {"type": "enabled", "budget_tokens": budget} if budget else {"type": "disabled"}
                else:
                    parameters["thinking_config"] = {"thinking_budget": budget}
                decisions.append({"option": "reasoning", "action": "mapped", "style": "budget"})
    if profile is not None and profile.chat_token_parameter is not None:
        parameters[profile.chat_token_parameter] = config.max_output_tokens
        decisions.append({"option": "token_parameter", "action": "mapped", "field": profile.chat_token_parameter})
    return MappedModelRequest(parameters, decisions)
