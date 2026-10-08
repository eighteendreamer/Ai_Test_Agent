# 跨审批模型预算与待审批恢复调研

日期：2026-10-08；基线 `fc7f94bd`。本批实现图/审批累计预算，不包含正式回执的只读纠正。原可信默认模型 UI 运行仍为 error，原历史不变。

## 真实反例与根因

原默认模型可信 UI 记录在 `p0-tool-evidence-production-final/trusted-parent/`：20 次执行请求，而最终图快照的 loop_iteration=0、max_iterations=8。不能把这 20 次都叫重复请求；每次审批恢复进入模型前没有累计预算检查，原循环计数只在自动继续工具循环时增加。

第一次修改后，真实 HTTP/PG 诊断还显示第一次快照 count=1，后续 count 消失。`graph/nodes/tool_executor.py` 自己构造 pending_turn，漏存了预算；只修改 RuntimeService 的序列化不足以覆盖实际暂停边界。原失败和 PG 原件保存在 `p0-approval-budget-diagnostic/`。

多项审批测试进一步复现：初始两项审批，批准第一项后尚有一个 pending_approval_id，但恢复状态里的 pending_approvals 为空，snapshot.stage=resumable，正式适配器正确拒绝继续。根因是同一图节点只保存审批 ID，没有保存审批记录。真实内存与独立 PG 的失败均记录在 `p0-approval-budget-batch-before/`；不能靠让正式适配器接受 resumable 来绕过。

## 当前代码依据 → 采用方式

| 依据 | 事实与本批实现 |
|---|---|
| `application/runtime/agent_loop.py::run_turn` | loop_iteration 只记录自动重新入图；保留它，新增累计逻辑模型调用数，不用审批次数推算 |
| `graph/nodes/model_invoker.py` | 正常 LangGraph 模型发送的共同入口；在 invoke 前增加 model_call_count，并在下一次准入前检查原 max_iterations 及既有 90% 上下文阈值 |
| `application/runtime/state_persistence.py::persisted_turn_budget` | 已有预算序列化；图暂停、Runtime 快照/暂停、恢复、TurnState 拆装共用它，保存累计数和原上限 |
| `graph/nodes/tool_executor.py`、`runtime_service.py::_build_pending_turn` | 后者已有 pending_approvals；前者同步完整审批集合。批准部分工具后保留 waiting_approval，最后一项后才重新调用模型 |
| `graph/nodes/finalizer.py` | 保留准入前已经确定的预算终态；普通工具循环使用新累计数，缺失新字段的旧快照仍按旧逻辑恢复 |
| `runtime_service.py::execute_turn` | 仅正常 LangGraph 新 turn 初始化累计数，专用 security/DeepAgents 路径不假造计数 |
| `core/config.py`、`main.py` | 复用原 orchestration.runtime_max_iterations；没有新配置键、MySQL配置表、依赖或数据库迁移，也没有修改默认模型配置 |
| `agent_web/src/components/chat/SnapshotTracePanel.vue` | 显示可选“模型调用”累计值/原上限；保留原循环值，历史快照缺字段不显示虚构计数 |

一次模型回复中的多项工具审批共用一次模型调用。达到原预算时允许已有工具完成并保存证据，禁止下一次模型发送；上限最后一次直接返回合法最终回执时仍可通过。SDK内部HTTP attempt属于传输指标，不能冒充逻辑调用数。

## 实际验证范围

独立 PG/真实 HTTP 场景验证：POST创建、GET查询、DELETE清理；原上限3在配置默认改99后仍停止第4次调用；上下文用受控提供方950/1000 usage验证审批后停止；两项审批首项通过后不增加模型调用，三次调用内合法回执通过并且原终态重放零新增。

生产 API/独立 Worker 场景使用隔离 MySQL、全新 PG、RustFS、临时 Redis Docker、真实进程关闭/重启和受控 HTTP 模型。原上限3/8，重启进程默认改为13/18后仍恢复原上限；分别验证预算耗尽 error 与合法 passed、真实清理和原终态重投。这是受控模型的运行恢复验收，不能当作外部默认模型 UI 已通过。

前端挂载真实组件，读取实际生产 PG 新/旧快照；节点测试放在项目已有 tests 目录，以免将 Node 证据读取混入浏览器类型检查。最初在 src 放测试引起 Node 模块类型检查失败，原 build 日志保留，未为了测试新增依赖或扩大 tsconfig。

完整命令、各次失败与最终结果见 IMPLEMENTATION.md 和 `p0-approval-budget-validation.json`。模型 admission 在当前图状态增加，仍在既有快照边界持久化；这不证明任意发送后、快照前断电都已解决传输未知。只读正式回执阶段另按 2.4.1 保存调用意图与响应，必须使用剩余原预算，不能重做业务操作。

## 未完成

正式只读回执纠正、默认模型可信 UI passed/重投、无父会话的产品目标授权仍未实现/验收。本批没有修订原失败结果或完成运行条目。人工 DeepSeek、完整 App/录制清洗遗漏、Docker 性能安全、其余完整 P0—P5 和生产历史迁移继续保留。
