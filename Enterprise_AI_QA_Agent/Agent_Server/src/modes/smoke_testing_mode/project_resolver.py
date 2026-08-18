from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.application.documents.api_docs_service import ApiDocsService
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService


@dataclass(frozen=True)
class SmokeProjectMatch:
    project_scope: str
    source: str
    score: float
    reason: str
    metadata: dict[str, Any]


class SmokeProjectResolver:
    def __init__(
        self,
        *,
        api_docs_service: ApiDocsService | None = None,
        knowledge_graph_service: KnowledgeGraphService | None = None,
        project_service=None,
    ) -> None:
        self._api_docs_service = api_docs_service
        self._knowledge_graph_service = knowledge_graph_service
        self._project_service = project_service

    async def resolve(self, *, target_url: str, explicit_project_scope: str = "") -> tuple[str, list[SmokeProjectMatch], list[str]]:
        warnings: list[str] = []
        explicit = explicit_project_scope.strip()
        if explicit:
            if self._project_service is not None:
                try:
                    project = await self._project_service.require_active(explicit)
                    return str(project.graph_scope_key or project.name or explicit), [
                        SmokeProjectMatch(
                            project_scope=str(project.graph_scope_key or project.name or explicit),
                            source="explicit",
                            score=100.0,
                            reason="用户显式指定 project_id。",
                            metadata={"project_id": str(project.id)},
                        )
                    ], warnings
                except (KeyError, ValueError):
                    page = await self._project_service.list(
                        status="active",
                        query=None,
                        limit=200,
                        offset=0,
                    )
                    normalized = explicit.casefold()
                    project = next(
                        (
                            item
                            for item in page.items
                            if normalized
                            in {
                                str(item.id or "").casefold(),
                                str(item.project_key or "").casefold(),
                                str(item.graph_scope_key or "").casefold(),
                                str(item.name or "").casefold(),
                            }
                        ),
                        None,
                    )
                    if project is not None:
                        return str(project.graph_scope_key or project.name or explicit), [
                            SmokeProjectMatch(
                                project_scope=str(project.graph_scope_key or project.name or explicit),
                                source="explicit_legacy_alias",
                                score=100.0,
                                reason="旧 project_scope 已解析为正式 project_id。",
                                metadata={"project_id": str(project.id)},
                            )
                        ], warnings
            return explicit, [
                SmokeProjectMatch(
                    project_scope=explicit,
                    source="explicit",
                    score=100.0,
                    reason="用户显式指定 project_scope。",
                    metadata={},
                )
            ], warnings

        matches: list[SmokeProjectMatch] = []
        target_host = _host(target_url)
        target_path = _path(target_url)

        if self._api_docs_service is not None:
            try:
                for doc in await self._api_docs_service.list_documents():
                    project_url = str(doc.project_url or "")
                    if not project_url:
                        continue
                    score = _url_score(target_host, target_path, project_url)
                    if score <= 0:
                        continue
                    matches.append(
                        SmokeProjectMatch(
                            project_scope=str(doc.project_name or doc.title or _slug(project_url)),
                            source="api_doc",
                            score=score,
                            reason=f"API 文档项目地址匹配 {project_url}",
                            metadata={
                                "doc_id": doc.id,
                                "project_id": str(doc.project_id or ""),
                                "project_url": project_url,
                                "title": doc.title,
                            },
                        )
                    )
            except Exception as exc:
                warnings.append(f"API 文档项目匹配失败：{exc}")

        if self._knowledge_graph_service is not None:
            try:
                for project in await self._knowledge_graph_service.list_projects():
                    project_id = str(project.project_id or "").strip()
                    graph = await self._knowledge_graph_service.get_graph(project_id or project.project_scope)
                    best_url = ""
                    best_score = 0.0
                    for node in graph.nodes:
                        candidate_url = str(node.summary or node.metadata.get("url") or "")
                        score = _url_score(target_host, target_path, candidate_url)
                        if score > best_score:
                            best_score = score
                            best_url = candidate_url
                    if best_score > 0:
                        matches.append(
                            SmokeProjectMatch(
                                project_scope=project.project_scope,
                                source="ui_graph",
                                score=best_score,
                                reason=f"UI 图谱页面 URL 匹配 {best_url}",
                                metadata={"page_url": best_url, "project_id": project_id},
                            )
                        )
            except Exception as exc:
                warnings.append(f"UI 图谱项目匹配失败：{exc}")

        matches.sort(key=lambda item: item.score, reverse=True)
        if matches and (len(matches) == 1 or matches[0].score - matches[1].score >= 15):
            return matches[0].project_scope, matches, warnings
        if target_host:
            return _slug(target_host), matches, warnings
        return "smoke-default", matches, warnings


def _url_score(target_host: str, target_path: str, candidate_url: str) -> float:
    candidate_host = _host(candidate_url)
    if not target_host or not candidate_host or target_host != candidate_host:
        return 0.0
    score = 70.0
    candidate_path = _path(candidate_url)
    if candidate_path and target_path.startswith(candidate_path):
        score += min(25.0, len(candidate_path) / max(len(target_path), 1) * 25.0)
    elif target_path and candidate_path.startswith(target_path):
        score += 12.0
    return score


def _host(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.netloc:
        return parsed.netloc.lower()
    return ""


def _path(value: str) -> str:
    parsed = urlparse(str(value or ""))
    return (parsed.path or "/").rstrip("/") or "/"


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "project"

