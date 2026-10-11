# DeepSeek 真实页面人工录制验证

本批验证的是现有人工浏览器录制链路，不调用默认 AI，不登录 DeepSeek，也不提交聊天内容。测试使用 Playwright 驱动真实 Chromium 页面，生产 Uvicorn API、隔离 MySQL/PostgreSQL/Redis/Memgraph 和现有录制存储；浏览器操作只包含打开公开首页、点击“深度思考”两次、点击“智能搜索”、滚动和截图。

## 实际页面与录制结果

- `https://www.deepseek.com/` 返回 HTTP 200，页面标题为 `DeepSeek | 深度求索`。
- 页面存在唯一的“深度思考”和“智能搜索”按钮；实际点击操作被录制。
- 终态 `step_count=8`，PostgreSQL 原始事件数为 8，`recording-scopes-v2` 回执 `pending=0`、`frames_unconfirmed=0`、`channel_dropped=0`、`transport_dropped=0`，发布序号为 0—7。
- `recording-quality-v1` 将 3 条 `page_scan/page_response` 标记为观察噪声，将两次“深度思考”标记为重复候选；`removed_event_sequences=[]`，所以原始事件没有被删除或改写。
- `business_capture_complete` 保持 `unknown`。序号完整只证明录制事件已交付并落库，不证明业务意图或页面网络响应已经完整关联。

## 首轮失败与修正

首轮测试先关闭浏览器，再请求录制停止。实际回执出现两个 document scope，当前 scope 未结束确认，结果为 `finish_unconfirmed`；PG 中的 8 条事件仍然存在。根因是测试生命周期顺序错误，不是录制器丢事件。修正为“停止录制并保存终态回执，再关闭浏览器”后，实测 `1 passed in 29.81s`。

失败原件保留在 `p5-deepseek-recording-live/`，修正版原件保留在 `p5-deepseek-recording-live-fixed/`，页面探针和截图在 `p5-deepseek-ui-research/`。测试文件为 `Agent_Server/tests/test_recording_deepseek_live.py`。

## 结论与边界

本批证明公开 DeepSeek 页面上的人工浏览器操作能够进入现有录制、PostgreSQL 落库、结束回执和确定性质量投影链路，并能识别观察噪声、重复候选和交付遗漏状态。尚未验证登录、聊天提交、复杂网络响应归属、跨文档/iframe、Shadow DOM、selector 修复评审、失败定位报告，以及默认 AI 读取录制知识后自主规划和执行；默认 AI 仍按用户决定等待账号实名认证后再验收。
