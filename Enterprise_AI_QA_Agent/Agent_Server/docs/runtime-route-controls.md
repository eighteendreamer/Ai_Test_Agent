# Deep Agents 新回合路由与存量执行控制

本说明对应 v4 §14.2 和实施记录 §106，描述当前实现，不是生产灰度或 E4 回滚演练的验收证明。

## 只停止新接入

在已经具备兼容运行能力的服务配置中设置：

```dotenv
DEEP_AGENTS__NEW_TURNS_ENABLED=false
```

它只关闭 `RuntimeService` 对**无绑定新请求**的 Deep Agents 选路，使其沿原 legacy 路径执行。默认值为 `true`，因此不改变已有选路行为；运行能力总开关 `DEEP_AGENTS__ENABLED` 仍默认 `false`，不会因为新增配置就启用生产流量。

已有 `runtime_binding` 先按原身份与运行能力校验，再沿原框架路径执行，不受此开关影响。已经进入框架的调用不会被取消，已有待审批回合也不会被切成 legacy 重跑。请求正文或 context 中同名字段不能覆盖服务端配置。

当前正式入口尚未全面开放新 v2 绑定生产。此开关不是底层 `submit_v2` 的全局禁投开关：预先建立绑定的任务仍属绑定执行路径。未来新绑定生产者必须在创建绑定前接入相应选路控制，不能声称本开关已经覆盖所有未来入口。

## 三种职责不能混用

| 控制 | 影响 | 不代表什么 |
| --- | --- | --- |
| `deep_agents.new_turns_enabled` | 无绑定新回合是否可进入 Deep Agents；仍受模式白名单和总能力约束 | 不撤销绑定、不停止在途工具、不清空审批或任务 |
| `deep_agents.enabled` 和 `checkpoint_enabled` | 兼容 Worker 的运行能力、checkpoint 装配及 v2 claim 前准入 | 不是“只停止新接入”；关闭后可能使存量任务无法继续 |
| 既有显式中断入口 | 按原 Session/Task 停止、owner 撤销与未知效果契约处理 | 不等于远端领域 Runner 已停止，也不由本开关触发 |

| `enabled` | `new_turns_enabled` | 无绑定新回合 | 已有 Deep Agents 绑定 |
| --- | --- | --- | --- |
| false | 任意 | legacy | 运行能力不足；不能静默 fallback |
| true | false | legacy | 兼容能力及原 owner/审批校验通过后继续原路径 |
| true | true | 命中模式白名单且 adapter 可用才进入框架 | 兼容能力及原 owner/审批校验通过后继续原路径 |

兼容 v2 Worker 仍要求 `enabled && checkpoint_enabled`，并保留 runtime/version、依赖锁、operation 和持久格式检查。停止新接入时，**不要同时移除排空 Worker 已经具备的这些能力**。身份漂移、不支持的 runtime 或缺失 adapter 仍明确拒绝，不转换为 legacy 文本重放。

## 配置生效与部署边界

- Settings 由现有缓存加载，Runtime 在 lifespan 装配时捕获配置；没有新增热更新 API。修改文件不会自动改变已经运行的对象。测试中修改私有属性只模拟选路变化，不是运维接口。
- 依照既有部署流程让新配置进入实际消费者，同时保留承接存量回合的兼容制品。旧制品可能忽略新字段；只有兼容读者已覆盖对应新流量入口，才能依赖本控制。
- 清点待审批、排队、在途、恢复及未知效果绑定后，才能判断旧 Worker 是否可排空；不要删除记录、重置终态、改写 checkpoint 或盲目重发外部操作来“排空”。
- 本变更没有 DDL、依赖升级、绑定格式或公开 API 变更；未开启生产路由，未提供远端 Runner 停止协议。双制品、备份恢复及真实部署回滚仍待 E4 验收。

## 日志与验证落点

关闭新选路时记录 `deep_agent_new_turn_route_disabled`，带 `session_id`、`turn_id` 和 `recovery_reason=new_turn_admission_disabled`；这是路由决定，不是取消/停止回执。

- `tests/test_framework_route_rollback.py`：配置、实际 Runtime 选路、绑定矩阵、在途调用、请求覆盖负例，以及真实 CLI/dispatcher 搭配替身队列的能力准入。main 的接线审计为静态 AST 检查，不替代完整 lifespan。
- `tests/test_framework_approval_runtime.py`：固定官方框架和真实隔离 PostgreSQL/Redis 下，关闭新接入后重建 Runtime，仍保留审批重放、后继、工具效果次数和原消息身份。该开关场景本身不是独立进程部署验收。
- `tests/test_deep_agent_runtime_adapter.py`：原无绑定 pilot 在途审批的批准、拒绝、范围改变与混合批次；官方框架、内存 saver、受控工具，不是实际 CLI 工具或供应商验收。
- `tests/test_agent_v2_worker_contract.py`、`test_runtime_binding_persistence.py`、`test_framework_task_control.py`、`test_framework_approval_recovery.py`：沿既有版本/绑定、停止和恢复契约回归；具体命令、实际结果及证据限制见实施记录 §106。

实现依据是当前 `src/application/runtime/runtime_service.py`、`src/main.py`、`src/cli/run_agent_v2_worker.py` 和 `src/runtime/framework_worker_capabilities.py`。不新增调度层，不把路由权限当成外部执行停止证明。
