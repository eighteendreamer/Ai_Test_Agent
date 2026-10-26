"""Fenced observation intents and memory-row receipts, not vector-cache proofs.

No upstream counterpart (checked: MemoryRuntimeService, PostgreSQL memory and
compaction owner transactions, LangGraph persistence/interrupts, plan v3 §12.1):
graph checkpoints do not prove that an observation projection committed.
"""
from __future__ import annotations

import asyncio
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_application_projection import COMMIT_KEY, _hash, _require
from src.runtime.framework_message_stages import message_stage_table, validate_message_stage
from src.runtime.framework_runtime_results import RESULT_KEY, lock_runtime_result_stage, validate_runtime_result
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_observation_projection import FrameworkObservationAsset, FrameworkObservationCommits


LOGGER = logging.getLogger(__name__)
ASSET_KEY = "observation_projection_asset"
COMMITS_KEY = "observation_projection_commits"
EMBEDDING_METADATA = frozenset({"embedding", "embedding_model", "embedding_provider", "embedding_adapter",
    "embedding_source_dimension", "embedding_dimension", "embedding_version", "normalized"})
MEMORY_COLUMNS = "id,scope,kind,content,summary,tags,session_id,turn_id,trace_id,source,stale,mode_key,metadata,embedding::text AS embedding,created_at,updated_at"


def observation_asset(stage, binding, result_sha256):
    raw = (stage.source_manifest or {}).get(ASSET_KEY)
    _require(raw is not None, "Observation projection has no original asset")
    asset = FrameworkObservationAsset.model_validate(raw)
    _require(asset.binding_fingerprint == binding.fingerprint
        and asset.runtime_result_asset_sha256 == result_sha256
        and all(item.session_id == binding.session_id and item.turn_id == binding.turn_id
            for item in asset.observations), "Observation projection identity changed")
    return asset


def observation_commits(stage, asset):
    raw = (stage.source_manifest or {}).get(COMMITS_KEY)
    _require(raw is not None, "Observation projection has no atomic receipt container")
    proof = FrameworkObservationCommits.model_validate(raw)
    _require(proof.binding_fingerprint == asset.binding_fingerprint
        and proof.observation_asset_sha256 == asset.asset_sha256, "Observation receipt identity changed")
    return proof


def _save_stage(cur, settings, stage, values):
    manifest = {**stage.source_manifest, **values}
    updated = validate_message_stage(stage.model_copy(update={"schema_version": max(10, stage.schema_version),
        "source_manifest": manifest, "source_sha256": approval_value_hash(manifest)}).model_dump())
    cur.execute(f"UPDATE {message_stage_table(settings)} SET record=%s::jsonb WHERE task_id=%s",
        (updated.model_dump_json(), stage.task_id))


def _locked_projection(cur, settings, write):
    stage, task = lock_runtime_result_stage(cur, settings, write.owner)
    manifest = stage.source_manifest or {}
    _require(COMMIT_KEY not in manifest, "Committed observations must use ACK recovery")
    _require(RESULT_KEY in manifest, "Observation projection has no original runtime result")
    result = validate_runtime_result(cur, settings, write.owner, stage, task, manifest[RESULT_KEY])
    _require(result.asset_sha256 == write.runtime_result_asset_sha256, "Observation runtime result changed")
    return stage, result


