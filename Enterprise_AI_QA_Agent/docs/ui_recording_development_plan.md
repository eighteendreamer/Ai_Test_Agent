# UI 操作录制与元素图谱构建 · 工程开发方案

> 版本：v2.1 · 日期：2026-08-23 · 状态：待评审
>
> 目标：用「人工操作录制」替代「AI 自主探索」，把用户在浏览器中的操作解析为带双重定位的元素信息，沉淀进 Memgraph 知识图谱，为 UI 自动化测试提供确定性输入。
>
> v2.0 变更：录制不是用户手动启动的独立功能，而是 UI 测试模式下 **harness 全自动编排链路**的一环——意图识别 → 项目确认 → 资源检索 → 缺口审批 → 自动启动浏览器录制 → 固化入图谱 → 回到测试任务。
>
> v2.1 变更：补充 6.4「页面快照策略与 DOM 指纹幂等」（录制中不做全量 DOM 抓取的三层机制）与 7.2「数据清洗管线与完整性校验」（五道关口 + 零遗漏的工程边界）。

---

## 0. 一页结论

| 问题 | 结论 | 依据 |
|---|---|---|
| Electron 桌面端能否直接当录制浏览器？ | **能，且作为 P0 默认载体**。Electron 31 内嵌 Chromium 126，`WebContentsView` 加载任意产品 URL，`webContents.debugger` 是官方内置 CDP 客户端，可注入脚本、收事件、截图。 | Electron 官方文档（WebContentsView / debugger API）；`agent_web/electron/main.js` 现状 |
| 浏览器如何自动启动？ | 审批通过后由**后端创建录制会话 → 实时事件通道通知前端 → IPC 调起 Electron 录制窗口**，全程无用户手动操作；非桌面端降级为 `playwright-managed` 弹窗。 | 既有审批机制（`sessions.py` approvals + 前端 ApprovalPanel）+ SSE 事件流架构 |
| 如何"无缝切换任何浏览器"？ | 抽象 **BrowserDriver 接入层**，统一 CDP 注入协议：`embedded`（Electron 内嵌）/ `cdp-attach`（attach 任意带 `--remote-debugging-port` 的 Chromium 系）/ `playwright-managed`（服务端自启）。录制脚本与事件协议三端共用。 | ego-lite 的 `ego-browser` 底层即 CDP（`cdp-eval.ts`）；Playwright 官方 `connect_over_cdp` |
| ego-lite 何时能接入？ | **ego lite 目前仅支持 macOS**（README 明示 Windows/Linux 在 roadmap）。以 `cdp-attach` 驱动接口预留接入位，不阻塞主线。 | `项目借鉴/ego-lite/README.md` "runs on macOS today" |
| 知识缺口如何判定？ | 升级现有 `UIAutomationModeRuntime._assess_knowledge` 为**三源检索**：Memory 语义检索 + Memgraph 图谱覆盖（Page/Element/Action）+ 用例库已有 UI 用例。 | `src/modes/ui_automation_mode/runtime.py` 现有知识门控骨架 |

---

## 1. 背景与目标

### 1.1 现状痛点

现有 UI 自动化「信息探索」由 AI Agent 驱动浏览器自主探索（`UIExplorationService` → Playwright `explore` → ARIA 语义图）：慢、不全面、结果随模型波动。

### 1.2 目标业务流程（用户定义的全流程）

```
用户在 UI 测试模式下输入："帮我测试一下支付流程"
  │
  ├─ ① 意图识别：识别为 ui_automation 模式 + "支付流程"测试目标
  │
  ├─ ② 项目确认：project_id 缺失 → 反问用户"测试哪个项目？"
  │     用户告知 → 锁定正式 project_id（禁止自由文本冒充，AGENTS.md 第七章）
  │
  ├─ ③ 资源检索：到该项目下检索
  │     ├─ Memgraph 知识图谱：Page/Element/Action 是否覆盖"支付"相关路径
  │     ├─ 用例库：该项目是否已有可复用的 UI 测试用例
  │     └─ Memory：语义检索补充
  │
  ├─ ④ 缺口处置：无可用资源 → 告知用户"没有这部分 UI 资源"
  │     → 询问是否录制 UI 操作 → 弹出审批卡片（复用既有审批机制）
  │
  ├─ ⑤ 审批通过 → 系统自动启动浏览器（录制窗口自带控制条：
  │     开始 / 暂停·继续 / 结束 / 销毁）→ 用户操作产品，系统录制
  │
  ├─ ⑥ 录制结束 → 事件流固化：PostgreSQL 存原始流水，Memgraph 存结构化图谱
  │
  └─ ⑦ 图谱就绪 → 知识门控转为"充分" → 进入测试用例生成与执行链路
```

**关键原则：用户全程不手动启动浏览器、不手动打开录制面板**。启动、注入、采集、固化全部由 harness 编排驱动；用户只在三个节点介入：确认项目、审批录制、在被测产品里正常操作。

