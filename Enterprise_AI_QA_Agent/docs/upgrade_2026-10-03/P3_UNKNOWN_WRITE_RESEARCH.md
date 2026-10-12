# API 未知写入结果与依赖阻断〔2026-10-12〕

基线a0097a5a。依据现有 `ApiTaskExecutor`、`ApiTestCoordinator`、`ApiTaskPool`、`ReportBuilder` 及 `test_api_unknown_write_recovery.py` 调研。

现有 Executor 会把写请求超时、连接异常以及 5xx 响应标记为 `request_outcome=unknown`；超时不自动重试，任务报告带 `reconciliation_required`。Coordinator 只在任务状态为 `completed` 后保存输出并解除依赖，TaskPool 会把 `failed` 依赖传播为后续 `skipped`。未知写入必须在 Executor 内保持 `failed`。

根因是 Executor 先设置 `request_outcome=unknown`，随后用“所有断言通过”直接设置 `TASK_COMPLETED`。真实返回 500、用例又断言 500 时，原实现会完成任务并放行后续步骤，违反先只读查证、禁止重试或继续依赖的现有约束。

修复后未知写入保留响应、HTTP 观察和断言证据，但强制 `failed`，写入结构化日志 `api_task_unknown_write_blocks_dependents`，错误说明只读查证要求；依赖任务由现有 TaskPool 标记 `skipped`，评估增加未知副作用建议。没有新增状态、表、配置、依赖、模型协议或权限变化。

实际证据：未知超时、真实 500、匹配 500 断言仍不放行依赖、评估建议4项通过2.00秒；API/回放/操作证据/效果关系/鉴权刷新回归61项通过、50项门控跳过30.04秒；compileall退出0。外部AI0，未用默认模型，未扩大为正式Run全流程验收。

未覆盖：只读查证工具如何由Agent依据项目资料选择、查证后的人工/模型确认，以及正式用例Run跨进程恢复；这些仍属于完整P0—P5后续范围。
