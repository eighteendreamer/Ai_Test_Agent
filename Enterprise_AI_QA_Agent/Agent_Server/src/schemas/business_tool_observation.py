"""Read an owned persisted tool value against a fixed business target."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.http_observation import HttpResponseObservation
from src.schemas.document_differences import UUID_PATTERN


class BusinessToolObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_job_id: str = Field(pattern=UUID_PATTERN)
    output_path_tokens: list[str] = Field(min_length=1, max_length=32)


class BusinessToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    implementation_version: Literal["business-tool-observation-v1"] = "business-tool-observation-v1"
    project_id: str
    environment_id: str
    environment_snapshot_id: str
    environment_snapshot_sha256: str
    tool_job_id: str
    tool_key: str
    call_id: str
    session_id: str
    turn_id: str
    run_id: str | None
    run_item_id: str | None
    attempt_id: str | None
    job_status: str
    output_sha256: str
    output_path_tokens: list[str]
    value: Any
    value_sha256: str
    http_observation: HttpResponseObservation | None
    target_status: Literal["matched", "mismatch", "unknown"]
    limitations: list[str]
    observed_at: datetime | None
    deployment_status: Literal["unknown"] = "unknown"
