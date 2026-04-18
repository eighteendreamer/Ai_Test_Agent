from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from src.schemas.agent import ToolDescriptor
from src.schemas.session import SessionMode, ToolApprovalRequest


PermissionBehavior = Literal["allow", "ask", "deny"]


@dataclass
class ToolPermissionDecision:
    tool_key: str
    tool_name: str
    behavior: PermissionBehavior
    reason: str
    source: str
    permission_level: str
    category: str

    def to_payload(self) -> dict[str, str]:
        return {
            "tool_key": self.tool_key,
            "tool_name": self.tool_name,
            "behavior": self.behavior,
            "reason": self.reason,
            "source": self.source,
            "permission_level": self.permission_level,
            "category": self.category,
        }


@dataclass
class PermissionEvaluation:
    allowed_tool_keys: list[str]
    approval_required_tool_keys: list[str]
    denied_tool_keys: list[str]
    decisions: list[ToolPermissionDecision]


class PermissionService:
    def evaluate(
        self,
        session_mode: SessionMode,
        tools: list[ToolDescriptor],
    ) -> PermissionEvaluation:
        allowed: list[str] = []
        approval_required: list[str] = []
        denied: list[str] = []
        decisions: list[ToolPermissionDecision] = []

        for tool in tools:
            decision = self._decide_tool_behavior(session_mode=session_mode, tool=tool)
            decisions.append(decision)
            if decision.behavior == "allow":
                allowed.append(tool.key)
                continue
            if decision.behavior == "deny":
                denied.append(tool.key)
                continue

            approval_required.append(tool.key)

        return PermissionEvaluation(
            allowed_tool_keys=allowed,
            approval_required_tool_keys=approval_required,
            denied_tool_keys=denied,
            decisions=decisions,
        )

    def create_approval_request(
        self,
        session_id: str,
        tool: ToolDescriptor,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> ToolApprovalRequest:
        return ToolApprovalRequest(
            id=str(uuid4()),
            session_id=session_id,
            tool_key=tool.key,
            tool_name=tool.name,
            reason=reason,
            created_at=datetime.utcnow(),
            metadata={
                "permission_level": tool.permission_level,
                "category": tool.category,
                "permission_behavior": "ask",
                "permission_source": "static_policy",
                "permission_reason": reason,
                **(metadata or {}),
            },
        )

    def get_tool_decision(
        self,
        evaluation: PermissionEvaluation,
        tool_key: str,
    ) -> ToolPermissionDecision | None:
        for item in evaluation.decisions:
            if item.tool_key == tool_key:
                return item
        return None

    def _decide_tool_behavior(
        self,
        session_mode: SessionMode,
        tool: ToolDescriptor,
    ) -> ToolPermissionDecision:
        source = "static_policy"
        if tool.permission_level == "safe":
            return ToolPermissionDecision(
                tool_key=tool.key,
                tool_name=tool.name,
                behavior="allow",
                reason=f"Tool '{tool.name}' is marked safe and can run without approval.",
                source=source,
                permission_level=tool.permission_level,
                category=tool.category,
            )
        if tool.permission_level == "restricted":
            return ToolPermissionDecision(
                tool_key=tool.key,
                tool_name=tool.name,
                behavior="deny",
                reason=(
                    f"Tool '{tool.name}' is restricted and is denied by default "
                    f"in {session_mode.value} mode."
                ),
                source=source,
                permission_level=tool.permission_level,
                category=tool.category,
            )
        return ToolPermissionDecision(
            tool_key=tool.key,
            tool_name=tool.name,
            behavior="ask",
            reason=(
                f"Tool '{tool.name}' requires explicit approval before execution "
                f"in {session_mode.value} mode."
            ),
            source=source,
            permission_level=tool.permission_level,
            category=tool.category,
        )
