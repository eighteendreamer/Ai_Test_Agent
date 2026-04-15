from __future__ import annotations

from uuid import uuid4

from src.domain.models import SessionRecord
from src.schemas.session import ExecutionRequest, SendMessageRequest


class PromptSubmissionService:
    def prepare(self, session: SessionRecord, payload: SendMessageRequest) -> ExecutionRequest:
        content = payload.content.strip()
        if not content:
            raise ValueError("Message content cannot be empty.")

        normalized_input = " ".join(content.split())
        skill_keys = list(dict.fromkeys(payload.skill_keys))

        return ExecutionRequest(
            turn_id=str(uuid4()),
            session_id=session.id,
            user_message=content,
            normalized_input=normalized_input,
            agent_key=payload.agent_key or session.selected_agent,
            model_key=payload.model_key or session.preferred_model,
            skill_keys=skill_keys,
            context=payload.context,
        )
