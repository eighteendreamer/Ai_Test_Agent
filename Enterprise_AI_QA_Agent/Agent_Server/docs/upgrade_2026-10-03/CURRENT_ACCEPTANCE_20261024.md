# 2026-10-24 当前升级验收进度

本记录依据当前工作树、MySQL 默认模型配置、隔离验收和真实外部请求更新。完整升级目标仍未完成；上一日记录见 `CURRENT_ACCEPTANCE_20261023.md`。

## 默认模型两轮切换

用户当天在 MySQL 配置库中两次切换激活模型（只读核对 `llm_model_config`，验收未写入）：

- 11:37–11:38：`step-5-preview`（id=21）停用，`deepseek-reasoner`（id=17）激活，`task_execution` 默认解析为 `deepseek-reasoner`。
- 12:24:59：id=17 行原地改为 `deepseek-flash`（provider=deepseek，transport=`openai_chat_completions`，base_url=`https://api.deepseek.com`），当前默认为 `deepseek-flash`。

两组模型各做过应用自身 `OpenAIChatClient` 的最小真实探针（非流式、流式、工具调用、流式+工具调用），均通过：密钥与订阅可用，Step 时代的 `no active step plan subscription` 阻塞不再存在；`deepseek-reasoner` 返回 `reasoning_content`，`deepseek-flash` 工具调用与流式 delta 正常。验收测试自带的全局默认配置身份守卫在 12:24:59 切换窗口如实触发过一次失败（`p2-default-full-deepseek-current-20261024-01`），属于守卫正确工作，不是产品缺陷。

## 上传内容类型检测挂死缺陷（本轮发现并修复）

### 发现过程

运行默认模型 P2 闭环验收时，生产 API 在 `POST /registry/api-docs/upload` 上无错误日志失联（两次失败：连接重置与 240 秒读超时），复现于 `p2-default-multi-case-deepseek-current-20261024-01/02/03/`。py-spy 抓取挂住进程的主线程栈，定位到 `upload_security_service._detect_content_type` 在**事件循环线程**上同步 `import magic`，卡死在 `magic/compat.py` 模块级 `Magic(_open(MAGIC_MIME))` 的原生 `magic_open()` 调用。

### 根因

python-magic 0.4.27 的加载器会接受 PATH 上的 `msys-magic-1.dll`（本机命中 Git for Windows 自带的 `E:\Git\Git\usr\bin\msys-magic-1.dll`，MSYS2 版 libmagic 对原生 CPython 不兼容），加载后 `magic_open()` 挂死或段错误。原实现的 `import magic` 位于同步调用与 `except Exception` 内：异常可被兜底，**挂死不能**；且发生在事件循环线程上，一次上传即冻结整个 API。2026-10-23 及之前在 PowerShell 下运行时 PATH 不含 Git 的 `usr\bin`（注册表 PATH 仅 `E:\Git\Git\cmd`），`import magic` 快速失败走签名回退，故未暴露；Git Bash 会话注入 `usr\bin` 后必现。

### 修复

`src/application/security/upload_security_service.py`：

- `_detect_content_type` 改为异步：libmagic 检测放入守护线程并有界（`_MAGIC_DETECTION_TIMEOUT_SECONDS=10.0`），事件循环不再被阻塞；超时/导入失败记入结构化日志（`upload_magic_detection_disabled` / `upload_magic_detection_unavailable`），不再静默吞错。
- 检测结果按进程缓存：超时或 `ImportError` 后标记不可用，后续上传直接走既有字节签名回退，不再重复挂起。
- 守护线程保证即使原生调用挂死也不阻塞解释器退出。

### 修复验证

- 触发环境下端到端调用 `secure_store_upload`：首次 10.51 秒超时后回退成功（`decision=allow`），第二次 0.03 秒，进程正常退出，RustFS 临时/正式对象均删除。
- 既有套件无回归：`tests/test_api_doc_original_assets.py tests/test_api_docs_origin_security.py` **16 passed, 12 skipped**；`tests/test_case_import_service.py` **8 passed, 6 skipped**。
- 新增回归 `test_content_type_detection_falls_back_when_libmagic_hangs`（模拟挂死 magic 模块）：断言 0.1 秒内回退、状态缓存后二次检测瞬时完成。

## 默认 DeepSeek 模型 P2 多用例依赖闭环验收通过

`RUN_LIVE_DEFAULT_COVERAGE_PRODUCTION=1`、`QA_DEFAULT_CASE_SHAPE=multi-case-dependency`、隔离 Redis DB 13 下执行 `tests/test_default_coverage_production.py::test_actual_default_model_plans_and_executes_complete_case`：

```text
1 passed in 98.94s
```

证据：`p2-default-multi-case-deepseek-current-20261024-04/` 与同名 JUnit XML。

- 默认模型身份：全程 `deepseek-reasoner` / `openai_chat_completions`，`execution_model_role=default`，`global_default_unchanged=true`（真实 MySQL 配置未被测试改写）。
- 规划（真实默认模型一次流式调用）：正确将"创建临时订单并记录实际ID"补入为前置依赖（`kind=dependency`），"查询订单详情"为目标（`depends_on=[create-order]`），`order_id` 经 `input_bindings` 引用前置实际输出；无关的"商品排序"用例未被选取；`unresolved=[]`，规划理由引用正式用例版本证据。
- 业务执行（真实受控 HTTP 目标）：`POST /orders` 创建、`GET /orders/{实际ID}` 查询、`DELETE /orders/{实际ID}` 清理各一次；`actual_created_ids` 与实际请求一致，`remaining_orders={}`（清理完成）。
- 执行条目两个均为 `passed`；流式 SDK 观测记录了 chunk 计数、首块耗时与 `reasoning_content` 事件。
- 本轮仅关闭"按用例执行"的多用例依赖闭环：默认模型的更广场景质量、未知副作用只读查证、其他测试模式与完整产品流程仍按总表继续推进。

## P2 其余两种形态默认模型验收通过（deepseek-flash）

同一测试在另外两种 `QA_DEFAULT_CASE_SHAPE` 下以当前默认 `deepseek-flash` 通过：

| 形态 | 结果 | 证据 |
|---|---|---|
| full | `1 passed in 140.55s` | `p2-default-full-deepseek-current-20261024-02/` 及同名 XML |
| single-create-cleanup | `1 passed in 77.94s` | `p2-default-single-create-cleanup-deepseek-current-20261024-02/` 及同名 XML |

- full：规划出 2 个准备步骤（按需求文档创建两条订单并保存各自 `order.id`）、1 个目标（依赖两个创建项并绑定 `actual_order_id`）、2 个清理步骤（DELETE 两条订单，理由引用需求文档对 DELETE 的约定）；真实业务 HTTP 共 5 次（2×POST、1×GET、2×DELETE），`remaining_orders={}`，`unresolved=[]`，Run 条目 passed。
- single-create-cleanup：规划仅 1 个 target（创建订单、用真实返回 `order.id` 清理并核对状态码）；业务 HTTP 为 POST+DELETE 各一次，`remaining_orders={}`，Run 条目 passed。
- 两次运行 `global_default_unchanged=true`；每轮证据的 `verification.json` 记录了默认模型身份与全部业务请求。

