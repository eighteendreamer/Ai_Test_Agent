# 2026-10-22 P2 Step 默认多用例复验与 P3 API 专项回归

## P2：Step 默认模型多用例闭环

用户确认 Step 账号认证已完成，本次据此重新运行默认模型真实场景。测试从 MySQL 读取默认模型配置并复制到隔离配置库；结束后核对全局默认模型身份未改变。开始前确认 Redis 测试镜像已在本机、RustFS 健康检查返回 HTTP 200，证据目录不存在。

实际命令（`Agent_Server` 目录）：

```powershell
$env:RUN_LIVE_DEFAULT_COVERAGE_PRODUCTION='1'
$env:QA_DEFAULT_CASE_SHAPE='multi-case-dependency'
$env:QA_DEFAULT_COVERAGE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p2-default-multi-case-step-user-auth-confirmed-current-20261022-01'
$env:QA_FORMAL_EFFECT_RELATIONS='1'
$env:QA_LINKED_SOURCES='1'
$env:QA_EVIDENCE_REFERENCES='1'
$env:QA_EVIDENCE_REFERENCE_PROTOCOL='case-planning-evidence-references-v2'
$env:QA_EFFECT_TASKS='1'
$env:QA_SOURCE_IDENTITY='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_default_coverage_production.py::test_actual_default_model_plans_and_executes_complete_case -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-default-multi-case-step-user-auth-confirmed-current-20261022-01.xml
# 1 failed in 29.64s
```

夹具为规划 outbox 和 Worker 临时启动了 Redis 容器，并使用隔离的 PostgreSQL 数据库及 MySQL 配置表。测试结束后清理完成；随后查询 `docker ps -a --filter name=qa-execution-cli-`，没有发现残留容器。RustFS 使用已有服务。全局默认模型身份仍为 `step-5-preview`。

真实规划任务运行一轮后进入终态 `failed`。模型 SDK 收到 HTTP 400，错误类型为 `request_params_invalid`。该轮物理请求观察器只记录状态码，没有读取错误正文，因此不能确定该轮400的具体原因。随后使用当前 MySQL 默认模型配置，以不落盘的诊断进程发送两条最小流式请求，一条带 `stream_options.include_usage`、一条不带，两者都返回同一 HTTP 400 和正文 `you have no active step plan subscription`。后续诊断直接证明当时该配置的 Step Plan API 订阅不可用，并未证明 `stream_options` 被拒；不能追溯替代原始规划请求缺失的正文。规划任务提示为 `模型调用失败（http_error）；未创建测试运行`。没有持久化 CasePlan、没有创建 Run、业务 HTTP 请求数为0。用户确认账号已认证与后续诊断报告当前密钥没有活动 Step Plan 订阅是两个不同事实。

JUnit 为 `p2-default-multi-case-step-user-auth-confirmed-current-20261022-01.xml`；脱敏 SDK/HTTP 与任务状态证据位于同名目录。默认 Step 链路目前只验证到首次规划请求；项目内用例选择、依赖排序、补全、Run 执行和结果证据均未到达。

### 模型 SDK 与参数映射审查

当前代码已实现传输适配：`resolve_client` 按 `transport` 分派独立客户端，统一接收 `ModelInvocationRequest` 并返回统一结果。Anthropic 客户端调用官方 SDK 的 `client.messages.create`；OpenAI Responses 是另一客户端并调用 `client.responses.create`；OpenAI-compatible Chat Completions 仍调用 `client.chat.completions.create`。`map_model_request` 按 transport 映射结构化输出和推理参数，`ModelRequestProfile` 为具体模型声明输出格式、token 字段和推理约束。

