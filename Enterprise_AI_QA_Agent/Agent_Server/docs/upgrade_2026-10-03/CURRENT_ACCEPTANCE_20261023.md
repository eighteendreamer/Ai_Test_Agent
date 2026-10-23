# 2026-10-23 当前升级验收进度

本记录依据当前工作树、MySQL 默认模型配置、PostgreSQL/RustFS 隔离验收和真实外部请求更新。完整升级目标仍未完成。

## Step 默认模型复验

用户已确认 Step 账号认证完成，本轮按该前提读取 MySQL 默认配置并执行真实多用例依赖场景。实际配置为：

- model：`step-5-preview`
- transport：`openai_chat_completions`
- base URL：Step Plan OpenAI-compatible Chat Completions 地址
- 全局默认模型身份：复验前后均为 `step-5-preview`

实际命令：

```powershell
$env:RUN_LIVE_DEFAULT_COVERAGE_PRODUCTION='1'
$env:QA_DEFAULT_CASE_SHAPE='multi-case-dependency'
$env:QA_DEFAULT_COVERAGE_DIR='Agent_Server/docs/upgrade_2026-10-03/p2-default-multi-case-step-authenticated-current-20261023-01'
..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/test_default_coverage_production.py::test_actual_default_model_plans_and_executes_complete_case -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-default-multi-case-step-authenticated-current-20261023-01.xml
```

结果：`1 failed in 26.52s`。测试只完成了任务提交和首次规划调用，任务状态为 `failed`，没有创建 Run，没有业务 HTTP 请求，因此项目内用例选取、排序、补全、固定执行和结果回执尚未得到默认 Step 真实证据。

本次继续复验相同多用例依赖场景，结果 `1 failed in 38.42s`。默认仍为 `step-5-preview` / `openai_chat_completions`，全局默认身份未变；规划 Worker 执行一次流式 SDK 调用并发送一次 HTTP 请求，供应商返回 HTTP 400（`request_params_invalid`，SDK `BadRequestError`）。任务进入 Worker 后失败，未形成计划或 Run，业务 HTTP 请求数为 0。故当前依旧没有默认 Step 的选取、排序、补全和执行证据；用户确认的账号认证状态不因此改写为认证失败。结构化复验记录：同名目录中的 `verification.json` 与 `planning-task.json`；JUnit 原件保留在本地证据目录。

失败的物理请求返回 HTTP 400。随后使用同一 MySQL 配置做三个最小脱敏请求：非流式、流式、流式加 `stream_options.include_usage`，三者均返回：

```json
{"error":{"message":"you have no active step plan subscription","type":"request_params_invalid"}}
```

因此当前结论是：认证状态按用户说明记录为已完成；Step Plan entitlement/订阅仍不可用。不能把该结果写成认证失败，也不能据此修改模型协议、切换 Responses 或替换默认模型。测试临时 Redis、隔离 PostgreSQL、MySQL 测试表和 RustFS 工件均由夹具清理，`qa-execution-cli-*` 无残留。

## 已有实现且有实际验收证据

