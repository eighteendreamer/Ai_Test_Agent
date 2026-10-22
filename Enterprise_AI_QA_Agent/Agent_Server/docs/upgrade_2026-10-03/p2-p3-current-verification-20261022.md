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

真实规划任务运行一轮后进入终态 `failed`。模型 SDK 收到 HTTP 400 并抛出 `BadRequestError`，记录的错误类型为 `request_params_invalid`。物理请求观察器只记录状态码，没有读取响应正文。规划任务提示为 `模型调用失败（http_error）；未创建测试运行`。没有持久化 CasePlan、没有创建 Run、业务 HTTP 请求数为0。现有证据不能确定供应商返回的具体原因，也不推翻用户确认的账号认证状态；不能把本次运行描述为认证失败，也不能把历史订阅错误当成本次响应。

JUnit 为 `p2-default-multi-case-step-user-auth-confirmed-current-20261022-01.xml`；脱敏 SDK/HTTP 与任务状态证据位于同名目录。默认 Step 链路目前只验证到首次规划请求；项目内用例选择、依赖排序、补全、Run 执行和结果证据均未到达。

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

## 当前状态

P2 默认 Step 多用例流程仍停在外部规划请求，账号认证按用户确认视为完成。P3 本次覆盖的 HTTP 与恢复逻辑在当前工作树通过；受门控的持久化 Worker 和进程重启场景尚未验证。本次未修改产品代码、schema、全局模型配置或依赖。
