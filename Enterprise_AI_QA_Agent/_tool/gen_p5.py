"""生成 6.9 用例流水线 / 6.10 录制器 / 6.11 图谱探索 / 6.12 技能与 MCP / 7.2 ER / 9.1 路由 / 10.3 质量门禁。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C, wrap, text_width

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")
os.makedirs(OUT, exist_ok=True)


def box(s, cx, cy, w, h, title, body=None, fill=C["box"], stroke=C["boxS"],
        tsize=11.5, bsize=10, tfill="#1f2933", bfill="#546e7a"):
    s.node(cx - w / 2, cy - h / 2, w, h, title, body, fill=fill, stroke=stroke,
           tsize=tsize, bsize=bsize, tfill=tfill, bfill=bfill)


def term(s, cx, cy, label, w=72, h=28):
    s.stadium(cx, cy, w, h, fill=C["grey"], stroke=C["greyS"])
    s.t(cx, cy + 4, label, size=11, anchor="middle", weight="600", fill="#37474f")


def dia(s, cx, cy, w, h, text, size=11):
    s.diamond(cx, cy, w, h, fill=C["yellow"], stroke=C["yellowS"], sw=1.4)
    lines = wrap(text, w * 0.6, size)
    for i, ln in enumerate(lines):
        s.t(cx, cy - (len(lines) - 1) * 7 + i * 14 + 4, ln, size=size, anchor="middle",
            fill="#5d4037", weight="600")


def note(s, x, y, w, title, body, fill=C["yellow"], stroke=C["yellowS"]):
    lines = wrap(body, w - 16, 10)
    h = 26 + len(lines) * 13.5
    s.rect(x, y, w, h, fill=fill, stroke=stroke, rx=5, dash="5 3")
    s.t(x + 8, y + 17, title, size=10.5, weight="700", fill="#5d4037")
    for i, ln in enumerate(lines):
        s.t(x + 8, y + 31 + i * 13.5, ln, size=10, fill="#5d4037")
    return y + h


# ---------------------------------------------------------------- 图 6-9 用例驱动流水线
def d13():
    s = Svg(1180, 0)
    s.t(24, 30, "图 6-9  用例驱动的测试流水线", 15, weight="700", fill="#263238")
    s.t(24, 48, "从草稿到回归全程有版本锚点，任何一次运行都能复现“用了哪一版用例”", 11, fill="#78909c")

    CX, W = 260, 300
    chain = [("项目 Project", "租户边界，承载全部资源", 84, C["blue"]),
             ("用例草稿 Case Draft", "可编辑，未生效", 146, C["grey"]),
             ("评审 Review", "人工确认后可激活", 208, C["grey"]),
             ("激活", "固定 content_hash(sha256)", 270, C["orange"]),
             ("Case Version 不可变", "内容指纹即版本身份", 332, C["orange"]),
             ("套件冻结 Suite Freeze", "pin 住 case_version_id", 394, C["indigo"]),
             ("测试运行 Run", "一次冻结套件的执行", 456, C["blue"]),
             ("Run Items", "运行分解出的执行项", 518, C["blue"])]
    for label, body, y, col in chain:
        box(s, CX, y, W, 40, label, body, fill=col, stroke="#90a4ae", tsize=11.5, bsize=9.5)
    for i in range(len(chain) - 1):
        s.arrow(CX, chain[i][2] + 20, CX, chain[i + 1][2] - 20, stroke="#546e7a", sw=1.5)

    dia(s, CX, 594, 300, 62, "认领：FOR UPDATE SKIP LOCKED")
    s.arrow(CX, 538, CX, 563, stroke="#546e7a", sw=1.5)

    ys = 664
    for cx, label in [(110, "Worker 1"), (260, "Worker 2"), (410, "Worker N")]:
        box(s, cx, ys, 130, 38, label, None, fill=C["green"], stroke=C["greenS"])
        s.arrow(cx, 625, cx, 645, stroke="#546e7a", sw=1.5)
    s.t(420, 640, "并发认领，行锁保证不重复执行", size=10, anchor="start", fill="#6d4c41")

    box(s, 260, 726, 300, 40, "结构化验证 Verification", "按断言集判定，不靠大模型拍脑袋",
        fill=C["purple"], stroke=C["purpleS"])
    for cx in (110, 260, 410):
        s.arrow(cx, 683, cx, 706, stroke="#546e7a", sw=1.5)

    box(s, 150, 790, 230, 40, "证据 Evidence", "截图/日志/产物 → RustFS / S3", fill=C["blue"],
        stroke=C["blueS"])
    box(s, 400, 790, 230, 40, "判定 Result", "pass / fail / blocked", fill=C["green"],
        stroke=C["greenS"])
    s.path("M %g %g L %g %g L %g %g" % (190, 746, 190, 770, 150, 770), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.path("M %g %g L %g %g L %g %g" % (330, 746, 330, 770, 400, 770), stroke="#546e7a",
           sw=1.5, marker="ah")

    box(s, 260, 860, 340, 40, "回归运行 Regression Run", "regression_source_result_id 反查来源",
        fill=C["indigo"], stroke=C["indigoS"])
    s.arrow(400, 810, 400, 840, stroke="#546e7a", sw=1.5)

    box(s, 800, 518, 300, 52, "Run Item Attempts", "UNIQUE(run_id, run_item_id, attempt_no)",
        fill=C["orange"], stroke=C["orangeS"])
    s.path("M %g %g L %g %g L %g %g L %g %g" % (410, 518, 560, 518, 560, 545, 650, 545), stroke="#546e7a", sw=1.5, marker="ah")
    s.t(560, 512, "每次重试留痕", size=10, anchor="middle", fill="#6d4c41")

    note(s, 580, 90, 580, "为什么必须是不可变版本",
         "用例内容变化必须生成新版本而不是覆盖旧版本：套件冻结 pin 住 case_version_id 后，"
         "历史运行的回放、回归对比、责任追溯才不会失真。content_hash(sha256) 同时用作幂等与去重依据。")
    note(s, 580, 240, 580, "为什么用 FOR UPDATE SKIP LOCKED",
         "多个 Worker 同时抢同一条执行项时，SKIP LOCKED 让抢不到的线程立刻去看下一条，"
         "既不阻塞也不重复执行；相比先查后写，彻底消除竞态窗口。", fill=C["orange"], stroke=C["orangeS"])
    note(s, 580, 380, 580, "证据与判定分离",
         "证据落对象存储、判定落关系库，二者通过 result_id 关联。这样报告可以无限重组，"
         "而不会因为删报告而丢失原始取证材料。", fill=C["blue"], stroke=C["blueS"])
    s.h = 900
    s.save(os.path.join(OUT, "13-pipeline.svg"))
    print("13-pipeline.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-10 录制器
def d14():
    s = Svg(1300, 0)
    s.t(24, 30, "图 6-10  浏览器操作录制与用例生成链路", 15, weight="700", fill="#263238")
    s.t(24, 48, "三种驱动共享同一套 recorder.js，录制产物统一落库并进入图谱，再反哺用例草稿", 11,
        fill="#78909c")

    s.layer(20, 66, 320, 250, "三种录制驱动")
    for i, (t, b) in enumerate([
            ("嵌入式驱动", "自动打开带调试端口的浏览器"),
            ("CDP 接入", "attach 到用户已开着的浏览器"),
            ("Playwright 托管", "完整生命周期管控，最稳")]):
        box(s, 180, 116 + i * 66, 260, 52, t, b, fill=C["purple"], stroke=C["purpleS"],
            tsize=11.5, bsize=10)

    CH = [("recorder.js", "注入页面的统一录制脚本", 400),
          ("浏览器事件流", "click / input / navigate", 585),
          ("幂等写入", "ui_recording / ui_recording_event", 770),
          ("Memgraph 子图", "元素节点 + 关系", 955),
          ("7 级定位决策链", "5 级 DOM + 几何重锚 + 坐标兜底", 1140)]
    for i, (t, b, cx) in enumerate(CH):
        box(s, cx, 180, 175, 62, t, b, fill=C["green"] if i == 2 else C["blue"],
            stroke=C["greenS"] if i == 2 else C["blueS"], tsize=11.5, bsize=9.5)
        if i:
            s.arrow(CH[i - 1][2] + 88, 180, cx - 88, 180, stroke="#546e7a", sw=1.5)
    for i in range(3):
        s.arrow(340, 116 + i * 66, 312, 180, stroke="#546e7a", sw=1.5)

    box(s, 585, 300, 250, 52, "用例草稿 Case Draft", "录制结果转为可编辑步骤", fill=C["orange"],
        stroke=C["orangeS"])
    box(s, 870, 300, 250, 52, "评审 → 激活版本", "走与手工用例完全相同的流水线",
        fill=C["orange"], stroke=C["orangeS"])
    s.path("M %g %g L %g %g L %g %g" % (1140, 211, 1140, 300, 710, 300), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.arrow(710, 300, 745, 300, stroke="#546e7a", sw=1.5)

    note(s, 400, 372, 420, "7 级定位决策链",
         "依次尝试 role/name、文本、CSS、XPath、data-testid，失败后按几何位置重锚，"
         "最后才退回视口坐标。前 5 级对 DOM 结构有一定容错，最后一级为兜底。")
    note(s, 860, 372, 420, "安全红线：掩码值",
         "密码等敏感输入在录制与回放全链路只保留掩码值，真实值不落库、不进日志、不进快照，"
         "回放时从运行时注入。", fill=C["red"], stroke=C["redS"])
    s.h = 510
    s.save(os.path.join(OUT, "14-recorder.svg"))
    print("14-recorder.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-11 图谱探索
def d15():
    s = Svg(1140, 0)
    s.t(24, 30, "图 6-11  页面图谱探索与三元关系建模", 15, weight="700", fill="#263238")
    s.t(24, 48, "图谱不是用来“存页面”的，而是用来回答“点了这个元素会走到哪、属于哪个业务实体”", 11,
        fill="#78909c")

    box(s, 390, 92, 320, 44, "Playwright aria_snapshot()", "唯一事实来源", fill=C["indigo"],
        stroke=C["indigoS"])
    box(s, 150, 200, 200, 46, "Page 节点", "页面", fill=C["yellow"], stroke=C["yellowS"])
    box(s, 390, 200, 200, 46, "Element 节点", "可交互元素", fill=C["blue"], stroke=C["blueS"])
    box(s, 630, 200, 200, 46, "Entity 节点", "业务实体", fill=C["green"], stroke=C["greenS"])
    s.path("M %g %g L %g %g L %g %g" % (270, 114, 270, 150, 150, 150), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.arrow(390, 114, 390, 177, stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g L %g %g" % (510, 114, 510, 150, 630, 150), stroke="#546e7a",
           sw=1.5, marker="ah")

    s.arrow(250, 200, 290, 200, stroke="#546e7a", sw=1.5)
    s.t(270, 192, "CONTAINS", size=10, anchor="middle", fill="#6d4c41")

    s.arrow(390, 223, 390, 277, stroke="#546e7a", sw=1.5)
    box(s, 390, 300, 200, 46, "Element 节点", "被揭示的后续元素", fill=C["blue"],
        stroke=C["blueS"])
    s.t(400, 250, "REVEALS", size=10, anchor="start", fill="#6d4c41")

    s.path("M %g %g L %g %g L %g %g" % (390, 323, 390, 380, 250, 380), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.t(320, 372, "TRIGGERS_NAVIGATION", size=10, anchor="middle", fill="#6d4c41")
    box(s, 150, 380, 200, 46, "Page 节点", "导航后的目标页面", fill=C["yellow"],
        stroke=C["yellowS"])

    s.path("M %g %g L %g %g L %g %g" % (530, 223, 530, 254, 150, 254), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.t(340, 248, "BELONGS_TO", size=10, anchor="middle", fill="#6d4c41")
    s.path("M %g %g L %g %g L %g %g" % (630, 223, 630, 406, 250, 406), stroke="#546e7a",
           sw=1.5, marker="ah")
    s.t(440, 400, "BELONGS_TO → Page 2", size=10, anchor="middle", fill="#6d4c41")

    s.legend(60, 470, [("Page", "box"), ("Element", "box"), ("Entity", "box")])
    note(s, 620, 300, 330, "图谱的查询价值",
         "回答“这个实体在哪几页出现”、“从 A 页能不能点到 B 页”、“改动影响哪些用例”"
         "这类问题，关系库要做多层自连接，图库一次遍历。", fill=C["indigo"], stroke=C["indigoS"])
    s.h = 540
    s.save(os.path.join(OUT, "15-explorer.svg"))
    print("15-explorer.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 6-12 技能与 MCP
def d16():
    s = Svg(1240, 0)
    s.t(24, 30, "图 6-12  技能包与 MCP：工具可见面的两级放大", 15, weight="700", fill="#263238")
    s.t(24, 48, "deferred → available → visible → allowed：每一层都在缩小模型能碰到的工具集", 11,
        fill="#78909c")

    cols = [("deferred_tool_keys", "技能未展开前仅可见", 110, C["yellow"]),
            ("available_tool_keys", "技能激活后已挂载", 330, C["yellow"]),
            ("model_visible_tool_keys", "告诉模型有哪些工具", 550, C["blue"]),
            ("allowed_tool_keys", "实际放行可调用", 770, C["blue"]),
            ("approval_required_tool_keys", "需人工审批才可执行", 990, C["orange"])]
    for label, body, cx, col in cols:
        box(s, cx, 150, 195, 56, label, body, fill=col, stroke="#90a4ae", tsize=11, bsize=9.5)
    for i in range(4):
        s.arrow(cols[i][2] + 98, 150, cols[i + 1][2] - 98, 150, stroke="#546e7a", sw=1.5)
    s.t(220, 138, "技能激活展开", size=10, anchor="middle", fill="#2e7d32")

    box(s, 770, 262, 195, 46, "denied_tool_keys", "显式拒命", fill=C["red"], stroke=C["redS"])
    s.path("M %g %g L %g %g" % (770, 178, 770, 239), stroke="#546e7a", sw=1.5, marker="ah")
    s.t(780, 220, "最小权限兜底", size=10, anchor="start", fill="#c62828")

    box(s, 150, 380, 260, 56, "MCP Server", "browser / filesystem / knowledge / tracker",
        fill=C["purple"], stroke=C["purpleS"])
    box(s, 470, 380, 260, 56, "ToolRegistry", "统一注册中心（57 静态 + 动态）", fill=C["green"],
        stroke=C["greenS"])
    s.arrow(280, 380, 340, 380, stroke="#546e7a", sw=1.5)
    s.t(310, 370, "mcp__<server>__<tool>", size=9.5, anchor="middle", fill="#6d4c41")
    box(s, 790, 380, 260, 56, "prompt 组装", "只注入 visible 子集", fill=C["blue"],
        stroke=C["blueS"])
    s.path("M %g %g L %g %g L %g %g L %g %g" % (600, 380, 700, 380, 700, 408, 660, 408), stroke="#546e7a", sw=1.5, marker="ah")
    s.t(700, 370, "注册后进入可用集", size=9.5, anchor="middle", fill="#6d4c41")

    note(s, 60, 470, 360, "为什么要五层而不是一层",
         "把“能用的工具”和“模型看见的工具”分开，才能在给模型更多上下文的同时，"
         "不放大它的行动面。denied 是最后一 道闸。")
    note(s, 460, 470, 360, "命名空间隔离",
         "MCP 工具统一以 mcp__<server>__<tool> 命名，天然避免与本地工具重名，"
         "也便于按 server 维度审计与吊销。", fill=C["purple"], stroke=C["purpleS"])
    note(s, 860, 470, 360, "技能懒加载",
         "技能包里声明的工具在技能未激活前不进入 available，模型连名字都看不到，"
         "prompt 体积与注意力都不会被稀释。", fill=C["green"], stroke=C["greenS"])
    s.h = 610
    s.save(os.path.join(OUT, "16-skill-mcp.svg"))
    print("16-skill-mcp.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 7-2 总体 ER
def d17():
    s = Svg(1300, 660)
    s.t(24, 30, "图 7-2  总体 ER 图（领域级概览）", 15, weight="700", fill="#263238")
    s.t(24, 48, "24 个关系型核心表按业务边界分组；字段级关系拆分到图 7-2A 与图 7-2B",
        11, fill="#78909c")

    def domain(x, y, w, h, title, tables, fill, stroke):
        s.layer(x, y, w, h, title)
        row_h = 28
        gap = 7
        for i, table in enumerate(tables):
            ry = y + 38 + i * (row_h + gap)
            s.rect(x + 18, ry, w - 36, row_h, fill=fill, stroke=stroke, rx=5, sw=1.1)
            s.t(x + w / 2, ry + 18, table, size=10.2, anchor="middle", fill="#263238")

    domain(30, 76, 350, 216, "项目与用例域",
           ["agent_projects", "agent_test_cases", "agent_test_case_versions",
            "agent_test_case_embeddings"], C["orange"], C["orangeS"])
    domain(475, 76, 350, 286, "套件与运行域",
           ["agent_test_suites", "agent_test_suite_items", "agent_test_runs",
            "agent_test_run_items", "agent_test_run_attempts", "agent_test_case_results"],
           C["blue"], C["blueS"])
    domain(30, 372, 350, 251, "会话与事件域",
           ["agent_sessions", "agent_session_messages", "agent_session_events",
            "agent_session_snapshots", "agent_session_approvals", "agent_session_resources"],
           C["green"], C["greenS"])
    domain(475, 422, 350, 181, "持久任务域",
           ["agent_turn_tasks", "agent_turn_attempts", "agent_task_outbox"],
           C["yellow"], C["yellowS"])
    domain(920, 76, 350, 321, "产物与资源域",
           ["agent_tool_jobs", "agent_tool_artifacts", "ui_recording", "ui_recording_event",
            "agent_resource_allocations", "agent_browser_allocations",
            "agent_resource_cleanup_jobs"], C["cyan"], C["cyanS"])

    s.arrow(380, 180, 475, 180, stroke="#546e7a", sw=1.5,
            label="case_version → suite_item", lsize=9.5)
    s.arrow(205, 292, 205, 372, stroke="#546e7a", sw=1.5,
            label="project_id", lsize=9.5, loff=(46, -4))
    s.arrow(380, 492, 475, 492, stroke="#546e7a", sw=1.5,
            label="session → turn_task", lsize=9.5)
    s.arrow(650, 362, 650, 422, stroke="#546e7a", sw=1.5,
            label="运行调度", lsize=9.5, loff=(36, -3))
    s.arrow(825, 236, 920, 236, stroke="#546e7a", sw=1.5,
            label="结果 / 证据", lsize=9.5)
    s.path("M 380 552 L 870 552 L 870 316 L 920 316", stroke="#546e7a", sw=1.5,
           dash="6 3", marker="ah")
    s.t(870, 543, "工具产物 / 资源租约", size=9.5, anchor="end", fill="#6d4c41")

    s.t(920, 430, "图示口径", size=11, weight="700", fill="#37474f")
    s.wrapped(920, 450,
              "本图用于论文总体设计章节，表达领域边界与主关联；基数、外键和回归链请引用后续两张分域 ER 图。",
              335, size=10.5, lh=16, fill="#546e7a")
    s.save(os.path.join(OUT, "17-er.svg"))
    print("17-er.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 9-1 前端路由
def d18():
    s = Svg(1000, 0)
    s.t(24, 30, "图 9-1  前端路由与视图结构", 15, weight="700", fill="#263238")
    s.t(24, 48, "两套外壳：带侧边导航的 DefaultShell 与全屏 BlankShell，共 12 条路由记录", 11,
        fill="#78909c")

    box(s, 530, 82, 300, 44, "App.vue", "应用根组件", fill=C["grey"], stroke=C["greyS"])
    box(s, 530, 154, 380, 44, "vue-router", "12 条路由记录", fill=C["indigo"],
        stroke=C["indigoS"])
    s.arrow(530, 104, 530, 132, stroke="#546e7a", sw=1.5)

    box(s, 300, 240, 240, 44, "DefaultShell", "带侧边导航", fill=C["blue"], stroke=C["blueS"])
    box(s, 760, 240, 240, 44, "BlankShell", "meta.blankShell: true", fill=C["orange"],
        stroke=C["orangeS"])
    s.arrow(430, 176, 430, 218, stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g L %g %g" % (630, 176, 630, 200, 760, 200), stroke="#546e7a",
           sw=1.5, marker="ah")

    s.path("M %g %g L %g %g L %g %g" % (300, 262, 300, 286, 70, 286), stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g" % (70, 286, 70, 760), stroke="#546e7a", sw=1.5)
    shell_routes = [("/home", "WorkbenchView 会话工作台"),
                    ("/taskpool", "TaskPoolView 任务池"),
                    ("/projects", "ProjectsView 项目列表"),
                    ("/projects/:projectId", "ProjectsView 项目详情"),
                    ("/knowledge", "KnowledgeView 知识库"),
                    ("/tools", "ToolsView 工具管理"),
                    ("/reports", "ReportsView 报告"),
                    ("/settings", "SettingsView 设置")]
    for i, (path, view) in enumerate(shell_routes):
        y = 310 + i * 56
        box(s, 300, y, 440, 42, path, view, fill=C["blue"], stroke=C["blueS"], tsize=11,
            bsize=10)
        s.arrow(70, y, 78, y, stroke="#546e7a", sw=1.5)

    s.path("M %g %g L %g %g L %g %g" % (760, 262, 760, 286, 600, 286), stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g" % (600, 286, 600, 410), stroke="#546e7a", sw=1.5)
    for i, (path, view) in enumerate([("/flow", "FlowView 编排链路 @vue-flow/core"),
                                      ("/recorder-window", "RecorderWindowView 录制窗口")]):
        y = 310 + i * 56
        box(s, 760, y, 400, 42, path, view, fill=C["orange"], stroke=C["orangeS"], tsize=11,
            bsize=10)
        s.arrow(600, y, 558, y, stroke="#546e7a", sw=1.5)

    note(s, 620, 450, 360, "历史链接兼容",
         "/ 与 /dashboard 都重定向到 /home，避免旧书签与外部引用失效；"
         "路由元信息同时驱动菜单高亮与页面标题。", fill=C["grey"], stroke=C["greyS"])
    s.h = 800
    s.save(os.path.join(OUT, "18-routes.svg"))
    print("18-routes.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 10-3 质量门禁
def d19():
    s = Svg(1180, 0)
    s.t(24, 30, "图 10-3  质量门禁与真实中间件环境开关", 15, weight="700", fill="#263238")
    s.t(24, 48, "门禁不过则阻塞合并；需要真实中间件的行为靠环境变量显式开启，不默认跑", 11,
        fill="#78909c")

    box(s, 90, 130, 130, 46, "代码提交", "push / PR", fill=C["grey"], stroke=C["greyS"])
    box(s, 320, 130, 220, 62, "pytest 后端套件", "1039 passed / 221 skipped", fill=C["blue"],
        stroke=C["blueS"])
    box(s, 560, 130, 200, 62, "vitest 前端套件", "45 passed", fill=C["blue"], stroke=C["blueS"])
    dia(s, 790, 130, 200, 66, "全部通过 ?")
    box(s, 1040, 74, 240, 46, "阻塞合并", "回到根因修复，不打补丁绕过", fill=C["red"],
        stroke=C["redS"])
    box(s, 1040, 190, 240, 46, "允许合并", "绿灯即放行", fill=C["green"], stroke=C["greenS"])
    s.arrow(155, 130, 210, 130, stroke="#546e7a", sw=1.5)
    s.arrow(430, 130, 460, 130, stroke="#546e7a", sw=1.5)
    s.arrow(660, 130, 690, 130, stroke="#546e7a", sw=1.5)
    s.arrow(890, 122, 1040, 84, stroke="#546e7a", sw=1.5, label="否", lfill="#c62828")
    s.arrow(890, 140, 1040, 186, stroke="#546e7a", sw=1.5, label="是", lfill="#2e7d32")

    box(s, 90, 330, 130, 46, "行为验证", "触及真实中间件", fill=C["grey"], stroke=C["greyS"])
    dia(s, 300, 330, 200, 66, "环境门禁 RUN_LIVE_*")
    s.arrow(155, 330, 200, 330, stroke="#546e7a", sw=1.5)
    s.path("M %g %g L %g %g L %g %g" % (300, 363, 300, 386, 430, 386), stroke="#546e7a",
           sw=1.5)
    s.path("M %g %g L %g %g" % (430, 386, 430, 430), stroke="#546e7a", sw=1.5)

    envs = [("RUN_LIVE_POSTGRES_TESTS", "真 PostgreSQL", 480),
            ("RUN_LIVE_REDIS_TESTS", "真 Redis", 690),
            ("RUN_LIVE_DOCKER_TESTS", "真 Docker", 900),
            ("RUN_LIVE_BROWSER_TESTS", "真浏览器", 1080)]
    for label, desc, cx in envs:
        box(s, cx, 450, 190, 62, label, desc, fill=C["cyan"], stroke=C["cyanS"], tsize=10,
            bsize=10)
        s.arrow(cx, 386, cx, 419, stroke="#546e7a", sw=1.5)

    note(s, 480, 530, 620, "为什么要开关而不是全跑",
         "真实中间件测试依赖 Docker 与网络，在 CI 未就绪时会大量假失败；"
         "默认只跑不依赖外部服务的部分，需要时显式开启，避免把环境问题当成代码问题。")
    s.h = 660
    s.save(os.path.join(OUT, "19-gate.svg"))
    print("19-gate.svg", s.w, s.h)


if __name__ == "__main__":
    d13(); d14(); d15(); d16(); d17(); d18(); d19()
