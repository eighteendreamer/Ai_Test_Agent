"""SVG 绘图工具：为毕设图表生成无外部依赖的矢量图。

设计约束：
- 纯 SVG 1.1，无外部字体/脚本引用，任何浏览器与 Word/PDF 均可渲染。
- 中日韩字符按全宽估算行内断行，保证中文标签不被截断。
"""

import unicodedata


def is_wide(ch):
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return True
    return 0x2E80 <= ord(ch) <= 0x9FFF or 0xAC00 <= ord(ch) <= 0xD7A3


def esc(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def text_width(s, size):
    """按字宽估算字符串像素宽度（CJK 全宽，ASCII 约 0.55 em）。"""
    w = 0.0
    for ch in s:
        if is_wide(ch):
            w += size
        elif ch in "iljtf.,:;'|!()[]{}/\\":
            w += size * 0.32
        elif ch in " ":
            w += size * 0.3
        elif ch.isupper() or ch in "mwMW@%&":
            w += size * 0.68
        else:
            w += size * 0.55
    return w


def wrap(s, max_w, size):
    """贪心断行：CJK 逐字可断，ASCII 单词整体不断行。"""
    out = []
    for para in s.split("\n"):
        tokens = []
        buf = ""
        for ch in para:
            if is_wide(ch):
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)
            elif ch == " ":
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(" ")
            else:
                buf += ch
                if ch in "-/_>":
                    tokens.append(buf)
                    buf = ""
        if buf:
            tokens.append(buf)
        line = ""
        for t in tokens:
            cand = line + t
            if cand.strip() and text_width(cand.rstrip(), size) > max_w and line:
                out.append(line.rstrip())
                line = "" if t == " " else t
            else:
                line = cand
        out.append(line.rstrip())
    return [l for l in out]


