import httpx


class TapdClient:
    """
    TAPD 客户端
    认证方式: Basic Auth (api_user + api_password)
    文档: https://www.tapd.cn/help/show#1120003271001000093
    """

    API_BASE = "https://api.tapd.cn"

    def __init__(self, api_user: str, api_password: str):
        self.client = httpx.Client(
            timeout=30,
            verify=False,
            auth=(api_user, api_password),
            headers={"Content-Type": "application/json"},
        )

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.API_BASE}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def get_workspaces(self) -> dict:
        """获取项目空间列表"""
        return self._get("/workspaces/projects")

    def get_stories(self, workspace_id: int, page: int = 1, limit: int = 50) -> dict:
        """获取需求列表"""
        return self._get("/stories", {"workspace_id": workspace_id, "page": page, "limit": limit})

    def get_bugs(self, workspace_id: int, page: int = 1, limit: int = 50) -> dict:
        """获取缺陷列表"""
        return self._get("/bugs", {"workspace_id": workspace_id, "page": page, "limit": limit})

    def get_tasks(self, workspace_id: int, page: int = 1, limit: int = 50) -> dict:
        """获取任务列表"""
        return self._get("/tasks", {"workspace_id": workspace_id, "page": page, "limit": limit})

    def get_test_cases(self, workspace_id: int, page: int = 1, limit: int = 50) -> dict:
        """获取测试用例"""
        return self._get("/tcases", {"workspace_id": workspace_id, "page": page, "limit": limit})

    def get_test_plans(self, workspace_id: int) -> dict:
        """获取测试计划"""
        return self._get("/test_plans", {"workspace_id": workspace_id})

    def test_connection(self) -> dict:
        try:
            data = self.get_workspaces()
            workspaces = data.get("data", [])
            return {"success": True, "message": "TAPD 连接成功", "data": {"workspaces_count": len(workspaces)}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"认证失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
