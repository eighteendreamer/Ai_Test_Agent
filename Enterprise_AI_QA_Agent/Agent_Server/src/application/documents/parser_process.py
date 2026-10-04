from __future__ import annotations

import base64
import hashlib

from src.runtime.limited_parser_process import LimitedParserProcess
from src.schemas.document_parsing import DocumentParseOptions, DocumentParsePolicy, DocumentParseResult


class DocumentParseProcess:
    def __init__(self):
        self._process = LimitedParserProcess(worker_module="src.application.documents.parser_worker",
            temporary_prefix="qa-document-parse-")

    async def parse(self, *, filename: str, content: bytes, policy: DocumentParsePolicy | None = None,
                    options: DocumentParseOptions | None = None) -> DocumentParseResult:
        policy = policy or DocumentParsePolicy()
        options = options or DocumentParseOptions()
        if not content or len(content) > policy.limits.max_bytes:
            self._process.fail("文档为空或超过文件大小限制", None, "", "")
        payload = await self._process.run({"filename": filename, "content_base64": base64.b64encode(content).decode(),
            "policy": policy.model_dump(mode="json"), "options": options.model_dump(mode="json")}, policy)
        result = DocumentParseResult.model_validate(payload)
        if result.source_sha256 != hashlib.sha256(content).hexdigest() or result.source_size_bytes != len(content):
            self._process.fail("解析进程结果与原件不一致", 0, "", "")
        return result
