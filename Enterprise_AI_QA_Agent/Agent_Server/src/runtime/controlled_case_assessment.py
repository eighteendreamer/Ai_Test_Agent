"""Retain the original domain Assessment beside the existing application proof.

No upstream counterpart (checked: AgentCaseAssessmentService, original RunItem
checkpoint, ControlledCaseResults, v4 §9/§12.1): the Session proof does not judge
a formal case. Reuse the same evaluator under its original dual-owner locks;
this is neither a second Session finalizer nor RunItem/task completion.
"""
import asyncio
from copy import deepcopy
import logging
from uuid import NAMESPACE_URL, uuid5
from typing import Any

from pydantic import TypeAdapter

from src.application.runtime.tool_job_service import ToolJobService
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.run_service import TestRunService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import current_framework_owner
from src.runtime.framework_application_projection import require_application_commit, _require, _require_event_intent
from src.runtime.framework_projection_source import lock_result_source
from src.runtime.postgres_case_effect_evidence import PostgresCaseEffectEvidence
from src.runtime.postgres_case_operations import PostgresCaseOperations
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.postgres_tool_job_store import PostgresToolJobStore, _artifact_from_row
from src.schemas.controlled_case_admission import ControlledCaseAssessmentCheckpoint
from src.schemas.controlled_case_assessment import ControlledCaseAssessmentAsset
from src.schemas.controlled_case_task_input import parse_controlled_case_task_input
from src.schemas.framework_approval import approval_value_hash
from src.schemas.model_config import ModelInvocationResult
from src.schemas.session import ExecutionEvent
from src.schemas.tool_job import ToolJobDetail
from src.schemas.tool_runtime import ToolExecutionRecord


logger = logging.getLogger(__name__)


def assessment_event(asset):
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-assessment:" + asset.binding.fingerprint)),
        session_id=asset.binding.session_id, timestamp=asset.prepared_at, type="test_run.case_assessment_prepared",
        payload={"turn_id": asset.binding.turn_id, "task_id": asset.task_id, "attempt_id": asset.attempt_id,
            "owner_epoch": asset.owner_epoch, "run_id": asset.run_id, "run_item_id": asset.run_item_id,
            "run_item_execution_attempt_id": asset.run_item_execution_attempt_id,
            "assessment_sha256": asset.asset_sha256, "runtime_result_asset_sha256": asset.runtime_result_asset_sha256,
            "application_commit_sha256": asset.application_commit_sha256, "run_item_completed": False})