### 共享测试流的验收基础设施边界（本轮确认）

前两次 full/single 启动失败（规划任务停留 `queued`、规划 Worker exit 0），根因是测试 Redis DB 13 的共享流 `qa:tasks:case_planning` 中残留此前失败运行的投递：测试使用的规划 Worker CLI 为单批消费（`--once`），首批读到指向已销毁隔离数据库的旧条目时按 `ValueError(规划投递没有对应的项目持久任务)` 结束，本轮自己的条目排在后面未被消费。删除该流键后重跑即通过。生产 Worker 是常驻循环（`run()`），失败投递走转移/死信后继续消费，不受此影响；故这属于验收基础设施边界而非产品缺陷。按既有约定，本轮各次运行前精确清理 DB 13 的 `qa:tasks:*` 与 `qa:session:*:events` 测试键，运行后复核仅余常驻键（`qa:fencing:agent`、`qa:quota:limits`）。

## Agent API 默认模型完整流程验收通过（deepseek-flash）

`RUN_LIVE_API_AGENT_WORKER=1`、`RUN_LIVE_API_AGENT_WORKER_DEFAULT=1`、隔离 Redis DB 13 下执行 `tests/test_api_agent_worker_live.py::test_real_agent_worker_private_auth_and_terminal_redelivery` 的 `default-model` 两个变体：

| 变体 | 结果 | 证据 |
|---|---|---|
| default-model-same-process-child | `1 passed in 82.77s` | `p3-api-agent-worker-default-deepseek-current-20261024-01/configured-default-in-process/` |
| default-model-durable-child-process | `1 passed in 99.52s` | 同目录 `configured-default-durable/` |

默认模型身份：`deepseek-flash` / `openai_chat_completions`（复制到隔离配置表，真实配置只读）。断言与证据覆盖：

- 真实默认模型驱动完整 API Agent 链路：持久化任务提交 → 独立 Worker 领取 → 模型工具调用 → 业务 HTTP。持久变体记录 `physical_model_http=10` 次真实模型 HTTP（同进程变体同样 >0）。
- 业务目标（受控登录/刷新/详情三接口）最终 `[200,200,200]`；依赖传值与鉴权续期按固定 campaign 执行；父任务 `completed`。
- 报告 ToolJob 恰 1 份且 `completed=3, failed=0`；持久子进程场景 3 个子会话全部属于本项目。
- 登录与刷新凭据加密存储（2 条），且不出现在公开输出、会话记录、ToolJob 或进程日志中。
- API 重启后任务历史保留；终态重放 completed 任务 0 次新增业务 HTTP、0 次新增模型 HTTP。
- 场景中的中断/审批等待/Worker 重启边界由测试按参数实际驱动并通过。

这关闭了总表中"Agent API 测试——完整默认 Agent 流程仍未验收"的缺口；剩余缺口为更完整的断网/进程故障组合与产品页面报告的默认模型路径对账。

## 正式回执与恢复：默认模型纠正语义与 UI 验收（deepseek-flash）

### 默认模型纠正调用语义验收通过

`RUN_LIVE_DEFAULT_RECEIPT_STREAM=1` 下执行 `tests/test_default_receipt_stream_live.py::test_default_correction_uses_frozen_stream_and_saved_response_once`：

```text
1 passed in 61.71s
```

证据：`p2-receipt-stream-default-deepseek-current-20261024-01/` 及同名 XML。受控图提供确定性无效的原始回执，**纠正调用本身走真实默认模型**（`deepseek-flash`，流式冻结协议 `case-receipt-stream-response-v1`，真实 MySQL/PostgreSQL/RustFS 与受控 HTTP 目标）：纠正物理请求恰 1 次、响应落盘后评估通过、条目 `passed`；业务 POST/GET/DELETE 各一次；重放已完成的 execute 不新增业务或模型请求（`terminal_extra_business_requests=0`、`terminal_extra_model_requests=0`）；纠正请求/响应原件哈希逐一核对。该测试为首次运行即通过。

### 默认 AI UI 测试正式通过（positive + negative）

`RUN_LIVE_DEFAULT_UI_SCENE=1` 下执行 `tests/test_default_ui_scene_live.py::test_default_ui_reads_fixed_scene_and_verifies_actual_page` 两变体：

```text
2 passed in 152.42s
```

证据：`p2-default-ui-scene-deepseek-current-20261024-02/`（positive/、negative/）及同名 XML。

- 真实默认模型（`deepseek-flash`）完成"规划 → 读取固定场景知识（knowledge-rag 引用场景版本内容哈希）→ 真实 Chromium 打开项目页 → click → 只读 eval 回读"全链路；`uses_global_default_model=true`。
- positive：实际计数与预期一致，条目正式 `passed`；浏览器证据（transcript/截图）持久化到 RustFS 并可经 API 读回，页面身份与录制页区分（`page_id` 不同）。
- negative：页面实际计数不符时，条目正式 `failed`——默认 AI 如实报告失败而不是迁就预期。
- 场景内的审批等待、浏览器资源租约、资源清理 Worker 与终态重投均按测试断言通过。

### 回执纠正"真实进程中断"的默认模型缺口（本轮如实记录）

`tests/test_default_receipt_process_recovery_live.py`（在纠正调用进行中真实 `taskkill` 生产 API 进程树、自然租约过期后由生产恢复 Worker 接管）的默认模型路径本轮首次实跑：规划与执行均由真实 `deepseek-flash` 完成，但 **Agent 首次回执即通过全部校验**（只读健康检查用例），纠正阶段从未进入，`waiting_approval` 后条目直接 `passed`，测试在等待纠正边界处失败（证据 `p2-default-receipt-recovery-deepseek-current-20261024-01/invoking/`）。该测试写于 Step 时代，前提是"默认模型的原始回执大概率无效"；对回执质量足够高的默认模型（deepseek-flash），该前提不成立，且不存在向独立 API/Worker 进程注入确定性无效回执的测试钩子（`AgentRegistry` 为进程内硬编码）。

当前状态判定：纠正管线的持久化/恢复/不重复语义已由两路证据覆盖——受控模型版本的真实进程中断两窗口（2026-10-23，`p2-receipt-process-recovery-current-20261023-response/invoking`）+ 本轮真实默认模型纠正调用语义（stream 版）。**剩余缺口**仅为"真实默认模型纠正调用进行中的进程中断"这一组合场景，需要先设计确定性触发机制（产品级测试钩子或可确定性触发首轮回执校验失败的用例设计）再补验收；不建议通过篡改模型响应伪造。

### 验收基础设施根因修复：隔离运行时的任务流按运行唯一化

