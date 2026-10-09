"""DeepSeek public-page physical recording probe.

This is deliberately gated because it needs outbound browser access.  It uses
the production CDP attach driver and recorder.js, but keeps the scope explicit:
the probe verifies browser capture and the finish receipt; the separate product
tests verify PostgreSQL/RustFS persistence and graph finalization.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.schemas.recording import RecorderEvent, RecordingDriverConfig, RecordingDriverKind


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DEEPSEEK_RECORDING") != "1",
    reason="set RUN_LIVE_DEEPSEEK_RECORDING=1 to run the outbound DeepSeek recording probe",
)


def _evidence_root() -> Path:
    configured = os.environ.get("RECORDING_EVIDENCE_DIR", "")
    root = Path(configured) if configured else (
        Path(__file__).resolve().parents[2] / "docs" / "upgrade_2026-10-03" /
        "p0-deepseek-recording-verified"
    )
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _drain(stream: Any, *, quiet_seconds: float = 0.35) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while True:
        try:
            value = await asyncio.wait_for(stream.__anext__(), timeout=quiet_seconds)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return events
        if isinstance(value, dict):
            events.append(value)


def test_live_deepseek_page_records_real_events_and_finish_receipt() -> None:
    """Use the production recorder against DeepSeek without submitting a prompt."""

    async def scenario() -> None:
        from playwright.async_api import async_playwright

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        evidence = _evidence_root()
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=True, args=[f"--remote-debugging-port={port}"]
        )
        driver = CdpAttachDriver(
            config=RecordingDriverConfig(
                kind=RecordingDriverKind.cdp_attach,
                endpoint=f"http://127.0.0.1:{port}",
            ),
            recording_id="deepseek-live-recording",
        )
        try:
            await driver.open("https://www.deepseek.com/", viewport=(1440, 960))
            await driver.inject_recorder()
            await driver.set_capture_enabled(True)
            stream = await driver.on_recorder_event()
            page = driver._page

            await page.get_by_text("和 DeepSeek 对话", exact=True).click()
            await page.wait_for_timeout(800)
            await page.locator("textarea").first.fill("录制探针")
            await page.wait_for_timeout(600)

            events = await _drain(stream)
            typed = [RecorderEvent.model_validate(event) for event in events]
            receipt = await driver.finish_capture()
            screenshot = await driver.capture_screenshot()

            assert any(event.type == "click" for event in typed)
            assert any(event.type == "fill" for event in typed)
            assert any(event.type == "navigate" for event in typed)
            assert receipt["protocol"] == "recording-scopes-v2"
            assert receipt["pending"] == 0
            assert receipt["frames_unconfirmed"] == 0
            assert receipt["transport_dropped"] == 0
            assert receipt["channel_dropped"] == 0
            assert receipt["emitted_count"] == len(typed)
            assert len(screenshot) > 100

            (evidence / "events.json").write_text(
                json.dumps(events, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            (evidence / "finish.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            (evidence / "screenshot.png").write_bytes(screenshot)
        finally:
            await driver.close()
            await browser.close()
            await playwright.stop()

    asyncio.run(scenario())
