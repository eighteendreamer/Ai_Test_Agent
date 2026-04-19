from __future__ import annotations

from time import perf_counter

import httpx

from src.application.model_compatibility import ModelCompatibilityLayer
from src.core.config import Settings
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import ModelInvocationRequest
from src.schemas.email_config import EmailConfigUpdateRequest
from src.schemas.settings import (
    ModelConfigActionResponse,
    ModelConfigConnectionTestResponse,
    ModelConfigUpdateRequest,
)


class SettingsService:
    def __init__(
        self,
        settings: Settings,
        model_config_store: MySQLModelConfigStore,
        email_config_store: MySQLEmailConfigStore,
    ) -> None:
        self._settings = settings
        self._model_config_store = model_config_store
        self._email_config_store = email_config_store
        self._compatibility = ModelCompatibilityLayer()

    def list_model_configs(self):
        return [self._model_config_store.to_public(item) for item in self._model_config_store.list_all()]

    def update_model_config(self, payload: ModelConfigUpdateRequest):
        return self._model_config_store.upsert(payload)

    def edit_model_config(self, original_model_name: str, payload: ModelConfigUpdateRequest):
        return self._model_config_store.update_existing(original_model_name, payload)

    def activate_model_config(self, model_name: str):
        item = self._model_config_store.activate(model_name)
        return ModelConfigActionResponse(
            ok=True,
            message=f"Model '{item.name}' is now active.",
            item=item,
        )

    def delete_model_config(self, model_name: str):
        deleted, replacement = self._model_config_store.delete(model_name)
        message = f"Model '{deleted.name}' was deleted."
        if replacement is not None:
            message += f" '{replacement.name}' is now active."
        return ModelConfigActionResponse(
            ok=True,
            message=message,
            item=self._model_config_store.to_public(replacement) if replacement else None,
        )

    def test_model_config_connection(self, model_name: str):
        record = self._model_config_store.get_by_name(model_name)
        public_item = self._model_config_store.to_public(record)
        if not record.api_key:
            return ModelConfigConnectionTestResponse(
                ok=False,
                message=f"Model '{record.name}' has no API key configured.",
                item=public_item,
                provider=record.provider,
                api_base_url=record.api_base_url,
            )

        request = ModelInvocationRequest(
            system_prompt="You are a model connection health check. Reply with a short pong.",
            messages=[{"role": "user", "content": "ping"}],
        )
        url = self._compatibility.build_url(record)
        headers = self._compatibility.build_headers(record, record.api_key)
        payload = self._compatibility.build_request(record, request)

        started_at = perf_counter()
        try:
            with httpx.Client(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                parsed = self._compatibility.parse_response(record, response.json())
        except httpx.HTTPError as exc:
            return ModelConfigConnectionTestResponse(
                ok=False,
                message=f"Connection test failed: {exc}",
                item=public_item,
                provider=record.provider,
                api_base_url=record.api_base_url,
                latency_ms=int((perf_counter() - started_at) * 1000),
            )

        preview = (parsed.get("text") or "").strip()
        if preview:
            preview = preview[:120]

        return ModelConfigConnectionTestResponse(
            ok=True,
            message=f"Connection test succeeded for '{record.name}'.",
            item=public_item,
            provider=record.provider,
            api_base_url=record.api_base_url,
            latency_ms=int((perf_counter() - started_at) * 1000),
            preview=preview or None,
        )

    def list_email_configs(self):
        return [self._email_config_store.to_public(item) for item in self._email_config_store.list_all()]

    def update_email_config(self, payload: EmailConfigUpdateRequest):
        return self._email_config_store.to_public(self._email_config_store.update(payload))
