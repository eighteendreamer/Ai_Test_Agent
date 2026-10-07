"""Capture once; read exact text assets or parser snapshots without latest reads."""
import hashlib
import logging
from pathlib import Path
from uuid import UUID

from src.schemas.document_content import DocumentContentBasis
from src.schemas.document_parsing import DocumentParseSnapshot
from src.schemas.case_management import TestCaseSourceRef

logger = logging.getLogger(__name__)


class FixedDocumentContentService:
    def __init__(self, documents, parsing):
        self._docs, self._parsing = documents, parsing

    async def capture(self, project_id, source):
        source = TestCaseSourceRef.model_validate(source)
        if source.source_type != "api_doc":
            raise ValueError("该来源不是可读取的文档引用")
        try:
            UUID(source.source_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("关联来源缺少正式文档身份，不能按名称读取") from None
        document = await self._docs.get_document(source.source_id)
        if document.project_id != project_id:
            raise ValueError("关联文档不属于当前项目")
        metadata = source.metadata
        parsed = DocumentParseSnapshot.model_validate(metadata["parse_snapshot"]) if metadata.get("parse_snapshot") else None
        version_id = source.version or (parsed.seed.version_id if parsed else document.current_version_id)
        if not version_id:
            raise ValueError("关联文档没有可固定的版本")
        try:
            UUID(version_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("关联来源缺少正式文档版本身份，不能改读当前版本替代") from None
        version = await self._docs.get_version(document.id, version_id)
        warnings = ["original_context_truncated"] if metadata.get("truncated") else []
        role = "authored_snapshot" if parsed or metadata.get("content_asset_id") else "version_linked_source" if source.version else "current_linked_source"
        if role == "current_linked_source":
            warnings.append("authored_version_not_recorded")
            # No upstream counterpart (checked: generation source_refs, exact
            # asset/snapshot readers and old current query): only an unversioned
            # link may capture the current parser, before a durable read.
            if self._parsing is not None:
                page = await self._parsing.list_snapshots(project_id, document.id, version_id=version.id, limit=1)
                parsed = page.items[0] if page.items else None
        if parsed:
            if self._parsing is None:
                raise ValueError("固定解析快照读取尚未配置")
            actual = await self._parsing.get_snapshot(project_id, document.id, parsed.id)
            if actual != parsed or (parsed.seed.project_id, parsed.seed.document_id, parsed.seed.version_id) != (project_id, document.id, version.id):
                raise ValueError("关联解析快照与固定文档版本不一致")
            asset = parsed.seed.original_asset
        else:
            if Path(version.snapshot.filename).suffix.lower() in {".pdf", ".docx"}:
                raise ValueError("历史二进制来源缺少明确解析快照，不能改读最新解析")
            assets = [*version.snapshot.derived_assets, *([version.snapshot.original_asset] if version.snapshot.original_asset else [])]
            asset_id = metadata.get("content_asset_id")
            asset = next((item for item in assets if item.id == asset_id), None) if asset_id else next((item for item in assets if item.storage_uri == version.snapshot.storage_uri), None)
            if asset is None:
                raise ValueError("关联来源缺少明确文本资产")
        if asset.version_id != version.id or (metadata.get("content_asset_id") and asset.id != metadata["content_asset_id"]):
            raise ValueError("关联文本资产与固定文档版本不一致")
        if role == "authored_snapshot" and not source.version and parsed is None:
            raise ValueError("历史文本资产引用缺少文档版本")
        basis = DocumentContentBasis(project_id=project_id, version=version, asset=asset, parse_snapshot=parsed,
            expected_content_sha256=metadata.get("content_sha256"), role=role, warnings=warnings)
        logger.info("document_content_basis_captured", extra={"project_id": project_id,
            "document_id": document.id, "version_id": version.id, "asset_id": asset.id,
            "snapshot_id": parsed.id if parsed else None, "basis_role": role})
        return basis

    async def read(self, basis):
        basis = DocumentContentBasis.model_validate(basis)
        fixed = basis.version
        document = await self._docs.get_document(fixed.document_id)
        if document.project_id != basis.project_id or fixed.snapshot.project_id != basis.project_id:
            raise ValueError("固定来源不属于当前项目")
        if await self._docs.get_version(fixed.document_id, fixed.id) != fixed or basis.asset.version_id != fixed.id:
            raise ValueError("固定来源版本或资产已变化")
        structure = None
        warnings = list(basis.warnings)
        if basis.parse_snapshot:
            if self._parsing is None:
                raise ValueError("固定解析快照读取尚未配置")
            snapshot, result = await self._parsing.read_snapshot(basis.project_id, fixed.document_id, basis.parse_snapshot.id)
            if snapshot != basis.parse_snapshot or snapshot.seed.original_asset != basis.asset or (
                snapshot.seed.project_id, snapshot.seed.document_id, snapshot.seed.version_id) != (basis.project_id, fixed.document_id, fixed.id):
                raise ValueError("固定解析结果与来源身份不一致")
            if result.status == "unsupported":
                raise ValueError("固定解析快照没有可用文本")
            text = result.text
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            structure = {"sections": [item.model_dump(mode="json") for item in result.sections],
                "tables": [item.model_dump(mode="json") for item in result.tables],
                "blocks": [item.model_dump(mode="json", exclude={"text"}) for item in result.blocks]}
            warnings.extend(item.message for item in result.warnings)
            if result.status == "partial":warnings.append("source_parse_partial")
        else:
            asset, text = await self._docs.read_text_asset(fixed.document_id, basis.asset.id)
            if asset != basis.asset:
                raise ValueError("固定文本资产记录已变化")
            content_hash = asset.sha256
        if basis.expected_content_sha256 and content_hash != basis.expected_content_sha256:
            raise ValueError("关联来源正文哈希与当时记录不一致")
        return {"document": fixed.snapshot.model_dump(mode="json"), "content": text,
            "content_asset": basis.asset.model_dump(mode="json"), "content_sha256": content_hash,
            "parse_snapshot": basis.parse_snapshot.model_dump(mode="json") if basis.parse_snapshot else None,
            "parse_structure": structure, "warnings": warnings, "basis_role": basis.role}
