"""Business target identities and immutable configuration captures, not tool leases."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.document_differences import UUID_PATTERN

TARGET_CONFIG_KEY = "project.environment_target"


class EnvironmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessEnvironmentCreateRequest(EnvironmentModel):
    request_id: str = Field(min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")
    environment_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

    @field_validator("environment_key", mode="before")
    @classmethod
    def normalize_key(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class BusinessEnvironmentRecord(EnvironmentModel):
    implementation_version: Literal["business-environment-v1"] = "business-environment-v1"
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    request: BusinessEnvironmentCreateRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "archived"] = "active"
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class BusinessTargetConfiguration(EnvironmentModel):
    display_name: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=2048)
    description: str = Field(default="", max_length=2000)

    @field_validator("display_name", "description", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_url")
    @classmethod
    def validate_address(cls, value):
        parts = urlsplit(value)
        # This is a non-secret identity address, not a request URL or auth store.
        # Do not guess a scheme, discard query values, or silently rewrite paths.
        if (value != value.strip() or any(c.isspace() for c in value)
            or parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or "?" in value or "#" in value or "\\" in value):
            raise ValueError("目标地址须为不含账号、密码、查询参数或片段的HTTP(S)地址")
        if parts.port == 0:
            raise ValueError("目标地址端口无效")
        return value


class BusinessTargetConfigurationRecord(EnvironmentModel):
    config_key: Literal["project.environment_target"] = TARGET_CONFIG_KEY
    project_id: str = Field(pattern=UUID_PATTERN)
    environment_id: str = Field(pattern=UUID_PATTERN)
    scope_key: str = Field(pattern=UUID_PATTERN)
    revision: int = Field(ge=1)
    payload: BusinessTargetConfiguration
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BusinessEnvironmentCaptureRequest(EnvironmentModel):
    request_id: str = Field(min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")
    expected_config_revision: int = Field(ge=1)
    expected_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BusinessEnvironmentSnapshot(EnvironmentModel):
    implementation_version: Literal["business-environment-snapshot-v1"] = "business-environment-snapshot-v1"
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    environment_id: str = Field(pattern=UUID_PATTERN)
    environment_key: str
    registration_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: BusinessEnvironmentCaptureRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: BusinessTargetConfigurationRecord
    deployment_status: Literal["unknown"] = "unknown"
    captured_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BusinessEnvironmentPage(EnvironmentModel):
    items: list[BusinessEnvironmentRecord]
    limit: int
    offset: int
    has_more: bool


class BusinessEnvironmentSnapshotPage(EnvironmentModel):
    items: list[BusinessEnvironmentSnapshot]
    limit: int
    offset: int
    has_more: bool
