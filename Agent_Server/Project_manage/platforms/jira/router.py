from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import get_db
from Project_manage.platforms.jira.service import JiraBugService

router = APIRouter(
    prefix="/api/jira",
    tags=["Jira Integration"],
)


class BugPushRequest(BaseModel):
    bug_ids: list[int]
    project_key: str
    issue_type: Optional[str] = "Bug"
    priority_name: Optional[str] = None


@router.post("/bugs/push")
async def push_bugs(body: BugPushRequest, db: Session = Depends(get_db)):
    try:
        if len(body.bug_ids) == 1:
            result = await JiraBugService.push_bug(
                db=db,
                bug_id=body.bug_ids[0],
                project_key=body.project_key,
                issue_type=body.issue_type or "Bug",
                priority_name=body.priority_name,
            )
        else:
            result = await JiraBugService.batch_push_bugs(
                db=db,
                bug_ids=body.bug_ids,
                project_key=body.project_key,
                issue_type=body.issue_type or "Bug",
                priority_name=body.priority_name,
            )
        return {"success": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/bugs/sync")
async def sync_bug_status(bug_id: Optional[int] = None, db: Session = Depends(get_db)):
    try:
        result = await JiraBugService.sync_bug_status(db, bug_id)
        return {"success": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
