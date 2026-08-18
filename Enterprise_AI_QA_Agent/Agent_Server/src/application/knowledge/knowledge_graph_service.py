from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.schemas.knowledge import (
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeGraphResponse,
    KnowledgeGraphSummary,
    KnowledgeProjectDeleteResponse,
    KnowledgeProjectSummary,
)


class KnowledgeGraphService:
    _PROJECT_CACHE_TTL_SECONDS = 5.0
    _FAILURE_CACHE_TTL_SECONDS = 3.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = MemgraphRuntimeProvider(settings)
        self._project_cache: tuple[float, list[KnowledgeProjectSummary]] | None = None
        self._last_failure: tuple[float, str] | None = None
        self._projects = None

    def set_project_service(self, project_service) -> None:  # noqa: ANN001
        self._projects = project_service

    async def list_projects(self) -> list[KnowledgeProjectSummary]:
        cached_error = self._cached_failure_message()
        if cached_error is not None:
            raise RuntimeError(cached_error)

        if getattr(self, "_projects", None) is None:
            cached_projects = self._cached_projects()
            if cached_projects is not None:
                return cached_projects

        try:
            if getattr(self, "_projects", None) is not None:
                page = await self._projects.list(status="active", query=None, limit=200, offset=0)
                summaries: list[KnowledgeProjectSummary] = []
                for project in page.items:
                    if not project.graph_scope_key:
                        continue
                    summary = await self.get_project_summary(project.id)
                    if summary.page_count + summary.element_count + summary.entity_count + summary.edge_count:
                        summaries.append(summary)
                projects = summaries
            else:
                projects = await asyncio.to_thread(self._list_projects_sync)
        except Exception as exc:
            self._last_failure = (time.monotonic(), str(exc))
            raise

        self._project_cache = (time.monotonic(), projects)
        self._last_failure = None
        return projects

    async def get_graph(self, project_id: str) -> KnowledgeGraphResponse:
        cached_error = self._cached_failure_message()
        if cached_error is not None:
            raise RuntimeError(cached_error)
        try:
            identity = await self._resolve_identity(project_id, require_active=True)
            return await asyncio.to_thread(self._get_graph_sync, identity)
        except (KeyError, ValueError):
            raise
        except Exception as exc:
            self._last_failure = (time.monotonic(), str(exc))
            raise

    async def get_project_summary(self, project_id: str) -> KnowledgeProjectSummary:
        identity = await self._resolve_identity(project_id, require_active=False)
        return await asyncio.to_thread(self._get_project_summary_sync, identity)

    async def get_generation_context(
        self,
        project_id: str,
        *,
        node_limit: int = 100,
        edge_limit: int = 150,
    ) -> dict[str, Any]:
        identity = await self._resolve_identity(project_id, require_active=False)
        return await asyncio.to_thread(
            self._get_generation_context_sync,
            identity,
            max(1, min(int(node_limit), 500)),
            max(1, min(int(edge_limit), 1000)),
        )

    async def _resolve_identity(self, project_id: str, *, require_active: bool) -> dict[str, str | None]:
        value = str(project_id or "").strip()
        if not value:
            raise ValueError("project_id is required")
        if self._projects is None:
            return {"project_id": None, "project_scope": value}
        project = (
            await self._projects.require_active(value)
            if require_active
            else await self._projects.get(value)
        )
        scope = str(project.graph_scope_key or "").strip()
        if not scope:
            raise ValueError(f"Project has no graph_scope_key: {value}")
        return {"project_id": str(project.id), "project_scope": scope}

    def _migrate_legacy_records(self, identity: dict[str, str | None]) -> None:
        if not identity.get("project_id"):
            return
        self._provider.execute_write(
            """
            MATCH (n)
            WHERE n.project_id IS NULL AND n.project_scope = $project_scope
            SET n.project_id = $project_id
            """,
            identity,
        )
        self._provider.execute_write(
            """
            MATCH ()-[r]->()
            WHERE r.project_id IS NULL AND r.project_scope = $project_scope
            SET r.project_id = $project_id
            """,
            identity,
        )

    def _get_project_summary_sync(self, identity: dict[str, str | None]) -> KnowledgeProjectSummary:
        project_id = identity.get("project_id")
        project_scope = str(identity.get("project_scope") or "")
        self._migrate_legacy_records(identity)
        selector = "project_id: $project_id" if project_id else "project_scope: $project_scope"
        parameters = identity
        rows = self._provider.execute(
            f"""
            MATCH (n:Page {{{selector}}})
            RETURN count(n) AS total, max(n.updated_at) AS latest_updated_at, 'page_count' AS count_field
            UNION ALL
            MATCH (n:Element {{{selector}}})
            RETURN count(n) AS total, max(n.updated_at) AS latest_updated_at, 'element_count' AS count_field
            UNION ALL
            MATCH (n:Entity {{{selector}}})
            RETURN count(n) AS total, max(n.updated_at) AS latest_updated_at, 'entity_count' AS count_field
            UNION ALL
            MATCH ()-[r]->()
            WHERE r.{('project_id' if project_id else 'project_scope')} = ${('project_id' if project_id else 'project_scope')}
            RETURN count(r) AS total, max(r.updated_at) AS latest_updated_at, 'edge_count' AS count_field
            """,
            parameters,
        )
        counts = {"page_count": 0, "element_count": 0, "entity_count": 0, "edge_count": 0}
        latest = None
        for row in rows:
            field = str(row.get("count_field") or "")
            if field in counts:
                counts[field] = int(row.get("total") or 0)
            value = self._parse_datetime(row.get("latest_updated_at"))
            if value and (latest is None or value > latest):
                latest = value
        return KnowledgeProjectSummary(
            project_id=project_id,
            project_scope=project_scope,
            latest_updated_at=latest,
            **counts,
        )

    def _get_generation_context_sync(
        self,
        identity: dict[str, str | None],
        node_limit: int,
        edge_limit: int,
    ) -> dict[str, Any]:
        summary = self._get_project_summary_sync(identity)
        project_id = identity.get("project_id")
        project_scope = str(identity.get("project_scope") or "")
        selector = "project_id: $project_id" if project_id else "project_scope: $project_scope"
        per_kind_limit = max(1, node_limit // 3)
        nodes: list[dict[str, Any]] = []
        for label, kind in (("Page", "page"), ("Element", "element"), ("Entity", "entity")):
            rows = self._provider.execute(
                f"""
                MATCH (n:{label} {{{selector}}})
                RETURN n.id AS id, n.label AS label, n.url AS url,
                       n.role AS role, n.type AS type
                ORDER BY n.updated_at DESC
                LIMIT {per_kind_limit}
                """,
                identity,
            )
            nodes.extend(
                {
                    "id": str(row.get("id") or ""),
                    "kind": kind,
                    "label": str(row.get("label") or row.get("id") or ""),
                    "url": str(row.get("url") or ""),
                    "role": str(row.get("role") or ""),
                    "type": str(row.get("type") or ""),
                }
                for row in rows
                if row.get("id")
            )
        edge_rows = self._provider.execute(
            f"""
            MATCH (a)-[r]->(b)
            WHERE r.{('project_id' if project_id else 'project_scope')} = ${('project_id' if project_id else 'project_scope')}
            RETURN a.id AS source_id, b.id AS target_id, type(r) AS relation
            ORDER BY r.updated_at DESC
            LIMIT {edge_limit}
            """,
            identity,
        )
        return {
            "project_id": project_id,
            "project_scope": project_scope,
            "summary": summary.model_dump(mode="json"),
            "nodes": nodes[:node_limit],
            "edges": [
                {
                    "source_id": str(row.get("source_id") or ""),
                    "target_id": str(row.get("target_id") or ""),
                    "relation": str(row.get("relation") or ""),
                }
                for row in edge_rows
            ],
            "latest_updated_at": (
                summary.latest_updated_at.isoformat() if summary.latest_updated_at else None
            ),
        }

    async def delete_project(self, project_id: str) -> KnowledgeProjectDeleteResponse:
        cached_error = self._cached_failure_message()
        if cached_error is not None:
            raise RuntimeError(cached_error)
        try:
            identity = await self._resolve_identity(project_id, require_active=True)
            deleted = await asyncio.to_thread(self._delete_project_sync, identity)
        except (KeyError, ValueError):
            raise
        except Exception as exc:
            self._last_failure = (time.monotonic(), str(exc))
            raise
        self._project_cache = None
        self._last_failure = None
        return deleted

    def _list_projects_sync(self) -> list[KnowledgeProjectSummary]:
        summary_map: dict[str, dict[str, Any]] = {}
        rows = self._provider.execute(
            """
            MATCH (n:Page)
            WHERE n.project_scope IS NOT NULL AND n.project_scope <> ""
            RETURN n.project_scope AS project_scope,
                   count(n) AS total,
                   max(n.updated_at) AS latest_updated_at,
                   'page_count' AS count_field
            UNION ALL
            MATCH (n:Element)
            WHERE n.project_scope IS NOT NULL AND n.project_scope <> ""
            RETURN n.project_scope AS project_scope,
                   count(n) AS total,
                   max(n.updated_at) AS latest_updated_at,
                   'element_count' AS count_field
            UNION ALL
            MATCH (n:Entity)
            WHERE n.project_scope IS NOT NULL AND n.project_scope <> ""
            RETURN n.project_scope AS project_scope,
                   count(n) AS total,
                   max(n.updated_at) AS latest_updated_at,
                   'entity_count' AS count_field
            UNION ALL
            MATCH ()-[r]->()
            WHERE r.project_scope IS NOT NULL AND r.project_scope <> ""
            RETURN r.project_scope AS project_scope,
                   count(r) AS total,
                   max(r.updated_at) AS latest_updated_at,
                   'edge_count' AS count_field
            """
        )
        for row in rows:
            self._merge_scope_counts(summary_map, [row], str(row.get("count_field") or "edge_count"))
        items = [
            KnowledgeProjectSummary(
                project_scope=scope,
                page_count=int(data.get("page_count") or 0),
                element_count=int(data.get("element_count") or 0),
                entity_count=int(data.get("entity_count") or 0),
                edge_count=int(data.get("edge_count") or 0),
                latest_updated_at=self._parse_datetime(data.get("latest_updated_at")),
            )
            for scope, data in summary_map.items()
            if scope
        ]
        items.sort(
            key=lambda item: (
                item.latest_updated_at or datetime.min,
                item.page_count + item.element_count + item.entity_count + item.edge_count,
                item.project_scope,
            ),
            reverse=True,
        )
        return items

    def _get_graph_sync(self, identity: dict[str, str | None]) -> KnowledgeGraphResponse:
        project_id = identity.get("project_id")
        scope = str(identity.get("project_scope") or "")
        self._migrate_legacy_records(identity)
        selector = "project_id: $project_id" if project_id else "project_scope: $project_scope"
        relation_key = "project_id" if project_id else "project_scope"
        pages = self._provider.execute(
            f"MATCH (n:Page {{{selector}}}) RETURN n ORDER BY n.updated_at DESC",
            identity,
        )
        elements = self._provider.execute(
            f"MATCH (n:Element {{{selector}}}) RETURN n ORDER BY n.updated_at DESC",
            identity,
        )
        entities = self._provider.execute(
            f"MATCH (n:Entity {{{selector}}}) RETURN n ORDER BY n.updated_at DESC",
            identity,
        )
        if not pages and not elements and not entities:
            raise KeyError(scope)
        edge_rows = self._provider.execute(
            f"""
            MATCH (a)-[r]->(b)
            WHERE r.{relation_key} = ${relation_key}
            RETURN a.id AS source_id, b.id AS target_id, type(r) AS relation, r
            ORDER BY r.updated_at DESC
            """,
            identity,
        )
        nodes = [
            *[self._node_from_record(item["n"], "page") for item in pages],
            *[self._node_from_record(item["n"], "element") for item in elements],
            *[self._node_from_record(item["n"], "entity") for item in entities],
        ]
        edges: list[KnowledgeGraphEdge] = []
        relation_counts: dict[str, int] = {}
        for row in edge_rows:
            edge = self._edge_from_record(row)
            edges.append(edge)
            relation_counts[edge.type] = relation_counts.get(edge.type, 0) + 1
        latest_values = [
            parsed
            for parsed in (
                self._parse_datetime(self._record_value(item["n"], "updated_at"))
                for item in [*pages, *elements, *entities]
            )
            if parsed is not None
        ]
        latest_updated_at = max(latest_values, default=None)
        return KnowledgeGraphResponse(
            summary=KnowledgeGraphSummary(
                project_id=project_id,
                project_scope=scope,
                page_count=len(pages),
                element_count=len(elements),
                entity_count=len(entities),
                edge_count=len(edges),
                relation_counts=dict(sorted(relation_counts.items())),
                latest_updated_at=latest_updated_at,
            ),
            nodes=nodes,
            edges=edges,
        )

    def _delete_project_sync(self, identity: dict[str, str | None]) -> KnowledgeProjectDeleteResponse:
        scope = str(identity.get("project_scope") or "")
        graph = self._get_graph_sync(identity)
        deleted_counts = {
            "pages": graph.summary.page_count,
            "elements": graph.summary.element_count,
            "entities": graph.summary.entity_count,
            "edges": graph.summary.edge_count,
        }
        relation_key = "project_id" if identity.get("project_id") else "project_scope"
        self._provider.execute_write(
            f"""
            MATCH (n)
            WHERE n.{relation_key} = ${relation_key}
            DETACH DELETE n
            """,
            identity,
        )
        return KnowledgeProjectDeleteResponse(
            ok=True,
            project_id=identity.get("project_id"),
            project_scope=scope,
            deleted_counts=deleted_counts,
            message=f"Deleted knowledge graph project '{scope}'",
        )

    def _cached_projects(self) -> list[KnowledgeProjectSummary] | None:
        if self._project_cache is None:
            return None
        cached_at, projects = self._project_cache
        if time.monotonic() - cached_at > self._PROJECT_CACHE_TTL_SECONDS:
            return None
        return [project.model_copy() for project in projects]

    def _cached_failure_message(self) -> str | None:
        if self._last_failure is None:
            return None
        cached_at, message = self._last_failure
        if time.monotonic() - cached_at > self._FAILURE_CACHE_TTL_SECONDS:
            return None
        return message

    def _merge_scope_counts(self, summary_map: dict[str, dict[str, Any]], rows: list[dict[str, Any]], count_field: str) -> None:
        for row in rows:
            scope = str(row.get("project_scope") or "").strip()
            if not scope:
                continue
            entry = summary_map.setdefault(
                scope,
                {
                    "page_count": 0,
                    "element_count": 0,
                    "entity_count": 0,
                    "edge_count": 0,
                    "latest_updated_at": None,
                },
            )
            entry[count_field] = int(entry.get(count_field) or 0) + int(row.get("total") or 0)
            latest = row.get("latest_updated_at")
            if latest and (entry["latest_updated_at"] is None or str(latest) > str(entry["latest_updated_at"])):
                entry["latest_updated_at"] = latest

    def _node_from_record(self, record: Any, kind: str) -> KnowledgeGraphNode:
        metadata = self._metadata_from_payload(self._record_value(record, "payload_json"))
        label = str(self._record_value(record, "label") or self._record_value(record, "id") or kind.title()).strip()
        summary = str(
            self._record_value(record, "url")
            or self._record_value(record, "role")
            or self._record_value(record, "type")
            or ""
        ).strip()
        return KnowledgeGraphNode(
            id=str(self._record_value(record, "id") or label),
            label=label,
            kind=kind,
            summary=summary,
            metadata=metadata,
        )

    def _edge_from_record(self, row: dict[str, Any]) -> KnowledgeGraphEdge:
        record = row.get("r")
        relation = str(row.get("relation") or self._record_value(record, "type") or "RELATED_TO").strip()
        relation_key = self._relation_key(relation)
        metadata = self._metadata_from_payload(self._record_value(record, "payload_json"))
        return KnowledgeGraphEdge(
            id=str(self._record_value(record, "edge_id") or f"{relation_key}:{row.get('source_id')}:{row.get('target_id')}"),
            source=str(row.get("source_id") or ""),
            target=str(row.get("target_id") or ""),
            type=relation_key,
            label=self._format_relation_label(relation_key),
            metadata=metadata,
        )

    def _record_value(self, record: Any, key: str) -> Any:
        if record is None:
            return None
        try:
            return record.get(key)
        except AttributeError:
            return None

    def _metadata_from_payload(self, payload_json: Any) -> dict[str, Any]:
        if not payload_json:
            return {}
        try:
            payload = json.loads(str(payload_json))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _relation_key(self, relation: str) -> str:
        normalized = relation.strip().upper()
        mapping = {
            "CONTAINS": "page_contains_element",
            "BELONGS_TO": "element_belongs_to_entity",
            "TRIGGERS_NAVIGATION": "element_triggers_navigation",
            "REVEALS": "element_reveals_element",
            "INTERACTED_WITH": "page_interacted_with_element",
            "NAVIGATES_TO": "page_navigates_to_page",
        }
        return mapping.get(normalized, normalized.lower())

    def _format_relation_label(self, edge_type: str) -> str:
        return edge_type.replace("_", " ").title()

    def _parse_datetime(self, value: Any) -> datetime | None:
        return ensure_utc_datetime(value)
