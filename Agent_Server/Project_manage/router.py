"""
项目管理平台统一配置 API 路由
"""
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from database.connection import get_db
from Project_manage.platforms.eightmanage.client import EightManageClient
from Project_manage.platforms.msproject.client import MsProjectClient
from Project_manage.service import ProjectPlatformService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/project-platform",
    tags=["项目管理平台"]
)


# =====================================================================
# Pydantic 请求模型
# =====================================================================

class PlatformConfigCreate(BaseModel):
    platform_id: str
    platform_name: str
    config_name: str
    base_url: str
    account: str
    password: str
    api_token: Optional[str] = None
    default_product_id: Optional[int] = None
    api_version: Optional[str] = "v2"
    extra_config: Optional[str] = None
    description: Optional[str] = None


class PlatformConfigUpdate(BaseModel):
    config_name: Optional[str] = None
    platform_name: Optional[str] = None
    base_url: Optional[str] = None
    account: Optional[str] = None
    password: Optional[str] = None
    api_token: Optional[str] = None
    default_product_id: Optional[int] = None
    api_version: Optional[str] = None
    extra_config: Optional[str] = None
    is_enabled: Optional[int] = None
    description: Optional[str] = None


class TestConnectionRequest(BaseModel):
    base_url: str
    account: Optional[str] = None
    password: Optional[str] = None
    api_token: Optional[str] = None
    platform_id: Optional[str] = None
    api_version: Optional[str] = "v2"
    extra_config: Optional[str] = None  # JSON 字符串，存储平台特有字段（如 PingCode 的 client_id/secret）


# =====================================================================
# 配置管理接口
# =====================================================================

@router.get("/platforms/supported")
def get_supported_platforms():
    """获取系统支持的所有项目管理平台列表"""
    return {"success": True, "data": ProjectPlatformService.get_supported_platforms()}


@router.get("/list")
def list_platforms(db: Session = Depends(get_db)):
    """获取所有项目管理平台配置列表"""
    return {"success": True, "data": ProjectPlatformService.list_all(db)}


@router.get("/active")
def list_active_platforms(db: Session = Depends(get_db)):
    """获取已激活的项目管理平台列表（用于动态菜单）"""
    return {"success": True, "data": ProjectPlatformService.list_active(db)}


@router.get("/{platform_id}/remote-projects")
def list_remote_projects(platform_id: str, db: Session = Depends(get_db)):
    """获取平台远端项目列表，供同步到本地项目管理使用。"""
    try:
        projects = ProjectPlatformService.list_remote_projects(db, platform_id)
        return {"success": True, "data": projects}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{platform_id}")
def get_platform(platform_id: str, db: Session = Depends(get_db)):
    """获取指定平台配置"""
    cfg = ProjectPlatformService.get_by_platform_id(db, platform_id)
    if not cfg:
        return {"success": True, "data": None, "message": "该平台尚未配置"}
    return {"success": True, "data": cfg}


@router.post("")
def create_platform(body: PlatformConfigCreate, db: Session = Depends(get_db)):
    """新增项目管理平台配置"""
    try:
        cfg = ProjectPlatformService.create(db, body.model_dump())
        return {"success": True, "data": cfg, "message": "平台配置创建成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{config_id}")
def update_platform(config_id: int, body: PlatformConfigUpdate, db: Session = Depends(get_db)):
    """更新项目管理平台配置"""
    try:
        data = body.model_dump(exclude_none=True)
        logger.info(f"[ProjectPlatform] 更新配置 ID={config_id}, 数据: {data}")
        cfg = ProjectPlatformService.update(db, config_id, data)
        return {"success": True, "data": cfg, "message": "平台配置更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{config_id}")
