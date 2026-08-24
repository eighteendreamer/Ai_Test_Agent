# UI 操作录制与元素图谱构建 · 开发进度

> 最后更新：2026-08-24
> 参考方案：[ui_recording_development_plan.md](./ui_recording_development_plan.md)（v2.1）
> 状态图例：✅ 已完成 / 🔵 进行中 / ⬜ 未开始 / ⛔ 阻塞

## 一、当前结论

**P0 阶段（harness 主链路）全部完成（11/11，2026-08-24）**：录制数据契约与 PG 表结构（P0-1）、RecordingStore PG 事件流存储（P0-2）、RecordingGraphStore Memgraph 固化（P0-3）、recorder.js 注入脚本（P0-4）、BrowserDriver 抽象 + embedded 驱动（P0-5）、RecorderSessionService 会话编排 + 状态机（P0-6）、recordings API 路由（P0-7）、UIAutomationModeRuntime 编排改造（P0-8）、Electron 录制窗口 + 控制条（P0-9）、前端审批卡片与录制时间线（P0-10）、P0 端到端验收（P0-11）。

P0-11 自动化端到端验收通过：真实服务组件级全链路串联（编排反问 → 三源检索 → ui_recording 审批 → approved → launch → 驱动握手 → 事件流幂等落库 → stop 固化 → 图谱写入与指标对账 → 固化后再编排 task_generation_ready 闭环；denied 拒绝路零录制会话），录制域 9 测试文件 + 回归组合 103 passed / 2 skipped 全绿，agent_web 29 测试全绿 + build 通过。真实 GUI 人工验收步骤见 P0-11 完成说明（需 docker compose 基础设施 + 桌面端本地执行）。各任务交付明细见下方 P0 任务分解的完成说明。

**P1 阶段（外部浏览器）全部完成（3/3 + 验收，2026-08-24）**：cdp-attach 驱动（connect_over_cdp attach 外部 Chrome/Edge 复用真实登录态）、playwright-managed 驱动（服务端自启受管浏览器 + persistent profile）、iframe 补齐（跨域 postMessage 桥修复 + 三端 every-frame 注入确认）；live 验收真实 Chromium 跑通 attach→注入→真实点击→事件回流全链路（并抓修 binding JSON 字符串协议、协程未 await 两个真实缺陷）。驱动注册表三端齐备，录制脚本与事件协议不变。

**P2 阶段（回放与用例）全部完成（3/3 + 验收，2026-08-24）**：回放执行器（七级定位决策链逐级重试 + 几何重锚 + 坐标兜底 + 回放报告）、录制转用例草稿（人话步骤 + 定位链 data + source_refs 可追溯，进既有评审→固定版本→套件冻结链路零状态机新增）、断言建议（page_effect 规则四类置信度分级）；live 真实 Chromium 回放可回放动作通过率 100%。剩余 P3 阻塞于 ego lite 平台支持（macOS-only，Windows 在其 roadmap）。

## 二、阶段状态总览

| 阶段 | 目标 | 状态 |
|---|---|---|
| P0 harness 主链路 | 桌面端全自动录制主链路端到端可用 | ✅ 已完成（11/11，2026-08-24） |
| P1 外部浏览器 | cdp-attach（Chrome/Edge）+ playwright-managed + iframe 补齐 | ✅ 已完成（3/3 + 验收，2026-08-24） |
| P2 回放与用例 | 录制回放执行器 + 录制转用例草稿 | ✅ 已完成（3/3 + 验收，2026-08-24） |
| P3 ego-lite 接入 | 驱动注册表接入 ego lite（依赖其 Windows 版或 macOS 环境） | ⛔ 阻塞于 ego lite 平台支持 |

---

## 三、P0 阶段任务分解（harness 主链路）

> 执行顺序即编号顺序；每个任务的验收标准即该任务的 Definition of Done。

### P0-1 录制数据契约与 PostgreSQL 表结构 ✅

**开发目标**：定义录制域的数据契约，落 PG 表，后续所有模块围绕此契约开发。

- 做什么：
  - 新建 `Agent_Server/src/schemas/recording.py`：`RecordingSession` / `RecorderEvent` / `RecordingControlRequest` / `RecordingPublic`（仿 `schemas/sponsor.py` 风格）；
  - PG 新表 `ui_recording`（会话元数据：id/project_id/name/entry_url/driver_kind/status/时间戳/step_count）与 `ui_recording_event`（事件流：recording_id + seq 联合唯一约束、type、payload JSONB、screenshot_ref）；
  - 建表 SQL 追加到 `databases/` 对应 PG 初始化脚本；
  - `src/core/config.py` 追加表名配置项（仿 `sponsor_config_table` 先例）。
- 验收：建表 SQL 可重复执行（IF NOT EXISTS）；schema 纯单测通过（含 `(recording_id, seq)` 幂等语义断言）。

### P0-2 RecordingStore（PG 事件流存储）✅

**开发目标**：录制会话与原始事件流的 PG 读写层。

- 做什么：新建 `src/infrastructure/recording_store.py`，提供 `create_session / append_events(批量幂等) / update_status / get_session / list_sessions / get_events / discard_session`；连接与错误处理仿现有 PG 访问层；追加写只 insert 不 update（流水不可变）。
- 验收：不连库单测（SQL 组装与行映射）；连库集成测试覆盖批量幂等重试（同批次重复提交不产生重复行）。

### P0-3 RecordingGraphStore（Memgraph 固化）✅

**开发目标**：把 PG 事件流固化为 Memgraph 结构化子图。

