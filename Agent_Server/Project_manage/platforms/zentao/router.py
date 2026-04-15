"""
禅道集成 API 路由

提供 Bug 推送/同步、用例导入等接口。
配置管理已统一到项目管理平台总控制台。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from database.connection import get_db
from Project_manage.platforms.zentao.service import ZentaoBugService, ZentaoCaseService

router = APIRouter(
    prefix="/api/zentao",
    tags=["禅道集成"]
)


class BugPushRequest(BaseModel):
    bug_ids: list[int]
    product_id: Optional[int] = None
    severity: int = 3
    pri: int = 3


class CaseImportRequest(BaseModel):
    product_id: Optional[int] = None
    suite_id: Optional[int] = None
    case_ids: Optional[list[int]] = None
    project_id: int = 1  # 项目ID，默认为1
    limit: Optional[int] = None
    concurrency: Optional[int] = 5


@router.post("/bugs/push")
async def push_bugs(body: BugPushRequest, db: Session = Depends(get_db)):
    """将 Bug 推送到禅道"""
    try:
        if len(body.bug_ids) == 1:
            result = await ZentaoBugService.push_bug(
                db, body.bug_ids[0], body.product_id, body.severity, body.pri
            )
        else:
            result = await ZentaoBugService.batch_push_bugs(
                db, body.bug_ids, body.product_id
            )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bugs/sync")
async def sync_bug_status(bug_id: Optional[int] = None, db: Session = Depends(get_db)):
    """从禅道同步 Bug 状态"""
    try:
        result = await ZentaoBugService.sync_bug_status(db, bug_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/import")
async def import_cases(body: CaseImportRequest, db: Session = Depends(get_db)):
    """从禅道导入测试用例（支持增量导入和项目隔离）"""
    try:
        result = await ZentaoCaseService.import_cases(
            db, body.product_id, body.suite_id, body.case_ids,
            project_id=body.project_id,
            limit=body.limit, concurrency=body.concurrency or 5
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/products")
async def list_products(db: Session = Depends(get_db)):
    """获取禅道产品列表"""
    try:
        products = await ZentaoCaseService.list_products(db)
        return {"success": True, "data": products}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
