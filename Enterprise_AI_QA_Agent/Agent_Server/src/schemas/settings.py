from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from src.schemas.email_config import EmailConfigPublic
from src.schemas.model_config import (
    ModelApplication,
    ModelAuthType,
    ModelCapabilitiesOverride,
    ModelConfigPublic,
    ModelTransport,
)


class GeneralSettingsPayload(BaseModel):
    language: str = "zh-CN"
    modelOutputLanguage: str = "follow-system"
    notifySessionCompleteWhenAway: bool = True
    notifyApprovalRequiredWhenAway: bool = True
    notificationsAwayOnly: bool = True
    fontFamily: str = "system"
    fontSize: str = "standard"
    reduceMotion: bool = False
    lastSavedAt: str = ""


class ModelConfigUpdateRequest(BaseModel):
    model_name: str
    provider: str = ""
    transport: ModelTransport | None = None
    base_url: str
    api_key: str | None = None
    is_active: bool = False
    use_provider_defaults: bool | None = None
    capability_overrides: ModelCapabilitiesOverride = Field(default_factory=ModelCapabilitiesOverride)
    auth_type: ModelAuthType = "api_key"
    oauth_provider: str | None = None
    oauth_refresh_token: str | None = None
    context_window: int | None = Field(default=None, ge=1024)
    max_output_tokens: int | None = Field(default=None, ge=1)
    applications: list[ModelApplication] = Field(
        default_factory=lambda: ["task_execution"]
    )

    @field_validator("applications")
    @classmethod
    def normalize_applications(cls, value: list[ModelApplication]):
        normalized = list(dict.fromkeys(value))
        if len(normalized) != 1:
            raise ValueError("Exactly one model application must be selected.")
        return normalized


class ModelConfigEditRequest(BaseModel):
    model_name: str
    provider: str = ""
    transport: ModelTransport | None = None
    base_url: str
    api_key: str | None = None
    is_active: bool = False
    use_provider_defaults: bool | None = None
    capability_overrides: ModelCapabilitiesOverride = Field(default_factory=ModelCapabilitiesOverride)
    auth_type: ModelAuthType = "api_key"
    oauth_provider: str | None = None
    oauth_refresh_token: str | None = None
    applications: list[ModelApplication] = Field(
        default_factory=lambda: ["task_execution"]
    )

    @field_validator("applications")
    @classmethod
    def normalize_applications(cls, value: list[ModelApplication]):
        normalized = list(dict.fromkeys(value))
        if len(normalized) != 1:
            raise ValueError("Exactly one model application must be selected.")
        return normalized


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
