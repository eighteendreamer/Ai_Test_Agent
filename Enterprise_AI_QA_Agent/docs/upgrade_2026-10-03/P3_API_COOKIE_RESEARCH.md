# API Set-Cookie 登录、旋转与可恢复属性研究

基线 `f6402ec9`。本次处理已有 Cookie 输入与仍缺失的动态 Cookie 会话，不引入通用 401 重试，不改变模型协议。

## 依据与复现

- `CredentialManager` 已支持用户 Cookie 字符串；`ApiTaskExecutor._handle_dynamic_login` 只读取 JSON Token。真实 204 Cookie 登录和重定向登录之后没有可传递的 Cookie 凭据。
- 现有 `cookie_jar: dict[str,str]` 无法表达同名 Cookie 的不同 domain/path；将 Set-Cookie 扁平拼进 Cookie 头会丢失 Secure/Expires/删除语义。沿当前依赖传播、Worker 和加密引用链路补同一会话的属性快照，不做全局 Cookie 状态。
- 查证已安装 HTTPX 的 `Cookies`、`extract_cookies`、`AsyncClient._build_redirect_request` 与标准库 `Cookie` 构造签名。HTTPX 已用标准库 CookieJar 处理服务端 Cookie 和重定向。依据：[HTTPX Clients](https://www.python-httpx.org/advanced/clients/)、[Python 3.11 http.cookiejar](https://docs.python.org/3.11/library/http.cookiejar.html)。使用现有库读取客户端最终 jar，再用公开 Cookie 属性恢复；不自写 Set-Cookie 解析器。
- `CredentialSession` 的 JSON 通过现有 PG `PostgresApiCredentialStore` 加密、追加且校验内容哈希；给所有旧会话强行加入新空字段会制造同身份哈希冲突。参照 `ModelInvocationRequest.preserve_legacy_request` 的 wrap serializer：旧会话缺快照时不输出新字段，新快照显式记录空数组也可以表示删除全部 Cookie。
- `OutputSafetyPolicy` 已脱敏整个 credential_session 与 Set-Cookie；`runtime.py` 还依据已知具体值处理自定义响应字段。新增快照需要同步这两处消费者，公开输出不承载执行凭据。

首次真实 HTTP：`p3-api-cookie-before.xml` **4 failed、1 passed，6.83 秒**。本地和已有受控 Worker 的直接 204、303 重定向登录均未完成 Cookie 登录→刷新→查询链；旧用户输入序列化兼容检查通过。失败请求与报告只保存 Cookie 名称/来源标签，不保存值。

## 做法与验收范围

新增可选的私有 Cookie 属性快照，由 HTTPX 客户端捕获并由 CookieJar 恢复；以现有客户端 Cookie 机制发请求，使 path/domain/Secure/过期和删除由已有实现处理。新快照追加新凭据身份，不修改旧凭据；JSON 存在现有加密 PG 行内，不新增表或配置。旧用户 Cookie 头输入仍沿原明确输入语义兼容。

验证直接/重定向登录、204 旋转、同名不同路径、Max-Age=0 删除、异域拒收、不同 hostname 不发送、路径外不发送、HTTP 不发送 Secure Cookie、过期快照不发送、属性恢复、公开输出脱敏和终态重投。CookieJar 是 HTTP 客户端规则，不宣称实现浏览器 SameSite/Partitioned 语义；保留标准 Cookie 属性和 HttpOnly/SameSite 元数据。

默认 AI 完整多账户规划、自动 Token 过期/401 修复、带可靠来源的安全重放、多认证生产进程中断、未知副作用只读查证和执行中计划修订继续属于完整升级剩余范围。外部 AI 本轮不调用，Step 按已认证记录。

## 当前实测结果

- 原四条 Cookie 路径修复后 `p3-api-cookie-after.xml` **5通过，8.55秒**。增加过期恢复、空快照的权威性和预期401不创建登录凭据后，属性专项 `p3-api-cookie-attributes-final.xml` **8通过、1存储门控跳过，8.87秒**，显式核对同名路径、Domain/host-only、Secure、Expires、HttpOnly/SameSite、旋转与删除。
- 最终十一模块 `p3-api-cookie-final2.xml` **83通过、5跳过，25.48秒**，覆盖 Cookie/Bearer、多账户传播、请求体、派发契约、模式隔离、恢复、unknown写入及Worker结果回读；跳过真实存储门控，不计为通过。
- 因本批同步共享 `OutputSafetyPolicy` 的凭据容器脱敏，补跑工具安全门和意图安全路由回归 `p3-api-cookie-safety-regression.xml` **105通过，13.47秒**。该回归验证既有安全边界没有被 Cookie 字段扩展破坏，不代替 Cookie 的实际 HTTP 与加密存储验收。
- 独立真实存储 `p3-api-cookie-storage4.xml` **1通过、8排除，11.03秒**。真实隔离 MySQL/PG/ToolRuntime/ToolJob/Session 与既有RustFS环境，受控Worker实际Cookie重定向登录→204旋转→profile；新服务解密回读后再发一次明确只读profile，返回200；原三个Job携带原身份重投新增HTTP为0。Cookie快照属性一致、旧JSON再次保存无哈希冲突、密文/公开Job/Worker提示均不含随机Cookie值，自定义opaque_echo公开值已脱敏。本轮未要求或证明额外RustFS原件产生；不将环境存在等同于原件链路完成。
- 存储前三次失败分别是夹具漏Session必填参数、错误ToolRegistry模块路径、重投未携带原JobID；均保留log/XML。第三次已完成业务/存储前半段，但不是完整通过。停止自搭重投方式，采用已有 `test_api_worker_auth_refresh_live.py` 的新服务和 `replace(context, tool_job_id=原ID)` 真实恢复路径，第四次通过。
- 首轮广回归 `p3-api-cookie-final.xml` **1失败、71通过、4跳过**：原Bearer实测出现一次写入网络失败，单项 `p3-api-cookie-bearer-reproduce.xml` **1通过**；最终整批复跑通过。该失败未稳定复现，保留为观察项，不凭猜测修改重试或网络行为。
- compileall实际退出0；没有前端契约、配置项、表或模型传输变更。完整目标仍未完成。
