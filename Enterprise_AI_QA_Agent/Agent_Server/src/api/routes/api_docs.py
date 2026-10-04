from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from src.application.documents.api_docs_service import ApiDocNotFoundError
from src.application.documents.api_doc_store import ApiDocConflictError
from src.schemas.api_docs import (
    ApiDocImportIntegrationRequest,
    ApiDocImportUrlRequest,
    ApiDocUpdateRequest,
    ApiDocUploadRequest,
)

router = APIRouter(prefix="/registry/api-docs", tags=["api-docs"])
logger = logging.getLogger(__name__)


def _raise_api_doc_internal_error(exc: Exception, *, operation: str, doc_id: str | None = None) -> None:
    logger.error(
        "api_doc_operation_failed",
        extra={"operation": operation, "api_doc_id": doc_id, "error_type": type(exc).__name__},
    )
    raise HTTPException(status_code=500, detail="API 文档操作失败。") from exc


@router.get("")
async def list_api_docs(
    request: Request,
    project_id: str | None = None,
    unbound: bool = Query(default=False),
):
    return await request.app.state.api_docs_service.list_documents(
        project_id=project_id,
        unbound=unbound,
    )


@router.get("/{doc_id}")
async def get_api_doc(doc_id: str, request: Request):
    try:
        return await request.app.state.api_docs_service.get_document(doc_id)
    except ApiDocNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected failures at the HTTP boundary.
        _raise_api_doc_internal_error(exc, operation="get", doc_id=doc_id)


@router.post("/upload")
async def upload_api_doc(payload: ApiDocUploadRequest, request: Request):
    try:
        return await request.app.state.api_docs_service.upload_document(
            filename=payload.filename,
            content_base64=payload.content_base64,
            source=payload.source,
            title=payload.title,
            project_id=payload.project_id,
            project_name=payload.project_name,
            project_url=payload.project_url,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:
        _raise_api_doc_internal_error(exc, operation="upload")


@router.get("/{doc_id}/original")
@router.get("/{doc_id}/assets/{asset_id}")
async def download_api_doc_asset(doc_id: str, request: Request, asset_id: str | None = None):
    try:
        asset, content = await request.app.state.api_docs_service.read_asset(doc_id, asset_id)
        return Response(content=content, media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.filename, safe='')}"})
    except ApiDocNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        _raise_api_doc_internal_error(exc, operation="read_asset", doc_id=doc_id)


@router.patch("/{doc_id}")
async def update_api_doc(doc_id: str, payload: ApiDocUpdateRequest, request: Request):
    try:
        return await request.app.state.api_docs_service.update_document(
            doc_id,
            **payload.model_dump(exclude_unset=True),
        )
    except (ApiDocNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApiDocConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:
        _raise_api_doc_internal_error(exc, operation="update", doc_id=doc_id)


@router.post("/import-url")
async def import_api_doc_from_url(payload: ApiDocImportUrlRequest, request: Request):
    try:
        return await request.app.state.api_docs_service.import_document_from_url(
            url=payload.url,
            title=payload.title,
            project_id=payload.project_id,
            project_name=payload.project_name,
            project_url=payload.project_url,
            source=payload.source,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected failures at the HTTP boundary.
        _raise_api_doc_internal_error(exc, operation="import_url")


@router.post("/import-integration")
async def import_api_doc_from_integration(payload: ApiDocImportIntegrationRequest, request: Request):
    try:
        integration = await request.app.state.integration_catalog_service.get_integration(payload.integration_id)
        import_request = await request.app.state.integration_catalog_service.resolve_import_document(
            payload.integration_id,
            override_document_url=payload.document_url,
            import_source_id=payload.import_source_id,
            workspace_id=payload.workspace_id,
        )
        if import_request.mode == "inline":
            if not import_request.filename or not import_request.content_base64:
                raise ValueError("导入源未返回可用的文档内容。")
            return await request.app.state.api_docs_service.upload_document(
                filename=import_request.filename,
                content_base64=import_request.content_base64,
                source=payload.source,
                title=payload.title or import_request.title,
                project_id=payload.project_id,
                project_name=payload.project_name or import_request.project_name,
                project_url=payload.project_url or integration.base_url,
                content_type=import_request.content_type,
            )
        return await request.app.state.api_docs_service.import_document_from_integration(
            integration=integration,
            title=payload.title,
            project_id=payload.project_id,
            project_name=payload.project_name,
            project_url=payload.project_url,
            document_url=import_request.document_url,
            source=payload.source,
            headers=import_request.headers,
            auth=import_request.auth,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected failures at the HTTP boundary.
        _raise_api_doc_internal_error(exc, operation="import_integration")


@router.delete("/{doc_id}")
async def delete_api_doc(doc_id: str, request: Request):
    try:
        return await request.app.state.api_docs_service.delete_document(doc_id)
    except ApiDocNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - sanitize unexpected failures at the HTTP boundary.
        _raise_api_doc_internal_error(exc, operation="delete", doc_id=doc_id)
