# P2 正式 Run 计划修订边界与验收设计

日期：2026-10-20。本文前半部分记录实现前的源码核对与方案，历史判断予以保留；正式 Run 当前状态和后续验收以文末“实现后的验收与状态”为准。

## 源码确认

| 当前实现 | 证据 | 对修订的影响 |
|---|---|---|
| 项目计划创建、读取和首次 Run 启动 | `Agent_Server/src/api/routes/case_plans.py`、`application/test_runs/plan_run_service.py` | 目前没有 Run revision route/service |
| CasePlan 以新身份只增不改 | `Agent_Server/src/application/test_cases/planning_store.py` | 修订候选应由现有规划流生成并另存，不能覆盖原始依据 |
| 首次 Run 固定 Plan、Suite、RunItem 和 Outbox | `Agent_Server/src/application/test_runs/plan_mapping.py`、`application/test_runs/run_store.py::_create_planned_run_sync` | 候选修订不得重跑“创建 Run/Suite”的副作用 |
| 原快照按 canonical SHA 归档，Attempt 保存计划身份 | `Agent_Server/src/application/test_runs/plan_snapshot_identity.py`、`run_store.py` | 新投影可追加归档；旧 Attempt 和恢复必须继续解析原 SHA |
| 当前领取与修订候选共享事务锁 | `Agent_Server/src/application/test_runs/plan_revision_lock.py`、`run_store.py::_claim_items_sync` | 生产修订用排他 advisory lock；不能只对 Run JSON 做无锁覆盖 |
| RunItem/SuiteItem 唯一约束不同 | `Agent_Server/src/application/test_runs/run_store.py::_initialize_sync`、`application/test_suites/suite_store.py` | Run 增项与 Suite 冻结必须分离；同一 Run 不能重复插入同一用例版本或位置 |
| dispatch 循环、TaskDeferred 和 Redis reclaim | `Agent_Server/src/application/orchestration/redis_execution_dispatcher.py`、`runtime/redis_task_worker.py`、`infrastructure/redis_task_queue.py` | 非终态 Run 的已挂起任务可继续读取后续 queued 项；要用真实 Redis/Worker 验证重领时序 |
| 取消顺序为 Item → Run，但原路径不参加计划投影 advisory lock | `Agent_Server/src/application/test_runs/run_store.py::_cancel_run_sync` | 仅让领取使用共享锁并不足以协调修订；取消需要在读 Run 和锁条目前先取得共享锁，修订仍用排他锁 |

项目测试课程的 Agent 轨迹与协议鲁棒性章节要求覆盖重复执行、半成功、超时重试、补偿和数据隔离；评测工程化课程要求版本化输入与结果可追溯。由此，不能把一个新的 Plan JSON 写到当前 Run、再覆盖 RunItem 作为“修订完成”。

## 确定的修订语义

1. 用现有 CasePlanningService 生成并保存新的候选 CasePlan。候选计划必须属于同一 `project_id`，固定用例版本、来源证据、依赖和环境适用性仍由后端核验。原 CasePlan 与首次 Suite 保持不变。
2. 每次修订携带 `expected_revision`、`idempotency_key`、候选 `plan_id` 和人类可读原因。以规范化请求哈希区分相同请求重放和同 key 异请求。revision 记录及 CAS/幂等依据保存在 PostgreSQL；原/新完整执行快照继续按 SHA 写入 Run 的 `plan_snapshots`。
3. 同一 Run 只调整从未领取、`attempt_no == 0` 的 queued 项。新增目标插入新的 RunItem；移除项转为带修订原因的 `cancelled` 历史项，不删除数据库行、不伪造 Result。SuiteItem 仍表示首次冻结范围，报告需展示后续加入/移除的 RunItem 及修订来源。
4. 任何已有 Attempt、过期重领、正在等待审批/资源或终态的条目都不可重写。曾执行后回到 queued 的条目继续使用 Attempt 上的旧快照身份；不能因新计划覆盖其 owner、phase、输入绑定或依赖来改变恢复上下文。
5. 保留已归档的每个快照。新增 RunItem、更新未领取项、调整依赖/拓扑、更新统计、追加 revision 记录和写入新快照必须处于同一 PG 事务；Run 已完成或取消时拒绝修订。
6. 由 `UNIQUE(run_id,case_id,case_version_id)` 限制同一版本在一个 Run 只能出现一次。修改候选时须把已有版本映射到原 RunItem；不得重置 completed/cancelled 条目。已取消版本需要再次运行时，明确使用新 Run，而不是复活旧条目。
7. revision writer 获取与 claim/cancel 相同的 advisory 锁（排他）；claim 与 cancel 使用共享锁。修订按现有 Item → Run 行锁顺序锁定并复读，检查 expected revision、Run 终态、候选来源和所有被影响的 RunItem。取消必须先取共享锁再读取状态或锁条目，否则可能绕过修订屏障。不能让网络/模型规划持有数据库锁。
8. Run 初次 dispatch 仍是同一个 Run 任务。`RedisExecutionDispatcher` 在 Run 未终态时反复 claim；没有可执行项但仍有 outstanding 项会 defer。需要验证 defer 消息在实际 `XAUTOCLAIM` 后能继续执行刚加入的 RunItem，不额外发第二个不幂等的 Run 创建请求。

