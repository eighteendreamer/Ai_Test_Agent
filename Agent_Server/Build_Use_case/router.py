"""
Test case generation API routes.
"""
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from Build_Use_case.service import TestCaseService
from database.connection import ExecutionCase, get_active_project_by_id, get_db, resolve_project_context

router = APIRouter(prefix="/api/test-cases", tags=["测试用例"])


class GenerateTestCaseRequest(BaseModel):
    requirement: str
    project_id: Optional[int] = None


class TestCaseResponse(BaseModel):
    id: int
    module: str
    title: str
    precondition: Optional[str]
    steps: List[str]
    expected: str
    keywords: Optional[str]
    priority: str
    case_type: Optional[str]
    stage: Optional[str]
    test_data: Optional[dict]
    created_at: Optional[str]


class UpdateTestCaseRequest(BaseModel):
    module: str
    title: str
    precondition: Optional[str] = None
    steps: List[str]
    expected: str
    keywords: Optional[str] = None
    priority: str
    case_type: Optional[str] = None
    stage: Optional[str] = None


@router.post("/generate")
async def generate_test_cases(request: GenerateTestCaseRequest, db: Session = Depends(get_db)):
    project = resolve_project_context(db, request.project_id)
    if not project:
        raise HTTPException(status_code=400, detail="没有可用的项目，请先创建并启用一个项目")

    result = await TestCaseService.generate_test_cases(
        requirement=request.requirement,
        db=db,
        project_id=project.id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message"))
    return result


@router.get("/list")
def get_test_cases(
    limit: int = 20,
    offset: int = 0,
    module: str = None,
    search: str = None,
    priority: str = None,
    case_type: str = None,
    project_id: int = None,
    db: Session = Depends(get_db),
):
    # 如果指定了 project_id，使用指定的项目
    # 如果没有指定，使用默认项目
    # 如果默认项目也不存在，则查询所有项目的数据（不过滤 project_id）
    project = resolve_project_context(db, project_id)
    
    # 如果有项目，使用项目ID过滤；否则不过滤（查询所有数据）
    filter_project_id = project.id if project else None

    result = TestCaseService.get_test_cases(
        db=db,
        limit=limit,
        offset=offset,
        module=module,
        search=search,
        priority=priority,
        case_type=case_type,
        project_id=filter_project_id,
    )
    return {"success": True, "data": result["data"], "total": result["total"]}


@router.get("/{case_id}")
def get_test_case(case_id: int, db: Session = Depends(get_db)):
    case = TestCaseService.get_test_case_by_id(db=db, case_id=case_id)
    if not case:
        raise HTTPException(status_code=404, detail="测试用例不存在")

    case_obj = db.query(ExecutionCase).filter(ExecutionCase.id == case_id).first()
    if case_obj and case_obj.project_id:
        project = get_active_project_by_id(db, case_obj.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="用例所属项目未启用")
    return {"success": True, "data": case}


@router.put("/{case_id}")
def update_test_case(case_id: int, request: UpdateTestCaseRequest, db: Session = Depends(get_db)):
    case_obj = db.query(ExecutionCase).filter(ExecutionCase.id == case_id).first()
    if not case_obj:
        raise HTTPException(status_code=404, detail="测试用例不存在")

    if case_obj.project_id:
        project = get_active_project_by_id(db, case_obj.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="用例所属项目未启用")

    result = TestCaseService.update_test_case(
        db=db,
        case_id=case_id,
        module=request.module,
        title=request.title,
        precondition=request.precondition,
        steps=request.steps,
        expected=request.expected,
        keywords=request.keywords,
        priority=request.priority,
        case_type=request.case_type,
        stage=request.stage,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=404 if "not found" in result.get("message", "").lower() else 500,
            detail=result.get("message"),
        )
    return result


@router.post("/upload-file")
async def upload_file_and_generate(
    file: UploadFile = File(...),
    project_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    allowed_extensions = [".md", ".txt", ".pdf", ".docx"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式。支持的格式：{', '.join(allowed_extensions)}",
        )

    max_size = 10 * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="文件大小超过10MB限制")

    project = resolve_project_context(db, project_id)
    if not project:
        raise HTTPException(status_code=400, detail="没有可用的项目，请先创建并启用一个项目")

    result = await TestCaseService.process_uploaded_file(
        filename=file.filename,
        content=content,
        db=db,
        project_id=project.id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message"))
    return result
