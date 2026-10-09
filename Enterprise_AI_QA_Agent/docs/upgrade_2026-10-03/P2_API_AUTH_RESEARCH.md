# P2 API 鉴权输入与真实受保护接口研究

## 研究依据

- 现有实现：`src/modes/api_testing_mode/credential_manager.py`、`campaign_state.py`、`executor.py` 和 API 运行时恢复逻辑。
- 项目课程：`项目借鉴/llm-testing-course/接口测试-通用篇.html` 的 6.2、7.2 和“登录/鉴权接口”章节。课程要求登录成功后的 token 必须实际调用后续受保护接口；401 需要检查 Authorization、token 有效期；鉴权方式需要区分 token、session/cookie 和权限。
- 真实复现：新增 `tests/test_api_authentication_live.py`，使用标准库 `ThreadingHTTPServer` 作为受控受保护目标，仍通过生产 `CredentialManager`、`ApiTaskExecutor` 和 `httpx` 发请求。

## 复现事实

当前代码对用户输入 `auth_type=cookie` 无法解析多 Cookie 字符串，而是固定生成 `session=<整串输入>`。受保护服务要求 `sid=abc; tenant=blue` 时实际返回 401。

当前代码声明了 `AUTH_CUSTOM`，但 `create_from_user_input()` 没有读取 `headers`，生产请求不会带 `X-API-Key`，实际返回 401。

动态登录返回 `access_token` 的 Bearer 路径已经可以工作，本轮以真实 HTTP 请求保留回归。

复现命令：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_authentication_live.py -q --tb=short
```

修复前结果为 `2 failed, 1 passed in 2.56s`；失败 XML 保存在 `p2-api-auth-before.xml`。

## 修复边界

首期只修复已有契约明确支持的输入：

1. Cookie 字符串按 Cookie 名值对解析，或接受已结构化的 Cookie 字典，再由执行器生成标准 `Cookie` 请求头。
2. `AUTH_CUSTOM` 使用用户明确提供的自定义 headers；不把凭据写入日志或普通报告。
3. 保留 Bearer、Basic、API key 和动态登录的现有行为；没有臆造刷新接口、刷新 token 字段或自动续期协议。

`expires_at` 当前只是状态字段，刷新需要项目接口契约明确提供刷新端点、请求参数和副作用规则；本轮只记录它仍是未完成项，不用猜测实现。

## 数据边界

凭据只存在当前 API 测试运行内存和请求头；测试服务仅保存认证方式是否匹配及请求次数，不保存真实凭据。没有新增依赖、配置键、表或迁移；配置仍走 MySQL，运行/结果仍走 PostgreSQL，原件/证据仍走 RustFS。
