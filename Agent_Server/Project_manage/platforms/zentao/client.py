"""
禅道 API 客户端

支持禅道开源版 16.x ~ 21.x，自动探测 URL 前缀、API 版本和密码格式。

Token 端点 & 业务接口版本对应：
  - APIv1: POST /api.php/v1/tokens        业务接口: GET/POST /api.php/v1/...
  - APIv2: POST /api.php/v2/users/login   业务接口: GET/POST /api.php/v2/...

URL 路由差异：
  - 重写 ON:  /api.php/v1/tokens
  - 重写 OFF: /index.php/api.php/v1/tokens
  - 子目录:   /zentao/api.php/v1/tokens

base_url 填写 http://host:port 即可，末尾的 /index.php 会被自动去除。
"""
import hashlib
import httpx
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _parse_json_safe(resp: httpx.Response) -> dict:
    """安全解析响应 JSON，HTML 登录页/空响应体均抛出 ValueError。"""
    content_type = resp.headers.get("content-type", "")
    body = resp.text.strip()
    if not body:
        raise ValueError(
            f"禅道返回空响应体（HTTP {resp.status_code}），该路径可能不存在"
        )
    if "text/html" in content_type or body.startswith("<!"):
        raise ValueError(
            f"返回 HTML 登录页（HTTP {resp.status_code}），路径或密码错误"
        )
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"响应非合法 JSON（HTTP {resp.status_code}）: {e} | body: {body[:200]}"
        )


