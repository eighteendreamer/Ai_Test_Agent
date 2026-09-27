"""Dispatch security testing tasks to worker sessions via ``subagent-dispatch``."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from src.modes.security_testing_mode.agent import resolve_security_worker_agent
from src.modes.security_testing_mode.campaign_state import (
    AgentActivityRecord,
    SecurityTask,
)
from src.modes.security_testing_mode.contracts import (
    MAX_CONCURRENT_WORKERS,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_RUNNING,
    TOOL_EXEC_TIMEOUT_SECONDS,
)
from src.modes.security_testing_mode.output_compaction import (
    DEFAULT_THRESHOLD_BYTES,
    compact_security_output,
)
from src.modes.security_testing_mode.prompt_contract import build_security_worker_prompt
from src.modes.security_testing_mode.task_pool import SecurityTaskPool
from src.modes.security_testing_mode.subtask_refiner import SecuritySubtaskRefiner
from src.modes.security_testing_mode.scenario_incremental_planner import (
    SecurityScenarioIncrementalPlanner,
)

logger = logging.getLogger("uvicorn.error.security_testing_mode.coordinator")

CheckpointCallback = Callable[[str, SecurityTask, list[SecurityTask]], None]
InterruptCheck = Callable[[], bool]
BatchHydrator = Callable[[list[SecurityTask]], None]


# Phrases that indicate the target platform cannot be tested further without
# additional access (credentials, lab activation, VPN, paid subscription, ...).
# Kept as a constant so runtime-level helpers can reuse the same vocabulary.
RESTRICTED_ACCESS_SIGNALS: tuple[str, ...] = (
    "login required",
    "login_required",
    "subscription required",
    "premium required",
    "room locked",
    "machine not deployed",
    "lab not started",
    "lab offline",
    "target offline",
    "vpn required",
    "vpn_required",
    "access denied",
    "auth wall",
    "authentication required",
    "not authorized to access",
    "tryhackme room",
    "hack the box subscription",
    "htb subscription",
    "forbidden by platform",
)


def _detect_restricted_access(signals: str) -> bool:
    if not signals:
        return False
    haystack = signals.lower()
    return any(phrase in haystack for phrase in RESTRICTED_ACCESS_SIGNALS)


class SecuritySubagentCoordinator:
    """Orchestrate security task execution through background worker sessions."""

    def __init__(
        self,
        *,
        pool: SecurityTaskPool,
        coordinator_runtime_service: Any,
        session_store: Any,
        parent_context: dict[str, Any],
        max_workers: int = MAX_CONCURRENT_WORKERS,
        worker_model_key: str | None = None,
        poll_interval_seconds: float = 0.5,
        checkpoint_callback: CheckpointCallback | None = None,
        target_guard: Any = None,
        execution_monitor: Any = None,
        runner_lookup: Callable[[str], str] | None = None,
        max_reflect_attempts: int = 3,
        output_summary_threshold_bytes: int = DEFAULT_THRESHOLD_BYTES,
        task_refiner: SecuritySubtaskRefiner | None = None,
        scenario_incremental_planner: SecurityScenarioIncrementalPlanner | None = None,
        campaign: Any = None,
        request: Any = None,
        batch_hydrator: BatchHydrator | None = None,
        interrupt_check: InterruptCheck | None = None,
    ) -> None:
        self._pool = pool
        self._coordinator_runtime_service = coordinator_runtime_service
        self._session_store = session_store
        self._parent_context = parent_context
        self._max_workers = max(1, max_workers)
        self._worker_model_key = worker_model_key
        self._poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._checkpoint_callback = checkpoint_callback
        self._target_guard = target_guard
        self._execution_monitor = execution_monitor
        self._runner_lookup = runner_lookup
        self._max_reflect_attempts = max(0, max_reflect_attempts)
        self._output_summary_threshold_bytes = max(0, output_summary_threshold_bytes)
        self._task_refiner = task_refiner or SecuritySubtaskRefiner()
        self._scenario_incremental_planner = scenario_incremental_planner
        self._campaign = campaign
        self._request = request
        self._batch_hydrator = batch_hydrator
        self._refinement_pass = 0
        self._interrupt_check = interrupt_check
        self._interrupted = False
        self._activities: list[AgentActivityRecord] = []
        self._active_resource_locks: set[str] = set()
        # Mentor state: how many times each runner family has failed so far, and
        # the follow-up directive injected into remaining same-family tasks.
        self._family_failure_counts: dict[str, int] = {}
        self._mentor_directives: dict[str, str] = {}

    @property
    def activities(self) -> list[AgentActivityRecord]:
        return list(self._activities)

    async def run_all(self) -> list[SecurityTask]:
        """Execute all tasks in the pool, respecting dependencies and concurrency."""
        logger.info(
            "security_coordinator_start %s",
            self._log_payload(task_count=len(self._pool.all_tasks), max_workers=self._max_workers),
        )
        while not self._pool.is_complete:
            if await self._interrupt_if_requested():
                break
            self._pool.resolve_blocked()
            batch = self._select_batch()
            if not batch:
                if self._pool.has_running:
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                break
            logger.info(
                "security_coordinator_batch_selected %s",
                self._log_payload(task_ids=[task.task_id for task in batch]),
            )
            await self._dispatch_batch(batch)
            if self._interrupted:
                break
            self._refine_after_batch(batch)
            self._pool.resolve_blocked()

        # Retry failed tasks
        await self._retry_failed()

        logger.info(
            "security_coordinator_complete %s",
            self._log_payload(
                status_counts={
                    status: sum(1 for task in self._pool.all_tasks if task.status == status)
                    for status in sorted({task.status for task in self._pool.all_tasks})
                }
            ),
        )

        return self._pool.all_tasks

    async def _retry_failed(self) -> None:
        """Retry tasks that are eligible for retry."""
        retryable = self._pool.retryable_tasks()
        if not retryable:
            return

        for task in retryable:
            self._pool.reset_for_retry(task.task_id)

        # Run another pass
        while not self._pool.is_complete:
            if await self._interrupt_if_requested():
                break
            self._pool.resolve_blocked()
            batch = self._select_batch()
            if not batch:
                if self._pool.has_running:
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                break
            await self._dispatch_batch(batch)
            if self._interrupted:
                break
            self._refine_after_batch(batch)
            self._pool.resolve_blocked()

    def _refine_after_batch(self, settled_tasks: list[SecurityTask]) -> None:
        self._refinement_pass += 1
        refinement_id = f"batch_{self._refinement_pass}"
        if self._batch_hydrator is not None:
            try:
                self._batch_hydrator(settled_tasks)
            except Exception:
                logger.exception(
                    "security_scenario_batch_hydration_failed %s",
                    self._log_payload(refinement_id=refinement_id),
                )
        scenario_result: dict[str, Any] = {}
        if (
            self._scenario_incremental_planner is not None
            and self._campaign is not None
            and self._request is not None
        ):
            scenario_result = self._scenario_incremental_planner.replan(
                campaign=self._campaign,
                request=self._request,
                pool=self._pool,
                refinement_id=refinement_id,
            )
            if scenario_result.get("replanned"):
                logger.info(
                    "security.scenario.replanned %s",
                    self._log_payload(
                        refinement_id=refinement_id,
                        previous_scenario_id=scenario_result.get("previous_scenario_id"),
                        scenario_id=scenario_result.get("scenario_id"),
                        previous_product_type=scenario_result.get("previous_product_type"),
                        product_type=scenario_result.get("product_type"),
                        new_observed_facts=scenario_result.get("new_observed_facts"),
                        changed_dimensions=scenario_result.get("changed_dimensions"),
                        added_task_ids=scenario_result.get("added_task_ids"),
                        removed_task_ids=scenario_result.get("removed_task_ids"),
                        updated_task_ids=scenario_result.get("updated_task_ids"),
                    ),
                )
                self._activities.append(
                    AgentActivityRecord(
                        activity_id=f"scenario_replan_{refinement_id}",
                        agent_key="security-scenario-incremental-planner",
                        agent_name="security-scenario-incremental-planner",
                        action="scenario_replanned",
                        summary=(
                            f"Scenario changed from {scenario_result.get('previous_product_type')} "
                            f"to {scenario_result.get('product_type')}; "
                            f"added={len(scenario_result.get('added_task_ids') or [])}, "
                            f"removed={len(scenario_result.get('removed_task_ids') or [])}, "
                            f"updated={len(scenario_result.get('updated_task_ids') or [])}."
                        ),
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        execution_mode="scheduler",
                    )
                )
        result = self._task_refiner.refine_task_pool(
            pool=self._pool,
            settled_tasks=settled_tasks,
            refinement_id=refinement_id,
        )
        added = [
            *(scenario_result.get("added_tasks") or []),
            *(result.get("added_tasks") or []),
        ]
        removed = [
            *(scenario_result.get("removed_tasks") or []),
            *(result.get("removed_tasks") or []),
        ]
        updated = list(scenario_result.get("updated_tasks") or [])
        if not added and not removed and not updated:
            return
        logger.info(
            "security_refiner_applied %s",
            self._log_payload(
                refinement_id=refinement_id,
                added_task_ids=[task.task_id for task in added],
                removed_task_ids=[task.task_id for task in removed],
                updated_task_ids=[task.task_id for task in updated],
                task_count=self._pool.task_count,
            ),
        )
        for task in added:
            self._emit_checkpoint("task_refined", task)

    def _select_batch(self) -> list[SecurityTask]:
        """Select the next batch of tasks to dispatch."""
        ready = self._pool.ready_tasks()
        if not ready:
            return []

        # Sort by priority: tasks with fewer dependencies first, lower risk first
        ready.sort(key=lambda t: (len(t.depends_on), t.risk_level != "info"))

        batch: list[SecurityTask] = []
        running_count = len(self._pool.running_tasks())
        available_slots = self._max_workers - running_count

        for task in ready:
            if len(batch) >= available_slots:
                break
            if self._has_resource_conflict(task, batch):
                continue
            # High-risk tasks run alone
            if task.requires_approval:
                if not batch:
                    batch.append(task)
                break
            batch.append(task)

        return batch

    async def _interrupt_if_requested(
        self,
        *,
        task_ids: list[str] | None = None,
        child_session_ids: list[str] | None = None,
    ) -> bool:
        if self._interrupted:
            return True
        if self._interrupt_check is None or not self._interrupt_check():
            return False
        self._interrupted = True
        reason = "Parent security campaign interrupt requested."
        if self._coordinator_runtime_service is not None:
            await self._coordinator_runtime_service.cancel_workers(
                task_ids=list(task_ids or []),
                child_session_ids=list(child_session_ids or []),
                reason=reason,
            )
        interrupted_tasks = self._pool.interrupt_unsettled(reason)
        for task in interrupted_tasks:
            self._emit_checkpoint("task_interrupted", task)
        logger.info(
            "security_coordinator_interrupted %s",
            self._log_payload(
                task_ids=[task.task_id for task in interrupted_tasks],
                child_session_ids=list(child_session_ids or []),
            ),
        )
        return True

    def _has_resource_conflict(self, task: SecurityTask, batch: list[SecurityTask]) -> bool:
        """Check if a task conflicts with active or batched resource locks."""
        if not task.resource_locks:
            return False
        task_locks = set(task.resource_locks)
        if task_locks & self._active_resource_locks:
            return True
        for other in batch:
            if set(other.resource_locks) & task_locks:
                return True
        return False

    def _reject_if_out_of_scope(self, task: SecurityTask) -> bool:
        """Fail a task before dispatch when its target is outside the allowlist (S6).

        Returns True when the task was rejected (and already marked failed), so
        the caller skips dispatching it. A configured-but-empty allowlist never
        rejects; it only logs a warning for public targets.
        """
        guard = self._target_guard
        if guard is None:
            return False
        result = guard.evaluate_target(task.target)
        if result.ok:
            if getattr(result, "warn_public", False) and result.reason:
                logger.warning(
                    "security target allowlist warning: task=%s %s",
                    task.task_id,
                    result.reason,
                )
            return False
        logger.warning(
            "security target rejected before dispatch: task=%s target=%s reason=%s",
            task.task_id,
            task.target,
            result.reason,
        )
        task.failure_analysis = {
            "failure_category": "target_not_allowed",
            "root_cause": result.reason,
            "retryable": False,
            "suggested_fix": (
                "Add the target to security_target_allowlist or retest an in-scope target."
            ),
            "alternative_profile": "",
            "notes": f"target={task.target}",
        }
        # An out-of-scope target will never become in-scope on retry.
        task.max_retries = 0
        self._fail_task(task, f"target_not_allowed: {result.reason}")
        return True

    async def _dispatch_batch(self, batch: list[SecurityTask]) -> None:
        """Dispatch a batch of tasks to worker agents."""
        launched_tasks: list[SecurityTask] = []
        try:
            for task in batch:
                if self._reject_if_out_of_scope(task):
                    continue
                self._pool.mark_running(task.task_id)
                task.worker_execution_mode = "subagent_session"
                task.worker_status = "dispatching"
                self._emit_checkpoint("task_running", task)
                for lock in task.resource_locks:
                    self._active_resource_locks.add(lock)
                launched_tasks.append(task)

            if not launched_tasks:
                # Every task in this batch was rejected by the allowlist gate.
                # Nothing to dispatch; the outer loop will settle the campaign.
                return

            # Build worker specs
            workers = [self._build_worker_spec(task) for task in launched_tasks]
            logger.info(
                "security_workers_dispatching %s",
                self._log_payload(
                    workers=[
                        {
                            "task_id": task.task_id,
                            "agent_key": task.worker_agent_key,
                            "profile": task.command_profile,
                            "family": task.tool_family,
                            "target": task.target,
                        }
                        for task in launched_tasks
                    ]
                ),
            )

            # Dispatch via coordinator runtime service
            dispatch_result = await self._coordinator_runtime_service.dispatch(
                payload={"workers": workers},
                context=self._parent_context,
            )

            worker_records = {
                str(item.get("task_id") or ""): item
                for item in dispatch_result.get("workers", [])
                if isinstance(item, dict)
            }

            # Collect child session IDs
            child_session_ids = [
                str(record.get("child_session_id") or "")
                for record in worker_records.values()
                if str(record.get("status") or "") == "running"
                and str(record.get("child_session_id") or "")
            ]

            # Wait for all sessions to complete
            settled_sessions = await self._wait_for_sessions(
                child_session_ids,
                task_ids=[task.task_id for task in launched_tasks],
            )
            if self._interrupted:
                return
            logger.info(
                "security_workers_settled %s",
                self._log_payload(
                    child_session_ids=child_session_ids,
                    settled_count=len(settled_sessions),
                ),
            )
            settled_map = {session.id: session for session in settled_sessions}

            # Process results
            for task in launched_tasks:
                record = worker_records.get(task.task_id)
                if not record:
                    self._fail_task(task, "worker_dispatch_missing")
                    continue

                child_session_id = str(record.get("child_session_id") or "")
                task.worker_session_id = child_session_id

                session = settled_map.get(child_session_id)
                if session is None:
                    self._fail_task(task, "worker_session_not_found")
                    continue

                session_status_value = session.status.value
                task.worker_status = session_status_value
                tool_output = self._extract_runner_output(session.messages)
                assistant_summary = self._extract_assistant_summary(session.messages)

                if tool_output:
                    self._apply_worker_output(task, tool_output)
                elif session_status_value == "waiting_approval":
                    # The worker paused for approval before invoking the
                    # runner. Treat the task as failed-with-context so the
                    # campaign can settle and report on the approval gap,
                    # rather than retrying forever or claiming success.
                    task.failure_analysis = {
                        "failure_category": "approval_required",
                        "root_cause": (
                            "Worker session is waiting for tool approval and never produced a runner result."
                        ),
                        "retryable": False,
                        "suggested_fix": "Approve the pending tool request or lower the requested risk level.",
                        "alternative_profile": "",
                        "notes": assistant_summary[:500],
                    }
                    self._fail_task(
                        task,
                        "approval_required: worker paused before runner execution",
                    )
                elif session_status_value in {"running", "idle"}:
                    # The worker is still in a non-terminal state when the
                    # wait loop deadline expired. Treat as timeout so the
                    # campaign settles instead of looping.
                    task.failure_analysis = {
                        "failure_category": "worker_timeout",
                        "root_cause": (
                            "Worker child session did not reach a terminal state before the wait deadline."
                        ),
                        "retryable": False,
                        "suggested_fix": (
                            "Investigate why the worker session is stuck (model hang, infinite loop, "
                            "or downstream service hang) before retrying."
                        ),
                        "alternative_profile": "",
                        "notes": assistant_summary[:500],
                    }
                    # Disable retries so we don't spin again on the same hang.
                    task.max_retries = 0
                    self._fail_task(
                        task,
                        f"worker_timeout: child session never settled (status={session_status_value})",
                    )
                elif assistant_summary:
                    # The worker completed the conversation but did not invoke
                    # any runner tool. Before recording a coverage gap, give the
                    # Reflector a bounded chance to re-dispatch with an explicit
                    # instruction to call the runner (S2).
                    task.result_summary = assistant_summary
                    if self._maybe_reflect(task, assistant_summary):
                        continue
                    # We cannot treat this as a successful security finding —
                    # there is no structured evidence to back it. Surface this
                    # as a failed task so the campaign report records the gap.
                    task.failure_analysis = {
                        "failure_category": "no_runner_output",
                        "root_cause": (
                            "Worker session ended without invoking a security runner tool."
                        ),
                        "retryable": False,
                        "suggested_fix": (
                            "Tighten the worker prompt so the agent always invokes the assigned runner."
                        ),
                        "alternative_profile": "",
                        "notes": assistant_summary[:500],
                    }
                    self._fail_task(
                        task,
                        f"no_runner_output: worker finished without runner evidence ({session_status_value})",
                    )
                else:
                    if self._maybe_reflect(task, assistant_summary):
                        continue
                    self._fail_task(task, f"Worker finished without runner output ({session_status_value})")

                # Record activity
                self._record_activity(task, assistant_summary)

            # Mentor: escalate repeatedly-failing families for this batch (S2).
            self._apply_mentor_feedback(launched_tasks)

        except Exception as e:
            logger.exception("security_batch_dispatch_failed %s", self._log_payload(error=str(e)))
            for task in launched_tasks:
                if task.status == TASK_RUNNING:
                    self._fail_task(task, f"dispatch_error: {e}")
        finally:
            for task in launched_tasks:
                for lock in task.resource_locks:
                    self._active_resource_locks.discard(lock)

    def _build_worker_spec(self, task: SecurityTask) -> dict[str, Any]:
        """Build a worker dispatch specification for a task."""
        agent_key = task.worker_agent_key or resolve_security_worker_agent(
            surface_type=task.surface_type,
            tool_family=task.tool_family,
            command_profile=task.command_profile,
        )
        parent_bundle = self._parent_context.get("context_bundle", {})
        if not isinstance(parent_bundle, dict):
            parent_bundle = {}
        parent_state = parent_bundle.get("security_testing_state")
        parent_request = parent_state.get("request") if isinstance(parent_state, dict) else None
        if not isinstance(parent_request, dict):
            parent_request = {}
        risk_tolerance = str(
            getattr(self._request, "risk_tolerance", "")
            or parent_request.get("risk_tolerance")
            or parent_bundle.get("risk_tolerance")
            or "medium"
        ).strip().lower()

        runner_args: dict[str, Any] = {
            "worker_action": "execute_security_task",
            "task": task.model_dump(mode="json"),
            "command_profile": task.command_profile,
            "target": task.target,
        }
        runner_key = self._runner_lookup(task.tool_family) if self._runner_lookup else ""

        return {
            "task_id": task.task_id,
            "description": f"[{task.surface_type}] {task.name} -> {task.target}",
            "prompt": build_security_worker_prompt(
                task,
                agent_key=agent_key,
                runner_args=runner_args,
                directives=self._build_task_directives(task),
            ),
            "agent_key": agent_key,
            "model_key": self._worker_model_key,
            "context": {
                "dispatch_role": "security_testing_worker",
                "mode_key": "security_testing",
                "security_task_id": task.task_id,
                "security_runtime_arguments": runner_args,
                "risk_tolerance": risk_tolerance,
                "surface_type": task.surface_type,
                "tool_family": task.tool_family,
                "command_profile": task.command_profile,
                # Server-generated worker context asks the generic router to
                # expose the one assigned runner immediately. Capability,
                # agent support, safety and approval policies still apply.
                "requested_tool_keys": [runner_key] if runner_key else [],
                "target_fingerprint": str(parent_bundle.get("target_fingerprint") or ""),
                "campaign_id": str(parent_bundle.get("campaign_id") or ""),
                "platform_label": str(parent_bundle.get("platform_label") or ""),
                "security_memory_scope": "session_only",
            },
        }

    def _build_task_directives(self, task: SecurityTask) -> list[str]:
        """Assemble scheduler feedback directives injected into the worker prompt (S2)."""
        directives: list[str] = []
        if task.reflect_attempts > 0:
            runner_key = self._runner_lookup(task.tool_family) if self._runner_lookup else ""
            directives.append(
                "You previously finished WITHOUT invoking the assigned security runner tool. "
                f"You MUST call the runner tool for command_profile '{task.command_profile}'"
                + (f" (runner {runner_key})" if runner_key else "")
                + " and return the structured JSON result (success, summary, findings, "
                "evidence, raw_output). Prose-only answers are rejected."
            )
        mentor = self._mentor_directives.get(task.tool_family or "")
        if mentor:
            directives.append(mentor)
        return directives

    def _maybe_reflect(self, task: SecurityTask, assistant_summary: str) -> bool:
        """Reflector: re-dispatch a task that returned no structured runner output.

        Bounded by ``max_reflect_attempts`` (default 3, aligned with pentagi's
        Reflector). Returns True when a reflect retry was scheduled so the
        caller must skip failing the task. Only triggers on the genuine
        "no structured result" case, never on a task that already has runner
        output (from-wide principle: do not misfire on slightly-off formats).
        """
        if self._max_reflect_attempts <= 0:
            return False
        if task.reflect_attempts >= self._max_reflect_attempts:
            return False
        task.reflect_attempts += 1
        logger.info(
            "Reflector re-dispatch: task=%s attempt=%d/%d (no structured runner output)",
            task.task_id,
            task.reflect_attempts,
            self._max_reflect_attempts,
        )
        self._activities.append(
            AgentActivityRecord(
                activity_id=f"reflect_{task.task_id}_{task.reflect_attempts}",
                agent_key=task.worker_agent_key or "",
                agent_name="reflector",
                task_id=task.task_id,
                action="reflected",
                summary=(assistant_summary or "")[:500],
                started_at=task.started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                execution_mode=task.worker_execution_mode or "subagent_session",
            )
        )
        self._pool.reset_for_reflect(task.task_id)
        return True

    def _apply_mentor_feedback(self, settled_tasks: list[SecurityTask]) -> None:
        """Mentor: after a batch settles, escalate repeatedly-failing families.

        When a tool family's failure count reaches the execution monitor's
        threshold, register an ``APPROACH CHANGE REQUIRED`` directive that is
        injected into every subsequent (retried / reflected) task of that
        family, instead of letting the campaign retry the same failing approach
        indefinitely (QA-Agent rule: failure >= N must switch root approach).
        """
        if self._execution_monitor is None:
            return
        threshold = max(1, int(getattr(self._execution_monitor, "max_consecutive_runner_failures", 2)))
        for task in settled_tasks:
            if str(task.status or "") != TASK_FAILED:
                continue
            family = task.tool_family or ""
            self._family_failure_counts[family] = self._family_failure_counts.get(family, 0) + 1
            count = self._family_failure_counts[family]
            if count >= threshold and family not in self._mentor_directives:
                runner_key = self._runner_lookup(family) if (self._runner_lookup and family) else ""
                directive = (
                    f"APPROACH CHANGE REQUIRED: the {family or 'previous'} approach"
                    + (f" (runner {runner_key})" if runner_key else "")
                    + f" failed {count} time(s). Do not repeat the same failing profile/command. "
                    "Do not change the assigned command_profile inside the current task; "
                    "return a structured failure so the Refiner can create a separately tracked alternative. "
                    "Try a materially different in-scope technique only when it is dispatched as a new task. "
                    "Reference, not mandate: if this approach keeps failing in this environment, abandon it."
                )
                self._mentor_directives[family] = directive
                logger.warning(
                    "Mentor directive activated: family=%s failures=%d threshold=%d",
                    family,
                    count,
                    threshold,
                )
                self._activities.append(
                    AgentActivityRecord(
                        activity_id=f"mentor_{family or 'unknown'}_{count}",
                        agent_key="",
                        agent_name="mentor",
                        task_id=task.task_id,
                        action="approach_change_required",
                        summary=directive,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        execution_mode="scheduler",
                    )
                )

    async def _wait_for_sessions(
        self,
        child_session_ids: list[str],
        *,
        overall_timeout_seconds: float | None = None,
        task_ids: list[str] | None = None,
    ) -> list[Any]:
        """Wait for child sessions to reach a terminal state with a hard deadline.

        This is the single point in the campaign loop that must NEVER block
        indefinitely. Three exits are possible:

        1. The session reaches ``completed`` / ``failed`` / ``interrupted``.
        2. The session sits in ``waiting_approval`` longer than
           ``max_approval_polls`` (so the caller can classify the task as
           approval-pending instead of spinning).
        3. The overall wait exceeds ``overall_timeout_seconds`` (default
           ``TOOL_EXEC_TIMEOUT_SECONDS * 2``). Any session still in flight is
           surfaced as ``timed_out`` and the caller decides how to record it.

        Without the third exit the entire security mode runtime can hang
        forever when even one worker child session gets stuck in
        ``running`` — which is exactly what was observed in production
        runs against PortSwigger / TryHackMe / HTB.
        """
        from src.schemas.session import SessionStatus

        pending = {sid for sid in child_session_ids if sid}
        settled: dict[str, Any] = {}
        approval_wait_counts: dict[str, int] = {}
        max_approval_polls = 60  # ~30s at default 0.5s interval

        deadline: float | None = None
        if overall_timeout_seconds is None:
            overall_timeout_seconds = float(TOOL_EXEC_TIMEOUT_SECONDS * 2)
        if overall_timeout_seconds > 0:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + overall_timeout_seconds

        while pending:
            if await self._interrupt_if_requested(
                task_ids=task_ids,
                child_session_ids=list(pending),
            ):
                break
            completed_ids: list[str] = []
            for session_id in list(pending):
                session = await self._session_store.get_session(session_id)
                if session is None:
                    completed_ids.append(session_id)
                    continue
                if session.status in {
                    SessionStatus.completed,
                    SessionStatus.failed,
                    SessionStatus.interrupted,
                }:
                    settled[session_id] = session
                    completed_ids.append(session_id)
                    continue
                if session.status == SessionStatus.waiting_approval:
                    approval_wait_counts[session_id] = approval_wait_counts.get(session_id, 0) + 1
                    if approval_wait_counts[session_id] >= max_approval_polls:
                        settled[session_id] = session
                        completed_ids.append(session_id)
            for sid in completed_ids:
                pending.discard(sid)
            if not pending:
                break
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                # Hard timeout: surface whatever state the remaining sessions
                # are in so the caller can record them as timed_out without
                # blocking the entire campaign.
                logger.warning(
                    "Security worker wait timed out after %.0fs; %d session(s) still pending",
                    overall_timeout_seconds,
                    len(pending),
                )
                for stuck_id in list(pending):
                    stuck_session = await self._session_store.get_session(stuck_id)
                    if stuck_session is not None:
                        settled[stuck_id] = stuck_session
                pending.clear()
                break
            await asyncio.sleep(self._poll_interval_seconds)

        return list(settled.values())

    def _apply_worker_output(self, task: SecurityTask, tool_output: dict[str, Any]) -> None:
        """Apply structured worker output to the task."""
        reported_profile = str(tool_output.get("command_profile") or "").strip()
        if reported_profile and reported_profile != task.command_profile:
            # A model may choose an alternative tool after a failure, but that
            # is a new route and must not rewrite the identity of the task it
            # was assigned. Keeping this task failed preserves auditability and
            # lets the Refiner create an explicit alternative task.
            task.result_summary = (
                f"Worker executed profile {reported_profile}, but task was assigned "
                f"{task.command_profile}. Alternative profile was not accepted."
            )
            task.failure_analysis = {
                "failure_category": "profile_identity_mismatch",
                "root_cause": task.result_summary,
                "retryable": False,
                "alternative_profile": reported_profile,
            }
            self._fail_task(task, task.result_summary)
            logger.warning(
                "security_task_profile_identity_mismatch %s",
                self._log_payload(
                    task_id=task.task_id,
                    assigned_profile=task.command_profile,
                    reported_profile=reported_profile,
                ),
            )
            return
        task.result_summary = str(tool_output.get("summary") or "")
        # S4: preserve ports/CVE/URL/vuln signals instead of a blind head cut.
        task.raw_output = compact_security_output(
            str(tool_output.get("raw_output") or ""),
            max_bytes=self._output_summary_threshold_bytes,
        )
        task.parsed_result = tool_output.get("parsed_result") or {}

        # Check if worker reported success
        success = bool(tool_output.get("success") or tool_output.get("ok"))
        if success:
            self._pool.mark_completed(task.task_id, task.result_summary)
            self._emit_checkpoint("task_completed", task)
            # Collect findings/artifacts only on success
            findings = tool_output.get("findings") or []
            if findings:
                task.finding_refs = [f.get("finding_id", "") for f in findings if isinstance(f, dict)]
            artifacts = tool_output.get("artifacts") or []
            if artifacts:
                task.artifacts = [
                    str(a.get("artifact_id") or a.get("path") or a.get("filename") or "")
                    for a in artifacts
                    if isinstance(a, dict)
                ]
            return

        error = tool_output.get("error") or task.result_summary or "execution_failed"
        # Detect restricted-access conditions early so the campaign does not
        # spin on retries against platforms that cannot be probed without
        # additional credentials or VPN access.
        signals = " ".join(
            str(value)
            for value in (
                task.result_summary,
                task.raw_output,
                error,
                tool_output.get("status"),
                str(tool_output.get("parsed_result") or ""),
            )
            if value
        )
        if _detect_restricted_access(signals):
            task.failure_analysis = {
                "failure_category": "restricted_access",
                "root_cause": (
                    "Target platform requires additional access (login, subscription, VPN, "
                    "or lab activation) that the runner could not satisfy."
                ),
                "retryable": False,
                "suggested_fix": (
                    "Provide platform credentials, deploy the target lab, or run from an "
                    "authorized network before retrying."
                ),
                "alternative_profile": "",
                "notes": str(error)[:500],
            }
            # Disable retries for this task so retry_failed() will skip it.
            task.max_retries = 0
            self._fail_task(task, f"restricted_access: {error}")
            return

        self._fail_task(task, error)

    def _fail_task(self, task: SecurityTask, error: str) -> None:
        """Mark a task as failed."""
        task.last_error = str(error or "execution_failed")
        self._pool.mark_failed(task.task_id, task.last_error)
        self._emit_checkpoint("task_failed", task)

    def _emit_checkpoint(self, event_type: str, task: SecurityTask) -> None:
        if self._checkpoint_callback is None:
            return
        try:
            self._checkpoint_callback(event_type, task, self._pool.all_tasks)
        except Exception:
            return

    def _record_activity(self, task: SecurityTask, summary: str = "") -> None:
        """Record an agent activity for this task."""
        agent_key = task.worker_agent_key or resolve_security_worker_agent(
            surface_type=task.surface_type,
            tool_family=task.tool_family,
            command_profile=task.command_profile,
        )
        action = "completed" if task.status == TASK_COMPLETED else "failed"
        duration = 0.0
        if task.started_at and task.completed_at:
            try:
                start = datetime.fromisoformat(task.started_at)
                end = datetime.fromisoformat(task.completed_at)
                duration = (end - start).total_seconds()
            except (ValueError, TypeError):
                pass

        self._activities.append(AgentActivityRecord(
            activity_id=f"act_{task.task_id}",
            agent_key=agent_key,
            agent_name=agent_key,
            task_id=task.task_id,
            action=action,
            summary=summary or task.result_summary or task.last_error or "",
            started_at=task.started_at,
            completed_at=task.completed_at or datetime.now(timezone.utc).isoformat(),
            duration_seconds=duration,
            execution_mode=task.worker_execution_mode or "subagent_session",
        ))

    def _log_payload(self, **values: Any) -> str:
        payload = {
            "session_id": str(self._parent_context.get("session_id") or ""),
            "turn_id": str(self._parent_context.get("turn_id") or ""),
            "trace_id": str(self._parent_context.get("trace_id") or ""),
            **values,
        }
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    def _extract_runner_output(self, messages: list[Any]) -> dict[str, Any] | None:
        """Extract the security runner tool output from session messages."""
        from src.schemas.session import MessageRole

        runner_keys = {
            "security-scan-runner",
            "network-recon-runner",
            "web-scan-runner",
            "service-audit-runner",
            "credential-attack-runner",
            "traffic-analysis-runner",
            "exploit-workbench-runner",
        }

        for message in reversed(messages):
            if message.role != MessageRole.tool:
                continue
            tool_key = str(message.metadata.get("tool_key") or "")
            if tool_key not in runner_keys:
                continue
            parsed = self._parse_tool_payload(str(message.content or ""))
            if isinstance(parsed, dict):
                return parsed
        return None

    def _parse_tool_payload(self, content: str) -> dict[str, Any] | None:
        """Parse JSON payload from tool message content."""
        if not content:
            return None
        # Try to find JSON in the content
        payload_text = content.split("\n\n", 1)[1] if "\n\n" in content else content
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            # Try the whole content
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return None
        return parsed if isinstance(parsed, dict) else None

    def _extract_assistant_summary(self, messages: list[Any]) -> str:
        """Extract the last assistant message as summary."""
        from src.schemas.session import MessageRole

        for message in reversed(messages):
            if message.role == MessageRole.assistant:
                return str(message.content or "").strip()
        return ""


__all__ = ["SecuritySubagentCoordinator", "CheckpointCallback"]
