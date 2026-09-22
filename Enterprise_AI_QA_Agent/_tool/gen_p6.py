"""生成图 7-2A / 7-2B 两张 ER 图（用例运行域 / 会话任务资源域）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C, wrap, text_width

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")
os.makedirs(OUT, exist_ok=True)

ST = "#78909c"


class ER:
    def __init__(self, s):
        self.s = s
        self.E = {}

    def group(self, x, y, w, h, label):
        self.s.layer(x, y, w, h, label)

    def ent(self, name, cx, cy, w=250, h=34, col=None):
        col = col or (C["blue"], C["blueS"])
        s = self.s
        s.node(cx - w / 2, cy - h / 2, w, h, name, None, fill=col[0], stroke=col[1],
               tsize=11)
        self.E[name] = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def anc(self, name, side):
        x0, y0, x1, y1 = self.E[name]
        return {"L": (x0, (y0 + y1) / 2), "R": (x1, (y0 + y1) / 2),
                "T": ((x0 + x1) / 2, y0), "B": ((x0 + x1) / 2, y1)}[side]

    def chip(self, x, y, text, size=9.5):
        s = self.s
        w = text_width(text, size) + 10
        s.rect(x - w / 2, y - 9, w, 16, fill="#ffffff", stroke="#d7dee3", rx=3, sw=0.8)
        s.t(x, y + 3.5, text, size=size, anchor="middle", fill="#6d4c41")

    def crow(self, x, y, orient, flip):
        s = self.s
        if orient == "h":
            for k in (-5, 0, 5):
                s.path("M %g %g L %g %g" % (x, y + k, x + 8 * flip, y + k * 1.6),
                       stroke=ST, sw=1.1)
        else:
            for k in (-5, 0, 5):
                s.path("M %g %g L %g %g" % (x + k, y, x + k * 1.6, y + 8 * flip),
                       stroke=ST, sw=1.1)

    def one(self, x, y, orient):
        s = self.s
        if orient == "h":
            s.path("M %g %g L %g %g" % (x, y - 6, x, y + 6), stroke=ST, sw=1.1)
        else:
            s.path("M %g %g L %g %g" % (x - 6, y, x + 6, y), stroke=ST, sw=1.1)

    def rel(self, a, sa, b, sb, label, lm=(0, 0)):
        p1, p2 = self.anc(a, sa), self.anc(b, sb)
        if sa in "LR" and sb in "LR":
            mx = (p1[0] + p2[0]) / 2
            self.s.path("M %g %g L %g %g L %g %g L %g %g" % (p1[0], p1[1], mx, p1[1], mx,
                                                            p2[1], p2[0], p2[1]),
                        stroke=ST, sw=1.1)
            self.one(p1[0] + (7 if sa == "R" else -7), p1[1], "h")
            self.crow(p2[0], p2[1], "h", 1 if sb == "L" else -1)
            self.chip(mx + lm[0], p1[1] - 15 + lm[1], label)
        elif sa in "TB" and sb in "TB":
            my = (p1[1] + p2[1]) / 2
            self.s.path("M %g %g L %g %g L %g %g L %g %g" % (p1[0], p1[1], p1[0], my,
                                                            p2[0], my, p2[0], p2[1]),
                        stroke=ST, sw=1.1)
            self.one(p1[0], p1[1] + (7 if sa == "B" else -7), "v")
            self.crow(p2[0], p2[1], "v", 1 if sb == "T" else -1)
            self.chip((p1[0] + p2[0]) / 2 + lm[0], my + lm[1], label)
        else:
            self.s.path("M %g %g L %g %g L %g %g" % (p1[0], p1[1], p2[0], p1[1], p2[0], p2[1]),
                        stroke=ST, sw=1.1)
            if sa in "LR":
                self.one(p1[0] + (7 if sa == "R" else -7), p1[1], "h")
                self.crow(p2[0], p2[1], "v", 1 if sb == "T" else -1)
                self.chip(p2[0] + 40 + lm[0], p1[1] + 15 + lm[1], label)
            else:
                self.one(p1[0], p1[1] + (7 if sa == "B" else -7), "v")
                self.crow(p2[0], p2[1], "h", 1 if sb == "L" else -1)
                self.chip(p1[0] + 40 + lm[0], p2[1] + 15 + lm[1], label)

    def loop(self, a, sa, gx, b, sb, label):
        """经右侧渠沟绕行的 1:N 关系，避免穿过中间的实体框。"""
        p1, p2 = self.anc(a, sa), self.anc(b, sb)
        s = self.s
        s.path("M %g %g L %g %g L %g %g L %g %g" % (p1[0], p1[1], gx, p1[1], gx, p2[1],
                                                  p2[0], p2[1]), stroke=ST, sw=1.1)
        self.one(p1[0] + (7 if sa == "R" else -7), p1[1], "h")
        self.crow(p2[0], p2[1], "h", 1 if sb == "R" else -1)
        self.chip((gx + p2[0]) / 2 + 10, (p1[1] + p2[1]) / 2 - 12, label)


# ---------------------------------------------------------------- 图 7-2A
def d17a():
    s = Svg(1240, 0)
    s.t(24, 30, "图 7-2A  总体 ER 图（一）：项目、用例、套件与运行域", 15, weight="700",
        fill="#263238")
    s.t(24, 48, "版本不可变 + 套件冻结 pin 版本，是这套模型能支撑回归可比对的关键", 11,
        fill="#78909c")
    e = ER(s)
    BL, OR, ID = (C["blue"], C["blueS"]), (C["orange"], C["orangeS"]), (C["indigo"], C["indigoS"])
    GR, CY = (C["green"], C["greenS"]), (C["cyan"], C["cyanS"])

    e.group(20, 66, 340, 300, "项目与用例域")
    e.ent("agent_projects", 190, 118, w=260, col=BL)
    e.ent("agent_test_cases", 190, 186, w=260, col=OR)
    e.ent("agent_test_case_versions", 190, 254, w=260, col=OR)
    e.ent("agent_test_case_embeddings", 190, 322, w=260, col=CY)

    e.group(620, 66, 600, 360, "套件与运行域")
    e.ent("agent_test_suites", 700, 118, w=250, col=ID)
    e.ent("agent_test_suite_items", 700, 186, w=250, col=ID)
    e.ent("agent_test_runs", 700, 254, w=250, col=OR)
    e.ent("agent_test_run_items", 700, 322, w=250, col=OR)
    e.ent("agent_test_run_attempts", 700, 390, w=250, col=OR)
    e.ent("agent_test_case_results", 1050, 322, w=250, col=GR)

    e.rel("agent_projects", "B", "agent_test_cases", "T", "拥有")
    e.rel("agent_test_cases", "B", "agent_test_case_versions", "T", "版本")
    e.rel("agent_test_case_versions", "B", "agent_test_case_embeddings", "T", "向量化")
    e.rel("agent_projects", "R", "agent_test_suites", "T", "拥有套件", lm=(60, -4))
    e.rel("agent_test_suites", "B", "agent_test_suite_items", "T", "冻结引用版本")
    e.rel("agent_test_case_versions", "R", "agent_test_suite_items", "L", "被 pin", lm=(-30, -26))
    e.loop("agent_test_suites", "R", 900, "agent_test_runs", "R", "被执行")
    e.rel("agent_test_runs", "B", "agent_test_run_items", "T", "分解")
    e.rel("agent_test_run_items", "B", "agent_test_run_attempts", "T", "尝试")
    e.rel("agent_test_run_items", "R", "agent_test_case_results", "L", "产出")

    s.t(24, 455, "命名约定：agent_test_case_results.regression_source_result_id 指向被回归的原始结论，"
                  "回归链由此反查；所有 *_id 外键均建索引，运行类表按 project_id 分片预留。",
        size=10, fill="#90a4ae", italic=True)
    s.h = 480
    s.save(os.path.join(OUT, "17a-er-case.svg"))
    print("17a-er-case.svg", s.w, s.h)


# ---------------------------------------------------------------- 图 7-2B
def d17b():
    s = Svg(1240, 0)
    s.t(24, 30, "图 7-2B  总体 ER 图（二）：会话、任务与资源域", 15, weight="700", fill="#263238")
    s.t(24, 48, "事件表靠单调序号支撑 SSE 增量拉取，任务表靠 Outbox 支撑至少一次投递", 11,
        fill="#78909c")
    e = ER(s)
    GR, YL = (C["green"], C["greenS"]), (C["yellow"], C["yellowS"])
    PU, CY, RD, GY = (C["purple"], C["purpleS"]), (C["cyan"], C["cyanS"]), (C["red"], C["redS"]), \
                     (C["grey"], C["greyS"])

    e.group(20, 66, 340, 440, "会话与事件域")
    e.ent("agent_sessions", 190, 118, w=260, col=GR)
    e.ent("agent_session_messages", 190, 186, w=260, col=GR)
    e.ent("agent_session_events", 190, 254, w=260, col=GR)
    e.ent("agent_session_snapshots", 190, 322, w=260, col=GR)
    e.ent("agent_session_approvals", 190, 390, w=260, col=RD)
    e.ent("agent_session_resources", 190, 458, w=260, col=CY)

    e.group(620, 66, 340, 300, "任务域")
    e.ent("agent_turn_tasks", 790, 118, w=250, col=YL)
    e.ent("agent_turn_attempts", 790, 186, w=250, col=YL)
    e.ent("agent_task_outbox", 790, 254, w=250, col=PU)

    e.group(1020, 66, 200, 300, "产物域")
    e.ent("agent_tool_jobs", 1120, 118, w=180, col=GY)
    e.ent("agent_tool_artifacts", 1120, 186, w=180, col=GY)
    e.ent("ui_recording", 1120, 254, w=180, col=PU)
    e.ent("ui_recording_event", 1120, 322, w=180, col=PU)

    e.group(620, 420, 600, 240, "资源域")
    e.ent("agent_resource_allocations", 790, 470, w=250, col=CY)
    e.ent("agent_browser_allocations", 790, 538, w=250, col=CY)
    e.ent("agent_resource_cleanup_jobs", 790, 606, w=250, col=RD)
    e.ent("agent_projects", 1120, 470, w=180, col=(C["blue"], C["blueS"]))

    for name, label in [("agent_session_messages", "消息"), ("agent_session_events", "事件"),
                        ("agent_session_snapshots", "快照"),
                        ("agent_session_approvals", "审批"),
                        ("agent_session_resources", "会话资源")]:
        e.loop("agent_sessions", "R", 470, name, "R", label)
    e.rel("agent_sessions", "R", "agent_turn_tasks", "L", "产生任务")
    e.rel("agent_turn_tasks", "B", "agent_turn_attempts", "T", "重试")
    e.loop("agent_task_outbox", "R", 945, "agent_turn_tasks", "R", "投递")
    e.rel("agent_sessions", "R", "agent_tool_jobs", "L", "工具作业", lm=(0, -15))
    e.rel("agent_tool_jobs", "B", "agent_tool_artifacts", "T", "生成产物")
    e.rel("ui_recording", "B", "ui_recording_event", "T", "包含事件")
    e.rel("agent_projects", "L", "agent_resource_allocations", "R", "配额")
    e.rel("agent_projects", "L", "agent_browser_allocations", "R", "浏览器配额", lm=(0, 30))
    e.loop("agent_resource_allocations", "R", 945, "agent_resource_cleanup_jobs", "R", "回收")

    s.h = 690
    s.save(os.path.join(OUT, "17b-er-session.svg"))
    print("17b-er-session.svg", s.w, s.h)


if __name__ == "__main__":
    d17a(); d17b()
