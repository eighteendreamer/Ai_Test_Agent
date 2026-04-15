"""
One-click testing and skill management API routes.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import (
    OneclickSession,
    TestEnvironment,
    get_active_project_by_id,
    get_db,
    resolve_project_context,
)
from OneClick_Test.service import OneClickService
from OneClick_Test.session import SessionManager
from OneClick_Test.skill_manager import SkillManager

router = APIRouter(prefix="/api", tags=["OneClick & Skills"])


class StartSessionRequest(BaseModel):
    user_input: str
    skill_ids: Optional[List[int]] = None
    project_id: Optional[int] = None


class ConfirmRequest(BaseModel):
    session_id: int
    confirmed_cases: Optional[List[dict]] = None


class ConfirmTreeRequest(BaseModel):
    session_id: int
    selections: Optional[Dict[str, bool]] = None


class InstallSkillRequest(BaseModel):
    slug: str


class ToggleSkillRequest(BaseModel):
    is_active: bool


class TestEnvRequest(BaseModel):
    name: str
    base_url: str
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    extra_credentials: Optional[dict] = None
    description: Optional[str] = None
    is_default: Optional[int] = 0


def _ensure_session_project_available(db: Session, session_id: int) -> Optional[OneclickSession]:
    session = db.query(OneclickSession).filter(OneclickSession.id == session_id).first()
    if not session:
        return None
    if session.project_id:
        project = get_active_project_by_id(db, session.project_id)
        if not project:
            return None
    return session


@router.post("/oneclick/start")
async def start_oneclick(req: StartSessionRequest, db: Session = Depends(get_db)):
    project = resolve_project_context(db, req.project_id)
    if not project:
        return {"success": False, "message": "没有可用的项目，请先创建并启用一个项目"}
    return await OneClickService.start_session(db, req.user_input, req.skill_ids, project.id)


@router.get("/oneclick/session/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)):
    detail = OneClickService.get_session_detail(db, session_id)
    if not detail:
        return {"success": False, "message": "会话不存在"}

    session = _ensure_session_project_available(db, session_id)
    if session is None:
        return {"success": False, "message": "会话所属项目未启用或不存在"}
    return {"success": True, "data": detail}


@router.post("/oneclick/confirm")
async def confirm_execute(req: ConfirmRequest, db: Session = Depends(get_db)):
    session = _ensure_session_project_available(db, req.session_id)
    if session is None:
        return {"success": False, "message": "会话不存在或所属项目未启用"}
    return await OneClickService.confirm_and_execute(db, req.session_id, req.confirmed_cases)


@router.post("/oneclick/confirm-tree")
async def confirm_tree_execute(req: ConfirmTreeRequest, db: Session = Depends(get_db)):
    session = _ensure_session_project_available(db, req.session_id)
    if session is None:
        return {"success": False, "message": "会话不存在或所属项目未启用"}
    return await OneClickService.confirm_task_tree(db, req.session_id, req.selections or {})


@router.post("/oneclick/stop")
async def stop_session(session_id: int = Body(..., embed=True), db: Session = Depends(get_db)):
    session = _ensure_session_project_available(db, session_id)
    if session is None:
        return {"success": False, "message": "会话不存在或所属项目未启用"}
    return await OneClickService.stop_session(db, session_id)


@router.get("/oneclick/history")
def get_history(page: int = 1, page_size: int = 20, project_id: int = None, db: Session = Depends(get_db)):
    project = resolve_project_context(db, project_id)
    if not project:
        return {"success": True, "data": {"items": [], "total": 0, "page": page, "page_size": page_size}}
    data = SessionManager.list_sessions(db, page, page_size, project.id)
    return {"success": True, "data": data}


@router.get("/skills/list")
def list_skills(category: str = None, db: Session = Depends(get_db)):
    return {"success": True, "data": SkillManager.list_skills(db, category)}


@router.post("/skills/install")
async def install_skill(req: InstallSkillRequest, db: Session = Depends(get_db)):
    return await SkillManager.install_skill(db, req.slug)


@router.post("/skills/upload")
async def upload_skill(
    file: UploadFile = File(...),
    skill_name: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".md"):
        return {"success": False, "message": "仅支持 .md 文件"}
    content = await file.read()
    return SkillManager.install_skill_from_file(db, file.filename, content, skill_name, description)


@router.delete("/skills/{skill_id}")
def uninstall_skill(skill_id: int, db: Session = Depends(get_db)):
    return SkillManager.uninstall_skill(db, skill_id)


@router.get("/skills/{skill_id}/detail")
def get_skill_detail(skill_id: int, db: Session = Depends(get_db)):
    detail = SkillManager.get_skill_detail(db, skill_id)
    if not detail:
        return {"success": False, "message": "Skill 不存在"}
    return {"success": True, "data": detail}


@router.put("/skills/{skill_id}/toggle")
def toggle_skill(skill_id: int, req: ToggleSkillRequest, db: Session = Depends(get_db)):
    return SkillManager.toggle_skill(db, skill_id, req.is_active)


@router.get("/skills/search")
async def search_skills(q: str = ""):
    if not q:
        return {"success": False, "message": "请输入搜索关键词"}
    return await SkillManager.search_skills(q)


@router.get("/test-env/list")
def list_test_envs(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(TestEnvironment).filter(TestEnvironment.is_active == 1)
    if project_id is not None:
        query = query.filter(TestEnvironment.project_id == project_id)

    envs = query.order_by(
        TestEnvironment.is_default.desc(),
        TestEnvironment.created_at.desc(),
    ).all()
    return {
        "success": True,
        "data": [
            {
                "id": env.id,
                "name": env.name,
                "base_url": env.base_url,
                "login_url": env.login_url,
                "username": env.username,
                "password": env.password,
                "extra_credentials": env.extra_credentials,
                "description": env.description,
                "is_default": env.is_default,
                "is_active": env.is_active,
                "created_at": env.created_at.isoformat() if env.created_at else None,
            }
            for env in envs
        ],
    }


@router.post("/test-env/create")
def create_test_env(req: TestEnvRequest, db: Session = Depends(get_db)):
    if req.is_default:
        db.query(TestEnvironment).filter(TestEnvironment.is_default == 1).update({"is_default": 0})

    env = TestEnvironment(
        name=req.name,
        base_url=req.base_url,
        login_url=req.login_url,
        username=req.username,
        password=req.password,
        extra_credentials=req.extra_credentials,
        description=req.description,
        is_default=req.is_default or 0,
    )
    db.add(env)
    db.commit()
    db.refresh(env)
    return {"success": True, "data": {"id": env.id}, "message": "创建成功"}


@router.put("/test-env/{env_id}")
def update_test_env(env_id: int, req: TestEnvRequest, db: Session = Depends(get_db)):
    env = db.query(TestEnvironment).filter(TestEnvironment.id == env_id).first()
    if not env:
        return {"success": False, "message": "环境不存在"}

    if req.is_default and not env.is_default:
        db.query(TestEnvironment).filter(TestEnvironment.is_default == 1).update({"is_default": 0})

    env.name = req.name
    env.base_url = req.base_url
    env.login_url = req.login_url
    env.username = req.username
    env.password = req.password
    env.extra_credentials = req.extra_credentials
    env.description = req.description
    env.is_default = req.is_default or 0
    db.commit()
    return {"success": True, "message": "更新成功"}


@router.delete("/test-env/{env_id}")
def delete_test_env(env_id: int, db: Session = Depends(get_db)):
    env = db.query(TestEnvironment).filter(TestEnvironment.id == env_id).first()
    if not env:
        return {"success": False, "message": "环境不存在"}
    db.delete(env)
    db.commit()
    return {"success": True, "message": "删除成功"}


@router.put("/test-env/{env_id}/set-default")
def set_default_env(env_id: int, db: Session = Depends(get_db)):
    env = db.query(TestEnvironment).filter(TestEnvironment.id == env_id).first()
    if not env:
        return {"success": False, "message": "环境不存在"}

    db.query(TestEnvironment).filter(TestEnvironment.is_default == 1).update({"is_default": 0})
    env.is_default = 1
    db.commit()
    return {"success": True, "message": "已设为默认"}