### 1.3 非目标（本期不做）

- 移动端（小程序/安卓/iOS/鸿蒙）录制——契约中对应方向本就 `available: false`；
- rrweb 式全量 DOM 回放录像（截图 + 事件流已够定位与审计，录像作为后续增强）；
- 删除既有 AI 探索链路（保留为兜底，录制是其上位替代）。

---

## 2. 现状盘点（方案的事实基础）

### 2.1 harness 编排相关（本次改造的直接接入点）

| 组件 | 位置 | 现状 | 本方案用途 |
|---|---|---|---|
| 意图识别 | `src/application/intent/semantic_intent_service.py`（`SemanticIntentService`，main.py 装配进 `input_orchestrator_service`；config: `intent_semantic_classifier_enabled` / `intent_deterministic_confidence_threshold`） | 已有多模式意图分类 | 环节①直接复用 |
| 模式运行时 | `src/modes/ui_automation_mode/runtime.py`（466 行） | `_resolve_request` 从 arguments/context_bundle/user_message 解析 target_url/objective/project_id 等；`_assess_knowledge` 只查 Memory（hit≥3 或 score≥0.78 为充分）；知识不足时返回 `awaiting_exploration_selection` 让用户选方向后跑**旧版 AI 探索** | 环节②③④的改造主体：补项目反问、三源检索、录制审批分支 |
| 审批机制 | `src/api/routes/sessions.py`：`GET/POST /{session_id}/approvals[/{approval_id}]`；PG 表 `agent_session_approvals`；flow 投影有 `waiting_approval` 状态；前端有 ApprovalPanel（`approvalPanel.*` i18n 齐全）与桌面通知（`settings.notify_approval`） | 完整可用 | 环节④直接复用：新增 `approval_type = "ui_recording"` |
| 层级契约 | `src/modes/ui_automation_mode/contracts.py` | boss/方向组长/子方向员工；browser+information_exploration 已 available，test_execution 占位 | 不变；录制归入 information_exploration 的新输入源 |

### 2.2 浏览器与图谱相关

| 组件 | 位置 | 现状 | 本方案复用方式 |
|---|---|---|---|
| `PythonPlaywrightCliRuntime` | `src/application/runtime/python_playwright_cli.py`（1958 行） | 管理 `_BrowserSession`；支持 chromium/chrome/msedge channel、headed、persistent profile；**无 `connect_over_cdp`** | `playwright-managed` 驱动复用会话管理；`cdp-attach` 需新增 |
| `UIGraphStore` | `src/application/exploration/ui_graph_store.py`（455 行） | Page/Element/Entity MERGE（`project_id`+`id`）；CONTAINS/INTERACTED_WITH/NAVIGATES_TO 等边；去重规范化完整 | 图谱写入模板：新 `RecordingGraphStore` 沿用 MERGE/去重/`_scoped_key` 模式 |
| Electron 主进程 | `agent_web/electron/main.js`（438 行） | 两个 BrowserWindow、静态服务器 + `/api` 代理、3 个 IPC；**无 WebContentsView / debugger** | 新增录制窗口 + debugger 桥接 |
| preload | `agent_web/electron/preload.cjs` | `contextBridge` 暴露 `qaAgentDesktop` | 同模式扩展 `qaAgentDesktop.recorder.*` |
| 用例库 | `src/api/routes/case_management.py` 等 | 用例驱动链路完整（草稿→评审→固定版本→套件） | 环节③检索源之一；环节⑦的下游 |

### 2.3 ego-lite 参考项目结论

Chromium 系浏览器 + `ego-browser` Node 驱动包，**底层通信用 CDP**；亮点：内核级语义快照、task space 隔离、继承用户 Chrome 登录态；**硬约束：macOS only**（当前开发机为 Windows），接入列为 P3 预留。

---

## 3. 总体架构

