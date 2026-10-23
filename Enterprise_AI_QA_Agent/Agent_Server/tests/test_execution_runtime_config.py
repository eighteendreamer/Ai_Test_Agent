"""Missing live-test Redis configuration must not allocate any resources."""
import pytest

import test_execution_worker_cli as execution_cli


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_url", [None, "", "   "])
async def test_missing_redis_is_reported_before_runtime_allocation(tmp_path, monkeypatch, configured_url):
    if configured_url is None:
        monkeypatch.delenv("QA_ISOLATED_RUNTIME_REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("QA_ISOLATED_RUNTIME_REDIS_URL", configured_url)

    def unexpected_settings_read():
        raise AssertionError("runtime allocation started before Redis configuration was checked")

    monkeypatch.setattr(execution_cli, "get_settings", unexpected_settings_read)
    with pytest.raises(pytest.fail.Exception, match="QA_ISOLATED_RUNTIME_REDIS_URL"):
        async with execution_cli.isolated_runtime(tmp_path):
            pytest.fail("unconfigured runtime was yielded")
    assert list(tmp_path.iterdir()) == []
