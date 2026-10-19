# P2 `produced` 响应保存与截断分类查证

日期：2026-10-19

## 问题

最新真实 Step 规划在 `produced` 调用中以 `finish_reason=length`、8192 completion tokens、正文 0 字符结束。需要确认空正文是否由本地流解析/阶段处理丢失，还是 Provider 实际没有发出可用于结构化计划的正文。

## 当前代码链

1. `OpenAIChatCompletionsClient.stream` 只收集 `choices[0].delta.content` 作为 `text`，并独立保存 `finish_reason`、`usage` 和 `stream_terminal_received`。`reasoning` / `reasoning_content` 不会被当作用户可见正文。
2. `planning_workflow` 在 SDK 调用返回后，将 `result.text` 和响应元数据写入计划 checkpoint，再进入 `response` 阶段。重启时可从已保存响应继续，不需要再次调用模型。
3. `response` 阶段先依据 `finish_reason` 分类完整性。`length` 被分类为 `truncated`；规划响应协议在调用 JSON Schema 解析器之前拒绝此响应，保存 `response_rejected`、`response_disposition=truncated` 及原响应文本，并将任务失败结束，不自动重发。
4. 真实 `live7` 工件与该路径相符：第三个 SDK 调用有 8192 completion tokens、`finish_reason=length`、流读取正常结束，采集到 reasoning 字段但 content 正文为空；PG 计划终态没有 Run，实际业务 HTTP 为 0。

## 结论与边界

当前证据支持：这次 `produced` 空正文来自模型响应在输出上限结束，不是本地先解析失败、错误过滤正文或把 reasoning 隐去后丢掉了已有 content。截断响应被保存和拒绝是现有完整性保护；放宽它会让不完整结构进入计划校验，不能作为修复。

这只排除了本地正文拼接/解析器为本次失败根因的假设，未证明完整输入组织、分阶段任务拆分和模型输出预算的架构已经合理，也未证明 P2 默认模型闭环。此前 16K 隔离对照同样未产出有效计划；不重复单独增大上限、切换协议或把推理字段转作计划正文。下一步须针对阶段职责与真实输入投影做设计验证，保持正式用例、证据引用、清理关系和既有全局预算/协议约束。

## 验证

源代码依据：

- `Agent_Server/src/application/model_clients/openai_client.py`
- `Agent_Server/src/application/model_clients/completion.py`
- `Agent_Server/src/application/test_cases/planning_workflow.py`
- `Agent_Server/src/application/test_cases/planning_stages.py`

真实响应证据：

- `docs/upgrade_2026-10-03/p2-default-full-reference-feedback-8192-live7-20261019/verification.json`
- 同目录 `physical/*.sdk-stream.json`、`physical/*.response.json`、`PG-planning.json`

实际运行 `cd Agent_Server; ..\\.venv\\Scripts\\python.exe -Xutf8 -m pytest tests/test_planning_response_completion.py tests/test_model_completion_contract.py tests/test_model_completion_http.py -q --tb=short`，结果 **58 passed、3 skipped，16.03 秒**。skip 为测试自身环境门控，不计入通过。`git diff --check` 通过。该查证没有修改生产代码、模型配置、MySQL 配置或 PostgreSQL 业务数据。
