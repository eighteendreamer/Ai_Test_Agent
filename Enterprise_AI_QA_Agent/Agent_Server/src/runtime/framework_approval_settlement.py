"""Finish a proven projected approval without granting execution or writes.

No upstream counterpart (checked: existing approval reconciliation/finalization,
message settling, v4 §10.1 A2-3): task settlement is a platform transaction, not
a native continuation. Keep this grant outside immutable result/commit assets.
"""
from __future__ import annotations

from src.runtime.framework_approval_finalization import _context, _material, _require, _save, _stage, OWNER_FIELDS
from src.runtime.framework_application_projection import require_application_commit
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_message_stage import FrameworkApprovalSettlementProof
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ExecutionRequest


SCOPE = "application_settlement"


def is_settlement_task(row):
    proof = (row["input_payload"] or {}).get("recovery_proof") or {}
    return isinstance(proof, dict) and proof.get("scope") == SCOPE


def _committed(cur, settings, session, binding, stage):
    _require(stage is not None and stage.phase == "projected", "Approval settlement requires projected stage")
    request = ExecutionRequest.model_validate(stage.execution_request)
    _require(request.runtime_binding == binding and request.session_id == binding.session_id
        and request.turn_id == binding.turn_id, "Approval settlement execution request changed")
    commit = require_application_commit(cur, settings, session, binding, stage)
    _require(stage.result_reference == (commit.output_message_id or commit.snapshot_id),
        "Projected approval differs from its committed result reference")
    return commit


def _latest(stage, asset):
    links = stage.approval_settlements or stage.approval_finalizations
    if links:
        link = links[-1]
        return link.task_id, link.owner_attempt_id, link.owner_epoch
    return asset.task_id, asset.attempt_id, asset.owner_epoch


def _require_input(cur, settings, row, binding):
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore

    expected = (PostgresAgentTaskStore._input_hash("approval_resume_v2", {
        "decision": (row["input_payload"].get("approval") or {}).get("status"),
        "runtime_binding_fingerprint": binding.fingerprint}, reference_id=row["reference_id"])
        if row["operation"] == "approval_resume" else approval_value_hash(row["input_payload"]))
    _require(row["input_hash"] == expected, "Approval settlement predecessor input changed")
    if row["operation"] == "approval_recover":
        from src.runtime.framework_approval_recovery import require_successor_lineage

        require_successor_lineage(cur, settings, row)


def prepare_approval_settlement(cur, settings, session, binding, origin):
    stage = _stage(cur, settings, binding)
    if stage is None or stage.phase != "projected":
        return None
    commit = _committed(cur, settings, session, binding, stage)
    asset, producer = _material(cur, settings, binding, stage)
    expected = _latest(stage, asset)
    _require((origin["id"], origin["attempt_id"]) == expected[:2] and expected[2] is not None,
        "Approval settlement origin is not the latest predecessor")
    budget = _context(cur, settings, session, binding, stage, asset, origin)
    _require_input(cur, settings, origin, binding)
    return stage, asset, budget, expected[2]


