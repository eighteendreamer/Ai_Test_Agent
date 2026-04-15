"""
Bug analysis API routes.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from Bug_Analysis.service import BugAnalysisService
from database.connection import BugReport, get_active_project_by_id, get_db, resolve_project_context

router = APIRouter(prefix="/api/bugs", tags=["Bug 管理"])


@router.get("/list")
def get_bug_reports(
    limit: int = 20,
    offset: int = 0,
    status: str = None,
    severity: str = None,
    project_id: int = None,
    db: Session = Depends(get_db),
):
    project = resolve_project_context(db, project_id)
    if not project:
        return {"success": True, "data": [], "total": 0}

    result = BugAnalysisService.get_bug_reports(
        db=db,
        limit=limit,
        offset=offset,
        status=status,
        severity=severity,
        project_id=project.id,
    )
    return {"success": True, "data": result["data"], "total": result["total"]}


@router.get("/{bug_id}")
def get_bug_report(bug_id: int, db: Session = Depends(get_db)):
    from Project_manage.platforms.jira.models import JiraBugLink, ensure_jira_bug_links_table

    ensure_jira_bug_links_table()
    bug = db.query(BugReport).filter(BugReport.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug 报告不存在")

    if bug.project_id:
        project = get_active_project_by_id(db, bug.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="Bug 所属项目未启用")

    jira_link = db.query(JiraBugLink).filter(JiraBugLink.bug_id == bug.id).first()
    return {
        "success": True,
        "data": {
            "id": bug.id,
            "bug_name": bug.bug_name,
            "test_case_id": bug.test_case_id,
            "test_record_id": bug.test_record_id,
            "error_type": bug.error_type,
            "severity_level": bug.severity_level,
            "status": bug.status,
            "zentao_bug_id": bug.zentao_bug_id,
            "jira_issue_key": jira_link.issue_key if jira_link else None,
            "location_url": bug.location_url,
            "reproduce_steps": bug.reproduce_steps,
            "result_feedback": bug.result_feedback,
            "expected_result": bug.expected_result,
            "actual_result": bug.actual_result,
            "description": bug.description,
            "screenshot_path": bug.screenshot_path,
            "case_type": bug.case_type,
            "execution_mode": bug.execution_mode,
            "created_at": bug.created_at.isoformat() if bug.created_at else None,
            "updated_at": bug.updated_at.isoformat() if bug.updated_at else None,
        },
    }


@router.put("/{bug_id}/status")
def update_bug_status(bug_id: int, status: str, db: Session = Depends(get_db)):
    bug = db.query(BugReport).filter(BugReport.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug 报告不存在")

    if bug.project_id:
        project = get_active_project_by_id(db, bug.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="Bug 所属项目未启用")

    valid_statuses = ["待处理", "已确认", "已修复", "已关闭"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"无效状态，有效值: {valid_statuses}")

    bug.status = status
    db.commit()
    return {"success": True, "message": f"Bug 状态已更新为: {status}"}