class Svg:
    def __init__(self, width, height, bg="#ffffff"):
        self.w = float(width)
        self.h = float(height)
        self.bg = bg
        self.parts = []
        self.defs = []

    # ---------- 基础元素 ----------
    def add(self, s):
        self.parts.append(s)

    def define(self, s):
        self.defs.append(s)

    def text(self, x, y, s, size=13, anchor="start", weight="normal",
             fill="#1f2933", italic=False, mono=False):
        st = 'font-family="%s"' % (
            "Consolas, Menlo, monospace" if mono
            else "'Segoe UI','Microsoft YaHei','PingFang SC','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif"
        )
        if weight != "normal":
            st += ' font-weight="%s"' % weight
        if italic:
            st += ' font-style="italic"'
        return s

    def t(self, x, y, s, size=13, anchor="start", weight="normal",
          fill="#1f2933", mono=False, italic=False):
        fam = ("Consolas,Menlo,monospace" if mono else
               "'Segoe UI','Microsoft YaHei','PingFang SC','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif")
        attrs = [
            'x="%g"' % x, 'y="%g"' % y,
            'font-family="%s"' % fam,
            'font-size="%g"' % size,
            'text-anchor="%s"' % anchor,
            'fill="%s"' % fill,
        ]
        if weight != "normal":
            attrs.append('font-weight="%s"' % weight)
        if italic:
            attrs.append('font-style="italic"')
        self.add("<text %s>%s</text>" % (" ".join(attrs), esc(s)))
        return y

    def wrapped(self, x, y, s, w, size=12, lh=None, anchor="start",
                fill="#37474f", weight="normal", mono=False):
        lh = lh or size * 1.45
        lines = wrap(s, w, size)
        for ln in lines:
            self.t(x, y, ln, size=size, anchor=anchor, fill=fill, weight=weight, mono=mono)
            y += lh
        return y, len(lines)

    # ---------- 形状 ----------
    def rect(self, x, y, w, h, fill="#ffffff", stroke="#7b8b9a", rx=7, sw=1.4,
             dash=None, opacity=1.0):
        a = ['x="%g"' % x, 'y="%g"' % y, 'width="%g"' % w, 'height="%g"' % h,
             'rx="%g"' % rx, 'fill="%s"' % fill, 'stroke="%s"' % stroke,
             'stroke-width="%g"' % sw]
        if dash:
            a.append('stroke-dasharray="%s"' % dash)
        if opacity != 1.0:
            a.append('opacity="%g"' % opacity)
        self.add("<rect %s/>" % " ".join(a))

    def diamond(self, cx, cy, w, h, fill="#fff8e1", stroke="#c8a415", sw=1.5):
        pts = "%g,%g %g,%g %g,%g %g,%g" % (
            cx, cy - h / 2, cx + w / 2, cy, cx, cy + h / 2, cx - w / 2, cy)
        self.add('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%g"/>'
                 % (pts, fill, stroke, sw))

    def ellipse(self, cx, cy, rx, ry, fill="#ffffff", stroke="#7b8b9a", sw=1.4, dash=None):
        a = ['cx="%g"' % cx, 'cy="%g"' % cy, 'rx="%g"' % rx, 'ry="%g"' % ry,
             'fill="%s"' % fill, 'stroke="%s"' % stroke, 'stroke-width="%g"' % sw]
        if dash:
            a.append('stroke-dasharray="%s"' % dash)
        self.add("<ellipse %s/>" % " ".join(a))

    def stadium(self, cx, cy, w, h, fill="#e8eaf6", stroke="#5c6bc0", sw=1.4):
        self.rect(cx - w / 2, cy - h / 2, w, h, fill=fill, stroke=stroke,
                  rx=h / 2, sw=sw)

    def cylinder(self, cx, cy, w, h, fill="#e3f2fd", stroke="#546e7a", sw=1.4):
        ry = h * 0.16
        self.add("<rect %s/>" % " ".join([
            'x="%g"' % (cx - w / 2), 'y="%g"' % (cy - h / 2 + ry),
            'width="%g"' % w, 'height="%g"' % (h - 2 * ry),
            'fill="%s"' % fill, 'stroke="%s"' % stroke, 'stroke-width="%g"' % sw]))
        self.add('<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="%s" stroke="%s" stroke-width="%g"/>'
                 % (cx, cy - h / 2 + ry, w / 2, ry, fill, stroke, sw))
        self.add('<path d="M %g %g A %g %g 0 0 0 %g %g" fill="none" stroke="%s" stroke-width="%g"/>'
                 % (cx - w / 2, cy + h / 2 - ry, w / 2, ry, cx + w / 2, cy + h / 2 - ry, stroke, sw))

    def path(self, d, stroke="#455a64", sw=1.6, dash=None, fill="none", marker=None):
        # SVG defaults an omitted fill to black.  Most paths in these diagrams
        # are open connectors, so make the intended non-filled geometry explicit.
        # Without this attribute, browsers fill the large closed routes formed
        # by orthogonal connectors and produce the progressively larger black
        # regions seen in the exported diagrams.
        a = ['d="%s"' % d, 'stroke="%s"' % stroke, 'stroke-width="%g"' % sw,
             'fill="%s"' % fill]
        if dash:
            a.append('stroke-dasharray="%s"' % dash)
        if marker:
            a.append('marker-end="url(#%s)"' % marker)
        self.add("<path %s/>" % " ".join(a))

    # ---------- 箭头 ----------
    def arrow(self, x1, y1, x2, y2, stroke="#455a64", sw=1.6, dash=None,
              label=None, lsize=11, loff=(0, -6), lfill="#5d4037", lanchor="middle"):
        d = "M %g %g L %g %g" % (x1, y1, x2, y2)
        self.path(d, stroke=stroke, sw=sw, dash=dash, marker="ah")
        if label:
            mx, my = (x1 + x2) / 2 + loff[0], (y1 + y2) / 2 + loff[1]
            self.t(mx, my, label, size=lsize, anchor=lanchor, fill=lfill)

    def elbow(self, x1, y1, x2, y2, stroke="#455a64", sw=1.6, dash=None,
              first="h", label=None, lsize=11, lfill="#5d4037"):
        """正交折线。first='h' 先横后纵，'v' 先纵后横。"""
        if first == "h":
            mx = x2
            d = "M %g %g L %g %g L %g %g" % (x1, y1, mx, y1, x2, y2)
        else:
            my = y2
            d = "M %g %g L %g %g L %g %g" % (x1, y1, x1, my, x2, y2)
        self.path(d, stroke=stroke, sw=sw, dash=dash, marker="ah")
        if label:
            self.t(x1 + (x2 - x1) / 2, min(y1, y2) - 8, label, size=lsize,
                   anchor="middle", fill=lfill)

    def selfloop(self, x, y, w=34, h=22, stroke="#455a64"):
        d = "M %g %g C %g %g %g %g %g %g" % (
            x - 6, y, x - 6 - w, y - h, x + 6 + w, y - h, x + 6, y)
        self.path(d, stroke=stroke, marker="ah")

    # ---------- 高层组件 ----------
    def node(self, x, y, w, h, title, body=None, fill="#ffffff", stroke="#7b8b9a",
             rx=7, tsize=13, bsize=11.5, tfill="#1f2933", bfill="#546e7a", mono=False,
             dash=None, lh=None):
        self.rect(x, y, w, h, fill=fill, stroke=stroke, rx=rx, dash=dash)
        pad = 9
        lines_t = wrap(title, w - 2 * pad, tsize) if title else []
        lines_b = wrap(body, w - 2 * pad, bsize) if body else []
        lh_t = tsize * 1.4
        lh_b = lh or bsize * 1.4
        total = len(lines_t) * lh_t + (4 if lines_b else 0) + len(lines_b) * lh_b
        cy = y + (h - total) / 2 + tsize * 0.95
        for ln in lines_t:
            self.t(x + w / 2, cy, ln, size=tsize, anchor="middle", fill=tfill,
                   weight="600", mono=mono)
            cy += lh_t
        cy += 4
        for ln in lines_b:
            self.t(x + w / 2, cy, ln, size=bsize, anchor="middle", fill=bfill, mono=mono)
            cy += lh_b

    def layer(self, x, y, w, h, label, fill="#f7f9fb", stroke="#b0bec5",
              hfill="#eceff1", dash="7 4"):
        self.rect(x, y, w, h, fill=fill, stroke=stroke, rx=10, dash=dash)
        self.add('<path d="M %g %g L %g %g L %g %g L %g %g Z" fill="%s"/>'
                 % (x, y + 26, x + w, y + 26, x + w, y, x, y, hfill))
        self.t(x + 12, y + 18, label, size=13, weight="700", fill="#37474f")

    def legend(self, x, y, items, size=11.5):
        cx = x
        for label, kind in items:
            if kind == "box":
                self.rect(cx, y - 9, 16, 11, fill="#ffffff", stroke="#7b8b9a", rx=3)
                tx = cx + 21
            elif kind == "dash":
                self.path("M %g %g L %g %g" % (cx, y - 4, cx + 16, y - 4),
                          stroke="#455a64", sw=1.5, dash="6 3")
                tx = cx + 21
            elif kind == "db":
                self.cylinder(cx + 8, y - 4, 15, 13)
                tx = cx + 22
            else:
                self.stadium(cx + 9, y - 4, 17, 11)
                tx = cx + 24
            self.t(tx, y, label, size=size, fill="#5d4037")
            cx = tx + text_width(label, size) + 22
        return cx

    def save(self, path):
        head = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="%g" height="%g" viewBox="0 0 %g %g">\n' % (self.w, self.h, self.w, self.h)
        )
        bg = '<rect x="0" y="0" width="%g" height="%g" fill="%s"/>' % (self.w, self.h, self.bg)
        defs = "<defs>%s</defs>\n" % "".join(self.defs) if self.defs else ""
        # 箭头 markers
        if any('marker-end="url(#ah)"' in p for p in self.parts):
            mk = (
                '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" '
                'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="CONTEXT"/></marker>'
            )
            # 单色箭头：替换为固定色
            mk = mk.replace("CONTEXT", "#455a64")
            defs = "<defs>%s%s</defs>\n" % ("".join(self.defs), mk)
        body = "\n".join([bg] + self.parts)
        with open(path, "w", encoding="utf-8") as f:
            f.write(head + defs + body + "\n</svg>\n")
        return path


# 配色
C = dict(
    box="#ffffff", boxS="#7b8b9a",
    layer="#f7f9fb", layerS="#b0bec5", layerH="#eceff1",
    blue="#e3f2fd", blueS="#5c9ce6",
    green="#e8f5e9", greenS="#66a06a",
    orange="#fff3e0", orangeS="#e09a3e",
    purple="#f3e5f5", purpleS="#a874b5",
    red="#ffebee", redS="#d98a8a",
    yellow="#fff8e1", yellowS="#c8a415",
    indigo="#e8eaf6", indigoS="#6f7dd0",
    cyan="#e0f7fa", cyanS="#4fa8b0",
    grey="#eceff1", greyS="#90a4ae",
)
