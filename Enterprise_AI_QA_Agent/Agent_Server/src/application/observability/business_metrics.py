"""Business metric names and label sets for the durable task and retrieval paths.

Every metric used outside ``metrics.py`` itself is declared exactly once here: a
call site must never build a metric name dynamically, because a name/label
conflict raises inside ``Registry.get_or_create``.  Handles are resolved through
the registry on use (same discipline as ``src/api/http_metrics_middleware.py``) so a
registry reset can never leave a caller writing into a detached metric object.
"""

from __future__ import annotations

from typing import Callable

from src.application.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_histogram,
)

OUTBOX_PUBLISHED_TOTAL = "qa_outbox_published_total"
OUTBOX_PENDING = "qa_outbox_pending"
TASK_CLAIMED_TOTAL = "qa_task_claimed_total"
TASK_DEFERRED_TOTAL = "qa_task_deferred_total"
TASK_RECLAIMED_TOTAL = "qa_task_reclaimed_total"
TASK_DEAD_LETTER_TOTAL = "qa_task_dead_letter_total"
REDIS_ERRORS_TOTAL = "qa_redis_errors_total"
WORKER_ITERATION_SECONDS = "qa_worker_iteration_seconds"
WORKER_ERRORS_TOTAL = "qa_worker_errors_total"
AGENT_TASKS_SUBMITTED_TOTAL = "qa_agent_tasks_submitted_total"
AGENT_TASK_WAIT_SECONDS = "qa_agent_task_wait_seconds"
AGENT_TASK_RETRIES_TOTAL = "qa_agent_task_retries_total"
AGENT_TASKS_FINISHED_TOTAL = "qa_agent_tasks_finished_total"
AGENT_TASK_EXECUTION_SECONDS = "qa_agent_task_execution_seconds"
RUN_ITEM_EXECUTION_SECONDS = "qa_run_item_execution_seconds"
RESOURCE_ACQUIRE_TOTAL = "qa_resource_acquire_total"
RESOURCE_LEASE_RENEW_LOST_TOTAL = "qa_resource_lease_renew_lost_total"
RESOURCE_RELEASE_ERRORS_TOTAL = "qa_resource_release_errors_total"
RESOURCE_LEASE_EXPIRED_TOTAL = "qa_resource_lease_expired_total"
RESOURCE_QUOTA_USED = "qa_resource_quota_used"
RESOURCE_QUOTA_LIMIT = "qa_resource_quota_limit"
SSE_CONNECTIONS_ACTIVE = "qa_sse_connections_active"
COMPACTION_TOTAL = "qa_compaction_total"
COMPACTION_EVENTS_TOTAL = "qa_compaction_events_total"
COMPACTION_DURATION_SECONDS = "qa_compaction_duration_seconds"
COMPACTION_LAG_EVENTS = "qa_compaction_lag_events"
EMBEDDING_CALLS_TOTAL = "qa_embedding_calls_total"
EMBEDDING_CACHE_TOTAL = "qa_embedding_cache_total"
EMBEDDING_API_SECONDS = "qa_embedding_api_seconds"
VECTOR_QUERIES_TOTAL = "qa_vector_queries_total"
VECTOR_ERRORS_TOTAL = "qa_vector_errors_total"
VECTOR_QUERY_SECONDS = "qa_vector_query_seconds"
LANGSMITH_REPORTS_TOTAL = "qa_langsmith_reports_total"
PG_POOL_CHECKED_OUT = "qa_pg_pool_checked_out"
PG_POOL_SIZE = "qa_pg_pool_size"
REDIS_USED_MEMORY_BYTES = "qa_redis_used_memory_bytes"
REDIS_SLOWLOG_ENTRIES = "qa_redis_slowlog_entries"
REDIS_SLOWLOG_MAX_DURATION_MICROSECONDS = "qa_redis_slowlog_max_duration_microseconds"
STREAM_LENGTH = "qa_stream_length"
STREAM_PENDING = "qa_stream_pending"
COLLECTOR_ERRORS_TOTAL = "qa_collector_errors_total"

REDIS_VECTOR_STORE = "redis"
POSTGRES_VECTOR_STORE = "postgres_memory"


def _counter(name: str, help: str, *labels: str) -> Callable[[], Counter]:
    return lambda: get_or_create_counter(name, help, labels)


def _gauge(name: str, help: str, *labels: str) -> Callable[[], Gauge]:
    return lambda: get_or_create_gauge(name, help, labels)


def _histogram(name: str, help: str, *labels: str) -> Callable[[], Histogram]:
    return lambda: get_or_create_histogram(name, help, labels)


outbox_published_total = _counter(
    OUTBOX_PUBLISHED_TOTAL, "Outbox rows published to Redis Streams.", "stream"
)
outbox_pending = _gauge(
    OUTBOX_PENDING, "Outbox rows still unpublished, by destination stream.", "stream"
)

task_claimed_total = _counter(
    TASK_CLAIMED_TOTAL, "Task deliveries read from a Redis Stream.", "stream"
)
task_deferred_total = _counter(
    TASK_DEFERRED_TOTAL, "Task deliveries deferred by a handler.", "stream"
)
task_reclaimed_total = _counter(
    TASK_RECLAIMED_TOTAL,
    "Deliveries re-published for retry or taken over by XAUTOCLAIM.",
    "stream",
)
task_dead_letter_total = _counter(
    TASK_DEAD_LETTER_TOTAL, "Deliveries moved to a dead letter stream.", "stream"
)
redis_errors_total = _counter(
    REDIS_ERRORS_TOTAL, "Redis transport failures by queue operation.", "operation"
)

