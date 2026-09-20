from __future__ import annotations

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI

from src.api.http_metrics_middleware import HttpMetricsMiddleware
from src.api.routes.metrics import router as metrics_router
from src.application.observability.metrics import (
    DEFAULT_BUCKETS,
    Counter,
    Histogram,
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_histogram,
    incr,
    observe,
    render,
    reset_registry,
    set_gauge,
)
from src.core.log_format import CORRELATION_FIELDS, LOG_FORMAT, ContextFilter
from src.core.request_context import bind_request_context

LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')
NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
UNESCAPE_RE = re.compile(r"\\(.)")
UNESCAPED = {"\\": "\\", '"': '"', "n": "\n"}


@pytest.fixture(autouse=True)
def isolated_registry():
    reset_registry()
    yield
    reset_registry()


def _unescape(value: str) -> str:
    return UNESCAPE_RE.sub(lambda found: UNESCAPED.get(found.group(1), found.group(1)), value)


def parse_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        rendered, _, value = line.rpartition(" ")
        assert value, f"sample without value: {line!r}"
        if rendered.endswith("}"):
            name, _, blocks = rendered.partition("{")
            labels = {
                found.group("key"): _unescape(found.group("value"))
                for found in LABEL_RE.finditer(blocks[: blocks.rindex("}")])
            }
        else:
            name, labels = rendered, {}
        assert NAME_RE.match(name), f"invalid sample name: {name!r}"
        samples.append((name, labels, float(value)))
    return samples


def test_counter_renders_help_type_and_labels():
    counter = get_or_create_counter("qa_tasks_total", "Tasks dispatched.", ("task_type",))
    counter.labels(task_type="regression_run").inc()
    counter.labels(task_type="regression_run").inc(2.5)
    counter.labels(task_type="case_generation").inc()

    text = render()
    assert "# HELP qa_tasks_total Tasks dispatched." in text
    assert "# TYPE qa_tasks_total counter" in text
    assert 'qa_tasks_total{task_type="regression_run"} 3.5' in text
    assert 'qa_tasks_total{task_type="case_generation"} 1' in text
    assert get_or_create_counter("qa_tasks_total", "Tasks dispatched.", ("task_type",)) is counter


def test_get_or_create_is_idempotent_and_rejects_redefinition():
    counter = get_or_create_counter("qa_shared_total", "shared", ("route",))
    assert get_or_create_counter("qa_shared_total", "other help", ("route",)) is counter
    with pytest.raises(ValueError):
        get_or_create_counter("qa_shared_total", "shared", ("method",))
    with pytest.raises(ValueError):
        get_or_create_gauge("qa_shared_total", "shared", ("route",))
    custom = get_or_create_histogram("qa_other_total", "h", ("route",), buckets=(1.0, 2.0))
    assert get_or_create_histogram("qa_other_total", "h", ("route",), buckets=(1.0, 2.0)) is custom
    with pytest.raises(ValueError):
        get_or_create_histogram("qa_other_total", "h", ("route",))


def test_histogram_renders_cumulative_buckets_with_pos_inf():
    histogram = get_or_create_histogram("qa_latency_seconds", "Latency.", ("method",), buckets=(1.0, 5.0))
    histogram.labels(method="GET").observe(0.4)
    histogram.labels(method="GET").observe(3.0)
    histogram.labels(method="GET").observe(900.0)

    lines = [line for line in render().splitlines() if line.startswith("qa_latency_seconds")]
    assert lines == [
        'qa_latency_seconds_bucket{method="GET",le="1"} 1',
        'qa_latency_seconds_bucket{method="GET",le="5"} 2',
        'qa_latency_seconds_bucket{method="GET",le="+Inf"} 3',
        'qa_latency_seconds_sum{method="GET"} 903.4',
        'qa_latency_seconds_count{method="GET"} 3',
    ]