- 模板、项目模板、Excel 行式/分表式及 JSON 导入、预览、错误阻断、评审启用、项目隔离和重启恢复。
- 本轮对模板与导入做了当前工作树的产品复验：构建版 App/Playwright、独立 API、隔离 MySQL/PostgreSQL/RustFS/Redis；实际构造并导入 `rows.xlsx`、`split.xlsx`、`cases.json`、`invalid.xlsx`。覆盖项目模板保存、原件字节读回与下载、预览及错误报告下载、草稿幂等提交、评审/启用、JSON 新版本不改活动版本、编号冲突阻断、跨项目隔离及 API 重启后的数据读回。结果 **1 passed in 63.60s**；页面错误和非预期 API 错误均为0，项目隔离返回404，重启后数据仍可读。证据：`p1-case-import-full-product-current-resume-20261023-01/` 与对应 XML。该证据关闭了这些模板/导入页面流程的验收缺口，不包括未来项目平台数据源接入，也不代表普通需求文档生成闭环通过。
- 普通文档生成当前工作树产品复验通过：`test_ordinary_document_modules_and_independent_saved_response_recovery` 的流式/非流式两组在干净隔离证据目录 **2 passed in 394.38s**。使用真实 Markdown/DOCX/PDF 输入、隔离 MySQL/PostgreSQL/RustFS/Redis、生产 API/Worker 和构建版 App/Playwright，并通过本机受控 HTTP 模型验证；两个页面场景均实现3/3模块完成，提交成功但丢失回执后通过回查恢复且没有重复提交，完整 JSON 草稿查看/人工新版本修订通过，页面错误与网络错误均为0。模块级错误恢复场景保留 `[completed, failed, completed]`，失败模块从已保存响应独立重试完成；模板在请求提交后修改不改变固定生成范围，API 重启后仍可完成。DOCX 的3个模块及文本型 PDF 的1个模块均完成并校验来源映射。JUnit 与逐项证据：`Agent_Server/docs/upgrade_2026-10-03/p1-document-generation-full-product-current-20261023-02.xml` 及同名目录。该模型是本地受控模型，因此证明产品链路、存储、恢复和页面行为，不证明默认 Step 的生成语义质量；该缺口仍未验收。
- 执行中逻辑缺口触发持久 Run 重规划的受控闭环已验证：执行 Agent 收到只读业务响应后引用已结算 ToolJob 的精确 JSON 路径，追加正式前置到未领取条目并保留已领取 Attempt；状态查询可恢复丢失回执，终态重投不重复提交。服务回归先前为 `5 passed in 14.39s`；本轮重新核对真实隔离 PostgreSQL/MySQL/RustFS 与本机 HTTP 目标，运行 `tests/test_execution_replanning.py` **5 passed in 23.12s**。新一轮覆盖缺口触发、提交回执丢失后的幂等查回、跨项目证据拒绝、过期修订拒绝，以及证据限幅和工具暴露边界。真实链路写入一份含业务依据的 Markdown 文档，受控 Agent 通过只读响应发现缺口，持久规划 Worker 将修订应用到未领取项；已领取 Attempt 与原结果不变。该夹具不调用外部模型、不启动 Docker；不证明默认 Step 的推理质量、正式产品页面或目标业务测试通过。JUnit 与逐项记录位于 `Agent_Server/docs/upgrade_2026-10-03/p2-execution-replanning-regression-current.xml` 及同名目录。
- 文档版本、上传原件、解析快照、确定性差异、影响分析和需求追踪专项。
- 文档解析现状核对发现工厂、Markdown/DOCX/PDF 解析器、Docling 不可用注册入口和受限解析子进程均已有实现；`IMPLEMENTATION.md` 中 P3 解析“待实施”属于历史起点表，不是当前代码状态。逻辑及现场生成文件回归 `tests/test_document_parsers.py tests/test_document_parser_process.py` **29 passed、1 skipped**。另以真实 DOCX 文件走构建版生产 API 上传、RustFS 原件读回、MySQL 项目解析策略、PostgreSQL 任务与固定快照、独立解析 Worker CLI、API 重启和终态重投，`tests/test_document_parse_production.py::test_actual_production_lifespan_docx_parse_survives_api_restart_and_terminal_replay` **1 passed in 46.40s**；DOCX 表格/章节来源位置及7天文本均读回，设置变更不改任务冻结配置，没有模型调用。复用本机 Redis DB 14、没有启动 Docker。证据：`Agent_Server/docs/upgrade_2026-10-03/p3-document-parse-production-current-20261023-01.xml` 与同名目录。核心工厂/隔离进程已实现，DOCX 生产持久链路已验收；Markdown/PDF 的解析器功能有真实文件测试，生产 API/独立 Worker 仍需分别补验，扫描 PDF OCR 明确不支持，Docling 未部署。
- 随后将现场生成的 Markdown、PDF 文档分别上传到隔离 PostgreSQL/RustFS，经实际解析任务和受限子进程读回文本与来源位置；PDF 再验证解析策略变化生成新快照、历史快照仍保持 plain 结果，且原件 V1/V2 的 7/9 天内容各自可追溯。`tests/test_document_parse_tasks.py::test_actual_uploaded_requirement_document_parses_with_source_locations[md]` 与 `::test_actual_fixed_original_to_durable_snapshot_and_terminal_replay` **2 passed in 12.56s**，JUnit：`Agent_Server/docs/upgrade_2026-10-03/p3-document-parse-md-pdf-current-20261023-01.xml`。没有启动 Docker。
- API 鉴权刷新、依赖传值、未知写入只读查证、进程恢复和报告脱敏专项。
- DeepSeek 公开首页的真实 Playwright/CDP 录制当前工作树复验通过：`test_actual_deepseek_manual_actions_are_recorded_and_diagnosed` **1 passed in 39.47s**。实际访问返回 HTTP 200，保存 6 条事件（4 条可回放动作、2 条观察事件），四个脚本动作按顺序落库；序列无缺口，结束回执确认 6/6 已发布并由 PostgreSQL 校验，重复动作只作为候选标记没有误删，10 条网络响应完成关联且没有未解析关联，事件中没有 `Authorization` 或 Cookie。测试复用 `redis://127.0.0.1:6379/15`，没有创建 `qa-execution-cli-*` 容器。证据：`docs/upgrade_2026-10-03/p5-deepseek-recording-current-20261023-04.xml` 及同名目录。该证据关闭公开页面 Playwright 录制、清洗、响应关联和末尾落库对账边界；不代表人工 Electron 入口、登录/聊天场景或默认 AI 使用录制知识已经验收。
- 当前工作树的原生 Electron 录制产品链路已补充复验：`settled`、`response-dom`、`bridge-loss`、`nested-frame`、`popup` 和 `popup-close` 共 **6项通过**。这些场景实际启动 Electron，通过录制 HTTP API 写入隔离 PostgreSQL，将截图写入并读回 RustFS，并查询项目范围知识投影；同时验证操作后新增 DOM、完成后晚到批次返回409且不改变终态、跨来源两层 iframe 的操作/输入/响应关联、弹窗独立页面身份及关闭对账。逐项证据明确记录 `manual_deepseek=false`、`ai_model_use=false`、`full_recording_acceptance=false`，因此这里只关闭当前受控页面的 Electron 采集边界，不代表人工 DeepSeek 或默认 AI 使用录制知识已经验收。JUnit：`p5-electron-recording-current-20261023-01.xml`、`p5-electron-recording-current-20261023-response-dom.xml`、`p5-electron-recording-current-20261023-bridge-loss.xml`、`p5-electron-recording-current-20261023-nested-frame.xml`、`p5-electron-recording-current-20261023-popup.xml`。
- open Shadow DOM 当前工作树真实链路复验 **1项通过（40.20秒）**：生产 API、Chromium CDP、隔离 MySQL/PostgreSQL/Redis/Memgraph 实际记录嵌套 open root、运行时新增 open root 和 iframe 内 open root 的三个操作及响应，并生成包含这些响应的用例草稿；敏感祖先下的 `fixture-sensitive-marker` 未进入响应证据。契约继续明确 closed shadow root 为 `unobserved`，不虚报可见性；本次没有模型调用。证据：`p5-recording-shadow-current-20261023-02/` 与对应 JUnit XML。
- 录制场景管理和元素别名的当前完整产品复验 **1项通过（89.82秒）**：真实 Chromium 录制后，在构建版项目页面创建场景、整理步骤顺序/说明/预期、绑定元素别名、保存不可变版本并转为正式用例草稿；同时验证响应丢失恢复、请求幂等与冲突、并发版本竞争、项目隔离、归档只读、API 重启读回，以及场景整理不会改写原始事件。最终 PostgreSQL 中2个场景头、6个版本，原事件和原始行保持不变，页面错误为空；本次 `default_ai_invoked=false`，不证明默认模型消费。证据：`p5-recording-scene-product-current-20261023-01/` 与对应 JUnit XML。
- 录制回放失败定位已补成生产闭环并完成当前工作树实测：真实 Chromium 先录制输入和“执行保存”按钮，再让受控网页移除该按钮并放入不同语义的邻近按钮；生产回放 API 保留输入步骤通过、将点击步骤记录为失败且没有触发邻近按钮。相同 `request_id` 重投没有再次打开页面，跨项目请求返回404，逐步报告直接写入 PostgreSQL；API 重启后可读回。构建版项目页面实际显示失败步骤并能发起新回放。最终复验 **1 passed in 78.52s**，页面错误为0，Redis DB 15 清理后为空，未创建测试容器。证据：`p5-recording-replay-product-current-20261023-05.xml`、同名目录的 `facts.json` 和 `first-replay.json`。
- 浏览器回放动作检查点已接入当前回放 Worker：PostgreSQL 回放表保存 JSONB `checkpoint`；每个动作执行前保存 `in_progress`，动作返回后保存结果，并由当前租约校验后写入。重领时，已有完成检查点直接跳过；只有导航/滚动允许在只读状态不一致时安全重放；输入值必须只读核对一致，点击、提交、双击、按键等没有通用只读证明时返回 `recovery_required=stop_without_replay`，不盲目重复副作用。逻辑回归 **18 passed**，真实 PostgreSQL/Redis 租约中断与重领 **2 passed**，真实浏览器回放产品回归 **1 passed in 72.12s**。本项证明了动作边界持久化和安全停止，尚未宣称未知业务副作用可以自动恢复。
- Docker 性能与安全工具调用、原始输出、解析、RustFS 证据和报告中心已有受控真实目标验收；仍缺 Agent 自主规划和默认模型驱动的完整闭环。
- Docker 安全报告中心在修复后的独立完整产品复验通过（任务统计 3/2/1、发现 4、原件下载、API 重启恢复和容器清理均有证据）；这仍不等同于 Agent 自主安全规划或默认 Step 驱动验收。
- Docker 性能报告产品闭环当前工作树复验通过：真实 k6 与受控 HTTP 目标进入生产报告 API 和构建版 App，覆盖正常、缺失指标、畸形结果、排队、取消、引擎失败、负载不确定、证据存储不完整及 API 重启共 9 个状态。报告页状态和结论正确，指标 JSON 下载与 API 指标一致，重启后最新报告保留，排队/取消没有额外调用引擎，页面错误为 0；本轮自建的 k6/目标容器全部清理。结果 `1 passed in 100.70s`，证据：JUnit、`report-product-facts.json`、`normal-report.png`、`restarted-report.png`。测试从已有安全门和已分析报告检查点开始，不证明 Agent 自主规划、分级加压或默认 Step 驱动。
- 模型适配层的独立 transport：Anthropic Messages、OpenAI Chat Completions、OpenAI Responses、Google Gemini 及 LangChain 兼容路径。本轮核查确认 Anthropic 非流式和流式均调用 `client.messages.create`；OpenAI Responses 只由 `openai_responses` transport 选择。内部统一请求/结果契约与各协议客户端分层保留。本轮模型客户端、请求映射、完成状态、HTTP 故障和 embedding 路由回归 **184 passed in 124.23s**；这验证本地映射及错误处理，不证明每家供应商当前服务可用。
- 模型媒体能力补验发现配置字段此前未在 runtime 消费，导致不支持的图像可被发送、部分 URL/文件又会静默降成文字。现有 `map_model_request` 已按逐模型 capability 对 vision、image URL/base64、多图做发送前拒绝；目前无文件/PDF协议 serializer 的输入也明确返回 `unsupported_request`，Google provider profile 不再声明 `file_input=true`。四种原生 SDK 的 base64 图片请求 wire format 与零 HTTP 拒绝由 `tests/test_model_request_profile.py tests/test_model_completion_http.py tests/test_model_clients.py` **94 passed in 42.01s** 实际验证。未改 MySQL 配置、schema 或协议。文件/PDF传输、JSON mode capability 全覆盖及外部具体模型验收仍待完成。
- SDK 重试与物理 HTTP 发送追踪复验：`test_model_sdk_fault_http.py`、`test_sdk_midstream_retry_boundary.py` **66 passed in 90.98s**。五种 transport 在真实本机 TCP/HTTP 故障下逐一核对逻辑调用、物理发送、状态码、响应流、取消和 trace 关联；OpenAI/Anthropic 对 429、503、响应头前断连按各自 SDK 策略重试，已输出流内容后不重发；Google 当前重试设置为单次请求。真实 Agnes 安全 Agent 首次失败样本中，一个 SDK create 产生 3 次相同请求体哈希的 HTTP attempt，均未收到响应头并最终以 `RemoteProtocolError` 失败；其余 SDK 调用完成。后续隔离复验安全 Agent 10 次 SDK 调用对应10次 HTTP，任务及报告完成，终态重投新增模型/目标请求均为0。故本次重发是 SDK 对连接故障的重试，未发现重复消息拼接或重复 Agent 派发根因；响应头前失败仍不能证明远端未处理请求，结果不确定时不得将其等同于“服务端未执行”。没有新增代码或统一化模型协议。证据：`p5-security-agent-current-20261023/physical/`、`p5-security-agent-current-20261023-rerun/`、`p5-security-agent-agnes-codex-rerun-20261023.xml`、`model-sdk-actual-http-retries-current-20261023.xml`。
- 持久 Run 修订规划回归本轮 **16 passed in 25.37s**。这证明当前工作树的修订规划服务测试通过，不替代默认 Step 的真实模型规划和业务执行验收。
- 旧 Smoke 历史导入/投影补充真实 PostgreSQL 验收：`test_legacy_smoke_import_postgres_live.py` 在隔离数据库中实际写入旧目录结果，执行显式 `project_scope → project_id` 预检和物化，重建 Store 后读回 canonical case/suite/run/item/attempt/result，重复执行保持同一 Run，源快照篡改进入 `failed_count/errors` 且不覆盖账本。结果 **1 passed in 4.27s**；内存预检回归合计 **6 passed、1 skipped**。这只关闭旧 Smoke 导入边界，不代表其他历史表迁移或未知来源自动归属已完成。证据由提交 `3313e1ea3` 中的测试保留。