```
┌──────────────────────────── Harness 编排层（Agent_Server）────────────────────────────┐
│ 意图识别(SemanticIntentService) → UIAutomationModeRuntime.handle()                     │
│   → 项目确认(反问) → 三源资源检索 → 缺口审批(approvals) → RecorderSessionService        │
│      创建录制会话 → 实时事件通知前端 → 自动启动浏览器驱动                                 │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │ driver 选择
┌────────────────────────── 浏览器接入层（BrowserDriver 抽象）──────────────────────────┐
│  embedded            │  cdp-attach               │  playwright-managed              │
│  Electron            │  Chrome/Edge/任意Chromium  │  Agent_Server 自启浏览器          │
│  WebContentsView     │  --remote-debugging-port  │  （复用 PythonPlaywrightCliRuntime）│
│  + webContents.debugger（未来 ego-lite 走 cdp-attach）│                                │
└──────────┬───────────────────────┬──────────────────────────────┬───────────────────┘
           │ 统一注入 recorder.js（addScriptToEvaluateOnNewDocument / add_init_script）
           │ 统一事件回传 window.__qaRecordEmit（Runtime.addBinding / exposeBinding / IPC）
           ▼
┌───────────────────────── 录制引擎（Recorder Session）─────────────────────────┐
│  事件采集 → 双重定位解析 → 节流/合并 → 批次上报；控制态：开始/暂停/结束/销毁      │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │  REST: /api/v1/recordings/*
           ┌───────────────────────┴────────────────────────┐
           ▼                                                ▼
┌──────────────────────┐                      ┌─────────────────────────────┐
│ PostgreSQL           │                      │ RecordingGraphStore         │
│ 原始事件流 + 会话元数据 │ ──结束固化────────▶  │ （仿 UIGraphStore）          │
│ （高频、可重放、可审计）│                      │ Memgraph：Recording/Action/  │
└──────────────────────┘                      │ Page/Element + 关系          │
           │                                  └─────────────────────────────┘
           ▼                                                │
┌──────────────────────┐                                    ▼
│ MinIO / 本地产物      │                      ┌─────────────────────────────┐
│ 截图（节流采样）      │                      │ 知识门控转充分 → 用例生成     │
└──────────────────────┘                      │ → 套件冻结 → 任务池执行       │
                                              └─────────────────────────────┘
```

---

## 4. Harness 全流程编排（核心新增）

### 4.1 状态机

`UIAutomationModeRuntime.handle()` 的 phase 扩展（现有 phase：`awaiting_input` / `awaiting_exploration_selection` / `exploration_completed` / `task_generation_ready`）：

```
entered
  ├─ 缺 project_id ──────────────▶ awaiting_project_selection   （反问：测试哪个项目）
  │                                   ▲ 用户回复后携带 project_id 重入
  ├─ 缺 target_url/目标 ───────────▶ awaiting_input              （既有，保留）
  ▼
资源检索 assess_ui_resources
  ├─ 有用例/图谱覆盖 ──────────────▶ task_generation_ready        （既有，知识充分）
  ├─ 无资源 ───────────────────────▶ awaiting_recording_approval  （新：发起录制审批）
  │      │ 审批拒绝 ───────────────▶ recording_declined           （降级：可选回退 AI 探索）
  │      │ 审批通过 ───────────────▶ recording_launching
  │                                     │ 驱动就绪
  │                                     ▼
  │                                 recording_active  ⇄  recording_paused
  │                                     │ 结束                  ▲ 销毁（任意时刻）
  │                                     ▼                       │
  │                                 recording_finalizing        │
  │                                     │ 固化完成              ▼
  │                                     ▼                  recording_destroyed
  │                              task_generation_ready           （丢弃数据，会话终止）
```

### 4.2 各环节接入点

**环节② 项目确认（反问）**
- 现状：`_resolve_request` 在 project_id 缺失时用 `_derive_project_scope` 从 host/hash 派生 scope——这只能当 scope，不能冒充 project_id（AGENTS.md 明令禁止自由文本项目名替代正式 `project_id`）。
- 改造：project_id 缺失时返回 `awaiting_project_selection`，附候选项目列表（`GET /api/v1/knowledge/projects` 已有，knowledge.py 提供项目摘要），用户选择/回复后重入 handle。

**环节③ 三源资源检索（`_assess_knowledge` 升级为 `assess_ui_resources`）**

| 检索源 | 判定 | 数据访问 |
|---|---|---|
| Memgraph 图谱 | 该项目下与目标关键词相关的 Page/Element 覆盖数、是否有 Action（录制产物） | `MemgraphRuntimeProvider` 只读查询（UIGraphStore 同一 provider） |
| 用例库 | 该项目下状态为"已启用固定版本"的 UI 用例数 | 既有 case_management 查询 |
| Memory 语义检索 | 既有 hit_count/max_score 阈值逻辑 | `MemoryRuntimeService.retrieve_for_turn`（保留现状） |

充分条件（三选一即充分）：有用例可复用 或 图谱覆盖达标 或 memory 命中充分；都不足 → 进入录制审批分支。**审计要求**：返回结构带三源各自的命中计数与判定理由（对齐规则"错误信息必须携带上下文"，也让用户知道"查过哪里、为什么不够"）。

**环节④ 录制审批**
- 复用既有审批流：创建 `approval_type = "ui_recording"` 的审批记录（PG `agent_session_approvals`），flow 投影进入 `waiting_approval`；前端 ApprovalPanel 展示审批卡片（说明：目标项目、目标 URL、缺口原因、将启动的浏览器类型），桌面通知提醒（既有 `settings.notify_approval` 链路）。
- 审批通过（`POST /{session_id}/approvals/{approval_id}` decision=approved）→ 会话编排继续，调用 `RecorderSessionService.launch(...)`。

**环节⑤ 自动启动浏览器**

