from __future__ import annotations

from collections import defaultdict


class RuntimeControlRegistry:
    def __init__(self) -> None:
        self._interrupt_reasons: dict[str, str] = {}
        self._replay_requests: dict[str, int] = defaultdict(int)

    def request_interrupt(self, session_id: str, reason: str = "") -> None:
        self._interrupt_reasons[session_id] = reason.strip()

    def clear_interrupt(self, session_id: str) -> None:
        self._interrupt_reasons.pop(session_id, None)

    def get_interrupt_reason(self, session_id: str) -> str:
        return self._interrupt_reasons.get(session_id, "")

    def is_interrupt_requested(self, session_id: str) -> bool:
        return session_id in self._interrupt_reasons

    def mark_replay_requested(self, session_id: str) -> int:
        self._replay_requests[session_id] += 1
        return self._replay_requests[session_id]
