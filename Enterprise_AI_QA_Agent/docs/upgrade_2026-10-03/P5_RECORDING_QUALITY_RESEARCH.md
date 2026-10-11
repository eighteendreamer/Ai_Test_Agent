# UI 录制清洗投影与遗漏诊断研究

基线：`cdd2ce1c`；外部默认 AI 按用户决定暂缓。本轮只处理录制原始事件的确定性质量诊断，不宣称完成 DeepSeek 业务录制、复杂网络关联或默认 AI 使用。

## 现有代码查证

- `PostgresRecordingStore` 的 `ui_recording_event` 追加写且按 `(recording_id, seq)` 幂等；这只能消除重复投递，不能判断两个不同序号是否是用户的真实重复操作。
- `RecorderSessionService._verify_capture_receipt` 已根据 finish receipt、已落库序号和文档范围检查传输完整性；`RecordingGraphStore._check_integrity` 已检查序号间隙和 PG 步数对账。
- `recording_case_draft_service.py` 已把 `page_scan` 等非回放事件放进观察信息，并只把固定回放类型生成步骤；但终态没有保存“观察噪声、重复候选、清洗序号和遗漏状态”的统一派生结果。
- `recorder.js` 实际发出 `click`、`page_response`、`page_scan` 等事件。相邻真实点击可能拥有不同 `recording_scope.local_seq`、时间和 DOM 变化计数，不能把完整事件 JSON 直接作重复判断。

## 方案决定

新增无副作用的 `recording-quality-v1` 派生投影：

1. 原始事件和 PG 行不删除、不更新；`cleaned_event_sequences` 只列可回放事件，观察/未知类型列入 `noise`。
2. 使用事件类型、页面 URL/页面、frame、document 身份、稳定 DOM 定位字段和输入值生成候选指纹，连续相同候选只记录 `duplicate_candidates`，不自动删除，避免误删连续两次真实点击；动态 `recording_scope.local_seq` 不参与指纹。
3. 结合终态 receipt 和事件序号生成 `omission.status`。缺 receipt、缺序号、PG 未确认或 pending/未确认 frame/传输丢失时报告 `missing/unknown`，不猜测遗漏内容。序号完整只代表已发布事件交付完整，`business_capture_complete` 明确为 `unknown`。
4. 终态指标保存该投影；录制转用例草稿携带同一投影，供评审和后续 AI 读取。该投影不是测试通过结论，也不能代替复杂网络、跨文档或 Shadow DOM 关联。

## 实际验证

- 修复前合同测试无法导入质量分析模块，证明该能力尚未存在；原始失败 XML 保留于 `p5-recording-quality-live-before.xml`。
- `test_recording_quality.py`：6 项确定性合同通过，覆盖观察噪声、真实重复动作保留、序号缺口和未确认回执。
- 后续质量合同补充覆盖真实 `recording_scope` 变化、receipt 丢失计数和敏感输入不进入诊断结果；受影响录制回归最终 74 通过、8 跳过、1 个既有弃用警告。
- `test_recording_quality_live.py`：实际 Chromium/CDP、独立 Uvicorn、隔离 MySQL/PG/Redis、真实录制 API 和用例草稿转换通过。两次真实点击得到两个回放序号，质量投影标记同一动作候选但 `removed_event_sequences=[]`；`page_response/page_scan` 被标为观察噪声；原始 PG 事件逐行保持不变；草稿中的质量投影与终态指标一致。证据在 `p5-recording-quality-live-fixed/`。
- 首次真实运行发现稳定指纹错误地包含动态 `recording_scope`，导致真实重复点击未被识别；保留 `p5-recording-quality-live-debug/` 原件，随后只将稳定指纹收窄到 DOM 定位和输入值后通过。这是实际事件驱动的根因修复，不是阈值补丁。

## 当前边界

本轮完成的是确定性清洗投影和基础遗漏诊断。人工 DeepSeek 实测、复杂网络响应关联、跨文档/iframe/Shadow DOM、selector 修复评审、失败定位报告和默认 AI 读取/执行仍未完成；这些不能由本轮受控 HTML 验证替代。

\n

## 2026-10-11 Shadow DOM 响应关联补验

先按现有 `recorder.js`、`RecordingCaptureSettings` 和录制范围测试查证。原实现使用 `MutationObserver.observe(document, {subtree:true})`，能记录 open Shadow DOM 内的点击目标和 `shadow_path`，但 MutationObserver 不会跨越独立的 shadow tree；真实复现 `nested shadow action → DOM update`、动态创建 shadow root 和 iframe 内 shadow root 时，点击事件已写入 PG，而 `page_response.elements=[]`，响应内容丢失。该结论与 [DOM Standard 的 shadow tree 与 mutation observer 定义](https://dom.spec.whatwg.org/#mutation-observers)一致。

修复 `recorder.js`：递归观察已有 open shadow roots；包装 `Element.prototype.attachShadow` 以观察后续创建的 open root；响应文本遍历 host 的 open root；敏感判断从 shadow 节点继续走到 host，防止敏感祖先内容泄露。响应协议保留 `recording-dom-response-v1` 和旧 `scope=document_light_dom` 兼容字段，新增 `shadow_dom=open_roots`、`closed_shadow_dom=unobserved` 能力声明。closed shadow root 不宣称可见。

真实验收 `tests/test_recording_shadow_live.py`：生产 Uvicorn/API、隔离 MySQL/PostgreSQL/Redis/Memgraph、Chromium/CDP、PG 原始事件和草稿读取均参与；nested、动态、iframe 三种响应均进入与点击 local sequence 关联的 `page_response`，敏感 fixture 内容未进入响应或草稿，模型调用为0。修复前原件在 `p5-recording-shadow-before/`，修复后在 `p5-recording-shadow-verified/`；修复后 **1通过22.49秒**。既有录制策略/跨文档/质量/E2E 回归 **17通过、40跳过、4.39秒**，Node 语法检查通过。

本批完成 open Shadow DOM 的确定性响应采集，仍不等同网络请求因果、closed shadow root、selector 自动修复、失败定位报告、默认 AI 使用或 DeepSeek 全业务闭环。
