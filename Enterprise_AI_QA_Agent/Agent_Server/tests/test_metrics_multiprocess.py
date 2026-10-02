"""Ordinary requests in real processes; no transport, executor or clock faults."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

import pytest

from src.application.observability.metrics import Counter, Gauge, Registry
from src.application.observability.metrics_multiprocess import MetricSnapshotError
from tests.test_metrics_core import parse_samples

WORKER_CODE = '''
import asyncio, json, sys
import httpx
from fastapi import FastAPI
from src.api.http_metrics_middleware import HttpMetricsMiddleware
from src.api.routes.metrics import router
from src.application.observability import business_metrics as business
app = FastAPI()
app.include_router(router)
app.add_middleware(HttpMetricsMiddleware)
@app.get("/work")
async def work():
    return {"ok": True}
async def command(payload):
    if "active" in payload:
        business.sse_connections_active().set(payload["active"])
    if "quota" in payload:
        business.resource_quota_used().replace_samples({("browser",): payload["quota"]})
    if payload.get("clear_quota"):
        business.resource_quota_used().replace_samples({})
    for stream, pending in payload.get("pending", {}).items():
        business.stream_pending().labels(stream=stream).set(pending)
    if "remove_pending" in payload:
        business.stream_pending().remove(stream=payload["remove_pending"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(payload.get("requests", 0)):
            response = await client.get("/work")
            assert response.status_code == 200
        if payload.get("no_scrape"):
            return {"status": 200, "body": ""}
        response = await client.get("/metrics")
        return {"status": response.status_code, "body": response.text}
for line in sys.stdin:
    payload = json.loads(line)
    if payload.get("exit"):
        break
    print(json.dumps(asyncio.run(command(payload))), flush=True)
'''


class Worker:
    def __init__(self, directory: Path):
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", WORKER_CODE],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "QA_METRICS_MULTIPROCESS_DIR": str(directory)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def request(self, **payload):
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        output = Queue()
        threading.Thread(target=lambda: output.put(self.process.stdout.readline()), daemon=True).start()
        try:
            line = output.get(timeout=15)
        except Empty:
            self.process.kill()
            _, stderr = self.process.communicate(timeout=5)
            pytest.fail(f"Metrics worker did not respond: {stderr}")
        assert line, self.process.communicate(timeout=5)[1]
        result = json.loads(line)
        assert result["status"] == 200, result
        return result["body"]

    def close(self):
        if self.process.poll() is None:
            try:
                _, stderr = self.process.communicate(json.dumps({"exit": True}) + "\n", timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                _, stderr = self.process.communicate(timeout=5)
                pytest.fail(f"Metrics worker did not exit: {stderr}")
            assert self.process.returncode == 0, stderr


def value(text, name, **labels):
    found = [sample for metric, actual, sample in parse_samples(text) if metric == name and actual == labels]
    assert len(found) == 1, text
    return found[0]


def test_two_real_workers_scrape_same_totals_and_retire_local_gauges(tmp_path):
    first, second = Worker(tmp_path), Worker(tmp_path)
    try:
        first.request(requests=2, active=1, quota=7)
        second.request(requests=3, active=2, quota=7)
        for worker in (first, second):
            text = worker.request()
            assert value(text, "qa_http_requests_total", method="GET", route="/work", status_class="2xx") == 5
            assert value(text, "qa_http_request_duration_seconds_count", method="GET", route="/work") == 5
            assert value(text, "qa_http_request_duration_seconds_bucket", method="GET", route="/work", le="+Inf") == 5
            assert value(text, "qa_sse_connections_active") == 3
            assert value(text, "qa_resource_quota_used", resource_type="browser") == 7
        # A newer authoritative empty snapshot must remove the old global label.
        text = second.request(clear_quota=True)
        assert not any(name == "qa_resource_quota_used" for name, _, _ in parse_samples(text))
        first.close()
        text = second.request(requests=1)
        assert value(text, "qa_http_requests_total", method="GET", route="/work", status_class="2xx") == 6
        assert value(text, "qa_sse_connections_active") == 2
    finally:
        first.close()
        second.close()


def test_global_gauges_merge_independent_labels_and_removals(tmp_path):
    first, second = Worker(tmp_path), Worker(tmp_path)
    try:
        first.request(pending={"agent": 3, "embedding": 4})
        second.request(pending={"agent": 5})
        text = first.request()
        assert value(text, "qa_stream_pending", stream="agent") == 5
        assert value(text, "qa_stream_pending", stream="embedding") == 4
        text = second.request(remove_pending="agent")
        assert not any(name == "qa_stream_pending" and labels == {"stream": "agent"}
                       for name, labels, _ in parse_samples(text))
        assert value(text, "qa_stream_pending", stream="embedding") == 4
    finally:
        first.close()
        second.close()


def test_peer_publishes_without_scrapes_and_saves_final_counter_on_exit(tmp_path):
    first, second = Worker(tmp_path), Worker(tmp_path)
    try:
        first.request(requests=2, no_scrape=True)
        deadline = time.monotonic() + 10
        while True:
            text = second.request()
            counts = [sample for name, labels, sample in parse_samples(text)
                      if name == "qa_http_requests_total" and labels.get("route") == "/work"]
            if counts == [2]:
                break
            assert time.monotonic() < deadline, text
            time.sleep(0.05)
        first.request(requests=3, no_scrape=True)
        first.close()
        assert value(second.request(), "qa_http_requests_total", method="GET", route="/work", status_class="2xx") == 5
    finally:
        first.close()
        second.close()


def test_conflicting_gauge_modes_are_rejected():
    registry = Registry()
    registry.get_or_create(Gauge, "qa_same", aggregation="sum")
    with pytest.raises(ValueError, match="aggregation mode"):
        registry.get_or_create(Gauge, "qa_same", aggregation="latest")


@pytest.mark.parametrize("broken_snapshot", ["not json", '{"version": 999}', '[]'])
def test_controlled_invalid_snapshot_is_visible_without_leaking_its_content(tmp_path, broken_snapshot):
    """Robustness contract for test-owned files; not a production corruption incident."""
    worker = Worker(tmp_path)
    try:
        worker.request(requests=1)
        (tmp_path / "metrics-controlled-invalid.json").write_text(broken_snapshot, encoding="utf-8")
        with pytest.raises(AssertionError, match="Metrics aggregation is unavailable"):
            worker.request()
    finally:
        worker.close()


def test_conflicting_snapshot_definitions_fail_visibly():
    first, second = Registry(), Registry()
    first.get_or_create(Counter, "qa_shared", label_names=("stream",)).labels(stream="agent").inc()
    second.get_or_create(Gauge, "qa_shared").set(2)
    with pytest.raises(MetricSnapshotError):
        Registry._render_aggregate([(True, first._snapshots()), (True, second._snapshots())])
