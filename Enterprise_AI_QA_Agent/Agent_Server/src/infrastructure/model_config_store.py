from __future__ import annotations

from datetime import datetime
import json

from src.application.model_clients.provider_profiles import (
    normalize_provider,
    normalize_transport,
)
from src.core.config import Settings
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.model_config import (
    ModelCapabilities,
    ModelCapabilitiesOverride,
    ModelConfigPublic,
    ModelConfigRecord,
)
from src.schemas.settings import ModelConfigUpdateRequest


# All model capabilities are enabled by default on the backend; the frontend no
# longer exposes per-model capability toggles. Legacy capability_overrides rows
# still apply on top for backward compatibility.
_DEFAULT_CAPABILITIES = ModelCapabilities(
    text_input=True,
    text_output=True,
    tool_calling=True,
    vision=True,
    multi_image=True,
    file_input=True,
    pdf_input=True,
    reasoning=True,
    json_mode=True,
    streaming=True,
    parallel_tool_calls=True,
    image_url_input=True,
    image_base64_input=True,
)


class MySQLModelConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.database.llm_model_table}` (
                        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `model_name` VARCHAR(255) NOT NULL UNIQUE,
                        `api_key` TEXT NULL,
                        `base_url` VARCHAR(1024) NOT NULL,
                        `provider` VARCHAR(64) NOT NULL,
                        `transport` VARCHAR(64) NULL,
                        `capability_overrides` JSON NULL,
                        `context_window` BIGINT NULL,
                        `max_output_tokens` INT NULL,
                        `is_active` TINYINT(1) NOT NULL DEFAULT 0,
                        `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.database.mysql_charset}
                    """
                )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'id'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT UNIQUE FIRST
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'capability_overrides'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `capability_overrides` JSON NULL AFTER `provider`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'transport'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `transport` VARCHAR(64) NULL AFTER `provider`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'applications'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `applications` JSON NULL AFTER `transport`
                        """
                    )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.llm_model_table}`
                    SET `applications` = JSON_ARRAY('task_execution')
                    WHERE `applications` IS NULL OR JSON_LENGTH(`applications`) = 0
                    """
                )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.llm_model_table}`
                    SET `applications` = JSON_ARRAY(
                        JSON_UNQUOTE(JSON_EXTRACT(`applications`, '$[0]'))
                    )
                    WHERE JSON_LENGTH(`applications`) > 1
                    """
                )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.llm_model_table}`
                    SET `transport` =
                        CASE
                            WHEN LOWER(TRIM(`provider`)) IN ('anthropic') THEN 'anthropic_messages'
                            WHEN LOWER(TRIM(`provider`)) IN ('google', 'gemini') THEN 'google_gemini_generate_content'
                            ELSE 'openai_chat_completions'
                        END
                    WHERE `transport` IS NULL OR TRIM(`transport`) = ''
                    """
                )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'auth_type'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `auth_type` VARCHAR(32) NOT NULL DEFAULT 'api_key' AFTER `transport`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'oauth_client_id'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `oauth_client_id` TEXT NULL AFTER `auth_type`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'oauth_client_secret'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `oauth_client_secret` TEXT NULL AFTER `oauth_client_id`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'oauth_token_endpoint'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `oauth_token_endpoint` VARCHAR(1024) NULL AFTER `oauth_client_secret`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'oauth_scope'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `oauth_scope` VARCHAR(512) NULL AFTER `oauth_token_endpoint`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'oauth_provider'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `oauth_provider` VARCHAR(64) NULL AFTER `auth_type`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'oauth_refresh_token'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `oauth_refresh_token` TEXT NULL AFTER `oauth_scope`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'context_window'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `context_window` BIGINT NULL AFTER `capability_overrides`
                        """
                    )
                cur.execute(
                    f"SHOW COLUMNS FROM `{self._settings.database.llm_model_table}` LIKE 'max_output_tokens'"
                )
                if not cur.fetchone():
                    cur.execute(
                        f"""
                        ALTER TABLE `{self._settings.database.llm_model_table}`
                        ADD COLUMN `max_output_tokens` INT NULL AFTER `context_window`
                        """
                    )
                cur.execute(
                    f"SELECT COUNT(*) AS total FROM `{self._settings.database.llm_model_table}`"
                )
                total = cur.fetchone()["total"]
                if total == 0:
                    cur.executemany(
                        f"""
                        INSERT INTO `{self._settings.database.llm_model_table}`
                        (model_name, api_key, base_url, provider, transport, applications, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                "claude-sonnet-4-20250514",
                                "",
                                "https://api.anthropic.com",
                                "anthropic",
                                "anthropic_messages",
                                json.dumps(["task_execution"]),
                                0,
                            ),
                            (
                                "gpt-5.4",
                                "",
                                "https://api.openai.com/v1",
                                "openai",
                                "openai_chat_completions",
                                json.dumps(["task_execution"]),
                                0,
                            ),
                            (
                                "qwen-max",
                                "",
                                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                "qwen",
                                "openai_chat_completions",
                                json.dumps(["task_execution"]),
                                0,
                            ),
                            (
                                "deepseek-reasoner",
                                "",
                                "https://api.deepseek.com/v1",
                                "deepseek",
                                "openai_chat_completions",
                                json.dumps(["task_execution"]),
                                0,
                            ),
                        ],
                    )
            conn.commit()

    _SELECT_COLS = (
        "id, model_name, api_key, base_url, provider, is_active, created_at, updated_at"
        ", capability_overrides, transport, applications"
        ", auth_type, oauth_provider, oauth_refresh_token"
        ", context_window, max_output_tokens"
    )

    def list_all(self) -> list[ModelConfigRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
                    ORDER BY id ASC
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active(self) -> list[ModelConfigRecord]:
        return [item for item in self.list_all() if item.is_active]

    def get_for_application(self, application: str) -> ModelConfigRecord:
        candidates = [
            item
            for item in self.list_all()
            if application in item.applications
        ]
        if not candidates:
            raise KeyError(application)
        active = [item for item in candidates if item.is_active]
        return (active or candidates)[0]

    def get_by_name(self, model_name: str) -> ModelConfigRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (model_name,),
                )
                row = cur.fetchone()
        if not row:
            raise KeyError(model_name)
        return self._row_to_record(row)

    def upsert(self, payload: ModelConfigUpdateRequest) -> ModelConfigPublic:
        is_task_model = "task_execution" in payload.applications
        should_activate = bool(payload.is_active and is_task_model)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (payload.model_name,),
                )
                existing_row = cur.fetchone()
                if should_activate:
                    cur.execute(
                        f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=0"
                    )
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.database.llm_model_table}`
                    (`model_name`, `api_key`, `base_url`, `provider`, `transport`, `applications`, `capability_overrides`, `is_active`,
                     `auth_type`, `oauth_provider`, `oauth_refresh_token`, `context_window`, `max_output_tokens`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `api_key`=VALUES(`api_key`),
                        `base_url`=VALUES(`base_url`),
                        `provider`=VALUES(`provider`),
                        `transport`=VALUES(`transport`),
                        `applications`=VALUES(`applications`),
                        `capability_overrides`=VALUES(`capability_overrides`),
                        `is_active`=VALUES(`is_active`),
                        `auth_type`=VALUES(`auth_type`),
                        `oauth_provider`=VALUES(`oauth_provider`),
                        `oauth_refresh_token`=VALUES(`oauth_refresh_token`),
                        `context_window`=VALUES(`context_window`),
                        `max_output_tokens`=VALUES(`max_output_tokens`)
                    """,
                    (
                        payload.model_name,
                        payload.api_key if payload.api_key else ((existing_row or {}).get("api_key") or ""),
                        payload.base_url.rstrip("/"),
                        normalize_provider(payload.provider),
                        normalize_transport(payload.transport, provider=payload.provider),
                        json.dumps(payload.applications or ["task_execution"]),
                        self._serialize_capability_overrides(
                            payload,
                            existing_row=existing_row,
                        ),
                        int(should_activate),
                        payload.auth_type or "api_key",
                        payload.oauth_provider or None,
                        payload.oauth_refresh_token if payload.oauth_refresh_token else ((existing_row or {}).get("oauth_refresh_token") or None),
                        payload.context_window if payload.context_window else ((existing_row or {}).get("context_window") or None),
                        payload.max_output_tokens if payload.max_output_tokens else ((existing_row or {}).get("max_output_tokens") or None),
                    ),
                )
                self._ensure_task_execution_active(cur)
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (payload.model_name,),
                )
                row = cur.fetchone()
            conn.commit()
        return self.to_public(self._row_to_record(row))

    def update_existing(self, original_model_name: str, payload: ModelConfigUpdateRequest) -> ModelConfigPublic:
        is_task_model = "task_execution" in payload.applications
        should_activate = bool(payload.is_active and is_task_model)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
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
                        f"SELECT COUNT(*) AS total FROM `{self._settings.database.llm_model_table}` WHERE model_name=%s",
                        (target_model_name,),
                    )
                    duplicate_total = cur.fetchone()["total"]
                    if duplicate_total:
                        raise ValueError(f"Model '{target_model_name}' already exists.")

                if should_activate:
                    cur.execute(f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=0")

                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.llm_model_table}`
                    SET `model_name`=%s,
                        `api_key`=%s,
                        `base_url`=%s,
                        `provider`=%s,
                        `transport`=%s,
                        `applications`=%s,
                        `capability_overrides`=%s,
                        `is_active`=%s,
                        `auth_type`=%s,
                        `oauth_provider`=%s,
                        `oauth_refresh_token`=%s,
                        `context_window`=%s,
                        `max_output_tokens`=%s
                    WHERE `model_name`=%s
                    """,
                    (
                        target_model_name,
                        payload.api_key if payload.api_key else (existing_row.get("api_key") or ""),
                        payload.base_url.rstrip("/"),
                        normalize_provider(payload.provider),
                        normalize_transport(payload.transport, provider=payload.provider),
                        json.dumps(payload.applications or ["task_execution"]),
                        self._serialize_capability_overrides(
                            payload,
                            existing_row=existing_row,
                        ),
                        int(should_activate),
                        payload.auth_type or "api_key",
                        payload.oauth_provider or None,
                        payload.oauth_refresh_token if payload.oauth_refresh_token else (existing_row.get("oauth_refresh_token") or None),
                        payload.context_window if payload.context_window else (existing_row.get("context_window") or None),
                        payload.max_output_tokens if payload.max_output_tokens else (existing_row.get("max_output_tokens") or None),
                        original_model_name,
                    ),
                )
                self._ensure_task_execution_active(cur)
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
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
                    f"SELECT {self._SELECT_COLS} FROM `{self._settings.database.llm_model_table}` WHERE model_name=%s",
                    (model_name,),
                )
                existing_row = cur.fetchone()
                if not existing_row:
                    raise KeyError(model_name)
                if "task_execution" not in self._parse_applications(
                    existing_row.get("applications")
                ):
                    raise ValueError(
                        "Only a model applied to task execution can be activated as the main model."
                    )
                cur.execute(f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=0")
                cur.execute(
                    f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=1 WHERE model_name=%s",
                    (model_name,),
                )
                cur.execute(
                    f"""
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
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
                    SELECT {self._SELECT_COLS}
                    FROM `{self._settings.database.llm_model_table}`
                    WHERE model_name=%s
                    """,
                    (model_name,),
                )
                existing_row = cur.fetchone()
                if not existing_row:
                    raise KeyError(model_name)

                deleted_record = self._row_to_record(existing_row)
                cur.execute(
                    f"DELETE FROM `{self._settings.database.llm_model_table}` WHERE model_name=%s",
                    (model_name,),
                )

                replacement_row = None
                if deleted_record.is_active:
                    cur.execute(
                        f"""
                        SELECT {self._SELECT_COLS}
                        FROM `{self._settings.database.llm_model_table}`
                        WHERE JSON_CONTAINS(`applications`, JSON_QUOTE('task_execution'))
                        ORDER BY id ASC
                        LIMIT 1
                        """
                    )
                    replacement_row = cur.fetchone()
                    if replacement_row:
                        cur.execute(f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=0")
                        cur.execute(
                            f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=1 WHERE model_name=%s",
                            (replacement_row["model_name"],),
                        )
                        cur.execute(
                            f"""
                            SELECT {self._SELECT_COLS}
                            FROM `{self._settings.database.llm_model_table}`
                            WHERE model_name=%s
                            """,
                            (replacement_row["model_name"],),
                        )
                        replacement_row = cur.fetchone()
            conn.commit()
        return deleted_record, (self._row_to_record(replacement_row) if replacement_row else None)

    def _ensure_task_execution_active(self, cur) -> None:
        cur.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM `{self._settings.database.llm_model_table}`
            WHERE `is_active`=1
              AND JSON_CONTAINS(`applications`, JSON_QUOTE('task_execution'))
            """
        )
        if int(cur.fetchone()["total"] or 0) > 0:
            return
        cur.execute(
            f"""
            SELECT `model_name`
            FROM `{self._settings.database.llm_model_table}`
            WHERE JSON_CONTAINS(`applications`, JSON_QUOTE('task_execution'))
            ORDER BY `id` ASC
            LIMIT 1
            """
        )
        replacement = cur.fetchone()
        if replacement:
            cur.execute(
                f"UPDATE `{self._settings.database.llm_model_table}` SET `is_active`=1 WHERE `model_name`=%s",
                (replacement["model_name"],),
            )

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
            context_window=record.context_window,
            max_output_tokens=record.max_output_tokens,
            has_secret=bool(record.api_key),
            capabilities=record.capabilities,
            capability_overrides=record.capability_overrides,
            created_at=record.created_at,
            updated_at=record.updated_at,
            auth_type=record.auth_type,
            oauth_provider=record.oauth_provider,
            has_oauth_refresh_token=bool(record.oauth_refresh_token),
            applications=record.applications,
        )

    def _connect(self):
        return mysql_raw_connection(self._settings)

    def _row_to_record(self, row: dict) -> ModelConfigRecord:
        provider = normalize_provider(row["provider"] or "")
        transport = normalize_transport(row.get("transport"), provider=provider)
        capability_overrides = self._parse_capability_overrides(row.get("capability_overrides"))
        capabilities = capability_overrides.apply_to(_DEFAULT_CAPABILITIES)
        base_url = (row["base_url"] or "").rstrip("/")
        if transport == "openai_chat_completions" and base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        if transport == "openai_responses" and base_url.endswith("/responses"):
            base_url = base_url[: -len("/responses")]
        if transport == "anthropic_messages" and base_url.endswith("/v1/messages"):
            base_url = base_url[: -len("/v1/messages")]
        if transport == "google_gemini_generate_content" and ":generateContent" in base_url:
            base_url = base_url.split("/v1beta/models/", 1)[0]

        model_name = row["model_name"]
        canonical_key = self._canonical_model_key(provider, model_name)
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")
        auth_type = str(row.get("auth_type") or "api_key").strip() or "api_key"
        if auth_type not in ("api_key", "oauth2"):
            auth_type = "api_key"
        context_window = int(row.get("context_window") or 0) or 128000
        max_output_tokens = int(row.get("max_output_tokens") or 0) or 8192
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
            description=f"Active model config loaded from MySQL table `{self._settings.database.llm_model_table}`.",
            supports_tools=capabilities.tool_calling,
            supports_vision=capabilities.vision,
            supports_reasoning=capabilities.reasoning,
            is_active=bool(row.get("is_active")),
            is_default=False,
            temperature=None,
            max_tokens=max_output_tokens,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            extra_headers={},
            capabilities=capabilities,
            capability_overrides=capability_overrides,
            created_at=self._normalize_datetime(created_at),
            updated_at=self._normalize_datetime(updated_at),
            auth_type=auth_type,
            oauth_provider=row.get("oauth_provider") or None,
            oauth_refresh_token=row.get("oauth_refresh_token") or None,
            applications=self._parse_applications(row.get("applications")),
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

    def _parse_applications(self, raw_value) -> list[str]:
        if raw_value in (None, "", b""):
            return ["task_execution"]
        if isinstance(raw_value, (bytes, bytearray)):
            raw_value = raw_value.decode("utf-8")
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except json.JSONDecodeError:
                return ["task_execution"]
        allowed = {"task_execution", "embedding_retrieval"}
        applications = [
            str(item)
            for item in raw_value
            if str(item) in allowed
        ] if isinstance(raw_value, list) else []
        return (list(dict.fromkeys(applications)) or ["task_execution"])[:1]

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