def validate_settlement_link(cur, settings, row, *, current=False, adopting=False):
    binding = RuntimeBinding.model_validate(row["runtime_binding"])
    stage = _stage(cur, settings, binding)
    cur.execute(f"SELECT * FROM {settings.database.postgres_session_table} WHERE id=%s", (binding.session_id,))
    session = cur.fetchone()
    commit = _committed(cur, settings, session, binding, stage)
    asset, producer = _material(cur, settings, binding, stage)
    proof = FrameworkApprovalSettlementProof.model_validate((row["input_payload"] or {}).get("recovery_proof"))
    links = [link for link in stage.approval_settlements if link.task_id == row["id"]]
    _require(len(links) == 1, "Approval settlement has no scoped admission")
    link = links[0]
    envelope = ("session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id", "root_trace_id")
    _require(row["operation"] == "approval_recover" and all(row[field] == producer[field] for field in envelope)
        and row["reference_id"] == producer["reference_id"]
        and row["input_hash"] == approval_value_hash(row["input_payload"])
        and row["input_payload"].get("approval") == producer["input_payload"].get("approval")
        and row["input_payload"].get("decision_id") == producer["input_payload"].get("decision_id"),
        "Approval settlement successor changed its original task envelope")
    _require(proof.settlement.model_dump(mode="json") == link.model_dump(mode="json", exclude=OWNER_FIELDS)
        and proof.origin_task_id == link.predecessor_task_id and proof.origin_attempt_id == link.predecessor_attempt_id,
        "Approval settlement successor differs from its frozen lineage")
    _require(adopting or link.owner_attempt_id is None or row["attempt_id"] == link.owner_attempt_id,
        "Approval settlement captured task attempt changed")
    cur.execute(f"SELECT * FROM {settings.database.postgres_agent_task_table} WHERE id=%s", (link.predecessor_task_id,))
    origin = cur.fetchone()
    _require(origin is not None and origin["attempt_id"] == link.predecessor_attempt_id,
        "Approval settlement predecessor attempt changed")
    _require_input(cur, settings, origin, binding)
    budget = _context(cur, settings, session, binding, stage, asset, origin, current=current)
    _require(link.budget_sha256 == approval_value_hash(budget.model_dump(mode="json"))
        and link.source_sha256 == stage.source_sha256 and link.execution_sha256 == stage.execution_sha256
        and link.result_reference == stage.result_reference
        and link.result_sha256 == approval_value_hash(commit.model_dump(mode="json"))
        and link.model_calls_sha256 == commit.model_calls_sha256,
        "Approval settlement committed facts changed after admission")
    return stage, asset, link, session, commit


def require_settlement_claim(cur, settings, row, owner):
    stage, asset, link, session, commit = validate_settlement_link(cur, settings, row, current=True)
    _require(stage.approval_settlements[-1].task_id == row["id"]
        and owner["task_id"] in {row["parent_task_id"], row["id"]},
        "Approval settlement is not the current successor")
    if link.owner_attempt_id is not None:
        _require(row["status"] in {"claimed", "waiting_resource"}
            and (owner["task_id"], owner["attempt_id"], owner["owner_epoch"])
                == (row["id"], link.owner_attempt_id, link.owner_epoch),
            "Approval settlement captured claim changed")


def adopt_settlement_owner(cur, settings, row, owner):
    from src.runtime.postgres_runtime_owner import assert_runtime_owner

    assert_runtime_owner(cur, settings, owner, states=("claimed",))
    stage, asset, link, session, commit = validate_settlement_link(cur, settings, row, current=True, adopting=True)
    _require(stage.approval_settlements[-1].task_id == row["id"], "Approval settlement is no longer current")
    adopted = link.model_copy(update={"owner_attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch})
    return _save(cur, settings, stage.model_copy(update={
        "approval_settlements": [*stage.approval_settlements[:-1], adopted]}))


def require_projected_approval_settlement(cur, settings, session, row, owner, stage):
    commit = _committed(cur, settings, session, owner.binding, stage)
    asset, producer = _material(cur, settings, owner.binding, stage)
    _require((row["id"], row["attempt_id"], owner.owner_epoch) == _latest(stage, asset),
        "Projected approval settlement has no current owner admission")
    if row["operation"] == "approval_recover":
        from src.runtime.framework_approval_finalization import is_finalization_task
        from src.runtime.framework_approval_recovery import require_successor_lineage, require_successor_proof

        if is_settlement_task(row) or is_finalization_task(row):
            require_successor_proof(cur, settings, row)
        else:
            # The original native successor has already produced this commit.
            # Recheck its issuance, not eligibility for a second graph run.
            proof = require_successor_lineage(cur, settings, row)
            _require(row["id"] == asset.task_id and proof.get("schema_version") == 1
                and proof.get("scope") is None, "Projected producer has no original native recovery lineage")
    _context(cur, settings, session, owner.binding, stage, asset, row, recovering=False)
    _require_input(cur, settings, row, owner.binding)
    return commit
