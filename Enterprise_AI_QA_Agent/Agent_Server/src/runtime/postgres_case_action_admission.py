"""Serialize current applicability and the original ToolJob claim in PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import logging

from src.application.runtime.case_action_binding import (
    ADMISSION_PROTOCOL, CaseActionClaimUnavailable, bind_case_action, fixed_action_context,
    validate_action_admission_receipt,
    OPERATION_PROTOCOL,
)
from src.application.runtime.case_operation import (
    CaseOperationRecoveryRequired, operation_identity, operation_receipt, replay_output,
    declared_operation_verification, operation_verification_receipt,
    RECEIPT_KEY as OPERATION_RECEIPT_KEY, VERIFICATION_KEY,
)
from src.runtime.postgres_case_operations import PostgresCaseOperations
from src.runtime.postgres_case_consumption import PostgresCaseConsumption
from src.application.runtime.case_effect_evidence import effect_intent, INTENT_KEY
from src.application.test_runs.environment_restrictions import restriction_reviews
from src.application.test_runs.plan_snapshot_identity import validate_claimed_plan_snapshot
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_business_environment_store import environment_digest
from src.runtime.postgres_tool_job_store import _job_from_row
from src.schemas.case_execution import AgentCaseExecutionCheckpoint

logger = logging.getLogger(__name__)
RECEIPT_KEY = "case_action_admission"


class PostgresCaseActionAdmission:
    def __init__(self, *, runs, jobs):
        if runs._settings != jobs._settings:
            raise ValueError("动作确认与原工具任务必须使用同一PG配置")
        self._runs, self._jobs, self._settings = runs, jobs, runs._settings
        self._table = self._settings.database.postgres_tool_job_table
        self.operations = PostgresCaseOperations(self._settings)
        self.consumption = PostgresCaseConsumption(self)

    async def initialize(self):
        await self.operations.initialize()
        await self._jobs.effect_evidence.initialize()
        await self.consumption.initialize()

    async def claim(self, job_id, bundle):
        try:
            result = await asyncio.to_thread(self._claim, job_id, bundle)
            if result is not None and not isinstance(result,CaseOperationRecoveryRequired):
                logger.info("case_action_claim_confirmed", extra={"tool_job_id":job_id,
                    "decision":result.metadata[RECEIPT_KEY]["decision"], "status":result.status.value})
            return result
        except Exception as exc:
            logger.warning("case_action_claim_unconfirmed", extra={"tool_job_id": job_id,
                "error_type": type(exc).__name__})
            raise CaseActionClaimUnavailable(
                f"工具任务 {job_id} 动作领取未获可靠确认；保留原记录并先查证，不能重复发送"
            ) from exc

    def _read_job(self, cur, job_id, *, lock=False):
        cur.execute(f"SELECT * FROM {self._table} WHERE id=%s" + (" FOR UPDATE" if lock else ""), (job_id,))
        row = cur.fetchone()
        if row is None:
            raise KeyError("动作确认缺少原ToolJob")
        return _job_from_row(row)

    @staticmethod
    def _binding(job, bundle, context):
        binding = bind_case_action(bundle=bundle, arguments=job.input_payload,
            session_id=job.session_id, turn_id=job.turn_id)
        if binding is None or job.metadata.get("case_action_binding") != binding.model_dump(mode="json"):
            raise ValueError("动作确认与原Job固定关联、输入或完整上下文不一致")
        if (context.action_admission_protocol != ADMISSION_PROTOCOL or
            any(job.metadata.get(k) != getattr(binding, k) for k in ("project_id", "run_id", "run_item_id", "attempt_id"))):
            raise ValueError("动作确认与原Job运行身份或新策略不一致")
        if job.metadata.get(INTENT_KEY) != effect_intent(bundle, binding):
            raise ValueError("原工具对象证据意图与固定上下文不同")
        return binding

    @staticmethod
    def _check_prior_receipt(job, binding):
        validate_action_admission_receipt(job, binding, required=True)

    def _claim(self, job_id, bundle):
        context = fixed_action_context(bundle)
        if context is None or context.action_admission_protocol != ADMISSION_PROTOCOL:
            raise ValueError("动作确认缺少服务器固定新策略")
        runs = self._runs
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            job = self._read_job(cur, job_id)
            binding = self._binding(job, bundle, context)
            self._check_prior_receipt(job, binding)
            if job.started_at is not None or job.status.value not in {"queued", "waiting_approval"}:
                # Historical terminal/unknown evidence takes priority; do not re-evaluate it.
                return None
            run = runs._read_run(cur, binding.run_id)
            cur.execute(f"SELECT record FROM {runs._item_table} WHERE id=%s", (binding.run_item_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError("动作确认缺少原运行条目")
            observed_item = runs._item_from_value(row["record"])
            selected_attempt = runs._read_attempt(cur, observed_item.id, observed_item.lease_token)
            run = runs._run_for_attempt_in_cursor(cur, run, selected_attempt)
            if (run.project_id != binding.project_id or run.execution_strategy != "agent" or
                run.plan_snapshot is None or run.plan_snapshot.plan.id != binding.plan_id or
                run.plan_snapshot.plan.config_snapshot.policy.action_admission_protocol != ADMISSION_PROTOCOL):
                raise ValueError("动作确认与PG原固定计划不一致")
            if context.consumption_protocol != run.plan_snapshot.plan.config_snapshot.policy.consumption_protocol:
                raise ValueError("消费阶段策略与PG原固定计划不一致")
            # Same order as append confirmation and RunItem start: project, then item/attempt, then Job.
            cur.execute(f"SELECT status FROM {self._settings.database.postgres_project_table} WHERE id=%s FOR UPDATE",
                (run.project_id,))
            if cur.fetchone() is None:
                raise KeyError("动作确认项目不存在")
            reviews = runs._current_environment_reviews(cur, run)
            item = runs._lock_item(cur, binding.run_item_id)
            cur.execute("SELECT clock_timestamp() AS checked_at")
            now = cur.fetchone()["checked_at"]
            runs._validate_lease(item, item.lease_token, now, {"running"})
            attempt = runs._lock_attempt(cur, item.id, item.lease_token)
            if attempt.status != "running" or attempt.run_id != run.id:
                raise ValueError("动作确认与当前运行Attempt不一致")
            if attempt.plan_snapshot_identity != selected_attempt.plan_snapshot_identity:
                raise ValueError("领取计划身份在动作确认期间变化，不能使用陈旧依据发送")
            validate_claimed_plan_snapshot(run, attempt)
            if attempt.checkpoint_key != "agent_case_execution":
                raise ValueError("动作确认缺少原Agent执行检查点")
            checkpoint = AgentCaseExecutionCheckpoint.model_validate(attempt.checkpoint_payload)
            if (checkpoint.project_id, checkpoint.run_id, checkpoint.run_item_id, checkpoint.plan_id,
                checkpoint.execution_attempt_id, checkpoint.session_id, checkpoint.turn_id, checkpoint.context_hash) != (
                binding.project_id, binding.run_id, binding.run_item_id, binding.plan_id,
                binding.attempt_id, binding.session_id, binding.turn_id, binding.context_sha256):
                raise ValueError("动作确认与原Agent检查点身份或完整上下文不同")
            detail = runs._detail_from_cursor(cur, run.id)
            detail.run = run
            lineage = {entry.id:entry for entry in detail.attempts if entry.run_item_id == item.id}
            ancestor, visited = attempt, set()
            while ancestor.id != binding.attempt_id:
                if ancestor.id in visited or ancestor.recovered_from_attempt_id not in lineage:
                    raise ValueError("动作确认原Attempt不属于当前恢复祖先")
                visited.add(ancestor.id)
                ancestor = lineage[ancestor.recovered_from_attempt_id]
            snapshot = run.plan_snapshot
            if (item.run_id != run.id or snapshot.run_item_ids.get(item.plan_item_key) != item.id or
                snapshot.owners.get(binding.item_key) != item.plan_item_key or
                snapshot.preparation_phases.get(binding.item_key, "case") != binding.phase or
                context.business_environment != snapshot.plan.environment):
                raise ValueError("动作确认执行项归属、阶段或固定目标不同")
            job = self._read_job(cur, job_id, lock=True)
            self._binding(job, bundle, context)
            self._check_prior_receipt(job, binding)
            if job.started_at is not None or job.status.value not in {"queued", "waiting_approval"}:
                return None
            restricted = restriction_reviews(snapshot.plan, item, reviews or [])
            # After-phase is not proof of cleanup. Preserve it until effect ownership is implemented.
            decision = ("after_not_restricted" if binding.phase == "after" else
                "explicit_invalid" if restricted else "no_explicit_invalid" if reviews is not None else "no_fixed_environment")
            receipt = {"version":ADMISSION_PROTOCOL, "decision":decision, "tool_job_id":job.id,
                "binding":binding.model_dump(mode="json"), "lease_attempt_id":attempt.id,
                "checked_at":now.isoformat(), "reviews":[r.model_dump(mode="json") for r in reviews or []],
                "restricted_review_ids":[r.id for r in restricted],
                "after_phase_is_cleanup_evidence":False}
            receipt["sha256"] = environment_digest(receipt)
            identity = operation_identity(job,binding) if context.operation_protocol == OPERATION_PROTOCOL else None
            operation = None
            verification = None
            verification_origin = None
            if identity is not None:
                verification = declared_operation_verification(
                    context,
                    item_key=binding.item_key,
                    tool_key=job.tool_key,
                    operation_key=identity["scope"]["operation_key"],
                    input_payload=job.input_payload,
                )
                if verification is not None:
                    verification_origin = self.operations.find_by_scope_and_operation_key(
                        cur, identity["scope"], verification.origin_operation_key)
                    if verification_origin is None:
                        raise ValueError("固定查证操作缺少已登记的原逻辑操作")
                    origin_job = self._read_job(cur, verification_origin["origin_job_id"], lock=True)
                    verification_origin = self.operations.validate_origin(verification_origin, origin_job)
                    if origin_job.status.value in {"running", "resume_requested"}:
                        if origin_job.metadata.get("tool_output_observation"):
                            return CaseOperationRecoveryRequired(origin_job.id)
                        # Keep the origin unknown. The new read operation is
                        # registered below with an explicit relation to it.
                        verification_origin = {
                            **verification_origin,
                            "origin_job_id": origin_job.id,
                        }
                    else:
                        # A read declaration is useful only while the origin is
                        # unknown. A known origin remains an ordinary read.
                        verification = None
                operation = self.operations.read(cur,identity["operation_id"])
                if operation is not None:
                    origin = self._read_job(cur,operation["origin_job_id"],lock=True)
                    original = self.operations.validate_origin(operation,origin)
                    self._check_prior_receipt(origin,bind_case_action(bundle=bundle,
                        arguments=origin.input_payload,session_id=origin.session_id,turn_id=origin.turn_id))
                    if original != identity:
                        return self._operation_alias(cur,job,receipt,identity,origin,"input_conflict")
                    if origin.status.value in {"running","resume_requested"} and origin.metadata.get("tool_output_observation"):
                        return CaseOperationRecoveryRequired(origin.id)
                    if origin.status.value not in {"completed","partial","failed","denied","cancelled"}:
                        return self._operation_alias(cur,job,receipt,identity,origin,"outcome_unknown")
                    return self._operation_alias(cur,job,receipt,identity,origin,"replayed")
            consumption, consumption_error = (None, None) if decision == "explicit_invalid" else self.consumption.prepare(
                cur, context=context, job=job, binding=binding, detail=detail, now=now)
            if consumption_error or consumption is not None:
                receipt = {**receipt, "decision":consumption_error or decision,
                    "consumption":consumption['proof'] if consumption else {"error":consumption_error, "run_item_completed":False}}
                receipt["sha256"] = environment_digest({k:v for k,v in receipt.items() if k != "sha256"})
                decision = receipt['decision']
            if decision == "explicit_invalid" or consumption_error:
                output = {"status":"denied", "error":consumption_error or "case_action_explicit_invalid", RECEIPT_KEY:receipt,
                    "summary":("消费阶段证据未满足；本次动作未发送："+consumption_error if consumption_error else
                        "固定目标已明确确认此精确用例版本失效；本次待执行动作未发送"), "action_sent":False}
                cur.execute(f"""UPDATE {self._table} SET status='denied', completed_at=%s, updated_at=%s,
                    summary=%s, output_payload=%s::jsonb, metadata=metadata || %s::jsonb
                    WHERE id=%s AND started_at IS NULL AND status IN ('queued','waiting_approval') RETURNING *""",
                    (now, now, output["summary"], json.dumps(output,ensure_ascii=False),
                        json.dumps({RECEIPT_KEY:receipt},ensure_ascii=False),job.id))
                saved = _job_from_row(cur.fetchone())
            else:
                metadata = {RECEIPT_KEY:receipt}
                if identity is not None:
                    self.operations.register(cur,identity,job)
                    metadata[OPERATION_RECEIPT_KEY] = operation_receipt(identity,origin_job_id=job.id,
                        decision="dispatched",action_sent=None)
                    if verification is not None and verification_origin is not None:
                        metadata[VERIFICATION_KEY] = operation_verification_receipt(
                            declaration=verification,
                            origin=verification_origin,
                            read_identity=identity,
                            read_job_id=job.id,
                            action_sent=None,
                        )
                saved = self._jobs.claim_job_execution_in_transaction(cur, job.id, metadata=metadata)
                if consumption is not None:
                    if saved is None:
                        raise ValueError("消费结束与原清理领取未同时确认")
                    self.consumption.save(cur, consumption)
            logger.info("case_action_admission_prepared", extra={"tool_job_id":job.id, "project_id":run.project_id,
                "run_item_id":item.id, "item_key":binding.item_key, "item_phase":binding.phase,
                "decision":decision, "review_ids":[r.id for r in reviews or []], "receipt_sha256":receipt["sha256"]})
            return saved

    def _operation_alias(self,cur,job,admission,identity,origin,decision):
        if decision == "replayed":
            output = replay_output(origin)
            status = origin.status.value
            summary = "复用原逻辑操作的固定结果；本次没有新发送。" + origin.summary
        else:
            output = {"status":"denied","error":"case_operation_"+decision,
                "origin_job_id":origin.id,"action_sent":False}
            status = "denied"
            summary = ("原逻辑操作完整输入不同；本次未发送。" if decision == "input_conflict" else
                "原逻辑操作结果未知；先按项目真实只读能力查证，本次未发送。")
        receipt = operation_receipt(identity,origin_job_id=origin.id,decision=decision,action_sent=False,
            output=output if decision == "replayed" else None,
            origin_output=origin.output_payload if decision == "replayed" else None)
        admission = {**admission,"decision":"operation_"+decision}
        admission["sha256"] = environment_digest({k:v for k,v in admission.items() if k != "sha256"})
        cur.execute(f"""UPDATE {self._table} SET status=%s,output_payload=%s::jsonb,summary=%s,
            completed_at=now(),updated_at=now(),metadata=metadata || %s::jsonb
            WHERE id=%s AND started_at IS NULL AND status IN ('queued','waiting_approval') RETURNING *""",
            (status,json.dumps(output,ensure_ascii=False),summary,
                json.dumps({RECEIPT_KEY:admission,OPERATION_RECEIPT_KEY:receipt},ensure_ascii=False),job.id))
        row = cur.fetchone()
        if row is None:
            raise ValueError("逻辑操作别名原状态已变化，不能覆盖")
        logger.info("case_operation_alias_saved",extra={"tool_job_id":job.id,"origin_job_id":origin.id,
            "operation_id":identity["operation_id"],"decision":decision,"action_sent":False})
        return _job_from_row(row)
