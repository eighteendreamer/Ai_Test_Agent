# UI 操作录制与元素图谱构建 · 开发进度

> 最后更新：2026-08-24
> 参考方案：[ui_recording_development_plan.md](./ui_recording_development_plan.md)（v2.1）
> 状态图例：✅ 已完成 / 🔵 进行中 / ⬜ 未开始 / ⛔ 阻塞

## 一、当前结论

P0 进行中：P0-1（录制数据契约与 PG 表结构）、P0-2（RecordingStore PG 事件流存储）、P0-3（RecordingGraphStore Memgraph 固化）、P0-4（recorder.js 注入脚本）、P0-5（BrowserDriver 抽象 + embedded 驱动）、P0-6（RecorderSessionService 会话编排+状态机）完成。P0-2 落地 `infrastructure/recording_store.py`（create_session / append_events 批量幂等 / update_status / get_session / list_sessions / get_events / discard_session；事件流水只 INSERT 不 UPDATE，(recording_id, seq) ON CONFLICT DO NOTHING），`tests/test_recording_store.py` 8 个不连库单测 + 1 个连库集成（RUN_LIVE_RECORDING_PG=1，本机 PG 实测重复批次重试 0 新增、11 重复、step_count 对账一致）全部通过。P0-3 落地 `application/exploration/recording_graph_store.py`（finalize 固化 + delete_recording；严格仿 `ui_graph_store.py` 的 MERGE 键/payload_json/scoped_key 惯例；Recording/Action 节点 + HAS_STEP/TARGETS/ON_PAGE/NAVIGATED_TO 边 + 复用 CONTAINS；Element 指纹与 `_element_dedupe_key` 同构且内容寻址 id（跨录制收敛）；7.2 完整性校验：seq 连续性缺口、PG 事件数 vs Action 数对账、locators 全空标 resolution_status=degraded、raw-vs-dedup 指标；密码字段防御性脱敏兜底），`tests/test_recording_graph_store.py` 11 个不连库单测 + 1 个连库集成（RUN_LIVE_RECORDING_MEMGRAPH=1，本机 Docker Memgraph 实测：10 次点击同一按钮收敛 1 个 Element 节点、11 条 Action 流水不去重、finalize 重复执行幂等、delete 只删 Recording/Action 保留 Page/Element）全部通过。P0-4 落地 `Agent_Server/src/application/recorder/assets/recorder.js`（单文件无依赖、三端共用：click/dblclick/fill(input 500ms debounce 合并+敏感脱敏)/file_change/功能键与组合键/submit/scroll(300ms 节流)/navigate(pushState+replaceState+popstate+hashchange+pageshow)/page_scan 六类采集；`composedPath()[0]` 穿透 shadow DOM；locator 链 id→testid→role+name→css(唯一 id 短路+nth-of-type)→xpath(全序号)→text；像素三件套 viewport_point/bbox/rel_offset；可交互元素轻量扫描+纯 JS SHA-1 dom_hash；MutationObserver 计数随事件携带并重置；top frame 统一分配 seq，sessionStorage 跨整页导航续号；binding 缺失入缓冲 2s 重试；同源 iframe postMessage 桥接；控制协议 SetEnabled/GetState/Scan/Flush；密码字段 value 与 accessible name 双重脱敏），`agent_web/tests/recorder/recorder.test.mjs` 20 个用例（每用例独立 JSDOM realm + vitest 假定时器；runScripts:"outside-only" 使 eval 注入生效）全部通过，agent_web 全量 29 测试无回归。P0-5 落地 `application/recorder/drivers/`（base.py：BrowserDriver ABC（方案 5.1 七方法契约）+ DriverRegistry（kind→factory，create(config, **context)，重复/空白/未知 kind 防御）+ EventChannel（容量 10000 防爆、满时丢弃计数不阻塞采集）；embedded_bridge.py：EmbeddedBridge（attach 登记/register_session 握手/ingest_events 事件转发（批内去重+seen_seqs 幂等预收敛，DB 唯一约束为最终防线）/enqueue+poll_commands 下行指令（long-poll 语义）/report_screenshot 最近帧缓存/close_session 标记保留-detach 终态清理两段式，close 后 Electron 仍可拉取 close 指令、未 detach 禁止重建防指令丢失）+ EmbeddedDriver（open→navigate 指令、inject_recorder 记录 binding（实际注入在 Electron）、on_recorder_event 异步流、current_page_info 取最近事件 page、set_capture_enabled→指令、capture_screenshot 取最近帧、close→指令+回收）），`tests/test_recorder_drivers.py` 10 个纯单测（契约齐全性/抽象不可实例化/注册表语义/完整生命周期/幂等重试/未知会话拒绝/long-poll）全部通过，既有录制域 30 测试无回归。P0-6 落地 `application/recorder/recorder_session_service.py`（launch→ready 后台握手/五控制动作迁移表+内存占位防并发重复指令+异常回滚/stop=flush+固化+指标入库/destroy=丢弃不冲刷+discarded 审计保留/事件消费循环 20 条·0.5s 攒批+3 次退避重试+暂停丢弃计数；`wait_ready` 补入 BrowserDriver 契约（默认 True，embedded 覆盖等登记）；消费循环超时后重建迭代器（wait_for 取消会终止 async generator，重建不丢队列事件）；flush 语义按占位状态判定（discarded 不冲刷），删除从未生效的 `_cancel_task(flush=)` 参数），`tests/test_recorder_session_service.py` 11 个纯单测全部通过；顺手修复 `test_recording_graph_store.py` 8 处废弃 `get_event_loop().run_until_complete`（asyncio.run 结束会清线程 loop，组合运行时抛 RuntimeError）为项目惯例 `asyncio.run`，录制域 5 文件组合 51 测试全绿。

