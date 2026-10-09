# P0 UI 录制真实浏览器验收记录

## 依据与边界

本轮先读取现有 `CdpAttachDriver`、`PlaywrightBindingDriverBase`、`recorder.js`、`RecorderEvent` 和 `RecorderSessionService`，再执行真实浏览器。驱动负责 CDP 连接、注入统一录制脚本、事件回流和结束回执；会话服务负责事件批量入库和图谱固化。没有把模板、模拟事件或默认 Step 模型当作录制证据。

DeepSeek 只使用公开首页 `https://www.deepseek.com/`。浏览器未登录，不发送对话请求；输入的“录制探针”是非敏感测试字符串。

## 实际结果

1. `tests/test_recorder_live_cdp.py` 使用真实 Chromium、生产 `CdpAttachDriver` 和本地页面，`1 passed`，证明 CDP attach、recorder.js 注入、真实点击、定位链、像素信息和事件回流契约可用。
2. `tests/test_recorder_deepseek_live.py` 使用生产 CDP 驱动访问 DeepSeek，点击“和 DeepSeek 对话”并在首页文本框输入非敏感探针，`1 passed in 4.26s`。实际采到 `click`、`page_scan`、`navigate`、`fill`，结束回执为 `recording-scopes-v2`，`pending=0`、`frames_unconfirmed=0`、`transport_dropped=0`、`channel_dropped=0`。原始事件、回执和截图保存在 `p0-deepseek-recording-verified/`。
3. `tests/test_recording_embedded_product.py` 的 `settled` 和 `response-dom` 场景使用真实 Electron、隔离 PostgreSQL/RustFS/图谱服务，`2 passed, 14 deselected in 23.97s`。这证明现有采集事件可以走真实产品的批量写入、结束对账、响应后 DOM 观测和知识固化路径。

## 尚未由本轮证明的内容

- DeepSeek 这次是生产驱动级物理采集，未把外部页面事件直接写入正式 PostgreSQL/RustFS，避免把外部站点和产品测试数据混在一起。
- 没有登录、提交消息、跨账号或业务数据操作，因此不证明登录态保持、鉴权页面和对话业务步骤。
- 还没有完成完整的去重/干扰清洗、遗漏检查、跨页面/跨域/Shadow DOM 关系、场景管理、步骤整理、元素别名、失败定位和默认 AI 使用；这些仍需用本地可控页面和正式产品存储逐项验收。
- 默认 Step 模型因实名认证返回 403，按用户决定暂缓真实 AI 验收。

## 可复现命令

在 `Agent_Server` 目录执行：

```powershell
$env:RUN_LIVE_CDP_RECORDING='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_recorder_live_cdp.py -q --tb=short

$env:RUN_LIVE_DEEPSEEK_RECORDING='1'
$env:RECORDING_EVIDENCE_DIR='docs/upgrade_2026-10-03/p0-deepseek-recording-verified'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_recorder_deepseek_live.py -q --tb=short

$env:RUN_LIVE_RECORDING_PRODUCT='1'
$env:RECORDING_PRODUCT_EVIDENCE='docs/upgrade_2026-10-03/p0-recording-product-verified'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_recording_embedded_product.py -q -k "settled or response-dom" --tb=short
```

这些命令均未调用默认 Step/Agent 模型；第一、二项需要外网浏览器，第三项需要本地 Docker/数据库/电子录制运行环境。
