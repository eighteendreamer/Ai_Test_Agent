"""Owned formal original reads; no recovery admission, writes or finalizer."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging

from src.application.deep_agents.native_terminal import require_native_terminal_original
from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.runtime.controlled_case_projection import project_controlled_case_result
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_event_completion import _event_references, _require_ordered_log, read_completion
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_tool_materials import read_unapproved_tool_materials
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.postgres_tool_job_store import _job_from_row, _artifact_from_row
from src.schemas.controlled_case_task_input import ControlledCaseResultTaskInputAsset, parse_controlled_case_task_input
from src.schemas.runtime_binding import RuntimeCheckpointSource


logger = logging.getLogger(__name__)


def _require(condition, message):
    if not condition:
        raise FrameworkMessageStageRecoveryRequired(message)


class ControlledCaseResultReader:
    """Same sources/rules for live prepare and completed-collection reads.

    This still requires the original live dual owner. It cannot recover an
    expired task, freeze a result asset, finalize a Session or judge a case.
    Model settlement is checked; absent response objects are NOT invented or
    promoted to model-response import evidence.
    """

    def __init__(self, tasks):
        self._tasks = tasks

    async def prepare(self, owner, original, outcome, event_log):
        if outcome.native_terminal is None or outcome.native_interrupts or outcome.subagent_events:
            raise ValueError("Controlled result preparation requires its completed direct native outcome")
        return await self._run(owner, original, outcome.native_terminal,
            event_log=event_log, root_model_budget=outcome.root_model_budget)

    async def read(self, owner, original, native):
        return await self._run(owner, original, native)

    async def _run(self, owner, original, native, *, event_log=None, root_model_budget=None):
        # Capture JSON/native values before yielding to the DB thread. Retain
        # the worker-only second lease, not a serialized/stripped owner.
        owner, original, native, event_log, root_model_budget = deepcopy((owner, original, native, event_log, root_model_budget))
        scope = {"task_id": getattr(owner, "task_id", None), "binding_id": getattr(getattr(owner, "binding", None), "binding_id", None)}
        try:
            result = await asyncio.to_thread(self._read, owner, original, native, event_log, root_model_budget)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_result_read_rejected", extra={**scope, "error_type": type(exc).__name__,
                "recovery_reason": "original_materials_unsettled", "automatic_replay": False})
            raise
        logger.info("controlled_case_result_originals_read", extra={**scope,
            "checkpoint_id": native.proof.checkpoint_id, "event_collection_completed": event_log is None,
            "application_finalized": False})
        return result

    def _read(self, owner, original, native, event_log, root_model_budget):
        with postgres_connect(self._tasks._settings) as connection, connection.cursor() as cur:
            return self.read_in_transaction(cur, owner, original, native,
                event_log=event_log, root_model_budget=root_model_budget)

    def read_in_transaction(self, cur, owner, original, native, *, event_log=None, root_model_budget=None):
        """Same original checks for readers and the existing owner's asset write."""
        original = parse_controlled_case_task_input(original)
        _require(owner is not None and owner.controlled_case_lease is not None
            and isinstance(original, ControlledCaseResultTaskInputAsset) and owner.binding == original.graph_input.binding,
            "Controlled result requires its original selected preparation and dual owner")
        tasks, binding = self._tasks, owner.binding
        settings = tasks._settings
        configure_runtime_owner_transaction(cur, settings)
        locked = assert_runtime_owner(cur, settings, owner)
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
        task = cur.fetchone()
        _require(task is not None and task["status"] == "running", "Controlled result task is not running")
        result = self._read_originals_in_transaction(cur, owner, original, native, locked, task,
            event_log=event_log, root_model_budget=root_model_budget)
        # Live readers still require both live leases, including after waiting reads.
        assert_runtime_owner(cur, settings, owner)
        return result

    def _read_originals_in_transaction(self, cur, owner, original, native, locked, task, *,
            event_log=None, root_model_budget=None):
        """Shared facts only, not authority. Terminal ACK calls this only AFTER
        validating the complete atomic settlement proof and actual terminal rows.
        No public skip-owner flag, writes, graph invocation or response import.
        """
        tasks, binding, settings = self._tasks, owner.binding, self._tasks._settings
        _require(task is not None and task["operation"] == "case_execute"
            and task["lease_token"] == owner.owner_token
            and task["attempt_id"] == owner.attempt_id and task["worker_id"] == owner.worker_id
            and task["runtime_binding"] == binding.model_dump(mode="json")
            and task["input_payload"] == {"case_execution_input": original.model_dump(mode="json")}
            and locked.checkpoint.input_sha256 == original.asset_sha256,
            "Controlled result differs from its durable original task/admission")
        ledger = PostgresFrameworkModelCalls(settings)
        cur.execute(f"SELECT binding_id FROM {ledger.budgets_table} WHERE binding_id=%s FOR SHARE", (binding.binding_id,))
        cur.execute(f"SELECT attempt_id FROM {ledger.calls_table} WHERE binding_id=%s ORDER BY attempt_id FOR SHARE", (binding.binding_id,))
        budget = ledger._snapshot(cur, binding)
        expected = tasks._input_hash("case_execute", {"input": task["input_payload"],
            "runtime_binding_fingerprint": binding.fingerprint, "model_budget": budget.policy.model_dump(mode="json")})
        _require(expected == task["input_hash"] == locked.checkpoint.submission_sha256
            and budget.calls and all(call.phase == "completed" for call in budget.calls),
            "Controlled result has changed original input/budget or unsettled model calls")
        source = RuntimeCheckpointSource(binding=binding, owner_epoch=owner.owner_epoch,
            task_id=owner.task_id, attempt_id=owner.attempt_id, worker_id=owner.worker_id)
        require_native_terminal_original(cur, settings, source, native, original.graph_input)
        sealed = event_log is None
        if sealed:
            read = read_completion(cur, settings, binding, native.proof.model_dump(mode="json"))
            material = read.completion
            _require((material.task_id, material.attempt_id, material.owner_epoch, material.worker_id,
                material.returned_event_offset) == (owner.task_id, owner.attempt_id, owner.owner_epoch, owner.worker_id, 0),
                "Controlled result completion differs from its original producer")
            event_log = read.event_log[:-1]
            root_model_budget = read.event_log[-1]["payload"].get("root_model_budget")
        _require(read_model_budget_material(binding, root_model_budget) == budget,
            "Controlled result root model budget differs from its original ledger")
        _require_ordered_log(binding, event_log, 0)
        _event_references(cur, settings, binding, event_log)
        table = settings.database.postgres_tool_job_table
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,))
        jobs = []
        if cur.fetchone()["present"]:
            cur.execute(f"SELECT * FROM {table} WHERE session_id=%s AND turn_id=%s ORDER BY id FOR SHARE",
                (binding.session_id, binding.turn_id))
            jobs = [_job_from_row(row) for row in cur.fetchall()]
        _require(all(job.status.value in {"completed", "partial", "failed", "denied"} for job in jobs),
            "Controlled result has unsettled business ToolJobs")
        from src.application.runtime.tool_runtime_service import ToolRuntimeService

        outputs = {}
        for job in jobs:
            cur.execute(f"SELECT * FROM {settings.database.postgres_tool_artifact_table} WHERE tool_job_id=%s "
                "ORDER BY created_at ASC FOR SHARE", (job.id,))
            artifacts = [_artifact_from_row(row) for row in cur.fetchall()]
            _require(len(artifacts) == job.artifact_count and all((a.session_id, a.turn_id, a.tool_key)
                == (job.session_id, job.turn_id, job.tool_key) for a in artifacts),
                "Controlled result original artifacts differ from their ToolJob")
            outputs[job.id] = ToolRuntimeService.project_record_output(tool_key=job.tool_key,
                result=job.output_payload, session_id=job.session_id, artifacts=artifacts, metadata=job.metadata)
        materials = read_unapproved_tool_materials(original.graph_input, binding,
            native.values.get("messages", []), jobs, event_log=event_log, projected_outputs=outputs)
        result = project_controlled_case_result(original, native, root_model_budget, event_log, materials)
        _require(not sealed or result.state["event_log"] == read.event_log,
            "Controlled result completion differs from its frozen deterministic projection")
        return result
