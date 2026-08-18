from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from src.schemas.knowledge import KnowledgeGraphResponse, KnowledgeProjectDeleteResponse, KnowledgeProjectSummary


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/projects", response_model=list[KnowledgeProjectSummary])
async def list_knowledge_projects(request: Request):
    try:
        return await request.app.state.knowledge_graph_service.list_projects()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Knowledge graph unavailable: {exc}") from exc


@router.get("/graph", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph(
    request: Request,
    project_id: str = Query(..., min_length=1),
):
    try:
        return await request.app.state.knowledge_graph_service.get_graph(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project graph not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Knowledge graph unavailable: {exc}") from exc


@router.delete("/project", response_model=KnowledgeProjectDeleteResponse)
async def delete_knowledge_project(
    request: Request,
    project_id: str = Query(..., min_length=1),
):
    try:
        return await request.app.state.knowledge_graph_service.delete_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project graph not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Knowledge graph unavailable: {exc}") from exc