## 二、阶段状态总览

| 阶段 | 目标 | 状态 |
|---|---|---|
| P0 harness 主链路 | 桌面端全自动录制主链路端到端可用 | 🔵 进行中（6/11） |
| P1 外部浏览器 | cdp-attach（Chrome/Edge）+ playwright-managed + iframe 补齐 | ⬜ 未开始 |
| P2 回放与用例 | 录制回放执行器 + 录制转用例草稿 | ⬜ 未开始 |
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

### P0-7 recordings API 路由 ⬜

**开发目标**：方案第 8 章端点全部落地。

- 做什么：新建 `src/api/routes/recordings.py`（仿 `sponsors.py` 风格）；`main.py` 注册路由 + lifespan 初始化 + `app.state` 挂载；截图 multipart 上传接 MinIO（本地产物目录兜底）。
- 验收：API 契约测试覆盖正常/异常/边界/幂等/并发多会话；未审批 session 不得创建录制（与 P0-8 联动）。

### P0-8 UIAutomationModeRuntime 编排改造 ⬜

**开发目标**：harness 全流程接通（方案第 4 章状态机）。

- 做什么：改 `src/modes/ui_automation_mode/runtime.py`：① project_id 缺失 → `awaiting_project_selection` + 候选项目列表（复用 knowledge projects 查询）；② `_assess_knowledge` → 三源检索（Memgraph 覆盖 + 用例库 + Memory），返回各源命中计数与判定理由；③ 缺口分支改为 `awaiting_recording_approval`，创建 `approval_type="ui_recording"` 审批（复用 `agent_session_approvals`）；④ 审批通过回调 → `RecorderSessionService.launch`；⑤ 固化完成 → `task_generation_ready`。
- 验收：编排单测覆盖全流程分支（有资源/无资源/审批通过/审批拒绝/项目反问）；既有 UI 探索链路测试全绿（不破坏旧能力）。

### P0-9 Electron 录制窗口 + 控制条 ⬜

**开发目标**：桌面端自动弹出录制窗口，用户零手动启动。

- 做什么：`electron/main.js` 新增 `recorder:create-window`（BrowserWindow 控制条 + WebContentsView，`partition: "persist:recorder"`）/ `recorder:attach-debugger`（attach 1.3 → addScriptToEvaluateOnNewDocument 注入 → addBinding 收事件 → 转发）/ `recorder:navigate` / `recorder:set-capture` / `recorder:close`；`preload.cjs` 扩展 `qaAgentDesktop.recorder.*`；控制条页面四按钮（开始/暂停·继续/结束/销毁）+ 状态徽标（状态/步数/当前 URL），销毁二次确认。
- 验收：审批通过后窗口自动弹出且已注入；四按钮驱动后端状态机；截图回传落产物目录。

### P0-10 前端审批卡片与录制时间线 ⬜

**开发目标**：主界面侧的录制可见性。

- 做什么：审批卡片扩展 `ui_recording` 类型（显示项目/目标 URL/三源缺口原因/驱动选择，复用 ApprovalPanel）；会话面板追加录制实时步骤时间线（动作类型 + 元素名 + 缩略截图，轮询或事件增量）；录制结束后的步骤清单（删误操作/补备注/确认固化）入口；15 个语言文件补 i18n key。
- 验收：前端 `npm run build` 通过；时间线组件测试；i18n fallback 验证。

### P0-11 P0 端到端验收 ⬜

**开发目标**：按方案第 12 章验收 P0。

- 做什么：桌面端 UI 模式输入"测试 XX 流程" → 反问项目 → 检索无资源 → 审批通过 → 自动弹录制窗口 → 真实产品操作（登录 + 表单提交）→ 结束固化 → 检查 Memgraph 子图（Recording/Action/Page/Element 齐全、指标对账一致）与 PG 事件流。
- 验收：全链路一次跑通；图谱指标与 PG 事件数一致；既有测试套件全绿。

---

## 四、P1 阶段任务（外部浏览器）

| 编号 | 任务 | 开发目标 | 状态 |
|---|---|---|---|
| P1-1 | cdp-attach 驱动 | `connect_over_cdp` attach Chrome/Edge，复用真实登录态；注入同一 recorder.js | ⬜ |
| P1-2 | playwright-managed 录制 | `_ensure_session` 增加录制模式（add_init_script + expose_binding），服务端/纯 Web 部署可用 | ⬜ |
| P1-3 | iframe 补齐 | 同源 iframe postMessage 桥接；跨域 iframe CDP `Target.setAutoAttach` 子 frame 注入 | ⬜ |

P1 验收：本地 Chrome（带登录态）录制同一流程，元素与 P0 收敛同一批 Element 节点。

## 五、P2 阶段任务（回放与用例）

| 编号 | 任务 | 开发目标 | 状态 |
|---|---|---|---|
| P2-1 | 回放执行器 | 定位决策链（id→testid→role+name→css→xpath→bbox 相对偏移→绝对坐标兜底）；回放报告 | ⬜ |
| P2-2 | 录制转用例草稿 | 录制步骤 → 用例草稿，进既有「评审 → 固定版本 → 套件冻结」链路 | ⬜ |
| P2-3 | 断言建议 | 基于 page_effect 与元素语义生成断言建议 | ⬜ |

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
