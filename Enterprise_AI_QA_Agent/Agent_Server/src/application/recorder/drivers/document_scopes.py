"""Native document identities and loss-visible local/global recording sequences."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class DocumentScope:
    document_id: str
    page: Any
    frame: Any
    metadata: dict[str, Any]
    received: set[int] = field(default_factory=set)
    ended: bool = False
    receipt: dict[str, Any] | None = None
    close_receipt: dict[str, Any] | None = None


class RecordingDocumentScopes:
    # No upstream counterpart (checked: recorder.js v1, EventChannel, Playwright
    # expose_binding docs): native document/local seq mapping preserves the PG key.
    def __init__(self):
        self.documents: dict[str, DocumentScope] = {}
        self.current: dict[Any, str] = {}
        self.page_ids: dict[Any, str] = {}
        self.frame_ids: dict[Any, str] = {}
        self.events: dict[tuple[str, int], tuple[int, str]] = {}
        self.next_seq = 0

    def register(self, page, frame, frame_path):
        old = self.current.get(frame)
        if old:
            self.documents[old].ended = True
        document_id = uuid4().hex
        parent = frame.parent_frame
        metadata = {"document_id": document_id,
            "page_id": self.page_ids.setdefault(page, uuid4().hex),
            "frame_id": self.frame_ids.setdefault(frame, uuid4().hex),
            "parent_frame_id": self.frame_ids.setdefault(parent, uuid4().hex) if parent else None,
            "frame_path": frame_path, "document_url": frame.url}
        self.documents[document_id] = DocumentScope(document_id, page, frame, metadata)
        self.current[frame] = document_id
        return document_id

    def owned(self, document_id, page, frame):
        scope = self.documents.get(document_id)
        if scope is None or scope.page != page or scope.frame != frame:
            raise ValueError("Recording document native source mismatch")
        return scope

    def event(self, raw, *, page, frame):
        document_id, local_seq = raw.get("recording_document_id"), raw.get("seq")
        scope = self.owned(document_id, page, frame)
        if not isinstance(local_seq, int) or isinstance(local_seq, bool) or local_seq < 0:
            raise ValueError("Recording document local sequence invalid")
        signature = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = (document_id, local_seq)
        if key in self.events:
            seq, original_hash = self.events[key]
            if original_hash != signature:
                raise ValueError("Recording document event identity cannot change")
            return None
        seq = self.next_seq
        self.next_seq += 1
        self.events[key] = (seq, signature)
        scope.received.add(local_seq)
        page_metadata = {**raw.get("page", {}), "recording_scope": {**scope.metadata, "local_seq": local_seq}}
        target = raw.get("target")
        if target is not None and scope.metadata["parent_frame_id"]:
            target = {**target, "in_iframe": True}
        return {**{name: value for name, value in raw.items() if name != "recording_document_id"},
            "seq": seq, "page": page_metadata, "target": target}

    def activate(self, document_id, *, page, frame):
        scope = self.owned(document_id, page, frame)
        previous = self.current.get(frame)
        if previous and previous != document_id:
            self.documents[previous].ended = True
        self.current[frame] = document_id
        scope.ended = False
        scope.receipt = None

    def confirm(self, receipt, *, page, frame, closed=False):
        scope = self.owned(receipt.get("document_id"), page, frame)
        if closed:
            scope.close_receipt = dict(receipt)
            scope.ended = True
        else:
            scope.receipt = dict(receipt)
            scope.ended = False

    def result(self):
        scopes = []
        for document in self.documents.values():
            receipt = document.close_receipt if document.ended else document.receipt
            limit = receipt.get("next_seq") if receipt else None
            valid = isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0
            confirmed = bool(valid and receipt.get("emitted_count") == limit and not receipt.get("pending")
                and document.received == set(range(limit)))
            scopes.append({**document.metadata, "next_seq": limit, "received_count": len(document.received),
                "emitted_count": receipt.get("emitted_count") if receipt else None,
                "pending": receipt.get("pending") if receipt else None,
                "ended": document.ended, "confirmed": confirmed})
        return {"protocol": "recording-scopes-v2", "next_seq": self.next_seq,
            "emitted_count": self.next_seq, "pending": sum(row["pending"] or 0 for row in scopes),
            "frames_unconfirmed": sum(not row["confirmed"] for row in scopes), "scopes": scopes}
