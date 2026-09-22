"""静态检查生成的 SVG：文本是否越界、元素是否落在画布内。"""
import sys, os, re, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import text_width, esc

DIAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")


def unesc(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&amp;", "&"))


def boxes(path):
    """返回所有 <text> 的包围盒 (x0, y0, x1, y1, body)。"""
    txt = open(path, encoding="utf-8").read()
    out = []
    for tag in re.finditer(r'<text ([^>]*)>(.*?)</text>', txt, re.S):
        attrs, body = tag.group(1), unesc(tag.group(2))
        x = float(re.search(r'x="([-\d.]+)"', attrs).group(1))
        y = float(re.search(r'y="([-\d.]+)"', attrs).group(1))
        size = float(re.search(r'font-size="([\d.]+)"', attrs).group(1))
        anchor = re.search(r'text-anchor="(\w+)"', attrs)
        anchor = anchor.group(1) if anchor else "start"
        w = text_width(body, size)
        x0 = x if anchor == "start" else (x - w / 2 if anchor == "middle" else x - w)
        out.append((x0, y - size * 0.8, x0 + w, y + size * 0.25, body))
    return out


def collisions(path, thresh=0.35):
    """文字压字检测：重叠面积超过较小框面积的 thresh 即报告。"""
    bs = boxes(path)
    bad = []
    for i in range(len(bs)):
        ax0, ay0, ax1, ay1, at = bs[i]
        for j in range(i + 1, len(bs)):
            bx0, by0, bx1, by1, bt = bs[j]
            if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
                continue
            inter = (min(ax1, bx1) - max(ax0, bx0)) * (min(ay1, by1) - max(ay0, by0))
            small = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
            if small > 0 and inter / small > thresh:
                bad.append((at[:26], bt[:26]))
    return bad


def check(path):
    txt = open(path, encoding="utf-8").read()
    m = re.search(r'width="([\d.]+)" height="([\d.]+)" viewBox', txt)
    W, H = float(m.group(1)), float(m.group(2))
    bad = []
    for tag in re.finditer(r'<text ([^>]*)>(.*?)</text>', txt, re.S):
        attrs, body = tag.group(1), unesc(tag.group(2))
        x = float(re.search(r'x="([-\d.]+)"', attrs).group(1))
        y = float(re.search(r'y="([-\d.]+)"', attrs).group(1))
        size = float(re.search(r'font-size="([\d.]+)"', attrs).group(1))
        anchor = re.search(r'text-anchor="(\w+)"', attrs)
        anchor = anchor.group(1) if anchor else "start"
        w = text_width(body, size)
        x0 = x if anchor == "start" else (x - w / 2 if anchor == "middle" else x - w)
        if x0 < -1 or x0 + w > W + 1 or y < 0 or y > H:
            bad.append(("text", round(x0), round(y), round(x0 + w), body[:40]))
    for tag in re.finditer(r'<rect (x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)")', txt):
        x, y, w, h = (float(tag.group(i)) for i in (2, 3, 4, 5))
        if x < -1 or y < -1 or x + w > W + 1 or y + h > H + 1:
            bad.append(("rect", round(x), round(y), round(x + w), round(y + h)))
    for tag in re.finditer(r'<polygon points="([^"]+)"', txt):
        pts = [tuple(map(float, p.split(","))) for p in tag.group(1).split()]
        for px, py in pts:
            if px < -1 or py < -1 or px > W + 1 or py > H + 1:
                bad.append(("poly", round(px), round(py), 0, 0))
    for tag in re.finditer(r'<path\b([^>]*)>', txt):
        attrs = tag.group(1)
        if 'fill=' not in attrs:
            bad.append(("path-fill", "missing explicit fill", attrs[:80]))
    for attr, value in re.findall(r'\b(fill|stroke)="([^"]*)"', txt):
        if value.startswith("#") and len(value) not in (4, 7):
            bad.append(("color", "invalid " + attr, value))
    for tag in re.finditer(r'<ellipse cx="([-\d.]+)" cy="([-\d.]+)" rx="([\d.]+)" ry="([\d.]+)"', txt):
        cx, cy, rx, ry = (float(tag.group(i)) for i in (1, 2, 3, 4))
        if cx - rx < -1 or cy - ry < -1 or cx + rx > W + 1 or cy + ry > H + 1:
            bad.append(("ellipse", round(cx - rx), round(cy - ry), round(cx + rx), round(cy + ry)))
    return W, H, bad


if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(DIAG, "*.svg")))
    if len(sys.argv) > 1:
        files = [f for f in files if any(a in f for a in sys.argv[1:])]
    total = 0
    for f in files:
        W, H, bad = check(f)
        col = collisions(f)
        total += len(bad) + len(col)
        flag = "OK  " if not bad and not col else "FAIL"
        print("%s %-28s %5gx%-5g %d issue(s)" % (flag, os.path.basename(f), W, H, len(bad) + len(col)))
        for b in bad[:12]:
            print("      ", b)
        for c in col[:12]:
            print("       collide:", c)
    print("total issues:", total)
