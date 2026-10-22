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

证据目录：

- `p3-api-worker-auth-storage-current-20261023-01/`
- `p3-api-worker-result-recovery-current-20261023-02/`
- `p3-api-agent-worker-controlled-current-20261023-01/`

测试夹具创建的临时 Redis、隔离 PostgreSQL 数据库、MySQL 配置表和 RustFS 对象均在 teardown 清理；本轮结束时没有 `qa-execution-cli-*` 容器残留。

## 默认 Step 边界

Step 账号按用户确认记录为“已认证”。现有默认文档生成证据中的真实请求已经到达配置的 OpenAI-compatible endpoint，但服务返回 HTTP 400 `request_params_invalid`，SDK create 没有返回模型结果，因而没有生成草稿。此前响应还指向无有效 Step Plan 订阅；这属于供应商计划/可用性未通过，不应改写为认证失败，也不能把受控模型结果写成默认 Step 通过。

因此当前 P3 已完成受控 Agent API 链路和 provider 适配契约验收，仍未完成默认 Step 驱动的 Agent API 全流程质量验收。

## 尚未完成

1. 默认 Step 能返回可用结果后，重跑项目隔离的 Agent API 完整流程，覆盖真实模型选取、工具调用轨迹、响应提取、依赖传值、鉴权续期、超时/幂等、报告呈现和恢复。
2. 补充断网、进程中断和多 Worker 竞争的默认配置场景；现有受控场景已覆盖部分恢复边界。
3. 完成产品页面的真实浏览器回归，并将 API 报告、证据下载和失败定位与前端状态逐项对账。

