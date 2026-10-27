"""Tool display is a deterministic projection, not another execution clock."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.runtime.tool_messages import build_chat_tool_messages
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.schemas.session import ChatMessage, MessageRole


WHEN = datetime(2026, 10, 27, 1, 2, 3, 456789)


def record(**updates):
    return {"call_id": "original-call", "job_id": "original-job", "tool_key": "inspect_value",
        "tool_name": "Inspect value", "status": "completed", "summary": "original result",
        "trace_id": "original-trace", "output": {"value": "evidence"}, "completed_at": WHEN, **updates}


def project(records, **kwargs):
    return build_chat_tool_messages("original-turn", records, binding_id="original-binding",
        hygiene_metadata=TranscriptHygieneService().tool_message_metadata(), **kwargs)


def refusal_event(**updates):
    return {"id": "original-event", "type": "tool.execution_denied", "timestamp": WHEN.isoformat(),
        "payload": {"turn_id": "original-turn", "call_id": "original-call", "tool_key": "inspect_value",
            "tool_job_id": None, "status": "denied", "harness": "deepagents", **updates}}


def test_persisted_completion_time_survives_json_and_timezone_round_trip():
    first = record(completed_at=WHEN.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8))))
    saved = {**first, "completed_at": first["completed_at"].isoformat()}
    assert project([first]) == project([saved])
    assert project([first])[0].created_at == WHEN


def test_pending_slot_preserves_ordinal_and_no_tool_result_needs_no_time():
    results = [record(call_id="A", status="waiting_approval", completed_at=None), record()]
    message, = project(results)
    assert message.metadata["ordinal"] == 2 and message.created_at == WHEN
    assert project([]) == []


def test_jobless_refusal_uses_first_original_completion_event_without_inventing_a_job():
    item = record(job_id=None, status="denied", completed_at=None)
    events = [refusal_event(), {**refusal_event(), "id": "replayed-event", "timestamp": "2026-10-28T00:00:00Z"}]
    before = deepcopy(item), deepcopy(events)
    first = project([item], event_log=events)
    assert first[0].created_at == WHEN
    assert first == project(json.loads(json.dumps([item])), event_log=json.loads(json.dumps(events)))
    assert (item, events) == before


@pytest.mark.parametrize("change", [{"turn_id": "other"}, {"call_id": "other"}, {"tool_key": "other"},
    {"tool_job_id": "other"}, {"status": "completed"}, {"harness": "legacy"}])
def test_unrelated_event_cannot_supply_a_refusal_time(change):
    item = record(job_id=None, status="denied", completed_at=None)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="time is unavailable"):
        project([item], event_log=[refusal_event(**change)])


def test_request_event_is_not_a_completion_time_and_missing_fact_does_not_use_now():
    item = record(job_id=None, status="denied", completed_at=None)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="time is unavailable"):
        project([item], event_log=[{**refusal_event(), "type": "tool.execution_requested"}])
    assert item["completed_at"] is None


def test_old_published_refusal_retains_its_time_even_without_record_or_event_time():
    item = record(job_id=None, status="denied", completed_at=None)
    previous, = project([{**item, "completed_at": WHEN - timedelta(seconds=9)}])
    assert project([item], previous_messages=[previous]) == [previous]
    # A historically published projection is authoritative for its own time;
    # do not rewrite it with a newer operation timestamp during migration.
    assert project([{**item, "completed_at": WHEN}], previous_messages=[previous]) == [previous]


@pytest.mark.parametrize("field", ["content", "ordinal", "turn_id", "tool_call_id", "role"])
def test_original_terminal_message_drift_is_not_hidden_by_reusing_its_time(field):
    previous, = project([record()])
    if field in {"content", "role"}:
        previous = previous.model_copy(update={field: "changed" if field == "content" else MessageRole.user})
    else:
        previous.metadata[field] = "changed"
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="differs|changed"):
        project([record()], previous_messages=[previous])


def test_only_relevant_message_identity_is_checked_and_conflicting_original_is_rejected():
    unrelated = ChatMessage(id="user-message", role=MessageRole.user, content="original", created_at=WHEN)
    history = [unrelated, unrelated.model_copy(update={"content": "unrelated"})]
    assert project([], previous_messages=history) == []
    original, = project([record()])
    assert project([record()], previous_messages=history + [original]) == [original]
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="conflicting"):
        project([record()], previous_messages=[original, original.model_copy(update={"created_at": WHEN + timedelta(seconds=1)})])


def test_legacy_unbound_messages_keep_existing_random_identity_and_clock_behavior():
    first = build_chat_tool_messages("legacy-turn", [record(completed_at=None)], hygiene_metadata={})
    second = build_chat_tool_messages("legacy-turn", [record(completed_at=None)], hygiene_metadata={})
    assert first[0].id != second[0].id and first[0].created_at != WHEN
    assert first[0].content == second[0].content and first[0].metadata == second[0].metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "partial", "failed", "denied", "missing", "exception", "delivery_error", "waiting_children"])
async def test_dispatch_uses_settled_job_times_without_repeating_output_projection(outcome, monkeypatch):
    from src.application.runtime.tool_job_service import ToolJobService
    from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
    from src.runtime.tool_job_store import InMemoryToolJobStore
    from src.schemas.agent import ToolDescriptor
    from src.schemas.tool_runtime import ModelToolCall

    jobs = ToolJobService(InMemoryToolJobStore())
    tool = ToolDescriptor(key="inspect_value", name="Inspect value", description="controlled", category="test")
    job = await jobs.create_job(tool=tool, call_id="original-call", session_id="original-session",
        turn_id="original-turn", trace_id="original-trace", input_payload={})
    runtime = ToolRuntimeService(tool_job_service=jobs)
    effects, projections = [], []
    original_projection = runtime._record_output

    async def project_once(**kwargs):
        projections.append(kwargs)
        return await original_projection(**kwargs)

    async def handler(arguments, context):
        effects.append(context.tool_job_id)
        if outcome == "exception":
            raise ValueError("controlled handler failure")
        return {"status": "completed" if outcome == "delivery_error" else outcome, "summary": "controlled result"}

    async def fail_delivery(*args, **kwargs):
        raise ConnectionError("controlled output processing failure")

    monkeypatch.setattr(runtime, "_record_output", project_once)
    if outcome != "missing":
        runtime._handlers[tool.key] = handler
    if outcome == "delivery_error":
        monkeypatch.setattr(jobs, "finish_output_processing", fail_delivery)
    result = await runtime.execute(tool=tool, call=ModelToolCall(id=job.call_id, name=tool.key),
        context=ToolExecutionContext(session_id=job.session_id, turn_id=job.turn_id, trace_id=job.trace_id,
            user_message="inspect", normalized_input="inspect", context_bundle={}, tool_job_id=job.id))
    saved = await jobs.get_job(job.id)
    assert effects == ([] if outcome == "missing" else [job.id])
    assert len(projections) <= 1
    if outcome == "waiting_children":
        assert result.completed_at is None and saved.completed_at is None
    else:
        replay = await runtime.record_from_job(saved)
        assert result.started_at == replay.started_at == saved.started_at
        assert result.completed_at == replay.completed_at == saved.completed_at
