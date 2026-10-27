"""Read-only assembly of an approval's original terminal-to-asset output.

No upstream counterpart (checked: Runtime event completion, applied tool
reader, shared turn_projection, LangGraph 1.2.12 aget_state, v4 §9.2/§10.2):
native state alone omits the application's preparation and original events.
Original Verification is projected only when its frozen rules are available.
This port returns no recovery proof, result asset or write rights.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging

from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER

from src.application.deep_agents.native_terminal import collect_native_terminal
from src.application.deep_agents.result_projection import project_deep_agent_result
from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.runtime.turn_projection import (
    project_original_completed_turn, project_deep_agent_callback_facts,
)
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_application_projection import COMMIT_KEY, _model_hash, _tool_hash
from src.runtime.framework_event_completion import RuntimeEventCompletion, completion_event_id, read_completion
from src.runtime.framework_message_stages import message_stage_table, validate_message_stage
from src.runtime.framework_runtime_results import RESULT_KEY, _approval_preparation, _require
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction
from src.schemas.framework_graph_input import FrameworkApprovalGraphInput
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from src.schemas.session import VerificationResult
from src.schemas.runtime_binding import RuntimeCheckpointSource


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkApprovalResultRead:
    preparation: FrameworkApprovalGraphInput
    completion: RuntimeEventCompletion
    runtime_result: FrameworkRuntimeResult
    model_calls_sha256: str
    tool_jobs_sha256: str
    originals: tuple = field(repr=False)
    verification_results: list[VerificationResult] | None = None


def _originals(tasks, binding, task_id, continuation):
    with postgres_connect(tasks._settings) as connection, connection.cursor() as cur:
        configure_runtime_owner_transaction(cur, tasks._settings)
        return read_approval_originals(cur, tasks, binding, task_id, continuation)


def read_approval_originals(cur, tasks, binding, task_id, continuation):
    """Share exact material verification with the owned sealing transaction."""
    settings = tasks._settings
    _require(continuation._settings == settings, "Approval result reader requires its shared material store")
    cur.execute(f"SELECT id,project_id FROM {settings.database.postgres_session_table} WHERE id=%s FOR SHARE",
        (binding.session_id,))
    session = cur.fetchone()
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s FOR UPDATE", (task_id,))
    task = cur.fetchone()
    _require(session is not None and task is not None
        and task["operation"] in {"approval_resume", "approval_recover"}
        and task["session_id"] == binding.session_id and task["turn_id"] == binding.turn_id
        and task["runtime_binding"] == binding.model_dump(mode="json")
        and task["project_id"] == session["project_id"], "Approval result has no matching original task")
    cur.execute(f"SELECT record FROM {message_stage_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (binding.binding_id,))
    row = cur.fetchone()
    _require(row is not None, "Approval result original message stage is missing")
    stage = validate_message_stage(row["record"])
    manifest = stage.source_manifest or {}
    _require(stage.phase == "runtime_submitted" and RESULT_KEY not in manifest and COMMIT_KEY not in manifest,
        "Approval result reader requires the original unprojected asset gap")
    preparation = _approval_preparation(stage, task)
    _require(preparation is not None, "Approval result original preparation is missing")
    cur.execute(f"SELECT payload FROM {settings.database.postgres_event_table} WHERE session_id=%s AND id=%s",
        (binding.session_id, completion_event_id(binding)))
    declaration = cur.fetchone()
    _require(declaration is not None, "Approval result original event completion is missing")
    material = RuntimeEventCompletion.model_validate(declaration["payload"].get("completion"))
    _require((material.task_id, material.attempt_id, material.owner_epoch)
        == (task_id, preparation.attempt_id, preparation.owner_epoch),
        "Approval result event completion belongs to another original producer")
    read = read_completion(cur, settings, binding, material.native_terminal.model_dump(mode="json"))
    initial = preparation.graph_input_asset.runtime_preparation.initial_state
    prefix = initial["event_log"]
    _require(read.event_log[:len(prefix)] == prefix and material.returned_event_offset == len(prefix) - 1,
        "Approval result preparation differs from its original invocation event boundary")
    ledger = PostgresFrameworkModelCalls(settings)
    ledger._require_binding(cur, binding)
    budget = ledger._snapshot(cur, binding.binding_id)
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s", (stage.task_id,))
    root = cur.fetchone()
    _require(root is not None and root["runtime_binding"] == binding.model_dump(mode="json")
        and root["project_id"] == task["project_id"] and root["input_hash"] == stage.input_sha256
        and tasks._v2_input_hash(root["input_payload"], binding, budget.policy) == stage.input_sha256,
        "Approval result differs from its original admitted root input or budget")
    original_budget = read.event_log[-1]["payload"].get("root_model_budget")
    if original_budget is not None:
        _require(read_model_budget_material(binding, original_budget) == budget,
            "Approval result original root model budget differs from its ledger")
    else:
        # Preserve historical no-policy output exactly, not a fabricated
        # budget in its result summary. This is NOT automatic admission;
        # nonempty ledgers still require original captured material.
        _require(not budget.calls, "Approval result original root model budget material is missing")
    materials = {}
    for record in initial["tool_results"]:
        candidates = [raw["task_id"] for raw in manifest["approval_graph_inputs"].values()
            if raw["approval_id"] == record.get("approval_id")]
        _require(bool(candidates), "Approval result original tool preparation is missing")
        for candidate in candidates:
            original = continuation.read_applied_tool_projection_in_transaction(cur, binding, candidate)
            _require(original.record.call_id == record["call_id"] and original.record.job_id == record["job_id"]
                and (record["call_id"] not in materials or materials[record["call_id"]] == original),
                "Approval result original tool materials conflict with their prepared slots")
            materials[record["call_id"]] = original
    cur.execute(f"SELECT id,call_id FROM {settings.database.postgres_tool_job_table} WHERE session_id=%s AND turn_id=%s",
        (binding.session_id, binding.turn_id))
    jobs = {(row["id"], row["call_id"]) for row in cur.fetchall()}
    _require(len(materials) == len(initial["tool_results"])
        and jobs == {(value.record.job_id, value.record.call_id) for value in materials.values()},
        "Approval result has an unprepared business tool or missing original job")
    return (preparation, read, materials, budget.model_dump(mode="json"),
        _model_hash(cur, settings, binding), _tool_hash(cur, settings, binding), stage.source_sha256)


async def _read_native(agent, saver, source, completion):
    _require(getattr(saver, "runtime_source", None) == source,
        "Approval result native reader differs from its original bound producer")
    config = {"configurable": {"thread_id": source.binding.thread_id,
        "checkpoint_id": completion.native_terminal.checkpoint_id, CONFIG_KEY_CHECKPOINTER: saver}}
    return await collect_native_terminal(agent, saver, config)


async def read_completed_approval(tasks, binding, task_id, *, continuation, agent=None, saver=None, native_reader=None):
    """No long transaction across native I/O; recheck original facts afterwards."""
    try:
        before = await asyncio.to_thread(_originals, tasks, binding, task_id, continuation)
        preparation, read, materials, _, model_hash, tool_hash, _ = before
        source = RuntimeCheckpointSource(binding=binding, task_id=task_id, attempt_id=preparation.attempt_id,
            owner_epoch=preparation.owner_epoch, worker_id=read.completion.worker_id)
        if native_reader is None:
            _require(agent is not None and saver is not None, "Approval result requires its original native reader")
            native = await _read_native(agent, saver, source, read.completion)
        else:
            _require(agent is None and saver is None, "Approval result native reader selection is ambiguous")
            async with native_reader(preparation.graph_input_asset, source=source) as (agent, saver):
                native = await _read_native(agent, saver, source, read.completion)
        _require(native.proof == read.completion.native_terminal, "Approval result native terminal changed")
        after = await asyncio.to_thread(_originals, tasks, binding, task_id, continuation)
        _require(after == before, "Approval result original materials changed during native read")
        facts = project_deep_agent_callback_facts(read.event_log, turn_id=binding.turn_id,
            returned_event_offset=read.completion.returned_event_offset)
        result = project_deep_agent_result(native.values, stage=facts.adapter_stage, subagent_events=facts.subagent_events,
            native_terminal=native.proof.model_dump(mode="json"),
            native_checkpoint_timestamp=native.checkpoint_timestamp,
            root_model_budget=read.event_log[-1]["payload"].get("root_model_budget"))
        initial = preparation.graph_input_asset.runtime_preparation.initial_state
        projected = project_original_completed_turn(preparation=preparation.graph_input_asset.runtime_preparation,
            result=result, binding=binding, facts=facts, event_log=read.event_log,
            returned_event_offset=read.completion.returned_event_offset,
            materials=[materials[item["call_id"]] for item in initial["tool_results"]])
        state, records = projected.state, projected.state["tool_results"]
        from src.application.testing.verification_service import VerificationService
        from src.schemas.framework_verification_projection import VERIFICATION_PREPARATION_KEY

        # Older originals remain readable, but absence is not proof of an
        # empty Verification set or permission to evaluate today's rules.
        verifications = (VerificationService().build_results(session_id=binding.session_id, turn_id=binding.turn_id,
            trace_id=state["trace_id"], tool_results=records, context_bundle=state["context_bundle"])
            if VERIFICATION_PREPARATION_KEY in state["context_bundle"] else None)
        assembled = FrameworkApprovalResultRead(preparation, read.completion, FrameworkRuntimeResult.from_result(projected),
            model_hash, tool_hash, before, verifications)
    except Exception as exc:
        logger.warning("framework_approval_original_result_read_rejected", extra={"binding_id": binding.binding_id,
            "task_id": task_id, "error_type": type(exc).__name__})
        raise
    logger.info("framework_approval_original_result_read", extra={"binding_id": binding.binding_id,
        "task_id": task_id, "checkpoint_id": read.completion.native_terminal.checkpoint_id})
    return assembled
