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

**存储边界治理：** 引入内部 session state revision，并让完整快照写入携带预期 revision；不匹配时明确返回冲突，不再静默覆盖新状态。所有会改变 status/metadata 的行级原子更新同步推进 revision。API DTO 不暴露 revision。把调用方逐步迁移为按意图的原子命令：metadata projection、状态转换、消息追加、运行检查点提交各自只写自己的状态域；只有持有有效 turn/continuation lease 且 revision 匹配的运行写入可以提交完整执行状态。冲突时重新读取并重新应用意图，禁止盲目重试旧副本。

该策略沿用本项目已经存在的行锁、turn lease、continuation lease、事件追加及 detached-value 契约；它不增加第二套会话存储、不改变 Worker 操作方式，也不依赖跨进程内存锁。

## 实施顺序

1. 新增确定性交错回归，证明旧通知保存会丢失新 `pending_turn`，并验证通知分支识别新检查点。**完成。**
2. 修正通知投影：基于刚读取的会话状态继续；非持久通知不做全量 SessionRecord 保存；自动停止使用原子 status/message/event 投影。**完成。**
3. 为 PostgreSQL 与内存存储实现 state revision/CAS，覆盖初始化、读取、保存、metadata/status 原子写、导入/导出和所有 SessionStore 实现；冲突错误向调用方可见。
4. 逐个审计 `save_session` 消费点，处理冲突并将易并发的消息、队列、审批、运行状态更新迁移至 intent-scoped 写接口。
5. 验证确定性并发、PostgreSQL 实际存储、父子 Worker 同进程通知、跨进程恢复、审批与终态重投；同时确认原有模式、工具权限及公开 DTO 未变。

## 验收边界

短期修复验收已满足：交错回归证明最新检查点不会被通知覆盖，等待工具通知从重新读取的父会话识别并交给恢复路径；既有双协调器重复投影测试与相关定向套件通过。原子 status/message/event 操作在真实 PostgreSQL 隔离表上验证了状态写入、检查点保留和重复消息/事件去重。架构治理还需独立的 PostgreSQL 并发/CAS 验收。持久 Worker 的真实恢复验收必须使用当前生产 reclaim 默认值，或有明确依据的测试时限；不能把 1 秒 ownership uncertainty 解释为产品通过或产品缺陷。Step 当前请求返回 400 的错误正文没有采集，不能将其与 Worker 受控模型失败混为一谈。