桌面端（embedded）启动链路：
```
审批通过 → RecorderSessionService 创建录制会话（PG 落行，status=launching）
  → 会话事件流推送 "recorder.launch_requested"（复用既有 SSE 实时事件架构）
  → 前端收到事件 → window.qaAgentDesktop.recorder.createWindow({recording_id, entry_url, driver})
  → Electron 主进程：创建录制窗口（控制条 + WebContentsView，partition persist:recorder）
  → debugger attach + 注入 recorder.js → 回告后端 driver_ready → status=ready（待开始）
```

非桌面端降级：`playwright-managed` 由后端 `_ensure_session` 弹有头浏览器，注入同一 recorder.js。

**环节⑥ 录制控制条（窗口自带四个按钮）**

| 按钮 | 语义 | 后端动作 |
|---|---|---|
| 开始 | 激活采集（recorder.js 开关置 on） | status: ready → active，记录 started_at |
| 暂停 / 继续 | 挂起/恢复采集，浏览器不关、会话保留 | active ⇄ paused；暂停期间事件不入库 |
| 结束 | 停止采集，触发固化 | active/paused → finalizing → PG 事件 → Memgraph 固化 → completed |
| 销毁 | 终止会话：关浏览器、丢弃未固化数据 | 任意态 → destroyed；PG 标记 discarded，图谱不写 |

控制条与录制状态机的对应关系由后端权威维护（前端按钮只是发令枪），页面刷新/窗口重开可凭 recording_id 恢复状态。

**环节⑦ 固化完成后回归主链路**
- `RecordingGraphStore.finalize()` 完成后，knowledge_gate 重新评估 → `task_generation_ready`；
- Agent 向用户汇报：录制了多少步、覆盖多少页面/元素、图谱写入指标、下一步建议（生成用例草稿）。

---

## 5. 浏览器接入层设计（无缝切换的核心）

### 5.1 驱动接口契约

```python
class BrowserDriver(Protocol):
    kind: str  # "embedded" | "cdp-attach" | "playwright-managed"

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None: ...
    async def inject_recorder(self, binding_name: str = "__qaRecordEmit") -> None: ...
    async def on_recorder_event(self) -> AsyncIterator[dict]: ...   # 事件流
    async def capture_screenshot(self) -> bytes: ...
    async def current_page_info(self) -> dict: ...  # url/title/viewport/dpr
    async def set_capture_enabled(self, enabled: bool) -> None: ...  # 暂停/继续
    async def close(self) -> None: ...
```

### 5.2 三种实现

**① embedded（P0 默认，桌面端）**
- Electron 主进程新建录制窗口：`BrowserWindow` 上半部加载本地控制条页面，下半部挂 `WebContentsView` 加载目标产品 URL（Electron 30+ 官方推荐方案，替代已废弃的 BrowserView）；
- `webContents.debugger.attach("1.3")` → `Page.addScriptToEvaluateOnNewDocument` 注入 recorder.js → `Runtime.addBinding("__qaRecordEmit")` 收事件 → IPC 转发 → POST 后端；
- 截图：`Page.captureScreenshot`；登录态：`partition: "persist:recorder"` 持久化（Electron session 官方机制），用户只需登录一次。

**② cdp-attach（P1，外部真实浏览器）**
- attach `--remote-debugging-port` 启动的 Chrome/Edge，Playwright 官方 `chromium.connect_over_cdp(...)`，**复用真实登录态**；
- 注入：`context.add_init_script` + `page.expose_binding`；
- **ego-lite 未来走这里**：ego lite 是 Chromium 系、ego-browser 底层即 CDP；若开放 CDP 端口直接 attach，否则经其 SKILL.md 已暴露的原生 `cdp(method, params)` 通道桥接。

**③ playwright-managed（P1，服务端/无桌面端降级）**
- 复用 `PythonPlaywrightCliRuntime` 会话管理（headed + persistent profile），加 `add_init_script` + `expose_binding`；用户在弹出的受管窗口操作。

### 5.3 切换机制

- 审批卡片上给出可用驱动（桌面端默认 embedded，可改 cdp-attach 端点）；
- **录制脚本与事件协议与驱动无关**——切换浏览器 = 换驱动实现，产物格式不变。ego-lite 就绪后零协议改动接入。

---

## 6. 录制引擎设计

### 6.1 注入脚本 `recorder.js`（三端共用，单文件无依赖）

注入时机：文档创建前（`Page.addScriptToEvaluateOnNewDocument` / `add_init_script`），SPA 路由切换后仍存活；`window.__qaRecorderInstalled` 幂等守卫；采集开关由驱动侧 `set_capture_enabled` 控制（对应暂停/继续）。