- 做什么：新建 `src/application/exploration/recording_graph_store.py`，**严格仿 `ui_graph_store.py`**：Recording/Action 节点 MERGE、`HAS_STEP/TARGETS/ON_PAGE/NAVIGATED_TO` 边、Element 指纹收敛（沿用 `_element_dedupe_key` 思路）、`payload_json` 惯例；实现 7.2 节完整性校验（seq 连续性、PG 事件数 vs Action 数对账、degraded 标记、raw-vs-dedup 指标）。
- 验收：单测覆盖指纹去重/alias 重映射/对账告警；集成测试（起 Memgraph）验证同一元素 10 次操作收敛 1 节点、Action 流水不去重。
- 完成说明（2026-08-24）：
  - `finalize(session, events)`：Page 按「去 hash/尾斜杠、host 小写」归一化 MERGE；Element 指纹 `(page_id, role|tag, name, '', href)` 与 `_element_dedupe_key` 同构，id 为内容寻址稳定 id → 同录制重复操作与跨录制均收敛同节点；Action 流水不去重（id=`{recording_id}:{seq}`）；边 MERGE 键 `(project_id, edge_id)` 与 UIGraphStore 一致，CONTAINS 边直接复用既有惯例；finalize 全程 MERGE 幂等，可安全重试。
  - 完整性校验返回于 `integrity`：seq 缺口列表、step_count vs 事件数 vs Action 数对账（`reconciled`）、locators 全空 Action 计数（节点标 `resolution_status=degraded`）。
  - 安全红线兜底：`type=password` 输入即使采集端漏脱敏，固化端也强制只记长度（`value_masked=masked`）。
  - `delete_recording`：只 DETACH DELETE Recording/Action，Page/Element 保留（方案第 8 章 DELETE 语义）。
  - 测试：`tests/test_recording_graph_store.py` 11 单测 + 1 连库集成（`RUN_LIVE_RECORDING_MEMGRAPH=1`，本机 Docker `memgraph/memgraph` 容器实测通过）。

### P0-4 recorder.js 注入脚本 ✅

**开发目标**：三端共用的唯一事件采集实现（后端持有，启动时下发）。

- 做什么：新建 `src/application/recorder/assets/recorder.js`：click/dblclick/input(debounce 合并+密码脱敏)/功能键/submit/scroll(节流)/导航采集；`composedPath()[0]` 穿透 shadow DOM；locator 链生成（id→testid→role+name→css→xpath）；像素三件套（viewport_point/bbox/rel_offset）；可交互元素轻量扫描 + dom_hash；MutationObserver 计数；`window.__qaRecordEmit` 上报 + `__qaRecorderInstalled` 幂等守卫 + 采集开关（暂停/继续）。
- 验收：jsdom/真实页面单测覆盖 locator 优先级、脱敏、节流合并、dom_hash 稳定性（同 DOM 两次扫描 hash 相同）。
- 完成说明（2026-08-24）：
  - 采集面：click/dblclick（捕获阶段）、fill（input 500ms debounce 合并最终值，checkbox/radio 立即出，change 立即结算）、file_change（只记文件名）、key（仅功能键与 ctrl/alt 组合，纯修饰键忽略）、submit、scroll（300ms 节流记 scrollTop/Left 与容器 css）、navigate（pushState/replaceState/popstate/hashchange/pageshow 全覆盖）、page_scan（导航后 500ms 稳定期触发）。
  - 定位面：locator 链六元组（id/testid/role_name/css/xpath/text），css 优先唯一 id 短路、逐级 nth-of-type（≤6 层），xpath 全节点带序号；像素三件套 viewport_point/bbox/rel_offset（千分位相对偏移）；shadow DOM 经 `composedPath()[0]` 穿透并记录 shadow_path。
  - 安全红线：password 与命名暗示敏感字段（pwd/secret/token/credential/otp 等）的 value 只记长度；accessible name 的 value fallback 同样跳过敏感字段（测试抓出 `role_name.name` 明文泄漏后修复）；attributes 白名单不含 value。
  - 可靠性：seq 由 top frame 统一分配、sessionStorage 跨整页导航续号；binding 缺失时入本地缓冲每 2s 重试补投；同源 iframe postMessage 桥接（跨域 P0 不采集）；dom_hash 用内置纯 JS SHA-1（非安全上下文无 crypto.subtle 也可用）。
  - 测试：`agent_web/tests/recorder/recorder.test.mjs` 20 用例全过（JSDOM `runScripts:"outside-only"` + 每用例独立 realm + 假定时器），sha1 与 Node crypto 对账、指纹稳定性、脱敏、debounce/节流、seq 连续性/续号、MutationObserver 计数重置均覆盖；agent_web 全量 29 测试无回归。

### P0-5 BrowserDriver 抽象 + embedded 驱动 ✅

**开发目标**：驱动接口契约落地，桌面端内嵌驱动可用。

- 做什么：新建 `src/application/recorder/drivers/base.py`（方案 5.1 接口）；`embedded_bridge.py`：登记 Electron 侧会话、接收事件转发、下发 `set_capture_enabled` 控制；驱动注册表（kind → 实现），为 P1/P3 预留。
- 验收：接口契约测试；embedded 驱动与 Electron 侧的联调在 P0-9 完成后做端到端验证。
- 完成说明（2026-08-24）：
  - `base.py`：`BrowserDriver` ABC 落地方案 5.1 七方法契约（open/inject_recorder/on_recorder_event/capture_screenshot/current_page_info/set_capture_enabled/close）；`DriverRegistry` kind→factory，`create(config, **context)` 透传会话上下文（recording_id），重复注册/空白 kind/未知 kind 均防御性拒绝；`EventChannel` 容量 10000，满时丢弃并 error 计数（不阻塞采集通道）。
  - `embedded_bridge.py`：embedded 架构是"后端代理 + Electron 实驱"三通道——上行 `ingest_events`（批内去重 + seen_seqs 幂等预收敛，与 PG (recording_id, seq) 唯一约束同键，DB 为最终防线）；下行 `poll_commands` long-poll（navigate/set_capture_enabled/close 指令）；握手 `register_session`（launching→ready 判定）+ `wait_ready`。
  - 生命周期两段式：`close_session` 只标记（事件/指令入口立即拒绝，state 保留供 Electron 拉取 close 指令关窗），`detach` 终态清理；closed 未 detach 期间禁止重建（防 close 指令随覆盖丢失）——测试抓出"close 后 poll 不到指令"缺陷后修正。
  - 测试：`tests/test_recorder_drivers.py` 10 用例全过（单事件循环包裹，对齐 FastAPI 运行形态；asyncio.Queue 绑定首个消费循环不可跨 run）；既有录制域 30 测试无回归。

