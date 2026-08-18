from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider


class UIGraphStore:
    """Persist UI Explorer output as project-scoped Memgraph nodes and relationships."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = MemgraphRuntimeProvider(settings)

    async def write_exploration_graph(
        self,
        graph: dict[str, Any],
        *,
        app_map: dict[str, Any] | None = None,
        session_id: str,
        turn_id: str,
        trace_id: str,
        project_id: str | None = None,
        project_scope: str = "default",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._write_sync,
            graph,
            app_map=app_map or {},
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            project_id=project_id,
            project_scope=project_scope,
        )

    def _write_sync(
        self,
        graph: dict[str, Any],
        *,
        app_map: dict[str, Any],
        session_id: str,
        turn_id: str,
        trace_id: str,
        project_id: str | None,
        project_scope: str,
    ) -> dict[str, Any]:
        self._provider.initialize()
        normalized_graph, normalized_app_map, normalization = self._normalize_for_write(graph, app_map)
        now = datetime.utcnow().isoformat()
        common = {
            "project_id": str(project_id or "").strip() or None,
            "project_scope": project_scope,
            "session_id": session_id,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "updated_at": now,
        }
        if not common["project_id"]:
            return {"status": "blocked", "reason": "project_id_required"}
        page_count = self._upsert_nodes("Page", normalized_graph.get("pages") or [], common)
        element_count = self._upsert_nodes("Element", normalized_graph.get("elements") or [], common)
        entity_count = self._upsert_nodes("Entity", normalized_graph.get("entities") or [], common)
        edge_count = 0
        for edge in normalized_graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            edge_type = str(edge.get("type") or "").strip()
            if edge_type == "page_contains_element":
                edge_count += self._upsert_edge("Page", "CONTAINS", "Element", edge, common)
            elif edge_type == "element_belongs_to_entity":
                edge_count += self._upsert_edge("Element", "BELONGS_TO", "Entity", edge, common)
            elif edge_type == "element_triggers_navigation":
                edge_count += self._upsert_edge("Element", "TRIGGERS_NAVIGATION", "Page", edge, common)
            elif edge_type == "element_reveals_element":
                edge_count += self._upsert_edge("Element", "REVEALS", "Element", edge, common)
        interaction_edge_count = self._upsert_interaction_edges(
            graph=normalized_graph,
            app_map=normalized_app_map,
            common=common,
        )
        return {
            "status": "success",
            "backend": "memgraph",
            "project_id": project_id,
            "project_scope": project_scope,
            "metrics": {
                "page_vertices": page_count,
                "element_vertices": element_count,
                "entity_vertices": entity_count,
                "edges": edge_count + interaction_edge_count,
                "interaction_edges": interaction_edge_count,
                "raw_page_vertices": len(graph.get("pages") or []),
                "raw_element_vertices": len(graph.get("elements") or []),
                "raw_entity_vertices": len(graph.get("entities") or []),
                "raw_edges": len(graph.get("edges") or []),
                "deduplicated_elements": int(normalization.get("deduplicated_elements") or 0),
                "deduplicated_edges": int(normalization.get("deduplicated_edges") or 0),
                "deduplicated_interactions": int(normalization.get("deduplicated_interactions") or 0),
            },
        }

    def _normalize_for_write(
        self,
        graph: dict[str, Any],
        app_map: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        pages = [dict(row) for row in graph.get("pages") or [] if isinstance(row, dict)]
        elements = [dict(row) for row in graph.get("elements") or [] if isinstance(row, dict)]
        entities = [dict(row) for row in graph.get("entities") or [] if isinstance(row, dict)]
        edges = [dict(row) for row in graph.get("edges") or [] if isinstance(row, dict)]

        page_aliases: dict[str, str] = {}
        entity_aliases: dict[str, str] = {}
        dedup_pages = self._dedupe_nodes(pages, self._page_dedupe_key, page_aliases)
        dedup_entities = self._dedupe_nodes(entities, self._entity_dedupe_key, entity_aliases)

        entity_name_by_id = {
            str(row.get("id") or "").strip(): self._normalize_text(
                row.get("name") or row.get("label") or row.get("title") or row.get("url") or row.get("id") or ""
            )
            for row in dedup_entities
            if str(row.get("id") or "").strip()
        }

        element_page_ids: dict[str, set[str]] = {}
        element_entity_ids: dict[str, set[str]] = {}
        element_hrefs: dict[str, set[str]] = {}
        for edge in edges:
            edge_type = str(edge.get("type") or "").strip()
            source_id = self._apply_alias(page_aliases, str(edge.get("from") or "").strip())
            target_id = str(edge.get("to") or "").strip()
            if edge_type == "page_contains_element" and source_id and target_id:
                element_page_ids.setdefault(target_id, set()).add(source_id)
            elif edge_type == "element_belongs_to_entity":
                source_id = str(edge.get("from") or "").strip()
                target_id = self._apply_alias(entity_aliases, str(edge.get("to") or "").strip())
                if source_id and target_id:
                    element_entity_ids.setdefault(source_id, set()).add(target_id)
            elif edge_type == "element_triggers_navigation":
                source_id = str(edge.get("from") or "").strip()
                href = str(edge.get("href") or "").strip()
                if source_id and href:
                    element_hrefs.setdefault(source_id, set()).add(href)

        element_aliases: dict[str, str] = {}
        dedup_elements = self._dedupe_nodes(
            elements,
            lambda row: self._element_dedupe_key(
                row=row,
                page_ids=element_page_ids.get(str(row.get("id") or "").strip(), set()),
                entity_ids=element_entity_ids.get(str(row.get("id") or "").strip(), set()),
                hrefs=element_hrefs.get(str(row.get("id") or "").strip(), set()),
                entity_name_by_id=entity_name_by_id,
            ),
            element_aliases,
        )

        remapped_edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str, str, str]] = set()
        for edge in edges:
            remapped = dict(edge)
            remapped["from"] = self._apply_alias(page_aliases, str(remapped.get("from") or "").strip())
            remapped["from"] = self._apply_alias(element_aliases, remapped["from"])
            remapped["from"] = self._apply_alias(entity_aliases, remapped["from"])
            remapped["to"] = self._apply_alias(page_aliases, str(remapped.get("to") or "").strip())
            remapped["to"] = self._apply_alias(element_aliases, remapped["to"])
            remapped["to"] = self._apply_alias(entity_aliases, remapped["to"])
            if not remapped.get("from") or not remapped.get("to"):
                continue
            edge_key = (
                str(remapped.get("type") or "").strip(),
                str(remapped.get("from") or "").strip(),
                str(remapped.get("to") or "").strip(),
                str(remapped.get("href") or "").strip(),
                str(remapped.get("target_url") or "").strip(),
            )
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            remapped_edges.append(remapped)

        normalized_app_map = dict(app_map)
        interaction_dedupe_count = 0
        normalized_pages: list[dict[str, Any]] = []
        for page in app_map.get("pages") or []:
            if not isinstance(page, dict):
                continue
            normalized_page = dict(page)
            interactions = []
            seen_interactions: set[tuple[str, str, str, str]] = set()
            for interaction in page.get("interactions") or []:
                if not isinstance(interaction, dict):
                    continue
                normalized_interaction = dict(interaction)
                trigger = dict(normalized_interaction.get("trigger") or {})
                trigger_id = self._apply_alias(element_aliases, str(trigger.get("id") or "").strip())
                if trigger_id:
                    trigger["id"] = trigger_id
                normalized_interaction["trigger"] = trigger
                interaction_key = (
                    str(trigger.get("id") or "").strip(),
                    str(trigger.get("name") or "").strip(),
                    str(normalized_interaction.get("effect") or "").strip(),
                    str(normalized_interaction.get("target_url") or "").strip(),
                )
                if interaction_key in seen_interactions:
                    interaction_dedupe_count += 1
                    continue
                seen_interactions.add(interaction_key)
                interactions.append(normalized_interaction)
            normalized_page["interactions"] = interactions
            normalized_pages.append(normalized_page)
        normalized_app_map["pages"] = normalized_pages

        normalized_graph = {
            "pages": dedup_pages,
            "elements": dedup_elements,
            "entities": dedup_entities,
            "edges": remapped_edges,
        }
        normalization = {
            "deduplicated_elements": max(0, len(elements) - len(dedup_elements)),
            "deduplicated_edges": max(0, len(edges) - len(remapped_edges)),
            "deduplicated_interactions": interaction_dedupe_count,
        }
        return normalized_graph, normalized_app_map, normalization

    def _upsert_interaction_edges(
        self,
        *,
        graph: dict[str, Any],
        app_map: dict[str, Any],
        common: dict[str, Any],
    ) -> int:
        page_id_by_url: dict[str, str] = {}
        for page in graph.get("pages") or []:
            if not isinstance(page, dict):
                continue
            page_id = str(page.get("id") or "").strip()
            page_url = str(page.get("url") or "").strip()
            if page_id and page_url:
                page_id_by_url[page_url] = page_id

        edge_count = 0
        for page in app_map.get("pages") or []:
            if not isinstance(page, dict):
                continue
            page_url = str(page.get("url") or "").strip()
            source_page_id = page_id_by_url.get(page_url) or self._stable_node_id("page", page_url)
            for interaction in page.get("interactions") or []:
                if not isinstance(interaction, dict):
                    continue
                trigger = interaction.get("trigger") if isinstance(interaction.get("trigger"), dict) else {}
                trigger_id = str(trigger.get("id") or "").strip()
                effect = str(interaction.get("effect") or "").strip()
                target_url = str(interaction.get("target_url") or "").strip()
                if trigger_id:
                    interaction_edge = {
                        "type": "page_interacted_with_element",
                        "from": source_page_id,
                        "to": trigger_id,
                        "effect": effect,
                        "target_url": target_url,
                        "trigger_name": str(trigger.get("name") or "").strip(),
                        "trigger_role": str(trigger.get("role") or "").strip(),
                        "revealed_count": int(interaction.get("revealed_count") or 0),
                    }
                    edge_count += self._upsert_edge("Page", "INTERACTED_WITH", "Element", interaction_edge, common)
                if target_url:
                    target_page_id = page_id_by_url.get(target_url) or self._stable_node_id("page", target_url)
                    navigation_edge = {
                        "type": "page_navigates_to_page",
                        "from": source_page_id,
                        "to": target_page_id,
                        "effect": effect or "navigation",
                        "target_url": target_url,
                        "trigger_id": trigger_id,
                        "trigger_name": str(trigger.get("name") or "").strip(),
                    }
                    edge_count += self._upsert_edge("Page", "NAVIGATES_TO", "Page", navigation_edge, common)
        return edge_count

    def _upsert_nodes(self, label: str, rows: list[Any], common: dict[str, Any]) -> int:
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            node_id = str(row.get("id") or "").strip()
            if not node_id:
                continue
            props = self._node_properties(label, row, common)
            self._provider.execute_write(
                f"""
                MERGE (n:{label} {{project_id: $project_id, id: $id}})
                SET n += $props
                """,
                {
                    "project_id": common["project_id"],
                    "id": node_id,
                    "props": props,
                },
            )
            count += 1
        return count

    def _upsert_edge(
        self,
        from_label: str,
        relation: str,
        to_label: str,
        edge: dict[str, Any],
        common: dict[str, Any],
    ) -> int:
        source_id = str(edge.get("from") or "").strip()
        target_id = str(edge.get("to") or "").strip()
        if not source_id or not target_id:
            return 0
        edge_id = self._scoped_key(common["project_id"], relation, source_id, target_id, edge.get("href") or "")
        props = self._edge_properties(edge, common, relation, edge_id)
        self._provider.execute_write(
            f"""
            MATCH (a:{from_label} {{project_id: $project_id, id: $source_id}})
            MATCH (b:{to_label} {{project_id: $project_id, id: $target_id}})
            MERGE (a)-[r:{relation} {{project_id: $project_id, edge_id: $edge_id}}]->(b)
            SET r += $props
            """,
            {
                "project_id": common["project_id"],
                "source_id": source_id,
                "target_id": target_id,
                "edge_id": edge_id,
                "props": props,
            },
        )
        return 1

    def _node_properties(self, label: str, row: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
        scalar = self._safe_scalar_map(row)
        if label == "Page":
            scalar["label"] = str(row.get("title") or row.get("name") or row.get("url") or row.get("id") or "Page")
        elif label == "Element":
            scalar["label"] = str(row.get("name") or row.get("title") or row.get("role") or row.get("id") or "Element")
        else:
            scalar["label"] = str(row.get("name") or row.get("title") or row.get("id") or "Entity")
        scalar["kind"] = label.lower()
        scalar["payload_json"] = json.dumps(row, ensure_ascii=False)
        scalar.update(common)
        return scalar

    def _edge_properties(self, row: dict[str, Any], common: dict[str, Any], relation: str, edge_id: str) -> dict[str, Any]:
        scalar = self._safe_scalar_map(row)
        scalar["type"] = str(row.get("type") or relation.lower())
        scalar["edge_id"] = edge_id
        scalar["payload_json"] = json.dumps(row, ensure_ascii=False)
        scalar.update(common)
        return scalar

    def _safe_scalar_map(self, row: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in row.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                result[str(key)] = value
        return result

    def _scoped_key(self, *parts: Any) -> str:
        raw = "::".join(str(part or "") for part in parts).strip()
        if not raw:
            return ""
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        label = re.sub(r"[^A-Za-z0-9_.:@()+,=;$!*'%-]+", "_", str(parts[-1] or "item")).strip("._")
        label = label[:64] or "item"
        return f"{label}_{digest}"

    def _stable_node_id(self, *parts: Any) -> str:
        digest = hashlib.sha1(":".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()
        return f"ui_{digest[:20]}"

    def _dedupe_nodes(
        self,
        rows: list[dict[str, Any]],
        key_builder: Any,
        aliases: dict[str, str],
    ) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        canonical_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in rows:
            node_id = str(row.get("id") or "").strip()
            if not node_id:
                continue
            key = key_builder(row)
            existing = canonical_by_key.get(key)
            if existing is None:
                canonical_by_key[key] = row
                deduped.append(row)
                continue
            aliases[node_id] = str(existing.get("id") or "").strip()
            merge_count = int(existing.get("merged_duplicate_count") or 0) + 1
            existing["merged_duplicate_count"] = merge_count
            existing["duplicate_ids"] = ",".join(
                value for value in [str(existing.get("duplicate_ids") or "").strip(), node_id] if value
            )
        return deduped

    def _page_dedupe_key(self, row: dict[str, Any]) -> tuple[str, ...]:
        return (
            self._normalize_text(row.get("url") or row.get("id") or ""),
            self._normalize_text(row.get("title") or row.get("name") or ""),
        )

    def _entity_dedupe_key(self, row: dict[str, Any]) -> tuple[str, ...]:
        return (self._normalize_text(row.get("name") or row.get("label") or row.get("title") or row.get("id") or ""),)

    def _element_dedupe_key(
        self,
        *,
        row: dict[str, Any],
        page_ids: set[str],
        entity_ids: set[str],
        hrefs: set[str],
        entity_name_by_id: dict[str, str],
    ) -> tuple[str, ...]:
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        parent_entity = self._normalize_text(
            context.get("entity")
            or "|".join(entity_name_by_id.get(entity_id, "") for entity_id in sorted(entity_ids))
        )
        container_name = self._normalize_text(context.get("container_name") or "")
        return (
            "|".join(sorted(page_ids)),
            self._normalize_text(row.get("role") or ""),
            self._normalize_text(row.get("name") or row.get("label") or ""),
            parent_entity or container_name,
            "|".join(sorted(hrefs)),
        )

    def _apply_alias(self, aliases: dict[str, str], node_id: str) -> str:
        current = node_id
        seen: set[str] = set()
        while current and current in aliases and current not in seen:
            seen.add(current)
            current = aliases[current]
        return current

    def _normalize_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()