| 事件 | 监听 | 策略 |
|---|---|---|
| click / dblclick | 捕获阶段 `addEventListener(..., true)` | 全量 |
| input / change | 捕获阶段 | debounce 500ms 合并为一次 `fill`（保留最终值）；**密码/敏感字段只记长度不记明文（安全红线）** |
| keydown | 捕获阶段 | 仅功能键（Enter/Tab/Escape/方向键/F*）与快捷键组合 |
| submit | 捕获阶段 | 全量 |
| scroll | 捕获阶段 | 300ms 节流，记录滚动容器 + scrollTop/Left |
| 导航 | `pageshow`/`popstate`/重写 `history.pushState` + CDP `Page.frameNavigated` 兜底 | 记录 from/to URL |
| 文件上传 | change 且 `input[type=file]` | 只记文件名，不读内容 |

**shadow DOM 与 iframe**：事件目标用 `event.composedPath()[0]` 穿透 open shadow root；同源 iframe 向 top frame 桥接（postMessage）；跨域 iframe 内事件 P0 不采集，P1 用 CDP `Target.setAutoAttach({flatten:true})` 子 frame 注入补齐。

### 6.2 双重定位设计

```jsonc
{
  "type": "click",
  "seq": 17,
  "timestamp": "2026-08-23T10:00:01.234Z",
  "page": { "url": "...", "title": "...", "viewport": {"w":1440,"h":960}, "dpr": 1.0 },

  // ── DOM 定位链（按稳定性降序，回放时逐级尝试）──
  "target": {
    "locators": {
      "id": "login-submit",
      "testid": null,                        // data-testid / data-test / data-qa
      "role_name": {"role": "button", "name": "登 录"},
      "css": "form.login > button.primary",
      "xpath": "/html/body/div[1]/form/button[1]",
      "text": "登 录"
    },
    "tag": "BUTTON", "role": "button",
    "attributes": {"type": "submit", "class": "..."},
    "in_iframe": null,
    "shadow_path": null
  },

  // ── 像素级定位（三件套）──
  "pixel": {
    "viewport_point": {"x": 712, "y": 503},          // 视口坐标（CSS 像素）
    "bbox": {"x": 640, "y": 488, "w": 144, "h": 30}, // 元素包围盒
    "rel_offset": {"rx": 0.5, "ry": 0.5}             // 元素内相对偏移比例
  },

  "screenshot_ref": "artifacts/<session>/<turn>/rec_17.png",
  "value": null,
  "page_effect": {"navigated_to": null, "dom_mutation_count": 12}
}
```

**回放定位决策链**（P2 回放执行器，也定义采集端为什么都记）：

```
locators.id → testid → role+name → css → xpath        （DOM 层逐级重试）
  ↓ 全部失效
bbox + rel_offset：找几何上最接近的元素重新锚定（元素漂移容错）
  ↓ 仍失败
viewport_point 绝对坐标兜底（Canvas 类页面唯一手段；ego-browser SKILL.md
对 canvas 类应用同样以视觉工作流为标准路径）
```

### 6.3 事件回传通道（按驱动桥接，协议统一）

| 驱动 | 桥接机制 |
|---|---|
| embedded | CDP `Runtime.addBinding` → debugger `message` → IPC → 渲染进程 |
| cdp-attach / playwright-managed | Playwright `expose_binding`（内部同样是 CDP addBinding） |

事件缓冲后**每 2s 或每 20 条**批量 POST `/api/v1/recordings/{id}/events:batch`；截图节流（每个动作前后 300ms 内最多一帧）走 multipart 上传。

### 6.4 页面快照策略与 DOM 指纹幂等

**核心原则：录制中不做全量 DOM 抓取。** 业界成熟录制方案（Chrome DevTools Recorder / Playwright Codegen / Selenium IDE / ego-browser）无一采用全量抓取——全是事件驱动 + 定点解析。本方案同样如此，分三层：

**① 录制中：只对事件命中元素做定点解析（O(1)，非 O(DOM)）**

```js
// 事件瞬间只解析命中的那一个元素，不碰整页
const el = event.composedPath()[0];          // 穿透 open shadow root
const bbox = el.getBoundingClientRect();     // 包围盒（像素定位）
const locators = buildLocatorChain(el);      // id/testid/role/css/xpath 链
```

微秒级成本，用户连续快速操作无压力；URL/title/viewport/DPR 随事件携带，不单独抓取。

**② 导航与页面切换：只记事件，零 DOM 成本**

「点按钮 → 跳转 → 切回来」在事件流中是四条独立记录：click（定点解析）→ navigate(from,to) → navigate(from,to) → click（再次定点解析）。同一按钮被操作两次会产生两条 Action——这是正确行为：Action 是流水，重复收敛发生在图谱固化端（见 7.2），不在采集端。

**③ 页面元素清单：每页一次轻量扫描，由 DOM 指纹决定是否重扫**

