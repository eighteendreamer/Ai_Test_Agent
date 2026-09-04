"""D3 Unified Router: single pipeline replacing 6 overlapping routers.

The UnifiedRouter chains the existing routing components in a deterministic order:
  1. IntentRecognitionService (keyword fast path)
  2. SemanticIntentService (LLM slow path, only when low confidence)
  3. ModeSelectionPolicy (mode activation decision)
  4. AgentRegistry.resolve_for_message() (agent selection)
  5. QATaskDirectionService + QATaskRouterService (QA-specific routing, parallel path)

The RoutingDecision aggregates all outputs into a single structured result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.application.intent.intent_recognition_service import IntentDecision, IntentRecognitionService
from src.application.intent.mode_selection_policy import ModeSelectionDecision, ModeSelectionPolicy
from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.application.testing.direction_service import QATaskDirectionService, QATaskState
from src.application.testing.router_service import QATaskRouterService
from src.registry.agents import AgentRegistry
from src.schemas.agent import AgentDescriptor
from src.schemas.intent import SafetyAssessment


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingDecision:
    """Unified routing result from the single pipeline."""

    intent: IntentDecision
    mode_decision: ModeSelectionDecision
    agent: AgentDescriptor
    qa_task_state: QATaskState | None = None
    qa_routing: dict[str, str] | None = None
    safety: SafetyAssessment | None = None

    @property
    def agent_key(self) -> str:
        return self.agent.key

    @property
    def mode_key(self) -> str:
        return self.mode_decision.mode_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_key": self.agent_key,
            "agent_name": self.agent.name,
            "mode_key": self.mode_key,
            "mode_source": self.mode_decision.source,
            "intent_confidence": self.intent.confidence,
            "intent_mode": self.intent.candidate_mode_key,
            "intent_target_kind": self.intent.target_kind,
        }


class UnifiedRouter:
    """Single routing pipeline that replaces 6 overlapping routers.

    Usage:
        decision = await unified_router.route(message, session_context)
    """

    def __init__(
        self,
        intent_recognition: IntentRecognitionService,
        semantic_intent: SemanticIntentService,
        mode_policy: ModeSelectionPolicy,
        agent_registry: AgentRegistry,
        qa_direction: QATaskDirectionService | None = None,
        qa_router: QATaskRouterService | None = None,
        safety_policy: ExecutionSafetyPolicy | None = None,
    ) -> None:
        self._intent_recognition = intent_recognition
        self._semantic_intent = semantic_intent
        self._mode_policy = mode_policy
        self._agent_registry = agent_registry
        self._qa_direction = qa_direction
        self._qa_router = qa_router
        self._safety_policy = safety_policy

    async def route(
        self,
        message: str,
        *,
        payload_mode_key: str | None = None,
        session_mode_key: str | None = None,
        session_agent_key: str | None = None,
        history: list[dict[str, Any]] | None = None,
        model_key: str | None = None,
    ) -> RoutingDecision:
        """Execute the unified routing pipeline.

        Steps:
          1. Deterministic intent classification (keyword fast path)
          2. Semantic intent enrichment (LLM slow path, if needed)
          3. Mode selection (activation policy)
          4. Agent selection (capability matching)
          5. QA task routing (parallel path, if applicable)
          6. Safety evaluation
        """
        # Step 1: Deterministic intent
        intent = self._intent_recognition.recognize(message)

        # Step 2: Semantic enrichment (only when low confidence)
        intent = await self._semantic_intent.enrich(
            message=message,
            baseline=intent,
            model_key=model_key,
        )

        # Step 3: Safety assessment
        safety = None
        if self._safety_policy is not None:
            safety = self._safety_policy.evaluate(
                user_message=message,
                mode_key=intent.candidate_mode_key or "",
                agent_key=session_agent_key or "",
                tool_keys=[],
                context_bundle={},
            )

        # Step 4: Mode selection
        mode_decision = self._mode_policy.resolve(
            payload_mode_key=payload_mode_key,
            session_mode_key=session_mode_key,
            intent=intent,
            safety=safety,
        )

        # Step 5: Agent selection
        agent = self._agent_registry.resolve_for_message(
            message,
            explicit_key=session_agent_key,
        )

        # Step 6: QA task routing (parallel path)
        qa_task_state = None
        qa_routing = None
        if self._qa_direction is not None:
            qa_task_state = self._qa_direction.classify(message)
            if self._qa_router is not None and qa_task_state.is_test_task:
                qa_routing = self._qa_router.route(qa_task_state)

        return RoutingDecision(
            intent=intent,
            mode_decision=mode_decision,
            agent=agent,
            qa_task_state=qa_task_state,
            qa_routing=qa_routing,
            safety=safety,
        )
