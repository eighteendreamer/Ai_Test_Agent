from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
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


def prepare_submission(current_source_release, *, session_id="prepared-session", turn_id="prepared-turn", **changes):
    from datetime import datetime, timedelta, timezone
    from src.runtime.framework_submission_preparation import prepare_release_submission
    from tests.test_framework_model_admission import model_configuration

    store, reference = current_source_release
    now = datetime.now(timezone.utc)
    values = dict(binding_id="prepared-binding", session_id=session_id, turn_id=turn_id,
        checkpoint_namespace="prepared:v2", root_model_key="primary",
        model_configurations={"primary": model_configuration()}, max_calls=8, max_tokens=100_000,
        created_at=now, deadline_at=now + timedelta(minutes=5))
    values.update(changes)
    return prepare_release_submission(store, reference, **values)


def test_submission_preparation_uses_real_release_and_model_materials(current_source_release, caplog):
    import hashlib
    import zipfile
    from src.schemas.framework_transcript import SUMMARY_CONTEXT_RUNTIME_VERSION
    from src.runtime.framework_release_archive import require_installed_release, verify_release

    store, reference = current_source_release
    require_installed_release(Path(__file__).resolve().parents[2], store, reference)
    prepared = prepare_submission(current_source_release)
    selected, budget = prepared.binding, prepared.model_budget
    manifest = verify_release(store, reference)
    assert selected.release_reference == reference and selected.runtime_kind == "deepagents"
    assert selected.runtime_version == SUMMARY_CONTEXT_RUNTIME_VERSION and selected.thread_id == selected.turn_id
    assert selected.prompt_version == selected.skill_version == "release:" + reference.archive_sha256
    assert selected.tool_schema_version == "sha256:" + manifest.files["release/tools.json"].sha256
    assert selected.dependency_lock_sha256 == manifest.files["uv.lock"].sha256
    assert budget.schema_version == 2 and budget.model_configurations["primary"].fingerprint == selected.model_config_fingerprint
    with zipfile.ZipFile(store / (reference.archive_sha256 + ".zip")) as archive:
        assert hashlib.sha256(archive.read("release/tools.json")).hexdigest() in selected.tool_schema_version
        assert archive.read("Agent_Server/src/application/prompting/prompt_assembly_service.py")
        assert any(name.startswith("Agent_Server/src/SKILLS/") and name.endswith("SKILL.md") for name in archive.namelist())
    assert "synthetic-private-material" not in budget.model_dump_json() and "synthetic-private-material" not in caplog.text


@pytest.mark.parametrize("change", ["missing_root", "mapping_identity", "inactive", "embedding_only",
    "invalid_profile", "zero_limit", "deadline", "naive_creation"])
def test_submission_preparation_rejects_invalid_models_and_limits_before_archive_access(tmp_path, monkeypatch,
        caplog, change):
    from datetime import datetime, timedelta, timezone
    from src.runtime import framework_submission_preparation as module
    from tests.test_framework_model_admission import model_configuration

    def unexpected(*args):
        pytest.fail("Invalid model or budget must fail before the archive probe")

    monkeypatch.setattr(module, "require_installed_release", unexpected)
    changes = {}
    if change == "missing_root":
        changes["root_model_key"] = "private-missing-model"
    elif change == "mapping_identity":
        changes["model_configurations"] = {"other": model_configuration()}
    elif change == "inactive":
        changes["model_configurations"] = {"primary": model_configuration(is_active=False)}
    elif change == "embedding_only":
        changes["model_configurations"] = {"primary": model_configuration(applications=["embedding_retrieval"])}
    elif change == "invalid_profile":
        changes["model_configurations"] = {"primary": model_configuration().model_copy(update={"request_profile": {
            "future_field": "private-format-diagnostic"}})}
    elif change == "zero_limit":
        changes["max_calls"] = 0
    elif change == "deadline":
        changes["deadline_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    else:
        changes["created_at"] = datetime(2030, 1, 1)
    with pytest.raises(ValueError, match="Framework release submission preparation failed") as caught:
        prepare_submission((tmp_path, REFERENCE), **changes)
    assert caught.value.__suppress_context__
    records = [row for row in caplog.records if row.message == "framework_submission_preparation_failed"]
    assert len(records) == 1 and records[0].status == "models" and records[0].exc_info is None
    assert records[0].error_type in {"KeyError", "ValueError", "ValidationError", "TypeError", "PydanticSerializationError"}
    assert all(value not in caplog.text + str(caught.value) for value in (
        "synthetic-private-material", "private-missing-model", "private-format-diagnostic", "models.invalid"))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["missing", "corrupt", "different_source"])
