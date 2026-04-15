import httpx
from typing import Optional


class WorktileClient:
    """
    Worktile 客户端
    认证方式: OAuth2 Authorization Code
    文档: https://dev.worktile.com/document/using-oauth2
    Token 端点: https://dev.worktile.com/api/oauth2/token
    注意: Worktile 只支持 Authorization Code 流程，不支持 Client Credentials
    """

    TOKEN_URL = "https://dev.worktile.com/api/oauth2/token"
    API_BASE = "https://dev.worktile.com/api"

    def __init__(self, client_id: str, client_secret: str, access_token: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.client = httpx.Client(timeout=30, verify=False)

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """用授权码换取 access_token（Authorization Code 流程）"""
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
        return data

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.API_BASE}{path}", headers=self._headers(), params=params or {})
        resp.raise_for_status()
        return resp.json()

    def get_projects(self) -> dict:
        return self._get("/mission/projects")

    def get_tasks(self, project_id: str, page: int = 1, per_page: int = 50) -> dict:
        return self._get(f"/mission/projects/{project_id}/tasks", {"page": page, "per_page": per_page})

    def get_members(self) -> dict:
        return self._get("/members")

    def test_connection(self) -> dict:
        if not self.access_token:
            return {
                "success": False,
                "message": "Worktile 使用 Authorization Code 授权，需要先完成浏览器授权流程获取 access_token"
            }
        try:
            projects = self.get_projects()
            return {
                "success": True,
                "message": "Worktile 连接成功",
                "data": {"projects_count": len(projects.get("values", []))}
            }
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API 请求失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