本轮第 3 次复现"一次性规划 Worker 消费到上一运行的遗留投递后提前退出"（`case_planning_task_claim_skipped` → 退出，本运行条目停留 `queued`）。根因：`isolated_runtime` 已将 runs/agents 流与消费组按运行唯一命名，但 `case_planning`/`cleanup`/`compaction`/`embedding`/`vector_rebuild` 五个后缀解析流仍沿用生产名，共享流中前序运行（尤其被杀运行）的投递会先于本运行条目被单批 `--once` Worker 读到，其任务行在当前隔离库不存在而失败退出。修复（`tests/test_execution_worker_cli.py`）：`case_planning`/`compaction`/`embedding`/`vector_rebuild` 四个一次性任务流注册为 `qa:test:{suffix}:*`，与既有命名模式一致；**cleanup 流保持共享**——租约登记表位于全局 `qa:` 命名空间，清理投递必须能被后续运行的 Worker 继续消费（详见下节 Docker 安全验收的发现）。生产常驻 Worker 不受影响。修复后 UI 两变体连跑通过（此前同批运行必现失败）。运行后复核 Redis 仅余常驻键。

## Docker 性能与安全：默认模型完整闭环验收通过（deepseek-flash）

### 性能：默认 Agent 自主规划 k6 全链路

`RUN_LIVE_DEFAULT_PERFORMANCE_AGENT=1` 下执行 `tests/test_default_performance_agent_live.py::test_default_agent_actual_k6_report_and_terminal_replay`：

```text
1 passed in 103.16s
```

证据：`p5-docker-performance-default-deepseek-current-20261024-02/` 及同名 XML。

- 真实默认模型（`deepseek-flash`）在完整生产 API + 独立 Agent Worker 中驱动 performance-testing-agent：用户消息 → 自主规划 k6 方案（1 VU closed、2 秒、SLA P99<1000ms）→ 3 次审批 → 真实 Docker 冒烟 + 负载两次执行 → 正式报告。会话 `completed`，模型物理 HTTP 共 3 次，`global_model_unchanged=true`。
- 真实指标落 PostgreSQL `perf_runs` 恰 1 行：samples=6619、p50/p95/p99、error_rate=0、throughput ≈3309 tps，`verdict=pass`；指标与 `performance-test-runner` ToolJob 的 `metrics` 完全一致。
- 原件下载：冒烟+负载两个 execution 的证据工件经 API 读回并核对 SHA-256（`performance-execution-v1` schema）。
- 构建版前端实测（Playwright）：报告中心列表 → 详情"良好"/pass → 指标 JSON 下载内容与 ToolJob metrics 一致，页面零错误、无 ≥400 网络请求。
- API 重启后终态重投：模型 HTTP、ToolJob 数、perf_runs 行数三项全部零新增。

### 前端密闭性修复：字体图标改为本地自托管

性能验收首轮在最后的前端步骤失败（`Page.goto` 30 秒 load 超时，页面最终渲染正常）。逐请求复现定位：`agent_web/index.html` 此前从 `cdnjs.cloudflare.com` 加载 font-awesome 6.4.0——该公网请求一旦悬挂即阻塞 `load` 事件，验收环境对公网延迟敏感且产物不密闭。修复：CSS 与 8 个 webfont 文件自托管到 `agent_web/public/font-awesome/`（Vite 原样拷入 dist，CSS 内相对路径不变），`index.html` 改为本地引用。复现脚本确认构建产物零外网请求、字体本地加载；修复后性能验收整体通过。首轮失败证据保留于 `p5-docker-performance-default-deepseek-current-20261024-01/`。

### 安全：默认配置模型驱动的 Docker 安全战役

`RUN_LIVE_SECURITY_AGENT=1`、`QA_SECURITY_AGENT_MODEL_NAME=deepseek-flash` 下执行 `tests/test_security_agent_live.py::test_configured_security_agent_runs_docker_campaign_and_terminal_replay`：

```text
1 passed in 107.58s
```

证据：`p5-security-agent-deepseek-current-20261024-05/` 及同名 XML。真实默认配置模型（显式选择 `deepseek-flash`，与全局默认同源同参）驱动安全 Agent 完成真实 Docker 扫描战役：自有回环 HTTP 目标（故意缺失安全头）→ Agent 规划并执行容器化扫描 → 发现解析与报告 `report_verified=true` → 终态重投零新增（`sdk_calls=0`、`physical_model_requests=0`、`target_requests=0`、`tool_jobs=0`）；Docker 资源租约全部结清（`pending=0/active=0`）。

### 配套根因修复（本轮发现）

1. **清理流与全局租约登记表的命名空间必须一致**（`tests/test_execution_worker_cli.py`）：安全验收前三轮均失败于 `assert not pending_docker`。排查确认租约登记表 `qa:lease:metadata` 是全局共享命名空间，而上一轮把 cleanup 流改为按运行唯一——运行内未处理完的清理投递随隔离运行时销毁，登记永久滞留 `cleanup_pending`，且其流消息已不存在、任何后续 Worker 都无法收敛（三轮失败的 `external_resource_id` 完全相同，即同一批滞留条目反复绊倒后续运行）。修复：cleanup 流恢复共享，使遗留投递可被后续运行消费；另精确清理了本机测试遗留的 7 条死会话登记。教训：登记表全局的队列，其流也必须全局。
2. **安全测试的资源断言按 project 过滤**（`tests/test_security_agent_live.py`）：`list_cleanup_pending()`/`list_active()` 此前不带项目过滤，在全局登记表上断言——生产部署中其他项目的合法滞留条目也会令其失败。改为 `project_id=project["id"]` 限定本战役。
3. **清理 Worker 补齐运行日志**（`src/cli/run_resource_cleanup_worker.py`）：该 CLI 是唯一未调用 `configure_logging()` 的入口，处理过程与失败完全不可见（本轮排查的直接障碍）。补齐后与其他 CLI 一致。

## 需求变更与追踪：默认模型更广业务闭环验收通过（deepseek-flash）

新增验收 `tests/test_default_requirement_change_live.py::test_default_model_requirement_change_closed_loop`（门控 `RUN_LIVE_DEFAULT_REQUIREMENT_CHANGE=1`），以真实默认模型驱动完整需求变更闭环：

```text
1 passed in 186.50s
```

证据：`p4-requirement-change-default-deepseek-current-20261024-03/` 及同名 XML。业务场景为订单查询需求的真实变更（V1：total 为商品原价合计 → V2：total 改为折后应付合计并新增 discount 字段），区别于既有受控验收的保留期场景：

