import httpx


class ClickUpClient:
    """
    ClickUp 客户端
    认证方式: Personal API Token
    文档: https://clickup.com/api
    """

    API_BASE = "https://api.clickup.com/api/v2"

    def __init__(self, api_token: str):
        self.client = httpx.Client(
            timeout=30,
            verify=False,
            headers={"Authorization": api_token, "Content-Type": "application/json"},
        )

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.API_BASE}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def get_authorized_user(self) -> dict:
        """获取当前授权用户"""
        return self._get("/user")

    def get_teams(self) -> dict:
        """获取工作区 (team) 列表"""
        return self._get("/team")

    def get_spaces(self, team_id: str) -> dict:
        """获取 Space 列表"""
        return self._get(f"/team/{team_id}/space")

    def get_folders(self, space_id: str) -> dict:
        """获取 Folder 列表"""
        return self._get(f"/space/{space_id}/folder")

    def get_lists(self, folder_id: str) -> dict:
        """获取 List 列表"""
        return self._get(f"/folder/{folder_id}/list")

    def get_tasks(self, list_id: str, page: int = 0) -> dict:
        """获取任务列表"""
        return self._get(f"/list/{list_id}/task", {"page": page})

    def test_connection(self) -> dict:
        try:
            data = self.get_authorized_user()
            user = data.get("user", {})
            return {"success": True, "message": "ClickUp 连接成功",
                    "data": {"username": user.get("username"), "email": user.get("email")}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"认证失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
