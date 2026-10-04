"""Typed comparison delivery over the existing leased document Worker lifecycle."""
from __future__ import annotations

from src.runtime.document_operation_task_handler import DocumentOperationTaskHandler
from src.schemas.document_differences import DocumentDifferenceCommand


class DocumentDifferenceTaskHandler(DocumentOperationTaskHandler):
    _command_type = DocumentDifferenceCommand
    _task_type = "document_difference"
    _label = "比较"
    _log_prefix = "document_difference"
