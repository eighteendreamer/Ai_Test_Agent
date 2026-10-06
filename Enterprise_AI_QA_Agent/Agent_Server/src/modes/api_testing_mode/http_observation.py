"""Capture response origins from HTTPX, independently of caller URL claims."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from src.schemas.http_observation import HttpTarget, HttpResponseHop, HttpResponseObservation

HTTP_OBSERVATION_KEY = "http_observation_v1"


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
