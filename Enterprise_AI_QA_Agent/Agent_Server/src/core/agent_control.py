from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


class AgentDepthExceeded(Exception):
    def __init__(self, depth: int, max_depth: int) -> None:
        super().__init__(f"Agent 深度 {depth} 超过上限 {max_depth}")
        self.depth = depth
        self.max_depth = max_depth


class AgentConcurrencyLimitExceeded(Exception):
    def __init__(self, active: int, max_concurrent: int) -> None:
        super().__init__(f"活跃 Agent {active} 达到上限 {max_concurrent}")
        self.active = active
        self.max_concurrent = max_concurrent


class AgentLifetimeExceeded(Exception):
    def __init__(self, elapsed: float, max_lifetime: float) -> None:
        super().__init__(f"Agent 运行 {elapsed:.1f}s 超过上限 {max_lifetime:.1f}s")
        self.elapsed = elapsed
        self.max_lifetime = max_lifetime


class AgentTurnBudgetExceeded(Exception):
    def __init__(self, created: int, max_per_turn: int) -> None:
        super().__init__(f"单 turn 创建 {created} 个 Agent 超过上限 {max_per_turn}")
        self.created = created
        self.max_per_turn = max_per_turn


@dataclass
class AgentRecord:
    agent_id: str
    parent_id: str | None
    depth: int
    started_at: float
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    result: Any = None
    error: str | None = None


class AgentControlService:
    MAX_DEPTH = 3
    MAX_CONCURRENT = 5
    MAX_LIFETIME_SEC = 300.0
    MAX_PER_TURN = 20

    def __init__(
        self,
        max_depth: int = MAX_DEPTH,
        max_concurrent: int = MAX_CONCURRENT,
        max_lifetime_sec: float = MAX_LIFETIME_SEC,
        max_per_turn: int = MAX_PER_TURN,
    ) -> None:
        self._max_depth = max_depth
        self._max_concurrent = max_concurrent
        self._max_lifetime_sec = max_lifetime_sec
        self._max_per_turn = max_per_turn
        self._agents: dict[str, AgentRecord] = {}
        self._children: dict[str, list[str]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._turn_created_count: int = 0

    @property
    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.status == "running")

    def _get_depth(self, parent_id: str | None) -> int:
        if parent_id is None:
            return 0
        parent = self._agents.get(parent_id)
        if parent is None:
            return 0
        return parent.depth

    async def spawn(self, parent_id: str | None, spec: dict[str, Any] | None = None) -> AgentRecord:
        depth = self._get_depth(parent_id) + 1 if parent_id else 1
        if depth > self._max_depth:
            raise AgentDepthExceeded(depth, self._max_depth)

        if self.active_count >= self._max_concurrent:
            acquired = self._semaphore._value > 0
            if not acquired:
                raise AgentConcurrencyLimitExceeded(self.active_count, self._max_concurrent)

        self._turn_created_count += 1
        if self._turn_created_count > self._max_per_turn:
            raise AgentTurnBudgetExceeded(self._turn_created_count, self._max_per_turn)

        agent_id = str(uuid4())
        record = AgentRecord(
            agent_id=agent_id,
            parent_id=parent_id,
            depth=depth,
            started_at=time.monotonic(),
        )
        self._agents[agent_id] = record

        if parent_id:
            self._children.setdefault(parent_id, []).append(agent_id)

        await self._semaphore.acquire()
        return record

    def complete(self, agent_id: str, result: Any = None) -> None:
        record = self._agents.get(agent_id)
        if record and record.status == "running":
            record.status = "completed"
            record.result = result
            self._semaphore.release()

    def fail(self, agent_id: str, error: str) -> None:
        record = self._agents.get(agent_id)
        if record and record.status == "running":
            record.status = "failed"
            record.error = error
            self._semaphore.release()

    def cancel(self, agent_id: str) -> None:
        record = self._agents.get(agent_id)
        if record and record.status == "running":
            record.status = "cancelled"
            self._semaphore.release()
            for child_id in self._children.get(agent_id, []):
                self.cancel(child_id)

    def check_lifetime(self, agent_id: str) -> None:
        record = self._agents.get(agent_id)
        if record and record.status == "running":
            elapsed = time.monotonic() - record.started_at
            if elapsed > self._max_lifetime_sec:
                raise AgentLifetimeExceeded(elapsed, self._max_lifetime_sec)

    def get_record(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    def get_children(self, agent_id: str) -> list[AgentRecord]:
        return [
            self._agents[cid]
            for cid in self._children.get(agent_id, [])
            if cid in self._agents
        ]

    def reset_turn_budget(self) -> None:
        self._turn_created_count = 0
