from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_case_planning import environment
from test_recorder_session_service import _service, _launch_until_ready, _payload
from src.api.routes.recordings import router
from src.application.recorder.drivers import EmbeddedBridge
from src.application.recorder.drivers.base import CaptureCheckpoint
from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_store import PostgresRecordingStore, RecordingEventWriteRejected
from src.schemas.project import ProjectCreateRequest
from src.schemas.recording import RecorderEvent, RecordingControlAction as Action, RecordingDriverConfig, RecordingFinishReceipt, RecordingSession, RecordingStatus


@pytest.mark.asyncio
async def test_pause_waiter_cancelled_preserves_checkpoint_and_serializes_controls():
    service, store, graph, bridge, drivers = _service()
    recording_id = await _launch_until_ready(service, bridge, drivers)
    await service.control(recording_id, Action.start)
    driver = drivers[recording_id]
    original, release = driver.pause_capture, asyncio.Event()

    async def delayed():
        await release.wait()
        return await original()

    driver.pause_capture = delayed
    waiting = asyncio.create_task(service.control(recording_id, Action.pause))
    await asyncio.sleep(0)
    with pytest.raises(ValueError, match="control in progress"):
        await service.control(recording_id, Action.resume)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError): await waiting
    driver.publish_event(_payload(0))
    release.set()
    try:
        paused = await service._runtimes[recording_id].pause_task
        assert paused.status == RecordingStatus.paused
        assert paused.finalize_metrics["pause_receipts"][0]["pg_verified"]
        assert [event.seq for event in await store.get_events(recording_id)] == [0]
        await service.control(recording_id, Action.resume)
        driver.publish_event(_payload(1))
        completed = await service.control(recording_id, Action.stop)
        assert completed.status == RecordingStatus.completed
        assert len(completed.finalize_metrics["pause_receipts"]) == 1
        assert [event.seq for event in await store.get_events(recording_id)] == [0, 1]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_receipt_rejects_stop_operation_and_keeps_stream_open():
    bridge = EmbeddedBridge()
    driver = bridge.attach("owned-pause")
    bridge.register_session("owned-pause", capabilities={})

    async def consume():
        stream = await driver.on_recorder_event()
        async for event in stream:
            if isinstance(event, CaptureCheckpoint): event.completed.set_result(None)

    consumer = asyncio.create_task(consume())
    pausing = asyncio.create_task(driver.pause_capture())
    command = (await bridge.poll_commands("owned-pause", wait_seconds=1))[0]
    receipt = RecordingFinishReceipt(request_id=command["payload"]["request_id"], next_seq=0,
        emitted_count=0, pending=0, frames_unconfirmed=0)
    try:
        with pytest.raises(ValueError, match="operation mismatch"):
            bridge.acknowledge_finish("owned-pause", receipt)
        assert not pausing.done()
        bridge.acknowledge_finish("owned-pause", receipt.model_copy(update={"operation": "pause"}))
        assert (await pausing)["operation"] == "pause"
        assert not consumer.done()
    finally:
        pausing.cancel()
        await driver.close()
        consumer.cancel()
        await asyncio.gather(pausing, consumer, return_exceptions=True)


