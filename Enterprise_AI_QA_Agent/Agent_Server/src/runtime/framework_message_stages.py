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
from src.schemas.semantic_input import PreparedSemanticInput


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
            or (link.resume_phase == "orchestrating" and (stage.schema_version < 4
                or not link.source_sha256 or not link.model_calls_sha256 or link.execution_sha256 is not None))
            or (link.resume_phase == "runtime_submitted" and (stage.schema_version < 5
                or not all(assets) or not link.result_reference or not link.result_sha256))
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
    manifest = stage.source_manifest or {}
    if "application_projection_commit" in manifest:
        from src.schemas.framework_projection_commit import FrameworkProjectionCommit

        if stage.schema_version < 6 or stage.phase not in {"runtime_submitted", "projected"}:
            raise FrameworkMessageStageRecoveryRequired("Application commit has no matching message stage format")
        FrameworkProjectionCommit.model_validate(manifest["application_projection_commit"])
    if "semantic_input" in manifest:
        semantic = PreparedSemanticInput.model_validate(manifest["semantic_input"])
        if semantic.request is not None and semantic.invocation_parameters is None:
            raise FrameworkMessageStageRecoveryRequired("Persisted semantic input has no invocation contract")
    elif manifest.get("semantic_input_required") and stage.phase != "orchestrating":
        raise FrameworkMessageStageRecoveryRequired("Execution was frozen without its prepared semantic input")
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


