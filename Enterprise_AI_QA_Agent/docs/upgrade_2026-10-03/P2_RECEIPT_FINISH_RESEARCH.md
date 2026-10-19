# P2 正式回执的动作与消息边界

2026-10-19。延续 `a94777a2` 的工具输出根路径修复；完整 P0—P5 未完成。

## 查证结果与本次范围

真实默认模型失败现场 `p2-default-single-staged-v2-live3-20261019/` 已有一次 POST 201 和一次 DELETE 200，产生与清理证据均捕获，实际订单全部删除。失败发生在模型随后调用 `api-test-runner`，只传 `_case_action.operation_key=receipt`，没有原生操作的 `worker_action/task`，实际进入 Campaign 项目选择并留下 `selection_unknown`。

沿调用链核对：`AgentCaseExecutionAdapter` 从会话的模型回复取得结果，`AgentCaseAssessmentService` 核验回执；`api-test-runner` 是业务执行工具，其 Campaign 入口和原生任务入口都不是回执提交接口。当前模型工具选择为自动；没有证据表明 SDK 强制模型调用工具。旧提示要求返回 JSON，却没有明确 JSON 应放在最终 assistant 消息中。

本次修正 `agent_case_contract.case_result_contract` 与 API 用例执行提示，明确最终消息承担回执提交，原生业务调用仍需要完整 task 或已经分派的 dispatch_ref。没有增加拦截器、忽略 unknown 操作、猜测任务参数或放宽证据校验。其他模式沿用各自原生工具和既有消息链路。

测试夹具在真实默认模型成功后重投同一终态派发，分别检查模型物理请求数、业务端请求列表及正式运行记录均未变化；原有 executor-only 诊断仍保留。清理对象数按单对象/双对象场景核对，不再把单对象场景误要求为至少两个对象。

## 已实际运行

```powershell
uv run --project . python -m pytest -q tests/test_agent_case_assessment.py tests/test_agent_case_receipt_correction.py tests/test_prompt_assembly_service.py tests/test_scene_expectation_contract.py
```

结果：89 passed、17 skipped，32.47 秒。跳过项需其各自实时存储或模型环境，不计通过。

```powershell
uv run --project . python -m pytest -q tests/test_model_clients.py tests/test_model_sdk_fault_http.py tests/test_model_completion_http.py -k anthropic
```

结果：20 passed、80 deselected，28.06 秒。测试使用实际 Anthropic SDK 及本地 HTTP 服务，核验 `/v1/messages`、流式/非流式及故障边界；未调用线上 Anthropic。`await_provider_io` 仅等待原 SDK 操作并归一网络异常，未转换为 OpenAI Responses。

## 尚待验收及明确实现缺口

真实默认 Step 复验现场：`p2-default-single-receipt-finish-live4-20261019/`。结果1 failed，443.53秒：第5轮清理声明的T引用携带了不允许的excerpt，规划失败，Run/业务HTTP均为0，尚未验证到执行和回执收尾。已另以真实样本复现Schema/解析约束不一致，见 `P2_REFERENCE_SCHEMA_RESEARCH.md`。该场景仍只指定一个完整正式用例，使用隔离 MySQL/PG/Redis/RustFS、正式 API 和独立 Worker，16K 输出与600秒规划时限是隔离诊断参数，不是全局默认配置通过的证明。

即使单用例通过，也不能代替多候选选取、必要正式前置补入、排序及输出传递、运行内准备、负向条件、执行中计划修订或其他模式的完整验收。

后续Schema修复后的live5已结束：1 failed，626.71秒。规划完成，目标POST/DELETE各一次且实际订单已清理；最终消息承担回执提交，没有再发生receipt工具调用。一次真实只读纠正得到合法回执、四个验证点通过。正式Run仍blocked：首次原生参数验证失败却被计入unknown产生来源，且冻结声明把不同的整个工具输出作为对象身份，导致清理匹配失败。没有重写冻结计划或强制通过；因未通过，本次未执行“成功终态重投”断言。实际消息边界和只读纠正已有本次证据，默认API完整通过继续未验，详见同批Schema调研。

源码核对还确认：`CasePlanRunService.create` 和 `build_plan_run` 冻结计划、套件及 RunItem；现有回归入口创建新计划/运行，不能当作在途计划修订实现。当前尚无完整的执行中修订服务及其持久事务、Worker恢复和前端呈现，需要补实现，不能仅标记为“尚未验收”。已完成运行及历史固定依据保持不变。
