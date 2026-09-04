from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


class WorkerTimeout(Exception):
    def __init__(self, session_id: str, timeout: float) -> None:
        super().__init__(f"Worker {session_id} 超时 {timeout}s")
        self.session_id = session_id
        self.timeout = timeout


@dataclass
class WorkerResult:
    session_id: str
    status: Literal["completed", "failed", "cancelled", "timeout"]
    output: Any = None
    error: str | None = None


class ChildSessionWatcher:
    def __init__(self, child_session_id: str, default_timeout: float = 300.0) -> None:
        self._session_id = child_session_id
        self._settled = asyncio.Event()
        self._result: WorkerResult | None = None
        self._default_timeout = default_timeout

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_settled(self) -> bool:
        return self._settled.is_set()

    async def wait(self, timeout: float | None = None) -> WorkerResult:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        try:
            await asyncio.wait_for(self._settled.wait(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            return WorkerResult(
                session_id=self._session_id,
                status="timeout",
                error=f"Worker 超时 {effective_timeout}s",
            )
        assert self._result is not None
        return self._result

    def notify_settled(self, result: WorkerResult) -> None:
        self._result = result
        self._settled.set()

    def notify_completed(self, output: Any = None) -> None:
        self.notify_settled(WorkerResult(session_id=self._session_id, status="completed", output=output))

    def notify_failed(self, error: str) -> None:
        self.notify_settled(WorkerResult(session_id=self._session_id, status="failed", error=error))

    def notify_cancelled(self) -> None:
        self.notify_settled(WorkerResult(session_id=self._session_id, status="cancelled"))

    def reset(self) -> None:
        self._settled.clear()
        self._result = None


@dataclass(frozen=True)
class AgentMessage:
    from_agent: str
    to_agent: str
    kind: Literal["task", "result", "question", "notification"]
    payload: dict[str, Any]
    trace_id: str = ""
    message_id: str = field(default_factory=lambda: str(uuid4()))
    reply_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "kind": self.kind,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "reply_to": self.reply_to,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            kind=data["kind"],
            payload=data.get("payload", {}),
            trace_id=data.get("trace_id", ""),
            message_id=data.get("message_id", str(uuid4())),
            reply_to=data.get("reply_to"),
        )


class AgentMessageBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._history: list[AgentMessage] = []

    def register(self, agent_id: str) -> None:
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()

    def unregister(self, agent_id: str) -> None:
        self._queues.pop(agent_id, None)

    async def send(self, message: AgentMessage) -> None:
        self._history.append(message)
        queue = self._queues.get(message.to_agent)
        if queue is not None:
            await queue.put(message)

    async def receive(self, agent_id: str, timeout: float | None = None) -> AgentMessage | None:
        queue = self._queues.get(agent_id)
        if queue is None:
            return None
        if timeout is not None:
            try:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        return await queue.get()

    def get_history(
        self,
        agent_id: str | None = None,
        kind: Literal["task", "result", "question", "notification"] | None = None,
    ) -> list[AgentMessage]:
        results = self._history
        if agent_id:
            results = [m for m in results if m.from_agent == agent_id or m.to_agent == agent_id]
        if kind:
            results = [m for m in results if m.kind == kind]
        return results
