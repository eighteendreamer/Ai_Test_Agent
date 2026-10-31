from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_release import FrameworkReleaseReference
from tests.test_runtime_binding_contract import binding
from tests.test_framework_release_archive import release
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_model_reader_admission import database_state
from tests.test_framework_model_reader_admission import dispatcher, deliver
from tests.test_agent_v2_worker_contract import command


REFERENCE = {"schema_version": 1, "archive_sha256": "b" * 64,
    "size_bytes": 1234, "source_revision": "c" * 40}


@pytest.fixture
def isolate_recovery_stores(admission):
    # Same explicit isolation as the recovery harness, but not autouse: pure
    # source/codec tests must not silently require a database or be skipped.
    admission.settings.orchestration.agent_task_v2_stream = admission.prefix + ":message-recovery"
    admission.settings.deep_agents.checkpoint_schema = admission.prefix + "_checkpoints"
    admission.settings.database.postgres_tool_job_table = admission.prefix + "_jobs"


def test_binding_freezes_a_release_reference_and_fingerprint():
    original = binding()
    selected = binding(release_reference=REFERENCE)
    assert selected.fingerprint != original.fingerprint
    assert selected.model_dump(mode="json")["release_reference"] == REFERENCE
    assert RuntimeBinding.model_validate(selected.model_dump(mode="json")) == selected
    assert "release_reference" not in original.model_dump(mode="json")
    changed = selected.model_dump(mode="json")
    changed["release_reference"]["source_revision"] = "d" * 40
    with pytest.raises(ValueError, match="fingerprint"):
        RuntimeBinding.model_validate(changed)


def test_lock_and_runtime_version_alone_cannot_claim_a_release_pinned_binding():
    selected = binding(release_reference=REFERENCE)
    capabilities = FrameworkWorkerCapabilities("a" * 64, True)
    with pytest.raises(TaskDeferred, match="framework_release_artifact_unavailable"):
        capabilities.require(selected, operation="message")


@pytest.mark.parametrize("field,value", [("archive_sha256", "d" * 64), ("size_bytes", 5678),
    ("source_revision", "e" * 40)])
def test_worker_requires_the_complete_release_reference_not_only_the_digest(field, value):
    selected = binding(release_reference=REFERENCE)
    reference = FrameworkReleaseReference.model_validate(REFERENCE)
    capabilities = FrameworkWorkerCapabilities("a" * 64, True, release_reference=reference)
    capabilities.require(selected, operation="message")
    changed = FrameworkReleaseReference.model_validate({**REFERENCE, field: value})
    with pytest.raises(TaskDeferred, match="framework_release_artifact_unavailable"):
        replace(capabilities, release_reference=changed).require(selected, operation="message")
    # Unpinned historical contracts remain readable; no automatic association.
    capabilities.require(binding(), operation="message")


@pytest.mark.parametrize("version", [True, "1", 1.0, None, 2])
def test_nested_release_version_is_strict_and_cannot_use_model_copy_to_skip_validation(version):
    with pytest.raises(ValueError):
        binding(release_reference={**REFERENCE, "schema_version": version})
    forged = FrameworkReleaseReference.model_validate(REFERENCE).model_copy(update={"schema_version": version})
    with pytest.raises(ValueError):
        binding(release_reference=forged)


def test_future_reference_version_is_deferred_by_the_existing_command_reader():
    wire = command(binding(release_reference=REFERENCE)).model_dump(mode="json")
    wire["runtime_binding"]["release_reference"]["schema_version"] = 2
    with pytest.raises(TaskDeferred, match="framework_command_format_unavailable"):
        FrameworkWorkerCapabilities.require_command_format(wire)


def test_restored_source_can_be_checked_against_its_release_without_git(tmp_path, release):
    from src.runtime.framework_release_archive import require_installed_release, restore_release

    store, reference = release
    destination = tmp_path / "installed"
    restore_release(store, reference, destination)
    checked = require_installed_release(destination, store, reference)
    assert checked.source_revision == reference.source_revision
    assert not (destination / ".git").exists()


