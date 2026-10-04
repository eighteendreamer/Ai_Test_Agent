"""Fixed-entry Git reads using the existing bounded process lifecycle."""
from src.runtime.limited_parser_process import LimitedParserProcess
from src.schemas.document_git import GitDocumentPolicy


class GitDocumentProcess:
    def __init__(self):
        self._process = LimitedParserProcess(worker_module="src.runtime.document_git_worker", temporary_prefix="qa-document-git-")

    async def run(self, request: dict, policy: GitDocumentPolicy):
        return await self._process.run({**request, "policy": policy.model_dump(mode="json")}, policy)
