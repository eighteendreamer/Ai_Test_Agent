"""Detect preconditions (auth / data dependencies) for selected endpoints."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.modes.api_testing_mode.campaign_state import EndpointCandidate
from src.modes.api_testing_mode.contracts import (
    PRECOND_AUTH_TOKEN,
    PRECOND_RESOURCE_ID_DEPENDENCY,
    PRECOND_SESSION_COOKIE,
)


PATH_PARAM_PATTERN = re.compile(r"\{([a-zA-Z0-9_]+)\}|:([a-zA-Z0-9_]+)")
logger = logging.getLogger("uvicorn.error.api_testing_mode.preconditions")


@dataclass
class PreconditionAnalysis:
    """Aggregated precondition view for a campaign."""

    requires_auth: bool = False
    auth_kind: str = ""  # PRECOND_AUTH_TOKEN / PRECOND_SESSION_COOKIE
    auth_endpoint_ids: list[str] = field(default_factory=list)
    path_parameter_dependencies: dict[str, list[str]] = field(default_factory=dict)
    path_parameter_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    requires_manual_data: bool = False
    details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "requires_auth": self.requires_auth,
            "auth_kind": self.auth_kind,
            "auth_endpoint_ids": list(self.auth_endpoint_ids),
            "path_parameter_dependencies": dict(self.path_parameter_dependencies),
            "path_parameter_sources": {key: dict(value) for key, value in self.path_parameter_sources.items()},
            "requires_manual_data": self.requires_manual_data,
            "details": list(self.details),
        }


class PreconditionResolver:
    """Inspect endpoints to surface implicit preconditions."""

    def analyze(
        self,
        *,
        endpoints: list[EndpointCandidate],
        auth_hint: dict | None = None,
    ) -> PreconditionAnalysis:
        analysis = PreconditionAnalysis()
        auth_hint = auth_hint or {}

        for endpoint in endpoints:
            detail: dict = {
                "endpoint_id": endpoint.endpoint_id,
                "method": endpoint.method,
                "path": endpoint.path,
                "preconditions": list(endpoint.preconditions),
            }

            if PRECOND_AUTH_TOKEN in endpoint.preconditions:
                analysis.requires_auth = True
                if not analysis.auth_kind:
                    analysis.auth_kind = PRECOND_AUTH_TOKEN
                analysis.auth_endpoint_ids.append(endpoint.endpoint_id)

            if PRECOND_SESSION_COOKIE in endpoint.preconditions:
                analysis.requires_auth = True
                if analysis.auth_kind in {"", PRECOND_AUTH_TOKEN}:
                    analysis.auth_kind = PRECOND_SESSION_COOKIE

            path_params = self._extract_path_params(endpoint.path)
            if path_params:
                analysis.path_parameter_dependencies[endpoint.endpoint_id] = path_params
                detail["path_parameters"] = path_params
                sources = self._find_id_producers(endpoints, endpoint)
                analysis.path_parameter_sources[endpoint.endpoint_id] = sources
                detail["path_parameter_sources"] = dict(sources)
                if set(path_params) - sources.keys():
                    analysis.requires_manual_data = True

            analysis.details.append(detail)

        if auth_hint.get("type") in {"bearer", "api_key", "cookie"} and not analysis.requires_auth:
            # Document declares auth globally even if individual endpoints did not.
            analysis.requires_auth = True
            analysis.auth_kind = analysis.auth_kind or PRECOND_AUTH_TOKEN
        return analysis

    def _extract_path_params(self, path: str) -> list[str]:
        params: list[str] = []
        for match in PATH_PARAM_PATTERN.finditer(path or ""):
            param = match.group(1) or match.group(2)
            if param and param not in params:
                params.append(param)
        return params

    def _find_id_producers(
        self,
        endpoints: list[EndpointCandidate],
        target: EndpointCandidate,
    ) -> dict[str, str]:
        # Reuse the existing collection-POST heuristic, but match the actual
        # collection path for each placeholder instead of parameter-name
        # substrings. This is an API hint, not a general case dependency rule.
        sources: dict[str, str] = {}
        for match in PATH_PARAM_PATTERN.finditer(target.path):
            param = match.group(1) or match.group(2)
            collection_path = target.path[:match.start()].rstrip("/")
            candidates = [candidate for candidate in endpoints
                          if candidate.endpoint_id != target.endpoint_id
                          and candidate.method.upper() == "POST"
                          and candidate.path.rstrip("/") == collection_path]
            if len(candidates) == 1:
                sources[param] = candidates[0].endpoint_id
            else:
                logger.info(
                    "api_path_parameter_source_unresolved",
                    extra={"endpoint_id": target.endpoint_id, "parameter": param, "candidate_count": len(candidates)},
                )
        return sources


__all__ = ["PreconditionResolver", "PreconditionAnalysis"]