录制事件只覆盖「被操作过的元素」，图谱 `Page-[:CONTAINS]->Element` 完整关系需要补充一次**可交互元素扫描**（只扫 `button/a/input/select/textarea/[role]/[tabindex]`，不抓全文，思路同 ego-browser snapshot）。何时算「新页面」由 DOM 指纹判定，而非无脑每页抓：

```
page_key = (project_id, 归一化URL, title)
dom_hash = sha1(可交互元素的 tag+role+name+href 有序拼接)
```

- 切回已见页面且 `dom_hash` 未变 → 只更新 `last_seen_at`，**零重抓**；
- `dom_hash` 变化（SPA 局部刷新）→ 增量重扫一次，新元素 upsert、消失元素标 `stale` 不删除（避免误删被其他录制引用的节点）；
- 操作引起的页面响应：注入侧 `MutationObserver` 只计数变化量（`page_effect.dom_mutation_count`），用于判断「操作是否有页面响应」，不做全量对比。

---

## 7. 图谱 Schema 扩展（Memgraph）

复用 `UIGraphStore` 既有节点（Page/Element）与 MERGE 约定（`(project_id, id)` / `(project_id, edge_id)` 为键），新增录制域：

| Label | 关键属性 | 说明 |
|---|---|---|
| `Recording` | id, project_id, name, driver_kind, entry_url, started_at, ended_at, status, step_count | 一次录制会话 |
| `Action` | id, project_id, recording_id, seq, action_type, value_masked, page_url, occurred_at, payload_json | 一个操作步骤；payload_json 存完整定位负载 |

```
(Recording)-[:HAS_STEP {seq}]->(Action)
(Action)-[:TARGETS]->(Element)
(Action)-[:ON_PAGE]->(Page)
(Action)-[:NAVIGATED_TO]->(Page)        # 引起导航时
(Page)-[:CONTAINS]->(Element)           # 复用既有边：录制时元素就地 upsert
```

Element 去重键沿用 `_element_dedupe_key` 思路（page + role + name + entity/container + href），保证 AI 探索产物与录制产物**收敛到同一批 Element 节点**。

### 持久化分层

| 数据 | 存储 | 理由 |
|---|---|---|
| 录制会话 + 原始事件流 | PostgreSQL `ui_recording` / `ui_recording_event`（JSONB） | 高频追加、可审计、可重放 |
| 结构化 页面/元素/动作 关系 | Memgraph | 图谱查询：元素被哪些操作覆盖、页面已录制路径 |
| 截图 | MinIO（本地产物目录兜底） | 二进制大对象，库中只存 ref |

### 7.2 数据清洗管线与完整性校验

重复抓取的收敛不靠采集端，靠固化端五道关口。核心机制直接复用 `UIGraphStore._normalize_for_write` 已验证的三级清洗（`_dedupe_nodes` 节点指纹去重 + 边五元组去重 + alias 重映射 + `(project_id, id)` MERGE 幂等），录制链路在其上扩展：

```
① 采集端降噪      input debounce 500ms 合并 / scroll 300ms 节流 / 不录 mousemove
       ↓
② 入库端幂等      (recording_id, seq) 唯一约束——网络重试、重复批次不产生重复事件
       ↓
③ 固化端收敛      Element 按指纹 MERGE：同一按钮被操作 10 次 → 1 个 Element 节点，
                  10 条 Action 各自 TARGETS 它（Action 是流水，不去重，这是特性）；
                  Page 按归一化 URL MERGE（尾斜杠 / hash 路由差异归一）
       ↓
④ 完整性校验      a. seq 连续性检查：缺口 = 丢事件，告警并标记会话 degraded；
                  b. 固化对账：PG 事件数与图谱 Action 数必须相等；
                  c. 解析失败计数：locators 全空的 Action 标 resolution_status=degraded；
                  d. raw vs deduplicated 计数（沿用 UIGraphStore metrics 模式）
       ↓
⑤ 干净完整入图    重复已收敛、丢失有告警、失败有标记
```

**「零遗漏」的工程边界**（诚实声明，与第 11 章风险表呼应）：跨域 iframe 内操作受同源策略限制，P0 不采集（P1 用 CDP `Target.setAutoAttach` 补齐）；Canvas/自绘组件无 DOM 可解析，由像素三件套兜底保证可回放而非可解析。除这两类明示边界外，管线保证「抓到即入图、入图必收敛」。

---

## 8. API 设计（Agent_Server 新增）

