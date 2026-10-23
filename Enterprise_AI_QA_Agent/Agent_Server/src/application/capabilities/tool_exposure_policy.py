from __future__ import annotations

from dataclasses import dataclass

from src.schemas.agent import AgentDescriptor, ToolDescriptor
from src.application.test_runs.execution_replanning import TOOL_KEY as REPLANNING_TOOL_KEY, execution_replanning_enabled


# No upstream counterpart (checked: existing mode/agent exposure and local
# Agent test/trajectory guidance): RunItems need an explicit opt-in for helpers.
CASE_SCOPED_OPTIONAL_TOOL_KEYS = frozenset({"file-artifact-manager", "report-writer"})


@dataclass(frozen=True)
class CaseToolScope:
    required_tool_keys: frozenset[str] | None = None
    unknown_tool_keys: frozenset[str] = frozenset()
    invalid_reason: str | None = None


class ToolExposurePolicy:
    """Require the selected agent to explicitly support a tool or capability."""

    def case_tool_scope(self, *, context_bundle: dict) -> CaseToolScope:
        """Read an opt-in for auxiliary file/report tools from the frozen case JSON.

        Missing declarations preserve existing exposure for historical cases.
        Mode and agent policy continue to govern every other tool.
        """
        if not context_bundle.get("case_execution_managed"):
            return CaseToolScope()
        execution = context_bundle.get("case_execution")
        if not isinstance(execution, dict):
            return CaseToolScope()
        target_key = execution.get("target_key")
        steps = execution.get("steps")
        if not isinstance(steps, list):
            return CaseToolScope()
        for step in steps:
            if not isinstance(step, dict) or step.get("phase") != "case":
                continue
            item = step.get("item")
            if not isinstance(item, dict) or item.get("key") != target_key:
                continue
            content = step.get("content_json")
            if not isinstance(content, dict) or "required_tool_keys" not in content:
                return CaseToolScope()
            requested = content["required_tool_keys"]
            if not isinstance(requested, list) or any(
                not isinstance(key, str) or not key.strip() for key in requested
            ):
                return CaseToolScope(invalid_reason="required_tool_keys must be a string array")
            normalized = {key.strip() for key in requested}
            return CaseToolScope(
                required_tool_keys=frozenset(normalized.intersection(CASE_SCOPED_OPTIONAL_TOOL_KEYS)),
                unknown_tool_keys=frozenset(normalized.difference(CASE_SCOPED_OPTIONAL_TOOL_KEYS)),
            )
        return CaseToolScope()

    def is_supported(self, *, tool: ToolDescriptor, agent: AgentDescriptor) -> bool:
        if tool.key == "skill":
            return True
        if tool.key in agent.supported_tools:
            return True
        return bool(set(agent.supported_capabilities).intersection(tool.capability_keys))

    def filter_supported(
        self,
        *,
        tools: list[ToolDescriptor],
        agent: AgentDescriptor,
        excluded_tool_keys: set[str] | frozenset[str] | None = None,
        context_bundle: dict | None = None,
    ) -> list[ToolDescriptor]:
        excluded = excluded_tool_keys or set()
        return [tool for tool in tools if tool.key not in excluded and (
            execution_replanning_enabled(context_bundle or {}) if tool.key == REPLANNING_TOOL_KEY
            else self.is_supported(tool=tool, agent=agent))]
