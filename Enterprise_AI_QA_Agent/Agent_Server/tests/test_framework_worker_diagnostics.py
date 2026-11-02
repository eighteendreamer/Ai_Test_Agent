"""Optional child-Worker evidence must not change execution or its deadline."""
from __future__ import annotations

import asyncio
import faulthandler
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.core import log_format
from tests.test_agent_task_dispatch import worker_process as agent_worker_process
from tests.test_framework_task_control import framework_worker_process
from tests.framework_worker_diagnostics import controlled_case_wait_worker_process


ENTRIES = [framework_worker_process, agent_worker_process, controlled_case_wait_worker_process]


@pytest.mark.parametrize("entry", ENTRIES)
def test_spawn_entry_arms_diagnostics_before_importing_heavy_worker_fixtures(entry):
    assert entry.__module__ == "tests.framework_worker_diagnostics", (
        "spawn imports the target module before calling it; diagnostics inside the fixture module start too late"
    )


def probe(monkeypatch, caplog, *, enabled, error=None, entry=framework_worker_process):
    # Restore stdlib hooks before pytest's own faulthandler failure reporting.
    with monkeypatch.context() as scoped:
        return _probe(scoped, caplog, enabled=enabled, error=error, entry=entry)


def _probe(monkeypatch, caplog, *, enabled, error=None, entry=framework_worker_process):
    private = "fixture-private-worker-arguments"
    if enabled:
        monkeypatch.setenv("QA_FRAMEWORK_WORKER_DIAGNOSTICS", "1")
    else:
        monkeypatch.delenv("QA_FRAMEWORK_WORKER_DIAGNOSTICS", raising=False)
    caplog.set_level("INFO")
    calls = []
    monkeypatch.setattr(log_format, "configure_logging", lambda level: calls.append(("logging", level)))

    def arm(*args, **kwargs):
        calls.append(("arm", args, kwargs))

    def cancel():
        calls.append(("cancel",))

    def run(coroutine):
        coroutine.close()  # No queue/database/domain execution in this unit probe.
        calls.append(("run",))
        if error is not None:
            raise error

    monkeypatch.setattr(faulthandler, "dump_traceback_later", arm)
    monkeypatch.setattr(faulthandler, "cancel_dump_traceback_later", cancel)
    monkeypatch.setattr(asyncio, "run", run)
    args = (SimpleNamespace(private=private), private, SimpleNamespace(private=private), "none")
    if entry is agent_worker_process:
        args = args[:3]
    elif entry is controlled_case_wait_worker_process:
        args = (SimpleNamespace(private=private), None, private, {}, private, "read")
    if error is None:
        entry(*args)
    else:
        with pytest.raises(type(error)) as caught:
            entry(*args)
        assert caught.value is error, "Diagnostics replaced the original execution exception"
    assert private not in repr([record.__dict__ for record in caplog.records])
    return calls


@pytest.mark.parametrize("entry", ENTRIES)
def test_default_worker_does_not_arm_diagnostics_or_change_logging(monkeypatch, caplog, entry):
    calls = probe(monkeypatch, caplog, enabled=False, entry=entry)
    assert calls == [("logging", "WARNING"), ("run",)]
    assert not [record for record in caplog.records if record.name == "tests.framework_worker_diagnostics"]


@pytest.mark.parametrize("failure", [None, RuntimeError("fixture-private-worker-arguments"),
    asyncio.CancelledError("fixture-private-worker-arguments")])
@pytest.mark.parametrize("entry", ENTRIES)
def test_enabled_worker_records_phases_without_native_watchdog_or_masking_execution(monkeypatch, caplog, failure, entry):
    calls = probe(monkeypatch, caplog, enabled=True, error=failure, entry=entry)
    assert calls == [("logging", "INFO"), ("run",)]
    records = [record for record in caplog.records if record.name == "tests.framework_worker_diagnostics"]
    assert [record.getMessage() for record in records] == ["framework_test_worker_entered",
        "framework_test_worker_fixtures_loaded", "framework_test_worker_exited"]
    assert records[0].worker_id == records[1].worker_id == records[2].worker_id
    assert 0 <= records[1].operation_seconds <= records[2].operation_seconds
    assert records[2].error_type == (type(failure).__name__ if failure is not None else None)
    assert all(record.exc_info is None for record in records)


def test_real_child_waits_normally_without_native_watchdog_sampling():
    script = """
import time
from tests.framework_worker_diagnostics import framework_worker_diagnostics
with framework_worker_diagnostics():
    time.sleep(8.2)
print('worker_returned_normally')
"""
    result = subprocess.run([sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QA_FRAMEWORK_WORKER_DIAGNOSTICS": "1"},
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "worker_returned_normally"
    assert "Timeout (" not in result.stderr
    assert "framework_test_worker_entered" in result.stderr
    assert "framework_test_worker_exited" in result.stderr


@pytest.mark.parametrize("entry,fixture", [(framework_worker_process, "tests.test_framework_task_control"),
    (agent_worker_process, "tests.test_agent_task_dispatch"),
    (controlled_case_wait_worker_process, "tests.test_controlled_case_approval_wait_process")])
def test_real_child_diagnoses_slow_fixture_import_without_masking_its_failure(entry, fixture):
    script = """
import builtins
import sys
import time

original_import = builtins.__import__
failure = ImportError('fixture-private-import-failure')

def slow_fixture_import(name, *args, **kwargs):
    if name == sys.argv[3]:
        time.sleep(8.2)
        raise failure
    return original_import(name, *args, **kwargs)

builtins.__import__ = slow_fixture_import
try:
    module = __import__(sys.argv[1], fromlist=[sys.argv[2]])
    args = {'agent_worker_process': (None, None, None),
        'framework_worker_process': (None, None, None, 'none'),
        'controlled_case_wait_worker_process': (None, None, None, None, None, 'read')}[sys.argv[2]]
    getattr(module, sys.argv[2])(*args)
except ImportError as exc:
    assert exc is failure
    print('original_import_failure_preserved')
else:
    raise AssertionError('The controlled import failure must propagate')
finally:
    builtins.__import__ = original_import
"""
    result = subprocess.run([sys.executable, "-c", script, entry.__module__, entry.__name__, fixture],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QA_FRAMEWORK_WORKER_DIAGNOSTICS": "1"},
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "original_import_failure_preserved"
    assert "Timeout (" not in result.stderr
    assert result.stderr.index("framework_test_worker_entered") < result.stderr.index("framework_test_worker_exited")
    assert "framework_test_worker_fixtures_loaded" not in result.stderr
    assert '"error_type":"ImportError"' in result.stderr
    assert "fixture-private-import-failure" not in result.stderr
