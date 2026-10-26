"""Fence the original input/orchestration/runtime submission boundaries.

No upstream counterpart (checked: submit_v2, FrameworkApprovalRecovery,
LangGraph persistence, plan §14.2): pre-graph application phases have no native
graph task position. This journal records facts; it never creates successors.
"""
from __future__ import annotations

import asyncio
import json
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction, runtime_owner_table
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_message_stage import FrameworkMessageStage


logger = logging.getLogger(__name__)


class FrameworkMessageStageRecoveryRequired(RuntimeError):
    pass


def validate_message_stage(record):
    stage = FrameworkMessageStage.model_validate(record)
    if stage.schema_version == 1 and stage.recoveries:
        raise FrameworkMessageStageRecoveryRequired("Initial message stage v1 has no recovery lineage contract")
    predecessor, identities = stage.task_id, {stage.task_id}
    for link in stage.recoveries:
        if link.predecessor_task_id != predecessor or link.task_id in identities:
            raise FrameworkMessageStageRecoveryRequired("Initial message stage recovery lineage is invalid")
        assets = (link.source_sha256, link.execution_sha256, link.model_calls_sha256)
        if ((link.resume_phase == "orchestrated" and (stage.schema_version < 3 or not all(assets)))
            or (link.resume_phase == "submitted" and any(assets))):
            raise FrameworkMessageStageRecoveryRequired("Message recovery phase has no matching frozen proof")
        predecessor = link.task_id
        identities.add(link.task_id)
    expected_assets = {"source_manifest": stage.phase != "submitted",
        "execution_request": stage.phase in {"orchestrated", "runtime_submitted", "projected"}}
    for field, required in expected_assets.items():
        payload = getattr(stage, field)
        digest = getattr(stage, "source_sha256" if field == "source_manifest" else "execution_sha256")
        if (required and (payload is None or digest != approval_value_hash(payload))) or (
            not required and (payload is not None or digest is not None)):
            raise FrameworkMessageStageRecoveryRequired(f"Initial message stage asset integrity failed: {field}")
    return stage


def message_stage_table(settings):
    return f"{settings.database.postgres_agent_task_table}_message_stage"