def _normalize_base_url(url: str) -> str:
    """
    去掉 base_url 末尾的 /index.php 或多余的 /。
    http://host:82/index.php → http://host:82
    """
    url = url.rstrip("/")
    for suffix in ("/index.php", "/zentao/index.php"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url


class ZentaoClient:
    """
    禅道 API 客户端。

    自动探测可用的 Token 端点和 URL 前缀，探测成功后缓存配置，
    后续业务接口（Bug/产品/用例）使用同一套版本和前缀。
    """

    # 候选探测组合: (url_prefix, use_md5_password, token_path)
    _CANDIDATES = [
        # ── APIv1 (开源版最广泛) ─────────────────────────────────────────
        ("",           False, "/api.php/v1/tokens"),
        ("",           True,  "/api.php/v1/tokens"),
        ("/index.php", False, "/api.php/v1/tokens"),
        ("/index.php", True,  "/api.php/v1/tokens"),
        ("/zentao",    False, "/api.php/v1/tokens"),
        ("/zentao",    True,  "/api.php/v1/tokens"),
        # ── APIv2 (较新版本) ─────────────────────────────────────────────
        ("",           False, "/api.php/v2/users/login"),
        ("",           True,  "/api.php/v2/users/login"),
        ("/index.php", False, "/api.php/v2/users/login"),
        ("/index.php", True,  "/api.php/v2/users/login"),
        ("/zentao",    False, "/api.php/v2/users/login"),
        ("/zentao",    True,  "/api.php/v2/users/login"),
    ]

    def __init__(self, base_url: str, account: str, password: str):
        self.base_url = _normalize_base_url(base_url)
        self.account = account
        self.password = password
        self._token: str | None = None
        self._token_expire: datetime | None = None
        self._api_prefix: str | None = None
        self._use_md5: bool | None = None
        self._token_path: str | None = None
        self._api_version: str | None = None

    async def _ensure_token(self):
        if self._token and self._token_expire and datetime.now() < self._token_expire:
            return
        await self._refresh_token()

    async def _refresh_token(self):
        """遍历候选组合直到成功，记忆 api_prefix / api_version 用于后续请求。"""
        if (
            self._api_prefix is not None
            and self._use_md5 is not None
            and self._token_path is not None
        ):
            candidates = [(self._api_prefix, self._use_md5, self._token_path)]
        else:
            candidates = self._CANDIDATES

        errors: dict[str, str] = {}

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for prefix, use_md5, token_path in candidates:
                url = f"{self.base_url}{prefix}{token_path}"
                pw = _md5(self.password) if use_md5 else self.password
                label = (
                    f"prefix='{prefix}', "
                    f"api='{token_path}', "
                    f"pwd={'MD5' if use_md5 else '明文'}"
                )
                try:
                    resp = await client.post(
                        url, json={"account": self.account, "password": pw}
                    )
                    if resp.status_code >= 400:
                        errors[label] = f"HTTP {resp.status_code}"
                        continue
                    data = _parse_json_safe(resp)
                    token = data.get("token")
                    if not token:
                        errors[label] = f"响应无 token 字段: {list(data.keys())}"
                        continue

                    self._token = token
                    self._token_expire = datetime.now() + timedelta(minutes=55)
                    self._api_prefix = prefix
                    self._use_md5 = use_md5
                    self._token_path = token_path
                    self._api_version = "v2" if "v2" in token_path else "v1"
                    logger.info(f"[Zentao] Token 获取成功 ({label})")
                    return

                except ValueError as e:
                    errors[label] = str(e)
                    logger.debug(f"[Zentao] 探测失败 {label}: {e}")
                except (httpx.ConnectError, httpx.TimeoutException):
                    raise
                except Exception as e:
                    errors[label] = str(e)

        detail = "\n".join(f"  {k}: {v}" for k, v in errors.items())
        raise RuntimeError(
            f"禅道 Token 获取失败（已尝试 {len(errors)} 种组合）：\n{detail}\n\n"
            f"排查建议：\n"
            f"  1. base_url 只填 http://host:port，无需加 /index.php\n"
            f"  2. 检查账号/密码（在浏览器登录验证）\n"
            f"  3. 禅道后台「后台→安全」关闭「强制修改弱密码」\n"
            f"  4. 禅道后台「后台→系统→应用」确认已开启 API 访问"
        )

    def _api_url(self, path: str) -> str:
        prefix = self._api_prefix or ""
        return f"{self.base_url}{prefix}{path}"

    def _headers(self) -> dict:
        return {"Token": self._token} if self._token else {}

    def _ver(self) -> str:
        return self._api_version or "v1"

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        await self._ensure_token()
        url = self._api_url(path)
        if "json" in kwargs:
            logger.debug(f"[Zentao] {method} {url} body={kwargs['json']}")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.request(
                method, url, headers=self._headers(), **kwargs
            )
            resp.raise_for_status()
            data = _parse_json_safe(resp)
            if isinstance(data, dict) and data.get("result") == "fail":
                msg = data.get("message") or data.get("msg") or str(data)
                logger.error(f"[Zentao] 业务层错误 ({method} {url}): {msg}")
                raise ValueError(f"禅道拒绝请求: {msg}")
            return data

    async def get_products(self) -> list:
        """获取产品列表"""
        ver = self._ver()
        if ver == "v2":
            data = await self._request("GET", "/api.php/v2/products")
            if isinstance(data, dict):
                return data.get("products", data.get("data", []))
            return data
        else:
            data = await self._request("GET", "/api.php/v1/products")
            if isinstance(data, dict):
                return data.get("products", [])
            return data

    async def create_bug(
        self,
        product_id: int,
        title: str,
        steps: str,
        severity: int = 3,
        pri: int = 3,
        bug_type: str = "codeerror",
        opened_build: list | None = None,
        extra: dict | None = None,
    ) -> dict:
        """创建 Bug（自动适配 v1/v2 字段名）"""
        await self._ensure_token()
        ver = self._ver()
        if ver == "v2":
            body: dict = {
                "productID": product_id,
                "title": title,
                "openedBuild": opened_build or ["trunk"],
                "severity": severity,
                "pri": pri,
                "type": bug_type,
                "steps": steps,
            }
            path = "/api.php/v2/bugs"
        else:
            body = {
                "product": product_id,
                "title": title,
                "openedBuild": ",".join(str(b) for b in (opened_build or ["trunk"])),
                "severity": severity,
                "pri": pri,
                "type": bug_type,
                "steps": steps,
            }
            path = "/api.php/v1/bugs"
        if extra:
            body.update(extra)
        logger.info(f"[Zentao] 创建 Bug: path={path}, product_id={product_id}, title={title[:50]!r}")
        result = await self._request("POST", path, json=body)
        logger.info(f"[Zentao] 创建 Bug 响应: {result}")
        return result

    async def get_bug(self, bug_id: int) -> dict:
        """获取 Bug 详情"""
        await self._ensure_token()
        ver = self._ver()
        return await self._request("GET", f"/api.php/{ver}/bugs/{bug_id}")

    async def get_product_bugs(self, product_id: int, limit: int = 200) -> list:
        """获取产品 Bug 列表"""
        await self._ensure_token()
        ver = self._ver()
        if ver == "v2":
            data = await self._request(
                "GET", f"/api.php/v2/products/{product_id}/bugs", params={"limit": limit}
            )
            if isinstance(data, dict):
                return data.get("bugs", data.get("data", []))
            return data
        else:
            data = await self._request(
                "GET", "/api.php/v1/bugs", params={"product": product_id, "limit": limit}
            )
            if isinstance(data, dict):
                return data.get("bugs", [])
            return data

    async def get_test_case(self, case_id: int) -> dict:
        """获取测试用例详情"""
        await self._ensure_token()
        ver = self._ver()
        return await self._request("GET", f"/api.php/{ver}/testcases/{case_id}")

    async def get_product_cases(self, product_id: int, limit: int = 500) -> list:
        """获取产品测试用例列表"""
        await self._ensure_token()
        ver = self._ver()
        if ver == "v2":
            data = await self._request(
                "GET", f"/api.php/v2/products/{product_id}/testcases", params={"limit": limit}
            )
            if isinstance(data, dict):
                return data.get("testcases", data.get("data", []))
            return data
        else:
            data = await self._request(
                "GET", "/api.php/v1/testcases", params={"product": product_id, "limit": limit}
            )
            if isinstance(data, dict):
                return data.get("testcases", [])
            return data

    async def get_test_suite_cases(self, suite_id: int) -> list:
        """获取测试套件下的用例"""
        ver = self._ver()
        data = await self._request("GET", f"/api.php/{ver}/testsuites/{suite_id}")
        if isinstance(data, dict):
            return data.get("testcases", data.get("cases", [data]))
        return data

    async def test_connection(self) -> dict:
        """测试禅道连接"""
        try:
            await self._refresh_token()
            products = await self.get_products()
            return {
                "success": True,
                "message": (
                    f"连接成功！API {self._api_version}，"
                    f"URL前缀: '{self._api_prefix or '无'}', "
                    f"密码格式: {'MD5' if self._use_md5 else '明文'}，"
                    f"产品数: {len(products) if isinstance(products, list) else '?'}"
                ),
                "products": products,
                "api_prefix": self._api_prefix,
                "api_version": self._api_version,
            }
        except httpx.ConnectError as e:
            return {"success": False, "message": f"无法连接禅道服务器：{e}"}
        except httpx.TimeoutException:
            return {"success": False, "message": "连接禅道超时，请检查网络或服务器状态"}
        except (RuntimeError, ValueError) as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {type(e).__name__}: {e}"}
