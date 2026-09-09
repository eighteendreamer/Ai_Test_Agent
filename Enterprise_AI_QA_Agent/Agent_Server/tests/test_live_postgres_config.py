from tests.live_postgres_config import LivePostgresTestConfig


def test_live_postgres_config_reads_process_environment(monkeypatch):
    monkeypatch.setenv("RUN_LIVE_POSTGRES_SOAK", "true")
    monkeypatch.setenv("RUN_LIVE_POSTGRES_SOAK_SECONDS", "300")
    monkeypatch.setenv("RUN_LIVE_POSTGRES_SOAK_ITERATION_INTERVAL_SECONDS", "0.25")
    monkeypatch.setenv("RUN_LIVE_POSTGRES_SOAK_MAX_ERROR_RATE", "0")
    monkeypatch.setenv("RUN_LIVE_POSTGRES_SOAK_MAX_P95_GROWTH_RATIO", "1.2")

    config = LivePostgresTestConfig(_env_file=None)

    assert config.run_live_postgres_soak is True
    assert config.run_live_postgres_soak_seconds == 300
    assert config.run_live_postgres_soak_iteration_interval_seconds == 0.25
    assert config.run_live_postgres_soak_max_error_rate == 0
    assert config.run_live_postgres_soak_max_p95_growth_ratio == 1.2