@pytest.mark.parametrize("change", ["modified", "missing", "extra"])
def test_installation_rejects_source_drift_before_importing_it(tmp_path, release, monkeypatch, change):
    from src.runtime import framework_release_archive as module

    store, reference = release
    destination = tmp_path / "installed"
    module.restore_release(store, reference, destination)
    path = destination / "Agent_Server/src/runtime/framework_worker_capabilities.py"
    if change == "modified":
        path.write_bytes(path.read_bytes() + b"\n# drift\n")
    elif change == "missing":
        path.unlink()
    else:
        (path.parent / "unarchived.py").write_bytes(b"# unarchived executable source")

    def unexpected_import(*args):
        pytest.fail("Source drift must be rejected before the import probe")

    monkeypatch.setattr(module, "_probe", unexpected_import)
    with pytest.raises(ValueError, match="Installed release source"):
        module.require_installed_release(destination, store, reference)


def test_installation_accepts_utf8_bom_and_windows_checkout_newlines(tmp_path, release):
    from src.runtime.framework_release_archive import require_installed_release, restore_release

    store, reference = release
    destination = tmp_path / "installed"
    restore_release(store, reference, destination)
    path = destination / "Agent_Server/src/runtime/framework_worker_capabilities.py"
    text = path.read_text(encoding="utf-8-sig")
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    (destination / "Agent_Server/.env").write_bytes(b"SYNTHETIC_UNUSED_PRIVATE_DATA=not-a-real-secret")
    assert require_installed_release(destination, store, reference).source_revision == reference.source_revision


@pytest.mark.parametrize("section", ["environment", "formats", "tools"])
def test_installation_rechecks_the_active_environment_and_definitions(tmp_path, release, monkeypatch, section):
    from src.runtime import framework_release_archive as module

    store, reference = release
    destination = tmp_path / "installed"
    module.restore_release(store, reference, destination)
    original = module._probe

    def changed(source):
        actual = original(source)
        actual[section]["synthetic_drift"] = "different"
        return actual

    monkeypatch.setattr(module, "_probe", changed)
    with pytest.raises(ValueError, match="environment or definitions"):
        module.require_installed_release(destination, store, reference)


def test_capability_factory_cannot_advertise_a_reference_before_installation_check(monkeypatch, tmp_path):
    from src.runtime import framework_release_archive as module

    checked = []

    def verify(project, store, reference):
        checked.append((project, store, reference))

    monkeypatch.setattr(module, "require_installed_release", verify)
    reference = FrameworkReleaseReference.model_validate(REFERENCE)
    caps = FrameworkWorkerCapabilities.from_release(tmp_path, reference, deepagents_available=True)
    assert checked == [(Path(module.__file__).resolve().parents[3], tmp_path, reference)]
    assert caps.release_reference == reference and caps.deepagents_available

    def denied(*args):
        raise ValueError("synthetic installation mismatch")

    monkeypatch.setattr(module, "require_installed_release", denied)
    with pytest.raises(ValueError, match="installation mismatch"):
        FrameworkWorkerCapabilities.from_release(tmp_path, reference, deepagents_available=True)


