from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.schemas.recording import RecordingDriverConfig, RecordingDriverKind

EVIDENCE = Path(__file__).resolve().parent
ENDPOINT = os.environ["QA_CDP_ENDPOINT"]

async def drain(stream: Any, quiet_seconds: float = 0.5) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while True:
        try:
            event = await asyncio.wait_for(stream.__anext__(), timeout=quiet_seconds)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return events
        if isinstance(event, dict):
            events.append(event)

async def main() -> None:
    driver = CdpAttachDriver(
        config=RecordingDriverConfig(kind=RecordingDriverKind.cdp_attach, endpoint=ENDPOINT),
        recording_id="deepseek-agent-browser-cdp-20261021",
    )
    try:
        await driver.open("https://www.deepseek.com/", viewport=(1440, 900))
        await driver.inject_recorder()
        await driver.set_capture_enabled(True)
        stream = await driver.on_recorder_event()
        print("READY: CDP recorder attached; perform the page operations, then press Enter.", flush=True)
        input()
        events = await drain(stream)
        receipt = await driver.finish_capture()
        screenshot = await driver.capture_screenshot()
        (EVIDENCE / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (EVIDENCE / "finish.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (EVIDENCE / "recorded.png").write_bytes(screenshot)
        print(json.dumps({
            "event_count": len(events),
            "event_types": {kind: sum(1 for item in events if item.get("type") == kind) for kind in sorted({str(item.get("type")) for item in events})},
            "receipt": receipt,
            "screenshot_bytes": len(screenshot),
        }, ensure_ascii=False, indent=2, default=str), flush=True)
    finally:
        await driver.close()

asyncio.run(main())