该边界将“修订计划”限定为更新当前 Run 尚未领取的执行投影，同时保留已执行依据和原始套件。若 Agent 发现问题时目标项已经领取，则新方案不能篡改已领取项；后续方案可只改其他未领取项，或在当前 Run 终态后创建新 Run。

## 实现前必须通过的功能验收

- 相同 idempotency key 与相同请求只新增一次 revision、RunItem 和归档；相同 key 但不同 hash 被拒绝；陈旧 expected revision 不写任何数据。
- revision 先拿锁时 claim 读到新依赖/顺序；claim 先领取时 revision 不修改该项；不同 RunItem 仍能并行领取。
- 修订前后的 Run/Suite/RunItem 与每个 Attempt 快照均能重读；已领取项的检查点、输入绑定、准备 owner/phase 和原始 Result SHA 不变化。
- 新增项由真实 Run dispatcher/Worker 消费；移除项有 `cancelled` 状态和明确原因；Run 统计、终态、报告及 RunItem 顺序与最终投影一致。
- 计划/用例/版本跨项目、依赖循环、重复 case/version、无效来源、环境状态变化以及 terminal Run 均被拒绝并且事务零部分提交。
- 修订持有排他投影锁期间，Run 取消必须等待；释放后取消只作用于已提交投影，修订和取消任一失败都不能留下半份 RunItem/快照/历史状态。
- 进程在 revision 提交前/后中断：提交前无部分写入；提交后以同 idempotency key 重放得到原 revision，不重复新增业务动作。
- 默认 Step 实际生成的多用例规划与自主修订仍需真实验收；受控 CasePlan 只能证明后端持久化与执行契约。

数据库仍按项目边界存储：模型/产品配置在 MySQL，Run、Plan、Suite、Attempt、Result 与 revision 历史在 PostgreSQL，原始文档/大型工件在 RustFS。此实施设计不要求修改全局模型配置或统一 Provider 协议。

## 取消协调边界的真实复核与修正

源码复核发现 `_claim_items_sync` 已先取计划投影共享 advisory lock，而 `_cancel_run_sync` 原先直接读取 Run、锁定未终态 RunItem 并锁 Run；因此取消可以在修订持有排他投影锁时继续提交。`tests/test_run_revision_coordination_research.py` 的候选修订实验只覆盖 claim 竞争，不能证明 cancel 竞争安全。原设计关于“沿用 Item → Run 锁顺序”的描述遗漏了这一共享协调入口。

先添加真实 PostgreSQL 回归并在修复前运行：取消在排他锁仍被持有时成功返回，测试以 **1 failed，9.30 秒**复现屏障缺口。随后修改 `PostgresTestRunStore._cancel_run_sync`，在读取 Run 状态、锁定 RunItem 之前调用现有 `lock_plan_projection(..., exclusive=False)`；该操作与 revision writer 的排他锁互斥，同时保留既有 Item → Run 行锁顺序和锁后状态复读。

功能验收：修复后的取消竞争测试 **1 passed，6.61 秒**。最终运行 `test_run_plan_projection_lock.py`、`test_run_revision_coordination_research.py`、`test_run_item_dependencies.py`、`test_test_run_duration_transitions.py`、`test_cancelled_run_missing_approval_recovery.py`，结果 **37 passed、8 skipped，27.42 秒**。开启的是 `RUN_LIVE_RUN_DEPENDENCIES=1`；8 个跳过项明确要求另设 `RUN_LIVE_POSTGRES_TESTS=1`。这验证了当前锁入口、候选协调实验及所选取消/依赖/恢复路径，不等于正式 Run 修订 API、revision history、增删条目、Redis defer/reclaim 或默认 Step 自主修订已实现/通过。

本次只修正 Run 取消加入现有共享投影锁；未创建 migration、未增 revision API/历史表、未修改模型配置，也没有改写用户 Run 数据。完整修订语义仍须按本设计的功能验收逐项实现。

## 实现后的验收与状态（2026-10-20）

本节覆盖上方“尚无 revision table/API”的实现前状态，保留前文作为设计决策和调研记录。

