"""
项目管理 API 路由

提供项目的 CRUD 操作、默认项目设置，以及从项目管理平台导入项目。

作者: 程序员Eighteen
"""
import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from database.connection import get_db, Project, get_default_project

router = APIRouter(
    prefix="/api/projects",
    tags=["项目管理"]
)


class ProjectCreate(BaseModel):
    """创建项目请求"""
    name: str
    code: str
    description: Optional[str] = None
    is_default: Optional[int] = 0
    is_active: Optional[int] = 1


class ProjectUpdate(BaseModel):
    """更新项目请求"""
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[int] = None
    is_active: Optional[int] = None


class PlatformProjectImportRequest(BaseModel):
    """从项目管理平台导入项目请求"""
    platform_id: str
    source_id: str | int
    source_name: str
    source_code: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[int] = 0
    is_active: Optional[int] = None


class ProjectResponse(BaseModel):
    """项目响应"""
    id: int
    name: str
    code: str
    description: Optional[str]
    is_default: int
    is_active: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _sanitize_project_code(value: str) -> str:
    """将外部平台代号规范化为本系统可用的项目代码。"""
    if not value:
        return ""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_-")
    return normalized[:50]


def _build_unique_project_name(db: Session, preferred_name: str, current_project_id: Optional[int] = None) -> str:
    """生成唯一项目名，避免与现有项目冲突。"""
    base_name = (preferred_name or "").strip()
    if not base_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")

    candidate = base_name
    suffix = 2
    while True:
        query = db.query(Project).filter(Project.name == candidate)
        if current_project_id is not None:
            query = query.filter(Project.id != current_project_id)
        if not query.first():
            return candidate
        candidate = f"{base_name} ({suffix})"
        suffix += 1


def _build_platform_project_code(platform_id: str, source_id: str | int, source_code: Optional[str] = None) -> str:
    """根据平台和外部产品信息生成稳定的项目代码。"""
    platform_part = _sanitize_project_code(platform_id) or "platform"
    source_part = _sanitize_project_code(source_code or str(source_id)) or str(source_id)
    return _sanitize_project_code(f"{platform_part}_{source_part}")[:50]