worker_iteration_seconds = _histogram(
    WORKER_ITERATION_SECONDS, "End-to-end worker handling time per delivery.", "stream"
)
worker_errors_total = _counter(
    WORKER_ERRORS_TOTAL, "Worker handler failures by stream.", "stream"
)

agent_tasks_submitted_total = _counter(
    AGENT_TASKS_SUBMITTED_TOTAL,
    "Durable Agent tasks accepted, by task type and destination queue.",
    "task_type",
    "queue",
)
agent_task_wait_seconds = _histogram(
    AGENT_TASK_WAIT_SECONDS, "Queue wait from task creation to worker claim.", "task_type"
)
agent_task_retries_total = _counter(
    AGENT_TASK_RETRIES_TOTAL, "Agent task claims beyond the first attempt.", "task_type"
)
agent_tasks_finished_total = _counter(
    AGENT_TASKS_FINISHED_TOTAL, "Agent tasks reaching a terminal status.", "task_type", "status"
)
agent_task_execution_seconds = _histogram(
    AGENT_TASK_EXECUTION_SECONDS, "Agent task execution time from claim to finish.", "task_type"
)
run_item_execution_seconds = _histogram(
    RUN_ITEM_EXECUTION_SECONDS,
    "Test run item execution time from claim to terminal result.",
    "mode_key",
    "status",
)

resource_acquire_total = _counter(
    RESOURCE_ACQUIRE_TOTAL, "Resource lease acquisition attempts.", "resource_type", "result"
)
resource_lease_renew_lost_total = _counter(
    RESOURCE_LEASE_RENEW_LOST_TOTAL,
    "Lease renewals rejected because another owner holds the lease.",
    "resource_type",
)
resource_release_errors_total = _counter(
    RESOURCE_RELEASE_ERRORS_TOTAL,
    "Lease retirements or releases that did not free the lease.",
    "resource_type",
)
resource_lease_expired_total = _counter(
    RESOURCE_LEASE_EXPIRED_TOTAL, "Expired leases reclaimed by the reaper.", "resource_type"
)
resource_quota_used = _gauge(
    RESOURCE_QUOTA_USED, "Held resource leases per resource type.", "resource_type"
)
resource_quota_limit = _gauge(
    RESOURCE_QUOTA_LIMIT, "Configured lease ceiling per resource type.", "resource_type"
)

sse_connections_active = _gauge(
    SSE_CONNECTIONS_ACTIVE, "Session event streams currently open."
)

compaction_total = _counter(
    COMPACTION_TOTAL, "Session compaction cycles by outcome.", "result"
)
compaction_events_total = _counter(
    COMPACTION_EVENTS_TOTAL, "Hot session events folded by compaction."
)
compaction_duration_seconds = _histogram(
    COMPACTION_DURATION_SECONDS, "Session compaction cycle duration."
)
compaction_lag_events = _gauge(
    COMPACTION_LAG_EVENTS, "Durable FlowEvents no completed compaction cycle has folded."
)

embedding_calls_total = _counter(
    EMBEDDING_CALLS_TOTAL, "Embedding provider calls by model and outcome.", "model_key", "outcome"
)
embedding_cache_total = _counter(
    EMBEDDING_CACHE_TOTAL, "Embedding cache lookups by outcome.", "result"
)
embedding_api_seconds = _histogram(
    EMBEDDING_API_SECONDS, "Embedding provider call duration by model.", "model_key"
)

vector_queries_total = _counter(
    VECTOR_QUERIES_TOTAL, "Vector similarity queries by store.", "store"
)
vector_errors_total = _counter(
    VECTOR_ERRORS_TOTAL, "Failed vector similarity queries by store.", "store"
)
vector_query_seconds = _histogram(
    VECTOR_QUERY_SECONDS, "Vector similarity query duration by store.", "store"
)

langsmith_reports_total = _counter(
    LANGSMITH_REPORTS_TOTAL, "LangSmith trace reports by outcome.", "outcome"
)

pg_pool_checked_out = _gauge(
    PG_POOL_CHECKED_OUT, "PostgreSQL pool connections checked out."
)
pg_pool_size = _gauge(PG_POOL_SIZE, "PostgreSQL pool configured size.")
redis_used_memory_bytes = _gauge(
    REDIS_USED_MEMORY_BYTES, "Redis used_memory reported by INFO memory."
)
redis_slowlog_entries = _gauge(
    REDIS_SLOWLOG_ENTRIES, "Commands waiting in the Redis slow log."
)
redis_slowlog_max_duration_microseconds = _gauge(
    REDIS_SLOWLOG_MAX_DURATION_MICROSECONDS,
    "Slowest command among the most recent Redis slow log entries.",
)
stream_length = _gauge(STREAM_LENGTH, "Redis Stream entry count by stream.", "stream")
stream_pending = _gauge(
    STREAM_PENDING, "Redis Stream pending entries by stream.", "stream"
)
collector_errors_total = _counter(
    COLLECTOR_ERRORS_TOTAL, "Metrics collector passes that could not read a source.", "item"
)