### 已实现

- `CasePlanRunService.revise` 读取同项目已保存的新 CasePlan，复用既有冻结用例校验和 `build_plan_run`；不创建或覆盖首次 Suite，不在数据库锁内调用模型。
- Run 增加 `plan_revision`，请求包含 `expected_revision`、`idempotency_key`、候选 `plan_id` 和原因。共享纯逻辑把候选计划映射回既有 RunItem；新增项用确定身份加入当前 Run，符合条件的移除项转为取消历史，已领取/有 Attempt/已有结果的项不被重置。
- PostgreSQL 增加从 Run 表名派生的 revision 历史表；应用启动时初始化。排他计划投影 advisory lock、Run/RunItem 行锁、revision CAS、快照归档、RunItem 投影、统计刷新和历史记录写入在同一事务中完成。相同幂等请求返回原修订；同 key 异请求、过期 revision 被拒绝。内存 Store 复用同一合并逻辑。
- 提供 Run 计划修订 POST 与历史 GET；前端已增加契约类型/API 方法，并在本次实现后增加人工评审页面；Agent 自动修订与真实浏览器到生产 API 联调仍未验收。
- 首轮真实 PostgreSQL 测试暴露位置投影缺陷：修订过程虽更新 JSON 记录，却未更新 RunItem 表的独立 `position` 列；详情聚合和 Worker claim 都按该列排序。修复 `_write_item` 将 position 与 record 同步写入。该缺陷在修复前由真实 PG 测试复现，修复后以详情顺序、列/JSON 一致性和实际 claim 顺序验证。

### 实际验证

在 `Agent_Server` 目录启用 `RUN_LIVE_PLAN_RUN_CREATION=1`、`RUN_LIVE_RUN_DEPENDENCIES=1`，运行计划创建/修订、RunItem 依赖、计划投影锁、候选修订协调、耗时状态、取消恢复及场景工具合同七个测试文件：**84 passed、8 skipped，61.95 秒**。该组合使用隔离 PostgreSQL/MySQL/RustFS 夹具和本地 `SequenceModel`；不调用默认 Step。新增 position 验收在修复前为 **1 failed（真实 PostgreSQL）、1 passed（内存）**，修复后为 **2 passed**。修订 API 前端单测 **1 passed**；`npm run build` 的 Vue 类型检查与 Vite 生产构建成功。

再启用 `RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_POSTGRES_TESTS=1`，运行 `test_revised_run_item_is_consumed_after_real_redis_defer_and_xautoclaim`：**1 passed、44 deselected，11.35 秒**。测试在随机 Redis stream/group 与隔离 PostgreSQL/MySQL/RustFS 中，经真实 PostgreSQL Outbox relay 发布首次 Run dispatch；生产 Dispatcher/Worker 因在途前置产生 defer，再由另一个 Worker 实际 `XAUTOCLAIM` 接管消息，领取、执行新加入的 RunItem 后再次 defer；释放前置后第三个 Worker 再次 `XAUTOCLAIM`，完成剩余 RunItem 并 ACK。用受控 RunItem 回执执行器，不调用默认 AI 或真实业务 HTTP；因此证明的是 Outbox/Redis/Persistent Run 调度续接，不是 Agent 工具执行质量。

随后对 `tests/test_case_plan_run_creation.py` 整个模块开启 `RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_POSTGRES_TESTS=1`：**45 passed、0 skipped，29.88 秒**。覆盖完整创建/修订模块的内存及实际隔离数据库路径，含上面的 Redis续接场景；这不是默认模型或真实业务工具测试。

### 仍未完成或未验收

- Agent 尚未在执行中自主识别新缺口、生成候选 CasePlan 并调用修订 API；默认 Step 模型尚未通过实际推理，因此默认模型“项目内选取→排序→补全→执行”仍未验收。
- 写入本节时尚无 revision 事务提交前/后的进程故障专项；后续新增结果见文末“事务进程故障与幂等恢复复核”。上述 Redis 验收仍使用受控执行回执，不覆盖 Agent 工具副作用恢复。
- 人工修订入口及历史/条目变化展示已有实现；真实浏览器到生产 API 用户路径仍未验收，Agent 自动候选规划与修订也尚未实现。
- 本轮验证保留 8 个受门控跳过项；它们不计入通过。完整 P0—P5、历史数据投影核对及两份旧 Worker 汇总原件恢复仍未完成。

模型配置仍在 MySQL，Run、RunItem、快照与 revision 历史在 PostgreSQL；未变更 Provider 协议或模型适配层。Step 认证已确认，但现有默认配置近期实测仍因 Step Plan 模型目录/推理额度问题返回供应商错误，不能把认证通过写成默认 AI 验收通过。

