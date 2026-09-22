"""生成 6.5 审批流 / 6.6 SSE 回放 / 6.7 资源租约 / 6.8 心跳竞争 四张图。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C, wrap
from seqkit import Seq
from gen_p3 import box, term, dia

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")
os.makedirs(OUT, exist_ok=True)


# ---------------------------------------------------------------- 图 6-5 审批流
def d09():
    s = Svg(1224, 0)
    s.t(24, 30, "图 6-5  危险操作人工审批全流程", 15, weight="700", fill="#263238")
    s.t(24, 48, "审批不占用执行进程：挂起→待批→续跑全程由持久任务驱动，Worker 重启不影响", 11,
        fill="#78909c")
    parts = [("U", "测试负责人"), ("FE", "工作台"), ("API", "FastAPI"),
             ("W", "Agent Worker"), ("G", "LangGraph"), ("PG", "PostgreSQL")]
    q = Seq(s, parts, x0=24, top=72, colw=200, headh=48)
    q.msg("G", "G", "permission_gate 判定需要人工审批")
    q.msg("G", "PG", "写 agent_session_approvals(status=pending)")
    q.msg("G", "PG", "落 graph.waiting_for_approval 事件")
    q.msg("G", "W", "pending_turn（本轮就此挂起）", kind="reply")
    q.msg("W", "PG", "任务转「等待审批」态")
    q.msg("PG", "FE", "SSE 推送 approval.created", kind="reply")
    q.msg("FE", "U", "渲染审批卡片（tool_key / reason）", kind="reply")
    q.msg("U", "FE", "点击「批准」")
    q.msg("FE", "API", "POST /api/v1/sessions/{id}/approvals/{approval_id}")
    q.msg("API", "PG", "resolve_approval（乐观并发，防重复决策）")
    q.msg("API", "PG", "写持久续跑任务 submit_approval，返回 202")
    q.msg("API", "FE", "202 + AgentTaskAccepted", kind="reply")
    q.note(("API", "PG"), "续跑复用与提交消息完全相同的持久任务机制：不依赖原进程、原 Worker 是否存活",
           fill=C["indigo"], stroke=C["indigoS"])
    q.msg("PG", "W", "新一轮任务开始执行续跑", kind="reply")
    q.msg("W", "PG", "approval.decision_replayed / applied")
    q.msg("W", "G", "从待恢复快照恢复 state")
    q.msg("G", "G", "重入图执行，完成原轮")
    q.msg("G", "PG", "approval.resolved + runtime.turn_completed")
    q.lifelines(q.y + 6)
    s.h = q.y + 30
    s.save(os.path.join(OUT, "09-approval.svg"))
    print("09-approval.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-6 SSE 回放
def d10():
    s = Svg(840, 0)
    s.t(24, 30, "图 6-6  跨进程 SSE 事件回放", 15, weight="700", fill="#263238")
    s.t(24, 48, "数据库序号 + 游标增量拉取，替代传统内存广播，天然支持断线重连", 11, fill="#78909c")
    parts = [("FE", "前端 useSSEStream"), ("API", "SSE 端点 /events"),
             ("PG", "PostgreSQL agent_session_events")]
    q = Seq(s, parts, x0=24, top=72, colw=260, headh=48)
    q.msg("FE", "API", "GET /api/v1/sessions/{id}/events，Last-Event-ID: evt_1024")
    q.msg("API", "API", "校验会话存在，否则返回 404")

    yl = q.y - 4
    q.msg("API", "PG", "SELECT ... WHERE event_sequence > :cursor ORDER BY event_sequence LIMIT :batch_size")
    q.msg("PG", "API", "返回一页事件", kind="reply")

    yil = q.y - 4
    q.msg("API", "FE", "逐条推送 id / event / data 帧")
    q.msg("FE", "FE", "更新本地 cursor")
    q.frame("loop 逐条推送", yil, q.y + 2)

    ya = q.y - 4
    q.msg("API", "API", "EventCursorNotFoundError → session_event_cursor_reset，游标重置为最早可用序号")

    ye = q.y - 4
    q.msg("API", "FE", ": keep-alive（空行保活）", kind="reply")
    q.msg("API", "API", "await is_disconnected()，客户端断开即退出")
    q.alt("alt 游标越界（事件已被清理）", "else 正常轮询", ya, ye, q.y + 2)
    q.frame("loop 轮询间隔 session_sse_poll_seconds", yl, q.y + 2)
    q.note(("PG",), "event_sequence 为 BIGSERIAL，配合 UNIQUE(session_id, event_sequence) 构成单调序号",
           fill=C["yellow"], stroke=C["yellowS"])
    q.lifelines(q.y + 6)
    s.h = q.y + 30
    s.save(os.path.join(OUT, "10-sse.svg"))
    print("10-sse.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-7 资源租约生命周期
def d11():
    s = Svg(1060, 0)
    s.t(24, 30, "图 6-7  资源租约生命周期与 fencing 令牌", 15, weight="700", fill="#263238")
    s.t(24, 48, "先持久化创建意图，再抢占写入令牌，最后绑定真实 ID；两段式避免半成品资源", 11,
        fill="#78909c")

    CX, BW = 220, 300
    term(s, CX, 66, "START", w=64)
    steps = [("intent 创建意图", "创建意图先落 PostgreSQL，再抢占", 120, C["grey"]),
             ("claimed 已抢占", "Redis Lua CLAIM 写入 fencing_token", 210, C["yellow"]),
             ("bound 已绑定", "BIND 绑定真实 container_id / browser_id", 300, C["green"]),
             ("renewed 已续约", "RENEW 心跳续约，延长租约", 390, C["blue"])]
    for label, body, y, col in steps:
        box(s, CX, y, BW, 44, label, body, fill=col, stroke="#90a4ae", tsize=11.5, bsize=10)
    s.arrow(CX, 80, CX, 98, stroke="#546e7a", sw=1.5)
    s.arrow(CX, 142, CX, 188, stroke="#546e7a", sw=1.5)
    s.arrow(CX, 232, CX, 278, stroke="#546e7a", sw=1.5)
    s.arrow(CX, 322, CX, 368, stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g L %g %g L %g %g" % (CX + BW / 2, 390, 430, 390, 430, 300,
                                                CX + BW / 2 + 5, 300),
           stroke="#546e7a", sw=1.5, marker="ah")
    s.t(440, 350, "继续运行", size=10, anchor="start", fill="#2e7d32")

    exits = [("released 正常释放", "RELEASE：任务结束后主动归还", 140, C["green"]),
             ("reaped 过期回收", "REAP：租约超时后被回收", 230, C["orange"]),
             ("retired 主动报废", "RETIRE：资源异常，直接作废不再复用", 320, C["red"]),
             ("reconciled 配额对账", "RECONCILE_QUOTA：周期性核对配额", 410, C["blue"])]
    EX, EW = 760, 280
    s.path("M %g %g L %g %g L %g %g" % (CX + BW / 2, 300, 600, 300, 600, 410),
           stroke="#546e7a", sw=1.5)
    for label, body, y, col in exits:
        box(s, EX, y, EW, 44, label, body, fill=col, stroke="#90a4ae", tsize=11.5, bsize=10)
        s.arrow(600, y, EX - EW / 2, y, stroke="#546e7a", sw=1.5)
    term(s, EX, 490, "END", w=64)
    for _l, _b, y, _c in exits:
        s.path("M %g %g L %g %g" % (EX + EW / 2, y, EX + 40, y), stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g" % (EX + 40, 140, EX + 40, 476), stroke="#546e7a", sw=1.5)
    s.arrow(EX + 40, 476, EX + 40, 476, stroke="#546e7a", sw=1.5)

    s.rect(40, 460, 480, 54, fill=C["yellow"], stroke=C["yellowS"], rx=5, dash="5 3")
    s.t(50, 480, "抢占失败时状态只到 waiting_resource", size=10.5, weight="700", fill="#5d4037")
    s.t(50, 496, "CLAIM 失败绝不伪装成 running，避免「假执行」占用配额", size=10, fill="#5d4037")
    s.h = 530
    s.save(os.path.join(OUT, "11-lease.svg"))
    print("11-lease.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-8 心跳竞争
def d12():
    s = Svg(1180, 0)
    s.t(24, 30, "图 6-8  会话执行权与心跳竞争模型", 15, weight="700", fill="#263238")
    s.t(24, 48, "执行协程与心跳协程并行，先完成者决定本轮走向，Fail-fast 而非无限等待", 11,
        fill="#78909c")

    box(s, 200, 84, 300, 44, "execute_claimed_message", "Worker 入口")
    dia(s, 200, 176, 240, 62, "获取会话执行租约")
    box(s, 620, 176, 280, 52, "放弃执行", "落 session_execution_ownership_lost，交还任务",
        fill=C["red"], stroke=C["redS"])
    box(s, 200, 280, 300, 44, "启动心跳协程 _monitor", "独立协程周期性 RENEW", fill=C["blue"],
        stroke=C["blueS"])
    box(s, 200, 390, 280, 44, "RuntimeService.execute_turn", "真正执行智能体一轮", fill=C["green"],
        stroke=C["greenS"])
    box(s, 560, 390, 300, 44, "心跳持续 RENEW lease", "维持会话执行权不丢", fill=C["blue"],
        stroke=C["blueS"])
    dia(s, 470, 510, 340, 72, "asyncio.as_completed（FIRST_COMPLETED）", size=10.5)
    box(s, 250, 630, 280, 44, "取消心跳，提交终态", "runtime.turn_completed", fill=C["green"],
        stroke=C["greenS"])
    box(s, 830, 630, 300, 52, "会话执行权丢失", "优雅退出，不做多余写入", fill=C["red"],
        stroke=C["redS"])
    box(s, 250, 716, 400, 44, "recover_expired_turn_executions", "兜底扫描，接管失联执行",
        fill=C["grey"], stroke=C["greyS"])

    s.arrow(200, 106, 200, 145, stroke="#546e7a", sw=1.5)
    s.arrow(320, 176, 480, 176, stroke="#546e7a", sw=1.5, label="失败", lfill="#c62828")
    s.arrow(200, 207, 200, 258, stroke="#546e7a", sw=1.5, label="成功", lfill="#2e7d32")
    s.path("M %g %g L %g %g L %g %g" % (200, 302, 200, 348, 200, 368), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.path("M %g %g L %g %g L %g %g" % (350, 280, 480, 280, 480, 368), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.t(415, 272, "并行", size=10, anchor="middle", fill="#6d4c41")
    s.path("M %g %g L %g %g L %g %g" % (200, 412, 200, 520, 300, 520), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.path("M %g %g L %g %g" % (560, 412, 560, 474), stroke="#546e7a", sw=1.5,
           marker="ah")
    s.path("M %g %g L %g %g L %g %g" % (320, 534, 250, 534, 250, 608), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.t(258, 528, "执行先完成", size=10, anchor="start", fill="#2e7d32")
    s.path("M %g %g L %g %g L %g %g" % (620, 534, 830, 534, 830, 604), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.t(742, 528, "心跳先失败", size=10, anchor="middle", fill="#c62828")
    s.arrow(250, 652, 250, 694, stroke="#546e7a", sw=1.5)

    s.rect(700, 690, 440, 74, fill=C["yellow"], stroke=C["yellowS"], rx=5, dash="5 3")
    s.t(712, 710, "为什么要竞争而不是串行等待", size=10.5, weight="700", fill="#5d4037")
    for i, ln in enumerate(wrap("RENEW 失败意味着会话执行权已被他人接管，此时再写终态会覆盖对方的"
                                "结果；Fail-fast 是唯一安全选择。", 416, 10)):
        s.t(712, 724 + i * 13.5, ln, size=10, fill="#5d4037")
    s.h = 790
    s.save(os.path.join(OUT, "12-heartbeat.svg"))
    print("12-heartbeat.svg", s.w, s.h)


if __name__ == "__main__":
    d09(); d10(); d11(); d12()
