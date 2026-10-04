from __future__ import annotations

import base64

from src.runtime.limited_parser_process import LimitedParserProcess, ParserProcessError
from src.schemas.case_import import CaseImportOptions, CaseImportPolicy, CaseImportPreview, CaseImportSheet

ImportProcessError = ParserProcessError


class CaseImportProcess:
    def __init__(self):
        self._process = LimitedParserProcess(worker_module="src.application.test_cases.import_worker",
            temporary_prefix="qa-case-import-")

    async def inspect(self, *, filename: str, content: bytes, policy: CaseImportPolicy, header_row: int = 1) -> list[CaseImportSheet]:
        result = await self._run({"operation": "inspect", "header_row": header_row}, filename, content, policy)
        return [CaseImportSheet.model_validate(sheet) for sheet in result]

    async def parse(self, *, filename: str, content: bytes, policy: CaseImportPolicy, options: CaseImportOptions) -> CaseImportPreview:
        result = await self._run({"operation": "parse", "options": options.model_dump(mode="json")}, filename, content, policy)
        return CaseImportPreview.model_validate(result)

    async def _run(self, request: dict, filename: str, content: bytes, policy: CaseImportPolicy):
        if not content or len(content) > policy.limits.max_bytes:
            self._process.fail("导入文件为空或超过文件大小限制", None, "", "")
        request = {**request, "filename": filename, "content_base64": base64.b64encode(content).decode(), "policy": policy.model_dump(mode="json")}
        return await self._process.run(request, policy)
