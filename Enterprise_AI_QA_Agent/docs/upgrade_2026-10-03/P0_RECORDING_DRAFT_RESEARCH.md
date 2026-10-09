# 录制转用例草稿的原事件、观察与完整性研究

2026-10-10，基线 `c4082384`。本轮处理的是 UI 录制事件已经落入 PG，但转成测试用例草稿后关键依据丢失的问题。目标仍是完整 P0—P5；本轮不调用默认 Step 模型，不把规则转换当作 AI 业务验收。

## 依据和复现

先读取了以下现有实现：

- `src/schemas/recording.py` 的 `RecorderEvent`、`recording_id + seq` 幂等键和敏感输入协议；
- `src/application/recorder/recording_case_draft_service.py` 的录制转草稿规则；
- `src/application/exploration/recording_graph_store.py` 的 PG 事件脱敏、元素指纹合并和完整事件知识读取；
- `src/application/recorder/recorder_session_service.py` 的 stop、PG flush、回执和图谱固化顺序；
- `src/schemas/case_management.py` 的 `TestCaseStep.data`、`test_data` 和版本持久化契约；
- 已有真实录制原件及 `P0_RECORDING_KNOWLEDGE_DESIGN.md`、`P0_RECORDING_RESPONSE_DESIGN.md`。

旧草稿转换只写 `seq`、定位器和像素摘要，`page_scan`/`page_response` 等观察完全丢失；动作说明还把 fill 文本截成 60 个字符。使用已保存的跨文档嵌套录制和真实 Chromium 录制复现：

1. `p0-recording-draft-before.xml`：跨文档原事件无法在草稿步骤中找到完整 `RecorderEvent`；截断响应不在草稿观察中；真实长输入也只剩摘要，3 项均失败。
2. 同一真实浏览器场景包含长 Unicode 输入、密码输入和同一按钮两次实际点击。两次点击必须保留，因为它们有不同 `seq` 且页面计数真实从 0 变为 2；不能按语义相似度误删。
3. 同一场景还暴露了固化顺序缺陷：PG 已保存 8 个事件，但 stop 使用旧内存会话的 `step_count=0` 调用图谱完整性校验，结果为 `step_count_mismatch=true`、`integrity.degraded=true`，会话仍可完成。

## 采用的修复

1. 在 `schemas.recording` 增加统一 `masked_recording_event_payload`，复用图谱写入规则：保留完整事件、页面/文档/frame 身份、定位链、像素、输入和 `page_effect`；密码字段只保留长度，已脱敏 `{length:n}` 继续保持长度。PG 原始事件不改写。
2. 新草稿版本 `p2-2.v2` 的每个可回放步骤保留兼容摘要，并在 `TestCaseStep.data.event` 放完整脱敏事件。`action` 只是展示文本，执行依据读取 `data.event`，不会把摘要当作完整数据。
3. 非动作事件进入 `test_data.recording_observations`。观察不生成回放步骤，也不自动证明业务断言；截断、无变化或未采集状态在原协议中继续可见。执行说明明确文件选择只有文件名、脱敏输入不可恢复，需可靠测试数据或人工评审。
4. 来源 metadata 记录 `finalize_metrics`，保留录制完整性、截断和图谱对账依据；日志只记录计数和版本，不记录输入值。
5. stop 在图谱固化前重新读取 PostgreSQL 权威会话，使用已持久化 `step_count`，并把该快照交给后续状态更新。若会话消失直接失败，不能用旧快照继续固化。

该修复没有引入新表、配置、依赖或存储层。项目配置仍在 MySQL，业务用例/录制事件/版本在 PostgreSQL，截图和大对象仍使用 RustFS，Memgraph 只是可重建投影。

## 实际验证

- 修复前：真实原件驱动的 3 项转换契约全部失败，原始 XML 和事件保存在 `p0-recording-draft-before`。
- 修复后受影响录制、图谱、知识、断言、回执和草稿测试：`61 passed, 6 skipped`；包含真实 Chromium、隔离 MySQL/PostgreSQL/RustFS/Memgraph，长输入、密码脱敏、重复真实点击、观察保留、PG 重启读回和跨项目拒绝。
- 全部录制/回放/断言/编排回归：`203 passed, 69 skipped`，保留 1 条既有 Starlette/AnyIO 弃用警告。
- 真实修复证据保存在 `p0-recording-draft-verified2`，包括 PG 事件、PG 会话、用例版本、规划证据和截图；最终 `step_count=8`、PG 事件数为 8、`integrity.degraded=false`。
- 没有调用默认 Step 模型；没有把真实 DeepSeek 事件冒充为已写入正式用例或默认 AI 已使用。

仍未完成人工 DeepSeek 业务流程、跨页面/跨域/Shadow DOM 完整清洗、场景/别名/失败定位、默认 AI 使用、正式评审及其余 P0—P5 验收。