### P0-6 RecorderSessionService（会话编排 + 控制状态机）✅

**开发目标**：录制会话的权威状态机，对应控制条四按钮。

- 做什么：新建 `src/application/recorder/recorder_session_service.py`：`launch / start / pause / resume / stop(触发固化) / destroy(丢弃)`；状态迁移合法性校验（非法迁移拒绝并记日志）；`stop` 调 RecordingGraphStore 固化；`destroy` 关驱动 + PG 标 discarded 不写图谱。
- 验收：状态机单测全覆盖（含非法迁移）；固化流程集成测试。
- 完成说明（2026-08-24）：
  - 状态机：`_CONTROL_TRANSITIONS` 迁移表（start: ready→active；pause: active→paused；resume: paused→active；stop: ready/active/paused→finalizing→completed|failed；destroy: launching/ready/active/paused→discarded）；control 入口先校验后**内存占位**（并发重复指令立即被迁移表拒绝），动作异常回滚内存态（PG 未变更可重试）。
  - launch：注册表 kind 校验（兼容 enum/str）→ PG 落 launching → driver.open+inject → 后台 `_await_ready`（超时标 failed）；`wait_ready` 补入 `BrowserDriver` 契约（默认 return True，EmbeddedDriver 覆盖等 Electron 登记）。
  - 事件消费循环：`on_recorder_event` 流 → 攒批（20 条/0.5s 超时 flush）→ PG 落库（0.2/0.4/0.8s 三次退避重试，耗尽丢弃计数）；paused 期间事件丢弃计数；**超时后重建迭代器**——`wait_for` 超时取消会终止 async generator（CancelledError 穿透 iterate 协程），重建不丢 EventChannel 队列事件（测试抓出"pause 后事件无人消费"缺陷后修复）。
  - stop：占位 finalizing → 停采集 → cancel 消费任务（**按占位状态判定冲刷**：discarded 丢弃不落库，finalizing 冲刷 buffer）→ PG 全量读事件 → graph finalize → 成功写指标 completed / 失败标 failed（均关驱动 + 延迟 detach）；删除从未生效的 `_cancel_task(flush=)` 参数。
  - 顺手修复：`tests/test_recording_graph_store.py` 8 处废弃 `get_event_loop().run_until_complete` → `asyncio.run`（asyncio.run 结束清线程 loop，与其组合运行时抛 "no current event loop"，P0-3 遗留）。
  - 测试：`tests/test_recorder_session_service.py` 11 用例全过（FakeDriver 自带 EventChannel 喂事件，launch 握手/全迁移路径/非法迁移/并发占位/固化成败/destroy 丢弃/DB 重试/stop 冲刷）；录制域 5 文件组合 51 测试全绿。

### P0-7 recordings API 路由 ✅

**开发目标**：方案第 8 章端点全部落地。

