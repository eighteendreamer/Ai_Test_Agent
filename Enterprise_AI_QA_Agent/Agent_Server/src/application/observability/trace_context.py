from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TraceContext(BaseModel):
    """Stable business correlation fields shared by local events and LangSmith."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    turn_id: str
    trace_id: str
    parent_trace_id: str = ""
    project_id: str = ""
    mode_key: str = "default"
    agent_key: str = ""
    case_id: str = ""
    case_version_id: str = ""
    suite_version_id: str = ""
    test_run_id: str = ""
    run_item_id: str = ""
    attempt_id: str = ""
    thread_id: str = ""
    environment: str = "development"
    runtime_version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)

    @field_validator("session_id", "turn_id", "trace_id")
    @classmethod
    def require_identity(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("trace identity fields cannot be empty")
        return normalized

    @field_validator(
        "parent_trace_id",
        "project_id",
        "mode_key",
        "agent_key",
        "case_id",
        "case_version_id",
        "suite_version_id",
        "test_run_id",
        "run_item_id",
        "attempt_id",
        "thread_id",
        "environment",
        "runtime_version",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

    @classmethod
    def from_graph_state(
        cls,
        state: dict[str, Any],
        *,
        project_id: str = "",
        environment: str = "development",
        runtime_version: str = "0.1.0",
    ) -> "TraceContext":
        context_bundle = state.get("context_bundle")
        context = context_bundle if isinstance(context_bundle, dict) else {}
        return cls(
            session_id=state.get("session_id", ""),
            turn_id=state.get("turn_id", ""),
            trace_id=state.get("trace_id", ""),
            parent_trace_id=context.get("parent_trace_id", ""),
            project_id=project_id or context.get("project_id", ""),
            mode_key=state.get("mode_key", "default"),
            agent_key=state.get("selected_agent_key", ""),
            case_id=context.get("case_id", ""),
            case_version_id=context.get("case_version_id", ""),
            suite_version_id=context.get("suite_version_id", ""),
            test_run_id=context.get("test_run_id", ""),
            run_item_id=context.get("run_item_id", ""),
            attempt_id=context.get("attempt_id", ""),
            thread_id=context.get("thread_id", ""),
            environment=environment,
            runtime_version=runtime_version,
            tags=[
                str(state.get("mode_key") or "default"),
                str(state.get("selected_agent_key") or "unassigned"),
            ],
        )

    def metadata(self) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in self.model_dump(exclude={"tags"}).items()
            if str(value)
        }
