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
            or (link.resume_phase == "finalizing" and (stage.schema_version < 11
                or not all(assets) or not link.result_reference or not link.result_sha256
                or "runtime_result_asset" not in (stage.source_manifest or {})))
            or (link.resume_phase == "settling" and (stage.schema_version < 22 or stage.phase != "projected"
                or not all(assets) or not link.result_reference or not link.result_sha256
                or link.result_reference != stage.result_reference
                or "application_projection_commit" not in (stage.source_manifest or {})))
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
    if (stage.schema_version >= 25 and "application_projection_commit" in manifest
        and "runtime_result_asset" in manifest and "post_commit_memory_intent" not in manifest):
        raise FrameworkMessageStageRecoveryRequired("Application commit v25 lost its original Memory intent")
    if (stage.schema_version >= 26 and "application_projection_commit" in manifest
        and "runtime_result_asset" in manifest and "post_commit_memory_plan" not in manifest):
        raise FrameworkMessageStageRecoveryRequired("Application commit v26 lost its frozen Memory plan")
    if "post_commit_memory_intent" in manifest:
        from src.schemas.framework_projection_commit import FrameworkTurnMemoryIntent
        from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

        if (stage.schema_version < 25 or stage.phase not in {"runtime_submitted", "projected"}
            or "runtime_result_asset" not in manifest or "application_projection_commit" not in manifest):
            raise FrameworkMessageStageRecoveryRequired("Memory intent has no atomic application commit or compatible format")
        asset = FrameworkRuntimeResultAsset.model_validate(manifest["runtime_result_asset"])
        intent = FrameworkTurnMemoryIntent.model_validate(manifest["post_commit_memory_intent"])
        if intent != FrameworkTurnMemoryIntent.from_result_asset(asset):
            raise FrameworkMessageStageRecoveryRequired("Memory intent differs from its frozen original result")
    if "post_commit_memory_plan" in manifest:
        from src.runtime.framework_application_projection import build_turn_memory_plan
        from src.schemas.framework_projection_commit import FrameworkTurnMemoryPlan
        from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

        if (stage.schema_version < 26 or stage.phase not in {"runtime_submitted", "projected"}
            or "runtime_result_asset" not in manifest or "application_projection_commit" not in manifest
            or "post_commit_memory_intent" not in manifest):
            raise FrameworkMessageStageRecoveryRequired("Memory plan has no atomic application commit or compatible format")
        asset = FrameworkRuntimeResultAsset.model_validate(manifest["runtime_result_asset"])
        plan = FrameworkTurnMemoryPlan.model_validate(manifest["post_commit_memory_plan"])
        expected = build_turn_memory_plan(asset)
        if plan != expected:
            raise FrameworkMessageStageRecoveryRequired("Memory plan differs from its frozen original result")
        intent = FrameworkTurnMemoryIntent.model_validate(manifest["post_commit_memory_intent"])
        if (plan.effect_id != intent.effect_id or plan.requested != intent.requested
            or plan.runtime_result_asset_sha256 != intent.runtime_result_asset_sha256
            or plan.source_snapshot_id != intent.source_snapshot_id):
            raise FrameworkMessageStageRecoveryRequired("Memory plan differs from its atomic Memory intent")
    if "approval_graph_inputs" in manifest:
        from src.schemas.framework_graph_input import FrameworkApprovalGraphInput, parse_graph_input_asset

        originals = manifest["approval_graph_inputs"]
        if (stage.schema_version < 16 or stage.phase not in {"runtime_submitted", "projected"}
            or not isinstance(originals, dict) or not originals or "graph_input_asset" not in manifest):
            raise FrameworkMessageStageRecoveryRequired("Approval graph input has no compatible message stage format")
        initial = parse_graph_input_asset(manifest["graph_input_asset"])
        if (initial.schema_version != 2 or initial.runtime_preparation.identity.binding.binding_id != stage.binding_id
            or manifest.get("binding_fingerprint") != initial.runtime_preparation.identity.binding.fingerprint):
            raise FrameworkMessageStageRecoveryRequired("Approval graph input has no original prepared binding")
        for task_id, raw in originals.items():
            original = FrameworkApprovalGraphInput.model_validate(raw)
            if stage.schema_version < original.graph_input_asset.required_message_stage_version:
                raise FrameworkMessageStageRecoveryRequired("Approval preparation has no compatible message stage format")
            if (original.task_id != task_id
                or original.graph_input_asset.runtime_preparation.identity.binding != initial.runtime_preparation.identity.binding):
                raise FrameworkMessageStageRecoveryRequired("Approval graph input differs from its original task or binding")
            if original.graph_input_asset.feature_flags.get("result_projection_version") != initial.feature_flags.get("result_projection_version"):
                raise FrameworkMessageStageRecoveryRequired("Approval graph input changed its original result projection contract")
            if original.graph_input_asset.feature_flags.get("terminal_event_projection_version") != initial.feature_flags.get("terminal_event_projection_version"):
                raise FrameworkMessageStageRecoveryRequired("Approval graph input changed its original terminal event projection contract")
            if original.graph_input_asset.feature_flags.get("structured_output_version") != initial.feature_flags.get("structured_output_version"):
                raise FrameworkMessageStageRecoveryRequired("Approval graph input changed its original structured output contract")
    if "graph_input_asset" in manifest:
        from src.schemas.framework_graph_input import parse_graph_input_asset

        graph_input = parse_graph_input_asset(manifest["graph_input_asset"])
        if stage.schema_version < graph_input.required_message_stage_version:
            raise FrameworkMessageStageRecoveryRequired("Graph tool material has no compatible message stage format")
        if graph_input.schema_version == 2 and (stage.schema_version < 12
            or graph_input.runtime_preparation.identity.binding.binding_id != stage.binding_id):
            raise FrameworkMessageStageRecoveryRequired("Prepared graph input has no compatible message stage binding")
    observation_keys = {"observation_projection_asset", "observation_projection_commits"}
    if observation_keys.intersection(manifest):
        from src.schemas.framework_observation_projection import FrameworkObservationAsset, FrameworkObservationCommits

        if (stage.schema_version < 10 or stage.phase not in {"runtime_submitted", "projected"}
            or "runtime_result_asset" not in manifest or not observation_keys.issubset(manifest)):
            raise FrameworkMessageStageRecoveryRequired("Observation projection has no matching message stage format")
        FrameworkObservationAsset.model_validate(manifest["observation_projection_asset"])
        FrameworkObservationCommits.model_validate(manifest["observation_projection_commits"])
    if "event_projection_commits" in manifest:
        from src.schemas.framework_projection_commit import FrameworkEventProjectionCommits

        if (stage.schema_version < 9 or stage.phase not in {"runtime_submitted", "projected"}
            or "runtime_result_asset" not in manifest):
            raise FrameworkMessageStageRecoveryRequired("Event projection has no matching message stage format")
        FrameworkEventProjectionCommits.model_validate(manifest["event_projection_commits"])
    if "snapshot_projection_commit" in manifest:
        from src.schemas.framework_projection_commit import FrameworkSnapshotProjectionCommit

        if (stage.schema_version < 8 or stage.phase not in {"runtime_submitted", "projected"}
            or "runtime_result_asset" not in manifest):
            raise FrameworkMessageStageRecoveryRequired("Snapshot projection has no matching message stage format")
        FrameworkSnapshotProjectionCommit.model_validate(manifest["snapshot_projection_commit"])
    if "runtime_result_asset" in manifest:
        from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

        if stage.schema_version < 7 or stage.phase not in {"runtime_submitted", "projected"}:
            raise FrameworkMessageStageRecoveryRequired("Runtime result has no matching message stage format")
        result = FrameworkRuntimeResultAsset.model_validate(manifest["runtime_result_asset"])
        if result.task_id in manifest.get("approval_graph_inputs", {}) and stage.schema_version < 17:
            raise FrameworkMessageStageRecoveryRequired("Approval runtime result has no compatible message stage format")
    if stage.approval_finalizations:
        from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

        if (stage.schema_version < 18 or "runtime_result_asset" not in manifest
            or stage.phase not in {"runtime_submitted", "projected"}):
            raise FrameworkMessageStageRecoveryRequired("Approval finalization has no compatible message stage format")
        result = FrameworkRuntimeResultAsset.model_validate(manifest["runtime_result_asset"])
        if result.task_id not in manifest.get("approval_graph_inputs", {}):
            raise FrameworkMessageStageRecoveryRequired("Approval finalization has no original approval result")
        predecessor, attempt, epoch = result.task_id, result.attempt_id, result.owner_epoch
        for link in stage.approval_finalizations:
            if (link.predecessor_task_id != predecessor or link.predecessor_attempt_id != attempt
                or link.predecessor_owner_epoch != epoch or link.task_id in identities
                or link.source_sha256 != result.source_sha256 or link.execution_sha256 != stage.execution_sha256
                or link.model_calls_sha256 != result.model_calls_sha256 or link.result_sha256 != result.asset_sha256
                or link.result_reference != result.runtime_result.snapshot.id
                or (link.owner_attempt_id is None) != (link.owner_epoch is None)):
                raise FrameworkMessageStageRecoveryRequired("Approval finalization lineage differs from its original result")
            predecessor, attempt, epoch = link.task_id, link.owner_attempt_id, link.owner_epoch
            identities.add(link.task_id)
    if "application_projection_commit" in manifest:
        from src.schemas.framework_projection_commit import FrameworkProjectionCommit

        if stage.schema_version < 6 or stage.phase not in {"runtime_submitted", "projected"}:
            raise FrameworkMessageStageRecoveryRequired("Application commit has no matching message stage format")
        FrameworkProjectionCommit.model_validate(manifest["application_projection_commit"])
    if stage.approval_settlements:
        from src.schemas.framework_projection_commit import FrameworkProjectionCommit

        if (stage.schema_version < 23 or stage.phase != "projected"
            or "application_projection_commit" not in manifest or "runtime_result_asset" not in manifest):
            raise FrameworkMessageStageRecoveryRequired("Approval settlement has no projected commit format")
        commit = FrameworkProjectionCommit.model_validate(manifest["application_projection_commit"])
        result = FrameworkRuntimeResultAsset.model_validate(manifest["runtime_result_asset"])
        if result.task_id not in manifest.get("approval_graph_inputs", {}):
            raise FrameworkMessageStageRecoveryRequired("Approval settlement has no original approval result")
        previous = stage.approval_finalizations[-1] if stage.approval_finalizations else None
        predecessor, attempt, epoch = ((previous.task_id, previous.owner_attempt_id, previous.owner_epoch)
            if previous else (result.task_id, result.attempt_id, result.owner_epoch))
        identities = {result.task_id, *(link.task_id for link in stage.approval_finalizations)}
        for link in stage.approval_settlements:
            if (link.predecessor_task_id != predecessor or link.predecessor_attempt_id != attempt
                or link.predecessor_owner_epoch != epoch or link.task_id in identities
                or link.source_sha256 != stage.source_sha256 or link.execution_sha256 != stage.execution_sha256
                or link.model_calls_sha256 != commit.model_calls_sha256
                or link.result_reference != stage.result_reference
                or stage.result_reference != (commit.output_message_id or commit.snapshot_id)
                or link.result_sha256 != approval_value_hash(commit.model_dump(mode="json"))
                or (link.owner_attempt_id is None) != (link.owner_epoch is None)):
                raise FrameworkMessageStageRecoveryRequired("Approval settlement lineage differs from its projected commit")
            predecessor, attempt, epoch = link.task_id, link.owner_attempt_id, link.owner_epoch
            identities.add(link.task_id)
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

    def _get_record(self, task_id, session_id, *, for_capability=False):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT stage.record FROM {self._tasks._tasks} task LEFT JOIN {self._table} stage "
                "ON stage.binding_id=task.runtime_binding->>'binding_id' "
                f"AND EXISTS(SELECT 1 FROM {self._tasks._tasks} source "
                "WHERE source.id=stage.task_id AND source.session_id=task.session_id "
                "AND source.runtime_binding=task.runtime_binding) "
                "AND (stage.task_id=task.id OR (task.operation='message_recover' AND task.reference_id=stage.task_id) "
                "OR (%s AND task.operation IN ('approval_resume','approval_recover','framework_reconcile'))) "
                "WHERE task.id=%s AND task.session_id=%s", (for_capability, task_id, session_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError(task_id)
            return row["record"]

    def get(self, task_id, session_id, *, include_continuations=False):
        # Read-only material access; accepting a continuation reference here
        # never changes _advance's message-only write authority.
        record = self._get_record(task_id, session_id, for_capability=include_continuations)
        return validate_message_stage(record) if record else None

    def get_version(self, task_id, session_id):
        # Read the binding's original message format for continuations too;
        # do not parse a future strict schema or grant its execution rights.
        record = self._get_record(task_id, session_id, for_capability=True)
        return record.get("schema_version", 3) if record is not None else None

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

    async def projected_approval(self, owner, reference):
        from src.runtime.framework_approval_finalization import acknowledge_finalization

        return await asyncio.to_thread(acknowledge_finalization, self._settings, owner, reference)

    async def resume_finalizing(self, owner):
        """Adopt only application writes after checking the successor's proof."""
        return await self._advance(owner, expected="runtime_submitted", phase="runtime_submitted",
            values={"owner_epoch": owner.owner_epoch, "owner_attempt_id": owner.attempt_id})

    async def record_graph_input(self, owner, asset):
        from src.schemas.framework_graph_input import parse_graph_input_asset

        frozen = parse_graph_input_asset(asset)
        saved = await self._advance(owner, expected="runtime_submitted", phase="runtime_submitted", values={},
            graph_input_asset=frozen.model_dump(mode="json"))
        logger.info("framework_graph_input_frozen", extra={"task_id": owner.task_id,
            "binding_id": owner.binding.binding_id, "owner_epoch": owner.owner_epoch,
            "graph_input_schema_version": frozen.schema_version, "message_stage_schema_version": saved.schema_version,
            "asset_sha256": frozen.asset_sha256})
        return saved

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
                    and original.recoveries[-1].resume_phase in {"runtime_submitted", "finalizing"})
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
                    from src.schemas.framework_graph_input import parse_graph_input_asset

                    asset = parse_graph_input_asset(graph_input_asset)
                    if (asset.session_id != owner.binding.session_id
                        or asset.turn_id != owner.binding.turn_id
                        or asset.trace_id != task["trace_id"]
                        or asset.checkpoint_namespace != owner.binding.checkpoint_namespace
                        or (asset.schema_version == 2 and asset.runtime_preparation.identity.binding != owner.binding)):
                        raise FrameworkMessageStageRecoveryRequired("Graph input asset differs from its runtime binding")
                    manifest = dict(original.source_manifest or {})
                    if "graph_input_asset" in manifest:
                        if manifest["graph_input_asset"] != graph_input_asset:
                            raise FrameworkMessageStageRecoveryRequired("Graph input asset is immutable")
                        return original
                    manifest["graph_input_asset"] = graph_input_asset
                    values.update(source_manifest=manifest, source_sha256=approval_value_hash(manifest))
                    if asset.schema_version == 2:
                        values["schema_version"] = max(asset.required_message_stage_version, original.schema_version)
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
                    "schema_version": max(values.get("schema_version", 3), original.schema_version),
                    "phase": phase, "updated_at": cur.fetchone()["now"]}).model_dump())
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
