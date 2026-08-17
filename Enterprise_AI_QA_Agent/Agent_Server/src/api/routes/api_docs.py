from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from src.schemas.api_docs import (
    ApiDocImportIntegrationRequest,
    ApiDocImportUrlRequest,
    ApiDocUpdateRequest,
    ApiDocUploadRequest,
)


router = APIRouter(prefix="/registry/api-docs", tags=["api-docs"])


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
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


@router.patch("/{doc_id}")
async def update_api_doc(doc_id: str, payload: ApiDocUpdateRequest, request: Request):
    try:
        return await request.app.state.api_docs_service.update_document(
            doc_id,
            **payload.model_dump(exclude_unset=True),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc


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
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{doc_id}")
async def delete_api_doc(doc_id: str, request: Request):
    try:
        return await request.app.state.api_docs_service.delete_document(doc_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
