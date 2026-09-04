"""D3 Reusable Orchestration Strategies.

5 strategies replace the 4 separate coordinator implementations:
  - Sequential: step-by-step (smoke testing)
  - Parallel: concurrent execution with dependency ordering (API testing)
  - Debate: independent discovery + cross-review + synthesis (code review)
  - Pipeline: stage-based flow with approval gates (security testing)
  - Dynamic: agent-driven next-step decision (UI automation)

Each strategy is a stateless policy that the CoordinatorRuntimeService consults
to decide which workers to dispatch next.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class StrategyKind(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    DEBATE = "debate"
    PIPELINE = "pipeline"
    DYNAMIC = "dynamic"


@dataclass(frozen=True)
class WorkerSpec:
    """Specification for a worker to dispatch."""

    task_id: str
    description: str
    prompt: str
    agent_key: str
    model_key: str | None = None
    skill_keys: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepState:
    """Mutable state for a strategy step during execution."""

    step_id: str
    status: str = "pending"  # pending | running | completed | failed | skipped
    worker_specs: list[WorkerSpec] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class OrchestrationStrategy(Protocol):
    """Protocol for orchestration strategies."""

    @property
    def kind(self) -> StrategyKind:
        ...

    def plan(self, objective: dict[str, Any]) -> list[StepState]:
        """Create an execution plan from the objective."""
        ...

    def next_batch(self, steps: list[StepState]) -> list[WorkerSpec]:
        """Determine the next batch of workers to dispatch."""
        ...

    def on_worker_completed(self, steps: list[StepState], step_id: str, result: dict[str, Any]) -> None:
        """Handle a worker completion event."""
        ...

    def on_worker_failed(self, steps: list[StepState], step_id: str, error: str) -> None:
        """Handle a worker failure event."""
        ...

    def is_complete(self, steps: list[StepState]) -> bool:
        """Check if all steps are terminal."""
        ...


class SequentialStrategy:
    """Step-by-step execution: plan -> review -> execute -> analyze.

    Used by: smoke testing, simple QA flows.
    """

    @property
    def kind(self) -> StrategyKind:
        return StrategyKind.SEQUENTIAL

    def plan(self, objective: dict[str, Any]) -> list[StepState]:
        steps_def = objective.get("steps", [])
        return [
            StepState(step_id=s.get("id", f"step-{i}"))
            for i, s in enumerate(steps_def)
        ]

    def next_batch(self, steps: list[StepState]) -> list[WorkerSpec]:
        for step in steps:
            if step.status == "pending":
                step.status = "running"
                return list(step.worker_specs)
        return []

    def on_worker_completed(self, steps: list[StepState], step_id: str, result: dict[str, Any]) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "completed"
                step.result = result
                return

    def on_worker_failed(self, steps: list[StepState], step_id: str, error: str) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "failed"
                step.error = error
                return

    def is_complete(self, steps: list[StepState]) -> bool:
        return all(s.status in ("completed", "failed", "skipped") for s in steps)


class ParallelStrategy:
    """Concurrent execution with dependency-aware ordering.

    Used by: API testing, multi-endpoint probing.
    """

    @property
    def kind(self) -> StrategyKind:
        return StrategyKind.PARALLEL

    def plan(self, objective: dict[str, Any]) -> list[StepState]:
        steps_def = objective.get("steps", [])
        return [
            StepState(step_id=s.get("id", f"step-{i}"))
            for i, s in enumerate(steps_def)
        ]

    def next_batch(self, steps: list[StepState]) -> list[WorkerSpec]:
        batch: list[WorkerSpec] = []
        for step in steps:
            if step.status == "pending":
                step.status = "running"
                batch.extend(step.worker_specs)
        return batch

    def on_worker_completed(self, steps: list[StepState], step_id: str, result: dict[str, Any]) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "completed"
                step.result = result
                return

    def on_worker_failed(self, steps: list[StepState], step_id: str, error: str) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "failed"
                step.error = error
                return

    def is_complete(self, steps: list[StepState]) -> bool:
        return all(s.status in ("completed", "failed", "skipped") for s in steps)


class DebateStrategy:
    """Independent discovery + cross-review + synthesis.

    Used by: code review (multi-reviewer debate).
    Phases: discover -> cross_review -> synthesize.
    """

    @property
    def kind(self) -> StrategyKind:
        return StrategyKind.DEBATE

    def plan(self, objective: dict[str, Any]) -> list[StepState]:
        reviewers = objective.get("reviewers", [])
        discover_step = StepState(step_id="discover")
        discover_step.status = "pending"
        cross_review_step = StepState(step_id="cross_review")
        cross_review_step.status = "pending"
        synthesize_step = StepState(step_id="synthesize")
        synthesize_step.status = "pending"
        return [discover_step, cross_review_step, synthesize_step]

    def next_batch(self, steps: list[StepState]) -> list[WorkerSpec]:
        for step in steps:
            if step.status == "pending":
                if step.step_id == "cross_review":
                    discover = next((s for s in steps if s.step_id == "discover"), None)
                    if discover is None or discover.status != "completed":
                        return []
                elif step.step_id == "synthesize":
                    cross = next((s for s in steps if s.step_id == "cross_review"), None)
                    if cross is None or cross.status != "completed":
                        return []
                step.status = "running"
                return list(step.worker_specs)
        return []

    def on_worker_completed(self, steps: list[StepState], step_id: str, result: dict[str, Any]) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "completed"
                step.result = result
                return

    def on_worker_failed(self, steps: list[StepState], step_id: str, error: str) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "failed"
                step.error = error
                return

    def is_complete(self, steps: list[StepState]) -> bool:
        return all(s.status in ("completed", "failed", "skipped") for s in steps)


class PipelineStrategy:
    """Stage-based flow with approval gates.

    Used by: security testing (recon -> vulnerability -> exploit with gates).
    """

    @property
    def kind(self) -> StrategyKind:
        return StrategyKind.PIPELINE

    def plan(self, objective: dict[str, Any]) -> list[StepState]:
        stages = objective.get("stages", [])
        return [
            StepState(step_id=s.get("id", f"stage-{i}"))
            for i, s in enumerate(stages)
        ]

    def next_batch(self, steps: list[StepState]) -> list[WorkerSpec]:
        for step in steps:
            if step.status == "pending":
                step.status = "running"
                return list(step.worker_specs)
        return []

    def on_worker_completed(self, steps: list[StepState], step_id: str, result: dict[str, Any]) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "completed"
                step.result = result
                return

    def on_worker_failed(self, steps: list[StepState], step_id: str, error: str) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "failed"
                step.error = error
                return

    def is_complete(self, steps: list[StepState]) -> bool:
        return all(s.status in ("completed", "failed", "skipped") for s in steps)


class DynamicStrategy:
    """Agent-driven next-step decision.

    Used by: UI automation (agent decides what to do next based on observations).
    The agent itself determines the next action; the strategy just tracks state.
    """

    @property
    def kind(self) -> StrategyKind:
        return StrategyKind.DYNAMIC

    def plan(self, objective: dict[str, Any]) -> list[StepState]:
        return [StepState(step_id="dynamic")]

    def next_batch(self, steps: list[StepState]) -> list[WorkerSpec]:
        for step in steps:
            if step.status == "pending":
                step.status = "running"
                return list(step.worker_specs)
        return []

    def on_worker_completed(self, steps: list[StepState], step_id: str, result: dict[str, Any]) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "completed"
                step.result = result
                return

    def on_worker_failed(self, steps: list[StepState], step_id: str, error: str) -> None:
        for step in steps:
            if step.step_id == step_id:
                step.status = "failed"
                step.error = error
                return

    def is_complete(self, steps: list[StepState]) -> bool:
        return all(s.status in ("completed", "failed", "skipped") for s in steps)


STRATEGY_REGISTRY: dict[StrategyKind, type] = {
    StrategyKind.SEQUENTIAL: SequentialStrategy,
    StrategyKind.PARALLEL: ParallelStrategy,
    StrategyKind.DEBATE: DebateStrategy,
    StrategyKind.PIPELINE: PipelineStrategy,
    StrategyKind.DYNAMIC: DynamicStrategy,
}


def create_strategy(kind: StrategyKind) -> OrchestrationStrategy:
    """Factory function to create a strategy by kind."""
    cls = STRATEGY_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"Unknown strategy kind: {kind}")
    return cls()
