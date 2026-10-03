from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from src.infrastructure.application_config_store import ApplicationConfigStore
from src.schemas.channel_config import ChannelAdvancedSettings
from src.schemas.settings import GeneralSettingsPayload

logger = logging.getLogger(__name__)
GENERAL_SETTINGS_KEY = "workspace.general"
CHANNEL_ADVANCED_KEY = "channel.advanced"


class WorkspaceSettingsService:
    """One source for API preferences, Prompt language and channel policy."""

    def __init__(self, store: ApplicationConfigStore, legacy_dir: Path | None = None) -> None:
        self._store = store
        self._legacy_dir = legacy_dir if legacy_dir is not None else Path(__file__).resolve().parents[2] / "data"

    def initialize(self) -> None:
        self._store.initialize()
        for key, filename, schema in (
            (GENERAL_SETTINGS_KEY, "general_settings.json", GeneralSettingsPayload),
            (CHANNEL_ADVANCED_KEY, "channel_advanced_settings.json", ChannelAdvancedSettings),
        ):
            if self._store.get(key) is not None:
                continue
            source = self._legacy_dir / filename
            try:
                raw = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
                payload = schema.model_validate(raw).model_dump(mode="json")
            except (OSError, ValueError, TypeError) as exc:
                logger.error("workspace_settings_migration_failed", extra={
                    "config_key": key, "source_path": str(source), "error_type": type(exc).__name__,
                })
                raise ValueError(f"Cannot migrate workspace configuration: {source}") from None
            # Another worker may have seeded or saved it after our read.
            # Never overwrite a database value with a legacy file or default.
            self._store.save(key, payload, only_if_missing=True)
            logger.info("workspace_settings_initialized", extra={"config_key": key, "legacy_source": source.exists()})

    def get_general_settings(self) -> GeneralSettingsPayload:
        record = self._store.get(GENERAL_SETTINGS_KEY)
        if record is None:
            raise RuntimeError("General settings have not been initialized.")
        return GeneralSettingsPayload.model_validate(record.payload)

    def update_general_settings(self, payload: GeneralSettingsPayload) -> GeneralSettingsPayload:
        data = payload.model_dump(mode="json")
        data["lastSavedAt"] = datetime.now(timezone.utc).isoformat()
        record = self._store.save(GENERAL_SETTINGS_KEY, data)
        return GeneralSettingsPayload.model_validate(record.payload)

    def get_channel_advanced_settings(self) -> ChannelAdvancedSettings:
        record = self._store.get(CHANNEL_ADVANCED_KEY)
        if record is None:
            raise RuntimeError("Channel advanced settings have not been initialized.")
        return ChannelAdvancedSettings.model_validate(record.payload)

    def update_channel_advanced_settings(self, payload: ChannelAdvancedSettings) -> ChannelAdvancedSettings:
        record = self._store.save(CHANNEL_ADVANCED_KEY, payload.model_dump(mode="json"))
        return ChannelAdvancedSettings.model_validate(record.payload)
