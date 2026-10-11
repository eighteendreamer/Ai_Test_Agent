# 默认 Step 最小用例真实验收

## 依据

- `Agent_Server/tests/test_default_minimal_case_live.py`
- `Agent_Server/src/application/test_runs/agent_case_context.py`
- `Agent_Server/src/application/test_runs/agent_case_execution.py`
- `Agent_Server/src/application/test_runs/agent_case_receipt.py`
- `Agent_Server/src/modes/api_testing_mode/campaign_state.py`
- `Agent_Server/src/modes/api_testing_mode/runtime.py`
- `docs/upgrade_2026-10-03/p2-default-minimal-case-live5/`
- `docs/upgrade_2026-10-03/p2-default-minimal-case-live6/`
- `docs/upgrade_2026-10-03/p2-default-minimal-case-live7/`
- `Agent_Server/tests/test_default_receipt_process_recovery_live.py`
- `Agent_Server/tests/default_model_probe.py`

## 真实失败样本

第一份最小 JSON 用例使用了项目自定义的 `operations`/`expect` 结构。默认 Step 能读出目标并调用 `api-test-runner`，但连续生成了 `type`/`field`/`value` 以及不受 `AssertionSpec` 支持的 `kind`，运行时在 `ApiTestTask.model_validate` 阶段返回 `invalid_task_payload`，真实目标 HTTP 请求数为 0。该样本证明执行提示要求 AI 理解任意 JSON 仍存在格式歧义，不能把工具 schema 已注册等同于模型一定按 schema 组装参数。

第二份用例改为现有 `ApiTestTask`/`AssertionSpec` 结构后，默认 Step 在真实本地 HTTP 目标上完成一次 GET，两个断言均通过；但第一次正式回执的第二个检查引用了两个实际值，严格回执校验拒绝并把结果记为 `error`。业务请求本身已经成功，结果错误来自回执结构，不得改写为通过。

## 可复现通过边界

在隔离项目策略启用既有 `case-receipt-correction-v1` 后，真实执行完成：规划任务 `completed`，运行 `completed`，用例结果 `passed`，目标只收到一次 GET，终态调度重新投递后目标请求数仍为 1。该次默认 Step 直接返回合法回执，最终结果没有 `receipt_correction` 字段，因此这里只证明“策略开启时正常回执可通过”，不证明真实默认模型已经进入纠正调用。MySQL 保存模型配置和项目策略，PostgreSQL 保存冻结计划、审批、ToolJob、快照、运行和结果，RustFS 保存本轮上传原件；未调用真实外部副作用服务。

## 结论

当前已证明：认证后的默认 Step 可以完成“项目内选用例→冻结计划→审批→执行工具→回执纠正→保存结果→终态幂等重投”的最小只读链路。当前未证明：多候选选取、排序与补全、准备/清理、未知副作用、运行中修订、复杂 API 依赖、其他模式和完整 App。

项目策略的回执纠正目前默认关闭。关闭时，已成功的业务工具只要模型回执不符合严格结构就会落成 `error`。是否把纠正协议纳入新项目默认值，仍需评估额外只读模型调用预算、供应商能力和失败策略，不能由最小验收自动改成全局默认。

## 2026-10-11 新证据：实际默认模型纠正

`p2-default-minimal-case-live7` **1通过103.97秒**。与 live6 不同，这次结果确实包含 `receipt_correction`，阶段为 `response`，原执行2次模型调用加纠正1次，未超过原8轮预算；两份 API 进程 SDK 请求分别为工具执行后的推理和无工具的只读纠正，均实际返回HTTP200。目标仅执行一次 GET，终态重投后没有新增业务请求。

通过 `p2-default-receipt-evidence.py` 从 RustFS 重读完整请求与响应，核对大小和SHA256。公开响应保留 `text`、`tool_calls` 和 `response_summary`；不导出供应商 `raw_response` 私有字段。MySQL全局默认模型、传输参数、项目默认策略没有改动，普通工具循环也未改为强制结构化输出。

## 进程恢复的实际边界

依据 `AgentCaseReceiptService.correct` 的持久检查点及既有 `test_receipt_process_recovery_live.py`：`invoking` 无可靠响应时保留未知，不允许重新调用模型或重执行业务；`response` 已落库时重读保存的响应。前者正确终态是 `error` 且带 `execution_recovery_required=true`，不能要求模型未返回时仍判定通过。

默认模型测试只在独立测试进程的 HTTPX 请求钩子观察纠正 system prompt，不修改请求/模型回复；120秒未释放即退出。`p2-default-receipt-process-live5` **1通过385.93秒**，杀掉 Windows venv launcher 及实际 API 子进程，重启后等待原RunItem和资源租约自然到期，使用现有清理及恢复Worker。目标仍只有一次GET，结果保留原纠正 `invoking` 身份并要求恢复，终态重投没有重复业务。RustFS请求原件已下载并核验；PG公共投影、CLI输出、容器删除证据保留。

这个边界是**持久意图已保存、HTTP发送前**，没有证明纠正请求被供应商接收后的断网、响应保存后中断，或默认模型UI正式回执。受控测试的两种边界不能替代上述默认模型验收。此前 live1—live4 失败分别涉及同步等待审批屏障、审批返回结构误用、未进入待审批分支和辅助函数缺省参数；保留原JUnit，未改生产恢复逻辑以让夹具通过。

最终加强原件、清理和模型请求计数后的 `p2-default-receipt-process-live6` **1通过372.83秒**：请求原件在对象删除前核对28,232字节及SHA；清理收据为本项的agent资源 `skipped`，活跃/待清理资源为空、配额归零。恢复及终态前后API模型请求钩子的记录集合相同，目标保持一次GET。相关SDK、回执、评估、会话和模型profile回归 **110通过、18门控跳过59.67秒**；未改前端、数据库schema或生产代码，不运行前端构建、不声称全App或其他供应商外网质量通过。