def delete_platform(config_id: int, db: Session = Depends(get_db)):
    """删除项目管理平台配置"""
    try:
        ProjectPlatformService.delete(db, config_id)
        return {"success": True, "message": "平台配置已删除"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{config_id}/activate")
def activate_platform(config_id: int, db: Session = Depends(get_db)):
    """激活指定平台配置"""
    try:
        cfg = ProjectPlatformService.activate(db, config_id)
        return {"success": True, "data": cfg, "message": "平台已激活"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{config_id}/deactivate")
def deactivate_platform(config_id: int, db: Session = Depends(get_db)):
    """取消激活指定平台配置"""
    try:
        cfg = ProjectPlatformService.deactivate(db, config_id)
        return {"success": True, "data": cfg, "message": "平台已取消激活"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{config_id}/enable")
def enable_platform(config_id: int, db: Session = Depends(get_db)):
    """启用指定平台配置"""
    try:
        cfg = ProjectPlatformService.enable(db, config_id)
        return {"success": True, "data": cfg, "message": "平台已启用"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{config_id}/disable")
def disable_platform(config_id: int, db: Session = Depends(get_db)):
    """禁用指定平台配置"""
    try:
        cfg = ProjectPlatformService.disable(db, config_id)
        return {"success": True, "data": cfg, "message": "平台已禁用"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest):
    """测试平台连接及账号密码/Token 是否有效"""
    import hashlib
    import base64

    base_url = body.base_url.rstrip("/")
    platform_id = (body.platform_id or "").lower()
    account = body.account or ""
    password = body.password or ""
    api_token = body.api_token or ""

    try:
        async with httpx.AsyncClient(timeout=12, verify=False, follow_redirects=True) as client:

            # ── 禅道 ──────────────────────────────────────────────
            if platform_id == "zentao":
                for suffix in ("/index.php", "/zentao/index.php"):
                    if base_url.endswith(suffix):
                        base_url = base_url[: -len(suffix)]
                        break
                api_ver = body.api_version or "v2"
                pwd_md5 = hashlib.md5(password.encode()).hexdigest()
                if api_ver == "v1":
                    url = f"{base_url}/api.php/v1/tokens"
                    payload = {"account": account, "password": pwd_md5}
                else:
                    url = f"{base_url}/api.php/v2/users/login"
                    payload = {"account": account, "password": pwd_md5}
                resp = await client.post(url, json=payload)
                text = resp.text.lower()
                if resp.status_code in (200, 201) and "token" in text:
                    return {"success": True, "message": "认证成功，账号密码有效"}
                elif resp.status_code == 401 or "unauthorized" in text or "wrong" in text:
                    return {"success": False, "message": "平台可达，但账号或密码错误"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）：{resp.text[:200]}"}

            # ── Jira（Cloud: email + api_token；Server: account + password）──
            elif platform_id == "jira":
                # Cloud 优先用 api_token，Server 用 password
                pwd = api_token if api_token else password
                cred = base64.b64encode(f"{account}:{pwd}".encode()).decode()
                headers = {"Authorization": f"Basic {cred}", "Accept": "application/json"}
                # Cloud
                resp = await client.get(f"{base_url}/rest/api/3/myself", headers=headers)
                if resp.status_code == 404:
                    # Server/DC 用 v2
                    resp = await client.get(f"{base_url}/rest/api/2/myself", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    name = data.get("displayName") or data.get("name") or account
                    return {"success": True, "message": f"认证成功，当前用户：{name}"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "平台可达，但账号或 API Token 错误"}
                elif resp.status_code == 403:
                    return {"success": False, "message": "认证通过但权限不足，请检查账号权限"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）"}

            # ── TAPD（腾讯）：Basic Auth ──────────────────────────
            elif platform_id == "tapd":
                cred = base64.b64encode(f"{account}:{password}".encode()).decode()
                headers = {"Authorization": f"Basic {cred}"}
                resp = await client.get(f"{base_url}/api/v1/users/info", headers=headers)
                if resp.status_code == 200:
                    return {"success": True, "message": "认证成功，账号密码有效"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "平台可达，但账号或密码错误"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）"}

            # ── PingCode：OAuth2 Client Credentials ──────────────
            elif platform_id in {"8manage", "msproject"}:
                extra = {}
                if body.extra_config:
                    try:
                        extra = json.loads(body.extra_config) if isinstance(body.extra_config, str) else (body.extra_config or {})
                    except Exception:
                        extra = {}

                if platform_id == "8manage":
                    merged_extra = ProjectPlatformService._merge_generic_project_extra(
                        ProjectPlatformService._default_eightmanage_extra(),
                        extra,
                    )
                    runtime_client = EightManageClient(
                        base_url=base_url,
                        account=account,
                        password=password,
                        api_token=api_token,
                        auth_type=merged_extra.get("auth_type", "basic"),
                        auth_header_name=merged_extra.get("auth_header_name", "Authorization"),
                        auth_header_prefix=merged_extra.get("auth_header_prefix", ""),
                        project_path=merged_extra.get("project_path", "/api/projects"),
                        response_list_path=merged_extra.get("response_list_path", "data.items"),
                        id_field=merged_extra.get("id_field", "id"),
                        name_field=merged_extra.get("name_field", "name"),
                        code_field=merged_extra.get("code_field", "code"),
                        status_field=merged_extra.get("status_field", "status"),
                        scope_field=merged_extra.get("scope_field", ""),
                        description_field=merged_extra.get("description_field", "description"),
                        custom_headers=merged_extra.get("custom_headers") or {},
                        query_params=merged_extra.get("query_params") or {},
                    )
                else:
                    merged_extra = ProjectPlatformService._merge_generic_project_extra(
                        ProjectPlatformService._default_msproject_extra(),
                        extra,
                    )
                    runtime_client = MsProjectClient(
                        base_url=base_url,
                        account=account,
                        password=password,
                        api_token=api_token,
                        auth_type=merged_extra.get("auth_type", "bearer"),
                        auth_header_name=merged_extra.get("auth_header_name", "Authorization"),
                        auth_header_prefix=merged_extra.get("auth_header_prefix", "Bearer"),
                        project_path=merged_extra.get("project_path", "/v1.0/me/planner/plans"),
                        response_list_path=merged_extra.get("response_list_path", "value"),
                        id_field=merged_extra.get("id_field", "id"),
                        name_field=merged_extra.get("name_field", "title"),
                        code_field=merged_extra.get("code_field", "id"),
                        status_field=merged_extra.get("status_field", "owner"),
                        scope_field=merged_extra.get("scope_field", ""),
                        description_field=merged_extra.get("description_field", "container.url"),
                        custom_headers=merged_extra.get("custom_headers") or {},
                        query_params=merged_extra.get("query_params") or {},
                    )

                try:
                    return runtime_client.test_connection()
                finally:
                    runtime_client.close()

            elif platform_id == "pingcode":
                import json as _json
                extra = {}
                if hasattr(body, 'extra_config') and body.extra_config:
                    try:
                        extra = _json.loads(body.extra_config) if isinstance(body.extra_config, str) else (body.extra_config or {})
                    except Exception:
                        extra = {}

                auth_type = extra.get("auth_type", "client_credentials")
                client_id = extra.get("client_id", "")
                client_secret = extra.get("client_secret", "")
                token_url = f"{base_url.rstrip('/')}/oauth/token"

                if auth_type == "client_credentials":
                    if not client_id or not client_secret:
                        return {"success": False, "message": "Client Credentials 模式需要填写 Client ID 和 Client Secret"}

                    token_resp = await client.post(
                        token_url,
                        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
                        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                                 "Origin": base_url.rstrip("/"), "Referer": base_url.rstrip("/") + "/"}
                    )

                    if token_resp.status_code in (200, 201):
                        try:
                            data = token_resp.json()
                            if data.get("access_token"):
                                return {"success": True, "message": "Client Credentials 认证成功，Access Token 已获取"}
                            err = data.get("error_description") or data.get("error") or "未返回 access_token"
                            return {"success": False, "message": f"认证失败：{err}"}
                        except Exception:
                            return {"success": False, "message": f"响应解析失败：{token_resp.text[:200]}"}
                    elif token_resp.status_code == 401:
                        return {"success": False, "message": "Client ID 或 Client Secret 错误（401）"}
                    elif token_resp.status_code == 400:
                        try:
                            err_data = token_resp.json()
                            err_msg = err_data.get("error_description") or err_data.get("error") or token_resp.text[:200]
                            return {"success": False, "message": f"请求参数错误：{err_msg}"}
                        except Exception:
                            return {"success": False, "message": f"请求参数错误（400）：{token_resp.text[:200]}"}
                    elif token_resp.status_code == 405:
                        if api_token:
                            verify_resp = await client.get(
                                f"{base_url.rstrip('/')}/iam/api/v1/members",
                                headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
                            )
                            if verify_resp.status_code == 200 and "html" not in verify_resp.headers.get("content-type", ""):
                                return {"success": True, "message": "API Token 有效，认证成功"}
                        return {"success": False, "message": "OAuth2 token 端点返回 405，请在管理后台生成 Personal Access Token 填入 API Token 字段后重试"}
                    else:
                        return {"success": False, "message": f"认证失败（HTTP {token_resp.status_code}）：{token_resp.text[:200]}"}

                elif auth_type == "authorization_code":
                    if not client_id:
                        return {"success": False, "message": "Authorization Code 模式需要填写 Client ID"}
                    redirect_uri = extra.get("redirect_uri", "")
                    auth_url = f"{base_url.rstrip('/')}/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&scope=read"
                    return {"success": True, "message": f"Authorization Code 模式需在浏览器中完成授权，请访问：{auth_url}"}
                else:
                    return {"success": False, "message": f"未知鉴权方式：{auth_type}"}

            # ── Worktile：OAuth2 Authorization Code ──────────────
            # Worktile 只支持 Authorization Code 流程，token 端点为 dev.worktile.com
            elif platform_id == "worktile":
                import json as _json
                extra = {}
                if hasattr(body, 'extra_config') and body.extra_config:
                    try:
                        extra = _json.loads(body.extra_config) if isinstance(body.extra_config, str) else (body.extra_config or {})
                    except Exception:
                        extra = {}

                client_id = extra.get("client_id", "")
                client_secret = extra.get("client_secret", "")
                redirect_uri = extra.get("redirect_uri", "")

                if not client_id or not client_secret:
                    return {"success": False, "message": "Worktile 需要填写 Client ID 和 Client Secret"}

                # Worktile 使用 Authorization Code 流程，无法在后端直接验证
                # 返回授权 URL 供用户在浏览器中完成授权
                auth_url = (
                    f"https://dev.worktile.com/api/oauth2/authorize"
                    f"?response_type=code&client_id={client_id}"
                    f"&redirect_uri={redirect_uri}&scope=mission"
                )
                return {
                    "success": True,
                    "message": f"Worktile 使用 Authorization Code 授权，请在浏览器中访问以下地址完成授权：{auth_url}"
                }

            # ── Asana：Bearer API Token ───────────────────────────
            elif platform_id == "asana":
                token = api_token if api_token else password
                if not token:
                    return {"success": False, "message": "Asana 需要填写 API Token 才能验证"}
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                resp = await client.get("https://app.asana.com/api/1.0/users/me", headers=headers)
                if resp.status_code == 200:
                    name = resp.json().get("data", {}).get("name", "")
                    return {"success": True, "message": f"认证成功，当前用户：{name}"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "API Token 无效"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）"}

            # ── ClickUp：Bearer API Token ─────────────────────────
            elif platform_id == "clickup":
                token = api_token if api_token else password
                if not token:
                    return {"success": False, "message": "ClickUp 需要填写 API Token 才能验证"}
                headers = {"Authorization": token, "Accept": "application/json"}
                resp = await client.get("https://api.clickup.com/api/v2/user", headers=headers)
                if resp.status_code == 200:
                    name = resp.json().get("user", {}).get("username", "")
                    return {"success": True, "message": f"认证成功，当前用户：{name}"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "API Token 无效"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）"}

            # ── ONES：email + password 登录 ───────────────────────
            elif platform_id == "ones":
                headers = {"Content-Type": "application/json", "Referer": base_url.rstrip("/")}
                payload = {"email": account, "password": password}
                resp = await client.post(f"{base_url.rstrip('/')}/project/api/project/auth/login", json=payload, headers=headers)
                if resp.status_code == 200:
                    try:
                        user = resp.json().get("user", {})
                        name = user.get("name") or user.get("email") or account
                        return {"success": True, "message": f"认证成功，当前用户：{name}"}
                    except Exception:
                        return {"success": True, "message": "认证成功"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "账号或密码错误（401）"}
                elif resp.status_code == 400:
                    return {"success": False, "message": "请求参数错误，请检查邮箱格式（400）"}
                elif resp.status_code == 404:
                    return {"success": False, "message": "API 端点不存在（404），请确认平台地址是你的 ONES 实例域名（如 https://your-team.ones.cn），而非官网地址"}
                elif resp.status_code == 813:
                    return {"success": False, "message": "账户已过期（813），请联系 ONES 管理员"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）：{resp.text[:200]}"}

            # ── 云效（阿里云）：API Token ─────────────────────────
            elif platform_id == "yunxiao":
                token = api_token if api_token else password
                if not token:
                    return {"success": False, "message": "云效需要填写 API Token 才能验证"}
                headers = {"Authorization": token, "Accept": "application/json"}
                resp = await client.get(f"{base_url}/oapi/v1/organization/members", headers=headers)
                if resp.status_code == 200:
                    return {"success": True, "message": "API Token 有效，认证成功"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "API Token 无效"}
                else:
                    return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）"}

            # ── 其余平台：尝试 Basic Auth 访问根 API 路径 ─────────
            else:
                tested = False
                # 有账号密码则尝试 Basic Auth
                if account and password:
                    cred = base64.b64encode(f"{account}:{password}".encode()).decode()
                    headers = {"Authorization": f"Basic {cred}", "Accept": "application/json"}
                    for path in ["/api/v1/user", "/api/user", "/api/me", "/api/v1/me"]:
                        try:
                            resp = await client.get(f"{base_url}{path}", headers=headers)
                            tested = True
                            if resp.status_code == 200:
                                return {"success": True, "message": f"认证成功（{path}）"}
                            elif resp.status_code == 401:
                                return {"success": False, "message": "平台可达，但账号或密码错误"}
                            elif resp.status_code == 404:
                                continue  # 尝试下一个路径
                        except Exception:
                            continue

                # 有 API Token 则尝试 Bearer
                if api_token:
                    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
                    for path in ["/api/v1/user", "/api/user", "/api/me", "/api/v1/me"]:
                        try:
                            resp = await client.get(f"{base_url}{path}", headers=headers)
                            tested = True
                            if resp.status_code == 200:
                                return {"success": True, "message": f"API Token 有效（{path}）"}
                            elif resp.status_code == 401:
                                return {"success": False, "message": "平台可达，但 API Token 无效"}
                            elif resp.status_code == 404:
                                continue
                        except Exception:
                            continue

                # fallback：仅验证可达性
                resp = await client.get(base_url)
                if resp.status_code < 500:
                    hint = "（该平台暂不支持自动认证验证，仅确认地址可达）" if tested is False else "（未找到标准认证端点，仅确认地址可达）"
                    return {"success": True, "message": f"平台地址可达（HTTP {resp.status_code}）{hint}"}
                else:
                    return {"success": False, "message": f"平台返回服务器错误（HTTP {resp.status_code}）"}

    except httpx.ConnectTimeout:
        return {"success": False, "message": "连接超时，请检查平台地址是否正确"}
    except httpx.ConnectError as e:
        return {"success": False, "message": f"无法连接到平台：{str(e)}"}
    except Exception as e:
        logger.exception("[TestConnection] 未知错误")
        return {"success": False, "message": f"测试失败：{str(e)}"}
