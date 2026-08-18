from __future__ import annotations

from typing import Any

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.documents.api_docs_service import ApiDocsService
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.modes.smoke_testing_mode.contracts import SmokeSource


class ProjectCaseProvider:
    """Future adapter for project-management test case platforms."""

    async def list_cases(self, *, project_scope: str, target_url: str) -> list[dict[str, Any]]:
        return []


class SmokeSourceResolver:
    def __init__(
        self,
        *,
        api_docs_service: ApiDocsService | None = None,
        knowledge_graph_service: KnowledgeGraphService | None = None,
        memory_runtime_service: MemoryRuntimeService | None = None,
        project_case_provider: ProjectCaseProvider | None = None,
    ) -> None:
        self._api_docs_service = api_docs_service
        self._knowledge_graph_service = knowledge_graph_service
        self._memory_runtime_service = memory_runtime_service
        self._project_case_provider = project_case_provider

    async def resolve(
        self,
        *,
        project_scope: str,
        project_id: str | None = None,
        target_url: str,
        session_id: str = "",
        trace_id: str = "",
        api_doc_ids: list[str],
        attachments: list[dict[str, Any]],
        max_api_docs: int = 3,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        sources: list[SmokeSource] = []
        api_documents: list[dict[str, Any]] = []
        ui_graph: dict[str, Any] | None = None
        credential_summary = ""
        project_cases: list[dict[str, Any]] = []

        if self._project_case_provider is not None:
            try:
                project_cases = await self._project_case_provider.list_cases(
                    project_scope=project_scope,
                    target_url=target_url,
                )
                if project_cases:
                    sources.append(
                        SmokeSource(
                            source_type="project_case",
                            source_id=project_scope,
                            title=f"{project_scope} 项目管理平台用例",
                            confidence=0.92,
                            metadata={"case_count": len(project_cases)},
                        )
                    )
            except Exception as exc:
                warnings.append(f"项目管理平台测试用例读取失败：{exc}")

        for item in attachments:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            sources.append(
                SmokeSource(
                    source_type="attachment",
                    source_id=str(metadata.get("attachment_id") or item.get("id") or ""),
                    title=str(item.get("name") or ""),
                    uri=str(item.get("uri") or ""),
                    confidence=0.72,
                    metadata={"content_type": item.get("content_type"), "text_excerpt": item.get("text_excerpt")},
                )
            )

        if self._api_docs_service is not None:
            try:
                docs = await self._api_docs_service.list_documents(project_id=project_id) if project_id else await self._api_docs_service.list_documents()
                selected = []
                requested = set(api_doc_ids)
                for doc in docs:
                    if requested and doc.id not in requested:
                        continue
                    if not requested and project_scope and project_scope.lower() not in f"{doc.project_name or ''} {doc.title}".lower():
                        if target_url and str(doc.project_url or "") and _host(target_url) != _host(str(doc.project_url or "")):
                            continue
                    selected.append(doc)
                    if len(selected) >= max_api_docs:
                        break
                for doc in selected:
                    content = await self._api_docs_service.read_document_content(doc.id, max_chars=16000)
                    api_documents.append({"record": doc.model_dump(mode="json"), "content": content.get("content") or ""})
                    sources.append(
                        SmokeSource(
                            source_type="api_doc",
                            source_id=doc.id,
                            title=doc.title,
                            uri=doc.storage_uri,
                            confidence=0.86,
                            metadata={"project_name": doc.project_name, "project_url": doc.project_url},
                        )
                    )
            except Exception as exc:
                warnings.append(f"API 文档读取失败：{exc}")

        if self._knowledge_graph_service is not None and (project_id or project_scope):
            try:
                graph = await self._knowledge_graph_service.get_graph(project_id or project_scope)
                ui_graph = graph.model_dump(mode="json")
                sources.append(
                    SmokeSource(
                        source_type="ui_graph",
                        source_id=project_id or project_scope,
                        title=f"{project_scope} UI 图谱",
                        confidence=0.78,
                        metadata={"page_count": graph.summary.page_count, "element_count": graph.summary.element_count},
                    )
                )
            except Exception as exc:
                warnings.append(f"UI 图谱读取失败：{exc}")

        try:
            credential_summary = await self._credential_summary(
                project_scope=project_scope,
                target_url=target_url,
                session_id=session_id,
                trace_id=trace_id,
            )
        except Exception as exc:
            warnings.append(f"测试凭据记忆查询失败：{exc}")

        return {
            "sources": [item.model_dump(mode="json") for item in sources],
            "api_documents": api_documents,
            "ui_graph": ui_graph,
            "project_cases": project_cases,
            "credential_summary": credential_summary,
            "warnings": warnings,
        }

    async def _credential_summary(self, *, project_scope: str, target_url: str, session_id: str, trace_id: str) -> str:
        if self._memory_runtime_service is None:
            return ""
        query = f"测试账号 凭据 {project_scope} {target_url}".strip()
        if not query:
            return ""
        result = await self._memory_runtime_service.retrieve_for_turn(
            session_id=session_id,
            trace_id=trace_id,
            query=query,
            context={"mode_key": "smoke_testing"},
        )
        memories = result.hits
        if not memories:
            return ""
        first = memories[0]
        content = str(getattr(first, "content", "") or getattr(first, "summary", "") or "")
        username = _extract_username(content)
        return f"已找到项目测试凭据：{username or '账号'} / ********"


def _host(value: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(str(value or ""))
    return parsed.netloc.lower()


def _extract_username(content: str) -> str:
    import re

    for pattern in (r"username[:：=]\s*([^\s,，;；]+)", r"账号[:：=]\s*([^\s,，;；]+)", r"user[:：=]\s*([^\s,，;；]+)"):
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""
