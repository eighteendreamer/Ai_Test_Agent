"""Serialize current applicability and the original ToolJob claim in PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import logging

from src.application.runtime.case_action_binding import (
    ADMISSION_PROTOCOL, CaseActionClaimUnavailable, bind_case_action, fixed_action_context,
    validate_action_admission_receipt,
)
from src.application.test_runs.environment_restrictions import restriction_reviews
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

    async def claim(self, job_id, bundle):
        try:
            result = await asyncio.to_thread(self._claim, job_id, bundle)
            if result is not None:
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
            if (run.project_id != binding.project_id or run.execution_strategy != "agent" or
                run.plan_snapshot is None or run.plan_snapshot.plan.id != binding.plan_id or
                run.plan_snapshot.plan.config_snapshot.policy.action_admission_protocol != ADMISSION_PROTOCOL):
                raise ValueError("动作确认与PG原固定计划不一致")
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
            if attempt.checkpoint_key != "agent_case_execution":
                raise ValueError("动作确认缺少原Agent执行检查点")
            checkpoint = AgentCaseExecutionCheckpoint.model_validate(attempt.checkpoint_payload)
            if (checkpoint.project_id, checkpoint.run_id, checkpoint.run_item_id, checkpoint.plan_id,
                checkpoint.execution_attempt_id, checkpoint.session_id, checkpoint.turn_id, checkpoint.context_hash) != (
                binding.project_id, binding.run_id, binding.run_item_id, binding.plan_id,
                binding.attempt_id, binding.session_id, binding.turn_id, binding.context_sha256):
                raise ValueError("动作确认与原Agent检查点身份或完整上下文不同")
            detail = runs._detail_from_cursor(cur, run.id)
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
            if decision == "explicit_invalid":
                output = {"status":"denied", "error":"case_action_explicit_invalid", RECEIPT_KEY:receipt,
                    "summary":"固定目标已明确确认此精确用例版本失效；本次待执行动作未发送", "action_sent":False}
                cur.execute(f"""UPDATE {self._table} SET status='denied', completed_at=%s, updated_at=%s,
                    summary=%s, output_payload=%s::jsonb, metadata=metadata || %s::jsonb
                    WHERE id=%s AND started_at IS NULL AND status IN ('queued','waiting_approval') RETURNING *""",
                    (now, now, output["summary"], json.dumps(output,ensure_ascii=False),
                        json.dumps({RECEIPT_KEY:receipt},ensure_ascii=False),job.id))
                saved = _job_from_row(cur.fetchone())
            else:
                saved = self._jobs.claim_job_execution_in_transaction(cur, job.id, metadata={RECEIPT_KEY:receipt})
            logger.info("case_action_admission_prepared", extra={"tool_job_id":job.id, "project_id":run.project_id,
                "run_item_id":item.id, "item_key":binding.item_key, "item_phase":binding.phase,
                "decision":decision, "review_ids":[r.id for r in reviews or []], "receipt_sha256":receipt["sha256"]})
            return saved