- API 自动鉴权续期与 Cookie 持久化补验：`test_api_401_refresh_automatic_live.py` 与 Cookie HTTP 回归在独立证据目录中 **21 passed、1 skipped，120.40 秒**；另运行真实 PG/ToolJob/加密凭据/Cookie Worker 场景 **1 passed，11.07 秒**。实际对账了登录→401→刷新→重试、刷新失败不重放目标、超时新 attempt、进程中断后三个暂停点恢复、Cookie 属性轮换、服务重启读回及终态重投 0 个额外 HTTP。公开结果、Worker prompt 和存储投影均未出现明文凭据。跳过项是同一 Cookie 文件中未启用的另一条存储门控，不影响已通过的实际存储专项。证据：`p3-api-401-auto-current-20261023-run2/`、`p3-api-cookie-storage-current-20261023-run2/`。本项仍属于受控本地目标，不等同默认 Step 质量验收。

## 正式回执纠正与进程恢复复验

本轮在受控真实 HTTP 目标、隔离 PostgreSQL/MySQL/RustFS、真实 Redis Worker 和生产 API 进程上，分别在回执纠正的两个持久边界杀掉 API：

- `response`：纠正响应已写入 RustFS，恢复后 Run 结果为 `passed`；原始 POST/GET/DELETE 业务副作用各一次。
- `invoking`：纠正请求已写入 RustFS、响应尚未落盘，恢复后结果为 `error` 且保留 `execution_recovery_required=true`；不把未知状态误报为通过。

