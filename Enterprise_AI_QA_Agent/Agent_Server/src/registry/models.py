from __future__ import annotations

from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.agent import ModelDescriptor
from src.schemas.model_config import ModelConfigPublic, ModelConfigRecord


class ModelRegistry:
    def __init__(self, store: MySQLModelConfigStore) -> None:
        self._store = store

    def list(self) -> list[ModelDescriptor]:
        return [self._to_descriptor(item) for item in self._store.list_active()]

    def list_configs(self) -> list[ModelConfigPublic]:
        return [self._store.to_public(item) for item in self._store.list_all()]

    def get(self, key: str) -> ModelDescriptor:
        return self._to_descriptor(self._store.get_active(key))

    def get_runtime_config(self, key: str) -> ModelConfigRecord:
        return self._store.get_active(key)

    def get_default_runtime_config(self) -> ModelConfigRecord:
        return self._store.get_default_active()

    def resolve_for_agent(
        self,
        requested_key: str | None,
        supported_model_keys: list[str],
    ) -> ModelDescriptor:
        active = {item.key: item for item in self._store.list_active()}

        if requested_key and requested_key in active and requested_key in supported_model_keys:
            return self._to_descriptor(active[requested_key])

        for key in supported_model_keys:
            if key in active:
                return self._to_descriptor(active[key])

        if requested_key and requested_key in active:
            return self._to_descriptor(active[requested_key])

        try:
            return self._to_descriptor(self._store.get_default_active())
        except KeyError:
            fallback_key = requested_key or (supported_model_keys[0] if supported_model_keys else "unconfigured-model")
            return ModelDescriptor(
                key=fallback_key,
                name=fallback_key,
                provider="unconfigured",
                summary="No active database-backed model configuration is available yet.",
                supports_tools=False,
                supports_vision=False,
                supports_reasoning=False,
                tags=["inactive", "requires-configuration"],
            )

    def _to_descriptor(self, record: ModelConfigRecord) -> ModelDescriptor:
        return ModelDescriptor(
            key=record.key,
            name=record.name,
            provider=record.provider,
            summary=record.description or f"Active model config for provider '{record.provider}'.",
            supports_tools=record.supports_tools,
            supports_vision=record.supports_vision,
            supports_reasoning=record.supports_reasoning,
            tags=[record.transport, "active" if record.is_active else "inactive"],
        )
