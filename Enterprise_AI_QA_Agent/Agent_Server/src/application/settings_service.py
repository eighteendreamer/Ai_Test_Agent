from __future__ import annotations

from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.email_config import EmailConfigUpdateRequest
from src.schemas.settings import ModelConfigUpdateRequest


class SettingsService:
    def __init__(
        self,
        model_config_store: MySQLModelConfigStore,
        email_config_store: MySQLEmailConfigStore,
    ) -> None:
        self._model_config_store = model_config_store
        self._email_config_store = email_config_store

    def list_model_configs(self):
        return [self._model_config_store.to_public(item) for item in self._model_config_store.list_all()]

    def update_model_config(self, payload: ModelConfigUpdateRequest):
        return self._model_config_store.upsert(payload)

    def list_email_configs(self):
        return [self._email_config_store.to_public(item) for item in self._email_config_store.list_all()]

    def update_email_config(self, payload: EmailConfigUpdateRequest):
        return self._email_config_store.to_public(self._email_config_store.update(payload))
