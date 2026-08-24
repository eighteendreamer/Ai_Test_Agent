"""录制事件流固化为 Memgraph 子图（方案第 7 / 7.2 节）。

严格仿 ``ui_graph_store.py``：节点 MERGE 键 ``(project_id, id)``、边 MERGE 键
``(project_id, edge_id)``、``payload_json`` 惯例、三级清洗（指纹去重 +
alias 重映射 + MERGE 幂等）。与 UIGraphStore 的差异在于数据来源：
本 store 输入是 PG ``ui_recording_event`` 流水，输出 Recording/Action 节点与
HAS_STEP/TARGETS/ON_PAGE/NAVIGATED_TO 边；Page/Element 沿用既有标签并在
指纹层与 AI 探索产物收敛（Element 指纹 = page + role + name + href，与
``UIGraphStore._element_dedupe_key`` 同构）。

完整性校验（方案 7.2 关口④）：seq 连续性、PG 事件数 vs Action 数对账、
locators 全空 Action 标 resolution_status=degraded、raw-vs-dedup 指标。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.core.config import Settings
from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.schemas.recording import RecorderEvent, RecordingSession


class RecordingGraphStore:
    """Persist finalized recording sessions as project-scoped Memgraph subgraphs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = MemgraphRuntimeProvider(settings)

    # ------------------------------------------------------------------ API

    async def finalize(
        self,
        session: RecordingSession,
        events: list[RecorderEvent],
    ) -> dict[str, Any]:
        """把一次录制会话的 PG 事件流固化为 Memgraph 子图（幂等，可重试）。"""
        return await asyncio.to_thread(self._finalize_sync, session, events)

    async def delete_recording(self, recording_id: str, *, project_id: str) -> dict[str, Any]:
        """删除录制的 Recording/HAS_STEP/Action 子图；Page/Element 保留（方案第 8 章 DELETE）。"""
        return await asyncio.to_thread(self._delete_recording_sync, recording_id, project_id)

    async def get_recording_subgraph(self, recording_id: str, *, project_id: str) -> dict[str, Any]:
        """读取录制在 Memgraph 中的子图投影（GET /recordings/{id}/graph）。

        返回扁平 nodes/edges（前端可视化友好）；只读，不触碰写入路径。
        """
        return await asyncio.to_thread(self._get_recording_subgraph_sync, recording_id, project_id)

    def _get_recording_subgraph_sync(self, recording_id: str, project_id: str) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        if not project_id:
            return {"status": "blocked", "reason": "project_id_required"}
        self._provider.initialize()
        rows = self._provider.execute(
            """
            MATCH (r:Recording {project_id: $project_id, id: $recording_id})
            OPTIONAL MATCH (r)-[hs:HAS_STEP]->(a:Action {project_id: $project_id})
            OPTIONAL MATCH (a)-[t:TARGETS]->(el:Element {project_id: $project_id})
            OPTIONAL MATCH (a)-[op:ON_PAGE]->(p:Page {project_id: $project_id})
            WITH r, a, el, p, hs ORDER BY a.seq
            RETURN r.id AS recording_id, r.name AS recording_name,
                   a.id AS action_id, a.seq AS seq, a.action_type AS action_type,
                   a.resolution_status AS resolution_status, a.occurred_at AS occurred_at,
                   el.id AS element_id, el.name AS element_name,
                   p.id AS page_id, p.url AS page_url, p.title AS page_title
            """,
            {"project_id": project_id, "recording_id": recording_id},
        )
        if not rows:
            return {"status": "not_found", "recording_id": recording_id}

        first = rows[0]
        nodes: dict[str, dict[str, Any]] = {
            f"recording:{recording_id}": {
                "id": recording_id,
                "label": "recording",
                "kind": "recording",
                "name": first.get("recording_name") or "",
            }
        }
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for row in rows:
            action_id = row.get("action_id")
            if action_id:
                key = f"action:{action_id}"
                if key not in nodes:
                    nodes[key] = {
                        "id": str(action_id),
                        "label": f"{row.get('action_type') or ''} #{row.get('seq')}",
                        "kind": "action",
                        "seq": row.get("seq"),
                        "action_type": row.get("action_type"),
                        "resolution_status": row.get("resolution_status"),
                        "occurred_at": row.get("occurred_at"),
                    }
                    edge = ("recording", recording_id, str(action_id))
                    if edge not in seen_edges:
                        seen_edges.add(edge)
                        edges.append(
                            {"source": recording_id, "target": str(action_id), "type": "HAS_STEP"}
                        )
            element_id = row.get("element_id")
            if element_id:
                key = f"element:{element_id}"
                if key not in nodes:
                    nodes[key] = {
                        "id": str(element_id),
                        "label": str(row.get("element_name") or ""),
                        "kind": "element",
                    }
                if action_id:
                    edge = ("targets", str(action_id), str(element_id))
                    if edge not in seen_edges:
                        seen_edges.add(edge)
                        edges.append(
                            {"source": str(action_id), "target": str(element_id), "type": "TARGETS"}
                        )
            page_id = row.get("page_id")
            if page_id:
                key = f"page:{page_id}"
                if key not in nodes:
                    nodes[key] = {
                        "id": str(page_id),
                        "label": str(row.get("page_title") or row.get("page_url") or ""),
                        "kind": "page",
                        "url": row.get("page_url"),
                    }
                if action_id:
                    edge = ("on_page", str(action_id), str(page_id))
                    if edge not in seen_edges:
                        seen_edges.add(edge)
                        edges.append(
                            {"source": str(action_id), "target": str(page_id), "type": "ON_PAGE"}
                        )
        return {
            "status": "success",
            "recording_id": recording_id,
            "nodes": list(nodes.values()),
            "edges": edges,
        }

    # ------------------------------------------------------------- finalize

    def _finalize_sync(self, session: RecordingSession, events: list[RecorderEvent]) -> dict[str, Any]:
        project_id = str(getattr(session, "project_id", "") or "").strip()
        if not project_id:
            return {"status": "blocked", "reason": "project_id_required"}
        self._provider.initialize()

        prepared = self._prepare_graph(session, events)
        integrity = self._check_integrity(session, events, prepared)

        now = datetime.now(timezone.utc).isoformat()
        common = {
            "project_id": project_id,
            "project_scope": "default",
            "recording_id": session.id,
            "updated_at": now,
        }
        if session.session_id:
            common["session_id"] = session.session_id

        recording_count = self._upsert_recording_node(session, common, integrity)
        page_count = self._upsert_nodes("Page", prepared["pages"], common)
        element_count = self._upsert_nodes("Element", prepared["elements"], common)
        action_count = self._upsert_nodes("Action", prepared["actions"], common)

        has_step_edges = on_page_edges = targets_edges = navigated_to_edges = 0
        for action in prepared["actions"]:
            has_step_edges += self._upsert_edge(
                "Recording",
                "HAS_STEP",
                "Action",
                {
                    "from": session.id,
                    "to": str(action.get("id") or ""),
                    "type": "recording_has_step",
                    "seq": action.get("seq"),
                },
                common,
            )
            page_id = str(action.get("page_id") or "")
            if page_id:
                on_page_edges += self._upsert_edge(
                    "Action",
                    "ON_PAGE",
                    "Page",
                    {"from": str(action.get("id") or ""), "to": page_id, "type": "action_on_page"},
                    common,
                )
            element_id = str(action.get("element_id") or "")
            if element_id:
                targets_edges += self._upsert_edge(
                    "Action",
                    "TARGETS",
                    "Element",
                    {"from": str(action.get("id") or ""), "to": element_id, "type": "action_targets_element"},
                    common,
                )
            navigated_page_id = str(action.get("navigated_to_page_id") or "")
            if navigated_page_id:
                navigated_to_edges += self._upsert_edge(
                    "Action",
                    "NAVIGATED_TO",
                    "Page",
                    {
                        "from": str(action.get("id") or ""),
                        "to": navigated_page_id,
                        "type": "action_navigated_to_page",
                        "target_url": action.get("navigated_to_url") or "",
                    },
                    common,
                )

        contains_edges = 0
        for pair in prepared["contains_pairs"]:
            contains_edges += self._upsert_edge(
                "Page",
                "CONTAINS",
                "Element",
                {
                    "from": str(pair.get("from") or ""),
                    "to": str(pair.get("to") or ""),
                    "type": "page_contains_element",
                },
                common,
            )

        integrity["action_vertices"] = action_count
        integrity["reconciled"] = action_count == integrity["pg_event_count"]

        return {
            "status": "success",
            "backend": "memgraph",
            "recording_id": session.id,
            "project_id": project_id,
            "integrity": integrity,
            "metrics": {
                "recording_vertices": recording_count,
                "page_vertices": page_count,
                "element_vertices": element_count,
                "action_vertices": action_count,
                "has_step_edges": has_step_edges,
                "on_page_edges": on_page_edges,
                "targets_edges": targets_edges,
                "navigated_to_edges": navigated_to_edges,
                "contains_edges": contains_edges,
                "raw_events": len(events),
                "raw_element_refs": prepared["metrics"]["raw_element_refs"],
                "deduplicated_element_refs": prepared["metrics"]["deduplicated_element_refs"],
                "raw_page_urls": prepared["metrics"]["raw_page_urls"],
                "deduplicated_page_urls": prepared["metrics"]["deduplicated_page_urls"],
                "degraded_resolution_actions": integrity["degraded_resolution_actions"],
            },
        }

    # ------------------------------------------------------------- prepare

    def _prepare_graph(self, session: RecordingSession, events: list[RecorderEvent]) -> dict[str, Any]:
        """事件流 → 归一化 Page/Element/Action 行 + alias 重映射 + 去重指标。

        纯函数：不触碰 Memgraph，可直接单测（验收：指纹去重 / alias 重映射）。
        """
        recording_id = str(session.id or "")
        pages_by_id: dict[str, dict[str, Any]] = {}
        page_title_by_id: dict[str, str] = {}
        raw_page_urls: list[str] = []

        element_rows_by_fingerprint: dict[tuple[str, ...], dict[str, Any]] = {}
        element_aliases: dict[str, str] = {}
        raw_element_refs = 0

        actions: list[dict[str, Any]] = []
        contains_pairs: dict[tuple[str, str], dict[str, str]] = {}

        for event in events:
            page = event.page if isinstance(event.page, dict) else {}
            page_url = self._normalize_page_url(page.get("url"))
            raw_page_urls.append(page_url)
            page_id = ""
            if page_url:
                page_id = self._stable_node_id("page", page_url)
                if page_id not in pages_by_id:
                    pages_by_id[page_id] = {
                        "id": page_id,
                        "url": page_url,
                        "title": str(page.get("title") or "").strip(),
                        "label": str(page.get("title") or "").strip() or page_url,
                        "kind": "page",
                    }
                elif not page_title_by_id.get(page_id) and str(page.get("title") or "").strip():
                    pages_by_id[page_id]["title"] = str(page.get("title")).strip()
                    pages_by_id[page_id]["label"] = str(page.get("title")).strip()
                if str(page.get("title") or "").strip():
                    page_title_by_id[page_id] = str(page.get("title")).strip()

            target = event.target if isinstance(event.target, dict) else None
            fingerprint: tuple[str, ...] | None = None
            candidate_id = ""
            if target is not None:
                raw_element_refs += 1
                fingerprint = self._element_fingerprint(page_id=page_id, target=target)
                candidate_id = f"{recording_id}:{event.seq}"
                element_id = self._stable_node_id("element", *fingerprint)
                element_aliases[candidate_id] = element_id
                existing = element_rows_by_fingerprint.get(fingerprint)
                if existing is None:
                    element_rows_by_fingerprint[fingerprint] = self._build_element_row(
                        element_id=element_id,
                        fingerprint=fingerprint,
                        target=target,
                    )
                else:
                    existing["merged_duplicate_count"] = int(existing.get("merged_duplicate_count") or 0) + 1
                    existing["duplicate_ids"] = ",".join(
                        value
                        for value in [str(existing.get("duplicate_ids") or "").strip(), candidate_id]
                        if value
                    )

            action_id = f"{recording_id}:{event.seq}"
            masked_payload = self._mask_sensitive_payload(event)
            element_id_remapped = self._apply_alias(element_aliases, candidate_id) if candidate_id else ""
            navigated_to_url = self._extract_navigated_to(event)
            navigated_to_page_id = ""
            if navigated_to_url:
                normalized_nav = self._normalize_page_url(navigated_to_url)
                if normalized_nav:
                    navigated_to_page_id = self._stable_node_id("page", normalized_nav)
                    if navigated_to_page_id not in pages_by_id:
                        pages_by_id[navigated_to_page_id] = {
                            "id": navigated_to_page_id,
                            "url": normalized_nav,
                            "title": "",
                            "label": normalized_nav,
                            "kind": "page",
                        }

            actions.append(
                {
                    "id": action_id,
                    "recording_id": recording_id,
                    "seq": int(event.seq),
                    "action_type": event.type,
                    "label": f"{event.type} #{event.seq}",
                    "kind": "action",
                    "value_masked": masked_payload.get("value_masked"),
                    "value_length": masked_payload.get("value_length"),
                    "page_url": page_url,
                    "page_id": page_id,
                    "element_id": element_id_remapped,
                    "navigated_to_page_id": navigated_to_page_id,
                    "navigated_to_url": navigated_to_url,
                    "occurred_at": event.timestamp.isoformat() if event.timestamp else None,
                    "screenshot_ref": event.screenshot_ref,
                    "resolution_status": self._resolution_status(target),
                    "payload_json": json.dumps(masked_payload["payload"], ensure_ascii=False, default=str),
                }
            )

            if page_id and element_id_remapped:
                contains_pairs[(page_id, element_id_remapped)] = {
                    "from": page_id,
                    "to": element_id_remapped,
                }

        element_rows = list(element_rows_by_fingerprint.values())
        return {
            "pages": list(pages_by_id.values()),
            "elements": element_rows,
            "actions": actions,
            "contains_pairs": list(contains_pairs.values()),
            "metrics": {
                "raw_element_refs": raw_element_refs,
                "deduplicated_element_refs": max(0, raw_element_refs - len(element_rows)),
                "raw_page_urls": len(raw_page_urls),
                "deduplicated_page_urls": len(pages_by_id),
            },
        }

    # ----------------------------------------------------------- integrity

    def _check_integrity(
        self,
        session: RecordingSession,
        events: list[RecorderEvent],
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        """方案 7.2 关口④：seq 连续性 / 步数对账 / 解析失败计数。"""
        seqs = sorted({int(event.seq) for event in events})
        seq_gaps: list[dict[str, int]] = []
        if seqs:
            if seqs[0] > 0:
                seq_gaps.append({"from": 0, "to": seqs[0] - 1, "count": seqs[0]})
            for prev, cur in zip(seqs, seqs[1:]):
                if cur > prev + 1:
                    seq_gaps.append({"from": prev + 1, "to": cur - 1, "count": cur - prev - 1})
        degraded_resolution_actions = sum(
            1 for action in prepared["actions"] if action.get("resolution_status") == "degraded"
        )
        step_count_mismatch = int(getattr(session, "step_count", 0) or 0) != len(events)
        degraded = bool(seq_gaps) or step_count_mismatch or degraded_resolution_actions > 0
        return {
            "degraded": degraded,
            "seq_gaps": seq_gaps,
            "pg_step_count": int(getattr(session, "step_count", 0) or 0),
            "pg_event_count": len(events),
            "step_count_mismatch": step_count_mismatch,
            "degraded_resolution_actions": degraded_resolution_actions,
        }

    # ------------------------------------------------------------ writers

    def _upsert_recording_node(
        self,
        session: RecordingSession,
        common: dict[str, Any],
        integrity: dict[str, Any],
    ) -> int:
        props = {
            "id": str(session.id),
            "label": str(session.name or session.entry_url or session.id),
            "kind": "recording",
            "name": session.name or "",
            "driver_kind": session.driver_kind.value,
            "entry_url": session.entry_url,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "status": "completed",
            "step_count": integrity["pg_event_count"],
            "integrity_degraded": integrity["degraded"],
            "payload_json": json.dumps(
                {
                    "id": session.id,
                    "name": session.name,
                    "entry_url": session.entry_url,
                    "driver_kind": session.driver_kind.value,
                    "step_count": integrity["pg_event_count"],
                    "integrity": integrity,
                },
                ensure_ascii=False,
                default=str,
            ),
        }
        props.update(common)
        self._provider.execute_write(
            """
            MERGE (n:Recording {project_id: $project_id, id: $id})
            SET n += $props
            """,
            {
                "project_id": common["project_id"],
                "id": str(session.id),
                "props": self._drop_none(props),
            },
        )
        return 1

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

    def _delete_recording_sync(self, recording_id: str, project_id: str) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        recording_id = str(recording_id or "").strip()
        if not project_id or not recording_id:
            return {"status": "blocked", "reason": "project_id_and_recording_id_required"}
        self._provider.initialize()
        self._provider.execute_write(
            """
            MATCH (r:Recording {project_id: $project_id, id: $recording_id})
            DETACH DELETE r
            """,
            {"project_id": project_id, "recording_id": recording_id},
        )
        deleted_actions = self._provider.execute_write(
            """
            MATCH (a:Action {project_id: $project_id, recording_id: $recording_id})
            DETACH DELETE a
            RETURN count(a) AS deleted
            """,
            {"project_id": project_id, "recording_id": recording_id},
        )
        return {
            "status": "success",
            "backend": "memgraph",
            "recording_id": recording_id,
            "project_id": project_id,
            "deleted_action_vertices": int((deleted_actions or [{}])[0].get("deleted") or 0),
        }

    # ------------------------------------------------------------- helpers

    def _build_element_row(
        self,
        *,
        element_id: str,
        fingerprint: tuple[str, ...],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        role = fingerprint[1]
        name = fingerprint[2]
        href = fingerprint[4]
        return {
            "id": element_id,
            "role": role,
            "tag": str(target.get("tag") or "").strip().lower(),
            "name": name,
            "href": href,
            "label": name or role or "Element",
            "kind": "element",
            "payload_json": json.dumps(target, ensure_ascii=False, default=str),
        }

    def _element_fingerprint(self, *, page_id: str, target: dict[str, Any]) -> tuple[str, ...]:
        """与 ``UIGraphStore._element_dedupe_key`` 同构：page + role + name + container + href。"""
        locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
        attributes = target.get("attributes") if isinstance(target.get("attributes"), dict) else {}
        role_name = locators.get("role_name") if isinstance(locators.get("role_name"), dict) else {}
        tag = str(target.get("tag") or "").strip().lower()
        role = str(target.get("role") or "").strip().lower() or tag
        name = self._element_display_name(locators, role_name, attributes)
        href = str(attributes.get("href") or "").strip()
        return (page_id, role, self._normalize_text(name), "", href)

    def _element_display_name(
        self,
        locators: dict[str, Any],
        role_name: dict[str, Any],
        attributes: dict[str, Any],
    ) -> str:
        for candidate in (
            locators.get("text"),
            role_name.get("name"),
            attributes.get("aria-label"),
            attributes.get("placeholder"),
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    def _extract_navigated_to(self, event: RecorderEvent) -> str:
        page_effect = event.page_effect if isinstance(event.page_effect, dict) else {}
        navigated_to = str(page_effect.get("navigated_to") or "").strip()
        if navigated_to:
            return navigated_to
        if event.type == "navigate" and isinstance(event.page, dict):
            return str(event.page.get("url") or "").strip()
        return ""

    def _resolution_status(self, target: dict[str, Any] | None) -> str:
        if target is None:
            return "no_target"
        locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
        has_locator = any(
            str(locators.get(key) or "").strip()
            for key in ("id", "testid", "role_name", "css", "xpath", "text")
        )
        return "resolved" if has_locator else "degraded"

    def _mask_sensitive_payload(self, event: RecorderEvent) -> dict[str, Any]:
        """防御性脱敏：采集端（recorder.js）已按红线脱敏，此处兜底密码字段。"""
        payload = event.model_dump(mode="json")
        value_masked: str | None = None
        value_length: int | None = None
        value = event.value
        if value is not None:
            if isinstance(value, dict) and "length" in value and set(value) == {"length"}:
                value_length = int(value["length"])
                value_masked = "masked"
                payload["value"] = {"length": value_length}
            elif self._is_password_field(event.target if isinstance(event.target, dict) else None):
                text = str(value)
                value_length = len(text)
                value_masked = "masked"
                payload["value"] = {"length": value_length}
            else:
                text = str(value)
                value_masked = text[:200]
        return {"payload": payload, "value_masked": value_masked, "value_length": value_length}

    def _is_password_field(self, target: dict[str, Any] | None) -> bool:
        if not target:
            return False
        attributes = target.get("attributes") if isinstance(target.get("attributes"), dict) else {}
        if str(attributes.get("type") or "").strip().lower() == "password":
            return True
        return str(target.get("tag") or "").strip().lower() == "input" and "password" in str(
            attributes.get("class") or ""
        ).lower()

    def _node_properties(self, label: str, row: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
        scalar = self._safe_scalar_map(row)
        if "label" not in scalar or not scalar.get("label"):
            scalar["label"] = str(row.get("name") or row.get("title") or row.get("id") or label)
        scalar["kind"] = label.lower()
        scalar.update(common)
        return self._drop_none(scalar)

    def _edge_properties(self, row: dict[str, Any], common: dict[str, Any], relation: str, edge_id: str) -> dict[str, Any]:
        scalar = self._safe_scalar_map(row)
        scalar["type"] = str(row.get("type") or relation.lower())
        scalar["edge_id"] = edge_id
        scalar["payload_json"] = json.dumps(row, ensure_ascii=False, default=str)
        scalar.update(common)
        return self._drop_none(scalar)

    def _drop_none(self, props: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in props.items() if value is not None}

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

    def _apply_alias(self, aliases: dict[str, str], node_id: str) -> str:
        current = node_id
        seen: set[str] = set()
        while current and current in aliases and current not in seen:
            seen.add(current)
            current = aliases[current]
        return current

    def _normalize_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    def _normalize_page_url(self, url: Any) -> str:
        """页归一化：去 hash、去尾斜杠、host/scheme 小写（方案 7.2 关口③ Page MERGE 规则）。"""
        raw = str(url or "").strip()
        if not raw:
            return ""
        parts = urlsplit(raw)
        if not parts.scheme and not parts.netloc:
            return raw
        path = parts.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