def _build_platform_project_description(
    platform_id: str,
    source_id: str | int,
    source_code: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """拼装导入项目的来源描述。"""
    lines: List[str] = []
    if description and description.strip():
        lines.append(description.strip())
    lines.append(f"来源平台: {platform_id}")
    lines.append(f"来源产品ID: {source_id}")
    if source_code:
        lines.append(f"来源代号: {source_code}")
    return "\n".join(lines)


@router.get("/list")
def list_projects(
    is_active: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """获取项目列表"""
    query = db.query(Project)
    
    if is_active is not None:
        query = query.filter(Project.is_active == is_active)
    
    projects = query.order_by(Project.is_default.desc(), Project.id.asc()).all()
    
    return {
        "success": True,
        "data": [
            {
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "description": p.description,
                "is_default": p.is_default,
                "is_active": p.is_active,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None
            }
            for p in projects
        ]
    }


@router.get("/default")
def get_default(db: Session = Depends(get_db)):
    """获取默认项目"""
    project = get_default_project(db)
    if not project:
        return {
            "success": False,
            "message": "没有可用的默认项目，请先创建并启用一个项目",
            "data": None,
        }
    
    return {
        "success": True,
        "data": {
            "id": project.id,
            "name": project.name,
            "code": project.code,
            "description": project.description,
            "is_default": project.is_default,
            "is_active": project.is_active
        }
    }


@router.post("/import-platform-product")
def import_platform_product(
    request: PlatformProjectImportRequest,
    db: Session = Depends(get_db)
):
    """将项目管理平台中的产品导入为本地项目。"""
    source_name = (request.source_name or "").strip()
    if not source_name:
        raise HTTPException(status_code=400, detail="来源产品名称不能为空")

    requested_active = request.is_active
    requested_default = request.is_default or 0
    if requested_default == 1 and requested_active == 0:
        raise HTTPException(status_code=400, detail="默认项目必须为启用状态")

    stable_code = _build_platform_project_code(
        request.platform_id,
        request.source_id,
        request.source_code,
    )
    if not stable_code:
        raise HTTPException(status_code=400, detail="无法生成有效的项目代码")

    existing_project = db.query(Project).filter(Project.code == stable_code).first()
    description = _build_platform_project_description(
        request.platform_id,
        request.source_id,
        request.source_code,
        request.description,
    )

    if existing_project:
        existing_project.name = source_name
        existing_project.description = description
        if requested_active is not None:
            existing_project.is_active = requested_active
        if requested_default == 1:
            db.query(Project).update({Project.is_default: 0})
            existing_project.is_default = 1
        existing_project.updated_at = datetime.now()
        db.commit()
        db.refresh(existing_project)
        return {
            "success": True,
            "message": f"项目已同步更新：{existing_project.name}",
            "data": {
                "id": existing_project.id,
                "name": existing_project.name,
                "code": existing_project.code,
                "created": False,
            }
        }

    project_name = _build_unique_project_name(db, source_name)
    project = Project(
        name=project_name,
        code=stable_code,
        description=description,
        is_default=requested_default,
        is_active=requested_active if requested_active is not None else 0,
    )

    if project.is_default == 1:
        db.query(Project).update({Project.is_default: 0})

    db.add(project)
    db.commit()
    db.refresh(project)

    return {
        "success": True,
        "message": f"项目导入成功：{project.name}",
        "data": {
            "id": project.id,
            "name": project.name,
            "code": project.code,
            "created": True,
        }
    }


@router.get("/{project_id}")
def get_project(
    project_id: int,
    db: Session = Depends(get_db)
):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    return {
        "success": True,
        "data": {
            "id": project.id,
            "name": project.name,
            "code": project.code,
            "description": project.description,
            "is_default": project.is_default,
            "is_active": project.is_active,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None
        }
    }


@router.post("/create")
def create_project(
    request: ProjectCreate,
    db: Session = Depends(get_db)
):
    """创建项目"""
    if request.is_default == 1 and request.is_active == 0:
        raise HTTPException(status_code=400, detail="默认项目必须为启用状态")

    # 检查项目名称是否已存在
    existing_name = db.query(Project).filter(Project.name == request.name).first()
    if existing_name:
        raise HTTPException(status_code=400, detail="项目名称已存在")
    
    # 检查项目代码是否已存在
    existing_code = db.query(Project).filter(Project.code == request.code).first()
    if existing_code:
        raise HTTPException(status_code=400, detail="项目代码已存在")
    
    # 创建项目
    project = Project(
        name=request.name,
        code=request.code,
        description=request.description,
        is_default=request.is_default or 0,
        is_active=request.is_active if request.is_active is not None else 1
    )

    if project.is_default == 1:
        db.query(Project).update({Project.is_default: 0})

    db.add(project)
    db.commit()
    db.refresh(project)
    
    return {
        "success": True,
        "message": "项目创建成功",
        "data": {
            "id": project.id,
            "name": project.name,
            "code": project.code
        }
    }


@router.put("/{project_id}")
def update_project(
    project_id: int,
    request: ProjectUpdate,
    db: Session = Depends(get_db)
):
    """更新项目"""
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 更新字段
    if request.name is not None:
        # 检查名称是否与其他项目重复
        existing = db.query(Project).filter(
            Project.name == request.name,
            Project.id != project_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="项目名称已存在")
        project.name = request.name
    
    if request.code is not None:
        # 检查代码是否与其他项目重复
        existing = db.query(Project).filter(
            Project.code == request.code,
            Project.id != project_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="项目代码已存在")
        project.code = request.code
    
    if request.description is not None:
        project.description = request.description

    if request.is_active is not None:
        project.is_active = request.is_active

    if request.is_default is not None:
        if request.is_default == 1 and project.is_active == 0:
            raise HTTPException(status_code=400, detail="不能将未启用的项目设为默认")
        if request.is_default == 1:
            db.query(Project).update({Project.is_default: 0})
            project.is_default = 1
        else:
            project.is_default = 0

    if project.is_default == 1 and project.is_active == 0:
        raise HTTPException(status_code=400, detail="默认项目必须为启用状态")

    project.updated_at = datetime.now()
    
    db.commit()
    db.refresh(project)
    
    return {
        "success": True,
        "message": "项目更新成功",
        "data": {
            "id": project.id,
            "name": project.name,
            "code": project.code
        }
    }


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db)
):
    """删除项目"""
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 不允许删除默认项目
    if project.is_default == 1:
        raise HTTPException(status_code=400, detail="不能删除默认项目，请先设置其他项目为默认")
    
    # 检查是否有关联数据
    from database.connection import ExecutionCase, ApiSpec, OneclickSession
    
    has_cases = db.query(ExecutionCase).filter(ExecutionCase.project_id == project_id).count() > 0
    has_specs = db.query(ApiSpec).filter(ApiSpec.project_id == project_id).count() > 0
    has_sessions = db.query(OneclickSession).filter(OneclickSession.project_id == project_id).count() > 0
    
    if has_cases or has_specs or has_sessions:
        raise HTTPException(
            status_code=400,
            detail="该项目下有关联数据，无法删除。请先清理或转移数据。"
        )
    
    db.delete(project)
    db.commit()
    
    return {
        "success": True,
        "message": "项目删除成功"
    }


@router.api_route("/{project_id}/set-default", methods=["POST", "PUT"])
def set_default_project(
    project_id: int,
    db: Session = Depends(get_db)
):
    """设置默认项目"""
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if project.is_active == 0:
        raise HTTPException(status_code=400, detail="不能将未启用的项目设为默认")
    
    # 取消其他项目的默认状态
    db.query(Project).update({Project.is_default: 0})
    
    # 设置当前项目为默认
    project.is_default = 1
    project.updated_at = datetime.now()
    
    db.commit()
    
    return {
        "success": True,
        "message": f"已将 '{project.name}' 设为默认项目"
    }


@router.get("/{project_id}/stats")
def get_project_stats(
    project_id: int,
    db: Session = Depends(get_db)
):
    """获取项目统计信息"""
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    from database.connection import (
        ExecutionCase, ApiSpec, ApiSpecVersion, OneclickSession,
        TestRecord, TestReport, BugReport, TestEnvironment
    )
    
    # 统计各类数据
    stats = {
        "test_cases": db.query(ExecutionCase).filter(ExecutionCase.project_id == project_id).count(),
        "api_specs": db.query(ApiSpec).filter(ApiSpec.project_id == project_id).count(),
        "api_versions": db.query(ApiSpecVersion).filter(ApiSpecVersion.project_id == project_id).count(),
        "oneclick_sessions": db.query(OneclickSession).filter(OneclickSession.project_id == project_id).count(),
        "test_records": db.query(TestRecord).filter(TestRecord.project_id == project_id).count(),
        "test_reports": db.query(TestReport).filter(TestReport.project_id == project_id).count(),
        "bug_reports": db.query(BugReport).filter(BugReport.project_id == project_id).count(),
        "test_environments": db.query(TestEnvironment).filter(TestEnvironment.project_id == project_id).count(),
    }
    
    return {
        "success": True,
        "data": {
            "project": {
                "id": project.id,
                "name": project.name,
                "code": project.code
            },
            "stats": stats
        }
    }
