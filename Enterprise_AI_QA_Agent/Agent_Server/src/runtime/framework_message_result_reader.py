"""Assemble an ordinary message from its original completed material only.

Like the approval reader this port grants no execution, sealing or successor
permission. Native I/O runs outside short material-verification transactions.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging

from langchain_core.messages import ToolMessage

from src.application.deep_agents.native_terminal import NativeTerminalRead
from src.application.deep_agents.result_projection import project_deep_agent_result
from src.application.deep_agents.framework_transcript import deserialize_messages
from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.runtime.turn_projection import project_deep_agent_callback_facts, project_original_completed_turn
from src.application.testing.verification_service import VerificationService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_application_projection import COMMIT_KEY, _model_hash, _tool_hash
from src.runtime.framework_approval_result_reader import _read_native
from src.runtime.framework_event_completion import (
    RuntimeEventCompletion, RuntimeCheckpointEventPrefix, completion_event_id, read_completion,
    original_checkpoint_source, read_terminal_event_materials,
)
from src.runtime.framework_message_recovery import MESSAGE_EXECUTIONS, _same_input, _exists
from src.runtime.framework_message_stages import message_stage_table, validate_message_stage
from src.runtime.framework_runtime_results import RESULT_KEY, _require
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction
from src.runtime.postgres_tool_job_store import _job_from_row
from src.schemas.framework_graph_input import FrameworkPreparedGraphInputAsset, parse_graph_input_asset
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from src.schemas.framework_tool_projection import FrameworkToolProjection, FrameworkRuntimeToolProjection, TOOL_PROJECTION_KEY
from src.schemas.framework_tool_projection import parse_tool_projection
from src.schemas.framework_verification_projection import VERIFICATION_PREPARATION_KEY
from src.schemas.runtime_binding import RuntimeCheckpointSource
from src.schemas.session import VerificationResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkMessageResultRead:
    graph_input_asset: FrameworkPreparedGraphInputAsset
    source: RuntimeCheckpointSource
    completion: RuntimeEventCompletion | None
    runtime_result: FrameworkRuntimeResult
    model_calls_sha256: str
    tool_jobs_sha256: str
    originals: tuple = field(repr=False)
    verification_results: list[VerificationResult] | None = None
    checkpoint_event_prefix: RuntimeCheckpointEventPrefix | None = None
    native_read: NativeTerminalRead | None = field(default=None, repr=False)


def _originals(tasks, binding, task_id, *, native=None, expected=None):
    settings = tasks._settings
    with postgres_connect(settings) as connection, connection.cursor() as cur:
        configure_runtime_owner_transaction(cur, settings)
        current = read_message_originals(cur, tasks, binding, task_id, allow_unsealed=True)
        if native is None:
            return current
        _require(current == expected, "Message result original materials changed during native read")
        original, source, read, _, _, _, _, budget = current
        return read_terminal_event_materials(cur, settings, source, original, native, budget, read)


def read_message_originals(cur, tasks, binding, task_id, *, allow_unsealed=False):
    """Share exact material verification with the separate admission transaction."""
    settings = tasks._settings
    cur.execute(f"SELECT id,project_id::text AS project_id FROM {settings.database.postgres_session_table} WHERE id=%s FOR SHARE", (binding.session_id,))
    session = cur.fetchone()
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE", (task_id,))
    task = cur.fetchone()
    _require(session is not None and task is not None and task["operation"] in MESSAGE_EXECUTIONS
        and task["session_id"] == binding.session_id and task["turn_id"] == binding.turn_id
        and task["runtime_binding"] == binding.model_dump(mode="json") and task["project_id"] == session["project_id"],
        "Message result has no matching original producer")
    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE", (binding.binding_id,))
    row = cur.fetchone()
    _require(row is not None, "Message result original stage is missing")
    stage = validate_message_stage(row["record"])
    manifest = stage.source_manifest or {}
    _require(stage.phase == "runtime_submitted" and RESULT_KEY not in manifest and COMMIT_KEY not in manifest
        and stage.current_task_id == task_id, "Message result reader requires its original unprojected asset gap")
    original = parse_graph_input_asset(manifest.get("graph_input_asset"))
    _require(isinstance(original, FrameworkPreparedGraphInputAsset)
        and original.runtime_preparation.identity.binding == binding,
        "Message result original application preparation is missing or foreign")
    initial = original.runtime_preparation.initial_state
    _require(not initial["tool_results"] and not initial["pending_approvals"],
        "Message result cannot replace an approval invocation preparation")
    cur.execute(f"SELECT payload FROM {settings.database.postgres_event_table} WHERE session_id=%s AND id=%s",
        (binding.session_id, completion_event_id(binding)))
    declaration = cur.fetchone()
    _require(declaration is not None or allow_unsealed, "Message result original event completion is missing")
    _require(stage.owner_attempt_id == task["attempt_id"], "Message result original attempt changed")
    source = original_checkpoint_source(cur, settings, binding, task, stage.owner_epoch)
    read = None
    if declaration is not None:
        material = RuntimeEventCompletion.model_validate(declaration["payload"].get("completion"))
        _require((material.task_id, material.attempt_id, material.owner_epoch, material.worker_id)
            == (task_id, source.attempt_id, source.owner_epoch, source.worker_id),
            "Message result event completion belongs to another original producer")
        read = read_completion(cur, settings, binding, material.native_terminal.model_dump(mode="json"))
        _require(read.event_log[:len(initial["event_log"])] == initial["event_log"] and material.returned_event_offset == 0,
            "Message result original preparation differs from its invocation event boundary")
    ledger = PostgresFrameworkModelCalls(settings)
    ledger._require_binding(cur, binding)
    budget = ledger._snapshot(cur, binding)
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s", (stage.task_id,))
    root = cur.fetchone()
    _same_input(stage, root, task, binding)
    _require(tasks._v2_input_hash(root["input_payload"], binding, budget.policy) == stage.input_sha256,
        "Message result original input or budget changed")
    if read is not None:
        original_budget = read.event_log[-1]["payload"].get("root_model_budget")
        _require(read_model_budget_material(binding, original_budget) == budget if original_budget is not None else not budget.calls,
            "Message result original root model budget differs from its ledger or is missing")
    jobs = []
    if _exists(cur, settings.database.postgres_tool_job_table):
        cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE session_id=%s AND turn_id=%s ORDER BY id",
            (binding.session_id, binding.turn_id))
        jobs = [_job_from_row(row) for row in cur.fetchall()]
    else:
        # Same optional-store contract as _tool_hash. Native business tool
        # material still requires a matching settled Job below, never []
        # as permission to omit an execution that actually happened.
        logger.debug("framework_message_original_tool_store_absent", extra={"binding_id": binding.binding_id,
            "task_id": task_id})
    return (original, source, read, jobs, _model_hash(cur, settings, binding), _tool_hash(cur, settings, binding), stage.source_sha256, budget)

def _tool_materials(original, binding, messages, jobs):
    # Original application ordinal is completion order, not native task-path
    # message order. Preserve it even for parallel/reordered ToolMessages.
    by_job, materials = {job.id: job for job in jobs}, {}
    frozen_history = deserialize_messages(original.framework_messages)
    for message in messages:
        if not isinstance(message, ToolMessage) or not isinstance(message.artifact, dict) or TOOL_PROJECTION_KEY not in message.artifact:
            continue
        material = parse_tool_projection(message.artifact[TOOL_PROJECTION_KEY])
        if material.binding_fingerprint != binding.fingerprint:
            _require(any(message == item for item in frozen_history),
                "Message result has foreign tool material outside its original frozen history")
            continue
        material = FrameworkToolProjection.from_tool_message(message, binding=binding, source=material.source)
        record = material.record
        job = by_job.get(record.job_id)
        _require(isinstance(material, FrameworkRuntimeToolProjection) and job is not None,
            "Message result has missing or unsupported original tool material")
        _require(job.metadata.get("framework_binding_id") == binding.binding_id and job.call_id == record.call_id
            and job.tool_key == record.tool_key and job.tool_name == record.tool_name and job.status.value == record.status
            and job.input_payload == record.input and job.output_payload == record.output and job.summary == record.summary
            and job.trace_id == record.trace_id and job.started_at == record.started_at and job.completed_at == record.completed_at
            and record.approval_id is None, "Message result original tool material differs from its settled ToolJob")
        _require(record.call_id not in materials or materials[record.call_id] == material,
            "Message result has conflicting original tool materials")
        materials[record.call_id] = material
    ordered = sorted(materials.values(), key=lambda item: item.message.metadata["ordinal"])
    _require({item.record.job_id for item in ordered} == set(by_job)
        and [item.message.metadata["ordinal"] for item in ordered] == list(range(1, len(ordered) + 1)),
        "Message result has an unprepared business tool or conflicting original ordinals")
    return ordered


def project_original_message_result(original, source, read, native, jobs):
    """Same deterministic projection for a read and its owned admission recheck."""
    binding = source.binding
    materials = _tool_materials(original, binding, native.values.get("messages", []), jobs)
    facts = project_deep_agent_callback_facts(read.event_log, turn_id=binding.turn_id, returned_event_offset=0)
    root_model_budget = (read.event_log[-1]["payload"].get("root_model_budget")
        if read.completion is not None else read.checkpoint_event_prefix.root_model_budget_material)
    result = project_deep_agent_result(native.values, stage=facts.adapter_stage, subagent_events=facts.subagent_events,
        native_terminal=native.proof.model_dump(mode="json"), native_checkpoint_timestamp=native.checkpoint_timestamp,
        root_model_budget=root_model_budget, structured_output_version=original.feature_flags.get("structured_output_version"),
        summary_coverage_version=original.feature_flags.get("summary_coverage_version"))
    projected = project_original_completed_turn(preparation=original.runtime_preparation, result=result,
        binding=binding, facts=facts, event_log=read.event_log, returned_event_offset=0, materials=materials)
    state = projected.state
    verifications = (VerificationService().build_results(session_id=binding.session_id, turn_id=binding.turn_id,
        trace_id=state["trace_id"], tool_results=state["tool_results"], context_bundle=state["context_bundle"])
        if VERIFICATION_PREPARATION_KEY in state["context_bundle"] else None)
    return FrameworkRuntimeResult.from_result(projected), verifications


async def read_completed_message(tasks, binding, task_id, *, native_reader):
    try:
        before = await asyncio.to_thread(_originals, tasks, binding, task_id)
        original, source, read, jobs, model_hash, tool_hash, _, _ = before
        async with native_reader(original, source=source) as (agent, saver):
            native = await _read_native(agent, saver, source, read.completion if read else None)
        read = await asyncio.to_thread(_originals, tasks, binding, task_id, native=native, expected=before)
        projected, verifications = project_original_message_result(original, source, read, native, jobs)
        assembled = FrameworkMessageResultRead(original, source, read.completion, projected,
            model_hash, tool_hash, before, verifications, read.checkpoint_event_prefix,
            native if read.completion is None else None)
    except Exception as exc:
        logger.warning("framework_message_original_result_read_rejected", extra={"binding_id": binding.binding_id,
            "task_id": task_id, "error_type": type(exc).__name__})
        raise
    logger.info("framework_message_original_result_read", extra={"binding_id": binding.binding_id,
        "task_id": task_id, "checkpoint_id": native.proof.checkpoint_id, "sealed": read.completion is not None})
    return assembled
