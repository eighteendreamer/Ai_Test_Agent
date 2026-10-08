from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path

import pytest

from test_recorder_session_service import _service, _launch_until_ready, _payload
from src.application.recorder.drivers import EmbeddedBridge
from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.schemas.recording import RecordingControlAction as Action, RecordingStatus, RecordingFinishReceipt, RecordingDriverConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["pending", "frame", "channel", "watermark", "middle", "db"])
async def test_known_delivery_gap_cannot_complete(monkeypatch, fault):
    service, store, graph, bridge, drivers = _service()
    recording_id = await _launch_until_ready(service, bridge, drivers)
    await service.control(recording_id, Action.start)
    driver = drivers[recording_id]
    finish = driver.finish_capture

    async def with_fault():
        receipt = await finish()
        if fault == "pending": receipt["pending"] = 1
        if fault == "frame": receipt["frames_unconfirmed"] = 1
        if fault == "channel": receipt["channel_dropped"] = 1
        if fault == "watermark": receipt.update(next_seq=2, emitted_count=2)
        if fault == "middle": receipt.update(next_seq=3, emitted_count=3)
        return receipt

    driver.finish_capture = with_fault
    if fault == "db":
        store.append_failures = 3
        monkeypatch.setattr("src.application.recorder.recorder_session_service._DB_RETRY_DELAYS", (0, 0, 0))
    driver.publish_event(_payload(0))
    if fault == "middle": driver.publish_event(_payload(2))
    try:
        result = await service.control(recording_id, Action.stop)
        assert result.status == RecordingStatus.failed
        assert result.finalize_metrics["failure_reason"] == "finish_unconfirmed"
        assert not graph.finalize_calls
        with pytest.raises(ValueError, match="illegal transition"):
            await service.control(recording_id, Action.start)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_http_waiter_does_not_cancel_stop():
    service, store, graph, bridge, drivers = _service()
    recording_id = await _launch_until_ready(service, bridge, drivers)
    await service.control(recording_id, Action.start)
    driver = drivers[recording_id]
    finish, released = driver.finish_capture, asyncio.Event()

    async def delayed_finish():
        await released.wait()
        return await finish()

    driver.finish_capture = delayed_finish
    waiting = asyncio.create_task(service.control(recording_id, Action.stop))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError): await waiting
    driver.publish_event(_payload(0))
    released.set()
    try:
        result = await service._runtimes[recording_id].stop_task
        assert result.status == RecordingStatus.completed
        assert [event.seq for event in await store.get_events(recording_id)] == [0]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_embedded_finish_receipt_identity_and_replay():
    bridge = EmbeddedBridge()
    driver = bridge.attach("owned-finish")
    bridge.register_session("owned-finish", capabilities={})
    finishing = asyncio.create_task(driver.finish_capture())
    commands = await bridge.poll_commands("owned-finish", wait_seconds=1)
    request_id = commands[0]["payload"]["request_id"]
    receipt = RecordingFinishReceipt(request_id=request_id, next_seq=0, emitted_count=0, pending=0, frames_unconfirmed=0)
    with pytest.raises(ValueError, match="request mismatch"):
        bridge.acknowledge_finish("owned-finish", receipt.model_copy(update={"request_id": "foreign"}))
    bridge.acknowledge_finish("owned-finish", receipt)
    bridge.acknowledge_finish("owned-finish", receipt)
    with pytest.raises(ValueError, match="cannot change"):
        bridge.acknowledge_finish("owned-finish", receipt.model_copy(update={"next_seq": 1}))
    assert (await finishing)["published_seqs"] == []
    await driver.close()
    with pytest.raises(ValueError, match="unavailable"):
        bridge.acknowledge_finish("owned-finish", receipt)


@pytest.mark.asyncio
async def test_actual_route_rejects_receipt_for_wrong_request(caplog):
    import httpx
    from fastapi import FastAPI
    from src.api.routes.recordings import router

    bridge = EmbeddedBridge()
    driver = bridge.attach("route-finish")
    bridge.register_session("route-finish", capabilities={})
    app = FastAPI()
    app.state.embedded_bridge = bridge
    app.include_router(router, prefix="/api/v1")
    finishing = asyncio.create_task(driver.finish_capture())
    command = (await bridge.poll_commands("route-finish", wait_seconds=1))[0]
    receipt = RecordingFinishReceipt(request_id="foreign", next_seq=0, emitted_count=0, pending=0, frames_unconfirmed=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post("/api/v1/recordings/route-finish/capture-finished", json=receipt.model_dump())
        assert rejected.status_code == 409 and not finishing.done()
        assert "recording_finish_receipt_rejected" in caplog.text
        accepted = await client.post("/api/v1/recordings/route-finish/capture-finished",
            json=receipt.model_copy(update={"request_id": command["payload"]["request_id"]}).model_dump())
        assert accepted.status_code == 200
        assert (await finishing)["pending"] == 0
    await driver.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CDP_RECORDING") != "1", reason="requires actual Chromium CDP")
async def test_actual_cdp_finish_delivers_immediate_input(tmp_path):
    from playwright.async_api import async_playwright

    evidence = Path(os.getenv("RECORDING_FINISH_EVIDENCE", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=False)
    source = evidence / "fixture.html"
    source.write_text('<!doctype html><meta charset="utf-8"><title>结束实测</title><input id="tail">', encoding="utf-8")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    async with async_playwright() as playwright:
        outer = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        driver = CdpAttachDriver(config=RecordingDriverConfig(kind="cdp-attach", endpoint=f"http://127.0.0.1:{port}"), recording_id="actual-finish")
        try:
            await driver.open(source.resolve().as_uri(), viewport=(1440, 960))
            await driver.inject_recorder()
            await driver.set_capture_enabled(True)
            await driver._page.fill("#tail", "最后一步实际输入")
            receipt = await asyncio.wait_for(driver.finish_capture(), timeout=5)
            stream = await driver.on_recorder_event()
            events = [event async for event in stream]
            (evidence / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            (evidence / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
            assert receipt["pending"] == 0 and receipt["frames_unconfirmed"] == 0
            assert len(events) == 1 and events[0]["value"] == "最后一步实际输入"
            assert receipt["next_seq"] - 1 == events[0]["seq"]
        finally:
            await driver.close()
            await outer.close()
