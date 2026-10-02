from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.application.orchestration.input_orchestrator_service import (
    InputOrchestratorService,
)
from src.application.sessions.session_service import SessionService
from src.registry.modes import ModeRegistry
from src.schemas.session import (
    PendingInputQueueEntry,
    SendMessageRequest,
    SessionStatus,
)

pytest_plugins = ["tests.test_session_event_delivery"]


@pytest.mark.asyncio
async def test_service_shutdown_settles_pending_handoff_without_losing_input(stores):
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    session.metadata["pending_input_queue"] = [PendingInputQueueEntry(
        id="shutdown-queue-entry", created_at=datetime.now(UTC), busy_status="running",
        queue_behavior="enqueue_if_busy", interrupt_policy="wait_for_active_turn",
        payload=SendMessageRequest(content="continue queued work"),
        metadata={"turn_id": "shutdown-turn"},
    ).model_dump(mode="python")]
    await store.save_session(session)
    handoff_started = asyncio.Event()
    handoff_settled = asyncio.Event()
    handoff_release = asyncio.Event()

    class PendingDispatcher:
        async def submit(self, submitted_session_id, payload, *, execution_request=None):
            assert submitted_session_id == session_id
            assert payload.content == "continue queued work"
            handoff_started.set()
            try:
                await handoff_release.wait()
                return SimpleNamespace(task=SimpleNamespace(id="shutdown-handoff-task"))
            finally:
                handoff_settled.set()

    modes = ModeRegistry()
    service = SessionService(
        store, InputOrchestratorService(modes), object(), modes, turn_reaper_interval_seconds=60,
    )
    service.set_agent_task_dispatcher(PendingDispatcher())
    await service.start_turn_reaper()
    try:
        await asyncio.wait_for(handoff_started.wait(), timeout=2)
        drain = service._queue_drain_tasks[session_id]
        await service.close()
        assert drain.done(), "Session shutdown returned with its pending handoff still running"
        assert handoff_settled.is_set()
        persisted = await reader.get_session(session_id)
        assert [item.id for item in service._get_pending_input_queue(persisted)] == ["shutdown-queue-entry"]
        assert not service._queue_drain_tasks
        service._maybe_schedule_pending_input_drain(session_id)
        assert not service._queue_drain_tasks
        await service.close()
    finally:
        await service.stop_turn_reaper()
        remaining = list(service._queue_drain_tasks.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_waits_for_all_owned_background_task_cleanup():
    service = SessionService(object(), object(), object(), object())
    cleanup_release = asyncio.Event()
    cleanup_started = [asyncio.Event() for _ in range(3)]
    started = [asyncio.Event() for _ in range(3)]
    cleaned = []

    async def background(index):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started[index].set()
            await cleanup_release.wait()
            cleaned.append(index)

    registries = [service._queue_drain_tasks, service._approval_forward_tasks, service._approval_resume_tasks]
    tasks = []
    for index, registry in enumerate(registries):
        task = asyncio.create_task(background(index))
        registry[str(index)] = task
        tasks.append(task)
    shutdown = None
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=2)
        shutdown = asyncio.create_task(service.close())
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleanup_started)), timeout=2)
        assert not shutdown.done()
        cleanup_release.set()
        await asyncio.wait_for(shutdown, timeout=2)
        assert sorted(cleaned) == [0, 1, 2]
        assert all(task.done() for task in tasks)
        assert all(not registry for registry in registries)
    finally:
        cleanup_release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if shutdown is not None:
            await shutdown


