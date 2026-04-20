from __future__ import annotations

from pydantic import BaseModel, Field

from src.schemas.email_config import EmailConfigPublic
from src.schemas.model_config import ModelCapabilitiesOverride, ModelConfigPublic


class ModelConfigUpdateRequest(BaseModel):
    model_name: str
    provider: str
    base_url: str
    api_key: str | None = None
    is_active: bool = False
    use_provider_defaults: bool | None = None
    capability_overrides: ModelCapabilitiesOverride = Field(default_factory=ModelCapabilitiesOverride)


class ModelConfigEditRequest(BaseModel):
    model_name: str
    provider: str
    base_url: str
    api_key: str | None = None
    is_active: bool = False
    use_provider_defaults: bool | None = None
    capability_overrides: ModelCapabilitiesOverride = Field(default_factory=ModelCapabilitiesOverride)


class ModelConfigActionResponse(BaseModel):
    ok: bool = True
    message: str
    item: ModelConfigPublic | None = None


class ModelConfigConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    item: ModelConfigPublic
    provider: str
    api_base_url: str
    latency_ms: int | None = None
    preview: str | None = None


class ModelConfigListResponse(BaseModel):
    items: list[ModelConfigPublic] = Field(default_factory=list)


class EmailConfigListResponse(BaseModel):
    items: list[EmailConfigPublic] = Field(default_factory=list)
