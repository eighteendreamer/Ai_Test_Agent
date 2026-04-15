"""
用例模板配置 API 路由
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
from sqlalchemy.orm import Session

from database.connection import get_db
from Project_manage.case_template.service import CaseTemplateService

router = APIRouter(
    prefix="/api/case-template",
    tags=["用例模板配置"]
)


class TemplateUpdateRequest(BaseModel):
    template_name: Optional[str] = None
    fields: Optional[List[Any]] = None
    priority_options: Optional[List[Any]] = None
    case_type_options: Optional[List[Any]] = None
    stage_options: Optional[List[Any]] = None
    extra_prompt: Optional[str] = None


@router.get("/active")
def get_active_template(db: Session = Depends(get_db)):
    """获取当前启用的模板（含系统默认回退）"""
    tpl = CaseTemplateService.get_template_for_llm(db)
    return {"success": True, "data": tpl}


@router.get("/list")
def list_templates(db: Session = Depends(get_db)):
    """列出所有已保存的模板"""
    return {"success": True, "data": CaseTemplateService.list_templates(db)}


@router.post("/sync/{platform_id}")
def sync_from_platform(platform_id: str, db: Session = Depends(get_db)):
    """从指定平台同步用例字段结构为全局模板"""
    try:
        result = CaseTemplateService.sync_from_platform(db, platform_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{template_id}")
def update_template(template_id: int, body: TemplateUpdateRequest, db: Session = Depends(get_db)):
    """更新模板字段配置"""
    try:
        data = body.model_dump(exclude_none=True)
        tpl = CaseTemplateService.update_template(db, template_id, data)
        return {"success": True, "data": tpl, "message": "模板更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/reset")
def reset_to_default(db: Session = Depends(get_db)):
    """重置为系统默认模板"""
    return CaseTemplateService.reset_to_default(db)
