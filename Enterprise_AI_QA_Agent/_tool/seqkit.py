"""时序图（Sequence Diagram）绘制助手。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Svg, C, wrap, text_width


class Seq:
    def __init__(self, s, parts, x0=24, top=84, colw=170, headh=50,
                 fill="#eceff1", stroke="#90a4ae"):
        self.s = s
        self.colw = float(colw)
        self.x0 = x0
        self.top = top
        self.headh = headh
        self.px = {}
        self.order = []
        for i, (k, label) in enumerate(parts):
            cx = x0 + i * self.colw + self.colw / 2
            self.px[k] = cx
            self.order.append((k, cx))
        for cx, label in [(v, dict(parts)[k]) for k, v in self.px.items()]:
            w = self.colw - 12
            s.rect(cx - w / 2, top, w, headh, fill=fill, stroke=stroke, rx=6)
            s.wrapped(cx, top + 19, label, w - 12, size=11, lh=13.5, anchor="middle",
                      fill="#37474f", weight="600")
        self.y = top + headh + 28
        self.right = x0 + len(parts) * self.colw
        self.bottom = None

    def lifelines(self, bottom):
        for _k, cx in self.order:
            self.s.path("M %g %g L %g %g" % (cx, self.top + self.headh, cx, bottom),
                        stroke="#cfd8dc", sw=1.1, dash="4 4")
        self.bottom = bottom

    def at(self, y):
        self.y = y
        return y

    def msg(self, a, b, text, kind="solid", y=None, lsize=10.5, lfill="#37474f"):
        s = self.s
        x1, x2 = self.px[a], self.px[b]
        if y is not None:
            self.y = y
        if a == b:
            lines = wrap(text, 150, lsize)
            base = self.y + 11
            for i, ln in enumerate(lines):
                s.t(x1 + 8, base + i * 13.5, ln, size=lsize, anchor="start", fill=lfill)
            ay = base + (len(lines) - 1) * 13.5 + 10
            s.path("M %g %g L %g %g L %g %g L %g %g"
                   % (x1 - 5, ay - 9, x1 + 34, ay - 9, x1 + 34, ay + 9, x1 + 5, ay + 9),
                   stroke="#455a64", sw=1.4, marker="ah")
            self.y = ay + 14
            return ay
        dx = abs(x2 - x1)
        maxw = max(80, dx - 16)
        lines = wrap(text, maxw, lsize)
        base = self.y + 11
        for i, ln in enumerate(lines):
            s.t((x1 + x2) / 2, base + i * 13.5, ln, size=lsize, anchor="middle", fill=lfill)
        ay = base + (len(lines) - 1) * 13.5 + 10
        s.arrow(x1, ay, x2, ay, dash=("7 4" if kind == "reply" else None), sw=1.4)
        self.y = ay + 13
        return ay

    def note(self, keys, text, y=None, size=10.5, fill="#fffde7", stroke="#c8a415",
             pad=8, lh=13.5, x0=None, x1=None):
        s = self.s
        if y is None:
            y = self.y
        if keys:
            xs = [self.px[k] for k in keys]
            x0 = min(xs) - 26 if x0 is None else x0
            x1 = max(xs) + 26 if x1 is None else x1
        lines = wrap(text, (x1 - x0) - 2 * pad, size)
        h = len(lines) * lh + 2 * pad
        s.rect(x0, y, x1 - x0, h, fill=fill, stroke=stroke, rx=5, dash="5 3")
        for i, ln in enumerate(lines):
            s.t(x0 + pad, y + pad + size + i * lh, ln, size=size, fill="#5d4037")
        if y + h > self.y:
            self.y = y + h + 12
        return y + h

    def frame(self, label, y0, y1, note=None):
        s = self.s
        s.rect(self.x0 - 8, y0, self.right - self.x0 + 16, y1 - y0,
                fill="none", stroke="#90a4ae", rx=6, dash="6 4")
        tw = text_width(label, 11) + 16
        s.rect(self.x0 - 8, y0 - 11, tw, 22, fill="#ffffff", stroke="#90a4ae", rx=4)
        s.t(self.x0 + 8, y0 + 4, label, size=11, weight="600", fill="#455a64")
        if note:
            s.t(self.right - 12, y0 + 16, note, size=10, anchor="end", fill="#8d6e63",
                italic=True)

    def alt(self, a, b, y0, ymid, y1):
        """alt/else 区块：外框 + 分支分隔线 + 两个分支标签。"""
        s = self.s
        s.rect(self.x0 - 8, y0, self.right - self.x0 + 16, y1 - y0,
                fill="none", stroke="#90a4ae", rx=6, dash="6 4")
        s.path("M %g %g L %g %g" % (self.x0 - 8, ymid, self.right + 8, ymid),
               stroke="#90a4ae", sw=1.1, dash="6 4")
        for label, yy in ((a, y0), (b, ymid)):
            tw = text_width(label, 11) + 16
            s.rect(self.x0 - 8, yy - 11, tw, 22, fill="#ffffff", stroke="#90a4ae", rx=4)
            s.t(self.x0 + 8, yy + 4, label, size=11, weight="600", fill="#455a64")

    def block(self, label, fn, note=None):
        """记录一个 alt/loop 区块：fn() 内部调用 self.msg，返回 (y0, y1)。"""
        y0 = self.y - 4
        fn()
        y1 = self.y + 2
        self.frame(label, y0, y1, note)
        return y0, y1
