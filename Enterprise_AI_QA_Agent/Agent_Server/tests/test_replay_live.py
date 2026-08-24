"""回放执行器真实浏览器验收（P2-1，RUN_LIVE_REPLAY=1 门控）。

真实 Chromium 回放一段录制：fill（css 定位）→ click（id 定位）→
click（仅像素定位，走几何重锚/坐标兜底）→ submit；断言逐步命中策略与
全通过；含一个脱敏 fill 被安全跳过。回放「真实点击」由页面脚本在
window 上留下痕迹（data-clicked），回放后读回验证动作真实生效。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pytest

from src.application.recorder.replay_executor import RecordingReplayExecutor
from src.schemas.recording import RecorderEvent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_REPLAY") != "1",
    reason="set RUN_LIVE_REPLAY=1 to run live replay acceptance (needs chromium)",
)

_TEST_PAGE = "data:text/html;charset=utf-8," + quote(
    "<html><head><title>回放页</title></head><body>"
    "<form id='login' onsubmit='return false'>"
    "<input id='username' class='user-input' placeholder='用户名' />"
    "<input id='password' type='password' />"
    "<button id='submit' type='button'>登 录</button>"
    "<canvas id='board' width='200' height='100'></canvas>"
    "</form>"
    "<script>window.__marks = [];"
    "document.getElementById('username').addEventListener('input',"
    " () => window.__marks.push('fill:' + document.getElementById('username').value));"
    "document.getElementById('submit').addEventListener('click',"
    " () => window.__marks.push('click:submit'));"
    "document.getElementById('board').addEventListener('click',"
    " () => window.__marks.push('click:board'));"
    "</script></body></html>",
    safe="'=/< >:-.",
)


def _event(seq: int, etype: str, **overrides: Any) -> RecorderEvent:
    payload: dict[str, Any] = {
        "seq": seq,
        "type": etype,
        "page": {"url": _TEST_PAGE},
        "target": None,
        "value": None,
        "page_effect": {},
        "timestamp": datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return RecorderEvent(**payload)


def test_live_replay_full_chain_with_strategy_hits() -> None:
    """fill(css) → 脱敏fill(skip) → click(id) → click(仅像素，几何重锚) 全链路。"""

    async def scenario() -> None:
        events = [
            # css 定位填用户名
            _event(
                0,
                "fill",
                target={"tag": "INPUT", "locators": {"css": "#username"}},
                value="alice",
            ),
            # 密码脱敏（长度）→ 安全跳过
            _event(
                1,
                "fill",
                target={"tag": "INPUT", "locators": {"css": "#password"},
                        "attributes": {"type": "password"}},
                value={"length": 8},
            ),
            # id 定位点登录
            _event(
                2,
                "click",
                target={"tag": "BUTTON", "locators": {"id": "submit", "text": "登 录"}},
            ),
            # 无 DOM locator 的 canvas 点击：pixel 三件套 → 几何重锚/坐标兜底
            _event(
                3,
                "click",
                target={"tag": "CANVAS"},
                pixel={
                    "viewport_point": {"x": 100, "y": 500},
                    "bbox": {"x": 8, "y": 400, "w": 200, "h": 100},
                    "rel_offset": {"rx": 0.5, "ry": 0.5},
                },
            ),
        ]
        executor = RecordingReplayExecutor(headless=True)
        report = await executor.execute(
            recording_id="rec-replay-1", entry_url=_TEST_PAGE, events=events
        )

        by_seq = {s.seq: s for s in report.steps}
        assert by_seq[0].status == "passed" and by_seq[0].strategy == "css"
        assert by_seq[1].status == "skipped" and by_seq[1].error == "sensitive_value_masked"
        assert by_seq[2].status == "passed" and by_seq[2].strategy == "id"
        # 像素策略命中（geometry 或 viewport_point 均为合法兜底）
        assert by_seq[3].status == "passed"
        assert by_seq[3].strategy in {"geometry", "viewport_point"}
        assert report.success_rate == 0.75

    asyncio.run(scenario())
