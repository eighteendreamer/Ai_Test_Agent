import httpx
from typing import Optional


class OnesClient:
    """
    ONES 客户端
    认证方式: 账号密码登录获取 token，或直接传入 API Token
    文档: https://ones.cn/openapi/
    """

    def __init__(self, base_url: str, account: str, password: str = "", api_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.account = account
        self.password = password
        self.api_token = api_token
        self.user_uuid: Optional[str] = None
        self.client = httpx.Client(timeout=30, verify=False, headers={"Content-Type": "application/json"})

    def login(self) -> bool:
        """账号密码登录，获取 token"""
        if self.api_token:
            return True
        try:
            resp = self.client.post(
                f"{self.base_url}/project/api/project/auth/login",
                json={"email": self.account, "password": self.password},
                headers={"Content-Type": "application/json", "Referer": self.base_url},
            )
            if resp.status_code == 200:
                user = resp.json().get("user", {})
                self.api_token = user.get("token", "")
                self.user_uuid = user.get("uuid", "")
                return bool(self.api_token)
        except Exception:
            pass
        return False

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Ones-Auth-Token": self.api_token,
            "Ones-User-Id": self.user_uuid or "",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.base_url}{path}", headers=self._headers(), params=params or {})
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict = None) -> dict:
        resp = self.client.post(f"{self.base_url}{path}", headers=self._headers(), json=body or {})
        resp.raise_for_status()
        return resp.json()

    def get_projects(self) -> dict:
        """获取项目列表"""
        return self._post("/project/api/project/team/projects", {})

    def get_tasks(self, project_uuid: str, page: int = 1, limit: int = 50) -> dict:
        """获取任务列表"""
        return self._post("/project/api/project/task/list",
                          {"project_uuid": project_uuid, "page": page, "limit": limit})

    def get_test_cases(self, project_uuid: str) -> dict:
        """获取测试用例"""
        return self._post("/project/api/project/testcase/list", {"project_uuid": project_uuid})

    def test_connection(self) -> dict:
        try:
            if not self.login():
                return {"success": False, "message": "登录失败，请检查账号密码或 API Token"}
            projects = self.get_projects()
            return {"success": True, "message": "ONES 连接成功",
                    "data": {"projects_count": len(projects.get("projects", []))}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API 请求失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
