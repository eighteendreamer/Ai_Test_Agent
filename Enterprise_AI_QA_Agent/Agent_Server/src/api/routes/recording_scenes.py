from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Query, Request
from src.schemas.recording_scene import SceneBundle, SceneCreateRequest, SceneVersionRequest

router = APIRouter(prefix="/scenes/projects/{project_id}",tags=["recording-scenes"])
logger = logging.getLogger(__name__)


async def _call(request, method, *args):
    service = getattr(request.app.state,"recording_scene_service",None)
    if service is None: raise HTTPException(status_code=503,detail="recording scene service not initialized")
    try:
        return await getattr(service,method)(*args)
    except KeyError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("recording_scene_request_rejected",extra={"operation":method,"project_id":args[0],"reason":str(exc)})
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post("",response_model=SceneBundle,status_code=201)
async def create_scene(project_id: str, payload: SceneCreateRequest, request: Request):
    return await _call(request,"create",project_id,payload)


@router.get("")
async def list_scenes(project_id: str,request: Request,limit: int=Query(default=50,ge=1,le=200),offset: int=Query(default=0,ge=0)):
    return await _call(request,"list",project_id,limit,offset)


@router.get("/by-request/{request_id}",response_model=SceneBundle | None)
async def get_creation(project_id: str,request_id: str,request: Request):
    return await _call(request,"by_request",project_id,request_id)


@router.get("/{scene_id}",response_model=SceneBundle)
async def get_scene(project_id: str,scene_id: str,request: Request,version_id: str | None=Query(default=None,min_length=1)):
    return await _call(request,"get",project_id,scene_id,version_id)


@router.post("/{scene_id}/versions",response_model=SceneBundle,status_code=201)
async def append_version(project_id: str,scene_id: str,payload: SceneVersionRequest,request: Request):
    return await _call(request,"update",project_id,scene_id,payload)


@router.get("/{scene_id}/versions")
async def list_versions(project_id: str,scene_id: str,request: Request):
    return await _call(request,"versions",project_id,scene_id)


@router.get("/{scene_id}/versions/by-request/{request_id}",response_model=SceneBundle | None)
async def recover_version(project_id: str,scene_id: str,request_id: str,request: Request):
    return await _call(request,"version_by_request",project_id,scene_id,request_id)


@router.post("/{scene_id}/versions/{version_id}/case-draft",status_code=201)
async def convert_scene(project_id: str,scene_id: str,version_id: str,request: Request):
    return await _call(request,"create_draft",project_id,scene_id,version_id)


@router.get("/{scene_id}/versions/{version_id}/case-draft")
async def recover_draft(project_id: str,scene_id: str,version_id: str,request: Request):
    return await _call(request,"get_draft",project_id,scene_id,version_id)