class ControlledCaseAssessments:
    """No upstream counterpart (checked: original Assessment/RunItem completion,
    case consumption PG transaction, v4 §12.1): retain that evaluator's original
    preparation, not a second verdict algorithm or a RunItem completion permit.
    """
    def __init__(self, tasks):
        self._tasks = tasks

    async def freeze(self, owner, *, native):
        return await self._run(owner, native, create=True)

    async def load(self, owner, *, native):
        return await self._run(owner, native, create=False)

    async def _run(self, owner, native, *, create):
        scope = {"task_id": getattr(owner, "task_id", None), "operation": "freeze" if create else "load"}
        try:
            _require(owner is not None and owner == current_framework_owner() and owner.controlled_case_lease is not None,
                "Controlled assessment requires its captured current dual owner")
            owner, native = deepcopy((owner, native))
            asset, reused = await asyncio.to_thread(self._material, owner, native, create)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_assessment_unsettled", extra={**scope, "error_type": type(exc).__name__,
                "recovery_reason": "assessment_write_receipt_unknown" if create else "assessment_read_unsettled",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_assessment_reused" if reused else "controlled_case_assessment_retained", extra={
            **scope, "run_item_id": asset.run_item_id, "asset_sha256": asset.asset_sha256, "run_item_completed": False})
        return asset

    def _source(self, cur, owner, native):
        settings = self._tasks._settings
        source, task = lock_result_source(cur, settings, owner, controlled_case_native=native)
        cur.execute(f"SELECT * FROM {settings.database.postgres_session_table} WHERE id=%s", (owner.binding.session_id,))
        proof = require_application_commit(cur, settings, cur.fetchone(), owner.binding, source)
        original = parse_controlled_case_task_input(task["input_payload"]["case_execution_input"])
        return source, original, proof

    def _evidence(self, cur, original, result):
        settings, context, jobs, artifact_rows = self._tasks._settings, original.context, {}, {}
        records = [ToolExecutionRecord.model_validate(record) for record in result.runtime_result.state.get("tool_results", [])]
        store = PostgresToolJobStore(settings)
        for record in records:
            if not record.job_id:
                continue
            job = store.get_job_in_transaction(cur, record.job_id)
            artifacts = []
            if job is not None:
                cur.execute(f"SELECT * FROM {settings.database.postgres_tool_artifact_table} WHERE tool_job_id=%s "
                    "ORDER BY created_at,id FOR SHARE", (job.id,))
                rows = [dict(row) for row in cur.fetchall()]
                artifact_rows[job.id] = rows
                artifacts = [_artifact_from_row(row) for row in rows]
            detail = ToolJobDetail(**job.model_dump(mode="python"), artifacts=artifacts) if job is not None else None
            AgentCaseAssessmentService.add_evidence_job(context, record, detail, jobs,
                session_id=original.graph_input.binding.session_id, turn_id=original.graph_input.binding.turn_id,
                attempt_id=original.run_item_execution_attempt_id)
            if context.action_binding_protocol is not None and context.operation_protocol is not None:
                PostgresCaseOperations(settings).validate_job_in_transaction(cur, detail)
        raw_inventory = (PostgresCaseEffectEvidence(settings).read_in_transaction(cur,
            context.project_id, context.run_id, context.plan_id) if context.effect_coverage_protocol else [])
        inventory = ToolJobService.case_effect_evidence(raw_inventory)
        digest = approval_value_hash(TypeAdapter(Any).dump_python({
            "jobs": [jobs[key].model_dump(mode="json") for key in sorted(jobs)],
            "artifact_rows": artifact_rows, "inventory": raw_inventory}, mode="json"))
        return jobs, inventory, digest

    def _require_original(self, cur, source, original):
        """Same evidence/audit check for live load and proof-scoped terminal ACK."""
        settings = self._tasks._settings
        _require(isinstance(source.checkpoint, ControlledCaseAssessmentCheckpoint), "Controlled original assessment is missing")
        asset = source.checkpoint.assessment
        evidence_hash = self._evidence(cur, original, source.checkpoint.runtime_result_asset)[2]
        _require(asset.evidence_sha256 == evidence_hash, "Controlled assessment original evidence changed")
        event = assessment_event(asset)
        cur.execute(f"SELECT * FROM {settings.database.postgres_event_table} WHERE session_id=%s AND id=%s",
            (event.session_id, event.id))
        row = cur.fetchone()
        _require(row is not None, "Controlled assessment has no atomic original receipt")
        _require_event_intent(row, event)
        return asset

    def _material(self, owner, native, create):
        tasks, settings = self._tasks, self._tasks._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            source, original, proof = self._source(cur, owner, native)
            if isinstance(source.checkpoint, ControlledCaseAssessmentCheckpoint):
                asset = self._require_original(cur, source, original)
                assert_runtime_owner(cur, settings, owner)
                return asset, True
            _require(create, "Controlled original assessment is missing")
            result = source.checkpoint.runtime_result_asset
            jobs, inventory, evidence_hash = self._evidence(cur, original, result)
            assessor, state, context = AgentCaseAssessmentService(None), result.runtime_result.state, original.context
            response = ModelInvocationResult(text=state.get("model_response_text", ""),
                response_summary=state.get("model_response_summary", {}))
            receipt = assessor.parse_response(response, scene_receipt_protocol=context.scene_receipt_protocol)
            outcome = assessor.assess_from_evidence(context=context, receipt=receipt, jobs=jobs, inventory=inventory,
                session_id=owner.binding.session_id, turn_id=owner.binding.turn_id,
                attempt_id=original.run_item_execution_attempt_id, model_key=original.graph_input.model_key,
                lease_token=owner.controlled_case_lease.lease_token)
            outcome.completion.actual["execution_identity"] = {"session_id": owner.binding.session_id,
                "turn_id": owner.binding.turn_id, "execution_attempt_id": original.run_item_execution_attempt_id,
                "result_attempt_id": original.run_item_execution_attempt_id, "snapshot_id": proof.snapshot_id}
            # Re-read after the evaluator; do not turn a stale check or mocked
            # clock into permission to save a result based on changed evidence.
            current, _, current_proof = self._source(cur, owner, native)
            _require(current_proof == proof and self._evidence(cur, original, result)[2] == evidence_hash,
                "Controlled assessment originals changed during evaluation")
            cur.execute("SELECT clock_timestamp() AS now")
            values = dict(schema_version=1, scope="controlled_case_assessment_preparation_only",
                binding=owner.binding.model_dump(mode="json"), task_id=owner.task_id, attempt_id=owner.attempt_id,
                owner_epoch=owner.owner_epoch, run_id=context.run_id, run_item_id=context.run_item_id,
                run_item_execution_attempt_id=original.run_item_execution_attempt_id, input_sha256=original.asset_sha256,
                runtime_result_asset_sha256=result.asset_sha256, application_commit_sha256=approval_value_hash(proof.model_dump(mode="json")),
                evidence_sha256=evidence_hash, completion=TestRunService.prepare_completion(outcome.completion).model_dump(mode="json"),
                verification_results=[v.model_dump(mode="json") for v in outcome.verification_results],
                tool_records=[r.model_dump(mode="json") for r in outcome.tool_records], prepared_at=cur.fetchone()["now"])
            # Same Pydantic JSON normalization as framework_model_evidence;
            # preserve aware DB time and the schema's canonical timestamp wire.
            values = TypeAdapter(Any).dump_python(values, mode="json")
            asset = ControlledCaseAssessmentAsset(**values, asset_sha256=approval_value_hash(values))
            checkpoint = ControlledCaseAssessmentCheckpoint.model_validate({**current.checkpoint.model_dump(mode="json"),
                "schema_version": 5, "assessment": asset.model_dump(mode="json")})
            assert_runtime_owner(cur, settings, owner)
            current.locked.store._write_attempt(cur, current.locked.attempt.model_copy(update={
                "checkpoint_version": 5, "checkpoint_payload": checkpoint.model_dump(mode="json")}))
            tasks._sessions.append_event_in_transaction(cur, owner.binding.session_id, assessment_event(asset), runtime_owner=owner)
            return asset, False