def message_source_manifest(session, binding, payload, *, modes=None, semantic_input_required=False):
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
    if semantic_input_required:
        from src.runtime.framework_orchestration_artifact import orchestration_artifact

        value["semantic_input_required"] = True
        value["orchestration_artifact"] = orchestration_artifact().model_dump(mode="json")
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

    async def freeze_semantic_input(self, owner, prepared):
        prepared = PreparedSemanticInput.model_validate_json(prepared.model_dump_json())
        saved = await self._advance(owner, expected="orchestrating", phase="orchestrating", values={},
            semantic_input=prepared.model_dump(mode="json"))
        logger.info("framework_semantic_input_frozen", extra={"task_id": owner.task_id,
            "binding_id": owner.binding.binding_id, "source_sha256": saved.source_sha256,
            "model_key": prepared.model_key, "skip_reason": prepared.skip_reason})
        return PreparedSemanticInput.model_validate(saved.source_manifest["semantic_input"])

    async def submit_runtime(self, owner):
        return await self._advance(owner, expected="orchestrated", phase="runtime_submitted", values={})

    async def resume_orchestrated(self, owner, *, modes):
        return await self._advance(owner, expected="orchestrated", phase="orchestrated",
            values={"owner_epoch": owner.owner_epoch, "owner_attempt_id": owner.attempt_id},
            recovery_modes_sha256=mode_registry_hash(modes))

    async def resume_orchestrating(self, owner, *, modes):
        return await self._advance(owner, expected="orchestrating", phase="orchestrating",
            values={"owner_epoch": owner.owner_epoch, "owner_attempt_id": owner.attempt_id},
            recovery_modes_sha256=mode_registry_hash(modes))

    async def projected(self, owner, reference):
        return await self._advance(owner, expected="runtime_submitted", phase="projected",
            values={"result_reference": reference})

    async def record_graph_input(self, owner, asset):
        from src.schemas.framework_graph_input import FrameworkGraphInputAsset

        frozen = FrameworkGraphInputAsset.model_validate(asset)
        return await self._advance(owner, expected="runtime_submitted", phase="runtime_submitted", values={},
            graph_input_asset=frozen.model_dump(mode="json"))

    async def _advance(self, owner, *, expected, phase, values, recovery_modes_sha256=None,
        semantic_input=None, graph_input_asset=None):
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
                resuming_frozen = (task["operation"] == "message_recover" and original.recoveries
                    and original.recoveries[-1].resume_phase == "orchestrated")
                resuming_prepared = (task["operation"] == "message_recover" and original.recoveries
                    and original.recoveries[-1].resume_phase == "orchestrating")
                resuming_projected = (task["operation"] == "message_recover" and original.recoveries
                    and original.recoveries[-1].resume_phase == "runtime_submitted")
                if (task is None or task["operation"] not in {"message", "message_recover"} or task["status"] != "running"
                    or task["runtime_binding"] != owner.binding.model_dump(mode="json")
                    or task["attempt_id"] != owner.attempt_id or task["lease_token"] != owner.owner_token
                    or original.binding_id != owner.binding.binding_id or original.input_sha256 != task["input_hash"]
                    or original.user_message_id != task["user_message_id"] or original.current_task_id != owner.task_id):
                    raise FrameworkMessageStageRecoveryRequired("Initial message stage identity differs from its origin")
                if original.phase != expected or (expected != "submitted" and recovery_modes_sha256 is None
                    and not resuming_projected and (
                    original.owner_epoch != owner.owner_epoch or original.owner_attempt_id != owner.attempt_id)):
                    raise FrameworkMessageStageRecoveryRequired("Initial message stage has already started; reconcile its origin")
                if expected == "orchestrating" and "orchestration_artifact" in (original.source_manifest or {}):
                    from src.runtime.framework_orchestration_artifact import require_orchestration_artifact

                    require_orchestration_artifact(original.source_manifest["orchestration_artifact"])
                if semantic_input is not None:
                    prepared = PreparedSemanticInput.model_validate(semantic_input)
                    if prepared.request is not None:
                        context = prepared.request.trace_context
                        if (context.get("session_id") != owner.binding.session_id
                            or context.get("turn_id") != owner.binding.turn_id
                            or context.get("trace_id") != task["trace_id"]
                            or context.get("logical_call_id") != "input_orchestration:semantic_intent:v1"):
                            raise FrameworkMessageStageRecoveryRequired("Semantic input differs from its admitted message identity")
                    manifest = dict(original.source_manifest)
                    if "semantic_input" in manifest:
                        if manifest["semantic_input"] != semantic_input:
                            raise FrameworkMessageStageRecoveryRequired("Prepared semantic input is immutable")
                        return original
                    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

                    if PostgresFrameworkModelCalls(self._settings)._snapshot(cur, owner.binding.binding_id).calls:
                        raise FrameworkMessageStageRecoveryRequired("Semantic input must be frozen before model reservation")
                    manifest["semantic_input"] = semantic_input
                    values.update(source_manifest=manifest, source_sha256=approval_value_hash(manifest))
                if graph_input_asset is not None:
                    from src.schemas.framework_graph_input import FrameworkGraphInputAsset

                    asset = FrameworkGraphInputAsset.model_validate(graph_input_asset)
                    if (asset.session_id != owner.binding.session_id
                        or asset.turn_id != owner.binding.turn_id
                        or asset.checkpoint_namespace != owner.binding.checkpoint_namespace):
                        raise FrameworkMessageStageRecoveryRequired("Graph input asset differs from its runtime binding")
                    manifest = dict(original.source_manifest or {})
                    if "graph_input_asset" in manifest:
                        if manifest["graph_input_asset"] != graph_input_asset:
                            raise FrameworkMessageStageRecoveryRequired("Graph input asset is immutable")
                        return original
                    manifest["graph_input_asset"] = graph_input_asset
                    values.update(source_manifest=manifest, source_sha256=approval_value_hash(manifest))
                if recovery_modes_sha256 is not None or (resuming_frozen and expected == "orchestrated") or resuming_projected:
                    from src.runtime.framework_message_recovery import require_message_recovery_claim

                    if (task["operation"] != "message_recover" or (recovery_modes_sha256 is not None
                        and (original.source_manifest or {}).get("mode_registry_sha256") != recovery_modes_sha256)):
                        raise FrameworkMessageStageRecoveryRequired("Frozen message mode sources changed")
                    session = self._tasks._lock_session(cur, owner.binding.session_id)
                    require_message_recovery_claim(cur, self._tasks, session, owner.binding, task,
                        {"task_id": owner.task_id, "attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch})
                    if resuming_projected and phase == "projected":
                        from src.runtime.framework_application_projection import require_application_commit

                        commit = require_application_commit(cur, self._settings, session, owner.binding, original)
                        if values.get("result_reference") != (commit.output_message_id or commit.snapshot_id):
                            raise FrameworkMessageStageRecoveryRequired("Recovered application result reference changed")
                        cur.execute(f"UPDATE {self._settings.database.postgres_session_table} SET status='completed', "
                            "metadata=jsonb_set(metadata,'{control}',COALESCE(metadata->'control','{}'::jsonb) || %s::jsonb), "
                            "state_revision=state_revision+1,updated_at=clock_timestamp() WHERE id=%s",
                            (json.dumps(commit.control), owner.binding.session_id))
                if resuming_prepared and phase in {"orchestrated", "runtime_submitted"}:
                    from src.runtime.framework_message_recovery import _require_frozen_sources, prepared_model_call
                    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

                    session = self._tasks._lock_session(cur, owner.binding.session_id)
                    _require_frozen_sources(cur, self._tasks, session, original)
                    prepared, call = prepared_model_call(original, owner.binding,
                        PostgresFrameworkModelCalls(self._settings)._snapshot(cur, owner.binding.binding_id))
                    if prepared.request is not None and (call is None or call.phase != "completed"):
                        raise FrameworkMessageStageRecoveryRequired("Recovered semantic input has no settled result")
                if expected == "submitted":
                    if task["operation"] == "message_recover":
                        from src.runtime.framework_message_recovery import require_message_recovery_claim

                        session = self._tasks._lock_session(cur, owner.binding.session_id)
                        require_message_recovery_claim(cur, self._tasks, session, owner.binding, task,
                            {"task_id": owner.task_id, "attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch})
                    self._require_input(cur, owner, task, values["source_manifest"])
                cur.execute("SELECT clock_timestamp() AS now")
                saved = validate_message_stage(original.model_copy(update={**values,
                    "schema_version": max(3, original.schema_version), "phase": phase, "updated_at": cur.fetchone()["now"]}).model_dump())
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
