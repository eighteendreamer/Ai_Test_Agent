# 正式回执纠正的真实进程中断调研

本批仅验证一次只读回执纠正的生产进程恢复。默认 Step 按用户决定等待实名认证，不调用该提供商。

## 依据与采用做法

- `agent_case_receipt.py:correct`：调用前保存固定请求对象及 invoking 检查点；只有已保存 response 检查点才能读取响应恢复。invoking 状态不能证明响应，转人工查证，不再次调用。固定模型、快照、原预算、传输选项都需要核对。
- `agent_case_execution.py:execute/_finish`：存在纠正检查点时直接恢复正式评估，不重新进入业务工具循环。
- `execution_service.py:_resolve_agent_approval`：最后审批后在 API 进程执行；因此应终止实际 API，不能以终止早已返回的 Worker 代替。
- `run_store.py:_save_checkpoint_sync/_complete_item_sync/_recover_expired_sync`：检查点独立提交；结果插入发生在另一事务。恢复真实过期租约后保留旧 attempt，领取新 attempt 时继承原检查点和恢复链。
- `test_execution_worker_cli.py`：现有完整 API lifespan、独立执行 CLI、真实隔离 MySQL/PostgreSQL/RustFS/Redis，以及本地 HTTP 模型与有状态订单目标作为测试依据。增加可选 HTTP 回应门控，默认行为不变。
- PostgreSQL 官方[显式锁文档](https://www.postgresql.org/docs/current/explicit-locking.html)：SHARE 表锁允许普通读取，阻止 INSERT 所需的 ROW EXCLUSIVE 锁。仅在测试独立数据库持有结果表 SHARE 锁；通过 pg_locks/pg_stat_activity 证明实际 INSERT 被阻塞，再终止 API。回滚释放锁，不新增生产故障注入代码。
- 本地课程《Agent轨迹与协议鲁棒性》《Agent测试专项》要求轨迹、重复执行、超时恢复及终态不可重领；实际物理 HTTP 和 POST/GET/DELETE 副作用计数与 PG/RustFS 原件共同验收，编译不代替功能验证。

## 验证设计与边界

两种现场：请求已实际到达 HTTP、invoking 已保存但回应尚未发送；回应与 response 检查点已实际保存，结果事务被实际表锁阻塞。终止自己启动的 API 进程，等待原 90 秒租约自然过期，调用现有 recover-expired，再由新独立 Worker 处理原投递。

需要证明：前者正式 error 并标注 recovery_required，后者复用响应完成正式评估；两者只调用一次纠正，不重复三次业务操作，原模型预算与传输选项不变化，新旧 attempt 可追溯，终态重投没有新调用。恢复前变更当前配置，验证固定上下文不被覆盖。

RunItem 与独立资源租约不同：生产默认分别约 90 秒和 300 秒。测试需等待两者自然过期，实际生产清理 CLI 处理未绑定外部资源的逻辑租约并保存 skipped 回执。这里 skipped 表示不需要删除外部资源，不能当作工具或用例通过。核对 Redis 活跃项、待清理项及三类配额均为空或零后再重投。

## 实测过程与纠正

- first：2 失败，147.33 秒。invoking 场景确实终止 API 并恢复条目，但资源租约未过期，Worker 正确等待，不能由 Worker 退出 0 宣称完成；response 场景误将数据库上下文管理器当连接，未执行该中断。
- recovery：2 失败，54.08 秒。已实际取得标准表锁、response 检查点及阻塞 INSERT，但测试把资源 dataclass 的 lease_expire_at 误当 Redis 原始字段。核查保存的原件和 Lua，实际为 lease_expire_at_ms；错误发生在 kill 前，不能算完成中断验收。
- verified：1 通过、1 失败，677.98 秒。invoking 已真实完成两类租约自然过期、清理、恢复 error/recovery_required 及终态重投，没有新请求/业务操作。response 已真实 kill、复用原响应并完成正式 error；失败原因是测试用例写“检查”而受控回执引用“期望”，生产正确拒绝不存在的固定预期路径。此阶段不是生产恢复缺陷，也不能算 response 通过。
- 对第三次失败改用共享原生产用例数据，停止复制一份不同的业务字段。新增真实失败响应回放测试：原“检查”数据四个引用均被拒绝，共享原“期望”数据分别解析 200、false、0、null；1 通过。未改生产校验、错误处理或资源算法。
- 回执、实际 SDK 发送重试、审批预算及执行核验五模块：82 通过、2 默认外部模型项跳过，87.62 秒。原生产 API/CLI 常规路径：1 通过，47.02 秒。共享样本及最终两类进程场景正在重跑，以最终记录为准。
- 对 recovery 失败留下的三个测试专属 RustFS 请求/响应，按原 PG 检查点的完整项目/run/item/attempt/call 前缀确认归属，实际下载 SHA/大小校验后删除；逐个读取确认 HTTP404。原件及删除回执本地保存，未覆盖失败输出或旧证据。

此专项不证明默认 AI 的质量、完整 UI 执行、未知业务副作用的通用自动查证或完整 P0—P5 完成。

## 最终验收

delivery：2 通过，687.30 秒，0 跳过。两个实际 kill、自然租约过期、生产清理 CLI、新执行 Worker、原预算及终态重投场景全部符合预期；response 正式 passed，invoking 正式 error/recovery_required。每项只有规划 1、Agent 4、纠正 1 次物理模型请求，业务创建/查询/清理各一次，无新动作。

共享样本后的原生产流程复跑 1 通过，46.90 秒。没有生产源码修复；新增实际进程测试、真实失败回放、可选 HTTP 门控及共享测试用例。没有增加前端、协议、依赖、配置项或数据迁移，因此未跑前端构建。配置仍用现有 MySQL；固定业务、attempt/结果/清理回执 PG；请求/响应 RustFS。原默认 Step 仍暂缓。

最终及失败批次 14 次对象实际下载的原件全部纳入 SHA/大小审计；旧 8176 工件和受保护文件保留。原失败目录不覆盖，审计及三份方案同步见 p0-receipt-process-validation.json 和 PLAN6.98。
