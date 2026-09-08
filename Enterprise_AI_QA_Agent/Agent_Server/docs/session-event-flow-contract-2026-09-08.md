# Session Event、Snapshot 与 Flow 契约台账

日期：2026-09-08  
阶段任务：P0-10  
状态：已完成

## 目的与边界

本台账冻结本地执行轨迹的跨层契约，供后续 LangChain、Deep Agents 和 LangSmith 接入复用。它不创建新的事件总线、Trace 存储或前端数据通路：PostgreSQL Session / Event / Snapshot 仍是业务事实源，LangSmith 只是可关闭的旁路观测。

## 权威模型

| 对象 | 权威定义 | 持久化 | 主要消费者 | 不承担的职责 |
|---|---|---|---|---|
| `ExecutionEvent` | `src/schemas/session.py` | `postgres_event_table.payload`（JSONB） | SSE、`/events/history`、Flow、会话活动面板 | 不能替代 Snapshot 或外部 LangSmith Run |
| `SessionSnapshot` | `src/schemas/session.py` | `postgres_snapshot_table.graph_state`（JSONB） | 恢复、回放、Flow Inspector | 不能替代逐条实时 Event |
| `SessionFlowResponse` | `src/schemas/flow.py` | 无；只读投影 | `agent_web` Flow | 不写 Event、不重放执行、不成为执行状态机 |
| `langsmith_trace` | Snapshot 的 `context_bundle.langsmith_trace` | 随本地 Snapshot 保存 | Flow 外链 | 不作为本地 Trace、Session 或 Turn 的主键 |

## `ExecutionEvent` 稳定字段

| 字段 | 类型 | 生产方 | 约束 |
|---|---|---|---|
| `id` | UUID 字符串 | `ExecutionEvent` 默认工厂 | SSE 的 `id`；客户端用于去重 |
| `type` | 字符串 | Runtime、Graph、Session Service、Tool/Worker 路径 | 保持稳定的点分名称；Flow 以映射表解释状态 |
| `session_id` | 字符串 | 创建 Event 的服务 | 必须属于当前 Session |
| `timestamp` | ISO 8601 时间 | 创建 Event 的服务 | 排序主键之一；同刻以 `id` / 业务 `step` 处理 |
| `payload` | 递归 JSON object | 各生产方，经 `OutputSafetyPolicy` 的 Graph Event 路径 | 可扩展；不得含密钥、Cookie、Authorization、密码或未脱敏原文 |

`payload` 的共享字段为 `turn_id`、`phase`、`message`、`step`、`trace_id` 和可选 `correlation_id`。其中 `turn_id` 用于 Flow 轮次筛选，`phase` 用于 Flow 节点投影；其余字段不能被假设为所有 Event 都存在。

前端在 `agent_web/src/types.ts` 以递归 `JsonObject` / `JsonValue` 接收 payload，与后端 `dict[str, Any]` 和 PostgreSQL JSONB 的实际能力一致；禁止再缩窄为一层标量字典。

## 单一数据通路

```text
append_graph_event / SessionService._make_event
  -> ExecutionEvent
  -> SessionStore.append_event
  -> PostgreSQL JSONB +（可选）SSE Queue
  -> /events/history 与 /events SSE
  -> FlowProjectionService.get_flow（只读）
  -> Vue Flow / Event Console
```

Graph Event 会先进入 `AgentGraphState.event_log`，在 `RuntimeService._events_from_log` 变为 `ExecutionEvent`。已实时推送的 Graph Event 在最终持久化时以 `publish=False` 写入，避免 SSE 重复；客户端仍以 Event ID 去重。Session Service 直接生成的 Event 则直接写入 Store。

## Flow 投影规则

| 输入 | 投影规则 | 验证位置 |
|---|---|---|
| Event 的 `payload.turn_id` | 默认展示最新轮；指定轮仅保留同轮或无轮次的 Event | 后端/前端 Flow 测试 |
| Event 的 `payload.phase` + `type` | `projection_service.py` 与 `stages.ts` 使用等价映射生成状态和边 | 后端/前端 Flow 测试 |
| Snapshot `graph_state.turn_id` | 只选目标轮 Snapshot；不回退到其他轮 | `test_session_flow_projection.py` |
| Snapshot `context_bundle.langsmith_trace` | 仅作为可选外链返回；未启用时返回空对象 | `test_session_flow_projection.py` |
| Session / Graph `worker_dispatches` | 按 task / child session 归并并按父 Turn 筛选 | 后端/前端 Flow 测试 |

## 不变量、测试与退出证据

1. Event 历史、Flow 响应和 SSE 对同一 Event 的嵌套 JSON payload 不得丢失或变形。
2. Flow 查询必须是只读的，不得新建 Event、Snapshot 或重放执行。
3. Event、Snapshot 与 LangSmith 关联均使用本地 `session_id` / `turn_id` / `trace_id`；外部 Run ID 不能反向成为业务主键。
4. LangSmith 禁用或上报失败时，上述本地通路不变。

本批新增回归：`test_nested_event_payload_survives_history_flow_and_sse_contract` 覆盖 Event History、Flow、Snapshot 中的 Trace 引用和 SSE 序列化；前端 `flow.test.ts` 覆盖递归 JSON payload 类型。完整后端、前端、实际 FastAPI 默认模型会话的执行结果记录在主工程方案第 14 节的批次记录中。