两种边界都等待原 Run lease 和资源 lease 自然过期，由生产恢复 Worker 接管；终态重投没有新增模型调用或业务请求，临时 Redis 容器、隔离数据库、对象和资源租约均清理完成。

实际命令分别为：

```powershell
$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
$env:QA_RECEIPT_PROCESS_DIR='Agent_Server/docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-response'
..\\.venv\\Scripts\\python.exe -X utf8 -m pytest 'tests/test_receipt_process_recovery_live.py::test_owned_api_kill_recovers_receipt_without_resending_or_repeating_business[response]' -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-response.xml
# 1 passed in 350.83s

$env:QA_RECEIPT_PROCESS_DIR='Agent_Server/docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-invoking'
..\\.venv\\Scripts\\python.exe -X utf8 -m pytest 'tests/test_receipt_process_recovery_live.py::test_owned_api_kill_recovers_receipt_without_resending_or_repeating_business[invoking]' -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-invoking.xml
# 1 passed in 356.93s
```

这证明的是当前工作区的回执纠正实现和恢复边界，不等同于默认 Step 模型真实调用成功；默认 Step 仍被供应商 `no active step plan subscription` 阻断在首次规划请求。

## 已实现但默认模型或产品闭环尚未验收

- 默认 AI 的“理解需求 → 当前项目选用例 → 排序 → 判断缺口 → 补正式用例/准备步骤 → 执行 → 结果证据”完整链路。
- 默认 Step 自主发现执行缺口、选择前置并完成目标执行的质量验收；受控执行重规划通过不能替代该验收。
- 默认 Step 普通需求文档全量生成质量、默认 UI 录制知识使用、默认 Agent API 完整流程。
- 人工操作 DeepSeek 登录/聊天、默认 AI 使用录制知识完成断言，以及 closed Shadow DOM 的能力边界呈现；受控页面的 Electron iframe/弹窗、open Shadow DOM、场景编辑、元素别名及回放失败定位已有上述当前证据。
- Agent 自主性能/安全规划、多级加压、TTFT/Token 吞吐和更多进程故障恢复。
- 需求变更更广业务质量、部署适用性、其他历史数据迁移和更多格式/故障组合。

