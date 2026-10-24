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

## 环境清理

- Redis DB 13 复核后仅余常驻键（`qa:fencing:agent`、`qa:quota:limits`、`qa:tasks:case_planning`）；本轮测试产生的 2 个会话事件缓存键已精确删除。
- 无 `qa-execution-cli-*` 容器残留；隔离 PostgreSQL/MySQL/RustFS 对象由夹具清理。
