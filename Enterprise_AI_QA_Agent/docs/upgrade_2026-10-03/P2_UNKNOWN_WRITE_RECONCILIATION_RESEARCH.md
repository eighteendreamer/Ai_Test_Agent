# P2 未知写入结果与只读查证边界

## 依据

- `Agent_Server/src/modes/api_testing_mode/executor.py`：API 任务在 HTTP 超时、连接错误和响应后才写入任务结果。
- `Agent_Server/src/modes/api_testing_mode/coordinator.py`、`subagent_coordinator.py`：失败任务按超时、连接错误和 5xx 判断是否自动重试。
- `Agent_Server/src/application/runtime/case_operation.py`、`tool_job_service.py`：正式用例工具调用已经有逻辑操作身份和未知结果不重发约束，但这套协议不替 API 文档测试任务凭空生成查询接口。
- `Agent_Server/src/application/test_runs/case_effect_evidence.py`、`case_effect_relations.py`：效果证据必须来自固定用例声明和可靠返回，缺少选择或输出时保持 unknown。
- `项目借鉴/llm-testing-course/接口测试-通用篇.html`：超时后的写请求不能只靠客户端重试判断结果，应核对业务侧状态；幂等键必须由实际接口契约提供。

## 复现事实

受控 `ThreadingHTTPServer` 的 POST 先记录请求，再延迟响应超过客户端超时。旧实现以普通 `failed` 返回，`ExecutionPolicy(max_retries=1)` 随后发送第二次 POST；服务端请求计数为 2，两个请求都进入服务端处理。另一个场景返回 HTTP 500，旧重试规则同样可能继续重发。

原始复现命令和失败输出应与本批证据目录一同保存；生产源码未通过测试夹具伪造服务端返回。

## 实施边界

`ApiTestTask.request_outcome` 记录 `not_started`、`completed`、`known_failed` 或 `unknown`。写方法（POST、PUT、PATCH、DELETE）的传输异常，以及收到 5xx 响应但无法证明事务回滚的情况，标记为 `unknown`；响应原件仍保留。两种协调器遇到该状态均停止自动重试，报告和评估输出 `reconciliation_required=true`、`unknown_side_effect`，并保留原请求、响应和错误证据。

只读 GET 等请求的普通超时仍可按原策略重试。系统没有通用的业务查询 URL、主键或幂等语义，因此不自动编造“查询副作用”请求；后续只读查证必须来自当前项目固定用例/接口契约中明确声明的读操作，或由用户/评审补充可靠依据。未知结果不能回写成成功，也不能以清理意图代替业务事实。

## 本批验证

`tests/test_api_unknown_write_recovery.py` 使用真实本地 HTTP 服务验证：写入超时实际只发送一次、服务端已收到请求、任务为 `unknown`；写入 500 也只发送一次并要求查证。受影响回归同时覆盖 API 执行器、直接协调器、子 Agent 协调器、报告和评估。

这项修复解决了自动重复发送的根因和可见性；通用只读查证任务的业务契约、默认 Agent 自主选择、完整 App 和真实默认模型仍未完成。
