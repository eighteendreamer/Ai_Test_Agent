# Session 写入并发与 Worker 通知检查点设计

## 范围

处理 Agent Worker 完成通知与父会话运行检查点并发时的状态丢失。保留现有 Agent、测试模式、工具、权限、运行状态和 Redis 任务协议；不以 Step 默认模型结果作为本问题的依据，不改默认模型配置。

## 代码依据与现场证据

1. `Agent_Server/src/runtime/store.py` 的 `SessionStore` 明确把读取结果定义为 detached values。`InMemorySessionStore._save_session_locked` 和 `PostgresSessionStore._save_session_in_transaction` 都接收一份完整 `SessionRecord` 再写回。
2. `Agent_Server/src/application/orchestration/coordinator_runtime_service.py` 的 `_project_worker_notification` 已通过 `update_metadata_and_append_event` 与 `mutate_metadata` 做通知元数据的行级更新；`_deliver_notification` 后续在通知消息上设置 `persist_transcript=False`，仍调用 `save_session(parent_session)`。两个存储实现都会跳过这类消息正文的 transcript 持久化，因此这次整行保存对通知正文没有持久化贡献。
3. PostgreSQL 的 `_save_session_in_transaction` 用传入副本整体替换 metadata，只额外保留 turn lease、coordinator dispatch claim 和 worker dispatch 等少数字段。`pending_turn`、`control`、`pending_input_queue`、`verification_results` 等不在保留集合中。数据库行锁只序列化 SQL 写入，不能识别传入副本是否已经过时。`mutate_metadata` 则是不同路径：它在 `SELECT ... FOR UPDATE` 后基于当前行计算新 metadata。
4. `Agent_Server/tests/test_busy_session_queue_concurrency.py::test_empty_pending_queue_cleanup_preserves_concurrent_session_status` 已为类似的“读旧 SessionRecord 后写回”风险增加专门保护，说明该风险不是 Worker 专属；不应再增加只保留某个 metadata key 的补丁。
5. `Agent_Server/docs/upgrade_2026-10-03/p3-api-agent-worker-current-20261020-01/controlled-in-process/parent-checkpoint-timeline.json` 末次采样中，最新快照为 `interrupted` 且包含一个 `waiting_tools` 检查点；会话行仍为 `running`、无该等待项，却保留了旧 `pending_approval_ids`。同批受控 Worker 验收仅完成 `/login`，没有完成预期的三次 API 事件。该时间线先提供了风险线索；随后新增的 `_deliver_notification` 确定性交错回归先以 `KeyError: pending_turn` 复现，证明通知路径的旧副本全量保存会覆盖并丢失运行方新写入的检查点。
6. 同批 `controlled-durable` 场景在首次模型请求前因 1 秒 Redis reclaim 阈值发生 ownership uncertainty；该阈值远低于生产默认 120 秒。它不是父检查点根因的证据，不据此修改 Worker 产品逻辑；后续需在生产默认阈值或有依据的时限下复验。

## 设计决策

**短期修复（已实施并验证）：** 用完整 `_deliver_notification` 路径做确定性交错测试：通知元数据投影完成后，模拟会话运行方提交更新后的 `pending_turn`，再让通知流程继续。修复前测试因 `pending_turn` 丢失而失败；修复后通知处理重新读取当前状态用于分支判断，不再把事件型非持久消息和旧 `SessionRecord` 整体写回。自动停止的终态状态、消息和事件由内存/PostgreSQL SessionStore 在一个原子操作中提交。内存与真实 PostgreSQL 隔离表上的原子操作测试均通过，重复投影保持单条消息和单个事件。

**存储边界治理：** 当前 `SessionRecord` 与 `agent_sessions` 表都没有 revision；PostgreSQL 的 `save_session` 是整行快照 upsert，InMemory 也是整对象写回。新增仅供存储层使用的 `state_revision`（DB `BIGINT NOT NULL DEFAULT 0`，领域记录携带预期值，API DTO 与 JSON 导入/导出不暴露）。完整快照写入按预期 revision 做 CAS；revision 不匹配时抛出明确冲突，不能静默覆盖，也不能把旧快照原样重试。

revision 只覆盖可由 `SessionRecord` 快照覆盖、且没有独立合并规则的会话状态：会话标量字段（不含 `updated_at`）及 metadata（排除 `turn_lease_*`、`coordinator_dispatch_claims`、`worker_dispatches`）。完整快照继续从当前存储行保留这三类 SQL 所有权字段。`event_count`、`snapshot_count` 由现有单调更新规则保护，事件、快照、审批行有各自的追加或条件更新语义，不推进会话 revision。租约取得/释放会推进 revision，以保护所有权边界；租约续期只改过期时间，不推进 revision，避免心跳造成冲突。消息是按 ID 追加/更新的独立行：新消息追加不推进 revision；任何可能覆盖快照中已有消息的 delivery metadata 更新必须先锁定所属 session 行并推进该行 revision。