@pytest.mark.asyncio
async def test_pause_timeout_uses_frozen_policy_and_fails():
    service, store, graph, bridge, drivers = _service()
    recording_id = await _launch_until_ready(service, bridge, drivers)
    await service.control(recording_id, Action.start)
    service._runtimes[recording_id].session.metadata["finish_settings"]["payload"]["timeout_seconds"] = 1

    async def no_receipt():
        await asyncio.Event().wait()

    drivers[recording_id].pause_capture = no_receipt
    try:
        result = await service.control(recording_id, Action.pause)
        assert result.status == RecordingStatus.failed
        assert result.finalize_metrics["failure_reason"] == "pause_timeout"
        assert not graph.finalize_calls
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_db_failure_cannot_return_paused(monkeypatch):
    service, store, graph, bridge, drivers = _service()
    recording_id = await _launch_until_ready(service, bridge, drivers)
    await service.control(recording_id, Action.start)
    store.append_failures = 3
    monkeypatch.setattr("src.application.recorder.recorder_session_service._DB_RETRY_DELAYS", (0, 0, 0))
    drivers[recording_id].publish_event(_payload(0))
    try:
        result = await service.control(recording_id, Action.pause)
        assert result.status == RecordingStatus.failed
        assert result.finalize_metrics["failure_reason"] == "pause_unconfirmed"
        assert not graph.finalize_calls
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_PG") != "1", reason="requires actual isolated PG/MySQL/RustFS")
@pytest.mark.parametrize("terminal", ["completed", "failed", "discarded"])
async def test_actual_pg_serializes_append_and_terminal_transition(tmp_path, terminal):
    root = Path(__file__).resolve().parents[2]
    evidence = Path(os.getenv("RECORDING_BOUNDARY_PG_EVIDENCE", str(tmp_path / "evidence"))) / terminal
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="boundary-" + uuid4().hex[:12], name="录制终态并发验收"))
        suffix = uuid4().hex[:12]
        tables = ["up_be_" + suffix, "up_bs_" + suffix]
        env.settings.database.postgres_recording_event_table, env.settings.database.postgres_recording_table = tables
        with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
            for table in tables:
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
        store = PostgresRecordingStore(env.settings)
        await store.initialize()
        session = await store.create_session(RecordingSession(project_id=project.id, entry_url="https://example.com", status=RecordingStatus.active))
        # Reuse original captured bytes as inputs for storage concurrency, not browser operations.
        original_path = root / "docs/upgrade_2026-10-03/p0-recording-drain-product-final/settled/pg-events-final.json"
        source = json.loads(original_path.read_text(encoding="utf-8"))[0]
        (evidence / "source.json").write_bytes(original_path.read_bytes())
        samples = [RecorderEvent.model_validate({**source, "seq": i}) for i in range(8)]
        try:
            acks = await asyncio.gather(*(store.append_events(session.id, [event]) for event in samples))
            assert sum(ack.accepted for ack in acks) == 8
            assert (await store.get_session(session.id)).step_count == 8
            late_event = samples[0].model_copy(update={"seq": 8})
            waiter = None
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT status FROM {tables[1]} WHERE id=%s FOR UPDATE", (session.id,))
                assert cursor.fetchone()["status"] == "active"
                waiter = asyncio.create_task(store.append_events(session.id, [late_event]))

                def blocked_count():
                    with postgres_connect(env.settings) as observing, observing.cursor() as query:
                        query.execute("SELECT count(*) AS count FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s", ("%" + tables[1] + "%",))
                        return query.fetchone()["count"]

                async with asyncio.timeout(5):
                    while not await asyncio.to_thread(blocked_count):
                        assert not waiter.done()
                        await asyncio.sleep(.05)
                save("lock-observed.json", {"wait_event_type": "Lock", "blocked_append": True, "terminal": terminal, "recording_id": session.id})
                cursor.execute(f"UPDATE {tables[1]} SET status=%s, ended_at=NOW(), updated_at=NOW() WHERE id=%s", (terminal, session.id))
            with pytest.raises(RecordingEventWriteRejected, match="recording_terminal"):
                await waiter
            before = (await store.get_session(session.id)).model_dump(mode="json")
            before_events = [event.model_dump(mode="json") for event in await store.get_events(session.id)]
            app = FastAPI()
            app.state.recording_store, app.state.embedded_bridge = store, EmbeddedBridge()
            app.include_router(router, prefix="/api/v1")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                replay = await client.post(f"/api/v1/recordings/{session.id}/events:batch", json={"events": before_events})
                altered = await client.post(f"/api/v1/recordings/{session.id}/events:batch", json={"events": [late_event.model_dump(mode="json")]})
                missing = await client.post("/api/v1/recordings/missing-owned-fixture/events:batch", json={"events": before_events})
            after = (await store.get_session(session.id)).model_dump(mode="json")
            after_events = [event.model_dump(mode="json") for event in await store.get_events(session.id)]
            save("terminal-write.json", {"before": before, "after": after, "before_events": before_events,
                "after_events": after_events, "replay_status": replay.status_code, "new_event_status": altered.status_code,
                "missing_status": missing.status_code, "parallel_accepted": 8})
            assert replay.status_code == altered.status_code == 409 and missing.status_code == 404
            assert before == after and before_events == after_events and len(after_events) == 8
        finally:
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f'DROP TABLE "{table}"')


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CDP_RECORDING") != "1", reason="requires actual Chromium CDP")
async def test_actual_cdp_pause_then_resume_retains_inputs(tmp_path):
    from playwright.async_api import async_playwright

    evidence = Path(os.getenv("RECORDING_BOUNDARY_CDP_EVIDENCE", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=False)
    source = evidence / "fixture.html"
    source.write_text('<!doctype html><meta charset="utf-8"><input id="tail">', encoding="utf-8")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    async with async_playwright() as playwright:
        outer = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        driver = CdpAttachDriver(config=RecordingDriverConfig(kind="cdp-attach", endpoint=f"http://127.0.0.1:{port}"), recording_id="actual-boundary")
        events = []

        async def consume():
            stream = await driver.on_recorder_event()
            async for event in stream:
                if isinstance(event, CaptureCheckpoint): event.completed.set_result(None)
                else: events.append(event)

        consumer = None
        try:
            await driver.open(source.resolve().as_uri(), viewport=(1440, 960))
            await driver.inject_recorder()
            consumer = asyncio.create_task(consume())
            await driver.set_capture_enabled(True)
            await driver._page.fill("#tail", "暂停前最后输入")
            paused = await asyncio.wait_for(driver.pause_capture(), timeout=5)
            assert events[0]["value"] == "暂停前最后输入"
            await driver.set_capture_enabled(True)
            await driver._page.fill("#tail", "继续后的输入")
            finished = await asyncio.wait_for(driver.finish_capture(), timeout=5)
            await consumer
            (evidence / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
            (evidence / "receipts.json").write_text(json.dumps({"pause": paused, "stop": finished}, indent=2), encoding="utf-8")
            assert [event["value"] for event in events if event["type"] == "fill"] == ["暂停前最后输入", "继续后的输入"]
        finally:
            if consumer and not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await driver.close()
            await outer.close()
