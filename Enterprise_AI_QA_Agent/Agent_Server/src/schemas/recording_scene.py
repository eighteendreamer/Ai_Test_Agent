from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.case_management import TestCaseAssertion


class SceneCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=160)
    recording_id: str = Field(min_length=1, max_length=160)


class SceneStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_seq: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=4000)
    expected: str | None = Field(default=None, max_length=4000)
    alias: str | None = Field(default=None, min_length=1, max_length=160)


class SceneAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    source_seq: int = Field(ge=0)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("alias name must not be blank")
        return value.strip()


class SceneContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    status: Literal["draft", "organized", "archived"] = "draft"
    steps: list[SceneStep] = Field(min_length=1)
    aliases: list[SceneAlias] = Field(default_factory=list)
    assertions: list[TestCaseAssertion] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    execution_notes: str = Field(default="", max_length=20000)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("scene name must not be blank")
        return value.strip()


class SceneVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=160)
    parent_version_id: str = Field(min_length=1, max_length=160)
    change_reason: str = Field(min_length=1, max_length=2000)
    content: SceneContent


class SceneVersion(BaseModel):
    id: str
    scene_id: str
    project_id: str
    version: int
    parent_version_id: str | None = None
    request_id: str
    request_hash: str
    content_hash: str
    created_at: datetime
    content: SceneContent
    source: dict[str, Any]


class SceneBundle(BaseModel):
    scene_id: str
    project_id: str
    current_version_id: str
    version: SceneVersion
