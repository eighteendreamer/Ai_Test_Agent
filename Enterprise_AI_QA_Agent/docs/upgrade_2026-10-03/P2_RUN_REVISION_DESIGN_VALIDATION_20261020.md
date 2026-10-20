# P2 正式 Run 计划修订边界与验收设计

日期：2026-10-20。此文档是实现前的源码核对结果，不代表正式 Run 已支持计划修订。

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