- **上传→解析→比较**：V1/V2 经真实上传 API 与独立解析 Worker 建立两个解析快照，独立差异 Worker 完成确定性比较（不经 AI）。
- **影响分析（真实默认模型）**：impact-coordinator-v2 协议下默认模型自主完成多轮协调——5 个单元全部 `analyzed`，产出 **2 条准确命中变更语义的规则**（"total 由商品原价合计改为折后应付合计"、"新增 discount 字段，无优惠时为 0"），7 处新版本来源引用，`semantic_status=pending_review`、`deployment_status=unknown`、状态 `partial`；物理模型 HTTP 仅 2 次。
- **需求评审与追踪**：按规则提交正式需求评审（2 个验证点、case_links 关联既有正式用例版本），追踪矩阵两个验证点均为 `design_covered`、执行状态 `not_assessed`、部署 `unknown`（设计与执行分离呈现）。
- **增量草稿（真实默认模型）**：`include_new_cases=True` 触发两个模块（既有用例新草稿版本 + 新用例草稿），均 `completed`；旧活动版本未被改写（`active_version_id` 不变）、新用例为 `draft` 生命周期，草稿 `generation_metadata` 记录需求版本来源；物理模型 HTTP 2 次。
- **终态重投**：两个生成模块重放后任务状态与结果完全一致，新增模型 HTTP 为 0。
- 全程 `global_model_unchanged=true`；真实 MySQL 配置只读。

本轮首次运行（`...-02/`）在 14 秒时遭遇一次未捕获的 API 连接中断（`httpx.ReadError`，当时未保留进程日志，无法归因）；相同代码立即重跑即完整通过，测试已补充进程日志留存以便未来取证。该测试为本轮新增验收源码，按仓库先例强制登记。

这关闭了总表"需求变更与追踪——更广业务场景的默认模型质量验收"在 API 生产链路的核心缺口；仍缺：用户业务评审的产品页面交互、部署/环境适用性（applicability/planning/admission）的默认模型路径，以及其他格式与更多故障组合。

## 普通需求文档全量生成：默认模型模块进度与独立恢复验收通过（deepseek-flash）

新增验收 `tests/test_default_document_generation_live.py::test_default_model_ordinary_document_modules_and_independent_recovery`（门控 `RUN_LIVE_DEFAULT_DOCUMENT_GENERATION=1`），流式/非流式两个变体：

```text
2 passed in 691.71s
```

证据：`p1-document-generation-default-deepseek-current-20261024-05/`（plain/、stream/）及同名 XML。每个变体的完整链路：

- **全量生成质量（第一个生成组）**：真实默认模型对商城需求 Markdown 的 3 个模块各执行 1 次真实调用（非流式与流式各 3 次），全部 `completed`；每个模块的 `generation_review` 通过全部语义门（`mapping_state=complete`、候选清单完整、逐条无 validation_issues、step_refs 均有 expected_path）；共 9 个用例草稿（每模块至多 3 条），`generation_metadata` 记录解析快照来源；模板提交后修改不影响已固定范围。
- **模块进度与独立恢复（第二个生成组）**：故意将 `max_response_bytes` 降至 1500，3 个模块的真实响应全部在落盘后按 `response_validation_rejected`（checkpoint `response_saved`）失败；恢复策略 `saved_response` 逐个重试——**重试全程 0 次模型调用**（仅使用 RustFS 中已保存的真实响应），模块状态保持 `failed` 供人工评审。
- **采样差异如实记录**：恢复重试中，非流式 2/3 完成、流式 1/3 完成，其余被子任务的质量门以 `draft_commit_rejected` 拒绝（响应仍保留）。测试断言接受"完成或被门拒绝"两种合法结局并记录明细；机制断言（0 模型调用、响应保留、门拒绝保持失败态）全部成立。测试设计观察：恢复生成冻结的是被故意替换为测试字符串的写作指导模板，该无意义指导可能推高了此段的门拒绝率；质量生成（正常模板）6/6 全过。
- 两个变体 `global_model_unchanged=true`；终态重投零模型调用（质量组逐模块重放后 send 数不变）。

排查过程记录：首轮两变体失败于共享文档流（`qa:tasks:document_parsing` 等为进程级常量）的跨运行旧投递——流式变体的一次性解析 Worker 消费了非流式变体的遗留条目；已在测试开始处增加共享流清理，并在运行间保持既有清理纪律。另一处修正：恢复重试的错误码存在采样差异（`response_validation_rejected` / `draft_commit_rejected` 两种质量门），断言按"完成或被门拒绝"设计而非固定错误码。

这关闭了总表"模板、生成、Excel 导入——普通需求文档全量生成的模块进度和独立恢复"的默认生成语义质量缺口（10-23 的受控版本明确不证明默认模型生成质量）；该方向仍缺：默认/项目模板与 Excel/JSON 导入的完整项目页面生产流程验收（页面交互层）。

## 需求变更与追踪：用户业务评审与环境绑定规划验收通过（deepseek-flash）

新增验收 `tests/test_default_business_applicability_live.py::test_default_model_environment_applicability_and_bound_planning`（门控 `RUN_LIVE_DEFAULT_BUSINESS_APPLICABILITY=1`）：

```text
1 passed in 179.33s
```

证据：`p4-business-applicability-default-deepseek-current-20261024-05/` 及同名 XML。场景：订单需求 V1→V2（total 口径变更+discount）经真实影响分析（默认模型）产出规则与需求评审后，部署到两个真实本地环境——旧语义部署（total=1000 无 discount）与新语义部署（total=900、discount=100）：

- **用户业务适用性评审（人工决策，0 模型调用）**：两个环境各经真实业务环境注册、配置、快照（config revision/sha256 固定），并用 `api-test-runner` 发起真实 HTTP 观察旧/新部署的 `/app/orders/1` 实际响应（total=1000 / discount=100，`target_status=matched`）；用户分别提交适用性决策——旧环境两点 `not_effective`（新语义未生效，无用例结论可下）、新环境两点 `effective` 且用例版本 `valid`，观察以 sha256 固定；旧环境结论按 revision=2 撤回。决策全程模型 HTTP 为 0（业务评审是用户职责）。
- **环境绑定规划（真实默认模型）**：用例激活后提交绑定新环境快照（`expected_snapshot_sha256`）的规划任务，独立规划 Worker 以真实默认模型完成——plan 的 `environment.snapshot` 与适用性评审逐字一致，决策项引用绑定用例版本；物理模型 HTTP 2 次；启动 Run 后 `plan_snapshot.plan.environment` 完整传播到持久 Run。
- 全程 `global_model_unchanged=true`。

本轮同时根因修复了一个贯穿全天的瞬时故障：**Windows 上 python-magic（无捆绑 libmagic）绑定 PATH 上的不兼容 DLL 后，`import magic` 除挂起外还会随机段错误杀死整个 API 进程**（线程模型不可捕获；今日多次"未归因 ReadError"均为此）。修复（`upload_security_service`）：Windows 直接跳过 libmagic 检测走字节签名回退，POSIX 保留有界检测；上传安全回归 17 passed，上传实测 0.65s 完成（原 10s 超时）。此前"需求变更验收首轮 ReadError"等记录的未归因故障在此一并归因。

