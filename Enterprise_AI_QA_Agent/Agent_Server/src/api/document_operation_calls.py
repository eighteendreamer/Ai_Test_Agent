"""Project-bound document service calls with consistent submission recovery errors."""
from __future__ import annotations

import logging

from fastapi import HTTPException
from src.application.documents.api_docs_service import ApiDocNotFoundError

logger = logging.getLogger(__name__)


async def call_document_operation(request, project_id, service_name, operation, *args, log_prefix, label, **kwargs):
    try:
        service = getattr(request.app.state, service_name)
        return await getattr(service, operation)(str(project_id), *args, **kwargs)
    except (KeyError, ApiDocNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning(f"{log_prefix}_request_rejected", extra={"project_id": str(project_id), "operation": operation})
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.error(f"{log_prefix}_service_unavailable", extra={"project_id": str(project_id),
            "operation": operation, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail=f"文档{label}服务暂时不可用；提交结果未知时，请按原请求身份查询或重试") from None
