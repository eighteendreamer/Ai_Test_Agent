from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


MCPTransportKind = Literal["runtime", "stdio", "http", "websocket"]


class MCPResourceDescriptor(BaseModel):
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPToolCallResult(BaseModel):
    server_key: str
    capability: str
    status: str = "completed"
    payload: dict[str, Any] = Field(default_factory=dict)
    resources: list[MCPResourceDescriptor] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