这关闭了总表"需求变更与追踪——用户业务评审"的验收缺口；仍缺：部署/环境适用性中的执行期准入（admission）默认模型路径与产品页面交互层。

### 执行期准入（admission）默认模型路径验收通过

同验收扩展执行段后（同一测试 `test_default_business_applicability_live.py`）：

```text
1 passed in 187.94s
```

证据：`p4-business-applicability-default-deepseek-current-20261024-06/` 及同名 XML。在环境绑定规划与 Run 启动之后：

- 提交"当前状态未知"修订评审（points/cases 全部 `unknown`，`expected_review_id` 指向原评审）——模拟上次确认后部署状态可能变化的真实场景。
- 独立执行 Worker 以真实默认模型执行 Run：条目 `passed`、Run `completed`；结果 `actual.environment_admission` 记录 `{reviews: [unknown 修订], decision: no_explicit_invalid}`，且该准入快照原样持久化到执行 attempt（`execution_attempt_id` 回读一致）——"无显式失效即放行、结论留痕"的准入语义在默认模型路径成立。
- 执行物理模型 HTTP 2 次；终态重投后 Run 与结果完全一致、零新增模型调用。
- 全程 `global_model_unchanged=true`。

这关闭了总表"需求变更与追踪——部署/环境适用性"在 API 生产链路的验收缺口（applicability → planning → admission 全链默认模型/人工决策路径）；仍缺产品页面交互层。

## 按用例执行：产品页面完整流程验收通过（deepseek-flash）

新增验收 `tests/test_default_coverage_product_live.py::test_product_dialog_plans_launches_and_executes_with_default_model`（门控 `RUN_LIVE_DEFAULT_COVERAGE_PRODUCT=1`）：

```text
1 passed in 108.94s
```

证据：`p2-coverage-product-default-deepseek-current-20261024-09/` 及同名 XML。真实产品 `CasePlanDialog` 组件（Vite 服务的产品源码页面 + Playwright）驱动完整链路，后端为完整生产 API 与独立 Worker，模型为真实默认 `deepseek-flash`：

- **页面规划**：真实对话框填入测试目标（多用例依赖场景）→ 点击"生成执行计划"→ 独立规划 Worker 以真实默认模型完成规划；计划结构正确（`dependency` 补入创建订单用例 + `target` 查询用例，理由引用 order_id 传递约束；无关的商品排序用例被排除）。页面代理以真实 token 注入认证（与桌面模式同机制），页面零错误。
- **页面启动**：点击"启动测试运行"→ 对话框确认"已保存运行"并回报 Run 身份。
- **真实执行**：独立执行 Worker 循环驱动（含审批处理），条目全部 `passed`，真实业务 HTTP POST/GET/DELETE 发生且清理完成；执行物理模型 HTTP 共 10 次；`global_model_unchanged=true`。
- **终态重投**：重放派发后 Run 与结果逐字一致、零新增模型调用。

过程记录（均为真实产品行为的边界确认，非测试特判）：规划校验对模型采样差异可能拒绝（本轮 10-24-07 运行规划两次校验未过而 failed），测试按产品用法走"另建规划任务 → 重新生成"的 UI 重试路径并保留失败尝试证据；对话框以 `modes` 列表首项作为默认模式，验收显式传入 `api_testing` 模式（此前一轮以首项默认模式启动被执行 Agent 正确 blocked——"未授予任何 API 执行工具"，属于模式与工具授予的正确联动）。

这关闭了总表"按用例执行——完整产品流程"的缺口（产品对话框 → 默认模型规划 → 启动 → 默认模型执行 → 结果与终态重投，全链真实）；该方向仍缺：未知副作用只读查证与执行中计划修订的专项（API 级已有受控证据）。

## 执行中计划修订：默认模型专项通过（提示词编排示例 + 夹具补全后）

同验收（`tests/test_default_execution_replanning_live.py`，门控 `RUN_LIVE_DEFAULT_EXECUTION_REPLANNING=1`）在两项改进后**通过**：

```text
1 passed in 40.83s
```

证据：`p2-execution-replanning-default-deepseek-current-20261024-12/` 及同名 XML。deepseek-flash 自主完成完整修订编排：文档探索 → **只读 GET 缺口端点（`gap.read`，请求精确一次）** → 真实拿到 409 + `required_case=item-4` → `inspect-run-revision` → `submit-run-revision`（reason 逐字引用真实响应体，evidence 引用 `task_result.response_body.required_case` 最窄路径）→ submit 回执丢失后按独立读键 `status-run-revision` 查询恢复 → 最终 blocked 回执（唯一 JSON 对象）；持久修订 applied、`plan_revision=1`、新增未领取项、已领取项 attempts/results 不变。全程 6 次模型调用。

两项改进（前两轮失败→逐层定位的完整过程保留于 `-01`~`-11` 证据目录）：

1. **产品：`execution_replanning` 提示词小节补充精确编排示例**（`prompt_assembly_service.py`）——四步合法参数形状（`_case_action`/`operation_key`/完整绝对 URL/回执唯一 JSON 形状），并引导"直接 api-test-runner 只读验证优先，勿先走库检索"。受控回归 `RUN_LIVE_EXECUTION_REPLANNING=1` 全程 **5 passed** 无破坏。
2. **测试基建：夹具对真实模型的补全**——`agent_runtime` 的 `ToolRuntimeService` 此前未注入 `api_docs_service`（模型调文档库即抛错触发 L5；产品环境无此缺口）、轮次租约 15s 对真实模型多轮不足（参数化默认不变）、夹具文档内容参数化（默认模型验收传入含完整端点 URL 的文档，与真实项目文档语义一致——此前脚本靠注入 URL 跳过了相对路径解析，模型在文档只有相对路径时正确诊断并转而引用文档证据提交修订，行为合理但偏离本验收剧本）。

中途如实记录的两个非问题发现：模型一轮把文档 URL 末段拼重（`/execution-gap/execution-gap`）——定位为验收文档自身拼接错误，模型是逐字复制（verbatim 指令生效）；受控断言期望的端点 URL 本就含路径段。

这关闭了总表"按用例执行——执行中计划修订"的默认模型专项（此前记录的选项 (a) 已实施并验证）；"未知副作用只读查证"由本验收的 gap.read 只读验证步骤与回放检查点专项共同覆盖。

## 正式回执与恢复：纠正阶段真实进程中断组合场景通过（deepseek-flash）

此前记录的设计缺口（deepseek-flash 首轮回执即过校验、纠正从不触发）已用**确定性触发机制**关闭：`default_model_probe` 新增"脚本化执行图"测试替身（`QA_DEFAULT_PROBE_SCRIPTED_GRAPH`）——执行图的模型轮次按消息历史幂等回放调用方脚本（第一轮返回只读工具调用、第二轮返回故意无效的回执文本），与受控 HTTP 模型服务器同性质；**只读纠正调用始终放行真实默认模型**。测试替身同时支持非流式与 SSE 流式合成（执行图固定流式），并手动补跑观察 hook 保持物理证据完整。测试侧将隔离模型 streaming 覆盖为 False（替身约束）并以真实默认配置注册。