def test_previous_committed_reader_preserves_old_wire_and_defers_new_reference(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release

    store, reference = release
    destination = tmp_path / "previous-release"
    restore_release(store, reference, destination)
    messages = [command(binding()).model_dump(mode="json"),
        command(binding(release_reference=REFERENCE)).model_dump(mode="json")]
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    probe = '''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'Agent_Server'))
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
old, pinned = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))
loaded = FrameworkWorkerCapabilities.require_command_format(old)
assert loaded.model_dump(mode='json') == old
try:
    FrameworkWorkerCapabilities.require_command_format(pinned)
except TaskDeferred as exc:
    assert str(exc) == 'framework_command_format_unavailable'
else:
    raise AssertionError('Previous reader must defer an unknown binding field')
print('previous_reader_old_wire_unchanged_new_reference_deferred')
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(destination), str(path)],
        cwd=destination, capture_output=True, check=True, timeout=30)
    assert b"old_wire_unchanged_new_reference_deferred" in result.stdout


def test_current_source_snapshot_resolves_a_pinned_binding_in_a_fresh_process(tmp_path):
    from src.runtime import framework_release_archive as module

    checkout = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "trusted-source-snapshot"
    snapshot.mkdir()
    names = set(module.METADATA)
    names.update(path.relative_to(checkout).as_posix() for path in (checkout / "Agent_Server/src").rglob("*")
        if path.is_file() and module._source_name(path.relative_to(checkout).as_posix()))
    for name in names:
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((checkout / name).read_bytes())
    # This is an isolated test commit of the current implementation, not a
    # published commit in the user's repository or an independent venv install.
    environment = {**os.environ, "GIT_AUTHOR_NAME": "Release Test", "GIT_COMMITTER_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid", "GIT_COMMITTER_EMAIL": "release@example.invalid"}
    for args in (("init",), ("add", "."), ("commit", "-m", "trusted current source snapshot")):
        subprocess.run(["git", *args], cwd=snapshot, env=environment, capture_output=True, check=True, timeout=120)
    store = tmp_path / "snapshot-store"
    reference = module.archive_release(snapshot, store)
    restored = tmp_path / "restored-current-source"
    manifest = module.restore_release(store, reference, restored)
    selected = binding(release_reference=reference,
        dependency_lock_sha256=manifest.environment["worker_capabilities"]["dependency_lock_sha256"])
    path = tmp_path / "pinned-binding.json"
    path.write_text(selected.model_dump_json(), encoding="utf-8")
    probe = '''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'Agent_Server'))
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.framework_release_archive import verify_release
from src.schemas.runtime_binding import RuntimeBinding
binding = RuntimeBinding.model_validate_json(pathlib.Path(sys.argv[3]).read_bytes())
caps = FrameworkWorkerCapabilities.from_release(pathlib.Path(sys.argv[2]), binding.release_reference, deepagents_available=True)
caps.require(binding, operation='message')
manifest = verify_release(pathlib.Path(sys.argv[2]), binding.release_reference)
loaded = [pathlib.Path(item.__file__).resolve() for name, item in sys.modules.items()
    if name.startswith('src.') and getattr(item, '__file__', None)]
assert loaded and all(path.is_relative_to(root) for path in loaded)
assert {'src.runtime.framework_worker_capabilities', 'src.runtime.framework_release_archive',
    'src.schemas.runtime_binding', 'src.schemas.framework_release'} <= set(sys.modules)
assert caps.release_reference.source_revision == manifest.source_revision
print(json.dumps({'reference_verified': True, 'inside_restored_source': True, 'modules': len(loaded)}))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(restored), str(store), str(path)],
        cwd=restored, capture_output=True, check=True, timeout=120)
    observed = json.loads(result.stdout)
    assert observed["reference_verified"] and observed["inside_restored_source"] and observed["modules"] > 0


@pytest.mark.asyncio
async def test_direct_pinned_claim_cannot_bypass_release_capabilities(admission, isolate_recovery_stores):
    h = admission
    assert h.settings.deep_agents.checkpoint_schema == h.prefix + "_checkpoints"
    h.binding = RuntimeBinding.model_validate({**h.binding.model_dump(mode="json", exclude={"fingerprint"}),
        "release_reference": REFERENCE})
    h.command = h.command.model_copy(update={"runtime_binding": h.binding})
    task = await submit(h)
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_release_artifact_unavailable"):
        await h.tasks.claim(task.id, worker_id="unverified-release", expected_binding=h.binding)
    assert database_state(h) == before and h.runtime.calls == []


def pin(h):
    assert h.settings.deep_agents.checkpoint_schema == h.prefix + "_checkpoints"
    h.binding = RuntimeBinding.model_validate({**h.binding.model_dump(mode="json", exclude={"fingerprint"}),
        "release_reference": REFERENCE})
    h.command = h.command.model_copy(update={"runtime_binding": h.binding})
    return FrameworkWorkerCapabilities("a" * 64, True,
        release_reference=FrameworkReleaseReference.model_validate(REFERENCE))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "claimed", "running", "cancelled"])
async def test_release_mismatch_defers_inside_claim_before_any_transition(admission, isolate_recovery_stores, status, caplog):
    h = admission
    matching = pin(h)
    task = await submit(h)
    if status != "queued":
        original = await h.tasks.claim(task.id, worker_id="release-origin", expected_binding=h.binding,
            framework_capabilities=matching)
        if status == "running":
            await h.tasks.begin(original, resource_id="release-fixture", fencing_token=1)
        if status == "cancelled":
            await h.tasks.finish(original, status="cancelled", error_code="synthetic_release_cancelled")
        else:
            assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
    before = database_state(h)
    with pytest.raises(TaskDeferred, match="framework_release_artifact_unavailable"):
        await h.tasks.claim(task.id, worker_id="different-release", expected_binding=h.binding,
            framework_capabilities=replace(matching, release_reference=None))
    assert database_state(h) == before and h.runtime.calls == []
    records = [row for row in caplog.records if row.message == "framework_release_reader_unavailable"]
    assert len(records) == 1 and records[0].task_id == task.id
    assert records[0].binding_id == h.binding.binding_id and records[0].worker_id == "different-release"
    assert records[0].reason == "framework_release_artifact_unavailable" and records[0].exc_info is None


