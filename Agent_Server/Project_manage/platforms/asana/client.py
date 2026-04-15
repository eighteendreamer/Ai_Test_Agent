import httpx


class AsanaClient:
    """
    Asana 客户端
    认证方式: Personal Access Token
    文档: https://developers.asana.com/docs
    """

    API_BASE = "https://app.asana.com/api/1.0"

    def __init__(self, api_token: str):
        self.client = httpx.Client(
            timeout=30,
            verify=False,
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
        )

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.API_BASE}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def get_me(self) -> dict:
        """获取当前用户"""
        return self._get("/users/me")

    def get_workspaces(self) -> dict:
        """获取工作区列表"""
        return self._get("/workspaces")

    def get_projects(self, workspace_gid: str = None) -> dict:
        """获取项目列表"""
        params = {"workspace": workspace_gid} if workspace_gid else {}
        return self._get("/projects", params)

    def get_tasks(self, project_gid: str, opt_fields: str = "name,status,due_on") -> dict:
        """获取任务列表"""
        return self._get(f"/projects/{project_gid}/tasks", {"opt_fields": opt_fields})

    def get_task(self, task_gid: str) -> dict:
        """获取单个任务"""
        return self._get(f"/tasks/{task_gid}")

    def test_connection(self) -> dict:
        try:
            me = self.get_me()
            user = me.get("data", {})
            return {"success": True, "message": "Asana 连接成功",
                    "data": {"name": user.get("name"), "email": user.get("email")}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"认证失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
