from __future__ import annotations

from datetime import datetime
import json

import pymysql

from src.application.model_adapters.provider_profiles import normalize_provider, resolve_provider_profile
from src.core.config import Settings
from src.schemas.model_config import (
    ModelCapabilitiesOverride,
    ModelConfigPublic,
    ModelConfigRecord,
)
from src.schemas.settings import ModelConfigUpdateRequest


class MySQLModelConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.llm_model_table}` (
                        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `model_name` VARCHAR(255) NOT NULL UNIQUE,
                        `api_key` TEXT NULL,
                        `base_url` VARCHAR(1024) NOT NULL,
                        `provider` VARCHAR(64) NOT NULL,
                        `capability_overrides` JSON NULL,
                        `is_active` TINYINT(1) NOT NULL DEFAULT 0,
                        `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.llm_model_table}` LIKE 'id'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.llm_model_table}`
                        ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT UNIQUE FIRST
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.llm_model_table}` LIKE 'capability_overrides'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.llm_model_table}`
                        ADD COLUMN `capability_overrides` JSON NULL AFTER `provider`
                        """
                    )
                cur.execute(
                    f"SELECT COUNT(*) AS total FROM `{self._settings.llm_model_table}`"
                )
                total = cur.fetchone()["total"]
                if total == 0:
                    cur.executemany(
                        f"""
                        INSERT INTO `{self._settings.llm_model_table}`
                        (model_name, api_key, base_url, provider, is_active)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                "claude-sonnet-4-20250514",
                                "",
                                "https://api.anthropic.com",
                                "anthropic",
                                0,
                            ),
                            (
                                "gpt-5.4",
                                "",
                                "https://api.openai.com/v1",
                                "openai",
                                0,
                            ),
                            (
                                "qwen-max",
                                "",
                                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                "qwen",
                                0,
                            ),
                            (
                                "deepseek-reasoner",
                                "",
                                "https://api.deepseek.com/v1",
                                "deepseek",
                                0,
                            ),
                        ],
                    )
            conn.commit()

    def list_all(self) -> list[ModelConfigRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    ORDER BY id ASC
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active(self) -> list[ModelConfigRecord]:
        return [item for item in self.list_all() if item.is_active]

    def get_by_name(self, model_name: str) -> ModelConfigRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (model_name,),
                )
                row = cur.fetchone()
        if not row:
            raise KeyError(model_name)
        return self._row_to_record(row)

    def upsert(self, payload: ModelConfigUpdateRequest) -> ModelConfigPublic:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (payload.model_name,),
                )
                existing_row = cur.fetchone()
                if payload.is_active:
                    cur.execute(
                        f"UPDATE `{self._settings.llm_model_table}` SET `is_active`=0"
                    )
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.llm_model_table}`
                    (`model_name`, `api_key`, `base_url`, `provider`, `capability_overrides`, `is_active`)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `api_key`=VALUES(`api_key`),
                        `base_url`=VALUES(`base_url`),
                        `provider`=VALUES(`provider`),
                        `capability_overrides`=VALUES(`capability_overrides`),
                        `is_active`=VALUES(`is_active`)
                    """,
                    (
                        payload.model_name,
                        payload.api_key if payload.api_key else ((existing_row or {}).get("api_key") or ""),
                        payload.base_url.rstrip("/"),
                        normalize_provider(payload.provider),
                        self._serialize_capability_overrides(
                            payload,
                            existing_row=existing_row,
                        ),
                        int(payload.is_active),
                    ),
                )
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (payload.model_name,),
                )
                row = cur.fetchone()
            conn.commit()
        return self.to_public(self._row_to_record(row))

    def update_existing(self, original_model_name: str, payload: ModelConfigUpdateRequest) -> ModelConfigPublic:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (original_model_name,),
                )
                existing_row = cur.fetchone()
                if not existing_row:
                    raise KeyError(original_model_name)

                target_model_name = payload.model_name.strip()
                if target_model_name != original_model_name:
                    cur.execute(
                        f"SELECT COUNT(*) AS total FROM `{self._settings.llm_model_table}` WHERE model_name=%s",
                        (target_model_name,),
                    )
                    duplicate_total = cur.fetchone()["total"]
                    if duplicate_total:
                        raise ValueError(f"Model '{target_model_name}' already exists.")

                if payload.is_active:
                    cur.execute(f"UPDATE `{self._settings.llm_model_table}` SET `is_active`=0")

                cur.execute(
                    f"""
                    UPDATE `{self._settings.llm_model_table}`
                    SET `model_name`=%s,
                        `api_key`=%s,
                        `base_url`=%s,
                        `provider`=%s,
                        `capability_overrides`=%s,
                        `is_active`=%s
                    WHERE `model_name`=%s
                    """,
                    (
                        target_model_name,
                        payload.api_key if payload.api_key else (existing_row.get("api_key") or ""),
                        payload.base_url.rstrip("/"),
                        normalize_provider(payload.provider),
                        self._serialize_capability_overrides(
                            payload,
                            existing_row=existing_row,
                        ),
                        int(payload.is_active),
                        original_model_name,
                    ),
                )
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (target_model_name,),
                )
                row = cur.fetchone()
            conn.commit()
        return self.to_public(self._row_to_record(row))

    def activate(self, model_name: str) -> ModelConfigPublic:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS total FROM `{self._settings.llm_model_table}` WHERE model_name=%s",
                    (model_name,),
                )
                if not cur.fetchone()["total"]:
                    raise KeyError(model_name)
                cur.execute(f"UPDATE `{self._settings.llm_model_table}` SET `is_active`=0")
                cur.execute(
                    f"UPDATE `{self._settings.llm_model_table}` SET `is_active`=1 WHERE model_name=%s",
                    (model_name,),
                )
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (model_name,),
                )
                row = cur.fetchone()
            conn.commit()
        return self.to_public(self._row_to_record(row))

    def delete(self, model_name: str) -> tuple[ModelConfigRecord, ModelConfigRecord | None]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    , capability_overrides
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (model_name,),
                )
                existing_row = cur.fetchone()
                if not existing_row:
                    raise KeyError(model_name)

                deleted_record = self._row_to_record(existing_row)
                cur.execute(
                    f"DELETE FROM `{self._settings.llm_model_table}` WHERE model_name=%s",
                    (model_name,),
                )

                replacement_row = None
                if deleted_record.is_active:
                    cur.execute(
                        f"""
                        SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                        , capability_overrides
                        FROM `{self._settings.llm_model_table}`
                        ORDER BY id ASC
                        LIMIT 1
                        """
                    )
                    replacement_row = cur.fetchone()
                    if replacement_row:
                        cur.execute(f"UPDATE `{self._settings.llm_model_table}` SET `is_active`=0")
                        cur.execute(
                            f"UPDATE `{self._settings.llm_model_table}` SET `is_active`=1 WHERE model_name=%s",
                            (replacement_row["model_name"],),
                        )
                        cur.execute(
                            f"""
                            SELECT id, model_name, api_key, base_url, provider, is_active, created_at, updated_at
                            , capability_overrides
                            FROM `{self._settings.llm_model_table}`
                            WHERE model_name=%s
                            """,
                            (replacement_row["model_name"],),
                        )
                        replacement_row = cur.fetchone()
            conn.commit()
        return deleted_record, (self._row_to_record(replacement_row) if replacement_row else None)

    def get_active(self, key: str) -> ModelConfigRecord:
        for item in self.list_active():
            if item.key == key:
                return item
        raise KeyError(key)

    def get_default_active(self) -> ModelConfigRecord:
        active = self.list_active()
        if active:
            return active[0]
        raise KeyError("No active model config found")

    def to_public(self, record: ModelConfigRecord) -> ModelConfigPublic:
        return ModelConfigPublic(
            id=record.id,
            key=record.key,
            name=record.name,
            provider=record.provider,
            transport=record.transport,
            model_id=record.model_id,
            api_base_url=record.api_base_url,
            description=record.description,
            supports_tools=record.supports_tools,
            supports_vision=record.supports_vision,
            supports_reasoning=record.supports_reasoning,
            is_active=record.is_active,
            is_default=record.is_default,
            temperature=record.temperature,
            max_tokens=record.max_tokens,
            has_secret=bool(record.api_key),
            capabilities=record.capabilities,
            capability_overrides=record.capability_overrides,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _connect(self):
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            database=self._settings.mysql_database,
            charset=self._settings.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _row_to_record(self, row: dict) -> ModelConfigRecord:
        provider = normalize_provider(row["provider"] or "")
        profile = resolve_provider_profile(provider)
        transport = profile.transport
        capability_overrides = self._parse_capability_overrides(row.get("capability_overrides"))
        capabilities = capability_overrides.apply_to(profile.capabilities)
        base_url = (row["base_url"] or "").rstrip("/")
        if transport == "openai_chat_completions" and base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        if transport == "anthropic_messages" and base_url.endswith("/v1/messages"):
            base_url = base_url[: -len("/v1/messages")]
        if transport == "google_gemini_generate_content" and ":generateContent" in base_url:
            base_url = base_url.split("/v1beta/models/", 1)[0]

        model_name = row["model_name"]
        canonical_key = self._canonical_model_key(provider, model_name)
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")
        return ModelConfigRecord(
            id=row.get("id"),
            key=canonical_key,
            name=model_name,
            provider=provider or "openai",
            transport=transport,
            model_id=model_name,
            api_base_url=base_url,
            api_key=row.get("api_key"),
            api_key_env=None,
            description=f"Active model config loaded from MySQL table `{self._settings.llm_model_table}`.",
            supports_tools=capabilities.tool_calling,
            supports_vision=capabilities.vision,
            supports_reasoning=capabilities.reasoning,
            is_active=bool(row.get("is_active")),
            is_default=False,
            temperature=None,
            max_tokens=4096,
            extra_headers={},
            capabilities=capabilities,
            capability_overrides=capability_overrides,
            created_at=self._normalize_datetime(created_at),
            updated_at=self._normalize_datetime(updated_at),
        )

    def _normalize_datetime(self, value) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    def _canonical_model_key(self, provider: str, model_name: str) -> str:
        lowered = model_name.lower()
        if provider == "anthropic":
            if lowered.startswith("claude-sonnet-4"):
                return "claude-sonnet-4"
            if lowered.startswith("claude-opus"):
                return "claude-opus"
            if lowered.startswith("claude-haiku"):
                return "claude-haiku"
        if "gpt-5.4" in lowered:
            return "gpt-5.4"
        if "qwen-max" in lowered:
            return "qwen-max"
        if "deepseek-reasoner" in lowered:
            return "deepseek-reasoner"
        return model_name

    def _parse_capability_overrides(self, raw_value) -> ModelCapabilitiesOverride:
        if raw_value in (None, "", b""):
            return ModelCapabilitiesOverride()
        if isinstance(raw_value, (bytes, bytearray)):
            raw_value = raw_value.decode("utf-8")
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except json.JSONDecodeError:
                return ModelCapabilitiesOverride()
        if isinstance(raw_value, dict):
            return ModelCapabilitiesOverride.model_validate(raw_value)
        return ModelCapabilitiesOverride()

    def _serialize_capability_overrides(
        self,
        payload: ModelConfigUpdateRequest,
        *,
        existing_row: dict | None = None,
    ) -> str | None:
        if payload.use_provider_defaults is None:
            if existing_row is None:
                return None
            existing_overrides = self._parse_capability_overrides(existing_row.get("capability_overrides"))
            return (
                json.dumps(existing_overrides.model_dump(exclude_none=True))
                if existing_overrides.has_values()
                else None
            )
        if payload.use_provider_defaults:
            return None
        overrides = payload.capability_overrides
        if not overrides.has_values():
            return None
        return json.dumps(overrides.model_dump(exclude_none=True))
