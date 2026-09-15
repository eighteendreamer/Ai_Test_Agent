from src.core.config import OrchestrConfig


def test_vector_store_is_enabled_by_default():
    assert OrchestrConfig().redis_vector_enabled is True