`RUN_LIVE_DEFAULT_RECEIPT_RECOVERY=1` 下 `test_default_model_receipt_correction_api_kill_recovers_without_repeat` 两持久窗口：

| 窗口 | 结果 | 恢复后结果 |
|---|---|---|
| invoking（纠正请求已落盘、响应未发出时杀 API） | `1 passed in 360.42s` | `error` + `execution_recovery_required=true`（不伪报通过） |
| response（纠正响应已落盘后杀 API） | `1 passed in 352.16s` | `passed` |

证据：`p2-default-receipt-recovery-deepseek-current-20261024-04/`（invoking/、response/）及两个 JUnit XML。两窗口均为：脚本化执行图真实执行业务 GET /health（恰 1 次，审批恢复重放不重复——probe 轮次按消息历史选择实现幂等）→ 无效回执触发纠正 → **真实默认模型**的只读纠正调用进行中/完成后被 `taskkill /T /F` 杀掉 API 进程树 → 自然租约过期 → 生产恢复 Worker 接管 → 按检查点恢复（响应未知的如实 error / 响应已存的 passed）→ 终态重投零新增业务与模型请求。纠正请求/响应原件 RustFS 哈希核对一致。

这关闭了总表"正式回执与恢复——纠正阶段真实进程中断"组合缺口。至此该方向三要素（默认 AI UI 测试、真实默认纠正语义、纠正中进程中断+不重复操作）全部有默认模型实测证据。过程迭代（脚本计数不幂等导致审批重放时真实模型接管重打业务、SSE 合成、hook 手动补跑等）保留于 `-01`~`-03` 证据目录。

## 需求变更与追踪：产品页面交互层（影响分析+用户业务评审+追踪）通过

新增验收 `tests/test_default_requirement_review_product_live.py`（门控 `RUN_LIVE_DEFAULT_REQUIREMENT_REVIEW_PRODUCT=1`）：

```text
1 passed in 178.78s
```

证据：`p4-requirement-review-product-default-deepseek-current-20261025-09/` 及同名 XML。构建版 App（Vite preview 服务的产品构建）单浏览器会话走完，后端完整生产 API + 独立 Worker，模型为真实默认 `deepseek-flash`：

- **页面提交影响分析**：API 完成 V1/V2 上传→解析→确定性差异后，页面导航至比较结果页，"文档影响分析入口"面板配置模型（deepseek 默认键）、single_attempt、explicit 用例范围（读取 order-total-check 完整版本并勾选 V1）、环境/发布声明，点击"提交固定影响分析"；独立影响 Worker 以真实默认模型完成——5 单元全部分析、2 条规则命中变更语义（total 口径+discount 新增），与 API 级验收结论一致。
- **用户业务评审页面场景**（复用 `requirement_product_scenario.review_from_product`）：任务完成后页面显示"影响结果已保存"，打开"需求评审与追踪"面板完成 6 次真实评审——含丢回执后按原请求身份恢复（"提交结果尚未确认"提示→重载→"评审已保存"）、冲突修订被 409 拒绝、移除版本、跨项目 API 评审隔离；PostgreSQL 评审记录与历史逐字对账；全程 0 次新增模型调用（业务评审是用户职责）。追踪矩阵在场景各阶段断言（v1 覆盖→v2 移除呈现）。
- 页面零错误、无 5xx；`global_model_unchanged=true`。

过程修正（页面态事实，保留于 `-01`~`-08` 证据）：preview 代理空 token 需 API 免认证（与受控产品验收同模式）；设置区折叠需先展开；explicit 范围需勾选版本 checkbox；任务详情/评审按钮由 `activeTaskId` 驱动且评审场景的 reload 依赖同源 sessionStorage 提交恢复——因此提交与评审必须在同一浏览器会话（两个 preview 会话是不同端口/域，sessionStorage 隔离）；评审场景故意的 409 冲突拒绝不计入网络失败。

这关闭了总表"需求变更与追踪——产品页面交互层"缺口：该方向 API 链路（更广业务闭环、用户业务评审、环境绑定规划、执行期准入）与页面交互层均已实测。"其他格式与更多故障组合"仍属后续深化项。

## UI 录制与知识：默认 AI 实际使用录制知识完成断言——证据澄清关闭

复核确认本日已通过的 `test_default_ui_scene_live.py` 双参数验收（`p2-default-ui-scene-deepseek-current-20261024-02/`）**已完整覆盖**此缺口，此前记录沿用的"不代表默认 AI 使用录制知识已经验收"是 10-23 旧边界表述，现按证据澄清：

- **知识固化**：场景版本写入正式断言 `assertions=[{kind: page, target: #count, operator: equals, expected: 参数}]`（positive=1 / negative=2）与"计数按钮"别名（`positive/fixed-scene.json` 在案）。
- **知识读取**：测试断言 knowledge-rag 作业引用场景版本 `content_hash` 且含"计数按钮"——默认模型真实读取了固化知识而非自由发挥。
- **知识使用与断言判定**：positive（场景预期 1，真实点击后计数 1）→ 条目 `passed`；negative（场景预期 2，真实点击后计数 1 ≠ 预期）→ 条目 `failed`。两变体 `default_planning/default_execution/uses_global_default_model` 均为真。negative 变体正是"默认 AI 用场景预期对照实际值、不迁就预期、如实判败"的直接证据。

至此"默认 AI 实际使用知识并完成断言"关闭。该方向仍缺：closed Shadow DOM 的能力边界呈现（契约已声明 `unobserved`，产品页面呈现待专项）与跨文档关联的产品层深化。

### closed Shadow DOM 能力边界呈现验收通过（真实 closed root）

既有 open-Shadow 验收的 fixture 全部为 open roots，`closed_shadow_dom: "unobserved"` 此前只是无 closed root 样本上的字段默认值断言。新增 `tests/test_recording_closed_shadow_live.py`（门控 `RUN_LIVE_RECORDING_CLOSED_SHADOW=1`）在页面**真实构造 closed shadow root**（`attachShadow({mode:'closed'})`，内部含按钮与泄漏哨兵文本）后验证诚实边界：

```text
1 passed in 22.98s
```

证据：`p5-closed-shadow-boundary-current-20261025-01/` 及同名 XML。真实 Chromium + CDP 录制：

- **对照路径不受影响**：light DOM 按钮动作与响应正常记录关联。
- **closed 内物理点击的如实归因**：closed root 内按钮无法被选择器定位，测试以坐标物理点击（light DOM 的 echo 标记证明点击真实发生）；录制器把该点击**归因到宿主元素 `#closed-host`**（role None）——既不伪造 closed 内部定位（`#closed-action` 零出现），也不静默丢弃。
- **无内容伪造/泄漏**：closed root 内部哨兵文本（`closed-private-marker`）在全部事件证据中零出现。
- **契约标注一致**：全部 page_response 的 `closed_shadow_dom == "unobserved"`。

