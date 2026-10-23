# P3 Agent API 测试当前验收

日期：2026-10-23

本轮只记录真实存储、真实 API/Worker 进程和本机受控 HTTP 目标的验收结果。受控模型只用于稳定地产生工具调用，不能替代默认 Step 模型质量验收。

## 已确认实现

- API Agent 使用现有会话、任务、ToolJob、Worker、权限和报告链路。
- 登录、刷新后的凭据通过受控引用保存，不把 token 写入公开工具输出、普通会话消息或报告。
- Worker 结果以 PostgreSQL ToolJob 为权威来源；子进程或 transcript 缺失时可以读回已完成结果。
- 重放已完成任务不会再次发送业务 HTTP；跨项目、跨父会话、错误 dispatch 身份和冻结输入覆盖会在发送前拒绝。
- 结果报告会保存 JSON/Markdown 工件到 RustFS，并返回真实状态、检查点和 HTTP 观察证据。

## 本轮实际验收

| 场景 | 命令 | 结果 |
|---|---|---|
| 鉴权续期、ToolJob、同进程/持久子进程、私有状态恢复 | `tests/test_api_worker_auth_refresh_live.py -k "actual_tool_jobs_and_saved_worker_sessions_replay_refreshed_credentials or parent_campaign_private_state_report_and_original_job_recovery"` | 3 passed，29.01 秒 |
| 无 transcript 时重建父任务并读回已完成 ToolJob | `tests/test_api_worker_result_recovery_live.py` | 1 passed，8.26 秒；恢复新增 HTTP 为 0 |
| Agent Worker 同进程与持久子进程，受控模型，真实 API/Redis/PG/MySQL/RustFS | `tests/test_api_agent_worker_live.py -k "controlled-model"` | 2 passed，100.37 秒 |
| provider 适配、请求映射、Responses/Chat 分离、完成状态和请求配置 | `tests/test_model_clients.py tests/test_model_completion_contract.py tests/test_model_request_profile.py tests/test_model_request_profile_storage.py` | 92 passed，1 skipped，24.34 秒 |
| 报告中心、原件下载、RustFS 工件、API 重启后读回和终态重投 | `tests/test_api_report_credentials_product.py::test_cookie_report_download_restart_and_original_job_recovery` | 1 passed，56.69 秒；浏览器、生产 API、隔离 PG/MySQL/Redis/RustFS 均实际运行 |

证据目录：

- `p3-api-worker-auth-storage-current-20261023-01/`
- `p3-api-worker-result-recovery-current-20261023-02/`
- `p3-api-agent-worker-controlled-current-20261023-01/`
- `p3-api-cookie-report-product-current-20261023-rerun/`

测试夹具创建的临时 Redis、隔离 PostgreSQL 数据库、MySQL 配置表和 RustFS 对象均在 teardown 清理；本轮结束时没有 `qa-execution-cli-*` 容器残留。

报告页验收还核对了下载文件 SHA-256 与 RustFS 元数据一致、PostgreSQL 只保存工件路径元数据、公开任务与页面均未出现凭据。API 重启窗口内页面先收到一次 `GET /sessions/{id}` 的 500，随后同一请求返回 200；网络记录将它定位在受控重启范围 `[28,29)`，没有页面错误或非计划失败，因此不把重启竞态误判为产品请求成功，也不新增无依据的重试逻辑。

## 默认 Step 边界

Step 账号按用户确认记录为“已认证”。现有默认文档生成证据中的真实请求已经到达配置的 OpenAI-compatible endpoint，但服务返回 HTTP 400 `request_params_invalid`，SDK create 没有返回模型结果，因而没有生成草稿。此前响应还指向无有效 Step Plan 订阅；这属于供应商计划/可用性未通过，不应改写为认证失败，也不能把受控模型结果写成默认 Step 通过。

因此当前 P3 已完成受控 Agent API 链路和 provider 适配契约验收，仍未完成默认 Step 驱动的 Agent API 全流程质量验收。

## 尚未完成

1. 默认 Step 能返回可用结果后，重跑项目隔离的 Agent API 完整流程，覆盖真实模型选取、工具调用轨迹、响应提取、依赖传值、鉴权续期、超时/幂等、报告呈现和恢复。
2. 补充断网、进程中断和多 Worker 竞争的默认配置场景；现有受控场景已覆盖部分恢复边界。
3. 完成产品页面的真实浏览器回归，并将 API 报告、证据下载和失败定位与前端状态逐项对账。

## 2026-10-23 自动续期与 Cookie 补验

在不调用外部模型的前提下，使用本机受控 HTTP 目标和独立证据目录运行：

- `tests/test_api_401_refresh_automatic_live.py tests/test_api_cookie_session_http.py`：**21 passed、1 skipped，120.40 秒**。
- `test_cookie_attributes_private_storage_worker_read_and_replay`：真实 PostgreSQL ToolJob、加密凭据和受控 Worker **1 passed，11.07 秒**。

证据证明自动 401 的登录、刷新、目标重试、刷新失败不重放、超时新 attempt，以及 API 进程在三个暂停点中断后的恢复均符合当前契约；Cookie 属性轮换、旧 Cookie 清理、Worker 读取、服务重启读回和终态重投也通过。三次进程恢复均无额外 HTTP，原 Job 不变；明文凭据不进入 prompt、公开结果或持久化公开字段。证据目录为 `p3-api-401-auto-current-20261023-run2/` 和 `p3-api-cookie-storage-current-20261023-run2/`。

这关闭了受控 Agent API 的自动 401/Cookie 持久化验收缺口，但不扩展为默认 Step 质量通过。默认 Step 的完整 Agent API、断网和多 Worker 竞争仍需在供应商推理可用后复验；产品报告页面已有独立通过证据，仍需把默认模型路径与页面状态逐项对账。

### 2026-10-23 持久 Worker 请求超时重试补验

在真实隔离 PostgreSQL/MySQL/RustFS 与持久 Worker 场景中，使用本机 `redis://127.0.0.1:6379/15`，不启动临时 Docker 容器，实际请求本机受控 HTTP 目标：

```powershell
$env:RUN_LIVE_API_401_AUTO_WORKER='1'
$env:QA_ISOLATED_RUNTIME_REDIS_URL='redis://127.0.0.1:6379/15'
$env:QA_API_401_AUTO_WORKER_DIR='docs/upgrade_2026-10-03/p3-api-timeout-worker-current-20261023-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_401_refresh_automatic_live.py::test_worker_campaign_timeout_retries_read_and_reports_final_result -q --tb=short --junitxml=docs/upgrade_2026-10-03/p3-api-timeout-worker-current-20261023-01.xml
# 1 passed in 18.26s
```

逐项证据 `p3-api-timeout-worker-current-20261023-01/request-timeout-retry/verification.json` 记录同一路径两次真实 HTTP 请求：第一次在 0.5 秒超时、标记 `known_failed` 且没有响应；第二次 attempt 返回 HTTP 200。Worker 最终任务为 `completed`，attempt 数为 2，报告统计为 completed=1、failed=0、skipped=0；没有调用外部 AI。测试结束时未发现 `qa-execution-cli-*` 容器。该结果只补强受控 API Worker 的安全读请求超时重试和报告，不证明默认 Step 完整流程、断网语义、多 Worker 竞争或默认模型质量。