## 仍需补实现或复现后才能确认的范围

不能把“未验收”直接判定为“缺实现”。下一阶段先以当前真实失败样本定位：Step 订阅恢复后重跑同一 P2 场景；若规划进入下一阶段，再根据真实轨迹判断是模型输出、协议字段映射、规划校验还是执行器缺陷，再决定是否改代码。其他模块继续保持现有模型适配和项目/数据隔离边界。

录制失败定位本轮已从“缺实现”转为“已有实现且产品实测通过”：新增项目范围的幂等回放 API、PostgreSQL 报告存储、启动装配和前端逐步报告；滚动字段读取与录制端的 `scroll_top/scroll_left` 对齐。实测同时发现并修正了两个旧执行器假阳性根因：普通 DOM 目标不再用绝对坐标盲点，几何候选必须匹配录制语义；Playwright `JSHandle` 转换及释放顺序按实际生命周期处理。

本轮补充了可选的后台回放任务链：显式注册 `qa:tasks:recording_replay` 后，回放记录和 PostgreSQL Outbox 在同一事务保存，独立 Redis Worker 使用数据库租约领取、心跳和终态幂等；前端会轮询 `running` 记录。真实隔离 PostgreSQL/Redis 验收先模拟 Worker 在领取后中断，租约过期后由新 Worker 领取完成，终态重复投递没有再次执行（`tests/test_recording_replay_worker_product.py`，1 passed，6.95 秒）。未注册该 Stream 的部署仍保留同步兼容路径。

