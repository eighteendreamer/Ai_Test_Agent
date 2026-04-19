from __future__ import annotations

from src.application.input_orchestrator_service import InputOrchestratorService
from src.domain.models import SessionRecord
from src.schemas.session import ExecutionRequest, SendMessageRequest


class PromptSubmissionService:
    def __init__(self, input_orchestrator: InputOrchestratorService | None = None) -> None:
        self._input_orchestrator = input_orchestrator or InputOrchestratorService()

    def prepare(self, session: SessionRecord, payload: SendMessageRequest) -> ExecutionRequest:
        return self._input_orchestrator.orchestrate(session, payload)
