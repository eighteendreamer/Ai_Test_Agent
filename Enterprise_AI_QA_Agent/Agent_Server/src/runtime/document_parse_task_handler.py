"""Typed parsing delivery over the shared leased document Worker lifecycle."""
from __future__ import annotations

from src.runtime.document_operation_task_handler import DocumentOperationTaskHandler
from src.schemas.document_parsing import DocumentParseCommand


class DocumentParseTaskHandler(DocumentOperationTaskHandler):
    _command_type = DocumentParseCommand
    _task_type = "document_parsing"
    _label = "解析"
    _log_prefix = "document_parse"
