"""Parse API documents into a normalized endpoint index.

The heavy lifting (RustFS fetch + markdown endpoint extraction) already lives
in :class:`ApiDocsService`. This module builds on top of it by:

- Generating a stable ``endpoint_id`` per endpoint
- Merging endpoints from multiple documents while de-duplicating
- Parsing auth hints (login endpoint, token field) from document content
- Identifying ``base_urls`` from the document's ``project_url`` metadata
- Caching parsed indexes to avoid re-parsing on every turn
- Extracting request body schema hints from endpoint sections
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.application.documents.api_docs_service import ApiDocsService
from src.modes.api_testing_mode.campaign_state import (
    DocumentCandidate,
    EndpointCandidate,
)
from src.modes.api_testing_mode.contracts import (
    AUTH_BEARER,
    AUTH_COOKIE,
    AUTH_NONE,
    PRECOND_AUTH_TOKEN,
    PRECOND_SESSION_COOKIE,
)


@dataclass
class RequestBodySchema:
    """Extracted request body schema hints for an endpoint."""

    content_type: str = "application/json"
    fields: list[dict[str, Any]] = field(default_factory=list)
    example: dict[str, Any] | None = None
    raw_text: str = ""


@dataclass
class ParsedApiDocIndex:
    """A lightweight structured index for one API document."""

    doc_id: str
    project_name: str = ""
    project_url: str = ""
    base_urls: list[str] = field(default_factory=list)
    auth_hint: dict[str, Any] = field(default_factory=dict)
    endpoints: list[EndpointCandidate] = field(default_factory=list)
    request_schemas: dict[str, RequestBodySchema] = field(default_factory=dict)


class ApiDocParser:
    """Build ``ParsedApiDocIndex`` objects by delegating to ``ApiDocsService``."""

    AUTH_KEYWORDS = {
        "authorization",
        "bearer",
        "token",
        "登录",
        "身份验证",
        "鉴权",
        "认证",
        "access_token",
        "access token",
        "api_key",
        "api key",
        "session",
        "cookie",
    }
    LOGIN_PATH_HINT = re.compile(r"/(login|signin|sign_in|auth|oauth|token)", re.IGNORECASE)
    TOKEN_FIELD_PATTERN = re.compile(
        r"(?i)(access_token|accessToken|id_token|token|apikey|api_key)"
    )
    # Patterns for extracting request body fields from markdown sections.
    JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
    FIELD_TABLE_PATTERN = re.compile(
        r"\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.*?)\s*\|",
    )

    def __init__(self, *, api_docs_service: ApiDocsService) -> None:
        self._api_docs_service = api_docs_service
        # In-memory index cache: doc_id -> ParsedApiDocIndex.
        self._index_cache: dict[str, ParsedApiDocIndex] = {}

    async def parse_documents(
        self,
        *,
        documents: Iterable[DocumentCandidate],
        max_chars: int = 40000,
        use_cache: bool = True,
    ) -> list[ParsedApiDocIndex]:
        parsed: list[ParsedApiDocIndex] = []
        for doc in documents:
            # Check cache first.
            if use_cache and doc.doc_id in self._index_cache:
                parsed.append(self._index_cache[doc.doc_id])
                continue
            try:
                parsed_doc = await self._parse_single(doc, max_chars=max_chars)
            except Exception:
                parsed_doc = ParsedApiDocIndex(
                    doc_id=doc.doc_id,
                    project_name=doc.project_name,
                    project_url=doc.project_url,
                )
            # Store in cache.
            self._index_cache[doc.doc_id] = parsed_doc
            parsed.append(parsed_doc)
        return parsed

    async def parse_documents_deduplicated(
        self,
        *,
        documents: Iterable[DocumentCandidate],
        max_chars: int = 40000,
    ) -> list[ParsedApiDocIndex]:
        """Parse documents and deduplicate endpoints across all documents.

        When the same method+path appears in multiple documents, keep only
        the first occurrence (by document order).
        """
        all_indexes = await self.parse_documents(documents=documents, max_chars=max_chars)
        seen_keys: set[str] = set()
        for index in all_indexes:
            unique_endpoints: list[EndpointCandidate] = []
            for endpoint in index.endpoints:
                dedup_key = f"{endpoint.method}|{endpoint.path}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                unique_endpoints.append(endpoint)
            index.endpoints = unique_endpoints
        return all_indexes

    def invalidate_cache(self, doc_id: str | None = None) -> None:
        """Clear cached index for a specific doc or all docs."""
        if doc_id:
            self._index_cache.pop(doc_id, None)
        else:
            self._index_cache.clear()

    async def _parse_single(
        self,
        doc: DocumentCandidate,
        *,
        max_chars: int,
    ) -> ParsedApiDocIndex:
        content_result = await self._api_docs_service.read_document_content(
            doc.doc_id,
            max_chars=max_chars,
        )
        raw_content = str(content_result.get("content") or "")
        raw_endpoints = self._api_docs_service._extract_markdown_endpoints(raw_content)  # noqa: SLF001

        endpoints: list[EndpointCandidate] = []
        request_schemas: dict[str, RequestBodySchema] = {}

        for raw in raw_endpoints:
            method = str(raw.get("method") or "").upper()
            path = str(raw.get("path") or "").strip()
            if not method or not path:
                continue
            full_url = str(raw.get("full_url") or "")
            summary = str(raw.get("summary") or "")
            section = str(raw.get("section") or "")
            preconditions = self._detect_preconditions(section)
            endpoint_id = self._build_endpoint_id(doc.doc_id, method, path)

            endpoints.append(
                EndpointCandidate(
                    endpoint_id=endpoint_id,
                    doc_id=doc.doc_id,
                    method=method,
                    path=path,
                    full_url=full_url,
                    summary=summary,
                    tags=[],
                    preconditions=preconditions,
                    section=section[:4000],
                )
            )

            # Extract request body schema for write methods.
            if method in {"POST", "PUT", "PATCH"}:
                schema = self._extract_request_schema(section)
                if schema and (schema.fields or schema.example):
                    request_schemas[endpoint_id] = schema

        base_urls = self._collect_base_urls(
            project_url=doc.project_url,
            endpoints=endpoints,
        )
        auth_hint = self._detect_auth_hint(raw_content, endpoints)

        return ParsedApiDocIndex(
            doc_id=doc.doc_id,
            project_name=doc.project_name,
            project_url=doc.project_url,
            base_urls=base_urls,
            auth_hint=auth_hint,
            endpoints=endpoints,
            request_schemas=request_schemas,
        )

    # ------------------------------------------------------------------
    # Request body schema extraction
    # ------------------------------------------------------------------

    def _extract_request_schema(self, section: str) -> RequestBodySchema:
        """Extract request body schema from a markdown endpoint section."""
        schema = RequestBodySchema()

        # Try to find a JSON example block.
        json_blocks = self.JSON_BLOCK_PATTERN.findall(section)
        for block in json_blocks:
            block_stripped = block.strip()
            # Skip response examples (heuristic: after "响应" or "response" heading).
            block_start = section.find(block_stripped)
            preceding = section[:block_start].lower() if block_start > 0 else ""
            # If the preceding text mentions "响应" or "response" more recently than "请求" or "request",
            # this is likely a response example.
            last_request = max(preceding.rfind("请求"), preceding.rfind("request"), preceding.rfind("body"))
            last_response = max(preceding.rfind("响应"), preceding.rfind("response"), preceding.rfind("返回"))
            if last_response > last_request and last_response >= 0:
                continue
            try:
                parsed = json.loads(block_stripped)
                if isinstance(parsed, dict):
                    schema.example = parsed
                    break
            except (json.JSONDecodeError, ValueError):
                continue

        # Try to find field table (| field | type | description |).
        fields: list[dict[str, Any]] = []
        # Look for request-related table section.
        request_section = self._find_request_section(section)
        if request_section:
            for match in self.FIELD_TABLE_PATTERN.finditer(request_section):
                field_name = match.group(1).strip()
                field_type = match.group(2).strip()
                field_desc = match.group(3).strip()
                # Skip table headers.
                if field_name.lower() in {"参数", "字段", "field", "name", "parameter", "---"}:
                    continue
                if field_type.lower() in {"类型", "type", "---"}:
                    continue
                fields.append({
                    "name": field_name,
                    "type": field_type,
                    "description": field_desc,
                    "required": "必" in field_desc or "required" in field_desc.lower(),
                })

        schema.fields = fields
        if section:
            schema.raw_text = section[:2000]
        return schema

    def _find_request_section(self, section: str) -> str:
        """Extract the portion of a section that describes request parameters."""
        lowered = section.lower()
        # Find markers for request body section.
        markers = ["请求参数", "请求体", "request body", "request parameters", "入参", "body参数"]
        best_start = -1
        for marker in markers:
            idx = lowered.find(marker)
            if idx >= 0 and (best_start < 0 or idx < best_start):
                best_start = idx

        if best_start < 0:
            # Fallback: look for any table in the section.
            if "|" in section:
                return section
            return ""

        # Find the end: next heading or response section.
        end_markers = ["响应", "response", "返回", "###", "## "]
        end_pos = len(section)
        for marker in end_markers:
            idx = lowered.find(marker, best_start + 10)
            if idx >= 0 and idx < end_pos:
                end_pos = idx

        return section[best_start:end_pos]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_endpoint_id(self, doc_id: str, method: str, path: str) -> str:
        digest = hashlib.sha1(f"{doc_id}|{method}|{path}".encode("utf-8")).hexdigest()
        return f"ep_{digest[:16]}"

    def _collect_base_urls(
        self,
        *,
        project_url: str,
        endpoints: Iterable[EndpointCandidate],
    ) -> list[str]:
        candidates: list[str] = []
        if project_url:
            candidates.append(project_url.rstrip("/"))
        for endpoint in endpoints:
            if not endpoint.full_url:
                continue
            match = re.match(r"(https?://[^/]+)", endpoint.full_url)
            if match:
                base = match.group(1).rstrip("/")
                if base not in candidates:
                    candidates.append(base)
        return candidates

    def _detect_preconditions(self, section: str) -> list[str]:
        lowered = section.lower()
        preconditions: list[str] = []
        if any(token in lowered for token in ("authorization", "bearer", "token", "鉴权", "认证")):
            preconditions.append(PRECOND_AUTH_TOKEN)
        if "cookie" in lowered or "session" in lowered:
            preconditions.append(PRECOND_SESSION_COOKIE)
        return preconditions

    def _detect_auth_hint(
        self,
        content: str,
        endpoints: list[EndpointCandidate],
    ) -> dict[str, Any]:
        hint: dict[str, Any] = {"type": AUTH_NONE}
        lowered = (content or "").lower()

        if any(keyword in lowered for keyword in self.AUTH_KEYWORDS):
            hint["type"] = AUTH_BEARER
            token_match = self.TOKEN_FIELD_PATTERN.search(content or "")
            if token_match:
                hint["token_field"] = token_match.group(1)

        if "cookie" in lowered and hint["type"] == AUTH_NONE:
            hint["type"] = AUTH_COOKIE

        # Try to find a login endpoint.
        for endpoint in endpoints:
            if endpoint.method in {"POST", "PUT"} and self.LOGIN_PATH_HINT.search(endpoint.path):
                hint["login_endpoint"] = f"{endpoint.method} {endpoint.path}"
                hint.setdefault("login_endpoint_id", endpoint.endpoint_id)
                hint.setdefault("type", AUTH_BEARER)
                break
        return hint

    def get_request_schema(self, endpoint_id: str) -> RequestBodySchema | None:
        """Retrieve cached request schema for an endpoint."""
        for index in self._index_cache.values():
            if endpoint_id in index.request_schemas:
                return index.request_schemas[endpoint_id]
        return None


__all__ = ["ApiDocParser", "ParsedApiDocIndex", "RequestBodySchema"]
