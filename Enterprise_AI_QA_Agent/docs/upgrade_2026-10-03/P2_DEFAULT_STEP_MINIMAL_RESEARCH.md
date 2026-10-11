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

## 真实失败样本

第一份最小 JSON 用例使用了项目自定义的 `operations`/`expect` 结构。默认 Step 能读出目标并调用 `api-test-runner`，但连续生成了 `type`/`field`/`value` 以及不受 `AssertionSpec` 支持的 `kind`，运行时在 `ApiTestTask.model_validate` 阶段返回 `invalid_task_payload`，真实目标 HTTP 请求数为 0。该样本证明执行提示要求 AI 理解任意 JSON 仍存在格式歧义，不能把工具 schema 已注册等同于模型一定按 schema 组装参数。

第二份用例改为现有 `ApiTestTask`/`AssertionSpec` 结构后，默认 Step 在真实本地 HTTP 目标上完成一次 GET，两个断言均通过；但第一次正式回执的第二个检查引用了两个实际值，严格回执校验拒绝并把结果记为 `error`。业务请求本身已经成功，结果错误来自回执结构，不得改写为通过。

## 可复现通过边界

在隔离项目策略启用既有 `case-receipt-correction-v1` 后，真实执行完成：规划任务 `completed`，运行 `completed`，用例结果 `passed`，目标只收到一次 GET，终态调度重新投递后目标请求数仍为 1。MySQL 保存模型配置和项目策略，PostgreSQL 保存冻结计划、审批、ToolJob、快照、运行和结果，RustFS 保存本轮上传原件；未调用真实外部副作用服务。

## 结论

当前已证明：认证后的默认 Step 可以完成“项目内选用例→冻结计划→审批→执行工具→回执纠正→保存结果→终态幂等重投”的最小只读链路。当前未证明：多候选选取、排序与补全、准备/清理、未知副作用、运行中修订、复杂 API 依赖、其他模式和完整 App。

项目策略的回执纠正目前默认关闭。关闭时，已成功的业务工具只要模型回执不符合严格结构就会落成 `error`；开启既有纠正协议后可恢复。是否把纠正协议纳入新项目默认值，需要单独评估额外只读模型调用预算、供应商能力和失败策略，不能由本次最小验收自动改成全局默认。