def test_submission_preparation_never_falls_back_from_missing_or_wrong_archive(tmp_path, release, caplog, change):
    store, reference = release
    if change == "different_source":
        from src.runtime.framework_release_archive import archive_release

        # This source really predates the preparation port; future HEAD must
        # not silently turn the negative sample into the current release.
        store = tmp_path / "stale-source"
        reference = archive_release(Path(__file__).resolve().parents[2], store,
            revision="b8a5f2604cfa8ccee53b29a41f515810e7211458")
    else:
        store = tmp_path / "private-store"
        store.mkdir()
        if change == "corrupt":
            (store / (reference.archive_sha256 + ".zip")).write_bytes(b"synthetic corrupt archive")
    with pytest.raises(ValueError, match="Framework release submission preparation failed"):
        prepare_submission((store, reference))
    records = [row for row in caplog.records if row.message == "framework_submission_preparation_failed"]
    assert len(records) == 1 and records[0].status == "release" and records[0].exc_info is None
    assert all(value not in caplog.text for value in ("private-store", "synthetic-private-material", "models.invalid"))


def test_submission_preparation_captures_all_models_before_the_installation_probe(current_source_release, monkeypatch):
    from src.runtime import framework_submission_preparation as module
    from src.schemas.model_config import ModelInvocationParameters
    from tests.test_framework_model_admission import model_configuration

    primary = model_configuration()
    summary = model_configuration(key="summary", transport="openai_responses", temperature=.5)
    expected = {config.key: ModelInvocationParameters.from_configuration(config) for config in (primary, summary)}
    original = module.require_installed_release

    def verify(*args):
        for config in (primary, summary):
            config.temperature = .75
            config.model_id = "changed-during-probe"
        return original(*args)

    monkeypatch.setattr(module, "require_installed_release", verify)
    prepared = prepare_submission(current_source_release, model_configurations={"primary": primary, "summary": summary})
    assert prepared.model_budget.model_configurations == expected
    assert prepared.binding.model_config_fingerprint == expected["primary"].fingerprint
    assert set(prepared.model_budget.model_config_fingerprints) == {"primary", "summary"}


def test_submission_preparation_does_not_emit_private_serializer_diagnostics(tmp_path, caplog):
    import warnings
    from tests.test_framework_model_admission import model_configuration

    configuration = model_configuration().model_copy(update={"request_profile": {
        "future_field": "synthetic-private-material"}})
    with warnings.catch_warnings(record=True) as diagnostics:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="Framework release submission preparation failed"):
            prepare_submission((tmp_path, REFERENCE), model_configurations={"primary": configuration})
    assert diagnostics == []
    assert "synthetic-private-material" not in caplog.text


def test_previous_source_budget_reader_preserves_v1_and_defers_snapshots(tmp_path):
    from datetime import datetime, timezone
    from src.runtime.framework_release_archive import archive_release, restore_release
    from src.schemas.framework_model_call import FrameworkModelBudget
    from src.schemas.model_config import ModelInvocationParameters
    from tests.test_framework_model_admission import model_configuration

    store = tmp_path / "previous-budget-store"
    reference = archive_release(Path(__file__).resolve().parents[2], store,
        revision="b8a5f2604cfa8ccee53b29a41f515810e7211458")
    restored = tmp_path / "previous-budget-reader"
    restore_release(store, reference, restored)
    snapshot = ModelInvocationParameters.from_configuration(model_configuration())
    budget = FrameworkModelBudget(max_calls=8, max_tokens=100_000,
        deadline_at=datetime(2030, 1, 1, tzinfo=timezone.utc), model_config_fingerprints={"primary": snapshot.fingerprint})
    v2 = FrameworkModelBudget(**{**budget.model_dump(), "schema_version": 2,
        "model_configurations": {"primary": snapshot}})
    path = tmp_path / "budget-wires.json"
    path.write_text(json.dumps([budget.model_dump(mode="json"), v2.model_dump(mode="json")]), encoding="utf-8")
    probe = '''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'Agent_Server'))
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.framework_model_call import FrameworkModelBudget
from src.runtime.task_deferred import TaskDeferred
old, new = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
caps = FrameworkWorkerCapabilities('a' * 64, True)
caps.require_model_ledger_format('budget', old)
assert FrameworkModelBudget.model_validate(old).model_dump(mode='json') == old
for wire in (new, {**new, 'schema_version': 1}):
    try:
        caps.require_model_ledger_format('budget', wire)
    except TaskDeferred as exc:
        assert str(exc) == 'framework_model_budget_format_unavailable'
    else:
        raise AssertionError('Old budget reader accepted unsupported snapshot fields')
loaded = [pathlib.Path(item.__file__).resolve() for name, item in sys.modules.items()
    if name.startswith('src.') and getattr(item, '__file__', None)]
assert loaded and all(path.is_relative_to(root) for path in loaded)
print('previous_source_v1_unchanged_v2_snapshot_deferred')
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(restored), str(path)],
        cwd=restored, capture_output=True, timeout=120)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert b"previous_source_v1_unchanged_v2_snapshot_deferred" in result.stdout


def test_restored_current_source_prepares_a_release_submission_in_a_fresh_process(tmp_path, current_source_release):
    from src.runtime.framework_release_archive import restore_release

    store, reference = current_source_release
    restored = tmp_path / "current-preparation-source"
    restore_release(store, reference, restored)
    path = tmp_path / "reference.json"
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    probe = '''
import json, pathlib, sys
from datetime import datetime, timedelta, timezone
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'Agent_Server'))
from src.runtime.framework_submission_preparation import prepare_release_submission
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.model_config import ModelConfigRecord
reference = json.loads(pathlib.Path(sys.argv[3]).read_bytes())
now = datetime.now(timezone.utc)
config = ModelConfigRecord(key='primary', name='Isolated configuration', provider='openai',
    transport='openai_chat_completions', model_id='fixture-model', api_base_url='https://models.invalid/v1', is_active=True)