路由风格对齐既有 `src/api/routes/*.py`（`APIRouter` + `asyncio.to_thread`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/recordings` | 创建录制会话（由审批通过后的编排层调用；也保留手动创建用于调试）：`{project_id, name, entry_url, driver:{kind,...}, session_id, approval_id}` |
| POST | `/api/v1/recordings/{id}/events:batch` | 批量追加事件（幂等：`(recording_id, seq)` 唯一约束去重） |
| POST | `/api/v1/recordings/{id}/screenshots` | multipart 截图上传，返回 ref |
| POST | `/api/v1/recordings/{id}/control` | 控制条指令：`{action: start\|pause\|resume\|stop\|destroy}`，后端权威状态机 |
| GET | `/api/v1/recordings?project_id=` | 列表 |
| GET | `/api/v1/recordings/{id}` | 详情：会话 + 事件流 + 图谱写入指标 |
| GET | `/api/v1/recordings/{id}/graph` | 该录制在 Memgraph 中的子图（前端可视化） |
| DELETE | `/api/v1/recordings/{id}` | 删除（PG 删行 + Memgraph 删 Recording/HAS_STEP/Action 子图；Page/Element 保留） |

资源检索复用既有端点：`GET /api/v1/knowledge/projects`（项目列表）、`/api/v1/knowledge/graph`（图谱查询），另在录制路由下加 `GET /api/v1/recordings/resource-check?project_id=&keyword=` 供编排层三源判定（或直接由 runtime 内部调服务，不暴露 HTTP——**推荐内部调用**，避免前端绕过编排直接查询导致状态不一致）。

新增后端文件（均有模板可仿）：

```
src/schemas/recording.py                           # Pydantic 契约（仿 schemas/sponsor.py）
src/infrastructure/recording_store.py              # PG 事件流 store
src/application/exploration/recording_graph_store.py  # Memgraph 写入（仿 ui_graph_store.py）
src/application/recorder/recorder_session_service.py  # 会话编排：驱动生命周期 + 事件聚合 + 固化 + 控制状态机
src/application/recorder/drivers/embedded_bridge.py   # Electron 侧登记/事件转发对接
src/application/recorder/drivers/cdp_attach.py        # connect_over_cdp
src/application/recorder/drivers/playwright_managed.py
src/application/recorder/assets/recorder.js           # 注入脚本（后端持有，保证三端同版本）
src/api/routes/recordings.py                          # 路由
```

`UIAutomationModeRuntime` 改造点（`src/modes/ui_automation_mode/runtime.py`）：
- `_resolve_request`：project_id 缺失 → `awaiting_project_selection`（附候选项目）；
- `_assess_knowledge` → `assess_ui_resources`：三源检索 + 判定理由；
- 缺口分支：`awaiting_exploration_selection` 替换为 `awaiting_recording_approval`（发起审批）；保留旧探索为审批拒绝后的可选项；
- 审批通过回调 → `RecorderSessionService.launch`；固化完成 → `task_generation_ready`。

## 9. 前端 / Electron 设计

### 9.1 Electron 主进程新增（`electron/main.js` 扩展）

- `recorder:create-window`：创建录制窗口（控制条 + WebContentsView，`partition: "persist:recorder"`）；
- `recorder:navigate` / `recorder:attach-debugger` / `recorder:set-capture` / `recorder:close`；
- debugger `message` → 过滤 `Runtime.bindingCalled(name === "__qaRecordEmit")` → 推渲染进程 → 批量 POST 后端；
- `preload.cjs` 按既有 `contextBridge` 模式扩展 `qaAgentDesktop.recorder.*`。

### 9.2 录制窗口控制条（录制窗口顶部，本地页面）

- 四按钮：开始 / 暂停·继续 / 结束 / 销毁（对应 4.2 状态机，按钮可用态由后端状态驱动）；
- 状态徽标：当前状态 + 已采集步数 + 当前页面 URL；
- 销毁需二次确认（不可逆，丢弃未固化数据）。

### 9.3 主界面侧

- 审批卡片（复用 ApprovalPanel）：显示项目、目标 URL、三源缺口原因、驱动选择（默认内嵌浏览器）；
- 录制中：会话面板追加录制实时步骤时间线（动作类型 + 元素名 + 缩略截图）；
- 结束后：步骤清单（可删误操作、补备注）→ 确认固化 → 跳转 KnowledgeView 看图谱子图；
- 非桌面端（纯浏览器访问）降级：驱动只显示 playwright-managed / cdp-attach。

### 9.4 与既有 UI 自动化模式契约的关系

- `contracts.py` 层级不变；录制归入 information_exploration 的新输入源；
- Agent 系统提示追加：优先查询录制产物（Recording/Action 子图），不足时才回退自主探索；
- 「测试执行」子方向后续以录制产物为输入启用：录制步骤 → 用例草稿 → 既有评审/固定版本/套件链路。

---

## 10. 分阶段实施计划

| 阶段 | 范围 | 验收标准 | 预估 |
|---|---|---|---|
| **P0 harness 主链路** | ① runtime 改造（项目反问 + 三源检索 + 录制审批分支）；② RecorderSessionService + embedded 驱动（Electron WebContentsView + debugger）；③ recorder.js（click/input/key/nav/scroll + 双定位）；④ 事件 API + PG 表 + RecordingGraphStore 固化；⑤ 录制窗口控制条四按钮；⑥ 审批卡片与状态时间线 | 桌面端 UI 模式输入"测试 XX 流程"→ 反问项目 → 检索无资源 → 审批通过 → 自动弹出录制窗口 → 操作 → 结束 → Memgraph 出现完整 Recording/Action/Page/Element 子图；PG 事件流可查 | 最大阶段 |
| **P1 外部浏览器** | cdp-attach（Chrome/Edge + connect_over_cdp）；playwright-managed；同源 iframe 桥接；跨域 iframe CDP auto-attach | 用本地 Chrome（带登录态）录制同一流程，元素与 P0 收敛同节点 | 中 |
| **P2 回放与用例** | 回放执行器（定位决策链 + 像素兜底）；录制 → 用例草稿；断言建议 | 回放成功率 ≥ 既有探索链路基线；用例进套件冻结流程 | 中 |
| **P3 ego-lite 接入** | ego lite（等 Windows 版或 macOS 环境）经 cdp-attach / ego-browser 桥接入驱动注册表；可选借鉴其内核级快照提升元素解析 | 同一录制协议在 ego lite 跑通，零协议改动 | 小（预留位已就绪） |

## 11. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| ego-lite 无 Windows 版 | 高（预期管理） | 驱动抽象预留接入位；P0/P1 不依赖 ego-lite |
| WebContentsView 与主窗口 session 隔离，需重新登录一次 | 中 | `persist:recorder` partition 持久化；P1 cdp-attach 复用系统浏览器登录态 |
| 审批通过后桌面端不在线/未运行，启动失败 | 中 | 会话状态停留 launching 并告知用户；超时未 ready 可改选 playwright-managed 或重新发起 |
| 跨域 iframe 内操作漏采 | 中 | P0 明示限制；P1 CDP auto-attach 补齐 |
| Canvas/自绘组件无 DOM 可解析 | 中 | 像素三件套本就是为此设计；坐标兜底 |
| 高频事件打爆后端 | 低 | 缓冲 + 批量上报；scroll/input 节流合并；PG JSONB 追加写 |
| 敏感输入泄露 | 高（红线） | 密码/敏感字段只记长度；入 PG 前字段脱敏；遵守规则 2.5 |
| 反问打断体验 | 低 | 项目候选列表一次给全；同一 session 记住上次项目，仅歧义时反问 |
| 录制脚本与驱动版本漂移 | 低 | recorder.js 唯一源放后端 assets，启动时下发 |

## 12. 验证方案（对齐 AGENTS.md 第七章）

- **单元**：locator 生成器优先级、事件节流合并、脱敏、控制状态机迁移（非法迁移拒绝）；store 层不连库纯单测（仿 `tests/test_sponsor_config.py`）；
- **契约**：`recordings` API 覆盖正常/异常/边界/幂等（重复批次去重）/并发（多会话并行录制）；审批联动（未审批不得启动驱动）；
- **编排**：harness 全流程用例——意图识别 → 反问项目 → 三源检索（有/无资源两路）→ 审批（通过/拒绝两路）→ 启动 → 控制四按钮 → 固化 → 图谱就绪；
- **集成**：起 Memgraph + PG（docker-compose 已有），跑 P0 验收流程，断言图谱节点/边数量与去重指标；
- **回归**：既有 UI 探索链路测试全绿（录制是新增，不得破坏 `UIExplorationService`）；
- **可追溯**：回放失败的步骤事件原样回流为回归用例（脱敏后），遵守失败样本治理规范。

---

## 附：关键决策的依据索引

| 决策 | 依据 |
|---|---|
| 录制嵌入 harness 编排而非独立面板 | 用户定义流程；`runtime.py` 已有知识门控 + 反问（awaiting_input）骨架 |
| 审批复用既有机制 | `sessions.py` approvals API + `agent_session_approvals` 表 + 前端 ApprovalPanel + flow `waiting_approval` 状态 |
| 项目确认必须反问且用正式 project_id | AGENTS.md："禁止用自由文本项目名替代正式 project_id" |
| Electron 内嵌录制用 WebContentsView + webContents.debugger | Electron 官方文档；项目 electron ^31.7.7（`agent_web/package.json`） |
| 统一 CDP 注入协议作为切换抽象 | ego-lite `package/ego-browser/src/cdp-eval.ts`；Playwright 官方 `connect_over_cdp` |
| 图谱写入仿 UIGraphStore | `src/application/exploration/ui_graph_store.py` 完整 MERGE/去重实现 |
| 事件直写 REST 不过 Agent 决策循环 | 录制为高频结构化数据；编排层只在启动/固化两个低频节点介入 |
| 双定位决策链「定位链 + bbox 相对偏移 + 绝对坐标」 | ego-browser SKILL.md 语义/视觉双工作流工程实证 |
| ego-lite 列 P3 预留 | `项目借鉴/ego-lite/README.md`："runs on macOS today. Windows and Linux are on the roadmap" |
