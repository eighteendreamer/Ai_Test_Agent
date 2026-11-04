"""One current parking basis; historical root admissions never change.

No upstream counterpart (checked: initial wait, progress/re-park original
readers, dedicated continuation, v4 §7.3/§9.2): material retention alone
does not publish a new parking. Reuse their source readers, not a second
native parser or a fabricated admission/owner.
"""
from dataclasses import dataclass
import logging

from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.task_deferred import TaskDeferred
from src.schemas.controlled_case_admission import read_wait_checkpoint


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentApprovalWait:
    receipt: object
    producer: dict
    state_asset: object | None


def read_current_approval_wait(cur, submission, command, case, session, owner, root, origin, capabilities):
    """Read locked originals only; callers still check current execution rights.

    A running owner may have retained its own material but not yet parked.
    Only that final asset may trail the published parking basis. All earlier
    assets require their original atomic parking receipts, including returns.
    The historical continuation reader deliberately never calls this reader.
    """
    receipt, producer, state = root, origin, None
    if case.attempt.checkpoint_version in {7, 8}:
        initial = read_wait_checkpoint(case.attempt)
        state = initial.state_asset
    if case.attempt.checkpoint_version == 8:
        from src.runtime.controlled_case_approval_continuation import ControlledCaseApprovalContinuations
        from src.runtime.controlled_case_approval_repark import ControlledCaseApprovalReparks
        from src.schemas.controlled_case_admission import ControlledCaseApprovalProgressCheckpoint

        checkpoint = ControlledCaseApprovalProgressCheckpoint.model_validate(case.attempt.checkpoint_payload)
        shared = ControlledCaseApprovalContinuations(submission)
        reparks = ControlledCaseApprovalReparks(shared)
        last = checkpoint.progress_assets[-1]
        saved = shared._locked(cur, shared._wire(cur, last.task_id, capabilities), capabilities)
        _, assets = reparks._progress._read_chain(cur, saved, capabilities)
        retained_by_active_owner = owner['execution_state'] in {'claimed', 'running'} and (
            last.task_id, last.attempt_id, last.owner_epoch) == (
                owner['task_id'], owner['attempt_id'], owner['owner_epoch'])
        parked = assets[:-1] if retained_by_active_owner else assets
        if parked and not capabilities.controlled_case_approval_successor_available:
            raise TaskDeferred("framework_case_approval_successor_unavailable")
        for asset in parked:
            original = saved if asset.task_id == last.task_id else shared._locked(
                cur, shared._wire(cur, asset.task_id, capabilities), capabilities)
            receipt = reparks._read_receipt(cur, original, asset, capabilities)
            producer, state = original.task, asset
    if (session['metadata'].get('pending_turn') or {}).get('controlled_case_wait_sha256') != receipt.receipt_sha256:
        raise FrameworkApprovalRecoveryRequired("Formal current parking differs from its original published receipt")
    logger.info("controlled_case_current_parking_read", extra={"task_id": producer['id'],
        "binding_id": command.runtime_binding.binding_id, "wait_sha256": receipt.receipt_sha256,
        "root_wait_sha256": root.receipt_sha256, "state_asset_sha256": state.asset_sha256 if state else None,
        "automatic_replay": False})
    return CurrentApprovalWait(receipt, producer, state)
