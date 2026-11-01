"""External material reads precede, but never replace, atomic PG admission."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from tests.test_agent_task_dispatch import harness, payload
from tests.test_framework_model_admission import admission, counts, model_configuration
from tests.test_framework_release_binding import current_source_release, isolate_recovery_stores, prepare_submission


def loading_dispatcher(h, source_release, loader):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher

    dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
        framework_capabilities=FrameworkWorkerCapabilities.from_release(*source_release, deepagents_available=True),
        framework_model_calls=h.calls, framework_submission_loader=loader)
    h.app.state.agent_task_dispatcher = dispatcher
    h.sessions.set_agent_task_dispatcher(dispatcher)
    return dispatcher


def require_unlocked_session(h):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {h.settings.database.postgres_session_table} WHERE id=%s FOR UPDATE NOWAIT",
            (h.session_id,))
        assert cur.fetchone()["id"] == h.session_id


@pytest.mark.asyncio
async def test_async_material_loading_does_not_hold_the_pg_session_lock_or_write_admission(admission,
        isolate_recovery_stores, current_source_release):
    h = admission
    entered, proceed = asyncio.Event(), asyncio.Event()
    prepared = []

    async def load(session, request, command):
        await asyncio.to_thread(require_unlocked_session, h)
        assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
        entered.set()
        await proceed.wait()
        value = await asyncio.to_thread(prepare_submission, current_source_release, session_id=session.id,
            turn_id=command.turn_id, binding_id=command.task_id, created_at=command.created_at)
        prepared.append(value)
        return value

    dispatcher = loading_dispatcher(h, current_source_release, load)
    pending = asyncio.create_task(dispatcher.submit(h.session_id, payload(key="outside-pg-lock")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        await asyncio.to_thread(require_unlocked_session, h)
        assert counts(h)["tasks"] == 0
        proceed.set()
        result = await asyncio.wait_for(pending, timeout=60)
        assert result.task.runtime_binding == prepared[0].binding
        assert (await h.calls.snapshot(result.task.runtime_binding)).policy == prepared[0].model_budget
        assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
    finally:
        proceed.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_async_material_loading_race_commits_one_candidate_without_rebinding(admission,
        isolate_recovery_stores, current_source_release):
    h = admission
    both = asyncio.Event()
    arrivals, candidates = [], []

    async def load(session, request, command):
        arrivals.append(command.task_id)
        if len(arrivals) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=15)
        value = await asyncio.to_thread(prepare_submission, current_source_release, session_id=session.id,
            turn_id=command.turn_id, binding_id=command.task_id, created_at=command.created_at,
            max_calls=8 + arrivals.index(command.task_id))
        candidates.append(value)
        return value

    dispatcher = loading_dispatcher(h, current_source_release, load)
    first, second = await asyncio.gather(*(dispatcher.submit(h.session_id, payload(key="material-race")) for _ in range(2)))
    assert first.task.id == second.task.id and len(candidates) == 2
    selected, = [value for value in candidates if value.binding == first.task.runtime_binding]
    assert (await h.calls.snapshot(selected.binding)).policy == selected.model_budget
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)

    async def never(*args):
        pytest.fail("A committed API retry cannot read mutable model/release material again")

    loading_dispatcher(h, current_source_release, never)
    retry = await h.app.state.agent_task_dispatcher.submit(h.session_id, payload(key="material-race"))
    assert retry.task == first.task


@pytest.mark.asyncio
async def test_async_material_failure_is_safe_and_can_retry_without_partial_facts(admission,
        isolate_recovery_stores, current_source_release, caplog):
    h = admission

    async def unavailable(*args):
        raise ValueError("synthetic-private-material-loader-detail")

    loading_dispatcher(h, current_source_release, unavailable)
    body = payload(key="material-unavailable").model_dump(mode="json")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        failed = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        assert failed.status_code == 400 and failed.json()["detail"] == "Framework message material loading failed"
        assert "synthetic-private-material-loader-detail" not in failed.text + caplog.text
        assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)

        async def available(session, request, command):
            return await asyncio.to_thread(prepare_submission, current_source_release, session_id=session.id,
                turn_id=command.turn_id, binding_id=command.task_id, created_at=command.created_at)

        loading_dispatcher(h, current_source_release, available)
        accepted = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        conflict = await client.post(f"/sessions/{h.session_id}/messages", json={**body, "content": "different"})
    assert accepted.status_code == 202 and conflict.status_code == 409
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)


@pytest.mark.asyncio
async def test_async_material_failure_after_another_commit_reuses_the_original_receipt(admission,
        isolate_recovery_stores, current_source_release, caplog):
    h = admission
    original = []
    submission = payload(key="material-concurrent-commit")

    async def unavailable_after_commit(*args):
        original.append(await h.dispatcher.submit(h.session_id, submission))
        raise ValueError("synthetic-private-material-loader-detail")

    dispatcher = loading_dispatcher(h, current_source_release, unavailable_after_commit)
    result = await dispatcher.submit(h.session_id, submission)
    assert result.task == original[0].task and result.task.runtime_binding is None
    assert counts(h) == dict(tasks=1, owners=0, outbox=1, budgets=0, messages=1)
    assert "synthetic-private-material-loader-detail" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "during"])
async def test_disabling_new_admission_never_commits_a_loaded_candidate(admission,
        isolate_recovery_stores, current_source_release, when):
    h = admission
    reads = []
    if when == "before":
        h.settings.deep_agents.new_turns_enabled = False

    async def load(session, request, command):
        assert when == "during", "Disabled admission must not read external model configuration"
        reads.append(command.task_id)
        prepared = await asyncio.to_thread(prepare_submission, current_source_release, session_id=session.id,
            turn_id=command.turn_id, binding_id=command.task_id, created_at=command.created_at)
        h.settings.deep_agents.new_turns_enabled = False
        return prepared

    dispatcher = loading_dispatcher(h, current_source_release, load)
    result = await dispatcher.submit(h.session_id, payload(key="disable-material-loading"))
    assert result.task.runtime_binding is None and len(reads) == (1 if when == "during" else 0)
    assert counts(h) == dict(tasks=1, owners=0, outbox=1, budgets=0, messages=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["explicit", "auto", "session_preference"])
async def test_release_loader_uses_real_registry_selection_and_explicit_budget_without_caching(admission,
        isolate_recovery_stores, current_source_release, monkeypatch, selection):
    from src.application.orchestration.framework_submission_service import FrameworkReleaseSubmissionLoader
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.registry.agents import AgentRegistry
    from src.registry.models import ModelRegistry
    from src.schemas.agent_task import AgentTaskCommand

    h = admission
    primary, secondary = model_configuration(), model_configuration(key="secondary")
    active, reads = [primary], []
    store = MySQLModelConfigStore(h.settings)

    def list_active():
        require_unlocked_session(h)
        reads.append(len(reads))
        return list(active)

    monkeypatch.setattr(store, "list_active", list_active)
    loader = FrameworkReleaseSubmissionLoader(release_store=current_source_release[0],
        release_reference=current_source_release[1], agent_registry=AgentRegistry(), model_registry=ModelRegistry(store),
        mode_keys=frozenset({"code_review"}), checkpoint_namespace="loading:v2", max_calls=8, max_tokens=100_000,
        deadline_seconds=300)
    session = await h.store.get_session(h.session_id)
    session.preferred_model = primary.key if selection == "session_preference" else None
    submission = payload(mode_key="code_review", model_key={"explicit": primary.key, "auto": "auto",
        "session_preference": None}[selection])
    request = h.sessions._input_orchestrator_service.orchestrate(session, submission)
    command = AgentTaskCommand(task_id="loader-primary", session_id=session.id, turn_id=request.turn_id,
        request_id="loader-request", trace_id=request.trace_id)
    first = await loader(session, request, command)
    assert first.model_budget.root_configuration(first.binding.model_config_fingerprint).key == primary.key
    assert first.model_budget.max_calls == 8 and first.model_budget.max_tokens == 100_000
    assert first.binding.created_at == command.created_at
    assert first.model_budget.deadline_at == command.created_at + timedelta(seconds=300)
    before = first.model_budget.model_dump_json()
    primary.temperature = .9
    assert first.model_budget.model_dump_json() == before
    count = len(reads)
    active[:] = [secondary]
    request = request.model_copy(update={"model_key": "auto", "turn_id": "loader-secondary-turn"})
    command = command.model_copy(update={"task_id": "loader-secondary", "turn_id": request.turn_id})
    second = await loader(session, request, command)
    assert len(reads) > count
    assert second.model_budget.root_configuration(second.binding.model_config_fingerprint).key == secondary.key
    assert first.model_budget.model_dump_json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("original_route", ["legacy", "bound"])
async def test_existing_message_receipt_and_conflict_are_read_before_any_external_material(admission,
        isolate_recovery_stores, current_source_release, original_route):
    from tests.test_framework_release_binding import prepared_dispatcher

    h = admission
    original = h.dispatcher
    if original_route == "bound":
        original = prepared_dispatcher(h, current_source_release,
            lambda session, request, command: prepare_submission(current_source_release, session_id=session.id,
                turn_id=command.turn_id, binding_id=command.task_id, created_at=command.created_at))
    body = payload(key="existing-material-receipt").model_dump(mode="json")
    await original.submit(h.session_id, payload(key="existing-material-receipt"))

    async def never(*args):
        pytest.fail("Neither a committed retry nor a conflicting request can reload external material")

    loading_dispatcher(h, current_source_release, never)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        accepted = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        conflict = await client.post(f"/sessions/{h.session_id}/messages", json={**body, "content": "different"})
    assert accepted.status_code == 202 and conflict.status_code == 409
    bound = int(original_route == "bound")
    assert counts(h) == dict(tasks=1, owners=bound, outbox=1, budgets=bound, messages=1)


@pytest.mark.asyncio
async def test_async_material_submission_lost_ack_does_not_load_a_second_candidate(admission,
        isolate_recovery_stores, current_source_release, monkeypatch):
    h = admission
    candidates, committed = [], []

    async def load(session, request, command):
        value = await asyncio.to_thread(prepare_submission, current_source_release, session_id=session.id,
            turn_id=command.turn_id, binding_id=command.task_id, created_at=command.created_at)
        candidates.append(value)
        return value

    dispatcher = loading_dispatcher(h, current_source_release, load)
    original = h.tasks.submit_prepared_message

    async def lost_ack(*args, **kwargs):
        committed.append(await original(*args, **kwargs))
        raise ConnectionError("controlled material submission acknowledgement loss")

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "submit_prepared_message", lost_ack)
        with pytest.raises(ConnectionError, match="acknowledgement loss"):
            await dispatcher.submit(h.session_id, payload(key="material-lost-ack"))
    result = await dispatcher.submit(h.session_id, payload(key="material-lost-ack"))
    assert result.task == committed[0] and len(candidates) == 1
    assert (await h.calls.snapshot(result.task.runtime_binding)).policy == candidates[0].model_budget


@pytest.mark.asyncio
async def test_cancelled_material_read_cannot_admit_when_its_thread_finishes(admission,
        isolate_recovery_stores, current_source_release):
    import threading

    h = admission
    entered, proceed, finished = threading.Event(), threading.Event(), threading.Event()

    def read(session, request, command):
        try:
            require_unlocked_session(h)
            entered.set()
            assert proceed.wait(timeout=30)
            return prepare_submission(current_source_release, session_id=session.id, turn_id=command.turn_id,
                binding_id=command.task_id, created_at=command.created_at)
        finally:
            finished.set()

    async def load(*args):
        return await asyncio.to_thread(read, *args)

    dispatcher = loading_dispatcher(h, current_source_release, load)
    pending = asyncio.create_task(dispatcher.submit(h.session_id, payload(key="cancelled-material-read")))
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        proceed.set()
        assert await asyncio.to_thread(finished.wait, 60)
        assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
    finally:
        proceed.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 60)


def test_async_and_locked_preparers_cannot_be_configured_together():
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher

    with pytest.raises(ValueError, match="not both"):
        AgentTaskDispatcher(None, None, None, None,
            framework_submission_preparer=object(), framework_submission_loader=object())


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["missing-model", "unsupported-model", "inherited-preference"])
async def test_release_loader_checks_original_model_intent_before_admission(admission,
        isolate_recovery_stores, current_source_release, monkeypatch, requested, caplog):
    from src.application.orchestration.framework_submission_service import FrameworkReleaseSubmissionLoader
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.registry.agents import AgentRegistry
    from src.registry.models import ModelRegistry

    h = admission
    store = MySQLModelConfigStore(h.settings)
    active = [model_configuration(key="gpt-5.4"), model_configuration(key="unsupported-model")]
    monkeypatch.setattr(store, "list_active", lambda: list(active))
    loader = FrameworkReleaseSubmissionLoader(release_store=current_source_release[0],
        release_reference=current_source_release[1], agent_registry=AgentRegistry(), model_registry=ModelRegistry(store),
        mode_keys=frozenset({"code_review"}), checkpoint_namespace="loading:v2", max_calls=8, max_tokens=100_000,
        deadline_seconds=300)
    loading_dispatcher(h, current_source_release, loader)
    if requested == "inherited-preference":
        session = await h.store.get_session(h.session_id)
        session.preferred_model = "unsupported-model"
        await h.store.save_session(session)
    body = payload(key="explicit-fallback", mode_key="code_review", agent_key="code-review-agent",
        model_key=None if requested == "inherited-preference" else requested).model_dump(mode="json")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(f"/sessions/{h.session_id}/messages", json=body)
    if requested == "inherited-preference":
        assert response.status_code == 202
        task = await h.tasks.get(response.json()["task"]["id"])
        budget = (await h.calls.snapshot(task.runtime_binding)).policy
        assert budget.root_configuration(task.runtime_binding.model_config_fingerprint).key == "gpt-5.4"
        assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
    else:
        assert response.status_code == 400
        assert response.json()["detail"] == "Framework message material loading failed"
        assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
    assert requested not in response.text + caplog.text
