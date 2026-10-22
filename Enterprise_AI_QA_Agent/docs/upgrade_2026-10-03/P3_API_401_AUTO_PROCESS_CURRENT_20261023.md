# Agent API 进程中断与 401 恢复当前验收

日期：2026-10-23。

本轮在上一条 Worker 级自动刷新、刷新失败和超时验收之后，继续执行真实进程级中断。测试使用隔离 PostgreSQL、MySQL、RustFS、Redis 和本机受控 HTTP 目标，分别在自动流程的三个阶段强制结束 Worker，再启动新进程恢复；不调用外部模型。

## 实际命令与结果

```powershell
$env:RUN_LIVE_API_401_AUTO_PROCESS='1'
$env:QA_API_401_AUTO_PROCESS_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p3-api-401-auto-process-current-20261023'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_401_refresh_automatic_live.py -q --tb=short --maxfail=1 -k 'real_process_kill_restores_automatic_401_without_duplicate_requests' --junitxml=docs/upgrade_2026-10-03/p3-api-401-auto-process-current-20261023.xml
# 3 passed, 10 deselected in 68.35s
```

`pause_after=1/2/3` 三个中断点均强制退出码为 1，随后新进程完成相同业务序列：`POST /login`、`GET /profile`（401）、`POST /auth/refresh`（200）、`GET /profile`（200）。恢复没有重复已提交的前置请求；终态重投的新增 HTTP 请求数为 0，原 ToolJob 快照保持不变。三份 `facts.json` 中的 Authorization 和 refresh token 均为 `[REDACTED]`，公开会话元数据不含凭据。

证据目录：`Agent_Server/docs/upgrade_2026-10-03/p3-api-401-auto-process-current-20261023/`。测试结束后没有残留 `qa-execution-cli-*` 容器。

## 分类

本轮将“自动 401 刷新流程的真实进程中断、重启恢复和终态不重放”列为**已有实现且真实验收通过**。

仍未覆盖错误凭据、刷新进程本身中断后的更细分组合、断网恢复、更多 endpoint 依赖和完整默认 Agent API 产品页面；P2 用例选取→排序→补全→正式 Run 仍未闭环。Step 按用户确认视为已认证，本轮没有修改模型协议或数据库分工。
