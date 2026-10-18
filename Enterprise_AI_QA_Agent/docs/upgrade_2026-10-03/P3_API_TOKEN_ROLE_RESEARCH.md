# API 登录响应的 Token 角色与显式字段路径

基线 `b3f02db8`。在继续 401 续期前复核基础凭据提取，发现可复现的角色混淆。本批范围为已有登录响应提取与文档提示，不实现 401 自动调度。

## 依据与根因

- `credential_manager.py:create_from_login_response` 首先读取配置字段或 `token`，失败后把任意名称包含 `token` 的长字符串作为 Bearer。只有 `refresh_token` 或 `id_token` 时也会得到可用凭据；显式自定义字段缺失时会静默使用其他字段。
- 同处使用 `str(...)` 转换值，可能把对象、数组、数字当成凭据。已有 `auth_token_field` 会沿 Runtime、两协调器、加密 dispatch 和 Worker 原样传递，但只支持顶层读取，嵌套路径无法使用。
- `doc_parser.py:_detect_auth_hint` 取正则第一次子串匹配。`refresh_token` 可误匹配为 `token`，前面的泛称 token 可盖过后面的明确 `access_token`。
- 本地课程 `项目借鉴/llm-testing-course/接口测试-通用篇.html` 要求从文档明确登录返回的 Token/Cookie 及实际响应依赖；已有 `input_binding.extract_path_value` 支持点路径与数组索引，可复用而无需新增路径语言。
- [RFC 6749 §1.5](https://www.rfc-editor.org/rfc/rfc6749#section-1.5) 明确区分刷新凭据与资源访问凭据。此依据用于停止自动猜测角色，不限制项目显式指定其自定义字段。

## 先复现

`test_api_token_role_http.py` 使用随机临时凭据和真实本地 HTTP，覆盖直接 Coordinator 及受控启动的生产 Worker runner。修复前 **21失败、9通过、15.35秒**。其中 HTTP 复现为 refresh/id 被发送到资源接口、嵌套映射取到备用 token、自定义路径缺失仍使用无关 token；其余为类型与文档提示断言，不能把 21 项都说成独立业务故障。pytest原失败输出包含随机临时测试凭据，本轮提交仅保存`p3-api-token-role-before-redacted.xml/log`脱敏副本；替换完整及截断的临时凭据表示，不改变失败数量、断言类别和时间。原始本地文件不进入Git；对应测试断言随后改为布尔事实，避免未来失败时打印值。

## 采用做法

1. 默认只兼容明确的 `access_token`、`accessToken`、`token` 顶层字段，按此优先级读取；不扫描任意 token 子串。
2. 已有显式 `auth_token_field` 优先精确键，再复用既有点/数组路径。显式字段缺失或无效时不回退到其他字段。仅接受非空、没有换行的字符串，不把结构化值字符串化。
3. 文档提示匹配完整字段名称，访问字段优先于泛称；不自动选择 refresh/id token。任意自定义字段仍通过已有显式参数传入，不新建配置存储或模型层。
4. 保持旧凭据不可变、Cookie 旋转及已有期限继承语义，缺失字段留下无敏感值的日志。不会猜刷新 URL、自动重放或改变负向 401 断言。

## 验证与限制

修复后 `p3-api-token-role-after.xml/log`：**30通过、13.72秒**，包括16个本地HTTP变体（8场景 × 2执行路径）、7个非法值和7个文档提示。请求事件只记录角色，不导出随机凭据。终态再次执行没有新增请求。

启用 `RUN_LIVE_API_TOKEN_ROLE_STORAGE=1` 后，`p3-api-token-role-storage.xml/log`：**2通过、30排除、16.86秒**。隔离 MySQL/PG/Redis/RustFS 环境中，生产 ToolRuntime 与 Worker runner 消费加密 dispatch；嵌套字段完成登录→资源访问，refresh-only 不形成可用访问凭据，资源请求不带 Bearer 且按预期401通过。新建服务核验冻结字段、加密行与不含已选凭据的公开 Job/消息，终态沿原 Job 回读，额外HTTP为0。此处没有跨进程 kill 或 RustFS 报告原件下载验收。

受影响16模块回归 `p3-api-token-role-regression.xml/log`：**196通过、4失败、9门控跳过、71.95秒**。4项都失败于原有 Cookie HTTP 测试的报告脱敏断言：报告响应投影中的 `opaque_echo` 含 Cookie 值。使用 HEAD 原版凭据提取方法重新执行真实 HTTP 测试，同一断言仍失败（`p3-api-token-role-baseline-cookie-verified.log`）；这是现有报告呈现路径的缺口，不由新 Token 选择逻辑造成，但仍须修复，不能报告整体回归通过。首次复核脚本缺少 tests 导入路径，错误见当次工具 stderr；修正导入后才完成上述复核。

compileall 与本批生产 diff 检查均退出0。当前 Anthropic 原生SDK实际HTTP故障专项另行复核13通过、46排除、24.20秒；仍是 `/v1/messages` 与 messages 请求体，包装函数为 `await_provider_io`，并未切换协议。此为现有能力复核，不计算成新实现。

修改测试断言避免打印临时凭据后，对最终交付的测试版本重新启用真实存储执行全专项：`p3-api-token-role-final.xml/log` **32通过、0失败、0跳过、44.84秒**。该批与前面30项/2项的覆盖重叠，不累加为64项，也不消除上述报告回归失败。

默认 Agent 如何理解任意项目文档并选择字段仍需质量验收；本批的显式路径支持不等于完整字段映射产品页面或401自动续期。Cookie 报告中动态凭据值的上下文脱敏列为下一项已确认缺陷；当前不能宣称全部鉴权/报告闭环通过。
