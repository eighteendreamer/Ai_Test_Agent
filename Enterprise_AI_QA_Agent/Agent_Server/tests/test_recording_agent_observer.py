"""Actual TCP streaming evidence must not turn into a buffered fake response."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

from test_recording_agent_production import observed_servers, verify_owned_approval


def test_actual_saved_approval_matches_agent_registry_and_owned_run():
    path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-recording-agent-required-production"
    captured = json.loads((path / "postgres-execution.json").read_bytes())
    approvals = captured["tables"]["postgres_approval_table"]["records"]
    run = json.loads((path / "run-after-worker.json").read_bytes())
    origin = json.loads((path / "recording.json").read_bytes())["entry_url"]
    assert approvals
    for approval in approvals:
        verify_owned_approval(approval, project_id=run["run"]["project_id"], run_id=run["run"]["id"],
            item_id=run["items"][0]["id"], origin=origin)
        with pytest.raises(AssertionError):
            verify_owned_approval(approval, project_id="foreign", run_id=run["run"]["id"],
                item_id=run["items"][0]["id"], origin=origin)


@pytest.mark.asyncio
async def test_original_chunks_reach_client_before_provider_finishes(tmp_path):
    release, finished = Event(), Event()
    first, last = b'data: {"part":"first"}\n\n', b'data: [DONE]\n\n'

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            release.wait(10)
            self.wfile.write(last)
            self.wfile.flush()
            finished.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = SimpleNamespace(api_base_url=f"http://127.0.0.1:{server.server_port}/v1", extra_headers={})
        async with observed_servers(tmp_path, model) as (_, proxy, sends, _):
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", proxy + "/chat/completions", json={"stream": True, "messages": []}) as response:
                    response.raise_for_status()
                    chunks = response.aiter_raw()
                    received = await asyncio.wait_for(anext(chunks), timeout=3)
                    assert received == first and not finished.is_set()
                    release.set()
                    received += b"".join([chunk async for chunk in chunks])
                    assert received == first + last
            async with asyncio.timeout(3):
                while "response_sha256" not in sends[0]:
                    await asyncio.sleep(.01)
            assert len(sends) == 1 and sends[0]["status"] == 200
            assert (tmp_path / "model-001-response.bin").read_bytes() == first + last
            assert json.loads((tmp_path / "model-001-request.bin").read_bytes())["stream"] is True
    finally:
        release.set()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join, 5)
