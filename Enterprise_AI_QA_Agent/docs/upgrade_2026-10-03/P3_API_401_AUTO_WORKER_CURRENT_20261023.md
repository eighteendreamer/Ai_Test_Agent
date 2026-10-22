# Agent API 自动鉴权与超时恢复当前验收

日期：2026-10-23。

本轮先读取 API 401 刷新契约、Worker 子任务协调器、私有凭据存储和报告投影，再使用真实隔离 PostgreSQL、MySQL、RustFS、Redis、独立 Worker 运行。业务目标是本机受控 HTTP 服务，不调用外部模型，避免把模型质量混入 API 行为验收。

## 实际命令与结果

```powershell
$env:RUN_LIVE_API_401_AUTO_WORKER='1'
$env:QA_API_401_AUTO_WORKER_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p3-api-401-auto-worker-current-20261023'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_401_refresh_automatic_live.py -q --tb=short --maxfail=1 -k 'worker_automatic_refresh_uses_same_contract_and_private_results or worker_refresh_failure_is_reported_without_replaying_target or worker_campaign_timeout_retries_read_and_reports_final_result' --junitxml=docs/upgrade_2026-10-03/p3-api-401-auto-worker-current-20261023.xml
# 3 passed, 10 deselected in 31.41s
```

三条场景的真实结果：

- 自动刷新成功：实际顺序为 `POST /login`、`GET /profile` 返回 401、`POST /auth/refresh` 返回 200、再次 `GET /profile` 返回 200；目标只读请求重放一次，三个任务均完成。
- 刷新失败：实际顺序为登录 200、目标 401、刷新 401；目标没有第二次发送，报告状态为 `1 completed / 2 failed / 0 skipped`，公开报告没有凭据。
- 只读超时：第一次 `/slow` 在 0.5 秒超时并标记 `known_failed`，第二次有限重试返回 200；两个 Attempt 使用不同的私有结果引用，最终任务为 `completed` 且尝试次数为 2。

证据目录：`Agent_Server/docs/upgrade_2026-10-03/p3-api-401-auto-worker-current-20261023/`。其中凭据字段已替换为 `[REDACTED]`，没有保存 API token 或 Authorization 值。测试结束后 `qa-execution-cli-*` 容器无残留。

## 分类

本轮将“Agent API 自动 401 刷新、刷新失败报告、只读超时有限重试及结果隔离”列为**已有实现且真实 Worker 验收通过**。

仍未由本轮覆盖：错误凭据组合、断网/进程中断后的跨进程恢复、更多 endpoint 依赖结构、正式默认 Agent 的完整 API 页面流程，以及 P2 用例选取→排序→补全→正式 Run 的闭环。Step 已按用户确认视为已认证；本轮没有更换模型协议或改变 MySQL/ PostgreSQL/RustFS 的存储职责。
