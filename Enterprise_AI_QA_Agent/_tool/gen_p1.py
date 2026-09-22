"""生成毕设文档的 SVG 图表（第一批：用例图 / 五层架构 / 部署架构）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C, wrap, text_width

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")
os.makedirs(OUT, exist_ok=True)


def mh(w, title, body, tsize=12.5, bsize=11, pad=8):
    lt = wrap(title, w - 2 * pad, tsize) if title else []
    lb = wrap(body, w - 2 * pad, bsize) if body else []
    return pad * 2 + len(lt) * tsize * 1.42 + (5 if lb else 0) + len(lb) * bsize * 1.42


# ---------------------------------------------------------------- 图 3-1 用例图
def d01():
    s = Svg(1010, 436)
    s.t(24, 30, "图 3-1  核心用例图（Use Case Diagram）", 15, weight="700", fill="#263238")
    s.t(24, 48, "四类参与者 · 13 个用例 · 3 处 include / 2 处 extend", 11, fill="#78909c")

    cols = [300, 522, 744]
    rows = [78, 146, 214, 282, 350]
    BW, BH = 202, 44

    uc = {
        1: ("创建会话并指定模式", 0, 0), 2: ("自然语言下发测试意图", 1, 0), 3: ("审批危险操作", 2, 0),
        4: ("中断 / 恢复长任务", 0, 1), 5: ("浏览用例版本链", 1, 1), 6: ("查看结构化验证报告", 2, 1),
        7: ("注册自定义工具", 0, 2), 8: ("上传技能包", 1, 2), 9: ("录制 UI 操作生成用例", 2, 2),
        10: ("探索页面建立图谱", 0, 3), 11: ("冻结套件并执行运行", 1, 3),
        12: ("监控资源配额与占用", 2, 3), 13: ("查看执行时间线与回放", 0, 4),
    }
    pos = {}
    for k, (label, c, r) in uc.items():
        x, y = cols[c], rows[r]
        pos[k] = (x, y, x + BW, y + BH)
        s.ellipse(x + BW / 2, y + BH / 2, BW / 2, BH / 2,
                  fill=C["blue"], stroke=C["blueS"])
        s.t(x + BW / 2, y + BH / 2 + 4, label, size=11.5, anchor="middle",
            fill="#1f2933", weight="600")

    actors = [("TE", "测试工程师", 178, [1, 2, 9, 10, 11]),
              ("TL", "测试负责人", 224, [3, 5, 6, 13]),
              ("TD", "测试开发工程师", 270, [7, 8]),
              ("OPS", "运维 / 安全人员", 318, [12])]
    for _k, name, ay, ucs in actors:
        s.stadium(96, ay, 116, 30, fill=C["indigo"], stroke=C["indigoS"])
        s.t(96, ay + 4, name, size=11.5, anchor="middle", fill="#283593", weight="600")
        for u in ucs:
            x1, y1 = 155, ay
            x2 = pos[u][0]
            y2 = pos[u][1] + BH / 2
            s.path("M %g %g L %g %g" % (x1, y1, x2, y2), stroke="#90a4ae", sw=1.1)

    rel = [(2, 3, "«include»", 0), (2, 4, "«include»", 1), (11, 6, "«include»", 2),
           (9, 5, "«extend»", 3), (10, 9, "«extend»", 4)]
    for a, b, label, kind in rel:
        ax1, ay1, ax2, ay2 = pos[a]
        bx1, by1, bx2, by2 = pos[b]
        ay, by = ay1 + BH / 2, by1 + BH / 2
        if abs(ay - by) < 4:
            x1, x2 = (ax2, bx1) if bx1 > ax2 else (ax1, bx2)
            s.arrow(x1, ay, x2, ay, dash="6 3", sw=1.3)
            s.t((x1 + x2) / 2, ay - 6, label, size=9.5, anchor="middle", fill="#6d4c41")
        elif abs(ax1 - bx1) < 4:
            y1, y2 = (ay2, by1) if by1 > ay2 else (ay1, by2)
            s.arrow(ax1 + BW / 2, y1, bx1 + BW / 2, y2, dash="6 3", sw=1.3)
            s.t(ax1 + BW / 2 + 6, (y1 + y2) / 2 + 4, label, size=9.5, anchor="start",
                fill="#6d4c41")
        else:
            # UC11 -> UC6 : 斜向；UC9 -> UC5：斜向；UC10 -> UC9：横跨
            x1, y1, x2, y2 = ax2, ay, bx1, by
            if bx1 < ax2:
                x1, x2, y1, y2 = ax1, bx2, ay, by
            s.arrow(x1, y1, x2, y2, dash="6 3", sw=1.3)
            s.t((x1 + x2) / 2, (y1 + y2) / 2 - 6, label, size=9.5, anchor="middle",
                fill="#6d4c41")

    s.legend(300, 408, [("参与者", "st"), ("用例", "box"), ("关联", "dash"),
                         ("«include»/«extend»", "dash")])
    s.save(os.path.join(OUT, "01-usecase.svg"))
    print("01-usecase.svg")


# ---------------------------------------------------------------- 图 5-1 五层架构
def d02():
    W = 1300
    s = Svg(W, 0)
    s.t(24, 30, "图 5-1  系统五层总体架构（核心架构图）", 15, weight="700", fill="#263238")
    s.t(24, 48, "① 接入层 · ② API 网关层 · ③ 应用服务层 · ④ 智能体内核层 · ⑤ 数据与基础设施层 · ⑥ 独立工作进程",
        11, fill="#78909c")

    LX, LW = 20, 1260
    IX = LX + 16
    IW = LW - 32
    y = 66
    bounds = []

    def layer(label, items, fills, gap=16, tsize=12.5, bsize=11):
        nonlocal y
        n = len(items)
        bw = (IW - (n - 1) * gap) / n
        h = max(52, max(mh(bw, t, b, tsize, bsize) for t, b in items))
        sy = y + 36
        s.layer(LX, y, LW, h + 54, label)
        for i, (t, b) in enumerate(items):
            s.node(IX + i * (bw + gap), sy, bw, h, t, b, fill=fills[i][0],
                   stroke=fills[i][1], tsize=tsize, bsize=bsize)
        bounds.append((y, y + h + 54, sy + h))
        y = y + h + 54 + 26
        return sy + h

    b1 = layer("① 接入层 · Presentation Layer", [
        ("agent_web（Vue 3 + Vite + Naive UI）",
         "工作台 / 设置 / 项目 / 工具 / 流程 / 记录仪 / 知识库 / 报告"),
        ("Electron 31 桌面端", "御策天检.exe，内嵌浏览器的本地一键启动"),
        ("VitePress 文档站", "架构图与文档同源维护"),
        ("外部集成", "OAuth / 邮件 / Docker / 第三方"),
    ], [(C["blue"], C["blueS"])] * 4)

    b2 = layer("② API 网关层 · API Gateway Layer", [
        ("FastAPI App（Agent_Server/src/main.py）", "端口 1032 · 统一前缀 /api/v1"),
        ("23 个路由文件 / 207 个端点",
         "sessions · run_management · registry · settings · recordings · projects · case_management · suite_management · docker …"),
        ("lifespan 生命周期", "启动期构建 99 个单例并挂载 app.state"),
    ], [(C["green"], C["greenS"]), (C["green"], C["greenS"]), (C["green"], C["greenS"])], gap=18)

    b3 = layer("③ 应用服务层 · Application Layer", [
        ("SessionService", "2,627 行\n消息 / 中断 / 恢复 / 回放 / 审批"),
        ("RuntimeService", "2,124 行\nturn 执行 / 审批续跑 / 快照"),
        ("AgentLoop", "外层多轮循环\nmax_iterations = 8"),
        ("AgentTaskDispatcher", "持久任务调度\n202 + Outbox"),
        ("RunStore / CaseService / SuiteService", "用例驱动流水线"),
        ("RecordingService / ExplorerService", "录制回放 / 图谱探索"),
        ("FlowProjectionService", "流程投影"),
    ], [(C["orange"], C["orangeS"])] * 7, gap=14)

    b4 = layer("④ 智能体内核层 · Agent Kernel Layer", [
        ("LangGraph 状态机（9 节点）",
         "context_builder → router → planner → permission_gate → prompt_assembler → model_invoker → (tool_executor | finalizer) → responder"),
        ("六大注册中心",
         "AgentRegistry(45) · ToolRegistry(57)\nModeRegistry(14) · SkillRegistry(54)\nModelRegistry(MySQL) · MCPRegistry(4)"),
        ("模型适配层", "provider_registry\n+ middleware_registry"),
        ("工具执行器", "浏览器 / CLI / HTTP\n容器 / 文件 / 报告"),
    ], [(C["purple"], C["purpleS"])] * 4)

    def db(cx, cy, w, h, t, b):
        s.cylinder(cx, cy, w, h, fill=C["blue"], stroke=C["blueS"], sw=1.4)
        s.t(cx, cy - 2, t, size=11.5, anchor="middle", fill="#1f2933", weight="600")
        s.wrapped(cx, cy + 12, b, w - 16, size=10, lh=13, anchor="middle", fill="#546e7a")

    n = 7
    gap = 14
    bw = (IW - (n - 1) * gap) / n
    h = 92
    sy = y + 36
    s.layer(LX, y, LW, h + 54, "⑤ 数据与基础设施层 · Data & Infrastructure Layer")
    dbs = [("PostgreSQL", "会话/事件/快照\n审批/任务/Outbox\n运行项/租约"),
           ("MySQL", "llm_model_config\n模型配置"),
           ("Redis", "Stream 队列\nLua 租约脚本\n热记忆缓存"),
           ("Memgraph", "图谱知识库"),
           ("RustFS / S3", "证据与产物"),
           ("Docker 引擎", "性能测试容器\n被测服务容器"),
           ("Playwright 浏览器", "浏览器实例池")]
    for i, (t, b) in enumerate(dbs):
        db(IX + i * (bw + gap) + bw / 2, sy + h / 2, bw - 6, h - 4, t, b)
    b5 = sy + h
    y = y + h + 54 + 26
    bounds.append((y - (h + 54 + 26), y - 26, b5))

    b6 = layer("⑥ 独立工作进程 · Worker Processes", [
        ("Outbox Relay", "轮询 outbox\n→ XADD Stream"),
        ("Agent Worker", "消费 qa:tasks:agent\n独立执行 turn"),
        ("Compaction Worker", "上下文压缩"),
        ("Embedding Worker", "用例向量化"),
        ("Vector Rebuild Worker", "向量重建"),
        ("Resource Cleanup Worker", "孤儿资源回收\n配额对账"),
    ], [(C["grey"], C["greyS"])] * 6)

    for i in range(len(bounds) - 1):
        bottom = bounds[i][2]
        top = bounds[i + 1][0]
        for x in (300, 650, 1000):
            s.arrow(x, bottom, x, top, stroke="#546e7a", sw=1.5)

    s.t(W - 24, y - 8, "跨层依赖：L3/L4/L6 均可直接访问 L5；L6 经 Redis Stream 与 L3/L4 解耦",
        size=10.5, anchor="end", fill="#90a4ae", italic=True)
    s.h = y + 24
    s.save(os.path.join(OUT, "02-layers.svg"))
    print("02-layers.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 5-2 部署架构
def d03():
    W = 1240
    s = Svg(W, 0)
    s.t(24, 30, "图 5-2  部署架构图", 15, weight="700", fill="#263238")

    y = 50
    s.layer(20, y, W - 40, 118, "客户端 Client")
    for i, (t, b) in enumerate([("浏览器", "localhost:5175（dev）· 生产构建为静态资源"),
                                ("桌面端 Electron", "御策天检.exe，内嵌浏览器外壳")]):
        s.node(60 + i * 560, y + 44, 480, 56, t, b, fill=C["blue"], stroke=C["blueS"])
    y += 118 + 20

    s.layer(20, y, W - 40, 100, "边缘 / 网关 Edge")
    s.diamond(W / 2, y + 52, 460, 54, fill=C["yellow"], stroke=C["yellowS"])
    s.t(W / 2, y + 42, "Nginx（可选）", size=12.5, anchor="middle", weight="600", fill="#5d4037")
    s.t(W / 2, y + 58, "/api 反向代理 · X-Accel-Buffering: no（关闭 SSE 缓冲）",
        size=11, anchor="middle", fill="#5d4037")
    s.t(W / 2, y + 74, "缺省时前端通过 Vite 代理直连 127.0.0.1:1032", size=10.5,
        anchor="middle", fill="#8d6e63")
    y += 100 + 20

    s.layer(20, y, W - 40, 92, "应用服务（单实例）App Tier")
    s.node(300, y + 36, 640, 50, "uvicorn src.main:app --port 1032",
           "FastAPI 应用进程 · 挂载 99 个单例 · /api/v1", fill=C["green"], stroke=C["greenS"])
    y += 92 + 20

    s.layer(20, y, W - 40, 128, "独立工作进程 Worker Tier")
    names = [("outbox-relay", "Outbox → Redis Stream"), ("agent-worker", "消费并执行 turn"),
             ("compaction-worker", "上下文压缩"), ("embedding-worker", "用例向量化"),
             ("vector-rebuild-worker", "向量重建"), ("resource-cleanup-worker", "孤儿资源回收")]
    bw = (W - 80 - 6 * 14 - 5 * 12) / 6
    for i, (t, b) in enumerate(names):
        s.node(60 + i * (bw + 12), y + 40, bw, 70, t, b, fill=C["grey"], stroke=C["greyS"],
               tsize=11.5, bsize=10)
    y += 128 + 20

    s.layer(20, y, W - 40, 112, "数据服务 Data Tier")
    dbs = [("PostgreSQL", "主库 / 权威数据源"), ("MySQL", "模型配置库"),
           ("Redis", "队列 + 租约 + 缓存"), ("Memgraph", "图谱库"),
           ("RustFS / MinIO", "对象存储")]
    bw = (W - 80 - 5 * 14 - 4 * 16) / 5
    for i, (t, b) in enumerate(dbs):
        cx = 60 + i * (bw + 16) + bw / 2
        s.cylinder(cx, y + 62, bw, 74, fill=C["blue"], stroke=C["blueS"])
        s.t(cx, y + 56, t, size=11.5, anchor="middle", weight="600")
        s.wrapped(cx, y + 72, b, bw - 14, size=10, lh=13, anchor="middle", fill="#546e7a")
    y += 112 + 20

    s.layer(20, y, W - 40, 112, "执行运行时 Execution Runtime")
    rts = [("Docker", "性能测试容器 / 被测服务容器"),
           ("Playwright", "浏览器实例"),
           ("MCP Server 子进程", "browser / filesystem / knowledge / tracker")]
    bw = (W - 80 - 3 * 14 - 2 * 16) / 3
    for i, (t, b) in enumerate(rts):
        s.node(60 + i * (bw + 16), y + 38, bw, 62, t, b, fill=C["orange"], stroke=C["orangeS"])
    y += 112 + 20

    ax = 620
    for (a, b, xs, dash) in [(168, 188, [ax], None), (288, 308, [ax], None),
                             (400, 420, [260, ax, 980], None),
                             (548, 568, [260, ax, 980], None),
                             (680, 700, [ax], "6 3")]:
        for x in xs:
            s.arrow(x, a, x, b, stroke="#455a64", dash=dash)
    s.t(ax + 12, 694, "虚线 = 运行时按需拉起，非请求路径常驻", size=10.5, fill="#8d6e63")

    s.h = y + 16
    s.save(os.path.join(OUT, "03-deployment.svg"))
    print("03-deployment.svg", s.w, s.h)


if __name__ == "__main__":
    d01(); d02(); d03()