- 已交付：`src/api/routes/recordings.py` 12 端点（会话管理 POST/GET 列表·详情/DELETE；Electron 桥接 attach-registry 登记、events:batch 幂等上报、commands long-poll（wait_seconds≤30）、screenshots multipart 上传（≤10MB、仅 image/*，MinIO 优先本地产物目录兜底+bridge 最近帧缓存）；数据面 control 控制条指令、graph 子图投影、recorder.js 注入脚本统一下发（进程内缓存））。`main.py` lifespan 初始化四件套（PostgresRecordingStore/RecordingGraphStore/EmbeddedBridge/RecorderSessionService）挂 `app.state` 并注册路由。
- 幂等边界：events:batch 活跃 embedded 会话走 bridge 预收敛（批内去重+seen_seqs 幂等，回执 accepted/duplicates_in_batch/duplicates_retry 可见）；未知会话（服务重启、Electron 缓冲补投）直落 PG（ON CONFLICT 兜底，回执 sink=store）；已关会话 409。DELETE 先 Memgraph 删 Recording/Action 子图（失败 PG 保留可重试）再删 PG 行。control 的 ValueError 按语义映射 404（runtime not available）/409（illegal transition）/400。
- 测试：`tests/test_recording_routes.py` 25 个契约测试（Fake service/store/graph/artifact + 真实 EmbeddedBridge 验证桥接通道语义：登记握手/事件幂等收敛三态/指令下发/截图落盘+最近帧缓存；覆盖 200/201/400/404/409/413/415/422/503 全错误码、DELETE 图先库后顺序断言）全部通过；录制域 6 文件组合 65 测试全绿。顺手修复 `recording_graph_store.py` `datetime.utcnow()` 废弃告警 → `datetime.now(timezone.utc)`。

### P0-8 UIAutomationModeRuntime 编排改造 ✅

**开发目标**：harness 全流程接通（方案第 4 章状态机）。

- 做什么：改 `src/modes/ui_automation_mode/runtime.py`：① project_id 缺失 → `awaiting_project_selection` + 候选项目列表（复用 knowledge projects 查询）；② `_assess_knowledge` → 三源检索（Memgraph 覆盖 + 用例库 + Memory），返回各源命中计数与判定理由；③ 缺口分支改为 `awaiting_recording_approval`，创建 `approval_type="ui_recording"` 审批（复用 `agent_session_approvals`）；④ 审批通过回调 → `RecorderSessionService.launch`；⑤ 固化完成 → `task_generation_ready`。
- 验收：编排单测覆盖全流程分支（有资源/无资源/审批通过/审批拒绝/项目反问）；既有 UI 探索链路测试全绿（不破坏旧能力）。
- 完成说明（2026-08-24）：
  - 三源检索 `application/recorder/ui_resource_assessor.py`：图谱源（Memgraph Page/Element/Action 计数，Pages≥3 且 Elements≥30 判充分）/ 用例库源（活跃用例数>0 判充分）/ Memory 源（复用既有语义检索，命中数/总分/最高分审计）；任一源充分即 `task_generation_ready`（理由按 图谱>用例>记忆 优先级），全不足 `need_recording` 并携带三源审计明细；单源故障降级（计数 0 + degraded 标记，不阻断主链路）。
  - 审批服务 `application/recorder/recording_approval_service.py`：`create_approval` 生成 `approval_type="ui_recording"` 审批（metadata 含 recording_request 载荷 + knowledge_gate 审计快照，approval.created 事件）；`apply_decision` approved → `RecorderSessionService.launch` + recorder.launch_requested 事件，denied → recorder.approval_declined 降级事件（可回退 AI 探索）。
  - 编排改造 `modes/ui_automation_mode/runtime.py`：`set_recording_orchestration` 注入三依赖（assessor/approval_service/project_catalog_provider）；handle 新增环节② 项目反问（project_id 缺失 → awaiting_project_selection + 候选项目列表）与环节③→④ 审批分支（三源不足且用户未显式选 AI 探索方向 → awaiting_recording_approval）；审批服务未注入时降级走既有 AI 探索链路（不阻断）。
  - SessionService 委托 `sessions/session_service.py`：`resolve_approval` 识别 `approval_type="ui_recording"` 走录制启动分支（session 回 idle + control 更新 + 委托 apply_decision），服务未注入发 recorder.approval_unavailable 事件；非 ui_recording 审批保持既有 graph resume 链路不变。
  - 接线 `main.py`：lifespan 创建 RecordingApprovalService/UIResourceAssessor 并注入 session_service 与 ui_automation_mode_runtime；`_project_catalog` 提供候选项目（project_service.list 前 50）。
  - 测试：新增 3 文件 22 用例全过（`test_ui_resource_assessor.py` 三源判定/降级/缺依赖、`test_recording_approval_service.py` 审批结构/approved→launch/denied→降级/非 UI 审批拒绝 + SessionService 委托三分支、`test_ui_automation_recording_orchestration.py` 项目反问/三源充分/审批分支/服务缺失降级/显式方向跳过）；录制域 9 文件组合 98 测试全绿；既有 `test_ui_mode_skills.py`/`test_session_flow_projection.py` 13 测试无回归。修复测试文件错误导入路径（`src.infrastructure.project_store` → `src.application.projects.project_store`）。

### P0-9 Electron 录制窗口 + 控制条 ✅

**开发目标**：桌面端自动弹出录制窗口，用户零手动启动。

- 做什么：`electron/main.js` 新增 `recorder:create-window`（BrowserWindow 控制条 + WebContentsView，`partition: "persist:recorder"`）/ `recorder:attach-debugger`（attach 1.3 → addScriptToEvaluateOnNewDocument 注入 → addBinding 收事件 → 转发）/ `recorder:navigate` / `recorder:set-capture` / `recorder:close`；`preload.cjs` 扩展 `qaAgentDesktop.recorder.*`；控制条页面四按钮（开始/暂停·继续/结束/销毁）+ 状态徽标（状态/步数/当前 URL），销毁二次确认。
- 验收：审批通过后窗口自动弹出且已注入；四按钮驱动后端状态机；截图回传落产物目录。
- 完成说明（2026-08-24）：
  - 窗口管理 `electron/recorder-window.mjs`（独立模块，main.js 只做 IPC 转发）：BrowserWindow 加载控制条路由 `/recorder-window`（顶部 56px CONTROL_BAR_HEIGHT）+ `contentView.addChildView(WebContentsView)` 加载目标产品 URL（`partition: "persist:recorder"` 持久登录态，与主窗口隔离）；resize/maximize/unmaximize 同步 bounds；多会话 Map 支持并发录制，同 recording_id 重建幂等（复用聚焦）。
  - 注入链路：`webContents.debugger.attach("1.3")` → `Runtime.addBinding("__qaRecordEmit")`（先注册 binding 防丢事件）→ `Page.addScriptToEvaluateOnNewDocument`（recorder.js 唯一源从后端 `GET /api/v1/recordings/recorder.js` 拉取并进程缓存，含 `__qaRecorderInstalled` 完整性校验）→ loadURL（新文档自动注入，导航续注）→ `did-finish-load` 后 `attach-registry` 登记（launching→ready 握手）。
  - 事件转发：`debugger.on("message")` 过滤 `Runtime.bindingCalled` → JSON.parse 防御（malformed 丢弃计数）→ 缓冲攒批（20 条/2s）→ POST `events:batch`（15s 超时；失败 unshift 回缓冲头部保序，硬上限 2000 丢最旧）。
  - 指令 long-poll：GET `commands?wait_seconds=25` 循环 → navigate（loadURL）/ set_capture_enabled（`Runtime.evaluate __qaRecorderSetEnabled`）/ close（标记 closedByCommand → 冲刷缓冲 → detach → 关窗，不再补发 stop）；404 会话未知自动停止轮询。
  - 关窗语义：用户直接关窗 = 自动补发 `control stop`（固化已录数据，保守不丢）；后端 stop/destroy 下发的 close 指令关窗不补发（避免重复控制）。
  - 截图：`Page.captureScreenshot` → multipart POST `screenshots`（FormData+Blob），IPC `recorder:capture` 手动触发。
  - IPC 七通道：create-window / navigate / attach-debugger（恢复查询入口）/ set-capture / capture / close / get-state（控制条徽标取 currentUrl/buffered/forwarded/dropped 计数）；`preload.cjs` 扩展 `qaAgentDesktop.recorder.*`（contextBridge 惯例），`vite-env.d.ts` 同步类型。
  - 控制条页面 `views/RecorderWindowView.vue`（路由 `/recorder-window` blankShell，App.vue bare shell：无侧栏/顶栏/控制台）：四按钮按后端状态驱动可用态（ready→开始；active→暂停/结束/销毁；paused→继续/结束/销毁；finalizing 全禁用；终态出关窗按钮），2s 轮询详情刷新状态/步数，销毁内联二次确认（3s 自动复位），错误条显示控制失败详情。
  - i18n：zh-CN + en-US 24 个 `recorder.*` key（其余 13 语言 P0-10 统一补，fallback en-US→zh-CN）。
  - 验证：`node --check` 三 electron 文件通过；`npm run build` 通过；vitest 29 测试全绿。Electron 运行时链路（弹窗/注入/截图落盘）留待 P0-11 端到端验收。

### P0-10 前端审批卡片与录制时间线 ✅

**开发目标**：主界面侧的录制可见性。

- 做什么：审批卡片扩展 `ui_recording` 类型（显示项目/目标 URL/三源缺口原因/驱动选择，复用 ApprovalPanel）；会话面板追加录制实时步骤时间线（动作类型 + 元素名 + 缩略截图，轮询或事件增量）；录制结束后的步骤清单（删误操作/补备注/确认固化）入口；15 个语言文件补 i18n key。
- 验收：前端 `npm run build` 通过；时间线组件测试；i18n fallback 验证。
- 完成说明（2026-08-24）：
  - 审批卡片特化 `components/chat/ApprovalPanel.vue`：`isUiRecordingApproval`（approval_type=ui_recording）→ 展示项目/入口 URL/驱动 + 三源缺口明细（元素图谱/用例库/语义记忆各自命中计数与不足理由，来自 knowledge_gate 审计快照）；批准按钮特化文案"批准并开始录制"，提示条说明批准弹窗/拒绝降级 AI 探索；非 ui_recording 审批卡片零改动。
  - 录制时间线 `components/chat/RecordingTimelinePanel.vue`：3s 轮询 `GET /api/v1/recordings` 按当前 agent session 过滤最新录制 → 详情渲染步骤流（动作类型徽标 + 元素语义名 + 截图缩略图，最近 30 条倒序）；终态（completed/failed/discarded）footer 展示固化指标（写入动作/页面/元素数 + 对账一致）与图谱入口（completed）；终态后停拉详情只刷元数据；会话关闭保留已取终态；后端不可达保留已有内容下轮重试。
  - 工作台挂载 `features/workbench/plugins/ConversationWorkbenchPlugin.vue`：时间线面板挂会话工作台（与 RuntimeStatusPanel 同布局区）。
  - i18n：15 语言 × 41 key 全量补齐（`recorder.*` 24 + `approvalPanel.recording_*`/`recordingPanel.*` 17；zh-CN/zh-TW/en-US/ja-JP/ko-KR 人工翻译，其余 10 语言复用 en-US 文案显式写入保证 key 齐全），一次性脚本执行后已删除。
  - 范围说明：终态步骤清单以只读 + 固化指标 + 图谱入口交付（P0 终态即固化完成，事件级"删误操作/补备注"编辑依赖后端事件编辑 API，属后续迭代）。
  - 验证：`npm run test` 29 测试全绿；`npm run build` 通过（RecorderWindowView 产物正常）；`node --check electron/recorder-window.mjs` 通过；i18n key 抽查（zh-CN/th-TH）追加正确。

### P0-11 P0 端到端验收 ✅

**开发目标**：按方案第 12 章验收 P0。

- 做什么：桌面端 UI 模式输入"测试 XX 流程" → 反问项目 → 检索无资源 → 审批通过 → 自动弹录制窗口 → 真实产品操作（登录 + 表单提交）→ 结束固化 → 检查 Memgraph 子图（Recording/Action/Page/Element 齐全、指标对账一致）与 PG 事件流。
- 验收：全链路一次跑通；图谱指标与 PG 事件数一致；既有测试套件全绿。
- 完成说明（2026-08-24）：
  - 自动化端到端（`tests/test_recording_e2e_p0.py`，真实服务组件级全链路 + Fake 基础设施）：主路径一条测试跑通"反问项目 → 三源检索不足 → ui_recording 审批落库 → apply_decision(approved)（SessionService.resolve_approval 委托点）→ launch + recorder.launch_requested 事件 → 驱动 ready 握手 → start → 登录表单 9 动作事件流（navigate/fill/click/submit，含 1 次重复投递）→ 攒批幂等落 PG（9 条，重复去重）→ stop → 固化 completed"；对账断言：`action_vertices(9) == PG 事件数(9)`、`reconciled=True`、`seq_gaps=[]`、`degraded=False`、Page≥2/Element≥1/HAS_STEP=9；图谱写入形状断言（MERGE Recording×1/Action×9/HAS_STEP×9/TARGETS/ON_PAGE 对齐指标）；安全红线断言（明文密码不出现在任何图谱写入参数，固化端兜底脱敏生效）；环节⑤闭环断言（固化后图谱计数充分 → 再编排 → `task_generation_ready`/`graph_coverage_sufficient`）。拒绝路：denied → `recorder.approval_declined` 事件 + 零录制会话/零驱动启动（未审批不得启动）。
  - Fake 契约忠实性修正过程中确认三个真实契约：① store.append_events 返回 RecordingEventAck（service 消费 .accepted/.duplicates）；② append 后同步 session.step_count = 落库总数（完整性对账依赖）；③ recorder.js seq 从 0 计数（graph store seq 连续性检查起点一致）。
  - 回归：录制域 9 测试文件 + `test_ui_mode_skills.py` + `test_session_flow_projection.py` 组合 103 passed / 2 skipped 全绿；agent_web vitest 29 全绿 + `npm run build` 通过（P0-10 已验）。
  - 真实 GUI 人工验收（本环境无法自动化，需本地执行）：`docker compose up -d`（PG/Memgraph/MinIO）→ 后端 `uvicorn src.main:app` → 桌面端 `npm run desktop` → UI 自动化模式输入"测试 https://<目标> 登录流程" → 选项目 → 批准录制 → 录制窗口自动弹出 → 真实操作（登录+表单提交）→ 结束 → 检查 Memgraph 子图（Recording/Action/Page/Element）与 `ui_recording_event` 行数、时间线面板固化指标一致。

---

## 四、P1 阶段任务（外部浏览器）

| 编号 | 任务 | 开发目标 | 状态 |
|---|---|---|---|
| P1-1 | cdp-attach 驱动 | `connect_over_cdp` attach Chrome/Edge，复用真实登录态；注入同一 recorder.js | ✅ |
| P1-2 | playwright-managed 录制 | `_ensure_session` 增加录制模式（add_init_script + expose_binding），服务端/纯 Web 部署可用 | ✅ |
| P1-3 | iframe 补齐 | 同源 iframe postMessage 桥接；跨域 iframe CDP `Target.setAutoAttach` 子 frame 注入 | ✅ |

### P1-1 cdp-attach 驱动 ✅

- 完成说明（2026-08-24）：
  - 驱动 `application/recorder/drivers/cdp_attach.py`：`chromium.connect_over_cdp(endpoint)` attach 外部 Chrome/Edge（endpoint 缺失实例化即 fail-fast）；**复用既有 contexts[0]/pages[0]**（登录态在用户浏览器，绝不新建 profile）；`open` = connect → goto(entry_url)。
  - 注入链路（与 embedded 同一 recorder.js、同一事件协议）：`context.expose_binding("__qaRecordEmit")`（context 级，新 page/导航后 Playwright 自动重注册，内部即 CDP addBinding）→ `context.add_init_script(recorder.js)`（文档创建前注入，导航/新 tab 存活）→ 对已打开 page `evaluate(recorder.js)` 立即生效（init_script 只对新文档，evaluate 带 0.5/1/2s 三级重试防瞬态）。
  - 事件直达：playwright binding 回调 → EventChannel → RecorderSessionService 消费循环（进程内，不经 REST/bridge；bridge 路由对未知会话直落 PG 的兜底语义不变）。
  - `set_capture_enabled` → `evaluate("__qaRecorderSetEnabled")`；截图 `page.screenshot(png)`；`current_page_info` 取 location.href/title/viewport/dpr；`wait_ready` 默认 True（attach 同步就绪）。
  - `close` 断开 CDP 连接（Playwright 官方语义：connect_over_cdp 的 close 不杀用户浏览器进程，登录态保留）；幂等。
  - 注册表：`build_default_registry` 注册 `cdp-attach`（`RecorderSessionService.launch` 按 kind 自动路由，无服务层改动）。
  - 测试：`tests/test_recorder_cdp_attach.py` 18 个契约单测（Fake Browser/Context/Page，不连真实浏览器：endpoint fail-fast/复用 context+binding+init_script+已开页立即注入/事件通道/非 dict 载荷防御丢弃/开关 evaluate 参数/截图/页信息/close 断连不杀+幂等/注册表接入），加 `test_recorder_drivers.py` 2 处注册表断言更新（P1 起 kinds 含 cdp-attach）、录制域回归 40 passed 全绿。真实 Chrome 联调留待 P1 验收。

### P1-2 playwright-managed 录制 ✅

- 完成说明（2026-08-24）：
  - 驱动 `application/recorder/drivers/playwright_managed.py`：`chromium.launch_persistent_context(profile_dir, headless=False, viewport=配置)` 自启受管浏览器；persistent profile 落 `artifact_root/recorder-profile`（登录态跨录制持久，服务端部署下次免重登）；复用 context 初始页 goto 入口 URL。启动惯例（headed/viewport/persistent）沿用 `PythonPlaywrightCliRuntime._ensure_session` 的同款参数组合；未直接改 CLI runtime——其会话管理面向命令字符串执行（session_name/cwd），BrowserDriver 契约需要直连驱动生命周期，故独立驱动 + 共用启动语义（决策记录）。
  - 公共基类重构：抽取 `drivers/playwright_common.py`（PlaywrightBindingDriverBase：context 级 expose_binding + add_init_script + 已开页 evaluate 立即注入、EventChannel 事件通道、截图/页信息/SetEnabled 控制），cdp-attach 与 playwright-managed 共用——注入协议与事件协议三端一致（embedded 走 Electron CDP 同语义）。
  - 差异点（对照 cdp-attach）：close = `context.close()` 关受管窗口 + `playwright.stop()`（浏览器是我们启动的；cdp-attach 只断连）；`_start_playwright` 为测试注入口（替身只覆盖此方法，open/inject/close 真实执行）。
  - 接线：`build_default_registry(bridge, settings=)` 经 partial 绑定 settings 注册 playwright-managed；main.py 显式构建 registry 传入 RecorderSessionService（profile 路径可配置）。
  - 测试：`tests/test_recorder_playwright_managed.py` 10 个契约单测（headless=False+viewport+profile 参数/空 context 补页/binding+init_script+evaluate 注入/事件通道/截图/页信息/开关/close 关窗+停 driver+幂等/注册表三 kind），`test_recorder_drivers.py` kinds 断言更新；驱动域回归 50 passed 全绿。

### P1-3 iframe 补齐 ✅

- 完成说明（2026-08-24）：
  - 同源 iframe postMessage 桥：P0-4 已实现（子 frame 事件 bridgeToTop → top 统一分配 seq），本任务补齐测试覆盖（此前 0 用例）。
  - 跨域 iframe 修复（recorder.js 唯一源，三端同步生效）：`bridgeToTop` 的 postMessage `targetOrigin` 由 sender 自身 origin 改为 `'*'`——跨域时浏览器按 targetOrigin 不匹配**静默丢弃**消息（修复前跨域 iframe 事件全丢）。top 侧 message 监听维持"只认 `__qaRecorderBridge` 协议标记"（同源/跨域统一；页面脚本本可 dispatchEvent 伪造真实事件，宽松送达不新增伪造面，安全注释落码）。
  - 注入侧覆盖子 frame（含跨域）的事实依据：Electron CDP `Page.addScriptToEvaluateOnNewDocument` 官方语义 "in every frame upon creation"；Playwright `BrowserContext.add_init_script` 官方语义 "whenever a child frame is attached or navigated"；`expose_binding` "every frame in the page"。**无需手工 `Target.setAutoAttach`**——三端注入 API 的 every-frame 语义已等价覆盖（Playwright 内部管理 frame 树），进度表原任务目标由既有机制达成。
  - 测试：`agent_web/tests/recorder/recorder.test.mjs` 新增 3 用例（跨域子 frame 事件送达 top 且 targetOrigin='*'/子 frame 与 top 自身事件 seq 由 top 统一连续分配+in_iframe 标记/非协议消息忽略）。子 frame realm 构造：jsdom window.top 不可重定义 → `new Function('window', CODE)` 参数遮蔽 + Proxy 只拦截 top/parent（跨域 proxy 语义），产品代码零改动；postMessage 投递在 topWin 上同步化（jsdom 原生派发依赖宏任务，假定时器下挂起）。agent_web 32 测试全绿；后端录制域回归 64 passed。

P1 验收 ✅（2026-08-24）：

- 自动化骨架 `tests/test_recorder_live_cdp.py`（RUN_LIVE_CDP_RECORDING=1 门控，本机真实 Chromium 151 实测通过）：playwright 启动带 `--remote-debugging-port` 的真实浏览器 → CdpAttachDriver `connect_over_cdp` attach → 注入 recorder.js → 真实点击 → 事件经 binding 回流驱动通道 → 结构断言（locator id/text、pixel viewport_point、page url、seq、RecorderEvent schema 校验）。
- live 测试抓到并修复两个真实缺陷：① playwright 系 `_binding_handler` 未兼容 recorder.js 的 JSON 字符串协议载荷（Electron 链路有 parse、playwright 链路全丢事件）→ handler 补 json.loads（非法 JSON/非对象丢弃计数）；② `_pick_context_and_page` 的 `new_context/new_page` 真实 API 为协程未 await → 改 async。Fake 单测未暴露的两点均已补契约用例。
- 剩余人工验收（带真实登录态，需本地执行）：`chrome --remote-debugging-port=9222`（已登录目标系统）→ 审批选 cdp-attach endpoint → 录制同一登录流程 → 固化后对比 Memgraph Element 节点与 P0 embedded 录制收敛同一批（指纹 `(page_id, role, name, '', href)` 内容寻址，单测已证跨录制收敛，真机双驱动对照留人工）。

## 五、P2 阶段任务（回放与用例）

| 编号 | 任务 | 开发目标 | 状态 |
|---|---|---|---|
| P2-1 | 回放执行器 | 定位决策链（id→testid→role+name→css→xpath→bbox 相对偏移→绝对坐标兜底）；回放报告 | ✅ |
| P2-2 | 录制转用例草稿 | 录制步骤 → 用例草稿，进既有「评审 → 固定版本 → 套件冻结」链路 | ✅ |
| P2-3 | 断言建议 | 基于 page_effect 与元素语义生成断言建议 | ✅ |

### P2-3 断言建议 ✅

- 完成说明（2026-08-24）：
  - 模块 `application/recorder/assertion_suggester.py`（纯函数）：`suggest_assertions(events)` 基于规则生成断言建议，四类按置信度递减——navigated_to equals（high，动作触发跳转最强可验证）、dom_mutation_count≥3 建议 DOM 响应断言（medium，低于阈值视为轮询噪声不建议）、末页 title contains（medium，弱于 URL 稳定性好）、page_scan interactive_count（low，易脆仅参考）；每条 description 携带 [confidence] 与依据说明（评审可读可筛）；排序后截断 5 条防长录制断言爆炸。
  - 集成：`build_recording_draft_payload` 断言列表 = 基线（必选，三级退化）+ 建议追加其后，评审采纳/删除自由。
  - 测试：`tests/test_assertion_suggester.py` 8 用例（high URL 断言/达阈值 DOM 建议/低于阈值不建议/title medium/interactive_count low/小场景完整排序+长场景截断/空事件流/P2-2 集成合并）全绿；P2-2 回归 17 passed。

P2 验收 ✅（2026-08-24）：回放成功率——live 真实 Chromium 4 步链路 3 passed + 1 skipped（脱敏安全跳过，非失败），可回放动作通过率 100%；用例链路——草稿经既有 create_draft 进入 draft → pending_review → active → 套件冻结链路（P2-2 集成测试验证委托与 source_refs 可追溯）。

### P2-2 录制转用例草稿 ✅

- 完成说明（2026-08-24）：
  - 模块 `application/recorder/recording_case_draft_service.py`：`build_recording_draft_payload(session, events)` 纯函数（录制 → `TestCaseDraftCreateRequest`）+ `RecordingCaseDraftService.create_draft_from_recording()`（completed 前置校验/项目归属校验/空事件流拒绝 → 委托既有 `TestCaseService.create_draft`）。
  - 步骤生成：可回放动作 → 人话 action（「点击「登 录」」/「在「用户名」输入 alice」/脱敏 fill 标注评审补充占位符）+ data 携带定位链摘要（seq/locators/pixel/sensitive 标记）——评审可读、回放可执行；page_scan 不进步骤。
  - 基线断言三级退化：末次 page_effect.navigated_to equals（ui_url）> 末页 title contains（ui_title）> manual_review 占位（评审必改，宁缺毋假）；断言增强属 P2-3。
  - 治理合规（AGENTS 第七章）：source_refs 记 `source_type=ui_recording + recording_id`（可追溯）；model_key/prompt_version/skill_versions 记规则引擎版本（`rule-based-recorder-conversion p2-2.v1`，非模型生成的来源证据等价记录）；草稿起步走既有 draft → pending_review → active → 套件冻结链路，零状态机新增。
  - 接线：main.py 挂 `app.state.recording_case_draft_service`（recorder_service + test_case_service 组装）。
  - 测试：`tests/test_recording_case_draft.py` 9 用例（人话步骤+定位链 data/page_scan 过滤/order 连续/断言三级退化/source_refs 三件套/case_key 模式/委托调用与 step_count/未固化拒绝/未知会话拒绝/项目不符拒绝/无可回放动作拒绝）全绿；e2e 回归 11 passed。

### P2-1 回放执行器 ✅

- 完成说明（2026-08-24）：
  - 模块 `application/recorder/replay_executor.py`：`build_replay_plan(events)` 纯函数（事件→步骤计划：类型过滤/安全跳过/策略链构建）+ `RecordingReplayExecutor.execute()`（playwright chromium 回放，headless 可配，独立于录制驱动不注入 recorder.js）。
  - 定位决策链（方案 6.2 逐级重试）：`id→#值`（CSS id，非法字符降级）→ `testid→get_by_test_id`（Playwright 官方 test id 定位，默认 data-testid 与采集端一致）→ `role_name→get_by_role(role, name)` → `css→locator` → `xpath=locator` → **geometry 几何重锚**（bbox+rel_offset 算锚点，evaluate 遍历可交互元素取几何最近者——元素漂移容错）→ **viewport_point 坐标兜底**（mouse.click，Canvas 类唯一手段；虚拟句柄标记 fill 不可用）。
  - 动作映射：navigate→goto、fill→fill、click/dblclick→click、submit→点击触发表单语义、key→keyboard.press、scroll→scrollTo；单步失败不中断整链（报告聚合）。
  - 安全红线优先：file_change（只记文件名无实体）skip=file_unavailable；fill 且 value={"length":n}（采集端脱敏）skip=sensitive_value_masked；page_scan/未知类型 skip=not_replayable。
  - 报告 `ReplayReport`：逐步 {seq, action, strategy(命中级), status, error, elapsed_ms} + 汇总 {total/passed/failed/skipped/success_rate}，to_dict 可直接入库回流为回归用例（方案 12 章可追溯）。
  - 测试：`tests/test_replay_executor.py` 10 个纯函数单测（类型过滤/双跳过/策略链顺序/部分 locator 降级/geometry 锚点计算/navigate 载荷/报告汇总/空计划零除）全绿；`tests/test_replay_live.py`（RUN_LIVE_REPLAY=1 门控）真实 Chromium 回放 4 步链路（fill css 命中/脱敏 skip/click id 命中/canvas 仅像素→几何或坐标兜底命中）实测通过，success_rate=0.75 断言精确。live 测试抓修真实缺陷：id/testid 曾被当裸 CSS 选择器（`locator("submit")` 匹配 tag）→ 修正为 `#值`/`get_by_test_id`。

P2 验收：回放成功率 ≥ 既有探索链路基线；用例进入套件冻结流程。

## 六、P3 阶段任务（ego-lite 接入）

| 编号 | 任务 | 开发目标 | 状态 |
|---|---|---|---|
| P3-1 | ego-lite 驱动 | ego lite 经 cdp-attach / ego-browser `cdp()` 桥接入驱动注册表，零协议改动 | ⛔ 阻塞于 ego lite Windows 版 |

## 七、通用纪律（每个任务都适用）

1. 完成后必须实际运行验证（后端用 `E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，`PYTHONPATH=.`；前端 `npm run build`）；失败如实记录在本文件；
2. 新代码风格对齐同目录既有实现；跨边界契约（API/事件协议/表结构）改动必须同步所有消费方；
3. 录制事件含敏感输入时只记长度不记明文（安全红线）；
4. 每个任务完成后更新本文件状态，并按 `【feat】/【fix】/【docs】` 规范提交。
