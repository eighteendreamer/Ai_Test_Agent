from __future__ import annotations
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    app_name: str = "Enterprise AI QA Agent"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    data_dir: str = "data"
    llm_request_timeout_seconds: float = 60.0
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "QA_Agent"
    mysql_charset: str = "utf8mb4"
    llm_model_table: str = "llm_model_config"
    email_config_table: str = "system_email_config"
    session_table: str = "agent_sessions"
    session_message_table: str = "agent_session_messages"
    session_event_table: str = "agent_session_events"
    session_snapshot_table: str = "agent_session_snapshots"
    session_approval_table: str = "agent_session_approvals"
    tool_job_table: str = "agent_tool_jobs"
    tool_artifact_table: str = "agent_tool_artifacts"
    artifact_root_dir: str = "data/artifacts"
    qdrant_enabled: bool = True
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "agent_memory"
    qdrant_distance: str = "Cosine"
    embedding_provider: str = "local_hash"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_vector_size: int = 256
    memory_top_k: int = 6
    tool_job_heartbeat_timeout_seconds: int = 90
    browser_backend: str = "selenium"
    browser_default_name: str = "chrome"
    browser_headless: bool = True
    browser_window_width: int = 1440
    browser_window_height: int = 960
    browser_action_timeout_seconds: int = 15
    runtime_max_iterations: int = 8
    coordinator_max_workers: int = 4
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
