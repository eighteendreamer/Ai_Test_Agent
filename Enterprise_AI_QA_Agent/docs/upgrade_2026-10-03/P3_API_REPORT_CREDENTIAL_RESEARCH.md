# API 报告中的实际凭据值与截断边界

基线 `4f77be93`。本批接续 7.66 已确认的四个 Cookie 报告回归失败，范围为公开报告、失败证据和 Worker 公开结果，不实现自动 401 续期。

## 依据与根因

- `CredentialManager` 已保存 Token、认证 Header、Cookie 属性及旋转前后的不可变会话。应使用这些明确来源的值，不能把任意 `token` 或 `opaque_echo` 字段全局认定为凭据。
- `OutputSafetyPolicy` 已提供字段规则、文本规则和递归公开投影；`ReportBuilder` 原来使用无运行上下文的策略，因此真实 `/api/profile` 返回的 `opaque_echo` 会泄漏 Cookie。
- `ReportBuilder._truncate_body/_build_evidence_snippet` 先截断再生成文本。凭据跨 500/2000/4000 字符边界时，只掩盖完整值不能清除已截断的前缀。
- 私有 Worker runner 已有已知值脱敏，但只匹配整个字符串，嵌在错误说明、响应文本中的值仍可能漏出。应复用既有公共策略，不再维护第二套递归实现。
- `ApiTaskExecutor` 和依赖绑定需要真实响应；直接修改执行对象或可恢复快照可能破坏断言与传值。此次仅处理报告副本及原本就应公开的 Worker 投影。
- 本地课程 `项目借鉴/llm-testing-course/接口测试-通用篇.html` 指出登录依赖需辨明 Token/Cookie。已有 Cookie HTTP 和加密引用测试分别验证真实登录、路径属性、旋转、终态及私有原件；沿用这些来源和现有测试设施，不增加依赖或猜测认证机制。

## 复现与采用做法

生产 Runtime 对真实本地 Cookie HTTP 执行登录重定向、刷新和读取；正负断言分别经过本地 Coordinator 与受控 Worker runner。修复前 5 项全部失败，见 `p3-api-report-credential-before.xml/log`。该批失败信息只报告布尔事实，未导出随机 Cookie 或 Token 值。

1. `OutputSafetyPolicy` 增加可选的已知敏感值集合，默认行为不变。字符串采用转义后的字面匹配，较长值优先、单次替换，支持重叠值和正则特殊字符。
2. `CredentialManager.sensitive_values()` 从当前实例实际持有的会话收集值，包含旋转前的会话。`ReportBuilder` 接收该实例，为公开副本提供上下文；不通过 HTTP 任意字段推断凭据。
3. 在截断前处理响应原值；报告字段、失败片段、错误、断言、Markdown、JSON 和工件标签统一走公开投影。保留真实执行响应及数字、布尔、空值、一般 schema/词汇。
4. 私有 Worker runner 使用同一策略处理公开 `task_result`，代替原本只能匹配整个字符串的内部递归函数。加密结果、原执行对象和恢复协议保持原有职责。
5. 沿现有 logger 记录报告身份、任务数量与是否具有凭据上下文，不记录凭据值。没有新表、配置项、依赖或模型协议变更。

最终组装补核又发现 Runtime 会在初始报告之后追加质量评估。用真实 HTTP 失败任务的名称携带本次 Cookie，报告最初已脱敏，随后却被 `failure_classifications.task_name` 重新带入；`p3-api-report-credential-finalization-before.xml/log` **2 失败、6 通过、7.57 秒**。因此 Runtime 的验证/质量评估、错误及建议也在形成公开投影时使用同一上下文，原任务名和执行响应仍保留。没有给某个字段添加特判。

独立 `ReportBuilder()` 不拥有动态凭据上下文；生产 Runtime 已显式接入 CredentialManager。此修复不声称识别未知/编码后的凭据或所有字典键，也不等于历史报告已被批量重写。

## 实际验证

