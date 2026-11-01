"""Load current model/release material before the PG admission transaction."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from src.registry.agents import AgentRegistry
from src.registry.models import ModelRegistry
from src.runtime.framework_submission_preparation import PreparedFrameworkSubmission, prepare_release_submission
from src.schemas.framework_release import FrameworkReleaseReference


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkReleaseSubmissionLoader:
    """Explicit composition, not a production enable switch or model executor.

    Reuse the router's registry selection, then capture the concrete current
    configuration through the existing runtime reader. No startup cache or
    cross-database transaction is implied. Limits/namespace are required from
    trusted composition; the existing preparer validates the resulting policy.
    """
    release_store: Path
    release_reference: FrameworkReleaseReference
    agent_registry: AgentRegistry
    model_registry: ModelRegistry
    mode_keys: frozenset[str]
    checkpoint_namespace: str
    max_calls: int
    max_tokens: int
    deadline_seconds: float

    async def __call__(self, session, request, command) -> PreparedFrameworkSubmission | None:
        if request.mode_key not in self.mode_keys:
            return None
        return await asyncio.to_thread(self._load, session, request, command)

    def _load(self, session, request, command):
        agent = self.agent_registry.resolve_for_message(message=request.user_message,
            explicit_key=request.agent_key or None)
        requested_key = str(request.model_key or session.preferred_model or "").strip()
        model = self.model_registry.resolve_for_agent(
            requested_key=agent.default_model if requested_key in {"", "auto"} else requested_key,
            supported_model_keys=agent.supported_models)
        configuration = self.model_registry.get_runtime_config(model.key)
        prepared = prepare_release_submission(self.release_store, self.release_reference,
            binding_id=str(uuid5(NAMESPACE_URL, "framework-submission:" + command.task_id)),
            session_id=session.id, turn_id=command.turn_id, checkpoint_namespace=self.checkpoint_namespace,
            root_model_key=model.key, model_configurations={model.key: configuration},
            max_calls=self.max_calls, max_tokens=self.max_tokens, created_at=command.created_at,
            deadline_at=command.created_at + timedelta(seconds=self.deadline_seconds))
        logger.info("framework_submission_materials_loaded", extra={"session_id": session.id,
            "task_id": command.task_id, "binding_id": prepared.binding.binding_id})
        return prepared
