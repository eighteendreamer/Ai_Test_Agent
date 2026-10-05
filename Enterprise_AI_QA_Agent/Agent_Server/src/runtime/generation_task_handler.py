"""Use the existing heartbeat/cancellation lifecycle for independent modules."""
from src.runtime.document_operation_task_handler import DocumentOperationTaskHandler
from src.schemas.case_generation_tasks import GenerationCommand


class GenerationTaskHandler(DocumentOperationTaskHandler):
    _command_type = GenerationCommand
    _task_type = "case_generation"
    _label = "增量生成"
    _log_prefix = "case_generation"