def test_default_buckets_end_at_pos_inf():
    assert DEFAULT_BUCKETS[-1] == float("inf")
    histogram = get_or_create_histogram("qa_default_hist", "defaults")
    assert isinstance(histogram, Histogram)
    assert histogram.buckets == DEFAULT_BUCKETS
    observed = [
        (labels, value)
        for name, labels, value in parse_samples(render())
        if name == "qa_default_hist_bucket"
    ]
    assert [labels["le"] for labels, _ in observed] == ["0.05", "0.1", "0.25", "0.5", "1", "2.5"] + [
        "5",
        "10",
        "30",
        "60",
        "120",
        "300",
        "600",
        "1800",
        "3600",
        "+Inf",
    ]
    assert all(value == 0 for _, value in observed)
    histogram.observe(0.2)
    buckets = {
        labels["le"]: value
        for name, labels, value in parse_samples(render())
        if name == "qa_default_hist_bucket"
    }
    assert buckets["0.05"] == 0 and buckets["0.25"] == 1 and buckets["+Inf"] == 1


def test_render_output_is_machine_readable_and_escapes_label_values():
    counter = get_or_create_counter("qa_escapes_total", "Escaping check.", ("path", "note"))
    counter.labels(path='C:\\sessions "a"\nb', note="plain").inc()
    samples = parse_samples(render())
    labeled = [labels for name, labels, _ in samples if name == "qa_escapes_total"][0]
    assert labeled["path"] == 'C:\\sessions "a"\nb'
    assert labeled["note"] == "plain"
    raw = next(line for line in render().splitlines() if line.startswith("qa_escapes_total{"))
    assert raw.count("\n") == 0
    assert '"C:\\\\sessions \\"a\\"\\nb"' in raw


def test_gauge_and_convenience_helpers_write_registry():
    set_gauge("qa_sse_connections_active", 3.0, {"worker": "w-1"}, help="Active SSE streams.")
    gauge = get_or_create_gauge("qa_sse_connections_active", "Active SSE streams.", ("worker",))
    gauge.labels(worker="w-1").inc()
    gauge.labels(worker="w-1").dec(2.0)
    incr("qa_queue_depth_added", {"task_type": "perf_run"}, help="Queue adds.")
    observe("qa_queue_wait_seconds", 7.5, {"task_type": "perf_run"})
    text = render()
    assert 'qa_sse_connections_active{worker="w-1"} 2' in text
    assert 'qa_queue_depth_added{task_type="perf_run"} 1' in text
    assert 'qa_queue_wait_seconds_count{task_type="perf_run"} 1' in text
    assert 'qa_queue_wait_seconds_bucket{task_type="perf_run",le="+Inf"} 1' in text


def test_counter_refuses_negative_increment():
    counter = get_or_create_counter("qa_never_negative_total", "counter")
    assert isinstance(counter, Counter)
    with pytest.raises(ValueError):
        counter.inc(-1)


def test_registry_is_thread_safe_under_concurrent_writes():
    counter = get_or_create_counter("qa_concurrent_total", "Concurrency probe.", ("worker",))
    gauge = get_or_create_gauge("qa_concurrent_gauge", "Concurrency probe.", ("worker",))
    histogram = get_or_create_histogram("qa_concurrent_seconds", "Concurrency probe.", ("worker",))
    workers, per_worker = 8, 250

    def hammer(index: int) -> None:
        for _ in range(per_worker):
            counter.labels(worker=f"w-{index}").inc()
            gauge.labels(worker=f"w-{index}").inc()
            gauge.labels(worker=f"w-{index}").dec()
            histogram.labels(worker=f"w-{index}").observe(0.3)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(hammer, range(workers)))

    totals: dict[str, float] = {}
    for name, _labels, value in parse_samples(render()):
        if name in {"qa_concurrent_total", "qa_concurrent_gauge", "qa_concurrent_seconds_count"}:
            totals[name] = totals.get(name, 0.0) + value
    assert totals["qa_concurrent_total"] == workers * per_worker
    assert totals["qa_concurrent_seconds_count"] == workers * per_worker
    assert totals["qa_concurrent_gauge"] == 0


def test_reset_registry_empties_exposition():
    incr("qa_reset_probe_total", {"task_type": "smoke"}, help="Reset probe.")
    assert "qa_reset_probe_total" in render()
    reset_registry()
    assert render() == ""
    assert get_or_create_counter("qa_reset_probe_total", "Reset probe.", ("task_type",)).value(task_type="smoke") == 0.0


