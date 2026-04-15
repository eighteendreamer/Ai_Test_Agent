from __future__ import annotations

from datetime import datetime

import pymysql

from src.core.config import Settings
from src.schemas.model_config import ModelConfigPublic, ModelConfigRecord
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
                        `model_name` VARCHAR(255) NOT NULL PRIMARY KEY,
                        `api_key` TEXT NULL,
                        `base_url` VARCHAR(1024) NOT NULL,
                        `provider` VARCHAR(64) NOT NULL,
                        `is_active` TINYINT(1) NOT NULL DEFAULT 0,
                        `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
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
                    SELECT model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    FROM `{self._settings.llm_model_table}`
                    ORDER BY model_name ASC
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active(self) -> list[ModelConfigRecord]:
        return [item for item in self.list_all() if item.is_active]

    def upsert(self, payload: ModelConfigUpdateRequest) -> ModelConfigPublic:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT model_name, api_key, base_url, provider, is_active, created_at, updated_at
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
                    (`model_name`, `api_key`, `base_url`, `provider`, `is_active`)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `api_key`=VALUES(`api_key`),
                        `base_url`=VALUES(`base_url`),
                        `provider`=VALUES(`provider`),
                        `is_active`=VALUES(`is_active`)
                    """,
                    (
                        payload.model_name,
                        payload.api_key if payload.api_key else ((existing_row or {}).get("api_key") or ""),
                        payload.base_url.rstrip("/"),
                        payload.provider.strip().lower(),
                        int(payload.is_active),
                    ),
                )
                cur.execute(
                    f"""
                    SELECT model_name, api_key, base_url, provider, is_active, created_at, updated_at
                    FROM `{self._settings.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (payload.model_name,),
                )
                row = cur.fetchone()
            conn.commit()
        return self.to_public(self._row_to_record(row))

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
        provider = (row["provider"] or "").strip().lower()
        transport = (
            "anthropic_messages" if provider == "anthropic" else "openai_chat_completions"
        )
        base_url = (row["base_url"] or "").rstrip("/")
        if transport == "openai_chat_completions" and base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        if transport == "anthropic_messages" and base_url.endswith("/v1/messages"):
            base_url = base_url[: -len("/v1/messages")]

        model_name = row["model_name"]
        canonical_key = self._canonical_model_key(provider, model_name)
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")
        return ModelConfigRecord(
            key=canonical_key,
            name=model_name,
            provider=provider or "openai",
            transport=transport,
            model_id=model_name,
            api_base_url=base_url,
            api_key=row.get("api_key"),
            api_key_env=None,
            description=f"Active model config loaded from MySQL table `{self._settings.llm_model_table}`.",
            supports_tools=True,
            supports_vision=provider in {"anthropic", "openai", "qwen"},
            supports_reasoning=True,
            is_active=bool(row.get("is_active")),
            is_default=False,
            temperature=None,
            max_tokens=4096,
            extra_headers={},
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
