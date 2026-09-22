"""生成图 5-3 数据流全景（A 提交与投递 / B 认领与执行 / C 事件回放）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C
from seqkit import Seq

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")
os.makedirs(OUT, exist_ok=True)


# ------------------------------------------------- A 提交与投递
def d04a():
    s = Svg(1240, 0)
    s.t(24, 30, "图 5-3A  数据流全景（一）：提交与 Outbox 投递", 15, weight="700", fill="#263238")
    s.t(24, 48, "同步阶段只写库并立即返回 202；投递由独立中继进程异步完成，二者靠 Outbox 事务解耦",
        11, fill="#78909c")
    parts = [("U", "测试工程师"), ("FE", "Vue 工作台"), ("API", "FastAPI"),
             ("DS", "AgentTask-\nDispatcher"), ("OB", "PostgreSQL\nOutbox"),
             ("RL", "Outbox Relay"), ("RS", "Redis Stream\nqa:tasks:agent")]
    q = Seq(s, parts, x0=24, top=72, colw=170, headh=54)
    q.msg("U", "FE", "输入测试意图，点击「发送」")
    q.msg("FE", "API", "POST /api/v1/sessions/{id}/messages")
    q.msg("API", "DS", "submit(session_id, payload)")
    q.msg("DS", "DS", "计算 input_hash 与 idempotency_key")
    q.msg("DS", "OB", "同一事务内写入 agent_turn_tasks(status=queued) 与 agent_task_outbox")
    q.msg("DS", "FE", "202 Accepted + AgentTaskAccepted（task_id / turn_id）", kind="reply")
    q.msg("FE", "U", "显示「已受理，正在执行」，不等待执行结果", kind="reply")
    q.note(("DS", "OB"), "提交与投递解耦：API 进程崩溃也不丢任务（业务表与 Outbox 同事务）")
    q.msg("RL", "OB", "轮询未投递的 outbox 记录")
    q.msg("RL", "RS", "XADD qa:tasks:agent")
    q.msg("RL", "OB", "标记 delivered_at", kind="reply")
    q.lifelines(q.y + 6)
    s.h = q.y + 30
    s.save(os.path.join(OUT, "04a-dataflow-submit.svg"))
    print("04a-dataflow-submit.svg", s.w, s.h)


# ------------------------------------------------- B 认领与执行
def d04b():
    s = Svg(1740, 0)
    s.t(24, 30, "图 5-3B  数据流全景（二）：认领、执行与结算", 15, weight="700", fill="#263238")
    s.t(24, 48, "Worker 认领任务 → 租约与 fencing 令牌 → LangGraph 单轮执行 → 终态落库与资源回收",
        11, fill="#78909c")
    parts = [("AW", "Agent Worker"), ("RS", "Redis Stream\nqa:tasks:agent"),
             ("DS", "AgentTask-\nDispatcher"), ("RL_", "Redis 租约\nLua 脚本"),
             ("OB", "PostgreSQL"), ("SS", "SessionService"), ("RT", "RuntimeService"),
             ("AL", "AgentLoop"), ("G", "LangGraph\n状态机"),
             ("SINK", "SessionEventSink")]
    q = Seq(s, parts, x0=24, top=72, colw=170, headh=54)
    q.msg("AW", "RS", "XREADGROUP（消费者组）")
    q.msg("AW", "DS", "handle(task_id, command)")
    q.msg("DS", "DS", "校验任务身份 9 字段")
    q.msg("DS", "RL_", "resources.acquire(agent, turn:{task_id})")
    q.msg("RL_", "DS", "返回 fencing_token", kind="reply")

    ya = q.y - 4
    q.msg("DS", "OB", "tasks.defer + 记录 TaskDeferred 事件")
    q.msg("DS", "AW", "延期重试（资源被更高优先级占用）", kind="reply")
    q.note(("SS", "RT", "AL", "G", "SINK"),
           "资源不足时绝不伪装成 running：任务退回队列，DEFER 事件留下审计痕迹",
           y=ya + 2, x0=q.px["SS"] - 26, fill=C["cyan"], stroke=C["cyanS"])
    ydiv = q.y + 2

    q.msg("DS", "OB", "tasks.begin(attempt_id, lease_token, fencing_token)")
    q.msg("DS", "AW", "启动 _monitor 心跳协程")
    q.msg("AW", "SS", "execute_claimed_message(...)")
    q.msg("SS", "RT", "execute_turn(...)")
    q.msg("RT", "AL", "run_turn(...)")
    q.alt("alt 资源不足（延期重试）", "else 资源就绪（开始执行）", ya, ydiv, q.y + 2)

    q.msg("AL", "G", "graph.ainvoke(state)")

    yl = q.y - 4
    q.msg("G", "G", "context_builder / router / planner / permission_gate")

    ya2 = q.y - 4
    q.msg("G", "SINK", "graph.waiting_for_approval")
    q.msg("G", "AL", "pending_turn（跳出循环，等待审批续跑）", kind="reply")
    ydiv2 = q.y + 2

    q.msg("G", "G", "prompt_assembler / model_invoker")
    q.msg("G", "SINK", "model.request_prepared / model.response_received")
    q.msg("G", "G", "tool_executor → finalizer → responder")
    q.msg("G", "AL", "continue_loop / termination_reason", kind="reply")
    y_alt_end = q.y + 2
    q.alt("alt 触发审批（挂起等待）", "else 模型直接继续执行", ya2, ydiv2, y_alt_end)
    q.frame("loop 最多 max_iterations = 8 轮", yl, y_alt_end)
    q.at(y_alt_end)

    q.msg("AL", "RT", "汇总本轮结果与 token 用量")
    q.msg("RT", "SINK", "runtime.turn_completed")
    q.msg("RT", "OB", "更新任务终态并清理资源")
    q.msg("DS", "RL_", "RELEASE / RECONCILE_QUOTA")
    q.note(("DS", "RL_", "OB"),
           "RELEASE 释放租约，RECONCILE_QUOTA 对账配额，杜绝孤儿容器与浏览器实例堆积",
           y=q.y + 2, fill=C["cyan"], stroke=C["cyanS"])
    q.lifelines(q.y + 6)
    s.h = q.y + 30
    s.save(os.path.join(OUT, "04b-dataflow-execute.svg"))
    print("04b-dataflow-execute.svg", s.w, s.h)


# ------------------------------------------------- C 事件回放
def d04c():
    s = Svg(900, 0)
    s.t(24, 30, "图 5-3C  数据流全景（三）：事件持久化与 SSE 回放", 15, weight="700", fill="#263238")
    s.t(24, 48, "事件先落库拿到单调序号，前端再按游标增量拉取，跨进程、可重连、不丢不重", 11, fill="#78909c")
    parts = [("SINK", "SessionEventSink"), ("PG", "PostgreSQL\nagent_session_events"),
             ("SSE", "SSE 端点"), ("FE", "前端 useSSEStream")]
    q = Seq(s, parts, x0=24, top=72, colw=200, headh=54)
    q.msg("SINK", "PG", "INSERT agent_session_events（event_sequence BIGSERIAL）")
    q.msg("FE", "SSE", "GET /api/v1/sessions/{id}/events，Last-Event-ID: evt_1024")
    yl = q.y - 4
    q.msg("SSE", "PG", "SELECT ... WHERE event_sequence > cursor LIMIT batch_size")
    q.msg("SSE", "FE", "逐条推送 id / event / data 帧")
    q.msg("FE", "FE", "更新本地 cursor")
    q.frame("loop 轮询（session_sse_poll_seconds）", yl, q.y + 2)
    q.at(q.y + 8)
    q.note(("PG", "FE"), "event_sequence BIGSERIAL + UNIQUE(session_id, event_sequence)：单调序号是无锁增量拉取的唯一事实来源",
           fill=C["yellow"], stroke=C["yellowS"])
    q.msg("SSE", "FE", ": keep-alive（空行保活）", kind="reply")
    ya = q.y - 4
    q.msg("SSE", "SSE", "EventCursorNotFoundError → session_event_cursor_reset")
    q.frame("alt 游标越界（事件已被清理）", ya, q.y + 2)
    q.msg("SSE", "FE", "重置为最早可用序号后继续", kind="reply")
    q.lifelines(q.y + 6)
    s.h = q.y + 30
    s.save(os.path.join(OUT, "04c-dataflow-replay.svg"))
    print("04c-dataflow-replay.svg", s.w, s.h)


if __name__ == "__main__":
    d04a(); d04b(); d04c()
