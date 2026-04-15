from __future__ import annotations

from pydantic import BaseModel, Field

from src.schemas.email_config import EmailConfigPublic
from src.schemas.model_config import ModelConfigPublic


class ModelConfigUpdateRequest(BaseModel):
    model_name: str
    provider: str
    base_url: str
    api_key: str | None = None
    is_active: bool = False


class ModelConfigListResponse(BaseModel):
    items: list[ModelConfigPublic] = Field(default_factory=list)


class EmailConfigListResponse(BaseModel):
    items: list[EmailConfigPublic] = Field(default_factory=list)
