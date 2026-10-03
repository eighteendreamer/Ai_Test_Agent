from __future__ import annotations

import logging
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from src.application.test_cases.import_parser import build_import_template
from src.schemas.case_import import (
    CaseImportBatchView, CaseImportOptions, CaseImportSettings, CaseImportSettingsRecord,
    CaseImportSheet, CaseImportSourceRecord, CaseImportUploadRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects/{project_id}/test-case-imports", tags=["test-case-imports"])


async def _call(request, project_id, operation, *args, invalid_status=409):
    try:
        service = request.app.state.case_import_service
        return await getattr(service, operation)(str(project_id), *args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        logger.warning("case_import_request_rejected", extra={"project_id": str(project_id), "operation": operation, "error_type": type(exc).__name__})
        raise HTTPException(status_code=invalid_status, detail=str(exc)) from None
    except Exception as exc:
        logger.error("case_import_request_failed", extra={"project_id": str(project_id), "operation": operation, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="用例导入服务暂时不可用，请保留来源或批次编号后重试") from None


@router.get("/settings", response_model=CaseImportSettingsRecord)
async def get_settings(project_id: UUID, request: Request):
    return await _call(request, project_id, "get_settings")


@router.put("/settings", response_model=CaseImportSettingsRecord)
async def save_settings(project_id: UUID, payload: CaseImportSettings, request: Request):
    return await _call(request, project_id, "save_settings", payload)


@router.get("/templates/{layout}")
async def get_template(project_id: UUID, layout: str, request: Request):
    await _call(request, project_id, "get_settings")
    try:
        content = build_import_template(layout)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="test-cases-{layout}.xlsx"'})


@router.post("/sources", response_model=CaseImportSourceRecord, status_code=201)
async def upload(project_id: UUID, payload: CaseImportUploadRequest, request: Request):
    return await _call(request, project_id, "upload", payload, invalid_status=422)


@router.get("/sources/{source_id}", response_model=CaseImportSourceRecord)
async def get_source(project_id: UUID, source_id: UUID, request: Request):
    return await _call(request, project_id, "get_source", str(source_id))


@router.get("/sources/{source_id}/original")
async def download_original(project_id: UUID, source_id: UUID, request: Request):
    source, content = await _call(request, project_id, "read_original", str(source_id))
    return Response(content=content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(source.filename, safe='')}"})


@router.get("/sources/{source_id}/sheets", response_model=list[CaseImportSheet])
async def inspect_sheets(project_id: UUID, source_id: UUID, request: Request, header_row: int = Query(default=1, ge=1, le=50)):
    return await _call(request, project_id, "inspect", str(source_id), header_row, invalid_status=422)


@router.post("/sources/{source_id}/previews", response_model=CaseImportBatchView)
async def preview(project_id: UUID, source_id: UUID, payload: CaseImportOptions, request: Request):
    return await _call(request, project_id, "preview", str(source_id), payload)


@router.get("/batches/{batch_id}", response_model=CaseImportBatchView)
async def get_batch(project_id: UUID, batch_id: UUID, request: Request):
    return await _call(request, project_id, "get_batch", str(batch_id))


@router.post("/batches/{batch_id}/commit", response_model=CaseImportBatchView)
async def commit(project_id: UUID, batch_id: UUID, request: Request):
    return await _call(request, project_id, "commit", str(batch_id))
