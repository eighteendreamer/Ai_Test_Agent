"""Pure HTTP observation contracts, independent of mode/runtime initialization."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HttpTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheme: Literal["http", "https"]
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    path: str
    query_omitted: bool = False
    userinfo_omitted: bool = False
    fragment_omitted: bool = False


class HttpResponseHop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str
    target: HttpTarget
    status_code: int = Field(ge=100, le=599)


class HttpResponseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    implementation_version: Literal["api-http-observation-v1"] = "api-http-observation-v1"
    acquisition: Literal["network", "completed_request"]
    observed_at: datetime
    hops: list[HttpResponseHop] = Field(min_length=1, max_length=21)
