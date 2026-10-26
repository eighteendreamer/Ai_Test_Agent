"""The actual input to application intent enrichment, before any model send."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from src.schemas.intent import IntentDecision
from src.schemas.model_config import ModelInvocationRequest, ModelTransportOptions


class PreparedSemanticInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    baseline: IntentDecision
    model_key: str | None = None
    request: ModelInvocationRequest | None = None
    skip_reason: Literal["deterministic", "model_unavailable", "service_unavailable"] | None = None
    invocation_parameters: dict[str, Any] | None = None
    runtime_parameters: dict[str, Any] | None = None
    transport_options: ModelTransportOptions | None = None

    @model_validator(mode="after")
    def validate_preparation(self):
        if (self.request is None) != (self.skip_reason is not None):
            raise ValueError("Semantic preparation must record either a request or a skip")
        if self.request is not None and (not self.model_key or self.request.tools):
            raise ValueError("Semantic preparation requires a model and a tool-free request")
        if self.request is not None:
            messages = self.request.messages
            if len(messages) != 1 or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str):
                raise ValueError("Semantic preparation requires its single original user input")
            body = json.loads(messages[0]["content"])
            if (not isinstance(body, dict) or not isinstance(body.get("user_input"), str)
                or body.get("deterministic_baseline") != self.baseline.model_dump(mode="json")):
                raise ValueError("Semantic request differs from its frozen baseline")
        settings = (self.invocation_parameters, self.runtime_parameters, self.transport_options)
        if any(value is not None for value in settings) and (
            self.request is None or any(value is None for value in settings)
            or self.transport_options.max_retries != 0):
            raise ValueError("Frozen semantic settings require the full invocation contract without retries")
        if self.invocation_parameters is not None and self.invocation_parameters.get("key") != self.model_key:
            raise ValueError("Semantic invocation parameters differ from the chosen model")
        return self
