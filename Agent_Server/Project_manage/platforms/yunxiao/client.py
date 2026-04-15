import httpx


class YunxiaoClient:
    """
    阿里云效 (Yunxiao) 客户端
    认证方式: Personal Access Token
    文档: https://help.aliyun.com/document_detail/460465.html
    """

    API_BASE = "https://devops.aliyuncs.com"

    def __init__(self, api_token: str, organization_id: str = ""):
        self.organization_id = organization_id
        self.client = httpx.Client(
            timeout=30,
            verify=False,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"},
        )

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.API_BASE}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict = None) -> dict:
        resp = self.client.post(f"{self.API_BASE}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()

    def get_organizations(self) -> dict:
        """获取组织列表"""
        return self._get("/organization/list")

    def get_projects(self) -> dict:
        """获取项目列表"""
        params = {"organizationId": self.organization_id} if self.organization_id else {}
        return self._get("/project/list", params)

    def get_sprints(self, project_id: str) -> dict:
        """获取迭代列表"""
        return self._get(f"/project/{project_id}/sprints")

    def get_work_items(self, project_id: str, page: int = 1, per_page: int = 50) -> dict:
        """获取工作项列表"""
        return self._get(f"/project/{project_id}/workitems", {"page": page, "perPage": per_page})

    def get_test_cases(self, project_id: str, page: int = 1, per_page: int = 50) -> dict:
        """获取测试用例"""
        return self._get(f"/testhub/project/{project_id}/testcases", {"page": page, "perPage": per_page})

    def test_connection(self) -> dict:
        try:
            data = self.get_organizations()
            orgs = data.get("organizations", data.get("data", []))
            return {"success": True, "message": "云效连接成功", "data": {"organizations_count": len(orgs)}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"认证失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
