"""生成 6.1 注册中心 / 6.2 LangGraph / 6.3 AgentLoop / 6.4 任务状态机 四张图。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C, wrap, text_width

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")
os.makedirs(OUT, exist_ok=True)


def box(s, cx, cy, w, h, title, body=None, fill=C["box"], stroke=C["boxS"],
        tsize=11.5, bsize=10, tfill="#1f2933", bfill="#546e7a"):
    s.node(cx - w / 2, cy - h / 2, w, h, title, body, fill=fill, stroke=stroke,
           tsize=tsize, bsize=bsize, tfill=tfill, bfill=bfill)


def term(s, cx, cy, label, w=72, h=28, fill=C["grey"], stroke=C["greyS"]):
    s.stadium(cx, cy, w, h, fill=fill, stroke=stroke)
    s.t(cx, cy + 4, label, size=11, anchor="middle", weight="600", fill="#37474f")


def dia(s, cx, cy, w, h, text, size=11, fill=C["yellow"], stroke=C["yellowS"]):
    s.diamond(cx, cy, w, h, fill=fill, stroke=stroke, sw=1.4)
    lines = wrap(text, w * 0.62, size)
    n = len(lines)
    for i, ln in enumerate(lines):
        s.t(cx, cy - (n - 1) * 7 + i * 14 + 4, ln, size=size, anchor="middle",
            fill="#5d4037", weight="600")


def note(s, x, y, w, title, body, fill=C["yellow"], stroke=C["yellowS"]):
    lines = wrap(body, w - 16, 10)
    h = 26 + len(lines) * 13.5
    s.rect(x, y, w, h, fill=fill, stroke=stroke, rx=5, dash="5 3")
    s.t(x + 8, y + 17, title, size=10.5, weight="700", fill="#5d4037")
    for i, ln in enumerate(lines):
        s.t(x + 8, y + 31 + i * 13.5, ln, size=10, fill="#5d4037")
    return y + h


# ---------------------------------------------------------------- 图 6-1 六大注册中心
def d05():
    W, s = 1240, Svg(1240, 0)
    s.t(24, 30, "图 6-1  六大注册中心与统一描述符契约", 15, weight="700", fill="#263238")
    s.t(24, 48, "所有可插拔能力都走「描述符入册 → 统一契约读取 → 装配期绑定」的同一条路径", 11,
        fill="#78909c")

    LH, y = 404, 66
    s.layer(20, y, 470, LH, "① 六大注册中心 · RegistryService")
    rows = [("AgentRegistry", "45 个智能体", C["purple"]),
            ("ToolRegistry", "57 个静态工具 + 动态 MCP 工具", C["orange"]),
            ("ModelRegistry", "MySQL 驱动，运行时热更新", C["blue"]),
            ("SkillRegistry", "15 内置 + 54 磁盘 → 合并后 54", C["green"]),
            ("MCPRegistry", "4 个 MCP Server", C["cyan"]),
            ("ModeRegistry", "14 种模式（8 实现 + 6 占位）", C["indigo"])]
    ry = y + 44
    for name, desc, col in rows:
        box(s, 275, ry + 16, 200, 30, name, None, fill=col, stroke="#90a4ae", tsize=11.5)
        s.t(390, ry + 20, desc, size=10.5, anchor="start", fill="#546e7a")
        ry += 58
    s.t(255, y + LH - 14, "注册中心在 lifespan 期构建为 99 个应用级单例之一", size=10,
        anchor="middle", fill="#90a4ae", italic=True)

    s.layer(540, y, 320, LH, "② 统一描述符契约")
    descs = [("AgentDescriptor", "系统提示 / 工具集 / 模式"),
             ("ToolDescriptor", "签名 / 权限级 / 资源域"),
             ("ModelConfigRecord", "provider / 端点 / 上下文窗"),
             ("SkillDescriptor", "触发条件 / 附带工具 / 版本"),
             ("MCPServerDescriptor", "传输方式 / 命名空间"),
             ("ModeDescriptor", "绑定的 Agent / Skill / Tool")]
    ry = y + 44
    for name, desc in descs:
        box(s, 700, ry + 16, 210, 30, name, None, fill=C["grey"], stroke=C["greyS"],
            tsize=11)
        s.t(700, ry + 36, desc, size=9.5, anchor="middle", fill="#90a4ae")
        ry += 58
    s.t(700, y + LH - 14, "描述符即契约：新增能力不改动内核代码", size=10, anchor="middle",
        fill="#90a4ae", italic=True)

    s.layer(910, y, 310, LH, "③ 装配期关系")
    rels = [("Mode 绑定", "如 security_testing_mode，装配时绑定 Agent / Skill / Tool",
             C["indigo"]),
            ("MCP 命名空间", "工具以 mcp__<server>__<tool> 注册进 ToolRegistry", C["cyan"]),
            ("Skill 懒加载", "技能内工具展开后才进入 ToolRegistry 可见集合", C["green"])]
    ry = y + 44
    for name, desc, col in rels:
        box(s, 1065, ry + 16, 260, 30, name, None, fill=col, stroke="#90a4ae", tsize=11.5)
        s.wrapped(1065, ry + 44, desc, 250, size=10, lh=13.5, anchor="middle", fill="#546e7a")
        ry += 118

    for yy in (y + 100, y + 250, y + 380):
        s.arrow(490, yy, 540, yy, stroke="#546e7a", sw=1.5)
        s.arrow(860, yy, 910, yy, stroke="#546e7a", sw=1.5)
    s.h = y + LH + 26
    s.save(os.path.join(OUT, "05-registries.svg"))
    print("05-registries.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-2 LangGraph 状态机
def d06():
    W, s = 1180, Svg(1180, 0)
    s.t(24, 30, "图 6-2  LangGraph 九节点状态机（单轮 turn 内部）", 15, weight="700", fill="#263238")
    s.t(24, 48, "节点间只通过状态字典传递数据；条件边决定走「工具执行」还是「直接收尾」", 11,
        fill="#78909c")

    CX = 260
    ys = {}
    seq = ["context_builder", "router", "planner", "permission_gate",
           "prompt_assembler", "model_invoker"]
    y = 84
    for name in seq:
        ys[name] = y
        y += 62
    BY = ys["model_invoker"] + 62

    term(s, 88, ys["context_builder"], "START", w=64)
    s.arrow(120, ys["context_builder"], 140, ys["context_builder"], stroke="#546e7a", sw=1.5)
    for a, b in zip(seq, seq[1:]):
        s.arrow(CX, ys[a] + 15, CX, ys[b] - 15, stroke="#546e7a", sw=1.5)
    box(s, CX, BY + 6, 240, 44, "tool_executor", "执行工具调用并落执行事件",
        fill=C["orange"], stroke=C["orangeS"])
    box(s, 640, BY + 6, 240, 44, "finalizer", "模型直接产出结论，无工具调用",
        fill=C["green"], stroke=C["greenS"])
    s.t(CX + 40, BY - 18, "route_after_model_invoker = tool_executor", size=10,
        anchor="start", fill="#6d4c41")
    s.t(760, BY - 18, "= finalizer", size=10, anchor="end", fill="#6d4c41")
    s.path("M %g %g L %g %g L %g %g" % (CX, ys["model_invoker"] + 15, CX, BY - 26, 520, BY - 26),
           stroke="#546e7a", sw=1.5, marker="ah")
    s.path("M %g %g L %g %g L %g %g" % (CX, ys["model_invoker"] + 15, CX, BY - 26, 640, BY - 26),
           stroke="#546e7a", sw=1.5, marker="ah")

    RY = BY + 66
    box(s, CX, RY, 240, 44, "responder", "结果改写为面向用户的一轮回复",
        fill=C["blue"], stroke=C["blueS"])
    s.path("M %g %g L %g %g L %g %g" % (CX, BY + 28, CX, RY - 22, CX, RY - 22),
           stroke="#546e7a", sw=1.5, marker="ah")
    s.path("M %g %g L %g %g L %g %g" % (640, BY + 28, 640, RY - 22, CX, RY - 22),
           stroke="#546e7a", sw=1.5, marker="ah")
    term(s, CX, RY + 46, "END", w=64)
    s.arrow(CX, RY + 22, CX, RY + 32, stroke="#546e7a", sw=1.5)

    for nm in seq:
        box(s, CX, ys[nm], 240, 30, nm, None, fill=C["purple"], stroke=C["purpleS"],
            tsize=11.5)

    notes = {
        "context_builder": "组装 memory / test_case / observation / mcp 四类命中，输出 prompt block",
        "router": "判定本轮意图，决定后续规划与可用工具面",
        "permission_gate": "硬门禁：denied 直接拒绝；approval_required 则挂起等待人工审批",
        "model_invoker": "记录 token 用量与 context_window，落 model_request_prepared",
        "tool_executor": "execution_started → execution_completed / denied / blocked / failed",
    }
    ny = ys["context_builder"] - 10
    for nm, body in notes.items():
        h = 26 + len(wrap(body, 380, 10)) * 13.5
        s.rect(700, ny, 400, h, fill=C["yellow"], stroke=C["yellowS"], rx=5, dash="5 3")
        s.t(710, ny + 17, nm, size=10.5, weight="700", fill="#5d4037", mono=True)
        for i, ln in enumerate(wrap(body, 380, 10)):
            s.t(710, ny + 31 + i * 13.5, ln, size=10, fill="#5d4037")
        ny += h + 14
    s.h = max(RY + 70, ny) + 20
    s.save(os.path.join(OUT, "06-langgraph.svg"))
    print("06-langgraph.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-3 AgentLoop
def d07():
    W, s = 1020, Svg(1020, 0)
    s.t(24, 30, "图 6-3  AgentLoop 外层多轮循环控制流", 15, weight="700", fill="#263238")
    s.t(24, 48, "循环的四个出口：中断、异常恢复、continue_loop、达到 max_iterations", 11,
        fill="#78909c")

    box(s, 200, 90, 250, 46, "run_turn 入口", "state = build_state(session, mode)")
    dia(s, 200, 176, 220, 62, "interrupt_requested ?")
    box(s, 590, 176, 330, 52, "抛出 InterruptTurn", "runtime.interrupt_requested 事件后退出")
    box(s, 200, 262, 250, 46, "graph.ainvoke(state)", "执行单轮 LangGraph 状态机")
    dia(s, 200, 348, 220, 62, "执行异常 ?")
    box(s, 590, 348, 330, 58, "错误恢复路径", "提取 error_code 与部分结果，不向上抛原始异常")
    dia(s, 200, 434, 220, 62, "continue_loop ?")
    box(s, 200, 520, 250, 58, "finalize 收尾", "summary + token 用量；runtime.turn_completed / turn.failed")
    box(s, 560, 434, 300, 44, "runtime.loop_reenter", "loop_iteration += 1")
    dia(s, 560, 520, 200, 62, "loop_iteration >= 8 ?")
    box(s, 850, 520, 280, 44, "graph.max_iterations_reached", "强制收尾，防止无限循环")
    term(s, 200, 606, "返回")

    s.arrow(75, 90, 75, 176, stroke="#546e7a", sw=1.5)
    s.path("M 200 113 L 200 145", stroke="#546e7a", sw=1.5, marker="ah")
    s.arrow(310, 176, 425, 176, stroke="#546e7a", sw=1.5, label="是", lfill="#2e7d32")
    s.arrow(200, 207, 200, 239, stroke="#546e7a", sw=1.5, label="否", lfill="#c62828")
    s.arrow(200, 285, 200, 317, stroke="#546e7a", sw=1.5)
    s.arrow(310, 348, 425, 348, stroke="#546e7a", sw=1.5, label="是", lfill="#2e7d32")
    s.path("M 590 377 L 590 420 L 320 420 L 320 403", stroke="#546e7a", sw=1.5, marker="ah")
    s.arrow(200, 379, 200, 403, stroke="#546e7a", sw=1.5, label="否", lfill="#c62828")
    s.arrow(200, 465, 200, 491, stroke="#546e7a", sw=1.5, label="false", lfill="#c62828")
    s.arrow(310, 434, 410, 434, stroke="#546e7a", sw=1.5, label="true", lfill="#2e7d32")
    s.path("M 710 434 L 710 500 L 660 500", stroke="#546e7a", sw=1.5, marker="ah")
    s.arrow(660, 520, 755, 520, stroke="#546e7a", sw=1.5, label="是", lfill="#2e7d32")
    s.path("M 560 549 L 560 580 L 200 580 L 200 580", stroke="#546e7a", sw=1.5)
    s.path("M 200 549 L 200 145 L 200 145", stroke="#546e7a", sw=1.5)
    s.path("M 60 520 L 60 176 L 92 176", stroke="#546e7a", sw=1.5, dash="6 3", marker="ah")
    s.t(52, 360, "否：进入下一轮", size=10, anchor="middle", fill="#6d4c41")
    s.path("M 850 549 L 850 606 L 240 606", stroke="#546e7a", sw=1.5, marker="ah")
    s.arrow(200, 549, 200, 580, stroke="#546e7a", sw=1.5)
    s.h = 650
    s.save(os.path.join(OUT, "07-agentloop.svg"))
    print("07-agentloop.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-4 任务状态机
def d08():
    s = Svg(1240, 0)
    s.t(24, 30, "图 6-4  持久任务状态机（agent_turn_tasks）", 15, weight="700", fill="#263238")
    s.t(24, 48, "任何状态切换都先落库再发事件，进程重启后由恢复扫描接手", 11, fill="#78909c")

    box(s, 480, 90, 210, 44, "queued 已入队", "提交 POST 得到 202")
    box(s, 480, 170, 210, 44, "claimed 已认领", "handle 校验任务身份 9 字段")
    box(s, 480, 250, 210, 44, "running 执行中", "attempt_id / lease_token / fencing_token")
    box(s, 190, 170, 200, 44, "deferred 延期", "资源不足或让位")

    term(s, 480, 46, "START", w=64)
    s.arrow(480, 60, 480, 68, stroke="#546e7a", sw=1.5)
    s.arrow(480, 112, 480, 148, stroke="#546e7a", sw=1.5)
    s.arrow(480, 192, 480, 228, stroke="#546e7a", sw=1.5)
    s.arrow(375, 170, 290, 170, stroke="#546e7a", sw=1.5, label="资源不足", lfill="#c62828")
    s.path("M 190 192 L 190 130 L 375 130", stroke="#546e7a", sw=1.5, marker="ah")
    s.t(282, 124, "重新入队", size=10, anchor="middle", fill="#2e7d32")

    s.path("M 480 272 L 480 320 L 120 320 L 120 360 L 120 360", stroke="#546e7a", sw=1.5)
    s.path("M 480 272 L 480 320", stroke="#546e7a", sw=1.5)
    s.path("M 120 320 L 840 320", stroke="#546e7a", sw=1.5)
    for cx in (180, 330, 480, 630, 780):
        s.arrow(cx, 320, cx, 338, stroke="#546e7a", sw=1.5)

    terms = [(180, "completed 完成", C["green"]), (330, "failed 失败", C["red"]),
             (480, "cancelled 取消", C["grey"]), (630, "interrupted 中断", C["orange"]),
             (780, "expired 过期", C["indigo"])]
    for cx, label, col in terms:
        box(s, cx, 360, 140, 44, label, None, fill=col, stroke="#90a4ae", tsize=11)

    s.path("M 780 382 L 780 430 L 630 430 L 630 382", stroke="#546e7a", sw=1.5, marker="ah")
    s.t(705, 424, "不可恢复", size=10, anchor="middle", fill="#c62828")
    s.path("M 780 382 L 780 452 L 60 452 L 60 112 L 375 112", stroke="#546e7a", sw=1.5,
           marker="ah")
    s.t(240, 446, "可恢复（有快照）→ 重新入队", size=10, anchor="start", fill="#2e7d32")

    note(s, 900, 96, 320, "running", "心跳协程 _monitor 持续 RENEW；心跳竞争以 as_completed(FIRST_COMPLETED) 判定，失败方收到 session_execution_ownership_lost")
    note(s, 900, 250, 320, "queued", "UNIQUE(session_id, idempotency_key) 保证幂等，重复提交不会产生第二次执行")
    note(s, 900, 372, 320, "cancelled", "中断请求在准入前到达即取消；执行中到达则转 interrupted，二者语义不同", fill=C["orange"], stroke=C["orangeS"])
    s.h = 500
    s.save(os.path.join(OUT, "08-task-state.svg"))
    print("08-task-state.svg", s.w, s.h)


if __name__ == "__main__":
    d05(); d06(); d07(); d08()
