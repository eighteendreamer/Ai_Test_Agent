# DeepSeek 真实页面人工录制当前验收

日期：2026-10-23。

本轮先读取现有录制实现、`recording-quality-v1` 质量投影、CDP 录制范围回执以及 DeepSeek 专项测试，再运行真实 Chromium 和生产 API。测试没有登录 DeepSeek、没有提交聊天内容，只访问公开首页并执行两次“深度思考”、一次“智能搜索”和一次滚动。

## 实际结果

命令（`Agent_Server`）：

```powershell
$env:RUN_LIVE_DEEPSEEK_RECORDING='1'
$env:DEEPSEEK_RECORDING_EVIDENCE='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p5-deepseek-recording-live-current-20261023'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_recording_deepseek_live.py -q --tb=short --maxfail=1 --junitxml=docs/upgrade_2026-10-03/p5-deepseek-recording-live-current-20261023.xml
# 1 passed in 30.61s
```

真实页面事实：`https://www.deepseek.com/` 返回 HTTP 200，标题为 `DeepSeek | 深度求索`；“深度思考”和“智能搜索”按钮各 1 个。录制终态为 `completed`，6 条事件从序号 0 到 5 连续发布并写入 PostgreSQL，`pending/channel_dropped/transport_dropped/frames_unconfirmed` 均为 0。

质量投影事实：

- 2 条观察事件（`page_scan`、`page_response`）列为噪声，但原始事件不删除；
- 两次“深度思考”被标记为同一动作候选，仍保留两条真实点击；
- 4 条动作可回放，清洗序列为 `1,2,3,4`，`removed_event_sequences=[]`；
- 11 个网络响应与操作窗口关联，未解析响应关联数为 0；
- 发布交付遗漏为 `verified`，但 `business_capture_complete` 仍为 `unknown`，不会把事件交付完整误报为业务意图完整。

原始事件、质量投影、录制回执、页面事实及截图保存在：
`Agent_Server/docs/upgrade_2026-10-03/p5-deepseek-recording-live-current-20261023/`。

## 分类

本轮将“DeepSeek 公开页面基础人工操作→事件落库→确定性清洗投影→网络响应关联”从“尚未验收”更新为**已有实现且真实验收通过**。

以下仍未完成，不能由本轮结果替代：DeepSeek 登录或聊天提交、跨文档/iframe、Shadow DOM 复杂关联、selector 修复评审、失败定位报告、场景编辑/元素别名，以及默认 AI 读取该录制知识后自主规划和执行。性能与安全测试继续使用受控目标，不把 DeepSeek 作为压测或扫描目标。

## 清理

测试结束后回查没有残留 `qa-execution-cli-*` 容器。隔离 PostgreSQL、MySQL、Redis、Memgraph 和录制对象由测试夹具清理；本轮没有改生产配置、数据库 schema 或模型协议。