MySQL 当前默认项安全字段为 provider=`openai`、transport=`openai_chat_completions`、model_id=`step-5-preview`，Base URL 为 Step Plan 的 OpenAI 兼容路径。Step 官方文档分别列出 [Chat Completions API](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md)、[Anthropic-compatible Messages API](https://platform.stepfun.com/docs/zh/api-reference/chat/messages-create.md)、[OpenAI-compatible Responses API](https://platform.stepfun.com/docs/zh/api-reference/responses/responses-create.md)；[Step Plan 概览](https://platform.stepfun.com/docs/zh/step-plan/overview.md) 列出对应的两种 Base URL。协议不应按模型名称互换。当前实现保留每个 wire protocol 的独立客户端，不需要再新增一个统一 SDK 客户端；以后只对实际验证出的模型差异扩展 profile/capability。当前 Chat Completions 参数表未列 `stream_options`，但 Step 400 的两条最小请求诊断都被“没有活动订阅”拦截，尚未验证该字段能否使用；需在 Step Plan 订阅恢复后再验证，本次不改该字段。本次未修改模型客户端、profile、依赖或全局模型配置。

模型调用本地回归实际运行：

```powershell
# Agent_Server
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_clients.py tests/test_model_request_profile.py tests/test_model_completion_contract.py -q --tb=short
# 91 passed in 24.93s
```

### Agnes 多用例轨迹复核

按本地《Agent测试专项》的 Plan-and-Execute 计划完整性/依赖检查，以及《Agent轨迹与协议鲁棒性》的工具序列、参数来源和恢复断言，分别核对规划结果与后续证据阶段，不把某个阶段的成功当成完整 Run 通过。

- `p2-multi-case-agnes-current-worktree-20261022-01` 的第1轮逻辑计划正确选中 `dependency-create-order` 为正式前置、`target-query-order` 为正式目标，并建立依赖边。随后效果/工具证据阶段接受绑定回执、查询工具契约，第5轮因“声明任务查询和完成状态必须对应”校验失败；没有发布计划或业务 HTTP。
- `p2-multi-case-agnes-max-rounds8-current-20261022-01` 的逻辑阶段把已有正式创建用例改为 `prepare`，未满足优先复用正式前置的要求。后续效果声明多次因引用形状、任务角色和预期路径不符合固定 Schema 被拒，达到原有180秒总时限后规划失败；没有 Run 或业务 HTTP。
- 较早的 `p2-multi-case-agnes-v2-excerpt-fix-current-20261022-01` 保存过“订单已创建”的空 `no_match` 计划；当前工作树已有逻辑阶段拒绝“无 target 且无 unresolved”的校验，并要求返回具体缺口。该轮是 logic-v7 旧提示版本，不能代表当前校验。

效果子任务适配器会校验固定 task 身份，并要求 query 带查询内容、finish 提交回执。当前失败证据显示是模型输出不符合既定协议，尚未证明适配器存在转换缺陷。当前工作树的规划/效果任务回归实际运行：

```powershell
# Agent_Server
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_stages.py tests/test_planning_effect_tasks.py tests/test_planning_evidence_references.py tests/test_case_planning.py -q --tb=short -rs
# 73 passed, 15 skipped in 6.88s
```

15项跳过要求隔离 MySQL/PostgreSQL/RustFS 或当前已配置真实模型；未用其跳过数替代通过结论。当前范围分类为：固定版本选取、逻辑依赖校验、效果任务契约均有实现和受控回归；真实 Agnes 的正式前置选择尚不稳定，工具/效果证据阶段尚未形成可发布计划。当前未修改规划生产代码。

## P3：API 鉴权与依赖 HTTP 回归

两组测试均在当前工作树重跑。测试通过本机受控 HTTP 服务与项目执行器发送实际请求；未调用外部模型，也没有启动 Docker。

```powershell
# Agent_Server
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_401_refresh_gap.py tests/test_api_401_refresh_automatic_live.py tests/test_api_document_dependency_http.py tests/test_api_dependency_restore_http.py -q --tb=short -rs
# 32 passed, 8 skipped in 26.22s

..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_worker_auth_refresh_live.py tests/test_api_report_credentials_http.py -q --tb=short -rs
# 13 passed, 3 skipped in 8.31s
```

通过项覆盖本机 HTTP 登录/401/刷新顺序、不安全或负向请求不重放、响应和依赖值提取、已完成前置输出恢复，以及报告投影中的凭据脱敏。8项跳过需要隔离 PostgreSQL/MySQL/Redis/RustFS 或真实进程终止与恢复；另3项跳过需要隔离真实存储和受控 Worker 启动器。上述通过结果不能证明默认 Step 驱动的 Agent API 流程、跳过的持久化 Worker/进程场景或完整报告页/RustFS 产品链路，这些范围仍需分别验收。

刚完成 RustFS/PostgreSQL 实际存储场景：

```powershell
# Agent_Server
$env:RUN_LIVE_UPGRADE_STORAGE_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_api_document_dependency_http.py::test_created_document_drives_real_create_then_detail_requests
# 4 passed in 4.57s
```

四个参数组合覆盖单资源与嵌套资源，各有内存存储和真实存储两种模式；其中两项使用真实 RustFS 与 UUID 命名的隔离 PostgreSQL 目录表。测试创建临时上传文档、解析接口、运行本机受控 HTTP 创建/详情请求，复读原件并清理文档和隔离表。未调用 AI、未创建 Docker 容器；这只证明 API 文档依赖执行器的存储/传值链路，不证明完整 Agent API 页面流程。

### 写请求未知结果与真实进程恢复复验

```powershell
# Agent_Server；无需外部 AI 或 Docker
$env:RUN_LIVE_API_UNKNOWN_WRITE_RECOVERY='1'
$env:QA_API_UNKNOWN_WRITE_RECOVERY_DIR='docs/upgrade_2026-10-03/p3-unknown-write-current-audit-20261022-215854'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_unknown_write_recovery_live.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p3-unknown-write-current-audit.xml
# 2 passed in 42.92s
```

`before_merge` 与 `after_write` 两个场景均在目标端提交实际 PostgreSQL 写入、客户端超时后强制终止测试协调子进程，再启动新进程从 PostgreSQL 恢复。Worker 启动器受控；HTTP、ToolRuntime、持久化会话和新旧进程真实运行。每个场景实际只有一次 POST 和一次 GET，GET 查证得到同一实际资源 ID；目标表没有业务键唯一约束，仍然只存在一条记录。原写请求结果保留 `failed/unknown`，附加 `effect_present` 查证状态，不抹平失败或自动续跑后续写入；原 ToolJob 未改写。再次启动终态任务没有新增 HTTP 或 ToolJob。每场景12张隔离表均由测试清理后复查不存在。

证据目录为 `p3-unknown-write-current-audit-20261022-215854`。这两项证明受控 Worker 启动路径中的未知写入结果恢复与只读查证，不证明默认 AI 能自主识别缺口、选择查证方式或完成整个 Agent API 产品流程。

## 当前状态

P2 默认 Step 多用例流程仍停在首次外部规划请求；后续最小请求诊断报告当前 Step Plan 密钥没有活动订阅。用户确认的账号认证状态仍按原话记录，不改写为认证失败。P3 的 HTTP 回归、真实 RustFS/PostgreSQL 文档依赖场景以及两个未知写入结果的真实子进程恢复场景已通过；独立 Agent Worker 进程和默认 Agent API 流程尚未验收。本次只补充验收记录与结果，未修改产品代码、schema、全局模型配置或依赖。
