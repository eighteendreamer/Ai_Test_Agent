"""Optional, redacted Memgraph projection for security campaign relationships."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.modes.security_testing_mode.campaign_state import (
    AssetRelation,
    SecurityCampaign,
    SecurityGraphPersistenceState,
)


class SecurityGraphStore:
    """Write campaign entities to Memgraph without storing secret values."""

    def __init__(self, settings: Any, *, provider: Any = None) -> None:
        self._owns_provider = provider is None
        self._provider = provider or MemgraphRuntimeProvider(settings)

    async def close(self) -> None:
        if self._owns_provider:
            await asyncio.to_thread(self._provider.close)

    async def persist_campaign(self, campaign: SecurityCampaign) -> SecurityGraphPersistenceState:
        return await asyncio.to_thread(self._persist_sync, campaign.model_copy(deep=True))

    def _persist_sync(self, campaign: SecurityCampaign) -> SecurityGraphPersistenceState:
        try:
            self._provider.initialize()
            now = datetime.now(timezone.utc).isoformat()
            scope = f"security:{campaign.target_fingerprint or campaign.campaign_id}"
            node_count = 0
            relation_count = 0
            self._merge_node(
                label="SecurityCampaign",
                scope=scope,
                node_id=campaign.campaign_id,
                props={
                    "objective": campaign.objective[:600],
                    "created_at": campaign.created_at,
                    "updated_at": campaign.updated_at,
                    "target_fingerprint": campaign.target_fingerprint,
                    "persisted_at": now,
                },
            )
            node_count += 1
            for asset in campaign.assets:
                self._merge_node(
                    label="SecurityAsset",
                    scope=scope,
                    node_id=asset.asset_id,
                    props={
                        "asset_type": asset.asset_type,
                        "address": asset.address,
                        "hostname": asset.hostname,
                        "port": asset.port or 0,
                        "protocol": asset.protocol,
                        "service_name": asset.service_name,
                        "confidence": asset.confidence,
                        "updated_at": now,
                    },
                )
                self._merge_relation(
                    scope=scope,
                    relation="CONTAINS",
                    source_label="SecurityCampaign",
                    source_id=campaign.campaign_id,
                    target_label="SecurityAsset",
                    target_id=asset.asset_id,
                    props={"updated_at": now},
                )
                node_count += 1
                relation_count += 1
            for relation in campaign.asset_relations:
                relation_count += self._persist_asset_relation(scope, relation, now)
            for proof in campaign.access_proofs:
                self._merge_node(
                    label="AccessProof",
                    scope=scope,
                    node_id=proof.proof_id,
                    props={
                        "target": proof.target,
                        "principal": proof.principal[:160],
                        "privilege": proof.privilege,
                        "source_attempt_id": proof.source_attempt_id,
                        "credential_ref_id": proof.credential_ref_id,
                        "evidence_count": len(proof.evidence_ids),
                        "observed_at": proof.observed_at,
                        "expires_at": proof.expires_at,
                        "updated_at": now,
                    },
                )
                self._merge_relation(
                    scope=scope,
                    relation="HAS_ACCESS_PROOF",
                    source_label="SecurityCampaign",
                    source_id=campaign.campaign_id,
                    target_label="AccessProof",
                    target_id=proof.proof_id,
                    props={"updated_at": now},
                )
                node_count += 1
                relation_count += 1
            for credential in campaign.credential_references:
                self._merge_node(
                    label="CredentialRef",
                    scope=scope,
                    node_id=credential.credential_ref_id,
                    props={
                        "auth_type": credential.auth_type,
                        "principal_hint": credential.principal_hint[:160],
                        "source": credential.source,
                        "expires_at": credential.expires_at,
                        "secret_present": credential.secret_present,
                        "updated_at": now,
                    },
                )
                node_count += 1
            return SecurityGraphPersistenceState(
                status="completed",
                backend="memgraph",
                node_count=node_count,
                relation_count=relation_count,
                persisted_at=now,
            )
        except Exception as exc:
            return SecurityGraphPersistenceState(
                status="unavailable",
                backend="memgraph",
                detail=str(exc)[:500],
                persisted_at=datetime.now(timezone.utc).isoformat(),
            )

    def _persist_asset_relation(self, scope: str, relation: AssetRelation, now: str) -> int:
        if not relation.source_asset_id or not relation.target_asset_id:
            return 0
        safe_relation = str(relation.relation or "relates_to").upper()
        if safe_relation not in {"REACHES", "TRUSTS", "AUTHENTICATES_TO", "HOSTS"}:
            safe_relation = "RELATES_TO"
        self._merge_relation(
            scope=scope,
            relation=safe_relation,
            source_label="SecurityAsset",
            source_id=relation.source_asset_id,
            target_label="SecurityAsset",
            target_id=relation.target_asset_id,
            props={
                "relation_id": relation.relation_id,
                "discovered_by": relation.discovered_by,
                "evidence_count": len(relation.evidence_ids),
                "confidence": relation.confidence,
                "observed_at": relation.observed_at,
                "updated_at": now,
            },
        )
        return 1

    def _merge_node(self, *, label: str, scope: str, node_id: str, props: dict[str, Any]) -> None:
        if not node_id:
            return
        self._provider.execute_write(
            f"MERGE (n:{label} {{project_scope: $scope, id: $id}}) SET n += $props",
            {"scope": scope, "id": node_id, "props": props},
        )

    def _merge_relation(
        self,
        *,
        scope: str,
        relation: str,
        source_label: str,
        source_id: str,
        target_label: str,
        target_id: str,
        props: dict[str, Any],
    ) -> None:
        self._provider.execute_write(
            (
                f"MATCH (a:{source_label} {{project_scope: $scope, id: $source_id}}) "
                f"MATCH (b:{target_label} {{project_scope: $scope, id: $target_id}}) "
                f"MERGE (a)-[r:{relation} {{project_scope: $scope}}]->(b) SET r += $props"
            ),
            {
                "scope": scope,
                "source_id": source_id,
                "target_id": target_id,
                "props": props,
            },
        )


__all__ = ["SecurityGraphStore"]