class FrameworkObservationProjections:
    def __init__(self, tasks):
        self._settings = tasks._settings

    async def prepare(self, write, build):
        """The synchronous pure builder runs only once under the existing owner lock."""
        def prepare():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                stage, result = _locked_projection(cur, self._settings, write)
                if ASSET_KEY in stage.source_manifest:
                    asset = observation_asset(stage, write.owner.binding, result.asset_sha256)
                    observation_commits(stage, asset)
                    return asset
                observations, requests = build(result)
                values = dict(schema_version=1, binding_fingerprint=write.owner.binding.fingerprint,
                    runtime_result_asset_sha256=result.asset_sha256,
                    observations=[item.model_dump(mode="json") for item in observations],
                    write_requests=[item.model_dump(mode="json") for item in requests])
                asset = FrameworkObservationAsset(**values, asset_sha256=approval_value_hash(values))
                proof = FrameworkObservationCommits(binding_fingerprint=asset.binding_fingerprint,
                    observation_asset_sha256=asset.asset_sha256)
                _save_stage(cur, self._settings, stage,
                    {ASSET_KEY: asset.model_dump(mode="json"), COMMITS_KEY: proof.model_dump(mode="json")})
                return asset
        try:
            asset = await asyncio.to_thread(prepare)
        except Exception as exc:
            LOGGER.warning("framework_observation_preparation_rejected", extra={
                "binding_id": write.owner.binding.binding_id, "error_type": type(exc).__name__})
            raise
        LOGGER.info("framework_observations_prepared", extra={"binding_id": write.owner.binding.binding_id,
            "observation_count": len(asset.observations)})
        return asset


def prepare_memory_projection(cur, settings, write, request):
    stage, result = _locked_projection(cur, settings, write)
    asset = observation_asset(stage, write.owner.binding, result.asset_sha256)
    identity = str(request.metadata.get("memory_id") or "")
    pairs = [(item, intent) for item, intent in zip(asset.observations, asset.write_requests) if item.id == identity]
    _require(len(pairs) == 1, "Observation write is not part of the original asset")
    observation, original = pairs[0]
    metadata = {key: value for key, value in request.metadata.items() if key not in EMBEDDING_METADATA}
    _require(request.model_dump(mode="json", exclude={"metadata"}) == original.model_dump(mode="json", exclude={"metadata"})
        and _hash(metadata) == _hash(original.metadata)
        and not request.compaction_key and not request.compaction_owner_id,
        "Observation write differs from its original intent")
    proof = observation_commits(stage, asset)
    cur.execute(f"SELECT {MEMORY_COLUMNS} FROM {settings.database.postgres_memory_table} WHERE id=%s FOR UPDATE", (identity,))
    row = cur.fetchone()
    row = dict(row) if row is not None else None
    if identity in proof.row_hashes:
        _require(row is not None and _hash(row) == proof.row_hashes[identity],
            "Original observation row changed or disappeared")
    else:
        _require(row is None, "Existing observation row has no atomic projection receipt")
    return stage, asset, observation, row


def record_memory_projection(cur, settings, write, stage, asset, point):
    cur.execute(f"SELECT {MEMORY_COLUMNS} FROM {settings.database.postgres_memory_table} WHERE id=%s FOR UPDATE", (point.id,))
    raw = cur.fetchone()
    _require(raw is not None, "Original observation insert has no actual row")
    row = dict(raw)
    expected = point.model_dump(mode="python", exclude={"score", "updated_at"})
    different = [key for key, value in expected.items() if _hash(row[key]) != _hash(value)]
    _require(not different, "Observation row differs from its original insert intent: " + ",".join(different))
    previous = observation_commits(stage, asset)
    hashes = {**previous.row_hashes, point.id: _hash(row)}
    proof = previous.model_copy(update={"row_hashes": hashes})
    _save_stage(cur, settings, stage, {COMMITS_KEY: proof.model_dump(mode="json")})


def require_observation_projections(cur, settings, stage, binding, result_sha256):
    asset = observation_asset(stage, binding, result_sha256)
    proof = observation_commits(stage, asset)
    identities = [item.id for item in asset.observations]
    _require(set(identities) == set(proof.row_hashes), "Observation projections are incomplete")
    if identities:
        cur.execute(f"SELECT {MEMORY_COLUMNS} FROM {settings.database.postgres_memory_table} "
            "WHERE session_id=%s AND id=ANY(%s) ORDER BY id FOR UPDATE", (binding.session_id, identities))
        _require({row["id"]: _hash(dict(row)) for row in cur.fetchall()} == proof.row_hashes,
            "Original observation rows changed or disappeared before application commit")
    return asset
