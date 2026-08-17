from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_job import ToolJobRecord
from src.schemas.tool_runtime import ModelToolCall


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session-heartbeat",
        turn_id="turn-heartbeat",
        trace_id="trace-heartbeat",
        user_message="慢任务",
        normalized_input="慢任务",
        context_bundle={},
    )


def _tool() -> ToolDescriptor:
    return ToolDescriptor(
        key="heartbeat-test-tool",
        name="Heartbeat Test Tool",
        description="test",
        category="test",
        permission_level="safe",
    )


@pytest.mark.asyncio
async def test_tool_job_heartbeat_keeps_long_handler_alive_and_supervisor_is_cleaned_up():
    jobs = ToolJobService(InMemoryToolJobStore())
    heartbeat_calls: list[datetime] = []
    original_heartbeat = jobs.heartbeat

    async def counting_heartbeat(job_id: str, **kwargs) -> ToolJobRecord | None:
        heartbeat = await original_heartbeat(job_id, **kwargs)
        if heartbeat is not None:
            heartbeat_calls.append(heartbeat.heartbeat_at)
        return heartbeat

    jobs.heartbeat = counting_heartbeat  # type: ignore[method-assign]
    runtime = ToolRuntimeService(
        tool_job_service=jobs,
        tool_job_heartbeat_interval_seconds=0.01,
    )

    async def slow_handler(arguments, context):
        await asyncio.sleep(0.06)
        return {"status": "completed", "summary": "长任务完成"}

    runtime._handlers[_tool().key] = slow_handler
    result = await runtime.execute(
        tool=_tool(),
        call=ModelToolCall(id="call-heartbeat", name=_tool().key, arguments={}),
        context=_context(),
    )

    assert result.status == "completed"
    assert result.job_id
    assert len(heartbeat_calls) >= 3
    job = await jobs.get_job(result.job_id)
    assert job is not None
    assert job.status.value == "completed"
    assert job.completed_at is not None
    assert job.heartbeat_at is not None
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and getattr(task.get_coro(), "__name__", "") == "_keep_tool_job_alive"
    ]
