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

## 默认 Step 持久模式复跑

增加断言前的脱敏 PostgreSQL 诊断导出后，使用真实默认 `step-5-preview` / `openai_chat_completions`、隔离 MySQL/PostgreSQL/Redis/RustFS、独立 API/Worker 和本地认证目标，单独复跑 durable child 场景两次：第一次 **1 passed、5 deselected，114.37 秒**；修正 finally 汇总中把默认模型 HTTP 错报为 0 后，第二次 **1 passed、5 deselected，100.55 秒**。非 live dispatch/approval 回归另为 **1 passed、5 deselected，5.68 秒**。

两次默认模型成功样本都完成唯一三步目标请求（login、refresh、profile 均 HTTP 200）、父报告 3 completed/0 failed；9 次模型 HTTP attempt 均可从 Worker 日志对账，parent 与三个 child 最终均 completed。第二次检查 11 个 Agent task 对应 11 个 Outbox 记录均已发布且每条 `attempts=1`；终态重投新增业务 HTTP 与模型 HTTP 均为 0，凭据公开输出扫描通过。`target-events-at-cleanup.json` 现在也从实际 Worker 日志统计默认模型 attempt，不再固定写 0。

这两次成功不消除此前 durable 默认模型实测失败，当前结论应为**一次失败、两次成功，故障尚未稳定复现且根因未定**。诊断导出目前是最终状态和任务/outbox时间线；若再次失败，还需在 child 完成与 parent resume 的活动边界采集同一时点的 session metadata、snapshot 与恢复提交记录。生产逻辑未改，默认模型 API 全流程不标记为整体验收通过；同进程模式的重复 `/profile` 仍是模型收到第一次工具结果后新发的相同调用，尚未解决。

## 同进程默认模型最新复跑（2026-10-19）

在当前工作区以真实默认 Step 模型重跑同进程 child 场景，证据目录 `p3-api-agent-worker-default-current-rerun-20261019/configured-default-in-process/`。pytest **1失败、258.47秒**。SDK HTTP observer 记录 5 次请求 attempt 和 5 个响应；本地受控认证目标只收到 `POST /login` 与 `POST /auth/refresh`，均为 HTTP 200，`/profile` 未发生。PostgreSQL 最终投影包含两个已完成的 API 子任务和一个 `api-test-runner:waiting_children` 父 ToolJob；父 Agent turn 仍有 waiting-tool checkpoint。没有第三个 profile dispatch。凭据公开输出扫描通过。

该样本与先前的“同一 child 对相同 `/profile` 参数生成两个新 ToolJob”不同：本轮没有 profile 重复请求，而是协调链未推进到 profile，也未让父 ToolJob 收敛。最终 DB 状态只能确定停留位置，尚不能区分 child 完成通知、父工具恢复、API task pool checkpoint 或完成结果投影哪一环未推进；当前测试在断言前虽保存最终 PG 投影，但同进程模式尚未导出 child 完成通知/outbox 与父恢复提交的活动时序。Worker 另记录 10 次 memory embedding 调用失败，与单独 embedding provider 账户 HTTP 402 证据一致；这些非致命检索失败不能解释 Step 模型已返回响应或认证目标只收到两步。

下一步沿 `ApiSubagentCoordinator.run_all → CoordinatorRuntimeService` child 完成通知/恢复链，以及 `ToolJob waiting_children` 状态转换查证；先从本次隔离 PostgreSQL 原件确认父 ToolJob 的 child ID 列表与两个子 session 完成时间，再检查对应回调/Outbox 是否生成、领取和提交。只有确定状态转换根因后才改生产逻辑，并用该真实状态作为回归输入。不能把这次失败概括成模型输出重复，也不能宣称 Agent API 完整流程通过。

## 等待恢复合同复核（2026-10-19）

先为测试提供独立证据目录，再运行 `tests/test_tool_wait_batch.py tests/test_tool_child_wait.py`：**21 passed、4 skipped，9.99 秒**。证据位于 `p3-tool-wait-batch-current-rerun2-20261019/`，JUnit 位于 `p3-tool-wait-batch-current-rerun2-20261019.xml`。通过项覆盖原始等待批次、审批恢复、通知竞态及原子恢复约束；4 个跳过项属于显式启用的隔离 PostgreSQL 场景，不能据此声称真实持久层通知闭环通过。

源码顺序复核确认：`CoordinatorRuntimeService._maybe_resume_waiting_tool` 读取父 Session 的 `pending_turn`，校验 child scope 后再与最新 snapshot 中的 waiting checkpoints 精确比较；不一致时拒绝提交恢复。运行时产出恢复 snapshot 后，`SessionService._finalize_runtime_result` 先调用 `save_snapshot`，随后更新 pending-turn 元数据，最后通过 `save_session_with_events` 保存 Session。两处写入不是一个显式原子操作，因而存在短暂读到新旧版本不一致的可能；这是由代码顺序推导出的竞态候选，不足以解释受控失败日志在后续多次 reaper 检查中持续不匹配，也未证明实际隔离 PostgreSQL 中该不一致来自这段窗口。

