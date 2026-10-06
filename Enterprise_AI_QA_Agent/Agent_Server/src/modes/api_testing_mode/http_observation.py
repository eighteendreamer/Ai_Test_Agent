"""Capture response origins from HTTPX, independently of caller URL claims."""
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

HTTP_OBSERVATION_KEY = "http_observation_v1"


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


def http_target(url):
    parts = urlsplit(str(url))
    return HttpTarget(scheme=parts.scheme, host=parts.hostname or "",
        port=parts.port or (443 if parts.scheme == "https" else 80), path=parts.path or "/",
        query_omitted=bool(parts.query), userinfo_omitted=parts.username is not None,
        fragment_omitted=bool(parts.fragment))


def response_observation(response):
    # HTTPX QuickStart / Redirection and History: history is in request order;
    # response.request records the effective method after e.g. a 303 redirect.
    return HttpResponseObservation(acquisition="network", observed_at=datetime.now(timezone.utc),
        hops=[HttpResponseHop(method=hop.request.method, target=http_target(hop.request.url),
            status_code=hop.status_code) for hop in [*response.history, response]])


def target_in_scope(target, base_url):
    base = http_target(base_url)
    root = base.path.rstrip("/")
    return ((target.scheme, target.host, target.port) == (base.scheme, base.host, base.port)
            and (not root or target.path == root or target.path.startswith(root + "/")))
