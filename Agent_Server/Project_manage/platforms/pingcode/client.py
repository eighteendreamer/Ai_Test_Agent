import httpx
from typing import Optional


class PingCodeClient:
    """
    PingCode 开放平台客户端
    认证方式: OAuth2 Client Credentials 或 Personal Access Token
    文档: https://open.pingcode.com
    注意: SaaS 版 token 端点可能返回 405，建议使用 Personal Access Token
    """

    TOKEN_URL = "https://open.pingcode.com/oauth/token"
    API_BASE = "https://open.pingcode.com"

    def __init__(self, client_id: str, client_secret: str,
                 access_token: Optional[str] = None,
                 refresh_token: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token_value = refresh_token
        self.client = httpx.Client(timeout=30, verify=False)

    def get_token(self) -> bool:
        """使用 Client Credentials 获取 access_token"""
        resp = self.client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            self.access_token = resp.json().get("access_token")
            return bool(self.access_token)
        return False

    def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict:
        """授权码换取 access_token (Authorization Code Flow)"""
        resp = self.client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data.get("access_token")
        self.refresh_token_value = data.get("refresh_token")
        return data

    def refresh_token(self, refresh_token: str) -> dict:
        """使用 refresh_token 刷新 access_token"""
        resp = self.client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data.get("access_token")
        self.refresh_token_value = data.get("refresh_token", refresh_token)
        return data

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.API_BASE}{path}", headers=self._headers(), params=params or {})
        resp.raise_for_status()
        return resp.json()

    def get_spaces(self) -> dict:
        """获取空间列表"""
        return self._get("/scrum/api/v1/spaces")

    def get_projects(self, space_id: str = None) -> dict:
        """获取项目列表"""
        params = {"space_id": space_id} if space_id else {}
        return self._get("/scrum/api/v1/projects", params)

    def get_members(self) -> dict:
        """获取成员列表"""
        return self._get("/iam/api/v1/members")

    def get_issues(self, project_id: str, page: int = 1, per_page: int = 50) -> dict:
        """获取工作项列表"""
        return self._get(f"/scrum/api/v1/projects/{project_id}/issues", {"page": page, "per_page": per_page})

    def get_test_cases(self, project_id: str, page: int = 1, per_page: int = 50) -> dict:
        """获取测试用例"""
        return self._get(f"/testhub/api/v1/projects/{project_id}/cases", {"page": page, "per_page": per_page})

    def test_connection(self) -> dict:
        try:
            if not self.access_token:
                if not self.get_token():
                    return {"success": False, "message": "获取 access_token 失败，请检查 Client ID / Secret 或改用 Personal Access Token"}
            members = self.get_members()
            return {"success": True, "message": "PingCode 连接成功", "data": {"members_count": members.get("total", 0)}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API 请求失败 (HTTP {e.response.status_code}): {e.response.text}"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