@pytest.mark.asyncio
async def test_busy_internal_submissions_keep_both_queued_inputs(stores, monkeypatch) -> None:
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    assert session is not None
    session.status = SessionStatus.running
    await store.save_session(session)

    original_get = store.get_session
    read_count = 0
    both_read = asyncio.Event()

    async def concurrent_get(requested_id):
        nonlocal read_count
        result = await original_get(requested_id)
        read_count += 1
        if read_count <= 2:
            if read_count == 2:
                both_read.set()
            await both_read.wait()
        return result

    monkeypatch.setattr(store, "get_session", concurrent_get)
    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), object(), modes)
    async with service._session_locks[session_id]:
        responses = await asyncio.gather(
            service.send_message(session_id, SendMessageRequest(content="queued-a", submit_mode="queued")),
            service.send_message(session_id, SendMessageRequest(content="queued-b", submit_mode="queued")),
        )
    assert len(responses) == 2
    persisted = await reader.get_session(session_id)
    assert persisted is not None
    payloads = persisted.metadata.get("pending_input_queue", [])
    assert {item["payload"]["content"] for item in payloads} == {"queued-a", "queued-b"}
    assert [event.type for event in await reader.list_events(session_id)] == ["input.queued", "input.queued"]


@pytest.mark.asyncio
async def test_internal_queue_keeps_head_and_followers_if_orchestration_fails(stores) -> None:
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    assert session is not None
    session.metadata["pending_input_queue"] = [
        PendingInputQueueEntry(
            id=f"queued-{index}",
            created_at=datetime.now(UTC),
            busy_status="running",
            queue_behavior="enqueue_if_busy",
            interrupt_policy="wait_for_active_turn",
            payload=SendMessageRequest(content=f"queued-{index}"),
            metadata={"turn_id": f"turn-{index}"},
        ).model_dump(mode="python")
        for index in (1, 2)
    ]
    await store.save_session(session)

    class FailingOrchestrator:
        async def orchestrate_async(self, _session, _payload):
            raise RuntimeError("injected queue orchestration failure")

    modes = ModeRegistry()
    service = SessionService(store, FailingOrchestrator(), object(), modes)

    await service._drain_pending_inputs(session_id)

    persisted = await reader.get_session(session_id)
    assert persisted is not None
    assert [item.id for item in service._get_pending_input_queue(persisted)] == [
        "queued-1",
        "queued-2",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("enqueue_after_read", [False, True])
async def test_empty_pending_queue_cleanup_preserves_concurrent_session_status(
    stores, monkeypatch, enqueue_after_read
) -> None:
    store, reader, session_id, _ = stores
    original_get = store.get_session
    injected_worker_transition = False

    async def get_with_concurrent_worker_transition(requested_id):
        nonlocal injected_worker_transition
        snapshot = deepcopy(await original_get(requested_id))
        if not injected_worker_transition:
            injected_worker_transition = True
            current = await original_get(requested_id)
            assert current is not None
            current.status = SessionStatus.running
            if enqueue_after_read:
                current.metadata["pending_input_queue"] = [
                    PendingInputQueueEntry(
                        id="concurrently-queued-entry",
                        created_at=datetime.now(UTC),
                        busy_status="running",
                        queue_behavior="enqueue_if_busy",
                        interrupt_policy="wait_for_active_turn",
                        payload=SendMessageRequest(content="concurrent queued input"),
                    ).model_dump(mode="python")
                ]
                current.metadata["control"] = {"queued_input_count": 1}
            await store.save_session(current)
        return snapshot

    monkeypatch.setattr(store, "get_session", get_with_concurrent_worker_transition)
    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), object(), modes)

    await service._drain_pending_inputs(session_id)

    persisted = await reader.get_session(session_id)
    assert persisted is not None
    assert persisted.status == SessionStatus.running
    assert persisted.metadata["control"]["queued_input_count"] == int(enqueue_after_read)


@pytest.mark.asyncio
async def test_busy_internal_submission_uses_durable_dispatcher(stores) -> None:
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    assert session is not None
    session.status = SessionStatus.running
    await store.save_session(session)

    accepted = object()
    submissions = []

    class DurableDispatcher:
        async def submit(self, submitted_session_id, payload, *, execution_request=None):
            submissions.append((submitted_session_id, payload))
            return accepted

    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), object(), modes)
    service.set_agent_task_dispatcher(DurableDispatcher())
    payload = SendMessageRequest(
        content="worker result notification",
        submit_mode="queued",
        context={"task_id": "worker-1", "parent_turn_id": "turn-1"},
        metadata={
            "message_kind": "task_notification",
            "task_id": "worker-1",
            "worker_status": "completed",
        },
    )

    async with service._session_locks[session_id]:
        first = await service.send_message(session_id, payload)
        second = await service.send_message(session_id, payload)

    assert first is accepted
    assert second is accepted
    assert len(submissions) == 2
    assert all(item[0] == session_id for item in submissions)
    queued_payloads = [item[1] for item in submissions]
    assert all(item.submit_mode == "queued" for item in queued_payloads)
    assert queued_payloads[0].metadata["client_message_id"] == queued_payloads[1].metadata[
        "client_message_id"
    ]
    persisted = await reader.get_session(session_id)
    assert persisted is not None
    assert persisted.metadata.get("pending_input_queue", []) == []


