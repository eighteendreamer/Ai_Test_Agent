import httpx


class JiraClient:
    """
    Jira Cloud / Server 客户端
    认证方式: Basic Auth (account + API Token)
    文档: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
    """

    def __init__(self, base_url: str, account: str, api_token: str, api_version: str = "3"):
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.client = httpx.Client(
            timeout=30,
            verify=False,
            auth=(account, api_token),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(f"{self.base_url}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict = None) -> dict:
        resp = self.client.post(f"{self.base_url}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()

    def get_myself(self) -> dict:
        """获取当前用户信息"""
        return self._get(f"/rest/api/{self.api_version}/myself")

    def get_projects(self, max_results: int = 50) -> dict:
        """获取项目列表"""
        return self._get(f"/rest/api/{self.api_version}/project/search", {"maxResults": max_results})

    def get_issues(self, jql: str = "", start_at: int = 0, max_results: int = 50) -> dict:
        """搜索 Issue"""
        return self._get(
            f"/rest/api/{self.api_version}/search",
            {"jql": jql, "startAt": start_at, "maxResults": max_results},
        )

    def get_issue(self, issue_key: str) -> dict:
        """获取单个 Issue"""
        return self._get(f"/rest/api/{self.api_version}/issue/{issue_key}")

    def create_issue(self, fields: dict) -> dict:
        """创建 Issue"""
        return self._post(f"/rest/api/{self.api_version}/issue", {"fields": fields})

    def get_test_cases(self, project_key: str, max_results: int = 50) -> dict:
        """获取测试用例 (issuetype=Test)"""
        return self.get_issues(jql=f'project = "{project_key}" AND issuetype = "Test"', max_results=max_results)

    def test_connection(self) -> dict:
        try:
            user = self.get_myself()
            return {"success": True, "message": "Jira 连接成功",
                    "data": {"display_name": user.get("displayName"), "email": user.get("emailAddress")}}
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"认证失败 (HTTP {e.response.status_code})"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    def close(self):
        self.client.close()