prepared = prepare_release_submission(pathlib.Path(sys.argv[2]), reference, binding_id='restored-binding',
    session_id='restored-session', turn_id='restored-turn', checkpoint_namespace='restored:v2',
    root_model_key='primary', model_configurations={'primary': config}, max_calls=8, max_tokens=100_000,
    created_at=now, deadline_at=now+timedelta(minutes=5))
caps = FrameworkWorkerCapabilities.from_release(pathlib.Path(sys.argv[2]), reference, deepagents_available=True)
caps.require(prepared.binding, operation='message')
caps.require_model_ledger_format('budget', prepared.model_budget.model_dump(mode='json'))
assert prepared.binding.prompt_version == prepared.binding.skill_version == 'release:'+reference['archive_sha256']
assert prepared.binding.model_config_fingerprint == prepared.model_budget.model_configurations['primary'].fingerprint
assert prepared.model_budget.schema_version == 2
loaded = [pathlib.Path(item.__file__).resolve() for name, item in sys.modules.items()
    if name.startswith('src.') and getattr(item, '__file__', None)]
assert loaded and all(path.is_relative_to(root) for path in loaded)
assert 'src.runtime.framework_submission_preparation' in sys.modules
print('restored_source_submission_prepared_and_readable')
'''
    environment = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(restored), str(store), str(path)],
        cwd=restored, env=environment, capture_output=True, timeout=120)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert b"restored_source_submission_prepared_and_readable" in result.stdout


@pytest.mark.asyncio
async def test_prepared_release_binding_and_semantic_configuration_commit_in_the_original_admission(admission,
        isolate_recovery_stores, current_source_release):
    from tests.test_framework_model_admission import counts
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    prepared = prepare_submission(current_source_release, session_id=h.session_id, turn_id=h.binding.turn_id,
        binding_id=h.binding.binding_id)
    h.binding, h.budget = prepared.binding, prepared.model_budget
    h.command = make_command(h, h.binding)
    task = await submit(h)
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
    capabilities = FrameworkWorkerCapabilities.from_release(*current_source_release, deepagents_available=True)
    capabilities.require(h.binding, operation="message")
    claimed = await h.tasks.claim(task.id, worker_id="prepared-binding-reader", expected_binding=h.binding,
        framework_capabilities=capabilities, check_model_ledger=True)
    assert claimed.runtime_owner.binding == h.binding and claimed.attempt_no == 1
    assert (await h.calls.snapshot(h.binding)).policy == prepared.model_budget
    await h.tasks.finish(claimed, status="cancelled", error_code="prepared_binding_contract_probe")
    assert (await h.tasks.get(task.id)).runtime_binding == h.binding and h.runtime.calls == []


def prepared_dispatcher(h, source_release, prepare, *, loader=None):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher

    dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
        framework_capabilities=FrameworkWorkerCapabilities.from_release(*source_release, deepagents_available=True),
        framework_model_calls=h.calls, framework_submission_preparer=prepare, framework_submission_loader=loader)
    h.app.state.agent_task_dispatcher = dispatcher
    h.sessions.set_agent_task_dispatcher(dispatcher)
    return dispatcher


@pytest.mark.asyncio
async def test_http_prepared_submission_serializes_first_binding_and_reuses_it_after_route_changes(
        admission, isolate_recovery_stores, current_source_release):
    import asyncio
    import httpx
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_model_admission import counts

    h = admission
    prepared = []

    def prepare(session, request, command):
        assert session.id == command.session_id == request.session_id
        assert request.turn_id == command.turn_id and request.model_key == "primary"
        value = prepare_submission(current_source_release, session_id=command.session_id, turn_id=command.turn_id,
            binding_id="http-" + command.task_id, created_at=command.created_at)
        prepared.append(value)
        return value

    dispatcher = prepared_dispatcher(h, current_source_release, prepare)
    body = payload("prepared HTTP input", key="prepared-http", model_key="primary").model_dump(mode="json")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        responses = await asyncio.gather(*(client.post(f"/sessions/{h.session_id}/messages", json=body)
            for _ in range(8)))
        assert [response.status_code for response in responses] == [202] * 8
        assert len({response.json()["task"]["id"] for response in responses}) == 1
        task_id = responses[0].json()["task"]["id"]
        # Same API idempotency identity survives removing the new admission
        # route; retrying must not create an unbound second Turn.
        h.app.state.agent_task_dispatcher = h.dispatcher
        retry = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        assert retry.status_code == 202 and retry.json()["task"]["id"] == task_id
        h.app.state.agent_task_dispatcher = dispatcher
        conflict = await client.post(f"/sessions/{h.session_id}/messages", json={**body, "content": "different"})
        assert conflict.status_code == 409
    assert len(prepared) == 1 and h.runtime.calls == []
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
    task = await h.tasks.get(task_id)
    assert task.runtime_binding == prepared[0].binding and task.turn_id == prepared[0].binding.turn_id
    assert (await h.calls.snapshot(task.runtime_binding)).policy == prepared[0].model_budget
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT stream,payload FROM {h.settings.database.postgres_task_outbox_table}")
        row = cur.fetchone()
        cur.execute(f"SELECT input_hash,input_payload FROM {h.settings.database.postgres_agent_task_table}")
        original = cur.fetchone()
    assert original["input_hash"] == h.tasks._v2_input_hash(original["input_payload"], task.runtime_binding,
        prepared[0].model_budget)
    assert row["stream"] == h.settings.orchestration.agent_task_v2_stream
    assert row["payload"]["schema_version"] == 2
    assert row["payload"]["runtime_binding"] == task.runtime_binding.model_dump(mode="json")
    session = await h.store.get_session(h.session_id)
    assert session.messages[0].metadata["turn_id"] == task.turn_id
    assert session.messages[0].metadata["context_eligible"] is False


@pytest.mark.asyncio
async def test_http_legacy_retry_after_installing_preparer_does_not_rebind(admission,
        isolate_recovery_stores, current_source_release):
    import httpx
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_model_admission import counts

    h = admission
    body = payload(key="legacy-first").model_dump(mode="json")

    def unexpected(*args):
        pytest.fail("An accepted legacy request must not acquire a new runtime binding")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        first = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        prepared_dispatcher(h, current_source_release, unexpected)
        retry = await client.post(f"/sessions/{h.session_id}/messages", json=body)
    assert first.status_code == retry.status_code == 202
    assert first.json()["task"]["id"] == retry.json()["task"]["id"]
    assert counts(h) == dict(tasks=1, owners=0, outbox=1, budgets=0, messages=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["preparation", "identity", "budget", "stage", "event"])
async def test_http_prepared_submission_rolls_back_every_admission_fact_and_can_retry(admission,
        isolate_recovery_stores, current_source_release, monkeypatch, caplog, failure):
    import httpx
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_model_admission import counts
    from src.runtime import framework_message_stages
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = admission
    fail = True

    def prepare(session, request, command):
        if failure == "preparation" and fail:
            raise ValueError("synthetic-private-preparer-input")
        selected_session = "foreign-session" if failure == "identity" and fail else session.id
        return prepare_submission(current_source_release, session_id=selected_session, turn_id=command.turn_id,
            binding_id="rollback-" + command.task_id, created_at=command.created_at)

    def after(original):
        def injected(*args, **kwargs):
            result = original(*args, **kwargs)
            if fail:
                raise ValueError("controlled admission failure")
            return result
        return injected

    if failure == "budget":
        monkeypatch.setattr(PostgresFrameworkModelCalls, "freeze_submission", after(PostgresFrameworkModelCalls.freeze_submission))
    elif failure == "stage":
        monkeypatch.setattr(framework_message_stages, "admit_message_stage", after(framework_message_stages.admit_message_stage))
    elif failure == "event":
        monkeypatch.setattr(h.tasks, "_event", after(h.tasks._event))
    prepared_dispatcher(h, current_source_release, prepare)
    body = payload(key="rollback-prepared").model_dump(mode="json")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        assert response.status_code in {400, 409}
        assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
        assert await h.store.list_events(h.session_id) == []
        assert "synthetic-private-preparer-input" not in response.text + caplog.text
        fail = False
        retry = await client.post(f"/sessions/{h.session_id}/messages", json=body)
    assert retry.status_code == 202
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)


@pytest.mark.asyncio
async def test_explicit_preparer_can_keep_an_ineligible_mode_on_the_original_legacy_stream(admission,
        isolate_recovery_stores, current_source_release):
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_model_admission import counts

    h = admission
    dispatcher = prepared_dispatcher(h, current_source_release, lambda *args: None)
    receipt = await dispatcher.submit(h.session_id, payload(key="excluded-mode"))
    assert receipt.task.runtime_binding is None
    assert counts(h) == dict(tasks=1, owners=0, outbox=1, budgets=0, messages=1)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT stream,payload FROM {h.settings.database.postgres_task_outbox_table}")
        row = cur.fetchone()
    assert row["stream"] == h.settings.orchestration.agent_task_stream and row["payload"]["schema_version"] == 1


@pytest.mark.asyncio
async def test_disabled_new_turn_admission_never_invokes_the_installed_preparer(admission,
        isolate_recovery_stores, current_source_release):
    from tests.test_agent_task_dispatch import payload

    h = admission

    def unexpected(*args):
        pytest.fail("Disabling new Turns must prevent preparation without removing bound execution capability")

    dispatcher = prepared_dispatcher(h, current_source_release, unexpected)
    h.settings.deep_agents.new_turns_enabled = False
    receipt = await dispatcher.submit(h.session_id, payload(key="disabled-preparer"))
    assert receipt.task.runtime_binding is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", ["absent", "release", "budget", "runtime"])
async def test_prepared_http_writer_cannot_bypass_explicit_compatible_reader_capabilities(admission,
        isolate_recovery_stores, current_source_release, unsupported):
    import httpx
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_model_admission import counts

    h = admission

    def prepare(session, request, command):
        return prepare_submission(current_source_release, session_id=session.id, turn_id=command.turn_id,
            binding_id="capabilities-" + command.task_id, created_at=command.created_at)

    dispatcher = prepared_dispatcher(h, current_source_release, prepare)
    capabilities = dispatcher._framework_capabilities
    dispatcher.set_framework_capabilities(None if unsupported == "absent" else replace(capabilities, **{
        "release_reference": None} if unsupported == "release" else {
        "model_budget_max_version": 1} if unsupported == "budget" else {"deepagents_available": False}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(f"/sessions/{h.session_id}/messages", json=payload().model_dump(mode="json"))
    assert response.status_code == 400 and response.json()["detail"] == "Framework message preparation failed"
    assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)


@pytest.mark.asyncio
async def test_prepared_http_retry_after_commit_loses_ack_does_not_prepare_again(admission,
        isolate_recovery_stores, current_source_release, monkeypatch):
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_model_admission import counts

    h = admission
    prepared = []

    def prepare(session, request, command):
        assert not prepared, "A retry after commit must not re-read mutable release/model inputs"
        value = prepare_submission(current_source_release, session_id=session.id, turn_id=command.turn_id,
            binding_id="lost-ack-" + command.task_id, created_at=command.created_at)
        prepared.append(value)
        return value

    dispatcher = prepared_dispatcher(h, current_source_release, prepare)
    original = h.tasks.submit_prepared_message
    committed = []

    async def lost_ack(*args, **kwargs):
        value = await original(*args, **kwargs)
        committed.append(value)
        raise RuntimeError("controlled post-commit lost acknowledgement")

    monkeypatch.setattr(h.tasks, "submit_prepared_message", lost_ack)
    with pytest.raises(RuntimeError, match="controlled post-commit"):
        await dispatcher.submit(h.session_id, payload(key="lost-prepared-ack"))
    monkeypatch.setattr(h.tasks, "submit_prepared_message", original)
    retry = await dispatcher.submit(h.session_id, payload(key="lost-prepared-ack"))
    assert retry.task == committed[0] and len(prepared) == 1
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)


@pytest.mark.asyncio
async def test_prepared_api_idempotency_is_not_an_execution_digest_bypass(admission,
        isolate_recovery_stores, current_source_release):
    from src.infrastructure.redis_task_queue import QueuedTask
    from tests.test_agent_task_dispatch import payload

    h = admission

    def prepare(session, request, command):
        return prepare_submission(current_source_release, session_id=session.id, turn_id=command.turn_id,
            binding_id="digest-" + command.task_id, created_at=command.created_at)

    dispatcher = prepared_dispatcher(h, current_source_release, prepare)
    receipt = await dispatcher.submit(h.session_id, payload(key="execution-digest"))
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {h.settings.database.postgres_task_outbox_table}")
        wire = cur.fetchone()["payload"]
        # Controlled corruption of this fixture's original proof. Returning an
        # idempotent receipt is not permission to execute an altered task.
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET input_hash=%s WHERE id=%s",
            ("0" * 64, receipt.task.id))
    await dispatcher.handle_v2(QueuedTask("corrupt-prepared", wire), worker_id="digest-check")
    task = await h.tasks.get(receipt.task.id)
    assert task.status == "recovery_required" and h.runtime.calls == []
    assert (await h.calls.snapshot(task.runtime_binding)).reserved_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["explicit", "auto", "session_preference"])
@pytest.mark.parametrize("preparation", ["captured", "external"])
async def test_http_prepared_release_runs_full_context_graph_and_commits_original_assets(admission,
        isolate_recovery_stores, current_source_release, tmp_path, monkeypatch, selection, preparation):
    import httpx
    import shutil
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.runtime.runtime_service import RuntimeService
    from src.application.runtime.tool_job_service import ToolJobService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService
    from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.framework_message_stages import FrameworkMessageStages
    from src.runtime.framework_approval_projection import FrameworkApprovalProjection
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.application.skills.skill_runtime_service import SkillRuntimeService
    from src.registry.modes import ModeRegistry
    from src.registry.skills import SkillRegistry
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from tests.test_agent_task_dispatch import payload
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_framework_turn_context import assembly_components, build_assembly

    h = admission
    h.settings.database.postgres_tool_artifact_table = h.prefix + "_artifacts"
    job_store = PostgresToolJobStore(h.settings)
    jobs = ToolJobService(job_store)
    provider = DeepAgentCheckpointProvider(h.settings)
    parts = assembly_components(tmp_path)
    # HTTP orchestration adds the real mode's defaults. The small context-unit
    # fixture has only tdd-review; supply the actual additional source bodies
    # instead of weakening the production missing-Skill error or routing.
    source_skills = SkillRegistry().skills_root
    for key in ModeRegistry().get("code_review").default_skill_keys:
        if key != "tdd-review":
            shutil.copytree(source_skills / key, parts.skill_registry.skills_root / key)
    parts.skill_registry = SkillRegistry(skills_root=parts.skill_registry.skills_root)
    parts.skill_runtime_service = SkillRuntimeService(parts.skill_registry)
    frozen_inputs = []
    original_record = FrameworkMessageStages.record_graph_input

    async def record(stages, owner, asset):
        result = await original_record(stages, owner, asset)
        frozen_inputs.append(parse_graph_input_asset(asset))
        # The actual model request must use the already assembled original,
        # not reopen mutable Skills after its graph-input receipt was saved.
        parts.skill_file.write_text("# Changed Skill\nMUTATED_SKILL_BODY", encoding="utf-8")
        return result

    monkeypatch.setattr(FrameworkMessageStages, "record_graph_input", record)
    try:
        await job_store.initialize()
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url, key="fake", is_active=True)
            replacement = _config("openai_chat_completions", url, key="new-default", is_active=True)
            reads = []

            class Registry:
                def get_runtime_config(self, key):
                    reads.append(key)
                    if key == config.key:
                        return config
                    if key == replacement.key:
                        return replacement
                    raise KeyError(key)

                def get_default_runtime_config(self):
                    reads.append("default")
                    return replacement

            active_configurations = [config]
            external_registry = None
            configuration_reads = []
            if preparation == "external":
                from threading import Event
                from src.infrastructure.model_config_store import MySQLModelConfigStore
                from src.registry.models import ModelRegistry
                from tests.test_framework_submission_loading import require_unlocked_session

                model_store = MySQLModelConfigStore(h.settings)
                material_loading = Event()

                def list_active():
                    # The admission read must not own this row. Runtime reads
                    # coexist with legitimate Session heartbeat transactions.
                    if material_loading.is_set():
                        require_unlocked_session(h)
                        configuration_reads.append("admission")
                    else:
                        configuration_reads.append("runtime")
                    return list(active_configurations)

                monkeypatch.setattr(model_store, "list_active", list_active)
                external_registry = ModelRegistry(model_store)
                original_get = external_registry.get_runtime_config
                original_default = external_registry.get_default_runtime_config

                def get_model(key):
                    reads.append(key)
                    return original_get(key)

                def get_default():
                    reads.append("default")
                    return original_default()

                monkeypatch.setattr(external_registry, "get_runtime_config", get_model)
                monkeypatch.setattr(external_registry, "get_default_runtime_config", get_default)
                parts.model_registry = external_registry
            models = ModelRuntimeService(external_registry or Registry(), h.settings)
            owned = DeepAgentRuntimeAdapter(model_resolver=models.resolve_langchain_chat_model,
                checkpointer_factory=provider.open, model_call_policy=FrameworkModelCallPolicy(
                    calls=h.calls, model_config_resolver=models.get_model_config))
            h.sessions._runtime_service = RuntimeService(graph=None, model_runtime_service=models,
                tool_runtime_service=ToolRuntimeService(tool_job_service=jobs), tool_registry=parts.tool_registry,
                runtime_control=RuntimeControlRegistry(), tool_job_service=jobs,
                framework_approval_projection=FrameworkApprovalProjection(h.settings, sessions=h.store, jobs=jobs),
                deep_agent_enabled=True, deep_agent_new_turns_enabled=False, deep_agent_bound_runtime_adapter=owned,
                turn_context_assembly=build_assembly(parts), permission_service=parts.permission_service,
                deep_agent_governed_tools_enabled=True)
            prepared = []

            def prepare(session, request, command):
                selected = prepare_submission(current_source_release, session_id=session.id, turn_id=command.turn_id,
                    binding_id="full-" + command.task_id, created_at=command.created_at,
                    root_model_key="fake", model_configurations={"fake": config})
                prepared.append(selected)
                return selected

            if preparation == "external":
                from src.application.orchestration.framework_submission_service import FrameworkReleaseSubmissionLoader
                from src.core.config import DeepAgentsConfig

                reference_path = tmp_path / "configured-release-reference.json"
                reference_path.write_text(current_source_release[1].model_dump_json(), encoding="utf-8")
                h.settings.deep_agents = DeepAgentsConfig(**{**h.settings.deep_agents.model_dump(),
                    "enabled": True, "checkpoint_enabled": True, "release_store": current_source_release[0],
                    "release_reference": reference_path, "message_submission_policy": {
                        "checkpoint_namespace": "prepared:v2", "max_calls": 8, "max_tokens": 100_000,
                        "deadline_seconds": 300}})
                capabilities = FrameworkWorkerCapabilities.from_configuration(h.settings.deep_agents)
                source_loader = FrameworkReleaseSubmissionLoader.from_configuration(h.settings.deep_agents, capabilities,
                    agent_registry=parts.agent_registry, model_registry=external_registry)

                async def load(session, request, command):
                    material_loading.set()
                    try:
                        value = await source_loader(session, request, command)
                        prepared.append(value)
                        return value
                    finally:
                        material_loading.clear()

                dispatcher = prepared_dispatcher(h, current_source_release, None, loader=load)
            else:
                dispatcher = prepared_dispatcher(h, current_source_release, prepare)
            requested_key = "auto" if selection == "auto" else "fake" if selection == "explicit" else None
            if selection == "session_preference":
                session = await h.store.get_session(h.session_id)
                session.preferred_model = config.key
                await h.store.save_session(session)
            body = payload("ORIGINAL_CURRENT_INPUT", key="full-context-http", model_key=requested_key, mode_key="code_review",
                agent_key="context-reviewer", skill_keys=["tdd-review"], context={"requested_tool_keys": ["inspect_value"]},
                attachments=[{"name": "context.txt", "text_excerpt": "ATTACHMENT_CONTEXT"}]).model_dump(mode="json")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
                response = await client.post(f"/sessions/{h.session_id}/messages", json=body)
            assert response.status_code == 202 and requests == []
            task_id = response.json()["task"]["id"]
            if preparation == "external":
                assert configuration_reads and set(configuration_reads) == {"admission"}
                assert not material_loading.is_set()
                # A runtime config read does not participate in PG admission.
                # A legitimate concurrent heartbeat may own this row; prove
                # our config-store fixture does not add a runtime lock probe.
                with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT id FROM {h.settings.database.postgres_session_table} WHERE id=%s FOR UPDATE",
                        (h.session_id,))
                    assert external_registry.get_runtime_config(config.key).key == config.key
                assert configuration_reads[-1] == "runtime"
            active_configurations[:] = [replacement, config]
            if selection == "session_preference":
                session = await h.store.get_session(h.session_id)
                session.preferred_model = replacement.key
                await h.store.save_session(session)
            queue = RedisTaskQueue(h.settings.database.redis_url,
                stream=h.settings.orchestration.agent_task_v2_stream, group="prepared-http", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            errors = []

            async def handle(item):
                try:
                    await dispatcher.handle_v2(item, worker_id="full-context-http-worker")
                except Exception as exc:
                    errors.append(exc)
                    raise

            worker = RedisTaskWorker(queue, consumer="full-context-http-worker", handler=handle, block_ms=0)
            assert await worker.run_once() == 1
            if errors:
                raise errors[0]
            task = await h.tasks.get(task_id)
            assert task.status == "completed" and task.runtime_binding == prepared[0].binding
            assert len(requests) == len(frozen_inputs) == len(prepared) == 1
            graph = frozen_inputs[0]
            assert graph.runtime_preparation.identity.binding == task.runtime_binding
            assert graph.model_key == "fake" and graph.runtime_preparation.initial_state["selected_model_key"] == "fake"
            assert "default" not in reads and replacement.key not in reads and "auto" not in reads
            stage = FrameworkMessageStages(h.tasks).get(task_id, h.session_id)
            assert stage.phase == "projected"
            assert parse_graph_input_asset(stage.source_manifest["graph_input_asset"]) == graph
            assert stage.execution_request["model_key"] == "fake"
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT input_payload FROM {h.settings.database.postgres_agent_task_table} WHERE id=%s", (task_id,))
                assert cur.fetchone()["input_payload"] == body
            snapshot = await h.calls.snapshot(task.runtime_binding)
            assert snapshot.policy == prepared[0].model_budget and snapshot.actual_tokens == 10
            assert snapshot.reserved_calls == 1 and snapshot.calls[0].phase == "completed"
            wire = json.dumps(requests[0]["body"], ensure_ascii=False)
            for marker in ("FROZEN_SKILL_BODY", "OBSERVATION_CONTEXT", "MEMORY_CONTEXT", "MCP_CONTEXT",
                "ATTACHMENT_CONTEXT", "ORIGINAL_CURRENT_INPUT"):
                assert wire.count(marker) == 1, marker
            assert "MUTATED_SKILL_BODY" not in wire and "code review pilot" not in wire
            assert [kind for kind, _ in parts.reads] == ["observations", "memory", "mcp", "settings"]
            session = await h.store.get_session(h.session_id)
            assert [message.content for message in session.messages] == ["ORIGINAL_CURRENT_INPUT", "fixture answer"]
            # Duplicate delivery may only ACK the existing committed result.
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT payload FROM {h.settings.database.postgres_task_outbox_table}")
                command_wire = cur.fetchone()["payload"]
            await dispatcher.handle_v2(QueuedTask("duplicate-prepared", command_wire), worker_id="duplicate")
            assert len(requests) == 1 and await h.calls.snapshot(task.runtime_binding) == snapshot
    finally:
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")
            cur.execute(f"DROP TABLE IF EXISTS {h.settings.database.postgres_tool_artifact_table}")
            cur.execute(f"DROP TABLE IF EXISTS {h.settings.database.postgres_tool_job_table}")


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


def test_previous_committed_reader_preserves_old_wire_and_defers_new_reference(tmp_path):
    from src.runtime.framework_release_archive import archive_release, restore_release

    # HEAD advances to the new reader after its commit. A historical contract
    # must keep using the actual old implementation, not whichever HEAD is now.
    store = tmp_path / "previous-store"
    reference = archive_release(Path(__file__).resolve().parents[2], store,
        revision="b6646d89c83bd4cc9e45fc2b8bdca4499a407039")
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
        cwd=destination, capture_output=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert b"old_wire_unchanged_new_reference_deferred" in result.stdout


@pytest.fixture(scope="module")
def current_source_release(tmp_path_factory):
    from src.runtime import framework_release_archive as module

    tmp_path = tmp_path_factory.mktemp("current-source-release")
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
    # The capability identity hashes raw lock bytes. Preserve this test's
    # actual installed lock even under a host-wide core.autocrlf setting;
    # only the isolated fixture repository gets these attributes.
    (snapshot / ".gitattributes").write_text("uv.lock -text\n", encoding="utf-8")
    # This is an isolated test commit of the current implementation, not a
    # published commit in the user's repository or an independent venv install.
    environment = {**os.environ, "GIT_AUTHOR_NAME": "Release Test", "GIT_COMMITTER_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid", "GIT_COMMITTER_EMAIL": "release@example.invalid"}
    for args in (("init",), ("add", "."), ("commit", "-m", "trusted current source snapshot")):
        subprocess.run(["git", *args], cwd=snapshot, env=environment, capture_output=True, check=True, timeout=120)
    store = tmp_path / "snapshot-store"
    reference = module.archive_release(snapshot, store)
    return store, reference


def test_current_source_release_archives_the_actual_stage_material_schemas(tmp_path, current_source_release):
    import zipfile
    from src.runtime import framework_message_stages
    from src.runtime.framework_release_archive import require_installed_release, restore_release

    store, reference = current_source_release
    with zipfile.ZipFile(store / (reference.archive_sha256 + ".zip")) as archive:
        formats = json.loads(archive.read("release/formats.json"))
    assert formats["message_stage_materials"] == framework_message_stages.message_stage_material_schemas()
    destination = tmp_path / "installed-with-material-inventory"
    restore_release(store, reference, destination)
    assert require_installed_release(destination, store, reference).source_revision == reference.source_revision


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_installation_rejects_self_consistent_but_stale_material_definitions(tmp_path, current_source_release, change):
    import hashlib
    from src.runtime import framework_release_archive as module
    from tests.test_framework_release_archive import rewrite_release

    def alter(files):
        formats = json.loads(files["release/formats.json"])
        if change == "missing":
            del formats["message_stage_materials"]["runtime_result_asset"]
        else:
            formats["message_stage_materials"]["runtime_result_asset"]["properties"]["schema_version"]["const"] = 99
        content = module._json(formats)
        files["release/formats.json"] = content
        manifest = json.loads(files[module.MANIFEST])
        manifest["files"]["release/formats.json"] = {
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
        files[module.MANIFEST] = module._json(manifest)

    store, reference = rewrite_release(tmp_path, current_source_release, alter)
    # Byte integrity is not proof that definitions match the installed readers.
    module.verify_release(store, reference)
    destination = tmp_path / "installed-with-stale-inventory"
    module.restore_release(store, reference, destination)
    with pytest.raises(ValueError, match="environment or definitions"):
        module.require_installed_release(destination, store, reference)


def test_previous_source_without_material_inventory_keeps_its_original_release_definitions(tmp_path):
    import zipfile
    from src.runtime.framework_release_archive import archive_release, require_installed_release, restore_release

    # Freeze the implementation before the catalogue export. Do not treat a
    # future HEAD as an old reader or add definitions to an existing object.
    store = tmp_path / "previous-material-store"
    reference = archive_release(Path(__file__).resolve().parents[2], store,
        revision="908c11ba9d2f089f0d860c9c9634464176c5ee0d")
    path = store / (reference.archive_sha256 + ".zip")
    original = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        assert "message_stage_materials" not in json.loads(archive.read("release/formats.json"))
    destination = tmp_path / "installed-before-material-inventory"
    restore_release(store, reference, destination)
    assert require_installed_release(destination, store, reference).source_revision == reference.source_revision
    assert path.read_bytes() == original


def test_current_source_snapshot_resolves_a_pinned_binding_in_a_fresh_process(tmp_path, current_source_release):
    from src.runtime import framework_release_archive as module

    store, reference = current_source_release
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
