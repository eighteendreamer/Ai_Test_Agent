import base64
from typing import Any, Optional

import httpx


class RestProjectClient:
    """Configurable REST client that can fetch project-like rows from generic APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        account: str = "",
        password: str = "",
        api_token: str = "",
        auth_type: str = "none",
        auth_header_name: str = "Authorization",
        auth_header_prefix: str = "",
        project_path: str = "/api/projects",
        response_list_path: str = "",
        id_field: str = "id",
        name_field: str = "name",
        code_field: str = "code",
        status_field: str = "status",
        scope_field: str = "",
        description_field: str = "description",
        custom_headers: Optional[dict[str, Any]] = None,
        query_params: Optional[dict[str, Any]] = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.account = account or ""
        self.password = password or ""
        self.api_token = api_token or ""
        self.auth_type = (auth_type or "none").lower()
        self.auth_header_name = auth_header_name or "Authorization"
        self.auth_header_prefix = auth_header_prefix or ""
        self.project_path = project_path or "/api/projects"
        self.response_list_path = response_list_path or ""
        self.id_field = id_field or "id"
        self.name_field = name_field or "name"
        self.code_field = code_field or "code"
        self.status_field = status_field or "status"
        self.scope_field = scope_field or ""
        self.description_field = description_field or "description"
        self.custom_headers = custom_headers or {}
        self.query_params = query_params or {}
        self.client = httpx.Client(timeout=30, verify=False)

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        for key, value in self.custom_headers.items():
            if value is not None:
                headers[str(key)] = str(value)

        if self.auth_type == "basic":
            credential = f"{self.account}:{self.api_token or self.password}"
            encoded = base64.b64encode(credential.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif self.auth_type == "bearer":
            token = self.api_token or self.password
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif self.auth_type == "header":
            token = self.api_token or self.password
            if token:
                prefix = f"{self.auth_header_prefix} " if self.auth_header_prefix else ""
                headers[self.auth_header_name] = f"{prefix}{token}".strip()

        return headers

    def _get(self, path: str) -> Any:
        if not path.startswith(("http://", "https://")):
            path = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        resp = self.client.get(path, headers=self._auth_headers(), params=self.query_params)
        resp.raise_for_status()
        return resp.json()

    @classmethod
    def _extract_value(cls, data: Any, path: str, default: Any = "") -> Any:
        if not path:
            return data
        current = data
        for segment in str(path).split("."):
            if current is None:
                return default
            if isinstance(current, dict):
                current = current.get(segment, default)
                continue
            if isinstance(current, list):
                try:
                    current = current[int(segment)]
                except (ValueError, IndexError):
                    return default
                continue
            return default
        return current

    def get_projects(self) -> list[dict[str, Any]]:
        payload = self._get(self.project_path)
        items = self._extract_value(payload, self.response_list_path, payload if isinstance(payload, list) else [])
        if not isinstance(items, list):
            return []

        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row_id = self._extract_value(item, self.id_field, "")
            row_name = self._extract_value(item, self.name_field, "")
            row_code = self._extract_value(item, self.code_field, "") or row_id
            row = {
                "id": str(row_id or ""),
                "name": str(row_name or ""),
                "code": str(row_code or ""),
                "status": str(self._extract_value(item, self.status_field, "") or ""),
                "scope": str(self._extract_value(item, self.scope_field, "") or ""),
                "description": str(self._extract_value(item, self.description_field, "") or ""),
            }
            if row["id"]:
                rows.append(row)
        return rows

    def test_connection(self) -> dict[str, Any]:
        try:
            count = len(self.get_projects())
            return {
                "success": True,
                "message": f"连接成功，已获取到 {count} 条远端项目记录",
            }
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "message": f"认证或接口请求失败 (HTTP {exc.response.status_code})",
            }
        except Exception as exc:
            return {"success": False, "message": f"连接失败: {exc}"}

    def close(self):
        self.client.close()
