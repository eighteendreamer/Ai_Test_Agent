"""5-level cascading error recovery for D2 Agent Loop.

Recovery levels (in order of attempt):
  L1: Transient retry (network glitches, rate limits)
  L2: Output escalation / HTTP error -> resumable interrupt (migrated from RuntimeService)
  L3: Reactive compaction (prompt too long -> compact context and retry)
  L4: Context collapse (hard truncation when compaction unavailable)
  L5: Terminate (all recovery attempts exhausted)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from src.application.context.context_compaction_service import ContextCompactionService


logger = logging.getLogger(__name__)


class RecoveryLevel(IntEnum):
    TRANSIENT_RETRY = 1
    OUTPUT_ESCALATION = 2
    REACTIVE_COMPACTION = 3
    CONTEXT_COLLAPSE = 4
    TERMINATE = 5


@dataclass
class RecoveryResult:
    """Result of a recovery attempt."""

    action: str  # "retry", "terminate"
    level: RecoveryLevel
    state: dict[str, Any] | None = None
    error_message: str = ""
    retry_delay_seconds: float = 0.0


class ErrorRecoveryCascade:
    """Try each recovery level in order, returning the first successful action."""

    def __init__(
        self,
        context_compaction_service: ContextCompactionService | None = None,
        max_transient_retries: int = 2,
        compaction_threshold: float = 0.85,
        collapse_max_tail_messages: int = 8,
    ) -> None:
        self._context_compaction_service = context_compaction_service
        self._max_transient_retries = max_transient_retries
        self._compaction_threshold = compaction_threshold
        self._collapse_max_tail_messages = collapse_max_tail_messages
        self._transient_retry_counts: dict[str, int] = {}

    async def attempt_recovery(
        self,
        error: Exception,
        state: dict[str, Any],
    ) -> RecoveryResult:
        """Try each recovery level in order. Return first successful recovery."""
        error_str = str(error).lower()
        error_type = type(error).__name__
        session_id = str(state.get("session_id", "unknown"))

        # L1: Transient retry
        if self._is_transient_error(error_str, error_type):
            retry_count = self._transient_retry_counts.get(session_id, 0)
            if retry_count < self._max_transient_retries:
                self._transient_retry_counts[session_id] = retry_count + 1
                logger.info(
                    "L1 transient retry %d/%d for session %s: %s",
                    retry_count + 1,
                    self._max_transient_retries,
                    session_id,
                    error,
                )
                return RecoveryResult(
                    action="retry",
                    level=RecoveryLevel.TRANSIENT_RETRY,
                    state=state,
                    retry_delay_seconds=1.0 * (retry_count + 1),
                )

        # L2: Output escalation / HTTP error -> resumable interrupt
        if self._is_http_or_output_error(error_str, error_type, state):
            logger.info("L2 output escalation for session %s: %s", session_id, error)
            self._apply_interrupt_state(state, str(error))
            return RecoveryResult(
                action="terminate",
                level=RecoveryLevel.OUTPUT_ESCALATION,
                state=state,
                error_message=str(error),
            )

        # L3: Reactive compaction
        if self._is_prompt_too_long(error_str) and self._context_compaction_service is not None:
            logger.info("L3 reactive compaction for session %s", session_id)
            return RecoveryResult(
                action="retry",
                level=RecoveryLevel.REACTIVE_COMPACTION,
                state=state,
                error_message=str(error),
            )

        # L4: Context collapse (hard truncation)
        if self._is_prompt_too_long(error_str) or self._is_context_overflow(error_str):
            logger.info("L4 context collapse for session %s", session_id)
            self._collapse_context(state)
            return RecoveryResult(
                action="retry",
                level=RecoveryLevel.CONTEXT_COLLAPSE,
                state=state,
                error_message=str(error),
            )

        # L5: Terminate
        logger.warning("L5 terminate for session %s: %s", session_id, error)
        self._apply_interrupt_state(state, f"Unrecoverable error: {error}")
        return RecoveryResult(
            action="terminate",
            level=RecoveryLevel.TERMINATE,
            state=state,
            error_message=str(error),
        )

    def clear_retry_count(self, session_id: str) -> None:
        """Reset transient retry counter for a session (call on successful turn)."""
        self._transient_retry_counts.pop(session_id, None)

    def _is_transient_error(self, error_str: str, error_type: str) -> bool:
        transient_keywords = (
            "timeout", "timed out", "rate limit", "rate_limit",
            "429", "503", "connection reset", "connection refused",
            "temporarily unavailable", "service unavailable",
        )
        return any(kw in error_str for kw in transient_keywords)

    def _is_http_or_output_error(self, error_str: str, error_type: str, state: dict) -> bool:
        summary = dict(state.get("model_response_summary") or {})
        if str(summary.get("mode") or "") == "http_error":
            return True
        http_keywords = ("http error", "status code", "api error", "provider error")
        return any(kw in error_str for kw in http_keywords)

    def _is_prompt_too_long(self, error_str: str) -> bool:
        prompt_keywords = ("prompt too long", "context length", "token limit", "max tokens")
        return any(kw in error_str for kw in prompt_keywords)

    def _is_context_overflow(self, error_str: str) -> bool:
        overflow_keywords = ("context overflow", "context window", "token budget")
        return any(kw in error_str for kw in overflow_keywords)

    def _apply_interrupt_state(self, state: dict[str, Any], reason: str) -> None:
        """Convert state to interrupted/resumable (L2 behavior)."""
        state["continue_loop"] = False
        state["termination_reason"] = "interrupted"
        state["control_state"] = "interrupted"
        state["interrupt_requested"] = True
        state["interrupt_reason"] = reason

    def _collapse_context(self, state: dict[str, Any]) -> None:
        """Hard-truncate runtime_messages to keep only the most recent ones (L4)."""
        messages = list(state.get("runtime_messages") or [])
        max_tail = self._collapse_max_tail_messages
        if len(messages) > max_tail:
            state["runtime_messages"] = messages[-max_tail:]
