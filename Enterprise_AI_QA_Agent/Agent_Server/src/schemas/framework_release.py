"""Portable immutable reference; no machine paths or execution authority."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
GIT_REVISION_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


class FrameworkReleaseReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    schema_version: Literal[1] = 1
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_ARCHIVE_BYTES, strict=True)
    source_revision: str = Field(pattern=GIT_REVISION_PATTERN)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_exact_version(cls, value):
        if type(value) is not int:
            raise ValueError("Unsupported release reference version")
        return value
