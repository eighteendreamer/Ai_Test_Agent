# UI 录制响应观察窗口与动作序号投影（2026-10-20）

## 查证依据

- `Agent_Server/src/application/recorder/assets/recorder.js` 在结束 DOM 响应窗口时写入 `action_local_seqs`、`association=temporal_after_actions`，并可附加 fetch/XHR 元数据。
- `Agent_Server/tests/test_recording_network_live.py` 已用真实 Chromium、隔离 API 和 PostgreSQL 验证一个实际点击触发 fetch 200、fetch 404、XHR 201；原始事件中响应窗口引用的是页面作用域的局部动作序号。
- `Agent_Server/src/application/recorder/recording_quality.py` 原来把 `page_response` 全部放在 observation noise 中，没有输出该局部序号与 PG 全局事件序号的关系。
- `Agent_Server/src/application/recorder/recording_case_draft_service.py` 会把结束时的质量投影复制到正式用例草稿 `test_data.recording_quality`；无需另造 AI 或页面协议。
- 本地课程 `项目借鉴/llm-testing-course/测试数据合成与用例自动生成.html` 介绍完全、近似和语义去重。UI 动作重复可能是有意操作，因此沿现有录制约定保留全部原事件、仅标记重复候选，不套用训练样本去重规则删除用户操作。

## 根因与实现

已有采集信息本身包含动作引用；缺的是从 browser scope 局部序号到 PG 事件序号的确定性质量投影。现在以相同 `page_id`、`frame_id`、`document_id` 和 `local_seq` 精确匹配回放动作，并要求它先于 `page_response`。缺失引用、作用域错误、重复引用和动作晚于响应时输出固定诊断原因，不跨文档猜测。

`response_action_associations` 保存响应事件序号、关联动作事件序号、关联类型、网络观测状态及 outcome 计数。它明确描述为 `temporal_after_actions`；未宣称应用请求与服务端业务的因果关系。网络路径、查询串、头部、请求/响应正文不在新增摘要中。原始 PostgreSQL 事件仍追加写，`page_response` 仍作为不可回放观察证据保存；重复点击依然保留并仅作为候选。新质量字段自动通过既有 finalize metrics 和录制草稿 JSON 传递。

## 实测

| 命令/场景 | 结果 | 输出 |
|---|---|---|
| `python -X utf8 -m pytest tests/test_recording_quality.py -q --tb=short` | 11 passed，0.17秒 | 单元测试，无独立 JUnit |
| `RUN_LIVE_RECORDING_NETWORK=1`，`tests/test_recording_network_live.py` | 1 passed，27.56秒 | `p5-recording-response-association-live-20261020.xml` 和同名目录 |
| `RUN_LIVE_RECORDING_QUALITY=1`，`tests/test_recording_quality_live.py` | 1 passed，22.07秒 | `p5-recording-response-association-quality-live-20261020.xml` 和同名目录 |
| `RUN_LIVE_DEEPSEEK_RECORDING=1`，更新后 `tests/test_recording_deepseek_live.py` | 1 passed，29.21秒 | `p5-recording-response-association-deepseek-live-20261020-rerun.xml` 和同名目录 |
| Python `compileall`、`git diff --check` | 成功 | 命令退出码0 |

DeepSeek实际页状态为HTTP 200，标题 `DeepSeek | 深度求索`；最终6条原始PG事件、结束回执和 PG 对账正常，质量投影中一个响应观察窗口关联到4条此前动作，10个 fetch/XHR 观测 outcome 均为已收到响应，无未解析动作引用。测试不登录、不提交聊天、不调用模型。它由 Playwright 控制真实 Chromium，因此不是物理人工点击或完整 Vue 页面验收。

## 边界

该改动只补“响应观察窗口 → 已捕获动作”的可追溯投影。它不证明用户业务意图，也不证明远端服务端因果；网络正文仍不采集。beacon、WebSocket、Service Worker 内部请求、导航请求、closed Shadow DOM、selector 修复评审、失败定位报告、默认 AI 消费这条 DeepSeek 录制知识和完整 UI 产品流程仍未验收。无数据库迁移、依赖、前端契约或 Provider 协议变化；完整 P0—P5 保持进行中。
