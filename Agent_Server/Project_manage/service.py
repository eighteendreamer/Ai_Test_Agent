"""
项目管理平台统一配置与资源访问服务。
"""
import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.connection import ProjectPlatformConfig
from Project_manage.platforms.asana.client import AsanaClient
from Project_manage.platforms.clickup.client import ClickUpClient
from Project_manage.platforms.eightmanage.client import EightManageClient
from Project_manage.platforms.jira.client import JiraClient
from Project_manage.platforms.msproject.client import MsProjectClient
from Project_manage.platforms.ones.client import OnesClient
from Project_manage.platforms.pingcode.client import PingCodeClient
from Project_manage.platforms.tapd.client import TapdClient
from Project_manage.platforms.worktile.client import WorktileClient
from Project_manage.platforms.yunxiao.client import YunxiaoClient
from Project_manage.platforms.zentao.client import ZentaoClient

logger = logging.getLogger(__name__)


class ProjectPlatformService:
    """项目管理平台配置服务"""

    SUPPORTED_PLATFORMS = [
        {"platform_id": "zentao", "platform_name": "禅道 (Zentao)", "description": "开源项目管理软件"},
        {"platform_id": "pingcode", "platform_name": "PingCode", "description": "研发团队项目管理工具"},
        {"platform_id": "worktile", "platform_name": "Worktile", "description": "企业协作与项目管理工具"},
        {"platform_id": "ones", "platform_name": "ONES", "description": "研发项目管理工具"},
        {"platform_id": "yunxiao", "platform_name": "云效", "description": "阿里云研发协同平台"},
        {"platform_id": "tapd", "platform_name": "TAPD", "description": "腾讯敏捷产品研发协作平台"},
        {"platform_id": "8manage", "platform_name": "8Manage PM", "description": "企业级项目管理系统"},
        {"platform_id": "msproject", "platform_name": "Microsoft Project", "description": "微软项目管理软件"},
        {"platform_id": "asana", "platform_name": "Asana", "description": "团队协作和项目管理工具"},
        {"platform_id": "clickup", "platform_name": "ClickUp", "description": "全能型项目管理工具"},
        {"platform_id": "jira", "platform_name": "Jira", "description": "Atlassian敏捷项目管理工具"},
    ]

    REMOTE_PROJECT_ENABLED_PLATFORMS = {
        "zentao",
        "pingcode",
        "worktile",
        "ones",
        "yunxiao",
        "tapd",
        "8manage",
        "msproject",
        "asana",
        "clickup",
        "jira",
    }

    @staticmethod
    def get_supported_platforms() -> list[dict]:
        return ProjectPlatformService.SUPPORTED_PLATFORMS

    @staticmethod
    def list_all(db: Session) -> list[dict]:
        rows = db.query(ProjectPlatformConfig).order_by(
            ProjectPlatformConfig.is_active.desc(),
            ProjectPlatformConfig.id.desc(),
        ).all()
        return [ProjectPlatformService._to_dict(row) for row in rows]

    @staticmethod
    def list_active(db: Session) -> list[dict]:
        rows = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.is_active == 1,
            ProjectPlatformConfig.is_enabled == 1,
        ).order_by(ProjectPlatformConfig.id).all()
        return [ProjectPlatformService._to_dict(row) for row in rows]

    @staticmethod
    def get_by_platform_id(db: Session, platform_id: str) -> Optional[dict]:
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.platform_id == platform_id
        ).first()
        return ProjectPlatformService._to_dict(cfg) if cfg else None

    @staticmethod
    def create(db: Session, data: dict) -> dict:
        existing = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.platform_id == data.get("platform_id")
        ).first()
        if existing:
            raise ValueError(f"平台标识 {data.get('platform_id')} 已存在")

        cfg = ProjectPlatformConfig(
            **{k: v for k, v in data.items() if hasattr(ProjectPlatformConfig, k)}
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
        return ProjectPlatformService._to_dict(cfg)

    @staticmethod
    def update(db: Session, config_id: int, data: dict) -> dict:
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.id == config_id
        ).first()
        if not cfg:
            raise ValueError("配置不存在")

        for key, value in data.items():
            if hasattr(cfg, key) and key not in ("id", "platform_id", "created_at"):
                setattr(cfg, key, value)

        db.commit()
        db.refresh(cfg)
        return ProjectPlatformService._to_dict(cfg)

    @staticmethod
    def delete(db: Session, config_id: int):
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.id == config_id
        ).first()
        if not cfg:
            raise ValueError("配置不存在")
        db.delete(cfg)
        db.commit()

    @staticmethod
    def activate(db: Session, config_id: int) -> dict:
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.id == config_id
        ).first()
        if not cfg:
            raise ValueError("配置不存在")
        cfg.is_active = 1
        db.commit()
        db.refresh(cfg)
        return ProjectPlatformService._to_dict(cfg)

    @staticmethod
    def deactivate(db: Session, config_id: int) -> dict:
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.id == config_id
        ).first()
        if not cfg:
            raise ValueError("配置不存在")
        cfg.is_active = 0
        db.commit()
        db.refresh(cfg)
        return ProjectPlatformService._to_dict(cfg)

    @staticmethod
    def enable(db: Session, config_id: int) -> dict:
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.id == config_id
        ).first()
        if not cfg:
            raise ValueError("配置不存在")
        cfg.is_enabled = 1
        db.commit()
        db.refresh(cfg)
        return ProjectPlatformService._to_dict(cfg)

    @staticmethod
    def disable(db: Session, config_id: int) -> dict:
        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.id == config_id
        ).first()
        if not cfg:
            raise ValueError("配置不存在")
        cfg.is_enabled = 0
        db.commit()
        db.refresh(cfg)
        return ProjectPlatformService._to_dict(cfg)

    @staticmethod
    def list_remote_projects(db: Session, platform_id: str) -> list[dict]:
        platform_id = (platform_id or "").lower()
        if platform_id not in ProjectPlatformService.REMOTE_PROJECT_ENABLED_PLATFORMS:
            raise ValueError(f"{platform_id} 暂未实现远端项目同步")

        cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.platform_id == platform_id,
            ProjectPlatformConfig.is_active == 1,
        ).first()
        if not cfg:
            raise ValueError(f"请先在项目管理平台总控制台配置并激活 {platform_id}")

        client = ProjectPlatformService._build_runtime_client(cfg)
        try:
            ProjectPlatformService._prepare_client(platform_id, client)
            projects = ProjectPlatformService._fetch_remote_projects(platform_id, client)
            cfg.last_sync_at = ProjectPlatformService._utcnow()
            db.commit()
            return projects
        finally:
            try:
                client.close()
            except Exception:
                pass

    @staticmethod
    def _parse_extra_config(extra_config: Optional[str]) -> dict[str, Any]:
        if not extra_config:
            return {}
        try:
            return json.loads(extra_config) if isinstance(extra_config, str) else dict(extra_config)
        except Exception:
            logger.warning("解析 extra_config 失败", exc_info=True)
            return {}

    @staticmethod
    def _build_runtime_client(cfg: ProjectPlatformConfig):
        extra = ProjectPlatformService._parse_extra_config(cfg.extra_config)
        platform_id = cfg.platform_id.lower()

        if platform_id == "zentao":
            return ZentaoClient(
                base_url=cfg.base_url,
                account=cfg.account,
                password=cfg.password or extra.get("password", ""),
            )
        if platform_id == "pingcode":
            return PingCodeClient(
                client_id=extra.get("client_id", ""),
                client_secret=extra.get("client_secret", ""),
                access_token=extra.get("access_token") or cfg.api_token,
            )
        if platform_id == "worktile":
            return WorktileClient(
                client_id=extra.get("client_id", ""),
                client_secret=extra.get("client_secret", ""),
                access_token=extra.get("access_token"),
            )
        if platform_id == "ones":
            return OnesClient(
                base_url=cfg.base_url,
                account=cfg.account,
                password=cfg.password or extra.get("password", ""),
                api_token=cfg.api_token or extra.get("api_token", ""),
            )
        if platform_id == "yunxiao":
            return YunxiaoClient(
                api_token=cfg.api_token or cfg.password or "",
                organization_id=extra.get("organization_id", ""),
            )
        if platform_id == "tapd":
            return TapdClient(
                api_user=cfg.account,
                api_password=cfg.api_token or cfg.password or "",
            )
        if platform_id == "8manage":
            extra = ProjectPlatformService._merge_generic_project_extra(
                ProjectPlatformService._default_eightmanage_extra(),
                extra,
            )
            return EightManageClient(
                base_url=cfg.base_url,
                account=cfg.account,
                password=cfg.password or "",
                api_token=cfg.api_token or cfg.password or "",
                auth_type=extra.get("auth_type", "basic"),
                auth_header_name=extra.get("auth_header_name", "Authorization"),
                auth_header_prefix=extra.get("auth_header_prefix", ""),
                project_path=extra.get("project_path", "/api/projects"),
                response_list_path=extra.get("response_list_path", ""),
                id_field=extra.get("id_field", "id"),
                name_field=extra.get("name_field", "name"),
                code_field=extra.get("code_field", "code"),
                status_field=extra.get("status_field", "status"),
                scope_field=extra.get("scope_field", ""),
                description_field=extra.get("description_field", "description"),
                custom_headers=extra.get("custom_headers") or {},
                query_params=extra.get("query_params") or {},
            )
        if platform_id == "msproject":
            extra = ProjectPlatformService._merge_generic_project_extra(
                ProjectPlatformService._default_msproject_extra(),
                extra,
            )
            return MsProjectClient(
                base_url=cfg.base_url,
                account=cfg.account,
                password=cfg.password or "",
                api_token=cfg.api_token or cfg.password or "",
                auth_type=extra.get("auth_type", "bearer"),
                auth_header_name=extra.get("auth_header_name", "Authorization"),
                auth_header_prefix=extra.get("auth_header_prefix", "Bearer"),
                project_path=extra.get("project_path", "/v1.0/me/planner/plans"),
                response_list_path=extra.get("response_list_path", "value"),
                id_field=extra.get("id_field", "id"),
                name_field=extra.get("name_field", "title"),
                code_field=extra.get("code_field", "id"),
                status_field=extra.get("status_field", "owner"),
                scope_field=extra.get("scope_field", ""),
                description_field=extra.get("description_field", "container.url"),
                custom_headers=extra.get("custom_headers") or {},
                query_params=extra.get("query_params") or {},
            )
        if platform_id == "asana":
            return AsanaClient(api_token=cfg.api_token or cfg.password or "")
        if platform_id == "clickup":
            return ClickUpClient(api_token=cfg.api_token or cfg.password or "")
        if platform_id == "jira":
            return JiraClient(
                base_url=cfg.base_url,
                account=cfg.account,
                api_token=cfg.api_token or cfg.password or "",
                api_version=cfg.api_version or "3",
            )
        raise ValueError(f"不支持的平台: {cfg.platform_id}")

    @staticmethod
    def _prepare_client(platform_id: str, client):
        if platform_id == "pingcode":
            if not getattr(client, "access_token", None):
                if not client.get_token():
                    raise ValueError("PingCode 未获取到 access_token，请检查 Client ID / Client Secret 或 API Token")
        elif platform_id == "worktile":
            if not getattr(client, "access_token", None):
                raise ValueError("Worktile 需要先完成 OAuth 授权并保存 access_token 后才能读取项目")
        elif platform_id == "ones":
            if not getattr(client, "api_token", None):
                if not client.login():
                    raise ValueError("ONES 登录失败，请检查账号密码或 API Token")

    @staticmethod
    def _fetch_remote_projects(platform_id: str, client) -> list[dict]:
        if platform_id == "zentao":
            return ProjectPlatformService._normalize_zentao_projects(client.get_products())
        if platform_id == "pingcode":
            return ProjectPlatformService._normalize_pingcode_projects(client.get_projects())
        if platform_id == "worktile":
            return ProjectPlatformService._normalize_worktile_projects(client.get_projects())
        if platform_id == "ones":
            return ProjectPlatformService._normalize_ones_projects(client.get_projects())
        if platform_id == "yunxiao":
            return ProjectPlatformService._normalize_yunxiao_projects(client.get_projects())
        if platform_id == "tapd":
            return ProjectPlatformService._normalize_tapd_projects(client.get_workspaces())
        if platform_id == "8manage":
            return client.get_projects()
        if platform_id == "msproject":
            return client.get_projects()
        if platform_id == "asana":
            return ProjectPlatformService._normalize_asana_projects(client.get_projects())
        if platform_id == "jira":
            return ProjectPlatformService._normalize_jira_projects(client.get_projects())
        if platform_id == "clickup":
            return ProjectPlatformService._normalize_clickup_projects(client)
        raise ValueError(f"{platform_id} 暂未实现远端项目同步")

    @staticmethod
    def _normalize_zentao_projects(raw: Any) -> list[dict]:
        items = raw if isinstance(raw, list) else []
        return [
            ProjectPlatformService._project_row(
                str(item.get("id", "")),
                item.get("name", ""),
                item.get("code", "") or item.get("id", ""),
                item.get("status", ""),
            )
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        ]

    @staticmethod
    def _normalize_pingcode_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["data", "values", "projects", "items"])
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(item.get("id") or item.get("identifier") or ""),
                item.get("name", ""),
                item.get("key", "") or item.get("identifier", "") or item.get("code", ""),
                item.get("status", "") or item.get("state", ""),
                item.get("description", "") or item.get("desc", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_worktile_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["values", "data", "projects", "items"])
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(item.get("id") or item.get("_id") or ""),
                item.get("name", ""),
                item.get("identifier", "") or item.get("code", "") or item.get("id", ""),
                item.get("status", "") or item.get("state", ""),
                item.get("description", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_ones_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["projects", "data", "values"])
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(item.get("uuid") or item.get("id") or ""),
                item.get("name", ""),
                item.get("code", "") or item.get("uuid", ""),
                item.get("status", "") or item.get("state", ""),
                item.get("description", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_yunxiao_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["projects", "data", "items"])
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(item.get("id") or item.get("projectId") or ""),
                item.get("name", ""),
                item.get("identifier", "") or item.get("code", "") or item.get("id", ""),
                item.get("status", "") or item.get("state", ""),
                item.get("description", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_tapd_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["data", "workspaces", "projects"])
        rows = []
        for item in items:
            workspace = item.get("Workspace", item) if isinstance(item, dict) else {}
            if not isinstance(workspace, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(workspace.get("id") or workspace.get("workspace_id") or ""),
                workspace.get("name", ""),
                workspace.get("shortname", "") or workspace.get("id", ""),
                workspace.get("status", ""),
                workspace.get("description", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_asana_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["data", "projects", "items"])
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(item.get("gid") or item.get("id") or ""),
                item.get("name", ""),
                item.get("gid", "") or item.get("id", ""),
                item.get("resource_type", ""),
                item.get("notes", "") or item.get("description", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_jira_projects(raw: Any) -> list[dict]:
        items = ProjectPlatformService._extract_list(raw, ["values", "data", "projects", "items"])
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(ProjectPlatformService._project_row(
                str(item.get("id") or item.get("key") or ""),
                item.get("name", ""),
                item.get("key", "") or item.get("id", ""),
                item.get("projectTypeKey", "") or item.get("style", ""),
                item.get("description", ""),
            ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _normalize_clickup_projects(client: ClickUpClient) -> list[dict]:
        teams_raw = client.get_teams()
        teams = ProjectPlatformService._extract_list(teams_raw, ["teams", "data", "items"])
        rows: list[dict] = []
        for team in teams:
            if not isinstance(team, dict) or not team.get("id"):
                continue
            team_id = str(team.get("id"))
            team_name = team.get("name", "")
            spaces_raw = client.get_spaces(team_id)
            spaces = ProjectPlatformService._extract_list(spaces_raw, ["spaces", "data", "items"])
            for space in spaces:
                if not isinstance(space, dict):
                    continue
                rows.append(ProjectPlatformService._project_row(
                    str(space.get("id") or ""),
                    space.get("name", ""),
                    space.get("id", ""),
                    space.get("status", "") or team_name,
                    f"Team: {team_name}",
                    scope=team_name,
                ))
        return [row for row in rows if row["id"]]

    @staticmethod
    def _default_eightmanage_extra() -> dict[str, Any]:
        return {
            "auth_type": "basic",
            "auth_header_name": "Authorization",
            "auth_header_prefix": "",
            "project_path": "/api/projects",
            "response_list_path": "data.items",
            "id_field": "id",
            "name_field": "name",
            "code_field": "code",
            "status_field": "status",
            "scope_field": "",
            "description_field": "description",
            "custom_headers": {},
            "query_params": {},
        }

    @staticmethod
    def _default_msproject_extra() -> dict[str, Any]:
        return {
            "auth_type": "bearer",
            "auth_header_name": "Authorization",
            "auth_header_prefix": "Bearer",
            "project_path": "/v1.0/me/planner/plans",
            "response_list_path": "value",
            "id_field": "id",
            "name_field": "title",
            "code_field": "id",
            "status_field": "owner",
            "scope_field": "",
            "description_field": "container.url",
            "custom_headers": {},
            "query_params": {},
        }

    @staticmethod
    def _merge_generic_project_extra(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in (extra or {}).items():
            if value is not None and value != "":
                merged[key] = value
        return merged

    @staticmethod
    def _extract_list(raw: Any, keys: list[str]) -> list[Any]:
        if isinstance(raw, list):
            return raw
        if not isinstance(raw, dict):
            return []

        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested_values = list(value.values())
                if nested_values and all(isinstance(item, dict) for item in nested_values):
                    return nested_values
        return []

    @staticmethod
    def _project_row(
        remote_id: str,
        name: str,
        code: str = "",
        status: str = "",
        description: str = "",
        scope: str = "",
    ) -> dict:
        return {
            "id": str(remote_id or ""),
            "name": name or "",
            "code": code or "",
            "status": status or "",
            "description": description or "",
            "scope": scope or "",
        }

    @staticmethod
    def _utcnow():
        from datetime import datetime
        return datetime.now()

    @staticmethod
    def _to_dict(cfg: ProjectPlatformConfig) -> dict:
        return {
            "id": cfg.id,
            "platform_id": cfg.platform_id,
            "platform_name": cfg.platform_name,
            "config_name": cfg.config_name,
            "base_url": cfg.base_url,
            "account": cfg.account,
            "api_token": cfg.api_token,
            "default_product_id": cfg.default_product_id,
            "api_version": cfg.api_version,
            "extra_config": cfg.extra_config,
            "is_active": cfg.is_active,
            "is_enabled": cfg.is_enabled,
            "last_sync_at": cfg.last_sync_at.isoformat() if cfg.last_sync_at else None,
            "description": cfg.description,
            "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        }
