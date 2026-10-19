# Agent API 默认模型 Worker 实测记录

日期：2026-10-19

## 验收目标与边界

使用当前 MySQL 默认已认证 Step 配置，通过真实 API、PostgreSQL/Redis/RustFS 隔离运行时和 Agent Worker，执行本地受控认证 campaign：`POST /login` → `POST /auth/refresh` → `GET /profile`。只对本地目标发送业务请求；凭据由现有私有凭据存储处理。该测试验证默认模型驱动的 API Agent/Worker，不等同正式测试用例 Run 的 P2 闭环。

测试入口：`Agent_Server/tests/test_api_agent_worker_live.py::test_real_agent_worker_private_auth_and_terminal_redelivery` 的 `default-model` 参数化变体。当前测试使用 `default-model` pytest id；本次初次以源码变量名筛选时 6 个场景全部 deselected，之后使用正确 id 执行。原始证据目录：`docs/upgrade_2026-10-03/p3-api-agent-worker-default-current-20261019/`。

## 实测事实

命令实际结果：**2 failed、4 deselected，391.59 秒**。两个默认模型变体都未通过测试夹具最终断言：

- 同进程子 Worker：target 收到 `/login`、`/auth/refresh` 和两次 `/profile`，四个均 HTTP 200；因此与固定 campaign 的三次唯一任务期望不符。session/child transcript 中 task `task_002_profile` 被重复观察到，parent 经多次 resume 后最终 `completed`。凭据公开输出扫描通过。
- 持久子 Worker：target 只收到 `/login` HTTP 200；child session 已 `completed`，parent 仍 `running`，且存在已中断的 approval-resume/message continuation。该变体没有完成 refresh/profile campaign。

两种变体均确认读取的 MySQL 默认 transport/model 为 `openai_chat_completions` / `step-5-preview`，凭据刷新和 API 都返回成功，没有 target fixture error 或 HTTP 鉴权失败。默认 provider HTTP 尝试可从 Worker 日志计数；因测试在最终成功断言前抛错，`verification.json` 与终态重投断言没有生成，故不能宣称真实 HTTP attempt 总数或终态重投不重复已通过。

## 当前判断

这是**默认 Agent API 产品链尚未验收**的真实反例，而非成功记录。重复 GET 说明 transcript/dispatch/续接链路需要进一步区分：模型在工具结果后的合法再次决策、同一 tool call 的检查点重放、重复审批恢复，或完成事件的投影/消费重复。持久模式则需解释为何 child 已终态而 parent turn 与恢复任务未收敛。

目前收集的公开 `observed.json`、target events、Worker stdout/stderr 能证明最终副作用计数及 session/dispatch 摘要，但在测试最终断言前不会保存完整 PG ToolJob、turn snapshot、每个 approval ID 的时序和 HTTP attempt/SDK 调用关联。当前证据不足以安全地归因于生产逻辑或模型行为；先补足诊断读取并复现，再决定是否需要生产改动。不能把 GET 的安全幂等误当作“不得重复请求”，因为多余调用会污染计数、成本和报告；对非幂等操作更不能依赖模型自行避免重复。

## 下一步查证

1. 调整测试证据收集顺序，使断言前先保存完整公开 facts、PG session/task/tool-job 投影和 parent/child snapshots；脱敏凭据及原有安全扫描保持。
2. 以 parent turn、child turn、approval ID、tool call ID、ToolJob ID、API task ID 连接模型 HTTP 与 target HTTP 的因果链，确认 profile 第二次 POST 前是否出现第二个模型 tool call，或同一调用被重新 dispatch。
3. 对 durable 模式收集 parent wait checkpoint、child completion notification/outbox 和恢复任务状态，确定卡在通知、投影、消费或审批等待中的具体状态边界。
4. 在归因后先运行针对同一真实失败现场的确定性回归，再重复默认模型两变体；只有两个模式都完成 campaign、准确报告三项任务、秘密不泄漏、终态重投零新增请求，才把默认 API Worker 流程记为通过。

本批没有修改生产代码、MySQL 默认配置或业务规则。未创建新的业务数据表；实际 target 是本地受控服务。

## 补充复核：重复调用与持久等待的边界

2026-10-19 对原始 `postgres-finally.json` 中的 ToolJob、approval 与 session snapshot 做了只读结构复核；仅输出身份、状态、工具名、参数长度及 SHA-256，不输出工具参数正文、模型正文或凭据。

- 同进程模式的两次 `/profile` 分别对应两个不同的 `call_id`、ToolJob ID 和 approval ID，且都在同一 child session/turn 中完成。该 child 的最终 snapshot 顺序为：assistant 调用 → tool 结果 → assistant 再次调用 → tool 结果；两次 `api-test-runner` 参数长度均为 80，SHA-256 相同。因此已排除“同一 ToolJob 被原样重投”，证据更符合模型收到第一次结果后生成了新的、参数完全相同的调用。尚未证明模型为何选择重复调用，也未证明所有重复都属于此模式。
- 持久模式的 parent ToolJob 最终仍是 `waiting_children`；可见的 login child ToolJob 已 `completed`，parent 和 child 各只有初始等待及终态 snapshot。目标仅收到 login，refresh/profile child 尚未出现。该快照可以确认停留边界，但原产物没有保存相应 child-completion notification/outbox 消费前后的时序，因此不能进一步归因于通知丢失、投影未更新或 wait 协调器逻辑。

这项证据把原来的两类故障分开：同进程多余调用首先应调查 Agent 在工具结果后的下一轮决策及防止同轮无进展重复的确定性边界；持久模式首先应在断言前保存父 ToolJob、child completion、outbox/消费状态的关联快照，再判断是否需要生产修复。当前仍不改生产行为，也不将一次 live 观察泛化为所有重复调用的根因。
