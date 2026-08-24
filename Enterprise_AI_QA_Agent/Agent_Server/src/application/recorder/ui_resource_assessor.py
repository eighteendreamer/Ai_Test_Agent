"""UI 自动化三源资源检索（方案 4.2 环节③，P0-8）。

检索源与判定（方案 4.2 表格）：
- Memgraph 图谱：该项目下 Page/Element/Action 覆盖数（是否有 Action = 录制产物）；
- 用例库：该项目下 lifecycle_status=active（已启用固定版本）的用例数；
- Memory 语义检索：既有 hit_count/max_score 阈值逻辑（原样保留）。

充分条件三选一：有用例可复用 或 图谱覆盖达标 或 memory 命中充分；
都不足 → 编排层进入录制审批分支（方案 4.1 awaiting_recording_approval）。

审计要求（方案 4.2）：返回结构带三源各自的命中计数与判定理由——让用户知道
"查过哪里、为什么不够"。任一源不可用按 0 计并标记 status，不阻塞主链路。
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.config import Settings
from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider

logger = logging.getLogger(__name__)

# 方案 4.2 未给定具体阈值：保守可调常量——Page>=1 且 Element>=10 视为覆盖达标
# （Element 数量低于此值时元素图谱不足以支撑定位与用例生成）。
GRAPH_SUFFICIENT_PAGES = 1
GRAPH_SUFFICIENT_ELEMENTS = 10

# 用例库检索边界：只需判断"是否存在已启用用例"，limit=1 足够
_CASE_PROBE_LIMIT = 1


class UIResourceAssessor:
    """三源检索聚合器：图谱 / 用例库 / Memory，输出充分性判定与审计明细。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        test_case_service: Any | None = None,
        memory_runtime_service: Any | None = None,
        memgraph_provider: MemgraphRuntimeProvider | None = None,
    ) -> None:
        self._settings = settings
        self._test_case_service = test_case_service
        self._memory_runtime_service = memory_runtime_service
        self._memgraph_provider = memgraph_provider

    async def assess(
        self,
        *,
        project_id: str,
        target_url: str,
        objective: str,
        project_scope: str,
        context: Any,
    ) -> dict[str, Any]:
        """执行三源检索并聚合判定。任一源失败降级为该源计数 0（可审计）。"""
        graph = self._assess_graph(project_id)
        cases = await self._assess_cases(project_id)
        memory = await self._assess_memory(
            target_url=target_url,
            objective=objective,
            project_scope=project_scope,
            context=context,
        )

        graph_sufficient = (
            graph["page_count"] >= GRAPH_SUFFICIENT_PAGES
            and graph["element_count"] >= GRAPH_SUFFICIENT_ELEMENTS
        )
        cases_sufficient = cases["active_case_count"] > 0
        memory_sufficient = bool(memory.get("sufficient"))

        sufficient = graph_sufficient or cases_sufficient or memory_sufficient
        if graph_sufficient:
            reason = "graph_coverage_sufficient"
        elif cases_sufficient:
            reason = "cases_available"
        elif memory_sufficient:
            reason = "knowledge_sufficient"
        else:
            reason = self._insufficient_reason(graph, cases, memory)

        return {
            "decision": "task_generation_ready" if sufficient else "need_recording",
            "reason": reason,
            "sufficient": sufficient,
            "sources": {
                "graph": graph,
                "cases": cases,
                "memory": memory,
            },
            # 兼容既有 knowledge_gate 消费方（prompt 投影 / 既有测试）
            "memory_hit_count": memory.get("hit_count", 0),
            "total_docs": memory.get("total_docs", 0),
            "max_score": memory.get("max_score", 0.0),
        }

    # ------------------------------------------------------------------ 图谱源

    def _assess_graph(self, project_id: str) -> dict[str, Any]:
        if self._memgraph_provider is None:
            return self._graph_result(status="unavailable", reason="memgraph_provider_not_configured")
        try:
            self._memgraph_provider.initialize()
            rows = self._memgraph_provider.execute(
                """
                MATCH (p:Page {project_id: $project_id})
                WITH count(p) AS page_count
                OPTIONAL MATCH (e:Element {project_id: $project_id})
                WITH page_count, count(e) AS element_count
                OPTIONAL MATCH (a:Action {project_id: $project_id})
                RETURN page_count, element_count, count(a) AS action_count
                """,
                {"project_id": project_id},
            )
            row = rows[0] if rows else {}
            return self._graph_result(
                status="ok",
                page_count=int(row.get("page_count") or 0),
                element_count=int(row.get("element_count") or 0),
                action_count=int(row.get("action_count") or 0),
            )
        except Exception as exc:
            logger.warning(
                "ui resource assessment graph source failed: project_id=%s error=%s",
                project_id,
                exc,
            )
            return self._graph_result(status="unavailable", reason=str(exc)[:200])

    @staticmethod
    def _graph_result(
        *,
        status: str,
        page_count: int = 0,
        element_count: int = 0,
        action_count: int = 0,
        reason: str = "",
    ) -> dict[str, Any]:
        return {
            "status": status,
            "page_count": page_count,
            "element_count": element_count,
            "action_count": action_count,
            "sufficient_threshold": {
                "pages": GRAPH_SUFFICIENT_PAGES,
                "elements": GRAPH_SUFFICIENT_ELEMENTS,
            },
            **({"reason": reason} if reason else {}),
        }

    # ------------------------------------------------------------------ 用例库源

    async def _assess_cases(self, project_id: str) -> dict[str, Any]:
        if self._test_case_service is None:
            return {
                "status": "unavailable",
                "active_case_count": 0,
                "reason": "test_case_service_not_configured",
            }
        try:
            page = await self._test_case_service.list_cases(
                project_id=project_id,
                status="active",
                mode_key=None,
                priority=None,
                query=None,
                limit=_CASE_PROBE_LIMIT,
                offset=0,
            )
            items = list(getattr(page, "items", []) or [])
            # limit=1 探测：有 has_more 说明不止一条，计数语义为"至少 N"
            at_least = len(items) + (1 if getattr(page, "has_more", False) else 0)
            return {"status": "ok", "active_case_count": max(at_least, len(items))}
        except Exception as exc:
            # 项目不存在 / 存储不可用：按 0 计，不阻塞（宁可漏过不可误杀）
            logger.warning(
                "ui resource assessment cases source failed: project_id=%s error=%s",
                project_id,
                exc,
            )
            return {"status": "unavailable", "active_case_count": 0, "reason": str(exc)[:200]}

    # ------------------------------------------------------------------ Memory 源

    async def _assess_memory(
        self,
        *,
        target_url: str,
        objective: str,
        project_scope: str,
        context: Any,
    ) -> dict[str, Any]:
        query = " ".join(
            part for part in (target_url, objective, project_scope) if part
        ).strip()
        if not query or self._memory_runtime_service is None:
            return {
                "status": "unavailable",
                "hit_count": 0,
                "total_docs": 0,
                "max_score": 0.0,
                "sufficient": False,
                "reason": "memory_service_unavailable" if self._memory_runtime_service is None else "empty_query",
            }
        try:
            memory_result = await self._memory_runtime_service.retrieve_for_turn(
                session_id=context.session_id,
                trace_id=context.trace_id,
                query=query,
                context=context.context_bundle,
            )
        except Exception as exc:
            logger.warning(
                "ui resource assessment memory source failed: session_id=%s error=%s",
                getattr(context, "session_id", ""),
                exc,
            )
            return {
                "status": "unavailable",
                "hit_count": 0,
                "total_docs": 0,
                "max_score": 0.0,
                "sufficient": False,
                "reason": str(exc)[:200],
            }

        scores = [float(item.score or 0.0) for item in memory_result.hits]
        max_score = max(scores, default=0.0)
        hit_count = len(memory_result.hits)
        total_docs = int(memory_result.total_docs or 0)
        # 既有阈值逻辑原样保留（方案 4.2：Memory 源"既有 hit_count/max_score 阈值逻辑"）
        sufficient = hit_count >= 3 or (hit_count >= 1 and total_docs >= 6) or max_score >= 0.78
        return {
            "status": "ok",
            "hit_count": hit_count,
            "total_docs": total_docs,
            "max_score": round(max_score, 4),
            "sufficient": sufficient,
        }

    @staticmethod
    def _insufficient_reason(
        graph: dict[str, Any],
        cases: dict[str, Any],
        memory: dict[str, Any],
    ) -> str:
        parts = [
            f"graph(page={graph.get('page_count', 0)},element={graph.get('element_count', 0)})",
            f"cases(active={cases.get('active_case_count', 0)})",
            f"memory(hits={memory.get('hit_count', 0)},max_score={memory.get('max_score', 0.0)})",
        ]
        return "insufficient:" + ";".join(parts)
