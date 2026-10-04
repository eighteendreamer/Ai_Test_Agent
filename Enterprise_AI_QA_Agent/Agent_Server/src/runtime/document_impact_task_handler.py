"""Impact coordination over the existing leased document Worker lifecycle."""
from __future__ import annotations

from src.runtime.document_operation_task_handler import DocumentOperationTaskHandler
from src.schemas.document_impact import DocumentImpactCommand


class DocumentImpactTaskHandler(DocumentOperationTaskHandler):
    _command_type = DocumentImpactCommand
    _task_type = "document_impact"
    _label = "影响分析"
    _log_prefix = "document_impact"
