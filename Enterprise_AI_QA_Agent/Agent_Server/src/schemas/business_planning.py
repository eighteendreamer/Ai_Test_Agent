"""Fixed target selection and server-owned planning basis; no deployment inference."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.business_applicability import BusinessApplicabilityReview
from src.schemas.business_environment import BusinessEnvironmentSnapshot
from src.schemas.document_differences import UUID_PATTERN


class BusinessEnvironmentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: str = Field(pattern=UUID_PATTERN)
    snapshot_id: str = Field(pattern=UUID_PATTERN)
    expected_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("environment_id", "snapshot_id")
    @classmethod
    def canonical_identity(cls, value):
        return str(UUID(value))


class BusinessPlanningEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["business-planning-environment-v1"] = "business-planning-environment-v1"
    snapshot: BusinessEnvironmentSnapshot
    reviews: list[BusinessApplicabilityReview] = Field(default_factory=list)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
