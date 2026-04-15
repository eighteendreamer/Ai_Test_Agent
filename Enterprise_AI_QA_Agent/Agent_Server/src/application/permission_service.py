from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from src.schemas.agent import ToolDescriptor
from src.schemas.session import SessionMode, ToolApprovalRequest


@dataclass
class PermissionEvaluation:
    allowed_tool_keys: list[str]
    approval_required_tool_keys: list[str]


class PermissionService:
    def evaluate(
        self,
        session_mode: SessionMode,
        tools: list[ToolDescriptor],
    ) -> PermissionEvaluation:
        allowed: list[str] = []
        approval_required: list[str] = []

        for tool in tools:
            if tool.permission_level == "safe":
                allowed.append(tool.key)
                continue

            approval_required.append(tool.key)

        return PermissionEvaluation(
            allowed_tool_keys=allowed,
            approval_required_tool_keys=approval_required,
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
                **(metadata or {}),
            },
        )
