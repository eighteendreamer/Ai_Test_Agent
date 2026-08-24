"""录制回放执行器（方案 6.2 回放定位决策链，P2-1）。

定位决策链（逐级重试，元素漂移容错）：

    locators.id → testid → role+name → css → xpath     （DOM 层）
      ↓ 全部失效
    bbox + rel_offset：几何最近元素重锚定
      ↓ 仍失败
    viewport_point 绝对坐标兜底（Canvas 类页面唯一手段）

回放动作映射：navigate→goto、fill→fill、click/dblclick→click、
submit→表单提交语义（点击触发元素）、key→keyboard、scroll→滚动；
page_scan 是采集副产物不回放；file_change 只记文件名（无文件实体）与
脱敏输入（value={"length":n}）标记 skipped——安全红线优先于回放完整度。

报告：逐步 {seq, action, strategy, status, error} + 汇总 success_rate，
供「回放失败样本回流为回归用例」（方案 12 章可追溯要求）。

计划构建（build_replay_plan）为纯函数，不依赖 playwright 可直接单测；
执行（execute）持 playwright 生命周期，_start_playwright 为测试注入口。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.schemas.recording import RecorderEvent

logger = logging.getLogger(__name__)

# 可回放动作（其余事件类型视为采集副产物，跳过并说明理由）；
# file_change 属录制动作但无文件实体，单独拦截为 file_unavailable
_REPLAYABLE_TYPES = {"click", "dblclick", "fill", "key", "submit", "scroll", "navigate", "file_change"}

# 定位决策链顺序（方案 6.2）：strategy 名 → locators 字段
_LOCATOR_STRATEGY_FIELDS: list[tuple[str, str]] = [
    ("id", "id"),
    ("testid", "testid"),
    ("role_name", "role_name"),
    ("css", "css"),
    ("xpath", "xpath"),
]

_STEP_TIMEOUT_SECONDS = 10.0

# CSS id 标识符合法字符（数字/字母/连字符/下划线；不以数字开头由 # 语义兼容）
_CSS_ID_RE = re.compile(r"[A-Za-z_-][A-Za-z0-9_-]*")


def _is_masked_value(value: Any) -> bool:
    """采集端脱敏语义：敏感输入只剩长度（schemas.mask_sensitive_input）。"""
    return isinstance(value, dict) and set(value) == {"length"}


@dataclass
class ReplayStep:
    """单步回放计划（纯函数产物）。"""

    seq: int
    action: str
    detail: dict[str, Any] = field(default_factory=dict)
    strategies: list[dict[str, Any]] = field(default_factory=list)
    skip_reason: str | None = None


@dataclass
class ReplayStepResult:
    """单步执行结果。"""

    seq: int
    action: str
    strategy: str = ""
    status: str = "passed"  # passed / failed / skipped
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class ReplayReport:
    """整次回放报告（方案 12 章可追溯：失败样本可回流回归用例）。"""

    recording_id: str
    entry_url: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    steps: list[ReplayStepResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.steps if s.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for s in self.steps if s.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for s in self.steps if s.status == "skipped")

    @property
    def success_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "entry_url": self.entry_url,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "success_rate": round(self.success_rate, 4),
            },
            "steps": [
                {
                    "seq": s.seq,
                    "action": s.action,
                    "strategy": s.strategy,
                    "status": s.status,
                    "error": s.error,
                    "elapsed_ms": s.elapsed_ms,
                }
                for s in self.steps
            ],
        }


# ------------------------------------------------------------ 计划（纯函数）


def build_replay_plan(events: list[RecorderEvent]) -> list[ReplayStep]:
    """录制事件流 → 回放步骤计划（纯函数，不触浏览器）。

    - page_scan/未知类型：skip（采集副产物）；
    - file_change：skip（只记文件名，无文件实体可回放）；
    - fill 且 value 已脱敏（{length:n}）：skip（明文不可恢复，安全红线）；
    - 其余动作生成定位策略链：DOM 五级 → bbox 几何重锚 → 坐标兜底。
    """
    steps: list[ReplayStep] = []
    for event in events:
        if event.type not in _REPLAYABLE_TYPES:
            steps.append(ReplayStep(seq=event.seq, action=event.type, skip_reason="not_replayable"))
            continue
        if event.type == "file_change":
            steps.append(ReplayStep(seq=event.seq, action=event.type, skip_reason="file_unavailable"))
            continue
        if event.type == "fill" and _is_masked_value(event.value):
            steps.append(ReplayStep(seq=event.seq, action=event.type, skip_reason="sensitive_value_masked"))
            continue

        strategies: list[dict[str, Any]] = []
        target = event.target if isinstance(event.target, dict) else {}
        locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
        for strategy, key in _LOCATOR_STRATEGY_FIELDS:
            raw = locators.get(key)
            if not raw:
                continue
            if strategy == "role_name" and isinstance(raw, dict):
                role = str(raw.get("role") or "").strip()
                name = str(raw.get("name") or "").strip()
                if role:
                    # Playwright ARIA 定位语义（get_by_role）
                    strategies.append(
                        {"strategy": "role_name", "kind": "role", "role": role, "name": name}
                    )
            else:
                strategies.append({"strategy": strategy, "kind": "selector", "selector": str(raw)})

        pixel = event.pixel if isinstance(event.pixel, dict) else {}
        bbox = pixel.get("bbox") if isinstance(pixel.get("bbox"), dict) else None
        rel = pixel.get("rel_offset") if isinstance(pixel.get("rel_offset"), dict) else None
        if isinstance(bbox, dict) and isinstance(rel, dict):
            anchor_x = float(bbox.get("x") or 0) + float(rel.get("rx") or 0) * float(bbox.get("w") or 0)
            anchor_y = float(bbox.get("y") or 0) + float(rel.get("ry") or 0) * float(bbox.get("h") or 0)
            strategies.append(
                {"strategy": "geometry", "kind": "geometry", "x": anchor_x, "y": anchor_y,
                 "bbox": dict(bbox), "rel_offset": dict(rel)}
            )
        point = pixel.get("viewport_point") if isinstance(pixel.get("viewport_point"), dict) else None
        if isinstance(point, dict):
            strategies.append(
                {"strategy": "viewport_point", "kind": "point",
                 "x": float(point.get("x") or 0), "y": float(point.get("y") or 0)}
            )

        detail: dict[str, Any] = {"target": target}
        if event.type == "fill":
            detail["value"] = event.value
        elif event.type == "key":
            detail["value"] = event.value
        elif event.type == "scroll":
            detail["value"] = event.value
        elif event.type == "navigate":
            detail["url"] = str((event.page or {}).get("url") or "")
        steps.append(ReplayStep(seq=event.seq, action=event.type, detail=detail, strategies=strategies))
    return steps


# ------------------------------------------------------------ 执行


class RecordingReplayExecutor:
    """按计划在 playwright 页面上回放，产出报告。

    回放不注入 recorder.js（只执行动作）；浏览器独立于录制驱动
    （headless 可配），_start_playwright 为测试注入口。
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any | None = None
        self._browser: Any | None = None

    async def _start_playwright(self) -> Any:
        from playwright.async_api import async_playwright

        return await async_playwright().start()

    # ------------------------------------------------------------ 公共入口

    async def execute(self, *, recording_id: str, entry_url: str, events: list[RecorderEvent]) -> ReplayReport:
        plan = build_replay_plan(events)
        report = ReplayReport(recording_id=recording_id, entry_url=entry_url)
        self._playwright = await self._start_playwright()
        try:
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
            context = await self._browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(entry_url, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001 入口不可达：全链路失败
                for step in plan:
                    report.steps.append(
                        ReplayStepResult(seq=step.seq, action=step.action, status="failed",
                                         error=f"entry unreachable: {exc}")
                    )
                return report

            for step in plan:
                report.steps.append(await self._run_step(page, step))
        finally:
            await self._shutdown()
        report.finished_at = datetime.now(timezone.utc)
        logger.info(
            "replay finished: recording_id=%s total=%s passed=%s failed=%s skipped=%s",
            recording_id, report.total, report.passed, report.failed, report.skipped,
        )
        return report

    async def _shutdown(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                logger.debug("replay browser close ignored error")
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                logger.debug("replay playwright stop ignored error")
            self._playwright = None

    # ------------------------------------------------------------ 单步

    async def _run_step(self, page: Any, step: ReplayStep) -> ReplayStepResult:
        if step.skip_reason:
            return ReplayStepResult(seq=step.seq, action=step.action, status="skipped",
                                    strategy="", error=step.skip_reason)
        started = datetime.now(timezone.utc)
        try:
            strategy = await self._dispatch(page, step)
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            return ReplayStepResult(seq=step.seq, action=step.action, strategy=strategy,
                                    status="passed", elapsed_ms=elapsed)
        except Exception as exc:  # noqa: BLE001 单步失败不中断整链（报告聚合）
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            logger.warning(
                "replay step failed: seq=%s action=%s error=%s", step.seq, step.action, exc
            )
            return ReplayStepResult(seq=step.seq, action=step.action, status="failed",
                                    error=str(exc), elapsed_ms=elapsed)

    async def _dispatch(self, page: Any, step: ReplayStep) -> str:
        """按动作分发；定位类动作经策略链逐级重试，返回命中的 strategy。"""
        if step.action == "navigate":
            url = str(step.detail.get("url") or "")
            if url:
                await page.goto(url, wait_until="domcontentloaded")
                return "goto"
            return "noop"

        if step.action == "scroll":
            value = step.detail.get("value") or {}
            top = int(value.get("top") or 0) if isinstance(value, dict) else 0
            left = int(value.get("left") or 0) if isinstance(value, dict) else 0
            await page.evaluate("(p) => window.scrollTo(p.left, p.top)", {"left": left, "top": top})
            return "scroll_to"

        if step.action == "key":
            value = str(step.detail.get("value") or "")
            if value:
                await page.keyboard.press(value)
                return "keyboard"
            return "noop"

        handle = await self._resolve_element(page, step)
        if step.action == "fill":
            await handle.fill(str(step.detail.get("value") or ""), timeout=_STEP_TIMEOUT_SECONDS * 1000)
            return handle.strategy
        if step.action == "dblclick":
            await handle.dblclick(timeout=_STEP_TIMEOUT_SECONDS * 1000)
            return handle.strategy
        # click / submit：submit 以点击触发表单提交语义
        await handle.click(timeout=_STEP_TIMEOUT_SECONDS * 1000)
        return handle.strategy

    # ------------------------------------------------------------ 定位决策链

    async def _resolve_element(self, page: Any, step: ReplayStep) -> Any:
        """逐级尝试策略链：DOM 五级 → 几何重锚 → 坐标兜底；全失配抛错。"""
        last_error: Exception | None = None
        for strategy in step.strategies:
            try:
                handle = await self._apply_strategy(page, strategy)
                if handle is not None:
                    return handle
            except Exception as exc:  # noqa: BLE001 该级失效：降级下一级
                last_error = exc
                logger.debug(
                    "replay strategy miss: seq=%s strategy=%s error=%s",
                    step.seq, strategy.get("strategy"), exc,
                )
        raise RuntimeError(
            f"all strategies exhausted for seq={step.seq}: {last_error or 'no locator available'}"
        )

    async def _apply_strategy(self, page: Any, strategy: dict[str, Any]) -> Any:
        kind = strategy.get("kind")
        strategy_name = str(strategy["strategy"])
        if kind == "selector":
            raw = str(strategy["selector"])
            if strategy_name == "id":
                # id → CSS id 选择器（值含非法字符时该级失效，降级下一级）
                selector = f"#{raw}" if _CSS_ID_RE.fullmatch(raw) else None
                if selector is None:
                    return None
                handle = page.locator(selector).first
            elif strategy_name == "testid":
                # Playwright 官方 test id 定位（默认 data-testid，与采集端一致）
                handle = page.get_by_test_id(raw).first
            else:  # css / xpath
                handle = (
                    page.locator(raw).first
                    if strategy_name == "css"
                    else page.locator(f"xpath={raw}").first
                )
            await handle.wait_for(state="attached", timeout=_STEP_TIMEOUT_SECONDS * 1000)
            handle.strategy = strategy_name  # 命中策略回填报告
            return handle
        if kind == "role":
            locator = page.get_by_role(str(strategy["role"]), exact=False)
            name = str(strategy.get("name") or "")
            if name:
                locator = page.get_by_role(str(strategy["role"]), name=name, exact=False)
            handle = locator.first
            await handle.wait_for(state="attached", timeout=_STEP_TIMEOUT_SECONDS * 1000)
            handle.strategy = str(strategy["strategy"])
            return handle
        if kind == "geometry":
            # bbox+rel_offset 几何重锚：找几何上最接近的可交互元素（元素漂移容错）
            handle = await self._nearest_interactive(page, float(strategy["x"]), float(strategy["y"]))
            handle.strategy = str(strategy["strategy"])
            return handle
        if kind == "point":
            await page.mouse.click(float(strategy["x"]), float(strategy["y"]))
            return _VirtualHandle("viewport_point")
        raise RuntimeError(f"unknown strategy kind: {kind}")

    async def _nearest_interactive(self, page: Any, x: float, y: float) -> Any:
        selector_and_handle = await page.evaluate_handle(
            """(point) => {
                const nodes = document.querySelectorAll(
                    'button, a, input, select, textarea, [role], [tabindex]'
                );
                let best = null, bestDist = Infinity;
                for (const el of nodes) {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                    const d = Math.hypot(cx - point.x, cy - point.y);
                    if (d < bestDist) { bestDist = d; best = el; }
                }
                return best;
            }""",
            {"x": x, "y": y},
        )
        element = await selector_and_handle.as_element()
        await selector_and_handle.dispose()
        if element is None:
            raise RuntimeError("no interactive element near anchor point")
        return _ElementHandleAdapter(element)


class _VirtualHandle:
    """坐标兜底命中的虚拟句柄（动作已由 mouse 执行，fill 不可用）。"""

    def __init__(self, strategy: str) -> None:
        self.strategy = strategy

    async def click(self, timeout: float = 0) -> None:
        return None  # 坐标点击已在策略内完成

    async def dblclick(self, timeout: float = 0) -> None:
        return None

    async def fill(self, value: str, timeout: float = 0) -> None:
        raise RuntimeError("viewport_point fallback cannot fill text (use DOM strategies)")


class _ElementHandleAdapter:
    """geometry 命中的 ElementHandle 包装（补 strategy 字段）。"""

    def __init__(self, element: Any) -> None:
        self._element = element
        self.strategy = "geometry"

    async def click(self, timeout: float = 0) -> None:
        await self._element.click(timeout=timeout)

    async def dblclick(self, timeout: float = 0) -> None:
        await self._element.dblclick(timeout=timeout)

    async def fill(self, value: str, timeout: float = 0) -> None:
        await self._element.fill(value, timeout=timeout)