@pytest.mark.asyncio
async def test_release_mismatch_keeps_the_same_real_redis_delivery(admission, isolate_recovery_stores):
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.redis_task_worker import RedisTaskWorker

    h = admission
    pin(h)
    await submit(h)
    selected = dispatcher(h)
    queue = RedisTaskQueue(h.settings.database.redis_url,
        stream=h.settings.orchestration.agent_task_v2_stream, group="release-reference")
    await queue.connect()

    async def handle(item):
        await selected.handle_v2(item, worker_id="unverified-release")

    try:
        assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
        before, entries = database_state(h), await h.redis.xrange(queue.stream)
        assert await RedisTaskWorker(queue, consumer="unverified-release", handler=handle,
            block_ms=1, max_retries=0).run_once() == 0
        assert database_state(h) == before and h.executions == h.runtime.calls == []
        assert (await h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await h.redis.xrange(queue.stream) == entries and await h.redis.xlen(queue.stream + ":dead_letter") == 0
        pending = await queue.consume(consumer="unverified-release", stream_id="0", block_ms=0)
        assert len(pending) == 1 and pending[0].retry_count == 0
        assert pending[0].message_id == entries[0][0] and pending[0].payload == h.command.model_dump(mode="json")
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_matching_reference_is_persisted_once_and_reaches_original_dispatcher(admission, isolate_recovery_stores):
    from src.runtime.postgres_agent_task_store import AgentSubmissionConflict

    h = admission
    matching = pin(h)
    first = await submit(h)
    before = database_state(h)
    assert (await submit(h)).id == first.id and database_state(h) == before
    loaded = await h.tasks.get(first.id)
    assert loaded.runtime_binding == h.binding
    assert loaded.runtime_binding.release_reference == matching.release_reference
    changed = RuntimeBinding.model_validate({**h.binding.model_dump(mode="json", exclude={"fingerprint"}),
        "release_reference": {**REFERENCE, "archive_sha256": "d" * 64}})
    with pytest.raises(AgentSubmissionConflict, match="binding"):
        await h.tasks.submit_v2(h.command.model_copy(update={"runtime_binding": changed}),
            h.payload, h.message, idempotency_key=h.key, model_budget=h.budget)
    assert database_state(h) == before
    selected = dispatcher(h, matching)
    await deliver(h, selected)
    assert h.executions == [first.id] and h.claimed_epochs == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("store,reference", [(None, "reference.json"), ("store", None)])
async def test_cli_requires_both_release_arguments_before_service_start(monkeypatch, store, reference):
    from src.cli import run_agent_v2_worker as cli

    def unexpected_settings():
        pytest.fail("Invalid CLI options must fail before settings or service startup")

    monkeypatch.setattr(cli, "get_settings", unexpected_settings)
    with pytest.raises(ValueError, match="Both release"):
        await cli._run(once=True, worker_id="invalid-options",
            release_store=Path(store) if store else None, release_reference=Path(reference) if reference else None)


@pytest.mark.asyncio
async def test_cli_rejects_unverified_release_before_lifespan_or_queue(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.cli import run_agent_v2_worker as cli

    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(
        deep_agents=SimpleNamespace(enabled=True, checkpoint_enabled=True)))
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(REFERENCE), encoding="utf-8")

    def reject(*args, **kwargs):
        raise ValueError("synthetic unverified release")

    def unexpected(*args, **kwargs):
        pytest.fail("Unverified release must not start lifespan or queue")

    monkeypatch.setattr(FrameworkWorkerCapabilities, "from_release", reject)
    monkeypatch.setattr(cli, "app", SimpleNamespace(router=SimpleNamespace(lifespan_context=unexpected)))
    monkeypatch.setattr(cli, "RedisTaskQueue", unexpected)
    with pytest.raises(ValueError, match="unverified release"):
        await cli._run(once=True, worker_id="unverified-release", release_store=tmp_path, release_reference=path)