- `p3-api-report-credential-final.xml/log`：**16 通过、1 门控跳过、14.80 秒**。包括真实 HTTP 的两执行路径/正负断言、原响应不变、生成报告额外 HTTP 为 0；三个截断测试先证明原始截断会保留恰好 16 个凭据字符，再核验公开结果无该前缀。共享策略另验证嵌套结构、重叠值和默认无上下文行为。
- `p3-api-cookie-report-product-final.xml/log`：**1 通过、0 跳过、59.66 秒**。隔离 MySQL 配置、PG 项目/会话/加密凭据/ToolJob，真实本地接口，受控启动生产 Worker runner；生产 API、实际构建的完整 App、Chromium 下载和独立 API 重启。共 4 次业务 HTTP（登录、重定向、刷新、读取），3 个任务保持 2 通过/1 失败，公开 Job、报告、失败工件和页面不含生成 Cookie。下载哈希与持久原件一致；新服务沿原 Job 终态回读额外 HTTP 为 0。
- 初次证据审计发现该产品批次记录了一次会话 GET 500；其网络数组没有重启起止标记，因此不能声称网络全绿。沿既有安全报告产品测试的做法，补记录主动重启的起止范围并检查范围外失败；重新打开页面后再次核对三个结果单元格和脱敏。最终交付测试 `p3-api-cookie-report-product-restart.xml/log` **1 通过、0 跳过、81.21 秒**，重启范围外失败为 0，保存两次 API 进程的 stdout/stderr。最终产品证据以 `p3-api-cookie-report-product-restart/` 为准；此前结果仍保留，不相加为两个新功能。
- 该产品批次的三份工件实际均为 PG `inline://artifact`，不是 RustFS 报告下载；URI、字节数和 SHA-256 见 `p3-api-cookie-report-product-final/verification.json`。Worker 启动受控，不是默认 AI 自主规划或独立 Agent CLI 验收。
- `p3-api-report-credential-regression-final.xml/log`：**342 通过、8 门控跳过、95.27 秒**。覆盖原 16 个 API 模块、新报告专项、意图安全、可观测性和 ReportService，并启用真实 Cookie 存储专项。旧四项报告失败已通过；真实存储验证新服务可读属性、凭据加密和终态不重发。8 项其余门控测试未在本批执行，不记为通过。
- 修复上述后追加评估问题后，最终源码/测试重跑同一整组：`p3-api-report-credential-delivery.xml/log` **342 通过、8 门控跳过、92.48 秒**，新增最终评估/错误/建议脱敏断言通过，事实在 `p3-api-report-credential-delivery/`。完整产品也重跑为 `p3-api-cookie-report-product-delivery.xml/log` **1 通过、0 跳过、57.84 秒**，最终下载/重启/进程输出以 `p3-api-cookie-report-product-delivery/` 为准。前述各批是同一实现的逐次验证，不能累加为新增业务覆盖。

测试过程中的错误保留：产品第一轮工件类型断言写成 `api_test_evidence`，实际是已有 `api_test_task_evidence`，**1 失败、25.89 秒**，当时未进入浏览器；第二轮使用了依赖制表符的文本断言，页面已有正确表格但 matcher 使用 textContent，**1 失败、91.33 秒**，改按实际表格单元格断言。回归第一轮漏设已有测试要求的 `QA_API_COOKIE_STORAGE_DIR`，**341 通过、1 失败、8 跳过、100.89 秒**，失败在业务和凭据断言之后的证据目录读取。补齐运行参数后完整重跑。没有为这些测试错误修改生产语义。

`p3-api-report-credential-audit.py` 最终实际执行通过，核对 11 次运行的 XML、99 份原证据、报告/下载的字节与哈希、脱敏值和重启范围外网络结果；输出清单为 `p3-api-report-credential-audit.json`。审计不能替代前述真实功能执行。变更源码及测试的 compileall、限定范围 diff 检查通过，命令和返回码见 `p3-api-report-credential-code-checks.json`。复现入口在 Agent_Server 中为：

```powershell
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_api_report_credentials_http.py tests/test_api_cookie_session_http.py -q
# 完整产品入口需要独立的新输出目录；隔离资源由既有 fixture 创建和清理。
$env:RUN_LIVE_API_COOKIE_REPORT_PRODUCT='1'
$env:QA_API_COOKIE_REPORT_PRODUCT_DIR='<新的证据目录绝对路径>'
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_api_report_credentials_product.py -q
```

## 交付边界与下一步

本批关闭已知值回显及三个报告截断边界的确认缺陷，新增原件下载和打开浏览器后 API 重启的产品证据。配置仍在 MySQL，业务/凭据/本次报告仍在 PG，模型继续走各自原生适配器；Anthropic 仍为 Messages。

自动 401 续期与安全重放仍缺任务关系/持久检查点实现，按 `P3_API_401_REFRESH_RESEARCH.md` 继续调研，不猜刷新 URL、不重置已完成任务。默认 AI 项目选取→排序→补全→执行、完整准备生命周期与计划修订、复杂 UI 和失败定位、持续性能/安全、需求业务评审/部署/历史迁移、完整调用追踪及两份旧 Worker 原件等仍未全部验收。Step 已认证；外部 AI 沿用户此前安排暂缓。本批不宣称完整 P0—P5 完成。
