from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.config import ENV_FILE


class LivePostgresTestConfig(BaseSettings):
    """Opt-in live-test settings loaded from Agent_Server/.env or the process."""

    run_live_postgres_tests: bool = False
    run_live_postgres_capacity: bool = False
    run_live_postgres_capacity_sizes: str = "1000,10000,100000"
    run_live_postgres_capacity_workers: int = 32
    run_live_postgres_soak: bool = False
    run_live_postgres_soak_seconds: int = 14400
    run_live_postgres_soak_sample_interval_seconds: int = 60
    run_live_postgres_soak_iteration_interval_seconds: float = 1.0
    run_live_postgres_soak_workers: int = 4
    run_live_postgres_soak_max_iterations: int = 0
    run_live_postgres_soak_max_error_rate: float = -1.0
    run_live_postgres_soak_max_complete_p95_ms: float = 0.0
    run_live_postgres_soak_max_p95_growth_ratio: float = 0.0
    run_live_postgres_soak_max_rss_growth_bytes: int = 0
    run_live_postgres_soak_max_connections: int = 0

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
