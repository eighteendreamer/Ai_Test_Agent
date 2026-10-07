"""Project-scoped reads for planning; never write or execute business actions."""
from __future__ import annotations

import hashlib
import json
import logging

from src.schemas.case_planning import PlanningEvidence, PlanningQuery
from src.schemas.api_docs import ApiDocRecord

FULL_SOURCE_IDENTITY_PROTOCOL = "case-planning-source-full-hash-v1"
logger = logging.getLogger(__name__)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ProjectPlanningSources:
    def __init__(self, *, case_service, api_docs_service, recording_store=None, recording_graph_store=None):
        self._cases = case_service
        self._docs = api_docs_service
        self._recordings = recording_store
        self._graphs = recording_graph_store

    def set_recording_sources(self, *, recording_store, recording_graph_store):
        self._recordings = recording_store
        self._graphs = recording_graph_store

    async def case(self, project_id, case_id, *, version_id=None) -> PlanningEvidence:
        case = await self._cases.get_case(case_id)
        if case.project_id != project_id or case.lifecycle_status != "active" or not case.active_version_id:
            raise ValueError("用例不属于当前项目或没有启用版本")
        version = await self._cases.get_version(version_id or case.active_version_id)
        if version.case_id != case.id:
            raise ValueError("用例版本引用不一致")
        return PlanningEvidence(id=f"case:{version.id}", kind="case", source_id=case.id, version=version.id,
            content_hash=version.content_hash, payload={
                "case_id": case.id, "case_version_id": version.id, "case_key": case.case_key,
                "title": case.title, "historical_mode_key": case.mode_key, "content": version.business_content(),
                "execution_notes": version.execution_notes, "source_refs": [ref.model_dump(mode="json") for ref in version.source_refs],
            })

    async def case_search(self, project_id, query, limit, offset=0):
        page = await self._cases.list_cases(project_id=project_id, status="active", mode_key=None,
            priority=None, query=query or None, limit=limit, offset=offset)
        return [await self.case(project_id, case.id) for case in page.items], page.has_more

    async def catalog(self, project_id, limit, offset=0):
        cases = await self._cases.list_cases(project_id=project_id, status="active", mode_key=None,
            priority=None, query=None, limit=limit, offset=offset)
        docs = await self._docs.list_documents(project_id=project_id)
        recordings = await self._recordings.list_sessions(project_id=project_id, limit=limit + 1, offset=offset) if self._recordings else []
        return {"cases": [{"id": case.id, "case_key": case.case_key, "title": case.title} for case in cases.items],
                "cases_has_more": cases.has_more,
                "documents": [{"id": doc.id, "title": doc.title, "filename": doc.filename} for doc in docs[offset:offset + limit]],
                "documents_has_more": len(docs) > offset + limit,
                "recordings": [{"id": item.id, "name": item.name, "status": item.status.value} for item in recordings[:limit]],
                "recordings_has_more": len(recordings) > limit, "offset": offset,
                "recording_knowledge_available": self._recordings is not None and self._graphs is not None}

    async def query(self, project_id, request: PlanningQuery, limit, *, identity_protocol=None):
        if identity_protocol not in {None, FULL_SOURCE_IDENTITY_PROTOCOL}:
            raise ValueError("项目来源查询不支持该固定身份协议")
        if request.kind == "cases":
            if request.source_id:
                return [await self.case(project_id, request.source_id)], {"has_more": False}
            evidence, more = await self.case_search(project_id, request.query, limit, request.offset)
            return evidence, {"has_more": more, "offset": request.offset}
        if request.kind in {"documents", "recordings"}:
            catalog = await self.catalog(project_id, limit, request.offset)
            return [], {request.kind: catalog[request.kind], "has_more": catalog[request.kind + "_has_more"], "offset": request.offset}
        if not request.source_id:
            raise ValueError("读取文档或录制需要明确来源身份")
        if request.kind == "document":
            doc = await self._docs.get_document(request.source_id)
            if doc.project_id != project_id:
                raise ValueError("文档不属于当前项目")
            # Existing search_documents accepts legacy names, not project_id;
            # use the project catalog and guarded direct read instead.
            result = await self._docs.read_document_content(doc.id)
            if result.get("read_error") or result.get("truncated"):
                raise ValueError("文档原件无法完整读取，不能使用旧预览作为规划依据")
            doc = ApiDocRecord.model_validate(result.get("document"))
            if doc.project_id != project_id:
                raise ValueError("文档不属于当前项目")
            lines = str(result["content"]).splitlines()
            end = min(len(lines), request.start_line - 1 + request.max_lines)
            text = "\n".join(lines[request.start_line - 1:end])
            if not text:
                raise ValueError("文档指定范围没有内容")
            payload = {"title": doc.title, "content": text, "start_line": request.start_line, "end_line": end,
                       "has_more": end < len(lines), "source_uri": doc.storage_uri,
                       "content_asset_id": (result.get("content_asset") or {}).get("id"),
                       "content_sha256": result.get("content_sha256"), "catalog_revision": doc.catalog_revision}
            if result.get("parse_snapshot"):
                payload["parse_snapshot"] = result["parse_snapshot"]
                payload["parse_structure"] = result.get("parse_structure")
            version = doc.current_version_id or doc.updated_at.isoformat()
        else:
            if self._recordings is None or self._graphs is None:
                raise ValueError("录制知识读取尚未配置")
            recording = await self._recordings.get_session(request.source_id)
            if recording is None or recording.project_id != project_id or recording.status.value != "completed":
                raise ValueError("录制不属于当前项目或尚未完成固化")
            graph = await self._graphs.get_recording_subgraph(recording.id, project_id=project_id)
            if graph.get("status") != "success":
                raise ValueError("录制知识投影不可用")
            payload = {"name": recording.name, "graph": graph, "finalize_metrics": recording.finalize_metrics}
            version = recording.updated_at.isoformat()
        digest = hashlib.sha256(json_text(payload).encode()).hexdigest()
        identity_hash = digest if identity_protocol == FULL_SOURCE_IDENTITY_PROTOCOL else digest[:16]
        if identity_protocol is not None:
            logger.info("case_planning_source_identity_created", extra={"project_id": project_id,
                "source_kind": request.kind, "source_id": request.source_id, "source_version": version,
                "content_hash": digest, "identity_protocol": identity_protocol})
        evidence = PlanningEvidence(id=f"{request.kind}:{request.source_id}:{identity_hash}", kind=request.kind,
            source_id=request.source_id, version=version, content_hash=digest, payload=payload)
        return [evidence], {"has_more": payload.get("has_more", False)}
