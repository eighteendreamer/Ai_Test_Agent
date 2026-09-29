from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.domain.models import SessionRecord
from src.registry.tools import ToolRegistry
from src.runtime.store import InMemorySessionStore
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall


async def _reader_with_two_attachments() -> tuple[ToolRuntimeService, ToolExecutionContext]:
    store = InMemorySessionStore()
    now = datetime.now(UTC)
    await store.save_session(SessionRecord(
        id="session-attachment", title="attachments", status=SessionStatus.running,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
        mode_key="default", created_at=now, updated_at=now,
    ))
    runtime = ToolRuntimeService(session_store=store)
    context = ToolExecutionContext(
        session_id="session-attachment", turn_id="turn-attachment", trace_id="trace-attachment",
        user_message="Read the requested attachment", normalized_input="Read the requested attachment",
        context_bundle={"attachments": [
            {"id": "attachment-a", "name": "first.txt", "uri": "", "text_excerpt": "first document"},
            {"id": "attachment-b", "name": "other.txt", "uri": "rustfs://uploads/other", "text_excerpt": "other document"},
        ]},
    )
    return runtime, context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"attachment_id": "missing"},
        {"uri": "rustfs://uploads/missing"},
        {"name": "missing.txt"},
        {"attachment_id": "attachment-a", "name": "other.txt"},
    ],
)
async def test_attachment_reader_rejects_unmatched_explicit_selector(arguments) -> None:
    runtime, context = await _reader_with_two_attachments()
    tool = ToolRegistry().get("attachment-reader")

    result = await runtime.execute(
        tool,
        ModelToolCall(id="call-attachment", name="attachment-reader", arguments=arguments),
        context,
    )

    assert result.status == "failed"
    assert result.output["error"] == "attachment_not_found"
    assert "content" not in result.output
    assert [item["name"] for item in result.output["available_attachments"]] == ["first.txt", "other.txt"]


@pytest.mark.asyncio
async def test_attachment_reader_keeps_implicit_first_attachment() -> None:
    runtime, context = await _reader_with_two_attachments()

    result = await runtime.execute(
        ToolRegistry().get("attachment-reader"),
        ModelToolCall(id="call-attachment", name="attachment-reader", arguments={}),
        context,
    )

    assert result.status == "completed"
    assert result.output["attachment"]["name"] == "first.txt"
    assert result.output["content"] == "first document"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"attachment_id": "attachment-b"},
        {"name": "OTHER.TXT"},
        {"uri": "rustfs://uploads/other"},
        {"attachment_id": "attachment-b", "name": "other.txt"},
    ],
)
async def test_attachment_reader_reads_matching_explicit_attachment(arguments) -> None:
    runtime, context = await _reader_with_two_attachments()

    result = await runtime.execute(
        ToolRegistry().get("attachment-reader"),
        ModelToolCall(id="call-attachment", name="attachment-reader", arguments=arguments),
        context,
    )

    assert result.status == "completed"
    assert result.output["attachment"]["name"] == "other.txt"
    assert result.output["content"] == "other document"
