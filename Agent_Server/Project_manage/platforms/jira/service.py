import logging
from typing import Optional

from sqlalchemy.orm import Session

from database.connection import BugReport, ProjectPlatformConfig
from Project_manage.platforms.jira.client import JiraClient
from Project_manage.platforms.jira.models import JiraBugLink, ensure_jira_bug_links_table

logger = logging.getLogger(__name__)


def _get_jira_config(db: Session) -> Optional[ProjectPlatformConfig]:
    return db.query(ProjectPlatformConfig).filter(
        ProjectPlatformConfig.platform_id == "jira",
        ProjectPlatformConfig.is_active == 1,
    ).first()


def _build_client(cfg: ProjectPlatformConfig) -> JiraClient:
    return JiraClient(
        base_url=cfg.base_url,
        account=cfg.account,
        api_token=cfg.api_token or cfg.password or "",
        api_version=cfg.api_version or "3",
    )


class JiraBugService:

    @staticmethod
    async def push_bug(
        db: Session,
        bug_id: int,
        project_key: str,
        issue_type: str = "Bug",
        priority_name: str | None = None,
    ) -> dict:
        ensure_jira_bug_links_table()

        cfg = _get_jira_config(db)
        if not cfg:
            raise ValueError("请先在项目管理平台总控制台配置并激活 Jira 连接")

        if not project_key:
            raise ValueError("推送到 Jira 时必须指定项目 Key")

        bug = db.query(BugReport).filter(BugReport.id == bug_id).first()
        if not bug:
            raise ValueError("Bug 不存在")

        existing_link = db.query(JiraBugLink).filter(JiraBugLink.bug_id == bug_id).first()
        if existing_link:
            return {
                "success": False,
                "message": f"该 Bug 已推送到 Jira ({existing_link.issue_key})",
                "jira_issue_key": existing_link.issue_key,
            }

        fields = {
            "project": {"key": project_key},
            "summary": f"[AI Test Agent] {bug.bug_name}",
            "issuetype": {"name": issue_type or "Bug"},
            "labels": ["ai-test-agent"],
            "description": _build_jira_description(bug, cfg.api_version or "3"),
        }
        if priority_name:
            fields["priority"] = {"name": priority_name}

        client = _build_client(cfg)
        try:
            result = client.create_issue(fields)
        finally:
            client.close()

        issue_key = result.get("key")
        if not issue_key:
            issue_id = result.get("id")
            raise ValueError(f"Jira 响应中未返回 issue key: {result or issue_id}")

        link = JiraBugLink(
            bug_id=bug.id,
            issue_key=issue_key,
            project_key=project_key,
        )
        db.add(link)
        db.commit()

        return {
            "success": True,
            "jira_issue_key": issue_key,
            "message": f"Bug 已成功推送到 Jira ({issue_key})",
        }

    @staticmethod
    async def batch_push_bugs(
        db: Session,
        bug_ids: list[int],
        project_key: str,
        issue_type: str = "Bug",
        priority_name: str | None = None,
    ) -> dict:
        results = []
        for bug_id in bug_ids:
            try:
                result = await JiraBugService.push_bug(
                    db=db,
                    bug_id=bug_id,
                    project_key=project_key,
                    issue_type=issue_type,
                    priority_name=priority_name,
                )
                results.append({"bug_id": bug_id, **result})
            except Exception as exc:
                results.append({"bug_id": bug_id, "success": False, "message": str(exc)})

        success_count = sum(1 for item in results if item.get("success"))
        return {
            "total": len(bug_ids),
            "success_count": success_count,
            "failed_count": len(bug_ids) - success_count,
            "details": results,
        }

    @staticmethod
    async def sync_bug_status(db: Session, bug_id: int | None = None) -> dict:
        ensure_jira_bug_links_table()

        cfg = _get_jira_config(db)
        if not cfg:
            raise ValueError("请先在项目管理平台总控制台配置并激活 Jira 连接")

        query = db.query(JiraBugLink)
        if bug_id:
            query = query.filter(JiraBugLink.bug_id == bug_id)
        links = query.all()

        client = _build_client(cfg)
        synced = 0
        errors = []

        try:
            for link in links:
                bug = db.query(BugReport).filter(BugReport.id == link.bug_id).first()
                if not bug:
                    continue
                try:
                    remote = client.get_issue(link.issue_key)
                    remote_status = (((remote or {}).get("fields") or {}).get("status") or {}).get("name", "")
                    local_status = _map_jira_status(remote_status)
                    if local_status and bug.status != local_status:
                        bug.status = local_status
                        synced += 1
                except Exception as exc:
                    errors.append({"bug_id": link.bug_id, "issue_key": link.issue_key, "error": str(exc)})
        finally:
            client.close()

        db.commit()
        return {
            "total": len(links),
            "synced": synced,
            "errors": errors,
            "message": f"同步完成，更新 {synced} 条 Bug 状态",
        }


def _build_jira_description(bug: BugReport, api_version: str):
    lines = [
        f"问题描述: {bug.description or bug.bug_name}",
        f"错误类型: {bug.error_type or '-'}",
        f"严重程度: {bug.severity_level or '-'}",
        "",
        "复现步骤:",
        bug.reproduce_steps or "-",
        "",
        f"预期结果: {bug.expected_result or '-'}",
        f"实际结果: {bug.actual_result or bug.result_feedback or '-'}",
    ]
    if bug.location_url:
        lines.extend(["", f"定位地址: {bug.location_url}"])

    plain_text = "\n".join(lines)
    if str(api_version) == "2":
        return plain_text
    return _build_adf_document(lines)


def _build_adf_document(lines: list[str]) -> dict:
    content = []
    for line in lines:
        if line == "":
            content.append({"type": "paragraph", "content": []})
            continue
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": line}],
        })
    return {"version": 1, "type": "doc", "content": content}


def _map_jira_status(status_name: str) -> str | None:
    normalized = (status_name or "").strip().lower()
    if normalized in {"to do", "todo", "open", "backlog", "selected for development", "reopened"}:
        return "待处理"
    if normalized in {"in progress", "in review", "review", "confirmed"}:
        return "已确认"
    if normalized in {"resolved", "done", "fixed"}:
        return "已修复"
    if normalized in {"closed", "cancelled", "canceled"}:
        return "已关闭"
    return None
