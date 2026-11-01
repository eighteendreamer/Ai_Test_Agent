"""Prepare formal values only; admission must recheck locked live rows later."""
from __future__ import annotations

import logging

from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.plan_snapshot_identity import execution_attempt, validate_claimed_plan_snapshot
from src.schemas.controlled_case_graph_input import freeze_controlled_graph_input
from src.schemas.controlled_case_task_input import ControlledCaseTaskInputAsset, parse_controlled_case_task_input
from src.schemas.framework_approval import approval_value_hash


logger = logging.getLogger(__name__)


def prepare_controlled_case_input(*, detail, item, version, attempt_id, binding, graph_request) -> ControlledCaseTaskInputAsset:
    """Reuse formal context and claim pin, without writes, model or tool calls.

    This observed lease identity is not a current lease check: no DB clock,
    row lock or immutable admission CAS is acquired by this pure adapter.
    The supplied graph request must still be assembled by the server's full
    input pipeline; this codec does not claim that assembly is implemented.
    """
    scope = {"run_id": detail.run.id, "run_item_id": item.id, "attempt_id": attempt_id}
    reason = "original_execution_identity_invalid"
    try:
        stored = [entry for entry in detail.items if entry.id == item.id]
        attempt = execution_attempt(detail, item.id, attempt_id)
        if (len(stored) != 1 or len([entry for entry in detail.attempts if entry.id == attempt_id]) != 1):
            raise ValueError("Controlled case preparation requires unique formal execution identities")
        current = stored[0]
        reason = "run_item_attempt_identity_mismatch"
        if (any(getattr(current, key) != getattr(item, key) for key in (
                "run_id", "case_id", "case_version_id", "plan_item_key", "attempt_no", "lease_token", "lease_owner", "status"))
            or current.run_id != detail.run.id or current.status != "running" or attempt.status != "running"
            or not current.lease_token or attempt.lease_token != current.lease_token
            or attempt.attempt_no != current.attempt_no or attempt.worker_id != current.lease_owner):
            raise ValueError("Controlled case preparation differs from its running RunItem and attempt lease identity")
        reason = "execution_already_started"
        if (attempt.checkpoint_version or attempt.checkpoint_key is not None or attempt.checkpoint_payload
            or attempt.checkpoint_at is not None or attempt.recovered_from_attempt_id is not None):
            raise ValueError("Controlled case preparation cannot re-freeze an existing or recovered execution")
        reason = "claimed_plan_pin_missing"
        if attempt.plan_snapshot_identity is None:
            raise ValueError("Controlled case preparation requires the original claimed plan pin")
        reason = "claimed_plan_snapshot_mismatch"
        validate_claimed_plan_snapshot(detail.run, attempt)
        reason = "formal_case_context_invalid"
        context = build_agent_case_context(detail, current, version)
        reason = "controlled_graph_input_invalid"
        graph_input = freeze_controlled_graph_input(binding=binding, request=graph_request)
        reason = "controlled_case_input_invalid"
        payload = {"schema_version": 1, "context": context.model_dump(mode="json"),
            "run_item_execution_attempt_id": attempt.id,
            "plan_snapshot_identity": attempt.plan_snapshot_identity.model_dump(mode="json"),
            "graph_input": graph_input.model_dump(mode="json")}
        asset = parse_controlled_case_task_input({**payload, "asset_sha256": approval_value_hash(payload)})
    except (TypeError, ValueError) as exc:
        logger.warning("controlled_case_input_preparation_rejected", extra={**scope,
            "recovery_reason": reason, "error_type": type(exc).__name__})
        raise
    logger.info("controlled_case_input_prepared", extra={**scope, "project_id": context.project_id,
        "binding_id": graph_input.binding.binding_id, "asset_sha256": asset.asset_sha256,
        "artifact_id": asset.asset_sha256})
    return asset