所有会改变上述覆盖状态的原子写入均推进 revision，包括状态/metadata 投影、运行检查点全量写入、租约取得/释放、Worker 状态变更及已有 transcript 消息投递状态变更。事件、快照计数和专属 dispatch 字段的原有独立保护不并入 revision。PostgreSQL 所有涉及 session 与 task/approval/message 的写入继续遵守先锁 session、再锁子记录、最后写消息/事件的顺序；现有 AgentTaskStore 的 claim、transition、timeout-reaper 与审批提交均已按 session 优先顺序取锁。

调用方按意图写入：只改 metadata 的通知、队列、控制字段使用行锁下的 metadata mutation；状态及控制变更使用原子状态/metadata 命令；运行完成提交前读取最新 SessionRecord，将运行拥有的 `pending_turn`、本轮 control/context usage/verification 及新增 transcript 合并到最新记录，再以当前 revision 和有效 turn/continuation lease 提交。冲突只有在业务意图仍成立时才允许重新读取并重新应用该意图；用户偏好等快照编辑返回可识别的冲突供调用方重载，不自动覆盖。

该策略沿用本项目已经存在的行锁、turn lease、continuation lease、事件追加及 detached-value 契约；它不增加第二套会话存储、不改变 Worker 操作方式，也不依赖跨进程内存锁。

## 实施顺序

1. 新增确定性交错回归，证明旧通知保存会丢失新 `pending_turn`，并验证通知分支识别新检查点。**完成。**
2. 修正通知投影：基于刚读取的会话状态继续；非持久通知不做全量 SessionRecord 保存；自动停止使用原子 status/message/event 投影。**完成。**
3. 为 PostgreSQL 与内存存储实现 state revision/CAS，覆盖初始化、读取、保存、metadata/status 原子写、导入/导出和所有 SessionStore 实现；同步 AgentTaskStore 的 session/message 直接写入口；冲突错误向调用方可见。**完成。** Worker 投递状态、租约取得/释放、Session 状态与可覆盖 metadata 的直写路径都会推进 revision；心跳续租、事件/快照计数和独立 dispatch 字段遵循各自的并发规则。revision 不进入 Session API DTO 或 JSON 备份。
4. 逐个审计 `save_session` 消费点，处理冲突并将易并发的通知、队列、审批、控制字段更新迁移至 intent-scoped 写接口；运行完成从最新快照合并运行拥有的数据后 CAS 提交。**完成。** 新建直接保存；偏好更新将冲突返回为 HTTP 409；输入提交与运行完成在冲突后重新读取并重应用意图，最多重试三次。Worker 的执行消息上下文在 CAS 重读时保留领取时的消息 ID 集合，排队中的后续消息不会混入当前任务，但仍留在持久化会话供后续任务消费。
5. 验证确定性并发、PostgreSQL 实际存储、父子 Worker 同进程通知、跨进程恢复、审批与终态重投；同时确认原有模式、工具权限及公开 DTO 未变。**本轮定向验收通过：** `test_session_store_value_contract.py`（内存 + 真实 PostgreSQL）75 passed；`test_agent_task_dispatch.py`（真实 PostgreSQL + Redis）19 passed；`test_busy_session_queue_concurrency.py` 19 passed；Coordinator/dispatch/timeout 定向回归 11 passed；`test_worker_notification_reloads_parent_checkpoint_without_stale_full_save` 1 passed。AgentTask 全量回归曾暴露 CAS 重读重新引入后续排队消息的回归，修复后全文件通过。持久 Worker 的定向验收使用默认配置下的真实跨进程测试；历史上人为设为 1 秒的 ownership uncertainty 结果不作产品结论。默认模型真实请求返回 HTTP 403（账号认证限制），用户安排账号处理后再做真实 AI 验收；该结果与本次存储并发验收分开。

## 验收边界

短期通知修复和存储并发治理均已通过当前定向验收：交错回归证明最新检查点不会被通知覆盖，等待工具通知从重新读取的父会话识别并交给恢复路径；PostgreSQL 与内存 SessionStore CAS、Worker 派发与租约回收回归通过。生产环境 Step 模型验收因 HTTP 403 账号认证限制按用户安排暂缓；该阻塞不影响本次确定性存储和 Worker 协议测试结果。