受控失败原件中同一个父 ToolJob 按顺序等待三个 child，并有三份对应 parent snapshots；日志记录两次 `tool_child_wait_resume_submitted`，随后有多次 `tool_child_wait_resume_checkpoint_changed`。现存导出缺少每次提交时的 parent metadata、latest snapshot ID/内容、resume outbox 身份和事务时序，无法把这几条事件与具体 checkpoint 比对。故本轮没有放宽 checkpoint guard 或修改生产代码；下一步需要在 `_maybe_resume_waiting_tool` 的判断边界增加脱敏、仅测试/诊断启用的时序采样，或扩展现有隔离故障测试精确复现 metadata/snapshot 交错，再据同一失败状态决定根因修复。

## 陈旧审批投影修复后的复验（2026-10-19）

对后续隔离运行增加 parent Session 与最新 snapshot 时间线后，确认审批代理路径可能持有旧 SessionRecord；用它整对象保存会覆盖较新的 `interrupted + waiting_tool` pending-turn/control 状态，让协调器无法继续恢复。该真实失败样本把根因定位到代理审批投影使用陈旧对象覆盖整份 Session，而不是 checkpoint 内容本身。

修复将代理审批投影改为基于存储最新状态的原子 metadata 更新。PostgreSQL 在行锁内读取并传入当前状态，内存 store 在锁内执行相同接口；只有当前 Session 仍为 `waiting_approval` 时，才允许将 control/status 转到 `running`。若状态已经是 `interrupted`，只更新对应 worker dispatch，保留新的 waiting checkpoint。旧 `mutate_metadata(metadata)` 单参数契约保持原样。

复验结果：

- `tests/test_tool_wait_batch.py tests/test_tool_child_wait.py`：`22 passed, 4 skipped`；打开隔离 PostgreSQL 场景后：`3 passed, 23 deselected`。
- `tests/test_session_store_value_contract.py::test_atomic_metadata_update_uses_current_status`：memory/PostgreSQL 两种 store、`waiting_approval`/`interrupted` 两种当前状态共 `4 passed`。
- 真实默认 Step 模型 `step-5-preview` / `openai_chat_completions`、生产 API/Worker、隔离 PostgreSQL/Redis/RustFS 与本地认证 target 的同进程和持久子 Worker 场景：`2 passed, 4 deselected`，268.24 秒。两个场景各自只产生 `/login`、`/auth/refresh`、`/profile` 一次且均 HTTP 200；父报告 3/3 通过并最终 completed；9 次模型 HTTP attempt 与 Worker 日志对账；API 重启后的终态重投新增业务请求和模型请求均为 0；公开凭据扫描通过。
- 两个场景的 `verification.json`、PostgreSQL 任务/Outbox/Session/snapshot 诊断和 target 事件保存在 `Agent_Server/docs/upgrade_2026-10-03/p3-api-agent-worker-atomic-metadata-20261019/`。

这关闭了已复现的父审批投影覆盖等待 checkpoint 故障，并通过了这组默认模型 Worker 场景。Agent API 的其他鉴权负向、更多续期/依赖组合、超时异常以及完整产品报告流程仍需按总方案继续验收；P3/API 方向不因这两项 live 通过而整体宣告完成。

## 原子投影修复后默认模型当前工作区复验（2026-10-19）

接续上节的父审批投影修复，先根据 Registry 中 `api-test-runner` 的合并Schema及 API 编排Agent/子Worker不同职责，确认编排Agent不应拿到 `worker_action`、`task`、凭据容器或 dispatch 引用等内部参数；生产模型调用边界现在只为 `api-testing-agent` 且非正式用例执行场景投影精简Schema，Worker和正式用例执行保留原完整Schema。`tests/test_model_tool_visibility.py`、API派发契约、用例执行与Session回归 **27 passed、17 skipped，63.05秒**；跳过项是显式隔离服务门控测试。

随后运行 live Worker 测试的两个 `default-model` 变体（**2 passed、2 deselected，235.45秒**）。使用隔离 MySQL 配置表、PostgreSQL、Redis、RustFS、生产 API/Agent CLI Worker、MySQL 已认证 Step 默认模型 `step-5-preview` / `openai_chat_completions` 和本地受控认证 target；没有修改全局模型配置。新证据位于 `Agent_Server/docs/upgrade_2026-10-03/p3-api-agent-worker-default-current-20261019/`。

同进程与持久子 Worker 均完成 `POST /login → POST /auth/refresh → GET /profile`，三个目标请求各一次且均 HTTP 200；profile 使用刷新后的实际凭据。父任务最终 `completed`，报告 3/3 completed、0 failed、0 skipped；分别观察到 8 和 10 次模型 HTTP attempt。API 重启后重新投递已终结任务没有增加模型或目标 HTTP 请求。两份验证记录均确认生成凭据未出现在公开输出，credential scan 与 public-output scan 通过；持久模式另外保存父子 Session、Agent task、Outbox、审批及 snapshot 诊断。

另从当前 MySQL 只读读取默认模型密钥，在 33 个验收工件中进行精确字节扫描，没有发现密钥泄漏。本次结果是对固定正向认证链路的当前工作区复验，不覆盖鉴权失败、刷新失败、timeout/断网恢复、更多依赖关系或全部 API 产品流程；旧失败样本仍保留，不能据此声称所有重复调用或恢复故障已根除。全局 MySQL 默认配置未改变。