@pytest.mark.asyncio
async def test_http_metrics_middleware_records_route_templates():
    app = FastAPI()
    app.add_middleware(HttpMetricsMiddleware)

    @app.get("/api/v1/sessions/{session_id}")
    async def session_detail(session_id: str):
        return {"session_id": session_id}

    @app.post("/api/v1/runs")
    async def create_run():
        return {"status": "queued"}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("exploded")

    app.include_router(metrics_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://qa") as client:
        assert (await client.get("/api/v1/sessions/s-1")).status_code == 200
        assert (await client.post("/api/v1/runs")).status_code == 200
        assert (await client.get("/not-registered")).status_code == 404
        with pytest.raises(RuntimeError):
            await client.get("/boom")
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert 'qa_http_requests_total{method="GET",route="/api/v1/sessions/{session_id}",status_class="2xx"} 1' in body
    assert 'qa_http_requests_total{method="POST",route="/api/v1/runs",status_class="2xx"} 1' in body
    assert 'qa_http_requests_total{method="GET",route="unmatched",status_class="4xx"} 1' in body
    assert 'qa_http_requests_total{method="GET",route="/boom",status_class="5xx"} 1' in body
    assert "# TYPE qa_http_request_duration_seconds histogram" in body
    samples = parse_samples(body)
    durations = [
        (name, labels)
        for name, labels, value in samples
        if name == "qa_http_request_duration_seconds_count"
    ]
    assert {labels["route"] for _, labels in durations} == {
        "/api/v1/sessions/{session_id}",
        "/api/v1/runs",
        "unmatched",
        "/boom",
    }
    assert all(value >= 0 for _, _, value in samples)
    counts = {
        (labels["method"], labels["route"]): value
        for name, labels, value in samples
        if name == "qa_http_request_duration_seconds_count"
    }
    inf_buckets = {
        (labels["method"], labels["route"]): value
        for name, labels, value in samples
        if name == "qa_http_request_duration_seconds_bucket" and labels["le"] == "+Inf"
    }
    assert inf_buckets == counts
    assert sum(counts.values()) == 4


def test_context_filter_injects_correlation_fields(caplog):
    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(ContextFilter())
    logger = logging.getLogger("src.application.observability.tested")
    with bind_request_context(
        request_id="req-filter",
        trace_id="trace-filter",
        session_id="session-1",
        turn_id="turn-1",
        task_type="regression_run",
        queue="agent:high",
    ):
        logger.info("dispatch_requested")

    record = next(item for item in caplog.records if item.getMessage() == "dispatch_requested")
    assert record.request_id == "req-filter"
    assert record.trace_id == "trace-filter"
    assert record.task_type == "regression_run"
    assert record.queue == "agent:high"
    assert record.session_id == "session-1"
    assert record.status == ""
    assert record.project_id == ""
    assert record.run_item_id == ""


def test_configured_formatter_renders_without_bound_context(capsys):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger = logging.getLogger("src.core.log_format.tested")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    values = {
        "request_id": "req-line",
        "trace_id": "trace-line",
        "session_id": "session-line",
        "turn_id": "turn-line",
        "run_id": "run-line",
        "run_item_id": "item-line",
        "attempt_id": "attempt-line",
        "worker_id": "worker-line",
        "resource_id": "resource-line",
        "task_type": "perf_run",
        "queue": "perf:default",
    }
    try:
        with bind_request_context(**values):
            logger.info("metrics_scraped")
        logger.info("metrics_scraped_without_context")
    finally:
        logger.removeHandler(handler)

    lines = stream.getvalue().splitlines()
    # 第四节的 12 个统一字段必须全部出现在行内，缺一个就查不到是谁领的哪个资源。
    rendered = [
        "[" + " ".join(values.get(name, "") for name in CORRELATION_FIELDS) + "]",
        "[" + " ".join([""] * len(CORRELATION_FIELDS)) + "]",
    ]
    assert len(CORRELATION_FIELDS) == 12
    assert "status" in CORRELATION_FIELDS
    assert f"{rendered[0]} src.core.log_format.tested: metrics_scraped" in lines[0]
    assert f"{rendered[1]} src.core.log_format.tested: metrics_scraped_without_context" in lines[1]
    timestamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO ")
    assert timestamp.match(lines[0]) and timestamp.match(lines[1])
    assert "--- Logging error ---" not in capsys.readouterr().err
