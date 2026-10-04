from __future__ import annotations

import asyncio
import json

from src.application.documents.difference_engine import snapshot_reference
from src.runtime.limited_parser_process import LimitedParserProcess
from src.schemas.document_differences import DocumentDifferencePolicy, DocumentDifferenceResult
from src.schemas.document_parsing import DocumentParseSnapshotView


class DocumentDifferenceProcess:
    def __init__(self):
        self._process = LimitedParserProcess(worker_module="src.application.documents.difference_worker",
            temporary_prefix="qa-document-difference-")

    async def compare(self, *, old: DocumentParseSnapshotView, new: DocumentParseSnapshotView,
                      policy: DocumentDifferencePolicy | None = None) -> DocumentDifferenceResult:
        policy = policy or DocumentDifferencePolicy()
        def prepare():
            request = {"operation": "compare", "old": old.model_dump(mode="json"), "new": new.model_dump(mode="json"),
                "policy": policy.model_dump(mode="json")}
            if len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")) > policy.max_input_bytes:
                raise ValueError("比较输入超过大小限制")
            return request, snapshot_reference(old), snapshot_reference(new)
        request, left, right = await asyncio.to_thread(prepare)
        payload = await self._process.run(request, policy)
        result = await asyncio.to_thread(DocumentDifferenceResult.model_validate, payload)
        if result.old != left or result.new != right or result.policy != policy:
            self._process.fail("比较进程结果与固定输入不一致", 0, "", "")
        return result