当前仍未解决浏览器执行已经产生未知副作用后的自动业务恢复：动作检查点能够阻止盲目重放，并保留待人工/业务只读核对的 `recovery_required` 结果；针对具体业务页面的可验证断言、未知状态查证和继续执行仍需按场景设计和验收。本轮也没有宣称人工 DeepSeek 或默认 AI 知识消费完成。

## 生产历史迁移与投影只读调研

代码核对确认历史迁移不是一个覆盖全系统的迁移器：API 文档 JSON catalog 由显式脚本导入 PostgreSQL；旧 Smoke 运行历史通过只读预检、显式 `project_scope → project_id` 映射和追加式导入账本投影到正式 Case/Suite/Run/Attempt/Result；Alembic 目前只有 Outbox、ToolJob、Compaction 和 Run 计划快照四个 revision。其他 PostgreSQL Store 仍由各自 `initialize()` 创建/演进，不能把 Alembic revision 或单一导入专项视为所有生产历史迁移完成。`alembic/README` 已按当前实际的四个 revision 更新。

对当前应用配置的 PostgreSQL 数据库 `QA_Agent` 执行只读核对：`agent_smoke_run_history`、正式 Case/Suite/Run/Attempt/Result 表和 `agent_api_docs` 均为 0 行；`agent_legacy_smoke_import_ledger` 与 `public.alembic_version` 均不存在。结果说明这份当前配置库没有旧 Smoke→正式 Run 或 API 文档 catalog 迁移样本可供对应投影对账；不能据此推断其他部署环境状态或宣称生产迁移已通过。未写入或修改该数据库。

补充盘点确认同一配置库仍有 Session/事件/消息/快照/审批、Agent Task/Attempt、ToolJob/Artifact、Memory、Outbox 和 ResourceCleanup 等持久数据（例如 Session 109、事件 3,711、消息 217、快照 101、审批 9、Agent Task/Attempt 6/3、ToolJob/Artifact 35/10、Memory 313、Outbox 16）。只读取聚合计数并核对关联：事件、消息、快照、审批无孤儿；Session `event_count` 与事件行数一致，`snapshot_count` 与快照最大版本一致；TurnTask 当前 Attempt 指针有效；ToolArtifact 均关联 ToolJob，且 ToolJob 的 `artifact_count` 与工件行数一致。以上九项异常计数均为 0。该核对证明这些当前记录的关系投影一致，不代表它们已由 Alembic 管理，也不覆盖原始业务语义、外部对象内容或其他部署环境；查询未读取正文或凭据，未修改数据。

验证命令：`RUN_LIVE_POSTGRES_TESTS=1 ..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests\\test_schema_migrations.py tests\\test_legacy_smoke_import_postgres_live.py -q --tb=short`，结果 **32 passed in 15.29s**。测试在随机隔离 PostgreSQL Schema/数据库验证四 revision 的真实 DDL、受支持旧结构保留、重复升级、失败/回滚保护，以及 Smoke 导入的显式项目映射、完整 canonical 投影读回、重复导入幂等和源快照篡改拒绝；结束后只读确认随机 Schema/数据库均已清理。它证明受支持迁移边界，不替代其他 Store 的生产历史盘点或迁移，也不代表本机 `QA_Agent` 已运行 Alembic。

