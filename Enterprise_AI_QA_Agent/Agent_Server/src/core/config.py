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
    intent_semantic_classifier_enabled: bool = True
    intent_deterministic_confidence_threshold: float = 0.82
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "QA_Agent"
    mysql_charset: str = "utf8mb4"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_database: str = "QA-Agent"
    postgres_connect_timeout_seconds: float = 5.0
    postgres_pool_size: int = 12
    postgres_memory_table: str = "agent_memories"
    postgres_session_table: str = "agent_sessions"
    postgres_message_table: str = "agent_session_messages"
    postgres_event_table: str = "agent_session_events"
    postgres_snapshot_table: str = "agent_session_snapshots"
    postgres_approval_table: str = "agent_session_approvals"
    postgres_tool_job_table: str = "agent_tool_jobs"
    postgres_tool_artifact_table: str = "agent_tool_artifacts"
    postgres_session_resource_table: str = "agent_session_resources"
    postgres_security_bug_table: str = "agent_security_bugs"
    postgres_project_table: str = "agent_projects"
    postgres_api_doc_table: str = "agent_api_docs"
    postgres_test_case_table: str = "agent_test_cases"
    postgres_test_case_version_table: str = "agent_test_case_versions"
    postgres_test_suite_table: str = "agent_test_suites"
    postgres_test_suite_item_table: str = "agent_test_suite_items"
    postgres_mcp_server_table: str = "agent_mcp_servers"
    postgres_vector_dimension: int = 1536
    memgraph_host: str = "127.0.0.1"
    memgraph_port: int = 7687
    memgraph_user: str = ""
    memgraph_password: str = ""
    llm_model_table: str = "llm_model_config"
    email_config_table: str = "system_email_config"
    channel_config_table: str = "system_channel_config"
    channel_credential_encryption_key: str = ""
    channel_pairing_public_base_url: str = ""
    redis_url: str
    agently_cli_config_root: str
    agently_auth_lock_ttl_seconds: int
    agently_auth_lock_wait_seconds: float
    agently_auth_check_interval_seconds: float
    docker_managed_container_prefix: str
    docker_managed_volume_root: str
    docker_redis_image: str
    docker_minio_image: str
    docker_mysql_image: str
    docker_postgres_image: str
    docker_memgraph_image: str
    memory_backend: str = "postgres"
    session_backend: str = "postgres"
    tool_job_backend: str = "postgres"
    ui_graph_backend: str = "memgraph"
    artifact_root_dir: str = "data/artifacts"
    artifact_storage_backend: str = "minio"
    artifact_keep_local_copy: bool = False
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "qa-agent"
    minio_secure: bool = False
    minio_upload_temp_bucket: str = "upload-temp"
    minio_upload_safe_bucket: str = "upload-safe"
    minio_upload_quarantine_bucket: str = "upload-quarantine"
    upload_scan_max_bytes: int = 10 * 1024 * 1024
    upload_scan_medium_risk_threshold: int = 30
    upload_scan_high_risk_threshold: int = 70
    security_runner_backend: str = "local"
    security_runner_docker_image: str = "vxcontrol/kali-linux"
    security_runner_docker_container_prefix: str = "qa-security-runner"
    security_runner_docker_workdir: str = "/work"
    security_runner_docker_network: str = ""
    security_runner_docker_net_raw: bool = True
    security_runner_docker_net_admin: bool = False
    security_runner_docker_pull_policy: str = "never"
    security_runner_container_reuse: bool = False
    security_runner_docker_cleanup_after_run: bool | None = None
    security_runner_wrap_timeout: bool = True
    security_runner_rewrite_localhost: bool = True
    # Security target allowlist hard gate (S6). Comma-separated hosts / IPs /
    # CIDR / *.suffix wildcards. Empty means "do not restrict" but every
    # execution against a public target is logged as a warning.
    security_target_allowlist: str = ""
    # Polling cadence for detached security runner jobs (S1). Callers may use
    # a faster interval for tests, but production orchestration should respect
    # this default to avoid hammering the Docker daemon.
    security_runner_detach_poll_interval_seconds: float = 3.0
    # Worker tool output above this byte threshold is compacted with a
    # structure-preserving summary instead of a blind head truncation (S4).
    security_runner_output_summary_threshold_bytes: int = 16384
    # P0 attack-chain loop. Attempts remain bound to registered profiles,
    # the verified target scope, per-task timeout, and the existing approval gate.
    security_attack_chain_enabled: bool = True
    security_campaign_max_loops: int = 5
    security_campaign_max_attempts: int = 30
    security_bug_registry_enabled: bool = True
    security_bug_reproduction_required: bool = True
    security_attack_session_enabled: bool = False
    security_attack_session_timeout_seconds: int = 900
    security_attack_session_command_timeout_seconds: int = 120
    # P3 callback and graph capabilities stay opt-in. The callback broker
    # binds loopback only and never turns a callback into an executable shell.
    security_callback_broker_enabled: bool = False
    security_callback_port_range: str = "28000-28100"
    security_callback_lease_timeout_seconds: int = 300
    security_graph_memory_enabled: bool = False
    # P4 is deliberately separate from the legacy runner wrapper. Empty
    # allowlists deny every installation attempt, even when the feature flag
    # is enabled.
    security_tool_bootstrap_enabled: bool = False
    security_tool_bootstrap_package_allowlist: str = ""
    security_tool_bootstrap_image_allowlist: str = ""
    security_tool_bootstrap_repository_allowlist: str = ""
    security_tool_bootstrap_timeout_seconds: int = 300
    security_tool_bootstrap_cleanup_required: bool = True
    # Performance runner
    performance_runner_backend: str = "auto"
    performance_runner_ephemeral: bool = True
    performance_default_engine: str = "k6"
    performance_default_workload_model: str = "open"
    k6_docker_image_key: str = "perf_k6_default"
    jmeter_docker_image_key: str = "perf_jmeter_default"
    k6_docker_image: str = "grafana/k6:latest"
    jmeter_docker_image: str = "alpine/jmeter:5.6.3"
    perf_engine_pull_policy: str = "missing"
    performance_runner_docker_cpus: str = ""
    performance_runner_docker_memory: str = ""
    performance_max_concurrent_runs: int = 1
    performance_rewrite_localhost: bool = True
    performance_smoke_required: bool = True
    performance_smoke_iterations: int = 3
    performance_target_allowlist: str = ""
    performance_max_vus: int = 2000
    performance_max_rate_rps: int = 1000
    performance_max_duration_seconds: int = 1800
    performance_runner_docker_container_prefix: str = "qa-perf"
    performance_runner_docker_workdir: str = "/work"
    postgres_perf_runs_table: str = "agent_perf_runs"
    memory_top_k: int = 6
    tool_job_heartbeat_timeout_seconds: int = 90
    compatibility_runner_heartbeat_timeout_seconds: int = 120
    mcp_stdio_command_allowlist: list[str] = Field(
        default_factory=lambda: ["npx", "uvx", "node", "python", "python3"]
    )
    mcp_health_check_interval_seconds: float = 30.0
    browser_backend: str = "playwright-cli"
    browser_default_name: str = "chromium"
    browser_headless: bool = True
    browser_window_width: int = 1440
    browser_window_height: int = 960
    browser_action_timeout_seconds: int = 15
    runtime_max_iterations: int = 8
    coordinator_max_workers: int = 4
    anysearch_api_base_url: str = "https://api.anysearch.com"
    anysearch_api_key: str = ""
    # Context budget management
    context_compaction_watermark: float = 0.7
    context_max_tail_messages: int = 24
    tool_message_max_chars: int = 24000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # OAuth 2.0 Provider Credentials
    oauth_azure_ad_client_id: str = ""
    oauth_azure_ad_client_secret: str = ""
    oauth_azure_ad_tenant_id: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_google_project_id: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_codebuddy_client_id: str = ""
    oauth_codebuddy_client_secret: str = ""
    oauth_codebuddy_poll_url: str = ""
    oauth_codebuddy_models_endpoint: str = ""
    oauth_trae_client_id: str = ""
    oauth_trae_client_secret: str = ""
    oauth_codex_client_id: str = ""
    oauth_codex_client_secret: str = ""

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

    @field_validator("mcp_stdio_command_allowlist", mode="before")
    @classmethod
    def split_mcp_stdio_command_allowlist(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("postgres_pool_size")
    @classmethod
    def validate_postgres_pool_size(cls, value: int) -> int:
        return max(1, value)

    @field_validator("agently_auth_lock_ttl_seconds")
    @classmethod
    def validate_agently_auth_lock_ttl(cls, value: int) -> int:
        return max(5, value)

    @field_validator("agently_auth_lock_wait_seconds", "agently_auth_check_interval_seconds")
    @classmethod
    def validate_agently_positive_seconds(cls, value: float) -> float:
        return max(0.1, value)

    @field_validator(
        "redis_url",
        "agently_cli_config_root",
        "docker_managed_container_prefix",
        "docker_managed_volume_root",
        "docker_redis_image",
        "docker_minio_image",
        "docker_mysql_image",
        "docker_postgres_image",
        "docker_memgraph_image",
    )
    @classmethod
    def validate_agently_required_strings(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Agent Mail Redis and credential path settings are required.")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
