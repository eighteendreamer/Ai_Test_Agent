# 正式用例恢复与效果生命周期复核〔2026-10-12〕

基线b2f234a5。本步依据 `AgentCaseExecutionAdapter`、`TestRunExecutionService`、`case_operation`、`case_effect_evidence`、`preparation_scopes` 及本地 Agent 轨迹课程，复核正式 Run 与 API 未知结果之间的边界。

现有正式用例执行先保存固定项目/RunItem/Attempt/turn 的执行意图，再进入 Session 图；缺少快照时抛出 `AgentCaseRecoveryRequired`，不重新发送。原 ToolJob 的项目、Run、RunItem、Attempt、call/input 身份必须匹配；已保存可靠响应可回读，未知副作用不允许通过更换 provider call id 重新创建。准备项、正式目标和清理项通过固定依赖及对象效果声明关联，准备成功不等于目标通过，清理只消费已确认对象证据。

本地回归命令：

```text
..\.venv\Scripts\python.exe -m pytest tests/test_case_stateful_recovery_research.py tests/test_case_effect_lifecycle_research.py tests/test_case_shared_effect_research.py tests/test_agent_case_execution.py tests/test_preparation_scopes.py -q --tb=short
```

实际结果 **22 passed, 34 skipped, 60.17s**。通过部分证明租约身份、恢复链、逻辑操作身份、共享效果/清理依赖和回执处理的代码合同；门控项没有真实 PostgreSQL/RustFS/Redis 与完整产品 Run，因此不能宣称正式 Run 跨进程未知查证已验收。API 未知写入的真实 HTTP 验收另见 `P3_UNKNOWN_WRITE_RESEARCH.md`。

仍需真实门控：正式 Run 的未知写入只读查证工具选择与确认、进程中断后的真实原始证据投影、准备/目标/清理跨服务恢复，以及默认 AI 的用例选取、排序、补全质量。外部 AI 按用户安排继续暂缓。