### 应用启动时的数据库结构初始化边界

进一步检查启动调用链后确认，应用没有在启动流程统一执行 Alembic upgrade。API lifespan 在组装服务时直接调用多个 Store/Service 的 `initialize()`；独立规划 Worker、生成 Worker 也会初始化各自使用的 Store。相关入口包括 `Agent_Server/src/main.py` 的应用 lifespan、`Agent_Server/src/runtime/planning_worker_runtime.py` 的 `build_service()`、`Agent_Server/src/runtime/generation_worker_runtime.py` 的 `build_service()`。这些 Store 初始化实现包含各自的 `CREATE TABLE` / `ALTER TABLE`，而 `Agent_Server/alembic/README` 明确 Alembic 不覆盖应用自有表，也不应以 stamp 或运行时 initializer 假装完成迁移。

因此当前不是“应用启动由 Alembic 统一迁移”，而是“少数持久结构由显式 Alembic revision 管理，其余仍由运行时 Store 初始化创建或演进”。新增 revision 只能覆盖其明确声明的对象；在没有逐 Store 清点结构版本、旧部署形态和启动副作用前，不能把简单补 revision 视为完整迁移方案。本轮只完成代码路径调研，没有调用这些初始化器去改写现有数据库，也没有新增迁移。

### MySQL 配置库初始化与迁移覆盖

MySQL 配置库使用独立连接，应用中的模型、邮件、渠道、赞助配置 Store 在 API lifespan 中调用 `initialize()`；文档操作和规划 Worker 会初始化应用配置 Store。当前 Alembic `env.py` 使用 PostgreSQL URL 和 PostgreSQL advisory lock，仓库内没有 MySQL Alembic 环境或并行迁移目录。因此配置库结构当前仍由这些 Store 在运行时负责：模型配置 Store 建表并逐项补列；邮件和渠道配置 Store 会检查并补齐历史列，渠道配置还会补唯一键；应用配置 Store 建版本化配置表；赞助配置 Store 建表，并在空表时写入默认赞助记录。

MySQL 不是完全未经实测：`tests/test_model_request_profile_product.py::test_actual_mysql_profile_migration_editor_reload_and_restart` 在隔离 MySQL 上实际删除自有测试表的 `request_profile` 列，重复运行 Store 初始化后确认列恢复，再通过生产 API、构建版设置页面、API 重启及实际模型请求验证配置读写。该测试只证明模型配置中这一列的迁移和产品链路，不覆盖模型表其他历史字段、邮件/渠道/应用/赞助配置表，也不代表生产 MySQL 的所有旧结构已盘点。记录的列定义证据在 `p6-model-request-profile-product-current-20261022-01/mysql-migration.json`。

调研结论：数据库职责仍符合“配置放 MySQL、业务状态放 PostgreSQL”；但升级治理目前不对称，PostgreSQL 有有限的显式 Alembic revisions，MySQL 配置结构依赖启动时自修复，业务 PostgreSQL 也有大量 Store 初始化 DDL。正确的下一步不是立刻再引入一套迁移工具，而是完成按数据库分类的初始化清单，标记每项是纯建表、兼容性补列、索引/约束演进还是默认数据写入，并对照已部署结构和测试证据；再依据这个清单制定分阶段迁移方案，保留初始化的兼容读取/新库引导职责，避免启动过程意外改结构或重复播种数据。本轮仅做只读调研。

### 按配置表名的迁移覆盖粗盘

`DatabaseConfig` 当前声明 **41 个 PostgreSQL 表名**和 **5 个 MySQL 配置表名**。对照四个 PostgreSQL revision，显式建表覆盖为 Outbox、ToolJob、ToolArtifact、Compaction；Run revision 只为已有 Run 表补 `plan_snapshots` 列，不负责创建 Run/Suite/Case 等业务基线。故这些 revision 不能作为其余配置表的部署基线。此前只读检查的当前 `QA_Agent` 配置库没有 `public.alembic_version`，只说明该库尚无 Alembic 运行记录；不能外推到其他环境。

这仍是“按配置键盘点”的粗盘，不是完整 DDL 清单：Store 会创建配置项之外的派生表（例如文档 versions/assets/submissions、录制回放和场景版本、需求影响响应/计划等），并可能在同一张表上执行运行时 `ALTER TABLE`。下一轮需从 API/Worker 的实际初始化调用图枚举这些派生表，记录 DDL 行、旧形态支持、数据写入/播种副作用和已有真实测试，再谈迁移切换顺序。