def initialize_message_stages(cur, settings):
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {message_stage_table(settings)} (
        task_id TEXT PRIMARY KEY REFERENCES {settings.database.postgres_agent_task_table}(id) ON DELETE CASCADE,
        binding_id TEXT NOT NULL UNIQUE REFERENCES {runtime_owner_table(settings)}(binding_id) ON DELETE CASCADE,
        record JSONB NOT NULL
    )""")


def admit_message_stage(cur, settings, task):
    cur.execute("SELECT clock_timestamp() AS now")
    now = cur.fetchone()["now"]
    record = FrameworkMessageStage(task_id=task["id"], binding_id=task["runtime_binding"]["binding_id"],
        input_sha256=task["input_hash"], user_message_id=task["user_message_id"], phase="submitted",
        created_at=now, updated_at=now)
    cur.execute(f"INSERT INTO {message_stage_table(settings)}(task_id,binding_id,record) VALUES(%s,%s,%s::jsonb)",
        (record.task_id, record.binding_id, record.model_dump_json()))


def message_runtime_source_hash(session, user_message_id):
    """Compare runtime inputs while allowing only recorded lease/lifecycle changes.

    No upstream counterpart (checked: Session ownership/status projections,
    plan §14.2): a checkpoint cannot validate inputs that precede the graph.
    Only the current user message's delivery status is transport bookkeeping.
    """
    metadata = {key: value for key, value in session.metadata.items()
        if not key.startswith(("turn_lease_", "continuation_lease_")) and key != "control"}
    control = {key: value for key, value in (session.metadata.get("control") or {}).items()
        if key not in {"control_state", "is_interrupted", "preserve_resources"}}
    if control:
        metadata["control"] = control
    messages = []
    for message in session.messages:
        if message.metadata.get("delivery_status") == "queued" and message.id != user_message_id:
            continue
        value = message.model_dump(mode="json")
        if message.id == user_message_id:
            value["metadata"].pop("delivery_status", None)
        messages.append(value)
    return approval_value_hash({"session": {name: getattr(session, name) for name in
        ("id", "title", "project_id", "mode_key", "selected_agent", "preferred_model", "session_mode", "runtime_mode")},
        "metadata": metadata, "messages": messages})


def mode_registry_hash(modes):
    return approval_value_hash([mode.model_dump(mode="json") for mode in modes])


def message_source_manifest(session, binding, payload, *, modes=None):
    """Freeze references/digests, excluding leases and raw message contents.

    This manifest describes inputs observed at admission. It is not an archive
    of mutable external Memory/Skill/Prompt services or the later graph state.
    """
    metadata = {key: value for key, value in session.metadata.items()
        if not key.startswith(("turn_lease_", "continuation_lease_"))}
    value = {"binding_fingerprint": binding.fingerprint, "session_revision": session.state_revision,
        "payload_sha256": approval_value_hash(payload.model_dump(mode="json")),
        "project_id": session.project_id, "mode_key": session.mode_key,
        "selected_agent": session.selected_agent, "preferred_model": session.preferred_model,
        "session_mode": session.session_mode.value, "runtime_mode": session.runtime_mode.value,
        "metadata_sha256": approval_value_hash(metadata),
        "message_sources": [{"message_id": message.id, "sha256": approval_value_hash(message.model_dump(mode="json"))}
            for message in session.messages]}
    users = [message.id for message in session.messages
        if message.role.value == "user" and message.metadata.get("turn_id") == binding.turn_id]
    if len(users) == 1 and modes is not None:
        value.update(runtime_source_sha256=message_runtime_source_hash(session, users[0]),
            mode_registry_sha256=mode_registry_hash(modes))
    return value


class FrameworkMessageStages:
    def __init__(self, tasks):
        self._tasks, self._settings = tasks, tasks._settings
        self._table = message_stage_table(self._settings)

    def get(self, task_id, session_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT stage.record FROM {self._tasks._tasks} task LEFT JOIN {self._table} stage "
                "ON stage.binding_id=task.runtime_binding->>'binding_id' "
                "AND (stage.task_id=task.id OR (task.operation='message_recover' AND task.reference_id=stage.task_id "
                f"AND EXISTS(SELECT 1 FROM {self._tasks._tasks} origin "
                "WHERE origin.id=stage.task_id AND origin.session_id=task.session_id))) "
                "WHERE task.id=%s AND task.session_id=%s", (task_id, session_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError(task_id)
            return validate_message_stage(row["record"]) if row["record"] else None

    async def begin(self, owner, sources):
        return await self._advance(owner, expected="submitted", phase="orchestrating",
            values={"source_manifest": sources, "source_sha256": approval_value_hash(sources),
                "owner_epoch": owner.owner_epoch, "owner_attempt_id": owner.attempt_id})

    async def freeze_execution(self, owner, request):
        if (request.runtime_binding != owner.binding or request.session_id != owner.binding.session_id
            or request.turn_id != owner.binding.turn_id):
            raise ValueError("Frozen execution request does not match the original message binding")
        payload = request.model_dump(mode="json")
        return await self._advance(owner, expected="orchestrating", phase="orchestrated",
            values={"execution_request": payload, "execution_sha256": approval_value_hash(payload)})

    async def submit_runtime(self, owner):
        return await self._advance(owner, expected="orchestrated", phase="runtime_submitted", values={})

    async def resume_orchestrated(self, owner, *, modes):
        return await self._advance(owner, expected="orchestrated", phase="orchestrated",
            values={"owner_epoch": owner.owner_epoch, "owner_attempt_id": owner.attempt_id},
            recovery_modes_sha256=mode_registry_hash(modes))

    async def projected(self, owner, reference):
        return await self._advance(owner, expected="runtime_submitted", phase="projected",
            values={"result_reference": reference})

    async def _advance(self, owner, *, expected, phase, values, recovery_modes_sha256=None):
        # Snapshot mutable caller dictionaries before crossing the thread boundary.
        values = json.loads(json.dumps(values, ensure_ascii=False, allow_nan=False))

        def advance():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                configure_runtime_owner_transaction(cur, self._settings)
                assert_runtime_owner(cur, self._settings, owner)
                cur.execute(f"SELECT * FROM {self._tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
                task = cur.fetchone()
                cur.execute(f"SELECT record FROM {self._table} WHERE binding_id=%s FOR UPDATE", (owner.binding.binding_id,))
                row = cur.fetchone()
                if row is None:
                    raise FrameworkMessageStageRecoveryRequired("Initial message stage admission is missing")
                original = validate_message_stage(row["record"])
                if (task is None or task["operation"] not in {"message", "message_recover"} or task["status"] != "running"
                    or task["runtime_binding"] != owner.binding.model_dump(mode="json")
                    or task["attempt_id"] != owner.attempt_id or task["lease_token"] != owner.owner_token
                    or original.binding_id != owner.binding.binding_id or original.input_sha256 != task["input_hash"]
                    or original.user_message_id != task["user_message_id"] or original.current_task_id != owner.task_id):
                    raise FrameworkMessageStageRecoveryRequired("Initial message stage identity differs from its origin")
                if original.phase != expected or (expected != "submitted" and recovery_modes_sha256 is None and (
                    original.owner_epoch != owner.owner_epoch or original.owner_attempt_id != owner.attempt_id)):
                    raise FrameworkMessageStageRecoveryRequired("Initial message stage has already started; reconcile its origin")
                resuming_frozen = (task["operation"] == "message_recover" and original.recoveries
                    and original.recoveries[-1].resume_phase == "orchestrated")
                if recovery_modes_sha256 is not None or (resuming_frozen and expected == "orchestrated"):
                    from src.runtime.framework_message_recovery import require_message_recovery_claim

                    if (task["operation"] != "message_recover" or (recovery_modes_sha256 is not None
                        and (original.source_manifest or {}).get("mode_registry_sha256") != recovery_modes_sha256)):
                        raise FrameworkMessageStageRecoveryRequired("Frozen message mode sources changed")
                    session = self._tasks._lock_session(cur, owner.binding.session_id)
                    require_message_recovery_claim(cur, self._tasks, session, owner.binding, task,
                        {"task_id": owner.task_id, "attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch})
                if expected == "submitted":
                    if task["operation"] == "message_recover":
                        from src.runtime.framework_message_recovery import require_message_recovery_claim

                        session = self._tasks._lock_session(cur, owner.binding.session_id)
                        require_message_recovery_claim(cur, self._tasks, session, owner.binding, task,
                            {"task_id": owner.task_id, "attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch})
                    self._require_input(cur, owner, task, values["source_manifest"])
                cur.execute("SELECT clock_timestamp() AS now")
                saved = validate_message_stage(original.model_copy(update={**values,
                    "schema_version": 3, "phase": phase, "updated_at": cur.fetchone()["now"]}).model_dump())
                cur.execute(f"UPDATE {self._table} SET record=%s::jsonb WHERE task_id=%s", (saved.model_dump_json(), original.task_id))
                return saved

        try:
            saved = await asyncio.to_thread(advance)
        except Exception as exc:
            logger.warning("framework_message_stage_rejected", extra={"task_id": owner.task_id,
                "binding_id": owner.binding.binding_id, "expected_phase": expected, "target_phase": phase,
                "error_type": type(exc).__name__})
            raise
        logger.info("framework_message_stage_advanced", extra={"task_id": owner.task_id,
            "binding_id": saved.binding_id, "owner_epoch": owner.owner_epoch, "phase": saved.phase})
        return saved

    def _require_input(self, cur, owner, task, sources):
        if (sources["payload_sha256"] != approval_value_hash(task["input_payload"])
            or sources["project_id"] != task["project_id"]):
            raise FrameworkMessageStageRecoveryRequired("Initial message stage input changed after admission")
        if self._tasks._v2_input_hash(task["input_payload"], owner.binding) == task["input_hash"]:
            return
        from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

        ledger = PostgresFrameworkModelCalls(self._settings)
        cur.execute("SELECT to_regclass(%s) AS table_name", (ledger.budgets_table,))
        if cur.fetchone()["table_name"] is None:
            raise FrameworkMessageStageRecoveryRequired("Initial message stage input budget cannot be verified")
        policy = ledger._policy(cur, owner.binding.binding_id)
        if self._tasks._v2_input_hash(task["input_payload"], owner.binding, policy) != task["input_hash"]:
            raise FrameworkMessageStageRecoveryRequired("Initial message stage input digest differs from its admission")
