"""Internal formal-case execution bridge, not a dispatcher or result finalizer."""
from __future__ import annotations

import asyncio
import logging

from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.session_event_sink import SessionEventSink
from src.schemas.controlled_case_task_input import parse_controlled_case_task_input


logger = logging.getLogger(__name__)


class ControlledCaseExecutor:
    """Invoke the original graph once after the existing durable running intent.

    No upstream counterpart (checked: original task begin/claim, controlled
    adapter, v4 §9/§12.1): a native graph does not own our formal task start.
    There is no retry/resume, new claim, heartbeat loop or task completion here.
    Native outcomes are read material, NOT RuntimeTurnResult/test verdicts.
    The caller owns resource admission/heartbeat; main/Worker do not install
    this port until application projection and recovery contracts are ready.
    """

    def __init__(self, *, task_store, graph_adapter):
        self._tasks = task_store
        self._adapter = graph_adapter

    async def execute(self, task, *, build_business_tools, resource_id, fencing_token, project_outcome=None):
        # Capture worker-only authority too: serialization deliberately strips
        # the second lease, while a shallow copy retains mutable input aliases.
        task = task.model_copy(deep=True)
        scope = {"task_id": task.id, "attempt_id": task.attempt_id,
            "session_id": task.session_id, "run_id": task.run_id, "run_item_id": task.run_item_id}
        phase = "preflight"
        try:
            owner = task.runtime_owner
            if (task.operation != "case_execute" or owner is None or owner.controlled_case_lease is None
                or owner.binding != task.runtime_binding or owner.task_id != task.id
                or owner.attempt_id != task.attempt_id or owner.worker_id != task.worker_id
                or owner.owner_token != task.lease_token):
                raise ValueError("Controlled execution requires its original captured case owner")
            if set(task.input) != {"case_execution_input"}:
                raise ValueError("Controlled execution requires its single frozen case input")
            original = parse_controlled_case_task_input(task.input["case_execution_input"])
            if original.graph_input.binding != owner.binding:
                raise ValueError("Controlled execution input differs from its captured binding")
            phase = "begin_receipt_unknown"
            # This original transaction compares captured/durable inputs and
            # both live leases, then commits running BEFORE any graph action.
            # A cancelled to_thread waiter may still commit: never auto-retry.
            prepared = await self._tasks.begin(task, resource_id=resource_id, fencing_token=fencing_token)
            logger.info("controlled_case_execution_started", extra={**scope,
                "binding_id": owner.binding.binding_id, "execution_id": owner.controlled_case_lease.run_item_execution_attempt_id})
            sink = SessionEventSink(self._tasks._sessions, task.session_id,
                capacity=self._tasks._settings.orchestration.session_event_buffer_capacity,
                fence={"turn_lease_token": task.lease_token}, runtime_owner=owner)

            async def invoke(*, event_queue):
                nonlocal phase
                with framework_owner_scope(owner), graph_event_scope(event_queue):
                    # Governed tool assembly reads its original gates under a
                    # running owner. Do not weaken that check to pre-build tools.
                    phase = "tool_assembly_unsettled"
                    tools = list(await build_business_tools(prepared))
                    phase = "native_outcome_unknown"
                    outcome = await self._adapter.execute_frozen(prepared.graph_input, business_tools=tools)
                    if project_outcome is not None:
                        # Application projection/ACKs must finish BEFORE this
                        # original owner/sink scope closes, never after return.
                        phase = "application_result_unsettled"
                        await project_outcome(prepared, outcome)
                    return outcome

            # Reuse acknowledged, owner-fenced persistence and cancellation
            # cleanup, never the legacy queue's memory-only overflow fallback.
            # Only the optional application callback can complete its event
            # collection; this executor never declares application commit.
            outcome = await sink.run(invoke)
            logger.info("controlled_case_execution_returned", extra={**scope,
                "binding_id": owner.binding.binding_id, "native_terminal": outcome.native_terminal is not None,
                "native_interrupt_count": len(outcome.native_interrupts), "application_commit_authority": False})
            return outcome
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_execution_unsettled", extra={**scope,
                "recovery_reason": phase, "error_type": type(exc).__name__, "automatic_replay": False})
            raise