@pytest.mark.asyncio
async def test_legacy_pending_input_handoff_reuses_idempotency_key_after_lost_ack(stores) -> None:
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    assert session is not None
    session.metadata["pending_input_queue"] = [
        PendingInputQueueEntry(
            id="legacy-queue-entry",
            created_at=datetime.now(UTC),
            busy_status="running",
            queue_behavior="enqueue_if_busy",
            interrupt_policy="wait_for_active_turn",
            payload=SendMessageRequest(content="legacy queued input"),
            metadata={"turn_id": "legacy-turn"},
        ).model_dump(mode="python")
    ]
    await store.save_session(session)

    submissions = []
    durable_tasks = {}

    class CommitThenLoseAckDispatcher:
        async def submit(self, submitted_session_id, payload, *, execution_request=None):
            key = payload.metadata["client_message_id"]
            submissions.append(key)
            durable_tasks.setdefault(
                key, SimpleNamespace(task=SimpleNamespace(id=f"task-{key}"))
            )
            if len(submissions) == 1:
                raise RuntimeError("injected lost acknowledgement after commit")
            return durable_tasks[key]

    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), object(), modes)
    service.set_agent_task_dispatcher(CommitThenLoseAckDispatcher())

    await service._drain_pending_inputs(session_id)
    still_queued = await reader.get_session(session_id)
    assert still_queued is not None
    assert [item.id for item in service._get_pending_input_queue(still_queued)] == [
        "legacy-queue-entry"
    ]

    await service._drain_pending_inputs(session_id)
    recovered = await reader.get_session(session_id)
    assert recovered is not None
    assert recovered.metadata.get("pending_input_queue", []) == []
    assert submissions == ["legacy-queue-entry", "legacy-queue-entry"]
    assert len(durable_tasks) == 1


@pytest.mark.asyncio
async def test_startup_reaper_hands_off_legacy_pending_input_queue(stores) -> None:
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    assert session is not None
    session.metadata["pending_input_queue"] = [
        PendingInputQueueEntry(
            id="legacy-startup-entry",
            created_at=datetime.now(UTC),
            busy_status="running",
            queue_behavior="enqueue_if_busy",
            interrupt_policy="wait_for_active_turn",
            payload=SendMessageRequest(content="recover after restart"),
            metadata={"turn_id": "legacy-startup-turn"},
        ).model_dump(mode="python")
    ]
    await store.save_session(session)

    submitted = asyncio.Event()
    submissions = []

    class DurableDispatcher:
        async def submit(self, submitted_session_id, payload, *, execution_request=None):
            submissions.append((submitted_session_id, payload.content))
            submitted.set()
            return SimpleNamespace(task=SimpleNamespace(id="recovered-task"))

    modes = ModeRegistry()
    service = SessionService(
        store,
        InputOrchestratorService(modes),
        object(),
        modes,
        turn_reaper_interval_seconds=60,
    )
    service.set_agent_task_dispatcher(DurableDispatcher())

    await service.start_turn_reaper()
    try:
        await asyncio.wait_for(submitted.wait(), timeout=2)
        async with asyncio.timeout(2):
            while True:
                recovered = await reader.get_session(session_id)
                assert recovered is not None
                if recovered.metadata.get("pending_input_queue", []) == []:
                    break
                await asyncio.sleep(0.01)
        assert submissions == [(session_id, "recover after restart")]
        assert [event.type for event in await reader.list_events(session_id)] == [
            "input.dequeued"
        ]
    finally:
        await service.stop_turn_reaper()
