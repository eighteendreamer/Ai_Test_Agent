from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.api_docs import GitDocumentOrigin


class GitRepositorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    root_path: str = Field(min_length=1, max_length=4096)
    enabled: bool = True


class GitDocumentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_cpu_seconds: float = Field(default=20, gt=0, le=300)
    max_memory_bytes: int = Field(default=536_870_912, gt=0)
    max_output_bytes: int = Field(default=40_000_000, gt=0)
    max_blob_bytes: int = Field(default=20_000_000, gt=0)


class GitDocumentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repositories: list[GitRepositorySource] = Field(default_factory=list, max_length=50)
    policy: GitDocumentPolicy = Field(default_factory=GitDocumentPolicy)

    @model_validator(mode="after")
    def unique_sources(self):
        if len({source.id for source in self.repositories}) != len(self.repositories):
            raise ValueError("仓库来源编号不能重复")
        return self


class GitDocumentSettingsRecord(BaseModel):
    scope_key: str
    revision: int = Field(ge=0)
    payload: GitDocumentSettings


class GitDocumentResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str = Field(min_length=1, max_length=80)
    reference: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=2048)

    @field_validator("reference")
    @classmethod
    def valid_reference(cls, value):
        if value.startswith("-") or any(ord(char) < 32 for char in value):
            raise ValueError("Git 引用包含无效控制字符或选项")
        return value

    @field_validator("path")
    @classmethod
    def relative_file(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or "\\" in value or ":" in value or any(ord(char) < 32 for char in value):
            raise ValueError("文档路径必须是仓库内相对路径，使用正斜线")
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("文档路径不能包含空段、. 或 ..")
        return value


class GitDocumentImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=120)
    repository_id: str = Field(min_length=1, max_length=80)
    config_revision: int = Field(ge=1)
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    blob: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    path: str = Field(min_length=1, max_length=2048)
    document_id: str | None = None
    expected_parent_version_id: str | None = None
    title: str | None = Field(default=None, max_length=160)

    _relative_file = field_validator("path")(GitDocumentResolveRequest.relative_file.__func__)

    @model_validator(mode="after")
    def explicit_target(self):
        if bool(self.document_id) != bool(self.expected_parent_version_id):
            raise ValueError("更新已有文档必须同时指定文档身份及预期父版本")
        return self


class GitDocumentResolution(BaseModel):
    project_id: str
    origin: GitDocumentOrigin