这关闭了"closed Shadow DOM 的能力边界呈现"：边界不是 JSON 字段存在，而是真实 closed root 存在时物理交互被诚实归因、内部内容不进证据、契约如实标注。

## 按用例执行：compatibility 测试模式默认模型专项通过

新增验收 `tests/test_default_compatibility_mode_live.py`（门控 `RUN_LIVE_DEFAULT_COMPATIBILITY_MODE=1`）——`compatibility_testing` 模式（`case_driven_policy: required`）此前只有 runner 持久化机制测试（14 项），无默认模型编排证据：

```text
1 passed in 46.37s
```

证据：`compat-mode-default-deepseek-current-20261025-07/` 及同名 XML。生产 API + 独立 Agent Worker + 真实默认模型（deepseek-flash，3 次物理调用），目标为本机受控 HTTP 产品页（经会话 `resource_scope` 受信，与其他默认模型验收同一 authority fixture）：

- **默认模型自主编排**：先调 `compatibility-test-runner` 的 `get_capabilities`（completed），再以完整参数（objective/entrypoint/target_url/test_scope/data_policy/product_name/product_type）发起 `draft_plan`——参数语义正确，无编造。
- **真实计划产物**：6 个环境（Windows/Chrome、Windows/Edge、Linux/Firefox、macOS/Safari、Android 14/Pixel 7、iOS 18/iPhone 16——跨桌面与移动的真实矩阵）、4 条用例、5 个风险项；`phase=awaiting_approval`（计划等待用户批准——正确产品语义，本验收按用户指示"只做计划"停在此相位）。
- **环境可用性诚实呈现**：每个不可用环境带 `availability=missing_runner` 与具体 `unavailable_reason`（如"需要 macOS Runner 或远程浏览器云 Provider"）——隔离环境无 Runner 注册时计划如实降级为 partial 并说明原因，不伪报可执行。
- 会话 completed、`global_model_unchanged=true`。

迭代中发现的产品正确行为（保留于 `-01`~`-06`）：无受信 scope 时目标安全策略拦截私有地址（`untrusted_private_target`），且默认模型被拒后**如实报告拦截详情、不编造计划**（`-02` 会话证据）——这本身是安全门与模型诚实性的双重验证。批准续作竞态（400 already handled）按已处理容错。

边界声明：本验收覆盖默认模型在 compatibility 模式下的**计划编排**（get_capabilities→draft_plan→产物与批准门）；矩阵执行（execute_approved_plan→Runner 调度→多环境执行→证据聚合）依赖已注册的浏览器 Runner 基础设施，属后续项（runner 持久化机制已有 14 项专项）。

## UI 录制与知识：人工操作 DeepSeek 完整实测通过

用户本人操作可见 Chromium 完成 `tests/test_recording_deepseek_live.py::test_actual_deepseek_manual_actions_are_recorded_and_diagnosed` 的人工模式（`RUN_LIVE_DEEPSEEK_RECORDING=1`、`DEEPSEEK_RECORDING_INTERACTION=manual`，pytest 需 `-s` 使 `input()` 可读——首跑因缺 `-s` 失败属测试运行方式问题）：

```text
1 passed in 87.26s
```

证据：`p5-deepseek-recording-manual-current-20261024-03/` 及同名 XML。用户在真实 DeepSeek 首页（HTTP 200、目标按钮各 1 个）自由操作后停止录制：

- **忠实录制**：13 个事件 / 7 个动作——含初始 `navigate`、"深度思考"两次 `click` + 一次 `dblclick`（浏览器把快速连点报为双击）、"智能搜索"一次、`scroll` 两次；全部按序入库无遗漏（`omission.verified`、无序列缺口）。
- **清洗不误删**：1 个重复候选、`removed_event_sequences=[]`（真实重复操作零误删）。
- **响应关联**：4 组网络响应-动作关联全部解析、零未解析。
- **无凭据泄漏**：事件流中无 `Authorization`、无 cookie。

### 人工模式 oracle 适配（本轮修正，如实记录）

首跑失败暴露了人工模式的 oracle 设计缺陷：原断言要求动作流与脚本**精确全等**——只适用于脚本回放，真人操作天然包含额外动作。第二轮用户故意自由操作（22 动作：多次点击、双击合并、输入文字、SPA 导航）复现该缺陷，同时其数据证明了产品指标全部健康（22 动作全部被响应关联覆盖、零未解析、零误删）。修正（`tests/test_recording_deepseek_live.py`）：受控分支保留精确全等；人工分支改为"**脚本动作在 dblclick 归一（展开为两次 click）后保序包含于录制流**"——无遗漏与顺序仍被强校验，用户的额外操作本就应被如实录制。产品断言（质量协议/关联/不误删/无泄漏）未动。

这关闭了总表"UI 录制与知识——人工操作 DeepSeek 的完整实测"；该方向仍缺：默认 AI 实际使用录制知识完成断言、closed Shadow DOM 边界呈现。

## 两份旧 Worker 汇总原件恢复完成

定位：总表所指"两份旧 Worker 汇总原件"是 2026-09-09/09-10 两个早期 DA-E4 会话（`0ea013f1`"DA-E4 safe tool acceptance"、`bae284c1` 同名）的 session-history 汇总报告——当年在会话 **running** 状态下生成（仅 4 事件、报告 Status=running），会话完成后从未刷新，故长期呈现为"未恢复的旧汇总"。

恢复（2026-10-24，经产品自身 session-history 工具路径，非脚本拼装）：

- 对两个会话各执行一次 `action=session_report, scope=current_session`（独立 ToolJob：`042f551d`、`7ec1a495`，均 `completed`），生成反映最终状态的汇总原件：会话 1 报告 Status=completed、3 消息/17 事件/1 快照；会话 2 Status=completed、4 消息/19 事件/1 快照。新工件为 `inline_text` 存储（与该工具现有行为一致）。
- **原 4 份 9 月中期报告原封未动**（保留为历史记录）；恢复过程一次误重复（首次脚本在执行提交后才报错，产生同一会话的重复终版报告）已在逐字节核对内容一致后精确清除（删除误产生的 job `83491729` 及其 2 份工件），当前每个会话恰有一份终版汇总。
- 本地查阅副本：`Agent_Server/docs/upgrade_2026-10-03/p6-worker-summary-recovery-20261024/`（recovered-*.md/.html 与 recovery-summary.json；该目录按忽略规则留本地）。生产库当前 session-history 工件共 8 份（2 旧 + 6 终版/成对）。

这关闭了总表"重复请求与工程交付——两份旧 Worker 汇总原件仍未恢复"。

## 模板与 Excel/JSON 导入：当前工作树完整项目页面复验通过