## 人工修订页面复核（2026-10-20）

Run 详情现提供人工候选计划修订和追加历史面板，并复用现有 CasePlanDialog 生成、审阅候选而不另启动 Run。实现依据是上方已验收的 POST/GET 契约与 `CasePlanRunService.revise` 的项目、运行状态、已保存 CasePlan、revision CAS 和幂等限制。页面展示条目变化；结果不明时重用原幂等键；接收成功后重新查询 Run。`RunPlanRevisionPanel.test.ts`、`CasePlanDialog.test.ts` 与 API 测试联合 **94 passed**，production build 通过。组件/API 使用 mock，因此没有证明生产 API 的浏览器端到端连接；这不替代 revision 进程中断恢复或默认 Step 自动规划验收。

Agent 仍未在运行图自主发现缺口、生成候选 CasePlan 并调用修订 API；人工页面可打开现有 CasePlanDialog 生成、审阅并选择同项目候选，也允许输入已保存计划编号。Step 账号认证按用户确认为通过，但最新隔离的默认调用仍返回 HTTP 400，脱敏 provider 消息为 `you have no active step plan subscription`。当前推理授权问题使默认模型闭环处于未验收状态，不构成切换所有模型协议的理由。

## 事务进程故障与幂等恢复复核（2026-10-20）

源码链路 `CasePlanRunService.revise → PostgresTestRunStore.revise_plan → _revise_plan_sync` 确认 RunItem 行、Run 计划快照与快照归档、统计刷新和 revision history 写入共用同一 PostgreSQL 事务。已有 `test_killing_revision_process_releases_resource_without_partial_attempt` 只覆盖 advisory lock 进程持有者退出；已有回滚测试通过注入 Python 异常验证 rollback。两者均不能单独证明进程失联时 writer 事务的提交前/后语义。

新增 `tests/test_case_plan_run_creation.py::test_run_plan_revision_process_loss_is_atomic_and_idempotent`，针对两处边界运行真实 `PostgresTestRunStore` writer 子进程并使用真实 PG 表：

1. **提交前：** 对本测试独占的 revision history 表加 SHARE lock，确认 writer 已将 RunItem 与 Run 投影写入未提交事务，并实际阻塞在最后的 history `INSERT`。第二连接仍只能读取 revision 0、旧计划和原条目；终止 writer 后，精确终止仍被锁等待的 PostgreSQL backend 以确定事务中止。相同幂等请求随后经 Run 修订 ASGI 路由提交 revision 1，最终只有一份历史、一份新条目和新增的一份快照。
2. **提交后、回执前：** writer 子进程收到生产存储事务已返回的状态，输出 `COMMITTED` 标记后暂停；测试在返回修订结果前杀掉子进程。读回已提交 revision，再经相同 ASGI 路由重放同一幂等键，只返回原 revision，不增加 RunItem、revision history 或快照。

Windows 测试宿主上，单独结束 TCP 客户端并不会立即唤醒正等待 heavyweight relation lock 的 PostgreSQL backend。因此测试基于 `pg_stat_activity` 和 `pg_blocking_pids` 先确定具体 SQL 与自有 backend PID，结束客户端后显式终止这个隔离后端，再检查事务回滚。这项事实已纳入测试设计，不把客户端进程退出误当作数据库事务已回滚。该验证覆盖实际存储 writer 和 ASGI 路由重放；不等同于终止并重启完整生产 Uvicorn API/Worker，也不覆盖浏览器连接实际服务。

验收命令与结果：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION = '1'
$env:RUN_LIVE_POSTGRES_TESTS = '1'
uv run --locked python -m pytest tests/test_case_plan_run_creation.py -q --tb=short
```

整模块 **47 passed、0 skipped，73.25 秒**。两个进程边界场景单独运行 **2 passed，18.42 秒**；真实事务内异常回滚测试 **1 passed，7.00 秒**。本项不调用 Step 或业务 HTTP，不创建 Attempt/Result，不改生产代码、模型配置、数据库 schema 或 Provider 协议。模型配置仍在 MySQL，Run、快照和 revision history 仍在 PostgreSQL。

**当前结论：** Run revision 后端/API、人工评审页面和 PG revision writer 提交前/后中断后的幂等恢复已有实现与专项证据。生产 Uvicorn API 进程杀停/重启、浏览器到真实 API 的端到端验收、Agent 自动发现缺口并创建/提交候选计划仍未完成。Step 账号认证按用户确认为通过；最近推理请求的 400 属于 `subscription_or_plan_entitlement`，P2 默认 AI 多候选规划与 Run 执行仍不可验收。
