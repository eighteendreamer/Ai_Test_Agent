from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from arango import ArangoClient
from arango.database import StandardDatabase
from pydantic import BaseModel

from src.core.config import Settings


SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
UTC = timezone.utc


def ensure_utc_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def serialize_datetime(value: datetime | str | None) -> str | None:
    normalized = ensure_utc_datetime(value)
    return normalized.isoformat() if normalized is not None else None


def make_json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return serialize_datetime(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return make_json_safe(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    return value


def day_bucket(value: datetime | str | None) -> str:
    normalized = ensure_utc_datetime(value) or datetime.utcnow()
    local_time = normalized.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ)
    return local_time.strftime("%Y-%m-%d")


def recent_day_buckets(days: int) -> list[str]:
    now = datetime.utcnow().replace(tzinfo=UTC).astimezone(SHANGHAI_TZ)
    return [
        (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(max(days, 1))
    ]


class ArangoRuntimeProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = ArangoClient(
            hosts=f"http://{settings.arango_host}:{settings.arango_port}"
        )
        self._db: StandardDatabase | None = None

    @property
    def database_url(self) -> str:
        return (
            f"http://{self._settings.arango_host}:{self._settings.arango_port}"
            f"/_db/{self._settings.arango_database}"
        )

    def db(self) -> StandardDatabase:
        if self._db is None:
            self._db = self._client.db(
                self._settings.arango_database,
                username=self._settings.arango_username,
                password=self._settings.arango_password,
            )
        return self._db

    def initialize(self) -> None:
        database = self.db()
        database.collections()
        self._ensure_collection(database, self._settings.arango_session_collection)
        self._ensure_collection(database, self._settings.arango_message_collection)
        self._ensure_collection(database, self._settings.arango_event_collection)
        self._ensure_collection(database, self._settings.arango_snapshot_collection)
        self._ensure_collection(database, self._settings.arango_approval_collection)
        self._ensure_collection(database, self._settings.arango_tool_job_collection)
        self._ensure_collection(database, self._settings.arango_tool_artifact_collection)
        self._ensure_collection(database, self._settings.arango_memory_collection)

        self._ensure_persistent_index(
            self._settings.arango_session_collection,
            "idx_sessions_updated_day",
            ["updated_day_bucket"],
        )
        self._ensure_persistent_index(
            self._settings.arango_session_collection,
            "idx_sessions_updated_at",
            ["updated_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_session_collection,
            "idx_sessions_status",
            ["status"],
        )

        self._ensure_persistent_index(
            self._settings.arango_message_collection,
            "idx_messages_day_created",
            ["day_bucket", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_message_collection,
            "idx_messages_session_created",
            ["session_id", "created_at"],
        )

        self._ensure_persistent_index(
            self._settings.arango_event_collection,
            "idx_events_day_timestamp",
            ["day_bucket", "timestamp"],
        )
        self._ensure_persistent_index(
            self._settings.arango_event_collection,
            "idx_events_session_timestamp",
            ["session_id", "timestamp"],
        )

        self._ensure_persistent_index(
            self._settings.arango_snapshot_collection,
            "idx_snapshots_day_created",
            ["day_bucket", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_snapshot_collection,
            "idx_snapshots_session_version",
            ["session_id", "version"],
            unique=True,
        )

        self._ensure_persistent_index(
            self._settings.arango_approval_collection,
            "idx_approvals_day_created",
            ["day_bucket", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_approval_collection,
            "idx_approvals_session_status_created",
            ["session_id", "status", "created_at"],
        )

        self._ensure_persistent_index(
            self._settings.arango_tool_job_collection,
            "idx_tool_jobs_day_created",
            ["day_bucket", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_tool_job_collection,
            "idx_tool_jobs_status_updated",
            ["status", "updated_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_tool_job_collection,
            "idx_tool_jobs_session_created",
            ["session_id", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_tool_job_collection,
            "idx_tool_jobs_tool_key",
            ["tool_key"],
        )

        self._ensure_persistent_index(
            self._settings.arango_tool_artifact_collection,
            "idx_tool_artifacts_day_created",
            ["day_bucket", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_tool_artifact_collection,
            "idx_tool_artifacts_tool_job_created",
            ["tool_job_id", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_tool_artifact_collection,
            "idx_tool_artifacts_session_created",
            ["session_id", "created_at"],
        )

        self._ensure_persistent_index(
            self._settings.arango_memory_collection,
            "idx_memory_day_created",
            ["day_bucket", "created_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_memory_collection,
            "idx_memory_session_updated",
            ["session_id", "updated_at"],
        )
        self._ensure_persistent_index(
            self._settings.arango_memory_collection,
            "idx_memory_scope",
            ["scope"],
        )
        self._ensure_persistent_index(
            self._settings.arango_memory_collection,
            "idx_memory_kind",
            ["kind"],
        )
        self._ensure_persistent_index(
            self._settings.arango_memory_collection,
            "idx_memory_tags",
            ["tags[*]"],
        )
        self._ensure_persistent_index(
            self._settings.arango_memory_collection,
            "idx_memory_stale",
            ["stale"],
        )

    def collection(self, name: str):
        return self.db().collection(name)

    def execute(self, query: str, bind_vars: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cursor = self.db().aql.execute(query, bind_vars=bind_vars or {})
        return list(cursor)

    def is_available(self) -> bool:
        try:
            self.db().collections()
            return True
        except Exception:
            return False

    def _ensure_collection(self, database: StandardDatabase, name: str) -> None:
        if not database.has_collection(name):
            database.create_collection(name)

    def _ensure_persistent_index(
        self,
        collection_name: str,
        name: str,
        fields: list[str],
        unique: bool = False,
    ) -> None:
        collection = self.collection(collection_name)
        existing = collection.indexes()
        if any(index.get("name") == name for index in existing):
            return
        collection.add_persistent_index(fields=fields, name=name, unique=unique)