`RUN_LIVE_CASE_IMPORT_FULL_PRODUCT=1` 下于**包含今日全部修复后的当前工作树**重跑 `tests/test_case_import_full_product.py::test_product_templates_excel_json_review_restart_and_isolation`：

```text
1 passed in 74.18s
```

证据：`p1-case-import-full-product-current-20261024-01/` 及同名 XML。构建版 App/Playwright 页面交互 + 独立 API + 隔离 MySQL/PostgreSQL/RustFS/Redis：项目模板保存、rows.xlsx/分表 xlsx/cases.json 实际导入（上传安全报告 allow、原件字节读回）、预览与错误报告下载（invalid.xlsx 阻断）、草稿提交、评审/启用、项目隔离 404、API 重启后数据读回，页面零错误。特别地，导入上传路径经过了今日 win32 libmagic 修复与字体自托管后的构建版前端——本复验同时确认今日修复无回归。这维持 10-23 对该页面流程验收缺口的关闭结论，并补充了本会话证据；不含未来项目平台数据源接入。

## 重复请求与工程交付：模型断网与执行 Worker 中断恢复验收通过（deepseek-flash）

新增验收 `tests/test_default_execution_outage_recovery_live.py`（门控 `RUN_LIVE_DEFAULT_EXECUTION_OUTAGE_RECOVERY=1`），两个持久窗口一次运行（隔离 Redis DB13、完整生产 API + 生产执行 Worker + 受控 HTTP 业务目标）：

```text
2 passed in 210.04s
```

证据：`p2-execution-outage-recovery-current-20261025-07/`（`model-outage/`、`worker-kill/`）及同名 JUnit XML。

测试替身扩展（`tests/default_model_probe.py`）：脚本轮新增 `network_outage`（向已关闭端口发起真实连接，由 httpx 抛出真实 `ConnectError`）与 `block_model_call`（写标记后等待，供真实杀 Worker）；执行 Worker 现在也经探针入口启动（此前仅 API 有探针），使故障窗口确定性。

- **model-outage（模型调用中断网）**：脚本第 1 轮在真实审批后执行 `POST /orders`（恰 1 次），第 2 轮模型调用向关闭端口发起真实连接并按真实传输错误失败（probe 记录 `ConnectError`）。条目落 `error` 并携带 `execution_checkpoint`，不伪报通过；终态重投零新增业务请求与零新增模型物理请求（`physical/*.request.json` 前后一致）。
- **worker-kill（执行 Worker 进程中断）**：Worker 阻塞在首个模型调用时被真实 `taskkill /T /F`（探针进程 id 在案且确认已消失）；自然租约过期 → `recover-expired` → attempt1 `expired`；被杀死 Worker 的执行资源租约自然过期（隔离设置 20s）后由清理 Worker 结清；新 Worker `outage-recovery-second` 领取 attempt2，因“已记录执行意图但无快照”拒绝重放未知副作用，如实 `error` + `execution_recovery_required`；业务 POST 为 0 次，模型物理请求数不变。
- 两窗口 `global_model_unchanged=true`；真实默认模型仅用于规划，执行图由既有脚本化替身回放（与受控 HTTP 模型服务器同性质）。

边界（如实声明）：

- 断网只切断“执行图 → 模型端点”的传输路径（真实连接拒绝），不等同整机网络分区；SDK/HTTP 层真实 TCP 故障由 `test_model_sdk_fault_http.py` 覆盖，本验收补的是业务链路反应。
- 未知结果的 attempt 按产品设计**不自动重放**，本验收证明“不重复副作用、不伪造通过”，不证明“断网后自动续跑成功”；已完成副作用的安全续跑由检查点快照路径（回执恢复两窗口）覆盖。
- 进程中断点位于首个模型调用返回前（Worker 独占模型调用的窗口），故该窗口业务副作用为 0；带已完成副作用的跨进程恢复由回执/快照路径证据覆盖。

过程迭代（证据保留于 `-01`~`-06`）：Worker 未走探针导致脚本未生效、venv python 为启动器致 Popen pid 与真实解释器不同、被杀 Worker 的执行资源租约使接管 Worker 进入 `waiting_resource`、attempt 记录没有 `lease_owner` 字段（改按租约令牌断言）。运行后 Redis DB13 仅余常驻键与共享 cleanup 流（pending=0）。

## 重复请求与工程交付：API 文档 JSON catalog 导入投影核对

只读复核（2026-10-25，当前配置库 `QA_Agent`）：`agent_api_docs`、`_versions`、`_assets`、`_submissions` 四表均存在且各 **0 行**；仓库内 `data/api_docs/catalog.json` 不存在。与 10-23 结论一致：当前配置库仍无生产 catalog 样本，无法做生产投影对账；本轮不伪造样本、未写入配置库。

导入机制验收（非生产对账）：`RUN_LIVE_CASE_PLANNING_TESTS=1` 下执行既有 `tests/test_api_doc_versions.py::test_json_catalog_migration_preserves_versions_submissions_and_historical_assets`：

```text
1 passed in 5.44s
```

证据：`p6-api-doc-catalog-import-projection-current-20261025-01.xml`。在配置库中创建随机 `up_jd_*`/`up_jv_*`/`up_jc_*`/`up_jp_*` 表（先断言不存在，结束 DROP），真实 RustFS 保存并删除资产；legacy catalog 由产品自身 `JsonApiDocStore` 写出（2 个版本、派生资产、1 次提交）：

- 导入结果 `read=1 inserted=1 skipped=0 invalid=0`；导入记录 `project_id=NULL`（强制显式映射、不猜项目），`legacy_project_name` 保留。
- 版本/提交/派生资产完整投影：2 个版本、当前版本快照逐字段一致、派生资产内容含旧 `project_url`、提交 `next` 可读回。
- 重复导入 `skipped=1`（幂等）。
- 9 类目录篡改（版本父/ID、资产 ID/`version_id`、提交 `request_id`、快照 `created_at`、缺失 history 等）均 `invalid=1`，既有版本与快照未被覆盖。
- 重新绑定项目后，按原 request/版本上传仍返回同一记录与版本（历史身份不被改写）。
- 结束后复核：随机 PG/MySQL 表 0 残留，四张 catalog 表仍 0 行。

边界：不证明任何真实部署的 catalog 内容可迁移（无样本），也不覆盖非 JSON 旧来源；只证明“产品写出的 legacy JSON → PostgreSQL 目录 + 版本/资产/提交投影”的语义、幂等与防篡改。生产迁移仍需在持有真实 catalog 的部署上执行脚本并逐条对账。

## 环境清理

- Redis DB 13 复核后仅余常驻键（`qa:fencing:agent`、`qa:quota:limits`、`qa:tasks:case_planning`）；本轮测试产生的 2 个会话事件缓存键已精确删除。
- 无 `qa-execution-cli-*` 容器残留；隔离 PostgreSQL/MySQL/RustFS 对象由夹具清理。
