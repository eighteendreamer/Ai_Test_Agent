"""Locate candidate projects for an API testing request.

Relies on :class:`ApiDocsService` so we do not re-implement document IO:
just read the already-ingested catalog, aggregate by project, and score
candidates against the user's request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from src.application.documents.api_docs_service import ApiDocsService
from src.modes.api_testing_mode.campaign_state import (
    ApiTestingRequestState,
    DocumentCandidate,
    ProjectCandidate,
)
from src.schemas.api_docs import ApiDocRecord


UNSET_PROJECT_LABEL = "未命名项目"


@dataclass
class ProjectLocatorResult:
    candidates: list[ProjectCandidate]
    documents_by_project: dict[str, list[DocumentCandidate]]
    needs_clarification: bool
    reason: str = ""

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


class ApiProjectLocator:
    """Discover and rank candidate projects from the API docs library."""

    def __init__(self, *, api_docs_service: ApiDocsService) -> None:
        self._api_docs_service = api_docs_service

    async def locate(
        self,
        *,
        request: ApiTestingRequestState,
    ) -> ProjectLocatorResult:
        documents = await self._api_docs_service.list_documents(
            project_id=request.project_id or None,
        )
        if not documents:
            return ProjectLocatorResult(
                candidates=[],
                documents_by_project={},
                needs_clarification=False,
                reason="No API documents are registered in the library.",
            )

        groups: dict[str, list[ApiDocRecord]] = {}
        for record in documents:
            key = self._project_key(record)
            groups.setdefault(key, []).append(record)

        hint_tokens = self._request_tokens(request)
        candidates: list[ProjectCandidate] = []
        docs_by_project: dict[str, list[DocumentCandidate]] = {}
        for key, records in groups.items():
            candidate, doc_candidates = self._build_candidate(key, records, hint_tokens, request)
            candidates.append(candidate)
            docs_by_project[key] = doc_candidates

        candidates.sort(key=lambda item: item.score, reverse=True)

        needs_clarification, reason = self._needs_clarification(candidates, request)
        return ProjectLocatorResult(
            candidates=candidates,
            documents_by_project=docs_by_project,
            needs_clarification=needs_clarification,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Scoring and grouping
    # ------------------------------------------------------------------

    def _project_key(self, record: ApiDocRecord) -> str:
        if record.project_id:
            return f"project_id:{record.project_id}"
        name = (record.project_name or "").strip()
        if name:
            return name
        url = (record.project_url or "").strip()
        if url:
            host = urlparse(url).hostname or url
            return host or UNSET_PROJECT_LABEL
        return UNSET_PROJECT_LABEL

    def _build_candidate(
        self,
        key: str,
        records: list[ApiDocRecord],
        hint_tokens: list[str],
        request: ApiTestingRequestState,
    ) -> tuple[ProjectCandidate, list[DocumentCandidate]]:
        fallback_name = UNSET_PROJECT_LABEL if records[0].project_id else key
        project_name = (records[0].project_name or fallback_name).strip() or fallback_name
        project_url = self._pick_project_url(records)
        doc_ids = [record.id for record in records]
        doc_candidates = [
            DocumentCandidate(
                doc_id=record.id,
                title=record.title,
                project_id=record.project_id or "",
                filename=record.filename,
                project_name=project_name,
                project_url=record.project_url or project_url,
                endpoint_count=int(record.endpoint_count or 0),
                updated_at=record.updated_at.isoformat() if record.updated_at else "",
            )
            for record in records
        ]
        endpoint_count = sum(int(record.endpoint_count or 0) for record in records)
        score, rationale = self._score(
            project_name=project_name,
            project_url=project_url,
            hint_tokens=hint_tokens,
            request=request,
            records=records,
        )
        return (
            ProjectCandidate(
                project_name=project_name,
                project_id=str(records[0].project_id or ""),
                project_url=project_url,
                doc_ids=doc_ids,
                doc_count=len(records),
                endpoint_count=endpoint_count,
                score=score,
                rationale=rationale,
            ),
            doc_candidates,
        )

    def _pick_project_url(self, records: list[ApiDocRecord]) -> str:
        for record in records:
            if record.project_url:
                return record.project_url
        return ""

    def _request_tokens(self, request: ApiTestingRequestState) -> list[str]:
        tokens: list[str] = []
        for value in (
            request.project_hint,
            request.domain_hint,
            request.objective,
            request.raw_message,
        ):
            cleaned = (value or "").strip().lower()
            if not cleaned:
                continue
            # Split on whitespace, punctuation, and common Chinese separators.
            for chunk in re.split(r"[\s,.;/\-_，。；、]+", cleaned):
                chunk = chunk.strip()
                if chunk and len(chunk) <= 40:
                    tokens.append(chunk)
        # De-duplicate while keeping order.
        seen: set[str] = set()
        result: list[str] = []
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    def _score(
        self,
        *,
        project_name: str,
        project_url: str,
        hint_tokens: list[str],
        request: ApiTestingRequestState,
        records: list[ApiDocRecord],
    ) -> tuple[float, str]:
        score = 0.0
        reasons: list[str] = []
        lowered_name = project_name.lower()
        lowered_url = project_url.lower()

        for token in hint_tokens:
            if not token:
                continue
            if token in lowered_name:
                score += 40.0
                reasons.append(f"match project_name:{token}")
            if token in lowered_url:
                score += 25.0
                reasons.append(f"match project_url:{token}")
            for record in records:
                title = (record.title or "").lower()
                filename = (record.filename or "").lower()
                if token and token in title:
                    score += 10.0
                    reasons.append(f"match doc_title:{token}")
                    break
                if token and token in filename:
                    score += 6.0
                    reasons.append(f"match filename:{token}")
                    break

        endpoint_hint = (request.endpoint_hint or "").strip().lower()
        if endpoint_hint:
            for record in records:
                score += 4.0 * min(1, int(record.endpoint_count or 0))
                _ = record
                break

        # Prefer projects that already have endpoint metadata.
        endpoint_total = sum(int(record.endpoint_count or 0) for record in records)
        score += min(endpoint_total, 40) * 0.5

        if not hint_tokens:
            reasons.append("no specific hint - baseline score")

        return round(score, 3), "; ".join(reasons) or "baseline"

    # ------------------------------------------------------------------
    # Clarification gate
    # ------------------------------------------------------------------

    def _needs_clarification(
        self,
        candidates: Iterable[ProjectCandidate],
        request: ApiTestingRequestState,
    ) -> tuple[bool, str]:
        sorted_candidates = list(candidates)
        if not sorted_candidates:
            return False, "No candidates to clarify."
        # Only one candidate and it has evidence -> safe to auto-pick.
        if len(sorted_candidates) == 1:
            only = sorted_candidates[0]
            if only.project_name and only.project_name != UNSET_PROJECT_LABEL:
                return False, "Only one project available."
            # Single unnamed project: still require clarification unless user specified it.
            if request.project_hint:
                return False, "Hint matches the only available project."
            return True, "Only project is unnamed; user should confirm."

        top = sorted_candidates[0]
        second = sorted_candidates[1] if len(sorted_candidates) > 1 else None

        # If user did not specify project_hint, always clarify when >1 project exists.
        if not request.project_hint:
            return True, "Multiple projects available and user did not specify project."

        # With a hint, require the top candidate to dominate.
        if top.score <= 0:
            return True, "Top candidate has zero score."
        if second is not None and (top.score - second.score) < 20.0:
            return True, "Top two candidates have close scores."
        return False, "Top candidate dominates the others."


__all__ = ["ApiProjectLocator", "ProjectLocatorResult", "UNSET_PROJECT_LABEL"]
