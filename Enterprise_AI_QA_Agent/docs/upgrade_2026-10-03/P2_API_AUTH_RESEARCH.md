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

## 2026-10-11 刷新链路补验与根因修复

前一版结论只证明登录返回的 token 可以调用后续接口，并把“刷新端点/刷新 token 协议”列为未验收；没有凭空假设任何字段。继续查阅 `CapabilityMapper`、`DependencyPlanner`、`ApiTaskCoordinator`、`CredentialManager` 和接口测试课程登录章节后，确认项目已有一个可复用的显式刷新端点契约：路径被识别为 `refresh_token`，刷新任务属于认证任务，成功响应通过现有 `access_token` 提取逻辑生成新的 CredentialSession，后续任务应该使用最新 session。

真实本地 HTTP 验收先发现缺口：登录返回 `token-1`、刷新接口返回 `token-2` 均成功，但 `/profile` 仍带 `Bearer token-1`。根因是协调器只更新没有 `auth_ref` 的任务；登录传播后，依赖刷新任务的 `/profile` 已经有旧引用，刷新完成时没有被替换。修复 `ApiTestCoordinator`，让认证任务更新后续认证任务以及明确依赖该认证任务的未执行任务，并记录不含凭据的结构化传播日志；没有该认证依赖的任务保留显式凭据。

修复后真实 `ThreadingHTTPServer`、`CapabilityMapper`、`DependencyPlanner`、`ApiTaskCoordinator`、`ApiTaskExecutor` 和 `httpx` 链路验证登录→刷新→受保护接口 **4通过，2.87秒**；物理请求依次为 `/login`（无认证）、`/auth/refresh`（`token-1`）和 `/profile`（`token-2`）。完整鉴权回归 `6通过，11.86秒`；API 依赖与恢复回归 `28通过、2跳过，4.53秒`。

这次补验只覆盖项目明确提供刷新端点且刷新响应含可识别访问 token 的协议。`expires_at` 自动续期、不同刷新请求体/响应字段、Cookie 续期、并发刷新去重和默认 Agent 自主选择仍未实现或验收；系统不会仅凭过期时间猜测刷新 URL 或字段，也不把 401 自动重试写请求。