### 启动期数据迁移与播种副作用

启动调用图还包含不属于 DDL 的持久化写入，迁移审计不能只看建表语句：

- `WorkspaceSettingsService.initialize()` 在 MySQL 配置 key 不存在时读取旧 `general_settings.json` / `channel_advanced_settings.json`，校验后使用 `only_if_missing=True` 初始化配置；已有数据库值不会被旧文件覆盖。当前有服务级测试覆盖此逻辑，尚未找到针对真实 MySQL 旧文件迁移与 API 重启的专门验收。
- API 启动调用 `PostgresMCPServerStore.migrate_legacy_integrations()`，把本地 `data/integrations/catalog.json` 中 kind 为 `mcp` 的记录复制到 PostgreSQL MCP Store；目标行用 `metadata.legacy_integration_id` 做重复识别，保留本地源文件。源码中没有独立迁移账本，去重检查与插入之间的并发安全尚未由测试证明。
- MySQL 赞助 Store 在配置表为空时播种默认赞助数据；这与表结构升级分属不同副作用，迁移方案必须避免结构升级时重复或误覆盖运营配置。

MCP 顺序重跑已有真实隔离 PostgreSQL 专项：`tests/test_mcp_management_route_status.py::test_legacy_mcp_integration_migration_is_idempotent` 结果 **1 passed in 9.46s**。测试第一次迁入一条旧 HTTP 集成，第二次调用返回 0，读回数据库仍只有一条映射记录，并验证 transport、URL 和 headers 转换。它只证明顺序重试幂等，不覆盖多 API 实例并发启动时“查询旧 ID → 插入”的竞态，也未测试从真实本地 catalog 文件读取的 API 启动闭环。测试使用随机 PostgreSQL 表并在 fixture 结束时删除；未创建 Docker/Redis 容器，未触碰当前业务表。

以上是代码路径结论，不代表这些边界已经在多 API 实例或实际旧部署上验收。对 MCP 应继续验证多实例并发导入，再决定是否需要唯一约束或迁移账本；对 Workspace 旧文件和 MySQL 赞助结构则需分别准备真实隔离数据验证，不能把一个 `initialize()` 幂等测试外推到所有配置 Store。

### MCP 旧结构迁移字段往返修复

为确认 Store initializer 对旧扁平列的转换是否保真，在随机 PostgreSQL 表中构造含旧列的 MCP 记录（transport、description、project/document/endpoint、headers、provider_key、capabilities），执行当前 initializer 后通过正式 Store 读回。首次真实测试失败：旧 `capabilities` 已写入 `metadata.legacy_capabilities`，旧列也被删除，但 `_record_from_row()` 只读取已不存在的列，API 对象返回空 capabilities。根因是 PostgreSQL 规范表通过 JSONB `metadata` 存储该字段时，写入映射没有写 capabilities，读取映射也没有从迁移元数据回退。

修复后，`_record_params()` 将非空 capabilities 写入兼容元数据；`_record_from_row()` 优先读仍存在的旧列，缺列时从 `metadata.legacy_capabilities` 读取。旧结构迁移字段读回及新建 PostgreSQL 记录能力列表往返由 `tests/test_mcp_management_route_status.py` 覆盖；当时整个文件在真实隔离 PostgreSQL 上 **30 passed in 8.90s**。随机表由 fixture 清理。此修复不改变表结构或 API 字段。

后续对旧 integrations → PostgreSQL MCP 的并发边界做了屏障复现：两个独立 Store 同时完成空结果查询后继续写入，原实现最终产生同一 `legacy_integration_id` 的两条记录（测试失败，实际读回 2 条）。修复让该导入路径使用由旧 integration ID 经 UUIDv5 确定的 PostgreSQL 主键，复用现有 `ON CONFLICT (id) DO UPDATE` 原子收敛并发写入；普通 MCP 创建仍使用随机 UUID，JSON Store 未改变。并发测试、旧结构迁移字段测试、能力往返测试及原 MCP 路由测试在真实隔离 PostgreSQL 下全文件 **31 passed in 12.33s**。既有已迁移记录仍由 metadata 去重并保留原 ID；本修复不重写它们。该测试覆盖两个同进程独立 Store 和同步屏障，尚未覆盖多主机网络故障或长期历史重复数据清理。
