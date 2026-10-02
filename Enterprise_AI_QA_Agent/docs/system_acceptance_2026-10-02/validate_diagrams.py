"""Verify SVG canvas/card text bounds in Chromium and save raster previews."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent


async def main():
    checks = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1900, "height": 1700})
            for name in ("system-architecture", "functional-architecture"):
                svg = (ROOT / f"{name}.svg").read_text(encoding="utf-8")
                await page.set_content(f"<html><body style='margin:0'>{svg}</body></html>")
                await page.evaluate("document.fonts.ready")
                result = await page.evaluate("""() => {
                  const svg = document.querySelector('svg');
                  const width = svg.viewBox.baseVal.width, height = svg.viewBox.baseVal.height;
                  const cards = [...svg.querySelectorAll('rect[fill="white"]')].map(e => e.getBBox());
                  const errors = [];
                  for (const element of svg.querySelectorAll('text')) {
                    const b = element.getBBox(), x = +element.getAttribute('x'), y = +element.getAttribute('y');
                    if (b.x < 0 || b.y < 0 || b.x+b.width > width || b.y+b.height > height)
                      errors.push({kind:'canvas_overflow', text:element.textContent});
                    const card = cards.find(r => x >= r.x && x <= r.x+r.width && y >= r.y && y <= r.y+r.height);
                    if (card && (b.x < card.x || b.y < card.y || b.x+b.width > card.x+card.width || b.y+b.height > card.y+card.height))
                      errors.push({kind:'card_overflow', text:element.textContent});
                  }
                  return {width,height,text_count:svg.querySelectorAll('text').length,
                          foreign_objects:svg.querySelectorAll('foreignObject').length,
                          external_resources:svg.querySelectorAll('image,script').length,errors};
                }""")
                await page.locator("svg").screenshot(path=str(ROOT / f"{name}.png"))
                checks.append({"file": f"{name}.svg", **result})
            report = {"browser": browser.version, "checks": checks,
                      "passed": all(not c["errors"] and not c["foreign_objects"] and not c["external_resources"] for c in checks)}
            (ROOT / "diagram-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            if not report["passed"]:
                raise SystemExit(1)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
