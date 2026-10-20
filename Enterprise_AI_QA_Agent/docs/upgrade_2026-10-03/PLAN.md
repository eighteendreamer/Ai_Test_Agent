# 企业 AI 测试系统升级方案：用例理解、执行补全与需求变更闭环

当前核验日期：2026-10-21。完整P0—P5目标继续进行。已补充[绑定阶段职责与原生Schema对照](P2_NATIVE_SCHEMA_DIAGNOSTIC_20261020.md)：原生Schema/low隔离对照仍失败；最小只读调用证明同一Schema能正确输出前置来源。r4将bindings的数据来源说明与原始工具效果路径说明分开，合同、真实存储恢复及依赖专项共84通过。真实默认Step/8192多候选复验1失败（369.48秒）：目标/前置选取和bindings有阶段证据，但补核发现清理来源误引用自身，不能称完整逻辑通过；produced达到8192上限、正文为空。只读副本纠正来源后仍length/8192、正文为空（120.752秒），见[清理来源诊断](P2_CLEANUP_SOURCE_DIAGNOSTIC_20261020.md)。未创建Run、业务HTTP为0，停止同类提示/参数重试，P2继续未验收。当前独立embedding服务仍返回402/30001，自动向量召回验收继续受阻；进一步沿代码和专项回归确认，已有项目内启用用例目录/标题编号检索可作有界候选来源，但 provider 请求失败按现行契约保持`retrieval_failed`且不调用规划模型，不能冒充无命中后自动回退。代码路径与回归证据见[Embedding故障与项目目录路径核查](P2_EMBEDDING_FALLBACK_RESEARCH_20261020.md)。

模型适配层也已按当前源码和 SDK HTTP 请求重新核验：统一内部请求/结果契约与 OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini 原生 transport 分层保留，LangChain 仅走 OpenAI-compatible Chat；本批 6 个相关测试文件共 **186 passed**，没有发现需要新增统一协议栈或更换 Anthropic Messages 的依据。该结果是锁定 SDK 对本地 HTTP fixture 的功能验收，不代表所有真实外部供应商都已在线验收。详见[模型适配层复核](P0_CALL_TRACE_RESEARCH.md#模型适配层当前源码与真实-sdk-http-复核2026-10-20)。

P1 设置页模型激活链路已用构建版 App、真实 API/隔离 MySQL 和本地模型 HTTP fixture 验证：界面激活后，运行时读取新活动配置并把新模型 ID 发到 Chat Completions fixture，**1 passed**。这验证模型切换到运行时的配置链路；多模块替代模型业务生成及真实外部模型路径仍未完成，见 §7.137。

另有非默认模型的真实运行时探测：从 MySQL 读取已配置但未激活的 Agnes 模型，经现有 OpenAI-compatible Chat 适配器实际完成工具调用和流式文本；Qwen 的单次调用收到 HTTP 401，不能算响应映射通过。该探测不改全局激活配置，且不等同于设置页切换模型或完整业务流程验收，详见 §7.124。Step 账号认证按用户确认视为已通过；当前默认推理可用性仍按已有真实 400/402 证据记录，不要求重复认证。

P0 并发重复请求核验发现并修复：会话锁已占用但持久状态仍非忙时，同一输入会在锁外预编排后又在锁内重新编排，导致语义意图模型重复调用；真实 SDK/本地 HTTP 回归现为每条输入恰好一次发送。另已把单次用户 turn/trace 贯通语义判定、Agent 调用、工具后推理和工具调用 ID，并用本地 HTTP/SDK 与确定性回归验证；默认 Step 只读工具/SSE 实测仍在首个模型请求收到 HTTP 400、1次 attempt 后中断，没有进入工具阶段。按用户说明账号认证已通过，2026-10-20再次以当前 MySQL 默认配置实测仍为一次 HTTP 400、没有工具调用；见§7.126。认证通过不等于当前推理通道可用。全业务持久任务、生产环境及其他失败/恢复组合仍需继续核验。

以下2026-10-19摘要与分阶段原件作为历史保留；当前状态以2026-10-20调研和其后实际验收结果为准。

修订日期：2026-10-20。Step 认证已通过，但当前推理 entitlement 不可用；完整 P0—P5 未完成。P1 普通 Markdown 三模块生成、持久化及独立恢复、模板/Excel/JSON 项目页面已有专项验收。另以真实 Agnes 调用生产文档生成 Worker 完成单模块草稿闭环，仍未验设置页模型切换和多模块替代模型场景，见§7.125。P2 最新真实默认模型完整八步复验 `p2-default-full-reference-feedback-8192-live7-20261019/`：1失败、281.16秒。当前MySQL默认8192输出额度，隔离规划期限600秒；逻辑及bindings通过，produced以length/8192结束，正文为空。3次模型HTTP均200，无鉴权失败，未创建Run、业务HTTP为0；尚未到cleanup，不能宣布默认引用修复通过。此前live6的原文歧义和错误T结构保留；已复用现有引用指导并补声明/检查位置及计数诊断，真实存储/恢复64项通过，正式关系45项通过/3跳过，旧阶段与日志32项通过/22跳过；旧r2任务原样恢复，新任务r3。原生未执行证明和对象身份修复已提交7d8896dd。默认模型完整选取/补全/正式执行仍未验收，停止同方向提示微调，转查执行前声明职责与实际Agent/返回后核验的重复。真实默认 UI 正负向及终态重投已有受控页面专项；DeepSeek真实页面自动化录制已验，人手录制及默认模型消费该站知识仍未验。受控HTTP模型下正式回执 `invoking` / 已持久化 `response` 两个真实API进程中断点均已通过；默认Step提供商相同专项及P2完整执行仍未验。配置在MySQL，业务/运行在PG，原件在RustFS；Provider保留各自原生SDK协议。详见 [引用反馈调研](P2_REFERENCE_FEEDBACK_RESEARCH.md)、[未执行证据调研](P2_NON_EXECUTION_RESEARCH.md)、[规划对照研究](P2_PLANNING_CONTRAST_CURRENT_RESEARCH.md) 与 [本轮跨进程回执证据](p0-receipt-process-recovery-current/verification.json)。

P2 Run 修订进展补记（2026-10-20）：Run 修订后端/API 和人工评审页面已实现。新增真实 PostgreSQL 事务写入进程在提交前/后中断专项，通过同一幂等键从 Run 修订 API 重放，**2 passed**；整个 Run 创建/修订测试模块在真实 PostgreSQL/MySQL 及 Redis 场景下 **47 passed**。该专项通过 ASGI 路由与独立 writer 子进程验收，不等同于终止生产 Uvicorn API 进程或浏览器连接实际服务的 E2E；Agent 自动发现缺口并提交修订仍未实现。详见 [P2 Run 修订调研与验收](P2_RUN_REVISION_DESIGN_VALIDATION_20261020.md#事务进程故障与幂等恢复复核)。

P4 需求追踪的当前构建版产品流程补验（2026-10-20）：实际 DOCX 需求经构建版 App 评审，再把受控正式 Run 的失败/回归结果按选中检查点关联到需求矩阵；浏览器丢失回执、并发版本冲突、API 重启、归档后的历史只读均通过。该受控场景没有调用默认模型，也不等于业务负责人评审或部署环境适用性确认，详见 §7.131。

模型适配层在当前工作树重新运行 `test_model_clients.py`、`test_model_request_profile.py`、`test_langchain_model_adapter.py`，**67 passed，27.05秒**。结论不变：统一内部请求/结果契约与 provider-specific 原生协议客户端是当前代码结构；Anthropic 用 Messages，OpenAI Responses 独立分派，LangChain 仅接受 OpenAI-compatible Chat。本批本地 HTTP fixture 与 SDK 测试没有发现需要将供应商改成单一 wire protocol 的根因或失败证据；这不证明每个远程供应商都可用。

### 当前未完成项的分类〔2026-10-21〕

| 方向 | 已有实现/专项证据 | 尚未验收 | 仍需补实现或查证 |
|---|---|---|---|
| 模板、生成、导入 | 默认/项目模板、JSON渲染、Excel/JSON产品专项；普通Markdown模块生成及恢复专项；Agnes真实文档Worker单模块草稿闭环（§7.125）；设置页模型激活后运行时实际调用新模型的构建版产品验收（§7.137） | 更广业务文档、完整多模块替代模型链和用户生产流程 | 按新失败证据决定是否需要补实现，不把已有专项重列为未实现 |
| 按用例执行 | 项目隔离、完整版本读取、规划/依赖映射、原子领取和正式Agent链路；领取快照归档/恢复；Run 修订后端/API、人工评审页面、PG/Redis 执行续接及事务进程边界专项（§7.110、§7.128、本文件最新补记） | 默认模型多候选选取→排序→必要前置/运行内准备→执行；其他模式；生产 Uvicorn 进程中断和浏览器到实际服务的 Run 修订验收 | Agent 自动发现缺口、生成候选计划并提交修订仍需实现；未知副作用自动查证及完整准备状态须继续查证 |
| 正式回执与恢复 | 默认 Step UI 正向与终态重投通过；`invoking`/已保存 `response` 两阶段真实进程终止恢复通过（§7.74、§7.83）；受控模型 Worker 恢复专项通过 | 原默认 profile 负向与纠正质量（点击数不符、纠正响应截断）；更多错误/断网组合；P2 entitlement 可用后的完整规划执行；P2 Run 修订生产 API 进程恢复 | produced 阶段重复生成/length 空正文须架构审查；Agent 自动修订链路仍需实现，不放宽正式核验 |
| UI录制与知识 | DeepSeek真实 Chromium/API/CDP/PG录制，确定性质量投影及响应观察窗口到本地动作关联；固定场景知识读取及受控页面默认AI使用；2026-10-21 两种项目范围下当前工作树的真实CDP录制→PG/Memgraph知识投影→构建版知识页→API重启恢复 **2 passed**（本地fixture、无模型，§7.144） | 真实 DeepSeek 录制与存储的一体化产品路径、默认AI消费 DeepSeek 录制知识、完整产品链路 | 当前响应关联只表示同文档时间窗口；业务事件完整性、跨文档/iframe/Shadow DOM 全面关联、selector修复评审及失败定位报告仍待推进 |
| Agent API | 响应提取、依赖、鉴权与报告已有专项；显式401自动续期/只读重放及三阶段进程恢复通过；受控 Worker 正向刷新和刷新失败报告通过；API 报告页、RustFS 原件下载及 API 重启恢复受控产品流程通过（§7.143、§7.145、§7.147） | 默认 Step 完整 Agent 流程；错误凭据、超时/断网恢复、更多依赖形态及不同报告组合 | 已验收场景没有发现需补生产逻辑的缺口；其余按真实结果定位，不因夹具失败误改产品代码 |
| Docker性能、安全 | 实际Docker工具/报告专项；默认Step在服务端可信目标前提下实际k6→PG/RustFS→报告页及终态重投通过（§7.114）；真实Agnes安全Agent、Docker runner、本机隔离目标的三任务验收通过（§7.133） | 默认Step安全Agent模型链路、更多目标/故障组合，以及性能容量/流式指标 | 普通性能会话的可信目标登记缺少公开接入；仅登记项目base_url不会赋予该范围；保留现有权限规则 |
| 需求变更与追踪 | 上传版本、MD/DOCX/PDF解析、差异、影响/评审、增量和追踪专项；构建版 App 的 DOCX 需求矩阵、固定结果核验、丢回执/冲突/重启/归档恢复（§7.131） | 更广默认模型判断、真实用户业务评审、部署环境适用性闭环；当前产品测试使用受控业务目标与脚本化浏览器操作 | 其他格式/故障组合按范围继续核验，不部署Docling |
| 重复请求与交付 | 多SDK逻辑调用/HTTP attempt观测；并发首轮重复分类已修复；turn/trace 已从语义判定传到Agent、工具后推理和挂起队列；模型响应事件含可对账的工具调用ID；确定性与本地SDK/HTTP回归154通过（§7.122） | 默认 Step 实际 Agent 工具/SSE测试首个推理调用返回400，未执行工具或后续推理；其他持久任务入口、跨阶段断网/进程恢复、生产历史迁移/投影和旧Worker汇总原件追踪 | 按逐入口调查补齐持久身份和失败/恢复证据；继续按Profile与原生协议契约适配，不另建单一协议栈 |
| Session/Worker并发写入 | 通知后重读父会话；事件型通知不再全量保存；自动停止状态/消息/事件原子写入；内存与真实PG原子写测试通过 | detached旧快照覆盖风险已由交错回归复现并修复；revision/CAS、其他`save_session`调用点审计、生产时限跨进程恢复尚未完成 | 保持这项修复局部且可验证；架构级并发治理按[Session并发设计](SESSION_STORE_CONCURRENCY_DESIGN_20261020.md)继续评估与实施 |

单用例通过不代表P2整体通过；受控模型、真实默认模型和人手操作分别记账。在途计划修订不能由“失败后新建回归运行”替代：Run 修订后端/API、人工评审页面、PG/Redis 执行续接及事务进程边界已有实现和专项验收；Agent 自动发现缺口并生成/提交修订、生产 Uvicorn 重启和浏览器到实际服务的 E2E 仍需推进。
**2026-10-21 当前进度更正：**Step 账号/API认证按用户确认已完成。默认 Step `step-5-preview` 在 2026-10-18 的正式回执纠正恢复测试中，真实 API/Worker 通过了 `invoking` 和已持久化 `response` 两个进程强制终止/恢复场景，**2 passed，0 skipped**；两场景终态重投都没有新增模型或业务请求，清理后资源为空（§7.83 的脱敏回执及 JUnit）。默认 UI 正向用例与终态重投通过；原默认 profile 的负向用例仍为失败（实际只点击一次、预期两次，纠正响应被截断），`low/json_object` profile 的负向专项单独通过，不能代替默认 profile 验收（§7.74）。另一个独立的 2026-10-21 多用例规划请求仍在第一个模型 HTTP 收到 400（无有效 Step Plan 订阅），未生成计划/Run；这是当前推理 entitlement 问题，不是认证失败。P3 API Agent 的同进程/durable 夹具修正后，受控模型完整正向认证链路 **2 passed**（§7.143）。受控验收和默认 Step 业务闭环分开记账；P0—P5 总目标仍未完成。

### 7.89 用例执行字段与回执修复、默认模型 UI 正向验收〔2026-10-19〕

依据最新P2 API失败现场确认：服务端实际 POST 已创建记录并返回完整字面键 `order.id`，但执行器优先按点号拆分路径而未取完整字面键；同一真实模型回执在工具操作完成后附带说明文字和一份完整JSON，旧解析器只接受整体JSON，因此把已有成功证据判为格式错误。没有改模型协议、配置或用例参数。

提交 `f35765a5`（`【fix】修复用例执行字段解析与回执提取`）只包含两个生产修复和对应回归：字典中存在完整字面键时优先读取，缺失时保留原嵌套路径行为；回执解析仅接受唯一一个通过现有schema的完整JSON对象，多份或不完整候选仍拒绝。确定性验证 `tests/test_api_task_executor_recovery.py tests/test_agent_case_assessment.py` 为 **62 passed、2 skipped，20.71秒**；skip为原门控场景，不计通过。

随后运行真实默认模型、真实API/规划与执行Worker、隔离MySQL/PostgreSQL/Redis/RustFS及本机受控浏览器页面的正负向 UI 场景：

```text
cd Agent_Server
$env:RUN_LIVE_DEFAULT_UI_SCENE='1'
$env:QA_DEFAULT_UI_SCENE_DIR='.../p5-default-ui-certified-positive-20261019'
..\.venv\Scripts\python.exe -Xutf8 -m pytest 'tests/test_default_ui_scene_live.py::test_default_ui_reads_fixed_scene_and_verifies_actual_page[positive]' -q --tb=short -s
```

正向结果 **1 passed，116.65秒**。证据 `p5-default-ui-certified-positive-20261019/positive/verification.json` 标明 `controlled_model=false`、`default_planning=true`、`default_execution=true`、最终状态 `passed`。模型读取了此前录制的知识，并在本次新页面点击按钮；页面端记录录制页和新执行页各一次 POST，结果观察关联到新页面。重投同一终态任务后，新增业务请求 **0**、新增模型请求 **0**。

负向结果 **1 passed，157.96秒**。证据 `p5-default-ui-certified-negative-20261019/negative/verification.json` 标明 `controlled_model=false`、`default_planning=true`、`default_execution=true`、期望计数为2、最终状态 `failed`。实际页面仅执行一次点击；系统按固定预期保留失败结果。重投同一终态任务后，新增业务请求 **0**、新增模型请求 **0**。这两项验证受控页面场景下默认模型知识读取、真实执行、正负结果判定及终态不重放；仍不覆盖正式用户授权流程、DeepSeek人工录制或P2 API项目选取/排序/补全闭环。

P2 最新 API 默认模型证据仍显示 `produced` 阶段 reasoning-only 流在原180秒总期限内被取消，未返回可校验回执，Run和业务HTTP均为0；不能把 UI 通过外推为 API 规划通过。下一步沿既有 PostgreSQL 原领取事务验证“提交成功但客户端丢失回执”后的真实Worker恢复、并发和终态重投，再继续P2阶段职责与预算根因设计。没有根据多次提示/Schema对照擅自增加预算或放松canonical校验。

### 7.90 DeepSeek 公共页面录制交付与遗漏质量验收〔2026-10-19〕

先按真实失败证据调查遗漏：首次测试失败不是操作事件丢失或清洗删除。PG 已保存9条完整连续事件；首个零业务事件文档 scope 在测试再次导航时未能确认，finish receipt 如实报告 `frames_unconfirmed=1`，质量状态为 `missing`。生产驱动在录制会话创建时已打开入口页，测试又对同一页执行第二次 `goto`。`_verify_capture_receipt` 对未确认文档拒绝完成是必要的数据完整性约束，不能放宽为通过。

只修正测试启动顺序：驱动打开页面前订阅浏览器主文档 response，移除重复导航，并在 recorder 已启用的当前页面点击两次“深度思考”、一次“智能搜索”并滚动。该测试使用正式录制 API、CDP attach 生产驱动、PostgreSQL 事件表、隔离服务及图投影；没有登录 DeepSeek、提交提示或使用账号数据。真实重跑 `tests/test_recording_deepseek_live.py::test_actual_deepseek_manual_actions_are_recorded_and_diagnosed` **1 passed，22.20秒**。录制 `completed`，PG 与图投影均为8条事件，无序号缺口、无降级；finish receipt 的每个 scope 均确认并 `pg_verified=true`，`frames_unconfirmed=0`；质量 `omission.status=verified`。两次相同点击被记录为重复候选，但原始动作均保留；页面扫描与响应留为证据，不作为可回放步骤；Authorization/Cookie 脱敏断言通过。

受影响确定性回归 `tests/test_recording_document_scopes.py tests/test_recording_quality.py tests/test_recording_graph_store.py tests/test_recording_finish_protocol.py` 为 **33 passed、2 skipped**。失败现场与成功现场分别保留在 `p0-deepseek-recording-certified-20261019/` 和 `p0-deepseek-recording-certified-fixed-20261019/`，两处均为本地原始验收证据，不随提交。

本项证明了真实 DeepSeek 页面上的浏览器输入事件、响应/页面快照关联、PG持久化、清洗候选和遗漏回执；交互由 Playwright 发起，不是人手操作。它也没有证明该份 DeepSeek 录制知识已被默认模型检索并用于验证同站页面，因此“人工录制→清洗→入库→AI使用”仍未完整闭环。未改生产 recorder 逻辑或放宽丢失判定。

**本次直接更新这份方案，并同步所贴附件和原选中的 PLAN.md。** 正文采用以下约定：模板只生成用例；执行由 AI 理解需求，在当前项目选取完整用例、初排、检查条件来源、补全必要前置、最终排序和冻结。上传文件由系统独立保存原件与版本，比较所选解析快照；Git 是另一个可选来源。第 2.3 节说明缺口如何检测，第 3.1 节说明上传变更如何实现，第 3.2 节单列 Git。

**本次更新正式回执衔接的实际状态。** 暂停/快照/恢复保留原上限和累计数；一条模型回复中的多项审批共用一次调用。新计划可在项目策略中启用“回执错误时只读纠正一次”，原回复合法时不增加调用，纠正仅使用原剩余预算及已保存证据。请求与响应持久化后按原阶段恢复，不重新执行业务。真实中断时，有意图而无保存响应则保留未知并正式error/recovery_required，不重发；保存响应则经固定用例/证据核验恢复结果。两类现场的API kill、原90/300秒租约、实际清理CLI/新Worker及终态重投见6.98。该能力默认关闭；冻结传输配置及实际403见6.86，历史超时和6.83原默认UI error仍保持。

**本次继续按完整方案实施。** 录制来源实际读取及引用已增加明确请求契约，沿现有规划、持久恢复和回归链路验证；已统一工具证据身份并修复命令导航遗漏目标检查；默认模型已实际操作，未授权场景blocked/终态重投通过，可信场景因返回说明报告而非纯JSON回执仍为error，见6.82。对应八项诉求的交付与验收要求汇总在1.4，完整系统仍未验收完成。

**上传变更可以沿现有实现完成，无需 Git 缓存。** 上一方案批次在`4e35de24`核对上传路由、原件/版本保存和独立 Git 导入，并实际运行两项上传回归：2通过、0失败、0跳过，pytest输出7.99秒。范围为真实临时文件、应用路由和本地测试存储，见3.1.1。本次生产启动/DOCX及执行Worker范围单独记在6.79，不把两个批次的验证混为完整验收。

已提交的录制响应实现为 `4e35de24`，专项结果见4.1.6/6.78；本批已重跑真实受控页面录制、规划和默认模型正式运行，历史失败见6.81、本次结果见6.82。该批生产API冷启动的外键初始化顺序失败已按真实依赖修复：空数据库生产API/重启/实际DOCX与独立解析Worker1通过，原执行Worker/审批及副作用恢复1通过，生命周期/消费等65通过、审批存储15通过，见6.79。默认AI实际使用、完整清洗、正式App与人工DeepSeek继续待验收。

本方案按用户纠正后的流程执行：

- **模板仅用于生成。** 默认/项目模板提供写作指导、示例和 JSON 结构；执行读取用例自身的完整固定 JSON、说明和来源，保留现有测试模式。
- **按项目选取、排序和补全。** AI 理解需求，读取当前项目完整用例，初排后判断“这一步所需的信息或状态由谁提供”；查项目依据补入必要前置，再最终排序和冻结。必要前置可以不直接匹配用户目标，检测方法见 2.3。
- **上传独立保存。** 普通上传建立文档及 V1；明确在该文档下选择“上传新版本”才追加历史。RustFS 保存原件，PG 保存版本/快照；指定双方解析快照比较。首次上传没有旧版本时不产生变更结论，不使用 Git 缓存，具体操作和可行性依据见 3.1。
- **Git 为独立可选来源。** 仅用户选择 Git 入口时只读取得指定 commit/blob 文件；取得字节后复用保存、解析和比较服务，不自动合并来源历史，见 3.2。
- **配置与历史分开保存。** 可编辑配置放 MySQL；业务、任务、版本和运行记录放 PostgreSQL；原件及大工件放 RustFS。历史版本、失败结果和 unknown 记录保留。

消费阶段与原领取事务已提交 `67d134ae`，见2.3.12/6.69。上一实施批次在 `4ed1507f` 修复“取消无可靠返回仍被当作消费结束”和“自动清理等待误要求其他消费者通过”，见2.3.13/6.70。内部十五项、外部九种原图/API/审批场景及四项历史/来源边界已验；明确业务前置仍要求通过，旧运行按原映射恢复。完整固定JSON、原证据和审批保持；默认自主Agent、完整生命周期与App及其余P0—P5继续未完成。

当前完整进度、历史失败及验收边界见第六节；历史实施命令见 [IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)。本次方案修订、上传回归及三份同步核对见 [plan-revision-4e35de24-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-revision-4e35de24-validation.json)；历史共享消费证据见 [p2-consumption-return-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-return-validation.json)。历史方案保留在 [PLAN_HISTORY_918cdace.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN_HISTORY_918cdace.md)。历史章节中的“本轮”仅表示当时批次。

## 一、升级原则与实际代码依据

保留现有测试模式、Agent、工具、权限、套件、运行和结果体系。用例像 Skill 一样提供测试目标、操作、预期和说明，由 AI 理解后调用现有工具；不把新用例编译成测试模式参数。

三条核心链路如下：

```text
生成：默认/项目生成模板 + 固定文档依据 → 完整 JSON 草稿 → 评审 → 启用具体版本
执行：理解需求 → 当前项目选取用例 → 初排 → AI 检查衔接并补全 → 最终排序/冻结 → Agent 执行
变更：保存新原件版本 → 新解析快照 → 确定性差异 → AI 影响候选 → 评审 → 增量草稿/追踪/新回归
```

**生成模板只参与生成；执行读取用例自身的完整 JSON、自身说明、固定版本和来源。上传、保存版本、解析及比较均不依赖 Git。**

### 1.1 现有实现 → 采用做法

| 查证位置 | 事实与采用做法 |
|---|---|
| [对照分析报告](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/reference_ai_test_agent_2026-10-02/REPORT.md) | 吸收模块化生成、需求追踪、场景编辑、报告展示；报告明确参考项目许多行为只做过源码分析，不能直接当作可运行结论 |
| [生成管线](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/generation_pipeline.py)、[用例服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/case_service.py) | 复用生成/草稿/版本/评审；文档级 source_refs 仅是来源线索，不能自动当作每条需求的覆盖关系 |
| [项目规划](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_service.py)、[规划来源](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_sources.py) | 已有项目隔离、只读查询、完整依据及固定计划；扩展共享规划内核，不另外在通用 graph planner 建一套业务规划 |
| [检索摘要](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/embedding_service.py) | 前六步摘要只用于召回，判断和执行读取完整固定版本 |
| [API 前置分析](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/api_testing_mode/precondition_resolver.py) | 鉴权标记和路径参数只作辅助线索；业务缺口由 AI 阅读内容判断，不扩成注册/登录等通用清单 |
| [固定计划创建运行](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/plan_run_service.py)、[执行上下文](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_case_context.py) | 复用正式套件、RunItem、原子领取及完整上下文，不创建替代执行平台 |
| [Agent 适配器](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_case_execution.py)、[回执核验](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_case_assessment.py) | 新 Agent 策略已接既有图/审批/证据；继续补准备状态、未知副作用和真实模型验收，保留旧运行策略恢复 |
| [文档服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/api_docs_service.py)、[对象存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/artifacts/artifact_storage_service.py) | 原件与派生内容已分存；复用稳定文档身份、版本事务、哈希和原提交查询，上传不调用 Git |
| [比较服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/difference_service.py)、[三方读取](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/impact_sources.py) | 固定双方快照、完整用例及当时来源已有专项；影响提交、单元调用/核验、自动补读和全范围候选已有实现；生产API/独立Worker见3.5.6，正式App/任务池见3.5.7；最新默认模型固定场景已验九项业务判断，见6.12；正式需求评审/设计矩阵API已验，见6.13，页面及真实恢复/冲突/历史验收见6.14；用户业务评审和执行证据仍待验收 |
| [文档任务存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_document_operation_store.py)、[规划工作流](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_workflow.py) | 现已抽出共享任务/Outbox/租约职责；解析与比较子类保留最终快照语义，影响任务另存输入检查点。规划保存调用意图和响应，P4 后续批次检查点参考该做法 |

测试设计继续参考项目本地课程：用例生成与数据治理、线上流量闭环、评测工程化、Agent 轨迹与协议鲁棒性。采用来源、版本、轨迹和评审原则；其中依赖版本、统计比例、阈值不是本产品的实测事实。

### 1.2 存储与职责

| 内容 | 存储/责任 |
|---|---|
| 可编辑模板、导入格式、解析/比较/规划/影响策略及模型配置 | MySQL；提交任务时冻结实际配置修订和非敏感参数 |
| 项目、文档/用例/需求身份和版本、评审、计划、任务、运行、结果、工件引用 | PostgreSQL；业务身份和状态的权威来源 |
| 原件、派生内容、大输入、大响应、报告和失败诊断 | RustFS；PG保存对象身份、大小、SHA-256和关联 |
| 派发、语义召回、图关系等 | 沿用 Redis、向量、图谱；可重建投影，不替代冻结业务记录 |

模型密钥、凭据和 OAuth token 不进入普通日志、任务参数快照或向量。共享数据明确归属项目/运行；禁止自由文本项目名代替正式 `project_id`。

### 1.3 本次纠正的含义与实现边界

| 用户要求 | 当前代码核对与方案约定 |
|---|---|
| 模板只用于生成 | `CasePlanningService._system_prompt` 已明确不读取生成模板；执行仍以完整固定用例及自身说明为依据 |
| 选取、初排、补全、再排序 | 可按项目启用两阶段规划，逻辑决定及哈希入PG，执行证据只能追加，最终完整校验；实际范围见2.3.1/6.57。它不等于已经保存逐次初排与补全变化，默认自主流程仍未通过 |
| 简单说明如何检测缺口 | AI 阅读操作与预期，判断必要条件由谁提供；通过项目内只读查询补依据，后端核对引用和依赖。规划合理与实际执行成功分别验收 |
| 必要前置可以不直接匹配用户目标 | 查询并完整读取项目内正式用例后可加入 dependency；仅初始候选没有前置不等于项目没有前置；没有正式用例时有可靠依据才补 prepare |
| 上传不需要 Git 缓存 | `upload_document` / `upload_version` 直接保存原件对象和数据库版本；Git 来源信息是可选参数，普通上传不读取仓库、不调用 Git 命令 |
| 工程方案必须说明哪些可用 | 区分源码已实现、历史专项已验证、完整产品待验收。单元结束与引用一致不等于全范围分析、业务判断正确或需求已经生效 |

历史方案修订9be9bfd8核对上传、规划和影响调用链，随后接入内部协调、正式API/独立Worker和正式App/任务池。配置沿用MySQL，业务快照在PG，大结果在RustFS。当前状态以第六节为准，不把此前的待接入描述当作最新状态。

### 1.4 八项诉求的交付与验收要求

以下是本方案继续实施的范围，不能把表中交付要求当作已完成状态。实现位置、历史结果和未完成范围分别见对应正文与第六节。

| 原始诉求 | 交付内容与实际验收要求 |
|---|---|
| 文档生成、模板和导入 | 默认/项目模板只指导生成；保存完整 JSON，前端展示和编辑完整内容。以 Excel 为主、JSON 为辅，验证列映射、预览、错误行、草稿提交及版本评审；模块化生成逐模块保存依据和结果。见 2.1 |
| 需求变更 | 无 Git 配置也能上传 V1/V2、保留双方原件、解析并比较所选快照；AI 引用确定性差异提出影响候选，经评审增量生成。原件、解析失败与历史比较分别可查。见 3.1、3.3—3.6 |
| 需求追踪 | 展示每条需求及其验证点对应的固定用例版本、未覆盖项和变化后需复核项；区分设计覆盖与真实执行证据，保留旧关联和失败结果。见 3.6 |
| UI 录制知识 | 验证人工操作、操作后信息采集、清洗、遗漏诊断、入库、AI 读取和真实页面操作/验证全链路；补场景、步骤、别名、查询和失败定位。DeepSeek 人工录制和完整 App 单独验收，知识可读不等于执行成功。见 4.1 |
| Agent API 测试 | 沿现有 Agent/工具执行，验证真实响应提取、前置输出传值、鉴权及结果呈现；按用例与自由测试都保留。必要前置依据当前项目补全，不硬映射测试模式。见 2.2—2.4、4.2 |
| 性能与安全 | 保留现有 Docker 工具，核对真实工具调用、原输出、解析指标/发现、结果展示及证据；失败和缺证据必须可见。见 4.3 |
| 借鉴优秀实现 | 模块化生成、增量、追踪矩阵、场景编辑、报告分别落入现有功能；Mock、定时任务和脚本导出仍为后续候选。见 4.5、第五节 |
| AI 重复请求 | 对实际消息追踪意图识别、推理、工具后推理及物理 HTTP 发送，区分消息重复拼接、逻辑调用与传输重试；依据真实轨迹修复和回归。见 4.4 |

实施继续沿第五节 P0—P5 阶段推进；每阶段先查证当前实现和真实缺口，再修改、验证并更新第六节。专项通过只覆盖其实际输入、进程和存储范围，不能替代完整产品验收。

## 二、JSON 用例与 Agent 执行

### 2.1 默认模板、生成和 Excel 导入

项目管理提供“用例生成模板”弹窗：系统初始默认模板、项目模板、写作指导、示例和 JSON 结构。项目修改模板只影响后续生成。

用例保存完整 `content_json`、自身执行说明、来源、版本、哈希和治理信息。支持嵌套及未知字段；不能只保留统一步骤 DTO。前端提供业务步骤、通用 JSON 树和原文编辑/查看。固定版本的完整 JSON 是执行依据，模板快照只用于追溯编写来源。

生成开始时冻结实际模板指导/示例/修订及模型、Prompt、Skill版本；自身说明随用例版本保存。正式转换旧格式产生新版本，不原地重写旧正文/hash。分模块生成记录模块范围、来源位置、成功/失败和未处理范围，失败模块独立恢复；现有整批生成能力不能冒充模块化任务已经完成。

导入以 `.xlsx` 为主，同时支持 JSON，首期不支持旧 `.xls`：

**上传原件 → 选择工作表 → 表头识别/列映射 → 完整 JSON 预览与逐行错误 → 提交草稿 → 评审。**

支持“每行一条用例”和“用例/步骤分表”两种默认 Excel 格式。保存文件哈希、工作表、行号、列映射和错误报告；用户明确“不映射”不能被自动识别覆盖。更改映射产生新预览；提交使用已冻结预览，不重读当前模板/导入配置。编号冲突不覆盖已有用例，重复请求返回原批次，批次提交和草稿写入同 PG 事务。未来项目平台数据复用同一导入服务。

**实际验收状态：** 6.87已通过构建后的完整App、实际Excel行布局/分表及JSON、MySQL模板/映射、RustFS原件、PG草稿/版本和独立API重启；包含混合有效/错误行、编号冲突及项目隔离。该验收不调用AI，不替代文档生成及模块化任务验收。

### 2.1.1 逐版本评审与用例可执行状态

用例身份的启用/停用/归档状态，与每个版本的 draft/pending_review/approved 分开。V1 已启用时追加或评审 V2，V1 保持可执行；启用 V2 必须明确所选版本，不能顺便启用后来追加的 V3 草稿。

现有修正使用 PG case record 内的 `version_reviews` 和追加事件，不改变不可变版本正文/hash。追加、评审、启用读取锁内当前行；归档只合并生命周期字段。启用与必要 embedding Job/Outbox 同事务。请求身份按 case/action 检查，改变版本或预期启用指针不能复用；重放返回当前状态，不能回退启用指针。重新启用旧已确认版本创建新任务，不复用旧终态任务。

前端统一从完整用例弹窗选择版本、查看状态及操作事件。未知回执保留原请求，先 GET 核对，再按同一请求重试。历史状态推导明确标注，不伪造旧评审时间。内部迁移已有专项，生产迁移及运维入口仍待验收。

### 2.2 执行依据与现有测试模式

| 方向 | 行为 |
|---|---|
| 自由测试 | 使用现有 Agent 流程，不注入正式用例作为执行依据 |
| 按用例测试 | 理解用户目标，当前项目选取用例，再规划与冻结执行 |

保留现有测试模式；历史 `mode_key` 保留兼容，Agent 新规划不按它硬过滤候选。旧请求/旧策略仍按原契约恢复，不能破坏既有运行。

选取使用会话绑定的正式项目，通过编号/标题/向量查询获得候选，再读取完整固定版本判断相关性。保存目标与选择理由。检索故障、无匹配和信息不足分别呈现；找不到目标用例时不静默改为自由测试。

### 2.3 简单的逻辑缺口检测与补全

AI 只围绕一个问题检查初排后的用例：

> 这一步需要的信息或业务状态，现有输入或者前面的步骤能否提供？

输入是用户目标、完整选定 JSON/自身说明、运行信息/实际已有证据，以及可按需查询的当前项目用例、文档、接口资料和录制知识。不是扫描几个关键词，也不是给每个用例套注册、登录等固定清单。

检测和补全按以下方式运行：

1. AI 阅读操作和预期，标出确实需要的条件及其来源。已有输入可用；前序会产生则调整依赖/顺序；故意缺失的负向条件保持缺失。
2. 对没有来源的必要条件，查询当前项目。找到合适正式用例就补入，即使它不直接匹配用户原始目标。
3. 没有正式用例，但项目资料/工具结果提供可靠方法，则补为运行内准备步骤；不伪造 `case_id`，不自动入库启用。
4. 仍缺必要信息时记录缺口及影响项，独立部分可以执行；不编造接口、元素或规则。
5. 对新增前置再检查衔接，确定最终排序和依赖；受现有回合/耗时预算约束，超限保留未解决范围。

例如，查询真实订单没有订单ID，可以补项目内创建订单用例，或按项目资料补创建准备步骤；验证“订单不存在”的错误响应就保持不存在。是否还需要账号/会话由具体依据决定。规划只证明逻辑连贯，真实条件是否成立由执行证据确认。

一次具体检测的输入与结果如下，以下ID和字段仅用于说明，不作为真实接口或自动匹配规则：

| AI读取的内容 | 判断 | 计划中的处理 |
|---|---|---|
| 用户要求查询已有订单；选中的完整用例要求订单ID；本次输入及前序项均未提供 | 该目标缺少必要输入 | 查询当前项目用例目录并读取可能创建订单的完整用例 |
| 项目内创建订单用例明确产生订单ID | 找到条件来源 | 加入正式前置，记录目标对它的依赖；数据绑定引用其真实输出字段 |
| 项目内没有相应用例，但已读取资料明确给出创建方法 | 可以按依据准备 | 加入运行内准备步骤，记录操作、预期及来源，不新建正式用例身份 |
| 项目资料也没有创建方法 | 无法确认如何补全 | 保存订单ID缺口及受影响目标，不虚构创建操作 |

检测由现有规划模型阅读正文完成，后端执行只读查询并核对它提交的版本、证据、依赖和数据绑定。无需另建关键词检测器或通用前置清单。AI可能误判业务条件，实际执行必须继续核对前置输出；计划中有一个绑定并不能证明该输出已经存在。复用[现有规划实现](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_service.py:141)的`query/finish`协议，阶段可视化仍按下文补齐。

简单地说，检测就是**读完整用例 → 找必要条件的来源 → 缺来源就查询并补入 → 根据依赖排序**。AI在既有计划中用`reason`说明补入理由、用`depends_on`表达先后、用`input_bindings`表达传值、用`unresolved`保存无法补齐的缺口。这里复用已有字段，不新增一套检测表或注册/登录规则。后端只检查可确定的结构与引用问题；“是否确实需要这个前置”由AI依据项目内容判断，并由运行结果验证。

现有规划每回合返回 `query` 或 `finish`：后端执行项目隔离的只读查询，AI提交目标、正式前置、准备、数据绑定和缺口。后端核对项目/版本/引用/依赖循环/顺序，不取代 AI 判断业务必要性。具体输出路径使用字面量 `output_path_tokens`，不能把包含点号的字段误当嵌套路径。引用前序输出必须同时声明依赖。

实际代码中，`_recall` 获取候选并读取完整版本，`_system_prompt` 要求 AI 选择目标、判断条件来源、按需查询和补全，`_validate` 用 `TopologicalSorter` 按最终依赖排序。当前并没有独立保存初排结果及逐次补全差异。后续若需要界面展示“最初选了哪些、为何补入、顺序如何变化”，应先研究并保存同一规划任务的阶段记录，复用已有回合检查点；不能凭当前最终计划还原并伪造中间决策。

### 2.3.1 规划职责拆分与待验收工程设计

缺口检测仍然只问“所需的信息或业务状态由谁提供”。下面的拆分用于降低单次规划同时处理业务衔接和工具字段的负担，不增加注册、登录等固定检查项，也不改变现有测试模式。

| 阶段 | AI负责什么 | 保存什么、何时可执行 |
|---|---|---|
| 逻辑规划 | 理解用户需求；读取当前项目完整JSON用例及自身说明；选取目标、初排、补入必要正式前置或有依据的准备步骤，再确定依赖、清理要求和最终顺序 | 保存逻辑决定、引用及哈希；这时只是规划中间结果，不创建运行 |
| 执行证据绑定 | 阅读固定逻辑决定、完整用例和按需读取的工具接口；补充输入来源、真实输出字段及产生/清理证据的提取说明 | 仅为既有执行项追加技术声明和未解决缺口，不能换用例、改准备操作或重排目标 |
| 最终校验与发布 | 后端沿现有服务核对项目、固定版本、已读引用、依赖、数据绑定及必要证据声明 | 合并后执行完整校验，保存可执行固定计划；用户启动后进入现有Agent、工具及运行链路 |

例如“查询已有订单”的逻辑阶段判断缺少订单ID，补入项目内创建订单用例并建立依赖；证据阶段再依据工具的实际返回结构声明如何取得并传递ID。规划中的声明不能证明订单已经创建，执行必须读取本次真实结果。负向用例要求订单不存在时，逻辑阶段保留这个条件。

采用同一持久规划工作流和回合检查点，不另建执行器。按项目显式启用版本化协议；配置在MySQL，提交时将两阶段Prompt、配置和业务依据冻结入PG。两阶段共用原总时限与回合预算，保存响应后再判断完整性；恢复读取原阶段和固定决定，不能重新选择用例、覆盖逻辑哈希或重复未知调用。旧任务继续使用原seed和协议。

**当前状态：本批已接入阶段Schema/转换、工作流、PG检查点保护和项目页设置；实际功能与边界见6.57。** 新项目配置在MySQL，实际上传原件从RustFS读回；阶段转换丢PG回执后的恢复、固定逻辑不可改写、共享回合预算及完整最终校验已验。受控独立生产的部分/全部清理已验，默认自主新试验仍失败；完整App、正式项内混合效果和共享消费等仍待验。旧v3验证不计为新v4覆盖。

上述旧seed/转换/存储恢复、项目页类型和受控生产已经按6.57实测；默认模型本批未通过。下一步先研究并验证正式项清理关系及项内多个效果声明的版本化契约，再接全部消费者、正式结果/页面及默认自主流程。项目未配置新协议时继续原流程，旧任务沿原seed恢复；不自动升级旧运行。

依据为[现有规划服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_service.py)、[持久工作流](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_workflow.py)、[PG检查点存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_planning_task_store.py)及6.55实际默认失败。阶段设计属于本项目拟采用方案，不能将它描述为已证明解决模型超时的办法。

### 2.3.2 正式用例清理与项内多种效果的工程方案〔v2专项已验，自主执行待验〕

业务检测仍然由AI回答“必要信息或状态由谁提供”。本节处理的是计划如何表达真实操作及证据：完整正式用例可以自己创建、查询和清理，也可以查询并清理前置准备的数据。不能要求用户为了适配证据结构重写用例，或把用例内部步骤强制拆成多个正式用例。

**旧v1不能完整表达上述关系，原行为继续保留。** 旧正式项没有`cleanup_sources`，每项仅有一个产生或清理声明；6.57实际默认候选误把目标负责的清理写到prepare自身，旧校验仍拒绝。新v2用独立正式/准备Schema表达清理来源和多份声明，没有删除错误关系、放宽旧协议或改写历史目标JSON。当前v2默认任务正确把清理来源写在正式目标上，逻辑决定已冻结，但技术绑定尚未完成，见6.59。

已通过Schema和消费方核对并接入以下表示方式：项目策略同时启用`preparation-scope-v2`、`case-effect-evidence-v2`、`case-effect-coverage-v2`和`case-planning-staged-evidence-v2`；新任务固定Prompt v5，旧v1路径保持。正式项和prepare采用`effect_schema=2`，`effect_declarations`保存多份声明；每份`declaration_id`稳定，清理`sources`引用`item_key/declaration_id`。真实验收范围见6.59：

| 关系 | 计划表达与执行含义 |
|---|---|
| 执行项间依赖 | 保留`depends_on`；外部前置先提供条件，正式目标随后执行；项依赖仍不得指向自身 |
| 同一项内产生与清理 | 为该项保存多份有稳定身份的效果声明，每份有角色、操作匹配条件、对象提取和固定预期；清理引用同项的产生声明，不添加`depends_on`自环 |
| 正式项清理外部来源 | 逻辑阶段明确“目标负责清理哪个前置产生的数据”；证据阶段绑定来源执行项及产生声明；归属不再只限prepare |
| 操作匹配 | 使用实际工具输入与完整固定依据中的字面量条件，继续按token列表读取字段；不凭HTTP方法、标题或关键词推断角色 |
| 原操作证据 | 一个实际原ToolJob保留一个可靠返回事实；多份声明的匹配结果引用同一原Job，不复制Job或把别名计作再次创建 |
| 不确定结果 | 匹配字段缺失、相互矛盾的声明、未知返回、超预算分别记录原因；不把未知范围变成零对象或自动成功 |

例如前置创建两条订单，目标查询第一条并删除两条：逻辑阶段保留原目标完整JSON，记录目标消费前置且负责清理；证据阶段声明前置每次可靠返回中的订单ID，以及目标实际查询/删除操作的识别与验证条件。前置的两次实际创建均进入原对象清单，目标只删除一条时仍有一条未覆盖。同一正式用例内部创建后清理，采用相同证据来源机制，但不产生额外RunItem依赖自环。

**来源身份不能只有对象值。** 新关系需固定项目、计划、执行项、产生声明和实际拥有者；具体对象还关联原Job/逻辑操作、可靠观察及typed值哈希。清理引用固定来源，再核对实际对象及时间顺序。数值0、布尔false、空值和包含点号的字面量字段不能互相替代。多份声明合法匹配同一返回时保留各自依据；矛盾分类保留未知，不任选一种角色。

本次已同步以下消费链：Schema、分阶段规划/固定恢复、范围、上下文类型、原可靠返回、清理覆盖、失败回归及前端。既有`plan_mapping.py`、`agent_case_context.py`、PG JSONB保存及assessment读取无需改业务算法，通过新类型和原服务实际验证；表中工作说明保留作为契约约束：

| 现有依据 → 改造位置 | 具体工程工作 |
|---|---|
| `schemas/case_planning.py`、`case_effects.py`、`case_planning_stages.py` → 固定契约 | 新版本表达正式清理归属及多效果来源；逻辑决定与技术声明职责分开；旧缺省字段、序列化和哈希不变 |
| `planning_service.py`、`planning_stages.py`、`planning_workflow.py` → 规划与恢复 | 新任务冻结对应Prompt/Schema；证据阶段只追加既定关系的技术细节；恢复读取原seed、逻辑决定和预算，不读取当前策略升级旧任务 |
| `preparation_scopes.py`、`plan_mapping.py`、`agent_case_context.py` → 范围及运行 | 区分项间依赖和项内效果来源，固定原拥有者；完整用例及自身说明进入现有Agent，准备继续附属正式条目 |
| `case_effect_evidence.py`、`postgres_case_effect_evidence.py` → 原返回事实 | 在原可靠返回事务中保存多声明匹配与提取结果；核对原intent/绑定/输出SHA，保留未知与原操作索引；现有JSONB承载v2已做真实存储验证，无新表/列 |
| `case_cleanup_coverage.py`、`agent_case_assessment.py` → 结果与验证 | 从整个运行原操作清单按声明来源投影，不只读取Agent回执选中的最后一次输出；正式目标与prepare均接入覆盖断言、Result及证据引用 |
| `agent_regression_service.py` → 新回归 | 裁剪计划时同时核对正式项和准备项的效果来源；必要来源缺失沿现有重新规划，不静默改写关系；父失败结果保留 |
| `agent_web/src/types.ts`、`CasePlanDialog.vue`、`CaseEffectCoveragePanel.vue` → 产品展示 | 项目策略、正式项关系、声明和逐对象覆盖成对同步；面板不能只识别旧v1后把新结果隐藏；未知和未覆盖可追到原Job |

可编辑策略沿原MySQL规划配置保存；新任务冻结关系、声明、版本和检查点到PG；原件及大证据继续存RustFS。采用显式新协议分派，保留旧v1编译/核验及旧Prompt路径；不把新默认字段写入旧任务。实现使用现有规划、工具、结果和存储服务，不引入第二套执行平台或新依赖。

已用实际MySQL/PG/RustFS、原生HTTP和正式Result验证：正式项内部创建→查询→清理；prepare创建→正式目标消费并清理；同一来源两次实际创建、只清一条；unknown创建/删除重试不再发送；覆盖超预算保留已知对象数。原实际返回的离线故障回放另验false与0、匹配缺字段、角色冲突和返回证据超限。前端组件使用实际v2结果展示，完整浏览器流程本次未执行。新默认任务在原预算下正确冻结逻辑并读取操作契约，但证据绑定结束前超时，业务执行未通过。共享消费完成、多份清理声明的独立通道组合及其他模式仍需另验。

**共享数据何时消费完毕仍是独立未完成项。** 当前`consumption_completion_verified`明确为false；对象覆盖通过不能证明共享消费已结束。后续需要真实消费者状态及证据，不能在原归属结构上加一个会造成等待环的门卫，也不能因本节设计而宣称默认模型超时已经解决。研究依据及实施边界见[P4_FORMAL_EFFECT_RELATIONS_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_FORMAL_EFFECT_RELATIONS_RESEARCH.md)。

### 2.3.3 固定文档原文作为预期依据〔专项已验，自主绑定待验〕

实际默认八步用例没有DELETE常量，该操作及返回码来自项目文档。不能要求用户为适配证据路径增加“证据契约”字段。新可选MySQL策略`effect_expected_source_protocol=case-effect-document-expected-v1`使用独立effect_schema=3项/声明与Prompt v6，继续沿原v2范围、原返回、覆盖和阶段服务；旧v1/v2Schema、Prompt与省略字段保持。

每个检查/操作选择条件只选一种预期：自身完整content或prepare.instructions的`expected_path_tokens`；或`expected_source`引用该项已读document的`evidence_id`、`content_hash`和`payload_path_tokens`。结构值直接取路径；文本指定原文`excerpt`、其中唯一`literal`及`text/json`编码。服务端只精确定位和JSON标量解码，保存文档身份/版本、hash、原件位置、字符/行位置和typed预期hash；不改写原文、不凭方法推断角色、不从当前版本重新读取依据。

未读取、hash不符、摘录/字面值歧义及解码失败进入原具体校验反馈；原文null不能当成字段缺失，false不能等同0。正式JSON和说明仍由Agent理解执行，文档引用仅为具体预期提供可查依据。前端开关保存项目策略，关闭原前置清除该策略；固定计划展示原引用/摘录及完整用例，不把配置变化写回原任务。

真实测试使用原默认用例、原需求内容，仅替换受控服务器地址，没有添加字段；创建两条、查询、部分/全部清理已验。规划后上传新文档版本、执行中关闭当前策略，旧Run仍引用原版本；PG阶段提交丢回执后换attempt恢复原v6seed/逻辑/hash/deadline、终态不再调用。具体结果见6.60及[P4_EXPECTED_SOURCE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_EXPECTED_SOURCE_RESEARCH.md)。本步不能证明默认模型能在原预算内自主完成绑定，也不完成共享消费生命周期。

### 2.3.4 明确操作后的固定契约读取〔专项已验，默认尚未触发系统预读〕

原真实逻辑已写明prepare.instructions的tool/operation，原证据阶段仍用一轮模型仅请求读取相同契约，耗时56.697秒。新MySQL可选operation_prefetch_protocol=case-planning-declared-operation-prefetch-v1要求固定文档预期及v2阶段栈，Prompt v7和完整seed仍PG；旧v6及更早任务保持原Prompt/省略字段。

逻辑先验证、排序并冻结，系统只取prepare.instructions顶层明确的字符串tool/operation，复用原query_tool_contracts/阶段查询队列。保存模型原finish，系统读取另标origin、logical_hash及声明项键；同操作只查一次，超过原queries八条限制则分批保存并读取全部，不消耗next_round或延长deadline。未知操作沿原查询反馈呈现；没有明确选择时仍由模型查询，不从HTTP方法猜工具。完整用例、文档和操作模型保留；当前注册及配置不能替换固定seed。

真实原件上传、MySQL/PG/RustFS、查询队列/读取提交丢回执、新Worker恢复原截止与终态不重发已验；正式用例不加字段，受控模型三轮规划后原生HTTP创建两条、查询、逐条清理且无剩余。6.61默认试验因文档引用连续错误未进入本阶段；6.62统一身份后的默认逻辑只选正式项，没有prepare操作声明，因此未触发本协议的系统预读，仍由effects模型请求并读取操作契约。具体命令/原失败及下一步见6.61—6.62与[P4_DECLARED_OPERATION_PREFETCH_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_DECLARED_OPERATION_PREFETCH_RESEARCH.md)。

### 2.3.5 文档来源身份与完整摘要一致〔真实存储/恢复已验，自主执行未完成〕

原真实三次错误将document证据ID中的16位摘要写为payload暴露的64位content_hash；工具契约本身却一直使用完整摘要。严格拒绝错误引用保留，根因在来源身份表示不一致。新可选MySQL策略`source_identity_protocol=case-planning-source-full-hash-v1`使用Prompt v8：来源创建时即以完整内容摘要生成文档/录制ID，查询结果、模型上下文、引用、声明和PG恢复共用该身份，不对模型输出做模糊修正。

新策略要求已有固定文档预期/v2阶段契约，可独立于明确操作预读开启。源件业务ID、版本、payload及content_hash不变，case版本与工具ID不变；未启用及旧seed继续16位文档身份、旧Prompt/哈希/序列化，当前策略变更不改写原任务。阶段Prompt版本选择抽为原服务共用函数，避免三处继续扩展不同的版本判断。前端开关与前置清理同步，没有新依赖/表列/公开路由。

实际MD/DOCX/PDF原件经独立解析进程、RustFS字节读回、MySQL策略和PG规划保存/恢复，证明新旧ID对应相同内容/版本；真实任务丢回执换Worker仍使用固定身份/逻辑/hash/deadline，终态不新发；原生创建两条、查询、清理全部及V1固定执行另验。录制身份使用同一来源分支，但本次未验人工录制和实际录制数据库链路。

默认新任务四轮为logic、logic、effects、effects：文档完整ID被正确引用，正式目标的自身清理关系冻结；第三轮读取execute_task，第四轮尚无正文时原deadline取消。系统预读仅接受prepare.instructions明确操作，而本次计划只有正式项，不能用旧预读专项宣称已省掉此轮。下一步研究正式项内明确操作如何在固定逻辑阶段表达并复用原查询/恢复，结合实际阶段成本再验自主能力；不把任意JSON编译成模式参数，不增加虚构前置，不暗增预算。具体失败/证据见6.62及[P4_SOURCE_IDENTITY_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_SOURCE_IDENTITY_RESEARCH.md)。

### 2.3.6 正式项及准备项的只读操作选择〔专项已验，默认完整执行未过〕

原v1预读只能从prepare.instructions取明确tool/operation；实际v8逻辑只有正式目标时，仍消耗25.208秒模型回合请求已知接口。新MySQL值`operation_prefetch_protocol=case-planning-item-operation-prefetch-v2`采用Prompt v9及独立逻辑wire，要求原完整来源身份和文档预期栈，旧v1/v6—v8字节/哈希保留。

逻辑finish可填写operation_selections：item_key、tool_key、可选operation和已读业务evidence_ids，覆盖target、dependency与prepare。operation为空只读完整tool；没有明确选择仍可在effects查询。后端核对现有执行项及该项已读取的业务依据，不从HTTP方法推断工具、不为了读取接口增添虚构准备；未知工具或操作沿原查询错误反馈呈现。选择不编译用例参数，不增加执行授权。

选择保存在PG阶段检查点，与完整逻辑决定共同生成新hash；旧None仍按原决定hash。进入effects后PG比较整个固定stage，即便重算hash也不能删改选择。最终执行决定继续原PlanningDecisionV3，完整用例/自身说明不改；effects只追加技术绑定。查询去重后保存item_keys及业务引用，沿原八条队列、next_offset、租约及deadline恢复，不增加next_round或业务调用。

实际原件/MySQL/PG/RustFS、正式内部与prepare→正式两种真实产生/清理已验；原始JSON无证据字段，V2及当前政策改变后仍执行V1。前端保存一致政策，显示真实固定选择与完整用例。默认新任务对两个项的相同操作去重为一次读取，省掉独立模型读取回合；三次模型仍耗尽原预算，不能宣称自主完成。详细来源、命令及失败见6.63和[P4_ITEM_OPERATION_SELECTION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ITEM_OPERATION_SELECTION_RESEARCH.md)。

### 2.3.7 首次推理前读取明确文档来源〔已接入，完整自主执行仍未过〕

用例已有api_doc来源时，原默认仍花43.022秒请求已知原文。新MySQL可选`linked_source_protocol=case-planning-linked-documents-v1`与Prompt v10只读取固定候选完整source_refs，不注入项目全部文档；不改变选取、补全、排序或执行模式。旧None省略新字段，旧Prompt/seed/检查点按原字节恢复。

PG保存首次sources阶段及每份来源的所属case证据、原引用、固定基准、读取结果/错误和next_index。先保存引用队列，再保存版本/资产/解析身份，最后保存读取证据；这不是模型query，也不创建假round。提交回执丢失后沿原租约和deadline恢复，已捕获身份不再读取当前版本或最新解析；终态不新发。

文档基准分三种：完整当时资产/解析引用为authored_snapshot；仅明确版本为version_linked_source；只有ID时在捕获时固定当前版本，为current_linked_source并提示当时版本未记录。没有明确历史解析的DOCX/PDF保留缺口，不能选最新解析补造历史；原件/解析大对象继续RustFS，PG保存固定引用。正文hash、资产版本和解析身份均核对。

首次每个来源读取500行并展示实际范围/has_more；后续document分页继续同一基准。相同引用去重并保留全部所属case；同一文档有多个固定基准时提示歧义，固定来源读取失败不能改用当前内容替代。缺失、跨项目、错误版本/资产/hash和非文档引用在来源记录中可查，AI仍可判断独立部分。原完整上下文限制仍有效，超长明确失败、不截断后称全量。

三格式实际上传/独立解析进程/V2/重解析后固定阅读、PG存一次受控规划与首次完整原文已验；丢queue/capture/read提交回执、实际PG拒绝改写/丢弃基准、分页末尾及两用例来源归属已验。两种实际API业务各两受控模型/五业务HTTP，通过且现场零剩余；前端保存一致政策并显示实际来源记录。生产默认首次因误用解析分页对象而0模型失败，修正且加三格式实际服务验证后新任务2模型，证据阶段仍超时。具体命令、失败和范围见6.64及[P4_LINKED_SOURCE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_LINKED_SOURCE_RESEARCH.md)。

### 2.3.8 证据阶段的引用职责〔已接入，默认自主执行仍未过〕

MySQL可选evidence_reference_protocol=case-planning-evidence-references-v1冻结到新任务，Prompt v11仅改证据wire。逻辑仍按完整用例选取、初排、检查必要条件来源、补全和排序；None省略，旧任务/Prompt/版本/hash不升级。生成模板不进入执行，上传仍独立于Git。

当前effects调用按实际已读证据提供E目录、文档原文全段和非空行T目录。AI仍选择来源、具体字面值、操作匹配、输出路径、产生/清理关系；系统只还原正式身份/hash/精确原文，不生成业务预期或自动把方法识别为清理。结构文档可用E+字面路径，复杂唯一摘录可用E+excerpt；T不要求重复填写摘录和完整摘要。

目录随调用意图存PG round，保存原wire_response_text、结构化wire及canonical_evidence_decision；响应只能使用该调用目录。PG拒绝改写已发目录；后端重建核对目录后还原到原EvidencePlanningDecisionV3，再走原document_expected_value/validate_relations和业务计划核验。未知E/T、工具冒充文档、目录改动、字面值不存在或摘录歧义留下具体反馈，不能靠放宽验证取成功。False/0/null、含点号键、完整JSON与32字段接口保留；原权限/期限/回合及上下文限制不变。

实际MD上传/RustFS、MySQL/PG持久任务、响应提交丢回执后原目录恢复、当前政策关闭和V2不替换原依据、终态无重发已验。内部正式项与外部prepare两种业务各2受控模型/5次真实HTTP，两个对象完整清理、0剩余。新默认逻辑82.859秒完成两个项，证据96.53秒后取消、正文0；没有取得规范证据决定或创建运行。不能由短引用通过推导默认能力或总体加速。研究、原失败、准确命令和范围见6.65及[P4_EVIDENCE_REFERENCE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_EVIDENCE_REFERENCE_RESEARCH.md)。

### 2.3.9 已提供运行输入的路径读取〔已复现并修复，完整自主执行待验〕

`input_key`选择本次运行输入的顶层来源，`output_path_tokens`选择该来源的实际字面量子字段；空数组仍注入完整来源，旧点分路径保持兼容。比如`settings`里的空键下`enabled=false`，执行应收到false而非整个settings对象。原执行上下文忽略路径，新实际文件/固定计划/领取测试已复现；修复在原分支复用`output_at_path`，没有新增配置、表、依赖、模式或协议版本。

必要子字段缺失或为null时，沿原规则明确报缺口，不以父对象存在冒充条件满足；可选缺失保留未解析，False/0及完整嵌套内容保留。配置继续MySQL，业务固定计划/运行/工具结果继续PG，原件继续RustFS。本批真实文件、两种SDK适配的实际模型请求、现有API工具GET、持久回执和终态不重领已验，见6.66及[P2_RUNTIME_INPUT_PATH_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_RUNTIME_INPUT_PATH_RESEARCH.md)。

当前事实还表明：跨正式条目的绑定读取已保存回执`case_output`或`preparations/<key>/output`；项内准备由Agent使用先前真实工具返回。最终回执的`outputs/output_source`决定保存结构，产生对象范围由独立原ToolJob证据决定。不能把原生API的task_result路径自动等同最终准备输出路径，不能用受控取值成功声称默认模型已正确完成这些绑定；本批多对象组合与固定消费者传值专项见2.3.10/6.67，实际共享消费状态与完整生命周期仍待验。

### 2.3.10 多次工具返回的输出组合与消费者路径〔真实链路已验，自主执行与生命周期待验〕

规划的数据来源仍用当前项目固定执行项及字面量路径表示；执行时从已保存的 `case_output` 或 `preparations/<key>/output` 读取。原回执的 `outputs` 只支持顶层字段各取一次返回，`output_source` 只复制一次完整对象，不能组合两次 POST 各自返回的 ID。实际 Agent 图测试已复现：两次创建和第一个查询已发生，回执结构被拒绝，第二消费者与清理未执行；原失败保留。

在原回执追加可选 `output_structure`，支持 reference/object/array 三种明确节点。叶子只有原 `call_id` 和 `output_path_tokens`，由现有核验器读取持久 ToolJob；对象键和数组顺序由 Agent 明确声明，不按 HTTP 次序自动推测业务角色，不接受模型填写实际 ID。比如将两个原调用的 ID 引用组为 `ids=[引用A,引用B]`，计划的 `ids/0`、`ids/1` 就能指向保存后的两个实际值。缺失路径、无此调用、跨执行身份和未结束来源沿原证据核验处理；失败来源不能凭组合回执变成通过。

旧字段继续使用：三种输出方式互斥，未使用新字段时序列化省略，旧回执字节保持。新 Prompt 版本为 `case-execution-receipt-v3`，保留已保存的历史请求/回执，不改执行上下文哈希或测试模式；不新增配置、表或依赖。现有运行 API 的 `actual` 已承载通用 JSON，当前前端没有独立回执 DTO，无需新增页面契约。

本批真实两份八步 JSON、MySQL 固定策略、PG 计划/运行/原工具/审批及 RustFS 原件验证：准备创建两个对象，两个正式用例分别 GET 对应 ID，最后分别 DELETE，现场零剩余，终态不可重领；False/0/null、空键、点号键和空容器保留。执行仍走原 Agent 图、权限与审批，模型选择受控。组合输出不能替代原对象产生范围或清理证据，也不能证明共享消费者状态引擎、unknown 只读查证、默认自主执行或完整 App 已完成。研究、命令和边界见 [P2_OUTPUT_STRUCTURE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_OUTPUT_STRUCTURE_RESEARCH.md)及6.67。

### 2.3.11 清理待审批期间的逐项核验〔前批只读内核；消费状态接入见2.3.12〕

执行逻辑仍由 AI 阅读完整用例、初排并按项目依据补全。共享准备的后续问题是：怎样证明必要消费已结束，而不是把“排序靠前”“GET完成”或“当前RunItem终态”当作证明。当前RunItem自身终态还要等清理，直接让清理等待这个终态会形成循环；正式用例也可能同时产生、消费和清理对象，不能按HTTP方法或before/after一概判断。

先复用 [回执核验器](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_case_assessment.py) 的原内核，提供明确的 `assess_steps(requested_item_keys)`。保留完整固定上下文/hash及全部原ToolJob身份，请求和回执恰好对应唯一的固定项集合。未结束兄弟操作可以保留在清单中，但不能作为检查或输出来源；已完成项的结果、实际输出、核验及pending身份只读返回，没有RunItem completion，也不写消费结束。

旧完整 `assess` 继续要求全项和全部工具证据结束，使用同一内核；旧完整请求字节及结果语义已与提交基线配对验证。没有新增模式、配置、表、协议或前端契约。可编辑配置仍在MySQL，正式计划/运行/原Job/图检查点在PG，原件在RustFS。

真实八步JSON、原Agent图/API/审批及两种SDK验证：POST/GET后DELETE停在原审批，单独核验查询的五个检查并返回passed/failed，原运行、审批和现场数据不变；批准清理后原完整终态正常保存，现场零剩余且不能重领。未结束来源、伪造摘要、裁剪上下文、False/0/null和跨身份边界见6.68。受控模型验证不能证明默认自主Agent通过。

后续实施先研究并固定“实际消费结束”的语义及证据，再在原PG事务中追加不可变结束检查点、由清理动作领取读取；外部消费者与拥有者内部阶段分别处理，保留unknown和原历史。**6.68批次没有连接该状态机制，不能用其通过数证明阻止早删；本次连接范围见2.3.12/6.69，旧真实清理反例继续保留。** 调研见 [P2_CONSUMER_RECEIPT_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_CONSUMER_RECEIPT_RESEARCH.md)。

### 2.3.12 消费阶段结束与清理领取〔新固定策略；内部混合正式项已验〕

检测缺口仍按2.3的简单方法：AI阅读完整用例，判断必要条件由谁提供、是否要补全，不增加注册/登录等固定清单。执行期间需要另分清“必要消费结束”与“整个用例完成”：同一正式用例包含清理时，清理若等待自己的RunItem终态会互等；按HTTP方法或after顺序也无法证明消费结束。

实际研究依据是原[动作领取事务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_case_action_admission.py)、固定效果声明选择器、完整回执内核及原Agent/API/审批反例，见[P2_CONSUMPTION_PHASE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_CONSUMPTION_PHASE_DESIGN.md)。采用下列工程做法：

1. 项目配置提供可选`case-consumption-phase-v1`，沿现有MySQL规划配置保存；新计划冻结到PG，要求已有多效果v2和原动作领取协议。旧计划缺该字段继续原行为，关闭当前配置也不替换已冻结运行。
2. Agent首次执行固定声明匹配的清理动作时，在原`_case_action.consumption_closure`附上理由及当前正式目标的消费回执。读取完整固定预期，引用原已保存普通ToolJob的实际输出；不读取生成模板。系统按已有`select_when`匹配清理，不按DELETE/GET命名猜测。
3. 后端在原PG领取事务内核验固定身份、上下文、原普通动作是否已结束，以及消费回执的实际检查。确证passed或failed都能结束消费；空检查、错误来源、未结束/unknown动作不能关闭。系统核验的是提交的明确消费检查及原证据，不保证AI已经理解所有业务预期。
4. 不可变消费结束记录、原逻辑操作登记与首次清理领取同事务提交。事务回滚不留下单独关闭记录；提交响应丢失保留原running任务和关闭记录，原调用恢复不再发送，别名返回unknown及原任务引用。
5. 后续清理复用原关闭记录，可逐个清理两个或更多实际对象；改变关闭依据不能覆盖历史。关闭后新的普通动作被拒；同身份/输入的已知逻辑操作先复用原结果，不新发送。对象产生与清理覆盖仍由原证据链分别核验。
6. 当前拥有者使用明确的消费阶段记录；外部正式消费者实现按同一运行的已保存终态结果及未结束原动作核验，**6.69批次未验外部组合；本次固定共享来源的两正式消费者实测见2.3.13/6.70**。专门的关闭记录存PG，原件仍RustFS；没有新工具、执行器、模型预算或模式参数映射。

实际验收见6.69：内部正式项创建两个对象→查询→关闭消费→分别清理；正确/错误预期、提前清理、并发、回滚、丢响应、未结束/未知普通动作和错误回执均验证。两项原Agent图/API/六次审批也已通过。关闭记录`run_item_completed=False`，最终完整回执仍保存passed/failed，终态不可重领。关闭证据已在原ToolJob准入记录可查，本段6.69当时未验外部多个消费者；本次两正式消费者共享来源见2.3.13/6.70。独立报告展示、跨租约恢复、多Worker、完整App、默认自主Agent和生产历史迁移继续待验。

### 2.3.13 消费返回证据与清理等待语义〔真实反例修复；共享两消费者已验〕

用例选取、缺口检测仍按2.3：AI读完整用例，判断信息/状态由谁提供，查询项目依据、补全并排序。这里完善的是执行后的清理条件；不增加注册/登录等业务检测清单，不把用例转换成模式参数。

依据原[规划轮次](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_workflow.py)、[范围归一](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/preparation_scopes.py)、[运行映射](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/plan_mapping.py)、原ToolOutputObservation和实际原图/审批/HTTP反例，调研见[P2_CONSUMPTION_RETURN_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_CONSUMPTION_RETURN_RESEARCH.md)。采用两项根因修复：

- **确认已开始动作确有可靠返回。** 原取消只修改状态，不产生返回观察。内部普通动作、外部消费者已开始的原Job和关闭记录恢复共用判定：状态结束；若已经领取开始，还必须具有结构有效的原可靠输出观察。真实执行后返回丢失，即使随后取消，也保留unknown现场。已有返回后取消，或后续工件交付partial，不误判为无返回；最终工件处理可改变output，不能要求它的SHA与原观察SHA相等。可靠返回只证明工具返回检查点，成功、原对象和业务通过仍由各自证据判定。
- **清理自动等待与明确业务前置分开。** 原logic轮保存模型声明、preparation_scope_declaration和logical_hash。后端按原归一/排序重建并核对原哈希及最终固定逻辑，识别系统为共享清理自动加入的等待边；新映射用现有terminal语义等待消费者结束。模型明确声明的业务depends_on及实际required输出绑定仍保持passed/原数据要求，没有原声明就不猜测放宽。

新消费记录在既有normal_job_refs追加原返回观察SHA；读取旧记录不补字段、不重写原hash，但仍核验原动作。外部准入保存同一运行原终态结果及所有已开始Job的身份、原准入/逻辑索引、输出/返回观察SHA；原PG清理领取事务内只读核验，不在锁内调用AI。外部结果failed可结束消费，但它仍作为failed历史保存；未返回动作不能被该结果掩盖。

新启用消费协议的运行快照固定`dependency_mapping_version=case-cleanup-waits-v1`，纳入创建SHA；缺该字段的旧快照维持旧序列化、原依赖和创建SHA。首次提交选新映射，原创建请求重放先读取已存在运行，再按它原映射核对；事务校验也按固定版本重建。此批没有新配置键、表、依赖、执行器或工具；MySQL配置、PG业务/运行与RustFS原件分工保持，前端快照类型同步。

实际验证见6.70：共享来源由首个正式消费者的before准备产生，两个正式消费者使用；最后清理由after准备或正式项自身执行。明确通过要求失败时不自动放宽；自动清理等待可在另一个正式项failed且原prepare已成功时继续。终态不能重领。受控模型证明原图/API/审批及工具链路，不代表默认自主模型或完整App验收。

### 2.4 冻结、执行与证据

最终计划冻结用例完整版本、说明、证据、配置和依赖；后续 Agent 循环不重新召回替换版本。正式目标与前置进入已有套件/RunItem；准备和清理附属相关正式项。

规划响应先保存再判断完整性。新任务冻结`case-planning-response-completion-v1`：明确输出截断或空正文时保存原因、元数据及拒绝正文，不自动进入JSON修正或创建运行；普通非空、未声明截断的结构错误保留原修正流程。旧seed没有该协议则按原语义恢复，不补读当前策略或重写历史。同步计划和持久检查点均保存拒绝依据；这项机制不能证明默认模型能生成完整业务计划，当前验证及失败见6.53。

**原子领取 → 构造完整固定用例上下文 → Agent 理解并执行 → 保存实际工具证据 → 核验原正式回执 → 保存结果及报告关联。** 若原回复存在格式或引用错误，且固定项目策略启用纠正、原输出完整成功、证据归属正确及原预算尚有余额，则插入一次只读纠正，再按原规则核验。它不是每次执行都增加的模型阶段，实际实现和验收边界见2.4.1、6.86。

该过程不读取生成模板。通过 `ExecutionRequest.context` 的 `execution_basis`/`case_execution` 和现有 `context_bundle` 承载，贯穿图、TurnState、审批和持久恢复。服务器核对 run/item/attempt/项目/内部会话；公开聊天不能伪造冻结运行身份。

有依赖的条目等待前置及必要实际输出；独立项沿用现有并发。明确业务前置要求通过；系统为共享清理自动增加的等待在新固定映射中要求消费者结束，见2.3.13。准备条件仍按准备自身结果和必要实际输出判断，所属目标失败不抹去准备成功。共享准备必须明确拥有者、状态、输出引用及消费范围，不能隐式跨项目/运行复用。清理在实际消费结束后进行。这是目标设计；当前已有prepare归属、before/after阶段及6.46原可靠返回中的对象证据保存。6.68补齐只读逐项核验；本次6.69把内部消费回执、关闭记录和清理领取接入原事务并实际验证。新固定策略的逐对象清理覆盖已接并通过6.49实测；本次两正式消费者共享来源和返回未知边界见6.70；完整生命周期及未知只读查证仍待验收，旧策略真实反例继续保留。

**准备结果、实际产生的数据、清理证据分别记录。** 同一步合法创建两条数据，不能仅用一步的最终输出代替两条对象记录；清理回执必须说明针对哪些实际对象、引用什么固定预期和真实证据，未覆盖的对象仍显示待处理。after阶段、工具完成、HTTP 200或执行DELETE均不能单独证明全部必要清理完成。AI仍阅读完整用例决定业务操作，后端沿用原Job、真实输出和字面量字段核验，不新增注册/登录/订单等通用检查清单。

共享准备的计划范围已有实际研究与实现，见6.45及[P4_PREPARATION_SCOPE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_PREPARATION_SCOPE_DESIGN.md)。新可选`preparation-scope-v1`在MySQL配置，AI在清理prepare声明`cleanup_sources`；服务端按原计划正式消费者补齐依赖并冻结排序、拥有者和范围到PG，Agent读取同一范围。清理只等左侧时的真实删除/右侧404已复现；新声明修正归属为右侧后，实际两个消费者均能读取，再清理。原审批等待实验还确认：左侧未结束时右侧不可领取，因此不能简单在左侧清理前再加“等右侧结束”门卫。范围不等于实际消费状态；模型在右侧内部先删后查仍可导致真实失败，多对象与消费完成证据继续按[P4_EFFECT_LIFECYCLE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_EFFECT_LIFECYCLE_RESEARCH.md)推进。

Agent 策略不解释旧 `runner_arguments` 预占业务资源；浏览器、容器等由实际工具创建、续租和清理，沿用现有权限。结果引用本次已落库 ToolJob 证据，核对完整身份及输出路径。 graph及审批恢复现共用工具消息正文，展示实际call_id/job_id及output_reference；未落库或未结束的调用不可作为回执观察。压缩保留身份和来源声明，完整值继续从原Job核验；未执行步骤保存具体blocked/skipped/error及空checks，不能编造not_run观察。模型可读到身份仍不保证最终返回正确格式，正式回执契约仍要求纯JSON，实际未通过点见6.82。确定性比较沿用已有算子，语义判断保留模型理由与证据；工具结束、准备成功或 Agent 自述不能单独证明目标通过。

#### 2.4.1 报告结束后如何形成正式结果

原执行链路的架构缺口是：`finalizer` 遇到没有工具调用的最终回复结束图，`AgentCaseExecutionAdapter._finish` 随后严格解析 JSON；真实默认模型返回报告夹 JSON 时保存 error。原失败和研究结论保留在 [回执衔接调研](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECEIPT_HANDOFF_RESEARCH.md)。该适配器已接入 `AgentCaseReceiptService`，复用原核验服务、对象存储及 RunItem 检查点，不再把已接入能力写为待开发；但真实默认模型纠正仍未通过；本轮403与用户暂缓决定见6.86。

采用现有 `AgentCaseAssessmentService.build_assessment_request` 和 `evidence_manifest`，在原 RunItem/attempt/检查点中接入只读回执阶段，当前实现与后续验收要求如下：

1. 固定完整用例、说明、准备/清理、原图快照和执行身份，先核验原 Job 归属，再取得实际持久输出。模板不参与。
2. 原最终回复是完整合法 JSON 时直接核验；格式或引用有错时保存原回复和具体反馈，才允许有限的只读纠正。输出截断、模型传输失败、未完成操作和归属错误分别处理，不能统一自动重发。
3. 纠正请求仅提供固定预期、真实证据和具体错误，不暴露业务工具。它不能重新导航、点击、创建或删除，也不能把原报告中的指令当作新目标。
4. PG 检查点先保存请求身份、输入哈希、协议、非敏感模型配置和预算，再保存原响应及核验状态。恢复读取已保存响应；只有调用意图而无可靠响应时保留未知，不转回业务执行。
5. 正常 LangGraph 新 turn 已累计 model_call_count；图暂停、快照、TurnState 和审批恢复保存原 max_iterations 及上下文预算，下一次模型准入前检查。一条回复的多项审批不重复计模型调用，进程默认提高不扩大已有预算。纠正检查点另存 execution_model_calls、total_model_calls 与原 max_iterations，要求总数等于原图累计数加一且不超过原上限；原图快照不被改写。没有原累计预算的历史运行不补造额度。不能拿审批数、loop_iteration 或 SDK attempt 替代累计数，也不能暗增预算。6.84 的计数和 6.85 的检查点专项不代表任意物理发送后断电的未知状态已全部解决。
6. 最终回执继续逐项核验实际 call_id、原输出字面量路径、完整预期及清理覆盖。报告、artifact 或内嵌 JSON 不能直接当作通过结果；可读报告沿现有工件体系关联到正式结果。
7. 项目现有 MySQL 规划策略增加可选 receipt_correction_protocol，默认不启用；前端项目规划弹窗沿现有策略 GET/PUT 保存。新计划冻结协议后传入执行上下文；旧记录未配置时省略新增字段，保留旧序列化及哈希。原失败和完成条目不迁移为成功，配置变更只影响新计划。检查点/业务结果放 PG，请求及响应原件放 RustFS，复用现有 JSON 字段，不新增表或另一套执行器。

当前受控验证将保存的真实混合报告作为原回复样本，用本次真实 API Job 构造纠正依据；不能把历史报告中的观察当成本次证据。已验证默认关闭、合法原回执零额外调用、错误纠正不再重试、伪造引用、异项目 Job、截断、预算耗尽、意图未知和已保存响应恢复；检查点提交后的故障通过注入异常模拟，不等同于杀进程。真实生产 API/独立 Worker 专项验证审批和重启、纠正结果及终态重投，模型为受控 HTTP 服务。真实默认提供商的一次逻辑纠正调用超时失败，未采集该调用的物理 HTTP 发送次数。完整默认模型 UI passed、纠正阶段真实进程故障及更多未知响应边界仍待验收，见 6.85。

### 2.5 恢复、计划修订与失败回归

持久规划复用共享工作流、PG检查点和Outbox/Redis，按原请求查询任务；规划完成不自动开始运行。启动保存计划使用服务端固定内容，套件/运行/条目/Outbox同事务，未知回执按原创建请求找回。

恢复分清已成功和真正未知：

- 已保存同身份/同参数的工具返回或终态证据，恢复读取原结果，不再次创建/删除。
- 工具返回但工件交付中断，保留返回检查点和实际完整性状态；不能把 partial 当完整。
- 只有执行意图、没有可靠响应证据，先按项目实际只读能力核对；不能确认就保留未知及影响范围，不能宣称自动恢复成功。
- 审批决定已保存，按原决定续执行；完成条目不重领，旧 attempt 不再写入。
- 已接入可选的一次只读回执纠正，使用原剩余预算和固定证据，不因 JSON 问题再次执行业务。PG 已保存响应则读取并核验原 RustFS 对象，当前模型配置不可用也不重新发送；仅有调用意图而无可靠响应则保留未知。纠正返回错误或超时不再纠正第二次。原失败记录和未启用该协议的运行维持原行为，专项和实际失败见6.85—6.86。

原逻辑操作索引已实现同身份/同输入的已知结果复用和未知不重发，见6.43；它不等于实际效果生命周期。6.46已保存原可靠返回中的多对象来源证据及清理声明检查，原调用和别名分别保留；新可选覆盖判定及固定拥有者映射已接原结果，见6.49；实际消费者状态、未知副作用查证和执行中计划修订仍需补齐。6.44实际DELETE返回丢失场景中，测试服务器已删除对象，而系统原Job仍无可靠返回；系统应保留未知，后续只读查证追加新证据，不能凭测试现场或清理意图回写原Job为成功。修订只作用于尚未执行部分，追加修订身份/理由/新依赖，保留原计划与已完成证据；不能更换进行中条目的固定依据。

失败回归创建新运行，保留父运行失败及证据，重新确认必要前置；已经接入的持久回归与运行创建不重复建设。默认模型曾出现目标回执 `finish_reason=length`、8192输出Token、正文为空的实际失败，必须研究根因并另验，不能靠重复同配置调用或暗增预算声称通过。

## 三、上传版本、解析、差异与需求变更

### 3.1 上传文档的版本管理与比较

**可以沿项目已有代码实现：系统自己保存上传原件、版本及解析快照，上传和比较均不依赖 Git。** 普通上传路由调用 `ApiDocsService.upload_document`；指定文档的新版本路由调用 `upload_version`；比较服务读取指定的两份解析快照。Git 导入只在用户选择该入口时取得来源字节，再调用保存服务。具体代码位置及历史验证范围见 3.1.1。

本功能的必要条件是：系统保留旧原件及其解析依据，用户明确指出新文件属于哪份文档，并选定比较双方。只上传一个文件且系统没有旧版本时，只能保存、解析和生成用例，不能判断“改了什么”。上传新版本也不代表必须立即启动 AI；保存、解析、确定性比较和影响分析分别提交、分别显示结果。

用户操作与系统处理如下：

| 用户操作或当前情况 | 系统处理 |
|---|---|
| 首次上传需求文件 | 创建文档 D、原件版本 V1 并保存文件；可以解析、生成用例。没有旧版本时不产生变更结论 |
| 在 D 的详情选择“上传新版本” | 明确提交 D 和当前父版本；保存新原件及 V2，保留 V1 |
| 选择新旧版本比较 | 先分别解析，再明确选择 V1/V2 的解析快照 S1/S2；比较任务冻结双方依据 |
| 从普通入口再次上传同名文件 | 创建独立文档；文件名或相同哈希不用于自动建立版本关系 |
| 两份文件已经作为独立文档上传 | 当前同文档比较入口不能直接比较 D1/D2；明确选定业务文档，将另一份文件通过“上传新版本”入口加入；不自动合并历史 |
| 旧原件已经缺失 | 标明历史依据缺失；不根据预览或 AI 改写补造旧文件 |

例如“订单需求.docx”第一次保存为 D/V1，修改后在 D 下上传为 D/V2。这里的版本历史是应用数据库中的文档记录，不是 Git 仓库。

```text
普通上传 → 原件 V1 + 文档 D
D 下上传新版本 → 原件 V2 + 父版本 V1
分别解析 V1、V2 → 固定快照 S1、S2
比较 S1、S2 → 确定性差异
AI 引用具体差异 → 需求/用例影响候选 → 评审 → 增量草稿
```

保存原件、解析、比较和 AI 分析分别记录状态。上传成功只证明文件及版本保存成功；解析或比较失败仍保留原件，并显示失败原因。切换解析器产生新快照，历史比较继续引用当时选定的快照。原文抽取差异与归一差异同时可查，不先让 AI 改写再比较；删除需求只提出停用候选，保留历史用例和结果。

工程职责继续采用现有分工：RustFS 保存每个版本的原件与大结果；PostgreSQL 保存项目内文档身份、版本、父版本、来源、SHA-256、大小、解析快照及对象引用；MySQL 保存可编辑解析与比较策略。原件、解析快照和文档身份分别管理，解析器变化不重写原件版本。

版本提交沿用既有契约：同一请求重放返回原提交；新请求的字节等于当前原件时返回当前版本；恢复旧内容创建新的后继版本。预期父版本变化返回冲突。数据库提交回执丢失时先按原请求查询，不更换请求身份重复上传或误删已引用原件。OpenAPI/Postman/HAR 转换及项目 URL 派生修改保留原件。

验收先在未登记 Git 来源的项目完成：上传 V1/V2、读取双方原件并核对哈希、分别解析、选择快照比较、查询失败与原提交恢复。Git 固定 commit、脏工作区及仓库移除后的历史读取另行验收。历史上传回归已验证原件/版本与请求恢复；这不能代替生产解析、比较、完整页面或 AI 业务判断的验收，详情见 3.1.1—3.1.5。

### 3.1.1 可行性结论与具体实现依据

**可以做，依据是项目已有上传、原件保存、版本追加及固定快照比较服务。** 新文件上传保存原件；在指定文档下上传修改后的文件追加版本；比较固定双方解析快照。这里的应用版本记录无需Git缓存。上传与Git共用取得文件后的保存、解析和比较服务，分别提供来源入口；不是上传时再使用Git保存一遍。

**本次源码核对〔基线 `09286a68`〕：** `ApiDocsService.upload_document` 调用原件准备和版本提交；`upload_version` 核对项目、请求与预期父版本后追加或返回当前版本。这两条上传路径没有调用 `GitDocumentService`。Git 的 `import_document` 才读取登记仓库的指定文件，并在取得字节后调用保存服务。这里共用的是保存/解析能力，文档身份与新旧关系由明确的请求决定，不自动把两个来源合并成一条历史。当前上传回归结果和验证边界见文末 6.80。

**当前核对〔2026-10-08，基线4e35de24〕：** 普通上传路由只调用`upload_document`；新版本路由调用`upload_version`，按明确文档、项目、原请求及父版本保存。`GitDocumentService.import_document`先读取仓库字节，再调用上述保存服务；普通上传不会调用这个Git入口。同内容新版本请求返回当前版本，恢复旧内容则追加新版本，同名独立上传保持独立文档。实际重跑两个`[False]`节点：**2通过、0失败、0跳过，pytest输出7.99秒**，见[本次XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-revision-4e35de24-upload.xml)和[本次日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-revision-4e35de24-upload.log)。范围是实际临时文件、应用路由及本地测试存储；未重跑生产存储、解析/比较Worker、完整App或AI影响判断。

**以下均为历史批次证据：** 在`0d046514`核对的比较提交冻结`old_snapshot_id` / `new_snapshot_id`，Worker读取系统保存的快照；普通上传、解析和比较不反向调用Git服务。下文原日期、时长和产物保留，不计入本次通过数量。

本次实际执行下文两个 `[False]` 上传/原件回归节点，结果为**2通过、0失败、0跳过，5.53秒**。原始输出见[本次日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-refresh-0d046514-upload.log)及[本次XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-refresh-0d046514-upload.xml)。真实临时文件经应用路由验证：V1/V2和历史字节保留、同内容返回当前版本、恢复旧内容产生新版本、父版本冲突、请求恢复、同名独立上传，以及OpenAPI派生转换保留原件。测试没有配置Git，使用本地测试存储；本次未重跑生产MySQL/PG/RustFS、独立解析/比较Worker、完整App或AI影响判断。本次三份同步及范围核对见[验证记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-refresh-0d046514-validation.json)。

**以下为历史核对记录，不计作本次验证：** 在 `a990163a` 上核对的调用关系同样是“Git入口取得字节后调用保存服务”；上传变更不需要Git缓存，也不要求上传和Git自动共用文档历史。

本次实际重跑下方所列两个 `[False]` 上传/原件回归节点：**2通过、0失败、0跳过，4.89秒**，见[本次XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-upload-current-a990163a.xml)及[本次日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-upload-current-a990163a.log)。使用临时实际文件、应用上传路由和本地测试存储，覆盖新旧原件、版本追加、请求恢复、同名独立文档及派生修改保留原件；未配置Git。本次没有重跑生产MySQL/PG/RustFS、独立解析/比较进程、完整App或AI影响分析；下文历史专项保留原日期及验证范围，不计作本次通过。三份方案字节一致及本次核对记录见[修订核对](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-upload-current-a990163a-validation.json)。

| 已核对的代码位置 | 实际行为及方案采用方式 |
|---|---|
| [普通上传路由](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/api_docs.py:54) | 调用上传服务，不传Git来源或读取仓库；普通入口负责创建新文档 |
| [新版本上传](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/api_docs_service.py:431) | 依据明确document_id和预期父版本追加；与当前原件相同则返回当前版本；父版本变化返回冲突 |
| [原件保存](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/api_docs_service.py:558) | 原件按文档/版本路径保存对象存储；派生内容独立保存，不覆盖原件 |
| [固定快照比较](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/difference_service.py:48) | 提交比较任务时固定双方依据，后续比较依赖系统已保存的版本及解析快照 |
| [可选Git导入](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/git_service.py:101) | 这个独立入口才只读取得指定commit/blob的文件字节，然后调用上传/新版本服务 |

本轮依据核对与验证（2026-10-07）：重新阅读普通上传、新版本上传、原件保存、固定快照比较和Git导入实现，确认调用方向为“Git导入取得字节后调用保存服务”；普通上传没有反向调用Git。新版本必须指定文档及预期父版本，不能根据同名文件自动建立历史关联。

实际执行以下既有节点：

```powershell
# 工作目录：G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server
..\.venv\Scripts\python.exe -m pytest 'tests/test_api_doc_versions.py::test_actual_files_version_chain_replays_and_current_content_dedup[False]' 'tests/test_api_doc_original_assets.py::test_actual_openapi_original_survives_conversion_and_project_url_updates[False]' -q --junitxml='../docs/upgrade_2026-10-03/plan-current-request-upload.xml'
```

结果为**2通过、0失败、0跳过，3.61秒**，见[本轮日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-current-request-upload.log)和[本轮XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-current-request-upload.xml)。测试使用实际临时文件、应用上传路由及本地测试存储，覆盖V1/V2保留、同请求恢复、同名独立文档、恢复旧内容创建V3、父版本冲突，以及派生内容/项目URL修改不覆盖原件。它们没有配置Git；不据此宣称本轮验证了生产MySQL/PG/RustFS、独立解析/比较进程、完整App或默认Agent。三份方案同步与变更范围见[本轮核对记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-current-request-validation.json)。

此前[文档解析与比较验证记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p3-document-difference-final-delivery.xml)为57通过、0失败、0跳过，其中包含MD/DOCX/PDF真实上传版本及存储快照比较；[可选Git产品验证记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p3-git-full-product-final.xml)为2通过、0失败、0跳过。它们分别证明对应链路，不能把Git验证当成普通上传的前提。上述为历史验收结果；本轮只重新执行前述两项上传/原件回归，没有重跑这些生产专项。历史运行限制及其当时的方案修订见6.37—6.39。生产历史迁移及未完成项仍按第六节跟踪。

### 3.1.2 页面操作与各阶段结果

用户从普通上传入口选择文件，成功后看到新文档及V1。更新同一需求文档时，进入该文档详情选择“上传新版本”，提交明确的document_id和当前父版本，成功后看到V2。页面不根据文件名猜测新旧关联，也不要求用户登记仓库。

V1/V2原件保存后，分别选择版本提交解析任务，再选择这两个版本的具体解析快照进行比较。若同一版本有多个解析快照，页面展示解析器、版本、选项和警告，让用户明确比较依据；比较任务固定所选快照，不随之后重解析改变。历史原件、解析失败、比较失败及重试结果各自可查。

比较结果先展示原文抽取差异、归一差异及来源位置，随后才提交AI影响分析；AI引用实际差异提出候选，评审确认后增量生成草稿。删除需求只形成停用候选，保留历史用例与结果。上传成功、比较成功、AI分析成功和评审确认是不同状态，界面分别呈现。

本入口的验收必须在未登记Git来源的项目中完成：普通上传V1 → 文档详情上传V2 → 两个版本解析 → 指定快照比较 → 历史原件下载与hash核对。可选Git入口另行验收，不作为本流程通过的条件。

### 3.1.3 上传与Git的工程边界

**文件上传后直接保存到系统存储；保存上传历史无需Git。** 上传成功所需的是原件存储和文档版本事务；解析与比较随后作为独立任务提交。MySQL保存可编辑策略，PostgreSQL保存身份、版本、任务及快照元数据，RustFS保存原件和大结果。

已核对的调用方向如下，共用保存服务不产生对Git入口的反向依赖：

| 来源入口 | 实际调用 | Git职责 |
|---|---|---|
| 首次上传 | 普通上传路由 → `ApiDocsService.upload_document` → 保存原件及V1 | 无 |
| 上传新版本 | 文档版本路由 → `ApiDocsService.upload_version` → 核对项目/父版本并保存原件及新版本 | 无 |
| 可选Git导入 | `GitDocumentService.import_document` → 只读取得固定commit/blob字节 → 调用上述保存服务 | 取得来源字节并记录出处 |
| 解析、比较与历史读取 | 使用系统已保存的原件、版本和指定快照 | 无需重新读取仓库 |

因此，普通上传流程不创建仓库、不调用Git服务、不执行commit或写Git缓存。“上传和Git使用同一版本链”不作为功能要求；来源文件先各自保存。用户明确指定已有`document_id`及预期父版本时，才追加该文档的历史。文件名、相似内容和相同哈希均不用于自动合并业务文档。

本项完整验收在未登记Git来源的项目中执行：上传V1 → 同文档上传V2 → 下载两份原件并核对hash → 分别解析 → 选择固定快照比较。另行核验关闭Git来源后的已保存导入版本读取。上传、解析、比较和AI影响分析分别记录状态与失败。

2026-10-06本次文档修订实际运行以下既有回归，结果为 **2通过，0失败**：

```powershell
# 工作目录：G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server
..\.venv\Scripts\python.exe -m pytest 'tests/test_api_doc_versions.py::test_actual_files_version_chain_replays_and_current_content_dedup[False]' 'tests/test_api_doc_original_assets.py::test_actual_openapi_original_survives_conversion_and_project_url_updates[False]' -q
```

前项实际创建临时需求文件，通过上传路由验证V1/V2、历史字节读取、同内容去重、恢复历史内容产生V3、请求恢复、父版本冲突及同名独立上传；后项验证OpenAPI转Markdown和项目URL更新仍保留原件。两项使用本地测试存储，没有配置或调用Git。本次未重跑生产存储、独立解析/比较进程及正式App；这些链路的历史证据见3.1.1和第六节，不能计为本次通过。

最新方案同步时再次实际运行这两项：**2通过、0失败，3.59秒**，保存[原XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-update-upload-verification.xml)和[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-update-upload-verification.log)。验证范围仍是临时文件与本地测试存储，不将其当作生产存储或完整App验收。

2026-10-07本次同步重新执行上述两个指定节点：**2通过、0失败、0跳过，5.89秒**，见[本次XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-sync-2026-10-07-upload.xml)和[本次日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-sync-2026-10-07-upload.log)。这是无Git的上传及原件回归，不代替解析/比较进程、生产存储或完整页面验收。

### 3.1.4 上传变更的具体操作契约〔源码核对bb7cb3a8〕

这一功能采用应用内文件版本管理。用户上传文件即可保存原件；更新同一份文档时明确选择该文档的新版本；比较时选择系统已经保存的两份解析快照。Git配置和仓库缓存均不是这个流程的依赖。

以下为当前路由和请求模型的实际契约，路径相对于服务配置的API前缀。页面已有对应调用，后续优化继续沿用，不新建另一套上传或比较接口。

| 操作 | 已有入口及关键输入 | 保存或读取结果 |
|---|---|---|
| 首次上传 | `POST /registry/api-docs/upload`；当前项目`project_id`、文件名、`content_base64`及页面生成的`request_id` | 原件对象、文档D、V1和原提交记录；新请求不会因文件同名而自动合并 |
| 上传同一文档的新版本 | `POST /registry/api-docs/{document_id}/versions`；`project_id`、`request_id`、`expected_parent_version_id`及新文件 | 项目/父版本核对后追加V2；内容等于当前原件则返回当前版本，`created_new=false` |
| 读取历史原件 | `GET /registry/api-docs/{document_id}/versions/{version_id}/original` | 读取该版本保存的原件；不读取工作区或Git |
| 解析具体版本 | `POST /projects/{project_id}/documents/{document_id}/parse-tasks`；`creation_key`、`version_id`及可选`options` | 保存任务；成功后产生具体解析快照；重新解析不覆盖旧快照 |
| 比较两个快照 | `POST /projects/{project_id}/documents/{document_id}/difference-tasks`；`creation_key`、`old_snapshot_id`、`new_snapshot_id` | 核对同一项目/文档并冻结双方快照和比较策略；成功后保存差异结果 |
| 找回结果未知的提交 | 上传按原`request_id`读取提交记录；解析/比较按原`creation_key`读取`by-request`入口 | 查明原任务或版本是否已保存，再决定下一步；不换请求身份盲目重传 |

普通上传模型的`request_id`为可选字段，这是旧调用兼容边界；正式页面保存请求身份以支持找回。新版本上传要求明确请求身份和父版本。文件相同、业务文档相同、请求相同是三个不同判断：字节哈希核对内容，`document_id`表达文档归属，请求身份用于原提交恢复。

实现依据：[上传及历史路由](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/api_docs.py:54)、[上传请求模型](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/api_docs.py:74)、[页面请求身份](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/documentUploadRequest.ts:26)、[解析路由](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/document_parsing.py:39)、[比较提交](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/difference_service.py:48)。Git只在独立入口读取来源字节，然后复用保存服务，见[Git导入](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/git_service.py:101)。

本项交付判据：无Git来源配置时完成首次上传、同文档新版本、两份原件读取、指定版本解析、指定快照比较；分别检查保存/解析/比较状态与原提交恢复。本次按用户要求更新完整方案时，重新执行3.1.3所列两个既有测试节点：**2通过，4.30秒**，使用临时实际文件、真实上传路由和本地测试存储，验证版本追加、原件保留、请求恢复及同名独立文档。此次未重跑独立解析/比较进程、生产存储或完整页面，历史专项结果见3.1.1；本次核对记录见首页链接。

### 3.1.5 上传变更的状态、恢复及独立验收

上传后无需创建仓库或执行Git命令。页面沿已有接口显示每阶段结果；下表是各阶段交付时必须核对的事实，不把后续处理失败改称上传失败。

| 阶段或情况 | 应保存和呈现的结果 | 独立验收方式 |
|---|---|---|
| 原件保存成功 | 文档/版本ID、原件对象、hash和大小；可以下载该版本原件 | 未登记Git的项目上传V1/V2，读回原字节并核对hash |
| 新版本内容未变化 | 返回当前版本，`created_new=false`；保留本次提交记录 | 使用新请求上传与当前版本相同的字节，确认版本数未增加 |
| 父版本已变化 | 明确版本冲突，让用户刷新后基于当前版本提交新请求 | 两个请求基于同一父版本；核对不会覆盖胜出的版本 |
| 上传回执丢失 | 显示提交结果待查；沿原`request_id`找回版本及原件引用 | 保存成功后丢失回执，查询原提交；不换请求身份重复上传 |
| 解析失败或部分解析 | 原件版本仍保留；单独展示任务失败或快照警告 | 下载原件仍成功；失败原因和实际解析能力可查询 |
| 比较成功或失败 | 固定双方快照、比较配置和结果/诊断；不改写原件或快照 | 比较后新增版本或解析快照，确认历史结果仍指向原双方 |
| AI影响分析及评审 | 分析任务、原文引用和影响候选；确认后才增量生成草稿 | 先核对确定性差异，再核对AI引用及评审；不以上传成功代替业务判断 |

本次重新执行的两项回归覆盖第一、第二及部分请求恢复/冲突行为，使用临时实际文件和本地测试存储。生产数据库/RustFS、独立Worker与正式App应在对应实施批次另外记录结果。Git来源读取、脏工作区和仓库不可用后的历史恢复另行验证，不能成为上传验收的前置。

本次修订在`bb7cb3a8`上再次执行3.1.3的两个指定节点：**2通过、0失败、0跳过，3.54秒**，见[本次上传回归XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-upload-boundary-2026-10-07.xml)及[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-upload-boundary-2026-10-07.log)。实际临时文件经上传路由验证版本追加、历史原字节、同名独立文档、请求恢复及派生转换保留原件；没有Git配置或调用。此次未重跑生产存储、独立解析/比较进程、完整App或默认模型，因此这些项目仍按原验收状态记录。三份方案的同步及文件校验见[本次方案核对](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-upload-boundary-2026-10-07-validation.json)。

### 3.2 Git 仅作独立可选来源

项目登记本地仓库，明确路径和commit，只读获取固定文件，保存完整commit/blob/路径来源，复用文档保存、解析和比较服务。默认创建独立文档，只有明确“导入为该文档的新版本”才追加已有文档。

| 来源入口 | 文件字节从哪里来 | 是否需要Git | 何时关联已有文档 |
|---|---|---|---|
| 普通上传 / 上传新版本 | 用户所选文件，由系统保存原件 | 不需要 | 新版本入口明确指定`document_id`及预期父版本 |
| 可选Git导入 | 已登记仓库的固定commit、路径和blob | 仅这个入口需要 | 用户明确选择“导入为该文档的新版本” |

共用的是取得字节后的保存、解析和比较能力；是否属于同一文档由用户所选业务身份决定，不能由文件名、哈希或来源入口推断。

用户只使用文件上传时，本功能无需启用。用户确实要将Git文件作为已有上传文档的新版本时，需明确提供该文档身份和预期父版本；这是一次主动追加操作，不是上传入口自动建立的Git映射或缓存。

这里共用的是服务和数据结构：普通上传提供文件字节，Git入口只读取得文件字节，两者各自调用保存服务。普通上传不创建Git缓存，不把文件提交到仓库，也不通过文件名、相同哈希或相似路径自动关联Git文档。原件哈希用于校验内容和识别同文档的重复提交，不能证明两个文件属于同一业务文档。

上传不会写入仓库；Git导入不克隆、checkout、提交或自动同步。提交成功后历史读取依赖系统保存的对象/元数据，移除仓库或配置不影响原提交恢复。仓库工作区/暂存修改不能改变指定commit来源。Git额外diff证据是可选扩展，不成为确定性比较依赖。

### 3.3 轻量解析工厂

统一解析接口输入原件和选项，输出文本块、章节/表格、来源位置、解析器/实现版本、能力和警告：

| 格式 | 实现和边界 |
|---|---|
| Markdown | 文本解析，保留原文位置 |
| DOCX | `python-docx`，不能可靠提取的结构明确警告 |
| 文本型 PDF | `pypdf`，页码/位置和抽取局限可查 |
| 扫描 PDF | 首期不做OCR，明确不支持/不完整 |
| Docling | 保留HTTP注册、能力声明和统一转换入口，首期不部署容器 |

依赖已经在本项目声明/锁定。解析由可终止的独立任务进程运行，限制输入/输出、超时、CPU、内存，父进程负责回收；API提交任务后可查询进度与失败，不能直接阻塞执行解析。更换解析器、版本或选项产生新快照，历史依据不重写。任务状态和解析结果的complete/partial/unsupported分别展示。

### 3.4 确定性差异

用标准库 `difflib` 比较固定双方完整文本/解析结果；保留章节、行、字符及表格变化。原始抽取文本与确定性换行归一视图分别可查，二进制原件仍可下载。不先让 AI 改写文档，不以截断预览代替全文。

比较固定双方原件/快照、配置与实现版本，结果保存RustFS、元数据PG、策略MySQL。文档详情提交、双方分页选择、设置、原请求恢复和固定历史下载已有产品证据。部分解析保留警告，不支持的快照不能假装可比较；旧任务晚完成不替换用户正在看的指定历史结果。

### 3.5 P4 三方影响输入和持久 AI 分析

AI分析同时使用：**固定新旧差异 + 完整现有用例版本 + 当时编写来源**。来源缺失、错误、部分解析和原生成上下文截断逐引用留痕；不得读取当前/latest文本冒充旧来源。默认范围是项目非归档用例的启用版本及最新草稿，历史版本可显式选择；不按历史测试模式过滤。默认范围只是当前分析范围，不代表扫描了所有历史用例。

固定提交与输入准备已接：元数据捕获不下载RustFS正文，固定配置、比较及用例版本/全记录hash；PG影响任务与Outbox同事务，后续固定内容另存RustFS并保存不可改输入检查点。重读只用已存输入，不重新选择当前用例/配置，输入准备不标分析完成。新提交已冻结非敏感模型参数、完整Prompt/Skill及环境声明；模型配置和分析指导在MySQL，固定依据随PG seed保存。正式API/独立影响Worker见3.5.6，正式App/任务池见3.5.7，均已有受控HTTP功能证据。最新真实默认模型在固定三规则、三条完整用例场景通过严格分析及九项业务判断，原文位置警告仍使快照partial，用户评审与更多场景未完成，见6.12。单元候选与响应核验见3.5.4，自动协调与全范围结果见3.5.5，模型实测与后续修正见3.5.8—3.5.16。现有 `max_context_chars` 是证据准备容量，不是模型Token预算。超过容量当前明确拒绝，不能截断后宣称完成。

P4接入分以下小步；详细查证见 [P4_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_DESIGN.md)：

| 步骤 | 工程做法与验收依据 |
|---|---|
| 固定提交 | 先按creation_key核对已有任务，再捕获固定比较元数据、用例版本/来源/哈希、策略、模型非敏感参数、Prompt/Skill及环境依据；只读分析与运行操作分离 |
| 固定证据读取 | 耗时对象读取放Worker；扩展读取服务接受固定seed，不能重新选择当前用例或重读当前配置。完整输入保存RustFS，PG记录清单/哈希；没有完整冻结输入不调用模型 |
| 批次清单 | 按固定差异/文档结构分分析单元，记录精确覆盖范围和全部待处理用例。完整用例不截成摘要；大文档切分必须保留位置、边界上下文和跨节引用，无法纳入预算的项明确未处理 |
| 模型调用 | 复用现有ModelRuntimeService；冻结模型参数，核对上下文窗口、预留输出和输入预算。字符数不能冒充精确Token数；不支持计量或超限时明确限制/拒绝，不自动提高配置 |
| 批次检查点 | 保存ready→invoking→response_saved→validated。调用前保存逻辑调用ID/输入哈希；返回后先保存响应，再解析。已保存响应恢复只校验，不重新请求；终态任务不重领 |
| 未知响应 | 有调用意图无响应时标为outcome_unknown，不能宣称未发送。保留已完成批次和未处理范围；后续调用按显式重试及原预算记录新身份，不把未知调用偷偷当未发生 |
| 引用核验 | 原文引用指向固定资产/快照与字符区间，正文切片核对一致；用例引用指向case_version_id、正文哈希及JSON路径。位置合法只证明引用存在，业务含义仍由AI/评审判断 |
| 保存候选 | 保存需求新增/修改/删除/不确定匹配，以及受影响用例/验证点、理由、依据不足和生效判断；不自动修改正式需求、停用用例或开始测试 |
| 页面/运维 | 同步生产初始化、独立Worker/CLI、任务池投影、API/类型/文档比较页。未知提交先GET找回，历史查看不重新分析；独立CLI启动与进程重启需实际验证 |

复用现有PG租约、Outbox和Redis Worker，新增明确的impact领域任务/批次/结果，不塞进planning或difference记录。共享 `PostgresDocumentTaskStore` 已承载提交、查询、租约和失败生命周期；`PostgresDocumentOperationStore` 保留解析/比较的最终快照语义，`PostgresDocumentImpactStore` 独立保存不可改的 `input_checkpoint`。独立impact_calls表已保存单元请求/逻辑调用/响应引用，最终候选结果也已接入独立快照，保留旧解析/比较契约。

输入对象写入与PG事务不具备跨库原子性，采用项目已有提交回查/未引用对象补偿：未知提交保留对象，确认无引用才清理。旧worker迟到写入必须由attempt/租约身份拒绝；成功批次不可覆盖，整个任务失败也保留已完成结果。只有声明范围全部处理且结果核验通过才标分析完整；分析完成不代表评审通过或测试通过。

### 3.5.1 已实现的模型依据冻结与当前边界

新任务在提交时固定模型选择与非敏感参数、MySQL分析配置修订、完整Prompt和Skill块/SHA-256，以及项目元数据指纹。模型密钥、OAuth凭据、端点及认证头原值不复制到PG；端点/header只保存hash。共享参数快照现位于模型契约层，旧规划导入保持兼容，模型发送前仍核对参数变化。

分析指导和模型选择策略使用单独的MySQL配置 `document.impact_analysis_settings`，不往旧输入配置结构补默认字段，避免改变历史输入的canonical hash。历史任务没有模型依据则如实保留；同请求查询/输入恢复继续可用，需要分析时创建新任务，不能补读当前模型冒充旧模型。新增模型/环境/目标发布字段参与请求身份，未填写时保持历史请求hash。

环境名称和目标发布版本只是用户声明，`deployment_status=unknown`。原影响记录不能证明部署；固定环境证据及具体需求/用例版本的显式确认已按5.2.3、6.33—6.41分步接入，正式页面/AI候选及完整执行闭环仍待实施，不能把标签当作已生效事实。

固定请求只在已有输入检查点后构造，包含完整旧/新/当时来源及全部用例JSON，不读取当前模板或Skill。`max_input_bytes`仅计算canonical Prompt/messages的UTF-8内容大小，**不是精确Token计量，也不包括所有提供商封装**；超限拒绝且不截断。模型窗口/输出参数已冻结，原文分段与跨节读取见3.5.2；单元模型请求、内容编码计数/字节估算及逻辑调用总预算已实现，见3.5.3。提供商精确计量仍待验；自动协调采用原估算/总预算，见3.5.5。

功能专项通过真实文件与隔离存储，并实际调用受控本地HTTP提供商验证固定内容、输出参数和发送前拒绝变化参数；这不是默认模型的业务影响判断。此前模型依据阶段未生成候选；单元候选见3.5.4，本轮全范围候选结果见3.5.5，需求评审仍未接入；`input_saved`或清单准备后的`analyzing`仅表示对应准备阶段，构造请求不会自动发送模型。批次响应检查点已接内部服务；独立影响Worker进程与公开API见3.5.6，正式App入口继续推进；共享Handler与自动协调见3.5.5。

### 3.5.2 固定范围清单、读取和引用位置核验〔内部已验〕

分段/读取策略使用独立MySQL配置 `document.impact_batch_settings`；提交冻结修订、单段字符上限、总分段数、边界上下文和读取字节限制。历史seed无策略保持None，旧输入schema/hash不变，不能补读当前配置。当前环境已安装锁定的tiktoken 0.14.0（langchain-openai的依赖），但不同提供商/模型的计量和请求封装仍需逐项查证；此配置不是Token预算。

清单枚举固定旧/新/当时来源的资产、解析快照、原件与文本hash、完整章节目录及警告；同一快照只保存一份范围、保留多个来源角色。按章节起点/块终点分段，超大块按Python字符范围拆分并记录split_block_ids；所有正文字符（含分隔/末尾）恰好归属一个核心范围，读取时额外提供边界上下文。所有用例版本、原始/归一/章节/表格差异及来源缺口均入清单，analysis_status=not_started；清单存在不表示AI已阅读或判断。

固定读取支持分段、任意合法原文区间（供跨节引用）、完整用例JSON/自身说明/来源缺口和指定差异；超限拒绝不截断。表格随相关范围提供结构与来源，过大完整表格/用例仍可能超读取预算，需新任务调整策略，不能标处理完成。引用核验对照完整文本hash和原文切片；用例引用对照版本记录hash、字面JSON路径token和值，区分false/0/null及键中点号。核验仅证明引用存在和一致，不证明业务语义或影响结论。

清单正文存RustFS，独立PG impact_manifests表保存不可改检查点；写入核对固定input_sha256及当前attempt/租约。原任务无需改列/阶段枚举；有清单时阶段analyzing表示范围已准备，不能凭这个阶段推断已经调用模型或已完成；调用事实另查impact_calls记录。未知PG提交保留对象、传播异常；新Store恢复先读取原清单，不上传、不抓新配置，截止时间不重置。此范围准备阶段当时没有生产独立影响Worker/CLI验收；后续独立进程见3.5.6；单元请求/总预算/持久响应已接内部调用服务（见3.5.3），单元响应解析/核验/候选持久化见3.5.4，本轮自动协调固定读取和全范围候选结果见3.5.5。

### 3.5.3 单元模型调用、预算与响应恢复〔内部已验〕

依据现有ModelRuntimeService、两种模型适配器及planning_workflow的先保存意图/响应原则，新增 [DocumentImpactCallService](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/impact_calls.py) 与组合式PG调用Store。固定清单的每个segment、difference或case均有明确单元；请求含该单元完整内容、显式追加的固定读取、来源目录/缺口和未知生效状态。用例只读取完整JSON/说明，不读生成模板。跨节续读保留原全部消息与完整响应，再追加固定范围，不用摘要替代；输入过大拒绝，不静默删历史。

可编辑策略使用单独MySQL配置 `document.impact_invocation_settings`：逻辑调用数、累计预留估算量、单调用超时、响应大小、内容计量方式及封装预留。提交冻结策略修订、实际适配器/超时和该模型实际SDK版本；无关提供商SDK变化不会拒绝。ModelRuntimeService增加可选expected_runtime_parameters发送前核对，旧调用省略时保持原行为。历史seed没有调用快照时拒绝新调用，但原输入/清单/历史查询仍可用。

默认UTF-8字节估算内容；可显式选择cl100k_base或o200k_base，用现有tiktoken.encode_ordinary计数并冻结版本。BPE内容计数对所选编码实际准确，但编码与提供商模型映射及消息封装未证明，**总输入仍标estimate**，不是模型精确Token数；加封装预留和固定输出预算后检查窗口。准备请求时原子预留逻辑调用及累计估算额度，ready未发送也占预留；不退款，不按HTTP返回缺失假定未计费。真实响应usage单独保留，不能混为估算或预算保证。

请求和完整成功SDK响应存RustFS，PG impact_calls保存不可改对象引用、单元/续读/重试身份和ready→invoking→response_saved记录。多协程只能有一个原子发送权；同初始单元不能换key偷偷重发。请求或意图PG提交回执不确定时传播异常，只读回查；已保存响应恢复不重发，终态任务不重领。只有意图无响应时新租约拥有者标outcome_unknown；显式新重试引用旧未知调用、使用原固定请求并继续占剩余预算，不重置截止时间。组件调用仍依赖Worker维持原任务租约；生产Worker/心跳集成另验。

供应商报错、长度截断、空正文和意外工具调用均先保存响应并分类，不认为有效候选，更不执行工具。错误诊断可能含凭据，只保留允许的错误元数据并明确省略原因；成功SDK原响应保留。超响应大小不截断成成功，记录response_unavailable，保留已消耗额度。RustFS或PG响应保存失败保留invoking、传播未知检查点错误；恢复不盲重发。明确拒绝/竞争失去的请求对象经实际PG无引用查询才补偿清理；未知提交保留对象。

SDK保持原有重试：当前受控HTTP500实测1次逻辑意图产生3次HTTP请求。记录transport_attempts=not_observed，不能宣称物理单次发送、精确总计费或HTTP次数受到逻辑预算限制。本轮并未修改SDK重试设置，完整发送阶段追踪仍归P0。单元response_saved不等于validated、完整覆盖或分析完成；结构化单元候选解析和引用事实核验见3.5.4；自动协调与结果见3.5.5；独立Worker/CLI见3.5.6；引用语义评审及正式页面仍待实现。

### 3.5.4 固定响应协议、候选与独立核验记录〔内部已验〕

依据现有typed规划协议、先保存响应再解析及固定引用读取，接入 [DocumentImpactResponseService](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/impact_response_service.py)；查证细节见 [P4_RESPONSES_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_RESPONSES_DESIGN.md)。该阶段最初冻结impact-call-v2的完整响应Schema、指导正文和SHA-256；后来v3提供版本记录引用哈希，见3.5.8；当前新任务采用v4固定身份绑定，见3.5.10。已提交v2/v3任务继续读取其seed协议及原目录形状。代码当前Schema变化不能替换已提交任务的协议。历史seed没有协议快照保持None，已保存请求/响应及其显式未知重试仍按原请求恢复；准备新单元或新续读时另建任务。旧请求、未知重试及原Input/hash不重写。

impact-response-v1明确read或finish：read只能请求固定分段、完整用例、差异或原文区间；finish保存本单元的analyzed/evidence_insufficient声明、规则变化候选、用例影响/验证点、理由/引用和未解决项。局部候选key不是正式需求身份；applicability_suggestion只为模型建议，后端deployment_status仍unknown，semantic_status仍pending_review。单元结束不证明全范围覆盖，依据不足也不伪装完整。

后端确认结构、单元身份、实际已提供范围与固定引用。原输入里有资料不等于该调用已获得资料：原文以不可改请求的primary/additional_fixed_reads及完整旧消息为准；用例必须向该调用提供完整固定版本。目录/标题/章节元数据不当作正文；raw及章节/表格差异的原文范围可作为已提供证据，normalized坐标不能冒充原文位置。引用再对照固定文本切片、哈希、用例字面JSON路径和值；这些事实核验不证明业务影响结论正确。

解析允许围栏/说明文字，拒绝重复键、非有限数字、损坏整体JSON及多个对象歧义；不从坏对象中抢救嵌套片段。read_requested、unit_finished、rejected、ineligible分别保存；错误说明不复述原响应到日志。来源缺口、部分解析和差异警告由后端带入核验记录；候选只待评审，不启用/停用/执行。

RustFS保存核验正文；独立PG impact_response_checks按(call_id,validator_version)不可改关联原请求/响应/清单哈希，原call的response_saved和原响应对象不变。写入使用现有任务行锁、项目/seed/活租约及attempt/worker/token；并发只保留一致的原检查点。原记录恢复只读，不重解析、不抓当前配置、不调用模型；终态可读不可新写。PG/对象提交未知保留对象、传播检查点异常，确认无引用才清理。共享对象写入/未知回执逻辑从调用服务提取，保留原异常导入兼容，不另造状态通道。

该响应阶段尚无自动协调器；其补读→继续专项由显式调用方接续。本轮在3.5.5另接自动循环和全范围结果，生产影响API/独立Worker/CLI见3.5.6；正式页面仍未验收。首轮真实功能31项通过后补充协议冻结、旧调用hash及对象未知写入边界；最终相关回归另列第六节。没有调用默认影响模型，不将受控HTTP响应当作真实AI判断。

### 3.5.5 自动补读、跨单元规则传递与全范围结果〔内部已验〕

查证与修正见 [P4_WORKFLOW_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_WORKFLOW_DESIGN.md)。新增 [DocumentImpactWorkflow](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/impact_workflow.py)，复用原调用/核验组件和PostgresDocumentOperationStore结果生命周期，不用独立循环单元冒充规则到用例影响分析。

新提交冻结impact-coordinator-v1指导正文和hash。已有seed没有工作流协议保持None，原输入/请求/响应/核验仍可查询；自动协调需另建任务。旧DocumentImpactInput、CallInput、CallSelection和响应Schema不加新默认字段；协调上下文只在新工作流请求的user payload中显式增加。恢复原调用核对原上下文，不读取新配置或重写旧消息。

流程为：**固定输入/范围 → 文档及差异单元自动补读/结束 → 从已存核验建立完整规则候选集合 → 逐个完整用例读取集合及当时来源并判断 → 核对规则到用例覆盖 → 保存固定全范围结果 → 发布任务终态。**

| 工作 | 当前采用做法与边界 |
|---|---|
| 自动补读 | read_requested按明确固定身份续读同单元，保留原全部消息/响应；沿用原逻辑调用、估算量和截止时间限制，不自动重试未知发送 |
| 规则来源 | 每条候选保留原call/check/local_key；仅完整候选正文完全相同（不含局部key）时分组，所有来源保留。相同引用但不同正文列为潜在重叠/冲突，不凭标题相似合并，不当作正式需求身份 |
| 实际传递 | 用例调用获得规则集合完整正文、来源检查点、发现阶段结果和实际固定原文范围；读取完整JSON/自身说明/当时来源。准备时核对候选与已存核验；结果重读核对该用例所有已存请求确实获得同一集合 |
| 覆盖核对 | 模型按规则身份逐条判断当前用例的none/minor/major/uncertain并引用实际字段；漏规则、混用其他版本或依据不足不能计为该用例完整。没有规则仍处理完整用例及来源充分性；发现阶段未完整时结果保持partial |
| 未完成范围 | 所有声明单元均列入结果；analyzed、evidence_insufficient、rejected、ineligible、unknown、unprocessed、incomplete分别保存。调用数、累计估算、单请求字节/上下文、截止时间及回执类型保留具体原因；原回执不会因核验失败被丢弃 |
| 结果入库 | PG新增影响快照表，复用原checkpoint/result_saved/finish事务与租约；RustFS保存完整结果。completed表示结果已保存，可为partial；语义pending_review、部署unknown，不启用/停用用例、不执行测试 |
| 恢复 | 先读取数据库当前输入检查点，不能因旧claim未包含检查点重新准备。已存调用/响应/核验在截止时间耗尽后仍可只读恢复；对象或PG提交回执未知保留对象并传播，不重发已存模型响应 |
| Worker生命周期 | [DocumentImpactTaskHandler](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/document_impact_task_handler.py)薄型复用现有领取/心跳/取消/终态重投；生产初始化、独立影响CLI和正式API见3.5.6 |

本步没有新可编辑策略或依赖，沿用MySQL已冻结的影响/读取/调用配置；代码协议随seed存PG用于历史重放。新协议将来变化必须版本化，不原地改已有任务。受控HTTP固定响应只能证明链路和引用事实，不能证明真实AI业务影响判断；该内部阶段当时尚无正式生产入口/独立影响进程，后续接入见3.5.6；默认模型、评审/矩阵/增量草稿仍待推进。

### 3.5.6 正式 API、共用装配与独立 Worker〔真实进程已验〕

查证及失败修正见 [P4_PRODUCTION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_PRODUCTION_DESIGN.md)。API和独立Worker复用 [同一影响装配](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/document_impact_runtime.py)，上游解析/比较只读来源与比较Worker共用构造；沿用共享资源回收、Outbox、Redis、领取/心跳及现有固定检查点，不新建任务平台。

项目下四类影响策略提供GET/PUT，内容仍在MySQL；文档下提供impact-tasks提交、原请求恢复、分页/任务查询、按任务读取保存结果，以及impact-snapshots分页/固定结果查询。提交返回202，完整输入准备及模型调用在独立Worker。全局Bearer鉴权及项目/文档归属核对沿用既有机制；异常按现有404/409/503及原请求恢复提示处理。所有结果读取仍做完整对象/hash/固定输入/引用/规则交付核验，不在GET时发送模型。

独立CLI为 `python -m src.cli.run_document_impact_worker`，派发沿用 `python -m src.cli.run_task_outbox_relay`；各自独立运行，可用--once处理一轮。既有连接、派发开关和stream登记来自部署配置；新增业务策略没有放入环境变量。模板.env.example登记三类文档stream，实际用户.env不改。Worker不启动API、浏览器、录制或测试执行器，也不提交新的上游来源任务。

真实MD/DOCX/PDF及DOCX两适配器通过正式API上传V1/V2，独立解析/比较后提交完整12步跨模式用例与影响任务；实际独立relay和Worker处理。核对MySQL策略、PG冻结seed/Outbox、RustFS原件/结果，并保留实际HTTP消息及请求/响应/核验/清单/输入原始对象。后续配置和新草稿不能替换已提交依据；API重启、错误Bearer/跨项目访问、终态重投及关闭派发后的历史读取分别核验。

真实在途Worker被终止后，新进程重新领取原任务，第一调用保持unknown且实际只发送一次；其余单元继续处理，结果保持partial和原依据限制。正常流程全部声明单元analyzed仍保留抽取坐标及DOCX/PDF能力警告；不能把单元完成或任务completed改称原件完整语义分析、部署生效或测试通过。默认模型业务判断没有在这组受控HTTP测试中验证。

此前阶段同步前端API客户端/DTO和受影响测试/构建；固定seed按只读完整JSON保留用于审计。正式页面/任务池的后续接入及浏览器实测见3.5.7。生产历史迁移、需求身份/评审、矩阵、增量草稿与完整P0—P5继续推进。

### 3.5.7 正式页面、任务池与固定历史〔真实App已验〕

研究依据与实施设计见 [P4_PRODUCT_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_PRODUCT_DESIGN.md)。固定比较任务详情及文档详情内的比较结果，提供影响分析入口；可选择项目当前范围或当前项目的具体历史用例版本，不按测试模式过滤。输入环境/发布版本声明及可选模型，服务器冻结实际配置与完整依据。四类设置通过已有API保存MySQL，历史任务不读取新策略替换旧依据。

浏览器在发送前保存原creation_key、固定比较、用例范围和声明；未知回执按原请求查询/重试。切换比较不会改写已经保存的未知请求。历史列表分页并打开明确task_id；任务池联合分页读取PG权威表，仅输出专用公开摘要，不带输入检查点、工件地址、seed或租约。文档标题只与同项目关联，旧响应不覆盖新文档/历史选择。

任务详情轮询非终态，结果从固定snapshot读取。分别展示文档规则原文引用、case单元的用例影响/字段引用/验证点、所有单元处理状态、未完成原因、依据限制及潜在冲突；false、0、null保留字面JSON。completed仅标“影响结果已保存”，partial、pending_review和deployment unknown独立呈现；没有启用/停用/执行操作。支持下载完整固定结果，归档项目保留只读历史。

本轮真实DOCX7→9、完整12步用例、构建App/router、生产API与独立解析/比较/影响CLI、实际MySQL/PG/RustFS/Redis及HTTP已验证：四类配置保存、明确选择版本、实际丢弃已提交回执、刷新找回同任务、提交后指导/新草稿不替换固定依据、真实准备容量失败、任务池查看指定旧结果/失败、API重启关闭派发后读取及完整JSON下载。受控响应证明链路及引用，不证明默认外部模型业务语义；需求评审/矩阵/增量草稿仍继续。

### 3.5.8 默认模型取证与用例引用依据修复

查证与实际样本见 [P4_DEFAULT_MODEL_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_DEFAULT_MODEL_DESIGN.md)。使用真实Markdown V1/V2、修改/新增/删除规则、不变负向条件与三个完整8步用例，配置/业务/大对象分别使用隔离MySQL、PG和实际RustFS，调用当前真实默认模型step-5-preview；没有提高8192输出上限或修改60秒SDK超时。

三次复杂探针均未取得响应，实际调用120秒后保存unknown。第一轮拦截httpx未覆盖当前SDK的httpx2，不能声称未发送；第二/三轮拦截实际SDK客户端均确认两次相同POST，第一次ReadTimeout，第二次被总预算取消。第三轮已经使用修复后的v3；原证据及未知调用保留，不自动重发，也不将引用缺口当作超时根因。连续失败后切换现有连接检查，一次HTTP约2.04秒返回pong；后续应研究实际自动协调的单元输入、跨节补读及耗时，不能继续盲重跑同一复杂探针，不能将连接成功当影响语义验收。

实际固定请求要求模型输出`version_record_sha256`，却只提供case_id/version_id和完整JSON，没有提供该记录哈希。新提交冻结impact-call-v3，在用例目录提供清单中已核验的记录哈希与正文哈希；指导明确复制记录哈希而非content_hash。完整用例仍必须实际读取，引用路径/值和未读拒绝不放宽；目录哈希不等于证据已读。旧v2请求目录与消息保持原样，历史输入、请求及协议hash不重写。没有新增依赖、公开API字段或数据库迁移。

真实失败输入的旧请求回放、新目录与字面false/0/null引用、实际SDK请求全文和固定输出预算已验证3项；真实文件/HTTP/存储及独立生产Worker的回归按6.4记录。默认模型语义、自动全范围人工基线仍未通过，需求评审、增量草稿和矩阵继续推进。

### 3.5.9 真实自动流程查证与下一步协议设计

本轮直接使用既有 `DocumentImpactWorkflow` 与共享租约Handler，读取同一组真实修改/新增/删除需求及三个完整8步用例，未预填候选、替换提供商或改生产默认模型。源码确认当前顺序为：原文段 → 差异单元 → 完整用例；每个单元可请求补读，全部发现候选再传给每条用例。这个样本的清单有2个原文段、6个差异单元、3个用例，共11个单元。它是自动流程实测，不再用此前单次全资料探针代表该流程。实际结果见6.5及 [默认模型设计记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_DEFAULT_MODEL_DESIGN.md)。

实际响应已经证明一个协议问题：首个原文单元请求补读时，模型将单元ID中的 `d27` 写成 `4b27`，被现有核验以 `unit_mismatch` 拒绝。另一个单元成功进入自动补读，续读请求约49KB，随后超时；不能据此证明请求大小就是超时根因。当前协调器收到拒绝便结束该单元；已有用例规划工作流则保存核验反馈并在预算内继续。二者行为不能混为一谈。

下面是上一轮提出的修正方向。固定身份绑定已按本轮3.5.10实施；推理任务聚合、阶段预算及校验反馈仍待调研验证和实施，不能一并写为已上线：

| 已查证依据 | 采用方向 | 必须验证 |
|---|---|---|
| 实际响应误写当前单元ID，当前请求又要求模型重复长ID/哈希 | 新协议把当前单元绑定在服务端调用记录，模型只返回分析内容；资料和用例引用可使用本次固定目录中的短引用，后端按冻结目录转换正式身份及哈希 | 未登记引用、跨项目/跨版本、未读内容仍拒绝；不能模糊匹配或自动修正错误UUID；原响应与转换记录都保存 |
| 原文切片、来源哈希、完整用例及字面路径已有独立核验 | 身份由系统填入后仍运行同一事实核验；AI负责影响含义与引用选择，后端负责固定身份和完整性 | 引用存在不冒充语义正确；false/0/null、最后一步和自身说明必须实际读取；歧义保持待评审 |
| 自动流程把原文、raw/normalized/章节差异分别排成独立模型单元 | 区分“证据清单”与“推理任务”；研究按实际变更聚合相关证据、让跨节资料按需补读，再对完整用例判断 | 全部固定证据保留，原文段/差异/用例都有覆盖映射；不能因减少调用而省略范围，未处理项明确显示 |
| 新旧原文仅数百字符，仍有多个超时和续读；项目总预算与SDK尝试共同消耗时间 | 联合核对任务期限、各阶段调度、续读次数及实际SDK尝试；新策略存MySQL，提交时冻结 | 小样本也要完成规则到用例的判断；期限耗尽保留partial/unknown和未处理项，不能只调大超时或把任务completed当业务成功 |
| `planning_workflow.py`已保存结构校验反馈并限制轮次 | 复用反馈与检查点的做法；仅对已收到完整响应的可纠正结构问题继续，新逻辑调用保留原响应和具体反馈 | 不重发未知调用，不通过忽略核验换取成功；终态只读不发模型，预算耗尽明确停止 |

新增协议/协调版本只用于新任务。旧v2/v3请求和coordinator-v1结果继续按原规则读取，不重写历史消息、哈希、检查点或失败。先用本轮真实失败输入回放核对兼容与绑定，再验证受控HTTP的错误引用、补读、恢复，最后用真实默认模型与人工业务基线验收；尚无真实语义证据时，需求评审/矩阵/增量草稿也不能被写成完整闭环。

### 3.5.10 v4 固定身份绑定〔已接入，新语义尚未通过〕

依据与设计见 [P4_BOUND_PROTOCOL_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_BOUND_PROTOCOL_DESIGN.md)。新提交冻结impact-call-v4及模型wire格式impact-response-v2；[短引用绑定](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/impact_identity_bindings.py)依据冻结清单建立S/P/C/D目录，规则R目录来自已核验的协调候选，续读映射稳定。

模型不返回当前单元、正式版本ID或哈希。当前单元取原调用selection；短引用转换成清单中的正式来源/版本/哈希。用例判断阶段只返回rule_ref及用例影响，后端保留原固定规则正文、来源和说明，再检查逐规则覆盖。短引用仅在该固定调用范围内有意义，不是正式需求身份、跨项目标识或已评审关系。

还原后的DocumentImpactDecision继续走原范围、未读引用、字符切片、哈希和字面JSON值核验。资料目录不代替正文读取；完整JSON、自身说明、False/0/null和最后一步保留。未知引用、任意UUID/哈希覆盖、错误路径/原文和篡改目录仍拒绝；不模糊纠正错误ID。原wire响应与还原后的核验对象分别保存，runtime_versions记录impact-bindings-v1，日志记录绑定版本及静态问题代码。

旧v2/v3请求、Input/hash、响应、检查点及coordinator-v1结果不改写；新版本不自动重发原未知调用。正式API路由和结果结构不变，既有seed协议版本新增v4；前端对应字段为通用记录，当前结果类型仍兼容。无新增依赖、SQL表/列或可编辑配置，策略MySQL、业务PG、大对象RustFS约定保持。

旧协议专项显式注入v3工厂，验证历史兼容；新专项使用默认v4验证真实MD/DOCX/PDF、两适配器、自动补读、完整用例、规则传递和终态读取。生产API/独立Worker另验证默认工厂与真实消息。真实默认模型两份响应都返回v2并通过短引用补读，但续读仍超时，三个用例未被判断；详见6.6。后续转向任务划分、阶段预算和已有流式机制的实际诊断，不原样重复本轮自动任务。

### 3.5.11 推理任务、流式及输出预算查证〔研究基线f8d2f34e〕

依据与采用方式见 [P4_STREAM_DIAGNOSTIC_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_STREAM_DIAGNOSTIC_DESIGN.md)。当前普通影响调用与已有用例规划的流式调用不同；安装的SDK和ModelRuntime已有流式能力，首期不需要更换SDK。但“可流式接收”不等于“可完成分析”。

实际新建Markdown V1/V2、三个完整8步JSON及隔离MySQL/PG/RustFS，三项新诊断均失败：完整依据单元在120秒内无正文，保存unknown；分阶段发现调用及另建任务的low诊断都持续收到reasoning片段，最终length/8192完成token/正文0。合法规则没有产生，没有完成任何用例影响判断；完整依据诊断曾发送全部JSON，两次分阶段诊断没有发送用例。没有预填候选、改源模型、增大全局输出或回写旧未知调用。low在隔离项目MySQL保存，诊断wire实际发送；它尚未进入正式生产请求/seed契约，200不能证明其深度控制效果。详细数字和证据见6.7。

这修正了此前“先接流式再复验”的推断：当前样本的输出预算消耗也是实际问题；没有证据把过去所有超时都归为同一原因。reasoning_tokens=0与实际片段不一致时保留原usage，不能声称推理token是0；reasoning和reasoning_content是同一内容的别名，不重复累计。

后续按以下职责研究新协调/响应版本，不继续重复本次流式请求或更换low/medium试碰：

1. **保存完整证据清单，另划推理任务。** 新旧文档原文、raw/normalized/章节/表格差异及用例全部保留；相关证据可进入同一规则发现任务，但须保存逐证据覆盖映射，不能给所有证据单元复制同一响应来冒充已处理。
2. **由系统提供引用位置，AI选择实际依据并判断影响。** 当前v4已绑定身份/哈希，仍要求模型填写字符偏移和原文。研究利用已有解析块/实际读范围提供明确引用锚点，后端还原来源、偏移与原文，再执行既有事实核验。未知锚点、未读内容和歧义保留；不猜位置、不改原响应、不让AI自行改写原文。
3. **先发现规则，再判断完整用例。** 固定规则正文/原文引用复用，每条用例读取完整JSON、自身说明及编写来源；保留负向条件。发现没有合法结果时明确无规则依据，不能把未进入用例阶段写为用例无影响。
4. **预算与传输选择成为明确冻结契约。** 若采用新的推理参数或流式，配置在MySQL，实际值随新任务/请求冻结，并同步原生/LangChain、前端和运行核验。规则发现、补读、用例阶段各有可查消耗/未处理范围，未知不重发；旧协议/请求/结果仍按旧规则读取。

此处记录当时第2项的研究方向；后续引用实现和实测见3.5.12。尚未证明它能解除全部模型截断，不能写为根因修复已完成。官方参数查证不能代替当前网关实测，新的模型输出仍须通过真实引用及业务基线。完整需求评审/增量/矩阵和P0—P5继续按原目标实施。

### 3.5.12 实际原文引用锚点与v5兼容〔已接入，完整语义待验〕

依据来源 → 采用做法见 [P4_TEXT_ANCHORS_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_TEXT_ANCHORS_DESIGN.md)：已有固定范围/解析块、raw/章节/表格差异已经提供准确原文及位置，因此由后端提供引用目录，模型选择文本并判断含义，不再要求模型重新计算引用位置。这个职责调整有代码依据；尚未证明字符计算是历史超时或截断的全部根因。

新提交冻结impact-call-v5，模型回复impact-response-v3；实际读取的原文登记为T1等引用，后端还原来源、原文、偏移及哈希，再执行原有事实核验。规范结果、公开API/前端结构及coordinator-v1不变。旧v2/v3/v4原请求、协议hash、规范结果和终态按旧规则保留，不回写旧任务。

引用只登记当前与此前消息实际提供的内容：局部范围及相交解析块仅提供已读切片；raw/章节/表格使用原文范围；normalized坐标、空范围及用例目录不作为文档引用。补读追加引用编号，重复来源/范围复用编号；两个版本的相同文本仍保留不同来源。未知引用、目录篡改、正文篡改、用例字段/值不符继续拒绝，False/0/null及最后步骤保留。完整原文和完整JSON不缩减；超预算明确拒绝，不截断后宣称完整。

MD/DOCX/PDF×原生/LangChain、实际HTTP/MySQL/PG/RustFS回归、终态历史及独立API/Worker已有本轮证据；实际默认模型全流程另测，具体结论见6.8。没有SQL迁移、新依赖、全局模型参数变更或正式流式/推理参数接入。引用存在与业务判断正确分别验收，不能将受控响应通过当成默认模型已可用。

第3.5.11中的证据任务聚合已从3.5.13固定计划基础推进至3.5.14的生产调度及阶段响应，实际发送继续沿用冻结预算/传输。保留每份证据的覆盖映射，不复制同一回答冒充多个单元完成；先取得合法规则，再逐条判断全部固定完整用例及当时来源。仅改引用格式没有完整成功证据时，不继续原样重复调用。

### 3.5.13 完整证据与固定推理任务计划〔基础已验，后续消费见3.5.14〕

依据来源 → 采用做法见 [P4_REASONING_PLAN_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REASONING_PLAN_DESIGN.md)。真实v5样本126/139字符的新旧文档形成2原文和6差异发现单元，3条完整用例尚未进入判断；旧协调器逐单元调用，候选只按完整正文hash去重。这提供任务组织改进的依据，不证明所有历史超时都由重复发现造成。

**保存完整证据清单，另保存固定推理计划。** 发现组按原清单顺序及冻结的成员数/完整读取字节限制划分，每份非用例证据恰好归属一个组。raw、normalized、章节、表格及原文证据保留；超限单项仍保存且明确原因，不截断、不按模型答案合并需求。组保存稳定hash、成员、首个证据锚点、初始完整读取选择及字节数。读取字节预算不等于模型Token容量，实际发送仍须计量完整Prompt、协议和上下文。

用例推理项绑定每条完整固定JSON及自身说明，通过当时source_links关联所有固定原来源分段。缺失来源继续作为source_gaps呈现；不以当前文档、当前配置或最新用例替换依据。计划准备状态固定为not_started，计划保存成功不计为AI分析完成。

| 职责 | 当前实现与状态 |
|---|---|
| 可编辑分组策略 | 独立MySQL键`document.impact_reasoning_settings`；新提交冻结修订及实际参数，已有影响策略语义保留；此阶段仅服务层，后续公开API/页面见3.5.14 |
| 固定计划与恢复 | RustFS保存计划正文，PG新增`<docs_table>_impact_reasoning_plans`保存task/input/manifest/config及对象hash/大小检查点；实际初始化、同内容并发唯一、租约/项目核对已验 |
| 历史与未知回执 | 先读取已有检查点，恢复不读当前配置、不调用模型、不重新上传；PG提交回执丢失保留对象；旧任务未冻结策略不补造新计划，原输入/结果照旧可读 |
| 自动执行 | 此阶段生产Worker仍使用旧coordinator-v1；后续新任务消费冻结计划与实际调用数量见3.5.14/6.10，旧任务按原协议恢复 |
| 后续阶段契约 | 新版本协调器消费冻结组；响应逐证据报告处理情况，规则建议属于推理任务；用例阶段读取合法规则、完整用例和原来源。同步Skill、wire、结果、API/前端；不能把组级成功复制给全部证据 |

本阶段验证见6.9；后续接入与默认模型完整流程见6.10。全部证据与3条完整8步用例仍须实测，保留原失败和unknown，不原样重发旧任务。

### 3.5.14 分组生产协调、逐证据声明及阶段契约〔已接，默认模型未完整通过〕

依据来源 → 采用做法见 [P4_GROUPED_COORDINATION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_GROUPED_COORDINATION_DESIGN.md)。旧协调器将每份原文/差异独立发现，原结果又要求单元候选等于原响应；因此增加coordinator-v2推理任务结果及逐证据声明，复用现有调用/补读/租约/预算/unknown与结果发布，不把组响应复制为多个候选。

新任务冻结impact-call-v6、coordinator-v2、wire impact-response-v4与validator-v2。发现阶段实际读取组内完整原文及差异，模型按固定P/D成员顺序返回outcome及具体reason；后端核对成员完整且唯一、引用范围、原文是否确实提供。模型声明已读并不证明业务理解正确。规则及来源属于发现任务，证据成员不保存复制的规则候选。

用例阶段预读完整固定JSON、自身说明、当时全部原来源分段及规则引用，逐R判断每条规则，保留负向测试条件。发现失败或没有合法规则时，显式保留未知/依据不足，不推导为“没有变化”。完整证据清单及全部用例继续进入结果units，snapshot.unit_count不改成组数；另保存discovery_tasks和固定计划检查点。恢复核对计划、完整结果、实际请求、原核验和规则出处；已结束任务不可重领，unknown不自动重发。

项目设置增加GET/PUT `/document-impact-reasoning-settings`，沿用既有鉴权/项目隔离，配置实际写MySQL，新任务冻结修订，改变当前设置不改变在途计划。前端区分推理组数和证据/用例总数，展示成员状态、理由及计划引用。PG保存业务检查点，RustFS保存对象；旧v1及v3/v4/v5调用/结果读取保留，读取原response checkpoint的validator_version，不能用新核验覆盖旧历史。

本轮真实默认模型首次返回三项规则变化及八条覆盖声明，但finish的unit_outcome为null；模型侧Schema允许、canonical结束校验禁止，是实测发现的两侧契约不一致。修正同一分组wire的动作校验、字段说明及冻结提示：finish必须明确analyzed或evidence_insufficient，read才允许null；没有从覆盖声明代填结果，没有修补旧响应。用实际保存失败正文回归，另建新任务验证，具体结果见6.10。传输超时仍独立保留；契约对齐不等于所有超时已解决。

后续按真实请求/响应定位未完成环节，取得完整规则与全部固定用例判断后再进入需求评审、稳定需求身份、追踪矩阵及增量草稿；精确Token、环境部署证据、生产历史迁移和其他P0—P5项目继续按原范围实施。

### 3.5.15 项目调用策略、SDK发送与真实业务边界〔已接，完整判断仍待验〕

依据来源 → 采用做法见 [P4_TRANSPORT_BUDGET_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_TRANSPORT_BUDGET_DESIGN.md)。上一轮实际发现请求在60秒SDK超时后重复发送，到120秒逻辑期限取消；因此沿用现有客户端工厂与调用预算，增加项目可选发送策略，不修改全局模型或其他测试模式。

| 策略与职责 | 实际做法 |
|---|---|
| provider_defaults | 保留原提供商/适配器超时与重试行为；历史配置缺少新字段时按此读取 |
| single_attempt | SDK timeout使用既有call_timeout_seconds，max_retries=0；外层仍受调用和任务截止期限约束，不另建一套超时配置 |
| 配置冻结 | transport_policy存现有MySQL调用设置，正式页面可选；提交将策略修订与有效SDK选项冻结到PG任务依据，后续当前配置不能替换在途策略 |
| 公共Runtime与兼容 | 通过可选transport_options传入既有工厂/适配器；ModelInvocationRequest和模型消息格式不变，原请求hash及历史回执保持可读 |
| 能力边界 | Chat Completions原生/LangChain有实际文件与HTTP验收；Responses/Anthropic本轮核对既有构造参数传递，未做对应真实HTTP全流程；Google缺少已查证的同等重试控制，单次策略在提交冻结前明确拒绝 |
| 失败与恢复 | 新单次策略无HTTP状态的传输失败保留unknown；实际HTTP500保留provider_failure；已存回执与unknown不自动重发、不重新分类历史 |

一次逻辑调用可能主动补读，父调用及新固定请求分别保存。关闭SDK重试仅约束本系统可观测的SDK发送层，不证明上游网关内部没有重试。实际默认模型本轮为5逻辑/5物理，发现阶段73.063秒返回合法三规则及八证据声明；用例阶段仍有一条引用错误和两条unknown，不能宣布业务闭环成功。

下一步依据实际失败正文查证用例元数据与content_json的引用边界、当前用例阶段的补读范围和后续输入规模。原响应将content_json.title作为依据，但该字段不存在，标题实际位于case元数据；不代填路径、不放宽核验。另一用例请求读取其他两条用例，形成109708字节的独立续读后超时，不能归因为原请求重复拼接或SDK重试。是否需要字段引用锚点、阶段说明或可追溯修正轮次，要先查现有协议与真实样本后设计；不改写原失败或原unknown。

### 3.5.16 完整用例字段引用及阶段缺口〔已接，默认模型业务结果另列〕

依据来源 → 采用做法见 [P4_CASE_FIELD_REFERENCES_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CASE_FIELD_REFERENCES_DESIGN.md)。真实v6响应把case元数据标题写成不存在的content_json.title；沿用原文T引用的职责，新增实际已读完整用例的F引用。模型选引用编号，系统绑定固定case/version/hash/root/字面路径/实际值，继续走原事实核验，不代修错误路径或改写旧结果。

| 新任务契约 | 实际行为 |
|---|---|
| impact-call-v7 / impact-response-v5 | 仍使用coordinator-v2固定计划和canonical-v2；新增validator-v3，API/前端类型及PG按冻结协议读取原核验版本同步 |
| 完整字段 | 索引覆盖content_json与execution_notes全部节点，包括非空容器、任意键和完整数组；false/0/null/空字符串/空容器保留。不存在的title不会生成引用，JSON中真实存在的title等任意字段照常可用 |
| 实际读取 | 只给完整已交付版本登记引用，目录只保存case_ref/root/path，不重复字段值；服务器重算完整版本hash和目录，再绑定实际值。外用例编号、未知编号、伪造目录/正文继续拒绝 |
| 补读与预算 | 同一版本重复读取复用编号，后续完整版本只追加；完整JSON不缩成摘要，索引也进入既有输入预算，不截断。当前用例逐R判断，其他用例仍可补读作依据，各自任务负责其正式判断 |
| 阶段与状态 | unresolved仅表示阻止当前阶段判断的具体缺失证据，并指出受影响规则/成员；确实缺失仍按evidence_insufficient/uncertain保留。部署unknown、评审pending_review及后续任务等说明保留在summary/reason/候选uncertainties，不自动清洗模型字符串 |
| 历史与存储 | 原v3/v4/v5/v6及首轮v7的请求、Schema/指引/hash和核验原样恢复；描述澄清由新提交冻结新的协议hash。沿用MySQL设置、PG业务检查点、RustFS对象，无新依赖/表/复制配置 |

首次v7真实默认任务4逻辑/4物理，11项均analyzed，三规则及九用例判断符合固定样本；但模型将部署/评审等说明写入unresolved，严格验收仍失败，原结果保留。新增字段描述与阶段指引后再建新任务验证，不直接删除这些说明；全部真实文件/进程/App与最终业务结论见6.12。

字段存在、模型声明已分析、样本判断吻合、用户评审、环境已生效和实际测试通过分别验收。完整P0—P5、需求身份/评审/矩阵/增量、准备状态/未知副作用、正式生成导入、人工录制/Docker/报告及生产迁移继续保留。

### 3.6 需求评审、增量草稿与环境适用性

保存项目/文档下稳定需求身份、不可变需求版本及验证点。章节标题、块ID和哈希不是跨版本业务身份；AI提出匹配候选，人工可接受/拒绝/重新关联，所有决定追加记录原文依据和固定输入。删除只提出停用候选，保留历史。

确认后：新增需求生成新用例草稿；修改需求追加原case_id的新版本草稿；只生成确认范围，记录差异/需求/来源、模型/Prompt/Skill/模板。失败模块独立恢复，不覆盖旧启用版本。停用候选独立评审，新增草稿不自动启用。

| 影响和实际生效证据 | 行为 |
|---|---|
| 无影响/轻微影响 | 保留说明，继续适用当前用例 |
| 重大影响，已确认在指定环境生效 | 记录该用例版本在该环境的失效/复核状态，更新草稿并处理相关执行入口 |
| 尚未实现/未生效 | 旧版本继续用于当前环境；新草稿关联目标需求和目标环境 |
| 证据不足/无法确定 | 待评审/待复核，不全面阻断项目 |

适用性不能只在列表展示：选择、最终冻结和新运行启动核对同一确定的版本/环境事实。运行中收到变化时保留原冻结依据，按明确失效事实记录后续处理与计划修订，不悄悄换版本。AI的重大影响建议本身不证明环境已生效。

### 3.6.1 已接正式需求评审与不可变版本

依据及实施前研究见 [P4_REQUIREMENT_REVIEW_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_REVIEW_DESIGN.md)。[需求服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/requirement_service.py)复用影响快照完整核验，只接受其中明确rule_id；服务读取原rule、所有已核验用例判断及原响应检查点，客户端不上传候选正文替换系统依据。

明确请求接受、拒绝或暂缓。接受提供分类、正式正文/标题、验证点及具体用例版本关联；选择已有需求必须给出父版本。新需求UUID不从标题/章节/hash推导；验证点UUID依据该需求内明确key保留，包括移除后再次确认。拒绝/暂缓只追加评审事件，不自动撤销已有正式版本。

[PG存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_requirement_store.py)使用文档表派生的requirements/requirement_versions/requirement_reviews三表；身份指针可更新，需求版本和评审正文只追加。决定、版本和明确关联同事务，锁内核对项目活动状态、原快照、最新评审、父版本及固定用例项目/hash。项目内原请求参数变化拒绝，真实提交丢回执可按原请求找回，重放不读取原文/模型/可编辑配置；归档后历史可读和原请求可恢复，新评审拒绝。

[正式API](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/requirements.py)提供文档下固定影响快照评审、原请求查询、分页评审/需求、需求版本及指定版本矩阵；生产生命周期初始化和前端DTO/客户端同步。评审保留partial、依据限制和deployment unknown，不修改原AI结果、用例启用指针或历史用例正文；删除仅追加removed需求版本。

矩阵由明确评审关联产生设计覆盖，不由来源数量或AI影响等级推导。更新需求不复制旧覆盖；旧验证点关系在新版本显示needs_review，未关联点显示uncovered。指定旧版本仍返回旧正文/验证点/关系，但身份的current_version_id如实指向当前版本。执行证据尚未关联，execution_status明确not_assessed；正式评审/矩阵页面本轮已接，见3.6.2/6.14；该批次尚未实现的增量草稿、环境适用和运行证据随后按3.6.5—3.6.13及5.2.3分步接入，最新范围见6.41，完整P4仍未完成。

### 3.6.2 已接需求评审页面与后续变更闭环

本节的评审、设计关联、恢复、并发和历史矩阵页面已实现；后续增量草稿及运行/环境各小步进度以3.6.5—3.6.13和5.2.3/6.41为准，完整闭环仍未完成。实施前研究见 [P4_REQUIREMENT_PRODUCT_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_PRODUCT_DESIGN.md)，真实验收见6.14。依据是现有[影响结果组件](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/DocumentImpactSnapshot.vue)、[影响提交与恢复组件](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/DocumentImpactPanel.vue)、[需求评审API](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/requirements.py)和[需求服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/requirement_service.py)。沿用文档详情和固定影响结果入口，不另建一套文档身份或任务系统。

| 交付项 | 具体做法 | 验收依据 |
|---|---|---|
| 候选评审 | 在指定影响快照下选择规则，展示原文引用、用例判断和依据限制；提供接受/拒绝/暂缓。接受时明确新建或关联已有需求、变化分类、正文和验证点 | 保存的评审引用原快照和rule_id，不能由客户端替换AI原始依据；拒绝/暂缓不创建需求版本 |
| 明确设计关联 | 从该快照固定用例版本中选择关联，并为每条关联选择验证点；AI影响等级作建议，不能自动等同覆盖 | 未选择关联显示未覆盖；新需求版本不自动继承旧覆盖，旧关联保留待复核 |
| 原请求恢复 | 提交前冻结请求和参数，沿用现有会话存储恢复方式；回执丢失先查询原request_id，只能按原参数重试 | 实际服务端提交后中断响应、刷新页面，恢复同一评审/版本，不新增模型发送或重复记录 |
| 并发评审 | 已有需求提交预期父版本；规则再评审提交最新expected_review_id。现有评审列表按时间升序分页，不能把第一页当最新决定 | 读取历史不完整则提示未取得最新依据；冲突后重新读取并显式确认，不自动改父版本重提 |
| 历史矩阵 | 可选择需求版本，显示正文、验证点、具体用例版本、设计覆盖/未覆盖/待复核；执行未关联和部署未知如实显示 | V1正文与关系不随V2变化；项目/文档/快照切换后的晚响应不覆盖当前页面；归档后历史可读，新评审禁用 |
| 确认范围增量草稿 | 评审确认后固定需求版本、差异、完整相关用例和当时来源，复用生成请求构造及结果Schema，不调用会重读当前资料的旧生成入口；新增生成新case草稿，修改追加原case的新版本，删除提出停用候选 | 冻结基础已实现，见3.6.3；持久模块任务与原子草稿提交待接。记录实际模型/Prompt/Skill/模板版本及未处理范围，保留旧启用版本和原失败 |
| 运行证据与环境适用性 | 在设计关联上接具体RunItem/attempt/工具证据及明确环境事实；与选择、冻结、启动运行的核对保持一致 | 关联数不当作通过数，旧需求通过不能证明新版本通过；没有生效证据不全面阻断项目 |

页面已经补上既有API的产品入口：[评审面板](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/RequirementReviewPanel.vue)、[恢复控制](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/useRequirementReview.ts)、[矩阵组件](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/RequirementMatrix.vue)。刷新目录不改变表单捕获的父版本，冲突后的显式重新评审才读取新编辑依据；成功提交后重新初始化表单。随后实施增量草稿和运行/环境闭环。后两项的新增任务、表和接口要先核对现有生成/运行调用链再定契约，本方案不把尚未实现的接口写成已可调用。页面验收使用真实DOCX、独立API/Worker、MySQL/PG/RustFS及实际浏览器；组件测试用于晚响应、分页、冻结请求和冲突等边界，不能替代完整产品验证。

### 3.6.3 确认范围与冻结生成基础、待接持久任务

依据及研究见 [P4_INCREMENTAL_GENERATION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_INCREMENTAL_GENERATION_DESIGN.md)。现有生成器每次读取当前Skill，资料collector读取有截断的当前文档预览，case_service.generate只批量新建。因此不能直接把旧generate入口用于确认需求的增量更新或恢复。本阶段抽出共用请求构造，保留旧生成行为；增加冻结请求及原确认范围读取，尚未接增量任务/API/Worker/页面或提交草稿。

已经实现的输入链路是：

```text
指定正式需求ID和版本ID
→ 读取该版本原接受决定，核对请求hash、产出版本及固定影响来源
→ 从原影响输入读取完整旧/新解析、差异、固定用例JSON与当时来源
→ 捕获一次MySQL模板、完整Skill、Prompt版本及非敏感模型/SDK参数
→ 每个明确旧用例基线形成一个更新模块；明确新覆盖形成新建模块
→ 保存可核验的完整范围/生成请求；取得完整响应后再解析候选
```

[范围读取](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/incremental_generation_sources.py)不使用current_version_id替换指定需求版本，不调用项目资料预览collector。更新基线必须属于原影响固定用例范围且归属当前项目；记录原case_id/case_key/mode和提交时最新版本号，后续提交事务据此检查并发。一个case只能明确选择一个基线；没有目标用例或新覆盖范围不能提交。此处模式是既有治理元数据，不把JSON转换为模式工具参数。

[冻结Schema](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/case_generation.py)保存完整范围及每个模块的请求/hash；生成模板用于编写，完整Skill正文与hash、Prompt版本及实际模型参数由既有能力捕获。一个范围内所有生成模块复用同次快照。保存后的请求不因模板/Skill/当前文档/后续需求版本变化而重建；实际调用核对原模型与runtime参数，变化明确拒绝，不能悄悄改用新配置。原input仍由既有PG检查点/RustFS SHA/大小核验；来源缺口、partial和deployment unknown继续保留。

[生成器](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/generation_pipeline.py)新增显式冻结调用，返回完整原响应；未来任务必须先存调用意图，收到响应先保存原件，再解析。冻结解析拒绝供应商失败、工具操作、空/无效输出、截断及超出固定范围，不截取额外候选。更新只接受一条同case_key候选；完整JSON和未知字段作为模型输入保留，内容修改是否正确仍需业务评审。移除需求仅形成retirement_candidate，不读取生成模板/模型/Skill、不调用生成、不自动停用原case。

剩余产品链路按现有规划/影响任务和导入事务查证后实施：

1. 原请求幂等提交、范围冻结、PG模块/任务/Outbox及RustFS请求对象；可编辑任务策略在MySQL，提交时冻结实际修订。
2. 独立Worker原子领取模块，保存调用意图/响应检查点和未知发送；已存响应恢复只读，不因为重启或新租约重复发送。完整请求还需校验模型容量、字节与时间预算，不能把本轮受控HTTP成功当作任意文档都能装入模型。
3. 校验候选后，同一PG事务追加原case的新版本草稿或新case草稿，并保存模块提交收据。核对预期最新版本；冲突保留原候选供明确处理，不自动重定基线。不能循环create_version拼成假事务或依靠API进程内存防重。
4. 模块独立状态/失败/未处理范围、原请求恢复、原响应及来源可读；失败恢复沿用原依据，新的依据另建任务。停用候选单独存业务记录并评审；生成/需求接受不自动启用、停用或重写原结果。
5. 正式需求页面接提交/恢复/候选/模块报告，再进入既有草稿评审与固定版本治理。验证点关联由明确设计确认产生，不能把生成数量当作覆盖或执行通过。

本阶段真实MD/DOCX/PDF、数据库、RustFS及HTTP验收见6.15。当前没有增量公开路由或生产任务检查点，RustFS保存/重读范围及响应是基础测试，不冒充生产自动恢复。没有新增依赖、配置键或SQL表/列，原模板继续MySQL，原需求/用例/影响业务继续PG，完整对象继续RustFS；完整P0—P5仍未完成。

以上为`06471590`已提交基础阶段的状态。后续工作区已增加任务策略、表与检查点，最新情况见3.6.4；不能继续把“没有配置键/表”当作整个当前工作区的结论。

### 3.6.4 增量模块任务与草稿事务〔内部专项已验，正式产品待接〕

本节保留80dc609b内部模块阶段状态：增量组、独立模块任务、原调用意图/响应检查点及同事务草稿收据已通过15项实际模块场景和受影响回归。当时正式API、独立生产生成Worker入口及页面尚未接通；后续正式入口和独立进程验证见3.6.5，正式页面和完整App验证见3.6.6；这里的待接描述仅指该历史阶段。

| 当前实现或后续做法 | 依据与状态 |
|---|---|
| 提交一次固定范围，每个更新/新建/停用候选形成独立模块 | 工作区`generation_task_service.py`和`postgres_generation_task_store.py`复用已有文档任务租约/Outbox；组和全部模块派发记录同PG事务，范围正文在RustFS |
| 固定任务策略 | 新MySQL键`test_case.incremental_generation_settings`，提交时冻结修订；新增PG组/任务表记录业务状态，原模板仍只参与生成 |
| 模块执行与恢复 | 原子领取后保存调用意图，收到原响应先存RustFS和PG检查点再解析；已存响应恢复只读，只有意图而响应未知时保留未知、不自动再发 |
| 提交增量草稿 | 更新追加原case的新版本草稿，新覆盖创建新case草稿；草稿、收据及模块终态同事务，检查项目/预期最新版本和租约；旧启用版本保留 |
| 移除需求 | 形成停用候选收据，不调用生成模型，不自动停用用例或删除历史 |
| 事务预算 | 发送前、草稿事务开始及用例锁等待后检查原总期限；事务最后同时核验租约，失效整笔回滚，原响应保留；失败终态不重领 |
| 下一步交付 | 研究失败模块按原固定依据创建新任务的契约，接正式API、独立生产Worker/CLI、前端契约与页面，再做完整产品和真实业务评审 |

首轮6通过、扩大后8通过/1失败仍保留，见6.16。参数化测试SQL后，进一步通过真实行锁等待复现“期限已到仍提交草稿”的实现缺口，并补原期限事务fence；最终14项模块功能通过，新增关闭派发1项通过，49项共享回归和1项既有生产兼容通过，见6.17。原响应、失败日志、旧版本和原失败结果继续保留。模块独立失败已有实测，显式重试入口未实现；不能重置终态或重读当前依据冒充原任务恢复。完整P0—P5范围不缩减。

### 3.6.5 增量生成正式API与独立Worker〔51541987入口阶段，页面验证见3.6.6〕

依据见 [P4_INCREMENTAL_ENTRY_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_INCREMENTAL_ENTRY_DESIGN.md)。读取现有文档路由/统一错误、生产启动、Worker生命周期、容器组件与前端固定请求后，接入现有持久模块服务；不另建任务平台，不改旧范围/组/模块hash或终态。

| 接入内容 | 具体行为与依据 |
|---|---|
| 正式API | [生成路由](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/case_generation_tasks.py)提供项目策略GET/PUT、文档级提交、按原creation_key查询、分页组目录、固定组/模块状态、完整范围和原响应；复用UUID/项目文档隔离、Bearer中间件及404/409/503错误处理 |
| 提交与结果 | 202只表示组/模块/Outbox已经接受；completed只表示草稿或停用候选已保存。客户端明确需求版本、目标用例版本或新建范围；不能上传seed/租约/完成收据。回执未知先查询原请求，改参数的同编号请求409 |
| 生产装配 | [共用装配](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/generation_runtime.py)在API/Worker使用实际项目、用例、模板、生成器、需求及影响来源；正式启动初始化MySQL策略与PG两张表。配置在MySQL，业务元数据在PG，完整范围和原响应在RustFS |
| 独立Worker | [生成Worker生命周期](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/generation_worker_runtime.py)复用现有资源回收/Redis/Outbox和Handler；[CLI](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/cli/run_case_generation_worker.py)为`python -m src.cli.run_case_generation_worker --once`，登记`qa:tasks:case_generation`并运行现有relay。部署模板已加登记项，实际用户.env不改；未登记时启动拒绝 |
| 前端契约 | [类型](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/services/caseGeneration.types.ts)和[客户端](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/services/caseGeneration.api.ts)同步九个操作与版本收据、原scope/response；接口测试和vue-tsc/Vite通过。本入口阶段尚未接页面；后续完整App验证见3.6.6，客户端测试与浏览器验证分开记录 |
| 历史读取 | 关闭派发后保留原提交/按原请求查询、组报告和范围；新提交503且不捕获新依据。归档项目保留历史、拒绝新提交和策略写入。保存的scope/response核验SHA与大小，不读当前配置替换原依据 |

真实MD/DOCX/PDF正式API→Outbox→独立解析/比较/影响/生成CLI→完整响应→原case新版本草稿/新case草稿已验证；每场景实际HTTP为原影响2次+生成2次，终态重投没有新发送。原V1保留，新增草稿V3使用明确V1基线，当前启用指针不改变。提交后把MySQL容量改为1字节仍按原固定策略执行；关闭派发重启API后读取原组/范围成功。删除需求另由真实公共路由验证只形成候选，不调用生成或停用原case。

本入口阶段的下一步已由3.6.6完成正式增量页面及完整App验证。后续失败显式新任务重试仍要保留原范围/父失败和未知发送事实，不能重置终态。默认生成模型的业务质量、用户评审、运行/环境和完整P0—P5仍未完成。

### 3.6.6 正式增量页面与原请求恢复〔真实DOCX、完整App及独立Worker已验〕

依据见 [P4_INCREMENTAL_PRODUCT_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_INCREMENTAL_PRODUCT_DESIGN.md)。读取实际生成Schema/客户端、固定需求评审、用例版本列表、模式注册表和旧正式产品流程后接页面。这里没有新后端生产契约、依赖、数据库表或配置键；仍使用现有MySQL策略、PG业务记录及RustFS原范围/响应。

| 页面行为 | 实现及验收边界 |
|---|---|
| 明确生成依据 | 在原固定影响快照的需求评审页显式打开“需求增量生成”；选择该快照中已接受的具体需求版本，不用当前需求或最新用例替换。关闭面板不发起新生成读取 |
| 固定目标与完整JSON | 目标来自原快照的用例版本，可读取完整JSON和自身说明；更新不按模式过滤，新用例模式来自现有注册表，仅作治理元数据。生成模板仍只参与生成 |
| 提交恢复 | 发送前保存原编号和全部参数；丢回执先查询原请求、读取原范围并核对规范化参数/项目/文档/模块后确认。同编号改变参数不能重试；本地缓存只是恢复线索，PG仍是业务依据。明确4xx拒绝后由用户核对再新提交；本地校验未通过不产生“提交未知” |
| 结果和历史 | 展示逐模块状态、调用响应未知、错误、完整原范围/请求、原响应及下载、具体草稿版本和自身说明。切换组期间禁用下载，晚到的响应/草稿不能覆盖另一个组。文档历史包括原正常、失败和停用候选组 |
| 设置与终态 | 页面读写既有MySQL项目策略，新提交冻结修订；当前设置改变不能覆盖原组。原完成/失败模块没有重置或自动再发；原请求恢复与“失败模块创建新任务”是不同操作 |
| 保留旧启用版本 | 生成新版本草稿或新case草稿，不自动启用；移除需求仅形成明确目标的停用候选，不调用生成模型、不停用case、不删除历史 |
| 关闭派发/归档 | 原请求、范围、原响应、收据及历史仍可读；新提交和策略写入按现有服务限制。关闭派发不抹掉已经接受的任务 |

正式浏览器使用真实DOCX、生产API、独立解析/比较/影响/生成CLI、MySQL/PG/Redis/RustFS和构建后的App。实际丢失提交回执后刷新恢复原组；新需求、草稿和当前策略变化不改原范围；终态独立CLI重投没有新的物理发送。完整用例保留12步、False/0/null和扩展字段；V1继续启用，V3及新case保持草稿。实际物理请求逐条与冻结请求核对，详见6.19。

后续须研究失败模块显式新任务的父任务/固定范围关联、unknown限制及任务池投影；生成草稿经过评审后与需求验证点的正式关系也要另行核对。当前评审关系校验以原影响快照中的固定用例为依据，新case不是原快照成员，不能把生成收据直接算成矩阵覆盖。继续接运行结果/环境适用性，验证默认生成模型质量与用户业务评审，完成其余P0—P5及生产历史迁移。

### 3.6.7 失败模块显式新任务〔正式API与独立Worker已验，页面见3.6.8〕

研究见 [P4_GENERATION_RETRY_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_GENERATION_RETRY_DESIGN.md)。现有终态不重领、原调用/响应和同事务草稿作为依据；新增PG独立重试关系，复用原任务、Outbox与Handler。原group/scope/module字节与hash均不改，不给旧组添加默认字段后重新计算历史hash。

| 情况 | 实际行为 |
|---|---|
| 明确失败且尚未保存调用意图 | 创建独立子任务，执行原固定请求；新任务策略从MySQL捕获，文档/需求/模板/模型/Prompt/Skill及用例基线保持原样 |
| 已保存原响应的失败 | 子任务引用同一个响应对象，重新校验/解析并提交草稿，不再次调用模型；原响应被替换或原件SHA不符时拒绝 |
| 调用意图已存、响应结果未知 | 拒绝用重试新任务绕过unknown；原在途取消、意图和失败保留，不凭失败状态推断没有发送 |
| 原父版本冲突或当前模型参数变化 | 保留原预期父版本，冲突继续呈现；没有响应时当前模型/SDK不同在发送前拒绝。需要更换依据时明确创建新生成范围 |
| 已完成、排队或运行中 | 不作为失败重试来源；已有结果收据的异常失败也不能再写草稿 |
| 请求重放/回执丢失 | 同编号绑定原父任务；retry/新task/Outbox同PG事务。回执未知先GET原编号；重放返回原关系和当前子任务状态，不重新捕获配置 |
| 终态与历史 | 原组继续显示原任务及原失败，重试另列。子任务使用独立租约与总期限；完成/失败仍不可重领。策略只影响新子任务，不改旧任务 |
| 停用候选 | 期限失败可创建独立新任务，仍只保存候选，不调用生成、不自动停用case |

公开新增POST `/projects/{project_id}/documents/{document_id}/case-generation-tasks/{task_id}/retries`，仅接受creation_key；GET同路径分页历史及`/by-request`恢复。返回原失败关系、固定组/范围/模块hash、新策略、执行方式与子任务。前端类型/客户端同步三个操作；正式重试按钮与恢复组件已按3.6.8接入并实际验证，客户端验证与页面验收分别记录。

新PG表为既有文档前缀加`_case_gen_retries`，由现有API/Worker启动初始化；新任务seed追加可选retry引用，旧seed兼容读取，原组/范围schema及其hash不变。配置仍用现有MySQL键，原对象仍RustFS，无新依赖/配置键或实际.env变更。归档/关闭派发后原重试可读可重放，新请求按现有机制拒绝。真实文件/API/独立CLI及故障验证见6.20；生产历史迁移未执行。

正式重试页面、原请求恢复、完整下载与归档/关闭派发验收见3.6.8及6.22；继续任务池投影、草稿评审后的追踪关系与运行/环境，以及其他P0—P5。

### 3.6.8 正式模块重试页面与历史恢复〔真实App/独立Worker已验〕

先研究当前失败重试契约、原增量页面的缓存/晚响应与实际错误历史下载样本、本地Agent轨迹课程，再实现独立面板；研究见 [P4_GENERATION_RETRY_PRODUCT_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_GENERATION_RETRY_PRODUCT_DESIGN.md)。复用已有API/设置，不新增数据库、配置键、依赖或执行平台。原失败继续在原组呈现，子任务结果单独读取。

| 操作或情况 | 实际页面行为 |
|---|---|
| 为失败模块创建新任务 | 仅提交creation_key；父task从固定模块选择，明确展示执行原请求或复用原响应；原group/scope/module保持 |
| 未知/404/错误身份 | 保留原父任务和编号；刷新先GET恢复，不自动POST。重放原提交使用同编号，明确拒绝后才允许结束编号 |
| 历史与结果 | 分页读取独立重试关系；按原编号刷新，核对父任务/固定范围/module/hash/原响应及子收据；不可变关系不能被替换 |
| 完整用例/原响应/下载 | 按子收据读取明确case/version/hash，保留完整JSON/自身说明；原scope、retry/task/receipt、草稿与原响应分别下载 |
| 切换组/项目/销毁 | 请求绑定原上下文，晚返回不替换新面板；读取或父组切换期间禁用操作/下载。缓存按项目/文档/父task隔离 |
| 失败子任务 | 用户显式展开该任务的独立重试面板；展开状态与它自己的原请求分别保存，恢复只回查、不自动递归提交 |
| unknown/有收据/非失败 | 与后端契约一致，不能创建重新发送任务；前端展示不替代后端权威判断 |
| 归档/关闭派发 | 新任务不可提交；原历史、原编号回查/重放与完整下载仍可读取 |

当前15项新组件从真实保存失败/unknown/响应/草稿样本读取；与旧增量/需求/影响/客户端共72项通过。真实DOCX、built App、生产API/独立CLI及MySQL/PG/Redis/RustFS完成两类重试、原POST丢回执后的刷新恢复、完整下载和重启归档回查。总6次物理调用：影响2、原正常生成2、执行原固定请求1、响应预算失败的原发送1；响应复用没有第7次发送。原失败、终态重投、固定完整消息与对象SHA均核对，详细见6.22。默认生成模型质量、用户业务评审、草稿正式追踪、任务池投影及运行/环境闭环仍待完成。

### 3.6.9 草稿评审后的独立覆盖确认API与矩阵

实施前研究见[P4_REQUIREMENT_COVERAGE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_COVERAGE_DESIGN.md)。原需求评审的case_links仅允许原影响seed内版本；新生成的更新、新建及重试草稿不在原范围。保留该校验和不可变需求正文/hash，新增独立覆盖确认记录。生成元数据描述来源，不能自动计为覆盖。

确认绑定正式project/document/requirement/version/hash，提交完整case_links集合及note/request_id/expected_coverage_id；每项明确case_version_id/content_hash/point_keys，可选generation_task_id。共享原用例canonical算法核对完整JSON、说明、来源与生成字段，核对SQL身份及哈希；点必须属于所选需求版本，不能跨项目。允许明确关联本项目人工/导入版本，不按测试模式过滤，不增加必须启用的门禁。

指定生成来源时校验真实completed任务、原seed/group/retry、result_saved收据及case/version/content_hash、scope/module/response的一致性。完整provenance另存；其中原需求及point_ids只是编写来源，不证明每点覆盖。重试复用原生产校验，不复用原失败身份。确认不调用模型、不重建范围、不改写原任务或原件。

新PG表为现有文档前缀加_requirement_coverage，覆盖记录按版本追加修订；空集合撤销关联，旧记录仍可查。项目事务锁内先查原请求，再检查active状态和预期上次确认。未知回执先按原编号恢复，同编号改变参数409；回查/重放不访问当前case/配置/对象/模型。没有新增MySQL配置键或依赖，业务元数据仅PG，原大对象沿用RustFS。

公开POST requirements/{requirement_id}/versions/{version_id}/coverage-reviews；GET同路径分页历史，requirement-coverage-reviews/by-request恢复。matrix默认所选需求版本最新确认，coverage_id=initial读原始关联，明确UUID读固定历史。每个关联冻结真实评审及启用状态，缺失历史评审标unavailable，不伪造approved。旧版本关联以所选时间截断，之后对旧版本的确认不回写已选择的历史；后续需求提示needs_review，旧通过或旧关联不变成新需求覆盖。

API、Schema、前端DTO/客户端和旧矩阵消费者已同步。真实MD/DOCX/PDF、PG/MySQL/RustFS、受控HTTP及生产API/独立CLI验证见6.24。此前客户端和旧矩阵已兼容新结果；正式覆盖管理页面及真实DOCX产品验收另见3.6.10/6.26，不把此前客户端通过当作页面证据。执行证据、部署适用、生产迁移和用户业务评审继续待验。

### 3.6.10 覆盖确认正式页面与固定历史〔真实DOCX App已验〕

依据现有[矩阵组件](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/RequirementMatrix.vue)、[覆盖确认API](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/requirements.py)与[已验证存储设计](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_COVERAGE_DESIGN.md)，在现有需求评审/矩阵入口接入覆盖管理，不修改原需求评审的固定输入或历史正文。

| 页面步骤 | 实施方式与必须保留的行为 |
|---|---|
| 选择固定需求 | 明确需求身份、版本及验证点；历史版本可以查看。编辑绑定所选版本，不随当前版本变化替换依据 |
| 选择项目内用例 | 复用当前项目目录与完整版本读取，明确选择case_id、case_version_id及content_hash；展示完整JSON、自身说明和真实评审/启用状态，不用前六步摘要作确认依据 |
| 确认覆盖点 | 明确将用例版本映射到验证点，提交完整关联集合及说明。允许普通手工/导入用例；可选生成task引用由后端核对真实收据。生成元数据不自动等于覆盖，覆盖确认不自动启用用例 |
| 保存及冲突 | 使用现有request_id和expected_coverage_id契约追加确认；删除关联形成新记录。冲突显示原请求及当前确认，不能静默覆盖或把请求改成新编号重发 |
| 未知回执与恢复 | 保留原参数及编号，重新打开页面先按原编号GET回查；未知结果不自动POST。项目/需求版本切换隔离待恢复请求，迟到响应不能覆盖另一个页面上下文 |
| 历史与矩阵 | 提供最新确认、initial原始关联及指定历史确认；刷新明确的需求版本/确认。保留未覆盖及待复核点，设计覆盖、执行证据和环境生效分别显示 |

页面写权限沿用现有项目状态及接口规则；归档后的历史、完整关联版本读取和原请求恢复继续可用。矩阵显式打开独立覆盖管理，纯函数核对固定身份/原参数，独立控制器管理目录/完整版本、编辑、分页和原编号恢复；复用JSON树与用例评审对话框，不新增评审平台、API或配置键。对话框明确定位所选版本，后续新草稿不替换它；关联可保留草稿/待评审状态，不自动启用。实际结果见6.26。

本阶段用真实DOCX实际运行完整浏览器流程：上传两个版本 → 解析/比较/影响 → 需求评审 → 增量/重试草稿 → 按现有流程明确用例评审或启用 → 确认覆盖 → 读取矩阵。另核对丢回执、重载、API重启、项目/版本切换、冲突、撤销、指定历史及归档读取；用实际PG记录、原件/版本hash和物理HTTP轨迹证明没有重复生成或改写历史。受控模型产品验收与外部默认模型业务验收分别记录。

### 3.6.11 增量/重试任务池与固定详情〔真实DOCX App已验〕

接入case_generation公开列表与专属固定详情路由。列表只读PG业务表，分页同时覆盖旧类型和生成任务。重试关系由真实记录读取，原请求编号与子任务内部creation_key区分；结果种类来自已保存收据，未形成收据时保持空值，不猜测模块类型。

详情读取固定生成组、scope及任务；重试另读原父任务和by-request关系，核对完整身份/版本/模块。支持完整12步及未知字段JSON、自身说明、原响应和停用候选读取，复用原重试面板。项目归档后禁新写入而保留历史；失败显示具体错误，不替换为当前版本；活动任务刷新、迟到响应及卸载按上下文处理。

真实原件/独立任务进程/SQL关系/实际HTTP/完整App及重启归档验收见6.28。列表和导航没有增加模型发送，生成成功不计为测试通过。实施前研究见[P4_GENERATION_TASK_POOL_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_GENERATION_TASK_POOL_DESIGN.md)。

### 3.6.12 需求验证点关联实际执行〔API与矩阵已接，正式App见3.6.13〕

依据来源 → 采用做法见[P4_REQUIREMENT_EXECUTION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_EXECUTION_DESIGN.md)。调研时矩阵只有execution_status=not_assessed且未读取运行结果；现在[矩阵Schema](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/requirements.py)与[服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/requirement_service.py)已读取固定执行确认。设计覆盖、已关联正文检查、原运行总体状态和部署生效分别表达。

现有[Agent回执核验](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_case_assessment.py)已保存正文、准备和清理阶段的逐项核验，包括固定预期位置、实际工具输出位置、比较方法与判定。复用这些真实证据，新增独立执行关联；不让AI重新生成结果，不修改原需求、用例、覆盖确认或终态运行。

| 要解决的问题 | 已接做法与保留边界 |
|---|---|
| 哪个结果验证哪个点 | 明确选择需求版本、原始或指定覆盖确认，引用result_id、verification_id、check_index及point_keys；客户端不提交替代判定 |
| 如何检测证据是否对应 | 后端读取原run/item/attempt/result、完整固定用例版本和实际ToolJob；核对项目、版本、原结果hash、正文阶段、预期路径与工具实际值。确定性比较沿用现有比较算子；语义核验保留原模型、方法与理由 |
| 准备和清理混入结果 | 验证点关联正文phase=case；准备/清理及整条运行状态分别展示。整条结果失败不自动意味着正文每个验证点失败；准备通过不计作需求通过 |
| 部分核验是否代表完整通过 | execution_scope固定为selected_checks，passed/failed/partial仅汇总所选正文检查；未选检查仍不参与判定。未关联显示not_assessed；这组检查通过不能证明整个验证点或整条需求通过 |
| 历史、撤销和未知回执 | 执行关联在PG追加记录，保存原结果/版本hash和选中核验；空集合撤销，旧关联保留。原请求恢复、预期父记录冲突和归档读取复用覆盖确认的事务习惯 |
| 旧结果与新版本 | 没有结构化正文核验的旧结果仍可查，明确无法提供点级判定。旧需求或不同用例/覆盖版本的通过不迁移到新版本；失败回归新建运行并引用原失败 |
| 环境与部署 | 当前没有正式可信环境绑定，自由文本runtime_context不能证明部署生效。deployment保持unknown，环境适用性另行调研、实现和实测 |

公开接口已接入：需求版本下的POST/GET execution-reviews、GET execution-candidates，以及文档下的requirement-execution-reviews/by-request。候选仅分页读取当前项目、所选固定覆盖用例版本的实际结果及正文核验摘要。矩阵支持execution_id：省略取匹配固定覆盖依据的最新确认，initial明确不读取关联，UUID读取指定历史。确认修订按同一需求版本的完整历史递增，预期父记录取该版本全局最新确认；切换覆盖依据不能绕过并发冲突。

[固定关联契约](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/requirement_execution.py)、[原核验检查](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/requirement_execution_checks.py)及[PG存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_requirement_execution_store.py)现已接入RequirementService、公开路由和矩阵；前端DTO/API、身份核对与只读核验展示同步。保存前核对原结果payload/hash、固定用例预期及实际ToolJob，读取历史只依赖固定确认。实际大写UUID引用曾被字符串比较错误拒绝，已在输入契约统一UUID表示并实测恢复；不放宽证据匹配。

业务关联保存在PG新增的文档表前缀_requirement_execution表，随既有启动初始化；没有新增依赖、配置键或测试模式。可编辑配置仍MySQL，原件与既有大工件仍RustFS。API阶段的真实三格式、实际HTTP/ToolRuntime、独立生产进程和回归见6.30；随后正式页面及浏览器完整App见3.6.13/6.31。外部默认Agent自主执行、部署适用和生产历史迁移尚未验证。

### 3.6.13 正式正文执行关联页面与固定恢复〔真实DOCX完整App已验〕

依据 → 做法见[P4_REQUIREMENT_EXECUTION_PRODUCT_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_EXECUTION_PRODUCT_DESIGN.md)。在既有需求矩阵展开“管理正文执行核验”，与覆盖编辑互斥；复用既有API、原完整结果/用例读取和JSON树，不新增业务表、配置键、依赖或测试模式。

用户选定需求及覆盖版本，分页读取对应的实际结果，再读取原运行结果和完整固定用例/自身说明；明确选择正文检查与允许的验证点，保存完整集合。页面呈现原预期/实际/算子/方法、整条结果状态、回归源和原工具引用。清理失败可以与正文通过同时存在；所选检查通过不推导完整需求通过，部署仍unknown。

| 页面行为 | 已接约束 |
|---|---|
| 编辑与历史范围 | 编辑冻结打开时的需求及覆盖身份/hash；提交父确认取同需求版本全局最新历史。其他覆盖的记录仅标注，不能混入当前覆盖矩阵；历史查看不替换编辑集合 |
| 原请求与未知回执 | sessionStorage按项目/文档/需求/版本/覆盖保存原编号、参数及固定hash。重开先GET回查，不自动重发；404继续保留未知，显式原请求重试不改参数 |
| 撤销与冲突 | 空集合加说明明确撤销，旧确认保留。409不自动换父确认，显式重读后作新决定 |
| 归档与迟到响应 | 归档禁新确认，允许完整原结果/用例读取与原请求恢复；上下文代次过滤迟到响应，固定版本/hash错位可见 |

前端候选类型、确认核对与控制器/矩阵同步。21项新编辑组件、完整JSON/所选检查及原需求/覆盖/生成/重试/任务详情/API合计120项通过；真实DOCX、built App、独立API/解析/比较/影响CLI与实际存储验证见6.31。受控业务HTTP/ToolRuntime和回执证明执行证据与页面行为，尚不证明默认Agent自主执行或环境部署适用。

### 3.7 需求追踪矩阵

矩阵关联**具体需求版本/验证点 → 具体用例版本 → 运行结果/证据**，展示未覆盖、仅设计覆盖、待复核及实际验证。文档来源数量、关联用例数量和真实验证结果分别呈现；V1通过不能证明V2需求通过。

从既有source_refs回填只能产生候选，不伪造精确需求覆盖。历史用例/运行保持原关联依据，回归创建新运行并引用原失败；矩阵能下钻到原文、用例完整JSON和实际证据。

## 四、已有链路的实测与补强

### 4.1 人工 UI 录制和知识

先验收现有“人为浏览器操作 → 捕获操作及响应元素/DOM等 → 清洗 → 检查遗漏 → 入库 → AI查询/使用”。用户指定的 [DeepSeek 官网](https://www.deepseek.com/) 作为简单人工录制页面；只做低频UI操作。DeepSeek公开页实际点击、落库和质量投影已补验，见7.22，业务完整性仍unknown；受控HTML补验不替代完整公开页面业务验收。

核对客户端事件、服务端接收、数据库与固化结果，包含动作ID/顺序、动作前后响应关联、DOM稳定等待、结束排空、最后一步、截断和遗漏警告。去掉重复投递和干扰，不误删真实重复操作；保留脱敏流水、清洗版本与原因。动态DOM/iframe/连续点击等用受控页面补验。

录制后的场景管理、步骤编辑、业务别名及不可变历史/草稿转换已补实现，已验范围见7.14。现有knowledge-rag的固定场景查询/读取已接，见7.15；明确关联场景进入首次规划前的固定证据及恢复已接，见7.16。正式Worker启动函数也已接同一PG场景store并实际回读。按需检索未关联场景的规划契约、分页/正文及固定恢复已补并实测，见7.17；独立CLI真实进程kill、自然PG租约恢复与终态重投四组合已验，见7.18，受控localhost HTTP不代替默认AI自主选择质量及业务执行。录制前场景规划、selector修复评审与失败定位仍需实施和验收。编辑产生新版本；选择器修复不能抹去原失败。默认AI固定场景读取及简单正/负正式断言见7.37；复杂场景和完整App继续单独验收。

项目知识查询修复见7.09—7.10：无旧scope的正式项目按project_id隔离读取，补齐Recording/Action节点、计数和页面筛选/详情。真实CDP新录制、PG/现有图、完整构建App及重启已验；不代替人工DeepSeek、默认AI使用和完整清洗遗漏。录制衍生场景及整理已验范围见7.14，仍不能把知识页面可读计作默认Agent完整执行通过。

### 4.1.1 原Electron入口实测与结束协议缺口

原后端指令使用`action`，Electron却读取`kind/command`，原HTTP状态active与页面enabled=false已实际复现；原消费处现已对齐。二秒定时器也被二十条门槛挡住，实际两个click/一个page_scan六秒未入库，现已修正原定时flush。正常窗口/API/PG/知识投影、暂停/继续、重复投递与真实重复操作、关闭及RustFS截图读回通过。

此前实际输入后立即stop：页面输入存在、停止前seq=6，PG只有5条且没有对应fill，却显示completed和正常对账，原失败保留在6.71。现在接入共享finish_capture契约：先冲刷已接受输入/异步binding，再由Electron确认完整批次，原队列FIFO结束标记排空，PG核对全部发布seq及浏览器本页生成范围。真正尾部输入、部分批次和受控503结束超时已验，见6.72。

新配置recording.finish_settings沿用MySQL并在创建时冻结到PG metadata。失败/丢弃/未确认水位不能固化成功；取消HTTP等待不取消后台stop。研究见[P0_RECORDING_DRAIN_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_DRAIN_DESIGN.md)。

本批实际复现立即pause丢fill和bridge移除后终态重投更新updated_at。暂停复用浏览器冲刷/完整批次回执，以operation=pause及FIFO检查点确认原消费循环已提交PG，再保存paused；队列保持开放供resume。并发控制拒绝，取消HTTP等待保留后台pause，DB失败/超时明确failed。stop保留追加的pause_receipts。事件追加在同一PG事务FOR UPDATE锁会话行，缺失404、completed/failed/discarded拒绝409，避免路由先查状态再写的竞态；原终态不更新。真实Electron、CDP、PG锁竞争及完整快照已验，见6.73及[P0_RECORDING_BOUNDARY_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_BOUNDARY_DESIGN.md)。

6.73当时iframe/多top缺少分范围确认；当前接入见4.1.2—4.1.3。更多延迟/断网/进程恢复组合仍待验，不能把top回执当作全录制完成。动态响应、完整知识、AI实际使用、完整App和人工DeepSeek仍未完成。

### 4.1.2 文档、frame与弹窗身份〔Python原两驱动已实测〕

真实CDP四失败证明：整页导航后新脚本enabled=false，已有iframe没有注入，弹窗初始化关闭。JS模拟测试手动开启子frame，不能证明生产控制可用。原v1通过来源sessionStorage/top汇聚seq，不能保证跨来源/多窗口身份。

Python两驱动使用recording-scopes-v2：原生binding提供page/frame，宿主登记文档及父frame路径；每文档局部seq与宿主分配的会话seq分别保存，重投不重新分配。当前root全部frame立即注入，之后的document通过init_script登记并取得当前采集状态。原生opener链只纳入所选root的弹窗，不采集同context无关标签。暂停新frame保持关闭，resume再开启。

暂停/结束核对每个当前frame及已离开document。实际pagehide丢失离开消息已复现，加入更早beforeunload水位；只有该文档完整局部范围已收齐时才确认。最终PG同时核对全局seq、文档局部seq及原生page/frame/父路径，遗漏/重复文档和身份篡改拒绝。没有新增表、依赖或可编辑策略；超时仍由MySQL冻结，PG既有page/finalize_metrics保存身份与回执，RustFS原截图。

实际CDP和真实headed managed各五场景经原API/PG/图投影/项目知识读取通过，包括嵌套frame最后输入、跨来源、弹窗、暂停后新frame及无关标签隔离。完整事件对原通道、截图对RustFS字节已验；不是AI实际使用、完整Vue App或人工DeepSeek。6.74中的Electron五场景只验证v1兼容；当前Electron原生范围接入及实测见4.1.3/6.75。关闭、移除frame、普通回返和Python实际缓存恢复的后续结果见4.1.4/6.76；同context重复/并行attach、更多断网/恢复继续待验。

### 4.1.3 Electron原生context、OOPIF与弹窗〔十二实际场景已验〕

先以原Electron/API/真实存储复现四失败：导航后和弹窗操作未入库，跨站嵌套frame结束未确认。CDP binding不是Playwright的Promise接口；新增独立recording-scopes.mjs协调native context与回复桥，窗口继续复用原HTTP攒批、命令、截图和结束接口。不是增加另一套录制服务。

按实际executionContextId/sessionId及default world登记文档；Target.setAutoAttach接跨站OOPIF，did-create-window仅接根窗口的子窗口。当前/未来文档注入、导航继承状态；局部seq以原生来源和完整内容去重，再分配原会话全局seq。重复JSON字段顺序不改变身份，内容改写拒绝。原生frameAttached/frameNavigated与各target frame树共同保存父关系，避免动态frame被误归主页面。

初始化先等待登记回执再导航；后端open已排队的首条相同导航确认已执行，后续同URL导航仍实际加载。暂停/结束确认所有当前及已离开文档，再排空真实HTTP批次，PG核对全局/局部水位及来源。公开RecordingFinishReceipt增量允许recording-scopes-v2和scopes；旧v1保留。无新依赖、表或配置，超时仍冻结MySQL策略，事件/回执在PG，截图在RustFS。

十二Electron场景包括原五场景、同源/跨来源导航、跨站嵌套frame、弹窗、暂停新frame、同URL再次导航、事件身份改写拒绝。十个完成场景实际读取项目知识，HTTP503结束超时与身份改写为预期failed；十二场景均核对RustFS截图、实际绑定重投及同partition无关窗口隔离。自动化不等于人工操作，图摘要不等于完整知识原文已供AI。该批193后端/28JS及真实失败保留见6.75；后续关闭/移除/普通回返和缓存边界结果见4.1.4/6.76。

### 4.1.4 文档离开、缓存恢复与就绪迁移〔实际浏览器及存储已验〕

原Electron移除跨来源嵌套iframe后，最后输入及close已接收，根页面pending/in_flight均为0，但三个原生回复命令直到进程退出才结束，stop超时。现在每个native context登记命令等待，销毁/target离开沿原depart结束该realm的等待；原sendCommand仍接收最终返回。事件接受与回复分开：接受后的回复失败记录日志，不冒充事件丢失；未收到的局部范围及存活页面pending仍必须通过原结束协议核验。没有延长超时或重发页面操作。

真实完整Chromium缓存返回产生pageshow.persisted=true，原文档enabled=false、旧binding未结束，返回点击漏录。共享JS增加activate：宿主核对原文档归属并返回当前采集开关；保留document_id/local_seq，重投未确认的原消息字节，沿既有去重。旧Promise晚到不能重新排队。Python沿原page/frame恢复当前文档；Electron还核对原生uniqueId，但本次Electron仅普通回返实测及协议单测，未取得真实缓存恢复证据。宿主暂停期间回返保持暂停，恢复后再采集。

PG已提交ready、内存尚为launching时立即start曾返回409。采用项目既有会话级asyncio.Lock做法，将PG更新/状态回填与control迁移校验/占位串行；长时间驱动操作和排空仍在锁外。真实PG提交后暂缓回复的窗口在两Python驱动上验证HTTP等待原迁移，随后200，无操作重试。

MySQL仍保存既有冻结策略，PG保存原会话/事件/水位，RustFS保存截图；新增命令等待和迁移锁属于运行时协调，无新配置键、表、依赖或数据迁移。最终18模块211通过、JS36通过、前端build通过。15个实际Electron场景和20个Python API/存储场景均检查原事件、逐文档水位和截图；保留早期失败，详见6.76及[P0_RECORDING_LIFECYCLE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_LIFECYCLE_DESIGN.md)。响应关联、清洗/完整知识原文供AI、人工DeepSeek、完整App、场景编辑和报告继续待验。

### 4.1.5 完整录制事件供规划读取〔实际查询已验，默认AI操作待验〕

真实CDP/Electron反例证明PG已完整保存步骤，Action.payload_json也已保存脱敏内容，但原子图/规划知识出口只返回类型、序号与页面摘要。现在原`get_recording_subgraph`返回每个action的`event`，保留输入值、定位链、pixel、page_effect、来源及未知嵌套，原nodes/edges/摘要继续可用。读取每步自身target，不从合并Element反推定位链；复用写入侧密码脱敏作历史读取兜底，PG原流水不改写。新查询内容沿原PlanningEvidence hash冻结，历史计划不重新查图。

`event_payloads`明确协议`recording-event-payloads-v1`、完整/部分、期望/可读数量和警告。缺载荷、坏JSON/字段、seq/type身份冲突、Action范围与Recording.step_count不符均保留摘要和诊断，不能冒充完整。日志记录来源与警告数量，不打印坏载荷。没有新存储层、配置、表、依赖或迁移，MySQL/PG/RustFS职责保持，Memgraph仍为投影。

默认规划Prompt更新为`case-planning-v5`，说明如何读取完整事件、缺失与响应边界；旧任务继续原固定Prompt。受控模型原件回归证明事件实际进入第二轮请求，图发生变化后原计划GET仍返回原冻结证据。这不证明默认AI已经正确操作。真实20个Python场景及13个已完成Electron场景均核对公开API和规划知识返回原步骤，另2个预期失败保留，详见6.77及[P0_RECORDING_KNOWLEDGE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_KNOWLEDGE_DESIGN.md)。现有mutation_count在动作emit时读取，完整page_effect不是动作后DOM响应已经关联；此项、清洗、人工DeepSeek、AI实际使用与完整App继续实施。

**本次核对的执行入口缺口〔基线 `09286a68`〕：** [UI 模式合同](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/ui_automation_mode/contracts.py:65) 的 `test_execution.available` 仍为 `False`；[UI 模式运行时](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/ui_automation_mode/runtime.py:153) 在知识充足时返回 `status=partial`、`phase=task_generation_ready`，选择测试执行子方向则返回 `subdirection_not_supported`。因此后续先核对现有正式用例 Agent、浏览器工具及冻结录制依据的接入路径，再实测“读取知识 → 操作真实页面 → 对照验证点取证 → 保存结果”。保留现有测试模式，复用现有执行链；不能只把 `available` 改为 `True` 就宣称接通，也不能用探索结果或知识查询代替真实执行。本次只将源码事实与待实施项写入方案，未修改运行代码或重跑浏览器。

### 4.1.6 操作后的有限DOM响应〔三驱动与真实存储已验，完整闭环继续〕

真实反例先等待初始扫描，再点击并在50ms后新增按钮/文字；原CDP和管理浏览器均未记录响应，2失败。沿原MutationObserver、事件传输和PG/图出口修复，增加辅助事件`page_response`。响应保存`recording-dom-response-v1`、`association=temporal_after_actions`、`action_local_seqs`、实际变更数、有限元素及定位链、截止原因、观察状态和截断原因。关联需同时匹配`page.recording_scope.document_id`和局部序号，不能直接当作会话全局seq。多个重叠操作保留候选集合，不强行认定最后一步产生结果。

MySQL新增`recording.capture_settings`：是否观察响应、settle/max_wait、节点/文本/动作范围上限。创建会话冻结实际scope/revision/payload到PG metadata；用户输入不能覆盖该快照。Python驱动在注入前设置，Electron通过`recorder.js?recording_id=...`取得该会话脚本，移除全局配置脚本缓存。配置变化只影响新会话；无固定策略的旧会话继续原采集行为。原PG事件、历史计划与旧固化依据不重写，无新增PG表或依赖。

观察普通DOM的新增、移除、属性/文本变化；窗口结束时保存节点状态，删除节点标记`connected=false`。输入从接受时开始观察，关联防抖后最终fill；稳定窗口等待尚未结算的输入，最长窗口或控制边界排空已接受输入。change已结算的输入不再在结束时重复发送。暂停/停止/文档离开读取待回调变更，再关闭采集和提交原局部水位。历史dom_mutation_count是此前累积量，不再推导当前操作响应断言；新响应只形成同文档关联的低置信度评审建议，不证明业务通过，辅助事件不回放。

敏感节点、敏感容器的子节点及从其移除的节点只保留脱敏标记；父文本跳过敏感和script/style子树。节点、文本和定位文本有明确截断原因。`scope=document_light_dom`、`snapshot_at=observation_end`说明观察范围；无变化记录no_changes_observed，观察器不可用标明unavailable。此实现不代表完整DOM、网络响应、跨文档因果或Shadow DOM已经采集，也不代替遗漏检查和清洗。

实际扩大回归285通过、1默认模型专项跳过；46项JS通过，前端typecheck/build通过。46个真实场景含16个原Electron与30个Python API/存储场景，44完成项逐字段核对公开API、规划知识和PG原件，2个预期失败状态保持。独立MySQL/PG配置专项核对旧会话、新会话、另一项目和脚本交替读取；动态响应还覆盖真实跨来源嵌套frame、输入立即响应、无变化、停止截止和截断。首次Electron连接失败未稳定复现，原失败保留，不能宣称已定位根因。研究和实际结果见[P0_RECORDING_RESPONSE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_RESPONSE_DESIGN.md)、6.78和原验证记录。

### 4.2 Agent API测试

保持Agent操作方式和现有API工具。已有依赖身份/参数来源修正，继续实际验证响应提取、JSON路径/空值、依赖传值、鉴权/续期、超时/异常、幂等和结果展示。变量按项目/运行隔离，保存来源身份和实际输出，不能用计划示例值填充。

既有API课程的正常、异常、边界、鉴权、幂等、并发和可观测性作为测试设计依据，不强制增加第二套API执行器或将所有请求改成脚本。

### 4.3 Docker性能、安全测试

沿用现有Docker工具及权限。受控目标逐级压测，核对Agent实际调用参数、容器身份、退出码、stdout/stderr、原始工件、解析和报告。记录吞吐、错误率、P50/P95/P99；AI流式目标另采TTFT、Token吞吐和阶段耗时。未采指标显示缺失，不能以工具启动代替性能结论。

采集/解析专项已用现有生产runner、真实缓存k6和受管HTTP目标验证，见6.88。生成脚本load/smoke均采P99；两引擎缺失测量为null，SLA缺口保存missing_metrics及passed=null，报告inconclusive；实测0仍为0。k6旧/新嵌套阈值按实际格式转为{ok}；数值阈值保留小数。原件生产持久化、烟测缺件/损坏/非零退出、失败样本不误报pass和生产API重启下载已实测，见6.89。k6响应提取、预期状态及明确来源/消费方的header传值已经用实际HTTP、原checks、报告和PG验证，见6.90；不再以stdout变量名判断提取成功。JMeter真实断言/有序关联见6.91，正式短时load、生产ToolJob/PG报告/独立API重启下载及入库失败只保存恢复见6.92。入库失败保留analyzed检查点、工具partial；不重复压测或修改原SLA结论。性能指标按正式project_id隔离读取，冲突写入不能覆盖其他项目或收领未知归属历史，实际旧表升级/并发/重启和k6模式流程见6.93。JMeter缺值消费目标通过标准IfController局部跳过，独立目标继续；每轮清空关联变量，原线程/轮次JTL及来源布尔事实支持跳过明细，仍判目标失败，不把跳过算HTTP成功；实际五场景、两次短程执行与PG报告见6.94。两引擎POST/PUT/PATCH正文、字面特殊字符和正文/依赖头同时使用已实际验证，见6.95；对象按JSON、字符串按UTF-8原文发送，不把正文当代码，动态正文替换仍未实现。两引擎复杂路径、JSON原生类型和异常响应已用实际上下游核验，JMeter保留类型并分别展示提取/header准备事实，尾部拼接JSON不能通过；原36项冒烟、四份PG报告和40条正式清理回执见6.96。此专项不等于Agent完整调用、性能容量基线、安全扫描或正式App报告验收；负载/环境/多目标/版本可比性、阶段指标、更多JMeter业务组合及生产历史迁移继续待验。

安全测试同样用受控目标核验真实发现及原证据，不把空结果当安全、报错当无漏洞。DeepSeek不用于压测或安全扫描。

### 4.4 AI重复请求

消息重复拼接已修正，继续以实际trace关联用户turn、意图识别、规划、Agent推理、工具后推理、逻辑调用、SDK/HTTP attempt与响应。不同阶段允许合法多次调用；相同文本不是删除历史消息的依据。

定位业务重复组装、重复invoke和传输重试各自的首个发生点，保存真实失败输入与脱敏轨迹。无响应的调用不能声称没有发送；记录重试原因/预算，不虚报端到端恰好一次。

### 4.5 借鉴范围和报告

模块化生成归P1，执行补全归P2，需求变更/追踪/增量草稿归P4，场景编辑/知识/失败定位/报告归P5。Mock、定时任务和脚本导出保留后续候选，不扩进本轮核心交付。

报告分别呈现目标、正式前置、准备/清理、固定版本、预期/实际、工具轨迹和证据。规划完成、准备成功、设计覆盖、真实验证分别统计；当前失败、原失败及新回归可逐项追溯。

## 五、接口、迁移、实施顺序和验收

### 5.1 契约与兼容

扩展现有入口和消费者：生成模板/完整JSON/导入预览；规划提交与原请求查询；保存计划启动；运行依赖、准备状态/修订；文档版本/解析/比较；P4影响任务/候选评审/需求/矩阵/增量草稿。耗时分析复用任务基础设施，不用API内BackgroundTasks代替持久执行。

API、DTO、数据库、前端类型/API/页面、任务投影/恢复和测试成对更新。现有同步/异步规划、文档版本/解析/比较及Git入口已接，不重复建设。P4影响任务/设置/结果路由与前端API/DTO已按实际服务接入，见3.5.6；正式影响页面/任务池接入与实测见3.5.7；需求评审/版本/设计矩阵API和前端DTO/客户端已接，见3.6.1。正式需求页面及原请求/冲突/历史恢复已接，见3.6.2/6.14；增量生成、失败模块重试的正式API/Worker/App已接，见3.6.5—3.6.8；独立覆盖确认API及正式页面已接，见3.6.9—3.6.10。生成任务池入口与固定详情已接，见3.6.11；实际正文核验关联API与矩阵已接，见3.6.12/6.30；正式编辑页面/真实DOCX完整App已验，见3.6.13/6.31，环境适用性按5.2.3及6.33—6.41分步实施，完整产品仍待验收。

按项目启用新策略，关闭入口保留已存数据；旧运行按原策略恢复。增量迁移先预演，检查旧正文/hash、原件缺失、父链、套件/运行和失败引用；只记录可证明的历史事实。投影重建不改业务记录，终态任务不重置复用。生产迁移没有在当前环境执行。

### 5.2 P0—P5完整范围与当前推进顺序

| 阶段 | 完整交付 | 仍需推进 |
|---|---|---|
| P0 | 重复请求根因与API身份修正，实测录制/Docker；有限DOM/输入响应和冻结采集配置已验，见4.1.6/6.78 | 完整HTTP阶段追踪、人工录制/清洗/AI使用闭环；Docker性能原件和两引擎专项见6.88—6.92，完整Agent/App、容量/流式与安全真实证据继续 |
| P1 | 默认/项目模板、完整JSON、Excel/JSON导入、模块化生成 | 项目页模板、生产Excel/JSON导入完整App已验，见6.87；普通文档独立入口、三格式固定模块、进度、保存响应独立恢复及实际App已验，见6.97；默认模型生成质量和长文容量仍待验。P4确认需求增量模块及重试的已验能力见3.6.5—3.6.8 |
| P2 | 项目选取→初排→补全→冻结→现有Agent执行/恢复/新回归；固定范围、对象证据、清理覆盖及两阶段规划专项已验，见6.45—6.57 | 正式项清理及项内多效果v2专项已验，见2.3.2/6.59；默认新任务完成逻辑、证据阶段超时，固定文档预期已接入并专项验证，见2.3.3/6.60；明确操作读取及三轮真实业务专项已验，见2.3.4/6.61；来源身份一致及三格式/真实恢复专项已验，见2.3.5/6.62；正式/准备项操作选择、固定hash和去重读取已验，见2.3.6/6.63；首轮固定文档读取已接入并实测，见2.3.7/6.64；短引用职责已接入并实测，见2.3.8/6.65；运行输入路径丢失已复现并修复，见2.3.9/6.66；新默认仍在证据阶段超时，继续核对真实契约再推进自主执行；内部消费关闭及原事务见2.3.12/6.69，实际返回与两消费者共享清理/自动等待修复见2.3.13/6.70；完整准备状态、未知只读查证、计划修订、其他模式及完整产品流程继续 |
| P3 | 上传版本/原件/解析/差异/可选Git | 生产历史迁移、其他格式完整产品流程和更多故障组合；首期不部署Docling |
| P4 | 三方影响判断、稳定需求/评审、追踪矩阵、增量草稿、环境适用性/回归 | 影响生产API/Worker/正式页面及固定版本治理已接；默认模型固定场景九判断见6.12；需求身份/版本/验证点、明确评审及设计矩阵API和页面已验，见6.13/6.14；确认范围/冻结生成基础已验，见3.6.3/6.15；持久增量模块/原响应/草稿事务内部已验，见3.6.4/6.17；正式API/独立生成Worker/CLI及前端客户端已接并实测，见3.6.5/6.18；正式增量App及真实DOCX/独立Worker流程已验，见3.6.6/6.19；失败显式新任务API/独立Worker已验，见3.6.7/6.20；重试正式页面已验，见3.6.8/6.22；覆盖确认API、固定历史与矩阵已验，见3.6.9/6.24；正式覆盖管理页面、完整版本/评审/原请求/历史及真实DOCX App已验，见3.6.10/6.26；增量/重试任务池与固定详情已验，见3.6.11/6.28；正文执行关联API/矩阵及正式编辑/完整App已验，见3.6.12—3.6.13/6.30—6.31；默认Agent自主执行、部署适用和更广业务仍待验 |
| P5 | 录制场景、步骤/别名、知识查询、失败定位、结果报告 | 录制采集/项目知识及性能、安全证据与报告已有对应专项；录制后场景/步骤/业务别名/不可变版本/草稿转换实际App及存储范围见7.14；固定场景知识工具见7.15，明确关联场景规划预读/短引用兼容/Worker启动回读见7.16，未关联场景查询协议与恢复见7.17；默认AI自主查询/执行/断言、人工DeepSeek、完整清洗遗漏、失败定位及完整链路待推进 |

当前依赖顺序为：**固定影响输入/范围/调用/响应及分组协调〔专项已验〕→ 生产API/独立Worker及正式影响App〔已验〕→ 默认模型固定场景语义判断〔已验，更多业务场景待验〕→ 需求评审/身份/版本/验证点及设计矩阵API〔已验〕→ 正式需求评审与矩阵App〔DOCX真实存储/进程/浏览器已验〕→ 确认范围增量草稿及失败模块重试〔正式API/Worker和DOCX App已验〕→ 独立覆盖确认〔API/三格式生产进程及正式管理页面/真实DOCX App已验〕→ 增量生成与模块重试的任务池入口/固定详情〔真实存储及DOCX App已验，见5.2.1/6.28〕→ 正文执行证据关联〔API/矩阵/独立进程及真实DOCX完整App已验〕→ 环境适用性/回归闭环〔A、B、C1、C2a精确失效限制、C2b首次开始回执及C2c1/c2归属/原动作领取已验；C2其余与D产品待实施〕→ 用户业务评审及完整产品验收**。随后继续上述P2/P1/P0/P5及迁移剩余项；阶段编号不代表前一阶段已经全部完成。

每一步按“查实际调用链/参考 → 真实输入验证现状 → 修正方案 → 实施 → 功能/相关回归 → 同步状态/相关中文commit”推进。用户 `.gitignore` 修改保持不动、不提交；不自动推送或建PR。不把局部里程碑重新定义成整个目标完成。

### 5.2.1 增量生成与模块重试任务池〔已接入，实际范围见6.28〕

任务池继续直接分页查询PG权威业务记录，新增case_generation分支，不另建可变状态副本。API、公开摘要、前端筛选、路由和详情同步。列表读取不调用模型，不重置任务；原失败和新重试分别显示。

| 实际依据 | 已采用做法 |
|---|---|
| [任务池查询](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_task_pool_store.py)的统一联合分页 | 与原四类一起筛选/搜索/分页；项目与文档关联同时核对project_id，排序使用更新时间、类型和任务身份 |
| [公开摘要](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/task_pool.py) | 白名单目录字段、可空结果种类和重试关系；不返回checkpoint、seed、Prompt、响应、对象地址或租约 |
| [生成/重试存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_generation_task_store.py) | 结果种类只取已保存receipt；重试来源取真实parent_task_id、原creation_key及strategy。子任务内部retry:<关系ID>不能代替用户请求编号 |
| [固定详情控制器](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/useGenerationTask.ts) | 按URL固定project/document/group/task读取；子任务额外按原父任务和请求编号查询关系并核对。读取原scope/完整JSON/自身说明/收据/原响应，不回退latest |

“completed”只表示草稿或停用候选已保存。评审、启用、设计覆盖、执行和部署分别确认。归档保留完整历史读取、禁新重试；unknown继续保留原发送状态。详情复用原失败模块重试面板，查询失败显式呈现，迟到响应不能覆盖新上下文。

真实MD/DOCX/PDF和PG/MySQL/RustFS专项、旧四类兼容、108项组件、正式DOCX App/独立生产进程与旧App验收见6.28。没有新配置键、依赖或业务表，未执行生产历史迁移。下一步仍需研究运行/环境证据及回归闭环，保留P2/P1/P0/P5剩余范围。

### 5.2.2 正式执行关联已验，下一步环境适用与完整执行闭环

1. 已用真实三格式、实际本地业务HTTP、ToolRuntime、固定12步用例及原结果验证正文/准备/清理、False/0/null、失败新回归、版本隔离、证据篡改、并发和丢回执；已接PG、公开API、矩阵及前端契约。当前源码的11项专项与独立生产API场景通过，详细范围见6.30。
2. 正式编辑及真实DOCX浏览器完整App现已验收选择/提交、撤销、固定历史、未知回执、实际409及重启归档读取，见3.6.13/6.31。21项新编辑组件覆盖迟到响应、项目/版本/hash、分页和全局父确认，扩大兼容120项通过。
3. 环境现状查证已完成：已有影响环境/发布版本声明但固定unknown；项目base_url可改，runtime_context、资源作用域、浏览器/设备和工具容器均不能证明业务部署。实际两个已存Run重建保持原id/hash/快照；新增可选null字段会改变原创建hash，故新绑定必须保留旧序列化和恢复契约，详见5.2.3/6.32。
4. A步环境及固定捕获已验，见6.33；B真实HTTP来源/工具观察见6.34，具体需求版本/覆盖/点/精确用例的追加评审、撤回、并发和原请求恢复见6.35。观察与显式评审分开，回放/错目标/缺依据保持unknown，未选择点不推断。C1已接原规划/持久输入、PG运行创建核对和Agent上下文，见6.36；C2首步已在原PG创建/领取/开始事务限制明确失效的精确版本，见6.37；C2b通过项首次开始回执及原Agent恢复已验，见6.38；C2c1归属及C2c2未领取动作的当前确认限制/可靠返回恢复已验，见6.40—6.41；真实准备清理、运行中修订及换目标回归仍待实施，随后D页面/AI候选。目标地址匹配不证明已部署；文档变化不全局停用旧版。
5. 外部默认Agent完整执行与业务准确性独立验收。当前受控回执和完整App证明固定证据关联与页面行为，尚不证明自主Agent执行；编译和组件测试不能代替实际执行。
6. 完整执行闭环继续验证准备拥有者/状态、未知副作用、执行中修订、其他测试模式和失败回归报告，保留原失败/版本/结果。

该顺序不取消P0—P5其他工作：P2准备拥有者/状态、未知副作用、计划修订及其他模式，P1模板/生产导入，P0实际消息/人工录制/Docker，P5场景知识和报告，以及生产历史迁移仍须逐步实施和实际验收。API、组件、独立进程、完整App及默认模型分别记录状态。

### 5.2.3 环境适用性查证后的工程方案〔A、B、C1、C2a/b、C2c1/c2及C2c3逻辑操作专项已验，效果状态及其余步骤待实施〕

完整来源、原运行重放及设计见[P4_ENVIRONMENT_APPLICABILITY_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_APPLICABILITY_DESIGN.md)。6.32是实施前源码/原证据重放，6.33是之后A步的真实新功能验证，分别记录。A已有正式服务/路由、容器/main初始化、前端类型/API；没有新模式或依赖，C1现已绑定明确选择的新规划/运行，原无环境计划的hash与恢复契约保留；实际范围见6.36。

核心问题保持简单：**在这个固定目标里，这个需求变化是否已生效，哪些精确用例版本因此还适用？** AI基于原差异、完整用例和实际证据提出候选，用户明确确认；后端核对引用及事务状态。环境标签、文档版本或观察到release字符串都不能单独回答业务规则是否生效。

| 小步 | 做法和可验证边界 |
|---|---|
| A 环境及固定依据 | 稳定项目environment_id与生命周期在PG；可编辑名称、非敏感地址、已有连接配置引用在MySQL。任务捕获实际配置payload/revision/SHA并保存PG固定依据，恢复不按旧revision回读当前MySQL。登记和配置是明确的两个操作，缺配置可见，不假设跨库原子事务 |
| B 证据及确认 | 引用同项目/同固定目标的真实ToolJob/result/原件、字段路径和原值/hash。PG追加需求版本、具体覆盖及精确用例版本的生效/适用决定，保留原确认；大证据沿用RustFS。不猜测/version接口或引入自动部署探测 |
| C1 固定规划/执行依据〔已验〕 | 新请求仅选environment_id/snapshot_id/expected_snapshot_sha256；服务端冻结完整目标和当时确认，沿原规划、PG创建事务及Agent上下文传递；原请求恢复优先，不读当前MySQL或重新发模型。完整12步JSON及实际工具结果见6.36 |
| C2a 精确失效限制〔专项已验〕 | 原PG创建/领取/开始事务读取该固定快照的最新确认；只对明确invalid的精确用例版本产生原Attempt/Result体系的未执行结果，必要依赖传播、独立项继续。保存完整当前确认/采用确认/hash/阶段。实际锁竞争、领取后变化、原检查点恢复见6.37 |
| C2b 首次开始回执〔专项已验〕 | 首次允许start在原PG事务保存完整当前确认、精确版本/目标及root Attempt，进入原Agent上下文/hash和结果；同租约丢回执读取原记录，过期/原图恢复不替换原依据。三格式、真实Agent图、独立API/规划/执行CLI及SDK流式消息见6.38 |
| C2c2动作领取〔专项已验〕 | 新显式策略在原PG事务核对当前确认及原Job领取，before/case精确拒绝；after暂不限制，阶段不能证明清理。可靠返回恢复交付，真正未知不重发同Job，实际范围及领取边界见6.41 |
| C2c3逻辑操作〔新协议专项已验〕 | 6.42旧策略失败仍保留；新冻结协议以执行项内稳定operation_key和完整输入关联唯一原Job，新调用保存自身Job/审批并恢复原输出，真正未知不重发。不同key允许明确新操作/负向重复/只读刷新，相同key改输入明确冲突。实际订单和最终结果引用见6.43；不把身份或phase当作效果/清理状态 |
| C2c3效果/清理〔待实施〕 | 继续沿原Job、计划拥有者和实际输出研究执行期间效果记录、消费范围、未知只读查证和必要清理；单对象成功清理不能证明所有新对象、共享准备或中断场景均已处理 |
| C2其余〔待实施〕 | 已开始后的逐动作修订、未知副作用及换目标新回归分别实现/验收；不重写原计划、原确认或重置终态 |
| D 页面和回归 | 项目环境、影响环境选择、矩阵生效/适用确认、固定运行/回归详情同步契约。回归创建新Run并保留父失败，换目标形成新固定依据；真实文件/独立进程/built App/默认Agent分别验收 |

A当前MySQL配置键为project.environment_target、scope为正式environment_id；PG记录稳定登记和固定捕获。捕获须带明确revision与配置SHA，同请求先读原捕获，配置改址/删除/归档后仍可恢复；读取与PG提交之间配置变化不冒充跨库“提交瞬间最新”。配置PUT沿用当前设置save语义，丢回执须GET核对，不能宣称幂等或恢复已被覆盖的旧设置。合法最长项目表名、归档抢先、新参数/跨项目、四路并发及错登记/篡改已实测。部署状态仍unknown；这不是B—D或完整产品验收。

B先补真实来源证据，详见[P4_HTTP_ORIGIN_EVIDENCE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_HTTP_ORIGIN_EVIDENCE_DESIGN.md)。实际Response的每跳方法、scheme/host/port/path和响应码保存于原execution_checkpoint的http_observation_v1，不新增旧模型缺省字段或改写旧hash。当前项目API只读原ToolJob与固定环境捕获、具体字段/原值/hash，返回目标地址范围匹配/不匹配/未知。跨目标跳转即使body正确也不匹配；恢复completed_request不冒充新network，旧无来源、输出检查点不一致继续unknown。它还不是需求版本生效/适用确认，也未绑定Run环境。

B后续确认采用[business-applicability-v1设计](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_DECISIONS_DESIGN.md)：每个链绑定固定环境快照和需求版本；显式选择initial或具体覆盖及hash，确认具体点effective/not_effective/unknown和精确用例valid/invalid/unknown。事务重读PG原工具/版本，保存完整固定正文、来源和评审理由。撤回追加空范围记录；重确认/回滚再追加，不删除旧确认。部分点与其他环境/版本不继承；旧部署unknown不改写，未选择范围不当作已通过。接口提供原请求恢复/历史/分页，前端类型与客户端同步；此API不自动判定7/9天或全局停用用例；C2a另在原运行事务消费明确失效确认，见6.37。通过项首次开始回执已由C2b补齐，见6.38；正式页面/AI候选、运行中动作及换目标回归仍需实施；C1固定绑定自身不等于后端限制。

C1依据[P4_ENVIRONMENT_PLANNING_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_PLANNING_DESIGN.md)接现有规划内核，不新建环境规划平台。正式规划请求可选固定快照，服务端读取PG原快照及该快照各需求版本的最新完整确认，冻结为business-planning-environment-v1；包含撤回和unknown，不读当前配置替换目标。环境写入plan/seed/checkpoint，完整依据进入规划模型；PG创建事务核对原快照/登记和原确认。恢复读固定输入，不能使用客户端自报正文/hash。

Agent环境通过独立business_environment字段传递，用户runtime_inputs原值及同名键均保留；明确以固定snapshot的目标地址为准，沿现有工具权限执行。新增缺省字段从旧wire/hash省略，仅处理这些新字段，不全局exclude_none；原业务null/False/0、完整12步、旧普通与回归运行hash保留。会话首次/重放、审批、上下文hash、提示和核验输入统一序列化，避免同一身份误判改变。

C1的创建核对只证明原固定依据真实一致。C2a依据[P4_ENVIRONMENT_RESTRICTIONS_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_RESTRICTIONS_DESIGN.md)补入最新确认读取，复用既有依赖/未执行结果：绑定目标的创建、领取和开始先锁PG项目，再按精确版本判定；项目锁内不调用模型、HTTP或MySQL。未提及、unknown、撤回及同需求/点的相互冲突确认不硬阻断。限制回执保存原规划SHA、固定快照、全部当前确认、实际采用确认、精确版本、时间与阶段；独立项不消耗阻断项的领取名额。

恢复时先核对历史Attempt的started_at/checkpoint，不能因为过期恢复把item重新置queued，就宣称业务从未发生。已开始/有检查点的条目保留原恢复流程与unknown，不产生虚假的未执行结果。已有可靠工具结果的恢复可复用原结果；这不保证尚未执行的待审批工具不会发送HTTP。6.39已复现：start之后追加明确invalid，待审批动作获批后仍新增一次实际HTTP。终态阻断在撤回、归档、配置变化后仍返回原结果，重试另建Run。新规划可冻结新确认，既有计划不改写；允许通过项的当前决定回执已由C2b持久化；运行中逐动作修订和换目标新依据仍待C2其余。默认模型、正式页面和用户业务评审分别验收。

C2b依据[P4_ENVIRONMENT_ADMISSION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_ADMISSION_DESIGN.md)复用原Attempt JSONB，不新建表或配置。首次允许start在同一PG事务追加environment_admission：完整当前确认、原规划SHA、精确用例/目标、根执行Attempt、检查时间和SHA。decision为no_explicit_invalid，不等于适用、部署或通过。该依据沿完整Agent上下文、首次/工具后推理、原hash/快照/审批和最终结果传递。

同租约start提交回执丢失，先核对原running Attempt及有效租约，返回原状态，不重新读取当前确认；错token或过期仍拒绝。过期领取复制原回执，并验证本条目的根祖先链；旧已开始而没有回执的历史保留原缺失/序列化/hash，不事后补造。业务null/False/0保留，新缺省字段只在原wire中省略。实际图检查点恢复、终态派发和重启结果见6.38；这不代替逐动作修订、未知副作用处理或外部默认模型验证。

**C2c1归属与C2c2原动作领取已接，完整执行变化闭环仍未完成。** binding-v1由服务器从完整固定上下文推导执行项/阶段，保存在原Job/审批；新增显式admission-v1要求binding-v1，在原PG事务核对该固定目标的当前确认并领取或拒绝。旧计划保持原策略与hash，不按当前配置补造历史。新策略的三格式待审批精确invalid缺口已回归；完整原响应恢复、领取未知、确认锁与独立生产见6.41。after阶段仅暂保留，并不证明真实清理。依据见[P4_CASE_ACTION_BINDING_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CASE_ACTION_BINDING_DESIGN.md)及[P4_CASE_ACTION_ADMISSION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CASE_ACTION_ADMISSION_DESIGN.md)；6.39和6.40仍作为当时失败/进度保留。
采用以下工程顺序；第1步已接，第2步已在显式新策略下验收；第3步before/case限制及单次真实订单清理已验，执行中效果拥有者/多效果清理仍待实施；第4步及第5步跨新调用身份/换目标回归待实施。第6步已复现原图跨调用重复及未知新调用重发，后续用原失败输入验证修复，并补默认Agent：

1. 在新执行协议中显式关联固定计划的执行项；服务器按原计划核对项目、所属条目、版本和阶段。执行关联元数据与工具业务参数分开，保持现有工具与测试模式；旧协议、原hash和历史审批保留原恢复方式。
2. 沿既有工具治理、审批和原子领取入口处理。已有可靠终态或已观察输出先返回原结果；真正将发送的新动作，再在PG事务内读取该固定目标的最新确认，保存动作依据并领取。事务内不调用模型或HTTP，不能使用整行旧ToolJob覆盖并发状态。
3. 明确invalid只影响尚未执行的对应目标动作及其必要依赖；独立项按原权限继续。必要清理依据原固定归属、本次真实准备结果和拥有者判断，不能因目标失效一并禁止，也不能把任意DELETE当作清理。
4. 将变化和受影响范围作为真实工具观察交给Agent，允许在尚未执行范围内追加计划修订。保留首次开始回执、已完成输出和原计划，不把已经开始的条目重写为business_execution_started=false，不把限制动作写成测试通过。
5. 发送结果未知时保留原调用意图，优先用项目实际只读能力核实；不能因模型换了call_id就重复有副作用的操作。换目标回归创建新的固定依据和运行，不替换原运行环境。
6. 先用6.39的真实失败输入回归，再验证审批等待期间变化、实际有状态的准备/清理、独立项、并发确认/领取、发送未知、恢复及旧协议。静态HTTP夹具返回DELETE成功不能证明数据已清除；受控模型与外部默认Agent分别验收。

C2c3依据[真实状态研究](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_STATEFUL_OPERATION_RESEARCH.md)细化第3/5步：保留原工具、每次模型调用和审批，显式冻结新操作契约；逻辑操作身份绑定项目、固定运行/执行项及完整输入，独立于provider call_id。相同已声明操作恢复可靠输出，未知操作先核实；输入变化不能冒充原操作重试，合法的第二次创建、负向重复与只读刷新也不能按相同参数误删。具体协议尚待源码查证后确定。执行期间记录实际产生效果的工具证据、输出路径、拥有者/消费者及待清理对象，清理核对实际对象状态；after标签或单个成功回执不证明全部清理。6.42的两个失败是后续修复验收输入，现阶段没有新增生产去重或效果状态机。

依据为现有`run_store`原执行历史、`PostgresBusinessPlanning.current_reviews_in_cursor`的项目事务边界、固定执行上下文归属、工具治理/审批/领取，以及本地《Agent轨迹与协议鲁棒性》04.5幂等与补偿。C2c1的协议字段、原策略冻结及消费方已同步，见6.40；原Job领取/适用性回执与可靠输出恢复已由C2c2接入，见6.41；稳定逻辑操作身份、真实准备清理及计划修订契约仍需后续查证并实施。

只有已确认重大变化使旧版本失效，才限制**该用例版本在该固定目标中的新执行**。未生效保持旧版适用；unknown展示缺口；原用例仍符合新规则则继续适用。其他环境、版本、负向条件和独立目标不自动停用。配置改址、部署回滚或证据冲突需新依据/新确认；环境同名、另一环境通过不能迁移结论。

旧影响及正文执行关联的deployment_status=unknown保留，新的环境确认独立展示。生成模板仍只用于生成；按用例执行继续“理解→项目选取→初排→简单检查衔接/补全→最终排序/冻结→Agent”，不建立新的测试模式参数映射或通用部署平台。新增绑定契约须显式处理旧hash、原提交恢复、全消费方同步及生产迁移，不能只加可选字段。

### 5.3 验收矩阵

| 场景 | 必须证明 |
|---|---|
| 嵌套JSON/超过六步/模板修改 | 未知字段和完整步骤保留，旧执行不加载新模板 |
| 两种Excel及JSON导入/映射调整/重复提交 | 原件可查，预览固定，逐行错误可定位，草稿与批次事务且无覆盖 |
| 已有条件/前序产生/缺正式前置 | 不多补准备，必要时补不相关前置，可靠准备与实际传值可查 |
| 负向条件/无依据/无匹配 | 保持故意缺失，具体缺口可见，不编造、不切自由测试 |
| 多worker/前置失败/输出缺失 | 依赖未满足不领取，仅相关项blocked，独立项继续 |
| 审批/工具返回/进程中断/未知副作用 | 原决定与证据恢复；已成功不盲重发；真正未知明确核对 |
| 准备共享/清理/执行中修订 | 拥有者与独立状态、消费范围、真实输出及追加修订可追溯 |
| 上传无Git/同名/恢复旧内容/并发父冲突 | 独立正常；不误合并；V2→旧内容形成V3；未知提交不误删原件 |
| MD/DOCX/PDF/扫描页/资源超限 | 文本/结构/位置对应实际文件；不足与失败可见，原件和旧快照不变 |
| 固定比较/历史/换行变化/重启 | 字符/章节/表格差异可核对，不用当前/latest代替固定输入 |
| Git固定commit/脏工作区/移除仓库 | 只读取得指定对象，历史重读不依赖仓库，上传不受影响 |
| P4固定范围/来源缺失/新草稿 | 使用当时依据和完整JSON，缺口不伪造，V1可执行同时V2待评审 |
| 长文档/跨节引用/批次中断/模型异常 | 全范围清单与未处理项明确；响应先存后验；完成批次不重算 |
| P4提交回执丢失/多worker/终态重投 | 同请求同任务，过期worker无权写，已存响应不重复调用 |
| 7→9/新增/删除/同标题/仅格式变化 | 与人工标注基线核对影响/遗漏/误报；AI匹配经评审 |
| 原文引用/用例路径/未来环境 | 原切片与固定版本一致，位置存在不冒充语义成立，未生效不误阻断 |
| 增量/停用/矩阵/新回归 | 只改确认范围且保留旧版本；设计覆盖与实际证据分开；原失败不覆盖 |
| 验证点关联实际结果/部分核验/准备与清理 | 固定版本、原结果hash和实际工具值一致；正文证据独立；部分通过不冒充完整需求通过 |
| 执行关联撤销/历史/丢回执/环境未知 | 追加历史、同请求恢复，无新工具或模型调用；旧通过不迁移，部署无依据继续unknown |
| 人工DeepSeek录制/受控动态页 | 操作和响应可对账，最后一步入库，清洗不误删，AI实际使用 |
| API鉴权/依赖传值/空值/恢复 | 值来自实际响应且作用域正确，保持Agent执行 |
| Docker性能/安全/报告 | 参数、真实进程输出、原工件、解析指标与展示逐项一致 |
| 真实默认模型/完整产品/历史迁移 | 各自独立证据；替代模型、组件宿主、隔离存储不冒充生产迁移或全系统可用 |

## 六、当前实施状态与历史验证

当前最新已提交基线为 `09286a68`：6.79 的冷启动外键顺序修复及实际恢复已提交。以下各行记录对应批次的已验范围；本次仅更新方案并重跑两项上传回归，见 6.80。默认 AI 录制知识执行与完整 P0—P5 验收仍未完成。

历史消费批次基线`67d134ae`；可靠返回、两消费者共享清理及版本化自动等待修复见6.70，原消费阶段与清理事务见6.69，前批逐项核验基础见6.68，输出组合专项见6.67；运行输入路径修复见6.66，默认自主执行失败见6.65；文档预期专项见6.60；原可靠返回对象证据基础见6.46，旧策略真实清理覆盖失败见6.47，新固定覆盖路径及剩余范围见6.49；此前C2c3准备/清理及跨调用失败研究见6.42，C2c2原领取/当前确认范围见6.41。增量/重试任务池和固定详情已提交，原实测见6.28。正式重试页面见6.22，独立覆盖确认API及三格式生产进程见6.24；正式覆盖管理页面、明确固定用例评审、原请求恢复、历史/撤销/冲突与归档完整版本读取已接并经真实DOCX App验证，见3.6.10/6.26。需求正文执行证据关联的后端/API、前端契约与矩阵见6.30；正式编辑页面、真实DOCX完整App及重启归档恢复已验，见6.31。环境实际来源及独立显式适用确认API见6.34—6.35。各历史专项保留原研究基线、失败和实际运行记录；运行环境绑定、精确失效限制与首次开始回执分别见6.36—6.38；用户业务评审、精确Token、运行中动作修订/未知副作用、换目标回归、正式环境页面、其余P0—P5与生产历史迁移仍未完成。

| 模块 | 已提交/已验证范围 | 尚未完成 |
|---|---|---|
| P0消息/API | 消息重复组装、依赖身份和参数来源已有真实样本及回归；P4项目单次SDK策略、冻结参数及5逻辑/5物理实际追踪见6.11 | 其他全阶段HTTP追踪、完整API产品验收；不将P4单次策略等同整个系统没有重复发送 |
| P1模板/JSON/导入 | 后端、类型、实际组件及真实文件/存储专项已有证据 | 模块化生成与完整正式App模板/导入流程 |
| P2规划/执行/回归 | 持久规划、固定完整上下文、原Job/审批及新回归已有专项；范围/对象/逐对象覆盖见6.45—6.49；两阶段规划及正式项清理、多效果v2和持久恢复见6.57/6.59；实际运行输入子路径见6.66，多操作输出组合与固定消费者传值见6.67，逐项核验基础见6.68，内部消费结束/原事务见6.69，可靠返回及共享两消费者、明确业务依赖和旧映射重放见6.70；旧完整请求/回执语义保持 | 默认新任务逻辑完成但证据阶段超时，尚无默认自主业务执行；更多消费者/来源组合、多Worker/跨租约、完整生命周期、未知只读查证、计划修订、其他模式/完整App仍待验；历史失败保留 |
| P3上传/解析/比较 | 原件/派生分存、版本/历史、工厂/受限进程、持久API/独立CLI、DOCX正式App及比较提交/历史/恢复已有产品证据 | 生产历史迁移、其他格式完整产品及更多故障组合 |
| P3可选Git | `4a02ae2`；真实Git专项25通过，MD/DOCX正式App与生产API/CLI/真实存储两场景通过 | 生产迁移及其他格式完整产品；上传无需Git约定不变 |
| P4三方读取/固定提交 | 固定提交/输入/范围、模型/Prompt/Skill及unknown历史已有专项；分组生产和正式App见6.10，SDK策略见6.11，完整字段/默认模型固定场景九判断见6.12；稳定需求身份、不可变版本/验证点、明确评审及设计矩阵API、真实三格式与独立进程恢复见6.13；本轮正式需求App真实DOCX/原请求/冲突/重启/归档恢复见6.14 | 更广真实业务与用户评审、精确Token、部署适用、完整自主Agent执行、运行环境适用及生产历史迁移 |
| P4逐版本治理 | `918cdace`；后端106通过/2默认模型跳过、前端37通过、构建通过；正式App/生产API/隔离真实存储1场景通过，模型调用0 | 生产历史迁移、运维入口/投影重建、旧套件和原失败完整迁移；不代表影响AI完成 |
| P4增量生成 | 冻结基础06471590、持久模块80dc609b见6.15/6.17；正式API/独立生成Worker/CLI三格式通过，API/内部19项及公共停用候选1项通过，前端契约8项/类型构建通过，见3.6.5/6.18；正式增量App/真实DOCX和独立Worker通过，组件56项及旧需求App兼容通过，见3.6.6/6.19；显式失败新任务API/独立Worker及真实文件通过，见3.6.7/6.20；正式重试页面72项组件及真实DOCX App、丢回执恢复、独立CLI/存储和旧App通过，见3.6.8/6.22 | 覆盖页面及增量/重试任务池已验；真实业务评审、精确Token、运行/环境及迁移尚未完成 |
| P4覆盖确认 | `b908fe29` API/三格式生产进程见3.6.9/6.24；本阶段正式页面、完整JSON/固定版本评审、原请求恢复、历史/撤销/冲突及归档读取已验，84项组件和真实DOCX完整App见3.6.10/6.26 | 正文关联正式App已验；环境适用、更广业务及生产迁移 |
| P4正文执行关联 | 固定核验/PG追加历史/候选恢复API和矩阵见6.30；正式编辑、原请求恢复、历史/撤销/409/归档完整读取已验；120项组件、真实DOCX完整App与独立进程见3.6.13/6.31 | 默认Agent自主执行、点级完整性及环境适用/生产历史迁移 |
| P4环境/来源 | A登记/MySQL配置/PG捕获见6.33；B真实来源/确认见6.34—6.35；C1规划/Agent固定依据见6.36；C2a精确失效的创建/领取/开始限制、依赖/独立项、锁竞争和检查点恢复见6.37；C2b首次开始回执/原Agent恢复/真实生产执行见6.38；C2c1归属及C2c2未领取动作限制/原响应恢复见6.40—6.41 | 运行中动作修订、换目标回归、正式环境页面、AI候选及默认Agent；来源匹配不自动生成部署确认，未选择点不推断 |
| P0/P5录制与Docker/报告 | 生命周期见6.72—6.76；完整事件查询/API/规划入口见6.77；有限DOM响应及三驱动真实存储见6.78，285通过、1默认模型专项跳过，46JS/typecheck/build通过；生产冷启动和恢复修复见6.79 | UI模式执行子方向仍未开放，见4.1.5；默认AI引用知识并操作、完整清洗/遗漏检查、Electron真实缓存恢复/并行attach与更多故障、人工DeepSeek/App、Docker真实结果及场景/报告尚未闭环 |

此前内部输入专项：新增输入任务/来源、既有解析任务/比较任务和差异共 **62通过、0跳过（78.97秒）**；随后增加固定版本筛选/分页查询并复验新任务 **7通过、0跳过（17.22秒）**。实际MD/DOCX/PDF、MySQL/PG/RustFS、解析CLI与Redis比较Worker均运行；影响模型调用0。输入准备后的“重启”专项是新Store/Service读取同一真实数据库，尚未独立影响Worker进程验收。证据为 `p4-fixed-input-regression-final.xml`、`p4-fixed-input-query-final.xml` 和对应实际原件/输入目录。

此前方案整理的 **31通过、0跳过（22.66秒）** 证据仍保留在 `plan-current-input-verification.xml/`，不混为本轮运行。新输入专项首轮1失败是数据库适配包装异常断言不符，已修正；扩大回归60通过/2失败为既有极短租约时序，原基线/当前单独各2通过后按测试职责改为显式过期和实际跨租约心跳验证，最终62通过。失败XML保留。

此前方案同步另实际运行影响任务专项：**7通过、0跳过（17.69秒）**，使用真实MD/DOCX/PDF及隔离MySQL/PG/RustFS；证据为 `plan-sync-e152-impact-verification.xml/.log/`。只核验固定提交/输入、并发重放、恢复与权限边界，不调用影响模型。上次最终暂存检查报告的文件末尾空行已移除，实施记录已纠正，不能把原失败检查记为通过。

已提交的模型依据与原输入专项先 **12通过、0跳过（47.37秒）**；扩大覆盖模型传输、Skill、生成及持久规划后，首次 **75通过、1失败（91.20秒）**，失败为旧独立规划CLI夹具漏清理解析依赖表。登记启动前表归属、按依赖顺序清理，并把证据写入新目录后，实际复验 **76通过、0跳过（89.95秒）**。失败XML和清理记录保留，历史CLI文件原字节恢复。影响专项的MD/DOCX/PDF各1次受控HTTP调用，参数变化未发送；默认外部影响模型未调用。另用当前schema读取此前三份已提交输入，旧canonical/object hash一致，没有补造模型依据。证据为 `p4-analysis-context-regression-verified.xml/.log/` 与对应目录。

这些结果不证明待实施AI影响、矩阵、增量生成、人工录制或Docker可用。新增能力仍是内部服务，前端契约未改。本轮调用专项与相关回归实际运行，未改前端或公开契约，因此不重复前端构建/浏览器；未调用默认影响模型或执行生产迁移。查证、失败与验证记录见实施记录。完整升级目标继续保持未完成。

此前范围专项首轮15通过/3失败（64.27秒），三失败来自新夹具创建版本漏传既有模型/Prompt/Skill字段；补齐后18通过（65.91秒）。补充清单来源缺口后最终10模块 **82通过、0跳过（108.09秒）**，含真实MD/DOCX/PDF、23986字符长MD分82段（旧新共两来源）、跨节读取、完整12步JSON/字面路径、PG提交回执丢失/新Store恢复及终态不可重领。原模型传输专项含3次受控HTTP，清单新专项模型调用0；不是独立影响进程或默认模型业务判断。旧三份输入canonical hash不变、8份源码字节编译通过；证据p4-manifest-first/verified/final.xml及对应.log/目录，失败记录保留。

此前单元调用专项首先 **30通过、0跳过（132.97秒）**，扩大11模块 **101通过、0跳过（181.62秒）**。随后收紧为只冻结所选模型实际适配器/SDK，并增加响应对象保存失败后不重发的验证；当前源码最终复验 **101通过、0跳过（187.90秒）**。以 `p4-calls-verified.xml/.log` 和对应实际文件/存储/HTTP目录为最终依据，前两次证据保留，不替代最终源码验证。

新调用专项19项包含真实MD/DOCX/PDF×原生/ LangChain两适配器的6组HTTP场景、12步完整JSON及当时来源、6路并发唯一逻辑发送权、完整跨节续读、请求/意图/响应PG提交回执丢失、实际HTTP中断及新租约未知恢复、显式重试和预算保留、截断/空/工具/供应商错误/超大响应分类、Tokenizer内容计数、实际模型参数/适配器核对及对象保存失败。单次逻辑意图在HTTP500场景实际产生3次SDK传输；不据此宣称物理发送恰好一次。新Store/Service恢复不等于独立影响Worker重启，受控HTTP固定文本不等于真实AI业务判断。源码字节编译、历史hash、三份方案、本地引用、凭据格式及暂存原件字节核对见 `p4-calls-validation.json`。

此前响应专项首轮 **31通过、0跳过（88.02秒）**；增加提交阶段协议冻结、旧请求hash与未知对象写入边界后，扩大首次 **138通过、3失败（284.92秒）**。三失败为旧提交测试在整个seed字符串搜索content_json，新Schema合法字段名被误判为正文；改为对实际用例引用字段集合/哈希进行检查，保留禁止下载钩子及文档/用例变更后的固定输入验证。补充可供模型补读的固定用例/差异身份目录后，最终12模块 **141通过、0跳过（291.57秒）**。失败XML/log/目录保留，最终依据为 `p4-responses-final.xml/.log` 及对应真实原件/存储/HTTP目录。

新响应专项40项，其中16项为解析协议/六份已提交旧调用请求回放，24项为真实文件/HTTP/MySQL/PG/RustFS功能。6组文件×适配器验证完整12步JSON、False/0/null与最后一步、提交协议Schema变化后仍按旧seed准备请求、局部候选/依据缺口、后续用例/文档变化及终态查询；其他场景覆盖显式补读接续、未读/归一/错误/未知引用与协议、5路核验竞争、实际PG提交回执丢失、核验对象未知写入/保留/重做不发模型、对象篡改和不完整回执分类。unit_finished仍只是单元结构结束与模型声明，语义pending_review、部署unknown；当时尚无自动协调或全范围发布；本轮另接3.5.5，不改此前证据。

最终回归保留独立规划CLI真实进程中断/重启，impact恢复仍是新Store/Service，不当作独立影响进程。旧三份输入与六份请求canonical hash、旧CLI工件、源码字节编译、三份方案、链接及原件暂存字节核对见 `p4-responses-validation.json`。未调用默认影响模型、未改前端/公开API、未执行生产迁移；完整P0—P5目标继续，当时后续为自动协调/全范围结果；本轮见3.5.5，接下来继续生产入口和正式AI/需求闭环。

### 6.1 此前内部协调验证〔基线9be9bfd8〕

本轮协调专项首轮 **12失败（46.54秒）**，发生在提交前的协议哈希：误用只接受模型记录的impact_record_hash处理普通dict，尚未协调模型调用；改为项目已有canonical JSON方式。次轮 **11失败、1通过（50.14秒）**，暴露输入准备只读取旧claim：已有检查点再次准备造成不可改冲突；改为先读数据库当前检查点，保留已存输入。失败XML/log均保留。修正后真实功能 **12通过（78.07秒）**。

补充实际来源核对、在途HTTP取消、截止时间耗尽后的已存单元恢复、真实租约续期/失效和回执类型后，13模块 **161通过（414.12秒）**，含1条新测试导入TestCaseBundle产生的收集警告。别名修正，预算原因分为调用数/累计估算/字节/窗口，并增加两项来源篡改发送前拒绝后，当前源码最终 **163通过、0跳过（422.81秒）**；新协调专项 **22项，均为真实文件/HTTP/隔离MySQL/PG/RustFS场景**。最终依据p4-workflow-validated.xml/.log及实际原件/结果目录，前轮结果不替代最终源码验证。

6组MD/DOCX/PDF×两适配器验证自动补读、跨单元完整规则集合/原文传递、12步JSON及false/0/null、固定范围结果/终态读取；其余验证漏规则/来源不足/无规则、精确重复分组与潜在重叠、预算耗尽、截断/空/工具回执、结果PG已提交丢回执、新Store发布不重发、对象实际保存丢回执/保留、截止时间已耗尽仍读已有响应、真实在途取消后的unknown不自动重发、共享Handler真实心跳维持/过期取消和来源篡改发送前拒绝。历史输入/旧请求hash、源码字节编译、旧规划CLI和原件暂存核对见p4-workflow-validation.json。

本轮回归包含已有独立规划CLI进程，影响恢复仍是新Store/同进程Handler，不冒充独立影响进程。没有默认外部模型语义判断、生产初始化/API/独立影响CLI/前端或生产迁移。新增PG快照表由隔离Store初始化验证；生产部署须接正式初始化和迁移。三份方案全文同步，用户.gitignore不改不提交。完整目标未完成，下一步接生产依赖/入口/独立Worker及真实模型，再推进需求评审、矩阵、增量与其余P0—P5范围。

### 6.2 本轮正式 API 与独立影响进程〔基线d4a3d636〕

首轮1失败28.97秒：测试创建其他项目用例时空source_refs不满足既有契约，API实际422；改为明确人工来源。次轮4失败1通过208.54秒：正常流程已保存结果，但测试把解析坐标/布局限制下的partial错误预设为complete。核对比较器警告与原协调状态规则后，验收改为明确原限制、各声明单元analyzed及无缺口，不修改生产状态计算。两轮失败XML/log/原件/进程输出保留；次轮真实中断恢复已通过。

随后正式专项18通过2跳过286.07秒；跳过的是未设置live开关的两项既有SQL池测试，另外实际启用MySQL/PG开关运行8项全部通过3.75秒。补充原始HTTP消息与请求/响应/核验/清单/输入对象证据，并启用两个live开关后，当前源码正式专项及资源生命周期 **20通过、0跳过（281.38秒）**；原影响链与三格式正式比较回归 **135通过、0跳过（542.48秒）**。两组模块不同，记录分别见p4-production-validated.xml/.log、p4-production-regression.xml/.log。前端新增/既有文档API测试 **6通过**，npm run build（含vue-tsc）通过。

5个正式进程场景为MD、DOCX、PDF、DOCX LangChain及MD在途Worker中断。均从实际生产API提交，独立解析/比较/Outbox relay/影响CLI与隔离MySQL/PG/Redis、实际RustFS及HTTP运行；没有替换AppContainer生产Provider。验证固定完整12步JSON及False/0/null、后续配置/草稿不替换旧依据、原请求/重启、错误Bearer/项目隔离、终态重投及关闭派发历史。中断原调用只发送一次，新进程保留unknown，旧结果不伪造成功；正常流程保留真实坐标/格式限制为partial。完整原件、PG检查点/seed、原始HTTP和对象字节保存并核对SHA-256；物理消息与完整冻结请求对应，不以统计数代替原件。

本次没有调用默认外部模型进行语义验收，没有正式页面/任务池浏览器验收，也没有生产历史迁移。三份方案全文同步；源码、链接、历史证据及原始暂存字节核对另见p4-production-validation.json。完整目标未完成，接下来继续正式App/任务池、真实模型与需求评审/矩阵/增量闭环，再推进其余P0—P5和迁移。

### 6.3 正式App与任务池〔研究基线3ae0d9d9〕

真实存储任务池专项及回归4通过、0跳过（10.70秒）；前端最终62通过且无未处理错误，构建包含vue-tsc通过。影响正式浏览器1通过、0跳过（67.93秒），既有比较正式浏览器回归1通过、0跳过（65.68秒）。分别记录在p4-product-pool-first、p4-product-frontend-final、p4-product-build-final、p4-product-browser-preparation和p4-product-comparison-regression的XML/log及证据目录，测试数不累加为全系统覆盖。

浏览器原始失败均保留：54.34秒失败为把初始/home路径错误拼入站点根；47.10秒失败为未等待实际POST回执丢弃；86.83秒失败为刷新后设置折叠栏已关闭；60.60秒失败为误把API提交前容量拒绝当作Worker失败。最后依据capture/prepare真实职责重新设计容量样本，任务确实创建后在准备阶段失败，原成功历史保留且不增加模型发送。前端首轮58断言通过但有2项旧mock未隔离子组件的未处理错误，已修正测试挂载；最终62项无错误，不掩盖原记录。

实际成功流程保留7次物理HTTP、29份原始对象、固定PGseed/请求、MySQL配置、原文引用、下载结果及页面截图；重启后下载全文等于正式API历史，页面无pageerror，1440宽视口正文无横向溢出。只使用受控HTTP，不将其当真实默认模型判断。没有新增依赖或业务环境配置，不修改已保存输入/模型协议及终态；生产历史迁移、默认模型、需求评审/增量/矩阵/适用性及其余P0—P5仍未完成。

### 6.4 默认模型诊断与引用协议修复〔研究基线0aa271f1〕

三次默认模型单元探针分别1失败、126.06/125.80/126.86秒，固定真实文件、解析/比较、三个完整8步用例、PG输入及RustFS原字节均保留。首次物理取证漏覆盖当前SDK的httpx2；第二次确认两次同正文POST；第三次修复引用输入后仍为ReadTimeout及明确取消，任务调用保留outcome_unknown，没有响应或语义通过结论。原证据为p4-default-model-first/transport/citation-v3的XML/log/独立目录，不覆盖。三个项目总时限600秒在隔离MySQL，默认模型8192输出与60秒SDK超时未改；对应源模型前后参数一致。

换用项目既有SettingsService连接检查的ping/pong，直接调用现有方法，不替换提供商，实际一次POST、2037ms、返回pong。证据p4-default-model-health-diagnostic/及.log。这个只读诊断没有经过产品API，也没有文档影响或部署语义，不代替自动协调验收。

真实失败输入回放3通过、0跳过（0.39秒）；随后新协议、实际MD/DOCX/PDF×两适配器、固定读取、候选核验、持久响应/恢复、协调及旧输入回归 **102通过、0跳过（389.54秒）**；MD正式生产API、独立解析/比较/影响CLI、实际MySQL/PG/Redis/RustFS及HTTP **1通过、0跳过（68.54秒）**。分别保存p4-citation-basis-replay/live/production的XML/log及目录，3项回放包含在102项内，不累加为覆盖数。

离线核对正式生产6次物理HTTP消息等于对应完整冻结请求，均使用v3和给定版本记录哈希；结果仍partial、semantic pending_review、deployment unknown。真实引用、未读拒绝及历史恢复通过，不冒充默认模型语义通过。公开API/前端契约、依赖与数据库Schema未改，不重跑前端构建/浏览器或执行生产迁移。完整P0—P5及需求评审/增量/矩阵仍未完成，继续按原目标推进。

### 6.5 真实默认模型自动流程〔研究基线770f7159〕

既有自动工作流与共享租约Handler使用实际Markdown V1/V2、三个完整8步JSON及其固定来源，在隔离MySQL/PG和实际RustFS运行。真实默认模型、8192输出、SDK60秒和单调用120秒保持原值；只有隔离项目任务总期限为600秒，不能当成默认120秒策略的验收。没有替换提供商或预填模型答案。

实际功能测试 **1失败、0跳过（607.72秒）**：6次逻辑调用、11次物理POST，取得2份完整响应；首份补读因单元ID误写被拒绝，另一份补读合法并自动续读。最终1个单元rejected、4个unknown、6个因task_deadline未处理，规则0，完整11单元均保留在partial快照。三个用例没有进入实际模型判断，不能评价影响/遗漏/误报或将规则0当作无变化。任务completed仅表示部分结果持久发布；semantic pending_review、deployment unknown。

失败断言发生在业务全范围验收。此前同一测试已实际核对PG/RustFS结果、固定范围、终态不重领、终态重投及历史读取不发送模型、不重建输入或上传。源默认模型参数前后一致。失败XML/log、全部实际请求/响应/核验/清单/原件和对象原字节分别保留在p4-default-model-automatic-first.xml/.log/目录，不覆写此前失败。新增设计见3.5.9；本轮没有实施新响应或协调协议，也没有通过忽略核验修正误写ID。

既有真实失败输入兼容回放另 **3通过、0跳过（0.36秒）**，证据p4-default-model-automatic-replay.xml/.log。不新增依赖、公开API、前端契约或数据库Schema，本轮没有重跑前端/浏览器、独立生产Worker或生产迁移。新的自动流程测试本身使用同进程Handler，不冒充独立进程恢复。完整P0—P5仍未完成，按新设计继续查证与实施；原未知调用不自动重发。

### 6.6 v4固定身份绑定及真实模型复验〔研究基线3d71c589〕

初始离线回放21通过/6跳过（3.50秒）；6跳过是未启用实际存储/HTTP开关。启用后相关6模块 **115通过、0跳过（399.76秒）**，包括旧v3专项、新24项、原始失败请求回放，以及租约/未知/恢复回归。补核验日志后当前源码定向 **27通过、0跳过（46.76秒）**；27属于上述功能范围，不累计为额外覆盖。6个新实际场景为MD/DOCX/PDF×原生/LangChain，验证默认v4短引用、自动补读、完整12步用例、False/0/null、规则R1还原和终态只读。

正式生产API/独立解析、比较、影响CLI，隔离MySQL/PG/Redis及实际RustFS/HTTP，首先1通过0跳过60.78秒；补日志后最终源码 **1通过、0跳过（54.12秒）**。以p4-bound-protocol-production-final.xml/.log/目录为最终证据，最初记录保留。6次实际物理请求均v4，原始请求、响应/核验和对象分别保存，结果仍保留partial/pending_review/deployment unknown，不将受控提供商当默认模型语义。

真实默认模型新任务 **1失败、0跳过（607.68秒）**，Handler运行600.953秒，7逻辑调用/11物理发送。2份完整v2响应均通过read_requested和impact-bindings-v1；后续仍超时，最终5unknown/6unprocessed，三个完整8步用例未进入模型判断，规则0不能解释为无变化。SDK60秒、单调用120秒、8192输出及重试未改；600秒只写隔离项目MySQL，源默认参数前后一致。完整11单元、原wire响应、规范核验、unknown和原始对象保留，终态查询/重投不发模型，不重领。证据为p4-default-model-bound-v4.xml/.log/目录，与之前失败分别保留。

本轮新增内部wire契约，现有seed协议版本增加v4，公开路由/结果字段和前端通用seed类型兼容；无依赖升级、SQL表/列新增或生产历史迁移。未重跑前端构建/浏览器（前端契约/页面未改）。原始字节、冻结消息/引用、旧请求兼容、源码及三份方案同步核对见p4-bound-protocol-validation.json。完整P0—P5仍未完成；下一步按3.5.9研究任务划分、阶段预算及已有流式机制，避免原样重复同一失败流程。

### 6.7 实际流式、分阶段及推理参数诊断〔研究基线f8d2f34e〕

前一实现已提交f8d2f34e，345份原始证据暂存字节与本地SHA逐个相同、362个文件范围检查及git diff --cached --check通过，用户.gitignore保留。115回归、最终27定向子集和独立生产1通过的结论保留；真实默认全流程1失败也保留，不能改写为成功。

本次新增诊断使用实际新建原件、解析/差异/完整用例、隔离MySQL/PG和实际RustFS，全部使用真实默认提供商。源模型、SDK60秒、输出8192、单调用120秒不变，只有隔离项目总期限600秒：

| 新诊断 | 实际结果与限制 |
|---|---|
| 完整依据单元 + 既有流式 | 1失败/0跳过127.79秒；一次56250字节POST，HTTP200响应头2.218秒；调用120.094秒、正文0、dispatch_TimeoutError/outcome_unknown。没有采集SSE原字节，不能断言没有网络数据或推理片段。证据p4-default-model-stream-diagnostic-first |
| 规则发现与用例阶段分离 + 原始SSE | 1失败/0跳过117.63秒；发现调用111.375秒，一次33115字节POST；SSE1460607字节/3116个JSON事件，reasoning单别名29772字符；length、completion_tokens8192、正文0；response_saved/truncated及ineligible。用例阶段0调用。证据p4-default-model-stream-stages-first |
| 另建任务 + MySQL隔离low诊断参数 | 1失败/0跳过124.85秒；发现调用118.594秒，一次33140字节POST；SSE1843087字节/3971个JSON事件，reasoning单别名28165字符；仍length/8192/正文0及truncated/ineligible，用例阶段0调用。正式seed没有该参数，实际wire/隔离MySQL修订单存；没有修改源模型。证据p4-default-model-stream-low-first |

三个XML/log/目录分别保留，失败发生在要求完整合法结果的断言；不删除断言或将截断文字当响应。第二/第三次的1261/1680个网络片段逐个核对大小、SHA和顺序，拼接字节分别等于原SSE；reasoning与reasoning_content一致，不双倍累计。最后usage的reasoning_tokens=0与实际reasoning片段矛盾，原usage不修改。没有语义通过、自动完整产品或独立Worker结论。

普通/流式差异、实际消息字段大小、完整资料清单及原始SSE汇总见p4-stream-research-sizing.json、p4-stream-sse-observation.json；字节、消息、存储对象和三份方案核对见p4-stream-research-validation.json。官方Step 5模型及Chat Completions文档用于查证参数，不把其他模型参数套用；具体链接及采用边界在设计记录。

本轮只有研究方案、诊断测试及原始证据，尚未把流式或reasoning_effort加入正式业务实现，没有SQL迁移、依赖升级或前端契约变更，未重跑前端/浏览器和生产历史迁移。完整P0—P5继续；按3.5.11转向证据与推理任务职责、引用位置绑定及预算契约，不能仅增大超时或重复原样调用。

### 6.8 原文引用锚点v5与真实默认流程〔研究基线1d42f7ce〕

先研究固定原文读取、解析块/差异范围、响应核验、旧协议及实际失败输入，再写设计和实现。新任务用T引用选择已读原文，后端绑定准确位置/quote/hash；完整用例和原文保留，旧v4实际请求及协议hash重建一致。unknown和旧终态不回写、不重发。

最初离线回放 **32通过、12跳过（4.10秒）**；12跳过为两组实际存储开关未启用。启用后7模块 **132通过、0跳过（441.18秒）**，覆盖MD/DOCX/PDF×两适配器、HTTP/MySQL/PG/RustFS、自动补读、规则传递、完整JSON及False/0/null、未知/恢复和终态历史。随后补两个真实固定输入边界，定向 **13通过、6跳过（3.70秒）**；6跳过是此定向命令未启用live，相关六场景已在132项内实际通过。13项与前一批重叠，不累计为145覆盖。两个新增边界证明局部/相交块只引用实际读到的切片，两个版本相同文字不合并来源。

独立生产API、解析/比较/影响CLI、Outbox relay及实际MySQL/PG/Redis/RustFS/HTTP，MD原生正常场景 **1通过、0跳过（59.26秒）**。6次物理HTTP消息逐条等于冻结请求，均v5；原对象大小/SHA、引用目录及终态历史核对。这次没有杀死Worker，不冒充进程中断恢复专项。受控响应的结果仍可保留partial/pending_review/deployment unknown，不代表默认模型语义成功。

真实默认模型新任务 **1失败、0跳过（608.01秒）**，Handler实际601.219秒，10次逻辑调用、13次物理POST；8份完整响应通过v5引用/事实核验，其中4次read_requested、4次unit_finished。最终11单元全部保留：**4 analyzed、2 unknown、5 unprocessed**，结果partial；三个完整8步用例实际调用为0。规则共10个候选（modified 3、removed 3、added 4），来自重复证据单元的独立建议，不表示10条不同需求。实际建议包含保留期7→9天、删除优惠码、新增倒序导出，与文件变化一致；尚未完成完整用例影响判断和正式评审，不能报告完整业务通过。

源默认模型、SDK60秒、单调用120秒及8192输出保持不变；600秒任务总期限仅在隔离项目MySQL。首次20,780字节请求只读126字符的旧文档；固定范围含2原文单元、6差异单元、3用例，暴露证据组织与推理任务分工的负担。统计见p4-text-anchors-request-sizing.json；静态指令也有“同时读三方”与“仅当前发现单元”的阶段协调问题，仍需研究，不能直接归因全部超时。下一步按3.5.11/3.5.12研究任务聚合与阶段契约，不原样重复本次流程、暗增全局预算或复制一份答案给多个单元。

终态重投/读取不发送HTTP、不重建输入、不上传，不重领；源模型前后参数相同。原unknown、失败XML/log、wire响应、绑定后核验、原件与对象全部分别保存，证据为p4-default-model-text-v5-first及p4-text-anchors-*。此前345份v4和85份流式原始证据核对未变，用户.gitignore未改。当前源码py_compile、引用消费者搜索及diff检查通过；三份方案字节相同和原件/对象/冻结消息核对另记p4-text-anchors-validation.json。

没有公开API/前端结果契约、依赖或SQL表/列变更，未重跑前端构建/浏览器，未执行生产历史迁移。完整P0—P5仍未完成；需求评审/矩阵/增量、P2执行补全、P1正式模板/导入与模块生成、P0/P5人工录制/工具证据等继续保留，不因本轮协议验证删除范围。


### 6.9 固定推理计划基础及真实功能验证〔研究基线a0610ba3〕

先查逐证据调用、精确候选hash、固定清单/读取、MySQL配置冻结及PG租约/对象检查点，再实施独立计划Schema、确定性分组、计划服务和PG表。新提交冻结reasoning_settings，旧seed缺少该字段仍可读；不从当前配置补造历史策略。计划读取核对固定输入/清单/配置及RustFS对象SHA/大小，恢复先读检查点；源文件及相关旧协议回归已验。

最初离线回放 **5失败、14通过、11跳过（5.38秒）**：把仅接受Pydantic记录的canonical_impact_json/impact_record_hash用于dict，属于实现错误。查已有字典序列化后改为同样规范JSON方式；复验 **19通过、11跳过（3.89秒）**。11跳过是未开启真实存储开关，不隐去失败。6项新离线检查用原v5失败输入证明2原文+6差异仍全部保留，默认可为1组，成员限制2为4组，字节限制1保留8个明确超限单项；完整8步JSON、False/0/null、自身说明、原来源和读取超限不丢失。

启用实际存储后8模块回归 **145通过、0跳过（460.29秒）**，包含MD/DOCX/PDF原件→V1/V2→解析/比较→提交→计划保存，真实MySQL策略/PG检查点/RustFS对象，配置修改、新用例版本及归档后恢复，实际PG提交成功回执丢失，并发恢复读取、项目隔离、配置hash篡改、对象篡改和过期租约。后来新增的两项分别定向运行：**4路并发首次准备1通过（8.04秒）**，核对只有1份PG计划；**多分段及缺失人工来源1通过（6.73秒）**，核对全部证据、12步完整用例和non_document_source缺口。两项不在145收集内；记录各自范围，不把重叠回归累加为总覆盖。

独立生产API、解析/比较/影响CLI与真实MySQL/PG/Redis/RustFS/HTTP的MD原生正常场景 **1通过、0跳过（60.02秒）**，证明增加表/seed/config后既有v5自动流程仍可工作。它仍走coordinator-v1，没有调用新计划服务，不证明分组协调器已接或默认模型完整分析可用。此次不重复运行未改动的默认模型全流程；6.8失败、unknown和未处理用例保留。

计划专项保存actual_http_calls=0、analysis_status=not_started及full_model_workflow=False；没有把计划保存记为AI业务成功。证据为p4-reasoning-plan-replay-first/fixed、live-first、race-first、segments-first、production-first的XML/log及实际目录。源文件编译与diff检查通过；三份方案、原件、计划检查点、生产冻结消息和旧证据hash另见p4-reasoning-plan-validation.json。新增表实际初始化已验，生产历史迁移没有执行；无依赖变更，公开路由/前端类型未改，未重跑前端/浏览器。

下一步接版本化的分组协调器、逐证据处理响应及阶段Skill，再验真实默认模型完整规则与3条用例；同步设置API/页面与覆盖呈现。完整P0—P5及需求评审/增量/矩阵、P2准备与执行、P1正式生成导入、P0/P5人工录制/Docker/报告和迁移继续推进。

### 6.10 分组生产协调与真实默认模型〔研究基线f5705aef〕

先查v1结果/原核验、冻结计划、三方读取、租约与未知发送、Skill和前端消费方，再实现v2分组任务/逐证据结果、v6阶段wire/v2核验、生产调度及完整来源预读、项目设置API和前端。没有增加依赖或本轮新SQL表；使用上轮计划表。新默认协议按seed冻结，历史v1/v3/v4/v5读取和各自核验保留。

初次回放 **6失败、19通过、21跳过（6.64秒）**，原因是测试把CallInput误当dict拆解；修正 **25通过、21跳过（4.84秒）**，跳过为未启用真实环境开关。初次真实专项 **7失败、7通过（60.52秒）**，受控供应商夹具漏考虑完整JSON的未知字段，服务器断言引发SDK真实重试；按完整原JSON核对后 **14通过、0跳过（52.67秒）**。边界首轮 **1失败、3通过（25.99秒）**，HTTP500应是供应商失败ineligible，并非未知发送；纠正测试分类，另用真正的在途取消验证unknown，失败记录保留。

九模块实际文件/MySQL/PG/Redis/RustFS/HTTP回归 **166通过、0跳过（576.99秒）**，含MD/DOCX/PDF×原生/LangChain、完整12步JSON与原来源、八证据组、逐成员遗漏/重复/外项目/重排、成员依据不足、多组/超限保留、供应商失败、PG结果丢回执、HTTP在途取消及终态恢复。组候选只保存一次，成员保留各自声明，不以减少组数降低完整范围统计。

生产首轮 **1失败（30.77秒）** 是测试仍要求旧v1；随后更新断言引入缩进错误，收集 **1错误（0.26秒）**，修正独立生产 **1通过（53.61秒）**。加入正式reasoning设置API/真实MySQL及在途配置冻结后，MD正常、DOCX/LangChain正常、MD真实杀进程恢复共 **3通过、0跳过（186.01秒）**；正常各2次HTTP，恢复场景unknown不重发。完整正式App/Chromium、DOCX、任务池、提交丢回执、失败报告、历史下载和实际服务重启 **1通过（86.35秒）**；页面保存分组设置、显示推理组及逐证据理由，模型为受控HTTP。前端 **24通过**，`npm run build`含vue-tsc/Vite通过，不能替代真实默认AI语义验收。

真实默认模型首次自动流程 **1失败、0跳过（291.49秒）**，Handler284.390秒，**4逻辑调用、6物理POST**；发现组响应返回3项真实变化和8项覆盖声明，但unit_outcome=null，模型Schema允许而canonical结束规则禁止。最终保留11项：**8 rejected、1 unknown、2 evidence_insufficient**，合法规则0，结果partial。三个完整8步用例都进入模型调用；它们没有合法规则，不能报完整用例影响已判定。发现响应实际包含7→9天、删除优惠码和新增倒序导出；正文只作待评审诊断，不绕过原核验采纳。

修正该契约后，新增实际失败响应回归及模型Schema/提示说明；受影响三模块 **63通过、0跳过（174.49秒）**，最终独立生产MD **1通过（58.60秒）**。两次命令入口误写测试文件/函数导致没有运行测试，原错误日志保留，后按真实文件/函数重新运行；不计为功能失败或通过。源码18文件AST/字节编译、跨层引用搜索及diff检查通过。

修正后另建真实默认任务 **1失败、0跳过（313.21秒）**，Handler306.656秒，仍**4逻辑调用、6物理POST**。发现请求60.156秒ReadTimeout后SDK重试，逻辑120秒截止取消第二次发送，保存unknown；三条完整8步用例实际送达，返回依据不足均通过结构核验。最终11项为**8 unknown、3 evidence_insufficient**，规则0、partial、业务pending_review、部署unknown。源step-5-preview、8192输出、SDK60秒及调用120秒保持不变；终态查询不新增HTTP，不可重领。Schema/提示已对齐，但本次发现超时，未获得真实模型成功的发现响应，因此不能声称完整语义或超时根因已修复。

全部原始失败/成功XML、日志、原件、请求/响应、PG检查点和RustFS对象保留于`p4-grouped-*`、`p4-default-model-grouped-v6-first*`、`p4-default-model-grouped-v6-contract*`；字节/消息/旧证据/方案一致性见`p4-grouped-validation.json`。完整P0—P5仍未完成，未执行生产历史迁移；后续先研究实际传输/推理耗时与输出预算，不再原样重复失败默认任务，然后完成需求评审、矩阵、增量草稿和其他原定功能。

### 6.11 项目SDK策略与实际单次发送〔研究基线845287bb〕

先查上一轮实际60秒超时、重复发送及120秒取消，再读取Runtime/工厂/适配器、本地安装签名、官方SDK与LangChain文档及项目Agent轨迹课程。沿用既有MySQL调用设置和固定任务依据，增加可选single_attempt、Runtime不可变选项及正式页面；不改ModelInvocationRequest，不升级依赖、不增加表，原provider_defaults和历史读取保留。范围/依据见3.5.15及P4_TRANSPORT_BUDGET_DESIGN.md。

本轮模型客户端/适配器原回归 **26通过、0跳过（2.89秒）**；新增真实功能 **17通过、0跳过（71.37秒）**，覆盖旧四份请求hash、三种原生构造参数及Google提交前拒绝，MD/DOCX/PDF×Chat原生/LangChain六组完整流程，以及两适配器的HTTP500、断连接和调用取消。六组实际慢响应0.7秒，原全局SDK配置0.25秒，冻结调用4秒/重试0后每组两次HTTP完成；配置修改后仍使用原冻结选项。每条完整12步JSON、未知字段和当时来源保留。错误场景每条一次HTTP；无状态传输/取消为unknown，真实500为provider_failure；新租约恢复不再次发送。Responses/Anthropic本轮只有工厂参数核对，没有对应真实HTTP验收，不计入六组。

最终九模块实际回归 **156通过、0跳过（529.86秒）**。独立生产API、解析/比较/影响CLI与真实MySQL/PG/Redis/RustFS/HTTP **5通过、0跳过（294.62秒）**：MD/DOCX/PDF原生正常、DOCX LangChain正常，以及MD实际杀进程恢复。正式App/Chromium/DOCX/API/独立CLI/真实存储 **1通过、0跳过（85.06秒）**，页面选择单次策略，API/MySQL保存及任务冻结、历史读取与恢复已验；模型是受控供应商，不能当作默认模型语义成功。

前端两文件专项 **24通过**；第一次build实际失败为测试中Element.value缺少类型，按已有DOM类型修正为HTMLSelectElement，未改功能断言。修正后vue-tsc/Vite构建通过（Vite6.67秒），原失败日志保留。

真实默认模型另建任务，严格完整业务断言 **1失败、0跳过（468.08秒）**，Handler460.515秒。源step-5-preview、8192输出、全局SDK60秒、逻辑调用120秒及隔离任务600秒未改；此次项目单次策略冻结SDK120秒/重试0，实际每个POST的connect/read/write/pool均120秒。**5逻辑调用/5物理POST**，不是整个系统无重复发送的证明。

发现73.063秒取得响应，通过核验的三项规则为保留期7→9天、删除促销条款、新增导出倒序；八条证据声明已接受，业务仍pending_review。保留期用例119.906秒取得响应但invalid_case_citation拒绝；原引用content_json路径title不存在，标题实际是case元数据，不能自动把错误路径修正为真实依据。促销用例23.437秒请求补读其他两条固定用例，后续独立请求109708字节/4消息，120秒取消unknown；未鉴权用例52429字节/2消息120秒取消unknown。三条完整8步用例均实际发送；最终11项 **8 analyzed、1 rejected、2 unknown**，规则3，partial/pending_review/deployment unknown。结构接受不等于业务评审通过，任务completed不等于所有分析成功。

终态读取/重投不发送、不重建输入、不上传、不可重领；源模型前后参数相同。新原件、HTTP、请求/响应/核验、PG检查点、RustFS对象、构建失败及XML/log保存于p4-transport-*和p4-default-model-single-attempt-first*。原345+85+328+257+723份证据不改写，三份方案和消息/对象字节核对见p4-transport-validation.json。未执行生产历史迁移；完整P0—P5、需求评审/矩阵/增量、准备状态/未知副作用、正式生成导入、人工录制/Docker/报告继续保持未完成。下一步查证真实引用与补读阶段边界，不原样重跑已失败默认任务。

### 6.12 完整用例字段引用、阶段缺口与真实模型〔研究基线6f4bb0ea〕

先读取实际invalid_case_citation响应及完整JSON/元数据边界、原T绑定、literal路径/value核验、固定读取/补读、PG核验版本选择及所有API/前端消费方，再新增F完整节点索引、wire v5/call v7、validator v3和版本化读取。仍使用coordinator-v2/canonical-v2，不增加SQL表或依赖；MySQL设置/PG业务检查点/RustFS对象职责不变。依据见3.5.16及P4_CASE_FIELD_REFERENCES_DESIGN.md。

最初离线 **8失败、22通过、25跳过（9.50秒）**，原因是F索引漏掉真实响应已使用的合法expected容器引用。保留原canonical全部节点能力后 **30通过、25跳过（5.00秒）**；跳过为真实环境开关未开启，首失败不抹去。新实际专项 **16通过、0跳过（43.61秒）**，覆盖真实v6错误仍拒绝/原请求不变、F路径/值/说明绑定、未知/外用例/伪造目录或正文拒绝、任意路径/false/0/null/空值/重复与补读编号、MD/DOCX/PDF×两适配器完整12步JSON、原来源、两次真实HTTP和终态历史不重发。

描述澄清前11模块回归 **200通过、0跳过（683.65秒）**。独立生产API/解析/比较/影响CLI及真实MySQL/PG/Redis/RustFS/HTTP **5通过、0跳过（288.04秒）**，包含MD/DOCX/PDF原生、DOCX LangChain及MD实际杀进程恢复。正式App/Chromium/DOCX/API/独立CLI/真实存储 **1通过、0跳过（88.67秒）**；前端专项 **24通过**，vue-tsc/Vite构建通过（8.43秒）。这些受控供应商结果与真实默认模型分别记录。

首次v7实际默认新任务严格断言 **1失败、0跳过（207.39秒）**，Handler200.015秒，4逻辑/4物理，全部四条HTTP200。发现19.656秒、三用例51.531/68.579/56.187秒；11项均analyzed，3规则/9判断与样本吻合，旧title错误未再出现。但模型把部署未知/仅停用候选/其他任务等说明写入unresolved，严格完整验收仍失败；另有系统原文位置警告，快照partial。原响应/unresolved/失败XML和九判断核对p4-case-anchors-semantic-first.json保留，不删除说明或放宽断言。

查证unresolved缺少字段职责描述，按新提交冻结字段描述与阶段指引：仅记录阻止当前阶段判断的具体缺失证据，部署/评审等仍在既有状态及summary/reason/uncertainties中保存，真缺口照常报告。原Schema/指引/hash保留在首轮seed，不原地替换。最终受影响v7专项 **18通过、0跳过（44.19秒）**，包括首轮v7全部请求按原冻结指引重建及实际九判断回放；最终独立生产MD **1通过（58.86秒）**。前述200/5/正式App在描述澄清前，最后18/1及后述新默认任务验证澄清后源码；公开字段结构及前端未再改。

最新真实默认模型新任务严格验收 **1通过、0跳过（329.88秒）**，Handler322.734秒、**4逻辑/4物理POST**，SDK实际120秒/重试0。发现61.797秒、三用例100.687/116.813/38.844秒；未补读其他用例，全部三条完整8步JSON实际送达。八证据与三用例共11项analyzed，reason_codes/unresolved均空，三规则为7→9天、删除优惠码、新增倒序导出。独立读取原件和canonical结果核对9判断：保留期变更使retention重大影响，优惠码删除使promotion重大影响，未登录401保持负向条件，导出规则没有被误判为三个现有用例已覆盖，其余既有用例关系none。

随后将九项固定样本业务基线加入以后默认集成验收，使用这次实际保存的完整结果运行加强基线回放 **1通过（4.68秒）、0新增HTTP**；没有原样重复发送模型。最新默认XML是在新增该业务函数前执行，实际结果的独立核对及回放另记p4-case-anchors-semantic-stage.json和p4-case-anchors-business-replay.xml。样本符合不代表用户正式评审已通过。

系统仍保存difference_warning:extracted_text_positions，因此快照status为partial，semantic_status=pending_review、deployment_status=unknown；不隐藏警告、不断言已经上线或执行测试。终态读取/重投不调用HTTP/重建输入/上传，不可重领。源默认step-5-preview/8192/globalSDK60/call120/isolatedTask600前后一致，新项目单次选项是原策略，未继续调整全局参数。

全部原始原件/对象/HTTP/响应/核验/失败XML及日志保留于p4-case-anchors-*和p4-default-model-case-v7-*；三份方案、原始字节、版本hash/F映射、实际物理消息对冻结请求、旧2260份证据及用户.gitignore核对见p4-case-anchors-validation.json。无生产历史迁移；完整P0—P5、稳定需求身份/评审/矩阵/增量与部署适用、P2准备/未知副作用、P1正式生成导入、人工录制/Docker/报告继续未完成。下一步以这些可评审候选接稳定需求身份、确认关系、验证点和追踪，不把文档来源数量直接当需求覆盖。

### 6.13 正式需求身份、评审事务与设计矩阵〔研究基线6abe525f〕

先查不可变影响规则/逐用例判断、完整快照核验、case逐版本评审、PG事务和锁、API错误恢复及本地治理课程，再接三张PG表、需求/版本/验证点、明确接受/拒绝/暂缓、原请求恢复、设计矩阵及生产初始化/API/前端DTO客户端。没有新依赖或MySQL配置；规则hash、原文标题和来源不自动变成需求身份。细节见3.6.1及P4_REQUIREMENT_REVIEW_DESIGN.md。

首轮真实文件/存储/API专项 **3失败、5通过、0跳过（37.69秒）**，失败是测试把身份当前指针也当成历史正文不变量；V3应更新current_version_id，V1正文/关系保持不变。修正该断言并补拒绝/暂缓、首次四路并发、移除后验证点身份恢复和真实PG正文损坏检查，再连同既有完整字段引用专项 **28通过、0跳过（91.34秒）**：新增需求模块9项，既有字段引用19项，不与此前200项累加。

MD/DOCX/PDF实际上传、解析/比较进程、两次受控HTTP影响、真实MySQL/PG/RustFS后，通过实际ASGI路由确认固定rule/用例判断/核验来源、V1/V2/V3、稳定验证点、明确覆盖/未覆盖/待复核、指定历史版本、父版本冲突、移除保留旧用例启用指针、原影响对象不改写。实际PG提交后丢回执503，四路原请求恢复同决定；不同新请求竞争同父版本只有一个成功。首次四路同请求只产生一个版本，拒绝/暂缓不创建需求；移除后恢复保留验证点UUID；实际损坏正文被hash拒绝并恢复原孤立测试记录。归档后历史查询/原请求重放不读对象/模型/配置，新请求拒绝。

独立生产API、解析/比较/影响CLI和真实存储MD原场景 **1通过（71.37秒）**，证明新增生产初始化兼容。新增实际HTTP需求评审/矩阵及API重启恢复测试首次收集 **1错误（0.33秒）**，我漏写测试列表结束括号；修正后 **1通过、0跳过（61.59秒）**。新生产API提交决定后，关闭派发并重启API，原请求重放/查询、需求/版本/矩阵均恢复原记录，HTTP模型仍只有原两次；新需求不是依赖API进程内存的记录。两场景分别保存，失败收集不算功能通过。

前端客户端与既有影响客户端共 **6通过**，vue-tsc/Vite构建通过（8.89秒）。本轮未接正式需求评审/矩阵页面、未跑新的浏览器流程，没有再次发送外部默认模型；受控判断和测试评审请求不冒充用户真实业务审批。execution_status=not_assessed、deployment_status=unknown明确保留；需求接受并不自动证明旧case能验证新预期。

末尾补需求分页的project/document/id索引，使用短表名hash避免配置长标识截断。最终源码新增需求模块 **9通过、0跳过（39.44秒）**，独立生产HTTP评审/重启恢复 **1通过、0跳过（56.39秒）**；前述28与首生产记录在索引加入前，重叠结果不累计为新覆盖。最终源文件AST/编译、diff与证据hash另记validation；不是只用编译代替这些功能实测。

原始XML/log、实际文件、决定/版本/矩阵、物理请求、独立进程和对象证据见p4-requirement-reviews-*，hash/原请求/来源/历史证据/三份方案核对见p4-requirement-reviews-validation.json。三表启动初始化实测，未自动迁移/回填历史需求，无既有表/版本正文改写。完整P0—P5继续未完成；下一步完成正式评审/矩阵App和确认范围增量生成，再接运行证据、部署适用与其执行约束，并继续P2/P1/P0/P5剩余项。

### 6.14 正式需求评审与矩阵App〔研究基线fbf8bffb〕

先核对需求API/不可变版本/稳定点、评审分页升序与锁内最新决定、原影响展示组件和既有sessionStorage恢复机制，再实施页面。依据见P4_REQUIREMENT_PRODUCT_DESIGN.md。正式影响结果显式打开评审后才读取需求服务，旧纯展示调用不产生网络请求；前端只提交明确规则、正文/点/关联和预期父版本，继续使用原MySQL配置、PG业务及RustFS对象，没有新增依赖、配置、表或模型调用。

新增接受/拒绝/暂缓、已有需求/新建、验证点与固定用例版本多选、完整评审历史、指定版本矩阵；设计覆盖/未覆盖/待复核、执行未关联/部署未知分别展示。请求先冻结再提交，网络/503及404查询保留原编号/参数；实际提交丢响应刷新可找回。后端明确409时保留失败请求，用户显式重新读取才开始新评审，不自动重定父版本重发。复查进一步固定表单编辑父版本，普通目录刷新不把旧正文自动改挂最新父版本；项目/文档/快照晚响应与矩阵修订晚响应均不显示。

组件与既有影响/API客户端首轮 **1失败、37通过（2.40秒）**：我把创建V2前保存的V1矩阵当成创建V2后的历史读取。纠正断言，保持真实记录当时的current_version_id，再运行 **38通过（1.86秒）**；不是生产逻辑失败或改写旧证据。补刷新不自动重定父版本和迟到矩阵回归后，最终 **40通过（2.06秒）**。初次vue-tsc/Vite构建通过（8.58秒）；实际浏览器流程各自重新构建，首次9.51秒，最终10.49秒。

首次完整正式App **1通过、0跳过（96.96秒）**，真实DOCX原件/解析/差异、独立API/三类Worker、MySQL/PG/RustFS/Redis后实际操作接受、丢已提交响应、刷新按原请求恢复、V2不复制旧覆盖、V1历史、拒绝/暂缓、移除以及API重启/关闭派发/归档后的原请求与历史。保存5份评审、3份需求版本；模型物理POST仍仅原影响2次。V1正文/验证点/关联固定，身份当前指针正确更新。

固定编辑父版本后增加真实竞争API评审，最终完整App **1通过、0跳过（107.54秒）**。竞争决定保存后，旧表单实际提交409；页面没有自动重发，需求仍2个版本，明确重读并确认后再评审。最终6份评审/3份版本与PG原记录逐项一致；稳定验证点保留，新版本无继承关联显示待复核，旧版本查询及归档原请求恢复均成功。原影响全文和下载、终态任务保留，页面无pageerror，1440视口正文宽1440。该最终流程包括原影响失败任务/任务池/下载/重启场景，未缩减成组件或ASGI验收。

实际原件、请求、决定/版本/矩阵、PG、对象、调用消息、截图、失败/通过log/XML在p4-requirement-product-*。历史原始证据不改写，三份方案同步及hash核对见p4-requirement-product-validation.json。只使用受控HTTP及测试评审，不冒充真实用户业务审批或新的外部默认模型语义验证。大量历史记录的生产规模、生产历史迁移、增量草稿、运行证据和环境生效仍待验；完整P0—P5保持未完成。下一步先研究确认需求范围如何进入现有生成任务与版本治理，再实现增量草稿，继续其余P2/P1/P0/P5及迁移范围。

### 6.15 确认范围与冻结生成基础〔研究基线e88add3a〕

先核对原生成入口读取当前预览/Skill及只能批量新建、需求版本原review_id/请求hash、固定影响输入读取、非敏感模型白名单/SDK参数守卫、完整Skill捕获、导入事务和规划调用检查点，再抽出共用请求构造及冻结范围。依据见3.6.3和P4_INCREMENTAL_GENERATION_DESIGN.md。原生成路径、用例模式和启用版本保留；新增内部范围/模块/生成请求Schema和服务，不增加公开路由、生产任务调度、配置键、表/列或依赖。

旧生成/模板首轮 **7通过、3跳过（7.89秒）**，跳过为实际存储和默认模型开关未启用。新增首轮 **5失败、4通过（58.63秒）**：测试上传新版本漏传expected_parent_version_id、使用MySQL保存时不生效的temperature字段验证变化，以及把启用后case与启用前快照比较。查实际DTO/模型配置/启用记录后修正；第二轮 **3失败、6通过（59.54秒）**，误把create_version返回的版本记录当bundle读取version.version。两轮测试错误及原日志/XML保留；不是绕开失败场景，实际守卫改用已保存的max_output_tokens并核对数据库读取值。

修正后新范围/需求评审/旧生成/模板四模块回归 **31通过、1跳过（152.89秒）**；13项新范围、9项需求和10项旧生成/模板收集，唯一跳过为需要外部当前默认模型的旧生成测试。随后增加LangChain及真实对象损坏，最终新范围 **15通过、0跳过（107.77秒）**；含11项实际文件/存储/HTTP场景和4项范围输入校验，与前述13项重叠，不累计为46覆盖。MD/DOCX/PDF原生及MD LangChain生成均实际发送完整12步JSON、False/0/null/未知字段、自身说明、旧/新/差异及原来源；受控响应保留完整字段和确认验证点。实际RustFS保存范围/原响应并由新生成器读取，改MySQL模板、Skill文件、当前文档、用例新草稿及后续需求版本均不替换原请求，旧启用指针/版本正文保留。

实际模型参数或runtime适配器变化在HTTP前拒绝；外项目/非固定case基线和嵌套请求hash修改拒绝。实际PG评审请求及来源损坏、RustFS原输入字节损坏被对应hash/原来源核验拒绝，孤立测试记录和对象原字节已恢复。移除仅形成候选、不读生成配置、不发生成HTTP、不停用case。实际HTTP工具操作、截断、空响应、500四场景各一POST，完整原响应先写RustFS再解析拒绝；另基于实际保存响应验证超出范围、错误case_key和截断终止字段。最终这11场景生成13次受控物理POST；不是整个系统不重复发送或默认模型语义通过的证明。

独立生产API/解析/比较/影响CLI/真实MySQL/PG/Redis/RustFS及HTTP需求评审、关闭派发后API重启恢复 **1通过、0跳过（102.20秒）**，模型仍原影响两次POST。证明需求读取校验及生成请求抽取兼容现有生产路径；没有调用新增增量范围/任务，不能称为增量产品验收。生产未增加任务检查点，本轮RustFS范围与响应保存/重读为基础功能专项，不伪称完整任务或草稿恢复。受控模型、测试接受决定及候选不当作用户业务审批；来源位置警告和deployment unknown仍保留。

命令、实际原件、完整请求/响应/候选、PG来源/恢复、生产进程及各轮失败在p4-incremental-sources-*；新/旧证据hash、原请求、三份方案及源文件核对见p4-incremental-sources-validation.json。源码编译和diff检查另执行，未用语法检查替代上述实测。未重复外部默认模型、前端构建/浏览器或生产历史迁移：本轮未改前端/公开契约，新增基础未接页面。下一步实施PG持久模块/调用检查点、Worker及同事务草稿收据，再接正式增量页面；继续运行/环境、P2/P1/P0/P5及迁移，完整P0—P5未完成。

### 6.16 本次方案同步：上传可行性、执行补全与工作区状态

本节保留5e6cf20f方案同步时的状态；后续修复及内部模块最终验证见6.17。

本次只修订方案，没有修改或提交工作区在研业务代码。重新读取普通上传路由、上传/追加版本服务、原件对象保存、固定快照比较、独立Git导入、解析工厂和项目规划Prompt/查询/校验路径：普通上传不调用Git；上传先保存原件与版本，解析及比较各自提交任务；AI在当前项目读取完整用例并按必要条件来源补全，执行不读取生成模板。对应约定已更新第2.3、3.1、3.2节。

核对已有工作区增量模块任务实测记录：首轮日志报告**6通过、0失败、0跳过（82.42秒）**；扩大边界日志报告**8通过、1失败、0跳过（104.76秒）**。两个执行均已终止，本次未重跑。失败为测试查询`LIKE 'case-generation:%'`未参数化，触发psycopg占位符错误：`only '%s', '%b', '%t' are allowed as placeholders`。当前失败未修正复验，相关源文件、测试和原证据留给后续实现任务；不将它写成通过或借方案提交一并交付。

前三种正常文件场景使用真实MD/DOCX/PDF、MySQL/PG/RustFS、受控HTTP和同进程Redis Worker；其他恢复边界使用真实租约任务服务，不能统称全部为独立生产Worker验证。实际模块任务验证没有接正式增量API、生产CLI或页面，没有调用外部默认生成模型，也不能证明生成内容通过业务评审。工作区记录为`p4-generation-tasks-first.xml/.log`及`p4-generation-tasks-boundaries-first.xml/.log`，尚未作为已提交原始证据归档；既有已提交验证记录保留。

本次文档交付检查三份完整方案字节一致、代码引用存在、历史上传/Git验证XML计数及Git diff。未重新运行业务测试、前端构建或浏览器，因为本次变更只涉及方案；正式增量产品、运行/环境、其余P0—P5与生产历史迁移继续保留未完成状态。

### 6.17 持久增量模块、原响应恢复与草稿事务〔研究基线5e6cf20f〕

重新读取用户目标原附件和实际工作区，按[P4_INCREMENTAL_TASK_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_INCREMENTAL_TASK_DESIGN.md)核对共享文档任务/Outbox/Worker、导入批量事务、原case版本锁、固定需求评审和生成输入。新增MySQL任务策略，PG组/模块表及检查点，RustFS完整范围和原响应；没有增加依赖或将用例解释成模式参数。模板仍只参与生成。

原扩大测试8通过/1失败的LIKE百分号查询已改成项目已有参数绑定。另查到真正实现缺口：共享_owned核验租约，不包含原deadline，提交草稿等待case行锁后没有重查总期限。实际持有原case行锁、1秒总期限/30秒租约，原HTTP响应已保存后等待1.1秒，旧行为仍保存V2及完成收据；实测**1失败、9 deselected（23.62秒）**，原文件/范围/响应/完成收据保留。修正后在发送意图前及草稿事务开始/写入后用数据库时钟检查原总期限，最后同时fence租约；整笔回滚草稿及收据，原响应留存。

最终新增模块**14通过、0跳过（168.72秒）**，真实MD/DOCX/PDF、MySQL/PG/RustFS/HTTP，前三个正常场景走同进程Redis Worker；其他边界直接执行真实租约任务。覆盖更新/新建/移除、PG组/响应/草稿提交回执丢失后读取原对象/收据、真实在途取消unknown不重发、父版本冲突、四路提交及领取、独立模块失败、租约/总期限回滚、第二条编号冲突整批回滚、领取seed替换拒绝、模型变更前拒绝与范围/响应对象实际损坏后恢复。完整12步JSON及False/0/null/未知字段实际到达受控HTTP，正常草稿追加保持旧启用指针。后增加关闭派发场景**1通过、14 deselected（17.76秒）**：原提交/报告可读，新请求在捕获配置和保存对象前拒绝。当前15项均已实际验过，重叠运行不累计为新增覆盖。

共享解析/比较/逐版本评审/Excel及JSON导入四模块**49通过、0跳过（107.73秒）**；独立生产API/解析、比较、影响CLI/真实存储及需求评审重启、关闭派发历史恢复**1通过、0跳过（120.66秒）**。后者仍只有原影响2次POST，没有调用新增量生成模块，不能作为增量产品验收。其证据从独占测试临时目录复制原39份文件，未重写字节。新增SQL表尚未由正式启动入口初始化，配置服务只在专项初始化；这不等同部署已生效。

原失败、最终范围/响应对象、草稿收据、消息、实际原件与各轮log/XML保存在p4-generation-tasks-*，历史原始文件和三份计划按p4-generation-tasks-validation.json核对。旧证据泛写actual_module_worker只表示内部执行；新证据区分actual_leased_module/actual_redis_worker/independent_worker_process，不改旧字节。不重复外部默认模型、前端构建/浏览器或生产历史迁移，因为本阶段未改公开/前端契约。

下一步接正式增量API、独立生成Worker/CLI、前端DTO/客户端/页面和显式失败模块新任务重试，并做独立进程与完整App验收。用户业务评审、生成内容质量、精确Token、运行证据/环境适用性及其余P2/P1/P0/P5和生产迁移仍待完成；完整升级目标继续保持未完成。
### 6.18 增量生成正式入口与独立生产流程〔研究基线80dc609b〕

按3.6.5及P4_INCREMENTAL_ENTRY_DESIGN.md研究现有路由、初始化、共享Worker生命周期、固定需求和前端契约后实施。公开Schema用类型化收据表达草稿版本和停用目标，内部冻结范围/组/检查点hash保持原样。正式API与独立生成Worker共用装配；新增分页/按原请求/完整范围/原响应只读不重新捕获配置或当前文档。部署模板登记生成流及CLI，没有修改实际用户.env、增加依赖或另建权限/任务平台。

真实公共API及内部任务回归 **19通过、0跳过（178.72秒）**：4项新API场景和15项内部模块场景，使用真实MD/DOCX/PDF、MySQL/PG/RustFS及受控HTTP；正常API场景执行实际租约服务，不称为独立CLI。验证策略MySQL落库、固定提交/原请求、不同参数409、非法租约字段422、分页、跨项目404、完整scope/response、草稿及旧启用指针；实际PG提交回执丢失503后原请求恢复同组，不捕获新配置/来源。归档原请求/固定历史仍可读，新提交/设置写入拒绝。另补公共停用候选 **1通过、4 deselected（13.75秒）**：类型化收据没有草稿/响应，原case仍启用，不发送生成；范围没有模板/authoring，终态不可重领。

独立生产API、解析/比较/影响/生成CLI、Outbox/Redis和实际MySQL/PG/RustFS的三格式流程 **3通过、0跳过（253.67秒）**。每场景原影响2次POST、增量生成2次POST；完整12步JSON及False/0/null/未知字段实际到达提供商。按确认需求/明确V1基线追加同case的V3草稿并创建新case草稿，原V1正文与启用指针保留，新case没有启用指针。策略提交后改为1字节不替换原策略；每个完成模块终态重投通过独立CLI，没有第五次发送。重启API并关闭派发，原组/请求/范围/报告保持原样，新编号503。这个受控模型结果证明链路与固定依据，不能代替外部默认模型生成质量或用户评审。

既有独立生产需求评审/关闭派发恢复另 **1通过、0跳过（77.80秒）**，仍只有影响2次POST，不算新增量场景。启动该验证最初误用了WindowsApps的python别名，命令启动报`StandardOutputEncoding is only supported when standard output is redirected`，没有运行pytest；改用项目已记录的`.venv/Scripts/python.exe`后实测通过。未把空日志或外层返回码算为测试成功。

独立CLI另用未登记的流实际启动，退出码1、ValueError，资源初始化前拒绝；stdout/stderr及验证结果保留在p4-generation-entry-cli-boundary。交付校验脚本最初误读目标引用的version属性和构建命令文本；查实际Schema及package.json后修正，再通过全部对象、消息与证据核对，不是生产流程失败。

前端新客户端及既有需求/影响客户端 **8通过、3文件（326毫秒）**，vue-tsc/Vite构建通过（10.58秒）。没有浏览器页面验收、外部默认模型新调用或生产历史迁移；本阶段只接前端类型/客户端。原始文件、范围/原响应/草稿/收据、MySQL策略、物理HTTP、独立进程和log/XML保存于p4-generation-entry-*，新旧证据hash及三份方案核对见p4-generation-entry-validation.json。

正式增量页面、失败显式新任务重试、用户业务评审、精确Token、运行证据/部署适用、历史迁移及P2/P1/P0/P5剩余项继续实施；完整目标未完成。

### 6.19 正式增量App、历史下载与请求恢复〔研究基线51541987〕

按3.6.6研究实际需求评审/固定生成契约、请求缓存、完整用例、模式注册表、设置表单和正式浏览器流程后实施6份前端源/测试文件，并将旧正式产品测试扩成默认关闭的可选增量场景。完整目标原附件重新读取；没有新后端生产修改、依赖、SQL表/配置键、实际.env或用户.gitignore改动。新增实际功能设计见P4_INCREMENTAL_PRODUCT_DESIGN.md。

原失败保留：组件首轮1失败/51通过，原因是测试直接查找JSON转义后的文本，改为解析实际pre的完整对象。实际构建随后失败，暴露我使用了不存在的设置API导出名，宽松mock掩盖了错误；查实际客户端后修正并约束mock导出集合，类型检查/Vite通过12.33秒。最终独立浏览器每次重新构建最新App，不能用组件通过替代类型检查。

完整浏览器首轮 **1失败（150.75秒）**：增量正常、容量失败及停用候选均已完成，但旧影响失败场景的预算还按原V1/V2计算，新生成V3和新case后在提交阶段就拒绝。修正测试从实际PG当前版本查询计算原预算，保留捕获通过/准备失败的本来场景。第二轮 **1失败（152.05秒）** 发现真实页面缺陷：切到正常历史组后读取尚未结束，下载却导出之前的停用候选范围。保存的错误下载SHA与候选组一致，和正常组不同。修正页面切换期间的下载/操作状态、控制器下载保护和晚响应隔离，组件回归使用这个实际错误样本。

完整App第三轮 **1通过、0跳过（150.39秒）**，再复查本地请求边界：最大新建数量无效时旧控制器提前保存pending，虽然没有POST却显示未知；缺少crypto.randomUUID时无法提交。先以实际组件复现 **2失败/18通过（2.08秒）**，再改为校验通过后才保存pending、复用项目现有请求编号fallback。最终组件及旧需求/影响/生成客户端 **56通过、4文件（2.41秒）**；20项增量组件与旧36项回归，重叠轮次不累加。

最新交付浏览器 **1通过、0跳过（135.15秒）**，真实App类型/Vite构建8.61秒，浏览器实际移除randomUUID后通过fallback提交。真实DOCX、生产API与独立生成CLI形成正常2模块、失败1模块、停用候选1模块，共3组/4模块；原影响2次HTTP、增量2次HTTP，物理消息逐条匹配原冻结请求。PG原收据/任务状态、RustFS原范围与完整响应SHA/大小、下载字节、完整12步JSON/False/0/null/扩展字段与自身说明均核对。V1仍启用，同case的V3及新case保持草稿。MySQL当前容量改成1字节后原组仍使用原256000字节策略；另一个新组在发送前明确失败，原正常组不变。完成模块独立CLI重投没有第5次发送；停用候选没有生成响应或自动停用。

实际POST已提交再丢回执，刷新页面按原编号及完整原参数恢复同组；原scope/response下载一致。API重启并关闭派发、归档项目后正常/失败/停用历史可查，原请求可重放，新编号503。pageerror为空，1440宽无正文横向溢出，结果截图留存。另用默认关闭增量面板的旧正式需求App **1通过、0跳过（93.70秒）**，仍只有原影响2次HTTP，没有增量调用；共享失败预算和旧历史恢复继续通过。

实际文件、原物理请求、完整范围/响应对象、原PG收据、草稿、MySQL策略、失败下载及各轮log/XML/截图保存于p4-generation-product-*；旧4043份原文件与新证据、用户.gitignore、三份方案及暂存字节按p4-generation-product-validation.json核对。失败轮原文件保留，未把未到达的后续断言称为通过。命令见IMPLEMENTATION.md。

这些受控模型场景验证实际链路与固定依据，未调用外部默认生成模型，不代替用户业务评审；草稿生成成功不表示矩阵已正式关联、测试通过或需求已部署。失败显式新任务重试、任务池投影、草稿评审后的关系、精确Token、运行/环境、P2/P1/P0/P5剩余项及生产历史迁移仍未完成。完整目标继续保持未完成。

### 6.20 失败模块新任务与原响应复用〔研究基线12481ef2〕

上一轮12481ef2属于实际进展：正式增量页面、真实DOCX完整App和旧需求App兼容已验证。本轮重新读目标原附件、当前源文件、固定hash、共享租约/Outbox、Agent新回归和本地Agent课程幂等段，研究后采用3.6.7。修改5份后端生产文件、同步前端类型/客户端/现有mock及新客户端测试，并增加实际模块/API与独立生产流程；没有新依赖/MySQL配置键、实际.env或用户.gitignore改动。

首次真实新增模块 **7失败/2通过（77.65秒）**：我漏了内部GenerationTaskRecord到公开GenerationTaskView的显式转换，7项在新任务已保存后结果转换失败；不是未提交。原日志/XML保留，unknown拒绝路径与旧组hash读取通过。按既有公开结果方式转换完整model_dump后 **9通过、0跳过（74.21秒）**，实际MD/DOCX/PDF及MySQL/PG/RustFS/受控HTTP。之后新增真实父任务编号/模型改变、PG响应引用替换、RustFS原响应损坏与停用候选期限失败场景。

新增模块、原持久模块和公共API三模块回归 **33通过、0跳过（267.30秒）**，其中新增13项和旧20项。复查证据发现新helper把直接服务场景泛标为公共API，并与旧父冲突保存目录重名；原文件保留，旧混合目录不作为新增父冲突的完整依据。修正测试标记与独立目录，并将真实数据库重试提交回执丢失通过公共POST实际验证503，再按原编号恢复。标记/目录修正轮新增模块 **13通过、0跳过（104.70秒）**，与上述13项重叠，不累加。交付校验随后发现SQLAlchemy RowMapping被default=str写成字符串：测试中的SQL核对已通过，但保存文件不便按JSON验证。将SQL行明确转为dict并移除这份文件的字符串兜底后，最新交付轮 **13通过、0跳过（111.91秒）**，原旧文件保留；最新SQL字段/Outbox证据使用delivery目录。

最新范围包括旧组hash、3格式发送前预算失败/新策略/原完整消息/草稿，持久响应超过原预算后无新HTTP完成，实际在途取消unknown的公共409，四路同编号提交/四路领取、PG提交回执丢失503、分页、同编号改父任务409、当前模型变化发送前失败、原用例父冲突继续失败、PG子响应引用替换拒绝后恢复、实际RustFS损坏拒绝后恢复、项目越界404/归档/关闭派发历史，以及原停用候选期限失败后仅形成候选。原失败/终态/原group/scope保持；完整12步JSON及False/0/null/扩展字段、自身说明实际送达，草稿记录新retry引用。实际新task/不可变retry/Outbox同事务并通过SQL对照，原版本继续启用。

独立生产API/解析、比较、影响及生成CLI，真实DOCX、MySQL/PG/Redis/RustFS流程 **1通过、0跳过（135.10秒）**。原影响2次HTTP、原正常生成2次HTTP，另验证发送前失败后执行原请求1次和响应预算失败时原发送1次，总计6个物理POST。复用原响应的新任务没有第7次调用；两类原失败继续在原组显示。子任务完成后连同原父任务分别由独立CLI终态重投，没有新发送。API重启关闭派发后两类原请求/新task/原响应可读可重放，新编号503。所有物理消息按完整冻结请求核对；内容相同的明确新任务仍保留各自身份，不按文本误去重。

前端实际客户端与旧增量/需求/影响组件 **57通过、4文件（3.16秒）**；vue-tsc/Vite构建通过10.77秒。本轮没有正式重试按钮/缓存恢复页面的浏览器验收，没有调用外部默认生成模型、执行生产历史迁移或证明用户业务评审。受控模型证明链路及固定依据，不能证明需求已部署或测试通过。

原件、scope/response、PG重试关系/seed/收据/Outbox、实际物理消息、独立进程、首轮失败和各轮日志/XML在p4-generation-retry-*；最新功能依据使用delivery独立目录与production-first，旧4394份原文件及新证据、三份方案、用户.gitignore和暂存字节由p4-generation-retry-validation.json核对。具体命令见IMPLEMENTATION.md。

下一步实施正式重试页面与完整App恢复，继续任务池投影、草稿评审后精确关联、运行/环境适用性及P2/P1/P0/P5剩余项；完整P0—P5未完成，目标保持active。

### 6.21 本次完整方案与附件同步〔研究基线bd0092e6〕

本次按用户“更新这个方案”的要求修订文档。重新核对普通上传路由、upload_document/upload_version、原件保存、固定快照比较、独立Git导入及规划_system_prompt；确认普通上传不读取Git，前置缺口判断由AI阅读完整用例及查询依据完成。首页集中列出用户纠正的执行/模板/上传约定，3.1.2补齐页面操作及各阶段结果，研究基线更新为bd0092e6。既有P0—P5范围、历史失败证据和6.20待实施项保留。

本次交付验证为三份方案字节一致、章节与代码引用核对及git diff --check；没有改动业务代码，没有重新运行后端、前端或浏览器功能测试，历史测试结果仍属于原记录。

### 6.22 正式重试App与完整历史恢复〔研究基线7d560c73〕

上一轮7d560c73仅修订完整方案及附件，属于文档进展。本轮重新读取目标原附件及当前源码，按3.6.8研究后实施：新增独立重试组件/控制器/契约核对和实际样本组件测试，原生成页面接入；新增实际浏览器流程，将共享完整产品测试扩成默认关闭的可选重试场景。没有修改后端生产实现、依赖、数据库/配置键、实际.env或用户.gitignore。

原失败保留：组件首轮1失败/55通过，选择器同时匹配父/子两个面板，测试误期待1；修正断言并补子缓存重开回查。首轮类型构建失败为测试JSON样本过宽的状态/策略类型；明确类型转换后，完整浏览器首轮又在构建阶段失败1项（51.66秒），暴露泛型mount辅助函数读取props时类型为never。改为实际按钮选择，类型检查通过；该轮未进入重试功能，不能算功能验证。首轮日志/XML与原件、API/解析进程输出均保留。

最终新重试15项与旧增量/需求/影响/客户端合计 **72通过、5文件（2.91秒）**。新项覆盖真实失败/unknown/响应/完整草稿、只提交原编号、丢回执重开、404保留/同编号重放、父/范围/module/key错误、saved_response替换拒绝、草稿hash拒绝、明确拒绝后结束编号、项目切换晚返回、下载等待、非法缓存/跨项目、分页停滞/重复、父组读取等待、失败子面板展开及独立pending恢复、无randomUUID与存储不可用。前一56项轮次重叠，不累计。

真实DOCX、built App、生产API和独立解析/比较/影响/生成CLI，实际MySQL/PG/Redis/RustFS流程 **1通过、0跳过（175.24秒）**；其vue-tsc/Vite构建通过9.09秒。页面先完成原正常生成、失败与停用候选，再从原输入预算失败模块显式创建子任务，POST已持久后故意丢回执；刷新按原父和编号恢复同task，没有再次POST。改当前MySQL策略为1字节后原子task仍使用已冻结256000字节策略，原完整请求生成V4草稿，原组失败不变。另通过正式页面制造响应预算失败，扩大响应容量、输入保持1字节，新task复用同RustFS原响应形成V5，不调用模型。旧V1仍启用，完整12步JSON/False/0/null/扩展字段和自身说明保留，草稿retry引用与收据一致。

两类retry关系与实际PG record/task seed/checkpoint一致；原scope/response对象SHA及大小、原固定消息和实际下载逐项核对。原失败父task和完成子task分别独立CLI终态重投，没有新发送。总6个物理HTTP：原影响2、原正常生成2、执行原请求1、响应预算失败的原发送1；响应复用没有第7次。API重启并关闭派发、归档项目后，两类原pending缓存恢复同关系/结果，历史下载一致，原POST可重放，新编号503。pageerror为空，1440宽页面无正文横向溢出，结果截图留存。

旧正式增量完整App **1通过、0跳过（136.10秒）**，最新App类型/Vite构建8.43秒；未启用新增测试重试流程，仍为原4次物理调用。原生成/需求历史、unknown提交恢复、停用候选、原预算失败、重启关闭派发/归档与下载继续通过。没有因为新功能已通过而跳过旧产品验收。

原件、scope/response、PG关系/seed/收据、实际物理消息、下载、截图和各轮失败/log/XML在p4-generation-retry-product-*。旧4840份原始证据与新文件、三份方案、用户.gitignore及暂存字节按p4-generation-retry-product-validation.json核对。命令见IMPLEMENTATION.md。受控模型验证链路和固定依据，没有调用外部默认生成模型、执行生产历史迁移或代替用户业务评审；草稿不直接成为矩阵覆盖、测试通过或已部署需求。

继续研究生成草稿评审后的精确需求/验证点关联，再接任务池投影、运行/环境适用性和P2/P1/P0/P5剩余项。完整P0—P5未完成，目标保持active。

### 6.23 本次方案原文更新〔研究基线18c4671e〕

按用户“更新这个方案”的要求，直接修改完整方案并同步所贴附件与选中的PLAN.md。重新核对普通上传/追加版本路由、原件与派生对象保存、固定解析快照比较、独立Git导入及规划Prompt：3.1明确上传保存原件与数据库版本，无需Git提交或缓存；3.1.3明确两个来源入口的工程边界与无Git验收。第2.3—2.4继续保留完整用例、项目隔离、AI判断条件来源和必要前置补全，执行不读取生成模板。

本次只修改方案正文；核对三份文件逐字节一致、历史上传/比较与Git验证XML、引用位置及git diff --check。没有重新运行后端、前端、浏览器或默认模型测试，没有执行数据迁移；历史实测结论归属于原验证记录，P0—P5未完成项继续保留。

### 6.24 独立覆盖确认、真实文档与生产进程〔研究基线c2db4a4〕

上一轮c2db4a4修订完整方案，属于文档进展。本轮重读目标原附件，核对需求评审固定范围、不可变版本/hash、逐版本评审与生成固定收据、共享装配和本地版本化评测/治理课程后实施3.6.9。新增PG覆盖表及append-only记录，复用项目锁和需求服务；原需求评审约束保留。抽出原用例canonical算法和共享GenerationObject/Receipt，保持旧hash与导入位置兼容，避免循环Schema依赖。没有新配置键、依赖或实际用户.env变更。

新覆盖8项（1项输入校验、7项真实存储场景）与旧JSON/模板专项合计14通过、1跳过（74.15秒）；唯一跳过是未启用外部默认生成模型的旧测试。真实MD/DOCX/PDF上传、解析/比较进程、影响及两模块生成后，用公共ASGI路由确认更新/新建完整12步JSON及False/0/null/扩展字段，原需求/来源/hash不改写，未确认点仍未覆盖，执行not_assessed/部署unknown。实际提交评审后记录pending_review，显式启用后新确认记录approved，旧确认仍冻结原状态；initial/明确历史、分页/撤销与后续需求needs_review均核对。

实际PG提交后丢回执503，四路原请求恢复同记录，归档后原请求仍返回，禁止读取当前case/配置/模型/对象仍通过；四路同编号首次提交只产生一条，不同编号竞争同预期确认一成功一409。跨项目、未知点/错误哈希/生成来源、移除版本、重复版本/点拒绝。实际修改孤立PG收据和case正文、覆盖正文分别拒绝并恢复原字节。saved_response重试草稿可确认，原失败保留，不新增HTTP；其他模式的人工版本可显式关联，缺失旧评审状态标unavailable，不伪造approved。

旧需求评审、增量任务、失败重试、公开API及用例版本评审五模块53通过、0跳过（342.83秒）。前端客户端与旧需求/生成/重试组件52通过、4文件（2.91秒），vue-tsc/Vite构建通过10.82秒。没有新增覆盖编辑页面或浏览器功能验收；客户端与旧组件不是页面完成证据。

三格式生产API及独立解析/比较/影响/生成CLI3通过、0跳过（347.41秒）。真实MySQL/PG/Redis/RustFS完成固定文档、生成、明确版本评审/启用、覆盖修订、原始与历史矩阵、SQL记录/hash核对；DOCX额外实际重试两种策略并确认原响应重试草稿，原父失败仍保留。API重启关闭派发，四路原POST恢复、按编号GET及历史矩阵仍相同，归档后原请求可读/重放、新确认409。MD/PDF各4次物理模型发送，DOCX6次；覆盖确认0次，终态重投不发送。旧正常增量生产流程单独1通过、0跳过（88.55秒），仍为原4次发送，没有启用新增覆盖场景。

原件、完整scope/response、物理消息、SQL覆盖/需求/用例记录、版本hash、独立进程stdout/stderr及XML/log保存在p4-requirement-coverage-*，旧5062份原始证据不改写；三份方案、原件/对象/物理消息及暂存字节见p4-requirement-coverage-validation.json。没有调用外部默认生成模型、执行生产历史迁移或替代用户业务评审。正式覆盖确认页面、任务池投影、运行/环境及P2/P1/P0/P5剩余目标继续未完成，完整目标保持active。

### 6.25 本次完整方案与副本更新〔源码核对基线b908fe29〕

按用户“那不更新这个方案”的要求直接更新正文和两份副本。本次重新核对普通上传及追加版本服务、项目规划query/finish协议、覆盖确认公开路由、当前矩阵组件及6.24原验证记录。首页明确模板只参与生成、项目内选取/补全、上传独立于Git及轻量解析；5.2/第六节移除“正式增量产品未接”等已过时的当前描述，增加独立覆盖确认已验范围与3.6.10正式页面待实施步骤。

本次仅修改方案和实施记录；实际核对三份方案逐字节一致、引用文件存在、保留历史章节及验证XML统计，并运行git diff --check。没有重新运行后端/前端/浏览器/默认模型测试，没有迁移数据。此前6.24结果属于原实测记录，不记作本次测试。既有原始证据及用户.gitignore保持原字节，P0—P5剩余范围保留。

### 6.26 正式覆盖确认App、完整版本与归档恢复〔研究基线028dfca8〕

上一轮028dfca8同步完整方案，属于文档进展。本阶段重新读取用户原目标，核对覆盖确认API/不可变Store、项目用例目录与完整版本、矩阵/评审页面、原请求恢复和JSON树，实施前研究见[P4_REQUIREMENT_COVERAGE_PRODUCT_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_REQUIREMENT_COVERAGE_PRODUCT_DESIGN.md)。接入独立覆盖页面/控制器/身份核对；现有评审对话框增加可选固定初始版本和只读属性，旧调用默认行为保留。无新配置键、依赖、业务表或迁移；配置MySQL、覆盖业务PG、原件/生成范围RustFS的职责不变。

首轮构建因使用ES2020不支持的Array.at失败，改为索引访问。首轮组件运行出现矩阵回写反复初始化，停止原进程并保留日志；按项目/文档/需求版本/hash的稳定字符串判断上下文，修复反馈循环。随后40项中39通过/1失败，实际历史样本已有第三次重试确认而夹具只列前两次，按真实记录补齐后通过。扩大组件先78通过，再83通过；最后修正归档只读按钮及失败重读提示后最终 **84通过、0跳过（1.91秒）**，六文件覆盖完整JSON/12步/False/0/null、自身说明、项目隔离、原编号恢复、404/503/409、缓存/分页、历史/撤销、迟到响应、固定版本和旧需求/生成/重试/评审/API。

首轮真实DOCX App **1失败（176.80秒）**：旧受控创建夹具返回空步骤，新场景误要求12步；页面忠实显示原结果。仅覆盖场景开关提供完整12步及扩展字段创建样本，旧默认夹具不变。随后完整App **1通过（188.87秒）**。归档体验检查发现表单整体禁用连带禁止完整关联读取，改为仅禁用写控件，并实际扩大最终复验：**1通过、0跳过（206.41秒）**；最终源码的vue-tsc/Vite构建由真实App harness执行通过，三个Python测试文件字节编译及diff检查另作补充。

最终真实MySQL/PG/Redis/RustFS、独立API与解析/比较/影响/增量/两种重试CLI，浏览器明确选择需求V1及更新V3（此时已有新草稿V5），通过复用对话框提交评审/显式启用；完整更新/新建JSON与自身说明可查。覆盖五次确认分别为首次、撤销、恢复、API并发和原响应重试草稿；PG关系与hash逐条核对。原响应重试V5确认时pending_review，旧V3启用指针继续保留；设计覆盖、执行not_assessed和部署unknown分开，不自动启用或宣称测试通过。

首个覆盖POST真实保存后中断回执，重载按原编号GET恢复，不新增POST或生成。initial/指定第一次历史保持；409不自动换父确认，用户显式重读后才作新决定。API重启关闭派发、项目归档后原请求恢复/重放、固定历史和完整关联12步读取仍可用，新确认409，写控件禁用。全部模型物理发送6次（影响2/正常生成2/重试2），覆盖确认0次；终态/原响应重试不另发。1440宽度无页面横向溢出，截图/完整对象/SQL/原消息保留。

旧正常增量App保持覆盖开关关闭，单独 **1通过、0跳过（147.24秒）**，仍为原4次模型发送，未启用新覆盖夹具或页面流程；后续只读按钮修正作用于新覆盖面板，旧默认入口不挂载它。失败/诊断和最终证据均在p4-coverage-product-*，审计见p4-coverage-product-validation.json。此前16份报告的5534份原始证据逐项hash/大小核对不变，三份方案同步，用户.gitignore保留。没有外部默认生成模型、用户业务评审或生产历史迁移；任务池投影、运行/环境及P2/P1/P0/P5剩余目标继续未完成。

### 6.27 本次方案与所贴文件同步〔源码核对基线5966d833〕

直接更新方案正文、用户所贴附件及选中的PLAN.md。核对上传/追加版本、项目规划query/finish、正式覆盖管理组件、生成/重试业务表和任务池查询/路由：上传不需要Git缓存或提交；执行不读取生成模板；逻辑补全继续由AI阅读完整用例并追问条件来源。首页更新为当前提交基线，5.1修正增量/重试仍未接入的过时描述，5.2区分普通文档生成与已验的P4增量模块，新增5.2.1说明尚未接入的任务池工作及验收。

本次只修改方案与实施记录，实际检查三份方案字节一致、引用文件存在、历史XML计数、旧证据文件hash和git diff --check。6.26的84项组件及真实App结果属于原验证，未作为本次重新运行；本次没有功能实现、后端/前端/浏览器/默认模型测试或数据迁移。历史失败与证据、用户.gitignore保持，P0—P5剩余范围保留。

### 6.28 增量生成任务池、固定详情与实际恢复〔研究基线afd9d303〕

重新读取目标附件，核对任务池联合分页、生成/重试权威表、固定读取及本地课程后实施。新增公开case_generation类型和白名单摘要，前后端消费方同步；固定详情复用现有生成/重试API和JSON树，不增加依赖、配置键或业务表。MySQL配置、PG业务和RustFS原件/大依据职责保持。

真实MD/DOCX/PDF上传、解析/比较进程、影响及生成/重试形成实际记录。最终新目录专项和旧四类兼容 **10通过、0跳过（46.58秒）**；另外旧任务池首轮5通过。验证queued/running/completed/failed、字面量搜索、项目/状态筛选、稳定分页、两种重试关系、终态不重领、归档与重建读取。实际发送取消后租约恢复保留unknown，不创建新重试或额外发送。最终组件 **108通过、0跳过，9文件**，包括15项新固定详情场景、旧任务池/生成/重试/需求/覆盖/评审/API。

失败原样保留：测试夹具将checkpoint赋空触发TS2322，改为正式类型；浏览器owned context不能新增page，改显式独立context。分页测试误改业务更新时间，改为创建同时间会话而不改冻结任务历史，最终10项通过。完整App先错误要求8条任务；转用真实存储的独立浏览器诊断（4及8条均通过），再保存正式App响应与DOM，确认同为7条，任务身份无遗漏。8份来源引用中原失败任务出现两次，验收按唯一身份计7；此前“API8条/DOM7条”判断已纠正。最后一次URL即时断言在异步导航完成前失败131.99秒，按项目已有expect.to_have_url等待实际路由，保留原失败。

最终正式DOCX built App、独立生产API/解析/比较/影响/生成CLI及MySQL/PG/Redis/RustFS **1通过、0跳过（169.57秒）**。任务池读取7个唯一任务、8份来源引用；进入正常/失败/停用/两策略子任务六项详情，查看完整草稿/自身说明/原响应，再刷新URL。API重启关闭派发、归档后重复读取同一PG任务/关系及完整历史，禁新重试。全过程6次物理模型发送，列表/导航/恢复0次新发送；原失败与unknown不重写。截图、DOM、响应及SQL留存。

旧正式增量App单独 **1通过、0跳过（111.36秒）**，池场景开关关闭，仍原4次发送，未采用新增完整创建夹具。实际构建由两次正式App harness完成，8个受影响Python文件py_compile及diff检查另作补充。所有失败/log/XML/原件/对象/消息/截图在p4-generation-pool-*；审计见p4-generation-pool-validation.json，5953份旧原始证据不改写，三份方案同步，用户.gitignore保持。

受控模型验证流程和固定依据，未调用外部默认生成模型、执行生产历史迁移或代替用户业务评审。运行/环境/回归闭环及P2/P1/P0/P5剩余目标未完成，完整目标保持active。

### 6.29 本次方案原文与副本更新〔源码核对基线12d2ebd4〕

直接更新用户所贴完整方案，不另起替代方案。核对当前提交、普通上传/追加版本、项目规划Prompt、矩阵Schema/服务/前端类型及Agent实际回执核验；上传仍独立于Git，模板仍只用于生成，逻辑补全仍由AI结合完整用例判断所需条件来源。

首页和第六节改为当前提交基线；新增3.6.12与5.2.2，说明需求验证点如何引用正文实际核验、服务器如何核对原运行/版本/hash/ToolJob、部分核验与整条结果的区别、追加历史与恢复，以及正式API/页面/真实文件与HTTP验收顺序。当前执行关联草稿尚未接入且未经过功能测试，明确保留待实施状态；部署unknown和全部P0—P5剩余范围继续保留。

实际运行plan_sync_20261006_execution.py --sync：三份方案逐字节同步，所有本地文件引用存在；读取既有四份最终XML核对108项组件、10项专项和两次App各1项通过，未把它们计作本次重跑。18份历史审计中的6636份原始证据逐项核对大小/SHA，用户.gitignore原字节保持。另执行git diff --check及暂存范围检查。审计见p4-plan-execution-sync-validation.json。

同步校验首轮在带:245行号的本地文件链接上报AssertionError；原因是校验器未去掉行号，并非引用文件缺失。按应用文件链接格式修正后实际校验通过，最终更新再次同步和检查。

本次仅交付方案、研究设计、同步校验脚本及文档审计，没有接入或测试新业务功能，没有重新运行后端/前端/浏览器/外部模型，也没有数据迁移。原测试失败和证据不改写，工作区未接入源码草稿不纳入本次文档提交，不报告完整升级完成。

### 6.30 正文执行证据关联与真实结果恢复〔实施基线c27efe0e〕

继6.29文档更新后，重新核对原目标、固定覆盖/需求版本、实际Agent核验与运行结果、项目事务和本地课程，接入3.6.12。确认引用原result/verification/check，不接收客户端判定；服务器核对项目、完整固定用例、原结果payload/hash、run/item/attempt及实际ToolJob输出。核验路径和算子复用现有CaseExecutionCheck与VerificationService，不改变历史hash。PG追加确认/历史、公开候选/提交/恢复、前端DTO/API与矩阵读取已接，没有新增模式、依赖或可编辑配置。新表随启动幂等初始化，生产历史迁移未运行。

真实MD/DOCX/PDF上传、受限解析/比较任务和MySQL/PG/RustFS形成固定需求与覆盖；完整12步JSON保留False/0/null。实际本地业务HTTP经ToolRuntime生成工具输出，原结果经Agent回执核验和RunService.complete_item保存。清理实际500导致整条结果失败，但正文检查可通过；仅所选正文检查计入矩阵，execution_scope=selected_checks，未选检查不推导完整需求通过。真实7天返回失败后，新运行得到9天结果并引用原失败，原失败与终态不改写。该回执为受控测试输入，不声称已验证默认Agent自主执行。

| 实际验证 | 结果与对应证据 |
|---|---|
| 三格式/证据/恢复及旧需求、覆盖、Agent核验回归 | UUID修正前扩大专项67通过、1跳过，172.65秒；跳过外部实际模型。p4-requirement-execution-final.xml |
| 最后UUID规范化后的当前新模块 | 11通过、0跳过，66.85秒；固定历史、撤销、原请求、并发、跨项目/覆盖、篡改、缺正文核验和实际回归。p4-requirement-execution-identity-verified.xml |
| 独立生产API与原覆盖路径 | UUID修正前两场景2通过，139.74秒；修正后新执行场景1通过，60.91秒。生产API及独立解析/比较/影响CLI、真实存储、重启关闭派发/归档恢复及SQL核对。production-final.xml和production-identity.xml |
| 前端契约/所选检查/旧需求覆盖兼容 | 49通过、0跳过，4文件；vue-tsc/Vite构建通过。components-first.xml和build-final.log |

公开关联/候选/矩阵及恢复不调用模型或工具；新生产执行场景6次实际业务HTTP，关联与恢复模型发送0。新影响夹具的2次受控模型发送属于原文档影响路径，与自主执行验收分开。实际PG提交丢回执仍可按原编号恢复，四路并发和预期父冲突、归档读取/禁新确认、旧结果缺正文核验、实际ToolJob输出篡改拒绝及新覆盖/新需求不继承均已验证。原SQL、完整结果/用例、对象及HTTP轨迹保存。

原失败保留：首次三格式专项1通过/6失败，因夹具缺现有必需skill_versions，补manual:v1；另实际大写UUID引用1失败，根因是输入格式允许大写而核验引用用字符串比对。现由输入Schema规范化UUID，11项及独立生产复验通过；证据匹配没有放宽。各轮次分别记录，不累计为当前覆盖总数。

交付审计见[p4-requirement-execution-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-requirement-execution-validation.json)：核对三份方案、本地引用、原件/结果/核验/工具输出hash、最新生产对象、历史6636份原始证据及用户.gitignore。所有新增原失败/log/XML/实际工件保留。正式关联编辑页面、完整浏览器App、外部默认Agent自主执行、用户业务评审、点级完整性、正式环境身份/部署适用、生产历史迁移及其余P0—P5仍未完成。

### 6.31 正式执行关联App、实际回归与固定恢复〔研究基线8efcdd99〕

重读原目标与当前方案，核对覆盖编辑/恢复、执行PG全局父确认与按覆盖读取、原run/result/完整case接口及Agent轨迹课程后实施3.6.13。新增固定确认核对、执行编辑控制器和面板；矩阵覆盖/执行编辑互斥，候选前端类型细化，后端公开契约不变。没有新增配置键、依赖、业务表或测试模式。原执行夹具抽出共享准备方法，既有产品夹具增加可选执行场景，默认旧覆盖/生成场景保留。

前端首次67项通过；构建发现负向测试故意添加非法verdict时的TypeScript转换TS2352，修正测试表达，原失败log保留。增加固定用例hash、缺正文核验及分页边界后70项通过；一次扩大命令列了两个未命中的测试文件名，XML仅5文件，不作为7文件结果。改用实际CaseGenerationProduct/GenerationRetryProduct/GenerationTaskProduct，最终 **120通过、0跳过，8文件（2.46秒）**，其中21项新增编辑测试。验证原真实失败/清理样本、12步JSON/False/0/null、显式验证点、恢复/404/503/409/原参数、全局父确认、历史/撤销、跨项目/版本/hash、重复分页、缓存错位及迟到响应。

首次真实DOCX完整App **1通过（91.02秒）**；补固定用例hash检查后的最终源码 **1通过、0跳过（87.75秒）**。独立生产API兼容首轮 **1失败（49.42秒）**：共享夹具抽取后采集证据仍引用旧局部jobs，改为f.jobs；复验 **1通过（62.33秒）**。原正式覆盖App单独 **1通过、0跳过（152.65秒）**，覆盖开关/原生成与两种重试保持，物理发送仍6次。三次成功生产/App场景使用真实隔离MySQL/PG/Redis/RustFS，API及解析/比较/影响CLI独立；两个App harness实际vue-tsc/Vite构建通过。所有失败与前后轮次分目录保留。

最终执行App真实上传DOCX并解析/比较/评审；完整12步固定用例经实际本地业务HTTP和ToolRuntime保存两个失败结果。首个7天返回的正文核验失败；新回归9天/False/0/null正文四项通过，但清理实际HTTP500使整条result仍failed，回归源指向原失败。页面按固定覆盖读取完整JSON、原结果/检查和工具引用，明确选择验证点，没有把准备或整条结果混入正文判定。此处回执受控，尚未验证默认Agent自主执行。

浏览器第一次POST实际PG提交后中断回执，刷新按原编号GET恢复且没有第二次POST；显式撤销/恢复、initial和指定第一条历史可查，历史查看不替换编辑集合。API并发撤销后页面以旧父确认实际得到409，保留原请求；显式重读再提交新决定。PG五条确认依次为原失败、撤销、回归正文、API并发撤销及重读后新确认；浏览器五次POST包括一次409，原确认/需求/覆盖/hash和原结果不改写。

API重启关闭派发、项目归档后浏览器恢复原请求、读取固定历史与完整用例/结果/回归源仍可用，新写控件禁用；实际新POST409，原请求可重放。执行关联/读取/恢复模型发送0，实际业务HTTP6次；原文档影响路径2次物理模型发送逐条匹配固定请求。旧覆盖App仍6次原发送。1440宽度无横向溢出，页面错误为空，SQL、原件/对象/hash、原物理消息、完整run/result、截图及DOM保留。

三份方案和[p4-execution-product-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-execution-product-validation.json)同步，核对此前6964份原始证据不变及用户.gitignore。正式环境身份/部署适用、点级完整性、用户业务评审、默认Agent自主执行、生产历史迁移与其他P0—P5仍未完成；下一步按5.2.2研究实际环境/运行冻结，不缩减完整目标。

### 6.32 完整方案同步及环境适用性查证〔研究基线d47ab66d〕

重新核对原目标、上传路由/原件版本/固定比较及可选Git入口，保留3.1—3.2的独立上传结论：普通上传及明确同文档新版本均不调用Git保存文件；生成模板只用于生成，执行按项目选取完整用例、初排、检查衔接/补全及最终冻结。三份完整方案同步，完整P0—P5范围保留。

下一步环境设计经实际源码核对修订：已有影响environment_key/target_release声明且固定user_declared/unknown；项目base_url可改，runtime_context任意JSON，资源/兼容设备/安全容器均不等于被测业务部署。MySQL配置没有历史revision读取或expected_revision写入接口，固定目标必须捕获实际非敏感配置，恢复不假设不存在的调用。设计按身份/配置/固定依据、具体证据/追加确认、现有规划/运行/领取/回归、正式页面四步实施，见5.2.3和[P4_ENVIRONMENT_APPLICABILITY_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_APPLICABILITY_DESIGN.md)。

[实际研究脚本](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/environment_research_20261006.py)核对17个源码blob及上一阶段335份原始证据，重放真实DOCX App保存的普通运行与失败回归：原id、creation_hash和plan_snapshot一致，完整用例各12步，原结果failed，部署unknown，两个运行没有正式环境绑定。内存试验确认旧request增加environment_id=null会改变创建hash，后续新绑定必须保留旧hash及重放契约。初次审计误用size字段导致KeyError，按已有size_bytes契约纠正；首次与扩展后的成功事实报告分别保留，最终为[p4-environment-research-facts-verified.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-environment-research-facts-verified.json)。

本阶段只做源码/已存真实证据重放及方案同步，没有新数据库写入、HTTP、模型调用或生产功能变更；没有重跑独立API/Worker/built App，不能算新增环境功能已验收。环境身份、部署确认与执行限制仍待实施，历史结果与unknown不重写；后续先验证5.2.3的A步及原提交兼容，再继续完整目标。

### 6.33 A步环境身份、配置与固定捕获〔研究基线c1f34368〕

继续重读原目标与方案，并核对项目路由/服务、MySQL配置get/save、PG项目锁/固定记录、容器及main初始化、既有HTTP错误/客户端和隔离生产进程夹具后实施。新增business_environment契约、BusinessEnvironmentService、PG环境登记/捕获存储与项目business-environments路由；main/容器及前端类型/API/统一api成对接入。MySQL当前目标配置、PG业务身份/不可变捕获；无新依赖、测试模式或默认目标，不改原计划/运行hash与部署unknown。

实际MD/DOCX/PDF上传和独立解析进程、MySQL/PG/RustFS首轮 **16通过、10.14秒**。补原登记关联及重算hash篡改、当前MySQL真实提交丢回执和合法63字节项目表名后，原运行创建/Agent完整上下文/项目API扩大回归 **56通过、1跳过、25.67秒**；跳过的是需要当前配置外部模型的完整用例读取测试。证据SQL RowMapping显式转字段字典后的新环境专项 **19通过、0跳过、10.13秒**。三轮分别保留，不累计成当前覆盖数。

实际核对当前MySQLscope/payload/revision与PG完整捕获；配置改址、删除及项目/环境归档后按原请求和固定id读历史不访问当前配置，新提交409。真实PG提交回执丢失后新服务找回、四路并发只存一捕获、归档在配置读取后抢先导致PG拒绝、配置在读取后变化仍保留已选读值、跨项目/新参数/缺配置/注入部署拒绝、原件及解析hash不变均通过。当前配置PUT重复保存会追加revision而不是幂等；真实MySQL提交丢回执后GET确认保存值，不能冒充原请求记录。

独立生产API首轮 **1失败、16.64秒**：夹具误将旧上传200设为201；实际原件核对hash、复制后清理确切隔离测试URI，修正夹具与提前收集工件。第二轮 **1失败、23.90秒**：全流程后证据导出直接JSON化RowMapping失败，改为字段字典转换。最后源码 **1通过、0跳过、21.97秒**，实际main启动/重启、Bearer保护、隔离PG/MySQL/Redis/RustFS、Markdown原件、登记/配置/固定捕获、归档及删除当前配置后原捕获/原登记恢复、新写409均验。目标/模型哨兵0次访问，不推断已部署或自主Agent执行。

前端新客户端与原需求API **6通过**；vue-tsc/Vite构建通过，Vite7.13秒。三份方案同步，历史两个完整Run的id/hash/快照重放仍一致，用户.gitignore保留。无正式环境页面，未绑定规划/运行、未接部署证据/适用确认/执行限制、未验外部自主Agent或生产历史迁移；B—D及所有P0—P5剩余项继续，下一步研究真实ToolJob/result的目标证据后实施B。

### 6.34 HTTP实际来源与固定环境工具观察

研究基线bd1172d6。核对既有API执行器、Worker/完成检查点、ToolRuntime脱敏/输出检查点、PG原ToolJob、环境捕获、报告及本地Agent轨迹课程，并查HTTPX官方history/request/url接口后实施。真实跨端口303返回9天但旧结果无最终来源，复现 **2失败、2.65秒**，原HTTP轨迹、body和失败XML保留。旧7/9天验收的规则由query选择，只证明正文核验；本轮两个实际HTTP目标固定各自7/9天且release相同，不能用release替代规则判断。

新增api-http-observation-v1记录每跳实际方法、来源范围、状态及观察时间，不复制query值/userinfo/fragment或headers。新尝试清除旧回复/观察；外部同名键不能替代实际Response。既有completed_request恢复标记取得方式，旧无来源保持原结构和unknown，不为补证据重新发送。完成检查点、Worker原字段合并、结构化报告及失败工件同步保留观察。

业务环境的固定快照增加只读tool-observations/inspect API，加载同项目原ToolJob、具体output_path_tokens与值、整个原输出SHA、调用身份、观察及地址范围判定。所有hop都在固定目标范围才matched；跨源或路径边界不符mismatch；旧/回放/缺观察/输出检查点不一致unknown。固定环境捕获晚于观察也不补造先前绑定。记录0、False、null的区别；当前配置改址/归档不替换固定依据；不接受客户端判定、不发送HTTP、不写生效确认或停用用例。

修复及旧Worker恢复首轮 **18通过、5.19秒**；新增真实三格式/MySQL/PG/RustFS及原恢复首轮 **24通过、17.15秒**，补旧回放/客户端伪造/PG输出改动后最终 **24通过、16.75秒**。每格式原件上传/独立解析后固定两个环境，真实ToolRuntime保存6个ToolJob、5次业务HTTP；实际7/9/跨源9核对、同release、0/False/null、跨项目404/缺字段409/伪造判定422、回放和输出改动unknown、归档后重读无当前配置及无新HTTP通过。复现与早期成功均保留，不累计为新的测试数。

独立正式main API **1通过、25.78秒**：隔离PG/MySQL/Redis/RustFS、Markdown原件、实际ToolRuntime及原ToolJob，两个调用产生3次业务HTTP；POST→GET跨目标跳转不匹配。API重启、归档项目/环境并删除当前配置后原观察完全相同；Bearer401通过，读取没有新HTTP。前端新/原需求客户端 **7通过**，vue-tsc/Vite通过、Vite7.26秒；旧环境/正文执行关联/API文档依赖/会话隔离扩大回归 **46通过、74.98秒、0跳过**。

原件、原观察、PG输出/hash、实际两目标轨迹、失败/通过XML和进程日志在p4-http-origin-*；命令及局限见IMPLEMENTATION.md。没有新的外部默认模型调用、正式环境页面、需求版本/验证点生效确认、Run环境绑定、执行限制或生产历史迁移。完整P0—P5保持未完成；下一步B以原观察与已确认需求/覆盖的固定引用实现追加生效/适用决定、撤回/并发和原请求恢复，再接C/D及其余范围。

### 6.35 固定需求范围的环境生效/用例适用确认API

研究基线1ec34cef。重读原目标及方案，查既有需求/覆盖/正文执行确认、PG事务/原请求恢复、固定环境和原ToolJob，以及本地轨迹/版本化课程后实施。研究与采用方式见[P4_ENVIRONMENT_DECISIONS_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_DECISIONS_DESIGN.md)。提取原固定需求/覆盖读取为共享函数；旧执行关联继续保留原返回/哈希。新增独立业务确认契约、PG追加表、单一服务、正式main/API和前端类型/客户端；不新增依赖、模型调用、配置或测试模式。

提交绑定project/environment_snapshot/requirement_version、明确initial或固定覆盖及hash、所选验证点和精确用例版本。每点effective/not_effective/unknown，每用例valid/invalid/unknown，明确评审理由；原值/来源/地址匹配不由客户端填写，PG事务重新读原ToolJob/输出检查点/hash及完整需求/覆盖/用例版本。确定生效只能引用匹配目标的实际network，错目标/回放/早于捕获/脱敏值保持unknown；invalid只针对本次明确生效点。保存完整固定正文/来源，旧矩阵/部署unknown/版本/结果不改写；partial不代替完整需求通过。撤回、重确认及回滚均追加，原请求优先恢复，历史按修订号读取。

首次测试命令在仓库根缺后端模块路径，**1收集错误、0.48秒**；按已有Agent_Server执行路径纠正，离线 **1通过、5项真实存储开关跳过、4.27秒**。真实首轮 **6通过、0跳过、41.82秒**，三格式均上传/独立解析/影响后，通过ToolRuntime/PG读取两个各自固定7/9天、相同release的目标。旧目标确认not_effective/valid，新目标effective/invalid；未选择边界不继承。覆盖原值/hash、完整版本、追加/撤回/重确认、分页、四路首次并发、不同修订409、实际PG提交回执丢失及归档恢复。

新增覆盖/需求版本/新捕获/回放边界首轮 **1失败、12.31秒**：夹具浅复制使另一请求点状态改变，提前422；改为独立请求后第二轮 **1失败、14.07秒**，实际用新的task/idempotency身份没有恢复原请求，产生新network并被正确接受。读取原执行器和已存ToolJob契约后，夹具使用原task及真实幂等身份，并断言completed_request及HTTP不增加，复验 **1通过、12.52秒**。前后失败保留，没有修改生产执行器掩盖错误。

包含新增边界的完整专项先 **7通过、72.17秒**；复核历史排序由时间改为业务revision，并增加独立main分页验收后，最终 **7通过、0跳过、73.03秒**。证明明确新覆盖保存完整12步/False/0/null、新需求/新捕获不继承、回放/早于捕获只支持unknown、错版本/点/字段/hash/项目拒绝、SQL观察改动且重算外层hash仍拒绝、配置改址/归档后恢复无当前配置/对象/模型/HTTP访问。每组三个工具调用产生4次真实业务HTTP，回放不增加请求。

独立正式main/API、解析/比较/影响CLI和实际隔离MySQL/PG/Redis/RustFS首轮 **1通过、56.26秒**，补分页后最终 **1通过、92.38秒**。真实Markdown/固定12步用例、两个实际规则目标、Bearer401、显式评审/撤回、API重启/关闭派发、环境/项目归档及删除当前MySQL目标配置后原确认恢复均验；两个业务HTTP，确认阶段模型/HTTP新增0。物理影响模型请求仍为2条受控请求，不是外部默认Agent。

旧覆盖/正文执行关联/环境扩大回归 **38通过、0跳过、122.80秒**；前端新旧客户端 **9通过**，vue-tsc/Vite构建通过，Vite8.17秒。所有轮次、原件、原ToolJob、SQL、实际请求/固定对象和进程日志在p4-environment-decisions-*；[交付核对脚本](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/environment_decisions_delivery_20261006.py)校验三份方案、历史原证据、当前工件及暂存字节。详细命令与局限见IMPLEMENTATION.md。

交付审计首轮通过Tee保存自身输出，扫描后才创建的audit-first.log未进入首份清单；复核出现新增文件，首份/中间报告保留为validation-first/validation-recheck。最终直接输出控制台，在日志关闭后重建并核对，不修改功能证据。此前7505份原始证据及用户.gitignore保持原字节。

当前仅为所选点/精确版本的显式确认API，语义评审输入来自测试夹具；未验用户业务评审、自动AI候选、正式环境/矩阵页面、Run环境绑定/执行限制、默认Agent自主执行或生产历史迁移。完整P0—P5仍未完成；下一步研究C规划/启动/领取/回归的固定目标与确认读取，再接D和其余范围，不把确认记录存在等同执行已受约束。


### 6.36 C1固定环境规划、运行依据与真实功能验证〔研究基线67602761〕

先查现有规划提交/Worker、PG运行事务、Agent上下文/审批恢复和追加确认，再沿这些入口实施。新增纯选择/固定依据DTO与PostgresBusinessPlanning；main、容器、规划模型输入、PG任务/运行创建、Agent上下文和前端类型/客户端同步。HTTP观察纯类型从模式模块移到schema层，旧模块兼容导出，修复首次收集发现的导入环；原字段/schema不变。没有新依赖、模式、数据库表、MySQL配置键或Git保存机制。

真实MD/DOCX/PDF专项验证固定目标与完整12步JSON、False/0/null、实际工具调用及PG结果核验。最终独立上下文字段阶段10通过/0跳过（84.16秒）：固定九天目标返回200/9/0/False/null，五项正文检查通过；改址、撤回、归档后原计划/运行/context/hash保持。持久任务先冻结，无模型的提交重放与终态不可重领、环境checkpoint替换拒绝、跨项目/错hash/自重算hash伪造PG依据拒绝均实际执行。

独立生产流程1通过/0跳过（90.53秒）：真实main/API、Outbox、独立规划CLI、PG/MySQL/Redis/RustFS、完整JSON和正式运行创建。物理请求3条：既有影响2、规划1。重启、关闭派发、归档和删除当前配置后原task/plan/run可恢复，没有新模型HTTP；新归档后运行拒绝。该生产场景Run仍排队，不是生产Agent执行成功。

新/旧客户端6通过，vue-tsc/Vite通过（11.65秒）。旧规划/创建/环境/来源回归78通过、5跳过（152.35秒）；导入顺序及旧完整Run/hash专项5通过。独立环境字段后的Agent图/审批/检查点/真实HTTP恢复85通过、2个外部默认模型测试跳过（252.17秒）；HTTP来源及原API恢复24通过/0跳过（33.05秒）。最后提示/模型输入与旧上下文专项19通过、1个外部模型测试跳过（85.37秒）：三格式各1次生产SDK实际HTTP，固定环境完整JSON与用户同名输入同时进入模型，旧无环境提示保持；消息原件另存p4-environment-planning-model-context-final。

失败保留：首次导入1收集错误；错测试文件名命令退出4、零项测试；三格式曾因夹具读取错误的checks路径3失败；生产夹具响应非字符串、随后漏计新增规划1发送，各1失败。独立上下文复验还发现会话创建/重放遗漏wire函数，造成实际恢复误拒绝；扩大回归12失败中含该契约错误及2个工作目录导致的旧证据路径失败，另一次4失败。统一序列化并使用Agent_Server工作目录后85通过/2跳过；没有删除历史结果、放宽身份核对或重置运行掩盖失败。

本步模型和业务评审均为受控夹具；真实工具/PG证据与默认Agent自主推理分别验收。C2最新确认的执行限制、独立受影响项/依赖、未知副作用和换目标回归，D正式页面，业务评审及其他P0—P5继续未完成。历史原件/失败/发送记录保留；生成模板只用于生成，上传不依赖Git。

交付审计见[p4-environment-planning-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-environment-planning-validation.json)，核对三份方案/引用、旧原始证据、最新三格式原件/工具/结果/固定模型消息及暂存字节。审计统计按轮保留，不累计为当前功能覆盖。

### 6.37 C2a精确失效限制、真实锁竞争与独立生产验证〔研究基线bbea328e〕

先查原运行创建/领取/开始、依赖结果、过期恢复、追加确认的项目锁和本地Agent协议课程，再用真实文件复现C1缺口：新确认已明确旧精确版本invalid，原计划创建运行仍全queued。首次及完整输入捕获复现各1失败，保留原文件、完整计划/SQL确认和失败日志，未重写失败记录。

采用做法见[P4_ENVIRONMENT_RESTRICTIONS_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_RESTRICTIONS_DESIGN.md)、[精确限制判断](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/environment_restrictions.py)、[原运行事务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/run_store.py)。复用原PG事务及Attempt/Result/依赖传播，不新增模式、表、配置键或依赖。创建/领取/开始读取该固定快照各需求版本最新确认，只限制明确失效且无点级矛盾的精确用例版本；必要依赖blocked，独立项继续。未提及、unknown、撤回、其他目标及相互冲突确认不自动限制。

限制结果明确business_execution_started=false，并保存完整当前确认、实际采用确认、固定快照SHA、原规划SHA、精确case/version、时间和阶段；开始阶段消费方返回原结果，不继续查用例/起执行心跳/调用工具，事件使用item_blocked。租约过期重新排队时检查历史Attempt.started_at/checkpoint，保留原恢复，不把已发送条目冒充从未执行。终态同租约重放验证结果/hash；错租约拒绝，撤回/归档/配置删除不重置原结果。

真实MD/DOCX/PDF创建首轮3通过（46.14秒）；扩展首轮9通过、1个测试收集警告（81.30秒），将导入类型按既有风格起别名后，最终12通过/0跳过（119.22秒，11项实时功能及1项原失败输入重放）。每格式旧前置及必要目标blocked，独立项实际GET返回200/9/0/False/null，五项正文核验passed，运行完成且终态不可重领。四路实际领取只产生1个独立领取；领取后追加invalid阻止开始，实际PG已提交回执丢失后返回原结果。真实PG transactionid锁等待/阻塞PID证明确认与领取串行，随后开始读取已提交确认。实际已开始POST保留原ToolJob/检查点，过期恢复后无新HTTP、无伪造未执行结果；该静态目标不证明业务状态已改变，也未调用Agent自主续跑。

既有依赖/创建/规划/Agent上下文、会话、审批与核验回归132通过、2个外部默认模型测试跳过（277.48秒）。这些包含实际PG/HTTP/Worker与内存边界，不把全部数量当成实际部署验收。py_compile和源码diff检查通过；前端公开字段和类型未变，本轮未重跑完整App/前端构建。

独立生产首次及原输入捕获各1失败（75.38、67.09秒）：断言直接比较PG原JSON时间+00:00与API同一UTC时刻的Z，捕获显示只有updated_at/completed_at表示不同。按既有Run模型读取方式比较，不修改生产时间序列化；最终1通过/0跳过（99.22秒）。实际main/API、Outbox、独立规划CLI及独立执行CLI使用PG/MySQL/Redis/RustFS；模型HTTP4条：影响2、既有C1规划1、新限制规划1。精确invalid运行创建即完成blocked，执行CLI只接收实际终态派发，新增业务HTTP为0；撤回、API重启、停派发、归档及删除当前配置后原任务/计划/运行/结果恢复，没有新模型发送。规划和语义评审来自受控夹具；不是默认Agent业务执行成功。

审计及命令见[p4-environment-restrictions-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-environment-restrictions-validation.json)与[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)。三份方案保持相同字节，旧8010份原始证据及用户.gitignore不修改。C2a只是精确未执行限制；允许通过项的当前决定回执、已开始后的动作修订/未知副作用、换目标回归、D页面/AI候选/默认Agent、生产历史迁移和其余P0—P5仍未完成。模板只生成用例，上传无需Git，项目选取/初排/简单检查/补全/最终冻结的原要求继续保留。

### 6.38 C2b首次开始回执、原Agent恢复与独立生产执行〔研究基线afa9381e〕

查证和实际失败先于修改，见[P4_ENVIRONMENT_ADMISSION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ENVIRONMENT_ADMISSION_DESIGN.md)。真实MD上传/解析/比较/环境确认/固定计划复现：允许start已检查当前确认，却未保存给Agent；首次1失败、23.09秒。随后真实PG提交后丢回执又复现running重试被拒：1失败、17.79秒。两份完整输入、Attempt和原日志保留，不用事后生成的理想样本代替。

允许项首次start在原事务保存business-execution-admission-v1，根Attempt、开始时间、完整确认及hash固定。恢复只读原回执/祖先，旧历史缺失仍缺失；no_explicit_invalid不推导valid/已部署/通过。Agent首次/工具后推理、审批、持久上下文及结果同用原依据；原业务null/False/0和完整十二步不变。无新表、配置或依赖；后端DTO和前端Attempt类型同步。Pydantic项目声明>=2.8，采用既有wrap serializer只省略新缺省字段，未使用当前安装版本的exclude_if扩展。

| 独立轮次，前缀p4-environment-admission- | 实际结果与范围 |
|---|---|
| live-first | 3通过、37.75秒；真实MD/DOCX/PDF允许项、PG/RustFS/MySQL、真实GET和结果 |
| live-final | 19通过、0跳过、160.22秒；当时7项C2b及12项C2a，含丢回执/原检查点/真实失败wire；不包含后来新增图测试 |
| graph-first | 1通过、7未选、17.06秒；原Agent图/会话/工具/审批与PG检查点，过期恢复后的新模型调用和新业务HTTP均0；模型在进程内受控 |
| production-first | 1失败、91.33秒；5物理模型POST，Agent请求stream=True，但测试服务仅回普通JSON；保留error结果和原请求 |
| production-stream | 1通过、0跳过、114.46秒；按既有CLI夹具支持SSE后，新任务经独立API/Outbox/规划CLI/执行CLI及真实SDK、PG/MySQL/Redis/RustFS/HTTP完成 |
| regression | 171通过、2外部默认模型跳过、547.98秒；当前8项C2b、12项C2a以及依赖/创建/规划/会话/工具/审批/核验/影响调用兼容 |
| frontend-build | npm run build通过，vue-tsc和Vite真实执行；没有完整环境产品页面或浏览器验收 |

生产场景新建并明确评审有实际五项预期的正式JSON用例，不使用原十二条占位步骤冒充业务执行。规划固定七天快照和原确认，运行创建后追加unknown确认，start保存最新unknown并与旧规划分开。执行Worker等待原工具权限审批，经所属条目审批入口批准后继续原图。真实GET只新增1次，正文核验200/7/0/False/null，Result passed；unknown本身没有充当通过证据。

总6次物理SDK模型发送：原影响2、C1规划1、新用例规划1、Agent工具调用和工具后回执2。真实终态Outbox重投给执行CLI、API重启、关闭派发、归档和删除当前MySQL配置后，原任务/计划/Run/Result相同，新增模型发送和业务HTTP均0。图恢复测试另证明原检查点在后来unknown与过期领取后复用，不重新执行工具。生产进程存在可见memory_query_embedding_failed检索警告，未把该可选检索成功计入验收。

确认和模型回应均为受控夹具。默认外部Agent自主判断、运行中逐动作修订/未知副作用、换目标新回归、正式环境页面/AI候选、用户业务评审、点级完整性、生产历史迁移及其他P0—P5仍未完成。只核验实际记录副本的篡改拒绝，未向PG篡改业务行。历史原件/失败/未知发送保持；模板只生成、上传无需Git、项目选取/初排/简单衔接检查/补全/最终冻结继续保留。完整命令/回归及交付审计见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)和[p4-environment-admission-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-environment-admission-validation.json)。

### 6.39 运行中适用性变化：已复现缺口与方案修订〔代码基线573c3d40〕

本次更新方案前核对已有源码、完整复现输入、PG/工具记录、实际HTTP和测试报告。已有两轮工作区复现均失败：p4-environment-action-reproduction为1失败、23.25秒；p4-environment-action-captured为1失败、19.50秒。断言均为`after == before`，实际请求计数由`[2, 2]`变为`[2, 3]`，说明新确认之后仍新增一次待审批目标HTTP。本次仅修订方案及同步副本，没有重跑这两轮功能测试或修改生产代码。

具体过程是：原固定用例已start并保存首次确认；Agent请求目标工具，原Job进入waiting_approval，尚无新增目标HTTP；在同一固定目标对该精确用例版本追加明确invalid；原条目恢复并批准原审批后，工具仍执行一次GET。这个结果不能由首次开始回执覆盖，也不能因原Result写blocked就称工具已经被阻止：blocked回执来自受控诊断模型，实际网络请求已发生。

复现使用实际文档、存储、会话图、原工具审批及HTTP；模型与语义评审输入由测试夹具控制。该场景只有一个目标项，没有准备或清理，因此没有验证真实业务状态的创建、回滚或清理，也没有验证外部默认Agent自主判断。第一轮事后在终态构造的上下文缺少已清除租约对应的回执，不能当作当时执行上下文；第二轮在完成之前捕获，原模型请求和结果记录均保留。

根因边界：C2a限制尚未开始的精确版本，C2b保存首次开始及原恢复依据；它们尚未提供“当前确认→具体未执行动作→工具领取”的完整关联。`ModelToolCall`及ToolJob已有调用和运行身份，但尚未表达固定执行项归属，不能从HTTP方法或call_id猜测阶段。5.2.3据此补充C2c实施顺序，避免全局禁止工具误伤必要清理，也避免改写已执行历史。

证据索引：两轮XML的SHA-256分别为`ca138a9b02edf756d68a2b9edbb9ed28963b438fb3e09831255128b5c4b42adb`与`26012bf82f3aa5e4b12a830ef54509a3736951cd522d90944d7896fff50118d1`；同前缀日志和before/after完整输入保留在本方案目录的已有工作区记录。本次文档提交不打包这些既有未提交测试文件和原始工件；历史已提交的6.38验证记录保持不变。

本次方案交付验收：正文、所贴附件、选中PLAN.md同步并核对字节；检查新增说明与源码、原失败报告及章节引用一致，检查文档diff。C2c动作协议/治理消费、真实有状态清理、发送未知、换目标回归、正式环境页面、AI候选、用户业务评审、默认Agent及其余P0—P5继续待实施，完整升级没有完成。

### 6.40 C2c1固定执行项关联、实际工具与独立生产验证〔研究基线38643f9c〕

先重读用户原目标、6.39失败样本、固定归属、MySQL策略、模型工具schema、原Job/审批/恢复/核验和本地Agent协议课程，再沿现有入口实施。采用方式见[P4_CASE_ACTION_BINDING_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CASE_ACTION_BINDING_DESIGN.md)。共享绑定模块统一原图、DeepAgent治理、实际ToolRuntime和证据读取；不新增测试模式、数据库表或依赖。

项目页面可保存“记录工具所属执行项”，配置在原MySQL `test_case.planning_policy`；冻结后进入PG计划/原运行上下文，Job/审批保存完整原参数与服务器推导关联。模型选择所属item_key，服务器确定阶段和身份；不信任模型自报phase。处理器接收分离且独立复制的业务参数，新元数据不发送到业务接口。旧策略缺省字段省略，旧JSON/hash/恢复和业务null/False/0保持；当前设置变更不替换历史计划策略。实际证据manifest展示并重新核对归属。

| 独立轮次，前缀p4-action-ownership- | 实际结果与范围 |
|---|---|
| offline-first | 1通过、4真实存储测试跳过、8.04秒；旧策略、原普通/回归hash及上下文兼容 |
| live-first | 4通过、1失败、42.92秒；三格式实际调用通过，原图新增日志phase与函数形参重名导致中断；原失败保留 |
| live-fixed | 5通过、0跳过、38.47秒；改用item_phase后原图审批恢复通过 |
| live-final / boundaries-final | 分别6通过、45.19秒及6通过、44.86秒；正式配置API、未知协议422、三格式完整12步/False/0/null、当前配置修改、陌生项/伪造阶段/缺失选择/跨项目、实际PG/HTTP治理与终态复用 |
| governance-final | 46通过、20缺少deepagents跳过、7.63秒；修正frozen治理上下文赋值，使用不可变替换；不把跳过当作框架验证 |
| regression / regression-final | 首轮12失败、180通过、24跳过、301.14秒；该进程加载修正前赋值代码。修正后完整受影响回归192通过、24跳过、298.56秒；包括20缺少deepagents、3外部实际模型、1真实Redis开关跳过 |
| wire-final | 23通过、0跳过、50.64秒；当前最终参数副本及7项关联验证，原治理/终态/未知结果/审批边界；真实保存输入证明处理器嵌套修改不能回写原wire |
| public-worker-final | 1通过、27未选、7.30秒；补启真实Redis开关，原公开启动/Outbox/Worker/审批/终态复用；保持原策略 |
| production-first / production-fixed | 首轮1失败、75.75秒，工具及业务核验已经运行但夹具保存RowMapping失败；转成dict后完整复验1通过、0跳过、93.48秒 |
| frontend-test-final / frontend-build-final | 10组件测试通过、1.89秒；真实vue-tsc/Vite构建通过，Vite8.28秒；没有完整产品浏览器验收 |

三格式每组实际执行三个GET，分别关联before/case/after；它们验证归属和参数，不证明创建或清理改变状态。外来执行项、模型自报phase、缺选择及跨项目均没有新增HTTP。原Agent图的审批参数与PG原Job一致，获批后只新增一次HTTP；受控回执明确blocked且无业务检查，不冒充目标通过。DeepAgent治理入口另验PG原领取、实际HTTP及终态重放，但完整DeepAgent框架因环境缺少依赖未验。

独立生产验证使用正式main/API、Outbox、独立规划/执行CLI及真实SDK，生成并评审十二步有实际预期的正式JSON用例。项目策略进入持久规划和新运行，实际模型工具schema及返回参数携带所属项，PG原Job保留case阶段与原参数，真实GET核验200/7/0/False/null，Result passed。共6物理模型HTTP：既有影响2、既有C1规划1、新关联用例规划1、Agent工具调用及工具后回执2；新增业务GET为1。终态重投、API重启、归档和删除当前目标配置后原记录恢复，新模型/业务HTTP均0。模型与业务确认仍为受控夹具，未验证外部默认Agent。

stdout/stderr、原件、固定输入/消息、SQL及工件在各轮独立目录，原失败不覆盖；命令见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)。交付核对见[p4-action-ownership-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-action-ownership-validation.json)。三个方案同步，用户.gitignore与历史原证据保持原字节。

本步只使工具动作具有可靠的固定执行项归属。6.39的运行中invalid限制、逻辑操作身份、发送未知、准备状态/实际有状态清理、修订、换目标新回归、默认Agent、正式环境页面/AI候选、用户业务评审、生产迁移和其余P0—P5继续未完成。下一步在新协议下复现待审批动作变化，再接原PG确认事务和工具领取，不把元数据存在等同执行已受限制。
### 6.41 C2c2当前确认与原工具领取同事务处理〔研究基线fc7b7b8f〕

先查原固定计划/Attempt/Agent检查点、当前确认事务、工具治理/审批/原领取及本地Agent协议课程，再复现和实施；采用方式见[P4_CASE_ACTION_ADMISSION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CASE_ACTION_ADMISSION_DESIGN.md)。配置使用原MySQL `test_case.planning_policy`，新策略为`case-action-admission-v1`并要求binding-v1；新计划在PG冻结，旧计划不追加缺省字段或改变历史hash。不新增业务表、依赖或测试模式。

原PG共享服务核对原运行、固定目标/执行项、当前租约、原Agent检查点/恢复祖先、Session/Turn、完整上下文SHA和原Job参数，按原项目锁顺序读取当前确认并同事务保存原Job领取或拒绝。明确且无冲突的invalid只拒绝尚未领取的before/case动作，不调用处理器和HTTP；unknown/撤销/其他版本或目标保持从宽，无固定目标标注no_fixed_environment。after暂保留并记录after_not_restricted；这不证明清理，也不代表真实准备拥有者已实现。

首两轮失败是夹具确认另一旧版本，原PG正确返回no_explicit_invalid；修正为运行实际选择的完整用例和覆盖后，关闭新策略再复现真实缺口，HTTP由[2,2]到[2,3]。启用策略后MD/DOCX/PDF的同一精确版本待审批动作均拒绝，新HTTP为0。保留最初错误记录和真正失败，不混为同一根因。

| 独立轮次，前缀p4-action-admission- | 实际结果 |
|---|---|
| boundaries-first | 14通过、0失败、0跳过、113.14秒 |
| current-final | 32通过、1失败、0跳过、144.55秒 |
| current-fixed | 33通过、0失败、0跳过、145.57秒 |
| exact-fixed | 3通过、0失败、0跳过、34.29秒 |
| exact-reproduction | 0通过、1失败、0跳过、13.50秒 |
| live-first | 0通过、1失败、0跳过、13.46秒 |
| no-target-final | 1通过、0失败、0跳过、13.38秒 |
| production-current-fixed | 1通过、0失败、0跳过、93.80秒 |
| production-final | 1通过、0失败、0跳过、92.42秒 |
| production-first | 1通过、0失败、0跳过、87.66秒 |
| production-observed-final | 1通过、0失败、0跳过、94.16秒 |
| regression-final | 226通过、0失败、24跳过、547.01秒 |
| reproduction | 0通过、1失败、0跳过、15.43秒 |
| frontend-test / frontend-build | 11组件测试通过、2.20秒；vue-tsc/Vite构建通过，Vite8.92秒；完整浏览器页面未验 |

regression-final的226通过、24跳过验证当时源码；20跳过缺少deepagents、3外部真实模型和1真实Redis开关。之后审查发现新领取路径需保留既有可靠输出恢复，使用原recover_observed_output追加实际保存HTTP返回、交付中断和后来invalid的恢复测试。current-final为32通过/1失败，旧denied终态被误标为新拒绝；修正共享claim的旧终态None契约后，current-fixed对最终受影响源码33通过。原响应恢复为partial而非业务通过，原响应/确认保留且没有重发HTTP。各轮不累加为当前覆盖数。

实际四路同Job领取只有一条业务GET，原确认项目事务锁等待后精确拒绝；领取PG提交回执丢失和HTTP返回丢失保留running/未知，同原Job不重发。领取事务提交是本步边界，之后的新确认不能撤销已经领取或可能已发送的动作；跨新call_id的稳定逻辑操作身份仍未实现。原工具审批/终态重放和回执进入证据manifest已验；完整DeepAgent框架因缺依赖未验。

独立production-current-fixed使用正式main/API、Outbox、规划/执行CLI、SDK和实际HTTP，原完整十二步JSON/False/0/null进入固定依据，正文实际核验200/7/0/False/null，Result passed。共6次物理模型HTTP：影响2、既有C1规划1、新策略规划1、Agent调用/工具后2；本次新增业务GET1。原Job保存固定case归属、原参数和no_explicit_invalid当前unknown确认，未知确认没有充当业务通过证据。终态重投/API重启/归档/删除当前目标配置后历史保持，新模型/业务发送均0。模型及评审为受控夹具，不等于外部默认Agent。

完整命令/失败见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)；原件、物理消息、PG回执、旧8973份原字节及三方案核对见[p4-action-admission-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-action-admission-validation.json)。用户.gitignore及此前environment-action未提交诊断保持原状态。

真实有状态准备/效果拥有者/必要清理、稳定逻辑操作身份、执行中追加修订、换目标新回归、默认Agent、完整环境App/AI候选、用户业务评审/点级完整性、生产历史迁移及其余P0—P5继续未完成。模板只生成、执行完整用例、项目内简单衔接/补全和上传独立于Git的约定保持。

### 6.42 C2c3真实订单状态与跨调用恢复研究〔研究基线5102413d〕

先查原目标、固定计划归属/输出绑定、完整Agent上下文、原Job/审批/领取/输出恢复、API检查点及本地Agent协议课程，再复用已有真实订单HTTP服务、原图及原存储进行验证。研究和后续采用方式见[P4_STATEFUL_OPERATION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_STATEFUL_OPERATION_RESEARCH.md)。本轮仅增加研究测试及可选测试夹具策略；没有新增生产去重或效果状态机。

| 独立轮次，前缀p4-stateful-research- | 实际结果与范围 |
|---|---|
| first | 2通过、1失败、16.01秒；单次真实准备/清理与同Job未知恢复通过，新调用ID重复创建 |
| agent-first / agent-fixed | 各1失败、3未选，9.09/8.69秒；分别为研究夹具误读tool.input及内部tool_calls.function，图中断，不能作为业务重复证据 |
| agent-wire-corrected | 1失败、3未选、11.32秒；按实际内部arguments更正后，原图及四次审批真实创建两条，第二条未清理 |
| current | 2通过、2失败、18.92秒；最终四项研究完整运行，两个业务缺口真实复现，原日志/断言保留 |
| compat-final | 67通过、2跳过、160.09秒；既有assessment/execution模块。跳过实际模型专项与公开Redis Worker专项，开关未启；不是新增框架或生产验收 |

单次链路实际POST创建、GET返回的订单ID、DELETE该ID，服务器created=1、orders=0。目标count期望0实际1，PG目标结果failed；准备seed/cleanup为passed。它证明已有正常链路能清理已知单次效果，不证明执行中的拥有者/多效果状态机或任意中断清理。

原图换调用ID：同一准备项、同task_id及完整原参数，经原审批新增seed-recovery Job；四次HTTP为POST、POST、GET第一条ID、DELETE第一条ID。created=2、最终orders=1，原目标failed，终态准备回执仍只体现seed/cleanup通过。真实五次受控模型请求完整保存；局部决策视图不是物理消息。根因是原Job身份包含provider call_id，缺少跨调用的稳定逻辑操作身份；终态准备摘要没有记录第二条效果供清理。

未知返回分别验证：实际POST创建后持久观察前丢失返回，原Job保持running且无可靠输出；同原Job恢复不重发，HTTP=1。只换provider call_id进入共享ToolRuntime则新增POST，HTTP=2、created=2；原Job仍未知，另一Jobcompleted。这是共享执行入口的缺口，不宣称原Agent图在未知恢复时自动继续。未知场景未执行清理，销毁隔离HTTP服务器不等于业务清理成功。

实际使用MySQL冻结新binding/admission策略、PG原计划/运行/Job/检查点/审批、JSON原件上传RustFS及有状态服务器；受控Agent提供模型决策。本轮无默认外部模型、三格式解析专项、独立生产进程或完整App验收。完整命令/局限见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)，字节、实际状态、旧证据和三方案核对见[p4-stateful-research-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-stateful-research-validation.json)。两个失败保持失败，不累加历史通过数量。

下一步沿原Job、审批和领取扩展显式冻结的逻辑操作契约，并记录执行期间真实效果/对象与清理范围。相同已声明操作与相同完整输入才可恢复原输出，未知先查证；合法重复创建、负向重复与只读刷新不能只因参数相同被阻止。具体契约查证后实施，旧策略/原hash/未知及终态保持。操作身份、效果状态、执行修订、换目标回归、默认Agent、环境App及其余P0—P5未完成。

### 6.43 C2c3逻辑操作、原结果恢复及最终来源〔研究基线253d47f0〕

实施前重读原目标、6.42真实失败及原Job身份/领取/可靠返回、固定上下文、API检查点、评估回执、原图/治理/审批；参照已有生成任务creation_key/完整请求hash与项目事务锁、本地Agent轨迹课程。采用方式见[P4_CASE_OPERATION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CASE_OPERATION_DESIGN.md)。不另建执行平台，完整P0—P5继续保留。

配置使用原MySQL `test_case.planning_policy` 的可选`operation_protocol=case-operation-v1`，要求已有binding/admission。新计划在PG冻结；旧缺省字段省略，旧wire/hash和恢复策略不改变。项目设置组件提供启用/关闭及前置策略联动。PG增加不可变操作索引，引用原ToolJob；原件/大工件沿用RustFS，未引入依赖或测试模式。

新协议的每次`_case_action`携带所属`item_key`及稳定`operation_key`。逻辑身份绑定项目、固定运行/条目/计划、根执行attempt、完整上下文SHA及执行项/key；provider调用ID各自保留，不能代替逻辑身份。完整工具和业务参数另存SHA，相同key改参数明确冲突。key为非空不透明字符串，Unicode及空格保持原值；服务器不通过HTTP方法、关键词或相同参数猜测“是否为同一业务操作”。

沿原PG项目锁及领取事务核对租约/祖先/固定身份，操作索引与原Job领取一起提交：

| 原操作情况 | 当前调用行为 | 实际证据 |
|---|---|---|
| 已保存可靠终态 | 保存自己的Job/输入/审批，复用原输出及来源；不新发送 | 原图换ID后只创建一条订单，查询/删除同一真实ID |
| 可靠返回已保存但交付中断 | 先用原Job恢复交付，再重验并复用 | 原running观察恢复partial，只一条HTTP；原观察不改写 |
| 已领取而返回未知 | 保留原running与未知证据，新调用明确拒绝重复发送 | 实际POST已创建但返回丢失，同/新调用ID均没有第二次POST；订单仍存在，不冒充已清理 |
| 同key改工具或完整输入 | 保存冲突结果，不发送 | 实际业务参数变化未新建订单 |
| 明确的新操作/负向重复/只读刷新 | 使用不同key，继续现有权限与领取 | 相同业务参数明确创建两条，并实际删除两条；读取恢复与新刷新分别验证 |
| 多调用竞争同逻辑操作 | 唯一索引及原事务只允许一个原Job领取 | 实际4路PG竞争只有1次业务创建，未知别名没有发送 |

原领取回执`execution_claimed=true`、`action_sent=null`，领取成功不能证明HTTP或效果已发生。复用/拒绝为`execution_claimed=false`、`action_sent=false`。别名输出携带原Job及原输出hash，API检查点来源标为`completed_request`，原`network`证据不重写。assessment核对索引/固定身份/别名输出SHA，manifest和最终Result的证据元数据保存完整操作回执；复用记录不算独立采集，也不能证明目标通过。

当前最终源码7模块 **107通过/3跳过，208.80秒**。包括15项新操作专项、完整用例/评估/执行上下文、原图/治理/适配/审批；3项为未启用外部实际模型或公开Redis Worker开关。实际JSON原件、MySQL、PG、RustFS、有状态HTTP证明：准备创建1条、GET/DELETE真实ID、最终0条；目标期望count=0而实际1仍failed。另验真正未知、四路竞争、丢PG提交回执、可靠返回交付恢复、当前配置关闭后旧冻结协议恢复、同名协议业务字段、Unicode、输出篡改拒绝及明确新创建/刷新。单独空正文诊断复现 **1通过，6.29秒**，保存修改前原Job与HTTP现场。

独立main/API/Outbox/规划CLI/执行CLI及实际SDK流式HTTP **1通过/0跳过，101.11秒**。总模型HTTP7条，新增规划及Agent阶段4条；SDK使用两个不同provider调用ID，经两次审批保存两个Job，只新增1条业务GET。最终五项验证引用复用Job，Result保存其case_operation及原Job/输出SHA；False/0/null保留。API重启、停派发及终态重投不新增发送。模型和评审为受控响应，未证明默认外部Agent自主判断。实际最终原记录见[p4-case-operation-production-result-final](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-case-operation-production-result-final/md-native-finish/environment-admission.json)。

前端12项组件及vue-tsc/Vite构建通过。更早扩大回归 **240通过/24跳过，655.87秒** 在最后HTTP路径、反馈及最终Result来源修改前启动，不能作为后来分支的验证；其中20项缺DeepAgent框架依赖，3项缺外部模型开关，1项缺公开Worker开关。当前107项及独立生产覆盖最后修改；测试数量不累加。

失败原件均保留：前端初始11通过/1夹具失败，保存mock丢失新policy；wire-final为29通过/1夹具失败，缺ToolRegistry导入。真实业务字段`http_observation_v1`被初版递归JSON检查误认协议，business-field-first为1通过/1失败；修正为只处理服务端已定义的API检查点位置，合法业务JSON原样保留。早期领取回执曾写action_sent=true，现已更正为null，旧轮次不重写。wire-fixed为30通过/1失败，响应正文为空后测试改写字段失败；该次未保存原Job现场，原因尚未确认，不能宣称修复了产品根因。补修改前现场捕获后，单独复现及最终完整回归通过。

审计及全部命令见[p4-case-operation-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-case-operation-validation.json)和[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)。三份方案同字节；旧9723份原工件和用户.gitignore保持。新增原参数、上下文/回执SHA、PG索引、来源、实际业务状态与Git暂存原字节逐项核对。

本步只消费明确逻辑身份。模型换key却语义重复、未知效果只读查证、执行期间效果拥有者/共享消费/多对象必要清理、计划修订、换目标新回归、默认Agent、其他模式/完整环境App、生产迁移及其他P0—P5仍未完成。旧策略失败没有被重置或改为xfail；上传无需Git、模板仅生成、执行完整JSON及项目隔离的原要求保持。

### 6.44 C2c4实际效果与清理证据研究〔实现基线e8e9ed16〕

本次先复核原`plan_mapping`、完整用例/上下文、评估字段读取、原ToolJob返回检查点和6.43操作索引，并读取本地Agent轨迹课程。根因是实际对象、消费范围和清理覆盖没有独立于单步回执的记录；不是补一个after归属校验或HTTP方法白名单就能解决。依据、研究顺序和验收见[P4_EFFECT_LIFECYCLE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_EFFECT_LIFECYCLE_RESEARCH.md)。当前未修改生产源码、数据库或公开接口，未接入新效果协议。

使用实际JSON原件、隔离MySQL/PG/RustFS、原ToolRuntime和有状态HTTP服务器，保存原输入、完整固定上下文、Job、操作索引、实际HTTP、服务器对象及最终Result。最新7项实验 **4通过、3失败，18.49秒**，0跳过；[原XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-effect-research-current.xml)和[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-effect-research-current.log)保留失败。

| 实际场景 | 当前结果 | 方案据此调整 |
|---|---|---|
| 同一步使用两个明确不同operation_key创建两条数据，只删除第一条 | 第二条仍存在，清理回执却passed；失败 | 记录每个实际对象和清理覆盖，不能只保存一个最终步骤输出 |
| 没有清理动作，回执引用目标GET的200 | 对象仍存在，清理回执passed；失败 | 证据引用合法不等于满足清理目的；必须对照固定清理条件 |
| after所属步骤确实调用工具，但只做GET | 对象仍存在，清理回执passed；失败 | 仅校验步骤归属/阶段不足以修复 |
| 明确创建两条并分别真实删除两条 | 剩余0，清理passed；对照通过 | 合法重复创建保留，不能按同参数误阻断 |
| 对after GET追加已有字面量核验，固定method=DELETE、实际network method=GET | 清理及结果failed；对照通过 | 复用现有输出字段核验，无需为方法另建输入引用契约；方法一致仍不证明所有对象清理 |
| DELETE可靠返回已保存，再换provider调用ID恢复 | 复用原结果，无新增HTTP；通过 | 保留6.43原逻辑操作恢复及来源 |
| DELETE真实完成但可靠返回前丢失 | 原Job保持running/无输出，新调用拒绝且无新增HTTP；通过 | 实际状态与系统可证明状态区分，未知只读查证另行实施 |

首轮4失败、18.25秒来自研究辅助函数使用了错误session/turn，原身份校验正确拒绝，不能列为产品缺口。改用真实执行检查点身份并保存评估前现场后，4项 **1通过/3失败，12.48秒**；增加三个对照后得到上述7项结果。各轮分别保留，不累加；没有将失败改为xfail或放宽预期。回执由受控研究代码提交，未调用默认外部Agent；简单HTTP状态检查用于暴露清理缺口，不证明所有目标字段或业务语义已验证。

下一步先实测共享准备的消费者/清理归属和运行依赖，再确定冻结效果契约及原Job观察事务的集成点。确定后的可编辑策略仍放MySQL，固定意图/原对象引用/消费与追加证据放PG，大原件放RustFS；沿原权限、租约、审批和恢复链路实施，旧版本不补写。生产效果生命周期、共享消费、未知查证、计划修订及完整P0—P5均未完成。审计见[p4-effect-research-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-effect-research-validation.json)。

### 6.45 C2c4准备来源、消费者及清理的固定范围〔研究基线4cde9a7d〕

先用真实原件/存储和原Agent图、审批、API、有状态HTTP完成共享研究。两个正式消费者等待完再清理可用；左目标failed但seed passed仍允许右侧查询。只依赖左侧的清理提前删除，右侧真实GET 404。原清理审批pending时左侧未结束、右侧不可领取；这证明执行时另加“等右侧结束”的判断会形成条件循环，不表示原审批自身死锁。依据与采用做法见[P4_PREPARATION_SCOPE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_PREPARATION_SCOPE_DESIGN.md)。

已实现可选MySQL `test_case.planning_policy.preparation_scope_protocol=preparation-scope-v1`，要求已有逻辑操作策略。AI在有实际依据的清理prepare上声明`cleanup_sources`；服务端只对明确声明的来源，按原计划正式消费者补齐依赖，沿原拓扑排序后冻结。回合保存声明前的来源/依赖及规范化后决定，日志携带项目/计划/清理key和新增依赖。运行创建重算核对，不能原地修正已经冻结的计划。

PG原运行JSONB快照追加`preparation_scopes`，记录来源、拥有者、消费者及清理项；原Agent完整固定上下文携带同一范围。没有新执行器、工具、模式、数据库表或依赖。前端类型和项目规划设置同步，清理来源展示区分计划范围与实际证据。旧缺省字段省略，原计划/快照/上下文/hash保持；旧普通/环境规划Prompt与研究基线逐字比较一致。未声明来源不推测清理语义，范围为空不证明零对象。

最终范围专项 **16通过、1真实失败、0跳过，58.43秒**，见[原XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-preparation-scope-declarations.xml)和[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-preparation-scope-declarations.log)：9项来自实际捕获计划的边界、8项实际场景。唯一失败是原未启用范围策略的提前清理场景，保留原策略及原hash，没有改为xfail。启用新策略后，即使模型原depends_on仅left，固定清理归属right、依赖包含seed/left/right，实际POST→GET左→GET右→DELETE，最后0对象。真实八步JSON、末步、False/0/null和完整版本保持；原件RustFS字节一致，MySQL/PG、原Job和审批均实际操作。

另验当前MySQL关闭策略、新版本把右侧预期改为201之后，原请求仍找回同一范围和200固定版本。左目标失败的场景创建新回归：只保留左消费者，新的真实ID不复用父数据、清理归属左侧，原父运行与失败结果不变。跨来源切片若会丢掉清理声明来源，复用原重新规划流程，不裁改清理instructions。策略依赖、越界/重复/自引用、循环、正式来源、无声明、旧快照/Prompt及原冻结依赖核验都有对应边界。

新范围下还实际让模型在right内部先DELETE再GET：目标真实404/failed、cleanup passed。该对照通过只证明限制存在，**不证明目标或实际生命周期通过**。因此已实现的是计划范围和跨条目依赖；同条目消费完成、实际对象数量/清理覆盖、未知只读查证仍待实现，6.44三项真实缺口未宣称修复。

前端13项组件通过、vue-tsc/Vite构建通过。兼容回归首轮121通过/3跳过，119.46秒；最后补声明记录后一次为120通过/1失败/3跳过，111.30秒，已有来源专项原输出缺network观察。该次断言前未保存原Job，根因未确认；补现场采集后单项1通过、6.85秒，再次完整六模块 **121通过/3跳过，120.79秒**。当前全部兼容项通过不能证明此前来源缺失的根因已修。完整各轮见[p4-preparation-scope-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-preparation-scope-validation.json)及[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)。三个跳过均为未启用默认外部模型验证；受控模型、API在同进程中的实际调用和原图不代替独立生产进程、正式完整App或默认Agent验收。

早期领取结构`items`误读、工具JSON内`call_id`误读，各导致3项研究夹具失败；任务Seed检查使用简单模型配置又导致3项新场景失败。分别改为原`claims`、原消息契约及正式ModelConfig夹具，保留所有失败文件；它们不是三个产品缺口。首次范围11通过、27.86秒，扩大后12通过/4失败，43.42秒，再验证15通过/1旧策略失败，49.52秒；补右侧乱序对照后16通过/1旧策略失败，54.08秒；最终声明记录为上述58.43秒，范围变化不累加成覆盖数。

下一步围绕原Job领取/可靠观察/回执确定实际对象与消费完成证据，覆盖同条目顺序、多对象、返回未知及必要清理。旧结果、原未知、副作用和终态不改写，继续原授权/权限和租约。默认模型、完整App、其他模式、修订/未知查证、生产历史迁移与完整P0—P5仍未完成。

### 6.46 C2c4原可靠返回中的对象与清理证据〔研究基线186679a8；对象证据基础已验证〕

先查6.44真实多对象/GET反例、6.45同条目乱序、原Job观察/结算、逻辑操作索引、固定上下文/领取和本地Agent轨迹课程。根因是最终步骤输出可以遗漏第二次合法创建，结算后的输出也不能重新充当原返回SHA。采用方式见[P4_EFFECT_EVIDENCE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_EFFECT_EVIDENCE_DESIGN.md)。这是对象证据基础，尚不等于清理闭环。

MySQL原`test_case.planning_policy`追加可选`effect_evidence_protocol=case-effect-evidence-v1`与`effect_evidence_max_bytes`；启用要求原准备范围和逻辑操作策略，实际默认预算512000字节。项目设置可启闭和编辑预算；关闭前置策略一并移除新策略/预算。新PG计划冻结配置、字面量对象路径及固定预期检查。产生声明支持正式用例及prepare：正式项预期直接引用其完整固定JSON，prepare预期来自自身instructions；不复制正式步骤为准备指令。清理声明目前由prepare的cleanup_sources引用固定来源及其产生声明，不据此宣称支持任意正式清理项。Agent完整上下文沿原链路携带这些定义，不读取生成模板、不改测试模式或工具业务参数。

原ToolRuntime从固定项编译对象证据意图，原动作领取核对固定意图与绑定。原可靠返回UPDATE与新小型不可变PG证据INSERT在同一事务：保存原Job/逻辑操作/绑定、原返回观察及SHA、声明路径实际对象值和检查。不另外复制整份大响应；原件/大工件继续RustFS。查询沿原不可变操作索引按项目/运行/计划读取全部原操作，别名不新增对象证据，未知原Job仍无可靠对象证据。原索引补相同范围的查询索引。预算超限保留识别数量/字节/原观察/诊断，值不可用，不能解释为零对象；原工具真实返回继续可恢复。

实际两次POST创建两个不同ID，同操作换provider调用ID恢复没有第三次HTTP或第三份效果；两条实际DELETE分别留下两个对象的检查证据，最后0剩余。当前MySQL关闭策略后，原固定上下文及事件不变。跨项目/运行/计划查询不返回原证据。after所属GET虽然状态200且对象ID相同，固定清理method比较为failed；本例复用已有字面量验证要求，不按方法猜测业务效果。**该失败检查尚未接入最终结果覆盖判定，不能宣称6.44的GET误报已经修复。**

真实返回已持久但交付中断，原对象记录保留，别名partial且无新增HTTP；原返回丢失或观察事务故障回滚时，Job仍running/无输出、没有对象事件，别名拒绝重发。观察提交完成而回执丢失时，原查询确认同一记录并完成交付，无新增HTTP。隔离夹具另验证事件篡改被识别、恢复原字节后可读；预算1字节时原POST完成但事件明确超限/对象值不可用/实际识别数量1。没有把销毁测试服务器当作清理成功，未知场景的真实对象仍存在。

最新综合8模块 **146通过、0失败、3跳过，128.47秒**，149个条目，其中新对象证据16项全部通过，见工作区已有`p4-effect-evidence-final.xml`及同名日志。普通、环境、范围三个旧Prompt与186679a8逐字一致，原实际快照序列化一致；False/0/null/完整八步JSON保持。正式前置真实POST的对象也已记录，预期引用完整固定JSON。独立正式API/CLI/SDK原逻辑操作流程 **1通过，108.37秒**，见工作区已有`p4-effect-evidence-production-final.xml`；该流程未启用新对象声明，只证明旧自动链路兼容。前端14项通过，2.58秒；vue-tsc/Vite构建通过，Vite9.77秒，见已有frontend-formal/build-formal日志。完整浏览器页未验。方案同步轮读取这些原记录，未重复运行；对象证据实施批次随后核对源码、原工件、旧证据与暂存字节，并独立提交，不把既有测试算为同步轮新执行。

各轮保留：first6通过/7夹具失败31.17秒，原因是新领取后未走原start接口；修正后started13通过23.82秒。增加冻结预算后budget14通过40.55秒；captured118通过/14失败/15跳过98.96秒，失败均缺QA_STATEFUL_RESEARCH_EVIDENCE_DIR，12实时任务开关未启。tasks补开关单独13通过41.20秒；verified144通过/3跳过112.49秒是正式来源扩展前的历史轮次。随后formal-gap **1失败、14未选中，4.13秒**，揭示只允许prepare声明遗漏正式前置来源；扩大声明范围后formal-fixed **16通过，25.11秒**，最终为上述146通过/3跳过。夹具错误与来源设计缺口分别记录，不累加历史数量，不删除真实失败。三个最终跳过为默认外部模型专项，未启用。

原件、事务故障、对象事件和物理生产消息保留在`p4-effect-evidence-*`工件中；完整核对见[p4-effect-evidence-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-effect-evidence-validation.json)。[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)保留历史命令并同步最终实际执行记录；原失败和未完成范围继续保留。下一步把原对象证据接入完整模型输入、逐对象覆盖和正式结果/页面，再接消费完成与未知只读查证。输出声明缺少可靠工具返回格式时须保留unresolved；默认模型生成声明、实际数组返回、生产迁移仍需验收。完整P0—P5、6.44三个失败、6.45同条目乱序与此前网络来源缺失的未确认根因继续保留，不标完成。

### 6.47 原对象清理覆盖的下一步研究〔基线eac120cc〕

对象证据基础提交后，重新实际执行原多对象/GET专项，终态退出1，**3失败、1通过，15.60秒**。只删两条中的一条、借目标GET、after自身GET仍被旧结果路径标cleanup passed；实际删两条的对照通过。原记录见[p4-cleanup-coverage-baseline.xml](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-cleanup-coverage-baseline.xml)和[本批核对](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-cleanup-coverage-baseline-validation.json)。旧专项正式来源仅一步JSON、采用旧逻辑操作策略，未启用对象声明；不能据此替代新协议或完整八步场景验收。

查证原assess仍依据AI选取的检查和单项output汇总，未读取全运行原对象证据或逐对象覆盖。因此下一步需沿原ToolJobService提供固定对象清单，设计新的覆盖协议，再接原结果及页面；具体依据和待查边界见[P4_CLEANUP_COVERAGE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CLEANUP_COVERAGE_RESEARCH.md)。同一正式用例可能还包含查询，不能把缺输出字段一概当成失败创建，或只加数量/HTTP方法判断。消费完成、未知只读查证、默认Agent和完整P0—P5继续待实施。本步未改生产判定，真实失败未改为xfail。

### 6.48 本次方案正文同步与当前未验收实现〔核对基线ddd8d81a〕

本次已直接修改方案首页、2.4、3.1.4—3.1.5和第六节当前状态，并将正文同步到用户所贴附件及选中的PLAN.md。模板只生成用例、项目隔离选取/初排/必要补全/最终冻结、上传不依赖Git和可选Git来源的约定保持一致。修订的是方案；没有在本次修改业务代码、数据库、依赖或现有运行结果。

本次实际运行：

```powershell
# 工作目录：G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server
..\.venv\Scripts\python.exe -m pytest 'tests/test_api_doc_versions.py::test_actual_files_version_chain_replays_and_current_content_dedup[False]' 'tests/test_api_doc_original_assets.py::test_actual_openapi_original_survives_conversion_and_project_url_updates[False]' -q --junitxml=../docs/upgrade_2026-10-03/plan-revision-current-upload.xml
# 退出0：2通过、0失败、0跳过，3.54秒；本地测试存储，无Git来源配置。
```

原记录：[XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-revision-current-upload.xml)、[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-revision-current-upload.log)。本次没有重跑生产存储、独立解析/比较进程、正式页面、默认模型或完整P0—P5专项；历史通过保留在原节，不累计成当前验证数量。

当前工作区还存在未提交的`case-effect-coverage-v1`后续实现。本次读取已有真实日志为**1失败、3通过、7夹具错误，4.01秒**，见[p4-cleanup-coverage-live-first.log](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-cleanup-coverage-live-first.log)。失败为读取原动作绑定中不存在的`target_key`；7项原生场景因缺少夹具未执行。本次没有修复或重跑该实现，不能宣称新清理覆盖协议、对应界面或默认Agent已验收。

后续实施先按真实契约解决身份来源：原`CaseActionBinding`只有`run_item_id`和`item_key`等动作身份；正式项映射已保存在固定`CasePlanExecutionSnapshot.run_item_ids`，归属保存在`owners`，不能凭空读取动作中未定义字段。新覆盖路径应沿原固定快照核对来源拥有者与运行条目，保持旧上下文、hash和历史恢复；随后实际验证全部来源对象、合法查询排除、未知创建、未覆盖对象、正式结果和界面。消费完成、共享对象生命周期、未知只读查证及计划修订仍单独待实施，不扩大为通用前置检查表。

三份方案的字节一致性、原失败记录hash、工作区既有改动未变及本次验证边界记录在[同步核对文件](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-revision-current-validation.json)。完整升级仍以第五节P0—P5及未完成项为准。

### 6.49 C2c4全部原对象证据、清理覆盖及正式结果〔研究基线82de0568〕

实施前研究见[P4_CLEANUP_COVERAGE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_CLEANUP_COVERAGE_DESIGN.md)。原动作只有run_item_id/item_key，不能读取不存在的target_key；采用原PG固定run_item_ids冻结为覆盖专用effect_owner_item_ids，核对当前条目及来源拥有者，旧上下文省略该字段。旧wire及三个旧Prompt保持。没有新工具、模式、依赖或另一套执行器。

MySQL原规划策略增加可选case-effect-coverage-v1及实际1024000字节清单/覆盖预算；随原PG计划、执行检查点冻结。规划可只读查询冻结工具接口，原生API execute_task的task_result结构依据实际ApiTestTask保存；持久任务在当前配置关闭、接口再捕获被禁止后仍沿原PG seed完成，终态重投不调用模型。可选select_when只作固定字面量输入匹配，预期来自完整用例JSON或prepare.instructions；不同值排除，缺字段保留未知，不引入注册/登录/HTTP方法推断的通用检查表。

原ToolJobService查询全运行原操作，紧凑投影保留每个原Job、未知来源、固定声明、对象路径和检查。清理投影核对cleanup_sources、实际拥有者、typed值SHA及先后观察时间；别名不新增原对象。failed检查保留失败；未覆盖、未知及超预算保留blocked。只读assessment将覆盖断言接入原VerificationResult及Result.actual，引用未被模型选择的原对象Job，评估不发送业务HTTP。原运行详情面板显示声明范围、已知/已覆盖、未覆盖与未知，预算不足不显示假零。覆盖记录不是业务对象全集，也不证明共享消费完成。

最终综合实际运行：

```powershell
# Agent_Server；四个实际开关及三个独立证据目录均已设置。
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_EFFECT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-cleanup-coverage-final'
$env:QA_STATEFUL_RESEARCH_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-cleanup-coverage-final-operations'
$env:QA_PLANNING_TASK_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-cleanup-coverage-final-planning'
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_case_effect_evidence.py tests/test_case_cleanup_coverage.py tests/test_case_planning.py tests/test_case_planning_tasks.py tests/test_case_plan_run_creation.py tests/test_agent_case_context.py tests/test_case_operations.py tests/test_agent_case_assessment.py tests/test_preparation_scopes.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-cleanup-coverage-final.xml
# 退出0：167通过、3外部模型跳过，150.36秒；170个条目。

# agent_web
npm exec vitest -- run src/features/projects/CasePlanDialog.test.ts src/features/projects/CaseEffectCoveragePanel.test.ts
# 退出0：22通过，2.54秒；frontend-verified日志。
npm run build
# 退出0：vue-tsc及Vite通过，Vite9.06秒；build-final日志。
```

真实结果：创建两条只删一条→已知2/覆盖1、blocked且现场剩1；逐条删两条→覆盖2/2、passed且剩0；借目标GET→没有清理覆盖、blocked；after GET→固定清理检查失败、failed。合法source GET未误当创建；第二创建丢返回→已知1已覆盖，但未知原Job保留、blocked且真实仍剩1；预算1→清单不可用、已知1、blocked。正式八步JSON前置在独立RunItem产生两条，消费/清理在后续条目；单清理仍blocked，全清理passed，原前置结果不改写。同操作别名不再发送HTTP、不重复对象。真实数组一项生成两条、多来源生成四条，原路径下标/所有来源保留，单清理blocked、全清理passed。旧wire、来源/清理错误拥有者及早于产生的清理边界也已检查。

原first1失败/3通过/7夹具错误保留；owner-fixed和native-first各4通过/7跳过，开关未开，不计真实场景。native-enabled7失败/4通过17.00秒为受控模型只有一回合回复，新增工具查询后夹具耗尽；query-fixed7失败/4通过19.60秒为测试错误拆解原不可信内容封装。按已有两回合/封装方式修正后envelope-fixed11通过18.92秒；formal-first2通过8.68秒，arrays-first3通过11.61秒。持久工具专项first缺objective、fixed未转换RowMapping，原失败分别保留，serializable1通过4.91秒。前端first19通过但新套件被Vite拒绝；读取方式调整后22通过，构建先发现Node类型/ES2020 at及联合JSON类型问题；改用项目已有JSON夹具及普通下标后最终测试/构建通过。没有升级依赖或放宽类型检查。

所有实际原件、固定上下文、HTTP、PG观察/清单/正式结果、持久seed与失败日志/XML保留于p4-cleanup-coverage-*，前端夹具取实际结果并保存原记录SHA。核对见[p4-cleanup-coverage-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-cleanup-coverage-validation.json)；此前11254份原始证据未变，用户.gitignore未改，三份方案同步。新增开关不自动升级旧运行/补写历史结果；新数据沿已有JSONB及对象证据表，不增加另一套状态存储。

本批模型为受控查询/声明和只读回执；实际执行采用原生工具与真实HTTP/数据库/文件。未执行独立生产新策略、默认Agent自动生成/执行声明、正式完整App及生产迁移；三个外部模型专项未运行。实际消费完成、同条目提前清理、重复身份的业务含义、未知只读查证、运行修订及完整P0—P5仍待推进，旧6.44/6.45失败及限制不回写为通过。下一步先研究新协议如何沿原API/Worker/SDK完整执行，再实测。

### 6.50 C2c5新覆盖协议的独立生产执行〔源码基线56af7313〕

先读用户目标原附件及原main/API、任务/Outbox、规划CLI、执行CLI、Agent图、动作领取、对象证据和回执路径，研究见[P4_COVERAGE_PRODUCTION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_COVERAGE_PRODUCTION_RESEARCH.md)。旧生产专项只验单次规则GET/别名，不能证明新覆盖策略；新增独立生产专项复用原生命周期/存储夹具，不替换AppContainer、不增加业务执行器或依赖，没有修改业务源码。

正式鉴权API上传实际需求文件并读回RustFS，创建/评审/启用完整八步JSON；MySQL启用覆盖策略，任务提交后关闭当前策略。独立规划CLI按PG原seed查询固定原生工具接口并完成两个回合；原API创建固定运行，独立执行CLI进入原审批，API续执行真实POST/GET/DELETE。每条Agent请求都核对完整JSON末尾、False/0/null和模板未注入；全局默认模型参数未改。

最终实际命令：

```powershell
# 工作目录Agent_Server；前缀及证据目录每次独立，不覆盖原记录。
$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
$env:QA_COVERAGE_PRODUCTION_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-coverage-production-final'
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_case_cleanup_coverage_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-coverage-production-final.xml
# 退出0：2通过、0失败、0跳过，92.65秒。
```

两个场景分别：两次创建只清理一条→known2/covered1、blocked，实际剩1；全部清理→2/2、passed，实际剩0。部分/全部场景分别8/9次SDK物理模型请求（2次规划、6/7次Agent），4/5次实际业务HTTP，5/6个PG Job；同逻辑操作新provider ID是别名，不新增原对象或HTTP。第一条创建后重启API，固定运行、审批和已创建记录保留；终态投递不再调用模型或业务工具。最终seed输出明确引用第一条ID、目标实际输出一致，第二条没有被回执输出但仍纳入覆盖。

直接读取MySQL当前关闭策略、PG原规划seed/固定工具接口及原运行记录，与正式API结果对账。实际完整用例及全部Agent消息、PG Job/结果/对象清单、业务剩余对象、过程stdout/stderr保存于p4-coverage-production-final；数据传递与对象全集分别核对，不能只看最后一个回执输出。

原first为1失败19.01秒：夹具未登记规划派发流，提交503，尚无模型/业务HTTP；按main显式注册要求修正。stream-fixed2通过95.02秒；补鉴权及SQL对账后authenticated2通过91.59秒。这两个历史批次seed回执曾指第二条而目标用第一条，不作为正确数据传递证据；最终修正引用并新增核对后2通过，原记录全部保留，不累计成6项覆盖。

本轮使用受控模型验证实际main/API/独立CLI/SDK和原生业务工具，未执行默认自主Agent、完整App或生产历史迁移。build_assessment_request当前没有生产调用方；生产最终图回执进入assess确定性核验，不能宣称已追加只读模型调用。共享消费完成、未知只读查证、运行修订与P0—P5其余项仍未完成。下一步沿完整新协议研究并实测默认Agent与正式页面，保留既有默认length失败。


### 6.51 默认模型实测、原生任务契约及发送前错误〔源码基线cd06743d〕

先读用户目标附件、原生产测试、MySQL模型捕获、SDK/HTTPX、注册工具、ApiTestTask、运行时及Job可靠返回边界，研究见[P4_DEFAULT_COVERAGE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_DEFAULT_COVERAGE_RESEARCH.md)。本地Agent轨迹及协议课程确认应分别核对Schema、实际工具轨迹、错误恢复和副作用，不以模型自述验收。

测试复制当前默认step-5-preview到临时隔离MySQL表，并比对完整非敏感调用参数；全局配置不改。原API及两个独立CLI使用原SDK发送与流式读取，测试进程仅挂HTTPX官方请求/响应钩子，响应钩子不提前读正文。SDK透明性实测1通过5.00秒，原缺system_prompt的夹具失败保留；响应头200不能证明流式正文完成。

首次默认试验1失败156.48秒：规划已完成，模型合法选择由完整八步正式用例内部准备和清理；执行任务缺task_id/path，在ApiTestTask校验处失败，业务HTTP为0，原5次物理请求及错误/PG输入保留。注册task此前仅是object，未向模型提供真实必填字段。现在注册输入使用已有ApiTestTask生成Schema并提升$defs到参数根；原campaign接口仍保留。运行时仅捕获执行器创建前的ValidationError，返回可靠failed、字段位置/类型及http_request_sent=false，沿原Job观察/终态保存；不把网络丢返回转为可重试失败，不补写原终态。

真实失败任务回放、原生创建两条/查询第一条/逐条清理、规划契约与字面量引用及真实PG失败保存/原请求恢复：24通过9.86秒。原模式/请求体回归16通过7.16秒；真实未知创建/丢PG领取回执3通过10.36秒；受控模型的独立生产部分/全部清理2通过105.61秒。首次24项批次1失败22通过1跳过为测试未排除模型description；第二批1失败23通过为复用fixture没有启用operation策略；修正测试后24通过，原日志保留。未知专项最初选错节点、0收集，修正为已查到的节点后3通过。没有新增依赖、配置键或数据库结构；配置仍MySQL，业务事实仍PG，大对象仍RustFS。

修正契约后的默认模型新试验仍失败：1失败207.57秒，原180秒规划预算耗尽，未创建测试运行、未发业务HTTP。PG保留前两轮完整响应和第三轮interrupted；3次物理发送均收到响应头，第三轮无可靠完整响应，不记作成功。第一轮读文档并查目录；第二轮已有api-test-runner身份，却再次用tools搜索名称，未取得tool完整契约；第三轮在截止前中断。冻结seed实际含新task必填Schema。不能以受控生产通过或增大预算代替默认自主验证，不重复原配置试验。

完整正式用例内部准备/清理也是合法执行方式，测试不强求拆成prepare；若有cleanup_sources声明仍要求覆盖通过，同时对所有实际创建/删除ID逐一核对PG可靠返回与现场剩余数据。后一默认试验未进入执行，此路径尚未验收；当前覆盖仍依赖计划声明，同一正式项内部混合创建/清理的服务端全对象判定仍需研究，不能声称已实现。

原始证据为p4-default-coverage-*与p4-default-task-*，SHA/大小、历史保留和三份方案同步见[p4-default-task-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-default-task-validation.json)。本轮后端实际运行命令在IMPLEMENTATION.md；前端没有改动，未重跑。完整App、默认自主执行、共享实际消费完成、未知只读核对、运行修订和其余P0—P5继续推进，目标未完成。


### 6.52 固定工具查询导航及旧任务恢复〔源码基线b134f063〕

先核对上一轮实际PG响应、固定工具目录/查询、Workflow、Prompt及持久seed，研究见[P4_TOOL_NAVIGATION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_TOOL_NAVIGATION_RESEARCH.md)。已知api-test-runner后再次搜索名称只返回目录，无法取得契约；不能把这个流程继续失败归因于业务条件。新建覆盖规划Prompt为case-planning-effect-coverage-v2；固定seed版本决定导航语义，旧v1任务保留原目录和查询行为，不读取当前注册/Prompt替换历史。

新目录给每项detail_query，不把目录当已读Evidence。Prompt指引已知key直接读tool并批量读取独立文档/工具。非空搜索在完整固定目录中唯一匹配、且当前页包含它时，返回同一完整tool契约及contract_loaded；空搜索、多匹配、空页不自动猜选或伪造Evidence。直接详情与唯一搜索共用既有Schema/hash，完整内容仍受原上下文限制，不截断、不改变预算。只扩展内部导航与日志，没有新增依赖、可编辑配置键、数据库结构或前端接口；已有开关继续MySQL，固定事实与Prompt版本继续PG，大对象继续RustFS。

保存的真实失败查询、唯一/多匹配/空页/旧目录、引用hash及只读副本、真实文件上传、MySQL策略冻结与PG原任务恢复专项：8通过8.31秒。扩展规划/持久任务回归42通过、1旧外部模型开关未运行，43.15秒；其中新v2两轮完成、旧v1三轮完成，修改当前注册/策略不影响原seed，终态再投递没有新模型调用。受控模型的独立生产部分/全部清理2通过97.73秒。独立配置、源码编译及diff核对通过；前端无修改未重跑。

默认自主试验仍失败：1失败201.18秒。第一轮32.946秒正常，模型直接批量读取项目原文、api-tester和api-test-runner三个详情，已消除目录重复查询。第二轮111.379秒、completion_tokens=8192、finish_reason=length，可见正文为空，规划校验拒绝；第三个修正回合35.179秒后总180秒截止并interrupted。三次物理请求对应三个不同逻辑回合，请求SHA各不相同，本场景没有同一回合重复发送证据。未创建测试运行，业务HTTP仍为0；收到响应头不等于完整响应。不能将导航修正或受控生产通过称作默认执行通过，不增加预算或重发旧任务来掩盖失败。

原试验参数、三份原请求、PGseed/检查点、任务终态、实际文件和过程日志完整保留于p4-tool-navigation-*；核对见[p4-tool-navigation-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-tool-navigation-validation.json)。针对提供商的step-5-preview/max_tokens及reasoning_content官方域名检索未获得可用结果，未据此修改模型参数。下一步需核对实际SDK响应字段及输出额度/空正文处理；不凭猜测宣称隐藏推理或提供商行为。默认自主执行、正式项内部混合创建/清理的全对象判定、共享消费完成、未知只读核对、运行修订及其余P0—P5仍待实施，完整目标继续保持。

### 6.53 规划响应完整性、实际恢复与新默认试验〔源码基线4a77cf98〕

上一轮更新方案属于progress；本轮继续研究和实施。核对原失败PG响应、第三轮物理请求、ModelRuntime、规划Workflow、影响链路分类及本地官方OpenAI SDK类型，见[P4_PLANNING_RESPONSE_COMPLETION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_PLANNING_RESPONSE_COMPLETION_RESEARCH.md)。SDK正常返回的mode=ok不等于业务正文完整；原length/空正文错误进入JSON修正消耗下一逻辑调用，不能将不同回合称作SDK重复发送。

新同步/持久规划seed冻结响应协议；None字段序列化省略，原实际旧seed字节结构不增加默认字段。response阶段先处理原调用错误和工具调用，再按项目已有length/max_tokens及空白正文条件判断。截断即使JSON外形合法也拒绝；普通坏JSON仍可修正，合法资料查询仍按原回合进行。新拒绝轮保存response_rejected、具体分类、协议、原元数据与正文；持久检查点和同步最终计划均保留依据，日志不输出正文。没有增加全局failure mode、依赖、MySQL配置键、数据库列或公开API；算法协议属于固定代码版本，可编辑参数仍MySQL，seed/检查点/计划仍PG，实际上传原件仍对象存储。

实际回放6通过、2个真实存储开关未启用，4.65秒。启用真实存储后四模块回归50通过、1旧外部模型测试未启用，55.08秒；流式/非流式实际HTTP和官方SDK响应先提交PG，再模拟提交回执丢失，恢复不发第三次请求，原deadline及终态无新调用，完整八步JSON/False/0/null和MD原字节读回一致。独立main/API/规划CLI/执行CLI的受控部分/全部清理2通过108.14秒，对应RunItem分别blocked/passed；两份运行均completed表示已经结束，不代表所有条目通过。之后补充同步拒绝正文保存检查，首次1失败3.76秒（KeyError: response_text），保留原失败；修复后最终专项9通过10.20秒，含两种真实HTTP/SDK/存储恢复。最终源文件编译和diff检查通过；前端没有修改未重跑。

新的默认模型试验1失败203.43秒，终态失败、独立Worker退出0。固定response_protocol实际存PG；模型全局配置及原180秒任务预算未改变。第一轮27.332秒读文档/api-tester，第二轮55.453秒补读api-test-runner，两轮均stop/合法query；第三轮96.745秒后被总截止时间中断，没有可靠完整响应。三份物理请求对应三个不同逻辑回合、SHA不同，没有JSON修正回合；收到响应头不证明第三轮流完成。没有运行、业务HTTP为0。本次默认试验没有触发截断拒绝分支；不能拿它代替受控分支验收或称默认自主执行通过。默认试验在最后的同步正文保留字段补齐之前运行，该补齐不改变分类/发送规则；最终定向实测单独记录。

原请求、原件、PG事实、过程日志及失败完整保存于p4-planning-completion-*，核对见[p4-planning-completion-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-planning-completion-validation.json)。提供商无可见输出和规划用时仍需实际SDK字段/阶段观察，不能根据reasoning_tokens=0推断隐藏字段是否存在。下一步继续核验默认规划/执行，并补正式项内部混合创建/清理覆盖、共享消费、未知只读核对、运行修订、正式模板/导入产品、DeepSeek录制、Docker性能/安全及历史迁移等完整范围。旧未知发送、终态和原失败均保留，不提高预算或重发原终态任务掩盖失败。

### 6.54 实际SDK字段观察与默认规划截断拒绝〔源码基线a2ed767b〕

本次直接更新项目方案、所贴附件及选中PLAN，三份保持相同字节；上传/Git边界、生成模板用途、项目隔离与补全检测继续按首页及2.3、3.1执行。研究见[P4_SDK_STREAM_OBSERVATION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_SDK_STREAM_OBSERVATION_RESEARCH.md)。本步只改测试探针和测试消费方，不改生产源码、全局模型参数、数据库结构或可编辑配置；不将它称为正式P0产品追踪完成。

测试探针使用原官方SDK/HTTP，只记录字段计数、耗时、finish_reason和数字usage；通过SDK调用身份关联物理重试，正常正文、工具delta与异常原样传播。reasoning/refusal正文、认证头、响应体和错误字符串不保存，未知字段不保存名字或值。首次回归24通过12.80秒，保存受控观察7通过11.90秒，增加持续500检查后的最终专项8通过14.92秒；这些批次重叠，不能相加。实际正常正文、工具JSON的0/False/null、3次物理HTTP/1次SDK重试、持续500耗尽、消费中断不重试及取消均核对。

新的默认自主试验1失败168.47秒。真实PG固定协议case-planning-response-completion-v1，任务终态failed，Worker退出0。第一轮21.447秒正常stop，批量读文档/api-tester/api-test-runner；第二轮117.737秒，length/8192，可见正文0。第二轮SDK在1.436秒收到首块，共3707块，两种reasoning别名各31500字符，117.730秒迭代结束。只保存计数，不保存这些字段内容；别名字符不相加当Token。旧常规/low试验的原块SHA亦核对，usage.reasoning_tokens=0不能证明没有这类字段。

两次逻辑调用对应两个SDK调用、两个不同物理请求。实际触发6.53完整性拒绝，保留响应依据、不进入JSON修正或第三轮、不创建Run；业务HTTP及新建业务ID均为0。原配置和180秒预算保持，旧终态/未知发送不重发。SDK迭代结束不等于业务完成；默认规划仍未产出可执行计划，此试验不能称为默认执行通过。

第二轮请求32689字节，系统Prompt9258字符，api-test-runner查询结果9566字符。这些尺寸是下一步研究线索，尚不能证明输入复杂度是根因。下一步先核对完整用例、工具契约与规划职责的实际消费者，再评估按具体操作提供固定契约依据或分阶段规划；不能先截断完整用例、伪称部分契约为完整接口、使用reasoning代替最终计划，或只提高预算重复原方式。采用新阶段/协议前须验证旧seed、引用hash、未知发送和终态恢复，保留准备/目标/清理的完整证据。

实测原文件、物理请求、PG事实及过程日志为p4-sdk-observation-*；核对见[p4-sdk-observation-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-sdk-observation-validation.json)，实际命令见IMPLEMENTATION。前端无修改未重跑；完整App、默认业务执行、正式项混合创建/清理、共享消费、未知只读核对、运行修订、模板/导入产品、DeepSeek录制、Docker性能/安全和历史迁移仍需推进。完整P0—P5目标未完成。

### 6.55 指定操作的固定契约读取、旧版本恢复与默认新试验〔源码基线eae3393a〕

先核对原失败seed、完整工具注册、原生runtime、真实HTTP输入/输出及本地Agent轨迹课程，见[P4_OPERATION_CONTRACT_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_OPERATION_CONTRACT_RESEARCH.md)。api-test-runner同时包含Campaign接口与execute_task输出；本步新增明确operation读取，完整tool读取保留，不按业务目标自动猜入口。操作证据显式标注named-operation、父契约hash与独立来源身份；完整32字段ApiTestTask由输入/输出共同引用一份，不删除业务内容或模型字段。

新覆盖规划Prompt为case-planning-tool-operations-v3，seed冻结tool_operations。PlanningQuery的可选operation、模型解析、同步/异步服务、Workflow与前端内联查询类型成对同步；None序列化省略，保存的旧v2完整seed重序列化相等，旧v1/v2恢复维持原导航。目录只提供操作详情查询，不伪造已读证据；未知操作/旧快照不能静默降级成完整tool读取。无需新依赖、可编辑配置或数据库列，策略仍MySQL、固定事实仍PG、原件仍RustFS。

首次回放/实际原生HTTP8通过、3真实存储未启用，7.43秒。六模块真实批次59通过、1旧外部模型跳过、3失败，63.66秒；三个失败均为证据导出缺少QA_STATEFUL_RESEARCH_EVIDENCE_DIR或QA_PLANNING_COMPLETION_DIR的KeyError，保留原失败。补齐运行参数后单独三项12.80秒通过，没有修改业务源码掩盖失败。新读取、同回合完整与操作证据共存、真实上传原字节、完整八步/False/0/null、配置变化后的固定恢复及v1/v2终态无重发均实际核对；旧完整seed序列化追加检查1通过4.61秒。

受控独立main/API/规划CLI/执行CLI2通过111.90秒：部分清理RunItem为blocked且留1条记录，全部清理为passed且无遗留；两份Run的completed仅表示已结束。前端相关页面19通过2.68秒，vue-tsc与Vite构建退出0（Vite11.06秒）；未完成完整App及默认业务验收。

新的默认试验1失败203.46秒。第一轮17.304秒正常读文档/api-tester；第二轮54.609秒正常同时读execute_task操作与完整api-test-runner；第三轮107.380秒后原180秒截止，interrupted。三次逻辑调用对应三个SDK调用、三个不同物理请求，没有JSON修正回合或同回合重复发送证据；未创建Run，业务HTTP及业务新建ID均为0。第三轮SDK1.736秒已收块，共3206块，可见正文0，CancelledError，迭代未完成、无finish_reason；最新usage=7899不是完整最终用量，不判定为length返回。

保存失败契约中完整payload8827字符，指定操作payload4902字符，完整模型仍32字段；但实际模型同时读取两者，第三轮请求39801字节。因此不能宣称实际输入减少或默认失败已修复。连续默认规划失败是职责/协议需要重审的架构信号，不继续只加提示、加超时或重复同一流程。下一步研究逻辑选取/衔接与技术证据声明的分阶段协议，逐一检查固定计划、检查点、引用、预算及恢复消费者，不能在研究前弱化覆盖校验或丢弃完整用例。

原文件/PG事实/物理请求/失败/独立过程日志保存于p4-operation-contract-*，核对见[p4-operation-contract-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-operation-contract-validation.json)，实际命令见IMPLEMENTATION。全局模型配置、预算及历史终态/未知发送保持。默认自主执行、正式项混合创建/清理、共享消费完成、未知只读核对、运行修订、P1正式模板/Excel导入产品、DeepSeek录制、Docker性能/安全、历史迁移和其余P0—P5继续推进，完整目标未完成。

本批首次提交核对的原始字节检查已通过，但两个原SSE响应的协议末尾空行触发diff检查；命令脚本没有及时停止，仍产生144f7bcb提交。失败另存，原响应不修改。随后补齐.bin二进制属性，重新核对已提交及暂存原字节、提交范围和diff，追加纠正提交；这项记录修订不代表默认模型失败已解决。

### 6.56 本次完整方案更新、无Git回归及分阶段状态核对〔基线ebe3f7cf〕

本次直接修改首页、1.3、2.3.1、3.1和5.2，并同步用户所贴附件及原选中PLAN。执行链保留“理解需求→当前项目选取→初排→简单检查条件来源→必要补全→最终排序/冻结→现有Agent执行”；生成模板不进入执行上下文。新增2.3.1说明逻辑规划和执行证据绑定各自的职责、固定记录、恢复、共享预算与验收顺序，未增加通用前置检查清单。

再次核对普通上传路由、`upload_document`、`upload_version`、固定快照比较及Git独立导入：普通上传直接保存原件/数据库版本，不读取仓库；Git入口只读取得指定commit/blob后调用相同保存服务。上传与Git不自动合并历史，追加版本要求用户指定同一文档及父版本。

本次实际执行（工作目录Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -Xutf8 -m pytest 'tests/test_api_doc_versions.py::test_actual_files_version_chain_replays_and_current_content_dedup[False]' 'tests/test_api_doc_original_assets.py::test_actual_openapi_original_survives_conversion_and_project_url_updates[False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/plan-user-latest-update.xml
# 退出0：2通过、0失败、0跳过，5.57秒。
```

这两项使用真实文件、应用上传/版本路由及本地测试存储，验证原件保留、版本追加、同请求恢复、同名独立上传、旧内容恢复、父版本冲突及派生修改不覆盖原件。没有配置Git来源；本次未运行生产MySQL/PG/RustFS、独立解析/比较进程、正式App或默认模型，所以不据此新增这些方面的通过结论。原记录见[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-user-latest-update.log)、[XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-user-latest-update.xml)。

分阶段实现来自本次开始前已有工作区改动，仍未提交、未验证；本次没有修改或提交这些业务文件。前端和新协议的实测尚未完成，默认模型历史失败继续保留。完整P0—P5、人工录制、Docker真实证据及生产历史迁移范围不缩减。三份正文相同字节、源码依据、原有工作区文件未变及本次验证边界见[核对记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-user-latest-update-validation.json)。

### 6.57 逻辑规划与执行证据两阶段、真实恢复和默认失败〔源码基线c7d20303〕

实施前查原Workflow/完整_validate/PG租约与检查点、准备范围和真实v3失败，并读本地Agent轨迹、协议鲁棒性及评测工程课程。依据与采用方式见[P4_STAGED_PLANNING_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_STAGED_PLANNING_RESEARCH.md)。没有复制模型/HTTP循环、引入依赖、另建执行器或改变测试模式。

项目MySQL可选planning_stage_protocol=case-planning-staged-evidence-v1，缺字段继续旧流程；前端开关启用所需原策略，关闭任一前置清除阶段配置。新v4两份Prompt冻结入PGseed，第一阶段读取完整用例/业务资料，选择目标/必要前置/准备/清理与顺序；不填技术绑定。逻辑结束校验后固定决定/hash，不创建Run。第二阶段只为原item_key追加绑定、对象/清理证据及缺口；不能换用例、改instructions或依赖。合并后仍跑完整原校验，缺清理声明拒绝；无目标时不发送技术回合。PG阶段不得后退或重写固定逻辑，回合与deadline跨阶段共享。

实际验证（批次重叠不相加）：

| 原记录 | 真实结果与范围 |
|---|---|
| p4-staged-planning-first | 9通过、5真实存储未启用，5.96秒；实际旧v3检查点/生产计划回放，技术字段限制、完整八步/False/0/null与合并不改业务 |
| p4-staged-planning-live-first | 14通过、0跳过，12.68秒；实际MD上传/RustFS读回/MySQL/PG，逻辑提交丢回执后换attempt恢复不重复逻辑调用；改变当前策略和禁止注册重捕获仍完成原任务，deadline相同、终态无新调用；重算hash仍不能改固定逻辑；原回合超限、无目标及缺清理声明 |
| p4-staged-planning-regression | 99通过、1旧外部模型未启用，82.15秒；8模块，含旧查询/恢复、实际HTTP任务契约、响应完整性和固定运行创建 |
| p4-staged-planning-frontend-first / build-first | 前端26通过2.55秒；vue-tsc/Vite退出0，Vite9.57秒；未执行完整浏览器App |
| p4-staged-planning-production-final | 2通过、0跳过，103.98秒；正式鉴权API、MySQL策略、完整八步JSON/实际原件、PG原seed、独立规划CLI/执行CLI及原SDK/原生业务HTTP |
| p4-staged-planning-default-first | 1失败，201.49秒；原默认模型及全局参数不改，独立Worker终态退出0、任务failed，详情下述 |
| p4-staged-planning-default-replay | 新真实失败回放1通过7.01秒；复现准备自引用拒绝及正式项缺少清理来源字段，不代表业务失败修复 |
| p4-staged-planning-export-isolated | 2通过37.19秒；无导出环境变量时实际独立Worker/CLI保存到自身临时目录，项目两份汇总本批内容未再改写 |

受控生产两场景均4次规划回合，logic读文档/完成、effects读原生操作/完成；当前MySQL策略提交后关闭，PG固定v4仍执行。部分/全部场景分别10/11次物理模型请求，4/5次业务HTTP；创建两条只清一条为blocked且剩1条，全清理为passed且剩0条。完整JSON及自身说明进入原Agent上下文，模板未注入；重启API及终态重投保留原结果/对象且无新发送。Run的completed仅表示结束，不等于各条目都通过。

首批受控生产2失败57.54秒：测试夹具仍断言规划只有2次，实际两阶段4次，未开始业务。第二批2失败94.22秒：业务部分/全部结果已正确保存，但运行开始时加载的旧总请求数8/9与实际10/11不符。两处计数都按原/新协议同步后第三批2通过。第二批期间读取并修正了另一处源码断言，pytest显示的源码行可能已是新字面量，但实际失败输出10==8/11==9来自已加载旧测试；不把它描述为新断言失败。业务源码、全局参数和原原件未在这三批间修改，失败/过程/实际请求全部保留。

新默认实际三轮均在logic：47.344秒正常query读原件；119.377秒返回1283字符的完整JSON、finish_reason=stop，但把prepare_orders自身写入其cleanup_sources，现有范围校验拒绝；第三轮12.577秒后总180秒截止，interrupted，无完整可靠正文。三个不同逻辑/SDK/物理请求，不是同回合重复发送；第三轮为合法校验修正尝试，没有业务HTTP或Run。第二轮不是截断返回，第三轮也不能因最新usage判成length。

实际候选表达“准备创建两条、目标查询后清理，准备不提前删除”。源码查证FormalCasePlanItem没有cleanup_sources，现有范围函数只识别prepare清理；每项只有一个produced/cleanup角色声明。保持自引用拒绝，不自动删除关系或重写完整目标JSON来制造通过。正式清理关系及项内多角色属于架构缺口，下一步研究见[P4_FORMAL_EFFECT_RELATIONS_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_FORMAL_EFFECT_RELATIONS_RESEARCH.md)。分阶段已能取得逻辑正文，仍不能宣称解决默认超时或自主业务闭环。

此外，本批首次交付校验因旧独立Worker夹具未指定导出目录而停止：它覆盖了原有两份p2-planning-worker汇总JSON。新生成内容另存，本次未找到原字节副本，未提交这两个文件；旧12002份正式原始证据仍逐项匹配。已将默认导出改为测试自身临时目录，避免缺配置时写项目历史目录；失败审计见p4-staged-planning-validation-first-failure.json。两份汇总的原字节未恢复这一限制继续明确保留。

实际命令、stdout/stderr及限制同步[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)，完整核对见[p4-staged-planning-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-staged-planning-validation.json)。此前12002份原始证据已重新核对SHA/大小，原默认失败与unknown/终态保留。完整App、正式混合效果、共享消费完成、未知只读查证、计划修订、模板/Excel导入产品、DeepSeek录制、Docker性能/安全、生产迁移及P0—P5其余项仍未完成；阶段编号和局部通过不改变总目标。


### 6.58 用户所贴方案的直接更新与正式效果关系设计〔核对基线18f9fccc〕

本次直接更新首页、2.3.2及P2推进顺序，同步仓库完整PLAN、用户附件及原选中PLAN。执行保留“理解需求→当前项目选取→初排→AI判断必要条件来源→按依据补全→最终排序/冻结→现有Agent执行”；模板只生成。上传仍独立保存版本、原件和解析快照，Git只在用户选择来源入口时只读取得文件。

进一步核对正式/准备项Schema、范围归一化、声明编译、原ToolJob可靠返回、PG不可变JSONB、对象清单、清理覆盖、只读assessment、回归裁剪、运行归属和前端面板。新增2.3.2说明项依赖与项内效果来源的区别，正式清理归属、多声明的固定来源/操作匹配、完整消费者改造、旧任务兼容及真实业务验收顺序。它是待实施工程设计，没有放宽旧自引用拒绝，没有修改业务源码、旧Prompt、默认模型或原失败任务。

本次实际执行5个指定节点：**5通过、0失败、0跳过，6.41秒**，见[原XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-formal-relations-update-tests.xml)及[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-formal-relations-update-tests.log)。两项上传测试使用临时实际文件、上传路由和本地测试存储，核对版本/原件/请求恢复及派生不覆盖；三个规划测试读取保存的真实seed/检查点、默认失败候选及受控生产固定计划，核对旧字段往返、非法自引用/正式字段缺口和两阶段合并。这三个是离线事实回放，不是新模型推理或新业务执行。

本次未重跑MySQL/PG/RustFS生产存储、独立Worker、默认模型、前端构建或完整App，因为只修改方案/研究说明及其验证资料。6.57的已提交功能与默认失败仍按原批次保留，不累计为本次通过。共享消费完成、正式项内多效果、未知只读查证及完整P0—P5升级仍未完成。

三份字节同步、源码依据/本地链接、原始证据及无关工作区保护核对见[本次验证记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-formal-relations-update-validation.json)。6.57披露的两份Worker汇总原字节未恢复；本次只验证其当前字节未再次修改，不将它写成原历史已恢复。


### 6.59 正式清理归属、多效果v2及默认新任务〔源码基线d81b25e8〕

先读实际默认失败、完整用例、Schema、规划/恢复、范围/归属、原ToolJob、PG不可变对象证据、全运行覆盖、assessment、回归裁剪及前端消费方；依据及采用方式见[P4_FORMAL_EFFECT_RELATIONS_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_FORMAL_EFFECT_RELATIONS_RESEARCH.md)。新契约采用独立v2项/决定与四项一致策略；保留v1字段、序列化、Prompt、unknown及终态。没有引入依赖、第二执行器或新数据库表/列。

逻辑阶段明确由谁清理；正式目标可引用自身产生来源，项内引用不会变成depends_on自环。证据阶段为固定项追加多份声明，清理引用固定产生声明；输入匹配/固定预期仍由完整正式content或prepare.instructions的字面量路径提供。原ToolJob只保存一份可靠返回，多声明各自记录匹配、对象和验证结果，原对象清单保留每次实际创建；清理覆盖核对来源、拥有者、typed值及时间。当前预期不能直接引用仅在文档原文中的常量，这一适用限制保留，不能往原用例补造字段使默认任务通过。

| 实际批次 | 结果与边界 |
|---|---|
| p4-formal-effects-live-first / live-fixed | 各2失败、8通过，21.56/18.03秒；测试先误判未知重试应抛异常，再误判应返回running。查实际PG契约后修正断言：原Job仍running；重试为outcome_unknown的denied别名、action_sent=false，没有再次发送。产品未知操作语义未放宽，两轮失败保留 |
| p4-formal-effects-live-final | 10通过、0跳过，20.51秒；六项真实配置/存储/HTTP场景及三个旧事实回放；随后仅去掉声明重复编译，最新源码另由下批验证 |
| p4-formal-effects-current-final | 12通过、0跳过，24.86秒；六项真实业务场景、原默认候选回放、四项实际返回故障回放，以及真实PG阶段丢回执/换attempt恢复；冻结逻辑不能改写，当前策略改变不升级原任务，原deadline/四回合/终态保留 |
| p4-formal-effects-v1-regression | 61通过、0跳过，54.27秒；旧对象证据、覆盖、准备范围和两阶段规划模块，含实际HTTP/PG恢复及旧序列化/Prompt |
| p4-formal-effects-frontend-final / build-first | 两组件38通过2.13秒；vue-tsc和Vite退出0，Vite9.79秒。策略开启/前置关闭及实际v2覆盖展示已验，未执行完整浏览器App |
| p4-formal-effects-default-first | 严格验收1失败、0跳过，215.55秒；实际全局默认模型、鉴权API、独立规划Worker及原180秒总预算，终态failed，无Run/业务HTTP |

六个真实场景均保存两次已知创建。内部全部及外部prepare全部清理为passed，现场无剩余；内部只清一条为blocked、覆盖1/2且现场剩1条；未知创建额外现场产生一条，未知删除现场已删除但无可靠结果，两者均blocked且重试别名不再发送；覆盖预算不足仍显示known_object_count=2、blocked。原原件RustFS SHA/字节、完整八步JSON、False/0/null、固定策略、原Job/观察、Result及现场HTTP分别保存。新受控手工用例有明确的证据契约字段，不代表默认原业务用例能够自主完成同样绑定。

默认新任务四次逻辑调用对应四个SDK调用和四个不同物理POST；阶段为logic、logic、effects、effects。前两轮15.647/106.085秒，第二轮stop正文1727字符，正确冻结“prepare创建两条→正式target查询并清理”，cleanup_sources在target，没有此前prepare自引用错误。第三轮56.697秒、stop正文324字符，请求读取原生execute_task完整操作契约；第四轮0.984秒后原总deadline取消，interrupted。前三轮SDK迭代结束，第四轮create被取消；不将取消描述为完整响应或length。整个任务未完成技术绑定，严格业务验收失败，无Run及业务HTTP；不能据此声称默认自主执行已修复。

下一步先按该实际时间线研究逻辑/契约读取与技术绑定的职责、预算及固定预期来源，保留完整用例和工具结构；不原样重复超时任务、不只加超时、不补造预期字段。共享消费完成仍false，未知只读查证、计划修订、其他模式/完整App、P1模板与Excel导入产品、DeepSeek人工录制、Docker性能/安全和历史迁移继续。三份方案同步、原始工件及无关文件保护核对见[p4-formal-effects-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-formal-effects-validation.json)。此前两份Worker汇总原字节仍未恢复，本次只保护其当前字节。完整P0—P5目标保持未完成。


### 6.60 固定文档预期、真实原件/HTTP/PG恢复〔源码基线786b5b88〕

先核对原默认用例、需求原文、实际PG/物理请求及声明/阶段/上下文/原返回/覆盖/前端所有消费者，再采用独立effect_schema=3与可选来源协议。策略MySQL；新Prompt v6、引用、阶段和原返回证明PG；原件RustFS。没有新增依赖、数据库表列或执行器；旧v2的Prompt由原保存seed逐字核对，不原地升级旧任务。

| 实际记录 | 结果与适用范围 |
|---|---|
| p4-expected-source-replay-first | 2失败、10通过5.70秒；测试错把DELETE中的D视为重复、把nullable=null里的null视为唯一。原拒绝保留，改为E歧义/明确=null摘录，不放宽产品校验 |
| p4-expected-source-replay-fixed | 12通过、0跳过3.53秒；实际默认原文/原JSON回放、出处/typed值、原Prompt、缺来源/hash/改payload/歧义/解码及唯一预期 |
| p4-expected-source-live-first / live-final | 25/26通过、0跳过28.47/26.83秒；真实MySQL/PG/RustFS/native HTTP，新增文档产生/清理、当前版本变化后的固定执行及v6持久恢复，旧多效果/unknown保持；批次重叠不相加 |
| p4-expected-source-logged-final | 3通过、0跳过9.85秒；补结构化来源编译日志后重验文档全部/部分清理和v6阶段丢回执恢复，最终源码已验 |
| p4-expected-source-regression-first | 61通过、0跳过55.12秒；旧对象证据、覆盖、范围和两阶段模块，含真实存储/恢复 |
| p4-expected-source-frontend-first / frontend-final / frontend-fixed | 先41通过2.86秒；新增真实计划展示后1失败41通过2.29秒，夹具返回plan而实际计划ID不同，页面正确拒绝；改为匹配原ID后42通过1.86秒。原失败保留 |
| p4-expected-source-build-first / typecheck-final | vue-tsc/Vite退出0，Vite8.92秒；新增真实计划展示夹具后再运行vue-tsc退出0，无完整浏览器验收 |

两个新增业务场景均保存原八步及False/0/null，无证据契约新字段。实际POST两次、GET一次；全清DELETE两次、passed、剩0；部分清DELETE一次、blocked、覆盖1/2、剩1。文档V2已上传但原intent/检查/选择证明仍是V1，原件/版本hash及字符/行位置可查。v6持久阶段提交丢回执后，当前文档/配置改变，禁重新捕获工具注册；新Worker attempt2使用原seed/逻辑/hash/deadline完成原四次调用，终态无新调用。受控模型用于流程验证，不代替默认自主能力。

本次没有重跑外部默认模型，因为阶段耗时机制未改，历史四调用超时仍保留。下一步研究如何在逻辑确定后按明确声明的操作读取固定契约，减少模型只为读取接口结构消耗的回合；须先验证完整契约、原seed/阶段/截止时间和unknown，再作新策略。不得凭HTTP方法猜工具、截断完整用例或仅加超时。

完整App、共享消费完成、多清理通道组合、未知只读查证、计划修订/其他模式、P1正式模板/Excel导入、DeepSeek人工录制、Docker性能/安全及生产迁移仍未完成。三份正文、实际原文/对象/引用、源代码/原失败/前端真实夹具、上批原始证据和无关文件保护核对见[p4-expected-source-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-expected-source-validation.json)。旧Worker汇总原字节未恢复，不将保护其当前字节写成恢复历史；完整目标继续active。


提交前字节核对首先停止于真实计划前端夹具：Git仅将暂存CRLF转LF，原来源未变。为该夹具增加-text后，diff检查又报告原CR；按原工件属性加入cr-at-eol识别。核对发现Git仍复用此前暂存的LF字节，随后只对该夹具执行git add --renormalize，重新核对暂存原字节、范围和diff。两项失败保留于p4-expected-source-stage-first-failure.json和stage-diff-first.log，检查失败期间未提交。

### 6.61 明确操作读取、原查询恢复与新默认失败〔源码基线e4d268e8〕

先查原默认PG/实际请求、查询与阶段服务、固定政策/seed、PG检查点校验、Agent上下文及前端；研究先存P4_DECLARED_OPERATION_PREFETCH_RESEARCH.md。新配置仍在MySQL原规划政策，任务和来源/查询/固定证据仍PG，原件RustFS；没有新增依赖、表列、业务工具参数或恢复引擎。前端开关保存一致策略，关闭前置移除新开关。

| 实际批次 | 结果与边界 |
|---|---|
| p4-operation-prefetch-live-first | 10通过11.15秒；实际旧失败回放、原v6字节、声明缺失/去重/不能猜测，以及真实队列和读取丢提交回执恢复 |
| p4-operation-prefetch-live-final | 11通过10.10秒；最终源码另验九个明确操作分两批完整读取，八个不存在操作保留明确反馈而不制造额外模型轮次 |
| p4-operation-prefetch-regression-first / regression-final | 48通过34.84秒；分批追加后旧阶段/操作模块再次22通过9.61秒；批次重叠不累计覆盖 |
| p4-operation-prefetch-business-first / business-fixed | 首1失败6.73秒，受控回执引用prepare.expected而夹具遗漏此说明；按已读原文返回201补齐prepare说明后1通过6.27秒，不向正式JSON补字段、不弱化校验。原失败仅保存日志/XML，没有完整业务工件 |
| p4-operation-prefetch-frontend-first | 45通过2.43秒，项目策略保存和前置关闭清理 |
| p4-operation-prefetch-build-first / build-fixed | 首子进程日志完整到built10.02秒，但父探针打印GBK编码异常，未成功报告退出码；修正探针UTF8并在打印前保存command退出后，vue-tsc/Vite退出0，Vite7.51秒 |
| p4-operation-prefetch-default-first | 实际默认模型1失败213.76秒，规划任务failed/Worker退出0；5逻辑调用，0业务HTTP、0运行，未进入effects或系统契约读取 |

实际存储专项保存原完整八步/false/0/null，原件RustFS字节读回、固定操作32字段模型、MySQL开关、PG原seed/截止；查询排队或读取提交丢回执后attempt2恢复，两种均只三次模型调用，终态不新发。未知明确操作反馈后模型可改读登记操作；九个声明分批没有遗漏，错误来源保留。最新业务专项的同步服务三轮规划、POST两次/GET一次/DELETE两次、两个可靠对象覆盖passed、现场0剩余；计划后上传V2和关闭当前政策，固定执行仍用V1。

默认五次逻辑调用分别20.068/50.487/61.009/27.478/20.437秒；第2—4轮均计划引用了未读取的来源依据。三个原候选把document证据ID的16位摘要换成了该payload的完整64位hash；原可用ID与原候选分别保留。第5轮截止中断，未创建Run；5逻辑/物理请求和SDK完整观察保留，不重置该任务。原默认模型参数不变。该连续错误是引用契约表达的架构问题，下一步先核对来源ID生成及所有消费者，研究明确一致的引用表示和版本兼容；不能按模糊前缀接受错误引用、再追加一句提示或只加预算重复原任务。

全部功能源码/原失败工件、物理请求和固定正文、三份同步及前批原证据的核对见[p4-operation-prefetch-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-operation-prefetch-validation.json)。本次没有验完整App、共享消费完成、未知只读查证、计划修订/其他模式、P1模板及Excel正式流程、DeepSeek人工录制、Docker性能/安全或生产历史迁移。旧Worker汇总原字节仍未恢复，本次保护当前字节，完整P0—P5继续active。

### 6.62 一致来源身份、三格式实际持久化及新默认阶段证据〔源码基线5b5490ac〕

先读完整目标、原五条物理消息及三次引用失败、来源/阶段/工具契约、PG检查点、声明/Agent上下文和前端，再采用版本化一致身份。依据→做法见[P4_SOURCE_IDENTITY_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_SOURCE_IDENTITY_RESEARCH.md)。可编辑策略MySQL；新Prompt/固定版本/来源/查询/阶段PG；原件RustFS。未迁移历史、未引入依赖或第二执行器。

| 实际批次 | 结果与边界 |
|---|---|
| p4-source-identity-live-first | 24通过、0跳过23.35秒：5个离线来源/旧seed回放、3个真实MD/DOCX/PDF来源对照及原预读模块16项；新旧协议均测试队列/读取丢回执/批量/未知操作；批次重叠不累计 |
| p4-source-identity-sources-pg-final / sources-pg-fixed | 首3失败17.06秒：同步服务误用要求expected_parameters的持久模型夹具，实际返回planning_failed；查原调用后改用已有同步SequenceModel，3通过12.02秒。三种实际解析结果经模型上下文、MySQL固定策略和PG计划保存读回一致，no_match不建立Run；受控模型每格式2次，0业务HTTP，失败XML/log保留 |
| p4-source-identity-regression-first | 82通过、3跳过87.75秒：规划/任务/阶段/操作/文档预期/正式关系六模块。两项旧实时规划未开RUN_LIVE_CASE_PLANNING_TESTS、一项旧默认模型未开RUN_LIVE_CASE_PLANNING_MODEL；本批独立新默认实测另记，不用跳过代替通过 |
| p4-source-identity-business-first | 1通过12.39秒：新v8加操作预读，三轮受控规划，POST两次/GET一次/DELETE两次，两个可靠对象覆盖passed、现场0剩余；完整原JSON无证据字段补造，V2及当前策略变更后仍按V1执行 |
| p4-source-identity-frontend-first / build-first | 两组件48通过3.03秒；vue-tsc/Vite退出0，Vite10.96秒。开启来源策略不会自动开启操作预读，原180秒不变；关闭前置清除引用策略。未验完整浏览器App |
| p4-source-identity-default-first | 严格实际验收1失败、0跳过206.94秒；独立API/Worker退出0但规划failed，原180秒总预算。4逻辑/4SDK/4物理POST，无重复相同请求，0Run/业务HTTP |

新旧来源专项保持同payload/content_hash/source_id/version，文档16位和64位ID按固定策略明确区分；完整原件SHA/字节及解析快照不变，跨项目和未知协议拒绝。十个真实任务模式分别保存v7/v8seed、MySQL当前策略变更、attempt2恢复及原deadline；固定逻辑/hash与完整32字段操作契约保留，终态没有新增模型调用。业务工件的baseline字段沿用了原文档场景基线786b5b88；本次实际v8工作区由记录的源码摘要和prompt_version核对，不将该旧字段当本次代码基线。

默认四轮耗时18.666/61.435/25.208/74.048秒；前两轮已正确读取完整文档ID并冻结一个正式目标，cleanup_sources引用自身表示项内清理，不生成依赖自环。无validation_feedback及来源拒绝；第三轮仅请求工具execute_task操作契约，读取了完整固定模型；第四轮在original deadline取消，SDK迭代未结束、正文0字符，不能写成完整响应或length结束。四条物理POST均收到HTTP200头，最后一条流未完成；PG原seed/轮次、SDK及物理轨迹保留。全局模型step-5-preview、8192输出、原SDK/单调用策略不变。

本次逻辑没有prepare.instructions，因此旧prepare明确操作预读没有触发；手工受控业务成功不能代替默认自主成功。下一步先研究正式项内明确操作选择及固定查询队列，而不是按HTTP方法猜工具或为赶预算截断用例/接口；技术绑定阶段耗时另查证。不重复原失败任务、不重置unknown/终态、不直接加时限。共享消费完成、未知只读查证、计划修订/其他模式、P1模板/Excel正式产品、DeepSeek人工录制、Docker性能/安全、报告及生产历史迁移继续实施，完整P0—P5未完成。

三份完整方案、原件/PG/固定引用/物理消息、前批119份原始证据及无关当前字节核对见[p4-source-identity-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-source-identity-validation.json)。旧两份Worker汇总原字节仍未恢复，本次保护当前字节；没有推送或PR。

### 6.63 执行项操作选择、固定恢复与默认三轮实测〔源码基线07275986〕

完整目标及最新原失败、逻辑/Schema/查询/PG/Agent上下文和本地Agent轨迹课程先查证，研究存[P4_ITEM_OPERATION_SELECTION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_ITEM_OPERATION_SELECTION_RESEARCH.md)。新增原MySQL操作预读v2值及Prompt v9，独立逻辑wire和阶段选择/hash仍PG；原件RustFS。没有新依赖、表列、业务参数编译、运行/权限改写或旧任务升级。

| 实际批次 | 结果与边界 |
|---|---|
| p4-item-operation-live-first | 40通过、0跳过33.69秒；新选择9离线/7实际任务、旧来源8及旧预读16项，包含真实MD/DOCX/PDF原件/PG和原v5/v7/v8seed/Prompt回放；旧模块重叠不累计为新增覆盖 |
| p4-item-operation-choices-pg-final | 1通过5.50秒；加强固定选择专项，实际save_checkpoint事务拒绝替换后重hash及丢弃选择，原PG字节不变；换Worker仍原seed/截止/三次受控模型，终态不调用 |
| p4-item-operation-full-tool-final | 1通过8.03秒；operation=null读取完整固定tool，native输出模型32字段及原变体保留，未假装为单操作契约 |
| p4-item-operation-business-first | 2通过10.91秒；正式项内部创建/清理、prepare创建→正式清理；各三次受控模型，POST两次/GET一次/DELETE两次，两个可靠对象覆盖passed、现场0剩余，原完整JSON/False/0/null及V1依据保持 |
| p4-item-operation-regression-first | 84通过、3跳过101.05秒；六个既有模块与新增两个业务场景，实际存储/租约/恢复保留。跳过与6.62相同：旧实时规划两个开关缺失、旧默认专项一个开关缺失；本批独立默认另测 |
| p4-item-operation-frontend-first / frontend-fixed / frontend-actual | 首2失败52通过3.07秒，保存夹具回旧配置导致第二次关闭断言不符；改为回显实际提交策略后54通过2.52秒。新增真实内部场景的固定计划展示后55通过3.66秒，固定原件/八步及选择同屏可查 |
| p4-item-operation-build-first / typecheck-actual | vue-tsc/Vite退出0，Vite8.23秒；新增实际展示夹具后vue-tsc退出0。另一次typecheck-final探针误调vitest --help，只是帮助命令退出0，不能计作类型检查；原命令/日志保留 |
| p4-item-operation-default-first | 严格1失败、0跳过217.39秒，独立API/Worker退出0但任务failed；原180秒、实际3逻辑/3SDK/3物理POST，0Run/业务HTTP |

新选择任务实际验正式项、重复选择去重、未知操作反馈、九条分两批完整读取、queue/read提交丢回执、当前政策关闭和工具注册禁止重捕获、attempt2/原deadline/终态不新发。新的阶段hash绑定完整决定和操作选择；旧None按原hash且字段省略。最终FormalCasePlanItem/Agent上下文保持原v3，源件业务正文不补造证据字段；原生专项计划后上传V2仍核验V1预期和清理全部对象。

默认第一轮43.022秒读取case.source_refs已明确的项目文档；第二轮97.453秒按完整原件固定prepare_orders→query_first_order及清理来源，并为两项都选择api-test-runner.execute_task，系统去重一次只读完整32字段契约。第三轮38.808秒、已带固定原文/操作/选择/hash，在original deadline取消，正文0字符、流未结束，无validation_feedback。三条HTTP200仅证明收到响应头；最后流CancelledError，不写成完整响应/length。阶段及SDK统计保留推理字符数，不保存私有推理正文。

本次模型选择了prepare而v8选择了正式内部准备，都是完整需求可允许的逻辑表达；不为结果统一而改响应。系统预读实际有效，但逻辑耗时增加，不能推断整体加速或完整自主成功。原全局step-5-preview、8192输出、SDK/单调用配置不变，不重试已失败任务。下一步查证已有case.source_refs的明确文档能否在首轮前固定读取、如何保留版本/分页/原查询恢复，避免模型仅为请求已知原件花一轮；不能把项目全部文档盲注入，也不偷加预算。共享消费/未知只读查证、计划修订/其他模式、P1模板与Excel正式App、人工录制/DeepSeek、Docker性能/安全、报告及迁移继续，完整目标未完成。

三份完整正文、真实原件/PG/选择hash/物理消息、前批122份原始证据及无关当前字节核对见[p4-item-operation-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-item-operation-validation.json)。旧两份Worker汇总原字节仍未恢复，只保护当前字节；没有推送或PR。

### 6.64 固定关联原文、首轮只读恢复与默认两轮实测〔源码基线af790e15〕

完整目标、原失败/source_refs、生成来源、读取服务/分页返回类型、PG/Worker/前端及本地Agent轨迹课程先查证，研究见[P4_LINKED_SOURCE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_LINKED_SOURCE_RESEARCH.md)。MySQL原test_case.planning_policy增加可选关联来源协议；PG保存来源进度/基准及原模型调用，RustFS保存原件/解析。没有新依赖或表列，不编译用例为模式参数，不升级旧任务。

| 本批实际命令记录 | 结果与真实边界 |
|---|---|
| p4-linked-source-live-first / live-fixed | 首10失败6通过19.78秒：新夹具Skill版本为空违反既有草稿契约；修正夹具后16通过21.43秒，旧字节3/实际来源任务10/实际解析格式3。保留首次失败，不当作产品通过 |
| p4-linked-source-business-first | 3通过13.25秒：两种原生业务及加强实际PG拒绝改基准/删进度。各两受控模型，POST两次/GET一次/DELETE两次，两个可靠对象完整清理且现场0剩余；原八步、false/0/null和V1保持 |
| p4-linked-source-regression-first / regression-fixed | 首命令引用不存在的测试文件，exit4/0测试，不能记为通过。读取既有命令修正后119通过3跳过122.81秒；三个跳过仍为旧实时/默认开关缺失，独立默认本批另验。该回归开始于解析分页修正前，分页修正另验如下 |
| p4-linked-source-parser-page-fixed / live-final | 分页返回契约修正后实际三格式3通过9.79秒；新模块16通过19.89秒，均零跳过。实际附解析服务的ID-only捕获明确当前快照，重解析/V2后仍读取原捕获基准 |
| p4-linked-source-boundary-final | 新增三个实际边界3通过8.58秒：明确V1引用在queue丢回执及V2后仍读V1；两正式用例同源去重且保留双归属；错误资产明确失败不换当前内容 |
| p4-linked-source-legacy-version-first / identity-final | 新增真实时间戳来源专项先1失败10.70秒，DataError导致整项规划失败；沿现有impact_sources正式UUID身份核对，将旧时间戳保留为明确来源缺口、不改读当前版本。最终新模块20项与两种业务共22通过29.66秒、零跳过，涵盖最后源码 |
| p4-linked-source-formats-plan-final | 强化三格式3通过15.84秒：每种实际原件/进程解析/V2/重解析、原hash核对；首次受控模型读完整用例及当时解析，PG保存完整v10计划，与送达原依据一致。no_match不是业务通过 |
| p4-linked-source-frontend-first / frontend-actual / frontend-final | 设置56通过2.72秒；真实内部计划展示57通过4.61秒；原请求含不存在的CaseRender筛选，只实际运行CasePlanDialog，不能声称覆盖该文件。最后使用已查证的CasePlanDialog和CaseEffectCoveragePanel，61通过2.53秒 |
| p4-linked-source-build-first / typecheck-actual | 构建含vue-tsc，退出0/Vite9.04秒；新增实际来源夹具后实际vue-tsc退出0。完整浏览器App未重跑 |
| p4-linked-source-default-first | 严格1失败28.22秒，生产Worker已注入解析服务，list_snapshots实际返回分页对象而被当列表，TypeError；0模型/物理/业务HTTP/Run。沿存储实际返回修正为page.items，三格式补验后才另建新任务 |
| p4-linked-source-default-fixed | 严格1失败217.52秒；API/Worker退出0、任务failed，2逻辑/2SDK/2物理POST。首次已读原文，逻辑45.752秒完成正式项自身准备/清理及操作选择；证据133.732秒在原总deadline取消，0Run/业务HTTP |

sources预读不耗模型回合；ID-only来源在capture前V2发生时读V2且明确当前资料，带V1资产身份的同类队列恢复仍读V1，不能把二者混说。实际500行分页有has_more，下一次模型查询同一固定版本的末尾；非文档、跨项目、错误版本/资产/hash保留来源错误。current MySQL关闭后恢复仍原seed，capture/read丢回执后的固定基准不再选当前版本/解析，attempt2和原deadline保持；终态不调用。

新默认首次已送达当前关联MD全文及完整八步用例，固定正式target/self cleanup、明确execute_task，系统一次只读完整32字段契约。最后SDK流正文0字符、未结束、CancelledError，记录推理字符统计18877及输出用量6919，不保存私有推理正文；两次HTTP200仅证明响应头，不能称最后响应完整。原全局step-5-preview/8192输出、180秒及SDK/调用配置不变。原失败和unknown/终态保留，不重试同配置。受控两轮成功不能替代自主成功，也不能仅凭轮数减少宣称总耗时改善。

下一步先查证证据阶段的实际技术绑定工作量、已有声明与返回证据职责，才决定简化方式；不盲改模型或增加预算，不删完整用例/工具契约来获得绿灯。共享消费完成、完整准备状态、未知只读查证、计划修订/其他模式、P1模板/Excel正式App、DeepSeek人工录制、Docker性能/安全、报告及历史迁移继续，完整P0—P5未完成。

三份正文、固定原件/解析/PG/模型消息/业务现场、上批143份原证据及无关当前字节核对见[p4-linked-source-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-linked-source-validation.json)。旧两份Worker汇总原字节仍未恢复，只保护当前字节；没有推送或PR。

### 6.65 执行证据短引用与实际恢复/业务验收〔源码基线43f1700b〕

原目标、真实默认请求及实际成功声明、原编译/消费方、短引用先例和本地Agent轨迹课程先查证，见P4_EVIDENCE_REFERENCE_RESEARCH.md。发现系统固定身份与AI语义选择混在同一wire中；新适配器负责身份还原，原执行Schema/核验负责事实。没有编译为测试模式参数，没有改依赖或全局模型/超时，配置MySQL、任务/目录/回执PG、大原件RustFS。

| 实际命令记录 | 结果与边界 |
|---|---|
| p4-evidence-reference-replay-first | 8通过3失败14.82秒：三个新实时测试误导入不存在文件；真实成功声明回放通过。查明document_declaration位于现有test_case_effect_relations，修正导入，原失败保留 |
| p4-evidence-reference-live-fixed | 12通过1失败21.81秒：实际响应提交丢回执已发生，但新夹具误从公开任务摘要读取checkpoint；应读取PG原检查点。两种真实业务已通过，不将失败计为通过 |
| p4-evidence-reference-recovery-fixed | 13通过22.20秒，包含实际持久恢复/非法引用反馈与内部、外部两业务；PG明确拒绝改写本次已发目录 |
| p4-evidence-reference-raw-final | 最后增原wire正文保存后13通过12.19秒、零跳过；核对raw正文解析等于已保存结构化wire，规范决定继续原核验。两种业务各POST2/GET1/DELETE2，known=covered=2，现场0剩余 |
| p4-evidence-reference-regression-first / regression-fixed | 首命令引用不存在文件，exit4/0测试；没有验证功能。改为读取既有实际命令文件，7模块105通过3旧实时/默认开关跳过134.91秒。切片遗漏首个关联来源模块，另运行该模块如下，不重复计算覆盖 |
| p4-evidence-reference-regression-linked | 20通过23.79秒、零跳过，包含三格式实际原件/独立解析/PG首次计划、旧字节及14真实来源边界。这里仍是原v10流程，用于验证兼容，不称为三格式v11自主执行 |
| p4-evidence-reference-frontend-first / build-first | CasePlanDialog及CaseEffectCoveragePanel两文件66通过4.62秒；vue-tsc及Vite退出0，构建9.19秒。未重跑完整浏览器App |
| p4-evidence-reference-default-first | 严格1失败239.24秒，API/Worker退出0而任务failed；2逻辑/2SDK/2物理POST，原180秒期限。逻辑82.859秒完成prepare+target，证据96.53秒取消，0正文/Run/业务HTTP |

新默认effects系统提示7581字符，实际请求33947字节；原v10样本为单正式项且30705字节，不能据此宣称新输入缩小或总耗时改善。E3个/T原文实际完整提供，最后SDK流未结束、CancelledError，统计推理字符23161、输出用量5084，不保存私有推理正文。HTTP200仅证明响应头。实际工具契约预读仅来自prepare的明确操作选择，正式项没有选择不补造。两个失败任务均保留失败，不重置或同配置重发。

最终raw保存补充发生于默认实测之后；它未改变Prompt、模型请求、绑定算法或模型预算，实际原始正文保存由raw-final另验，不能称默认已返回该正文。当前两次新夹具错误及启动命令错误如实保留；后续工具/测试路径只从实际清单或已保存命令取得，不继续猜文件名。

下一步转向执行期准备输出与依赖传值的真实契约：核对规划字段、准备步骤最终输出、每次原ToolJob对象证据和Agent上下文的消费方式，先复现具体不一致再修改；不是已经证明它导致模型超时。完整自主Agent、共享消费/准备状态、未知只读查证、计划修订/其他模式、P1模板/Excel正式App、DeepSeek人工录制、Docker性能/安全、报告和历史迁移继续未完成。完整审计见[p4-evidence-reference-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-evidence-reference-validation.json)。

### 6.66 运行输入路径丢失的根因修复与真实传值〔源码基线64cc650c〕

先读目标附件、PlanInputBinding、实际JSON路径用例、规划/运行转换、上下文/Prompt和原回执消费链。确定顶层input_key与子路径本来是同一绑定契约；执行入口却只取顶层对象。研究与选型在生产修改之前保存，见[P2_RUNTIME_INPUT_PATH_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_RUNTIME_INPUT_PATH_RESEARCH.md)。这是契约在消费端遗漏，修复原分支一行，复用原读取函数；不增加新的版本化机制或字段。

| 实际批次 | 结果及边界 |
|---|---|
| p2-runtime-input-reproduction | 4失败、4实时组跳过，6.92秒。真实文件/计划/领取已复现错误完整对象注入及必要子字段缺失不报错；不是构造语法失败 |
| p2-runtime-input-fixed | 开启实时存储后8通过、0跳过，11.95秒；False/0、空键、含点号键、数组、空路径、旧点分、可选/必要缺失及null、固定源拷贝和两种SDK实际模型请求 |
| p2-runtime-input-business | 加实际ToolRuntime/API工具/本地HTTP/PG原工具结果/确定性回执/终态不重领后8通过、0跳过，14.33秒；四场景各一条真实GET，绑定ID来自原件数组子字段，4项事实核验通过 |
| p2-runtime-input-regression | 四个既有模块72通过、3跳过，34.35秒；三个外部实际模型验收开关未启用，分别为完整固定用例、字面量路径规划、真实HTTP回执；跳过不计通过 |
| p2-runtime-input-recovery | 旧上下文检查点保护2通过、0跳过，6.61秒；已admitted旧父对象的hash与修正值不同，原适配器拒绝替换，原检查点保持，模型/业务HTTP均0；不称自动恢复通过 |

实时场景使用独占MySQL配置、PG固定计划/运行/工具结果、RustFS原件，临时资源测试后按夹具归属清理。保存8步完整JSON、原件SHA、当时固定计划/运行、真实物理模型消息和实际工具/结果，不把测试身份当仍存活任务。模型/规划受控，业务GET由测试驱动现有ToolRuntime；不称默认Agent自主决策通过。本批未重跑前端/完整App，不重复未变化的默认超时。

此前短引用实现及174原始工件已在64cc650c单独提交，历史失败/unknown保持；本批不把它们累计为新覆盖。下一步仍核验准备输出结构和后续工具传值、多对象消费者/清理、未知只读查证以及其他P0—P5范围；完整升级未完成。三份方案、旧原证据及当前实际原件/物理请求/工具结果核对见[p2-runtime-input-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-runtime-input-validation.json)。

本批兼容性边界：新启动上下文会按原绑定读取实际子路径；已admitted且注入旧父对象的条目，继续受原context_hash保护，不能无声切换。上线前需核对这类原执行证据及历史协调；自动恢复该错误旧上下文仍未实现，不能用两个拒绝替换测试代替恢复验收。

### 6.67 多次真实工具输出组合与固定消费者传值〔源码基线55475b8e〕

生产修改前核对原规划路径、回执、原ToolJob读取、Agent图及本地轨迹课程，研究见[P2_OUTPUT_STRUCTURE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_OUTPUT_STRUCTURE_RESEARCH.md)。原回执无法表达多个调用组成的数组/嵌套对象，这是输出契约缺失；新增明确引用组合，继续原证据核验及工具权限，不按固定业务清单检测或编译测试模式参数。

| 实际批次 | 结果与边界 |
|---|---|
| p2-output-structure-before | 2失败、0跳过，12.19秒。Schema拒绝新结构；真实图已POST两次/GET一次，源回执被拒绝，第二消费者与清理没有执行，现场保留两个对象；失败原证据保存，不重置该运行 |
| p2-output-structure-fixed | 2通过、0跳过，9.89秒。原Agent图、执行API及实际审批，两个POST产生ID数组，两个GET分别读取实际ID，两次DELETE后现场零剩余，终态不重领 |
| p2-output-structure-final | 新模块15通过、0跳过，14.50秒。上述真实存储/业务链路加旧三种回执canonical字节、两种字段冲突、三种组合根、False/0/null/空键/点号键/空容器、伪造简报、错误调用/路径/身份/未结束来源与失败来源不冒充通过 |
| p2-output-structure-regression | 原assessment/execution/context/runtime-input四模块86通过、3跳过，170.49秒；两项外部模型与一项独立Redis Worker验收开关未启用，分别保留跳过节点 |

新节点值均由系统从持久原Job还原，叶子不接收模型实际值；后续正式项使用固定回执中的ids/1，False/0实际注入正确。JSON原件与RustFS读回字节一致，MySQL保存实际策略，PG保存固定计划、范围、原工具/审批及运行终态。实际JSON含完整八步与最后一步，生成模板没有注入。API的actual沿现有通用JSON契约承载；全局搜索未找到独立前端回执DTO，本批未改前端，未重跑完整App/构建。新Prompt版本v3，旧回执省略新字段，原执行context_hash不变；无需数据库迁移或新配置。

模型选择受控，不能称默认外部AI自主执行已通过；实际共享消费者完成状态、完整准备生命周期、多清理通道组合、unknown只读查证和计划修订仍未完成。已有默认失败不是该输出结构导致超时的证据，本批没有重复同配置默认请求。历史已admitted错误输入上下文仍受原hash保护，不宣称自动恢复。P1模板/Excel正式App、DeepSeek人工录制、Docker真实性能/安全、报告、生产历史迁移及其余P0—P5保持原目标。

本次同时清理方案开头重复进度陈述，保留所有历史章节及原失败；更新2.3.9—2.3.10、当前状态表，并同步用户附件与计划副本。三份字节一致、原件/模型消息/业务HTTP/PG回执、上批35原始工件HEAD字节与无关当前字节检查见[p2-output-structure-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-output-structure-validation.json)。

本批临时数据库/对象和本地业务服务器按夹具归属清理；保存记录表示测试当时的事实，不代表任务或对象仍在线。审计首次误取策略DTO的value字段而退出1，读取实际policy字段后修正，最终核对通过；该审计错误及日志保留，不计为功能测试。

### 6.68 原审批期间逐项核验与完整路径兼容〔源码基线a4d6c7e4〕

生产修改前核对原范围、清理归属、Agent图/原Job/完整上下文、核验器及本地Agent轨迹课程，保存研究P2_CONSUMER_RECEIPT_RESEARCH.md。不能把完整RunItem终态当内部清理前置；先实现复用原内核的只读逐项核验，不虚构消费结束状态。生产仅改agent_case_assessment.py，新增精确项集合入口；全P0—P5目标保持。

| 实际批次 | 结果与边界 |
|---|---|
| p2-consumer-receipt-before / reproduction | 各8失败，19.58/19.53秒。新夹具先误用只接受running的_load，再误判原快照只有两条工具记录；实际上有第三条待审批清理。另四个边界方法未实现。两批不计作完整功能复现 |
| p2-consumer-receipt-pending-reproduction | 8失败21.43秒。正确保留原context_hash/三条记录，四个真实场景在请求构造阶段被“工具证据尚未结束”阻断，0模型HTTP；其余四项缺assess_steps。原失败及原件保存后才改生产 |
| p2-consumer-receipt-fixed | 8通过22.67秒、0跳过。实际MySQL/PG/RustFS与原图/API/审批；两种SDK分别核验正确/错误期望；待审批清理身份保留，逐项核验不改变现场与运行，批准原清理后终态不重领 |
| p2-consumer-receipt-final | 4失败11通过24.72秒。新增断言按原JSON键顺序比较字符串，PG固定JSON重排导致失败；before.json证明内容完整。改为解析实际送达内容后结构比较并核对物理messages，没有因此修改生产代码 |
| p2-consumer-receipt-verified | 最终15通过26.90秒、0跳过。四个真实场景各一物理模型HTTP、完整八步/末步及False/0/null；实际POST/GET/审批期间只读核验五检查，随后DELETE/现场零剩余。其余11项覆盖精确集合、未结束/无此/跨身份来源、伪造摘要、完整原上下文及与a4d旧实现的请求字节/结果配对 |
| p2-consumer-receipt-regression | assessment/execution/context/runtime-input/output-structure五模块及正式混合效果internal-all/external-all两场景，103通过3跳过182.74秒。跳过为两外部模型和一独立Redis Worker开关未启用，不计通过 |

旧完整assess仍拒绝缺项及未结束原Job，完整请求字节与提交基线一致；结果配对只归一核验随机ID及时间。逐项结果没有completion；所有观察和输出均读取持久原Job，紧凑记录自报completed无效，裁剪上下文违反原绑定。当前控制场景的语义判定来自受控模型，不能称默认自主AI已通过。

新增核心路径走原logger，记录请求项/来源数/pending数及run_item_completed=False，不打印实际凭据。没有新依赖、MySQL配置或数据库迁移；现有前端没有改，本批未跑完整App/构建或重复未改变的默认超时。实际消费结束检查点、清理领取同事务、unknown只读查证、正式混合效果/关闭后动作、多Worker、计划修订及其余P0—P5仍未完成。原早删失败继续保留；旧两份Worker汇总原字节未恢复限制保持。

三个方案原文同步，命令、合并stdout/stderr的日志/XML、原件与RustFS元数据、实际SDK物理消息、原PG检查点/hash及终态核对见[p2-consumer-receipt-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumer-receipt-validation.json)。上批46原始HEAD工件及无关当前字节另核对；测试资源已按归属清理，保存的是测试当时快照。此次仅提交本批相关文件，不推送或建PR。

本批审计首次误要求待审批清理已有case_action_admission而KeyError退出1；实际原Job只有绑定、没有执行准入，已完成seed/target才有准入。核对保存metadata后修正审计分支，生产代码未变；audit-first.log保留，不计功能失败或通过。

### 6.69 消费阶段关闭与原清理领取事务〔研究基线e356905d〕

生产修改前研究并再次运行真实提前删除反例，1通过8.05秒表示反例采集测试成功，业务第二目标failed/404；不改写原失败。新可选策略保存MySQL，完整计划/上下文及消费记录在PG，实际原件RustFS。记录与原逻辑登记/清理领取同事务，没有新执行器、依赖或全局模型/预算改动。

最终新模块 **14通过、0跳过（41.02秒）**，实际MySQL/PG/RustFS、完整八步JSON/False/0/null与原工具业务HTTP。十一个直接工具场景及两个原Agent/API/审批场景，另一个旧策略/前置约束测试。原图两场景各七次受控模型决定、六次审批、五次实际业务HTTP；提前清理已拒、两个对象均清理，正确/错误预期分别passed/failed。当前配置关闭后固定策略不变，终态不可重领。受控Agent不等于默认外部模型验收。

并发四领取只实际DELETE一次；关闭INSERT后人为抛错使原领取/登记/关闭一起回滚，重试原未发送任务可继续。实际PG已提交但领取响应丢失时，原running Job无可靠返回且没有DELETE；别名返回case_operation_outcome_unknown及原引用，同原Job恢复抛明确未知，现场两对象保持。不能把关闭存在当作清理成功，也没有实现该unknown的自动只读查证。

空检查、未保存调用、错误目标、queued/running无返回普通动作均拒绝关闭，现场对象保持；历史关闭依据改变被拒，后续逐对象清理仍可继续。已知查询别名只复用原结果；关闭后新普通动作拒绝。原件/hash、固定配置/上下文、原Job、消费记录、HTTP及历史保护核对见[p2-consumption-phase-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-phase-validation.json)。

早期失败完整保留：首6失败1通过10.96秒为旧fixture未支持新分阶段策略；次6失败1通过13.05秒发现新DDL误用TEXT外键，实际原RunItem为UUID；定向1失败5.72秒为夹具遗漏候选和四阶段模型返回；再1失败6.17秒发现逻辑操作消费者未识别新回执字段。对应生产根因已按真实SQL/原调用链修正。随后1失败6通过18.86秒是测试误期待别名也抛异常；按实际unknown别名与原Job分别验证后7通过17.79秒，再边界11通过26.27秒。原图首2失败10.73秒、次2失败15.64秒分别是受控夹具读取错误消息字段和漏mode=ok；核对原tool_message/返回契约后2通过17.08秒，最终14项包含全部当前场景。

前端最终项目配置组件 **65通过（2.42秒）**；当前build含vue-tsc退出0/Vite6.90秒。受影响11模块回归 **137通过、8跳过（234.38秒）**；两个外部模型、一个独立Redis Worker未启用，另五个原动作关联live节点缺开关。随后启用原开关补验该模块 **7通过、0跳过（42.18秒）**，其中两个节点与前一回归重叠，不累计为144项覆盖；含三格式原关联及原图/DeepAgent审批。命令及精确跳过节点见IMPLEMENTATION.md与本批核对记录。源码编译只作附加检查，不作为实际功能通过依据。三份方案全文同步；既有117份原证据、用户.gitignore和无关当前文件分别校验保留。两份旧Worker汇总原字节未恢复限制保持。

6.69当时仅验证内部新固定策略，未验外部多个消费者；本次两消费者共享来源见6.70。租约恢复/多Worker、独立消费报告展示、完整生命周期、未知只读查证、计划修订及其他模式/完整App尚未验收。生产表初始化沿原动作服务，但未进行生产历史迁移。默认自主Agent原超时未原样重跑，P1正式模板/Excel导入产品、DeepSeek人工录制、Docker真实性能/安全、报告及其余P0—P5继续未完成。

### 6.70 可靠返回、外部共享消费者与自动清理等待〔研究基线67d134ae〕

实现前查原取消/可靠返回、逻辑原声明和依赖映射，并保留实际失败输入。内部原GET真实返回后在持久观察前异常，再用原cancel_job取消；旧代码仍写关闭记录并DELETE。新断言首次**1失败/14未选中（6.57秒）**，不是“已验证可用”。修复后定向**1通过（5.90秒）**；最终当前模块**15通过、0跳过（44.15秒）**，包含前批十四项和这个新增真实反例，原件/Job/HTTP及失败现场均保留。

外部首次**4通过、2失败（34.03秒）**。取消无返回已经阻止DELETE，但受控Agent错误引用被拒清理不存在的response_status，最终error；按原返回构造blocked回执修正测试。正式项内共享清理则确认生产缺陷：左消费者实际200与固定201不符，原failed结果保留，seed已成功；自动加入的left等待被映射为passed，right无法领取。新版本只将可重建的自动等待映射terminal，明确业务前置不变。修复六场景**6通过（34.47秒）**。

增加明确业务依赖/必要数据绑定失败、旧映射真实PG重放及原67d文件的旧快照/创建SHA、篡改原声明/哈希/固定依赖边界。首次**12通过、1失败（48.76秒）**为新旧运行测试遗漏原create_planned_run必需关键字；按原方法签名修正夹具，没有修改生产契约。最终外部模块**13通过、0跳过（48.14秒）**：九个实际MySQL/PG/RustFS、原Agent图/API/审批/业务HTTP场景，另四项原输入历史和篡改边界。

六个实际执行到最后消费者的场景均保留完整八步/False/0/null原件和送达上下文：正常及failed消费者分别清理成功；可靠返回后cancelled及交付partial也允许结束；取消无返回场景最终blocked、零DELETE、共享对象保留。提前清理拒绝、不发送。正常场景4业务HTTP，可靠返回边界5HTTP；取消未知场景4HTTP且没有DELETE。明确业务前置失败、必要输出失败与旧映射三场景均不领取右项，2HTTP、零清理、保留对象。原创建请求在当前配置关闭及左项终结后仍重放原运行；已结束运行不重领。

前端仅增加可选快照DTO，实际`npm.cmd run typecheck`退出0。受影响十五模块/节点回归**197通过、4跳过（286.59秒）**；两个外部模型、独立Redis Worker及真实路径绑定模型开关未启用，精确节点保留在XML。最终命令与结果在IMPLEMENTATION.md及[p2-consumption-return-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-return-validation.json)记录；源码编译是附加检查，不能替代上述功能测试。三个方案全文同步，上一批227份原证据/HEAD及用户.gitignore/无关当前文件按SHA检查，历史两份Worker汇总原字节未恢复限制保留。

这批验证的是工具可靠返回与原清理准入，不证明所有远端副作用已结束或全部业务通过。更多消费者/来源组合、跨租约/多Worker、独立消费报告、完整准备生命周期、unknown只读查证、计划修订/其他模式、完整App和默认自主Agent继续待验。未重复同配置默认超时、未进行生产历史迁移；P1模板/Excel正式产品、DeepSeek人工录制、Docker真实性能/安全、P0产品链路及其余P0—P5保持未完成。

### 6.71 原Electron录制实测、入口修复及停止漏录〔基线05f7403d〕

本批先核对原JS、三驱动、bridge/API、客户端、PG事件、清洗投影与规划知识读取，再启动隔离正式项目、实际MySQL/PG/RustFS/Memgraph、原Electron录制模块和真实HTTP。受控HTML原件实际保存，原Playwright CDP点击产生真实浏览器事件；不手造RecorderEvent，不替换原传输/固化模块。

实际原指令使用action，而Electron只读取kind/command：HTTP返回active、页面enabled=false，原日志记录unknown command。已在原消费处对齐action并兼容旧别名。修正后实际两次点击、原binding两click及page_scan，六秒仍未入库，证明原定时flush也要求满二十条；改为二秒提交部分批次。没有新增配置、表结构、依赖、测试模式或执行器。

独立正常场景**1通过11.08秒**；按原API开始、暂停、继续、停止，四次有效操作保存，暂停两次操作不录入，同seq重复投递去重，重复真实操作保留；原关闭命令生效。实际PG/图投影和项目只读知识来源读取、跨项目拒绝、RustFS原截图读回与SHA已核对。控制条采用静态夹具，不能当作完整Vue App或人工录制验收；没有模型调用。

新增立即停止场景：页面实际输入“最后一步实际输入”，停止前enabled=true/seq=6；PG仅5条、对应fill为零，返回completed且reconciled=true/degraded=false。**本批最终两场景为1通过、1失败18.65秒**，见[原XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-embedded-scenarios.xml)、[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-embedded-scenarios.log)和[停止结果](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-embedded-scenarios/tail-input/tail-input-result.json)。失败没有标为通过、xfail或重置运行；测试自有数据库对象按明确归属清理，原状态/输入/HTTP/binding/事件字节保留为工件。

受影响十三模块回归**143通过、0跳过13.32秒**，包括真实Chromium CDP、PG和Memgraph开关；既有Starlette弃用警告1条。原脚本**23通过1.74秒**。探针自身的启动时序、argv、未生效输入、隐藏view和清理glob失败均保存并逐一纠正，详见[P0_RECORDING_EMBEDDED_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_EMBEDDED_RESEARCH.md)。这些失败不能当作更多生产缺陷或虚增通过数。

下一步先实现有实际确认的跨端停止/排空契约，再补动作响应信息、清洗/遗漏与完整知识原文、人工DeepSeek及AI实际使用。单纯sleep、统计已有行数或把指令入队当执行完成都不满足本次反例。旧completed/failed/unknown历史保留，不原地重写。场景管理/编辑/别名/查询/失败定位、Docker性能安全及报告、P1正式模板/Excel、P2完整自主/生命周期和其他模式/App、需求业务评审/环境适用、P0完整发送与产品及生产历史迁移继续，完整P0—P5目标保持未完成。方案与用户附件/选中PLAN同步和原字节审计见[p0-recording-embedded-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-embedded-validation.json)。

### 6.72 停止回执、真实尾部输入与PG水位〔基线33f15e99〕

针对6.71实际失败，先研究原JS输入定时器/binding、三驱动、Electron部分批次和原消费/PG路径，再接入recording-finish-v1。JS枚举未结束输入并保存已接受的脱敏最终值，等待异步binding并保留拒绝缓冲；Electron确认accepted+duplicates等于本批数量，冲刷全部部分批次后才提交带请求身份的capture-finished回执。原后端在finalizing接受已采尾部，FIFO结束标记后消费循环自然退出并提交buffer，核对实际PG发布seq以及浏览器本页生成范围。缺口/解析失败/丢弃/DB失败/超时不能固化成功，不靠sleep或取消消费伪造排空。

结束超时配置使用既有MySQL key `recording.finish_settings`，项目优先/全局回退；生产lifespan初始化及注入。创建时冻结scope/revision/参数到原PG metadata，用户metadata不能覆盖。真实测试在创建后修改MySQL配置，原会话保持原策略；配置编辑页面/运维产品入口仍待接。没有新增表、依赖、测试模式或业务执行器。取消HTTP等待保留原后台stop；终态控制拒绝复活。原旧历史与事件不改写。

首轮两个原Electron场景**2失败22.87秒**，最后fill和PG水位已正确，但原窗口捕获额外真实点击，固定四次断言失败。工件不改写、不丢额外操作；改为至少四次自动操作且全部binding点击及type/page/target/pixel/value/page_effect与PG一致，并分别记录额外数量。来源未证实，不称作人工DeepSeek。首轮原后端**142通过、1失败17.88秒**：旧测试主动发布暂停期间seq=2后丢弃，新结束核验明确缺口，更新测试验证failed/missing=[2]且不固化图，保留原失败。

两个原Electron场景修正后**2通过21.30秒**；新增协议/真实CDP与原十三模块**152通过14.27秒**。最终再加入真实HTTP503结束回执场景，统一合跑**155通过、0跳过31.59秒**，见[最终XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-drain-final.xml)及[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-drain-final.log)。真实Electron/API/MySQL/PG/RustFS/Memgraph三场景：正常停止、立即输入后停止成功；结束回执实际503时failed/finish_timeout，没有成功图固化。真实CDP实际HTML立即输入后结束，原fill及流结束已核验。协议专项另覆盖中间/尾部水位缺口、DB失败、未确认frame、错/改写/重复回执、取消等待和终态。原JS及新增多个输入/敏感值/异步binding/iframe边界**26通过1.50秒**，见[JS结果](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-drain-web-fixed.xml)。一条既有Starlette弃用警告保留；编译只是附加检查。

追加独立三个原Electron场景**3通过、0跳过26.39秒**，保存创建后MySQL实际读取的当前一秒策略、原冻结三十秒/一秒策略及最终PG快照，确认修改配置不会影响已有录制。追加原ASGI路由的错请求拒绝/正确回执及原路由回归**27通过、0跳过1.06秒**，记录拒绝日志，见[路由XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-drain-routes-final.xml)。这些是额外核验，不能与此前合跑重复累计成新功能数。

本批不证明完整录制：iframe/多窗口缺分范围身份/结束确认，当前明确失败；暂停已接受输入与迟到批次仍需完整修复/验收。完整Vue App、真实managed结束、人工DeepSeek、响应关联、清洗/完整原文与AI实际使用均未验收。另需核验bridge丢失后的迟到写入和历史保护，不把活动bridge的终态拒绝当生产恢复已验。其余P0—P5、默认自主Agent、准备生命周期、其他模式/App、正式模板/Excel、Docker真实结果/报告、业务评审/环境适用及历史迁移保持未完成。研究与下一步见[P0_RECORDING_DRAIN_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_DRAIN_DESIGN.md)，三份方案和原字节核对见[p0-recording-drain-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-drain-validation.json)。

### 6.73 暂停检查点与终态迟到写入〔基线bd972911〕

本批先研究原control、输入冲刷、批次交付、消费循环及PG事务，见[P0_RECORDING_BOUNDARY_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_BOUNDARY_DESIGN.md)。两个真实原Electron反例 **2失败、51.12秒**：立即pause时原binding已有seq=3/fill，六秒PG无fill；正常completed后移除bridge，原批次重投HTTP200/duplicates=6且updated_at改变。失败原件与清理时finish_timeout均保留，不计为其他功能通过，见[复现XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-boundary-reproduction.xml)。

根因分别为paused占位先于已采输入入库，以及未知bridge回落的追加事务没有终态条件。pause与stop共用浏览器冲刷/传输回执/PG水位核验和失败处理；pause用不关闭队列的FIFO检查点，原消费循环冲刷后才保存paused。回执增加operation，错操作拒绝；并发控制和取消HTTP等待已有专项。暂停证据追加到原finalize_metrics，stop保留。沿用创建时冻结的MySQL recording.finish_settings，业务记录在PG；没有新配置、表、依赖、模式或执行器。

PostgresRecordingStore.append_events在同一事务FOR UPDATE锁定会话行，随后才计数、插入及更新。终态转换UPDATE与追加串行；不存在/终态有结构化拒绝日志，原未知bridge路由映射404/409。重复批次和新seq在completed/failed/discarded均拒绝，原会话全部字段及原事件保持；不只在路由GET一次状态。八路真实并发追加step_count=8，实际pg_stat_activity观察Lock后提交终态，已阻塞追加醒来仍拒绝。

两个真实反例修复后 **2通过、19.53秒**；原十四模块回归 **153通过、0跳过、13.62秒**；新增取消等待/并发控制/DB失败、真实PG三终态锁竞争和CDP暂停继续 **6通过、0跳过、6.73秒**。补错operation与暂停一秒超时后最终合跑 **161通过、0跳过、18.90秒**，有一条既有Starlette弃用警告，见[最终XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-boundary-final.xml)和[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-boundary-final.log)。各轮重叠节点不累计成功能数。

独立原Electron/API/实际MySQL、PG、RustFS、Memgraph五场景 **5通过、0跳过、38.36秒**：正常、立即stop、实际HTTP503结束超时、立即pause最后输入、移除bridge终态重投。四个成功场景保留全部binding字段/PG与RustFS截图核对；HTTP503为预期failed/finish_timeout，不固化成功图。移除bridge后的实际HTTP409及会话/事件完整前后相等已验，见[产品XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-boundary-product-final.xml)。CDP保存暂停前与继续后两次真实fill及两个operation回执；不是人工录制或完整Vue App。

本批未改recorder.js，26项JS是6.72历史结果；当前Electron实际载入修改后的operation协议，node --check附加验证。没有Vue契约变更，未重跑Vue完整构建。三份方案全文同步和本批/此前原件SHA核对见[p0-recording-boundary-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-boundary-validation.json)，命令见IMPLEMENTATION.md。用户.gitignore及无关Worker/环境工件保持；历史两份Worker汇总原字节未恢复限制不变。

暂停尾输入和终态写入专项通过，不代表断网/更多恢复、iframe/多窗口、真实managed结束、完整App/人工DeepSeek、响应关联、清洗/完整知识/AI已经验收。下一步继续分范围身份和响应/知识链路；P0全阶段HTTP、P1正式模板/Excel产品、P2默认自主Agent/生命周期/其他模式、需求业务评审/环境适用、Docker真实结果及报告、生产迁移与其余P0—P5仍未完成。没有重复未改变的默认自主Agent超时。

### 6.74 Python两驱动的文档/frame/弹窗范围〔基线d03ab1e5〕

先查原JS、注入/控制、官方及本机binding/Frame接口与本地Agent协议课程，见[P0_RECORDING_SCOPES_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_SCOPES_DESIGN.md)。原真实Chromium及保存HTML四场景 **4失败、6.88秒**：导航后新doc关闭、既有子frame未注入、弹窗关闭；不是JS realm模拟，见[原XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-scopes-reproduction.xml)。原生产跨范围采集缺身份/水位，不能以逐页sleep或单独开启子frame作为修复。

采用recording-scopes-v2，Python宿主通过原生page/frame登记每次文档，局部seq映射为现有PG会话seq；保存原生frame_path及local_seq到原page JSON。原binding处理重投/内容变更/来源身份，当前frame逐一注入，init_script中新doc登记继承采集状态；原生opener链只纳入root弹窗。暂停/结束逐frame确认，保留已离开doc水位，PG验证全局范围、局部范围和原生身份；遗漏/重复doc及来源错配拒绝，不改旧PG事件/唯一键或原v1历史。没有新表、依赖或可编辑参数，超时沿用MySQL冻结策略。内部版本化回执在原泛型finalize_metrics中保存；Electron公共回执协议未改。

首次修复 **2通过、2失败、5.21秒**：frame/popup采齐，旧doc pagehide水位未送达，导航明确未确认；改为更早beforeunload边界并继续要求全范围收齐。第二轮四真实场景及驱动合同 **32通过、2失败、5.61秒**：open后/inject前toggle遗漏root owned登记，保留原断言并补正归属。旧十五模块 **161通过、0跳过、21.05秒**。API/实际存储初轮 **12通过、27.39秒**；补RustFS截图实际上传/读回后 **12通过、31.45秒**。身份/PG错配六项用真实保存事件派生，**6通过、0.50秒**；暂停后新frame的CDP/managed两项 **2通过、10.65秒**。以上原件及失败保留，不重复累计节点。

第一次十八模块合跑XML **186通过、0跳过、78.66秒**，但原件审计发现十个存储场景记录了TypeError：知识查询漏传limit且使用复数目录kind，测试在赋值receipt后保存异常而未抛出。因此这一绿色XML不能证明知识读取或异常后未执行的完整字段断言成功；此前12项/暂停两项轮次也受此限制。原finish-error.json和所有XML/log保留，不改为新的成功证据。

已按现有query签名改为明确kind=recording/source_id/limit=1，并让所有结束验证异常保存后重新抛出。完整十八模块重跑 **186通过、0跳过、86.92秒**，含两驱动各五个API/实际MySQL、PG、RustFS、Memgraph/项目知识场景、四个原driver反例、身份专项及原Electron五场景，见[核验后XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-scopes-verified.xml)及[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-scopes-verified.log)。十个新知识工件存在且没有finish-error。五场景为同源导航、跨来源导航、跨来源嵌套frame最后输入、root弹窗、暂停新frame后继续；十场景都另开同context无关标签并实际点击，确认关闭且未纳入。managed实际使用原运行时headed窗口；原API停止completed及逐doc PG确认、完整通道字段/入库顺序和十份RustFS截图读回一致，模型调用0。

JS旧26项先 **26通过、3.63秒**，新增v2来源seq隔离及无归属页面后 **28通过、1.60秒**，见[JS XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-scopes-web-final.xml)。源码编译/node检查仅附加；未改Vue DTO，未重跑Vue完整构建。三份方案和本批/此前原件SHA核对见[p0-recording-scopes-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-scopes-validation.json)，实际命令见IMPLEMENTATION.md；用户.gitignore/无关Worker工件及历史失败保持，旧两份Worker汇总原字节未恢复限制保留。

Electron仍v1，五场景回归不等于它已支持frame/多窗口；下一步接native execution context/popup。关闭/移除frame、回返/BFCache、同context重复/并行attach、更多断网/进程恢复还需实测及必要修复。知识读取返回的原图摘要/回执不是完整步骤原文已供AI执行。完整Vue App、人工DeepSeek、响应关联、清洗/完整知识/AI、场景/报告、Docker、P0全阶段HTTP、P1正式模板/Excel产品、P2默认自主/生命周期/其他模式、需求业务评审/环境適用与生产迁移及其余P0—P5仍未完成；没有重跑同配置默认自主Agent超时。

### 6.75 Electron原生范围与真实重投〔基线a6f43697〕

研究原窗口/bridge/共享scope代码、本机Electron类型、官方Debugger/Runtime/webContents及本地Agent轨迹课程，见[P0_ELECTRON_SCOPES_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_ELECTRON_SCOPES_DESIGN.md)。原四真实Electron/API/MySQL/PG/RustFS/Memgraph反例4失败31.12秒；第一次测试收集因表达式语法错误1error，未运行功能；修正后的首轮4失败54.21秒，暴露初始登记未等到回执、OOPIF树遗漏及重复入口导航。全部原件保留。

协调模块接原CDP context/session及回复桥，完整事件映射原PG键；旧窗口删去仅默认context的控制/结束代码，不并存第二套路径。初始登记等待及跨target树修复后3通过/1失败63.92秒，嵌套仍受重复初始化导航导致会话失效；明确初始已加载导航后四反例4通过35.22秒。扩大十六模块168通过/1失败/8跳过95.07秒，动态frame虽采齐却缺父路径；用原生frameAttached/frameNavigated补真实父链后嵌套/暂停两项2通过20.41秒。实际绑定重投/改写身份两项2通过19.87秒；改写被原context拒绝，最终failed且transport_dropped=1。

首个十八模块合跑192通过/1失败/0跳过173.60秒，失败为Windows测试控制文件被读取时os.replace产生WinError5，测试中断又触发清理结束超时。改测试控制器为按编号追加的不可变命令文件，不重试覆盖读中的文件；生产源码不因这项测试错误修改。完整十八模块重新实际运行 **193通过、0跳过、149.32秒**，一条既有Starlette弃用警告，见[核验XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-electron-scopes-verified.xml)与[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-electron-scopes-verified.log)。源文件hash固定并核对，失败/跳过不计新通过。

十二Electron场景（十completed、两个预期failed）全部使用原HTTP及真实MySQL、PG、RustFS、Memgraph；包含跨站OOPIF最后输入、弹窗最后输入、暂停新frame、同URL再次导航、无关窗口隔离、实际绑定重投及内容改写拒绝。完成项核对原绑定全部业务字段、PG全局/局部seq和来源、十份原项目知识，十二份原RustFS截图下载SHA/大小；另复验两Python驱动各五实际场景和四driver反例，原v1/终态/恢复合同保留。没有模型调用、人工DeepSeek或完整Vue App验收。

JS第一次误用node --test运行Vitest文件，runner错误且没有执行实际用例；原失败日志保留。按项目既有npm test运行 **28通过、0跳过、1.30秒**，见[JS XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-electron-scopes-web-verified.xml)。node --check/py_compile仅附加。公开结束DTO允许v2/scopes且旧v1兼容，无新表、依赖或配置；前端没有这个HTTP回执发送方，Electron发送/后端model/bridge/PG核验及测试已同步。未重跑Vue完整构建、未进行生产历史迁移。

三份完整方案及本批原件、旧原件HEAD/当前字节、来源hash、实际场景核对见[p0-electron-scopes-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-electron-scopes-validation.json)，实际命令见IMPLEMENTATION.md。用户.gitignore/无关Worker及环境工件保持。旧两份Worker汇总原字节未恢复限制保留。

完整目标仍进行中。该批结束时关闭/移除/BFCache等未验，后续变化见6.76；并行attach及更多断网/进程恢复、响应关联、清洗完整知识、AI实际引用、人工DeepSeek/完整App/场景编辑/报告；P0全阶段HTTP与Docker真实结果、P1正式模板/Excel产品、P2默认自主Agent/准备生命周期/未知副作用/其他模式、需求业务评审/环境适用与生产迁移等其余P0—P5继续未完成。没有重复原样默认Agent超时任务。

### 6.76 真实文档离开、缓存恢复与就绪竞态〔基线77ab0e0〕

研究原JS/native context/原两Python驱动/会话服务、官方pageshow/pagehide及Playwright启动机制、本机Electron类型与本地Agent轨迹课程，见[P0_RECORDING_LIFECYCLE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_LIFECYCLE_DESIGN.md)。原两Python驱动关闭弹窗、移除嵌套iframe、普通回返6通过27.25秒。Electron首轮2通过1失败63.04秒；等待实际frame移除后仍失败43.23秒，后续原生轨迹/根页面状态诊断分别失败43.16/43.01秒。三个已接受回复命令直到退出才返回，根页面pending/in_flight=0；不是末尾输入丢失。

修正原context命令生命周期后2通过1失败29.24秒：弹窗事件与close完整、PG verified，但接受后的回复失败误计transport_dropped。将接受与回复职责分开后，三种生命周期加身份冲突场景4通过37.08秒；没有伪造缺失水位或延长超时。

真实缓存测试早期两次失败6.73/5.06秒，headless shell的原生原因包含BackForwardCacheDisabledForDelegate，不能算缓存覆盖。采用完整Chromium后pageshow.persisted=true；首次等待load失败37.58秒，修正夹具等待commit/实际pageshow后失败13.24秒，复现enabled=false、旧in_flight=1及回返点击漏录。activate/原消息恢复后独立真实缓存1通过7.51秒；原API/PG活动与暂停回返2通过13.19秒。重投原消息不新增重复事件，原document_id/局部seq保留。Electron回返仍persisted=false，原生uniqueId约束只作协议单测，不宣称Electron实际缓存恢复通过。

JS首轮32通过2.07秒，补原消息晚到/暂停及native来源约束后最终**36通过、0跳过、2.21秒**，见[JS XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-lifecycle-web-verified.xml)。`npm.cmd run build`实际vue-tsc与Vite通过、12.71秒；语法检查仅附加。

首个18模块合跑208通过1失败203.08秒，实际PG显示ready但control仍读取launching返回409。固定实际PG提交后暂缓返回复现1失败8.45秒；会话迁移锁修复后两驱动2通过13.16秒，HTTP等待原提交回填后200，无请求重试。最终同18模块**211通过、0失败、0跳过、207.58秒**，一条既有Starlette弃用警告，见[最终XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-lifecycle-final.xml)和[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-lifecycle-final.log)。

本次15个实际Electron场景（13completed、结束超时/身份冲突2项预期failed）、20个Python原API/存储场景（两驱动各9项加CDP真实缓存2项），实际MySQL冻结策略、PG、RustFS、Memgraph沿用。核对原binding业务字段、原生frame来源、全局/局部seq、末尾输入与35份RustFS截图读回；实际缓存开启/暂停及ready窗口均保存原件。模型发送0。失败输入、HTML、生命周期beacon、状态、原HTTP/context、XML/log不覆盖。

源文件hash固定，三份方案逐字节同步，原2751个历史工件HEAD/当前字节核对及本批审计见[p0-recording-lifecycle-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-lifecycle-validation.json)，命令见IMPLEMENTATION.md。无新配置键、表、依赖或迁移；用户.gitignore及无关Worker/环境工件保持，旧两份Worker汇总原字节未恢复限制保留。

完整P0—P5目标未完成：Electron真实缓存恢复、并行attach/更多网络与进程故障；完整响应关联、清洗/知识原文供AI、人工DeepSeek、完整App、场景/报告；Docker真实性能/安全、P0全阶段HTTP、P1正式生成/导入产品、P2默认自主执行/准备生命周期/未知副作用/其他模式、需求业务评审/环境证据与生产历史迁移仍需实施和验收。未重复原样默认Agent超时。

### 6.77 完整录制知识出口与历史证据〔基线5711b37e〕

先读取原采集/固化/查询/规划/公开API及实际存储夹具，查本地Agent轨迹与数据治理课程。原Action已保存脱敏全载荷，但知识出口只返摘要；真实CDP与Electron跨来源嵌套录制两项**2失败18.38秒**，原事件均入PG且知识action.event缺失。最初命令节点名称错误没有运行测试；后续单测又从错误工作目录运行产生收集错误，两者原日志保留，均不算功能覆盖，见[P0_RECORDING_KNOWLEDGE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_KNOWLEDGE_DESIGN.md)。

原查询返回每步脱敏完整event，添加明确完整/部分与数量/身份诊断，沿既有PG事件/Action投影和规划证据hash。长输入不使用200字符摘要；False/0/null及未知嵌套保留。缺失/坏JSON/字段、非有限值、seq/type不符和范围缺口不输出假事件；旧记录仍能看摘要/警告。密码读取沿原清洗函数兜底，不修改原投影。前端规划证据为Record<string,unknown>、公开图谱原为dict返回，旧nodes/edges契约保持，无新DTO消费方需要转换；实际公开HTTP返回逐字段检查。

离线原件/规划/图/API回归67通过、4项未开live跳过，6.24秒。新增受控模型测试首次1失败11通过4.50秒，原因是测试录制store替身缺目录list_sessions，未进入知识查询；补完整夹具后**12通过3.44秒**。它证明完整事件进入第二轮规划请求并固定，后来投影缺失也不改原计划GET；受控模型不冒充默认AI，默认Prompt新版本为case-planning-v5，已有任务Prompt保持原件。

最终20模块实际运行**244通过、0失败、1跳过、219.94秒**，一条既有Starlette弃用警告，见[最终XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-knowledge-final.xml)与[原日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-knowledge-final.log)。跳过为`RUN_LIVE_CASE_PLANNING_MODEL_TESTS`未开启的默认模型专项，没有算通过。真实20个Python原API/存储场景、15个Electron场景（13完成、2预期failed）；33个完成场景的公开HTTP/规划知识完整event与PG全字段一致，原项目隔离、末尾输入、逐文档回执及35份RustFS截图读回继续通过。原MySQL/PG/RustFS/Memgraph实际使用，未调用默认模型。

本批仅Python读取/默认Prompt/测试变化，未重跑前端build或JS；6.76的36项JS/build仍为历史结果。py_compile附加通过。三份方案同步，5个源文件SHA、5893个既有原件当前/HEAD字节、所有新输入/错误/XML/HTTP/查询/截图核对见[p0-recording-knowledge-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-knowledge-validation.json)，实际命令见IMPLEMENTATION.md。用户.gitignore/无关Worker与环境工件保持；两份旧Worker汇总原字节未恢复限制保留。

完整升级继续进行：默认AI实际引用知识与页面操作/断言；动作后响应关联、清洗/完整性和人工DeepSeek/完整App/场景报告；Electron实际缓存恢复、并行attach/更多网络进程故障；Docker真实性能/安全、P0全阶段HTTP、P1正式生成/导入产品、P2默认自主/准备生命周期/未知副作用/其他模式、需求业务评审/环境适用及生产历史迁移尚未完成。本批不把完整读取等同整个人工录制闭环。


### 6.78 有限DOM响应、输入及固定策略〔基线091e4200〕

完整P0—P5目标保持。研究P0_RECORDING_RESPONSE_DESIGN.md，原CDP/管理浏览器实际反例2失败15.67秒：页面已新增按钮/文字，原知识无响应。初次命令误选节点，31项全排除、退出码5，不算功能验证；正确选择后2通过17.32秒。输入防抖前响应另有JS实际失败1项，敏感父容器的新增/删除子节点另失败1项；均保留原输出后修复。

采集复用原事件链；MySQL recording.capture_settings在PG会话冻结，Python注入和Electron按recording_id下发使用原快照。page_response记录同文档局部候选操作、截止状态和有限增删/文本元素；敏感上下文继承脱敏，截断明确。旧dom_mutation_count不再证明当前操作响应；新低置信度建议需要真实响应及同文档操作匹配。辅助事件保持知识用途、不回放。输入接收即观察，最终fill才绑定身份，change结算不重复；停止前读取待回调变化。无新表/依赖/迁移，旧记录与策略保持。

真实输入/无变化/停止/截断8通过39.73秒；配置冻结/MySQL/PG/脚本隔离及原API/Schema/断言56通过5.52秒。首次Electron在pause请求发生ReadError，之后收尾超时，1失败44.89秒；追加服务端HTTP轨迹重测1通过17.74秒，根因未建立，不能声称连接错误已经修复。

扩大回归首轮285通过、1默认模型专项跳过、274.89秒。审计发现嵌套响应target.in_iframe默认值不准确，保留当时recorder.js原字节和初始源码hash后修正；最终按新冻结源码重跑23模块，285通过、0失败、1跳过、262.22秒，既有Starlette弃用警告一条。最终JS46通过，typecheck/build通过。46场景含16原Electron和30Python API/存储场景；44完成项公开API/规划知识逐字段等于PG流水，2预期失败保持；跨来源嵌套响应按原文档/局部seq关联，frame定位标记已核对。44份截图RustFS之外，失败场景的截图也保存并核对。

本次生产API/独立解析Worker专项未通过：冷启动在录制初始化之前退出，消费表FK先引用尚未创建的agent_test_run_items，原stderr和1失败22.79秒保存p0-recording-response-production。需要沿真实依赖修复初始化顺序，不能以受控录制服务通过代替生产启动通过。

实际命令见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)；[最终XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-response-verified.xml)、[最终日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-response-verified.log)、[审计记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-response-validation.json)包含实际项目、PG原事件/知识/API、截图、策略隔离和原件hash。15源/测试文件hash覆盖最终运行，三份计划字节一致，既有原证据与无关工作区文件保持。

该观察仅限document_light_dom及窗口结束状态；时间关联不是业务因果或完整响应。默认AI实际引用并操作、网络/跨文档/Shadow DOM、清洗/遗漏检查、人工DeepSeek、完整App/场景/别名/报告、Electron实际缓存与更多故障、Docker、其余P0—P5、生产历史迁移和两份旧Worker汇总原字节恢复继续未完成。


### 6.79 生产冷启动依赖顺序与实际恢复〔基线7497b469〕

完整P0—P5目标保持。本节处理6.78保留的生产API退出码3：消费表外键先引用未创建的agent_test_run_items。先读取真实原stderr、工具初始化/消费DDL和运行DDL/恢复，确认运行表还依赖项目、用例和套件，不能直接移至最初工具构造位置。研究见[P0_STARTUP_ORDER_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_STARTUP_ORDER_DESIGN.md)。

main.py保留原服务构造，项目/用例/套件初始化后先调用原run store幂等DDL，再初始化工具/动作/消费表及工具恢复，随后继续原run service恢复。新增两条原日志设施事件表明顺序；不忽略外键、不创建占位表、不增加依赖或接口，不重写历史。配置仍MySQL，业务仍PG，原件仍RustFS。

独立生产API/DOCX专项首轮1失败23.17秒：生产API已启动并通过外键和MySQL检查，新增测试保存RowMapping不能JSON序列化。沿项目已有dict(row)转换修正后，在另一个空库重跑：**1通过60.07秒**。启动前只读核对run item/消费表不存在；API建成两个validated外键，MySQL recording.capture_settings合法；API重启后不改配置修订。实际DOCX原件RustFS读回、上传/任务/配置冻结、独立Worker解析、终态重投和历史快照均通过，零模型调用。

独立生产执行Worker专项**1通过75.67秒**，真实POST→GET→DELETE，五次受控模型阶段调用，审批和创建后的API重启保持原attempt/Job/数据，终态重投无额外业务操作、零残留。受控模型不能证明默认自主Agent通过。

扩大回归首轮24失败54通过2跳过80.62秒：23项是本次命令遗漏两个证据目录变量，1项旧审批恢复测试替身缺少当前条目/运行读取能力；失败原件保留。配置目录和真实SQL池开关后，生命周期/原子依赖/消费与清理**65通过、零失败/跳过157.97秒**；旧测试补查询替身后其模块**15通过2.59秒**，不改运行生产算法、原attempt/首次时间断言仍在。语法检查仅为附加验证，没有前端契约变化，本次未重跑前端构建。

命令见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)。[生产DOCX XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-startup-order-production-fixed.xml)、[执行Worker XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-startup-order-execution-first.xml)、[配置后回归XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-startup-order-regression-configured.xml)、[审批模块XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-startup-order-claim-fixed.xml)及[本次审计](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-startup-order-validation.json)关联真实原件、FK/MySQL快照、进程输出、原HTTP和PG状态。6.78冷启动失败原件保持，不能删除旧失败或改称其当时已通过。

本步修复生产启动基础缺陷，不代替完整录制AI使用/清洗/人工DeepSeek、正式App/场景/别名/报告、网络/跨文档/Shadow DOM、Docker、其余P0—P5、生产历史迁移及两份旧Worker原汇总恢复。完整升级继续未完成。

### 6.80 所贴方案正文同步与上传边界复验〔基线09286a68〕

本次按用户“更新这个方案”的要求直接修改完整正文，并同步所贴附件及原选中的PLAN.md；没有实施新的业务功能。更新1.4八项交付/验收要求、3.1.1上传独立保存的当前源码依据、4.1.5现有UI模式未开放执行子方向的事实，以及第六节当前基线和录制进度。2.3继续使用简单的条件来源检查，模板仅生成、项目隔离选取、必要前置补全及上传无需Git的约定保持。

实际运行已有两个`[False]`上传节点：**2通过、0失败、0跳过，pytest输出7.28秒**，见[XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-refresh-09286a68-upload.xml)及[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-refresh-09286a68-upload.log)。实际临时文件经应用路由和本地测试存储，覆盖原件保留、版本追加/当前内容去重、请求恢复、父版本冲突、同名独立上传，以及OpenAPI转换/项目URL修改保留原件。此轮未启用真实MySQL/PG/RustFS，未运行生产Worker、完整App、默认模型或人工DeepSeek；这些历史结果与未完成验收继续按原章节保留。

三份字节同步、章节引用、源码依据、原始日志/XML及无关工作区文件保持核对见[本次验证](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-refresh-09286a68-validation.json)，实际命令见IMPLEMENTATION.md。没有更改API、配置、依赖、数据库或Git工作区策略；完整系统验收仍未完成。

### 6.81 录制知识进入默认规划、明确来源契约与正式执行边界〔基线d9702edd〕

完整P0—P5目标保持。上一目标轮为progress（d9702edd更新三份完整方案并实测上传）；本轮继续真实录制/默认模型/独立Worker，以及已确认产品缺口修复。依据与失败原件见[P0_RECORDING_AGENT_EXECUTION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_AGENT_EXECUTION_DESIGN.md)。配置沿MySQL，业务/任务/seed/checkpoint/计划沿PG，截图原件RustFS、知识沿原Memgraph投影；没有新表、依赖、第二执行器或历史改写。

实际受控页面点击“展开详情”产生服务端唯一DOM确认串。生产API/真实CDP录制→响应采集→完成/PG/图→RustFS截图读回已运行；默认模型使用当前MySQL模型的隔离配置，并通过真实SSE观察器透明转发原provider，保存物理请求/响应原字节。模板哨兵不进入执行，正式Agent收到完整固定JSON和完整录制图。人工DeepSeek及完整App未作为本轮验收。

前期first/connected/streamed三次夹具失败分别为跨客户端页面查找、误读decision.items和自设白名单遗漏合法runner；重审夹具后使用原AgentRegistry和正式审批身份，不删除模式Prompt或绕过审批。registered真实失败1项77.80秒：模型把source_refs的身份当成已读内容，实际未读录制而规划服务接受。据此增加可选required_sources=[{kind:document|recording,source_id}]，只表达本次明确选定的来源；目录/身份不算已读，各target须引用现有query取得的真实证据。未读/遗漏引用返回原规划循环的具体反馈，仍受冻结预算和超时约束；no_match保持无执行，不能变自由测试。旧请求序列化/hash不变，不要求所有历史来源必读、不用关键词判断。

来源要求在请求和PG seed冻结；专用指导全文与required-sources-v1版本追加保存，恢复不换当前指导。直接/持久/阶段规划、前端TS和原API JSON透传同步；回归重新规划继承原要求，固定子集保留来源证据，依赖提升为目标但缺引用时走既有重新规划。尚无App来源选择表单。新增真实文件/API、跨项目、旧请求/seed、检查点丢回执恢复、固定回归任务及终态重投11通过7.20秒；固定回归低层测试使用契约parent/result身份，真实失败项选择另由原集成模块验收。

扩大最终15模块 **172通过、0失败、1跳过，107.50秒**：[XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-agent-regression-final.xml)、[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-agent-regression-final.log)。唯一跳过为未开RUN_LIVE_CASE_PLANNING_MODEL_TESTS的旧默认规划专项，不能计通过；本轮另有真实默认模型专项失败，见下。真实MySQL/PG/RustFS、原解析进程/原任务Worker、旧seed与阶段协议、实际回归失败/恢复均纳入。前端npm.cmd run build实际vue-tsc/Vite通过，退出码0、Vite7.19秒；原10.20秒日志保留，原句柄已消失，没有臆补其退出码。初始缺测试文件命令、漏证据目录、旧environment空字段hash断言和Worker夹具parse子表清理失败均保留；修的是测试的原契约/所有权登记，不改业务hash/FK，也不覆盖旧汇总。

required-production两轮默认规划确实读取并引用完整录制，正式运行进入审批；夹具误读AgentRegistry返回类型而1失败106.90秒。用真实PG审批离线复核后，owned-production **1失败154.81秒**：7个真实模型HTTP200，只有录制阶段一次真实页面POST，执行阶段零页面副作用。现有目标策略以untrusted_private_target拒绝本地URL；模型明确生成blocked报告，未声称passed。最终回执使用browser-control#1等虚构call_id/错误路径，服务器拒绝后正式结果error。未放开安全策略或伪造证据；该终态保持，未重置/复用。没有通过运行，故不能声称“终态passed重投无模型/页面副作用”已验收。

下一步沿实际失败研究正式运行的可信目标继承、无父session的授权来源以及denied/not_run回执的真实证据引用。项目base_url和录制地址不是授权，不能为通过测试将资料升级成授权；现有严格工具身份检查保持。观察器只证明本地观察到的物理发送，不证明provider内部发送，也不把7次不同阶段调用视为重复请求。发送超时/截断和所有阶段追踪还需继续。

三份方案逐字节同步，相关源码/原请求响应/实际图/截图/PG/最终测试及无关文件保持核对见[p0-recording-agent-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-recording-agent-validation.json)，命令见IMPLEMENTATION.md。用户.gitignore/Worker与环境诊断保持；本轮硬编码路径覆盖的旧回归Worker汇总已先另存新结果、再恢复HEAD原字节；两份更早未恢复的Worker汇总限制仍保留。

完整升级未完成：UI默认实际操作/合规授权/回执、清洗/遗漏/网络跨文档ShadowDOM、人工DeepSeek/完整App/场景/别名/报告；Electron实际缓存/并行attach/更多故障、Docker性能安全原始结果、全阶段HTTP、P1生成导入产品、P2默认补全/准备状态/未知副作用/其他模式、需求业务评审/环境适用、历史迁移及两份旧Worker汇总恢复继续实施和验收。本步不将来源读取通过替代执行闭环。


### 6.82 工具证据身份、命令目标准入与默认执行终态〔基线f2ba964e〕

完整P0—P5目标保持，上一目标轮的调研提供了具体失败依据，本次已修改代码、实际测试并保存新原件。沿6.81真实PG轨迹确认：工具消息body缺少可引用身份，graph和审批恢复重复组装；消息预算压缩又丢来源声明，宽输出可能被截成无效JSON。采用原manifest引用结构，共用runtime/tool_messages，正文有call_id/job_id/output_reference；无job及待处理job为null，不冒充已执行证据。压缩保留身份和来源，过宽时明确省略并保留JSON。case_result_contract v4说明真实ID、输出根及未执行checks=[]；服务器仍严格核验所有权/实际路径，不新增执行器/表/依赖，不改变历史冻结依据。详见[P0_RECORDING_AGENT_EXECUTION_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECORDING_AGENT_EXECUTION_DESIGN.md)。

真实默认模型新增可信父会话与未授权两个独立场景。可信父scope是本次隔离PG测试服务端显式创建，run.session_id通过现有链路继承，只证明已有继承能力，不是完整产品授权入口。第一次2失败201.12秒：模型并列请求两项审批，夹具len(pending)==1错误；改按条目approval_id继续。保留的未授权轨迹还有真实产品缺口：target_url会拒，command args中的同一私有地址未检查，实际产生点击。读取MCP命令解析及原native导航语义后，抽browser_commands共享解析；只对browser-control的open/goto/tab-new/explore明确URL位置应用原目标策略，原可信scope及元数据限制保持，不扫描任意JS/填充文本。修复前7失败4通过0.98秒，原件保留；这不代表所有重定向/脚本网络目标均已受控。

可信场景第二次1失败205.03秒、1排除：range(8)审批夹具中止在下一项snapshot，11个真实模型HTTP、3次页面POST，仍无终态。该轨迹也保留了不完整命令args、独立evaluate_js打开新页和资源等待等问题，不宣称已修复。夹具重审为依正式状态继续，按原650秒期限收尾，不提高产品预算。真正执行始终要求passed和实际确认串，blocked场景另验，不替代执行通过。

最终十一模块 **215通过、0失败、1跳过，55.38秒**：[XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-tool-evidence-verified.xml)、[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-tool-evidence-verified.log)。包含实际文件/HTTP POST-GET-DELETE/PG jobs、原失败回执重放、graph/审批正文一致、空job/待处理证据、宽输出/长摘要、原所有权拒绝、消息历史与真实Chromium生命周期。真实图准入未授权零HTTP；同一实际命令授予可信scope后打开唯一标记页面，snapshot及已保存Job均有标记，原件见p0-browser-command-target-verified。末增畸形命令日志/原解析拒绝专项1通过0.95秒，12排除。唯一跳过是原read-only默认模型专项，另有以下生产默认UI专项。语法编译退出0仅为附加检查。本轮没有前端契约/配置/迁移变化，未重跑前端build。

原测试失败均保存：首轮52通过1失败1跳过26.83秒（把not_run验证记录误作工具证据）；六模块72通过1跳过27.72秒；扩大首轮196通过1失败1跳过39.77秒（HEAD已有bind_call，旧测试却传None上下文）；真实浏览器21通过1失败19.00秒（证据保存漏datetime转换）。按实际既有契约修测试，保留拒绝断言；不改产品判定/外键。查询诊断曾误执行一个不存在的占位模块import，退出1、无文件修改/功能测试，不计验证。

最终真实默认模型专项 **1通过、1失败，396.42秒**：[XML](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-tool-evidence-production-final.xml)、[日志](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-tool-evidence-production-final.log)。两项均走当前MySQL默认模型的隔离配置、透明SSE、生产API、真实CDP录制/响应/PG/Memgraph/RustFS读回、完整来源query与冻结、独立Worker及原审批。未授权场景4次真实HTTP200，只有录制1次POST，执行0次；正式结果blocked，原终态任务重投没有新增模型发送/页面操作，verification与独立Worker输出已保存。

可信父scope场景22次真实模型HTTP200，页面POST共4次（录制1、执行3），最后原DOM结果含服务端唯一确认串。模型正文工具观察和报告中已使用真实call_id与相对stdout路径；但最终响应为说明报告、其中夹带JSON回执，原parse_response按纯JSON契约拒绝（line=1,column=1），正式结果error。没有passed终态重投证据，不能把实际点击或报告自述通过计为用例passed。原error保持，不从报告抽块偷偷改成成功，不重置旧条目；下一步沿最终Prompt/模型/图终态研究报告呈现与正式回执职责、命令完整args与浏览器页面保持、审批后的预算及只读修正。不能以更多业务点击重试回执错误。

三份方案字节同步，原失败/原SDK请求响应/PG/截图/原进程输出以及用户无关文件保持见[p0-tool-evidence-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-tool-evidence-validation.json)，实际命令见IMPLEMENTATION.md。配置仍MySQL、业务/授权实例/结果仍PG、大工件RustFS。产品无父会话的目标授权配置/界面、完整UI回执与操作、录制清洗/遗漏/人工DeepSeek/App/场景别名/报告、Docker、全阶段HTTP、P1—P5其余验收及生产历史迁移/两份旧Worker原汇总恢复仍未完成。


### 6.83 方案同步与正式回执阶段再调研（基线 e7610fc4）

本批直接更新完整方案及用户所贴附件、原选中的 PLAN.md。保留八项诉求和 P0—P5；模板只生成，项目内选取完整用例、初排/补全/最终排序，上传独立保存版本且不经 Git，轻量解析与历史快照约定继续生效。

按保存的真实 HTTP、PG 和页面证据复核：可信场景 22 次物理发送、其中 20 个执行上下文请求，共 4 次页面 POST（1 录制、3 执行），最终正式 error；原混合报告在当前严格解析器再次复现 line=1、column=1 错误。原截图 artifacts/0/uri 路径确存在，仅证明该引用可解析。独立未授权场景的纯 JSON blocked 能解析，原终态重投零模型/页面副作用，不能替代可信场景 passed。

只读回执请求/原证据目录已在服务中存在，但正式适配器尚未调用；2.4.1 因此改为明确的待实施阶段，2.5 去掉容易被理解为现已实现的纠正表述。新增持久调用意图/响应、归属与严格判定、只读边界、预算及新旧协议恢复的工程要求。原最后快照 loop_iteration=0、max_iterations=8 与 20 次执行请求不一致，剩余预算不能依该字段直接推算；先查证跨审批累计语义，不暗增预算。详见 [回执衔接调研](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_RECEIPT_HANDOFF_RESEARCH.md)。

本批执行只读原证据复核脚本，检查三份方案逐字节一致、408 个上一批原工件及受保护文件哈希不变；输出见 [方案复核记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-receipt-handoff-validation.json)。这是现有真实失败的再复现及方案校验，没有修改生产代码、配置、表或迁移，没有重跑默认提供商/浏览器或扩大功能测试，也没有将原失败改成通过。完整目标、无父会话的产品授权、完整 App/人工 DeepSeek/清洗遗漏/Docker 及其余 P0—P5 继续未完成。

### 6.84 跨审批模型预算与多项审批恢复（基线 fc7f94bd）

本批实际实现并同步这份方案、所贴附件与原选中的 PLAN.md。依据原默认 UI 的20次执行请求/loop_iteration=0反例，核查 AgentLoop、model_invoker、finalizer、图暂停和 Runtime 恢复。正常新 LangGraph turn 增加累计 model_call_count，调用前检查原 max_iterations 与既有上下文90%阈值；状态拆装、持久快照和审批暂停保留原预算。仅正常图初始化，专用 security/DeepAgents 不假造累计数；缺字段旧快照仍用旧规则。没有新配置键、依赖、表或迁移，没有修改全局 MySQL 默认模型配置。

第一版实测揭示图节点自建 pending_turn 漏存预算：第一快照累计1，后续丢字段；根因与原PG保存到p0-approval-budget-diagnostic。多审批进一步失败：两个审批只存ID，批准一个后剩余ID存在但审批集合空，stage=resumable，正式适配器拒绝。修复图节点完整审批记录持久化，部分批准后保持waiting_approval，最后一项后才进入模型；没有放宽正式恢复校验。详见 [预算与审批调研](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_APPROVAL_MODEL_BUDGET_RESEARCH.md)。

真实HTTP/内存与独立PG六项专项 **6通过，15.57秒**；最后增加终态重放的多审批专项 **2通过，8.61秒**。测试POST创建、GET查询、DELETE清理；原上限3在当前默认改99后仍不允许第4次调用；受控usage950/1000在审批后不再发送；同一回复两项审批首项批准不增加调用，三次调用内合法回执passed，重放不增加调用/效果。扩大九模块 **102通过、0失败、21跳过，170.02秒**，见[p0-approval-budget-regression-verified.xml](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-approval-budget-regression-verified.xml)。跳过1项旧Redis/MySQL/PG/RustFS标志未启用，20项因环境缺deepagents；这些均不计通过。

最终生产API/独立Worker专项 **2通过，95.35秒**，见[p0-approval-budget-production-final.xml](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-approval-budget-production-final.xml)。实际隔离MySQL、全新PG、RustFS、临时RedisDocker、真实进程停止/重启及受控HTTP模型。原上限3/8，重启进程默认改13/18仍保留原上限；分别为3次执行调用后预算耗尽error、4次执行调用合法passed。另有各1次规划请求，总物理发送4/5。两项均实际POST/GET/DELETE、清理零残留、终态任务重投零新增模型和业务操作。保存解析后的请求JSON、完整PG记录和进程stdout/stderr；不能把解析JSON称为原始传输字节，也不能把受控模型passed当作外部默认模型UI通过。

前端新增可选“模型调用”累计值/原上限，保留原循环值，读取真实新/旧生产PG快照挂载组件测试；历史缺字段不编造计数。组件及i18n **4通过**，vue-tsc/Vite build退出0，构建7.39秒；八个后端文件compileall退出0仅为附加检查。最初Node证据读取测试放src导致typecheck失败，按已有tests布局移动，未引入依赖或改tsconfig，原失败日志保留。

全部早期失败、预算丢字段、多审批、测试路径不存在、初版PG序列化字符串及前端URL/类型失败保留，最终命令见IMPLEMENTATION.md。三份方案同步、408个上一批工件和受保护文件保持、新原件和源码哈希、实际PG/HTTP计数与密钥不落证据校验见 [本批审计](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p0-approval-budget-validation.json)。当前调用数在既有快照边界持久，不声称任意发送后/快照前中断的未知回执均已解决。本节为 be660b34 的历史交付；当时只读正式回执纠正尚未接入，后续工作区实现和验证见6.85。默认可信UI passed/重投、无父会话产品授权及完整P0—P5仍未完成。

### 6.85 方案同步：一次只读回执纠正的工作区实现与实测边界

本次用户要求更新所贴方案，修订基线为 `be660b34`。本次只修改方案、实施记录和验证工件；前序尚未提交的后端/前端实现及其他工作区改动保留，不随本次文档提交发布。三份方案为项目 PLAN、用户粘贴附件和原选中 PLAN，同步核对见 [本次审计](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/plan-receipt-current-validation.json)。

**源码依据 → 本次修订：** `agent_case_receipt.py` 已复用对象存储及租约约束的原检查点，先保存 invoking，再保存 response；`agent_case_execution.py` 已在原回执错误时调用它，恢复进入原 `_finish`，不重新进入业务图；`case_planning.py` / `case_execution.py` 冻结可选协议及累计预算；`main.py` 注入真实模型运行时和对象存储；前端 `CasePlanDialog.vue` 已沿项目策略入口保存开关。因此2.4.1和2.5改成“当前工作区已接入、默认关闭、按原预算一次纠正”，不再笼统写“尚未接入”。不配置协议时省略新增序列化字段，旧哈希及旧运行语义保留。

当前保存完整用例、说明、已结束且归属正确的Job输出、原回复和具体错误；不提供工具，不能重做导航、点击、创建或清理。请求原件和原响应存RustFS，PG保存URI、大小、SHA-256、协议、调用身份、原图累计调用数、总数、原上限及非敏感模型参数。恢复有已保存响应就核对哈希后复用；只有意图则保留未知。已保存的错误响应也不会触发第二次纠正。原默认UI混合报告及失败结果保持不变。

| 验证批次 | 实际结果与范围 |
|---|---|
| 本次 `p0-receipt-correction-plan-review` | **21通过、1跳过，51.40秒**；真实本地业务HTTP、内存与隔离MySQL/PG/RustFS；包含原混合报告、引用错误、归属、截断、原预算、默认关闭、合法原回执、检查点提交后故障模拟及原结果重放。跳过真实默认提供商，未在本次重复外部调用 |
| 本次 `p0-receipt-correction-plan-contract` | **17通过、0跳过，29.72秒**；消费阶段回执及兼容基线、真实图/审批/PG专项，并重跑两项真实临时文件上传回归。上传未配置Git，验证新旧原件、版本追加、请求恢复、同名独立文档和OpenAPI派生更新保留原件；上传节点使用本地测试存储，不能当作生产上传全链验收 |
| 前序扩大 `p0-receipt-correction-verified` | **4失败、115通过、8跳过，231.46秒**；4失败均为执行命令遗漏 `QA_CONSUMER_RECEIPT_DIR`。本次配置该目录后重跑消费回执模块通过；不将原失败日志改为通过，不把未启用范围计为通过 |
| 前序生产 `p0-receipt-correction-production-fixed` | **1通过、2未选取，51.45秒**；真实生产API/独立Worker、隔离MySQL/PG/RustFS/Redis、审批及进程重启，模型为本地受控HTTP服务。实际4次图模型调用+1次纠正，另1次规划，共6次物理模型HTTP发送；业务POST/GET/DELETE共3次，清理零残留，正式passed，终态重投零新增模型/业务调用。该轮早于新增finally证据导出，不能声称验证了新的导出代码或纠正阶段杀进程恢复 |
| 前序真实默认提供商 `p0-receipt-correction-default` | **1失败、21未选取，190.81秒**；业务图为受控实现，只有纠正调用实际默认模型。原响应 `mode=http_error`、`ProviderClientError`、`Request timed out.`；检查点已保存response，原图4次+纠正1次，总5、原上限8。仅确认一次逻辑纠正调用，未采集SDK物理发送次数，不能推断只有一次HTTP请求，更不能称默认UI通过 |
| 前序前端 `p0-receipt-correction-web-fixed` / `web-build-fixed` | **68项组件测试通过，2.27秒**；类型检查/构建命令退出0，Vite构建7.96秒。未运行完整浏览器App；初次ES2020不支持测试中`.at`的失败日志保留，修订未更换依赖或tsconfig |

生产初次执行还发现新只读请求缺少可观测契约要求的trace_id，在实际发送前被拒绝。当前 `build_assessment_request` 使用原图trace_id，缺省沿原turn_id；旧兼容测试只归一这个新增追踪字段，完整用例和回执仍按原基线比较。初次失败及找回的请求原件保留，不把该失败称为提供商失败或业务测试通过。

**后续顺序：** 先审查并验证当前实现的剩余边界及独立Worker新增finally导出，再按实际超时证据查证默认模型调用配置、上下文大小和耗时；不以重复同配置调用、增加预算或重做业务替代研究。随后验证默认模型可信UI正式passed/终态重投及纠正阶段真实进程中断，完成代码批次交付后再更新状态。UI人工DeepSeek录制、完整清洗及知识使用、Excel/JSON完整导入产品、Docker性能/安全真实报告、需求追踪执行证据及其余P0—P5继续按原方案推进，均未因本节专项通过而标为完成。


### 6.86 回执纠正与冻结传输设置：本轮验证及账号暂缓

本轮沿现有 ModelTransportOptions 增加可选 receipt_correction_transport_options，项目MySQL策略及固定PG执行上下文同步；只有启用纠正协议时允许设置。未设置时省略序列化字段，旧哈希及提供商默认行为保持。参数捕获和实际调用使用同一冻结选项，设置不进入模型业务提示；已保存响应恢复不读取当前配置、不再发送。前端可选超时与SDK重试沿原项目策略保存。无新表、依赖或全局配置修改。模板依然只生成；上传依然独立于Git。

真实SDK断连服务复现默认一次逻辑调用3次POST、max_retries=0时1次；不能推断历史外部超时也是3次。新增严格工具调用拒绝、RustFS原件篡改与检查点身份/计数校验。扩大回归暴露旧无累计字段状态在请求日志中KeyError；model_invoker日志改用state.get，不补造额度或放宽实际预算准入。最初1失败、扩大5失败及单模块复现保留。

本轮最终八模块98通过、4跳过、266.01秒：跳过两种真实默认提供商、一项真实完整用例模型及一项另需public-worker开关的集成。独立生产API/Worker三场景3通过148.48秒；显式冻结60秒/0重试专项1通过66.90秒，6次模型HTTP（规划1、图4、纠正1）、3次真实业务效果、清理0残留、终态重投0新增。受控服务结果不等于默认UI通过。前端75项通过，类型检查与Vite构建通过12.61秒。

真实默认step-5-preview单次传输试验1失败11.99秒；SDK retry=0、timeout=60，记录到1次物理HTTP，403 real_name_required、0.485秒。请求、原响应、PG/对象证据保留；原历史超时不被改写为实名认证根因。用户选择“先继续其他模块，账号处理后再验收”，因此暂停后续真实Step调用，继续完整模板及Excel/JSON产品验收。

纠正检查点提交后故障目前是BaseException模拟，真实纠正阶段进程中断、默认可信UI passed与终态重投仍待验。完整人工录制清洗/遗漏/DeepSeek/App/场景别名与报告、Docker真实性能安全、全阶段物理HTTP、准备/未知效果/修订/其他模式、生产迁移和历史Worker汇总仍未完成。本轮证据与前序200/408/151个原工件保持校验见p0-receipt-transport-validation.json；研究见P0_RECEIPT_TRANSPORT_RESEARCH.md，实际命令见IMPLEMENTATION.md。


### 6.87 模板与Excel/JSON导入：完整产品验收〔基线97ac33b6〕

研究现有App/router、模板编辑器、导入弹窗、TestCasePage及PG版本治理后新增test_case_import_full_product.py；本批没有生产代码/配置/表/依赖修改。使用实际构建资源、Chromium、独立生产API及重启、隔离MySQL/PG/RustFS和Redis容器。最终1通过/0失败/0跳过54.82秒，原日志/XML与完整工件在p1-import-full-product-delivery。

实际页面创建两个项目，载入系统默认模板、修改项目写作指导/说明/完整示例并保存MySQL，载入默认内容不会隐式覆盖项目模板。实际xlsx行布局、分表2/1步骤排序及JSON原件上传下载字节相同；预览/错误报告下载、项目列映射MySQL保存、冻结批次提交PG草稿、同批次API重放不重复创建。含有效候选与错误行的同一Excel不能提交；新增预览的编号冲突不能覆盖旧用例。

JSON树/原文页使用完整业务JSON，自定义嵌套、false/0/null保持；提交评审/启用后在实际页面追加V2草稿，V1内容/启用指针保持。另一项目用例列表为空、不继承项目模板或导入配置、来源GET404；独立API重启后完整App重新导航，原项目模板及3用例4版本读回一致。业务数据PG、编辑配置MySQL、原件RustFS边界保持。

六次早期失败保留：select精确label、错误total假设、等待激活响应不完整、详情页新建路径、返回按钮图标名、计划停机窗口500。均为新验收测试问题，不修改生产代码以绕过；根据实际Schema/ARIA/HTTP/PG修正。最后停机失败由代理ECONNREFUSED/ECONNRESET证明，沿既有产品测试保存计划停机窗口，恢复后的未计划API错误及pageerror为0。审计p1-import-full-product-validation.json检查原回执413/方案151工件与受保护文件、三份方案、新源码/原件哈希和最终PG。

本步验证手工模板/导入/版本治理，不调用真实模型，也不代表文档生成、完整P1或P0—P5全部完成。默认Step验收按用户决定待账号处理；人工录制清洗/遗漏/DeepSeek/App/场景别名、Docker真实报告、物理HTTP全阶段、准备/未知效果/修订/其他模式、业务评审及生产迁移继续待验。下一步推进不依赖真实Step的Docker原工具/结果/报告证据调研。

### 6.88 Docker性能采集与结果契约〔基线055bf573〕

研究实际PerfRunnerService、两种adapter、RawMetrics/PerfMetrics、报告/模式/验证及本地课程后，用现有DockerResourceManager、Redis租约/PG分配意图、独立生产API与隔离存储复现。缓存k6 v2.2.0，完整镜像ID保存在原件。修复前默认40样本没有p(99)，代码读0，Markdown/JSON报告pass；实际用文档选项即可采到P99，因此保留--summary-export，无新引擎机制/依赖。官方同版本summary-wrapper明确旧嵌套threshold布尔值是失败标记，false即成功，不把它误判为失败。

生成load/smoke声明完整summaryTrendStats。两引擎缺失为None/null、实测0保持；非法汇总明确抛解析错误，runner已有异常日志记录失败，并新增测量缺口日志。SLA保存missing_metrics，缺失/零样本为inconclusive、passed=null，确定违约优先fail。曲线/观测/基线、Markdown/HTML、模式JSON工件和提示/枚举同步；正式用例验证对null输出partial，不能passed。PG现有JSONB保存，不新增表/迁移、不重写旧数值；前端通用JSON树/Record契约支持null，没有新增前端代码。

修复后首次真实四场景1通过42.29秒、扩大96通过17.36秒，但原件额外揭示0.001ms阈值被截成0。修订保留浮点阈值，中间:g版本1通过34.04秒/扩大97通过17.54秒；最终str格式避免六位有效数字损失，追加123.456789断言。最终受影响契约/适配器21通过12.04秒、真实四场景1通过33.75秒、0跳过；compileall实际退出0。旧失败、各次脚本/summary/stdout/stderr/退出码/镜像/报告/PG与清理证据都保留，见p5-perf-measurement-*；审计见p5-perf-measurement-validation.json。

实际四种结果：正常load和smoke采到P99且pass；命令故意关闭P99时null/inconclusive；受控目标真实违反0.001ms时引擎{ok:false}、退出非零、独立SLA与报告fail。通过模式报告路径保存PG后四行逐条核对，缺失和失败不进入后续基线。只删除本测试完整容器ID，分配记录及所有创建容器不存在的事实已验。这是短时测量链路验收，不是容量或Agent验收。

原引擎严格<与分析器最大值>在等于阈值时仍可能分歧，本批分歧inconclusive，不默认为pass。原件生产RustFS持久化、错误烟测/退出分支、性能Agent完整模式/正式App、逐级容量和AI流式指标、安全工具与报告仍待继续；默认Step按用户决定暂缓，人工录制清洗/遗漏/DeepSeek/App及其他P0—P5未完成项不取消。本轮研究P5_PERFORMANCE_MEASUREMENT_RESEARCH.md及实际命令IMPLEMENTATION.md已更新，三份方案同步。


### 6.89 性能原件持久化、冒烟与异常退出〔基线cf703e05〕

先实际复现再修订：现有runner在TemporaryDirectory退出后删除脚本/汇总，stdout只留末2000字符、stderr及原件URI缺失；缺汇总、坏JSON和真实teardown退出107仍通过冒烟，failed load有合法样本时报告pass。原反例p5-perf-evidence-reproduction保留，1失败28.06秒。首次before1失败18.99秒只是测试漏传PerfTarget.name，不能当作生产故障。研究见P5_PERFORMANCE_EVIDENCE_RESEARCH.md。

复用ToolRuntimeService的共享ArtifactStorageService，在临时目录删除前流式保存脚本、数据/引擎产物、完整已捕获stdout/stderr和最终清单；每次smoke/load用独立执行身份，文件保留SHA-256、大小及正式项目/会话/trace/运行/尝试归属。环境只记录变量名。RustFS保存原字节，现有PG ToolJob保存URI/元数据/业务结果；不增加表/迁移/配置/依赖，不新建私有存储客户端。成功冒烟保留到后续run，失败和正常原件都通过现有/content下载接口展示。旧历史结果不改写。

冒烟缺件、损坏、非法数值、缺失败率、非零退出、超时与新原件未保存完整明确失败；保留既有失败率>50%策略。实测SLA违约仍fail；没有确定违约但进程失败/未知或新原件不完整时inconclusive，不能pass或进入成功基线。Markdown/HTML显示引擎退出与证据缺口，新增外部错误文本HTML转义。实际本地子进程超时保留已输出双流；部分上传失败保留成功引用、记录明确错误，不重复压测。原下载路由仅对声明校验值的工件检查字节/大小，无校验字段的旧工件兼容读取。

最终真实工具/引擎/PG/RustFS/API验收p5-perf-evidence-delivery **1通过51.26秒，0跳过**：受管http-echo和缓存k6 v2.2.0；正常smoke+load共2次，缺件/损坏/107三场景各1次且停止load；24个原件重启后逐个下载并保存原字节，hash/大小/归属一致。另一项目下载404，同大小首字节篡改409，恢复原件下载相同。独立failed load退出107、SLA本身通过但报告inconclusive；真实local超时保留stdout/stderr；第一次真实RustFS上传成功后注入第二次上传失败，1次引擎执行/1份原件保留/inconclusive，不把注入故障称为存储服务宕机。终态重放0新增引擎，8个受管完整容器ID均已删除。测试从已验证guard阶段开始，不调用Agent/模型。

扩大19模块 **123通过21.42秒**；实际四场景测量回归 **1通过35.91秒**，pass/pass/inconclusive/fail及原PG指标保持。compileall退出0仅附加检查。前序contract-first77通过12.78秒；verified1失败51.43秒为测试错误比较派生下载展示字段，verified-final1失败52.11秒为测试使用不存在/download路由，修正为实际/content；download-verified1通过55.24秒。最终delivery额外保留24份实际下载字节，未覆盖前序输出。全部失败、脚本、summary、执行结果、PG、API日志及清理证据保留。

本批未改前端，未重复前端构建/完整App；未验证真实JMeter、完整Agent性能模式、容量逐级加压、AI流式指标、安全引擎。expect_extract仍使用stdout搜索，不能当作真实响应变量提取已验。下一步继续这些实际缺口和完整UI录制/清洗/遗漏/DeepSeek/App/场景别名、物理HTTP全阶段、准备/未知效果/修订/其他模式、业务评审及生产迁移。真实Step按用户决定等待账号处理；完整P0—P5仍未完成。三份方案同步与前序155/153/413/200/408/151原工件未改校验见p5-perf-evidence-validation.json；实际命令见IMPLEMENTATION.md。

### 6.90 k6响应、预期状态与依赖header〔基线d3e36b09〕

沿现有adapter/runner/模式/报告、API依赖身份及本地接口/性能课程查证；官方k6标准checks、Response.json、Params及expectedStatuses提供依据。缓存镜像实际v2.2.0，不升级镜像。原四场景反例1失败39.32秒：有效响应不打印变量名却失败、缺字段但stdout打印同名文字却通过、实际200不符合预期201却通过、两目标生成重复const导致引擎107且零HTTP。旧原件保留在p5-perf-correlation-before。

使用标准summary.root_group.checks的passes/fails投影request_validation；状态、提取与header准备分别保存，不新增私有stdout协议。Correlation沿原三字段增加可选from_target/into_target/header_prefix；未设置新增字段时序列化仍保持原三字段。默认首目标来源、紧邻下一目标消费；显式名称须唯一存在且消费在后，不向无关目标广播凭据。变量仅在当前iteration内；0/false有效，null/undefined/空字符串或坏JSON不能提供必要header。缺依赖跳过该消费目标，后续独立目标继续，不能将“header已准备”当作任意业务断言全部通过。

smoke和load/report读取同一事实，负向401配合responseCallback计量；check失败为fail，必需check缺失为inconclusive。进程、原件和SLA既有规则保留。模式输出/JSON工件、独立工具analyst工件及提示同步，报告新增结构化判定日志。编辑配置MySQL、业务结果PG、原件RustFS边界不变；无新表/迁移/依赖/前端修改。JMeter目前明确not_observed，尚未证明其提取、断言和业务相关性，不能称两引擎均验收。

| 本轮验证 | 实际结果和边界 |
|---|---|
| 初次修复四场景 verified | 1通过38.73秒；随后增加独立目标、负向和边界场景，未覆盖后续修订 |
| 扩展full-verified | 1失败102.39秒；前10场景结果正确，第11次resource_pending_reap。容器已删除，隔离测试只有API、缺独立清理Worker，配额待持久回执；保留失败，不扩大名额/手工释放租约 |
| 接现有清理CLI worker-verified | **1通过155.86秒，0跳过**；12种smoke和2种短时load全部实际运行。提取后受保护接口200并核对凭据hash；缺依赖不请求受保护接口、独立/plain仍运行；显式来源及Bearer前缀、0/false/null/空串/坏JSON/负向401均有服务端事实。两load退出0、SLA通过，报告分别pass/fail，现有PG保存两条对应结果；14份分配、14份completed清理回执、14次Redis配额归零和全部完整容器ID不存在已核对 |
| 扩大20模块 expanded / delivery-contract | 前序140通过44.36秒；加入判定日志后最终**140通过38.25秒**，0跳过，既有Starlette/AnyIO弃用警告1条 |
| 测量measurement-regression | **1通过65.82秒**，0跳过；真实四场景pass/pass/inconclusive/fail，PG指标及缺P99/违反阈值保持 |
| 原件evidence-regression | **1通过89.65秒**，0跳过；生产ToolRuntime从已验guard开始，PG/API重启保存响应投影，24份原件下载hash/大小相同，跨项目404、篡改409/恢复200、终态重投零新引擎、8个受管容器已删。未调用Agent/模型 |

compileall实际退出0。失败日志、实际脚本/summary/执行、HTTP事实/报告/PG/清理/原件保留，审计p5-perf-correlation-validation.json核对本批和旧253/155/153/413/200/408/151工件、受保护文件及三份方案；实际命令见IMPLEMENTATION.md。不把重叠回归次数累加为总覆盖。

继续待做：JMeter真实提取/断言及JTL报告；完整Agent性能模式/正式App、逐级容量和AI流式阶段；安全工具真实发现与证据；人工DeepSeek录制、完整清洗/遗漏/场景别名及AI知识使用；全阶段物理HTTP、准备/未知副作用/修订/其他模式、业务评审及生产迁移。真实Step按用户决定等账号处理。此批只完成有实际证据的性能专项，完整P0—P5仍未完成。


### 6.91 JMeter响应断言、有序旅程与布尔证据〔基线8a2b63f3〕

先读取原adapter/runner/报告、API关联来源、本地接口/性能课程及JMeter官方组件/监听器文档，用实际缓存justb4/jmeter:5.5复现。默认alpine/jmeter:5.6.3未验；没有升级镜像、依赖、配置或数据库。原before 1失败108.48秒：期望201/实际200误通过，预期401误失败，login/profile分属ThreadGroup无提取/传值。研究见P5_JMETER_RESPONSE_RESEARCH.md。

同一有序ThreadGroup读取明确Correlation来源与消费，JMESPath提取后动态header只用于指定目标。Response Assertion按预期Response Code Equals/OR，并使用首个断言Ignore Status支持负向401；删除中间JSR223状态归一。smoke显式覆盖并发/循环并关闭scheduler，避免正式hold_seconds截断3次迭代。保留CSV、吞吐Timer、思考时间和SyncTimer；未因此证明open/spike速率或容量。

sample_variables只能保存qa_extract_N/qa_dependency_N布尔事实，线程Token不写JTL。共享request_validation从实际JTL读取状态、来源/消费事实；明确false失败，缺件/缺观测不能作为通过；报告继续保留原SLA、进程及证据规则。早期变量原值批次只留本地，不提交含临时凭据的原件；对应失败日志/XML保留。最终原件不覆盖旧文件。配置仍MySQL，业务仍PG，原件仍RustFS。

最终response-delivery **1通过101.61秒，0跳过**：正常、错误状态、预期401、缺字段、成功链路五场景真实Docker/HTTP/生产runner；正式VUs=3，smoke实际1线程/3次，请求分别3/3/3/6/6。链路逐次验证受保护接口接受当前来源凭据hash。缺字段明确失败；当前仍发送空header消费请求并记录401，尚未实现k6的缺依赖跳过/独立目标规则。所有下载原件逐一hash/大小校验，扫描服务器内存签发值未出现在原件；五个完整容器ID不存在、独立清理CLI退出0。

报告/适配器等9模块contract-delivery **81通过20.39秒**，0跳过；其中四项使用实际JMeter汇总验证报告pass/fail，明确回归目标及SLA。初版contract-final 78通过/3失败20.34秒：测试错误把未声明回归的baseline期待为pass；仅修正测试契约，未改报告行为。前序最终四场景布尔证据final 1通过74.67秒；更早after4因scheduler1秒截断迭代1失败120.89秒，after5 1通过83.61秒，均不替代最终源码。此前after2确实已终态失败（105.417秒），早期进程观察未取得完整句柄，不能由当时未看到末尾文件声称资源清理是根因；其失败结果保留。

真实k6测量回归1通过40.65秒，原四报告pass/pass/inconclusive/fail；原件/ToolRuntime/PG/API重启回归1通过58.50秒。后二者早于最终布尔证据修订，因此只支持对应回归范围；最终81项包含统一runner和k6契约，未把早期实测当作后续源码证明。补充JMeter观测缺失及判定结构化日志后contract-logged再验81通过20.41秒，0跳过。附加compileall退出0。完整命令见IMPLEMENTATION.md，三份方案和原345份k6工件核对见p5-jmeter-validation.json。

当前尚未验JMeter load/生产ToolJob完整报告持久恢复、复杂JMESPath/数组/跨迭代变化/body与更多独立目标，完整Agent性能模式/App、逐级容量/流式阶段、安全工具、人工DeepSeek录制/清洗/遗漏/场景别名、全阶段物理HTTP、准备未知副作用/修订/其他模式、用户业务评审及生产迁移继续待实施或验收。Step按用户决定待账号处理，完整P0—P5仍未完成。

### 6.92 JMeter正式工具报告、入库失败与只保存恢复〔基线79c72775〕

调研现有runtime阶段、metrics store、ToolJob/工件/恢复、前端通用输出及本地课程，见P5_JMETER_REPORT_RESEARCH.md。未调用模型；真实Step继续按用户决定等待账号处理。

先复现后修复：before 1失败129.93秒，实际正常旅程load有82样本、状态/提取/鉴权通过，但一个请求Connect=69312ms、elapsed=69315ms，P99超标，报告正确fail。连接等待原因尚不能由该记录完全证明；依据标准库队列/轮询机制改进本测试拥有的HTTP服务接入（128队列、0.01秒轮询），不改变生产JMX或放宽SLA。后续pg-before 1失败141.90秒，三个正式场景前两者正确pass/fail，真实PG CHECK约束拒绝第三个指标INSERT，却错误返回completed/report_ready且无记录。对应原上下文回归contract-before 4失败1.10秒，原件均保留。

根因是_handle_report忽略save_run返回False，异常也不影响完成状态。现在失败保持已有analyzed阶段、已结束引擎/报告/原件，工具partial，metrics_persistence=failed/retryable；成功为saved，未配置存储明确not_configured，不冒充已入库。沿现有阶段恢复，新工具调用只保存原报告，不再smoke/load，不重置旧partial ToolJob，不把存储故障改判为SLA fail。顶层输出、metrics JSON、Prompt和结构化日志同步；数据库异常日志不输出绑定的完整参数。现有前端partial与Record<string,unknown>兼容，无前端/依赖/表/配置修改；没有执行生产迁移。

最终verified **1通过199.85秒、0跳过**：实际缓存JMeter5.5、固定计划，从生产plan_resolved开始，经过脚本/真实guard/smoke/load/分析/报告/ToolRuntime及PG/RustFS，独立API重启。三种正式load实际最大3线程，87/87/92样本；正常pass、SLA确定违约fail、PG拒写partial但原SLA仍pass。新runtime恢复第三份报告后同run_id、零新增引擎；旧partial保存，最终PG三条指标分别pass/fail/pass。四份工具结果重放零新增引擎。重启后1084次工件下载验证字节/hash/大小与凭据未泄露；保存76份关键原件，静态dashboard资产仅验证、不重复复制。六个完整容器ID已删除，六份独立清理CLI持久回执completed。

相关contract-fixed 26通过10.82秒；扩大21模块 **149通过30.80秒、0跳过**，既有Starlette/AnyIO弃用警告1条。修改后的真实k6测量回归1通过44.27秒，四报告仍pass/pass/inconclusive/fail；原件/ToolRuntime/PG/API重启回归1通过66.16秒，24份原件下载、跨项目404、篡改409/恢复200及终态零新增执行保持。compileall退出0仅作附加检查。审计p5-jmeter-report-validation.json核对旧1420份工件未改、受保护文件与三份方案，实际命令见IMPLEMENTATION.md；不累计重叠测试为总覆盖。

仍需做：性能基线按URL检索的项目隔离专项（源码无project_id，尚未实际复现/修复）；完整Agent性能模式/App、逐级容量与AI流式指标；JMeter复杂JSON/数组/body/跨迭代变化、缺依赖跳过和独立目标；安全工具真实发现/证据；人工DeepSeek录制、清洗/遗漏、场景/步骤/别名/失败定位及AI使用；全阶段物理HTTP；P2准备/未知副作用/计划修订/其他模式；普通文档模块生成、更多业务评审/格式和生产历史迁移。此次工具验证直接提供固定计划、只注入缓存镜像选择，不证明Agent自主计划、默认5.6.3镜像配置或完整App。完整P0—P5保持未完成。

### 6.93 性能基线、运行记录项目隔离与旧表升级〔基线1ae7af26〕

按用户决定继续不依赖Step账号的模块。依据[P5_PERFORMANCE_PROJECT_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P5_PERFORMANCE_PROJECT_RESEARCH.md)，实际原store无project_id：基线只按URL读取、列表无项目条件、相同run_id可以改写其他项目记录。真实API建立两个正式项目、隔离PG并使用前批实测JMeter指标复现1失败28.10秒：B读到A指标/列表，并将A判定从pass改为fail。原事实、日志/XML及修复前测试源码保存；该复现没有新压测或模型调用。

采用现有session store的nullable UUID外键和ADD COLUMN IF NOT EXISTS升级方式。指标表增加正式project_id及项目/URL/时间索引；未知历史保持NULL，不猜归属。get_baseline/list_runs显式传项目，缺项目返回无基线/空列表，无全局回退。save_run使用实际行数和条件upsert，在项目、计划、引擎、backend、意图及目标身份一致时才接受冲突更新；跨项目、NULL历史改绑和不存在正式项目不能保存。两项实际并发跨项目同run_id写入仅一项接受，不采用先查后写。没有改变已有同项目更新语义，不能称全部终态不可改写已经验证。

PerformanceTestingState保存正式会话项目；恢复项目不一致时拒绝并保留原检查点。分析/保存显式传该身份，顶层结果、metrics JSON和Prompt同步；不借父层日志上下文归属。旧状态未设置时省略新None字段，原序列化保持。独立analyst的显式baseline_metrics输入不查询该表；实际搜索未发现性能列表API消费方。

| 实际批次 | 结果与范围 |
| --- | --- |
| store-verified | 2通过65.77秒。两个正式项目同URL，基线/列表、跨项目/计划冲突、正式UUID外键及独立API重启验证；仓储资格专为归属测试设置，不重新评价前批SLA |
| store-delivery | 最终2通过51.92秒。增加实际并发两方写入，仅一方成功；旧表升级第二节点重新通过 |
| migration | 原表含false/0/null旧JSON，两次initialize后旧整行不变，仅新增NULL项目列；旧行不能自动领入新项目，fresh表另已验证 |
| mode-verified | 1通过91.52秒。真实受管HTTP目标/缓存k6/原guard/smoke/load/report/ToolRuntime/PG/RustFS/独立API重启；同URL A probe→B regression→A regression共6次引擎，B无跨项目比较、A比较自身实测P95；终态重放0新增引擎，39下载字节/哈希校验、7完整容器不存在及7completed清理回执 |
| contract-first / expanded | 先45通过11.22秒，最终22模块154通过27.30秒、0跳过；既有Starlette/AnyIO警告1条。新增5项契约源自真实失败和报告上下文 |
| measurement-regression | 实际k6四场景1通过53.77秒，pass/pass/inconclusive/fail保持，PG四行均为正式项目归属；compileall退出0仅为附加检查 |

此批固定计划直接进入plan_resolved，只注入缓存镜像选择，没有调用Agent/真实模型或完整App，也未重新执行真实JMeter load。业务归属PG、编辑配置MySQL、原件RustFS边界保持；没有新依赖或前端改动。生产数据库未迁移；部署需正式项目表和ALTER/CREATE INDEX权限。未知NULL历史仍留存但不作为任何项目基线；旧NULL指标ID与新项目保存冲突时沿原partial/analyzed恢复，需明确历史归属治理，不能自动改绑。

完整P0—P5继续未完成：性能负载/环境/多目标/版本基线可比性、完整Agent/App、容量/AI流式阶段指标；JMeter复杂JSON/body/跨迭代与缺依赖跳过/独立目标；安全工具真实发现与证据；人工DeepSeek录制、清洗/遗漏、场景/步骤/别名/失败定位及AI使用；全阶段物理HTTP；准备/未知副作用/计划修订/其他模式；普通文档生成与恢复、更多业务评审和生产历史迁移。Step默认模型按用户决定待账号处理后验收。旧6.92待办是当时状态，项目隔离最新结果以本节为准。

### 6.94 JMeter缺值依赖跳过、独立目标与轮次归属〔基线d3d03ec4〕

根据[P5_JMETER_DEPENDENCIES_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P5_JMETER_DEPENDENCIES_RESEARCH.md)逐项核对实际adapter/reader/runner、报告/Prompt、既有k6局部跳过、本地接口/性能课程与Apache官方控制器/变量/JTL文档后实施。原JSR223只记录依赖布尔事实、没有请求约束；真实反例before 1失败55.61秒，缺值三轮仍发送bridge/final各3次、返回401。12个物理请求中6个是缺前置的无效请求；原JMX/JTL/汇总、双流、对象和脱敏HTTP事实保留。

复用JMeter标准IfController包裹消费目标，所有必要关联当轮可用才发送；独立目标继续。第一采样的JSR223PreProcessor清空线程局部关联值/布尔事实并保存qa_iteration，避免被跳过的中间来源残留上一轮值。标准sample_variables仅保存布尔事实和轮次，不保存凭据，不用假采样增加指标。reader按threadName/qa_iteration关联原JTL，明确来源提取false或上游已证实跳过且实际没有请求，才展示skipped_targets；单纯缺少样本仍为证据缺口。没有HTTP响应的跳过不填200/通过；原缺口仍判fail，实际独立HTTP指标保留。JSON、Markdown/HTML及Prompt同步，有关日志保存跳过明细。老JTL无轮次列继续原核验，不重写历史。

| 实际批次 | 结果与范围 |
| --- | --- |
| fixed | 1通过53.27秒，原缺值场景只保留source/independent各3次，两级目标各跳过3轮 |
| verified | 1通过147.32秒，五种实际冒烟：缺值、单线程交替成功/缺值、双线程正常、末尾跳过、同一消费目标依赖两个来源；总实际请求6/12/24/6/9，仅正常场景通过 |
| delivery | 1通过149.28秒；以上五场景追加HTTP线程/轮次追踪，只比较凭据哈希，逐个来源/消费者严格匹配；没有跨线程或沿用上一轮凭据 |
| 两次报告执行 | 每个verified/delivery另用原execute明确三轮、单线程选项实际执行并保存PG；缺值6个实际样本、exit0/SLA通过但report fail，正常12个实际样本/report pass；各批7受管引擎/7completed清理回执及所有容器不存在 |
| response-regression | 原五场景1通过116.76秒，正常状态、错误状态、负向401、缺值、正常传值；缺值只发送来源3次 |
| contract / expanded | 首次43通过6.67秒，加入实际缺件/非法身份/重复来源/旧格式/报告回放后52通过7.17秒；最终23模块163通过35.21秒、0跳过，既有Starlette/AnyIO警告1条 |

前次扩大命令误写不存在的test_mode_skills.py，退出4、0测试，原.log/XML保留；更正为实际test_performance_mode_skills.py后才计通过。compileall退出0仅作附加检查。全部原对象下载校验SHA/字节大小并核对没有临时凭据；审计/三份方案同步及旧证据保护另见本批validation。生产配置仍MySQL、业务报告PG、原件RustFS，无新依赖、配置、表、迁移或前端改动。

本批直接固定计划、使用缓存JMeter5.5/生产runner及正式API项目/会话、隔离真实存储；报告执行采用固定三轮短程选项，不证明持续load/容量、Agent自主规划、默认5.6.3镜像配置或完整App。复杂JMESPath/数组/对象值、HTTP body生成、完整Agent/App、逐级容量/AI流式、安全、人工DeepSeek及录制清洗/遗漏/场景/别名/失败定位/AI使用、全阶段HTTP、准备/未知副作用/修订/其他模式、普通文档生成/恢复、业务评审及生产历史迁移继续待做。6.93的缺依赖/独立目标待办是当时状态，最新范围以本节为准；完整P0—P5保持未完成，Step按用户决定待账号处理后验收。

### 6.95 两引擎请求正文与实际字节〔基线1e97963e〕

根据[P5_PERFORMANCE_BODY_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P5_PERFORMANCE_BODY_RESEARCH.md)核对现有目标/文档解析/工具/Prompt/adapter、原runner/清理CLI/报告/PG/对象、本地接口及性能课程和官方HTTP正文/API后实施。JMeter原generator完全忽略body_template，k6字符串直接生成模板字符串。真实before 1失败39.78秒：JMeter六次POST都是0字节，k6三次文本把${1+1}变2并丢反斜杠；两引擎都exit0，但目标422/冒烟失败。旧计划/脚本/接收字节和哈希/汇总/JTL/双流/原件清单与修复前测试源码保持。

JMeter沿原生HTTPArgument/postBodyRaw/UTF-8发送正文，已有JSR223PreProcessor在树编译后设置数据；Base64仅运输正文，避免XML控制字符/换行规范化和函数提前展开，不执行正文。k6字符串使用JSON编码的JS字符串数据，dict保持原JSON.stringify；None保持原无正文路径。Prompt/工具描述同步：对象是JSON，字符串是UTF-8原文，Content-Type由接口文档明确提供；${...}不执行代码或自动替换。不引入模式/执行器、依赖、配置、表、迁移或前端改动。

| 实际批次 | 结果与范围 |
| --- | --- |
| fixed | 1通过51.35秒，原两个目标在两引擎各三轮冒烟/execute短程报告均正确；四受管引擎/清理回执、两条正式项目PG pass报告 |
| verified | 1通过50.26秒，七目标POST对象/文本、PUT特殊字节、PATCH嵌套JSON、空字符串、None以及POST正文/当轮bool关联header同时使用；中文/emoji/反引号/字面函数/反斜杠/CRLF/tab/NUL逐字节一致，false/0/null/嵌套/未知字段保留，方法/Content-Type一致 |
| 报告和清理 | verified两引擎各21次冒烟、21次报告请求，总84真实请求；标准请求核验/SLA/报告pass，真实PG两条正式project_id，四completed清理回执/四完整容器不存在 |
| expanded | 24模块169通过26.56秒、0跳过；1条既有Starlette/AnyIO警告；六个新增回归依据原失败输入/HTTP事实，compileall退出0仅作附加检查 |

全部RustFS原对象实际下载校验SHA/大小；关键脚本/JTL/统计/清单/双流/log保存原字节，dashboard静态vendor只保留实际下载核验清单。各批没有覆盖旧原件；三份方案同步、前批工件保护、下载和容器数量见p5-perf-body-validation.json。编辑配置MySQL、业务报告PG、原件RustFS职责保持，生产历史没有迁移。

本批固定计划直接调用生产runner/report，缓存k6 v2.2.0/JMeter5.5，三轮短程不代表持续load、容量、自主Agent、默认镜像或完整App。曾被k6意外执行的${...}现在按文字发送；没有新增插值语法。动态正文/CSV注入、form/multipart、GET/DELETE正文与更多数值边界仍需查证契约和真实验证。复杂JMESPath、null与字符串"null"、数组/对象及header类型一致性未以正文测试代替。完整Agent/App、负载/环境/版本可比性、逐级容量/AI流式、安全工具、人工DeepSeek录制/清洗/遗漏/场景/别名/失败定位/AI使用、全阶段HTTP、准备/未知副作用/修订/其他模式、普通文档生成/恢复、业务评审和生产历史迁移继续待做。6.94正文待办是当时状态，最新范围以本节为准；Step按用户决定待账号处理后验收，完整P0—P5仍未完成。

### 6.96 性能响应原生类型、复杂路径与实际下游验证〔基线06ae1f33〕

依据现有两adapter、请求核验/冒烟/报告/Prompt及原生JMeter5.5、Jackson、GJSON/JMESPath官方实现，见[P5_PERFORMANCE_TYPED_RESPONSE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P5_PERFORMANCE_TYPED_RESPONSE_RESEARCH.md)。实际复现JMeter将字符串"null"误判缺失、对象/数组错误变为header、来源状态不符合预期仍传值。复用标准JSR223、公开JMESPathCache与引擎已带Jackson树，保留线程局部原生值；JTL只存类型和布尔事实，不输出真实值。提取与header可用性分开，缺header事实不能推断准备完成；旧JTL仍按原观测读取，不改历史结果、不宣称历史类型已验证。

0、false和字符串"null"有效；JSON null、空字符串、缺值/异常JSON或来源状态错误不能提供header；对象/数组可提取，但不能隐式变成首项或JSON字符串header。JMeter复杂路径按JMESPath、k6按GJSON，Prompt明确两者语法不同。实际尾部拼接两个JSON值被JMeter误判成功，依据Jackson标准FAIL_ON_TRAILING_TOKENS在mapper首次使用前启用完整输入检查；k6原实现已检查完整字节，未修改其生产代码。

| 本轮批次 | 实际结果及范围 |
|---|---|
| before / reproduction | 1失败81.06秒是指标表惰性初始化前的夹具查询错误；修正夹具后1失败78.01秒，实际确认上述四个JMeter缺陷，k6符合预期 |
| fixed | 1通过110.36秒；原八项冒烟、四份PG报告及12条completed清理回执；不以exit0或SLA通过掩盖header不可用 |
| expanded-live / expanded-reproduction | 首轮1失败69.49秒，into_header非nullable字符串误传None，改用现有空字符串契约；下一轮1失败331.43秒，36项实际冒烟，确认JMeter尾部JSON误判和一次k6来源dial超时，原证据保留 |
| verified / dependencies-regression | 分别1失败346.96秒、1失败120.88秒；响应/报告符合预期，但Docker扫描RustFS临时文件失败后，夹具只运行一轮Worker，留下38/40和6/7回执；不将两批改为通过 |
| delivery | **1通过279.29秒、0跳过**。生产清理Worker持续处理，逐项读取实际PG回执。两引擎各18场景、每项三轮，含索引/筛选/带点键、0/false/null/对象/数组/空值/缺值/异常及尾部JSON；消费者真实值以哈希/匹配事实验证，独立目标继续。四报告exit0/SLA通过，业务结论pass/fail/pass/fail、正式项目PG入库；40条completed回执和40个完整容器ID不存在 |
| dependencies-verified | **1通过104.40秒、0跳过**；五个原依赖旅程、单线程交替/双线程/多来源、逐线程轮次值哈希、两份PG报告及7条completed回执保持 |
| body-regression | **1通过68.65秒、0跳过**；原七目标/两引擎84次真实请求正文、方法、Content-Type及依赖头保持，两份PG报告/四条清理回执 |
| final | **25模块182通过28.33秒、0跳过**，1条既有Starlette/AnyIO弃用警告；新增13项回放来自真实原件。compileall退出0仅作附加检查 |

原JSON/JTL/JS/JMX、完整已捕获双流、实际HTTP事实、PG、清理及RustFS下载字节均保存，审计p5-perf-typed-validation.json核对原件SHA/大小、前批历史及受保护文件；三份方案同步。实际命令和范围见IMPLEMENTATION.md。不修改生产清理重试、配额、所有权或Redis，不增加依赖/配置/表/迁移/前端；前端ReportsView沿原Markdown/HTML工件入口，未重跑前端构建。本批固定计划直调生产runner/report、使用缓存k6v2.2.0/JMeter5.5，不证明自主Agent、默认镜像、完整App、持续load或容量。

用户最新清单按现有提交重新分类：

| 方向 | 已有实现／已验范围 | 尚未验收或需继续补实现 |
|---|---|---|
| 模板、生成、Excel | 055bf573已提交完整项目页与生产导入产品验收，见6.87 | 普通文档全量生成的模块进度/独立恢复仍需核对与实际验收；增量专项不能替代 |
| 按用例执行、正式回执 | 选取/冻结/依赖与受控执行已有；97ac33b6已提交只读纠正/冻结传输，不再是未提交状态 | 默认AI完整闭环、可信UI正式通过、纠正真实进程中断、完整准备/未知只读查证/计划修订/其他模式和App仍未验；Step当前403待账号处理 |
| UI、Agent API、需求变更 | 已有采集/知识读取、真实API、文档/影响/追踪等专项，保留原限定范围 | 人工DeepSeek、清洗/遗漏和网络/跨文档/Shadow DOM完整关联、场景/步骤/别名/失败定位、默认AI知识与API完整使用；鉴权/续期/超时/幂等、更多业务评审/格式/部署环境产品闭环继续 |
| Docker、重复请求及交付 | 性能引擎采集/原件/响应/报告/PG已多批实测，最新类型事实见本节；重复组装和跨审批预算已修 | 完整Agent/App/前端报告闭环、负载/环境/多目标/版本基线可比性、逐级容量/AI流式指标、安全真实发现与证据；全阶段逻辑/SDK/HTTP、更多断网/进程恢复、生产历史迁移/投影及两份旧Worker原件仍需推进 |

更多数值/编码/非法表达式、动态正文/CSV/form/multipart/其他方法正文仍需查证契约和真实验证。本节完成的是响应类型与短程引擎专项；不把历史章节的当时待办当作现状，也不把专项通过当作完整系统完成。Step按用户决定等账号处理后验收，完整P0—P5保持未完成。


### 6.97 普通需求文档独立模块生成、固定来源与独立恢复〔2026-10-09〕

本轮先查普通生成、解析快照、增量来源、生成任务/重试及实际页面，再用真实文档复现。普通整批入口有默认文档数/字符截取，没有持久模块；增量任务要求需求版本/评审，不适合作为普通文档入口。真实上传/解析成功后，普通持久生成POST返回404。依据与记录见P1_DOCUMENT_GENERATION_RESEARCH.md。

新增普通文档来源契约 document-generation-scope-v1 和 /documents/{document_id}/document-case-generations。固定正式project_id、原件版本、已保存解析快照及来源位置、全文/章节范围、模板、模型/Prompt/Skill和请求；不伪造需求、影响分析或评审，不使用Git缓存上传文件。MySQL沿用项目生成策略与模板；PG沿用组、模块、重试、草稿及原子收据；RustFS保存固定范围/原响应。未向旧哈希快照添加默认字段，原增量接口保持；不新增表、迁移、依赖或调度器。

按解析章节选择重复的最浅层级，首模块包含前言，子章节和表格随所属范围。连续范围覆盖全文，每模块携带完整固定文档供跨节规则理解。没有可识别多章节时使用单模块，文本PDF保留分页和结构限制警告；部分/无文本快照不宣称全量生成。超过固定预算明确失败，不截断、不丢额外候选。章节范围覆盖不等于生成质量或覆盖率通过，结果仅为待评审草稿。

文档解析页面增加普通生成入口、原请求回查和模块进度，原固定详情显示普通来源并读取完整JSON/原响应；失败模块复用现有新子任务页面。丢失提交回复保留原编号，刷新只读找回；明确拒绝才允许修改重提。原失败及成功模块不重置，未知调用结果不自动重发。三处已有测试mock和跨层类型消费方同步。Unicode位置沿Python码点核对，避免emoji被前端UTF16长度误判。

实际验证：

| 验证 | 结果与范围 |
|---|---|
| before | 1失败25.58秒；普通持久入口404，真实原件/解析已成功，保留失败记录 |
| verified | 1通过63.98秒；三模块2成功/1保存响应后失败，提高当前策略后独立子任务复用响应成功；只有3次HTTP，原组/失败不变，四终态重投和项目隔离通过 |
| product | 1通过105.79秒；构建App/Chromium、文档页提交丢回复、刷新原请求回查、模块进度、共享详情完整JSON/原响应，零页面错误 |
| formats | 1通过133.67秒；重复实际产品流程并加DOCX三模块/表格、文本PDF单模块/分页/警告；四组共10次实际HTTP，独立恢复和终态重投无新发送；生成前改当前模板/策略不能替换原请求；14个RustFS固定对象实际下载校验SHA/大小 |
| 原增量兼容 | Worker/保存响应恢复4通过34.17秒；原公开API三格式/归档历史3通过27.17秒 |
| 分块/解析/生成器 | 25通过、1跳过、4.85秒；既有解析证据导出测试未配置专用目录而跳过，真实文件另由生产流程保存，不算通过 |
| 前端/编译 | 类型检查退出0，6文件67项通过2.64秒；实际产品构建成功。compileall退出0仅为附加检查 |

原件、双流、HTTP、PG API收据、RustFS原字节、截图及各次失败/成功记录保留，审计见p1-document-generation-validation.json。早期product汇总的terminal_replay_model_calls字段包含后来浏览器的合计6次，保留原文件不改写；其测试在启动浏览器前实际断言仍为3次，最新formats将该字段固定在终态回放结束时。三份方案同步，实际命令见IMPLEMENTATION.md。

分类更新：普通生成的独立来源、模块进度和保存响应恢复此前缺实现，本轮已实现并完成受控产品验收。默认Step、实际业务生成质量、长文容量/预算规模、更多模块故障与进程中断仍待验；现有增量通过不能替代这些验收。其他P0—P5仍需推进：完整准备/未知效果只读查证/计划修订/其他模式，自主默认Agent/UI/API，人工DeepSeek/清洗遗漏/网络及跨文档和Shadow DOM/场景别名，完整Docker Agent/App/容量流式及真实安全，更多需求业务/部署闭环和生产历史迁移/HTTP阶段及旧Worker原件。用户决定Step账号处理后再验收，完整目标保持未完成。

### 6.98 正式回执纠正的真实API中断、租约与独立恢复〔2026-10-10；基线5c6917e4〕

调研原correct/execute/finish、审批执行、RunStore检查点/完成事务、Redis独立资源与清理CLI，并核对本地Agent课程及PostgreSQL显式锁文档。依据及失败过程见P0_RECEIPT_PROCESS_RECOVERY_RESEARCH.md。97ac33b6已有生产恢复实现，本轮补实际进程验收，未修改生产逻辑、协议、表、配置字段、依赖或前端。

最后审批后的纠正在API进程内执行，因此实际kill自己启动的完整API；新独立执行Worker恢复，不能以停止已返回的Worker代替。两类固定现场：

1. HTTP已收到纠正请求，invoking检查点已提交，回应尚未发送。恢复正式error并标注execution_recovery_required，原请求保留，不能补造响应或重新发送。
2. 原回应已进入RustFS，response检查点已提交；测试独立PG数据库持有结果表SHARE锁，通过pg_locks与实际INSERT等待证明结果事务被阻塞，再kill API。恢复读取原响应并重新核对固定用例与真实工具证据，正式passed。保存响应本身不能证明通过。

沿现有恢复机制等待RunItem原约90秒和独立逻辑资源原300秒租约自然过期，不改时间戳、不清Redis、不手工释放锁。生产清理CLI留下归属原条目的skipped回执（无外部资源需删除），Redis活跃/待清理项为空且三类配额为0后重投。新attempt继承原检查点并记录recovered_from_attempt_id；原attempt保持expired，原session/turn/context/model预算不变。

恢复前修改当前模型base_url/输出上限4096→128、进程默认迭代8→18、关闭当前项目纠正策略，已冻结运行仍使用原8次上限、Agent累计4次+纠正1次及timeout=60/max_retries=0。每个场景实际模型HTTP仅6次（规划1+Agent4+纠正1），业务POST/GET/DELETE各一次，订单清理完成。两个终态重投都没有新模型请求或业务动作，正式结果与历史不变。完整固定请求、原HTTP体、检查点、锁等待、失联客户端、双进程输出、PG及RustFS原件保留。

实际结果：

- first：2失败147.33秒；RunItem已恢复但独立资源尚未过期，不能由Worker退出0当完成；另一项测试未正确进入连接上下文。保留原输出。
- recovery：2失败54.08秒；测试混淆资源dataclass与Redis原始lease_expire_at_ms，错误在kill前发生。按实际Lua/原件修正，不声称中断通过。
- verified：1通过/1失败677.98秒；未知响应现场完成；保存响应现场恢复后正确拒绝测试“检查”与回执“期望”路径不符，不能算passed。改用既有生产回归共享用例数据；真实失败响应回放1通过4.34秒，原错误仍被拒绝，固定正确来源能解析200/false/0/null。没有放宽生产校验。
- 最终delivery：**2通过687.30秒**，两个真实kill/自然过期/清理/新Worker/终态场景均符合预期，0跳过。
- 五模块回执、真实SDK重试、审批预算和执行核验：**82通过、2外部默认模型项跳过，87.62秒**。共享样本前/后的常规生产API/CLI分别1通过47.02秒、1通过46.90秒。compileall仅为附加检查，退出0。

失败测试遗留的三个专属RustFS对象按完整原项目/run/item/attempt/call前缀确认归属，校验下载后删除并逐项HTTP404确认；失败输出、本地原件均保留。最终与历史批次的14次实际对象下载逐项核对SHA/大小，原有8176份工件和三份受保护文件不变。审计p0-receipt-process-audit.py核对实际记录、固定上下文、敏感字段、原件及三份方案；当前清单以p0-receipt-process-validation.json为准。

**分类更新：已有一次只读纠正实现，本轮补验了真实进程中断与恢复；没有新增生产修复。** 仍未验默认AI可信UI回执、默认模型质量、完整准备状态、未知业务副作用的通用只读查证、执行中计划修订、其他模式及完整App。普通生成长文/默认质量、人工DeepSeek与完整清洗遗漏/网络关联/场景别名、完整Agent API鉴权续期、Docker Agent/App/容量流式及实际安全、更多需求业务/部署闭环、全阶段HTTP与生产历史迁移/旧Worker原件继续未完成。Step按用户决定待账号处理后验收，完整P0—P5目标保持active。
### 6.99 API 写入未知结果禁止自动重试〔2026-10-10〕

依据 [P2_UNKNOWN_WRITE_RECONCILIATION_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_UNKNOWN_WRITE_RECONCILIATION_RESEARCH.md) 查阅 API 执行器、直接/子 Agent 协调器、正式用例逻辑操作协议、效果证据协议及接口测试课程。旧 API 执行器把写请求超时当普通失败；两个协调器允许超时/连接错误或 5xx 自动重试。真实本地 HTTP 服务在客户端超时前已收到 POST，旧实现实际发送两次，证明会重复业务副作用。

新增 ApiTestTask.request_outcome：写方法的传输异常和 5xx 响应统一记录 unknown，仍保存实际 HTTP 观察（若有）；普通成功为 completed，读请求传输失败为 known_failed。直接协调器和子 Agent 协调器遇到 unknown 均停止自动重试。报告任务摘要/失败证据增加 request_outcome 与 reconciliation_required，评估归类为 unknown_side_effect/critical；未知不能回写成功，也不能凭空生成查询接口。后续只读查证必须来自当前项目固定用例/接口契约明确声明的读操作或人工补充可靠依据。

| 实际验证 | 结果 |
| --- | --- |
| `p2-unknown-write-verified` | 真实 HTTP 写入超时只发送 1 次，服务端已收到请求；写入 500 只发送 1 次；两者均为 unknown、要求只读查证。 |
| 受影响回归 | 23 passed、3 skipped（默认外部模型项），10.16 秒；覆盖执行器、直接协调器、子 Agent 协调器、报告及评估。 |
| 编译 | 本批生产文件与测试 compileall 退出 0；仅作附加检查。 |

本批没有新增依赖、配置、数据库表/迁移或前端契约；配置仍使用 MySQL，业务状态使用 PostgreSQL，原件/证据使用 RustFS。Step 默认模型仍按用户决定暂缓。该修复只解决 API 写入未知结果的重复发送根因和可见性；通用只读查证任务的业务契约、默认 Agent 自主选择、完整 App、运行中计划修订、其他模式及其余 P0—P5 仍未完成。

### 7.00 UI录制真实浏览器与DeepSeek物理探针〔2026-10-10〕

依据 [P0_DEEPSEEK_RECORDING_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_DEEPSEEK_RECORDING_RESEARCH.md)，先读取现有 CdpAttachDriver、PlaywrightBindingDriverBase、RecorderEvent、RecorderSessionService 及真实录制产品测试，再执行真实浏览器；没有把模拟事件或默认 Step 模型当作证据。

新增门控测试 `Agent_Server/tests/test_recorder_deepseek_live.py`。它使用生产 CDP 驱动访问公开 DeepSeek 首页，点击“和 DeepSeek 对话”并输入非敏感“录制探针”，不登录、不提交消息；原始事件、结束回执和截图保存到 `p0-deepseek-recording-verified`。实际 `1 passed in 4.26s`，采到 click/page_scan/navigate/fill，`recording-scopes-v2` 回执 pending、frames_unconfirmed、transport_dropped、channel_dropped 均为 0。既有 `test_recorder_live_cdp.py` 真实本地 Chromium 探针 `1 passed`。

另外复跑 `test_recording_embedded_product.py` 的 `settled`、`response-dom` 两个场景，真实 Electron + PostgreSQL/RustFS/图谱固化 `2 passed, 14 deselected in 23.97s`。因此现有录制驱动和真实产品存储各有直接证据，但本轮没有把外部 DeepSeek 事件写入正式业务库。

仍未完成：人工登录态与真实业务步骤、完整清洗去重/干扰和遗漏检查、跨页面/跨域/Shadow DOM 关联、场景与步骤整理、元素别名、失败定位、默认 AI 使用及正式评审。默认 Step 模型仍因实名认证返回 403，按用户决定暂缓；完整 P0—P5 目标保持 active。

### 7.01 API 鉴权输入与真实受保护接口〔2026-10-10〕

依据 [P2_API_AUTH_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_API_AUTH_RESEARCH.md)、现有 `CredentialManager`/`ApiTaskExecutor` 以及接口测试课程 6.2、7.2 和登录鉴权章节，先用真实本地受保护 HTTP 服务复现，再修改执行链路。课程要求登录返回的 token 实际用于后续受保护接口；Cookie、Bearer、Basic、自定义 Header 不能混为一种输入。

复现发现：用户 `auth_type=cookie` 的 `sid=abc; tenant=blue` 被原实现错误变成 `session=sid=abc; tenant=blue`，受保护接口返回 401；已声明的 `AUTH_CUSTOM` 没有读取用户 `headers`，`X-API-Key` 请求也返回 401。动态登录 Bearer 原路径先已通过，未改其语义。

修改 `Agent_Server/src/modes/api_testing_mode/credential_manager.py`：Cookie 按名称和值解析，接受结构化字典；`AUTH_CUSTOM` 转发显式 headers；自定义 Header 名称和值拒绝 CR/LF 和非法名称，避免外部输入形成 Header 注入；API key 可使用显式 header name，默认行为保持。凭据不写日志，不新增依赖、配置键、表或迁移。

实际验证见 `p2-api-auth-validation.json`：修复前真实 HTTP `2 failed, 1 passed`；修复后 Cookie/custom/Bearer 三项 `3 passed`；受影响 API 回归 `48 passed, 5 skipped`；全部 `test_api_*.py` `123 passed, 36 skipped`，保留一条既有 Starlette/AnyIO 警告。没有调用默认 Step 模型。

`expires_at` 目前只是已有状态字段，刷新端点、refresh token 字段及自动续期没有可靠项目契约，本轮不猜测实现；鉴权续期仍属于未完成项。默认 Agent 完整流程、完整 App、其他模式以及其余 P0—P5 继续未完成。


### 7.02 录制草稿保留完整事件与停止快照〔2026-10-10〕

依据 `P0_RECORDING_DRAFT_RESEARCH.md`、现有 `RecorderEvent`、`RecordingGraphStore`、`RecordingCaseDraftService`、`TestCaseStep.data` 和真实录制原件，先复现旧转换契约。旧草稿只保留定位摘要，丢失完整输入、文档/frame 身份和 `page_response` 观察；真实长输入、跨文档事件和截断响应分别复现失败。

新增统一脱敏派生载荷。新规则版本 `p2-2.v2` 的动作步骤保留兼容摘要并写入完整脱敏 `data.event`；非动作事件写入 `test_data.recording_observations`，但不作为回放动作或自动通过依据。真实重复操作按不同 `seq` 原样保留，网络/批次重复仍由既有 `(recording_id, seq)` 幂等处理。来源 metadata 保存 `finalize_metrics`，日志只记录计数，不记录输入值。历史草稿、PG 原事件和旧哈希不改写。

同时修复停止固化的快照根因：PG 事件 flush 后重新读取权威 `RecordingSession`，再做图谱完整性校验和 completed 更新，避免使用旧内存 `step_count` 把完整事件误报为 degraded；会话消失则失败。没有新增表、配置、依赖或前端契约，MySQL/PG/RustFS/Memgraph 职责保持。

修复前真实原件转换 3 项失败，证据见 `p0-recording-draft-before`；修复后真实 Chromium + 隔离 MySQL/PG/RustFS/Memgraph 的长输入、密码脱敏、两次真实点击、观察保留、项目隔离和重启读回 **61 通过、6 跳过**，最终 `step_count=8`、PG 事件数 8、`integrity.degraded=false`。录制相关扩大回归 **203 通过、69 跳过**，有 1 条既有 Starlette/AnyIO 弃用警告。详细依据、范围和限制见 `P0_RECORDING_DRAFT_RESEARCH.md`。人工 DeepSeek 业务流程、完整跨页面/跨域/Shadow DOM 清洗、场景/别名/失败定位、默认 AI 使用和其余 P0—P5 仍未完成。

### 7.03 录制草稿入口、只读恢复与会话过滤〔2026-10-10〕

依据 `P0_RECORDING_CASE_DRAFT_ROUTE_RESEARCH.md`，原内部转换服务已有，但缺 API/前端入口。新增 POST/GET `/recordings/{recording_id}/case-draft`，结果进入现有用例草稿、评审和启用链路；不读取生成模板。项目只来自原录制，查询参数只核对归属。

真实再次提交复现 409 后，沿现有项目/编号唯一约束补原转换读取。读取原 V1 并核验完整录制身份、来源和内容哈希；并发只恢复确认的赢家，不重生成、不换编号、不重写旧版本。前端先 GET 再决定是否 POST，GET 错误不写入，保存状态按录制隔离，切换会话不显示旧请求迟到结果；复用既有用例对话框显示原版本。另用 25 条其他会话 PG 记录复现全局 top20 漏录制，将 project_id/session_id 过滤移到数据库分页前。

最终真实 Chromium + 隔离 MySQL/PG/RustFS/Memgraph + Uvicorn TCP HTTP **1 passed in 6.41s**：3 并发请求中故意丢弃一个已提交响应，GET 找回同一结果，PG 仅 1 case/1 version；重放、重建服务、归档读原 V1、长输入/脱敏/重复点击、评审启用/完整规划证据及项目隔离通过。最终录制等回归 **221 passed、72 skipped**（门控外部场景）、前端 **18 passed**，vue-tsc/Vite build 和 compileall 退出 0。保留真实缺恢复的 409，以及测试夹具下标/middleware 生命周期失败证据；详情与工件见 `p0-recording-case-draft-validation.json`。

本批已实现入口/原件恢复/会话过滤；物理 HTTP 是生产服务与测试专属 FastAPI 装配，不是完整 Vue App 或独立 main 进程验收。完整 App、人工 DeepSeek 业务、默认 AI 知识使用尚待验收；回放报告持久化、场景/步骤/别名、清洗遗漏和失败定位仍需推进。无新增表、迁移、配置或依赖。默认 Step 按用户决定待账号处理；完整 P0—P5 保持未完成。

### 7.04 录制草稿真实 Vue 组件与物理 HTTP 联调〔2026-10-10〕

继续按现有 `test_case_import_browser.py`、`TestCaseContentDialog` 和 Pinia/路由契约验收。新增 `test_recording_case_draft_browser.py`：真实 Vite/Chromium 挂载生产时间线、Pinia、用例对话框，调用生产 recordings/case_management 路由和真实隔离 MySQL/PG/RustFS；读取 7.03 已采集的浏览器事件到测试所属 PG，不把夹具重新入库称为新人工录制或完整 App。

实际点击生成，故意返回“提交成功后的503”；页面显示错误，再点击通过 GET 找回原用例，不增加第二个转换 POST。打开真实对话框，JSON 原文与原固定版本的兼容投影逐字段一致，长 Unicode 输入完整；页面提交评审/启用经真实 HTTP200 落库，刷新后自动读回同一 case/version 且不 POST。PG 仍 1 case/1 version，页面 JS 错误 0。最终 **1 passed in 6.00s**，compileall 退出0；没有改生产代码、模式、配置、表或依赖。

前两批是验收脚本错误：直接比较 `content_json=None` 忽略既有旧结构投影；将请求中的 disabled 误判为启用终态。依现有对话框契约修正，最终等待实际 activate 响应和“当前启用”页面状态，旧失败日志/原件保留。范围、截图、实际请求和PG原件见 `P0_RECORDING_CASE_BROWSER_RESEARCH.md`、`p0-recording-case-browser-validation.json`。

录制草稿组件联调已有直接证据；生产完整 Vue App/main 独立进程、人工 DeepSeek 业务录制、默认AI知识使用，以及回放报告/场景步骤别名/清洗遗漏/失败定位和其余 P0—P5 仍未完成。Step按用户决定待账号处理。

### 7.05 现有模板/导入完整产品复验与模板修改后版本不变〔2026-10-10〕

当前基线 `b01cea74`。核对6.87确认手工模板及三种导入早已有完整构建 App 验收；不再把它们列为缺实现。先使用当前生产 main/完整源码 App 复验三布局 **3 passed in 74.24s**；随后移除重复测试入口，保留探针源码和证据，将“V1已启用且有V2草稿后再次修改模板”的检查并入原 `test_case_import_full_product.py`。

原构建版 App/独立生产 main 验收本轮实际 **1 passed in 52.25s，0 skipped**：模板 PUT 后 MySQL 修订增加，PG case、V1/V2完整记录和启用指针不变；主进程重启后模板/用例读回相同。原三布局、错误行/冲突、提交重放、两项目隔离、新草稿不覆盖旧版本、完整JSON和原件下载均复验，PG最终3用例4版本。主动停API时轮询500单列保留，恢复后非预期API错误和pageerror均0。本轮 build/preview/main双流、网络、PG、截图和原件见 `p1-template-fixed-version-app-verified`；compileall退出0，仅附加检查。

分类：模板/导入已有实现且已验；普通文档模块生成/恢复已有6.97受控产品验收，默认模型业务质量、更多容量/故障、正式部署/历史迁移仍待验。录制内部回放执行器未接 Agent 工具/资源管理，不能直接加 HTTP 入口替代既有授权和执行体系；场景/步骤/别名/清洗遗漏/报告仍需查证补齐。完整默认Agent及其余P0—P5保持未完成，Step按用户决定待账号处理。依据与本轮复验/新增检查的区别见 `P1_TEMPLATE_FIXED_VERSION_APP_RESEARCH.md` 与 `p1-template-fixed-version-app-validation.json`。

### 7.06 Docker安全响应头实测与完整输出证据〔2026-10-10〕

本轮基线 `106a1114`。既有响应头工具/解析/授权/资源体系继续复用，不新增扫描模式。真实隔离 MySQL/PG/RustFS/Redis、缓存 Kali、生产 ToolRuntime、独立 main 启动及重启验证：缺响应头4项发现、齐全0、连接失败exit1/failed/0，已有有效状态码校验生效，未增加重复校验。复现长输出在转录中被截断、重启下载缺原始前半段（1失败28.45秒），已补完整stdout/stderr工件、字节数和SHA256，并沿原统一上传/PG metadata/会话下载链路保存，原模型预览限长保留。

最终真实 **1 passed in 27.78s，0 skipped**：9工件下载，完整流和PG hash/长度一致，失败stderr保存，终态读回不增加GET，另一会话下载404，3个完整ID容器已删除。9模块回归 **111 passed in 10.62s**，compileall退出0。4个旧纯路径/后端测试因简化_Settings无database失败，按既有内存任务库注入修正夹具，原断言保留；初次import与不存在测试名的命令错误全部保存，不作功能验证。详情及原件见 `P5_SECURITY_EVIDENCE_RESEARCH.md` 和 `p5-security-evidence-validation.json`。

本批是现有响应头baseline与证据链实际验收，不是完整Agent或Vue报告，也不代表其他扫描器、超时/部分输出/存储故障、容量复用已验。API单进程不负责清理队列，本批只证明实际容器删除。安全完整Agent/报告、性能Agent/容量/AI流式、录制知识/场景/清洗遗漏、生产历史迁移及其余P0—P5继续未完成；Step按用户决定待账号处理。无新增配置、表、依赖或前端修改。

### 7.07 安全报告最终字段与现有报告中心完整产品验收〔2026-10-10〕

本轮基线 `2f404e68`。真实响应头工具结果继续进入现有报告阶段，复现下载JSON的settlement/verification/evaluation为空但工具输出已有最终字段，且安全会话被报告中心code_review过滤排除（1失败28.68秒）。三条报告路径统一在最终字段写齐后生成工件，保留历史；SessionStore协议/内存/PG同步增加可选多模式过滤，在数据库分页前筛选。报告中心读取代码审查、安全、性能三模式，绑定最新报告ToolJob、识别性能工件名称、安全结算/核验/活动，增加原件下载和三语言文案。缺报告、缺最终结论或inconclusive不能良好；代码审查子会话流程保留。

最终真实 **1 passed in 55.52s，0 skipped**：真实Kali/HTTP/PG/RustFS、构建版完整App/Chromium和独立main；3任务2完成1失败4发现，页面需关注/部分完成，浏览器JSON下载最终字段一致。12个default不挤占三模式分页，未生成性能报告显示进行中；再次重启API并点击刷新结果相同、无新增目标GET。计划停API期间1个轮询500保存，恢复后非预期API错误及pageerror均0。回归150通过33门控跳过，报告投影5通过，前端14通过，build/vue-tsc/compileall实际退出0。测试Node类型与旧metrics_persistence假定错误、50.04/55.47秒中间通过批次全部保留，详见 `P5_SECURITY_REPORT_PRODUCT_RESEARCH.md`、`p5-security-report-validation.json`。

范围从真实工具结果的既有报告恢复边界开始，未调用AI选取/规划或其他扫描器；性能仅入口/合同和原真实工件兼容读取，真实性能完整App仍待验。完整安全Agent、其他扫描器/超时/存储故障、容量/AI流式、录制知识/场景/清洗遗漏、需求业务质量/生产历史迁移等继续未完成。Step按用户决定待实名认证。无新增模式、配置、表、依赖或邮件投递。

### 7.08 性能报告按最新执行显示：真实工具与完整产品验收〔2026-10-10〕

本轮基线 `653e8e6d`。依据现有冒烟失败返回和报告选择逻辑，实际复现同会话先pass、再failed仍绑定旧成功任务，页面显示良好/pass（1失败48.12秒）。新增工具库协议/内存/PG/服务的批量最新任务读取；报告按当前任务身份选择工件，失败或无报告不回退历史成功。按创建身份排序，迟到的历史更新不改变当前执行；非报告子工具不抢占主体。前端使用当前job_status与verdict，修复idle遮盖完成，历史卡片补本次/历史任务、ID及时间，三个语言同步。

最终 **1 passed in 69.27s，0 skipped**：真实k6/受控目标/ToolRuntime、隔离MySQL/PG/RustFS/Redis、独立main和本轮构建完整App/Chromium。9个观察阶段覆盖pass、3类冒烟失败、真实排队/取消、异常退出与原件部分保存的inconclusive及重启；浏览器JSON指标/结论与实际输出一致，历史原件/任务归属可查。等待/取消与保存报告不增引擎调用，终态读回不重跑；跨项目404、同尺寸篡改409/恢复200，完整ID容器删除均实际核验。原件保存故障明确为第二次上传注入异常，不声称全RustFS断网；没有AI调用。

十模块回归 **84 passed、6 skipped in 39.90s**，报告投影7通过，前端报告两文件11通过，实际安全报告产品回归1通过56.48秒；最终build/vue-tsc与compileall退出0。最初扩大到迁移模块因当前虚拟环境缺alembic.config而收集失败，保留原日志，不报迁移通过；本轮没有DDL/配置/依赖变更。70.98/70.04秒中间产品批次均保留，最终标签及滚动复位后截图见 `p5-perf-report-final2`。详情见 `P5_PERFORMANCE_REPORT_PRODUCT_RESEARCH.md`、`p5-perf-report-validation.json`。

此批已有实现且已验的是性能工具结果/报告中心产品链路，从既有guard和analyzed检查点开始；完整默认Agent/规划、容量租约复用及AI流式仍待验。安全其他工具/故障、录制知识/场景/清洗遗漏/业务人工录制、API续期、需求业务质量及生产历史迁移等仍需继续。Step按用户决定等待账号处理；完整P0—P5不标完成。旧章节按当时范围解读，7.07的“绑定最新报告”现已进一步纠正为最新执行任务。


### 7.09 项目知识查询录制节点：真实采集、完整App与重启〔2026-10-10〕

本轮基线 `adf51172`。先读原录制固化/子图、知识身份/查询、项目概览、前端和参考报告5.3，发现正式录制写project_id而知识读取要求旧graph_scope_key；知识只读Page/Element/Entity却返回全部关系。实际新Chromium/CDP/生产API录制长Unicode输入和两次点击，PG落8条事件：第一次1失败17.28秒；有/无旧scope两项目复现2失败28.25秒，分别409和缺1Recording/8Action导致悬空关系。全部旧失败原件保留。

身份按正式ID读取；无旧映射不回退默认scope或项目名，明确登记旧映射的历史兼容保留。项目列表/摘要/全图增加Recording、Action与项目名称，前后端Schema/DTO、筛选/详情及三语言同步。事件元数据沿原完整协议和密码脱敏；损坏载荷记录节点身份warning且不可用，不改写历史。计数“动作与观察”含技术观察，不等于业务步骤数。配置仍MySQL，业务录制/事件PG，现有Memgraph仅投影；无新增表、依赖、模式或直接回放入口。

最终 `restored` **2 passed in 85.99s，0 skipped**：实际生产独立main/隔离MySQL/PG/Redis、现有Memgraph、真实受控HTML/CDP和本轮构建完整Vue App。两项目各8事件、1Page/2Element/1Recording/8Action/24关系；遗漏及悬空均0，详情长值完整且所属录制可查，空另一项目404。API重启知识JSON一致，页面重载再查同录制，PG原事件逐行不变；pageerror和非预期API错误均0。主动停API的轮询500单列保留；仅清理新项目图节点，两批12/0→0/0。

九模块回归55通过2门控跳过4.16秒，新增五个由真实样本派生的身份/完整事件/历史密码/损坏载荷合同；三个知识消费者模块9通过7.28秒。前端既有项目切换4通过；最终两次实际build含vue-tsc退出0，compileall退出0仅附加检查。中间verified 2通过86.42秒、final 2通过81.30秒保留，最终补强网络窗口、App重载、PG不变和按所有权清理。详情、实际命令和原件见 `P5_RECORDING_KNOWLEDGE_PRODUCT_RESEARCH.md`、`p5-recording-knowledge-validation.json`。

分类：知识查询/筛选/详情已有实现且本轮已验；默认AI业务知识使用、人工DeepSeek、复杂关联及清洗遗漏仍待验。业务场景管理/步骤整理/业务元素别名/失败定位报告仍需补齐，内部alias不是可编辑业务别名。无旧scope的项目概览仍可能显示零，项目列表前200项/逐项目查询未容量验收，通用生成摘要未读取完整录制事件。这些独立缺口保持未完成。API续期、性能容量/AI流式、安全其他工具故障、需求业务质量/生产历史迁移及默认Agent仍需继续；Step按用户决定待账号处理，完整P0—P5不标完成。


### 7.10 正式项目概览与生成上下文接入录制知识〔2026-10-10〕

基于7.09真实录制证据继续查证 `ProjectOverviewService`、`ProjectTestCaseContextProvider` 和知识服务调用契约：概览原先只有 `graph_scope_key` 才读取图谱，生成上下文也只在旧scope存在时读取。正式项目虽能从知识页按project_id读取，概览仍显示零，录制知识也无法进入生成上下文。这是跨层身份条件遗漏，不是新增图谱。

修复沿知识服务已有身份规则：绑定项目服务时传正式 `project_id`，无项目服务时才传旧scope；没有身份则明确不可用并保留错误。`ProjectGraphOverview`和前端类型增加 recording/action 计数，项目页节点总数包含录制及动作。生成上下文有界集合加入 Recording/Action，仅传身份、标签、动作类型等安全摘要，不传事件原始值；原文档、会话、旧三类图节点及历史契约保持。无新增表、依赖、配置键或测试模式。

合同回归 `p5-recording-formal-context-contract3` **19通过、3.83秒**。真实录制产品批次 `p5-recording-overview-live3` **2通过、102.74秒** 已验证两个无/有旧scope项目的概览API计数，并在完整App项目页看到图谱节点12；重启、项目隔离、PG事件不变和清理证据均通过。此前live2的2项断言失败（产品实际显示12，测试误写11）原件保留，不计通过。仍未调用默认AI，不能声称生成或执行用例已完成。

分类：正式项目概览和生成上下文的身份接入已补实现；真实知识/概览计数有产品证据。默认AI实际引用录制知识、场景/步骤/业务别名编辑、失败定位和回放报告仍待实现或验收。Step按用户决定待账号处理。


7.10交付补验：`p5-recording-overview-final` **2通过94.80秒，0跳过**，实际知识服务在两项目上下文中读到1Recording/8Action，长Unicode原值未进入安全摘要；有界摘要只供发现来源，不能代替完整录制执行依据。最终7模块回归 **27通过4.11秒**，补验空图不制造知识来源；前端项目切换4通过、build/类型检查和最终compileall退出0。无新增配置/表/依赖；默认AI未调用，场景/步骤/业务别名编辑本轮只调研，尚未实现。原live2测试计数错误和合同夹具错误批次均保留。原历史证据及受保护无关文件按哈希核对；详情见 `p5-recording-overview-validation.json`。

### 7.11 多模型适配：统一终态与流式usage，保留请求映射待办〔2026-10-10〕

基线 `3a11b05b`。查证 DTO、四种官方SDK客户端、LangChain/旧桥接、MySQL能力默认和既有usage统计，以及官方终态文档和本地Agent协议课程。项目已有适配层；改进应沿现有 ModelInvocationRequest/Result、UnifiedMessage、clients/adapters完成。不能把Step专有字段当作全部模型契约。调研与实际字段矩阵见 `P0_MODEL_ADAPTATION_RESEARCH.md`。

实际本地HTTP + 已安装SDK + 运行服务覆盖四传输/桥接、同步/流式、完整/截断。修正HTTP夹具后 before2 **4失败16通过19.69秒**：Responses的incomplete/max_output_tokens和Gemini的MAX_TOKENS被规划接受。统一解释已知终态并保留原供应商字段，规划/生成/影响/判定/纠正沿同一解释核验；失败不加预算或重发，旧未知终态仍兼容但不能证明完整。追加completion_status及结构化终态日志，无新表/配置键/依赖。

扩大usage验证后 final **2失败75通过20.42秒**，实际Anthropic流式最终SDK增量将缺失input_tokens展开None，覆盖首块7。usage-before **2失败15.64秒**原件独立保存；根因修复只忽略None，显式0仍更新。最终 delivery **78通过25.65秒，0跳过**：20协议场景、真实SDK显式0、30保存样本/语义合同及现有客户端/桥接回归。受控7/3计数不等于真实模型计费。

十模块回归 **158通过48门控跳过44.45秒**；存储/恢复 **8通过65.31秒**，两项PG响应提交确认丢失恢复保留原截止时间、保存响应及usage，终态无新增调用。首次生成/影响导出环境名错误只保留日志/XML；正确导出补验 **6通过53.84秒**，生成来源/配置/请求损坏及保存截断检查零新增发送，五项影响保留失败原件。HTTP500在一次逻辑调用内SDK实际3次HTTP，不抹去重试。生成守卫未提交草稿，协议矩阵不是真实外部供应商服务，不能当完整产品验收。最终compileall退出0；本批无前端改动，未重跑前端build。

默认Step本次真实验收 current **1失败1未选中201.61秒**：2次HTTP200，403已消失；旧单阶段策略第二轮正文0、推理持续至原180秒截止，task失败，Run与业务HTTP均0。没有启用最新分阶段选项，不能据此判定最新策略结果。此前未提交的自动JSON Mode改动已撤回，局部34通过7跳过只保留为试验证据。

| 未完成项 | 后续做法与验收边界 |
|---|---|
| 请求适配profile与能力治理 | 在现有MySQL配置增加版本化、显式已验证映射；内部格式/推理/工具/媒体语义由现有四clients转换。必需而无支持映射明确错误，可选回原提示词并记录。不依模型名字猜字段，不把全true默认当证明；PG冻结版本/哈希/预算，RustFS原件，API/前端/消费同步后实际HTTP及真实供应商矩阵验收。尚未实施 |
| 默认AI用例执行与完整App | 核验最新冻结规划策略；项目选取、必要准备、排序冻结、Agent工具、断言、回执、结果和恢复全链路。记录旧失败，不靠放大预算掩盖；录制知识生成/执行的默认AI使用仍待验 |
| UI录制业务闭环 | 人工DeepSeek、复杂关联/跨文档/Shadow DOM及清洗/去重/遗漏检测；场景/步骤/业务别名编辑、失败定位与报告。知识查询及正式项目摘要已有专项，不能替代上述工作 |
| Agent API测试 | 鉴权续期、完整默认Agent、幂等/超时/并发及真实依赖传值；已有专项不当作全部场景完成 |
| 性能、安全 | 完整Agent/App、性能负载/环境/版本可比性、逐级容量与AI流式阶段指标、安全其他工具及真实发现/故障证据；已有Docker实测、项目隔离与报告专项按各章范围保留 |
| 其余执行与需求闭环 | 准备状态、未知副作用只读核验、计划修订及其他模式；需求业务评审质量、部署/生产历史迁移、全阶段物理HTTP及旧Worker原件追踪 |

原历史证据和受保护文件哈希核对、三份方案同步见 `p0-model-completion-validation.json`。本批已修复的仅是统一已知终态和Anthropic增量usage两个确认缺陷；多模型请求语义治理及完整P0—P5保持未完成。

### 7.12 多模型流式结束、统一失败判定与工具路由〔2026-10-10〕

基线443f5af7。先读原目标325行、四客户端/两桥接/业务保存响应及图路由/完成、官方协议和现有MySQL/前端配置。请求映射查证见P0_MODEL_STREAM_TERMINAL_RESEARCH.md：LangChain会强制改写token字段，各供应商JSON/推理字段与预算约束不同，MySQL profile仍待实施；不能按模型名字猜或统一透传。本批先处理调研发现的实际协议/成功判定根因，不引入第二套模型框架。

实际SDK/受控HTTP删去结束事件但保留合法JSON，before **6失败11.96秒**；四传输、LangChain及Anthropic缺message_stop均被当作no_match。新增流式观察stream_terminal_received，保留原字段及旧unknown兼容；初版6通过13.50秒、93回归通过24.87秒。继续复现已知length仍被通用Agent当完成，补齐夹具后limit-before2 **5失败9.17秒**，实际assistant_final及graph.execution_completed原件保存。模型边界统一mode=incomplete_response，既有失败生命周期处理已知非完整；业务继续按具体原终态区分truncated等，不混淆HTTP错误。

真实SDK的两个不完整工具响应已标失败却被原route按非空tool_calls派发，tool-before **2失败8.31秒**。现有路由改为失败进入finalizer，原工具调用保留；两个正常工具响应仍走tool_executor，未通过清空调用掩盖风险。日志携带统一completion_status与流式观察，不完整输出不完成回合，也不派发其中工具。未知供应商语义未臆造为完整。

扩大delivery **204通过48门控跳过62.61秒**：13模块，15实际HTTP/SDK及Agent节点场景、20同步/流式完整/截断矩阵、显式0和业务合同。真实存储recovery-final **3通过7未选中14.76秒**：MySQL模型配置、PG任务/固定完整八步用例/检查点及RustFS新Markdown原件；先查询文档再保存原截断/缺终态正文，提交确认丢失后沿原deadline恢复，终态和同key重投无第三次发送。当前MySQL策略改成1，旧seed仍5。该范围是确认丢失/租约恢复，不等同真实kill进程或默认模型完整执行。

受影响生成/影响storage **6通过14未选中49.19秒**，实际存储/SDK，截断、空、工具、500、超大失败原件和分类保持；500 SDK3次尝试没有删去。生成守卫未提交草稿。最初limit-before夹具缺正常responder字段的5失败单列保留，不能当产品根因证据。最终日志字段专项与compileall另记实际结果。

分类：流式结束观察、统一已知失败模式及工具路由本批补实现并实际验证；四种真实外部模型、默认AI/完整App、原人工DeepSeek及录制清洗遗漏/业务管理、API续期、性能容量/AI流式、安全其他工具、准备/未知副作用/修订及生产迁移仍待验收或补实现。请求profile配置/API/前端、JSON/推理/媒体治理保持未实施；本批无配置/DDL/依赖/前端改动，未重跑前端build。旧历史与受保护文件保留，三份方案同步见p0-model-stream-terminal-validation.json；完整目标保持未完成。

7.12交付补验：最终日志字段专项trace-final **24通过18.04秒，0跳过**，真实SDK/HTTP及图节点失败事件携带统一completion_status；compileall退出0仅补语法检查。旧无观察字段仍按unknown兼容，不能证明其响应完整；Deep Agents pilot等独立模型路径未以本批替代验收。

### 7.13 配置驱动的模型请求适配与实际设置页面〔2026-10-10〕

基线06a66a17。先核对统一DTO、四native客户端、LangChain发送/解析分支、MySQL初始化和upsert/edit、API列表实际list契约、设置组件与任务固定来源；官方格式/推理/SDK和本地课程依据见P0_MODEL_REQUEST_PROFILE_RESEARCH.md。沿已有适配层补充严格profile，不新建调用平台，不按模型名称猜协议。

MySQL新增可空request_profile JSON；配置声明version/revision、允许JSON格式、推理effort/预算及默认、Chat token字段。API字段遗漏保留、null清除，未知字段及不兼容传输/默认预算拒绝；旧行不回填，能力全true不作为支持证明。前端设置编辑/重载/保存及中英繁体同步。原任务参数固定完整profile/哈希/mapping版本和实际适配器/SDK；旧无配置/新选项字段省略，保留旧哈希。PG业务seed、版本及结果不改写，RustFS原件保持。

生成请求携带原Schema，规划/影响携带可选JSON object；四传输各自映射，调用前必需不支持返回unsupported_request，可选保留原prompt并记录降级，不在400后换字段重发。Anthropic首期仅manual预算及Schema，Google仅generateContent预算；更多推理协议/媒体仍待补。显式max_tokens选既有native Chat，导出LangChain harness拒绝此配置；支持的默认推理按声明发送。独立invoke_structured旧路径未作为此轮验收。

实际工具组合补验确认LangChain遇response_format会启用SDK自动解析，非strict现有工具被发送前拒绝。查证官方SDK extra_body合并和LangChain源分支后，适配器仅将映射的格式字段由此发送，HTTP出线仍标准顶层response_format；不篡改工具Schema/权限、不开任意透传。同步/流式组合补验通过，未宣称实际执行业务工具。

| 实际批次 | 结果与范围 |
|---|---|
| 协议首轮/http及verified | 30通过5失败27.85秒；4项Google snake_case断言错误、1项空输入夹具。查证ProtoJSON/已安装SDK后修正测试，35通过24.52秒，不造SDK补丁 |
| 扩大regression及delivery | 229通过2失败41门控跳过69.90秒；选择函数漏原可选开关默认，恢复getattr后234通过41跳过62.91秒，18模块。跳过项不计完成 |
| wire-final及tools-before | 38通过1失败26.08秒；工具组合专门保存1通过1失败8.12秒，真实LangChain strict工具限制0HTTP |
| 最终wire-delivery | 63通过27.58秒，0跳过；新41项协议/旧快照兼容/导出默认/同步流式工具组合及22适配器/失败生命周期回归。400仅1HTTP，必需不支持0HTTP |
| 实际built App/product-verified | 1通过88.63秒，原首次1失败78.63秒为列表夹具封装错误。真实旧MySQL列迁移、重复初始化、页面编辑错误/保存/刷新、API重启/保留/清除，页面错误0；MySQL重载配置实际SDK发送已核对 |
| 实际storage-final | 1通过10.83秒；新Markdown/RustFS→受控SDK→完整八步404 JSON草稿入PG并重读；PG固定profile1、MySQL改2后原生成/规划拒绝且0新增HTTP，原seed不变。首次夹具重复generation_metadata失败保留，修正批1通过17.84秒 |
| 前端回归/构建 | settings API/设置生命周期3通过3.59秒；真实产品build包括vue-tsc/Vite通过。最终compileall退出0另存，仅语法补查 |

分类：profile保存/API/设置页面、统一JSON/effort/manual预算/token映射及固定配置衔接本批已实现并按上表范围验证；外部真实供应商兼容、默认Agent业务闭环、媒体/更多推理协议/能力依据治理、独立结构化harness、生产历史与并发DDL仍未验或需补。初始化沿既有迁移设施需要MySQL ALTER权限；未改实际全局模型配置、依赖或PG表。

其余原任务继续：人工DeepSeek/清洗遗漏/复杂采集/场景步骤与别名、默认AI用知识和正式断言；API续期及完整Agent异常/并发/幂等；性能容量/AI流式、其他安全工具与Agent报告；完整准备、未知副作用只读核验、计划修订/其他模式；业务需求评审、生产迁移、全阶段物理HTTP与旧Worker原件。不得用本轮设置页面或受控生成替代上述完整产品验收。

三份方案同步、本批原件/前序工件及受保护文件核对见p0-model-request-profile-validation.json；实施命令见IMPLEMENTATION.md。完整目标保持未完成。

### 7.14 录制衍生场景、步骤整理及业务别名〔2026-10-10〕

基线5276887c。已有录制/事件、图谱、完整知识读取和录制转正式草稿；此前没有用户可编辑的场景资产，内部alias只处理元素ID。先核对RecordingStore/转换器/正式Case版本与原评审、项目服务、API/路由、参考REPORT5.3及参考webui/action_schema、本地课程；依据和失误均见P5_RECORDING_SCENE_RESEARCH.md。

新增PG场景身份和不可变版本两表，表名沿DatabaseConfig，主进程沿既有幂等初始化；不回填/改写旧录制、用例、哈希或运行。配置继续MySQL，录制与场景业务为PG，原RustFS/图投影保留。创建需正式项目及已完成录制，快照保存完整脱敏原动作/观察、固化指标及原始哈希。步骤可排序/整理说明与预期/显式排除，排除序号与理由可查，原事件不删除；真实重复操作不是重复投递。业务别名引用原动作的target与page/文档范围，不静默修改selector。用户整理状态不等于执行通过。

创建请求项目内唯一；编辑锁场景头并比较父版本。同请求重读原版本，同身份变内容或落后父版本409；GET按请求恢复提交。浏览器保留提交身份，创建响应丢失后刷新先查原提交，不自动重发；编辑重开只保留ID而不缓存业务内容，先查结果，不能无原正文构造重发。已确认400/404/409/422拒绝可继续编辑。场景/历史读取精确项目隔离，归档项目可读不可新写。

项目详情及知识页增加场景入口，完整App支持从录制创建、名称/说明/步骤/预期/业务别名/断言JSON整理、历史只读、固定来源查看及分页。转草稿固定所选scene_version_id/hash，完整原event、场景说明、别名target/page及用途说明进入正式JSON业务内容，复用原TestCaseService和草稿→评审→启用→套件链；保存响应可只读找回，后续场景修改不重建旧草稿。没有浏览器执行入口、自动启用或模式参数编译。

| 实際批次 | 结果/根因 |
|---|---|
| before | 新HTML、实际Chromium/CDP与独立main录制已入库，场景POST404；1失败34.94秒，证明缺管理入口 |
| api-first | 1失败32.55秒，新存储误改只读RowMapping；改为构造新映射，原事务回滚，stderr保存 |
| product-first | API重复/版本/项目/草稿检查已通过，build因不存在getProject失败，1失败47.37秒；改用既有overview |
| product-verified | 实际App创建/编辑已成功，别名选项改变隐式标签导致定位失败，1失败93.62秒；补明确aria-label |
| restored/final | 1失败44.66秒（新测试Node类型不在当前前端配置）；1失败52.69秒（原草稿误声明正式版本）；按既有JSON导入和正确DTO修正，先独立类型检查再产品运行，不重复绕过build |
| product-delivery | 1通过70.42秒，实际提交后丢响应/刷新恢复、编辑/历史/转换/并发/API重启；新增两场景六版本 |
| projection-before | 旧现场别名仅name/seq，无独立target/page，1失败0.23秒；补完整定位及场景说明，旧工件保留 |
| product-complete | 1通过62.00秒，0跳过，完整main/built App与实际MySQL/PG/Redis/Chromium/Memgraph；别名/说明正式保存和原事件不变，项目隔离404、归档写409、并发201/409 |
| 最终product-integrity | 1通过72.94秒，0跳过；新增整理前PG原始整行快照，8条事件的所有列和完整payload在整理、转换、重启后逐行相等；同一完整App流程再次通过 |
| 回归/前端/编译 | 最终9模块72通过2门控跳过7.49秒；原live草稿开关未启用。前端13通过2.32秒，新增5项真实数据/未知提交/刷新/拒绝/项目切换测试；实际产品build/vue-tsc退出0，最终compileall_exit=0仅补查 |

浏览器脚本错误0；故意API重启窗口会话轮询产生ECONNRESET/ECONNREFUSED，保存原日志和network窗口，不能称全程网络无错。未将历史失败日志覆盖为成功。敏感脱敏复用既有masked_recording_event_payload，不打印用户载荷；前序工件/受保护文件/三份方案核对见p5-recording-scene-validation.json。

原PG payload时间带Z，API沿既有RecordingStore/ensure_utc_datetime契约使用索引列的naive UTC；审计核对两份PG原行完全相等，并单独核对三种时间表示的同一UTC时刻。没有改动时间契约，也没有将归一后的相等冒称原字节相同。

分类更新：**录制后场景资产、步骤/业务别名/断言整理、版本查询与草稿衔接，本批补实现并按上述范围已验；场景知识工具接入见7.15。** 录制前场景规划、规划来源自动关联、跨录制组合、selector修复评审、默认AI真正理解/执行/断言、完整清洗去重/遗漏、人工DeepSeek与复杂网络/跨文档/Shadow DOM关联、失败定位与报告仍需推进。场景归档版本单独恢复/更大数据量尚未专项验收，不能以项目归档代替。新表沿既有初始化需要PG CREATE/INDEX权限，生产历史/并发启动迁移未验。显示最近200录制且场景按50分页，容量未验。

其余完整P0—P5不缩减：准备/未知副作用只读核验/执行中修订和其他模式、默认Agent完整闭环，API鉴权续期/异常并发，性能容量/AI流式及安全其他工具，需求业务评审/部署/生产历史、全阶段物理HTTP和旧Worker原件，以及外部模型/更多推理与媒体。默认AI验收按用户决定继续暂缓；没有沿用旧403作为现状证据。本批不调用外部模型、不推送/建PR，完整目标保持active。

### 7.15 场景知识接入现有查询工具与固定版本回读〔2026-10-11〕

基线cc2d74f5。依据见P5_SCENE_KNOWLEDGE_RESEARCH.md：原knowledge-rag仅检索本地MD和记忆，场景已在PG保存完整不可变版本。复用现有safe工具和ToolJob，不增加知识副本、执行模式或配置；主进程注入读取服务，scene_search提供项目目录，scene_read按场景/版本/hash返回完整内容、原始响应/观察及别名target/page。项目取自持久会话并与上下文核对；归档项目可读历史，整理状态不证明测试通过。

先在新HTML/实际Chromium/CDP及独立main/PG/MySQL/Redis环境复现旧工具不返回场景，before1失败42.07秒。修复前旧Runtime没有场景操作；该失败不是仅测试漏注入。首个修复验证导入错误单列保留，修正夹具后fixed2/delivery/final分别1通过89.92/95.55/80.26秒；三次范围为直接Runtime与真实PG，不能称默认Agent或生产主进程实际调用工具。

最终complete1通过64.97秒，0跳过：同一完整App流程创建两项目六场景版本；注册LangChain StructuredTool实际调用权限治理与ToolRuntime，完整正文/source逐项相等，业务别名target/page来自原动作；跨项目正文失败。API重启后新建读取服务和PG stores，场景头已更新仍读取固定V2；项目归档后也可只读V2；两次终态重投复用同一PG ToolJob及原输出。测试驱动工具调用并提供框架runtime，不调用模型、Agent推理或完整图循环；主进程注入代码已核对但不以旁路测试冒称它发起了工具调用。

注册工具保留旧query必填契约，场景读取提交有内容的query；scene DTO允许空query的直接读取不作为治理入口用法。已安装StructuredTool使用dict Schema原样保留实参，不自动注入top_k默认；场景操作按描述省略top_k。目录支持参数化名称/说明查询及分页，真实多页容量、场景自身归档筛选、并发模型操作尚未专项验收。

回归、编译、原件及前序工件审计见p5-scene-knowledge-validation.json与IMPLEMENTATION.md。配置继续MySQL，业务版本/ToolJob为PG，原大工件RustFS；无新表、依赖、前端更改或实际全局模型配置修改。当前规划来源目录尚未纳入场景；默认AI真正使用知识并断言、人工DeepSeek、清洗遗漏、复杂响应关联、失败定位/报告及其余完整P0—P5仍未完成。账号处理前继续其他模块，目标保持active。

### 7.16 正式用例明确关联场景的固定预读〔2026-10-11〕

基线fee770f0。先核对scene转正式草稿的ui_recording_scene/version/hash、PG快照读取、规划关联文档阶段与固定seed、MySQL策略、旧Prompt/Schema恢复以及页面消费；依据见P5_SCENE_PLANNING_RESEARCH.md。新HTML、真实CDP/录制/场景/正式启用用例和实际主进程复现before1失败21.00秒：场景引用被当作文档，错误“该来源不是可读取的文档引用”，未读正文。

新增关联来源v2，保留旧v1行为和序列化。沿原basis字段增加严格场景身份，来源须有明确scene_id/version_id/content_hash；先校验、保存基准，再读取完整不可变版本。scene独立证据包含完整步骤/原始响应观察/业务别名target/page及not_executed，不冒充原录制或测试通过。原PG检查点拒绝替换基准、来源和已保存结果；项目不同、缺版本/hash、错误hash保留明确缺口，不选择当前头替代。复用现有知识投影，不新增知识副本或业务执行器。

MySQL现有项目策略字段接受v2，前端类型/开关/显示与API同步；重新启用关联来源时选文档与场景。已保存v1不会自动改写，页面说明其只读文档及启用场景的方法。主进程注入同一PG场景store。原全局/项目配置不回填，无新表、依赖或模型单一化改动。

| 实际验证 | 结果及范围 |
|---|---|
| verified/delivery | 1失败55.25秒为测试误访问任务summary字段（实际message）；主恢复已完成，纠正夹具后1通过52.32秒。失败原件保留 |
| 最终final | 2通过95.15秒，0跳过；各自新HTML/CDP/production main、完整built App、MySQL/PG/Redis。基准及读取结果两个提交确认丢失位置，首次模型前保存；API重启、场景头更新、当前MySQL策略变化后新建服务，原PG seed逐项不变。各原任务2个受控规划回合，终态重投0新增；完整正文/来源/别名依据核对，4类错误来源和2类检查点篡改分别拒绝 |
| 后端受影响回归 | 59通过3门控跳过55.75秒；linked_sources/evidence_references/stages/tasks及知识/投影，开启实际规划任务存储。3项原格式解析门控未启用，不计完成 |
| 新合同与原文档 | 5通过5.23秒；三份真实旧seed/checkpoint/Prompt完全相同，上一批实际知识工具投影不变；新增Markdown实际RustFS上传、MySQL v2、PG预读和终态重投，原文完整及hash相同 |
| 页面 | 初轮66通过1旧标题断言失败，保留原件；最终68通过3.36秒，旧配置不会静默升级、新选择保存v2及旧固定文档结果继续显示。两次实际产品build含vue-tsc/Vite通过，页面错误0 |

规划模型由受控响应驱动，不调用外部默认模型；planning结果说明资料已到达和固定，不证明AI判断质量或UI执行通过。提交后抛异常是在真实PG commit之后注入确认丢失；新建读取服务/租约恢复不等同真实kill Worker进程。实际main负责资产/策略API及App，规划handler在测试进程使用同一真实存储；不冒称主进程或默认Agent完成自主规划。首次推理前保存/回读与终态不重投均有PG原件。

分类：**已有正式用例明确关联场景的预读和固定恢复，本批补实现并按上表已验。** 未关联场景的自主目录检索、规划中动态选择场景、场景中的预期直接用于技术效果声明、真实Worker进程中断、默认AI理解/执行/断言、人工DeepSeek、清洗/遗漏/复杂响应关联、失败定位报告仍需推进。准备/未知副作用只读核验/执行中修订/其他模式、API续期与完整Agent、性能容量/流式及其他安全工具、业务评审/部署/生产历史迁移/全阶段物理HTTP及旧Worker原件保持未完成。

最终compileall退出0仅语法补查。149份前序工件、3个受保护文件、新原件与三份方案审计见p5-scene-planning-validation.json；实施命令见IMPLEMENTATION.md。无推送/PR，完整P0—P5继续active，外部AI按用户决定暂缓。

### 7.16 补充：投影哈希、短引用组合及正式Worker接线

交叉核对实际已保存场景证据，发现旧短引用目录将content_hash与整个payload哈希比较；场景content_hash是原始content/source快照哈希，两者不同。真实原件复现见p5-scene-planning-reference-before.log。新增可选payload_hash覆盖完整知识投影，目录校验同时核对原始快照与投影，场景E目录包含投影哈希防止同一快照下投影漂移；旧证据省略该可选字段，旧seed/checkpoint/Prompt和文档目录保持。logic仍用正式ID，开启短引用时effects使用E并由系统还原，不把场景冒充文档预期。

| 本次追加实际验证 | 结果及边界 |
|---|---|
| reference-final | 4失败189.70秒：新增篡改检查误用alias字段，实际SceneAlias为name；原失败及SQL保留，修正夹具后重新验收 |
| reference-complete | 4通过189.44秒，0跳过；普通/短引用×基准/已读结果提交确认丢失。每例独立HTML/CDP/main/App/真实数据库，首次推理前固定；头版本/当前策略变化和服务恢复后原seed与V2保持；E还原正式场景ID。每例3种快照/投影篡改、4种错误来源与2种检查点变更拒绝；原始事件整行不变，终态0追加调用 |
| regression-final | 69通过3格式解析门控跳过64.05秒；包含实际文档兼容、旧快照/Prompt等价、原知识投影、实际场景双哈希/目录漂移核验。3跳过不计完成 |
| worker-before/worker-complete | 新场景经正式planning_worker_runtime复现1失败19.88秒，未配置场景store。补齐原启动流程的初始化/注入及日志，最终5通过53.53秒；其中1项真实main/App/CDP/MySQL/PG/Redis和两次Worker Runtime初始化/关闭，API重启后读相同V2完整证据；其余4项CLI导入隔离和原部分启动清理回归，模型调用0 |

Worker专项是正式启动函数在测试进程的真实存储读取，不是独立CLI完成规划、更不是默认AI执行；上述规划仍由受控模型响应驱动。真实进程kill、独立CLI完整场景规划、未关联场景按需查询、场景预期技术声明和全部默认AI业务验收仍待推进。最新compile-worker-final退出0仅补语法。无新表/依赖、全局模型修改或旧配置自动升级；Worker初始化已有场景表沿既有PG初始化权限执行，生产部署/历史迁移未验。原件和旧工件校验、三份方案同步见p5-scene-planning-validation.json。

### 7.17 规划按需查询未关联项目场景〔2026-10-11〕

基线b8c69d4e，依据见P5_SCENE_QUERY_RESEARCH.md。已有场景search能给项目隔离、分页及version_id/content_hash导航，固定正文读取也已存在；原PlanningQuery/BusinessPlanningQuery不接受scene/scenes，规划目录未列场景。本批新HTML/CDP/main/PG场景复现before1失败20.46秒：数据库有实际场景，但旧Schema拒绝scenes。属于需补查询契约与消费能力，不能只把场景目录当正文。

新增MySQL可选scene_query_protocol；旧配置不回填，新政策冻结到PG seed。新增独立查询/两阶段Schema和v13 Prompt，保留旧Schema/Prompt/序列化；同步原pending_decision、logic/effects解析和查询消费。scenes按项目查询目录并分页；scene必须有source_id/version_id/content_hash，沿原PG固定读取完整步骤/原始响应/别名及双哈希投影。目录不产生已读证据；not_executed不代表通过。查询请求/逐项结果沿原检查点，共用原回合/时间，不新建规划循环。关闭查询策略仍保留历史任务恢复。

项目页面增加“允许规划时查询项目场景”；类型/API/保存与依赖策略关闭同步。不要求用例先关联场景，不修改原测试模式、不将用例编译为模式参数、不读取生成模板。main和正式Worker沿上批已接的同一场景store，未新增模型依赖或单一模型限制。

| 实际验证 | 结果与范围 |
|---|---|
| before | 1失败20.46秒，实际新场景存在、旧Schema拒绝。原PG导航与错误原件保留 |
| verified | 4失败146.66秒，分页新增1个真实场景，原产品夹具将总数写死2而实际3；按additional_scenes参数核对，默认夹具行为不变。原失败/SQL保留 |
| complete | 4通过186.79秒，0跳过；logic/effects×查询请求/读取结果提交确认丢失。每例真实HTML/CDP/main/App/MySQL/PG/Redis；正式用例无场景来源引用，page0/page1导航无scene证据，读取才产生正文；API重启、场景头/当前政策变化后原seed及V2保持。原任务5个受控模型回合、固定读取1次、终态追加0。证据阶段E还原正式ID；跨项目/错误hash/关闭策略拒绝，原事件整行不变 |
| 合同及回归 | 新合同12通过0.68秒，读取实际原导航与4份v12 seed/checkpoint/Prompt，保持等价。初查27通过9门控跳过4.48秒；实际规划存储开关启用后回归85通过3格式解析门控跳过68.89秒，最后恢复旧协议V4 query转换的原行为再验85通过3跳过61.03秒；不累加两次回归数量 |
| 页面/构建 | 73通过3.30秒；新开关保存及关闭依赖策略同步。4次实际product build包含vue-tsc/Vite通过、页面错误0。compileall退出0仅补语法 |

确认丢失发生在真实PG commit之后；新建服务/租约恢复不等同真实进程kill。规划handler在测试进程使用实际存储与受控模型；不宣称独立CLI完成新查询、更不宣称默认AI自主查询/理解/UI执行。已有查询契约与恢复已验，默认AI质量仍待验。

仍需推进：场景预期用于技术效果声明、独立CLI/Worker进程故障、人工DeepSeek、清洗/遗漏/复杂网络及跨文档/Shadow DOM响应关联、selector修复与失败定位报告；准备状态/未知副作用/执行修订/其他模式，API完整Agent及续期，性能容量/流式与其他安全工具，用户业务评审/部署/历史迁移及全阶段物理HTTP、旧Worker原件。完整P0—P5继续active，外部AI按用户决定暂缓。无新表/依赖/全局模型或旧项目配置修改。原件、前序456+149工件、保护文件及三份方案同步见p5-scene-query-validation.json。

### 7.19 场景业务预期回执与真实浏览器结果〔2026-10-11〕

依据：`P5_SCENE_EXPECTATION_RESEARCH.md`、全新录制场景、实际 Chromium、现有 browser-control/ToolRuntime、PostgreSQL 运行结果和 RustFS 原件。旧回执实测拒绝 `expected_source`，因此没有放宽旧 Schema，也没有把普通 UI 断言误归入 `produced/cleanup` 对象效果。

已实现可选 `case-scene-receipt-v1`：项目策略在 MySQL 保存，规划后冻结到 PostgreSQL 执行上下文。新回执使用 `schema_version=2`，每个检查只能引用当前项目已读场景中保存的 `content.steps[index].expected` 或 `content.assertions[index].expected`；服务器核对场景原件、投影哈希、项目、场景、版本、证据归属和路径，再用现有确定性比较服务核对真实持久化 ToolJob 输出。旧 `schema_version=1`、旧 V4 Prompt、旧文档预期路径保持原行为，关闭开关时上下文和工具 Schema 不带新字段。

真实验收：新场景保存计数 `2`，现有 browser-control 通过本机 HTTP 页面实际点击两次并读取 DOM；计数 `2` 通过，错误预期 `5` 失败，`0`、`false`、`null` 类型保持；修改当前策略和场景头后，已冻结运行仍使用原版本。实际工具 JSON 与 transcript 从 RustFS 下载并校验字节数/SHA256；同一 PostgreSQL 事务内生成消费候选，`run_item_completed=false`。前端场景预期开关测试 79 项通过；后端新旧契约 27 项通过；完整真实场景 1 项通过 69.60 秒；旧链路回归 145 通过、3 项因真实模型门控跳过；compileall 及前端 typecheck/build 通过。原始失败复现、旧兼容回归和每次实测原件均保留。

边界：这批是受控规划模型和真实浏览器/工具/存储验收，不代表默认 AI 已完成场景选取、理解或断言质量；默认模型因账号实名认证暂缓。消费候选验证不代表自动完成消费或 RunItem。场景预期不处理录制清洗、遗漏诊断、网络/跨文档/Shadow DOM 关联、selector 修复和失败定位报告。

### 7.18 独立规划CLI的场景查询真实进程恢复〔2026-10-11〕

基线1776cdd9，依据见P5_SCENE_CLI_RESEARCH.md。该能力已有生产实现，本批补真实独立进程验收，未修改生产代码。沿原CLI、planning_worker_runtime、RedisTaskWorker、PlanningTaskHandler及模型适配器；受控localhost HTTP模型配置存于本轮独立MySQL表，真实PG固定任务与场景、Redis投递、main/CDP新录制和已构建App。

首轮1失败24.60秒：测试夹具给只读派发流属性赋值，Worker未启动；改为登记流并核对派生属性。第二轮1失败24.80秒：真实HTTP和SQL检查点已到达，但虚拟环境启动器PID与Worker PID不同，原单PID断言错误。沿项目既有taskkill /T /F回收确切owned进程树，保存启动器/Worker/父PID、退出码及tasklist缺席证据；不改变生产租约。修正后单项1通过65.49秒，全部原失败原件保留。

最终complete **4通过243.89秒，0跳过**：logic/effects×读取前/读取结果提交后kill。每例真实SQL提交返回后保存观察标记，父进程核对PG再杀进程树；实际Worker消失。PG原30秒租约由服务器时间自然到期，不手工expire，不改时钟；隔离Redis传输回收1000ms，区别于默认120000ms，不能将此批称为默认传输等待时长验收。API重启、场景头V4、当前MySQL政策关闭查询后，原seed、v13 Prompt、原场景V2及首次截止时间仍保留。第二attempt理由lease_expired；每项4次真实SDK/HTTP、完整场景读取1次。读取结果已经提交时恢复不重读；终态重投原Redis并运行正式CLI，追加模型HTTP/读取/attempt均0，原计划及证据整行保留。证据阶段E引用还原正式身份，not_executed不当作通过。

兼容性初查25通过1门控跳过8.62秒；启用独立真实存储复验 **26通过11.23秒，0跳过**，涵盖旧Schema/Prompt/投影/引用、旧文档来源及Worker生命周期，不累加两次数量。四次实际App构建/vue-tsc/Vite退出0、页面错误0；compileall退出0仅补语法。原录制和PG原事件整行不变。四组原HTTP正文/响应、SQL检查点/seed/attempt/截止时间、进程日志、原场景及页面原件保留。

范围是受控HTTP语义响应下的只读规划，不是外部默认AI自主查询/理解/UI执行/正式断言，也不是未知副作用或其他模式恢复。7.16/7.17历史记录中“独立CLI/真实进程kill待验”在本批范围内已补验，历史失败不重写。无生产表、依赖、默认模型、全局配置或旧项目回填；生产部署/历史迁移仍未验。原件、前序855工件、3个保护文件及三份方案同步见p5-scene-cli-validation.json。

当前未完成项继续按两类管理：

| 方向 | 已有实现、仍待验收 | 仍需补实现或查证实际缺口 |
|---|---|---|
| 模板、生成、导入与需求变更 | 默认/项目模板、Excel/JSON完整页面及普通模块生成已有专项；真实模型业务质量、更多容量/故障、业务评审与生产部署仍待验 | 根据新增实际失败定位补强；不把已验模板/页面重新列为未实现 |
| 按用例执行 | 真实AI项目选取、排序、补全、工具执行、断言、回执与结果完整闭环 | 完整准备状态、未知副作用只读查证、执行中计划修订及其他模式仍需逐项核验补齐 |
| UI录制与知识 | 场景编辑/步骤/别名/查询、固定规划、独立CLI恢复、可选场景业务预期回执及确定性清洗/遗漏投影已有专项；人工DeepSeek和默认AI业务使用仍待验 | 复杂网络/跨文档/Shadow DOM关联、selector修复评审和失败定位报告 |
| Agent API | 依赖来源、响应提取、参数传值等专项已有；完整默认Agent与异常/并发/幂等组合待验 | 鉴权续期、未覆盖故障和结果呈现按实际缺口补齐 |
| 性能、安全 | Docker原输出、工件、解析及报告已有专项；完整Agent闭环、其他工具和故障待验 | 逐级容量、负载/环境可比性、AI流式阶段指标及其他安全发现证据仍需推进 |
| 模型适配、重复请求与交付 | 现有多传输/profile及终态映射已有专项，外部多供应商/推理/媒体质量待验 | 全阶段逻辑调用/SDK重试/物理HTTP故障追踪、生产历史迁移与投影核对、旧Worker汇总原件追踪 |

外部AI按用户决定暂缓；旧Step200/403均为历史，不推断当前账号状态。完整P0—P5目标继续active。

### 7.20 UI录制清洗投影与遗漏诊断〔2026-10-11〕

依据 `P5_RECORDING_QUALITY_RESEARCH.md` 和现有 `PostgresRecordingStore`、`RecorderSessionService`、`recording_case_draft_service.py`、`recorder.js`。现有能力已能按 `(recording_id, seq)` 消除重复投递，并在停止时核对 receipt、序号和 PG 对账；这不能判断不同序号是否是用户真实重复操作，也没有统一保存观察噪声和质量诊断。

新增 `recording-quality-v1` 派生投影。原始事件继续追加写且不删除；`page_scan/page_response` 等观察事件列入 `noise`，可回放事件列入 `cleaned_event_sequences`；相邻相同稳定 DOM 定位和输入值仅列为 `duplicate_candidates`，不自动删除，连续真实点击仍保留；receipt 或序号无法确认时列出 `missing_sequences/sequence_gaps`，不猜测遗漏内容。终态 `finalize_metrics.recording_quality` 与录制草稿 `test_data.recording_quality` 使用同一结果。

修复前测试无法导入质量分析模块；首次真实 Chromium 运行又发现稳定指纹错误包含动态 `recording_scope`，真实连续点击未被识别。收窄为页面/帧/文档身份、稳定 DOM 定位和输入值后，合同 `test_recording_quality.py` 6通过，受影响录制回归 74通过、8跳过，实际 Chromium/CDP、独立 API、隔离 MySQL/PG/Redis 及草稿转换 `test_recording_quality_live.py` 1通过；两次真实点击被保留并标为候选，观察事件被标记，原始 PG 行逐行不变；缺 receipt 或传输丢失计数时不报告已完整。失败 XML 和调试原件保留，见 `p5-recording-quality-live-before.xml`、`p5-recording-quality-live-debug/`、`p5-recording-quality-live-fixed/`、`p5-recording-quality-live-delivery/`。

本批只完成确定性清洗投影和基础遗漏诊断。人工 DeepSeek、复杂网络/跨文档/Shadow DOM 关联、selector 修复评审、失败定位报告和默认 AI 使用仍未验收；不能把本批受控 HTML 测试当作完整 UI 录制业务闭环。目标继续 `active`。

实际命令：Agent_Server内RUN_LIVE_SCENE_CLI=1、RECORDING_SCENE_EVIDENCE独立绝对first/verified/tree-verified/complete目录，运行`..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_scene_cli_live.py -q --junitxml=../docs/upgrade_2026-10-03/p5-scene-cli-complete.xml`。合同复验RUN_LIVE_CASE_PLANNING_TASKS=1，运行test_scene_query_contract.py/test_scene_planning_contract.py/test_planning_worker_runtime.py，输出contract-live。按白名单中文commit，不推送/PR。

### 7.21 性能关联清理 Worker 与回归补验〔2026-10-11〕

上次响应关联扩大实测在第11个场景因 `resource_pending_reap` 暂停；根因是生产 `DockerResourceManager.retire` 必须等待 PostgreSQL cleanup receipt 才释放配额。沿现有 `src.cli.run_resource_cleanup_worker --once` 逐场景启动清理，不扩大资源池、不手工释放租约。`p5-perf-correlation-worker-verified2` **1通过105.35秒**：12个 smoke、2个 load/report，14个 allocation 与14个 `completed` cleanup receipt 一一对应，Redis四类占用每次归零，容器完整ID删除；链式登录/受保护请求、独立目标、Bearer前缀、0/false/null/空串/坏JSON、负向401、缺值后独立目标继续及load失败报告均保留证据。

运行中发现录制质量文件末尾字面量 `\\n` 导致生产API导入SyntaxError；删除非法行后录制质量和会话回归 **19通过6.29秒**，提交 `4f6c492d`。既有性能测量 **1通过43.04秒**、性能证据重启/下载 **1通过53.09秒**。本批仍是缓存k6、受控本地HTTP、隔离存储和现有Worker验收，不代表默认AI、外部供应商、连续容量或完整App。完整目标继续 `active`。

### 7.22 DeepSeek 公开页面真实人工录制补验〔2026-10-11〕

依据 `P5_DEEPSEEK_RECORDING_RESEARCH.md` 和现有录制实现，使用 Playwright 驱动真实 Chromium 访问 `https://www.deepseek.com/`，通过生产录制 API 写入隔离 MySQL/PostgreSQL/Redis/Memgraph。页面返回 HTTP 200，标题为 `DeepSeek | 深度求索`；实际点击“深度思考”两次、“智能搜索”一次，滚动并截图，不登录、不提交聊天、不调用默认 AI。最终 `test_recording_deepseek_live.py` **1通过、29.81秒**：PG 原始事件 8 条，`recording-scopes-v2` 的 pending、未确认 frame、通道丢失和传输丢失均为 0，质量投影识别 3 条观察噪声和 1 组重复候选，`removed_event_sequences=[]`。

首轮失败原件保留在 `p5-deepseek-recording-live/`：测试先关闭浏览器再停止录制，scope 未结束确认并返回 `finish_unconfirmed`，但 PG 事件仍为 8 条。把停止动作移到浏览器关闭之前后通过，根因是测试生命周期顺序，不是录制器丢事件。`business_capture_complete` 仍为 `unknown`，本批只证明公开页面人工操作进入现有录制、落库、回执和确定性质量投影链路；复杂网络/跨文档/Shadow DOM 响应关联、selector 修复评审、失败定位报告和默认 AI 读取/执行仍未验收。Step/默认模型按用户决定等待账号实名认证后再验收，完整 P0—P5 目标继续 `active`。

### 7.23 API 显式刷新凭据传播补验〔2026-10-11〕

依据 `P2_API_AUTH_RESEARCH.md`、`CapabilityMapper`、`DependencyPlanner`、`ApiTaskCoordinator`、`CredentialManager` 及接口测试课程登录/鉴权章节，先用真实本地受保护 HTTP 服务验证项目已有显式刷新端点链路。修复前登录返回 token-1、刷新返回 token-2 均成功，但 `/profile` 仍携带 token-1；根因是协调器只给没有 `auth_ref` 的任务传播凭据。

修复协调器：认证任务成功后，更新未来认证任务以及明确依赖该认证任务的未执行任务；无该认证依赖的任务保留显式凭据；结构化日志只记录任务身份，不记录 token。真实 `ThreadingHTTPServer`、CapabilityMapper、DependencyPlanner、ApiTaskCoordinator、ApiTaskExecutor 和 httpx 验证登录→刷新→受保护接口 **1通过、1.30秒**，完整鉴权实测 **4通过、2.87秒**，API依赖/恢复回归 **28通过、2跳过、4.53秒**。物理请求顺序和认证事实为 `/login` 无认证、`/auth/refresh` 使用 token-1、`/profile` 使用 token-2。

本批只覆盖明确刷新端点且响应含可识别访问 token 的协议；`expires_at` 自动续期、不同供应商刷新字段、Cookie续期、并发刷新去重、默认 Agent 自主鉴权和完整 App 仍未验收。无新表、依赖、配置或前端改动，Step/默认模型按用户决定继续暂缓，完整 P0—P5 目标保持 `active`。


### 7.24 鉴权修复后的真实性能回归〔2026-10-11，f3cda5e〕

复核现有 k6/JMeter adapter、生产 runner、资源清理 CLI 和历史 7.21/6.94 验收；本批未新增性能生产代码，是现有能力的再次实测。k6 响应关联 **1通过87.12秒**，含12个冒烟及2个负载报告；测量四种判定回归 **1通过34.07秒**；ToolRuntime/PG/RustFS/API重启及原件下载 **1通过48.16秒**；JMeter依赖/独立目标/轮次及报告 **1通过95.55秒**，含5个冒烟变体和2个报告。

实际 k6 缺值场景只跳过依赖目标，独立目标继续；有效凭据仅送到指定消费者，预期401通过，0/false有效，null/空串/坏JSON不通过。两份load均exit0且SLA通过，但缺值报告仍fail。JMeter missing/mixed/valid/tail/multiple实际请求计数和判定与预期一致；7份清理回执completed，PG报告fail/pass。原件接口跨项目404、同大小篡改409、恢复后字节相同，终态重投新增引擎0次。原始事件只保存凭据哈希及布尔事实。

证据目录为 `p5-perf-correlation-auth-refresh-verified`、`p5-perf-evidence-regression`、`p5-jmeter-dependencies-worker-verified`，SHA256/大小清单见 `p5-performance-regression-validation.json`。测量回归误用了非受支持的证据目录环境变量，本次临时原件未留存，仅保留JUnit；前批测量原件保持。本批固定计划直调生产组件和缓存引擎，不证明默认AI、自主Agent、完整App、容量或持续负载。历史JMeter及Worker能力已验，不重新标为本轮新实现；默认模型按用户决定暂缓，完整目标继续active。

交付复核发现本次k6回归曾误复用旧目录；当前原件已移至独立auth-refresh目录，旧目录及XML按f3cda5e逐blob恢复并比对。测试结果保持，历史原件恢复，纠正过程保留提交记录。


### 7.25 模板、Excel/JSON导入和模型适配层当前实测〔2026-10-11〕

先读现有 `CaseImportParser`、`CaseImportService`、导入路由、生成模板服务、`ModelRuntimeService`、`request_mapping`、provider profiles 和五种 provider client，再运行真实项目页面测试；没有为已有适配层再套一层全局抽象。完整项目验收 **1通过、61.53秒**：构建前端并用 Chromium 操作真实项目页面，保存默认/项目生成模板和写作指导，上传并下载原件，预览并提交 rows Excel、split 分表 Excel、JSON，错误行只进预览不提交；提交幂等，项目隔离成立，API重启后模板、草稿和用例仍在。配置写入 MySQL，导入来源原件写 RustFS，用例草稿/版本写 PostgreSQL，未调用模型。导入逻辑回归 **29通过、9跳过、12.11秒**。

模型适配层实际矩阵 **81通过、17.41秒**，覆盖 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Google Gemini generateContent 和 LangChain OpenAI兼容路径的原生/流式、完整/截断终态、usage、工具调用、请求参数映射、错误包装和旧协议兼容。结论是当前已有统一应用请求/响应契约加 provider-specific client，不应再增加一层重复的全局适配；仍需外部供应商真实验收、供应商特有推理/输出能力核对和默认 Agent 路径验收。证据见 `p1-case-import-model-adapter-validation.json`、`p1-case-import-full-product-current.xml` 和 `p2-model-adapter-current.xml`。

本批只证明导入/模板产品闭环和适配器边界，不证明普通需求文档经默认模型全量生成质量，也不证明默认 AI 选用例执行；账号实名认证完成后再验收。完整 P0—P5 继续 active。

### 7.26 Shadow DOM 响应关联补验

现有脚本虽能给 open Shadow DOM 目标生成 `shadow_path`，但原 MutationObserver 只观察普通文档树。真实 Chromium/CDP/API/隔离存储复现后，点击已入 PG，shadow、动态 shadow root 和 iframe shadow root 的响应均丢失；依据 DOM Standard 确认这是观察边界缺口。生产脚本现递归观察已有和后续创建的 open root，响应文本进入 open root，并跨 shadow host 做敏感祖先判断；保留旧协议字段，增加 `shadow_dom=open_roots` 和 `closed_shadow_dom=unobserved` 能力声明。修复后 `test_recording_shadow_live.py` 1通过22.49秒，17项录制回归通过。原始事件、响应关联和草稿读取均查证，敏感 fixture 不泄露。复杂网络因果、closed root、selector修复、失败定位和默认AI仍未验收。

当前未完成项继续按两类管理：

| 方向 | 已有实现、仍待验收 | 仍需补实现或查证实际缺口 |
|---|---|---|
| UI录制与知识 | open Shadow DOM 响应采集、跨文档/iframe范围和确定性质量投影已有真实专项 | 网络请求因果关联、closed Shadow DOM、selector 修复评审、失败定位报告、默认 AI 使用和完整业务闭环 |

外部AI按用户决定暂缓；完整 P0—P5 目标继续 active。

### 7.27 UI录制 fetch/XHR 网络响应元数据关联〔2026-10-11〕

依据现有 `recorder.js`/`RecordingCaptureSettings`/PG事件消费者和浏览器 API 语义查证：原实现只观察 DOM，`page_response` 没有网络状态。真实 Chromium 夹具一次点击触发 fetch 200、fetch 404、XHR 201，旧脚本已有点击和 DOM响应但缺 network 字段，复现失败。修复脚本仅在录制响应窗口内包装 fetch/XHR，记录脱敏 pathname、method、status、ok、outcome、耗时，并与 action local sequence 同窗关联；不保存请求/响应体、查询串、头部或 token。HTTP 404保留为response且ok=false，网络异常/abort/timeout单独标记，XHR error/loadend重复收口。

修复后真实验收 `test_recording_network_live.py` **1通过16.43秒**：三个状态与一次点击关联，查询参数和响应体均未进入PG网络证据；Shadow DOM/策略/范围/质量回归 **17通过、40跳过、31.86秒**，Node语法检查通过。证据见 `p5-recording-network-before2/`、`p5-recording-network-verified2/` 和对应 XML。协议仍为 `recording-dom-response-v1`，新增 `network_correlation=instrumented_fetch_xhr_completion`、`network_observation_status` 和 `network` 字段，无新表/配置/依赖。

这一步是可审计的 fetch/XHR 元数据关联，不等同服务端业务因果或网络全覆盖；beacon、WebSocket、Service Worker 内部请求、导航、closed Shadow DOM、selector修复、失败定位报告和默认AI使用仍未完成。
### 7.28 安全工具结果到报告与完整 App 独立复验〔2026-10-11〕

本批没有修改安全生产代码。沿现有 `http_headers_probe`、Kali Docker 执行器、`SecurityTestingModeRuntime` 报告恢复边界、报告列表 API 和已构建前端，用真实本地 HTTP 目标产生缺少安全响应头、完整响应头和不可达三种结果；不调用 Agent/模型。

独立真实验收 `test_security_report_product.py` **1通过、64.13秒**：3个真实任务为2完成/1失败，4个发现，报告状态为部分完成；RustFS 原件下载后的 JSON 结算、核验、评估字段与最终报告一致；报告列表按模式过滤后分页，未就绪会话显示进行中；API 重启后报告内容一致且没有新增目标 GET。构建版完整 App 的报告页显示任务总数3、失败1、缺少 X-Frame-Options、等级“需关注”，浏览器下载成功，页面错误0。Kali/PG/RustFS/Redis、容器删除、跨会话404和 ToolJob 重放事实均保留在 `p5-security-report-current/`，JUnit 在 `p5-security-report-current.xml`。

该批证明现有响应头工具结果可以进入报告及完整 App 展示，不等同其他安全扫描器、完整 Agent 自主规划、权限/超时/存储故障组合、连续性能容量或生产部署迁移验收。默认 Step/AI 仍按用户决定等待实名认证。

### 7.29 当前默认 Step 验收：配置迁移、真实截断与架构复核〔2026-10-11〕

用户已说明认证完成，撤销继续暂缓外部AI的当前前提，历史403/200记录不改。先读原目标附件、当前规划/模型/原事务与测试。实际MySQL默认配置读取报1054（缺request_profile列）；按既有初始化补可空JSON列，两次执行幂等，8条原配置包含凭据及更新时间逐字段不变，不回填profile或更改默认模型。

| 实际验证 | 结果与范围 |
|---|---|
| 当前分阶段默认配置 | 1失败183秒；真实main/规划CLI、MySQL/PG/RustFS/Redis，完整Markdown+八步JSON。两次HTTP200，逻辑58.209秒stop，effects98.883秒length，8192 token、正文0，未创建Run/业务HTTP0 |
| 隔离显式profile | 官方支持的JSON Mode与low推理，沿现有适配器，未增加8192/180秒。1失败183.42秒；两次HTTP200，逻辑38.567秒stop，正式目标自行准备/查询/清理，effects120.399秒length、正文0。参数实际发送和PG映射均确认，不能称全局默认通过 |
| 受影响合同/存储 | SDK探针与profile42通过25.38秒；夹具改后42通过1存储门控跳过24.83秒；正确开启真实profile存储专项1通过6.55秒，MySQL配置/PG冻结/RustFS重读和变更后恢复保持 |

不是实名认证错误，也不是笼统的网络超时。两份原失败/物理请求/流式终态/PG保存于p2-default-current-staged和p2-default-current-profile，私有推理只记字段计数不保存内容。测试只指定一个目标候选，尚不能证明多候选检索；规划尚未进入正式业务，更不能声称终态重投或完整执行通过。

研究见P2_DEFAULT_CURRENT_RESEARCH.md。查到checkpoint.linked_sources把完整DocumentContentBasis、asset、version.snapshot、上传安全报告和preview_text都发送模型，正文又在evidence重复出现；本批effects该字段4724字符。恢复记录与推理输入职责混合是已确认事实，但尚不能证明它单独导致截断。下一步先实现版本化来源导航投影并验证完整原文/固定依据及旧请求重放，再核对技术声明工作量；不只增加预算、不删完整JSON/文档/工具契约、不再盲调参数。生产准备状态、unknown只读查证、计划修订、其他模式/App、默认UI回执及其余P0—P5仍未完成。

### 7.30 固定关联来源导航投影与默认 Step 复验〔2026-10-11〕

本批按7.29复核现有文档basis、关联读取、冻结seed与两阶段Prompt。新提交在已有MySQL策略启用关联预读及分阶段时，冻结`case-planning-linked-navigation-v1`及Prompt版本后缀；异步提交和同步create同时生效。此字段是PG任务行为身份，不新增配置开关/表/前端选择。旧seed无字段时仍发送原完整basis，旧Prompt/checkpoint保持；新导航保留项目、role、文档/版本/asset/解析哈希、警告、读取/错误、source及case/evidence关联。source.metadata和场景basis仍保留，不宣称清除所有元数据。完整原文/JSON/工具契约不截断，PG恢复basis及round事实不删除。

初次合同36通过21门控跳过4.70秒；实际MySQL/PG/RustFS、分页、隔离、错版本/asset/hash、已提交回执丢失恢复与终态重投回归67通过3格式门控跳过57.30秒。补强身份及PG完整basis断言并正确开启实际解析后，Markdown/DOCX/文本PDF原件、V2/重解析与关联变体24通过0跳过23.14秒。两批有重叠，不相加为独立功能数量。受控模型只证明请求/状态/恢复；旧兼容限序列化/Prompt，不重新执行历史failed默认任务。

真实默认Step新任务`p2-linked-context-default`1失败197.07秒；隔离显式low/json_object、原180秒/8192上限、全局模型未改。2次物理HTTP200，各SDK仅1次发送：logic60.620秒stop、正文1820；effects109.902秒length、8192、正文0、推理字段仅计数25886字符。failed/incomplete_response已保存，Run和业务HTTP0。关联导航实际4724→982字符，effects evidence仍8292字符；投影有效但不能称根因修复。失败原件、JUnit和SHA清单见`p2-linked-context-*`，调研见`P2_LINKED_CONTEXT_RESEARCH.md`。

同类截断已在3个新任务出现，停止相同参数微调/扩大预算。下一步依据现有产生/清理/预期声明的事务校验与绑定，设计可冻结可恢复的证据声明任务拆分；保留完整业务原文、逻辑固定项、原总轮数/期限及不重复副作用语义。拆分尚未实施。多候选项目选取、准备/unknown/修订、默认Agent执行与正式回执、其他模式/App、默认UI知识及完整P0—P5仍未完成。


### 7.31 可选分任务证据规划、恢复与真实 Step 未通过项〔2026-10-11〕

依据现有多效果关系编译/校验、E/T来源绑定、固定阶段Prompt、PG检查点和原ToolJob事务，新增现有planning_stage_protocol第三种策略`case-planning-staged-evidence-v3`：固定逻辑→传值绑定→按必要角色补产生/清理→合并原canonical声明→既有校验/套件/运行/Agent。模板不进入执行。可编辑策略仍MySQL，Prompt及接受回执追加保存PG，原件RustFS；旧seed精确兼容，不增加表、依赖、模型接入框架或测试模式。所有任务共享原轮数/总期限，空角色跳过，完整用例/原文/工具契约仍保留。

实际MySQL/PG/RustFS专项7种变体及合同共18通过13.00秒；覆盖绑定/产生回执提交后丢确认、清理响应提交后丢确认、策略和文档变化恢复、原3轮预算、坏引用反馈及真实工具契约查询。终态重投额外模型0，PG拒绝改写已接受回执；这些使用受控模型，不是Step成功。旧链路实际70通过64.57秒，含md/docx/pdf和场景/来源读取。前端设置83通过3.26秒，npm run build含vue-tsc通过。最终合同/实际存储/旧对象快照29通过9门控跳过13.97秒；扩大回归79通过53门控跳过，与各批有重叠不能相加作功能数量。首次17个旧链路失败源于本轮抽取校验后无匹配分支遗留变量引用，已根因修复复跑通过，失败证据保留；原工厂测试基线陈旧另经三种AST输出比对后校正，未覆盖上述自身缺陷。

| 真实默认Step新任务 | 实际结果 | 尚未证明 |
|---|---|---|
| p2-effect-tasks-default | 1失败130.96秒；6 SDK/6物理HTTP均200。规划只接受bindings，漏内部清理来源及角色；实际POST两次、GET一次、DELETE两次，本次两条记录均清理；正式回执error | 产生/清理拆分质量、正式通过、终态业务不重复 |
| p2-effect-tasks-default-lifecycle | 1失败207.65秒；3 SDK/3 HTTP200。logic正文1122、bindings正文412；produced90.504秒被原总期限取消，正文0、无finish_reason、已观察6621输出token；PG保存原绑定和interrupted调用，Run/业务HTTP0 | 完整规划、执行、只读纠正和默认模型回执；不能称length/8192 |

两批均显式隔离JSON/low profile、原180秒/8192，未修改全局MySQL模型。第二批在隔离项目启用现有一次只读纠正，但尚未到达该阶段。固定任务拆分已实现并局部验证，原截断/超时根因不能宣称解决；下一步查证技术声明是否承担了应由执行实际输出绑定的职责，以及原文/工具契约歧义，不再盲调参数或增加预算。

新增验收夹具只导出终态公共model_response_text/final_response及计数，避免保存私有推理；本批第二任务没有Run，新增终态导出路径尚未实测。v3普通工具查询已实测，场景选项不同组合尚未专门验收。清单见p2-effect-tasks-validation.json，调研见P2_EFFECT_TASKS_RESEARCH.md，完整P0—P5保持active。

当前按两类继续推进：已有实现仍待完整验收的包括模板/导入生产适用性、普通需求全量生成、需求影响业务质量、默认AI执行及正式回执、其他模式/App；仍需依据实际缺口补强的包括准备生命周期/unknown只读查证/执行中修订、UI selector修复评审和失败定位、复杂网络因果、API自动续期/Cookie/并发刷新、持续容量与AI流式指标、其他安全工具故障组合、全阶段HTTP故障恢复、历史迁移投影与两份旧Worker原件。不得将单项通过当成完整系统完成。


### 7.32 普通文档冻结流式传输、实际恢复与默认生成失败〔2026-10-11〕

先查普通全文生成、章节分组、生成任务/CLI、模型能力/stream_handler、原冻结范围及真实SDK。已有普通文档模块进度、独立重试及受控App验收不是新功能，本批补默认模型实测并修正新普通文档的传输入口。研究见P1_DEFAULT_GENERATION_RESEARCH.md。

首次真实默认请求1 SDK却2次物理发送，119秒取消，没有观察到响应头，PG保留invoking/call_outcome_unknown；旧记录不重置或重发。新普通文档按固定MySQL模型streaming能力启用现有provider stream，冻结response_transport_protocol=case-generation-stream-response-v1；缺字段的旧请求省略序列化，原哈希及非流式恢复不变，增量和非流式模型原入口保持。完整终态后才保存响应/校验/提交，部分结果不当草稿；不加新模型框架、依赖、表、预算或供应商特判。

| 实际验收 | 结果与范围 |
|---|---|
| 默认Step普通Markdown三模块，新流式任务 | 1失败147.60秒；1 SDK/1物理HTTP200，首chunk3.689秒，length/8192、公开正文0，原件已保存并SHA下载验证；第一模块response_validation_rejected，其余queued，草稿0；全局模型未改 |
| 旧请求/增量/恢复 | 48通过288.87秒，真实MySQL/PG/RustFS/Redis，含md/docx/pdf、未知不重发、原子草稿、scope/response篡改、版本/配置/预算及新重试；受控响应，不证明Step质量 |
| 新普通文档流式与非流式生产链路 | 2通过136.79秒，实际API、独立解析/生成CLI和四存储；三模块两完成一大响应失败，saved_response新任务恢复成功，仍仅3次HTTP，API重启/模板冻结/false-0-null/项目隔离/终态不新增发送成立 |
| SDK发送可观测 | 真实本地HTTP等待响应头：1 SDK/2发送，分别捕获ReadTimeout，最后APITimeoutError；原body字节相同，凭据/响应体不由探针保存。最终合同及真实SDK7通过6.80秒；适配器初次扩大60通过15门控跳过，非外部多供应商质量 |

三份方案同步，原始失败、stream终态字段计数、PG检查点、RustFS公开响应和受控恢复原件见p1-generation-default-*与p1-generation-stream-*，新清单见p1-generation-stream-validation.json。不是网络恢复即生成通过，不能把私有推理当用例、把未执行的其余模块算完成或把受控输出算默认质量。原试验仍未知、两次请求不证明服务端重复生成；新试验实际只发送一次，但不证明所有供应商/所有网络故障均无重试。

未改前端或数据库schema；前端不消费内部PreparedCaseGeneration新增字段，本批不跑前端构建、不声称新完整App验证。默认全文质量、独立恢复真实模型、跨模块业务充分性、更多格式故障、规划/执行正式回执及完整P0—P5仍未完成。下一步先复核生成职责及模型能力，不继续同参数盲重跑或扩大预算；正式回执仍需捕获公开终态原件定位，现有按“step”的措辞歧义尚不作为已证实根因修改校验。


### 7.33 正式回执结构化评估边界与预算耗尽真实核验〔2026-10-11，daab92f0〕

先读普通 Agent 图、`AgentCaseAssessmentService`、`AgentCaseReceiptService`、统一模型请求映射及 Step 官方 JSON 输出契约，再用实际默认 Step、受控规划、真实 Agent 工具循环和隔离 MySQL/PostgreSQL/RustFS/本地业务 HTTP 验证。尝试把 JSON 输出格式直接放进普通工具循环后，Step 返回了结构化空 `steps`，没有执行业务工具；该方案已撤回。普通执行继续使用现有工具调用协议，模板也不进入执行。

结构化输出只用于工具完成后的只读回执评估：请求无工具，使用 `agent_case_receipt` JSON Schema，`required=false`，由已有 provider profile 决定能否映射；普通图请求保持 `output_format=None`。保存响应后只读评估、固定身份和一次纠正仍复用原检查点/证据，不重新执行工具。

受控真实执行已发生两次创建、一次查询、两次清理，业务记录清空，PG公开快照保留工具成功证据；原图在 `max_iterations=8` 边界进入 `mode=budget_exhausted`，没有再发送模型请求。纠正服务按原预算门禁拒绝新增调用，终态保留错误和全部执行证据。旧批次另有一次只读纠正请求真实送达 Step，但传输超时失败；该证据只能证明请求持久化并尝试发送，不能称纠正成功。

本批回归：`tests/test_agent_case_session.py`、`test_agent_case_assessment.py`、`test_agent_case_receipt_correction.py`、`test_model_request_profile.py` **101通过、18跳过、50.63秒**。本批只收紧模型调用边界和证据导出，不提高普通工具预算、不修改旧运行、不重跑未知业务副作用。

仍未完成：默认 Step 在预算充足时的正式回执纠正成功、完整默认 AI 的多用例选取/排序/补全/执行/断言闭环、准备状态与未知副作用、运行中修订及其他模式/App；模板导入后的默认模型生成质量、需求变更业务评审、UI失败定位与selector评审、API续期/Cookie/并发故障、性能容量/流式阶段、安全工具扩展、全阶段HTTP故障恢复、历史迁移和旧Worker原件追踪。完整 P0—P5 仍为 `active`。

### 7.34 认证后默认 Step 最小用例闭环与回执纠正边界〔2026-10-11〕

依据 `P2_DEFAULT_STEP_MINIMAL_RESEARCH.md` 及真实默认 Step、独立规划/执行 Worker、隔离 MySQL/PostgreSQL/RustFS/Redis、本地 HTTP 目标。首份自定义 `operations/expect` JSON 用例复现 `api-test-runner` 参数格式歧义：Step 连续生成非 `AssertionSpec` 字段，运行时返回 `invalid_task_payload`，真实目标请求为0；该事实说明“模板/自定义 JSON 可被 AI 理解”不能仅凭注册 schema 推定。改用现有 `ApiTestTask/AssertionSpec` 结构后，真实目标 GET、HTTP 200 和 `ok=true` 均通过，但第一次正式回执多引用一个实际值，严格校验落成 `error`，没有篡改为通过。

在项目策略启用既有 `case-receipt-correction-v1` 后，真实最小闭环 **1通过75.04秒**：规划完成、按项目冻结唯一用例、审批后调用目标一次、默认 Step 直接返回合法正式回执并保存结果 `passed`；该次结果没有 `receipt_correction` 字段，未证明真实默认模型进入纠正调用；同一终态调度重新投递，目标请求仍为1。此次没有改生产代码，保留失败与通过原件：`p2-default-minimal-case-live3/`、`p2-default-minimal-case-live5/`、`p2-default-minimal-case-live6/`。

结论：认证后的默认 Step 已在最小只读链路证明“项目用例选取→冻结→审批→Agent工具→合法正式回执→结果→终态幂等”可运行；多候选排序/补全、准备/清理、未知副作用、修订、复杂 API 依赖、真实回执纠正中断恢复、其他模式和完整 App 仍未验收。回执纠正当前默认关闭，关闭时模型回执结构错误可能使已成功业务请求落成 `error`；是否设为新项目默认，需另行评估额外模型调用预算与供应商能力，暂不擅自改全局默认。


### 7.35 默认 Step 真实纠正与发送前进程中断恢复〔2026-10-11〕

先重读目标附件、AgentCaseReceiptService、正式评估请求、实际默认SDK探针和既有受控进程恢复测试。前批live6无纠正字段，已修正7.34和调研中的过度结论。live7确实保存receipt_correction=response；持久invoking但无响应时现有服务明确要求error/recovery_required，不得新发纠正或重复业务。未改生产恢复逻辑、全局模型、供应商框架、项目默认策略、原8轮/8192或SDK重试。

| 实际验证 | 证据与边界 |
|---|---|
| 默认Step最小用例实际纠正 | p2-default-minimal-case-live7：1通过103.97秒；目标GET一次，原执行2调用加纠正1调用，无工具纠正实际HTTP200，结果passed，终态重投业务0。RustFS请求/响应大小与SHA下载核对 |
| 纠正发送前API进程中断 | p2-default-receipt-process-live5：1通过385.93秒；最终加强原件/清理/请求数证据后的live6：1通过372.83秒。默认Step真实执行及非法回执触发纠正；意图入PG/原件入RustFS后，在测试进程请求钩子发送前屏障杀掉实际API进程树。重启、自然RunItem/资源租约过期、生产清理和恢复Worker，结果error且recovery_required，固定原invoking身份不改；目标仍一次GET，恢复及终态无新模型请求、无新增业务 |
| 受影响回归 | SDK实际本地HTTP/取消/释放/重试、回执、评估、Agent会话、模型profile：110通过18门控跳过59.67秒；新增屏障取消不发HTTP、释放后原字节仅发一次；compileall通过 |

回执纠正原件完整请求保存，响应只导出公共text/tool_calls/summary投影；不把供应商私有raw_response存到本轮公开工件。live1—live4的夹具失败JUnit保留；Windows venv launcher有实际子进程，必须杀 owned tree，不能把launcher退出当API终止。记录见P2_DEFAULT_STEP_MINIMAL_RESEARCH.md、p2-default-receipt-evidence.py和IMPLEMENTATION.md。

当前分类：**正式回执纠正已有实现，本批默认模型最小API成功及发送前进程恢复已验**；供应商接收请求后断网、保存响应后中断、默认UI正式回执、复杂多用例和完整App仍待验。**仍需补实现/依据实际缺口补强**的准备生命周期、unknown只读查证、运行内修订、selector修复/失败定位、复杂网络因果、API自动续期/Cookie/并发刷新、持续容量/AI流式指标、历史迁移投影仍保持。普通文档生成业务质量、需求变更广场景/用户业务评审、其他模式及安全工具组合、全阶段SDK/HTTP、两份旧Worker原件仍未完成。下一项继续默认AI的UI场景知识使用与正式断言，先对照实际录制/场景/工具及已有受控验收，不将本批API通过替代UI。


### 7.36 默认 UI 场景知识实测、参数契约与正式断言未通过〔2026-10-11〕

先重读完整目标及录制/场景/知识服务、工具暴露、UI/API Prompt、正式评估与真实默认验收。UI模式/Agent/Skill未登记knowledge-rag、cases仍要求探索入口，实际边界2失败1通过。同步登记与按execution_basis选UI用例Prompt，权限、项目隔离和自由入口保留；配置仍MySQL，业务/任务/版本/结果PG，原件RustFS，未新增表、依赖或供应商特判。

| 实際验证 | 结果与边界 |
|---|---|
| 真实默认UI首次 | live1：1失败128.63秒；已读取固定场景并真实点击。command=open/args=[URL]被丢弃动词，后续等待资源，原8轮耗尽，正式error；未启动清理CLI属验收生命周期缺失 |
| 命令/权限/真实浏览器 | 捕获参数专项复现后统一解析，保留完整args及导航安全规则。真实生命周期等30通过21.36秒；最终分开命令实际图门禁、浏览器和ToolJob17通过7.01秒 |
| 真实默认正/负及回执帧 | live2：2失败697.79秒；知识和实际点击均发生，正式error。完整JSON前有说明，触发额外纠正；原非流式3次HTTP尝试超时。夹具误读已删除的本地副本，改为API/RustFS原件及stdout对账；两项失败不改判 |
| 单一完整JSON块/回归 | 沿现有规划解析实现识别显式唯一块，类型和实际证据校验照旧，多块/截断/工具请求拒绝。61通过17门控跳过30.21秒；最终扩大159通过18门控跳过53.53秒；各批重叠不相加 |
| 修正后真实默认UI | live3：1失败330.55秒；真实规划CLI、执行CLI、API、MySQL/PG/Redis/RustFS，固定场景→新页面初始0→点击一次→当前1，4份命令原件API/RustFS一致且stdout对应真实ToolJob。候选路径[output,stdout]不存在，并以整个JSON文本对比标量，评估正确拒绝；纠正HTTP失败，正式仍error；尚未走终态重投验收 |

调研与失败原件见P5_DEFAULT_UI_RESEARCH.md及p5-default-ui-*。初次边界夹具身份/字段遗漏、上传后本地文件误读和本轮日志变量名错误均有失败记录，后两项已修正并复跑；不能将夹具错误当系统通过或系统缺陷。供应商私有推理不导出；第二批纠正原件经SHA/size验证，响应只公共投影。生产清理Worker已用于后续实际运行，没有扩配额或手改锁。

当前分类：**UI知识已有实现，本批真实默认模型已实际读取固定版本/别名并操作**；**默认UI正式结果与终态重投仍未验收通过**。下一步依据command eval的stdout与evaluate_js结构化result、工具消息包装/持久输出根的实际差异完善可引用观察及说明，再验正/负和终态。只读纠正非流式超时需独立核对固定模型传输策略，不再同参数盲重跑、提高预算或放宽错误证据路径。

完整P0—P5仍active。仍待验收的普通文档默认生成质量、模板/导入生产适用性、多候选排序/补全/正式执行、需求变化广业务评审及环境部署、其他模式/完整App不能以本批代替。仍需依据缺口补强的准备生命周期、unknown只读查证、执行中修订、selector修复/失败定位、复杂网络因果、API自动续期/Cookie/并发刷新、持续容量与AI流式指标、安全故障组合、全阶段SDK/HTTP恢复、生产历史迁移投影及两份旧Worker原件继续保留。没有新增前端变动或新完整App构建验收声明。

### 7.37 持续会话结构化观察、默认UI正负正式结果及终态〔2026-10-11〕

基线0907c39f。依据原native CLI/MCP、工具消息和持久证据根、CaseExecutionCheck校验及上批真实失败调研。eval在格式化前保存Playwright原类型，MCP与transcript直接传递result；原stdout/stderr不变。JSON.stringify仍是字符串，不猜类型，不多执行浏览器、不修正错误路径。公开工具及UI cases Prompt说明args单元素表达式、持续会话和独立evaluate_js区别，路径从result开始。原确定性比较只允许一个实际值的validator未变，字段description与公共契约补齐；普通Prompt v5、场景Prompt v6，历史请求/检查点不改。

| 实际验证 | 结果与边界 |
|---|---|
| 原缺口复现 | before夹具筛选原件空白格式错，保留失败；修正后reproduced在真实Chromium明确缺少result。false/0/null与同名字面字符串不能依靠stdout反推 |
| typed观察与回归 | 实际对象/数组/标量/null/空字符串/JSON.stringify/元素引用、同会话及transcript对账；早期31通过、后续117通过4跳过。最终214通过19门控跳过79.32秒，各批重叠不相加 |
| 默认live4 | 1失败119.96秒；知识、初始0与真实点击已发生，后续未引用command表达式被分成locator，原预算耗尽，正式error |
| 默认live5 | 1失败1通过462.86秒；负项期望2、实际1，正式failed且终态新增业务/模型请求均0；正项equals引用动作结果和计数两个值，校验正确拒绝，只读纠正3次HTTP超时，正式error |
| 新契约默认live6 | 正项1通过、1排除187.73秒；实际默认规划/执行CLI、生产API/清理CLI、MySQL/PG/Redis/RustFS，固定场景/别名→新页面初始0→点击一次→实际1→正式passed；终态新增业务/模型请求均0 |

四个运行的17份浏览器工件现场由API/RustFS下载并核对，持续命令stdout对应实际ToolJob。live5正项纠正原件另核SHA/size，响应只公共投影，清理仅本批拥有的2个对象。原默认8轮/8192、权限、模型接入与全局配置保留；没有新表/依赖/前端变动或新完整App构建声明。首次受控生命周期夹具无参数eval与历史Prompt恒等测试已按明确契约修订，失败日志保留；最终复跑通过。语法编译退出0是附加检查。

当前分类：**固定场景知识读取及简单默认UI正/负正式断言与终态重投，已按上述跨批范围实际验证**。**已有实现、尚需验收**：多候选项目召回/排序/补全/执行、复杂场景/其他模式/完整App、普通需求文档默认生成质量与模块恢复、广业务影响评审/环境适用性/生产部署。模板/Excel/JSON完整App专项已有6.87，不再将其列为完全未做。DeepSeek公开页采集已见7.22，完整业务质量仍未完成。

**仍需补实现或依据真实缺口补强**：只读纠正阶段固定传输策略（非流式超时仍复现；既有generation_pipeline.invoke_frozen和ModelRuntimeService.stream_handler可作依据，必须冻结新阶段策略并保留旧恢复，不能按供应商硬编码）；准备生命周期/unknown只读查证/执行中修订，selector修复评审/失败定位与复杂响应因果，API自动续期/Cookie/并发刷新，持续性能容量/AI流式阶段指标、安全故障组合，全阶段SDK/HTTP恢复与生产历史迁移投影。两份旧Worker原件仍未恢复。完整P0—P5保持active；没有用本批简单UI取代其余目标。

调研、原件与审计见P5_UI_OBSERVATION_RESEARCH.md、p5-ui-observation-*及IMPLEMENTATION.md；三份方案同步。仅提交本批白名单，不推送/PR。

### 7.38 只读纠正冻结流式策略、真实响应失败与生产恢复〔2026-10-11〕

基线cd85f815。查证现有ModelRuntimeService.stream_handler、generation_pipeline.invoke_frozen与CaseReceiptCorrectionCheckpoint。新纠正阶段按原MySQL模型streaming能力冻结response_transport_protocol，调用原统一适配器；关闭能力继续非流式。协议属于PG业务检查点，原件在RustFS，未新增可编辑PG配置、模型专用客户端或表。旧意图不能重发；保存的旧响应权威回读，不因今日模型/策略变化重写。公开路径说明明确数组索引也是字符串，普通Prompt v6、场景v7；校验保持，不转换错误候选。

| 实际验证 | 结果与边界 |
|---|---|
| 五传输SDK/SSE | Chat、Responses、Anthropic、Google和LangChain Chat的完整/缺终态/length十五参数，加旧真实检查点兼容，16通过24.78秒。实际POST/GET/DELETE后纠正及重投未重复业务；模型回复由受控HTTP提供 |
| 默认default-fixed | 1失败106.66秒；流式回复完成，但数组路径索引为数字0，正式校验拒绝；未自动修复或重判 |
| 默认default-paths | 1失败113.57秒；HTTP流正常结束但finish_reason=length，原8192额度耗尽，公共正文0。只记录私有字段计数，不保存私有推理；传输成功不等于回执可用。该请求保留当时冻结文本，后续源码句子整理未冒充默认通过 |
| 最终回归 | 十模块159通过5门控跳过124.63秒，含实际存储回执链路、非stream能力、历史恢复和真实数字路径失败样本；较早158通过5跳过不累加 |
| 生产流式进程恢复 | 2通过693.48秒；实际终止owned Windows API进程树，原自然90/300秒租约后生产清理CLI及新Worker恢复。invoking无保存响应正式error/recovery_required且不重发；response已有保存响应正式passed。恢复期间修改当前模型/策略/预算，仍保持原模型响应、原8轮和累计5次；均实际三业务操作、终态额外模型/业务0 |

初始夹具失败保留：supplier过早访问agent、Google显式重试不支持、默认registry key断言。这些未宣称生产缺陷，原适配器限制未放宽。compileall退出0；前端不消费内部检查点协议，没有前端改动/新构建或新完整App通过声明。后续按用户安排暂缓外部模型验收，不增加原8轮/8192、不缩短真实租约、不重发未知。调研与证据见P2_RECEIPT_STREAM_RESEARCH.md及p2-receipt-stream-*；最终审计另核历史、保护文件、原件及三份同步。

当前剩余项分开登记：

| 分类 | 未完成范围 |
|---|---|
| 已有实现，尚需完整验收 | 默认AI多候选项目选取、排序、补全、执行及断言闭环；普通需求文档默认全量生成质量与模块独立恢复；复杂UI/其他模式/完整App组合；需求影响更广业务、格式与环境适用性、用户业务评审和生产部署；默认流式只读纠正的正式成功 |
| 有局部机制，仍需依据实际缺口补强并验收 | 准备状态完整生命周期、未知副作用只读查证、运行内计划修订；selector修复评审/失败定位与复杂响应因果；API自动续期/Cookie/并发刷新；持续性能容量与AI流式阶段指标；其他安全工具/故障组合；全阶段SDK/HTTP恢复和生产历史迁移投影 |
| 历史证据待补 | 两份旧Worker汇总原件仍未恢复；保留缺失声明，不以新测试替代旧原件 |

模板/Excel/JSON完整App已有6.87，DeepSeek公开页实际采集已有7.22，Docker原件/响应验证和安全报告也已有专项；不能把它们重新列为完全未做，也不能据此宣布完整目标完成。完整P0—P5保持active。仅提交本批白名单，不推送/PR。


### 7.39 API Worker刷新传播、公开脱敏及真实存储缺口〔2026-10-12〕

基线15d8687b，按用户决定继续其他模块，本批零外部AI调用。先读接口课程6.2、既有认证研究、两协调器/生产runner/ToolRuntime/PG存储/OutputSafetyPolicy。最初判断仅需统一传播；真实持久化专项进一步发现内部凭据传输架构缺口，研究结论已修正。

| 实际验证 | 结果与边界 |
|---|---|
| 传播缺陷复现before | 2失败2通过3.52秒。直接执行正确；Worker刷新后仍用旧凭据、无依赖public收到凭据。真实owned HTTP，控制派发只替代模型/进程启动 |
| 共享传播与公开脱敏 | 直接算法抽为共享函数，完成来源task_id传入Worker；更新未来auth/明确依赖任务，保留无关read任务。具体credential_session整体脱敏，普通usage.token保持，不关闭脱敏 |
| 最终回归safety-final | 九模块175通过1门控跳过28.27秒，含真实文档依赖HTTP/PG/RustFS。前批50通过1跳过与48通过2跳过重叠不累加 |
| 消费方补验consumers | 六模块24通过3门控跳过13.94秒，涉及runner契约、安全输出/审批/能力及报告，跳过为真实存储/完整App门控，不当作通过 |
| 真实ToolRuntime/PG Worker专项 | captured：1失败4通过9.03秒，Authorization已脱敏而token字段仍有值；redacted：1失败5排除6.97秒，凭据容器已脱敏，登录200、刷新401、profile跳过。公开投影不能用于可执行凭据恢复；真实存储闭环仍未通过 |

没有虚构endpoint、自动401写重试、增加预算或更改模型接入。原共享算法仍更新未来auth任务，本批未证明任意多认证流隔离。受控Worker启动不等于独立Worker/默认AI验收。基础设施恢复使用原PG数据目录及既有RustFS脚本，未改配置/重置历史库；早期环境和夹具错误均保留。

**本批已验证**：两协调器来源传播一致、真实HTTP显式刷新后的受保护调用、无依赖public不携带凭据、终态run_all零新增HTTP、具体凭据容器公开脱敏。**仍需补实现**：正常ToolRuntime脱敏与PG持久化后的内部凭据传输及恢复。父子SessionStore已有正式project_id和父session/turn来源，ChannelCredentialCodec现有Fernet当前available=True；这些是后续研究依据，尚未实现API凭据vault。需持久化不透明引用，由可信executor按实际项目/派发关系解密，Prompt/公开Job/报告不保存明文，验证缺/错密钥、跨范围拒绝、服务重建及终态零重发；不能从公开token还原认证或直接关闭脱敏。

完整准备/unknown/计划修订、复杂UI失败定位/响应关联、自动续期/Cookie/并发刷新、持续性能/AI流式指标、安全故障组合、更多业务/部署/生产历史迁移及两份旧Worker原件仍保持未完成；默认生成/多候选排序补全执行/纠正正式成功等按用户安排待真实AI验收。没有新表/依赖/前端修改，没有新前端构建或完整App通过声明。研究、全部失败及SHA审计见P2_API_WORKER_AUTH_RESEARCH.md、p2-api-worker-auth-*。三份方案同步，只提交本批白名单，不推送/PR，完整目标不标完成。


### 7.40 API内部凭据引用、父报告与终态真实恢复〔2026-10-12〕

基线70a1daf8，上一轮为已提交实际进展。本批依据SessionStore真实父子身份、协调器先注册receipt、既有Fernet和PG业务存储调研，补上7.39的内部传输缺口。新增Session派生的两个PG业务表保存加密凭据/固定Worker输入，不新增依赖或可编辑PG配置；main初始化并注入。配置延续原MySQL体系/现有密钥管理，未改模型或测试模式。

新Worker Prompt只携带dispatch_ref；父session/turn/task/project及真实receipt匹配后可信executor取固定task和凭据。模型不能通过叠加task/body/checkpoint改变引用。凭据先加密入库，父协调器内部回读，公开输出不承担私密传输；父模式状态仅保存credential_ref。旧一次性task合同与历史快照保留，不原地重写。失败均有安全原因码和结构化日志。

| 实际验证 | 结果及明确边界 |
|---|---|
| 十六模块types-final | 205通过21门控跳过61.33秒。核心及public两参数真实ToolRuntime/PG/HTTP；login、refresh、profile均200，独立public没有Authorization；新服务读旧引用、Job终态重投零HTTP |
| 引用/恢复边界 | 缺/错密钥、覆盖固定输入、错误turn/task/role/project/父会话/缺引用、固定派发内容冲突、错误/不存在/跨会话Job拒绝；旧记录及公开输出不被拒绝调用改写/回泄。生成凭据不在Prompt、公开状态/Job或PG密文行中 |
| 父模式和报告 | 实际父ToolRuntime→受控启动的生产runner→父报告通过。两份报告以既有PG inline_text正文/索引保存，新服务回读；report-originals补采1通过7排除9.56秒，原JSON/Markdown现场保存及SHA/size核对；没有强改为RustFS |
| 正式动作/审批回归 | 首次遗漏owned证据目录，15个场景保存证据时KeyError、17通过；补正确QA_STATEFUL_RESEARCH_EVIDENCE_DIR后四模块32通过57.50秒，实际PG/原Agent操作、unknown、改call ID、回读、审批预算保持 |
| 独立main生产启动/重启 | 原HTTP origin专项1通过22.66秒，实际API进程与MySQL/PG/Redis/RustFS原件；新增表初始化不破坏冷启动/历史检查。不能据此说独立API Agent Worker已验收 |

初次receipt夹具错误、普通调用未指定原Job、旧冻结Schema断言、RustFS报告假设均保留。按原Job恢复还真实发现非case路径重复HTTP〔3变6〕：已补终态权威回读及scope/input校验，不重领终态。Worker合并已验证字段时先序列化会把AssertionSpec降为dict，父报告出现三个警告；保留原验证字段类型，实际类型断言和最终无警告验证通过。此前204/205/23通过批次重叠不累加。compileall退出0为附加检查。

**本批已实现且在上述真实范围验证**：加密内部传输、固定派发、范围隔离、父状态/报告、原Job终态回读。**已有实现尚未验收**：新的真实独立Agent Worker/默认AI鉴权闭环、完整App及更广业务模型质量。**仍需依据缺口补强**：自动过期/401续期、Cookie获取和并发刷新、任意多认证流隔离、加密保存边界进程kill、生产历史明文凭据迁移审计；其余准备/unknown/修订、复杂UI/响应因果、持续性能/AI流式、安全组合与生产历史投影继续保留。两份旧Worker原件仍缺失。

研究与全部失败见P2_API_CREDENTIAL_REFERENCE_RESEARCH.md和p2-api-credential-reference-*。无新增前端构建、外部AI或完整系统通过声明。三份方案同步，旧工件/保护文件/原件字节及秘密扫描另附validation；仅提交本批白名单，不推送/PR，完整目标不标完成。


### 7.41 独立 API Agent Worker、项目身份与子审批生命周期〔2026-10-12〕

基线8e7ebeb5，已提交7.40的加密内部引用、父报告和原Job终态回读。研究先见P2_API_AGENT_WORKER_RESEARCH.md及Agent协议鲁棒性课程；本批保留原API/Redis/CLI/Agent图/权限，只由本地HTTP模型服务提供可控回复，不调用外部AI、不改默认模型。

已修复三个实际契约缺口：PG Session项目UUID与字符串command直接比较导致同项目提交和恢复误拒；存储边界按已有SessionStore统一转换，异项目仍拒绝。API子派发漏mode_key，真实子会话落default隐藏runner；按安全模式现有派发传api_testing。父工具经approval_resume执行时原持久子调度找不到父任务；仍按原session/turn与running/claimed查找，将approval_resume纳入已有识别。

| 实际验收 | 结果与范围 |
|---|---|
| 项目身份/父任务合同 | 最终真实PG身份专项及模式/审批父身份合同2通过；同项目/无项目提交、同项目恢复、异项目/缺项目拒绝、恢复幂等、拒绝无新增Task/Outbox；已完成或其他turn不能成为父任务 |
| 十七模块相关回归 | 206通过21门控跳过3排除60.51秒；包括真实HTTP/PG加密引用、父报告、旧Job终态等，3排除是新的独立完整验收/身份专项，另单独执行，不冒充通过 |
| 既有Agent与持久调度回归 | 默认Redis关闭连接导致首批47 setup错误、14通过1跳过；换已有隔离存储夹具后61通过1跳过35.96秒，最终源码再跑61通过1跳过35.53秒。均保留原失败；不是改生产Redis配置 |
| 最终独立完整API验收 | 四项2通过2失败50.39秒；同进程子Agent仅login200，持久子任务login200→refresh401〔没有Authorization〕，profile未执行。实际父报告两份均0完成/2失败/1跳过；本地模型HTTP5/6次，fixture_errors空，外部AI0。未到达独立终态重投和API重启断言 |

实际子会话mode_key/project_id已正确，独立失败落在生命周期：waiting_approval被ApiSubagentCoordinator当成终态，无task_result即先合并失败/定稿；随后代理审批继续的子登录没有合并进原父campaign，下一认证任务得不到凭据。**仍需补实现**：父campaign对子审批的等待/恢复与权威结果合并。后续先核原代理审批、AgentTask等待/恢复和固定派发，不能仅延长轮询、删一项终态或自动批准。已执行HTTP不得重新发送，原失败报告保留。

RowMapping修正初次500、夹具未显式Agent/缺mode_key、普通审批202与代理200误断言、审批过早400、缺原工具输出等失败均保留；不放宽最终[200,200,200]或报告完成3项断言。受控launcher专项已通过的范围保持，但不能替代这条真实独立链路。

当前未完成分类：

| 分类 | 未完成范围 |
|---|---|
| 已有实现，尚需完整验收〔外部AI按用户安排暂缓〕 | 默认AI项目多候选选取/排序/补全/执行/断言闭环；普通需求文档全量生成质量及模块独立恢复；默认流式只读纠正正式成功；复杂UI/其他模式/完整App组合；更广需求影响业务/格式/环境适用性、用户评审与生产部署 |
| 已确认缺口或局部机制，仍需补强 | API父子审批/结果合并及独立完整鉴权；自动过期/401续期、Cookie、并发刷新/多认证流；准备状态生命周期、unknown只读查证、运行内修订；selector修复评审/失败定位与复杂响应因果；持续容量/AI流式指标、其他安全工具和故障组合；全阶段SDK/HTTP及进程故障、加密边界kill、生产历史迁移投影 |
| 历史证据待补 | 两份旧Worker汇总原件未恢复，保持缺失说明 |

模板/Excel/JSON完整App已有6.87，DeepSeek公开页实测已有7.22，简单默认UI正负和终态已有7.37，流式纠正进程恢复已有7.38，Docker/安全报告已有专项，不再列为完全未做。新源仅三个相关文件，无新依赖/schema/前端改动。研究、公开PG快照与全部失败、SHA及三方案同步另见本批validation；不推送/PR，完整目标不标完成。

### 7.42 多模型SDK故障边界及当前剩余范围〔2026-10-12〕

基线7fe85eca。外部AI按用户决定暂缓。本批先深入查API父子恢复：SessionService代理审批只转发到子会话，协调器会持续镜像子审批；父自动通知续跑限于coordinator会话/Agent，API父Agent不在其中。ApiTestingModeRuntime仅在campaign_ready执行，恢复中的task_running没有完整续跑入口；AgentTaskDispatcher不能仅靠数据库等待状态名称提供API暂停恢复。这是跨父任务、子通知、原工具结果/检查点及预算的生命周期缺口；本批没有修改它或宣布独立认证通过，也不重写7.41失败报告。

最终补核协议：Anthropic始终使用`AsyncAnthropic.client.messages.create`，辅助网络等待函数命名改为`await_provider_io`，不改为OpenAI Responses。受控HTTP夹具曾多加`/v1`，已修正并逐请求校验：Anthropic`/v1/messages`、OpenAI Responses`/v1/responses`及各自正文。协议最终87通过86.51秒，含原61参数及已有客户端/LangChain26项；共用正常夹具修正后十模块最终152通过3门控跳过53.84秒。前序55通过81.14秒仅补采响应序号/hash，不作为正确路由的证明；历史原件保持，重叠批次不累加。

随后按完整目标推进SDK故障验收。研究见[P0_SDK_FAULT_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_SDK_FAULT_RESEARCH.md)。五条传输保留：原生Chat、Responses、Anthropic、Google及LangChain Chat。实际官方SDK/TCP证明Anthropic、Google的流断开和超时会抛出底层网络异常，未进入既有正式失败。现于原适配器的SDK请求/取流边界转换httpx/httpx2 RequestError，保留取消和消费回调错误，日志/返回增加安全cause_error_type；不改变模型写作、正文、工具或SDK重试策略。当前Google配置未启用重试，429/503只发一次并返回失败，不为统一而强加其他SDK策略。

| 实际验证 | 结果与边界 |
|---|---|
| 初次真实HTTP | 24通过6失败49.43秒；4个真实裸异常、2个本批错误Google重试假设；原件保留 |
| 首次修复后 | 28通过2失败49.90秒；剩余是断言遗漏LangChain原异常名称，生产失败分类正常，原件保留 |
| 扩展功能 | 61通过82.97秒：55项真实运行时HTTP，4项真实消费回调边界，2项原MockTransport。流式7种、非流式4种各5传输；断开/超时/取消后无重发，重试成功正文无重复，403不重试 |
| 既有十模块回归 | 152通过3门控跳过52.89秒，含真实本地HTTP完成、终态、请求映射/项目追踪、SDK观察和审批预算；3项真实PG审批参数未启用 |
| 附加检查 | compileall退出0；本批无前端改动/构建，无新依赖、表、配置项或公共路由；源码引用、旧原件和4个保护文件SHA、三份方案、配置凭据及新原件另见p0-sdk-fault-validation.json |

配置保留MySQL，业务保留PG，原件沿原机制。当前Google为httpx后端；可选aiohttp后端及其他SDK版本未验收。本批是协议与故障边界的实际验证，不代表默认模型质量、完整Agent执行或全阶段追踪完成。

**当前仍未完成，按实际范围分类：**

| 方向 | 已有实现但未完成验收 | 仍需补实现或补强 |
|---|---|---|
| 模板/文档生成/导入 | 普通需求文档默认全量生成质量、模块独立恢复的完整流程；模板与Excel/JSON完整App已有6.87 | 独立恢复现场暴露的缺口按原模块任务机制修复；不重做已通过的导入页面 |
| 按用例执行/回执 | 默认AI多候选项目选取→排序→补全→执行→断言；默认流式只读纠正正式成功；其他模式/完整App组合 | 准备生命周期、unknown副作用只读查证、执行中计划修订 |
| UI录制与知识 | 简单DeepSeek公开页及默认UI已有专项；复杂页面和跨响应因果完整验收 | selector修复与评审、失败定位/报告及复杂响应关联的实际缺口 |
| Agent API | 独立Agent完整鉴权尚失败；已完成加密引用/公开脱敏及局部真实HTTP验收 | 父子审批挂起/原任务恢复/权威结果合并；自动过期/401续期、Cookie、并发刷新/多认证流、加密边界进程故障 |
| 性能/安全 | Docker原始输出/报告已有专项；更广工具与产品组合未完成 | 持续容量、AI流式阶段指标及更多安全故障组合 |
| 需求变更/追踪 | 更广业务/格式/环境的默认模型质量、用户业务评审、生产部署适用性 | 实际部署和历史迁移中确认的缺口；首期不部署Docling属于既定范围，不算遗漏 |
| 重复请求/恢复/交付 | 本批五传输故障范围已通过；全阶段逻辑调用/SDK/物理HTTP贯通未完成 | 更多断网/进程故障、生产历史数据迁移与投影核对 |
| 历史证据 | 两份旧Worker汇总原件仍缺失 | 继续查找原件；不能用新验收替代历史证据 |

完整P0—P5保持active。外部AI验收依用户安排暂缓，其余模块继续推进。仅提交本批白名单，不推送/PR。

### 7.43 API审批恢复的现场复核与修复路线纠正〔2026-10-12〕

基线742331d。深度调研与原件见P2_API_APPROVAL_RECOVERY_RESEARCH.md及p2-api-approval-wait-*。本批保存真实main/独立CLI、隔离MySQL/PG/Redis/RustFS和本地模型证据，增加批准前的PG报告/Session观测。默认完整断言仍必须login→refresh→profile200、完成3项、重启历史、终态额外HTTP/模型0；外部AI0。

before、observed、delay-final、delay-final2、resume-final完整现场均2失败2排除；分别57.63、54.07、54.96、58.73、52.35秒。包括自己观察时机错误和普通审批误取proxy字段KeyError，原件均保留。probe-final2通过52.80秒只是普通父审批无报告的窄探针，临时提前return已移除；**不能宣称子审批暂停或完整鉴权通过**。父普通审批尚未派发子任务时的正确等待，不否定7.41确认的子审批被当终态缺陷。最终现场HTTP0，真实PG父报告0完成/2失败/1跳过，子approval pending。

生产实验layer1/2/3均2失败2排除，142.66/54.17/49.78秒；默认同进程目标HTTP0，持久子仅login200。等待信息被checkpoint重建清掉、审批Runtime未保留变更context/等待状态、以子proxy调用信息恢复父工具等问题说明单模块if与进程内callback不是正确根因修复。停止该路线并撤回4个API生产文件到已验742331d；没有提交未验代码、没有改权限/预算/模型配置、没有覆盖失败结果。逻辑专项45通过19跳过不能掩盖现场失败。恢复已验生产代码后6模块46通过22门控跳过15.25秒，live门控未开启，不当成独立API通过。

下一实现需在原ToolJob/Session snapshot建立持久等待边界，保留父call/job/已批准scope/固定输入、更新后的campaign context、child身份、原预算；子结算通过既有outbox/lease投递原snapshot继续，权威读子ToolJob后再续父工具。不从普通新消息创建新预算，不自动重发已执行或unknown HTTP。默认同进程与持久子两路径、早到/重复通知、拒绝、重启/kill、终态0调用均实际通过后才能完成。本批只交付调查、观察增强与证据，API恢复仍需补实现；其他P0—P5未完成范围保持。

### 7.44 模板与 Excel/JSON 导入当前代码复验〔2026-10-12〕

基线05688163。重新阅读模板、导入服务、解析进程、路由和已有完整产品测试后，复验既有能力，不新增另一套实现。真实存储服务测试6通过8排除10.70秒；真实Chromium组件rows/split/json三布局3通过12.74秒；构建版完整App、独立main API和重启、隔离MySQL/PG/RustFS/Redis的完整产品1通过66.89秒。覆盖模板MySQL修订、真实Excel/JSON原件下载、预览错误行、冲突、提交重放、评审启用、V2草稿、模板修改后旧版本不变、跨项目隔离和重启读回。

这是6.87/7.05既有验收的当前复验，不能称新增功能，也不替代普通文档生成或真实AI质量验收。普通文档受控模块/独立恢复已有6.97证据；默认模型质量、容量和更多故障仍待验。原件、页面、PG、网络和独立进程输出21份及哈希见p1-case-import-full-product-current2/、p1-current2-validation.json；pytest终态在本会话工具输出，本轮未另存原pytest日志/XML，不伪造。无生产代码/配置/表/依赖修改，无外部AI调用；完整P0—P5保持未完成，下一步转向7.43确认仍缺实现的API原工具等待/恢复。

### 7.45 原 API 子 ToolJob 结果回读〔2026-10-12〕

基线cdf51dea。依据P2_API_RESULT_READBACK_RESEARCH.md调研：子ToolJob已结算与聊天落库是不同动作，不能依赖聊天文本判断工具是否完成；恢复running原来直接失败，未读原子Job。本批新增worker_result_reader，沿既有加密派发/receipt身份核验读取结算Job，生产ToolRuntime透传原ToolJobService。恢复原完成结果和auth_ref、重建依赖输出，不重发已完成HTTP；派发先记录child ID。恢复读取受既有超时共享截止时间约束。没有新增表/配置/依赖或改变模型协议/权限，进程内checkpoint不称持久snapshot。

实际复现reproduction 1失败9.45秒：真实ToolRuntime/隔离PG/MySQL/Redis/RustFS/本地HTTP，子聊天从未写入，原login200但父三结果None。修复后public-final 1通过10.07秒：login→refresh→profile200、三任务completed、三子聊天空，重建全部running任务恢复completed，额外HTTP0，跨父拒绝；原公共ToolJob及child身份投影落盘，随机凭据未导出。身份/请求/attempt/歧义/未结算/零值依赖和原恢复等deadline-final 39通过1门控跳过14.43秒，12模块regression 86通过3门控跳过48.98秒；重叠批次不累加。compileall退出0仅附加检查。

**完整独立Agent仍未通过**：agent 2通过2失败57.04秒，同进程/持久子都仅login200，完整目标应为login/refresh/profile200；后续API重启/终态断言未到达。before、verified、evidence-final等夹具及证据导出失败全部保留，不用后续通过覆盖。外部AI0，受控子启动专项不代替独立Agent/默认模型验收。

本批完成原结果回读基础，**仍需补实现**7.43父工具持久等待/原snapshot/outbox继续与自动合并；现有子waiting_approval终态及过早报告结算未修。其他默认AI多候选用例执行、准备/unknown/修订、复杂UI、容量/安全、业务评审/部署/历史迁移等完整P0—P5范围不缩减。三份方案同步，原件、历史/保护文件和敏感材料核对见p2-api-result-readback-audit.py/validation。仅提交本批白名单，无push/PR，完整目标保持active。

### 7.46 原工具子任务等待、快照续跑与独立Agent闭环〔2026-10-12〕

基线2df2e1ca，依据P2_TOOL_CHILD_WAIT_RESEARCH.md及7.43已确认跨层缺失实现。本批新增ToolJob/ToolExecution非终态waiting_children；Session沿原interrupted可恢复快照，pending_turn.waiting_tool区分子等待与人工中断。原父Job固定call/input、子身份及服务端授权检查点保留，PG条件停驻/领取避免取消复活和竞争所有者被覆盖。Runtime在普通工具与父审批后保存原快照；子通知、提交后核对和已有reaper从PG权威重读，通过原snapshot幂等manual_resume/outbox继续，先读原子结果和凭据再派发依赖；等待期间不构造最终报告、不再次调用模型。沿原预算与安全政策，无新表/依赖/配置，不修改模型协议。前端工具状态合同、两面板及中英文标签同步。

**已实现且本轮实测**：最终fixed campaign独立main/Agent CLI同进程子与持久子两种路径2通过2排除177.71秒，真实login→refresh→profile全200，父唯一报告3完成/0失败。子审批等待期间重启API，原snapshot/graph_state保持、业务/模型HTTP增量0；三次子等待父模型计数均1/8，最终2/8。终态重投原Agent消息任务0业务/模型HTTP，最终重启任务历史投影一致。随机凭据不在公共请求/Session/Job及密文原文中。first/restart前序成功及7.41/7.43/7.45失败全部保留，重叠结果不累加，不覆盖旧报告。

**附加验证**：10模块56通过4门控跳过25.61秒；原五模块owned隔离Agent回归61通过1跳过41.23秒。PG竞争/恢复边界12通过7.60秒；最终owner/回读/预算/审批/心跳38通过3門控跳过10.91秒，含实际PG八路领取仅一成功、取消原记录不变，以及真实ToolRuntime领取未确认时不覆盖所有者。PG夹具两次失败〔漏项目表、漏Session必需字段〕原log/XML保留，按现有main和构造顺序修正。Anthropic真实本地HTTP/原client14通过60排除20.80秒，实际仍/v1/messages、messages/system和message流事件；`await_provider_io`只处理I/O，该名称已由此前SDK提交更正。前端npm run build含vue-tsc/Vite成功9.70秒；compileall退出0。审计脚本及validation保留原件SHA、三方案同步、四保护文件和配置凭据核对。

**已有实现但未由本批证明**：真实默认AI的意图/文档发现/项目选取→排序→补全→执行质量，其他供应商全阶段及正式用例管理运行、多工具混合审批/等待、Deep Agents治理入口。这里使用固定campaign_ready与本地受控模型，不替代外部AI验收；按用户安排继续暂缓外部AI。

**仍需补实现或继续专项核验**：API拒绝与未知写入恢复、自动过期401续期/Cookie/并发多认证/加密边界kill；完整准备/只读查证/运行内计划修订；复杂UI响应关联/修复/报告、持续容量/AI流式指标/安全故障组合、更广需求业务评审/部署环境/历史迁移、全阶段重试故障和两份旧Worker原件。模板导入、普通生成、简单UI/录制、回执和Docker既有成功证据保持，不重新归为未实现。完整P0—P5保持active，仅本批白名单提交，无push/PR。

### 7.47 整批工具等待、审批检查点与通知并发保存〔2026-10-12〕

基线8df999ef，依据P2_TOOL_WAIT_BATCH_RESEARCH.md。先实测复现剩余审批ID丢失、第二等待工具未结算却交回模型，before2失败9.94秒。Runtime原完整_build_pending_turn迁至state_persistence共用，原入口和字段不变，AST原函数体一致；等待保存整批waiting_tools兼容旧单waiting_tool，沿原预算/原call/input/Job/快照。剩余父审批保持waiting_approval，全部审批后才进入子等待；整批结算才按原assistant顺序提交唯一结果给模型。Coordinator核验所有子及等待列表，不只首项。没有新表/依赖/配置、Session状态或模型协议变更。

独立Agent首批1失败1通过142.95秒，又确认子通知的旧整会话save覆盖Worker新pending/status。强制相同窗口内存/实际PG均2失败7.99秒；沿现有行锁mutate_metadata只写通知自身guard/report字段，再读当前Session，无自动父继续分支保留原事件投影、不整会话保存临时通知。原停止策略与权限保持。早期PG夹具两批失败是只等snapshot出现就close取消未完成Session保存，诊断保存原现场；按真实状态和审批续跑结算等待后9通过20.58秒，不扩大生产等待超时、不重置记录。

**已实现且本批实测**：notification-verified 11通过19.19秒，实际PG/内存真实图+HTTP，2等待工具/2父审批，批准全产生POST/POST/GET/GET，拒绝第二项产生POST/GET/GET且原Job denied；服务重建同一turn，预算1→2/max8〔新默认99未捐赠〕；唯一4工具结果，只有一子完成不自动继续；旧单等待、第二项身份/快照变化及通知覆盖边界通过。生产等待来源与模型决策受控，非正式case Run或默认AI质量。最终真实main/独立Agent CLI两路径agent-final 2通过2排除159.46秒：login/refresh/profile全200、报告3完成0失败、等待重启原snapshot保持、终态原消息任务重投0业务/模型HTTP。原5模块owned回归61通过1跳过40.74秒；11模块最终79通过42门控跳过91.12秒，未全启用PG等门控；Coordinator/报告41通过66排除10.57秒。重叠不累加，失败原件/报告保留。compileall退出0附加，前端未改未重构建。

**尚未验收或需继续补实现**：正式用例管理运行的等待组合、未知副作用只读查证、准备生命周期、执行中计划修订及Deep Agents入口；真实默认AI多候选项目选取→排序→补全→执行/断言质量；API自动401续期/Cookie/并发多认证/加密边界kill；复杂UI因果/选择器修复/失败报告、持续容量/AI流式阶段指标/安全故障、更广需求业务评审/部署适用性/生产历史迁移、全阶段发送追踪及两份旧Worker原件。批准/拒绝在本批普通图混合等待中已验，不能扩大称所有API恢复已通过。模板/生成/简单录制UI/回执/Docker既有证据保持。完整P0—P5继续active，外部AI按用户安排暂缓；三方案同步和白名单提交，无push/PR。

### 7.48 未知写入禁止按匹配断言放行〔2026-10-12〕

基线a0097a5a，依据P3_UNKNOWN_WRITE_RESEARCH.md。现有Executor已经把写请求超时/连接异常/5xx标记为`request_outcome=unknown`，报告已有查证标记；但随后按断言全通过设置completed，导致“写请求返回500且用例预期500”可能放行依赖。修复后未知写入无论断言是否匹配都保持failed，保留HTTP响应/观察/断言证据；现有TaskPool传播为后续skipped，评估增加只读查证且禁止重试建议。没有新表、配置、依赖、模型协议或权限变化。

实际4项未知写入专项通过2.00秒：真实超时仅1次HTTP，真实500仅1次HTTP，匹配500断言仍failed，后续依赖没有执行，评估分类`unknown_side_effect`并提示只读查证。API执行器、结果回读、操作观察、效果关系、鉴权刷新回归61通过50跳过30.04秒；compileall退出0。外部AI0，受控/真实本地HTTP，不代替默认AI或正式Run验收。

已实现但未覆盖：只读查证具体工具选择、查证后确认、跨进程正式用例Run恢复仍需后续专项；完整P0—P5保持active。

### 7.49 正式用例恢复与效果生命周期复核〔2026-10-12〕

基线b2f234a5，依据P3_FORMAL_CASE_RECOVERY_RESEARCH.md。正式AgentCaseExecutionAdapter沿现有Run/RunItem/Attempt/turn固定身份先保存执行意图，缺快照直接要求RecoveryRequired，不重新发送；原ToolJob项目/Run/Item/Attempt/call/input必须匹配，逻辑操作与对象效果证据用于区分可靠回执、未知副作用、准备成功和目标通过。准备/清理范围依赖和回执纠正沿既有协议。

相关正式用例研究回归22通过、34门控跳过、60.17秒；通过部分覆盖租约、恢复链、逻辑操作、共享效果/清理、回执处理。门控真实PG/RustFS/Redis与完整产品Run未启用，因此不宣称正式跨进程未知查证已完成。API未知写入真实HTTP已在7.48修复并验证；默认AI选取/排序/补全、查证工具选择和外部AI继续暂缓，完整P0—P5保持active。

### 7.50 项目用例召回故障显式化〔2026-10-12〕

基线2c0bc397，依据P3_CASE_RETRIEVAL_FAILURE_RESEARCH.md。调研确认项目用例召回已按project_id、active version、environment和mode_key过滤，但router捕获召回异常后仅记录日志并以空hits继续，可能把故障误判为无匹配。修复后在context_bundle标记`test_case_retrieval_status`和错误类型，写入`test_case_retrieval_failed`事件和明确提示；成功为completed，未请求为not_requested。不会切换自由测试、虚构用例或放宽项目隔离。

实际三模块19通过3.21秒，包含召回故障模拟、项目范围、版本索引与embedding缓存。默认AI、正式Run和外部模型继续暂缓，完整P0—P5保持active。

### 7.51 API项目候选正式身份与同名隔离〔2026-10-12〕

基线74a283ac，依据P3_PROJECT_IDENTITY_RESEARCH.md。原项目候选按project_name分组，正式同名项目可能合并；本批让ApiDocRecord.project_id沿请求、ProjectCandidate、DocumentCandidate、ApiTestCampaign传递，绑定会话项目ID先过滤目录，再按ID分组，文档选择优先按ID。旧无ID文档保留兼容分组，但正式项目绑定不会读取无ID文档。没有新表、配置、依赖或模型协议。

实际16通过、1跳过、2排除、7.36秒，覆盖同名不同ID不合并、绑定ID过滤、用例召回、会话隔离和非live Agent回归。默认AI项目理解与多候选排序质量仍未验收，完整P0—P5保持active。

### 7.52 API项目身份从候选到恢复的实测〔2026-10-12〕

基线b9d1ab60，依据P3_API_PROJECT_SCOPE_RESEARCH.md。实际上传核验发现发现结果未保存真实文档候选、选择会按名称扩大文档集合、恢复未在正文读取前核对项目/文档ID，嵌套api_testing_request还能伪造project_id。修复后正式身份唯一来自context_bundle.project_id；发现保存真实DocumentCandidate，选择只消费候选doc_ids；目录过滤、Campaign和恢复校验均使用正式ID，冲突在正文读取前拒绝并记录事件。旧无ID文档保留兼容，不替代正式绑定。

内存与隔离PG/RustFS实际上传/解析/恢复共6通过4.28/9.09秒，跨项目伪造0正文读取；正式身份/召回回归16通过，前端npm run build成功7.56秒。业务HTTP和外部AI均0。默认AI候选排序质量、正式Run完整查证和完整产品流程仍未验收，完整P0—P5保持active。

### 7.53 API项目选择确认门禁一致性〔2026-10-12〕

基线e8d340cc，依据P3_PROJECT_SELECTION_GATE_RESEARCH.md。Locator和Runtime原本对唯一未命名项目的确认规则不一致，Runtime会自动选择临时名称。已对齐：无正式名称且用户没有项目线索时进入awaiting_project_selection；只有正式名称或明确线索才自动选择。多候选分数规则保持。

项目身份/上传/解析/恢复和召回回归17通过1跳过3.72秒；默认AI多候选排序质量仍暂缓，完整P0—P5保持active。

### 7.54 API任务依赖领取与缺失路径前置〔2026-10-12〕

基线5156e19c。对照正式RunItem的`validate_run_dependencies`、`dependencies_ready`和API两个协调器后，真实复现API task pool静默忽略不存在的`depends_on`、保存的`ready`标签绕过未完成前置，以及未解析路径占位符被发送到目标。前置缺口不通过增加注册/登录清单解决；只在确定性执行边界校验任务图和实际输入。

`ApiTaskPool`构造及每次协调使用标准库`TopologicalSorter`拒绝重复任务、缺失/自引用、重复依赖和循环；`ready_tasks()`重新按依赖状态协调，恢复标签不能越过前置。路径参数只解析URL path，端口不再被识别为`:parameter`；本地协调器和Worker派发前对剩余占位符失败，不发HTTP、不派发Worker，具体缺口进入报告。Campaign保存`PreconditionAnalysis.to_dict()`，使人工数据缺口、来源和解析细节随冻结计划可查。无新表、配置、依赖、模型协议或权限。

首次复现9项失败；修复后`p3-api-dependency-final.xml` **11通过**，真实本地HTTP证明创建→实际输出`id=0`→查询顺序、未解析路径零HTTP/零Worker和终态零额外请求；相关回归`p3-api-dependency-verified4.xml` **70通过、3跳过**，加入 Campaign 前置摘要后的完整回归`p3-api-dependency-regression2.xml` **101通过、5跳过**，覆盖文档解析依赖、创建/查询、重试、未知写入、结果回读、工具等待、项目范围和模式隔离。外部AI调用0。该专项完成API确定性依赖门槛，不能替代默认AI多候选选取/排序/补全质量和正式用例Run全流程验收；完整P0—P5保持active，外部AI按用户安排暂缓。

### 7.55 Git文档来源实现与生产验收复核〔2026-10-12〕

基线4874360c。当前 Git 入口已经存在：项目级登记配置进入现有 MySQL 配置存储；受限进程只读固定 repository/commit/blob/path；导入与普通上传独立，只有用户明确选择 `document_id` 和父版本时才追加版本。普通上传不使用 Git 缓存，不写仓库、不 clone、不 checkout、不提交，不自动关联历史。

实际验收：`p3-git-current.xml` **24通过、4跳过**；启用 `RUN_LIVE_CASE_PLANNING_TESTS=1` 的 `p3-git-live2.xml` **26通过**，实际隔离 MySQL/PostgreSQL/RustFS；启用 `RUN_LIVE_DOCUMENT_GIT_PRODUCTION=1` 的 `p3-git-production-current.xml` **2通过**、132.11秒，实际独立 API/Redis Worker/MySQL/PG/RustFS/Chromium，验证 Markdown 与 DOCX 固定 Git 来源、丢回执恢复、解析、比较、重启及普通上传独立。审计见 `P3_GIT_SOURCE_CURRENT_RESEARCH.md`、`p3-git-current-audit.py` 和 `p3-git-current-validation.json`。

本项属于**已有实现且已完成当前范围验收**，不应再列为“待补 Git 入口”。默认 AI 影响判断、用户业务评审、部署适用性、生产历史迁移仍未完成。扫描 PDF OCR 属于首期范围外；Docling 保留扩展入口，按既定约定首期不部署容器。完整 P0—P5 保持 active。

### 7.56 API 前置响应在进程恢复后传值与有效请求地址〔2026-10-13〕

基线 b25d15cf，研究见 `P3_API_DEPENDENCY_RESTORE_RESEARCH.md`。先实测发现 Worker 已从完成任务恢复输出，本地协调器却置空；路径检查使用展示 path，与执行器优先发送 full_url 错位。修复前 7 项全部失败：真实创建后立即退出进程，恢复后丢失 id=0，body/query 丢失 False/0，有效 URL 含占位符时实际发出编码占位符或派发 Worker。失败原件完整保留。

**本批补实现并已验收**：本地协调器按已有 Worker 做法读取同一 Campaign 中 TASK_COMPLETED 的响应，增加仅含任务身份和数量的恢复日志；路径绑定与缺口检查按照执行器实际 full_url 或 path 选取地址，仅检查 URL path。未新增配置、数据库表、依赖、模型协议或业务依赖规则。原七项 7 通过；新增已完成/failed/unknown 输出隔离、False/0 显式字段传值、展示地址与实际地址、query/fragment/端口边界后的五模块最终 68 通过、2 真实 PG/RustFS 门控跳过，19.95 秒；compileall 实际退出 0。

**实际证据**：创建请求成功后检查点落盘，首进程立即退出 73，新进程恢复同一 Campaign，执行查询 /orders/0 或 POST /confirm?id=0，body 保留 enabled=False 与嵌套 count=0。每场景只有一次创建和一次依赖请求；原完成任务完整 JSON 不变；终态重投无新增 HTTP。首轮扩展回归因复用证据目录被原件保护拒绝，5 失败、63 通过、2 跳过日志保留，换独立 after2 目录后通过，未覆盖既有证据。

**验收边界**：本批使用实际本地 HTTP、独立进程和 Campaign 文件检查点，不宣称验收完整生产 PostgreSQL/API/Redis 重启流程。任意缺失 body/query 输出处理、Cookie/自动续期与多认证隔离、未知副作用只读查证、执行中计划修订及默认 AI 完整项目选取/排序/补全质量仍待推进；其余完整 P0—P5 目标不缩减。外部 AI 验收按用户此前安排暂缓，Step 已认证，不再沿用旧 403 判断账号状态。

**交付核对**：三份方案同步；`p3-api-dependency-restore-audit.py` 核对本批测试、真实 HTTP、检查点和原件 SHA，以及上次 Git 验收提交中的全部 48 份原产品工件逐字节不变。上次 Git 原件属性未处理 CRLF，且提交命令未严格阻断 diff 检查失败，现已修正对应原件 whitespace 属性并重新运行 `git diff --check b25d15cf^ b25d15cf` 通过。OCR/Docling 首期范围表述同步纠正。仅提交本批白名单，不推送、不建 PR，完整目标保持进行中。

### 7.57 API 凭据沿认证依赖来源传播与多账户 HTTP 验收〔2026-10-13〕

基线 3d494671，研究见 `P3_API_AUTH_LINEAGE_RESEARCH.md`。先复现 4 项失败，证明确认的缺失实现：两种协调器共享的传播把 A 凭据附到无依赖的 B 登录，真实返回 401；还覆盖多认证前置的既有输入与运行中任务。单账户旧成功不证明多认证正确。原失败 log/XML 和脱敏请求来源标签保留。

**本批补实现并已验收**：两种协调器共用来源推导，仅沿当前 Campaign 已记录的认证依赖传递，刷新遮蔽其自身登录，多个独立认证来源时保留已有输入并记录歧义；运行中/终态记录不改。简单新规划在唯一同源登录时记录刷新依赖，认证列表顺序不再替代来源。旧保存单账户计划仅对已声明 refresh_token 能力且同源登录唯一的场景投影兼容来源，冻结边不重写。没有新增全局认证状态、配置字段、表、依赖或模型协议。

**功能实测**：本地与既有受控 Worker 启动器各完成两条独立账户登录→刷新→profile 及无认证预期401，真实业务 HTTP 各7次，凭据来源正确，负向401仅1次，终态重投新增HTTP为0。旧冻结计划实际3次请求完成且依赖原文保持。首轮相关33通过3跳过；最终九模块89通过5真实存储门控跳过29.72秒，含前批实际进程退出和响应传值回归。启用原存储门控后独立3通过5排除25.51秒，实际隔离MySQL/PG/RustFS、ToolJob/Session加密凭据读取、父报告与新服务重读、终态重投。该三项与回归存在重叠，不累加成全部功能通过。compileall实际退出0。没有外部AI调用；本批受控Worker与服务重建不代表独立API/真实默认模型或多认证进程kill验收。

**仍需补实现或继续验收**：Set-Cookie登录/旋转和过期处理；有可靠刷新来源与负向意图约束的401修复及安全重放；默认AI多账户项目选取/规划/执行质量和多认证生产故障恢复；任意缺失body/query绑定、未知副作用只读查证、执行中计划修订、复杂UI及其余完整P0—P5范围。旧API文档规划器仍会附加汇总认证依赖，不宣称能从任意文档辨认多个账户；有歧义的正式执行需沿明确Agent/Campaign计划。外部AI仍按此前安排暂缓，Step按已认证记录。三份方案同步，原件/范围审计见本批audit/validation，白名单中文commit，不推送、不建PR，目标保持进行中。

### 7.58 Set-Cookie 登录、属性快照、旋转与加密回读〔2026-10-13〕

基线f6402ec9，研究见`P3_API_COOKIE_RESEARCH.md`。当前缺口由真实HTTP确认：原Token-only提取使本地/Worker的204直接登录、303重定向登录均无法完成Cookie会话，首次4失败1通过原件保留。没有把Cookie字典平铺后假装保留属性，也没有新建另一套HTTP解析器。

**本批补实现并已验收**：沿现有HTTPX/标准库CookieJar捕获、恢复domain/path/Secure/Expires及HttpOnly/SameSite元数据；同名不同路径分别保存，204刷新/Max-Age删除和重定向来自实际客户端jar。新增私有可选cookie_records，旧会话无该字段时序列化不追加空值，既有不可变凭据哈希不重写；显式空快照表示删除。现有PG加密JSON追加新凭据身份，未新增表/配置/依赖。同步已知具体值和公开容器脱敏；用户显式旧Cookie头保持兼容。预期认证401不被当作成功登录创建新会话。模型协议和现有测试模式保持。

**实测结果**：原五项5通过；属性专项8通过1存储门控跳过8.87秒，核对两协调器四条真实HTTP链路、同名路径、Domain/host-only、Secure、过期恢复、删除、空快照、公开输出和终态。最终十一模块83通过5门控跳过25.48秒，含Bearer、请求体、派发、恢复、模式隔离和unknown写入。共享脱敏边界补跑工具安全门和意图安全路由105通过13.47秒；该回归不替代业务HTTP与存储验收。独立真实存储1通过8排除11.03秒，隔离MySQL/PG/ToolJob/Session及RustFS环境，受控Worker Cookie登录/旋转/profile成功，新服务回读属性与原凭据一致，再作明确只读profile为200，原Job重投额外HTTP0。密文/公开Job/提示无随机Cookie值，自定义opaque_echo脱敏，旧JSON重复保存未产生哈希冲突。该项没有额外RustFS原件要求，不宣称验证独立API、外部默认模型或生产进程kill。compileall实际退出0。

**失败保留与限制**：存储前三次分别因夹具必填字段、错误模块路径、缺原JobID失败，日志/XML保留；改用已有原Job恢复路径后通过。首轮广回归有一次原Bearer写入网络失败，单项及最终整批复跑通过，但原因未稳定复现，保留观察，不猜测修复。CookieJar遵循HTTP客户端规则，不声明浏览器SameSite/Partitioned安全语义。Token过期识别、可靠来源与负向意图约束的自动401修复、安全重放和多认证生产故障仍待推进。

**整体进度仍按功能区分**：模板/Excel/JSON产品页已有验收；普通文档模块生成/恢复已有实现，默认模型全量质量仍待验收；上传/Git/解析/差异/影响草稿和追踪已有专项及产品证据，业务评审/环境/历史迁移仍待闭环；简单UI/真实DeepSeek录制和部分正式回执已有证据，复杂关联、失败定位及完整默认AI知识使用仍待验收或补强；Docker已有真实工具/解析专项，持续容量、AI流式指标和更多安全故障组合仍未闭环；按用例执行的准备生命周期、unknown只读查证和计划修订仍需实现/验收，默认AI项目选取→排序→补全→执行完整质量仍未验收。完整P0—P5不缩减，Step已认证，外部AI按之前安排暂缓。本批白名单提交，无push/PR，方案三份同步。


### 7.59 Token 显式有效期、负向401与加密恢复〔2026-10-17〕

基线4fd85832，依据P3_API_TOKEN_EXPIRY_RESEARCH.md。复核发现既有expires_at字段始终为空，已知过期凭据仍被普通前置检查视为有效。未验证的第一版在低层删凭据，会破坏“过期Token应401”负向目的；已撤回并保存rejected-draft，不当作最终代码或通过结果。修复前11失败2.77秒，其中两项HTTP相关元数据断言、一项继承和八项错误可见性断言，并非11个独立业务故障。

**本批补实现并验收**：沿CredentialManager保存显式顶层expires_in整数秒为一次性的UTC绝对时间；缺失/非法保持未知且非法有安全日志。明确用户expires_at仅按带时区ISO解释，不猜秒/毫秒或本地时区。未返回Token的Cookie旋转继承原期限，新Token缺TTL保持未知；旧payload与凭据身份不改。has_valid_session跳过已知过期会话，普通前置进入既有认证输入状态；低层不删请求头、不替换断言、不自动刷新或重放。配置仍MySQL，已有加密凭据JSON仍PG，无新配置/表/依赖/模型协议。

**实际结果**：原专项11通过2.45秒；功能扩展17通过6.91秒，实际HTTP、两协调器及原Worker runner的受控启动证明登录→预期401保留原Token，允许重试仍仅两次请求，终态额外HTTP0，绝对时间恢复不续命。独立真实存储1通过17排除7.07秒：隔离MySQL/PG/RustFS环境，HTTP登录、PG加密保存、新服务逐字段回读，回读没有HTTP；明确新建负向请求401，旧空期限JSON和原身份重复保存无哈希冲突。十三模块最终116通过8门控跳过43.10秒；跳过不计通过，重叠批次不累加。compileall退出0附加，未改前端所以未重跑构建，外部AI0。该项不是独立API/Worker进程kill验收，也不要求或证明额外RustFS原件。

**仍需实现/验收**：可靠来源与负向意图约束的自动401续期及安全重放；Token角色提取与任意项目字段映射；候选发现阶段负向意图、时钟/延迟适用性与多认证生产故障。普通需求默认模型全量质量、默认AI项目选取→排序→补全→执行、准备生命周期/unknown只读查证/计划修订、复杂UI及失败定位、持续容量/AI流式指标、需求业务评审/部署/历史迁移、全阶段物理发送与两份旧Worker原件仍未闭环。模板/Excel/JSON项目页面、Git独立来源和现有模型适配层已有对应验收，不应重新列成缺实现。Step已认证，外部AI按此前安排暂缓。三份方案同步，白名单中文commit，无push/PR，完整P0—P5继续进行。


### 7.60 API 前置实际输出绑定缺失阻断与中断恢复〔2026-10-17〕

基线fec6234b，依据P3_API_REQUIRED_BINDING_RESEARCH.md。先读方案中“传递实际输出/前置成功但输出缺失不能满足”及课程响应字段传递，再实测两协调器：修复前10项全部失败，缺失来源仍发送残留body/query/header示例值，实际null/false/0也未正确区分。原失败XML/log/事件保留。

**本批补实现并已验收**：将本地与Worker重复绑定逻辑收敛到已有`input_binding.py`。绑定在claim/dispatch前对副本执行，来源路径支持点和数组索引，缺失用哨兵与实际null区分；仅显式literal_value可做备用。body、request_body、query、request_query、request_headers、path/url.path目标统一处理，标量头/路径拒绝非法值。任一声明绑定缺失或目标非法时任务不发送、不派发，原请求对象不部分修改；后续依赖skipped，独立任务继续。计划缺口不重试，已完成上游恢复不重发。无新表、配置、依赖、模型协议或前端契约。

**实际结果**：核心修复10通过10.65秒；扩展16个真实HTTP事实文件覆盖本地/Worker、缺失body/query/header、别名、literal、null、非法目标/类型。最终受影响回归`p3-api-required-binding-final.xml` **150通过、8门控跳过、64.24秒**，含已有依赖/恢复、请求体、Worker结果、Cookie/Token、多账户及模式隔离。独立进程真实验证：首进程上游请求后退出73，新进程读取检查点，已完成上游不重发，缺失消费者失败且0次请求。compileall与审计通过，外部AI0；存储门控跳过不计通过。

**仍需继续**：默认AI项目选取→排序→补全质量、完整正式用例Run、准备生命周期/unknown只读查证/运行中修订、自动401续期/安全重放、复杂UI及失败定位、持续性能与其他安全工具、需求业务评审/部署/历史迁移和全阶段HTTP追踪。Step已认证，外部AI按此前安排暂缓。完整P0—P5保持进行中。


### 7.61 API 未知写入的显式只读查证〔2026-10-17〕

基线0f492ff9，依据P3_API_UNKNOWN_RECONCILIATION_RESEARCH.md。复核现有Executor、TaskPool、两协调器和Evaluation：未知写入已禁止重试并归类unknown_side_effect，但没有合法的显式只读查证入口；按HTTP方法猜查询会误查资源，因此本批不做自动发现。

**本批补实现并已验收**：ApiTestTask增加`reconciliation_for`、`reconciliation_observation`和`reconciliation_status`。查证任务必须指向unknown写任务、使用GET/HEAD/OPTIONS，并可保留普通认证前置；未知写失败后由TaskPool单独放行，非法方法在任务池初始化时拒绝。查证成功只记录effect_present/effect_absent，未声明或失败记录inconclusive；原写任务始终保持unknown，不自动重试、不把原业务依赖变成可执行。报告显示查证目标/状态及是否仍需处理。没有新表、配置、依赖、模型协议或前端契约。

**实际结果**：`p3-api-unknown-reconciliation-functional3.xml` **13通过**，本地和Worker真实HTTP均只发送POST一次和明确GET一次；effect_absent/effect_present、无观察值、非法写方法和原unknown阻断均通过。受影响回归`p3-api-unknown-reconciliation-final.xml` **159通过、8门控跳过、61.49秒**（XML总167，跳过不计通过），含绑定/恢复、Cookie/Token、多账户、请求体、Worker及模式隔离；40项烟测全部通过。compileall与审计通过，外部AI0。没有启用真实PG/RustFS存储门控；该项未实现自动查询发现或安全重放。

**仍需继续**：正式用例/默认AI自主选择查证工具、对象效果证据与跨进程生产恢复；准备生命周期、运行中修订、自动401续期/安全重放、复杂UI、持续性能/安全、需求业务评审/历史迁移和其余完整P0—P5。Step已认证，外部AI按此前安排暂缓。


### 7.62 Agent API 报告页结果呈现与脱敏〔2026-10-17〕

基线537a4aba，依据P3_API_REPORT_PRESENTATION_RESEARCH.md。复核发现Reports服务只筛选code_review/security_testing/performance_testing，api_testing会话不会进入历史报告页；API任务报告成功项也只有状态/断言，缺少可审阅请求与响应投影。

**本批补实现并已验收**：报告服务加入api_testing与api-test-runner映射，按当前ToolJob读取api_test_report_json，不回退旧执行；投影任务统计、summary、findings、evaluation/verification和api_tasks。ReportBuilder给每个任务提供脱敏request/response、HTTP状态、断言与reconciliation字段；Authorization、Cookie、password、access_token和Set-Cookie等沿OutputSafetyPolicy处理。前端ReportsView增加API任务卡片，可展开请求、响应、断言和查证状态，保留Markdown/JSON工件下载。无新表、配置、依赖或模型协议。

**实际结果**：后端报告投影/列表/脱敏回归23通过；前端reportPresentation测试8通过；前端typecheck和Vite production build通过。首次构建的模板引号语法错误已保留原日志，修复后重新构建通过。外部AI0；三份方案同步。尚未进行独立生产PG/RustFS报告页浏览器验收，仍需在完整产品批次补做。

**仍需继续**：默认AI完整项目选取→排序→补全→执行、默认Agent结果质量、自动401续期、准备生命周期/运行中修订、复杂UI知识与失败定位、持续性能/安全、需求业务评审/部署/历史迁移和完整P0—P5。Step已认证，外部AI按此前安排暂缓。

### 7.63 Agent API 报告页真实产品验收〔2026-10-17〕

基线`e4ed0282`，依据`P3_API_REPORT_PRESENTATION_RESEARCH.md`和既有性能/安全报告产品夹具。报告页走生产`/reports`、PG回读、构建版App和Chromium，API父ToolJob沿现有受控本地HTTP campaign产生，未调用外部AI。该夹具虽含RustFS环境，但API报告实际沿用PG的`inline://artifact`/`__content_text`，不能据此声称报告原件已存入RustFS或验收下载链接。

**本批已验收**：在隔离MySQL/PG/RustFS/Redis中执行真实`ToolRuntime`并由新生产API进程回读，`/reports`将当前`api_test_report_json`绑定到父ToolJob；Vite preview加载构建版App，Chromium打开报告页，显示3个API任务卡片、总任务数3、通过3、等级“良好”，页面错误数0，网络请求成功。`p3-api-report-product-final2.xml/log`为1通过、0失败、0跳过、49.46秒；页面文本、ARIA、截图、网络、报告列表和浏览器观察保存在`p3-api-report-product-evidence2/`。首次断言误把Markdown表格分隔符当作浏览器文本，`p3-api-report-product-final.xml/log`原样保留；按真实渲染语义修正后第二次完整产品运行通过。

该批关闭对应API报告列表和页面呈现范围的PG/浏览器验收；未覆盖RustFS报告原件下载、浏览器打开后的服务重启或任意动态凭据字段脱敏，不扩大为默认AI项目选取→排序→补全→执行或完整Agent质量通过。无新表、配置、依赖或模型协议变更。自动401续期/安全重放、准备生命周期、运行中修订、复杂UI知识/失败定位、持续性能/安全、需求业务评审/部署/历史迁移及其他P0—P5仍未完成。Step已认证，外部AI按此前安排暂缓。

### 7.64 分任务证据规划七变体真实存储复核〔2026-10-17〕

基线`ba7676ce`，依据`P2_EFFECT_TASKS_RESEARCH.md`、`P4_EFFECT_LIFECYCLE_RESEARCH.md`和现有`test_planning_effect_tasks_live.py`。复核更正：旧`p2-effect-tasks-live-query-final.xml`及7.31已覆盖七个变体；本批是当前版本回归，不是新增查询/坏引用/预算覆盖，没有重写准备范围或新增执行器。

**实际结果**：隔离 MySQL、PostgreSQL、RustFS 与实际 PostgreSQL 规划任务表中，`none`、`bindings-ack`、`produced-ack`、`cleanup-response-ack`、`budget`、`unknown-reference`、`produced-query` 七项 **7通过、0失败、0跳过、18.65秒**。验证已接受回执不能被后续策略或文档改写，提交回执后丢确认可以从 PG 检查点恢复，错误引用被拒绝，产生阶段查询继续沿原预算，终态重投不新增模型请求。所有验证记录保存于`p4-effect-task-live-current/`，审计为`p4-effect-task-live-current-audit.py`，业务 HTTP 明确为0，模型为受控测试模型。

这是“分任务规划回执与恢复”的当前存储回归证据，不增加新功能完成范围，不等于正式 Run/Agent 业务执行、共享对象消费完成、未知副作用只读查证、运行中计划修订或默认 AI 多用例闭环通过；完整准备生命周期及其余P0—P5继续保持未完成。无生产代码、表、配置、依赖或模型协议修改，Step已认证，外部AI按此前安排暂缓。

### 7.65 API 401 续期与安全重放边界复现〔2026-10-17〕

基线`75efee85`。先查`ApiTaskExecutor`、`CredentialManager`、认证依赖传播、两个 Coordinator、Token/Cookie有效期及已有负向401测试，确认当前系统能保存刷新凭据，但没有“401后选择哪个刷新任务并安全重放”的任务关系或持久检查点；不能仅按状态码猜`/refresh`，否则会破坏预期未授权用例和写请求未知副作用语义。

新增最小真实本地HTTP复现，不改生产代码：旧Token访问只读`/profile`得到401时，实际只发送一次且不猜refresh；显式执行已有刷新任务取得新Token后再显式访问得到200；写请求预期401只发送一次。`p3-api-401-refresh-gap.xml/log` **1通过、0失败、1.91秒**，事实见`p3-api-401-refresh-gap/verification.json`，研究见`P3_API_401_REFRESH_RESEARCH.md`。

本批确定后续实现契约：必须显式绑定同项目认证来源的refresh task；仅允许明确标记的只读任务最多重放一次；预期401/403、写请求、超时、连接异常和unknown outcome不自动重放；PG检查点保存原401、刷新Job/credential ref、重放Job、次数和最终结果；本地/Worker两条路径共用同一状态协议。当前自动401续期和安全重放仍是**需补实现**，没有新增表、配置、模型协议或外部AI调用。

### 7.66 API Token角色、显式字段路径与报告回归缺口〔2026-10-18〕

基线`b3f02db8`，依据`P3_API_TOKEN_ROLE_RESEARCH.md`。续期基础调研确认登录响应会把任意含token的长字符串当Bearer，文档提示也会从refresh_token内部匹配token；显式嵌套字段失效后会静默取无关token。真实本地HTTP复现为21失败、9通过，失败包含业务请求、类型与文档提示，不能当成21个独立业务故障。

**本批补实现并完成专项验证**：默认仅兼容access_token/accessToken/token，不自动使用refresh/id；显式auth_token_field先读精确键，再复用既有点/数组路径，缺失或非法不替换为无关字段。仅接受非空、无换行字符串；文档提示匹配完整字段、访问字段优先，缺失留安全日志。沿已有Runtime/Coordinator/私有dispatch/Worker参数传递，无新配置、表、依赖、公共API或模型协议。

**实际结果**：修复后专项30通过13.72秒；真实隔离存储2通过30排除16.86秒，生产ToolRuntime、加密PG、冻结字段和新服务终态原Job回读通过，额外HTTP0。调整测试断言避免打印临时凭据后，最终交付测试版本启用存储重新执行为32通过、0失败、0跳过44.84秒，与前面批次重叠不累加；提交的原失败输出仅保存脱敏副本。没有跨进程kill或RustFS报告原件下载。compileall及生产diff检查通过。Anthropic既有实际HTTP故障专项复核13通过，路径仍为`/v1/messages`，包装函数`await_provider_io`只处理网络等待与错误，未切换Responses。

**回归没有全绿**：16模块196通过、4失败、9门控跳过71.95秒，4项均为Cookie报告opaque_echo值脱敏断言；使用HEAD原凭据提取复核仍在同一断言失败，证据原样保留，列为已确认待修缺陷。不能用Token专项通过替代报告或完整鉴权验收。7.63已更正RustFS报告存储的过度表述，7.64已更正七变体当前复核并非新增覆盖。

自动401续期/安全重放、任意项目字段映射的默认Agent质量与产品入口、Cookie动态值报告脱敏、默认AI项目选取→排序→补全→执行、完整准备生命周期/运行中修订、复杂UI、持续性能/安全、需求业务评审/部署/历史迁移及其余P0—P5仍未完成。Step已认证，外部AI沿用户此前安排暂缓，完整目标保持进行中。

### 7.67 API 报告实际凭据、截断边界与下载/重启验收〔2026-10-18〕

基线`4f77be93`，调研见`P3_API_REPORT_CREDENTIAL_RESEARCH.md`。原有字段名脱敏无法处理真实Cookie在opaque_echo等普通字段里的回显；失败报告先截断再脱敏可能留下凭据前缀。生产Runtime/真实本地HTTP复现5失败8.37秒，失败原件保留且不打印随机凭据。

**本批补实现**：沿既有OutputSafetyPolicy提供可选已知值上下文，CredentialManager提供实际持有的Token/Header/Cookie及旋转前会话；ReportBuilder显式接入，在截断前处理公开副本，并覆盖JSON/Markdown/失败工件/标签。私有Worker公开结果复用同一策略，替换原仅精确整串匹配的内部函数。最终组装补核又复现后追加评估把失败任务名称中的凭据重新带入，2失败6通过7.57秒；Runtime的验证/质量评估、错误及建议也在公开投影边界使用同一上下文。原执行响应仍可断言和传值，未修改可恢复快照协议；没有新表、配置、依赖或模型协议。配置仍MySQL，业务/凭据和本次报告仍PG。

**实际验收**：专项16通过1门控跳过14.80秒，两执行路径/正负结果和500/2000/4000截断边界通过；旧16模块加新报告、安全/可观测性/ReportService回归最终342通过8门控跳过95.27秒，原四个Cookie报告失败已通过，启用的真实Cookie存储专项验证加密/属性回读/原Job终态不重发。其余8项门控测试未在该批执行，不计为通过。

完整产品最终`p3-api-cookie-report-product-restart.xml/log`：1通过0跳过81.21秒。真实本地Cookie接口4次HTTP、隔离MySQL/PG、生产ToolRuntime与受控Worker启动、独立生产API、实际构建完整App/Chromium，任务3项仍2通过1失败。公开Job/报告/失败证据/页面不含生成Cookie，浏览器下载字节和SHA-256与PG原件一致；API重启前后原报告与下载不变，原Job终态回读新增HTTP0，主动重启范围外失败0。三份工件真实URI均为`inline://artifact`，不是RustFS报告原件验收；也不是默认AI或独立Agent CLI验收。

过程错误均保留：首轮产品工件类型断言写错1失败25.89秒；第二轮文本断言错误依赖制表符1失败91.33秒，改用实际表格单元格；回归首次漏设已有证据目录变量341通过1失败8跳过100.89秒，补齐参数完整重跑。中间产品1通过59.66秒，但审计发现一次会话GET500未记录重启边界，不能声称网络全绿；补齐边界及恢复后页面断言后得到81.21秒结果。后追加评估修复后的最终源码重新完整回归：`p3-api-report-credential-delivery.xml/log`342通过8门控跳过92.48秒；最终产品`p3-api-cookie-report-product-delivery.xml/log`1通过0跳过57.84秒，最终原件/下载/重启/进程输出位于同名目录。前面结果为历史逐次验证，不累加覆盖。源码与测试compileall、限定diff检查通过；最终证据审计11次运行/99份文件通过，见`p3-api-report-credential-audit.json`。

| 分类 | 当前状态 |
|---|---|
| 已有实现，本批接入修复 | 公共输出安全策略、凭据会话及加密引用、执行/报告/工件、报告页、原Job终态恢复 |
| 本批补实现且功能验收 | 已知凭据普通字段回显、嵌入文本和三个截断边界；公开报告不改真实执行结果；浏览器下载及打开页面后API重启 |
| 仍需补实现 | 自动401续期/安全重放的显式任务关系与持久检查点，见7.65；不能把现有显式refresh顺序称为自动续期 |
| 已有实现但更广范围尚未验收 | 默认AI自主规划/工具调用质量、完整准备生命周期/计划修订、复杂UI和失败定位、更广持续性能/安全、需求业务评审/部署/历史迁移、全阶段调用追踪及旧Worker原件 |

历史报告未批量重写，本批不声称清除未知或编码后的所有凭据。模板/Excel/JSON项目页面、独立Git来源等已有对应验收，不因本批没有重跑而列为缺实现。Step已认证，外部AI沿用户此前安排暂缓；完整默认AI项目选取→排序→补全→执行及完整P0—P5仍未完成。三份方案同步，限定相关文件中文commit，不推送/不建PR。

### 7.68 Worker 原始结果私有回读与刷新凭据传值〔2026-10-18〕

基线`2f8e371c`，调研见`P3_API_PRIVATE_RESULT_RESEARCH.md`。复核发现 Worker 公开 Job 必须脱敏`refresh_token`，但父协调器此前把公开`task_result`直接用于后续`InputBinding`；真实 HTTP 复现中登录返回独立刷新凭据，刷新接口只接受请求体中的真实值，直接路径3次请求全为200，而旧 Worker 路径发送`[REDACTED]`，得到401并跳过profile。该失败证据保留在`p3-private-refresh-reproduction/`，不能把旧的 Access Token 头部刷新测试当作覆盖。

**本批补实现并已验收**：沿现有加密 PostgreSQL 业务存储增加不可变 Worker 原结果表，引用按父会话、子会话和原 ToolJob 稳定生成；保存前校验冻结派发关系和真实 Job 身份。Worker 在公开投影前保存原始`task_result`，公开 Job/提示/父快照仍只保留脱敏结果和不透明`private_result_ref`。`ApiWorkerResultReader`先完成原 Job、项目、父 turn、派发和请求身份核验，再受限回读原始结果；缺少、冲突、篡改或错误密钥不会回退使用`[REDACTED]`。

完成结果：真实本地 HTTP 的login→refresh(body)→profile路径3次均200；公开 Job、父结果、快照、进程输出及加密存储明文检查均不含临时凭据。登录完成后和刷新完成后两个PG快照暂停点，重建服务继续均无重复HTTP；实际子进程强制终止后新进程恢复3项任务，原Job保持终态，终态重投新增HTTP为0。错Job/父会话/turn/项目、随机引用、检查点引用冲突、缺Job身份、缺密钥和不可变内容冲突均在业务请求前拒绝。`p3-private-refresh-process.xml`为2通过，`p3-private-refresh-report-product.xml`为1通过，新增边界专项`p3-private-refresh-final.xml`为6通过；受影响API回归`p3-private-refresh-delivery.xml`为355测试、349通过、6门控跳过、0失败。完整结果和范围见证据目录；无外部AI调用。

| 分类 | 当前状态 |
|---|---|
| 已有实现 | 原凭据加密引用、冻结派发、公开输出脱敏、Job终态恢复和统一输入绑定 |
| 本批补实现且功能验收 | Worker原始响应私有保存/受控回读、刷新请求体传值、跨进程恢复、引用隔离和终态不重投 |
| 仍需补实现 | 自动401续期/安全重放的显式任务关系、只读/幂等策略和持久检查点；本批只修复其前置传值缺口，不把显式refresh顺序称为自动续期 |
| 已有实现但更广范围尚未验收 | 默认AI自主项目选取→排序→补全→执行、完整准备生命周期/运行中修订、复杂UI、持续性能/安全、需求业务评审/部署/历史迁移及全阶段调用追踪 |

配置仍保存于MySQL，业务/凭据/Worker原结果引用保存于PostgreSQL，大结果沿现有工件机制；无新模型协议或统一化模型适配改动。Anthropic仍使用原生`client.messages.create`和`/v1/messages`，`await_provider_io`只负责网络I/O错误边界。Step已认证，外部AI按此前安排暂缓，完整P0—P5仍未完成。

### 7.69 API 自动401续期、安全重放与真实进程恢复〔2026-10-18〕

基于7.65的安全边界调研继续实施。复核现有 `ApiTaskPool`、本地 `ApiTestCoordinator`、Worker `ApiSubagentCoordinator`、凭据引用和检查点后确认：恢复必须由两个 Coordinator 共用同一状态契约；不能在普通依赖解析后再把401目标当作普通 ready 任务，也不能在刷新已完成但重放尚未开始时重新发送刷新请求。

**本批补实现**：增加显式 `auth_refresh_task_id` 与 `allow_401_replay` 关系的恢复状态机；目标首次遇到非预期401后进入 `pending`，刷新任务才释放；仅复用已完成的刷新结果，保存 `refresh_completed`、`replay_running`、`replay_completed`/`replay_failed` 等检查点；恢复阶段遇到已运行但无终态结果的刷新或重放，标记 `recovery_unsettled`，不盲目重发。目标仍只允许 GET/HEAD/OPTIONS，预期401、写请求、超时、连接异常和未知副作用不进入自动重放。新增完成/重放开始检查点事件，便于父状态在阶段边界持久化。

**实际验收**：

- 本地真实HTTP自动续期专项：原始 `/login`、第一次 `/profile`（401）、`/auth/refresh`、重放 `/profile`（200）按预期执行；刷新请求体使用真实刷新凭据，公开结果不含凭据；自动续期、预期401、写目标、刷新失败、重放再次401及认证祖先约束均通过。
- 真实隔离 PostgreSQL、MySQL、Redis、RustFS 和 Worker 子进程强制终止/重启：在登录完成后、401待刷新、刷新完成待重放三个检查点分别终止进程，新进程只继续缺失阶段；每场景HTTP严格为四次，刷新只一次，终态重投新增HTTP为0，原Worker Job保持终态，父公开快照无凭据。`p3-api-401-auto-process-final2.xml` 为 **3通过、0失败**，事实见 `p3-api-401-auto-process-final2/`。
- 受影响回归 **124通过、16门控跳过、0失败**，命令及完整输出见 `p3-api-401-auto-process-regression.xml`。门控跳过项仍不计为通过。

| 分类 | 当前状态 |
|---|---|
| 已有实现 | 原生模型适配、API执行器、凭据引用、Worker私有结果和PG检查点基础设施 |
| 本批补实现且功能验收 | 显式401续期关系、只读一次重放、完成结果复用、两个Coordinator恢复状态机、真实Worker跨进程恢复和终态不重投 |
| 已有实现但更广范围尚未验收 | 默认AI项目选取→排序→补全→执行、完整准备生命周期/运行中计划修订、复杂UI知识与失败定位、持续性能/安全、需求业务评审/部署/历史迁移、全阶段逻辑/SDK/HTTP追踪及旧Worker原件 |

本批没有新增数据库表、配置项、外部AI调用或模型协议；配置仍在MySQL，业务/凭据/Worker结果引用仍在PostgreSQL，大对象仍沿RustFS。Anthropic继续使用`AsyncAnthropic.client.messages.create`和`/v1/messages`，OpenAI Responses仅由OpenAI Responses适配器使用。Step已认证，外部AI继续按用户安排暂缓；完整P0—P5仍保持进行中。

### 7.70 默认 Step 完整用例规划：执行项操作预读组合实测〔2026-10-18〕

基线`6c205ca4`。依据本地默认模型实测和`P4_ITEM_OPERATION_SELECTION_RESEARCH.md`，先复核live13配置只启用了分任务回执，没有启用执行项操作预读；不能用那次超时判定预读协议失效。现有实现已由组合配置/Prompt运行时检查确认可同时启用分任务回执、完整来源身份、证据引用和执行项操作预读。组合专项回归`test_planning_item_operation_selection.py`、`test_planning_effect_tasks.py`、`test_planning_operation_prefetch.py`、`test_scene_query_contract.py`为**38通过、18门控跳过**；跳过不计通过。

按同一真实默认Step、同一`low + json_object`调用配置及同一180秒规划截止，新增只读变量`QA_ITEM_OPERATION_SELECTION=1`运行真实隔离API/Worker/MySQL/PostgreSQL/Redis/RustFS、真实上传需求文档及正式八步用例。模型在逻辑阶段为正式目标固定`api-test-runner.execute_task`选择；系统随后按`case-planning-item-operation-prefetch-v2`只读取得命名操作契约，PG检查点记录`origin`、`logical_hash`、`contract_loaded=true`。故障阶段不再需要额外模型轮次寻找该接口，预读策略实际生效。

**结果仍未通过**：真实默认规划3轮（logic 56.97秒、bindings 49.20秒、produced 72.75秒）。bindings回执成功固定；produced调用收到HTTP 200并持续收到SDK流，但截止时`CancelledError`，`sdk_iteration_finished=false`、`finish_reason=null`，正文0字符；截至取消累计completion 5658 tokens，其中reasoning字段5658。原规划deadline耗尽，任务`planning_failed`，没有创建Run，隔离目标业务HTTP为0。物理请求只保存请求身份/哈希、计数和模型流阶段计数，不保存私有reasoning或响应正文。

与live13逐项对照：live13用了4次模型调用（17.07/44.08/37.95/79.85秒），最后produced请求为29,732字节、上下文含4项evidence和3条query_results；live14系统预取后减为3次调用，produced请求24,458字节、2项evidence和1条预取query_result。两次最终produced调用都收到了HTTP 200和持续流式reasoning、正文0字符后被180秒deadline取消，completion分别为5,178/5,658 tokens。因此已确认缺陷不是工具契约重复查询或仅由更大的live13上下文造成；成本集中在默认模型对produced结构化声明的长推理，现有日志尚不能判定应调整Schema/职责还是模型生成边界。预读已解决一轮冗余检索，但不代表复杂产生效果声明已完成；不擅自延长生产deadline、扩大模型预算或改全局MySQL模型配置。

原始文档、完整八步JSON、冻结请求、PG规划快照、三次HTTP/SDK计数、失败XML及请求profile位于`p2-default-coverage-live14/`和同名XML。受影响确定性回归命令为`..\\.venv\\Scripts\\python.exe -m pytest -q tests/test_planning_item_operation_selection.py tests/test_planning_effect_tasks.py tests/test_planning_operation_prefetch.py tests/test_scene_query_contract.py`，实际**38通过、18跳过**。真实模型命令及结果见`p2-default-coverage-live14.xml`；没有源码改动。下一步先对照live13/14已冻结produced请求、任务Schema和工具证据定位模型为何耗尽流式推理仍无正文，再决定是否存在可证据支持的规划输入/职责缺口；不以再跑同一请求或调高预算代替根因分析。完整P0—P5仍active。

### 7.71 Step 认证后默认完整用例失败现场与当前状态〔2026-10-18〕

用户再次明确 Step 已认证并要求继续。按此当前指示恢复真实 Step 验收；7.70 以前“暂缓外部 AI”的记录只代表当时安排，不再作为当前门槛。

复查 live16 原件后确认：logic 与绑定回执最终成功，第一轮 bindings 因模型使用完整来源 UUID 而不是规定的 `E1/E2` 短引用被正确拒绝，下一轮自行修正；最终 `produced` 调用使用 OpenAI-compatible Chat Completions `json_object`，HTTP 200，但达到隔离输出上限 16,384 tokens 后以 `finish_reason=length`、正文 0 结束。规划任务约 392 秒后失败，未到 600 秒策略期限；Run 和目标业务 HTTP 均为 0。证据见 `P2_EFFECT_TASKS_RESEARCH.md` 的“默认 Step produced 阶段现场根因复核”及 `p2-default-coverage-live16/`。

**当前判定**：这不是认证错误、Anthropic SDK/Responses 协议切换、物理 HTTP 重复发送或已证实的操作契约重复查询。全局模型配置未改。当前存在按传输分开的模型字段适配（Chat Completions、OpenAI Responses、Anthropic Messages、Gemini）；尚无证据要求将其合并，也没有证据支持放宽 canonical 来源/对象关系校验。

模板、Excel/JSON 页面已有真实项目流程验收；API 401续期和报告、录制网络事件及性能/安全报告等分别有专项真实验收，但均不代表完整 P0—P5。**按用例执行仍未完成**：默认 Step 的正式多操作用例未通过完整规划，尚未创建 Run；多候选排序/补全质量、准备状态与未知副作用查证、运行中计划修订、其他模式/App 和完整闭环仍待推进。

下一步不重放同一八步请求或调大生产预算。先依据 live16 工具契约与执行器真实输出边界，设计并运行同一默认 Step、相同策略和生产预算下的单目标少操作文档/正式用例，记录实际 planning、ToolRuntime、业务 HTTP 和 PostgreSQL 证据，判断失败与声明规模及单角色 Schema 的关系；只有证据指向可安全确定性派生时才研究服务端预填候选，继续保留现有 canonical 校验。完整 P0—P5 仍 `active`。

### 7.72 Step 单创建/清理真实规划复测〔2026-10-18〕

依据7.71所列下一实验，先扩展真实隔离模型验收夹具的场景开关，再上传新 Markdown 需求并创建只含“POST 创建一个订单、用真实返回 ID 执行 DELETE”的项目正式 JSON 用例。生产 API、Worker、MySQL、PostgreSQL、Redis、RustFS 和本地目标 HTTP 均实际参与；模型为当前已认证的默认 Step。全局模型配置保持原样；隔离调用 profile 为 `low + json_object`、max output 8192，项目规划 deadline 180 秒，启用同一组合分任务/来源/操作预读策略。

真实模型验收 **1失败、221.65秒**。logic 24.24秒和bindings 46.99秒完成，produced 使用最后108.02秒后被180秒规划 deadline取消；累计completion 6,964 tokens、reasoning流字段30,672字符、正文0，未收到 `finish_reason`，因此不是达到8,192 token上限。三次模型HTTP均200；项目正式文档原件已存并读回，但未创建Run，目标业务HTTP为0。证据及请求/流阶段计数见 `p2-default-coverage-live17/`、`p2-default-coverage-live17.xml`。真实测试因规划未达终态而失败，失败原件保留。

两步输入的 produced user上下文17,103字符，和live16八步上下文17,692字符相近，操作减少未使其在默认总期限内完成。这表明不能把故障仅归因于八步业务内容；它仍不能证明是哪条提示/schema职责产生了长推理。**当前方案正确性尚待下一轮研究**：按提示构造链逐段核对原始案例/工具契约、`ProducedTaskDecision`输出Schema与canonical校验，确认是否有可由已冻结事实无歧义确定的重复内容；在确认数据来源和所有用例/工具适用范围前不改生产策略、不自动预填对象关系。P0—P5继续active。

对`produced`请求继续沿代码追踪后，模型需为固定用例提供真实输入选择条件、响应对象路径、依据完整用例的断言检查及来源引用；服务端在实际原始ToolJob返回后核验身份/输入/哈希并保存证据。对象路径及写入语义取决于实际业务文档，不能仅凭通用`ApiTestTask`模型安全推导；删除或服务端自动接受声明会损害对未知结果和清理范围的校验。因此当前没有足够依据修改生产职责边界。对应的逐字段证据与候选研究边界见`P2_EFFECT_TASKS_RESEARCH.md`。下一步继续评估角色提示/Schema中哪些内容可从固定父回执、项目依据或冻结工具契约压缩，同时保证至少两类API、负向和重复调用情形仍经过原canonical校验；未完成前不宣称是代码缺陷已修复。

### 7.73 Step 提示 Schema 注释缩减与真实复测〔2026-10-18〕

检查确认规划回执仍通过原Pydantic模型和canonical校验；提示内嵌的JSON Schema是独立文本副本。对三个effect-task提示Schema递归移除非约束性的`title`注释，所有字段、必填、类型、枚举、引用、默认及边界约束保持一致；新增确定性结构测试。受影响回归 **39通过、25门控跳过**。produced Schema从6,050字符降至4,899字符。

同live17的隔离真实Step、low/json_object profile、正式两步创建/清理用例和组合策略重测，实际模型三次SDK/HTTP调用；logic 62.97秒、bindings 40.93秒、produced 74.86秒后在180秒总deadline被取消，completion 4,703 tokens、正文0、无finish reason。**1失败，未创建Run，业务HTTP为0**。produced请求正文从29,331字节降至27,886字节；该压缩保持Schema语义，但未让真实规划跨过produced阶段，不构成超时根因修复。证据见`p2-default-coverage-live18d/`及XML；两次夹具启动失败（模型未调用）也按原样保留在live18/live18b/live18c证据中。

当前：此步骤证明提示可去除冗余Schema注释，不能证明模型质量或端到端用例执行已改善；保留小幅prompt压缩，按用例执行仍未通过。真实运行使用OpenAI-compatible Chat Completions；Anthropic继续调用Messages API。按传输区分的适配映射无需因本次Step失败统一成Responses协议。三次夹具启动失败（profile层级、预建输出目录、误传timeout环境变量）均未调用模型，原件保留在live18/live18b/live18c。完整P0—P5继续active。

### 7.74 默认 UI 真实验收与证据投影纠错〔2026-10-18〕

先复核实际代码，确认持续command eval结构化result已实现，真实Chromium类型/同会话专项1通过，无须重复实现。新一批生产API、规划/执行/资源清理CLI、隔离MySQL/PG/Redis/RustFS及默认Step实测，三批完整正负场景分别1通过1失败、2失败、1通过1失败。前两批失败源于验收误将页面身份要求为Run投影内字符串，或要求每个观察都包含固定page_id键；原始正式结果分别为passed/failed且引用真实新页面ToolJob。修正为observation→Job引用→run/item归属→真实字面量路径→新执行页身份，支持实际eval表达式的page_id/pageId，保留无页面字段的合法动作/标量观察。新离线回放10通过；相关回归33通过3门控跳过。

第三批正向完整通过且终态重投业务/模型请求增量均0。负向真实点击一次后计数1、预期2；模型原回执前附未加围栏说明，进入一次只读纠正后8,192 tokens/length/正文0，正式error；保持失败，不改判。纠正原件已从RustFS回读并核验checkpoint哈希，响应仅导出公开字段。

进一步按单模型适配字段调研：原默认profile未声明结构化输出，纠正请求采用prompt fallback。沿已有low/json_object profile只写隔离MySQL副本，负向完整对照 **1通过，123.96秒**：默认Step知识读取/规划/实际点击/比较后正式failed，终态重投业务/模型请求增量0，原全局配置不变。本次未进入纠正阶段，不能证明解决纠正截断，也不等同原默认配置已稳定通过。当前模型协议和生产逻辑未改。

最新依据见`P5_UI_EVIDENCE_CURRENT_RESEARCH.md`，所有失败XML保留。分类：已有实现为结构化观察/知识读取/正式回执/恢复基础设施；本批补验收为简单UI正向、候选单模型profile负向及终态幂等；仍待验收/调研为默认配置负向与纠正质量、复杂UI/完整App、完整项目选取排序补全、准备/未知结果/计划修订及其余P0—P5。完整目标继续active。

### 7.75 普通文档 JSON 输出契约与默认 Step 真实模块验收〔2026-10-18〕

先按真实失败样本和实际读写契约确认生成 Schema 根因：新模板说业务只写 `content_json`，执行的 `business_content()` 也优先读取它，但生成 Schema 仍让新模型同时产生外层 legacy 业务字段与完整JSON。保留统一Pydantic解析模型和旧字段，只为模板/增量的新JSON请求生成窄投影：删除外层重复字段，`content_json` 必填且非空，Prompt Schema 与 `ModelOutputFormat` 一致；历史 frozen invocation、版本、哈希与读取不改。JSON请求Prompt版本升为 json-v3/document-v2/incremental-v2/frozen-v2，旧格式与版本保留。无依赖/数据库/API或供应商协议变更。

真实样本 `p1-generation-default-profile-current/original-1.json` 的首用例外层重复步骤/断言/前置/数据/清理，同时JSON已有完整内容；实际响应在8192 tokens截断。提取公开响应及来源SHA到回归fixture，不把完整坏响应接受或生产修补。首轮回归62通过3跳过；新/旧 frozen 均拒绝该截断响应，去掉重复键后业务含义、治理及说明逐字段相同，嵌套JSON语义保留。增量来源/生成任务/重试专项43通过0跳过（真实MySQL/PG/RustFS/Redis及受控HTTP）。compileall与git diff --check通过。

默认Step真实三章节需求再次跑新隔离项目，全部使用真实解析/生成API及Worker/数据库/对象存储，仅隔离模型profile为low/json_object，8192输出token、120秒单调用/原600秒任务期限；全局模型没变，未执行生成测试用例的业务请求。首次跑完整业务/恢复后，测试因自身误解 `GET /test-cases/{id}` 的正式响应结构而失败；按路由 `response_model=TestCaseRecord` 修正断言后，完整重跑 **1通过、345.35秒**。3模块9草稿入库；模型参数漂移模块发送前失败、恢复参数并重启API后完成独立新任务重试，终态重复投递无额外SDK请求。逐条人工检查新增9份结构化内容、范围和警告：无外层业务重复、未编造原文缺少接口细节；注册权限冲突如实作为评审问题。原始响应、scope、PG和RustFS对象留存并验SHA。证据/边界见 `P1_JSON_GENERATION_CONTRACT_RESEARCH.md` 及 `p1-generation-json-contract-current2/`。

**当前分类**：普通文档默认Step在隔离 low/json_object profile 下的三模块后端生成/恢复与新输出契约已经实际通过；原全局profile质量、完整项目页面流程、文档全量跨模块业务充分性和人工评审仍未通过验收。模板/JSON导入等以往项目页面专项不由此替代。模板以外的新 JSON 调用在请求结构上同样覆盖；前端及API DTO不变。完整P0—P5、按用例测试闭环及其他环境/模型仍未完成。

### 7.76 Accepted bindings 证据继承对照失败及当前进度〔2026-10-18〕

继续前先确认已认证默认 Step 可用，并复查 `p2-default-coverage-live18d` 原 frozen request/checkpoint 与 produced 声明 Schema。发现同一 `item_key` 的 `evidence_ids` 已由 accepted bindings 固定，可按 key 确定性继承；编写只读 wire projection 对照，成功保留 item 次序并拒绝缺失、重复、越界项的4个确定性用例。随后以 live18d 的真实 produced 输入执行**单次独立只读对照**：只移除模型需重复复制的 evidence_ids，accepted bindings 为唯一继承源；仍用隔离 MySQL、原 Step/`low + json_object`、8192 max tokens 和原约74.87秒阶段窗口，不恢复任务、不开放工具、不发业务HTTP。

真实模型 **1失败、74.879秒**：一个 SDK调用、一个物理 HTTP，HTTP 200；流持续73.964秒后按原窗口取消，2,103 chunks、completion usage 4,532、正文0、无 `finish_reason`，未收到响应。reasoning 与 reasoning_content 各记录20,850字符，作为同一流内容的重复字段，不相加。canonical 未接受、Run未创建、业务HTTP为0，全局配置不变。原始证据见 `p2-produced-inherit-bound-evidence-current/` 和 XML。该实测排除“重复输出 evidence_ids 单独导致超时”的根因判断；不证明继承字段无用，因为没有拿到可校验的模型声明。该候选不做生产实现、不重放同一请求。

当前进度：模板/导入已有实现及专项验收；API自动401续期、实际刷新传值和Worker跨进程恢复已专项通过；普通文档三模块生成在隔离 `low/json_object` profile 下已完成真实后端生成与恢复验收。按用例执行仍在 `produced` 规划阶段失败，未创建 Run；准备状态、未知副作用只读查证、计划修订和多候选排序补全尚未闭环。Schema标题缩减与 evidence 继承这两项输入简化都没有让默认 Step 在同等阶段窗口返回正文，现有证据不足以继续微缩提示或改变 canonical 职责。正式回执方面，默认 Step 实际只读纠正及“纠正意图已持久化、HTTP发送前杀API进程”恢复已有通过证据（`p2-default-minimal-case-live7.xml`、`p2-default-receipt-process-live6.xml`）；原始用例里这两项已不应再列未完成。纠正请求已发送后的断网/进程中断、供应商响应已持久化后中断仍未验。下一步先调研调用轨迹、重试/物理发送边界以及规划角色契约，再以至少两个不同 API 形状（含负向或重复调用）验证成功声明、canonical接受和恢复；不增预算、不放宽校验、不宣称完整 P0—P5 完成。

另对照用户提出的模型适配疑问，核查现有 `resolve_client`、`map_model_request`、各 provider client 及本地 SDK HTTP 合同：系统已有统一内部 `ModelInvocationRequest`/统一结果结构，再按 `openai_chat_completions`、`openai_responses`、`anthropic_messages`、Gemini 原生 transport 做专属字段映射；Anthropic仍明确调用 `AsyncAnthropic.client.messages.create`，测试地址为 `/v1/messages`。`await_provider_io` 只等待原 SDK 调用并归一网络I/O异常，不改变消息协议。运行 `tests/test_model_clients.py tests/test_model_completion_http.py tests/test_model_sdk_fault_http.py`，**95通过，95.20秒**；这些用真实SDK连接本地HTTP夹具证明所测序列化、endpoint、流式和故障边界，不代表外网真实供应商全覆盖。结论是该适配边界需要保留且当前已经存在；没有依据新建“统一Responses协议”或统一供应商参数，其他模型仍可按自己的字段与能力配置。

### 7.77 P0 逻辑调用与物理 HTTP 追踪边界调研〔2026-10-18〕

继续核对主图、ModelRuntimeService、LangSmith TraceContext、规划、生成和需求影响调用。现有 `trace_id`/`turn_id`、规划 `logical_call_id`、Worker `attempt_id`、生成 `request_sha256` 及影响分析 `call_id` 各有局部用途，不能互相替代；`TraceContext` 当前拒绝未知字段，模型 Runtime 的 LangSmith span 不包含统一逻辑调用标识，Provider SDK 内部重发也没有生产级 HTTP send 事件。因此当前生产证据能覆盖部分业务/逻辑调用，不能对全路径断言 SDK retry 数或实际物理发送数。

查阅锁定的 SDK 接口，OpenAI/Anthropic 均接受 `http_client`，Google GenAI `HttpOptions` 接受 `httpx_async_client`，LangChain OpenAI `ChatOpenAI` 接受 `http_async_client`。这些注入点说明保留各供应商原协议并接入统一观察接口存在可行入口；它们尚未验证自定义 client 的重试、超时、流式、资源关闭和并发语义，故没有改动生产发送路径。逐文件依据、版本和边界见 [P0_CALL_TRACE_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P0_CALL_TRACE_RESEARCH.md)。

**当前做到的步骤**：Step 已认证；P2 已完成到普通文档三模块生成的隔离 profile 全流程验收，但按用例执行仍停在真实 `produced` 阶段，最近一次只读实测 74.879 秒后未返回可校验正文，未创建 Run 且业务 HTTP 为0。现在转入 P0 调用追踪的架构核验；本小步完成了源码与锁定 SDK 注入点调研，尚未实现或验收实际 HTTP send observer。下一步先验证所有 SDK 的注入生命周期和重试/流式/并发行为，再实现最小跨 provider 观察接口并用本地 HTTP 夹具实测；不统一协议，不把应用层调用数冒充 HTTP 发送数。完整 P0—P5 仍未完成。

### 7.78 P0 Provider SDK 请求尝试观察首轮落地与异步 Hook 根因修复〔2026-10-18〕

在复核 SDK 公开注入点后，沿既有 ModelRuntime 调用上下文增加统一逻辑调用观察，并将 httpx2 client 注入 OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini 和 LangChain OpenAI-compatible client。请求摘要及安全日志只保留脱敏 route、method、请求尝试序号、HTTP 响应状态和耗时，不保留凭据、请求体或响应体；`logical_call_id` 可关联结果与日志。

真实 SDK/TCP 故障验收首轮发现观察 hook 实现违反锁定 httpx2 的异步调用约定：SDK 对 request hook 执行 `await`，原同步函数返回 `None`。Google 暴露 `TypeError`；OpenAI 将其包装为 connection error 并重复重试，TCP fixture 实际收到0个请求。修复为 async hook 后，完整五传输故障矩阵 **59通过**；模型客户端/HTTP 合同/故障三模块回归 **95通过，100.21秒**。覆盖重试、403/429/503、连接中断、超时、流式/取消、真实 endpoint/协议体和并发关联。Anthropic仍为 `client.messages.create`，fixture 验证 `/v1/messages` 与 Messages body；不涉及模型协议切换。

同时将 `http_send_count` 改名为 `http_request_attempt_count`。httpx2 request hook 位于 transport 发送之前，只能证明 SDK 已交给 HTTP client，不能独立证明网络字节或远端接收；response hook 可证明收到响应，本地 TCP fixture 的接收计数可证明本次测试实际到达。DNS/连接建立失败时，生产请求尝试数可能大于服务端接收数。当前完成的是**生产逻辑 ID → SDK HTTP 请求尝试/响应观察**，不是生产级精确物理发送计量。

本步骤分类：观察层已有实现并通过锁定依赖下真实 SDK/TCP 测试；全阶段 ID 传播、更多任务恢复/网络故障组合仍待验收；生产端精确远端接收数必须依赖服务端/网关证据，客户端 attempt 不能替代。已查锁定 httpx2 源码：传入 transport 会改变环境代理映射；包装内部 transport 需访问私有成员，且 transport 调用仍不能证明 DNS/连接成功或远端接收，因此不实现这种有副作用且证据不足的方案。依据更新在 `P0_CALL_TRACE_RESEARCH.md`。下一步核验并发/取消及各逻辑入口的 `logical_call_id` 如何贯穿 Worker retry/recovery；测试端继续用本地 TCP 真实接收数与 SDK attempt/response 日志做对照。完整 P0—P5 继续 `active`。

### 7.79 P0 全传输并发调用关联验收〔2026-10-18〕

依据 7.78 的边界，补做真实并行调用而不是仅依赖串行故障矩阵。对 OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini 和 LangChain 五条 transport 同时发起两个不同 `logical_call_id` 的请求，使用本地 TCP/HTTP fixture 记录服务端实际接收、SDK request attempt、response hook 和最终 `ModelInvocationResult`。

五条 transport **5通过**：每次结果保留自己的逻辑 ID，每次 attempt 序号从1开始，收到各自200响应，服务端收到2个请求且没有跨调用串线；日志未出现测试 API key。此前模型客户端、HTTP合同、故障及并发回归合计 **100通过，101.48秒**。这证明当前 ContextVar 观察上下文在并发任务中隔离，不能扩展为已证明所有 Worker 恢复路径都正确。

本步骤分类：跨 provider 的并发逻辑调用与 SDK attempt/response 关联已实际通过；普通 turn、持久 Worker retry/recovery、生成/规划/影响分析等入口的全阶段恢复核对仍待继续。生产端远端实际接收仍需服务端/网关证据。完整 P0—P5 继续 `active`。

### 7.80 P0 发送前失败出口的调用身份补齐〔2026-10-18〕

继续按“逻辑调用 → SDK attempt/response → 结果”的链路审计，确认 `ModelRuntimeService.invoke` 在模型不存在、请求参数不支持、认证异常、缺少密钥以及运行参数漂移等发送前出口会提前返回或抛错。此前成功和 HTTP 失败结果已有 `logical_call_id`，但部分发送前结果与日志没有该字段，无法和规划、生成或影响任务已保存的逻辑调用对账。

本轮只补齐统一调用关联：发送前失败结果的 `response_summary` 及对应结构化日志都带原请求的 `logical_call_id`；没有改变 provider 协议、请求映射、重试、认证或任务恢复策略。为真实失败分支增加回归断言，覆盖五种失败模式。`tests/test_model_failure_lifecycle.py tests/test_model_request_profile.py tests/test_model_clients.py` **67通过**；`compileall` 和 `git diff --check` 通过。该修复只解决结果/日志关联缺口，不代表 Worker retry/recovery 的全阶段调用轨迹或远端实际接收已经完成；后者仍需持久任务与真实 MySQL/PG/RustFS 流程验收。

### 7.81 规划响应提交丢回执的真实 Worker 恢复验收〔2026-10-18〕

在实际 MySQL、PostgreSQL、RustFS 和本地 HTTP provider 上执行规划任务响应提交丢回执场景，覆盖原生非流式、流式截断和流式缺少终态三种输入。每个任务先完成查询回合，再发送第二个逻辑规划调用；第二回合响应已写入 PostgreSQL 后故意丢失确认，Worker 接管从已保存响应继续校验，不重新调用模型。

三种场景均实际通过：每个 `verification.json` 中有两个不同且持久的 `logical_call_id`，实际 HTTP 请求数为2，恢复阶段没有新增模型调用，终态再次投递也没有新增调用；原始响应、用量、截止时间和失败判定均从保存的检查点恢复。重新执行时测试先因缺少 `QA_PLANNING_COMPLETION_DIR` 在证据导出处失败，补齐专用证据目录后 `tests/test_planning_response_completion.py` **10通过**；需求影响持久调用回归 `tests/test_document_impact_calls.py` **19通过**。这证明规划响应提交丢回执的 Worker 恢复和调用身份链路已通过受控真实存储验收，不等同默认 AI 按用例执行闭环完成，也不覆盖纠正请求发送后或供应商响应持久化后的进程中断。

### 7.82 正式回执纠正两阶段 API 进程中断验收〔2026-10-18〕

先复核当前源码和上一份失败原件：旧测试读取已经不存在的 `lease_expire_at`，失败并不代表回执恢复逻辑失败；当前测试依据真实 Redis 返回的 `lease_expire_at_ms`，没有修改生产协议或租约实现。随后用当前测试源码启动隔离 PostgreSQL、MySQL、Redis、RustFS、真实 API 进程、Worker 和本地受控 HTTP provider，分别在回执纠正的 `invoking` 与 `response` 边界杀掉所属 API 进程，再等待原运行租约和资源租约自然过期，由生产恢复 Worker 接管。

`tests/test_receipt_process_recovery_live.py` 当前实测 **2通过、0失败、0跳过（690.50秒）**，新 XML 为 `p0-receipt-process-recovery-current.xml`，脱敏断言摘要为 `p0-receipt-process-recovery-current/verification.json`。两场景均记录原始计划、用例版本、模型调用、回执请求对象和数据库恢复状态；业务 HTTP 始终只有创建、查询、清理各一次，终态重投没有新增业务请求或模型请求，资源租约自然到期后清理回执为 `skipped` 且 Redis 活跃资源为零。

- `invoking`：纠正请求原件已保存，API 在等待供应商响应时被杀，恢复读取 `phase=invoking` 且无响应的固定检查点，保持正式结果 `error`/未知；不重发纠正请求，也不重复业务操作。
- `response`：供应商响应原件已保存并通过大小/哈希校验，API 在结果提交被 PostgreSQL 锁阻塞时被杀；恢复读取同一响应，最终结果为 `passed`，请求和响应对象均保持原哈希，终态重投增加的模型调用和业务请求均为零。

这一步关闭了**受控模型**下“纠正请求发送后”和“供应商响应已持久化后”的真实 API 进程中断缺口；它不等同默认 Step 的响应阶段验收，也不等同完整按用例执行闭环。默认 Step 目前已有“纠正意图持久化、发送前杀 API”通过证据，默认 Step 的响应已持久化后中断仍需单独设计可复现的探针和真实验收。原始快照目录保留在工作区，但包含租约令牌等运行字段，不作为提交内容；提交只包含脱敏摘要和 JUnit 结果。

### 7.83 默认 Step 回执纠正两阶段进程恢复验收〔2026-10-18〕

根据 7.82 的源码顺序调研，在测试探针中增加了仅由环境变量开启的 barrier：`AgentCaseReceiptService.correct()` 返回前，生产代码已经完成 RustFS `response.json` 写入和 PostgreSQL `phase=response` 检查点保存；探针在这个返回点写入无凭据 marker 后等待杀进程。原 `invoking` barrier 保持不变。生产 API、回执服务、模型协议和全局 MySQL 配置没有改动。

使用当前已认证 `step-5-preview`、真实隔离 MySQL/PostgreSQL/Redis/RustFS、默认流式 Chat Completions、真实 API/Worker 进程和本地健康检查目标，`tests/test_default_receipt_process_recovery_live.py` 两个边界实际 **2通过、0失败、0跳过（826.10秒）**；JUnit 为 `p2-default-receipt-process-response-current.xml`，脱敏汇总为 `p2-default-receipt-process-response-current/verification.json`。受影响确定性回归 `tests/test_agent_case_receipt_correction.py tests/test_default_receipt_process_recovery_live.py` 实际 **14通过、17门控跳过**；跳过项不计为通过。

- `invoking`：纠正请求对象已保存但没有响应，API 被杀后恢复保持 `error`/未知；不重发纠正，不重复 `/health`，终态重投新增模型请求为 0。
- `response`：marker 明确记录 `phase=response`，response 对象 URI、大小和 SHA-256 在杀进程前从 RustFS 回读校验；恢复最终为 `passed`，复用相同 response 哈希，不新增模型请求或 `/health` 请求。
- 两场景的目标 HTTP 都只有一次 `GET /health`；资源租约自然过期，清理后 Redis 活跃资源为空。

这一步关闭了默认 Step 回执纠正的两个进程中断边界，但不代表默认 Step 已完成“项目选取→排序→补全→执行”闭环；该闭环仍停在 `produced` 规划阶段，后续仍需处理默认模型规划质量、准备状态、未知副作用查证和运行中计划修订。测试探针和结果已提交，运行原始快照仍留在工作区但不提交包含租约令牌的原始文件。

### 7.84 默认 Step 普通文档全量模块生成与独立恢复复验〔2026-10-19〕

Step 账号已认证后，重新执行普通需求文档的真实默认模型专项 `tests/test_document_generation_default.py`。本次先复核测试源码和生成/解析/Worker/存储职责，再使用独立 MySQL、PostgreSQL、Redis、RustFS、API 和 CLI 进程；没有替换模型、修改全局默认配置或使用受控响应。实际模型为 `step-5-preview`，传输为现有 OpenAI Chat Completions，流式能力来自 MySQL 模型配置；配置仍不改变 Anthropic Messages、OpenAI Responses、Gemini 等其他适配器。

结果 **1通过、0失败、0跳过，285.94秒**，证据目录为 `p1-generation-default-current-20261019/`。真实流程覆盖 Markdown 原件、解析快照、完整文本/章节范围冻结、注册/任务创建/任务查询三个模块、配置漂移发送前拒绝、API 重启、单模块 `frozen_request` 独立新任务恢复、PG 版本和来源读取、RustFS 原响应读取以及终态重投。实际得到9份草稿版本，SDK调用3次，终态重投无新增调用；物理请求均为200，流式迭代以`stop`终止，配置恢复后全局默认模型指纹未变。

逐份读取生成结果确认本样本文档的唯一邮箱注册、重复注册拒绝、未登录写入拒绝、`done=false`/`retry_count=0`/`deleted_at=null`、空标题拒绝、本人任务隔离、每页20条上限和空结果合法性均进入结构化`content_json`，每份都有执行说明、解析快照来源以及模型/Prompt/Skill元数据。该证据把“普通 Markdown 默认 Step 三模块生成、进度和独立恢复”标记为**已实际验收**；长文档、更多格式、跨模块业务充分性、用户评审、完整项目页面和其他供应商仍是“已有实现、尚未验收”，不把本批扩大为P1或完整P0—P5完成。

### 7.85 默认 Step produced 原生 Schema 认证模型对照复验〔2026-10-19〕

为推进 P2 规划失败根因调研，复核产生阶段提示/Schema、工具契约、canonical 接受器与最新认证模型证据，确认 `produced` 是只读规划回执：需要映射固定 item 的真实操作输入、工具响应对象路径、业务预期检查及来源引用；其声明之后仍须进入原关系校验，不能以规划声明充当执行证据。SDK 适配保持现有 Chat Completions，不涉及 Anthropic 或 Responses。

使用 `tests/test_planning_request_contrast_live.py::test_captured_produced_request_contrast` 对已认证 Step 运行一次独立隔离的原生 Schema 对照。固定旧 produced 请求、原 83.915 秒剩余窗口、low、8192 和完整输入，只启用已有 `json_schema` 输出格式；MySQL profile 存在隔离数据库，实际源任务未恢复、工具未开放，PostgreSQL/RustFS 证据保持独立，全局默认模型参数复核一致。结果 **1失败，95.964秒含夹具**；证据见 `p2-produced-native-schema-certified-20261019.xml` 和同名目录。

请求实际收到 HTTP 200，SDK 流持续 82.762 秒、2,375 个片段，content 正文为0，reasoning/reasoning_content 各记录27,578字符，未正常结束且无 `finish_reason`；在原阶段窗口被取消。故这是同类“推理流有内容但没有结构化正文”的失败，不是 HTTP 建连失败，也不是 canonical 拒绝。模型未返回可校验回执，Run/业务 HTTP 均为0。不能据此声称原生 Schema 无普遍价值、不能增加生产预算，也不能把reasoning字段当可解释推理文本。

**当前做到的步骤**：P1 普通 Markdown 三模块默认 Step 生成、PG/RustFS 持久化、独立模块恢复及终态不重投已实际验收；按用例执行仍停在 P2 `produced`，未创建 Run。默认模板/Excel/JSON 页面、Agent API 401自动续期和Worker恢复、Docker/k6指标缺失与报告已有对应专项验收；复杂UI默认负向回执、其他模式/App、持续容量及全部安全组合仍未闭环。现有模型适配已具备统一内部请求/结果契约和 provider-specific 字段映射；Anthropic 继续 `client.messages.create` `/v1/messages`，不需要为当前 Step produced 失败新增“Responses统一适配层”。下一步沿完整原用例和实际 Agent 工具执行器核对执行前效果声明是否有可安全确定性推导的重复职责，再决定是否存在可实施的根因修复；原预算、来源与 canonical 校验不变。完整 P0—P5 继续未完成。

### 7.86 效果证据与最终业务回执的职责边界核查〔2026-10-19〕

依据最新完整用例与实际执行器调用链，检查 ToolJob 原始输出观察、效果声明编译/捕获、PostgreSQL 持久化、结果恢复及最终 Agent 回执，并运行 `cd Agent_Server; ..\.venv\Scripts\python.exe -m pytest tests/test_case_effect_evidence.py tests/test_case_cleanup_coverage.py tests/test_case_effect_relations.py -q`：**20 passed、40 skipped，6.70秒**。跳过项是显式门控的真实 MySQL/PostgreSQL/RustFS/native HTTP 场景，不作为通过证据。

确认 `_observe_job_output_sync` 在同一 PG 事务内保存可靠 ToolJob 输出/观察和效果证据投影，效果投影按原 `origin_job_id` 关联，读回时验证原输出哈希、固定绑定、意图和逻辑操作；结果交付中断时可从原可靠输出恢复，不重新调用工具。最终 Agent 回执判定完整用例业务断言；效果投影支撑副作用对象清单、未知状态和整个运行的清理覆盖。存在部分断言字段重叠，但删除声明/投影会丢失回执本身不提供的原始来源、恢复及运行覆盖职责。因此本轮没有生产代码改动，也没有根据重叠猜测精简结构。

这次只证明源码边界及非 live 回归，不证明所有工具出口均遵守同一观察事务、共享消费者完成证据已实现，或真实默认 Agent 规划失败已解决。后续先核查 `observe_output` 的完整调用集合、ToolJob 状态迁移及共享消费者终态证据，再评估是否存在可安全消除的重复字段/真实缺口；P2 默认 Step 仍停在 `produced` 阶段且尚未创建 Run。完整 P0—P5 继续未完成。

### 7.87 清理前消费关闭与覆盖报告状态核查〔2026-10-19〕

继续核对后确认 `ToolRuntimeService.execute` 是生产侧唯一 `observe_output` 调用入口；只有工具返回且归一化/清洗后状态为 `completed/partial/failed` 才生成观察。另查清共享消费门禁：启用 `case-consumption-phase-v1` 时，清理动作领取事务核验目标完整业务断言、消费者终态 Result、所有已启动 ToolJob 的固定身份与可靠返回观察；关闭回执和清理领取同事务落库，缺证据则拒绝发送。其证明表示消费阶段关闭，不表示 RunItem 已完成。

覆盖投影仍将 `consumption_completion_verified` 固定为 false，且当前不读取关闭记录；门禁可以阻止过早清理，但 Result/覆盖报告没有呈现此关闭证明。运行 `tests/test_case_consumption_phase.py tests/test_case_consumption_external.py` 得 **5通过、23门控跳过，4.15秒**；跳过的隔离真实服务场景不计作通过。由此确认了运行门禁已实现、覆盖报告证据仍未贯通，不把两者混称为“共享消费完整验收”。

下一步先核对消费记录的读入口、Result/报告 DTO、前端覆盖面板及依赖闭包恢复，再设计真实 PG/RustFS/live 场景验证部分/全部清理、共享消费者、确认丢失和终态重投；只有明确状态契约后才决定是否向覆盖报告投影关闭证明。P2 默认 Step 的真实 `produced` 规划未返回可校验正文、Run 未创建，仍是独立未解决问题。完整 P0—P5 继续未完成。

### 7.88 消费关闭证明投影到运行覆盖结果〔2026-10-19〕

沿 7.87 确认的证据链实施只读投影：`ToolJobService.list_case_effect_evidence` 从已验证的 `case_action_admission.consumption` 读取关闭证明，并按 `closure_sha256` 去重；`case_relation_coverage` 仅在协议、固定清理来源、完整消费者集合、运行拥有者及外部消费者终态 Result 证明匹配时，才将对应清理项与运行级 `consumption_completion_verified` 标记为真，并附上已核验关闭证明。该值只表示消费阶段关闭已验证，不改变 RunItem 状态。前端覆盖面板据此区分“关闭回执已核验”和“RunItem 已完成”。

逻辑回归实际运行：

```text
cd Agent_Server
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_case_consumption_phase.py tests/test_case_effect_relations.py -q --tb=short
```

结果 **7 passed、32 skipped，8.37秒**；跳过项是隔离真实服务门控场景，不计通过。前端 `npm.cmd test -- --run src/features/projects/CaseEffectCoveragePanel.test.ts` **5 passed**，`npm.cmd run typecheck` 退出码 **0**。已有隔离 MySQL/PostgreSQL/RustFS/native HTTP 真实验收工件覆盖直连工具适配器及 Agent 图的通过/失败结果，断言关闭证明只入覆盖证据、不提前完成 RunItem，失败业务结果仍为失败；原始工件含完整运行提示和身份字段，仅保留在本地验证目录，不纳入提交。

这一步补齐了已存消费关闭证明到运行覆盖结果/前端的展示，未证明关闭事务丢回执恢复、共享消费者并发、终态重投及覆盖预算边界。下一步按真实事务与恢复调用链分别验证上述边界；Step 已认证，P2 默认 AI 的“项目选取→排序→补全→执行”可继续真实验收，但截至本条仍停在 `produced`、未创建 Run。P0–P5 整体仍未完成。

### 7.89 消费关闭事务丢回执边界复核〔2026-10-19〕

依据 7.87—7.88 的调用链，复核 `PostgresCaseActionAdmission._claim` 与 `PostgresCaseConsumption.save`：关闭记录写入和原 ToolJob 从待执行状态领取为 `running` 在同一 PostgreSQL 事务提交；业务 handler 只会在 `_claim` 返回后运行。若提交已成功但客户端未收到确认，调用包装会返回 `CaseActionClaimUnavailable`，原 Job 与关闭记录不能被当作可安全重发的空状态。

核对已有真实隔离服务原始工件 `p2-consumption-phase-final/lost-claim/verification.json`：测试在真实 `_claim` 提交后模拟回执丢失、但在业务分发前抛出连接错误。原清理 Job 保持 `running`，同一操作别名被拒绝为 `case_operation_outcome_unknown`，再次执行原 Job 仍未获可靠确认；HTTP 序列只有两次创建和一次查询，没有清理 DELETE，两个对象均保留。该证据证明“提交回执丢失时不重复发送”的保守行为，不证明生产环境中网络中断发生在业务请求发送之后时能够自动判断远端是否已执行。

另核对 `p2-consumption-phase-final/rollback/verification.json`：在真实事务内消费关闭记录保存后注入失败，确认记录写入和领取同时回滚；随后正常清理各发送一次、两个对象均已清除。并发场景现有原始工件及测试覆盖四个相同逻辑清理调用只产生一次 DELETE。运行 `tests/test_case_consumption_phase.py tests/test_case_consumption_external.py tests/test_case_effect_relations.py` 得 **11 passed、41 skipped，6.41秒**；跳过均由真实 MySQL/PostgreSQL/RustFS/native HTTP 门控，历史实测工件单独核对，不把本次跳过计作通过。

因此，事务回滚、提交后且业务分发前丢回执、同逻辑操作并发领取已有实际证据；7.88 所列的空白需要收窄为：提交后进入 handler、远端已执行但响应丢失或 API/Worker 进程中断时的原操作查证/恢复；关闭记录已提交后的覆盖报告读取、跨进程重建与终态重投；以及完整默认 AI 的 P2 规划与执行闭环。当前没有证据支持再改关闭事务或放宽 unknown 拦截。下一步按现有 API 工具操作回执恢复机制逐路径核实远端已执行但本地未收到结果的真实语义，优先确认是否存在可安全只读查证的既有操作；没有确定性查证能力时保持阻断并保留未知结果。Step 已认证，P2 默认 AI 仍停在 `produced` 规划阶段、未创建 Run；完整 P0—P5 未完成。

### 7.90 API Testing Mode 未知写入查证能力与正式 Run 边界复核〔2026-10-19〕

复核时发现 7.89 的下一步描述没有区分 API Testing Mode 和 P2 正式用例 Run。API Testing Mode 已有显式查证机制：`ApiTaskExecutor` 将写请求超时/传输错误及写请求 5xx 标记为 `request_outcome=unknown`；`ApiTestCoordinator._is_retryable` 对 unknown 返回 false；`ApiTaskPool` 只允许关联 unknown 写任务的 GET/HEAD/OPTIONS 查证任务，并在原写任务状态未知以外时跳过查证。查证只记录 `effect_present`、`effect_absent` 或 `inconclusive`，不会把原写任务改为成功，也不会释放依赖继续执行。`ReportBuilder` 输出查证状态和 `reconciliation_required`，前端报告页已有查证状态字段；以上由 7.61—7.63 的实现及报告产品验收记录支持。

本轮实际复跑 `tests/test_api_unknown_write_recovery.py tests/test_api_task_executor_recovery.py tests/test_api_http_origin.py`，**36 passed、3 skipped，17.93秒**。其中本地 HTTP 服务实际观察写请求超时后仅收到一次 POST，再收到明确的只读 GET；有无副作用两种查证均不重放原 POST，重复 `run_all()` 没有额外 HTTP；写入 5xx 即使断言匹配仍为 unknown 且不重试。跳过项是显式真实 MySQL/PostgreSQL/RustFS 门控，不计作本轮通过。

边界：这些证据证明 API Testing Mode 的协调器/Worker及报告机制，不证明模型会在生产默认 Agent 中正确生成查证任务；也不证明正式 TestRun 内的 `api-test-runner execute_task` 在业务请求已到远端但 ToolJob 返回观察未持久化时，可以直接复用同一 Campaign 查证关系。正式 Run 仍由完整固定用例、工具轨迹、对象证据和 canonical 回执约束，不能把 API Testing Mode 的 task 字段或查证状态自动移植成正式用例结果。因而“API unknown 只读查证底层能力”归类为**已有实现、专项 HTTP/报告验收通过**；“正式 Run/默认 AI 自主查证及跨进程恢复”仍属**尚未验收，可能需补跨边界接入**。下一步应从真实 Run 的 API ToolJob 故障检查点验证这一接口边界，再决定是否有必要补实现；不重做 API Testing Mode 查证机制，也不放宽未知状态阻断。Step 已认证，P2 仍未完成，P0—P5 未完成。

### 7.91 正式 Run 远端已执行但回执丢失的真实边界〔2026-10-19〕

继续核对正式 Run 后发现已有更直接的真实证据，需更新 7.90 的分类。读取 `p4-effect-research-current/cleanup-return-unknown/verification.json`：该记录的 `execution_strategy` 为 `agent`，使用隔离 PostgreSQL/RustFS 及真实本地业务 HTTP。创建对象后，清理 `DELETE` 已在服务端完成，但在可靠返回观察保存前模拟响应丢失；原 ToolJob 保持 `running`、没有可验证输出，恢复时使用同一业务输入但新的模型调用 ID，被逻辑操作索引拒绝为 `case_operation_outcome_unknown`，`action_sent=false`，没有再次发送 HTTP。工件同时明确 `original_remove_actually_completed=true`、`durable_remove_return_known=false`、`no_new_HTTP_on_recovery=true`，并将 `full_effect_lifecycle_implemented=false`。

这证明正式 Run 已有防重发和未知状态保留的实际实现，且不是仅 API Testing Mode 的行为；它也证明系统不会因为现场对象已被删除就擅自回写原 Job 为成功。现有非门控正式执行回归 `tests/test_agent_case_execution.py -k "unknown_tool_outcome or crash_after_intent or http_completed_but_approval_snapshot_stale"` 为 **3 passed、3 skipped，21.35秒**；跳过项是同一测试的真实服务参数，历史隔离工件承担真实业务 HTTP 证据。

当前仍缺的是另一层能力：正式 Run 在未知状态下没有通用、自动推导的查询接口；只有当冻结用例、项目接口契约或 Agent 明确提供合法只读操作（例如带实际对象标识的 GET）时，才能追加查证。查证结果应作为新的有来源证据和计划修订输入，不能改写未知原操作或释放依赖而没有明确规则。因而该项重新分类为：**正式 Run 未知不重发与跨进程阻断已有实现并通过真实隔离验收；正式 Run 的只读查证任务接入、默认 AI 自主选择及查证后继续执行仍未验收，必要时需补跨边界接入**。下一步先用固定用例中已声明的读操作做一条真实 Run 查证链，验证查询的项目、运行、条目、操作身份和证据关联，再决定是否需要新增契约；不自动猜 URL、主键或幂等键。Step 已认证，P2 仍未完成，P0—P5 未完成。

### 7.92 固定操作预读与正式 Run 查证边界〔2026-10-19〕

继续核对 `case-planning-item-operation-prefetch-v2` 后确认：现有能力属于**规划阶段的只读工具契约读取**。模型在逻辑计划中声明 `item_key`、`tool_key`、可选 `operation` 和已读取的业务依据；服务端按声明读取冻结的完整工具或命名操作契约，保存查询来源、逻辑计划哈希和恢复检查点。未知操作不会根据 HTTP 方法、业务动词或标题猜测，也不会执行工具或改变授权。

依据是 `planning_operation_prefetch.py`、`PlanningOperationSelection`、`CasePlanningWorkflow` 及 `test_planning_operation_prefetch.py`。非实时回归为 **6 passed、10 skipped**；在当前配置下运行两个隔离实时场景（`False-none`、`False-unknown-operation`）均为 **1 passed**。实时用例使用真实 MySQL 配置读取项目策略、真实 PostgreSQL 临时任务表和 RustFS 原件保存；未知操作场景只追加明确的 `api-test-runner.execute_task` 契约读取，没有业务 HTTP 请求。另运行 `test_case_operations.py -k test_actual_completed_read_replay_and_deliberate_refresh_are_distinct` 的真实隔离场景为 **1 passed**：`case-operation-v1` 对同一 GET 回放不发 HTTP，更换 `operation_key` 才发送一次新的 GET；该证据保存在 `Agent_Server/docs/upgrade_2026-10-03/p4-explicit-read-live-current/read-refresh/verification.json`，但它仍是底层运行时测试，不是正式 Run 查证闭环。

因此当前分类为：**规划阶段显式操作契约预读已有实现并通过真实隔离验收；正式 Run 的未知副作用只读查证仍未实现/验收**。后者不能直接复用规划查询结果：查证必须是冻结用例或项目契约明确声明的合法只读业务操作，创建新的 read operation/evidence，关联原 `project_id`、`run_id`、`run_item_id`、原逻辑操作身份及证据；不能改写原未知 ToolJob、重用原 operation identity、释放依赖或自动猜 URL、主键和幂等键。下一步应先为一个固定用例显式声明“原操作未知后的只读查证操作”，再验证正式 Run 的领取、HTTP、PG/RustFS 证据和后续状态转换；在该链路未验证前，不新增通用自动推导器。

### 7.93 正式 Run 查证回执原子保存与证据投影〔2026-10-19〕

根据 7.90—7.92 的职责边界，先补了正式 Run 已声明查证操作的持久化缺口。`CaseOperationVerificationDeclaration` 现在显式保存存在/不存在状态码集合；重复、重叠、越界状态码拒绝，旧声明的空集合保持兼容但结果为 `inconclusive`。查证请求仍只允许固定声明的工具、操作键、HTTP 方法和路径模板，未新增 URL、主键或幂等键推导。

工具返回经过现有输出清洗后，`ToolJobStore.observe_job_output` 在内存和 PostgreSQL 实现中同时保存工具输出、`tool_output_observation` 和查证回执的分类观察；分类只读取持久化 `task_result.response_status`，工具失败、响应缺失或未声明状态均为 `inconclusive`，不改写原未知写操作。`ToolRuntimeService` 使用最新保存的 Job 投影回执；正式 Run 的 `evidence_manifest`、工具证据引用和完成回执实际带出查证关系、SHA 和分类结果。

本次代码级功能测试：`tests/test_case_operation_verification.py` **9 passed**；受影响回归 `tests/test_case_operations.py tests/test_case_effect_evidence.py tests/test_tool_operation_observation.py tests/test_agent_case_assessment.py` **59 passed、31 skipped**；`compileall` 通过。跳过项由真实服务门控，尚未证明真实 PostgreSQL/HTTP 查证闭环、默认 AI 自动选取查证、查证后计划修订或依赖释放；这些继续归为**已有实现但尚未真实验收/仍需补跨边界实现**。Step 模型仍已认证，Anthropic 继续 Messages API，OpenAI Responses 仍保持独立适配器。

随后开启现有真实隔离服务门控，执行 `tests/test_case_operation_verification.py::test_live_http_and_postgres_job_persist_verification_observation`，结果 **1 passed，5.84 秒**。场景通过实际本地 HTTP 服务先 POST 创建对象，再由正式 `ToolRuntimeService` 发出一次 GET；临时 PostgreSQL ToolJob 表读回 `response_status=200`、`effect_present`、查证 SHA 和工具输出，HTTP 序列为一次 POST 加一次 GET。该证据确认新增原子保存路径可用，但场景预置了合法读操作，没有模拟未知 POST 返回丢失，也没有证明默认 AI 会自动选取查证或查证后释放依赖；正式 Run 的未知写入自动查证、计划修订和真实进程中断恢复仍未验收。

### 7.94 正式 Run 声明查证未知写入的真实闭环〔2026-10-19〕

针对 7.93 仍缺的正式 Run 边界，先沿 `AgentCaseExecutionAdapter` 建立真实执行检查点，再在同一冻结 `item_key` 内声明 `target.create → target.verify` 的查证关系。声明固定工具、`execute_task`、GET、路径模板及 200/404 解释；没有新增 URL、主键或幂等键推导，也没有把 API Testing Mode 的 task/campaign 字段移植到正式 Run。

真实隔离场景 `tests/test_case_operation_verification.py::test_live_formal_run_verifies_unknown_write_with_declared_read` 使用现有正式 Run、PostgreSQL ToolJob/operation index、MySQL 冻结策略和本地业务 HTTP。先让 POST 已在服务端创建对象，再在可靠输出保存前抛出连接错误；原 ToolJob 保持 `running` 且没有输出。随后只执行冻结声明允许的 GET，HTTP 序列严格为一次 POST 加一次 GET；GET ToolJob 读回 `effect_present`、HTTP 200、来源 operation、查证 operation 及 SHA，原 POST 没有被改写或重发。PostgreSQL 原始 operation index 同时读回 `target.create` 和 `target.verify` 两条独立逻辑操作。该测试通过 **1 passed，7.91 秒**；查证文件全量回归为 **11 passed**，受影响操作/证据/观察/评估回归在设置其约定证据目录后为 **89 passed、1 skipped，117.19 秒**。首次回归的 23 个失败均为未设置 `QA_STATEFUL_RESEARCH_EVIDENCE_DIR` / `QA_EFFECT_EVIDENCE_DIR` 的证据落盘门控，重跑已通过。

本场景响应是小型 JSON，沿现有内联 ToolJob 输出保存，没有生成 RustFS 工件；因此本次证明的是 PostgreSQL 运行/操作/查证回执关联和真实 HTTP 效果，不宣称 RustFS 大工件路径已由该场景覆盖。当前分类调整为：**正式 Run 在冻结用例明确声明合法只读操作时，未知写入的只读查证、PG 回执和不重发边界已有真实验收**；**默认 AI 是否能正确选择并生成查证声明、查证结果是否能自动修订后续计划或释放依赖、查证阶段进程中断恢复、带 RustFS 大工件的证据关联仍未验收**。原未知操作继续保持未知，查证结果是新增证据，不能把原操作回写为成功。

### 7.95 固定录制场景读取契约与默认模型 UI 断言〔2026-10-19〕

调研当前知识工具契约、冻结用例来源和真实 UI 工具输出后，确认两个确定性问题：固定 `scene_read` 已由 `scene_id`、`version_id`、`content_hash` 唯一定位，工具 Schema 却仍强制要求只用于文本检索的 `query`；执行提示也没有区分场景版本来源与原始 `recording_id`，并将 `browser-control` 的 eval `result` 路径语义容易泛化到非 eval 命令。修改范围沿用现有知识工具、提示契约和输出 Schema，没有添加新协议、数据库字段或通用工具。

`tests/test_scene_knowledge_live.py` 的真实隔离验收实际创建录制、整理场景版本并写入 PG/MySQL，再通过生产 `knowledge-rag` 工具在不传 `query` 的情况下读取相同完整知识；跨项目读取被拒绝。运行 `tests/test_ui_case_knowledge_contract.py tests/test_scene_knowledge_contract.py tests/test_scene_expectation_contract.py tests/test_recording_quality.py tests/test_recording_document_scopes.py` 得 **48 passed，5.34秒**。

随后以当前 MySQL 默认 Step 配置、默认请求参数、隔离 MySQL/PostgreSQL/Redis/RustFS/API/Worker 和正式 Run，对冻结场景知识执行正负向真实页面测试。`tests/test_default_ui_scene_live.py` 得 **2 passed，273.39秒**：默认模型通过精确场景 ID/版本/哈希读取录制知识，在新的页面实例只点击一次并以实际页面值判断；正向计数 1 与预期相符，正式结果 `passed`；负向期望 2、实际值为 1，正式结果正确保留 `failed`。两例的页面 POST 序列均各含录制页和新执行页的一次操作；终态任务重投后新增业务请求、模型请求均为 0。场景对象、ToolJob、原始浏览器 transcript、PG Run/Result 及物理模型请求响应保存在本地证据目录 `p5-default-ui-scene-contract-current-20261019/`。

本次仅证明固定场景读取契约、默认模型在受控页面使用录制知识、真实点击/断言、正负结果及终态不重放。页面由隔离业务夹具提供，不是 DeepSeek；因此默认 AI 读取 DeepSeek 实际录制知识、人工浏览器录制、正式用户授权界面和完整产品流程仍未验收。当前工作树的 UI 提示、工具 Schema 与回归测试已通过上述验证，其他并存改动未纳入本项提交。

### 7.96 多模型适配层现状与协议隔离回归〔2026-10-19〕

按用户提出的跨模型适配疑问，重新检查 `model_clients/registry.py`、四个原生 provider client、`request_mapping.py`、模型配置档案及调用结果契约。系统已有适配层：上层使用统一 `ModelInvocationRequest`/`ProviderClient`/结果字典；Registry 按显式 `transport` 选择 Chat Completions、OpenAI Responses、Anthropic Messages 或 Gemini；消息、工具调用、响应及流事件由各 provider client 原生转换。`map_model_request` 只统一内部选项，按 transport 映射 JSON 输出、推理字段和 token 字段，并对未声明能力作拒绝或记录 fallback。没有发现把 Anthropic 改成 Responses 的实现：Anthropic 仍调用 `client.messages.create`，OpenAI Responses 仅由单独的 `OpenAIResponsesClient` 调用。

实际运行 `Agent_Server` 下 `..\\.venv\\Scripts\\python.exe -Xutf8 -m pytest tests/test_model_request_profile.py tests/test_model_request_profile_product.py tests/test_model_clients.py -q --tb=short`，结果 **56 passed、1 skipped，27.28秒**。定向 HTTP 测试检查 Anthropic JSON Schema 映射到 `output_config.format.schema`，OpenAI Responses 映射到 `text.format.schema`；各自保留不同端点及请求体。由此确认适配层并非缺失，不应再添加单体“统一协议”或把 provider 特有字段硬塞进共享 SDK 调用。

分类：统一内部契约、显式 transport 分派和字段映射为**已有实现并通过本轮定向回归**；所有 provider/profile/SDK 版本/真实账号组合仍**未全部验收**。以后新增字段能力时仍需分别验证目标 SDK 的真实请求和返回语义，并保留未声明能力拒绝路径。此项没有生产代码、配置、数据库或协议改动，也未触碰工作区其他未提交改动。P2 默认 Step 完整用例执行仍未完成，最近完整记录停在 `produced` 规划回执阶段，完整 P0—P5 继续未完成。

### 7.97 P2 默认模型输出上限对照及结构化规划失败复核〔2026-10-19〕

复查工作区中晚于 7.96 的原始实测后，纠正“下一步尚未做 16K 输出上限对照”的过期记录。隔离默认 Step 用例 `p2-default-single-staged-low-16384-20261019/verification.json` 使用 16,384 `max_output_tokens`、600 秒规划期限及同一正式用例/阶段协议；真实 SDK 流记录到 5 次逻辑调用，任务最终失败，消息为“模型没有返回有效的结构化计划”，实际 Run 与业务 HTTP 均为 0。提高单次输出上限本身没有完成规划，不应重复该单变量实验，也不能据此推断只需继续加大上限。

较新的完整目标试验 `p2-default-full-reference-feedback-8192-live7-20261019/verification.json` 使用认证 Step、完整正式用例和来源引用反馈，但实际隔离 profile 为 8,192 输出上限。根据 PG 阶段回执及 SDK 调用 ID→物理请求 ID→响应 ID 的关联，真实顺序为 `logic → bindings → produced`，不能按请求文件名排序推断调用顺序。第三轮 `produced` 流返回 `finish_reason=length`、`completion_tokens=8192`、正文 0 字符；前两轮均以 `stop` 结束并分别保存逻辑选择与 `bindings` 回执。任务仍以“模型规划输出达到生成上限，响应不完整”失败；未创建 Run、无业务 HTTP。此试验的有效 `request_profile` 为 `null`，请求映射按未声明能力回退到提示 Schema，所以物理 Chat Completions 请求不含 `response_format`，不是适配器漏发已配置字段。原始请求文件分别为 19,946、27,952、32,775 字节，system/user正文长度分别为 7,096/8,208、4,653/16,967、8,278/17,577 字符。

结论：这是**模型输出预算/推理成本与 `produced` 结构化回执完成能力尚未解决**，而非 HTTP 传输、协议选择或 SDK 重复发送问题。认证模型证据显示首个正文可能延迟至约 76 秒；`produced` 输出仍可能在 reasoning 后耗尽，简单扩大规划期限或改变 provider 协议没有依据。隔离 profile 的 16K 对照已实际启用 JSON 输出声明但仍未产出有效计划，因此单独增加 JSON 格式映射也不是充分修复。下一步需沿该次 `produced` 输入、固定 scope、阶段Schema、保存响应/解析状态和 canonical validator 还原精确失败边界，优先确认模型是否曾返回可解析正文、是否被本地结构校验拒绝及拒绝反馈具体字段；不得把 reasoning 内容作为可见推理依据、不得重发终态任务，也不得在该边界查清前放宽引用、效果/清理契约或调整生产全局预算。

本项仅更新调研记录，未修改生产代码、MySQL 配置或 PostgreSQL 业务数据。P2 默认 Step 项目选取→排序→补全→执行仍未验收，完整 P0—P5 继续未完成。

### 7.98 `produced` 空正文的本地响应链复核〔2026-10-19〕

按 7.97 的下一步检查 `planning_workflow`、Provider 流转换和 `completion.py`。Chat Completions 适配器只把 `delta.content` 拼入模型正文，推理字段独立保留在观测侧；规划 Worker 将正文与 `finish_reason` 一起持久化，恢复读取已保存响应。进入响应校验时先把 `length` 分类为 `truncated`，在 JSON Schema 解析之前拒绝，保存原响应和截断诊断，不自动重发。`live7` 的第三次调用记录为流正常结束、8192 completion tokens、`finish_reason=length`、模型正文0字符；与代码路径一致。

因此本次失败没有证据指向本地漏拼正文、错误解析或错误使用 reasoning 字段。拒绝截断响应是正确的数据完整性行为；放宽 canonical/schema 校验会让不完整计划进入正式 Run，不能作为修复。该核查不证明模型阶段设计与输入投影合理，也不代表 P2 通过。此前 16K 对照已失败，不再重复只增加上限、改变 Provider 协议或将推理文本当作计划正文的单变量尝试。后续需研究 `produced` 阶段职责与输入投影，保持用例、引用、效果/清理关系及全局预算契约。

研究详情与对应代码/实时证据见 [P2 produced 响应分类调研](P2_PRODUCED_RESPONSE_CLASSIFICATION_RESEARCH_20261019.md)。当前状态：Step 已认证；P1 模板/导入及普通 Markdown 模块生成有专项验收；P2 默认 AI 仍未完成真实项目选取→排序→补全→执行闭环。其他 P0—P5 按本方案进度表继续区分已有实现、未验收和待补实现。

### 7.99 默认 Step 驱动 Agent API Worker 实测〔2026-10-19，未通过〕

使用已认证的 MySQL 默认 Step 配置，通过隔离 API、PG/Redis/RustFS、生产 Agent CLI Worker 和本地受控认证接口，运行固定 `login → refresh → profile` campaign。真实实测为 **2 failed、4 deselected，391.59秒**：同进程子 Worker 三项均成功，但 `/profile` 实际发送两次，违反唯一任务期望；持久子 Worker 的 login 子任务完成，但父 turn 保持 `running`，没有进入 refresh/profile。两种模式均无目标 HTTP 错误或凭据校验失败；公开结果的秘密扫描通过。由于失败发生在最终断言前，终态重投零新增请求未运行，不能记为通过。

当前证据确认“默认 Agent API 完整流程仍未验收”，但尚不能判定 `/profile` 重复源自模型产生第二个合法工具调用，还是同一工具调用在 resume/dispatch 检查点重放；durable parent 未收敛也需从 child completion 通知、Outbox、ToolJob 和 snapshot 还原。现有夹具在最终断言前没有保存完整 PG 工件，证据缺口已列入专项研究。先补诊断并按真实现场定位，再决定是否修生产代码，不以 GET 幂等掩盖重复，也不臆测编排根因。

详见 [P3 Agent API 默认模型实测调研](P3_API_AGENT_DEFAULT_MODEL_LIVE_RESEARCH_20261019.md)。本次无生产代码、全局 MySQL 默认配置或业务规则变更。Step 已认证；P2 正式用例闭环仍未完成，完整 P0—P5 继续未完成。

### 7.100 默认 Agent API 父等待恢复修复与复验〔2026-10-19〕

继续 7.99 的真实失败调查，在隔离 PostgreSQL 的 parent Session 与 snapshot 时间线中确认：代理审批投影持有旧 SessionRecord 并整对象保存，会覆盖较新的 `interrupted + waiting_tool` checkpoint。修复沿现有 SessionStore 增加原子状态与 metadata 更新；PostgreSQL 行锁内使用当前持久状态，内存实现保持相同契约，只有当前仍为 `waiting_approval` 才转换到 `running`。原有 `mutate_metadata(metadata)` 单参数接口保持兼容。

双后端合同 `test_atomic_metadata_update_uses_current_status` **4通过**；等待/子通知合同 **22通过、4跳过**，启用真实 PostgreSQL 的等待场景 **3通过**，真实子等待领取/取消 **1通过**。SessionStore 值契约与子等待组合回归有 **81通过**；另一个 opt-in PostgreSQL 测试首次因漏传 `QA_TOOL_CHILD_WAIT_STORE_DIR` 在夹具读取阶段失败，补上独立证据目录后同一测试 **1通过**。使用已认证默认 Step、隔离 API/Worker/PG/Redis/RustFS 及本地认证 target，同进程与 durable child 均通过：**2 passed、4 deselected，268.24秒**。两场景各自三项 HTTP 均成功、父报告 3/3、9 次模型 HTTP 可对账，API 重启后终态重投新增业务/模型请求为 0，凭据扫描通过。证据位于 `Agent_Server/docs/upgrade_2026-10-03/p3-api-agent-worker-atomic-metadata-20261019/`，专项记录已更新。

本次只关闭已定位的陈旧审批投影故障及这组 API Worker 复验，不代表鉴权负向、更多续期/依赖传值、超时异常和完整 API 产品报告流程均已验收。模型适配层继续采用统一内部请求/结果契约与 provider-specific 原生客户端；P0 最新 SDK/HTTP 矩阵 **120通过**，Anthropic 仍走 Messages。P2 有单个自包含用例真实执行通过，但多候选项目选取、排序、逻辑补全、准备生命周期和其他测试模式仍未闭环；DeepSeek 人工 UI 录制、Docker 性能/安全工具到报告的完整证据链及需求变更业务评审也仍未全部完成。完整 P0—P5 继续进行。

### 7.101 默认 Step Agent API 正向认证链路双 Worker 复验〔2026-10-19〕

为接续 7.100 并确认当前工作区中的默认模型验收扩展，先检查 Registry 暴露的 `api-test-runner` 合并参数Schema及提示词对编排Agent/子Worker的角色区分；`api-testing-agent` 在非用例执行时不应直接构造 Worker 内部的 `worker_action`、`task`、凭据容器和 dispatch 引用。因此在模型调用边界按 Agent 身份投影Schema，保留 `api-executor-worker` 和正式用例执行的完整Schema。`tests/test_model_tool_visibility.py`、API派发契约、用例执行与Session回归结果为 **27 passed、17 skipped，63.05秒**；跳过项为显式隔离服务门控测试。

随后使用隔离 MySQL 配置表、PostgreSQL、Redis、RustFS、独立生产 API/Agent CLI Worker，以及本地受控认证目标，执行 `tests/test_api_agent_worker_live.py::test_real_agent_worker_private_auth_and_terminal_redelivery` 的 `default-model` 两种子 Worker 变体。真实默认模型为 MySQL 已认证配置 `step-5-preview` / `openai_chat_completions`；没有改全局模型配置。pytest 结果 **2 passed、2 deselected，235.45秒**，未选中的两项是受控模型变体。

同进程与持久子 Worker 两个隔离运行均实际只收到 `POST /login`、`POST /auth/refresh`、`GET /profile`，全部 HTTP 200；refresh 使用登录返回的旧凭据，profile 使用 refresh 返回的新凭据。父会话完成，报告均为 3 completed / 0 failed / 0 skipped，创建三个真实子会话；实际模型 HTTP attempt 分别为 8 与 10。重启 API 后任务历史保持，终态 Outbox 重投的新增目标 HTTP 与模型 HTTP 都为 0。测试断言公开报告及日志不含生成凭据，`verification.json` 的 credential scan 和 public-output scan 均为真；另以当前 MySQL 默认模型密钥扫描33个验收文件，未发现密钥字节。证据目录为 `Agent_Server/docs/upgrade_2026-10-03/p3-api-agent-worker-default-current-20261019/`。

因此，默认 Agent API 的正向认证/续期/依赖传值链路在此固定场景及两种 Worker 生命周期下已有真实通过证据；这不覆盖错误凭据、刷新失败、超时/断网恢复或其他 endpoint 依赖结构，也不代表 P3 API 全面验收。完整 P0—P5 和 P2 用例选择/排序/补全/正式 Run 闭环仍未完成。

### 7.102 正式 Run 执行中计划修订的存储与领取边界调研〔2026-10-19〕

为推进 P2“新缺口可修订尚未执行部分”，检查 `CasePlanRunService`、`plan_mapping`、`run_store`、`build_agent_case_context`、正式 Run 的业务动作准入/准备消费，以及 Smoke 模式的 `_revise_plan`。当前正式计划运行创建时，把计划、RunItem 映射、准备项 owner/phase 和依赖一起冻结；`validate_plan_bundle` 用该快照重新构造 Suite/Run/RunItem 并要求完全相等。Agent 上下文、依赖领取、业务动作绑定、环境准入和准备消费分别读取这些固定结构。当前没有正式 Run revision API 或 revision history。Smoke 的已有版本修订发生在执行前并要求重新确认，不覆盖正式 Run 执行中修订需求。

PostgreSQL `claim_items` 当前在单事务中读取 Run、处理阻塞项，再对 queued RunItem 使用 `FOR UPDATE SKIP LOCKED` 领取；并发领取依赖 RunItem 行锁。只改 Run 的 `plan_snapshot` 会绕过/破坏 bundle 一致性，并让上下文、claim 依赖、动作准入和准备消费可能读取不一致依据。简单给整个 Run 加排他锁则会改变现有并行领取特性，未经实测不能采用。

执行上下文的 `context_hash` 被 Agent 执行检查点及动作准入绑定；动作准入还校验固定 plan ID、节点 owner 与 phase。故修订不得改变已领取或运行中条目的步骤、绑定、owner、phase 或环境依据，否则会使原检查点和业务操作回执失去可验证上下文。`RunItem` 与 SuiteItem 均有唯一位置约束；排序投影必须与依赖和快照在同一事务更新，并避免位置交换时触发中间唯一冲突。Suite/RunItem 还唯一约束同一用例版本，修订不能假设可以重复插入同一版本。

读取侧也要纳入一致性：`_detail_from_cursor` 分多条 SQL 读取 Run、RunItem、Attempt 和 Result；PostgreSQL raw connection 当前使用数据库默认隔离，没有在详情查询前设置 repeatable-read。即使修订的多表写入原子提交，详情读取的多条 statement 仍可能跨越该提交，拼出前后不同修订投影。实现必须让正式详情/API 与 Agent 上下文读取能检测并重读同一 revision，或在不影响现有写事务的条件下采用一致快照；需要并发读写测试覆盖这个窗口。

本次基线按 README 记录的 `uv run --locked python -m pytest` 方式执行 `tests/test_case_plan_run_creation.py tests/test_agent_case_context.py tests/test_run_item_dependencies.py`，结果 **25 passed、22 skipped，9.19秒**。这些测试证明现有固定计划映射、上下文和依赖行为的回归基线；22 个需要隔离 PostgreSQL/MySQL/RustFS 的门控项未运行，不能证明跨进程领取与修订竞态。

结论与实施门槛：正式 Run 修订是跨存储投影的架构能力，不能直接复用 Smoke 方案，也不能原地覆盖唯一历史快照。当前倾向在 PostgreSQL 内追加不可变 revision 历史，并在同一事务更新经验证的“当前执行投影”；原始计划与运行/结果历史必须仍可按版本读取。实施前还要明确新增正式用例如何分配稳定 plan key、SuiteItem/RunItem ID 与来源证据，移除目标如何保留审计和结果语义；不得用伪造用例或静默删除代替定义。修订提交须通过乐观版本号拒绝陈旧修订。修订只允许影响 queued 且未领取的项，已领取/运行/终态项及其上下文哈希、证据不可改写。领取与修订必须有数据库内一致的序列化点，并通过真实 PostgreSQL 双事务竞争证明：修订先提交时 claim 读取新依赖；claim 先成功时修订冲突且不写入；多个互不相关的 RunItem 仍可并发领取。还需逐一适配上下文、准备步骤、业务动作准入、环境约束、回归来源、幂等重放及历史报告读取，再运行真实端到端场景。实现方案尚未定稿，本项没有修改生产代码、MySQL 配置或 PostgreSQL 业务数据，也没有宣称正式 Run 计划修订已通过。

来源：`Agent_Server/src/application/test_runs/plan_run_service.py`、`plan_mapping.py`、`run_store.py`、`agent_case_context.py`、`Agent_Server/src/runtime/postgres_case_consumption.py`、`postgres_case_action_admission.py`、`Agent_Server/src/modes/smoke_testing_mode/runtime.py`；PostgreSQL 行锁兼容矩阵见[官方显式锁文档](https://www.postgresql.org/docs/17/explicit-locking.html)。Step 已认证；P2 正式 Run 选择/排序/补全/执行及执行中计划修订仍未闭环，P0—P5 继续未完成。

### 7.103 正式 Run PostgreSQL 隔离验收前提复核〔2026-10-19〕

按 `test_case_plan_run_creation.py` 的 live fixture 启用 `RUN_LIVE_PLAN_RUN_CREATION=1`，实际执行 `test_archival_between_service_read_and_commit_is_rechecked_in_transaction`，结果 **1 passed，6.34秒**。夹具使用随机命名的 PostgreSQL Run/Suite/Item 表、MySQL 配置临时表和临时项目/用例；测试退出时删除其拥有的表，模型为本地 `SequenceModel`，没有调用默认 AI，也没有接触正式业务记录。该证据确认当前隔离 PostgreSQL 与相关 fixture 可用，并证明计划创建时会在事务内重查归档状态；它不覆盖执行中修订、详情跨提交读取或领取竞争。

随后按 `RUN_LIVE_RUN_DEPENDENCIES=1` 执行 `tests/test_run_item_dependencies.py::test_atomic_claim_waits_for_predecessor_and_real_required_output`，结果 **2 passed**（内存与隔离 PostgreSQL 变体各一）。真实 PostgreSQL 变体同时调用两个 Worker 的 `claim_items`，验证实际 `FOR UPDATE SKIP LOCKED` 领取不重复、未满足依赖不提前领取，且前置输出可供后续消费者使用。计划创建并发重试 `tests/test_case_plan_run_creation.py::test_concurrent_retries_create_one_suite_run_and_dispatch_intent` 在 `RUN_LIVE_PLAN_RUN_CREATION=1` 下也为 **2 passed**（内存与 live 变体）；这项只证明创建/Outbox 幂等，不视为领取锁测试。

下一步可验证的协调候选是增加 PostgreSQL 每 Run 的修订头行：`claim_items` 在事务内先对该行获取兼容的共享锁，之后仍以 RunItem `FOR UPDATE SKIP LOCKED` 原子领取；修订事务对同一头行获取排他锁，检查 expected revision 并原子保存历史与投影。官方行锁兼容矩阵说明多个 `FOR SHARE` 可以并存，而 `FOR UPDATE` 与它冲突；这在锁模型上允许并行 claim 与单写修订并存。现有 live claim 测试是该候选设计的基线，不是候选锁本身的验证。此机制尚未实现或在本系统事务中实测，且需继续验证锁顺序、Run 刷新、过期租约恢复、详情读取一致性及修订写事务，不能据文档说明直接认定竞态已解决。

### 7.104 默认 Step `produced` 阶段失败请求与阶段职责复核〔2026-10-19〕

继续 7.98，不按 SDK 工件文件名或目录顺序推断调用顺序。将 `PG-planning.json` 中每轮 `logical_call_id`、`planning_stage`、`evidence_task` 与 `physical/*.request.json` 的实际消息内容交叉核对后，顺序确定为 `logic → bindings → produced`：前两轮分别完整结束，第三轮 `produced` 为 `finish_reason=length`、`completion_tokens=8192`、正文 0 字符并被既有完整性检查拒绝。调用各只有一个物理 HTTP attempt；没有创建 Run，也没有业务 HTTP。

第三轮有效配置仍为 MySQL 已冻结的 `step-5-preview` / `openai_chat_completions`，128,000 上下文窗口、8,192 最大输出、默认 `request_profile=null`。实际 request 的 prompt usage 为 8,431 tokens，未逼近上下文窗口；SDK 的可选 JSON Schema 因配置未声明格式而按 `format_not_declared` 回退到提示 Schema。此前单变量 16K 输出上限并启用 JSON 格式的隔离对照仍未形成有效计划，因此不能把协议切换、只增加输出预算或只启用 JSON mode 记作已验证修复，也不修改 MySQL 全局默认配置。

按当前工作区 `planning_workflow.py` 构造逻辑，effects 请求除了固定 `logical_plan/hash`、单项 scope 和完整来源 evidence，还包含操作工具契约、引用目录、关联文档导航、查询回执及 `completed_tasks`。本样本均只有一个正式目标项；因此 `produced` 不是因 200 项上限或多目标集合而超长。8,431 prompt tokens 本身也未证实输入超窗；观测到的失败是输出侧用尽。`produced` Schema 要求模型同时给出业务输出身份路径、操作输入选择条件、业务预期检查及来源引用，系统再由短引用解析、固定计划和原始工具结果做确定性校验。删除这些字段或放宽校验会损害结果可验证性，不能为通过模型调用而简化。

本轮结论：目前没有足够证据证明可通过删除重复上下文解决输出截断；单纯缩小输入也没有因果证据。可信的后续设计方向是研究把“效果声明的业务语义”与可从固定逻辑、已读工具契约/文档机械导出的字段分开，并保持 canonical 接受器仍负责验证，不把模型声明当执行证据。须先用真实失败用例精确列出 `produced` 最小必要字段、各字段唯一来源及可确定性推导规则，再以同一真实默认模型、同一正式用例和冻结预算做 A/B：结果须能通过当前 canonical 接受器、创建正式 Run 并完成真实工具执行/证据断言，负向/清理/终态重投也不回归，方可接受该设计。若无可靠推导或模型仍输出截断，应明确保留缺口，不换模型、不改全局预算、不通过自由模式绕过。

本轮只追加调研记录；无生产代码、MySQL 配置或 PostgreSQL 业务数据变更。已核对 `verification.json`、`PG-planning.json`、三份实际 SDK request/stream 记录、失败用例与关联文档，以及 `planning_workflow.py`、`planning_effect_tasks.py` 和对应 Schema。P2 仍未完成默认 AI 项目选取→排序→逻辑补全→正式执行；正式 Run 执行中计划修订也仍处于设计调研，完整 P0—P5 继续未完成。

### 7.105 `produced/cleanup` 可推导字段边界〔2026-10-19〕

针对 7.104 的字段最小化候选，沿正式逻辑封存、`task_roles`、任务回执接收器、引用绑定器及运行时 `compile_relations` 检查字段来源，并以 `planning_cleanup_ambiguous_text.json` 的真实项目形状（一个正式用例、通用 `api-test-runner/execute_task`、项目文档描述 POST/GET/DELETE 和 `order.id`）复核。

系统已确定性计算本轮 `produced`/`cleanup` scope：固定逻辑中的 `cleanup_sources` 建立角色，`task_roles` 推导来源和消费者；`accept_task_decision` 强制模型回复 scope 集合完全一致，并拒绝跨角色声明；合并后 `validate_relations` 再要求实际清理来源与固定逻辑完全一致。因此计划项、角色、scope 和清理来源无需第二次由模型重新决定。相反，`select_when` 的输入字段路径及固定值、原始工具输出中的稳定对象身份路径、业务预期检查和声明间身份关系不能由当前通用 `execute_task` 工具契约推出：该工具内部执行用户项目 API，path、请求、返回 JSON 与断言依据分别来自冻结项目文档和用例。把 `order.id`、`task.method`、`response_body` 等例子推广为全局映射会臆造业务字段；应继续由模型引用固定依据，并由短引用解析、canonical 编译及真实执行回执验证。

一个低风险协议候选是后续效果任务默认继承 `bindings` 回执已绑定的 item evidence IDs，仅允许模型补充 bindings 之后新查询得到的引用。现有每个效果 task 都重复要求输出 `evidence_ids`，而回执聚合会将它们追加到同一 item。交叉核对既有实测后确认这不是当前默认模型失败的根因：7.76 已用 live18d 原始请求/回执及同窗口认证 Step 做了 inheritance 对照，约 74.879 秒后 HTTP 200、4,532 completion tokens，仍无正文或 finish_reason；因此排除“重复复制 evidence IDs 单独导致超时”。该投影虽可省少量 JSON，但没有证据值得增加生产协议和恢复兼容复杂度，当前不实施。

原生结构化输出也已有真实对照，不再列作待调研：7.85 使用认证 Step 对冻结 produced 输入启用严格 JSON Schema，在隔离 MySQL profile 中实际映射并发送 schema；HTTP 200、流运行约 82.762 秒、正文 0 且无 finish_reason，仍未获得可校验响应。因此 `request_profile=null` 导致默认请求回退为提示 Schema 是事实，但不是空正文的唯一根因；能力映射保持按模型显式声明，不能据此修改全局 MySQL profile、假定所有 OpenAI-compatible 服务端都支持严格 Schema或切换 Responses transport。

本轮没有找到安全地由通用代码自动填充业务路径/身份/预期的依据，故不添加 heuristic，也不改 schema。已有默认 Step 证据同时否定了两个容易误判的简单修复：减少重复 evidence IDs、单独启用原生 Schema 都未让模型产生回执。继续缩短 prompt 或重复预算/协议单变量试验没有新因果依据。后续应转向阶段角色契约与调用轨迹：重建逻辑选取结果、bindings 回执、produced 输入/响应及被接受/拒绝位置，检查 `produced` 是否被要求重新推理已在固定 plan、binding receipt 或工具查询结果中确定的信息，以及 Agent planning 回执与正式执行契约是否存在职责重叠。任何可删除/自动派生字段都必须保持 canonical 结果逐字段一致，并先用本地真实 HTTP 对照，再跑默认 Step 的完整“选取→排序→补全→正式 Run→实际工具执行”验收。

依据：`planning_effect_tasks.py` 的 `task_roles/accept_task_decision/aggregate_task_receipts`、`case_effect_relations.py` 的 `compile_relations/validate_relations`、`case_effect_sources.py`、`planning_cleanup_ambiguous_text.json`，以及 PLAN §7.76 的证据继承 live 对照、§7.85 的原生 JSON Schema live 对照。本轮为代码与固定样本调研，没有修改生产代码、MySQL 配置或 PostgreSQL 业务数据，也未将控制模型 fixture 结果当默认 AI 验收。P2 默认 Step 正式 Run 闭环仍未完成；完整 P0—P5 继续未完成。

### 7.106 正式 Run 详情并发读取的快照一致性修复〔2026-10-19〕

先复核 §7.105 的成功/失败对照：`p2-default-minimal-case-live7/plan.json` 是 `case-planning-v5` 的单轮规划，未经过 `produced/cleanup`；`p2-default-single-staged-diagnostic-20261019/PG-planning.json` 的创建/清理成功样本使用隔离 16K 和显式 JSON profile，经历 logic、bindings、一次 produced 拒绝、produced 修正和 cleanup；完整八步 live7 则使用默认 8192/profile=null，在 produced 截断。三者不是同输入、同预算、同阶段实验，不能由最小用例成功推出复杂阶段修复，亦不重复只调整预算/Schema的旧实验。复杂规划职责仍须继续研究。

本轮推进 §7.102 已定位的正式运行一致性前提。旧 `PostgresTestRunStore._detail_from_cursor` 依次读取 Run、RunItem、Attempt、Result 四张表；默认 READ COMMITTED 每条 SQL 使用自己的快照，单个连接事务不能保证这四份投影属于同一时刻。沿 `_get_run_sync`、计划创建/幂等读取、取消、环境限制投影和 `PostgresCaseActionAdmission._claim` 检查消费方后，选择在现有方法内合并为一条 SELECT，用各子查询的 `jsonb_agg(... ORDER BY ...)` 保留集合与排序，空集合仍为 `[]`、不存在 Run 仍为 `None`。项目已有 PostgreSQL JSONB 聚合用法，官方依据为[事务隔离](https://www.postgresql.org/docs/17/transaction-iso.html)与[聚合函数](https://www.postgresql.org/docs/17/functions-aggregate.html)。此做法直接修复读取边界，不引入全局隔离级别、领取锁或重试机制；同事务已写入内容仍可见。

新增真实隔离 PostgreSQL 测试先暂停详情读取的第一条 SELECT，再让独立存储事务完成领取、完成或取消，最后继续读取。测试不替换 SQL/数据库返回，只控制两个真实事务交错。旧实现的领取场景实际 **1 failed、16 deselected，7.76秒**，返回旧 Run 加新条目/Attempt，JUnit 为 `p2-run-detail-snapshot-before.xml`；因此不是仅凭隔离文档推断的故障。

修复后第一次组合回归 **48 passed、1 failed、1 skipped，27.18秒**：取消场景的测试额外错误要求未领取条目也生成五个 Attempt，实际取消原有语义是零 Attempt，已修正测试断言，未改取消行为。最终开启 `RUN_LIVE_RUN_DEPENDENCIES=1` 与 `RUN_LIVE_PLAN_RUN_CREATION=1`，执行 `tests/test_run_item_dependencies.py tests/test_case_plan_run_creation.py tests/test_agent_case_context.py` 得 **49 passed、1 skipped，26.04秒**。三个并发场景均返回完整旧快照，随后重新读取完整新快照；写事务在读暂停期间正常提交，测试同时证明该读没有阻塞这些状态写入。唯一跳过项为未启用的真实模型完整读取专项，不能算作本轮默认 Step 验收。最终 JUnit 为 `p2-run-detail-snapshot-final.xml`。

消费方进一步在 `RUN_LIVE_PLAN_RUN_CREATION=1` 下运行 `tests/test_case_operation_verification.py tests/test_agent_case_execution.py`，**38 passed、1 skipped，152.25秒**；包含真实业务 HTTP、正式 Agent Run 恢复、未知写入的已声明只读查证及数据库回执，JUnit 为 `p2-run-detail-consumers-final.xml`。唯一跳过为未启用的独立公开 Worker 场景；本轮没有重新调用默认 AI。两组最终结果合计 **87 passed、2 skipped**，`compileall` 与本轮文件 `git diff --check` 均成功。没有前端或类型契约变更，本轮未重跑前端构建。

分类：详情读取一致性从“已定位待修复”变为**已有实现、真实 PostgreSQL 并发功能验收通过**。正式 Run 的 revision 历史、修订 API、queued 项投影原子更新、claim 与修订协调仍**需要补实现和验收**，本修复不等于 §7.103 的共享头行锁候选已验证，也不使整个动作准入事务的其他独立读取自动拥有同一快照。复杂默认 Step 项目选取→排序→补全→正式执行仍未闭环；自动 embedding 召回此前 402 余额不足也仍未解除。本轮没有修改 MySQL 配置、数据库结构、Provider 协议或前端契约；配置继续 MySQL、业务继续 PostgreSQL、原件继续 RustFS。完整 P0—P5 继续未完成。

### 7.107 执行中修订与真实领取的事务协调实验〔2026-10-19〕

继续 §7.103/7.106，先验证候选机制而未直接开放修订入口。读取 `plan_mapping.py`、`plan_run_service.py`、`run_store.py`、`agent_case_context.py`、`agent_case_execution.py`、`postgres_case_action_admission.py` 和固定快照 Schema：创建时 `validate_plan_bundle` 重建并校验全部套件/运行投影；执行适配器 `_load` 比较完整 `plan_snapshot`，再按执行上下文哈希和原 Attempt 检查点恢复。因此领取协调与执行依据固定是两个不同问题，只做数据库互斥不能得到正确的运行中修订功能。

依据 PostgreSQL [行锁兼容矩阵](https://www.postgresql.org/docs/17/explicit-locking.html)，新增 `tests/test_run_revision_coordination_research.py`。实验复用真实项目/正式用例/RunStore，创建随机命名、仅归本测试所有的 PostgreSQL head/history 表：领取仍执行原 `_claim_items_sync` 全部 SQL，仅在首个 `_read_run` 之前以测试包装注入 head 的 `FOR SHARE`；候选写事务使用同一 head 的 `FOR UPDATE`，检查 expected revision 和 item 是否仍 queued/无执行历史，然后原子保存依赖、历史及 revision。测试不复制领取算法，不模拟 SQL 返回值；`pg_backend_pid` 与 `pg_blocking_pids` 实际观察等待关系，不按时间猜是否发生阻塞。临时表最终清理；没有向生产库安装该机制。

真实隔离验收 **6 passed、7.52秒**，包括：修订先写入且尚未提交时，领取被数据库阻塞，提交后按新依赖保留被依赖项 queued；领取先写入时，修订等待其提交后拒绝已经 claimed 的项，head/history/依赖均不改；两个独立 claimant 同时持有共享 head 锁且各自写入不同项，之后统计为两项 claimed/三项 queued；三处写入后故障会全部回滚；陈旧 expected revision 拒绝且不改已接受投影。

第六个场景验证了仍未解决的执行边界：通过真实正式计划和 Run，领取并启动 `independent`，只在本测试独占数据中改 queued 的 `left.reason`；`build_agent_case_context` 对 independent 构建出的完整 `execution_json` 前后完全相同，但以原 Run 进入 `_load` 仍因全局快照不同被拒绝，未创建执行检查点。这个通过项证明的是**现有拒绝边界**，不是修订成功；生产保护保持原样，不据此删除整份快照比较或哈希校验。

随后启用 `RUN_LIVE_RUN_DEPENDENCIES=1`、`RUN_LIVE_PLAN_RUN_CREATION=1`，运行 `tests/test_run_revision_coordination_research.py tests/test_run_item_dependencies.py`，最终 **23 passed、无跳过，12.40秒**；编译检查通过。独立专项和组合 JUnit 分别为 `p2-run-revision-coordination-final.xml`、`p2-run-revision-coordination-regression.xml`。规划夹具使用受控 `SequenceModel`，本轮没有真实 Step 调用、业务 HTTP 或生产修订 API 验收。

当前分类更新：**共享 head 的领取协调候选已在本组真实事务竞争下验证，仍未接入生产；固定修订身份/不可变历史、套件版本语义、已领取条目的依据固定、Agent 恢复与动作准入、公开 API/页面和完整流程仍需实现/验收**。下一步应先落实“当前执行投影”与“条目已领取的固定修订”之间的读取契约，并明确新增/移除用例如何保留原套件、原条目及结果；只有历史检索、上下文、环境/消费范围和动作准入都能读回同一依据，才接入协调机制。该实验不证明环境限制、取消/租约恢复、所有锁顺序或未来完整修订事务无死锁，也不证明这是唯一可行的协调设计。不新增 MySQL 配置、不改 Provider 原生协议；P2 默认 Step 复杂规划与完整 P0—P5 继续未完成。

### 7.108 首次领取固定计划身份与恢复继承〔2026-10-19〕

重新读取原附件，沿 §7.107 检查 `CasePlanStore`、`build_plan_run`、两种 RunStore 的领取/开始/恢复、`AgentCaseExecutionAdapter._load`、上下文与 PG 动作准入。`CasePlanStore.save` 已以 ID 首次保存为准，PG 使用 `ON CONFLICT(id) DO NOTHING`，可复用其不可变计划记录；但计划记录不包含 Run 特有的 item 映射、owner、phase、消费范围和依赖映射版本，不能仅凭原 plan ID 宣称已实现完整历史快照读取。运行适配器的全快照比较发生在领取之后；旧实现首次领取没有保存计划身份，Agent 检查点也可能尚未创建。

先复现：在夹具独占项目创建真实正式计划/Run，领取后仅改变当前 Run 快照的 `request.runtime_context`，再启动原条目。内存与隔离 PostgreSQL 均实际启动成功，针对固定依据要求的回归测试均报 `DID NOT RAISE ValueError`，**2 failed、27 deselected，8.53秒**。这不是线上已经开放了修订 API，而是拟接入修订前明确暴露的执行依据缺口；原始输出见 `p2-claim-snapshot-before.xml`。

采用现有 `plan_mapping.json_digest` 的 canonical JSON/SHA256 与 expired Attempt 继承方式，在 Attempt 增加可选 `plan_snapshot_identity = {plan_id, sha256}`。首次 Agent 领取在原领取事务中随 Attempt 保存完整快照的哈希；快照包含完整计划以及 Run 映射、准备范围、回归和依赖映射版本。恢复复制原身份，不按当前快照重新计算；旧 Attempt 没有身份时保持缺失，序列化也不补新字段。legacy 执行不创建该身份。没有复制整份计划到每个 Attempt，没有引入新表、DDL、配置、依赖或模型调用。

`plan_snapshot_identity.py` 集中执行身份校验；开始执行、Agent 上下文构造、已有环境开始回执重放和 PG 动作准入均接入。计划不一致时保留原 Attempt/检查点并明确报错，不能把“调用方传入最新 Run”视为重新绑定执行依据的授权。现有 `_load` 整份快照比较和完整上下文 hash/祖先检查均保留；新身份不进入 Agent 业务 JSON，前端 Attempt 类型同步可选字段。日志记录选取来源（首次领取或原 expired Attempt）及不一致身份，不记录业务正文或凭据；选取日志不声称数据库已提交。

新增功能验收 **6 passed、23 deselected，6.22秒**：内存与真实 PG 分别证明领取后变化不能开始、失败后条目仍 claimed 且零检查点；开始/检查点/过期/重领后保持原 hash 和原 checkpoint，独立 PG Store 重新读取仍一致；历史无身份 Attempt 不被补写新 hash，继续按旧语义恢复。额外真实 PG/HTTP 专项 **1 passed、11 deselected，7.89秒**：先保存原 Agent 检查点，改变当前快照后，以最新 Run 调用 `_load` 被拒；原业务动作到 PG 准入时也被拒，Job 仍 queued/没有 `started_at`，业务 HTTP 为零，原检查点未改写。该样本没有使用外部 AI，也不是修订成功验收。

最终组合验证：

- `RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_RUN_DEPENDENCIES=1`，`test_case_plan_run_creation.py test_run_item_dependencies.py test_run_revision_coordination_research.py test_agent_case_context.py`：**61 passed、1 skipped，37.85秒**。原第六个协调实验保留历史无 pin 的上下文相同证明，同时验证新 pin 会拒绝改后快照；没有放松旧适配器保护。
- `RUN_LIVE_PLAN_RUN_CREATION=1`，`test_agent_case_execution.py test_case_operation_verification.py`：**39 passed、1 skipped，157.79秒**，包含真实本地业务 HTTP、PG 正式结果、恢复、只读查证、动作准入及新增拒绝样本。
- `RUN_LIVE_DOCUMENT_IMPACT_TASKS=1`，环境开始回执的后续评审/过期恢复及篡改/外来祖先两项：**2 passed、6 deselected，25.77秒**。
- `test_test_run_postgres_claim.py` 初次 **14 passed、1 failed**，失败为此前详情改成单 SELECT 后，取消锁顺序测试的假 cursor 仍只返回旧 `record`，触发 `KeyError: 'items'`；HEAD 生产查询已是聚合形态。仅同步该夹具的 `items/attempts/results` 空数组，保留锁顺序断言，复跑 **15 passed，5.00秒**。
- 上述最终四组共 **117 passed、2 skipped**；两个跳过分别为未启用的独立真实模型专项和公开 Worker 专项，不能视为已验收。`npm run build` 的 `vue-tsc` 和 Vite 构建成功；Python 编译和本轮 diff 检查通过。JUnit 为 `p2-claim-snapshot-{regression,agent-regression,environment-regression,store-contract-final}.xml`，初次夹具失败保留在 `p2-claim-snapshot-store-contract.xml`。

当前分类：**领取时固定完整快照身份、恢复继承和执行前一致性校验已有实现并通过上述功能验收；按该身份读取不可变历史快照、完整修订历史/提交 API、queued 项原子改动及生产领取协调、运行中条目继续使用原依据、环境/共享消费/回归/报告跨修订语义仍需补实现与验收。** 本次不开放修订写入口，也不把拒绝变更误报为支持执行中修订。下一步须保存并读取完整 revision envelope（除原计划外还包括 Run 映射与消费范围），才能让未执行项采用新修订、已领取项继续解析原修订；新增/移除正式用例及套件冻结语义仍需一并落实。

新字段存于已有 PostgreSQL Attempt JSONB，配置仍在 MySQL、原件仍在 RustFS；旧记录不回填。部署新增 pin 后须同步升级读写这些 Attempt 的 Worker：旧版 Schema 可能在重写 JSON 时丢弃未知字段，混跑兼容性尚未验收。Step 已认证；默认 Step 多候选选取→排序→补全→完整执行、DeepSeek 人工录制及其余 P0—P5 未闭环事项继续保留。Provider 仍走各自原生协议，Anthropic 保持 Messages。

### 7.109 领取时完整快照归档与执行读取（2026-10-19）

重新读取原附件后继续 §7.108，目标是让已领取条目沿用原完整依据，而不是仅拒绝当前计划变化。现有不可变 CasePlanStore 只有计划本身，缺少 Run 的条目映射、owner、准备阶段、消费范围、回归来源及映射版本，不能用重建计划代替原快照。依据 `run_store._insert_run_and_items` 的同事务创建、`_write_run` 的独立投影更新、已有 JSONB 路径读取与增量 `ADD COLUMN IF NOT EXISTS` 做法，在已有 Run 表新增独立 `plan_snapshots JSONB`，以完整快照 SHA 为键保存原 envelope；内存存储保持同契约。初始归档与 Run、Item、套件及 outbox 的原创建事务一起保存，不在每个 Attempt 重复复制。此次没有修订写入口，归档只有创建时写入；将来修订必须在原子提交中追加完整归档，不得覆盖旧键。

先升级真实功能断言：创建计划→领取→改变当前计划输入→开始执行，旧实现双后端实际 **2 failed、27 deselected，7.30秒**，均报“当前执行依据与领取时固定的计划快照不同”；保存于 `p2-history-snapshot-before.xml`。这是已有拒绝边界不能满足原依据继续执行的复现，不是用户能通过生产入口修订计划的证明。

读取契约：

- `get_execution_detail(run_id,item_id,attempt_id,supplied_run=...)` 基于同一份聚合详情选最新 Attempt，再按 pin 查询对应归档。不再独立读 Run、Item、最新 Attempt 拼接执行上下文。
- 归档先验证原始 JSON 的完整 SHA，再按既有 Schema 读取，校验 plan/project/run 身份及序列化后的 SHA。存在但损坏、被改或 JSON null 的归档直接报错，不能退回当前计划。
- 旧行没有归档时，仅在当前完整快照与原 pin 完全相同时兼容读取并记录 fallback；不同则明确缺失。无 pin 的历史 Attempt 不补绑今天的计划，原全快照比较仍有效。
- 调用方传入的 Run 快照必须等于当前投影或 pin 指向的完整原快照；任意拼装的第三份快照继续拒绝。Agent 实际上下文始终来自服务器选定的归档，保留 checkpoint context hash、恢复祖先、租约、正式版本和依赖结果校验。
- 启动及 PG 动作准入在 project/item 锁前只读原 Attempt 与归档，保持 project→item→attempt→Job 的已有锁顺序；锁定后再次核对 pin。开始回执重放使用原归档与原 admission，不用今天的评审替换历史回执。动作消费读取的 detail 也使用选定归档。
- 普通 Run 查询仍显示当前投影；运行状态更新不重写归档。配置继续在 MySQL；本字段属于 PostgreSQL 运行证据；原件仍在 RustFS。模型客户端及协议不变。

迁移 `20261019_0004` 与运行存储初始化均增量加列。空库 Run 表尚不存在时 Alembic 允许跳过，由已有初始化路径创建；已有行默认空归档，不凭当前内容伪造历史，不重写 record。禁止破坏性 downgrade。新旧 Worker 混跑、生产历史缺失归档的恢复与部署迁移仍需专项验收，未操作生产迁移。

实际验证（JUnit 均在本目录）：

| 范围与实际开关 | 结果 | 工件 |
|---|---|---|
| `RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_RUN_DEPENDENCIES=1`，规划创建、依赖、协调、上下文及存储合同五文件 | **84 passed、1 skipped，36.39秒** | `p2-history-snapshot-regression-final.xml` |
| `RUN_LIVE_PLAN_RUN_CREATION=1`，Agent 执行及操作查证两文件 | **44 passed、1 skipped，165.29秒** | `p2-history-snapshot-agent-regression-final.xml` |
| `RUN_LIVE_DOCUMENT_IMPACT_TASKS=1`，开始回执随计划/评审变化及过期恢复、篡改/外来祖先两项 | **2 passed、6 deselected，21.57秒** | `p2-history-snapshot-environment-final.xml` |
| `RUN_LIVE_POSTGRES_TESTS=1`，新增归档迁移（离线、已有/缺失 Run 表、普通/大小写/带点引号名、重入及拒绝降级） | **7 passed、24 deselected，1.36秒** | `p2-history-snapshot-migration-final.xml` |
| 最后补明确缺失错误后的归档、原身份恢复及实际动作专项 | **18 passed、34 deselected，16.20秒**，已被上述范围覆盖，不重复累计 | `p2-history-snapshot-final-functional.xml` |

前四组共有 **137 passed、2 skipped**，两个跳过仍是未启用的真实默认模型专项及独立公开 Worker；受控 Agent 不能替代默认模型质量验收。正向场景覆盖首次 Agent 检查点前改投影、审批中连续变化、原固定 plan ID 与当前不同、工具已完成但未提交结果后的改计划及重领：正式结果仍用原上下文，HTTP 不重复。无效归档的 PG 动作保留 queued Job、无 started_at、业务 HTTP 为零；伪造第三份 Run 继续拒绝。真实独立 PG 事务在预读后改变 pin，锁定后被拒，条目未开始。针对内存嵌套 JSON 引用另实测发现修改返回内容可连带破坏归档（**1 failed**），沿原深拷贝约定修复后纳入84项；持久归档内容不再暴露给返回对象修改。

过程失败均如实保留：首次新增 DDL 的 Python f-string 空括号导致收集失败，已转义后完成真实 PG 验证；存储合同初跑14通过1失败，为既有审批恢复 mock 未覆盖新增只读 Attempt 查询，仅同步 mock 后最终组合通过。迁移初跑因虚拟环境缺 `alembic.config` 无法收集；按已有 pyproject/根锁文件安装 `alembic==1.20.0`（只补本地开发依赖，没有改依赖声明）后，新增迁移验收通过。

完整迁移文件扩大运行时为 **22 passed、5 failed，5.93秒**，失败均为既有 ToolJob 的 schema 限定表名在 `PostgresCaseOperations.__init__` 被拒，报 `ValueError: Invalid original ToolJob table identifier`。将 HEAD 原测试复制到独占临时文件复验，完全相同5项失败（**5 failed、19 deselected，1.62秒**），临时文件已删除。原完整输出与基线分别见 `p2-history-snapshot-migration-regression.xml`、`p2-history-snapshot-migration-baseline.xml`；不能称整体迁移回归全绿。此缺陷需独立追根修复，不在快照读取变更内临时绕过。

Python compileall 和本轮 diff 检查通过；此次没有前端契约或页面改动，未重复前端构建。新增字段只在隔离业务 PG 验收，没有运行生产迁移，没有真实 Step 调用。同步升级 Worker 的部署约束保留。

当前分类：**原完整快照归档、按领取身份读取、Agent/启动/动作/环境回执消费及上述恢复功能已有实现并通过对应验收。** 生产修订提交与原子追加、queued 项与领取协调、新增/删除正式条目及套件冻结、共享消费/清理/回归/报告的完整跨修订语义仍需补实现与验收。此次保存的是创建时归档，不能将模拟投影变化的测试说成生产修订已实现；多修订体量、归档增长和读取性能也未验收。默认 Step 多候选选取→排序→必要补全→执行、其他模式、人工 DeepSeek 录制及其余 P0—P5 未闭环项继续保留，整体未完成。

### 7.110 生产领取与计划修订的 PostgreSQL 事务协调（2026-10-19）

继续重读原附件并核对 §7.103 的修订头行候选。代码库 `alembic/env.py` 已有事务级 advisory lock；[PostgreSQL 官方锁函数](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)及[显式锁说明](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)确认共享锁之间兼容、排他锁与共享锁冲突、事务提交/回滚/连接结束释放。采用现有机制替代另建协调头表：Run 统计刷新会写 Run 行，不以 Run 行共享锁引入升级争用；Item 继续使用原 `FOR UPDATE SKIP LOCKED`。乐观修订号和历史追加仍由未来正式修订写事务承担，advisory lock 不代替它们。

先让真实生产 `claim_items` 与独立排他事务竞争，接入前 **2 failed**，均为 `claim completed while an exclusive plan revision transaction still held the resource`（`p2-plan-coordination-before.xml`）。根因是领取只锁条目，没有修订与领取共同遵守的协调资源。现已在原领取事务首次读取 Run 之前取得共享事务锁；未来修订写事务须先取得同一资源排他锁，再按既有 project→item→attempt→Job/Run 的适用锁序校验、更新。AI、查询外部服务和网络规划必须在锁外完成，不能持锁等待模型。仅在测试中存在的 head/history 表用于检查 CAS/回滚，已经不承担协调职责，没有新增生产头表。

资源由实际 Run 表的 relation OID、规范化 UUID 和固定 namespace 计算为有符号 64 位键。先由参数化 regclass 查询解析物理表，避免 schema/引号别名或 UUID 大小写产生不同锁；不同物理表或不同 Run 使用不同资源。哈希碰撞只会额外等待，不会混同业务记录。此锁不是权限或隔离检查，原项目、租约、依赖和回执校验保留。新增 requested/locked 结构化日志记录运行及锁模式，不输出凭据。

实际功能验收：独立 PG writer 先持锁时 claim 在 `pg_stat_activity/pg_blocking_pids` 中明确等待 advisory 锁，无提前 Attempt；提交和回滚均释放。修订先行后领取读取新依赖；领取先行后候选 writer 等待、随后拒绝已领取项且无历史写入。两个 claimant 都能在刷新统计之前完成各自不同条目的写入，证明未串行化独立领取。显式 schema/引号别名和大写 UUID 与原资源仍互斥；其他 Run/物理表资源不阻塞。真实独立 Python 进程持锁后被终止，stdout/stderr/退出码均检查，等待领取继续且仅产生一次 Attempt。该进程只通过 argv 接收独占表/Run 身份，连接凭据仍由现有配置加载。

| 实际命令范围（Agent_Server 内） | 结果 | 工件 |
|---|---|---|
| `RUN_LIVE_RUN_DEPENDENCIES=1`；`test_run_plan_projection_lock.py test_run_revision_coordination_research.py test_test_run_postgres_claim.py` | **28 passed，14.54秒** | `p2-plan-coordination-final.xml` |
| `RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_RUN_DEPENDENCIES=1`；`test_case_plan_run_creation.py test_run_item_dependencies.py test_agent_case_context.py` | **63 passed、1 skipped，32.09秒** | `p2-plan-coordination-regression.xml` |
| 同上开关；`test_agent_case_execution.py test_case_operation_verification.py` | **44 passed、1 skipped，161.92秒** | `p2-plan-coordination-agent-regression.xml` |
| `RUN_LIVE_DOCUMENT_IMPACT_TASKS=1`；原开始回执/计划变化/过期恢复及篡改/外来祖先两项 | **2 passed、6 deselected，24.74秒** | `p2-plan-coordination-environment.xml` |

四组 **137 passed、2 skipped**；两项跳过仍为真实默认模型和独立公开 Worker，不能计为默认 Step 验收。最后为子进程补 Windows 隐藏窗口参数后，锁专项另 **7 passed，8.59秒**（`p2-plan-coordination-lock-final.xml`），已在范围内不重复累计。Python compileall 和 diff 检查通过；没有前端/API/schema 契约改动，未重复构建、未执行生产迁移、没有真实模型调用或 Provider 协议修改。此前完整迁移回归5项既有 ToolJob 表名失败仍未在本步骤解决。

当前分类：**生产领取共享协调、可供正式写事务调用的排他协调及上述真实并发/进程终止行为已有实现并验收。** 排他调用目前只由测试候选 writer 使用；生产修订提交 API、expected revision/幂等、原子追加完整归档、queued 投影和新增/移除正式条目、套件冻结、跨修订清理/回归/报告仍需实现。不能将锁基础报告为完整执行中修订。此机制要求所有领取与修订 Worker 使用同版实现，旧 Worker 不遵守该锁，混跑未验收。配置继续 MySQL、业务 PostgreSQL、原件 RustFS；Step 已认证，整体 P0—P5 目标继续未完成。

### 7.111 ToolJob 迁移与运行时辅助表命名契约修复（2026-10-19）

独立推进 §7.109 发现的迁移阻塞。重新运行完整 `test_schema_migrations.py`，实际 **26 passed、5 failed，6.12秒**（`p0-tool-table-migration-before.xml`），5项仍在 `PostgresCaseOperations.__init__` 报 `ValueError: Invalid original ToolJob table identifier`。迁移能创建 schema 限定、大小写和引号表，运行时却只接受未限定简单名称；放宽单处正则还不能解决问题，因为操作索引、原返回证据和消费结束三处均截取完整配置来派生表名，ToolJob 初始化也把限定表名直接拼进索引名。这属于同一命名契约缺失，不能各自加例外。

依据现有 Alembic `0002._identifier` 的分段、大小写和 SQLAlchemy 引号做法，以及 PostgreSQL 官方[标识符规则](https://www.postgresql.org/docs/17/sql-syntax-lexical.html#SQL-SYNTAX-IDENTIFIERS)、[CREATE INDEX](https://www.postgresql.org/docs/17/sql-createindex.html)的同 schema 索引语义，新增内部 `postgres_identifiers.py`。完整验证配置的标识符语法，分开处理限定段、双引号、转义引号及未引用名称折叠；安全生成派生表和索引名。操作、证据、消费三处复用同一逻辑。新限定名称的辅助表放在主表对应 schema，派生关系名使用安全前缀与哈希；索引名不拼 schema，复用迁移已冻结的命名。表解析/派生留下结构化调试日志，非法配置明确携带输入报错。

兼容约束：此前接受的简单未限定名称严格保留原前缀、原字符串 SHA 和物理表名，含大写配置及63字符名称，三类辅助表共9项合同验证；不重命名历史表、不搬移记录、不修改既有迁移脚本。未改变的名称继续读取原证据。没有为全系统其他 Store 声称 schema 支持，也没有执行生产数据库迁移。

真实存储验收使用独占 `migration_test_*` schema：先执行正式 Alembic，再初始化 ToolJob 和三类辅助表，实际重放既有捕获的原 Job/绑定/可靠输出，通过生产 `register`、`validate_origin`、`capture`、`list_run_evidence` 保存并核验对象证据及检查结果；重复初始化、重建 Store 后证据完全不变。四组主表名称包括普通、未引用大写、引号内带点、中文及转义引号。实际检查辅助表所在 schema、外键目标、关系名 UTF-8 长度，以及初始化未重复创建迁移索引。消费结束在该专项检查真实 DDL/外键；实际业务消费与清理由后续四文件回归覆盖，未混称完整 schema 化 Agent 产品验收。

| 范围 | 实际结果 | 工件 |
|---|---|---|
| `RUN_LIVE_POSTGRES_TESTS=1`，完整迁移文件与新增命名/真实证据专项 | **60 passed，12.76秒**，含完整迁移31项 | `p0-tool-table-migration-functional.xml` |
| 逻辑操作、原始返回证据、阶段消费、跨条目消费四文件，真实业务 HTTP/PG/MySQL/RustFS | **59 passed，156.67秒** | `p0-tool-table-action-final.xml`及`p0-tool-table-action-final/` |
| 最后补输入错误上下文后的新增命名专项复验 | **29 passed，5.67秒**，已在60项范围内，不重复累计 | `p0-tool-table-names-final.xml` |

前两组 **119 passed，无跳过**。扩大回归首次因本次命令漏配证据目录为 **46 failed、13 passed，118.55秒**；读取全部 JUnit 失败类型，14项缺 `QA_STATEFUL_RESEARCH_EVIDENCE_DIR`、14项缺 `QA_CONSUMPTION_PHASE_DIR`、9项缺 `QA_EFFECT_EVIDENCE_DIR`、9项缺 `QA_CONSUMPTION_EXTERNAL_DIR`，无其他类型。首个失败单独复跑也证实该原因（1失败1通过）。未因此改生产逻辑或跳过测试；补齐5类独占证据目录后59项全通过。两次失败工件 `p0-tool-table-action-regression.xml`、`p0-tool-table-first-regression-failure.xml` 保留。编译与本轮 diff 检查通过，无依赖变更、前端/API契约变更，不重复前端构建。

当前分类：**此前5项迁移兼容失败已修复并完成上述范围验收。** 这是隔离 schema 的实际迁移/运行存储证据，不能代替真实部署历史数据迁移与投影核对，也没有恢复两份旧 Worker 原件。P2 完整修订写入、默认 Step 多用例闭环、DeepSeek 人工录制、其他未验收产品场景仍未完成。配置 MySQL、业务 PostgreSQL、原件 RustFS 及 Provider 原生协议不变；Step 已认证。

### 7.112 默认 Step 多候选规划：600 秒诊断定位到输出截断（2026-10-19）

继续默认模型多候选端到端验收。此前 180 秒任务总时限在第三阶段调用中取消，不能据此判断候选选择或后续绑定。为区分任务时限与其他故障，在全新隔离项目重复同一三候选场景，只把该项目规划任务期限设为 600 秒；模型、transport、8192 输出上限和全局配置保持默认，没有 profile/reasoning 覆盖或受控模型。测试工件：`p2-default-multi-case-step-timeout600-20261019.xml`、同名目录中的 `verification.json`、`planning-task.json` 与 `PG-planning.json`。

本次实际 **1 failed、2 deselected，268.88秒**。任务已进入 `finished`，状态 `failed`；错误为模型规划输出达到生成上限，原响应已保留、未自动重发、未创建测试运行。SDK/物理 HTTP 观测共两次请求，各自仅一次物理发送：一次以 `stop` 正常结束、`completion_tokens=6625`；另一次以 `finish_reason=length` 结束，`completion_tokens=8192` 达到配置上限，输入为8339 tokens、耗时136.513秒。流终止事件已正常收到，所以这不是600秒总时限到期，也没有证据支持重复 HTTP 发送。

保存的逻辑阶段检查点包含两个正式项：订单创建正式依赖 → 订单详情目标；无关的商品排序候选未入选。规划随后停在 effects/bindings 阶段，未得到完整绑定回执；没有创建正式 Run，业务 HTTP 为0，没有创建或清理订单。截断响应的映射记录显示 `output_format` 采取 `prompt_fallback`（`format_not_declared`），因此这次只通过提示要求符合回执 Schema 的 JSON 文本，没有请求供应商原生格式。默认模型配置声明 `json_mode=true`，但没有 `request_profile`；`map_model_request` 以 profile 中明确声明的 `output_formats` 决定是否映射具体格式，所以本次回退到提示。查阅[Step 5 Preview 官方结构化输出示例](https://platform.stepfun.com/cookbook/step-5-vision-structured)确认示例使用 Chat Completions `response_format` 的 JSON Schema；[官方 JSON Mode 文档](https://platform.stepfun.com/docs/guide/json_mode)也说明可用 `json_object`。当前验收证据只保留了 `api_base_url_hash`，没有确认本次账号实际连接的 host；官方能力是否适用于此配置仍需先核实，且 JSON Schema 是否能减少该阶段生成量尚无证据。直接失败原因是8192输出额度耗尽。候选选择、排序与依赖判定有阶段性证据，但整条用例规划和执行仍未通过。2026-10-20补核该样本的脱敏SDK流计数：正常轮reasoning字符14,808；截断轮reasoning字符30,526、最终正文33字符，reasoning与reasoning_content为同一内容的两种呈现，不相加。该证据表明输出耗尽时生成量主要发生在推理部分，但不能单独确定阶段职责、输入组织与模型行为的全部原因；没有发生协议转换，也不是600秒总时限到期。

协议核验：Anthropic 客户端继续调用官方 SDK `client.messages.create`，`await_provider_io` 仅包装网络传输异常；OpenAI Responses 客户端是独立 transport。此次默认 Step 请求沿其既有 OpenAI Chat Completions transport，不切换到 Responses。不得为使该样本通过而扩大生产默认输出上限、替换 provider 协议、自动重发截断响应或以受控 profile 代替默认模型。下一步安全确认当前配置的实际 host；仅在其与官方文档适用端点一致、且能力声明依据清晰时，才评估显式 JSON Schema 映射。然后再分析阶段提示/上下文；每次改动仍使用全新隔离项目和默认 Step 配置验收，直到获得完整绑定、正式 Run、业务请求/证据、结果及终态重投的闭环。

### 7.113 清理来源补核与停止同类 produced 重试（2026-10-20）

依据原方案附件、r4 原请求和固定检查点补核：查询目标只查询并删除前置创建的订单，却把 `cleanup_sources` 指向自身。服务端根据该来源派生 produced 范围，使查询目标也被要求声明产生效果；bindings 正确不能修正已冻结的逻辑来源。自包含创建/清理用例合法使用自身来源，因此不能一律禁止自引用或用 HTTP 方法猜业务身份。此前“逻辑通过”收窄为目标/前置选取、排除干扰和 bindings 有阶段性证据。

只读诊断副本仅纠正清理来源、派生逻辑哈希及 produced 范围，保持完整用例/工具/证据与当前默认配置；不恢复原任务、不创建 Run、不发送业务请求。实际退出1、120.752秒，一次 SDK/物理 HTTP200，length/8192、正文0，未取得正式声明；源文件与全局配置读回未改变。详见 [清理来源诊断](P2_CLEANUP_SOURCE_DIAGNOSTIC_20261020.md) 及本地 `p2-cleanup-source-probe-20261020/verification.json`。脚本依赖保留的失败批次原件，不能在缺少这些原件的新检出目录中独立复验；它显式关闭自身 SDK 重试，仅用于一次只读对照。

纠正来源不足以解决 produced 输出耗尽，停止该方向同类参数/提示重试。后续独立研究 honest unresolved 与必填声明的契约冲突、普通无正文删除的真实取证依据，以及 AI 规划与执行后核验的职责边界。没有放宽正式关系核验、增加输出预算、修改历史或切换供应商协议；默认项目级选取→排序→补全→执行仍未验收。整体 P0—P5 未完成。

### 7.114 默认 Step 性能工具到完整报告页的正常路径（2026-10-20）

先核对旧工具专项的 guard/analyzed 起点，再新增默认模型从用户消息发起的独立API/Agent Worker验收；未注入计划或检查点。两次公开会话批次分别1失败78.16秒、1失败54.46秒，均因私有目标缺少服务端可信范围；项目PATCH登记base_url仍不足以产生该范围。第一批的指标表查询错误已改为先保存并核验真实工具结果，避免掩盖原始拒绝。该发现属于目标依据接入问题，不改安全策略放行。

沿现有默认UI专项的独占目标前提准备隔离服务端范围后，实际 **1 passed，170.32秒**。默认Step模型/8192参数原样使用，4次模型HTTP、3次现有审批，实际选择引擎、探针、performance-test-runner与report-writer；k6冒烟/正式各一次。1VU/2秒本地目标取得10600样本、错误率0、P50/P95/P99均有原值，PG指标与输出一致。13份性能工件真实下载及执行原件SHA核对通过，真实Vue构建/Chromium报告页与JSON下载通过；API重启后重投原终态任务，新增模型HTTP、ToolJob及性能运行均0，全局模型读回未变。4个完整受管容器ID的补充只读inspect确认均已不存在。详见 [实际调研与边界](P5_DEFAULT_PERFORMANCE_AGENT_RESEARCH_20261020.md)，JUnit与安全摘要保存于 `p5-default-performance-agent-owned-20261020.xml` 和同名目录。

本批只将“已有服务端可信目标时，默认性能Agent到报告的正常路径”列为通过。普通会话公开目标登记尚未闭环，不把隔离服务端夹具当作用户授权入口；默认安全Agent、性能故障/容量/AI流式指标、P2多用例执行、人工DeepSeek录制及其他目标仍未完成。没有生产代码、模式/工具权限或Provider协议修改，没有生产迁移；配置MySQL、业务PG、原件RustFS边界保持。

### 7.115 执行目标可信上下文的客户端来源隔离（2026-10-20）

沿7.114目标依据问题继续核对生产入口，发现客户端消息context可残留`trusted_resource_scope`、`resource_scope`、`trusted_target_hosts`及`trusted_environment`。现有工具策略消费这些字段，公开会话元数据限制无法覆盖消息入口。先实际运行四参数入口回归：4失败；再经生产输入编排、工具图入口及原生Chromium复现：伪造scope使工具completed且本地HTTP收到GET /，实际1失败。失败发生在第一个字段，未将其他字段的入口残留混称网络放行证据。

修复仅把四字段纳入既有客户端清理循环，复用结构化日志；服务端会话范围和环境按原机制恢复，不新增授权入口或修改审批策略。四文件回归实际 **133 passed，无跳过，18.91秒**，其中真实浏览器验证四种客户端字段均denied且无HTTP，服务端范围在客户端冲突字段下仍有效，页面随机标识与已完成ToolJob快照相符。该专项ToolJob使用内存Store与本地导出，不代表PG/RustFS或公开Worker验收。Python编译通过，未改前端、API schema或生产数据库，不重复构建与迁移。调研、复现命令和限制见 [可信上下文来源调研](P0_INPUT_TARGET_AUTHORITY_RESEARCH_20261020.md)。

当前只完成此输入边界缺陷修复。公开目标登记、P2默认模型多用例规划/执行及其他未闭环目标保持未完成；不从项目base_url或客户端字段推导新授权。Anthropic仍使用Messages，公共等待函数await_provider_io不转换协议；本批没有修改模型客户端或全局模型配置。


### 7.116 Step 默认模型认证成功，但 Step Plan 模型目录不匹配（2026-10-20）

按用户确认的 Step 认证继续默认多用例验收。只读 MySQL 得到当前 OpenAI-compatible 配置指向 `api.stepfun.com/step_plan/v1`，模型 ID 为 `step-5-preview`。同一凭据对该端点执行官方 SDK `GET /models` 成功，返回目录含 `step-3.5-flash`、`step-3.5-flash-2603`，不含 `step-5-preview`。当前 MySQL endpoint hash 按 `model_invocation_parameters()` 的 JSON 序列化口径与该新规划任务记录完全相同，确认目录查询对应本次测试所用端点。认证通过；该账号/端点未证明支持当前配置模型。

用新隔离项目运行完整多用例任务，唯一规划 HTTP 返回400，安全错误类型为 `request_params_invalid`，未创建Run或业务请求。新增最小 SDK 对照：Step5 模型在 `max_tokens=64/16384`、`stream_options.include_usage` 开/关下均返回同一400；未记录响应正文。Step 官方 Chat Completions API 文档支持 Step5 并展示标准 `/v1/chat/completions`，但没有证据确认 Step5 可在当前 Step Plan 目录使用。结论为配置服务目录/模型 ID 不匹配的强证据推断，不是认证失败、提示长度或单独由 `stream_options` 导致。

测试探针的安全错误元数据回归及 SDK 事件钩子专项 **11 passed，21.72秒**；真实默认多用例任务 **1 failed，46.33秒**，规划未完成、Run及业务HTTP均0。MySQL 全局模型记录没有修改；P2 当前准确状态是**Step 认证已确认，但 `step-5-preview` 在已配置 Step Plan 端点不可用，不能继续声称默认 Step 多用例规划已真实验收**。需先明确有效模型/端点组合，再在全新隔离项目完成选择、依赖补全、正式执行、结果及终态重投。完整 P0—P5 目标保持进行中。

依据：`P2_DEFAULT_CURRENT_RESEARCH.md`、`p2-default-multi-case-safe-error-20261020/step-parameter-probe.json`、`step-model-catalog.json`、同名目录的 `verification.json`。没有生产代码、协议或 MySQL 默认配置变更。


### 7.117 Step 两个 endpoint 的实时推理可用性（2026-10-20）

为细化§7.116，仍使用当前 MySQL 默认模型凭据做极短对照，未保存错误响应正文或模型输出：

- `api.stepfun.com/step_plan/v1`：官方 SDK `GET /models` 成功，但模型目录中 `step-3.5-flash-2603` 的最小 `model+messages` 推理请求仍返回400 `request_params_invalid`。追加 `stream`、`max_tokens=64`、`stream_options.include_usage` 的对照仍为400。
- `api.stepfun.com/v1`：`GET /models` 成功，目录含 `step-5-preview`；对该模型发一条极短 `max_tokens=64` Chat Completions 请求返回402 `quota_exceeded`。

故当前认证查询有效，但两条服务路径都没有成功推理：Step Plan 目录未列Step5且目录内模型的最小调用也失败；标准目录列出Step5，但当前推理额度不足。不能将问题归结为单纯模型名称或请求中的某一个可选参数，也不能判定应用 Provider SDK 是唯一根因。10月19日存在相同 endpoint hash 下的真实单用例成功记录；因凭据不保存，无法证明历史和当前密钥/额度状态相同。完整材料见 `P2_DEFAULT_CURRENT_RESEARCH.md` 与 `p2-default-multi-case-safe-error-20261020/step-*-probe.json`。

当前 P2 默认 Step 多用例规划→执行仍未验收；用户已确认认证不代表当前账户有推理额度或 Step Plan 目录含目标模型。没有修改全局 MySQL 配置，也没有改 Provider 原生协议。后续在可用的 Step5 推理通道恢复后，以新隔离项目继续真实默认模型验收；完整 P0—P5 仍进行中。

### 7.118 UI录制响应观察窗口与动作序号投影（2026-10-20）

依据现有 `recorder.js`、`recording_quality.py`、`assertion_suggester.py` 和 PostgreSQL 事件样本。录制脚本已经在 `page_response.response.action_local_seqs` 记录同一观察窗口内的动作局部序号，但质量投影此前只把 `page_response` 标成观察噪声，没有把它映射为可审阅的 PostgreSQL 动作事件序号；因此网络/DOM响应虽已采集，质量和草稿投影中仍看不清它对应哪些已录动作。

在既有 `recording-quality-v1` JSON 投影增加 `response_action_associations` 与 `unresolved_response_associations`：只按相同 `page_id/frame_id/document_id` 和精确 `local_seq` 匹配，要求动作事件在响应事件之前；缺失、歧义、非法引用保留固定原因代码，不猜测修复。关联明确标为 `temporal_after_actions`，不声称业务因果。投影只摘要网络观测状态和 outcome 数量，不复制网络路径、查询串、请求/响应正文。原始 PG 事件保持追加写；噪声继续保留为证据且不进入可回放步骤，重复动作只标候选，不自动删除。现有草稿将同一质量对象带入 `test_data.recording_quality`，不改外部 UI/API Schema、数据库迁移、MySQL 配置或 Provider 协议。

实际验证：

| 范围 | 实测 | 工件 |
|---|---|---|
| 确定性投影与负向引用 | `test_recording_quality.py`：11 passed，0.17秒；跨文档错配不关联，重复/非法引用、同作用域歧义及动作晚于响应均产生诊断 | 无独立 JUnit |
| 真实 Chromium fetch/XHR 与 PG 事件 | `RUN_LIVE_RECORDING_NETWORK=1`，1 passed，27.56秒；一次点击对应 fetch 200、fetch 404、XHR 201 三条完成响应；质量投影关联到同一动作 | `p5-recording-response-association-live-20261020/`、`.xml` |
| 质量投影进入草稿及原事件不变 | `RUN_LIVE_RECORDING_QUALITY=1`，1 passed，22.07秒；真实 PG 事件重读一致，草稿 JSON 的质量投影相同 | `p5-recording-response-association-quality-live-20261020/`、`.xml` |
| DeepSeek 公开首页真实 Chromium 录制 | `RUN_LIVE_DEEPSEEK_RECORDING=1`，更新后的回归1 passed，29.21秒；页面HTTP 200，6条原始事件，交付完整性 `verified`，1个响应窗口关联到4条此前动作，网络结果计数10，未解析引用0；无登录、聊天提交或模型调用 | `p5-recording-response-association-deepseek-live-20261020-rerun/`、`.xml` |

`compileall` 与 `git diff --check` 通过。以上证明应用采集的 DOM/fetch/XHR 观察可确定性关联到同一浏览器文档中的动作，并从 DeepSeek 页面真实记录到该投影；不会证明物理用户通过完整 Vue 界面创建录制、服务端业务因果、响应正文完整性、closed Shadow DOM、beacon/WebSocket/Service Worker/导航请求，或默认 AI 实际消费该条 DeepSeek 知识。selector 修复评审、失败定位报告和完整 UI 产品流程仍未完成；Step 默认多用例验收继续等待可推理模型/额度。完整 P0—P5 保持进行中。

### 7.119 受控提供商正式回执的真实 API 进程中断恢复（2026-10-20）

复核 `Agent_Server/tests/test_receipt_process_recovery_live.py` 与现有隔离工件。该测试在生产 API/Worker 和 MySQL、PostgreSQL、Redis、RustFS 隔离环境中，分别于纠正请求 `invoking` 检查点已持久化、以及响应对象和 `phase=response` 检查点已持久化后，杀死拥有该请求的 API 进程；等待原租约自然过期后，由新 API 和生产 Worker 继续恢复。不是同进程抛异常的模拟，也不重置 RunItem 或租约。

两种边界均通过：`invoking` 恢复后不重发纠正请求，保留未知副作用并以 `error` 结束；保存响应后恢复读取和核验原对象，正式结果为 `passed`。两场景各有 POST/GET/DELETE 三次业务效果，恢复创建第二个 attempt，终态队列重投新增模型/业务请求为零，清理后没有残余业务记录。测试运行结果为 **2 passed、0 failed、0 skipped，690.50秒**，见 `p0-receipt-process-recovery-current/verification.json`、`p0-receipt-process-recovery-current.xml` 及该目录的 `invoking/`、`response/` 原始证据。

边界：这里的模型服务是受控 HTTP fixture，不代表默认 Step 提供商的纠正语义/网络故障验收；Step 当前 entitlement 不可用，P2 多候选规划没有进入完整执行。该结果只关闭这两个受控提供商的真实 API 进程中断组合，其他中断、生产部署和历史数据恢复仍需单独验证。没有改生产代码、模型协议、全局配置或数据库结构；完整 P0—P5 仍未完成。

### 7.120 并发首轮下的重复语义模型调用复现与修复（2026-10-20）

先在真实 `SessionService.send_message`、`InputOrchestratorService` 和 `SemanticIntentService` 调用链中建立锁持有/状态仍为 `idle` 的并发窗口。修复前回归失败：第二条输入被调用两次，模型运行时记录为 `first request → second request → second request`。根因是 `send_message` 只要发现锁已占用就先完整语义编排；若持久会话状态尚未转为忙态，忙处理返回 `None`，取得锁后又完整编排同一输入。

修复后仅当持久状态为 `running`、`waiting_approval` 或 `interrupted` 时执行忙态预判并保留现有即时排队/拒绝行为。锁已占用但持久状态尚非忙时，记录 `session_input_waiting_for_lock` 并等待该锁，取得后只编排一次；忙状态集合提取为共享常量，避免预判与忙处理的状态定义分叉。

回归使用真实 `SemanticIntentService` 和官方 OpenAI SDK 发往本地 TCP/HTTP fixture。人为暂停第一条输入的意图模型调用，使第二条在首条尚未提交忙状态时到达；修复后fixture收到两条各一次、共两次请求。将同一最终HTTP回归临时对照旧分支，实际 **1 failed、1 skipped、17 deselected**；服务端请求体序列 first/second/second，第二条被真实SDK重复发送。恢复修复后组合运行 `tests/test_busy_session_queue_concurrency.py tests/test_session_execution_ownership.py tests/test_semantic_intent_failure_visibility.py`：**31 passed、27 skipped，13.37秒**；跳过项为未启用的 PostgreSQL 存储门控。证据证明本地fixture接收次数，不外推到生产远端接收。未修改 Provider 协议、提示/参数映射、数据库结构或用户消息内容。

限制：修复关闭的是已确认的并发语义分类重复调用；仍需验证意图识别逻辑调用如何与其后生成的 `turn_id/trace_id` 及 Agent/工具后推理串联。provider HTTP attempt 计数与本地服务端接收数的边界也保持区分。该修复不代表全系统重复发送已排除，P0—P5 仍未完成。

### 7.121 P2 清理来源与回归结果快照身份复核（2026-10-20）

根据 `P2_CLEANUP_SOURCE_DIAGNOSTIC_20261020.md` 中保存的默认 Step 失败计划，确认目标项只查询并删除前置项创建的订单，却将自身写为 `cleanup_sources`。规划执行器按该字段把来源加入 `produced` 声明范围，因此这个语义错误会让前置项和目标项都需要产生声明。修复规划提示版本为 r5：清理来源必须是实际产生被清理对象的 item；目标项自己的清理步骤不构成自引用依据。完整用例明确证明同一 item 创建并删除同一对象时仍允许自引用。没有按 HTTP 方法添加确定性推断规则。

同时修复回归依据选择：候选结果先核对保存的 Result、RunItem 和 Attempt 身份，再从该 Attempt 的归档 SHA 读取其固定计划；不能使用已变化的当前 Run 投影替代失败结果的原计划。PostgreSQL 在同一只读事务中锁定 Run 投影并读取归档。由于自动阻断依赖也会产生正式结果，为这些合成 Attempt 保存创建时的计划身份；MySQL 配置、PostgreSQL 业务证据分层不变，无数据库迁移。

执行回执提示版本 v7 另外明确：自然语言整句预期应使用 semantic 检查，不能从同一段描述推造布尔、数字或不相关字符串的确定性比较。它约束模型如何评估自然语言，不改变固定用例预期或检查器的比较语义。

代码逻辑回归覆盖规划角色/Prompt 版本、真实失败结果与 Attempt 对应、当前 Run 投影变化后的原计划读取、合成阻断结果的计划 pin，以及旧 Attempt 不追溯补绑：六个相关测试模块 **73 passed、32 skipped**；跳过项是未启用的真实存储门控。之后启用隔离 MySQL/PostgreSQL/RustFS 并运行真实用例存储、回归、阻断结果和 Markdown/DOCX/PDF 环境限制测试：**14 passed、0 skipped**，JUnit 为 `p2-result-snapshot-20261020-134215.xml`，证据目录为 `p2-result-snapshot-20261020-134215/`。`compileall` 与定向 `git diff --check` 通过。

按用户最新说明重新探测当前 MySQL 默认 Step 配置：凭据已配置，`step-5-preview` 仍使用 OpenAI-compatible Chat Completions。通过 `ModelRuntimeService` 发一次最小请求，测试副本仅将输出上限设为32、SDK重试设为0；真实 HTTP 一次返回400，安全分类为 `subscription_or_plan_entitlement`。没有保存供应商错误正文/模型输出，没有修改 MySQL，也没有创建业务 Run。安全摘要见 `p2-step-runtime-probe-current-20261020-1338.json`。因此认证状态已确认，但当前推理授权仍未恢复；本次真实模型调用没有进入规划阶段，不能据此宣称多用例选择、补全或执行通过。P2 尚欠默认模型完整闭环和更多计划修订/旧结果兼容验收，P0—P5 仍未完成。

### 7.122 P0 意图、Agent、工具调用与 HTTP 请求追踪贯通（2026-10-20）

沿 `SessionService.send_message → InputOrchestratorService.orchestrate_async → SemanticIntentService.enrich → AgentTaskDispatcher/RuntimeService → model_invoker → tool_executor → provider SDK` 逐段核对。缺口原因是语义模型在 `ExecutionRequest` 创建前运行：原先语义调用没有用户 turn/trace，而 Runtime 随后另建 trace，导致前置意图请求与 Agent/工具后的模型调用不能按同一用户请求还原。

现在由输入编排在语义调用前生成或继承唯一 `turn_id/trace_id`，把 session、project、mode 和逻辑调用身份交给语义模型，并将同一身份写入后续 `ExecutionRequest`。持久任务使用领取时的身份；待处理队列在入队、排空和恢复时保存/复用身份。Runtime 主图沿用该 trace；每次模型推理仍有独立 `logical_call_id`，HTTP observer 单独记录 SDK attempt。`append_graph_event` 已为模型及工具事件加入 turn/trace 字段。

代码审查另发现：`model.response_received` 只记录工具数量/名称，工具执行事件有 `call_id`，但二者不能直接按工具调用身份对账。响应事件现增加 provider 返回的 `tool_call_ids`。回归验证连续两次推理使用相同 turn/trace、不同逻辑调用 ID，下一条模型请求里的工具结果仍指向前一响应的 tool-call ID；工具执行事件以同一 ID 标识。此字段只补充现有通用事件 payload，不更改 provider 协议、API schema、数据库结构或前端展示契约。

最终确定性回归（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_intent_safety_routing.py tests/test_model_trace_project_binding.py tests/test_runtime_interrupt_tool_calls.py tests/test_semantic_intent_trace_http.py tests/test_semantic_intent_failure_visibility.py tests/test_busy_session_queue_concurrency.py tests/test_session_execution_ownership.py tests/test_agent_task_dispatch.py tests/test_scene_knowledge_contract.py tests/test_live_model_tool_stream.py -q --tb=short
# 154 passed、47 skipped，30.67秒。跳过包括未开启的 PostgreSQL/真实模型门控，不计通过。
```

`test_semantic_intent_trace_http` 使用官方 OpenAI SDK 向本地 TCP/HTTP 服务端发实际请求，核对单次 `/v1/chat/completions` 接收、semantic intent 与后续 ExecutionRequest 的 turn/trace 相同，以及 logical-call ID 与 HTTP attempt 日志一致。`test_model_trace_project_binding` 验证工具后第二次模型请求保留相同 trace/turn、获得新 logical-call ID，并携带前一次工具调用的 `tool_call_id`。`test_runtime_interrupt_tool_calls` 验证工具执行上下文也保留相同 trace/turn。Python 编译检查与 `git diff --check` 另行执行。

默认模型端到端验收仍失败。以用户说明认证已处理后，使用当前 MySQL 活动默认 `step-5-preview`，启动隔离内存 Session 和真实 SSE API，发送只读 `session-history` 工具目标。测试两次各只发起一个新 turn；最新证据 `p0-live-model-trace-auth-current-20261020-143020/acceptance.json` 显示仅1个 `model.request_prepared`、1个 HTTP attempt、收到 HTTP 400，随后 `interrupted`；没有 `model.response_received`、tool-call ID、工具任务或工具后推理。此端到端尝试不把 Step 认证与推理 entitlement 混为一谈；之前同一默认配置的零重试最小调用已将供应商400分类为 `subscription_or_plan_entitlement`，并由模型目录 GET 证实凭据可认证。没有保存密钥、HTTP正文或模型输出，也没有更改 MySQL 默认配置。该失败不能作为追踪功能回归失败，也不能作为 Agent 工具闭环通过。

当前完成的是入口身份生成/传递、图事件工具 ID 对账字段及本地 SDK/HTTP/确定性轨迹验证。真实默认 Step 请求仍无法进入工具阶段，因此默认 provider 的实际工具循环、SDK重试/服务端请求数及断网恢复尚未验收；其他规划、生成、影响分析、持久 Worker 入口也需逐一核对。没有证据要求统一模型协议或改写适配层；Anthropic Messages、OpenAI Responses、Gemini 原生协议保持独立。整体 P0—P5 继续未完成。

补充核对发现，普通工具审批恢复和 Deep Agent HITL 审批恢复都会从持久状态重建 `ExecutionRequest`，但原来没有复制 `trace_id`。两条恢复分支现都从恢复状态传回 `trace_id`，避免请求身份字段留空。`test_intent_safety_routing.py` 对两条重建路径分别做了确定性断言；Deep Agent 可选依赖未安装，因此真实 Deep Agent agent-loop 参数化测试的4个场景仍为 skipped，不计通过。Anthropic 传输仍调用 Messages SDK；这项修复也没有改模型协议。

### 7.123 Agent Worker 检查点分裂与 Session 全量写入并发设计（2026-10-20）

新增[Session并发设计](SESSION_STORE_CONCURRENCY_DESIGN_20261020.md)，依据 `_deliver_notification`、`SessionStore` detached-value 契约、PostgreSQL全量metadata upsert、现有行锁metadata mutator、同类队列并发保护，以及真实受控Worker时间线制定。完整通知路径的确定性交错回归现已证明因果：通知投影返回后，运行方写入新的 `pending_turn`；通知继续使用旧副本判断分支，并对 `persist_transcript=False` 的事件消息执行全量保存，最终覆盖掉新检查点。

短期修复已落地：通知投影后重新读取当前会话；事件型通知不再通过 detached `SessionRecord` 全量保存；自动停止的状态、消息和事件由内存/PostgreSQL SessionStore 原子提交。修复前新增回归以 `KeyError: pending_turn` 失败，修复后通过；`test_intent_safety_routing.py` 与 `test_coordinator_durable_children.py` 合计 106 passed、28 skipped；存储原子操作的内存与真实 PostgreSQL 隔离表测试 2 passed。并发重复通知幂等专项仍通过。

本轮只完成通知写入的根因修复，尚未实施 session revision/CAS，也未完成所有 `save_session` 调用点审计和真实跨进程 Worker 恢复验收。独立Worker同进程受控测试批次此前为2失败、0跳过：受控模型只完成 `/login`，父会话仍有旧审批ID而最新 `interrupted` snapshot 已含等待工具项；跨进程测试使用1秒 reclaim 阈值，在模型请求前已出现 ownership uncertainty，不作为产品行为结论。默认Step UI场景正负向此前均在第一次推理请求收到400；保存的安全元数据为 `request_params_invalid`，没有 response body、code 或 param，不能据此改默认模型或协议。详细实施顺序、源码位置和验收边界见设计文档。

### 7.124 已配置替代模型的真实工具调用与流式响应复核（2026-10-20）

针对“换用其他模型是否会让系统报错”，先读取当前 MySQL 模型配置的非敏感字段：默认激活模型仍为 `step-5-preview`；`qwen3.8-max-0902` 与 `agnes-2.5-flash` 均有凭据配置但未激活，二者使用 OpenAI-compatible Chat。没有修改激活状态或任何凭据。为避免改动用户默认配置，以只返回固定配置对象的临时 Runtime Registry 调用生产 `ModelRuntimeService.invoke`；真实配置仍由 MySQL 加载，调用统一内部 DTO、请求映射、官方 OpenAI SDK 及现有 HTTP observer。每次单独设置 SDK `max_retries=0`，输出上限为64或128 token，只发送合成工具/文本请求。

| 配置 | 实际请求 | 真实结果 | 可证明范围 |
|---|---|---|---|
| `qwen3.8-max-0902`（Qwen provider / OpenAI-compatible Chat） | 一次合成工具调用，单个 SDK HTTP attempt | HTTP 401，`http_error`，未得到工具调用 | 当前凭据/服务访问未通过；因未收到有效模型响应，不能评价工具参数序列化或响应解析 |
| `agnes-2.5-flash`（OpenAI-compatible Chat） | 要求调用 `record_test_step(action="search", target="status_panel")` | 一次 SDK HTTP attempt；`completion_status=tool_calls`；解析出的函数名和两个参数均与请求相符 | 该配置经生产 ModelRuntimeService、Chat 请求映射及 OpenAI SDK 实际完成了结构化工具调用 |
| `agnes-2.5-flash`（同一调用路径） | 流式请求“只输出 READY” | 一次 SDK HTTP attempt；收到2个文本片段；流终态 `complete`；聚合文本为 `READY` | 该配置的实际流式分块、聚合及最终响应解析通过 |

脱敏结果和输出边界见[替代模型实时验收记录](p0-model-provider-live-20261020.json)。这两项 Agnes 探测证明的是模型运行时/适配器层的真实外部调用，不是前端设置页激活路径、文档生成 Worker、按用例规划或 API Agent 的全链路。Qwen 的401也不能归因于协议或映射错误；不在没有成功响应的情况下臆测其具体凭据原因。

Anthropic 客户端仍调用 `client.messages.create(...)`，OpenAI Responses 仍由独立的 `OpenAIResponsesClient` 调用；现有证据支持保留“统一内部契约 + provider 原生 wire protocol + 显式模型请求 profile”的适配层，不把所有模型强行变成一种协议。Step 的账号认证按用户确认记为通过，实际推理仍不可用：此前 MySQL 默认配置的真实模型请求返回供应商 entitlement/计划错误，其他 Step 端点还出现目录不含目标模型或 quota 错误，详见 §7.116—7.117、7.121—7.122。本轮不重复发送 Step 请求、不更换默认配置。

本轮没有修改生产代码、模型配置、数据库结构或用户数据。§7.125补上了隔离配置下的真实文档生成Worker链路，但没有覆盖设置页激活流程、完整多模块替代模型场景或Qwen响应映射；这些仍需验收。Step推理通道恢复后的默认模型闭环也未完成；不能把替代模型专项写成P0—P5总体完成。


### 7.125 替代模型经过真实文档生成 Worker 的单模块验收（2026-10-20）

按§7.124尚缺的产品链路范围，先复用 `test_document_generation_default.py` 的隔离 API/Worker/存储设施和 `default_model_probe` 的真实SDK发送观测，再补一个单模块专项入口。源码依据为 `DocumentCaseGenerationSources.capture → GenerationTaskService.submit_document → 解析/生成 Redis Outbox → 独立 CLI Worker → RustFS对象及PostgreSQL草稿回执`。当前活动默认模型不可作为本次可用性证据；Agnes 已有同日 `ModelRuntimeService` Chat工具和流式响应成功证据，因此从MySQL只读取已配置 Agnes 记录，将同一配置复制到隔离 MySQL 并仅在那里激活。产品模型设置、全局激活状态和凭据均未修改；任务策略为 `single_attempt`，输出限制4096 token，意图是验证真实替代模型服务链而非默认设置页切换。

使用实际 Markdown 原件描述订单查询的用户隔离、订单不存在负向行为和只读约束，经生产 API 上传、生产解析 CLI、固定解析快照、单模块文档生成提交及独立生成 CLI。真实 Agnes HTTP 返回200，模型输出被适配为合法 JSON 用例，生成任务 `completed`；用例保存为草稿，`active_version_id` 为空，来源指向该解析快照，完整 JSON 含本人订单查询、跨用户拒绝、不存在订单错误以及不修改数据的验证。RustFS固定范围和原响应对象按大小/SHA重读校验。观测到1次 SDK create、1次物理HTTP请求，终态重投新增调用为0；全局默认模型的安全指纹前后相同。

| 验收 | 实测 | 工件 |
|---|---|---|
| 真实文档生成Worker闭环 | `RUN_LIVE_ALTERNATE_DOCUMENT_GENERATION=1`，`tests/test_document_generation_alternate_live.py`：**1 passed，57.80秒**；实际请求只有1次、HTTP 200；单模块和草稿版本已保存 | `p1-alternate-document-generation-current-20261020-rerun2/verification.json`、`fixed-scope.json`、`generation-result.json`、`draft-version.json`、RustFS对象下载及 `artifact-manifest.json` |
| 解析/分段/流式合同回归 | `tests/test_document_generation_sources.py tests/test_document_generation_stream.py tests/test_model_clients.py::test_anthropic_client_maps_request_and_parses_response`：**14 passed，7.04秒** | 本轮 pytest 输出；本地回归，无外部模型调用 |

本节证明的是**一个已配置OpenAI-compatible Agnes模型**经生产上传/解析/文档生成Worker实际生成并保存一个模块；不证明设置页把用户选择写入运行时、不证明多模块替代模型的独立恢复、文档生成业务充分性或其他Provider在线可用。Step认证已通过，但其当前推理额度/endpoint仍不可用，因此本轮不调用Step。Qwen仍因当前401无法评价响应映射。Anthropic仍使用Messages，Responses仍只由OpenAI适配器使用；本批没有修改Provider协议或生产源码。全局配置在MySQL，隔离测试配置也使用MySQL，业务/草稿在PostgreSQL，原件/响应在RustFS；完整P0—P5继续未完成。

### 7.126 Step 认证后的只读工具/SSE 重新验收（2026-10-20）

用户确认 Step 账号已认证后，按 `tests/test_live_model_tool_stream.py` 的既有隔离入口重新验证当前 MySQL 活动默认模型。该测试读取 MySQL 配置但不初始化或写入模型配置；会话与 ToolJob 使用内存 Store；通过正式 Session SSE route 调用生产 Agent、ModelRuntime 与只读 `session-history` 工具，不替换模型、工具或 HTTP 执行器。它不覆盖 durable dispatcher、PostgreSQL业务持久化、MCP、浏览器或 API 重启恢复。

执行 `RUN_LIVE_MODEL_TOOL_TESTS=1` 下的 `tests/test_live_model_tool_stream.py`，结果 **1 failed，30.32秒**。安全验收文件 `p0-live-model-tool-step-auth-current-20261020-2000/acceptance.json` 记录：当前模型为 `step-5-preview`；turn 为 `interrupted`；1个逻辑模型调用、1次物理 HTTP attempt、状态码400；没有模型响应、工具调用 ID、ToolJob 或工具后推理。失败断言是 Session turn 未完成。未保存供应商错误正文或模型输出，没有更改 MySQL、发送第二次请求或创建业务 Run。Windows 本地 SSE 测试退出时另有连接 reset teardown 日志；它不是这次 turn 中断的模型状态证据。

本项区分为：**账号认证按用户确认通过；当前配置推理请求仍不可用已有新鲜 HTTP 400 证据；只读工具/SSE 完整执行尚未验收。** 这次重试没有解决此前的 Step endpoint/model entitlement 问题，因此不再重复发送相同请求；P2 默认 AI 完整闭环继续等待推理通道恢复，其他不依赖 Step 的模块继续推进。

### 7.127 正式 Run 计划修订实施边界复核（2026-10-20）

为把§7.110已有的领取协调基础转为可实施计划，重新核对当前生产路径及项目测试课程中的不可重复执行、幂等、补偿和版本追溯要求。源码事实如下：

- `case_plans.py` 只有策略、创建、读取和首次启动 Run 的路由；`CasePlanRunService` 只有初次 `create`。`CasePlanStore.save` 对既有 ID 使用 `ON CONFLICT DO NOTHING`，CasePlan 是不可变候选，没有正式 Run 修订入口。
- `TestRunRecord` 当前只含一个执行 `plan_snapshot`；PG `plan_snapshots` 按 SHA 保存原始快照，Attempt 通过 `plan_snapshot_identity` 固定自己的原依据。RunItem 有 `(run_id,position)` 与 `(run_id,case_id,case_version_id)` 唯一约束；SuiteItem 另有 Suite 内的用例版本与位置唯一约束。
- `RedisExecutionDispatcher.handle` 在 Run 非终态时持续领取；无可领取项但仍有 queued/active/waiting 项时抛出 `TaskDeferred`。Worker 对 defer 不 ACK 消息，现有 reclaim 路径使用 Redis `XAUTOCLAIM`。因此同一未终态 Run 的后续 queued RunItem 可由待恢复的派发任务继续消费；必须以真实 Redis/Worker 联调证明。completed/cancelled Run 不可借此重开。
- 领取事务获取计划投影共享 advisory lock；候选修订须获取同一资源的排他锁，并 CAS 校验预期修订号。现有取消事务先锁 RunItem、再锁 Run 行；修订也必须遵守 item→Run 锁顺序，并在锁定后重新读取 Run 状态，避免行锁顺序冲突和陈旧取消状态。

实施方向确认：候选修订仍通过现有规划链生成并另存为新 CasePlan，不覆盖原计划；修订记录、revision key/hash、父子快照 hash、变更摘要和理由属于 PostgreSQL 运行证据，完整快照继续存现有 archive。Suite 是初次冻结范围，不随 Run 计划修订覆盖；新增目标进入该 Run 的 RunItem 投影，移除的从未领取 queued 项保留为带修订原因的 `cancelled` 历史条目，不伪造测试结果、不删除既有行。已领取、曾执行/过期重领和终态条目不改写；有 Attempt 的 queued 项必须继续依其 pin 读取原归档。对 `(case_id,case_version_id)` 冲突或再次尝试复活 terminal RunItem 必须明确拒绝，不能重置已完成/终态条目。修订后的拓扑依赖、顺序、统计、归档与修订历史须在同一 PostgreSQL 事务提交。

已有 advisory lock、JSONB 归档和 Run worker loop 为该方案提供了代码依据；但没有 revision table/schema、候选合并算法、公开 API、前端历史显示或 Agent 接入。接下来先为修订历史定独立 PG 表及追加/幂等契约，再实现内存与 PostgreSQL 双后端修订服务；真实验收必须覆盖 revision-first/claim-first 竞争、独立 RunItem 并发领取、旧 Attempt 按旧快照恢复、取消竞争、增删后统计和实际 Redis defer/reclaim。受控候选计划只能验证后端契约，不能替代默认 Step 自主产生修订的验收。此次仅复核并收窄实施边界，未修改生产代码、MySQL 配置、PG 业务数据或迁移。

### 7.128 P2 Run 取消加入计划修订共享锁并发复验（2026-10-20）

继续审查§7.127时发现取消路径是遗漏的并发写者：领取事务持有计划投影共享 advisory lock；但原 `_cancel_run_sync` 在不持有该锁时读取 Run、批量锁定未终态 RunItem、再锁 Run。新加的真实 PostgreSQL 测试在单独事务持有修订排他锁时调用正式 `cancel_run`，修复前取消仍能完成，**1 failed，9.30秒**，证明已有领取/修订竞争实验不能覆盖取消。

在 `PostgresTestRunStore._cancel_run_sync` 的首次状态读取前增加既有 `lock_plan_projection(..., exclusive=False)`。因此取消在取得 Run/Item 行锁前先与修订 writer 互斥；修订仍需按 Item → Run 行锁顺序并在锁后重读状态。迁移、API契约和 Run 状态语义未改变。

修复后竞争场景 **1 passed，6.61秒**。最终完整所选回归运行：

```text
RUN_LIVE_RUN_DEPENDENCIES=1
pytest tests/test_run_plan_projection_lock.py \
  tests/test_run_revision_coordination_research.py \
  tests/test_run_item_dependencies.py \
  tests/test_test_run_duration_transitions.py \
  tests/test_cancelled_run_missing_approval_recovery.py -q -rs --tb=short
37 passed, 8 skipped, 27.42秒
```

8个跳过项明确要求另设 `RUN_LIVE_POSTGRES_TESTS=1`。该结果证明所选真实领取/候选协调/取消/依赖/恢复行为通过；**完整计划修订 API、revision history、条目增删、Redis defer/reclaim 和默认 Step 自主修订仍未实现/验收**。此次仍未修改 MySQL 模型配置、既有用户 Run 数据或模型协议。详细边界与证据见 [P2 Run 修订设计复核](P2_RUN_REVISION_DESIGN_VALIDATION_20261020.md#取消协调边界的真实复核与修正)。

### 7.129 P2 执行中 Run 计划修订后端/API 与 PostgreSQL 顺序缺陷修复（2026-10-20）

实现前先依据 §7.127/§7.128 和 `P2_RUN_REVISION_DESIGN_VALIDATION_20261020.md` 核对 CasePlan、Run、RunItem、SuiteItem、Attempt 快照、PostgreSQL advisory lock 与 Worker claim 路径；并按本地《Agent 轨迹与协议鲁棒性》关于重复执行、幂等、半成功及恢复轨迹的要求验收。方案限定为：复用现有 CasePlan 规划/校验，更新当前 Run 的可执行投影，不覆盖初始 Suite，不改模型协议或 MySQL 模型配置。

新增 `plan_revision` 合并逻辑、内存/PG Store、PG revision 历史、预期 revision 与幂等键校验，以及项目级 POST 修订和 GET 历史 API。Run 快照以原有 SHA archive 方式保留；成功写入在同一 PostgreSQL 事务中完成，修订用现有计划投影排他 advisory lock，与 claim/cancel 共享锁协调。未领取且从未尝试的 queued 项可按候选重映射；新增目标成为新 RunItem；可安全移除项成为 cancelled 历史。已领取/有 Attempt/结果的项不重置。Suite 保持初始冻结内容。前端仅补 API 类型及调用方法，没有修订界面。

新功能验收发现一个 PostgreSQL 独有的根因缺陷：`_revise_plan_sync` 先将 position 列移到负值以避免唯一约束冲突，但 `_write_item` 只更新 JSON `record`，遗漏独立的数据库 position 列。故详情查询和 claim 仍按旧列排序。先加内存/真实 PG 测试，修复前结果 **1 failed（PG）、1 passed（内存）**；将 `_write_item` 改为同时写入 `position` 后，详情顺序、列与 JSON 一致性及真实 claim 顺序 **2 passed**。

最终所选后端回归：`RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_RUN_DEPENDENCIES=1`，运行计划创建/修订、RunItem 依赖、投影锁、候选修订协调、运行耗时、取消恢复和场景工具合同七个测试文件，**84 passed、8 skipped，61.95秒**。其中隔离 PostgreSQL 验收覆盖新增/取消、旧 Attempt 快照与检查点、并发幂等重放、claim/cancel 锁竞争、事务失败回滚及修订后实际 claim；测试使用本地受控模型，不调用 Step。前端修订 API 测试 **1 passed**；`npm run build`（`vue-tsc` + Vite）成功。

再以 `RUN_LIVE_PLAN_RUN_CREATION=1 RUN_LIVE_POSTGRES_TESTS=1` 对整个 `tests/test_case_plan_run_creation.py` 实测：**45 passed、0 skipped，29.88秒**。其中真实 Outbox/Redis/生产 Dispatcher/Worker 的 defer→`XAUTOCLAIM` 场景验证新增 RunItem 在同一 Run 后续被领取一次、前置完成后终态并 ACK；业务执行器是受控回执，不代表 Agent 工具或默认模型验收。

**当前边界：** Run 修订后端和 REST/API 契约已实现并通过受控真实存储验收；新增 RunItem 也已通过真实 Outbox→Redis→Dispatcher/Worker defer→`XAUTOCLAIM` 实际消费测试（受控执行器，不代表 Agent 工具验收）。尚无 revision 事务提交前/后真实进程终止恢复验收。Agent 还没有自主生成候选计划并提交修订，前端尚无修订/历史页面。默认 Step 已认证，但现有 `step-5-preview`/Step Plan 组合目录或额度近期仍导致实际推理失败，P2 默认“项目选取→排序→补全→Run 执行”仍未闭环。8 个门控跳过不计为通过，完整 P0—P5 继续进行中。详见 [实现后修订边界与验收](P2_RUN_REVISION_DESIGN_VALIDATION_20261020.md#实现后的验收与状态2026-10-20)。

### 7.130 Run 计划修订的人工评审页面与 Step 认证后状态复核（2026-10-20）

先按真实契约核对 `CasePlanRunService.revise`、`RunPlanRevisionRequest`、项目级 POST/历史 GET、`CasePlanDialog` 和 `ProjectsView` Run 详情：修订只允许当前项目的进行中原生 Agent Run；候选必须是已保存的同项目 CasePlan；需要 `expected_revision`、幂等键及原因；已领取/执行条目不可重置。于是新增 Run 详情修订面板，支持从现有 `CasePlanDialog` 生成并审阅候选（仅选择为候选，不会另启动 Run），也可粘贴已有计划编号；选择后核对项目身份。面板读取追加历史、展示每条新增/更新/取消记录；冲突版本禁用提交，结果不明时浏览器保存原请求并以同一幂等键重试或从历史核对。成功后重新读取正式 Run 详情与项目运行列表。此页面是人工审查/提交入口，没有接入 Agent 自主产生候选或调用修订 API，也不绕过现有项目计划生成和评审。

功能验证：`RunPlanRevisionPanel.test.ts`、`CasePlanDialog.test.ts` 和 `projects.plan-revisions.api.test.ts` 联合回归 **94 passed**，覆盖历史与条目变化、修订 CAS/资格、幂等重试、同项目候选选择、跨项目候选拒绝，以及候选模式不启动新 Run；`npm run build` 的 Vue 类型检查和 Vite 生产构建成功。既有后端 45 项隔离 MySQL/PostgreSQL/Redis/RustFS Run 创建/修订验收仍作为后端证据，不视为本轮 UI 对生产 API 的端到端验收。配置仍在 MySQL，Run/修订仍在 PostgreSQL。

Step 认证状态按用户确认记为通过；最新 MySQL 默认配置只读调用仍得到 Step provider HTTP 400，脱敏诊断正文为 `you have no active step plan subscription`，一次 SDK HTTP attempt、无模型响应。它说明当前 Step 推理订阅/计划授权尚未可用，不能归因 Anthropic/Responses 协议或通过改写适配层解决，也没有再次对相同输入重发。P2 默认 AI 的完整“项目选取→排序→补全→执行”继续是尚未验收，而非已通过。人工 Run 修订入口已实现；Agent 自动修订、revision 提交前/后进程恢复和生产页面/API 联调仍待做。完整 P0—P5 仍未完成。

### 7.131 P4 需求追踪矩阵与实际执行核验的构建版产品复验（2026-10-20）

#### 调研结论

复核 `test_requirement_execution_full_product.py`、`requirement_execution_product_scenario.py`、`test_document_impact_full_product.py`、`requirement_execution.py` 和需求执行 PostgreSQL Store 后，确认已有真实产品验收入口没有调用默认 AI：它使用正式执行服务创建 Run/Result/ToolJob，把受控业务 HTTP 的实际响应绑定到固定结果；内置构建版 App 和生产 API/独立 Worker 使用隔离存储。这个入口可验证“需求版本→验证点→固定用例版本→实际 Run/Result→人工选择的检查证据→需求追踪矩阵”产品链路，不应误写成默认模型自主测试或用户业务批准。

测试在实际页面创建含表格的 DOCX 版本差异（保留期限从 7 天改为 9 天），评审并建立两个验证点，只将其中一个关联到用例版本。随后针对被选用例执行固定正式 Run：初次 Result 失败；新回归 Run 的目标检查通过，但清理检查失败，因此回归 Result 仍为 `failed`，Run 生命周期本身正常进入 `completed` 终态。产品矩阵只显示被选验证点的部分检查通过，并明确“部分检查通过不能证明整个需求通过”；第二个验证点仍未覆盖。测试还验证原页面提交已落库但响应丢失后不重发、实际 API 并发修订返回冲突而不自动重定父版本、执行确认历史修订追加保存、API 重启后历史完整读取、归档后禁止新确认、终态重放不增加模型或业务请求。

#### 实际功能验证

执行：

```powershell
# Agent_Server
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-requirement-execution-product-current-20261020'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_execution_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-execution-product-current-20261020.xml
# 1 passed、0 failed、0 skipped，229.52秒。
```

验证使用真实构建版 Vue App、Chromium、独立 API/Worker、隔离 PostgreSQL/MySQL/Redis/RustFS 和本机受控 HTTP 业务目标。工件显示浏览器实际提交数与五条 PostgreSQL 执行确认历史一致；丢回执时仅一个请求已提交且仅有一个记录；确认记录可重读；回归中四条被选断言均通过，但 Result 仍为 `failed`、Run 进入 `completed`；重启后的历史读取和归档拒绝通过，恢复期间新增模型/业务 HTTP 调用数均为 0。生产验收 XML 为 `p4-requirement-execution-product-current-20261020.xml`，浏览器截图、HTTP、DOCX、PG 和 RustFS 工件位于同名目录。

本轮同时对模型适配层做当前代码回归：

```powershell
# Agent_Server
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_clients.py tests/test_model_request_profile.py tests/test_langchain_model_adapter.py -q --tb=short
# 67 passed，27.05秒。
```

上述模型测试经本地 HTTP fixture 核实 SDK 实际传输与 provider-specific 参数映射；不能代替真实远程模型逐家认证。Anthropic 仍由 `AnthropicMessagesClient` 调用 `client.messages.create`；OpenAI Responses 使用单独 `OpenAIResponsesClient`；两者共享内部调用 DTO 和结果解析契约。当前证据支持保留这种分层适配，不支持统一改成 Responses。测试没有修改模型配置或业务代码。

#### 状态边界

- **已有实现且本轮验收：** DOCX 需求版本、设计覆盖矩阵、固定执行结果读取、选中断言的人工核验记录、丢回执幂等、并发冲突拒绝、历史版本和归档恢复。
- **尚未验收：** 业务负责人对实际部署规则的批准；不同产品、接口、数据和多环境组合下默认 AI 影响质量；默认模型从差异分析到增量生成并在实际环境回归的完整业务闭环。
- **仍需补查/实现：** 按失败样本扩展格式和故障组合；只有证据表明现有需求/追踪模型缺字段或行为时才补实现。当前本批没有发现需要增加需求追踪 API/schema 的缺口。
- **模型状态：** Step 账号认证按用户确认及先前成功模型目录请求记为完成；当前默认推理探测已有 HTTP 400 entitlement 证据，P2 默认多用例 Agent 链路因此仍未验收。不要把认证完成写成推理可用，也不要重复发送同一个失败探针。

本轮仅增加方案/实施记录和实际验收证据；未更改生产模型适配器、MySQL 模型配置、PostgreSQL 业务 schema 或用例执行逻辑。完整 P0—P5 目标继续进行。

### 7.132 P3 API Agent 持久子 Worker 受控链路当前复验（2026-10-20）

前次独立 API 变体在 `wait_api` 的50秒内未就绪；旧验收 `finally` 又读取尚未初始化的时间线，导致 `UnboundLocalError` 覆盖启动超时。已将时间线容器初始化移到进程启动前。原 stderr 只有 Uvicorn 开始启动和 SQLAlchemy engine 创建记录，没有足够证据定位具体等待阶段；同一持久 Worker 变体修复验收夹具后重跑 **1 passed、0 failed、0 skipped，60.36秒**，所以不把一次未复现的启动超时归为已证实产品故障。

当前已通过的范围：隔离正式 API + 两个独立 Worker、MySQL 配置和 PostgreSQL 任务状态，使用本地受控模型与真实本机 API 目标执行 `/login → /auth/refresh → /profile`，三次业务请求均为200；旧凭据刷新后，后续请求确实使用新凭据。三个持久子会话报告3/3通过，API 重启后历史可读，终态重投新增业务和模型 HTTP 均为0。该测试没有执行 RustFS 对象读写，受控模型不计为外部 AI。

**尚未验收/实现：** 默认模型或其他已配置真实模型驱动的完整 API Agent 场景、更多鉴权失败/超时/依赖传值/幂等组合、RustFS 与结果前端展示的 API 测试闭环；首次启动超时的具体原因也尚未查明。Step 账号认证按用户确认为完成，模型目录查询有成功证据；默认推理 entitlement 的 HTTP400继续单独记录，P2 默认 AI 多用例闭环没有通过。本节只代表上述固定 API 受控场景验收，完整 P0—P5 仍未完成。

### 7.133 P5 安全 Agent 递归派发修复与真实 Agnes 验收（2026-10-20）

真实安全 Agent 验收复现了递归派发根因：只负责场景分析和失败分析的子会话也使用 `security_testing` 模式，并继承父会话的安全授权。子会话因此重新进入完整安全执行路由，而不是停留在分析，递归派发扫描任务。修复后这两类分析子会话使用 `default` 模式，并清除继承的执行授权；实际安全执行 worker 仍保留 `security_testing`、正式任务参数及工具指定，不改变 Agent 的安全操作方式。

验收还发现中文句子中的 URL 会把句末中文标点带入目标值，造成意图目标与服务端授权目标不相等。共享 URL 提取改为统一处理句末标点，并由安全授权目标回归覆盖。

实际功能验收使用独立 PostgreSQL/Redis/RustFS、真实 Agnes 模型、正式 API/Agent Worker、现有 Docker 安全 runner 和本机隔离 HTTP 目标。服务端会话元数据提供授权范围；whatweb 指纹、httpx 探测和 HTTP 响应头检查共3项任务均完成。报告核对为3个完成任务、4项发现、12项证据；API 重启后历史可读，终态重投没有新增模型 SDK 调用、物理模型请求、目标请求或工具任务。一次 `attachment-reader` 因场景没有上传附件而返回 `attachment_not_found`，不影响三项安全任务及报告；不将该非关键工具调用写成通过。证据目录为 `Agent_Server/docs/upgrade_2026-10-03/p5-security-agent-agnes-recursion-fix-20261020-234023/`。

安全路由及相邻会话/追踪回归 **161 passed、47 skipped，24.58秒**；跳过项不计为通过。真实 Agent 验收 **1 passed，187.35秒**。本验收证明该 Agnes + 本机隔离目标场景从正式 Agent 入口到 Docker 工具、证据和报告的链路；不证明 Step 默认模型在 entitlement 未恢复时可执行，也不覆盖其他目标类别、权限边界、工具故障矩阵或性能容量。整体 P0—P5 继续进行。

### 7.134 P2 Agnes 多用例真实规划对照与策略边界（2026-10-21）

修复 P2 真实验收夹具在重建 Worker 环境时丢失 `QA_DEFAULT_PROBE_DIR` 后，先运行针对环境重建、非目标正式前置用例及真实 SDK 事件观测的三个回归：**3 passed，6.56秒**。此前启动的旧 live 会话在 Worker 因该环境变量缺失退出后仍轮询 queued Run；Run 为零次领取，受控隔离资源清理后没有留下测试子进程。旧启动失败证据保留，不计为产品执行器失败。

随后用隔离 MySQL/PG/Redis/RustFS、实际 API 与 Worker、Agnes `agnes-2.5-flash` 进行“目标订单查询→必要订单创建前置→实际 ID 传递→清理”的正式多用例场景。该场景只使用替代模型，不能代表 Step 默认模型。两次执行均在规划阶段失败、没有创建 Run、没有业务 HTTP：

- 使用验收夹具原 `NEW_POLICY`（`preparation-scope-v1`、`case-effect-evidence-v1`、`case-effect-coverage-v1`）的运行 **1 failed，95.40秒**。5个 Provider HTTP 均为200且 SDK 流以 `stop` 结束，规划任务 5 轮后失败；具体校验错误为 `items.1.target.effect_evidence.cleanup_sources: Extra inputs are not permitted`。代码核对确认 v1 的 `FormalCasePlanItem` 没有顶层 `cleanup_sources`，而场景要求正式目标清理前置正式项创建的对象。该结果说明此 v1 策略不能表达该正式清理关系，不是 Responses 协议、网络或 Worker 执行失败。
- 启用项目隔离的现有 `QA_FORMAL_EFFECT_RELATIONS=1` v2 关系策略后，相关逻辑回归 **32 passed、5 skipped，6.13秒**；同一 Agnes 场景仍 **1 failed，98.00秒**。5个 Provider HTTP 均为200、5条 SDK 流均完整结束；逻辑规划 5 轮耗尽。逐轮回执显示：先把现有正式创建用例改成 `prepare` 并在逻辑阶段填写被禁止的 `input_bindings`，随后产生清理/依赖循环；后续修正又缺少 prepare `instructions`，以及给 prepare 项填写正式 case 身份。未创建 Run、未发生业务 HTTP。

结论收窄为：Worker 环境丢失已由验收夹具修正并通过专项回归；旧 v1 策略不支持本场景所需的正式项清理关系；现有 v2 Schema 能覆盖该关系，但 Agnes 在当前提示、5轮预算和本输入下仍未能选中现有正式前置项并返回有效逻辑计划。该项不能记为 P2 通过，也不能用 HTTP 200 或 v2 离线 Schema 回归替代真实模型规划成功。全局 MySQL 默认模型没有被替换；Anthropic 仍使用 Messages，OpenAI Responses 仍在独立适配器。

下一步须依据这两批已保存的真实校验回执复核逻辑阶段提示与“优先选取项目内正式前置用例”执行边界；保持 Schema 对错误关系的拒绝，不用放宽 extra-forbid、自动改写依赖或增加模型轮数来制造通过。提示或流程有据修改并通过对应回归后，再以全新证据目录复跑替代模型场景；Step 默认模型推理 entitlement 恢复后另行验收。完整 P0—P5 继续进行中。

### 7.135 P2 当前工作树复核、真实前置用例修正与协议核对（2026-10-21）

依据四份全新 Agnes 规划回执修正 P2，而不是把 HTTP 200 视为成功。第一份新检查点显示模型已选出正式“创建订单”用例作 dependency，但把用例 `case_key` 写入 `cleanup_sources`；该字段引用的是计划 `item.key`。另一个已复现的问题是关系 Prompt 在组合器之前裁剪 V2 Schema，导致文档预期构造器不能将其升级到 V3。现把逻辑 Schema 投影移到全部协议组合之后，明确正式前置的选择规则和 `cleanup_sources` 的 key 身份，关系 Prompt 升至 v7；旧的 case_key 错误响应和 v1 的双重 E/T 引用响应固化为最小真实失败输入回归。

当前默认多用例端到端尚未通过，证据按失败阶段区分：

- `p2-alternate-model-live-20261021-05`：没有关联来源预读。逻辑输出两次把 `dependency-create-order`（case_key）误填为来源，校验拒绝；最终规划任务 3 轮后因模型连接中断结束，未创建 Run，业务 HTTP 为0。
- `p2-alternate-model-live-20261021-06`：关系 Prompt v7 生效，模型正确选取正式创建用例为 dependency，目标用例引用它，依赖排序成立。证据阶段 v1 短引用模型响应把 `evidence_ref=E1` 和 `text_ref=T1` 同时写入一个字段，Schema 正确拒绝；没有创建 Run。
- `p2-alternate-model-live-20261021-07`：启用现有 `QA_LINKED_SOURCES=1`，文档作为固定来源进入证据，逻辑前置关系仍正确；v1 证据响应仍没有使用有效的 E/T 结构，原5轮结束前未形成计划，没有创建 Run。
- `p2-alternate-model-live-20261021-08`：隔离使用已有 `case-planning-evidence-references-v2` 单字段引用和修订版 r6。逻辑及传值绑定成功；首个 produced 回复混用完整证据 ID/E短引用且在 `select_when` 多给不支持的 `operator`，随后才查询真实工具契约。原总时限内任务以规划超时结束，未创建 Run。

四次均只调用替代模型 Agnes；全局 MySQL 默认配置保持不变。运行期间实际发出的 Provider 请求与 SDK 流均可在各目录 physical 子目录核对，不能把协议合法、阶段成功或 HTTP 200 记为业务测试成功。Step 默认认证按用户确认为已完成，但当前默认推理 entitlement 仍有 HTTP 400 证据；不重复相同探针，待账号订阅/额度状态变化后再验收默认模型。

实现兼容方面，文档/场景/来源协议的 Schema 替换必须发生在逻辑 Schema 投影之后；旧 PG seed/checkpoint 继续使用冻结 Prompt 与历史 prompt version，新任务使用 v7 组合版本。分任务证据 Prompt 将 T 原文引用说明为唯一的 `text_ref`；不能同时携带其底层文档 `evidence_ref`，Prompt 修订到 r6。没有改变模型 Provider 协议：Anthropic 仍使用 `client.messages.create`；`await_provider_io` 只等待传入的 SDK 操作并归一化网络异常；Responses 端点只由独立 `OpenAIResponsesClient` 调用。

当前工作树的 P2 规划与证据短引用回归：**91 passed、66 skipped，7.74秒**。模型适配/物理 SDK 协议回归 `test_model_clients.py`、`test_model_completion_http.py`、`test_model_request_profile.py`、`test_langchain_model_adapter.py`、`test_model_sdk_fault_http.py`、`test_model_stream_terminal_http.py`：**167 passed，129.05秒**；实际协议测试分别断言 Anthropic `/v1/messages` 和 OpenAI `/v1/responses`。因此“Anthropic 被换为 Responses”不符合当前代码或物理请求证据。

完整 P0—P5 继续进行；Agnes 多用例真实规划仍未建 Run，Step 默认模型的 entitlement 恢复后验收、规划后真实运行及业务结果、其他未闭环模块仍需继续。

### 7.136 P2 默认 Step 账号认证后多用例验收复核（2026-10-21）

用户确认 Step 账号认证已完成，因此使用新建隔离项目重启默认模型验收；本轮没有把认证状态当成推理可用的证据。验收读取 MySQL 当前默认项 `step-5-preview`，transport 为 OpenAI Chat Completions，使用默认 8,192 输出上限和 P2 当前关系/证据任务协议组合。全局模型身份前后保持一致。实际正式 API/规划 Worker 在隔离 MySQL、PostgreSQL、Redis、RustFS 中创建项目、上传 Markdown 需求、创建并启用“订单查询”目标、“订单创建”正式前置及无关候选，并向默认模型发出一次物理 HTTP 请求。

本次真实规划任务于首轮得到 HTTP 400，SDK 元数据为 `request_params_invalid`；Worker 正常退出，但任务以 `模型调用失败（http_error）` 终止，未创建计划或 Run，业务 HTTP 为 0。随后为区分认证/额度与流式字段，在不改 MySQL 配置的情况下用同一默认配置向同一 Step Plan host 发出一次最小非流式 Chat Completions 诊断（`messages`、`model`、`max_tokens=64`，无 `stream_options`）；仍为 HTTP 400，脱敏供应商响应为 `you have no active step plan subscription`。因此本次不能归因为 P2 Schema、流式 `include_usage` 或 Responses 协议；账号认证完成与 Step Plan 订阅授权是不同状态。

当前只核实默认配置 host 为 `api.stepfun.com`、base path 为 `/step_plan/v1`，没有切换端点。StepFun 官方开放平台快速开始展示 OpenAI `chat.completions.create` 和 `/v1`；Step Plan 页面描述为按月订阅服务。公开页面未证明当前账户的 Step Plan entitlement，也未给出本机 `/step_plan/v1` 配置的具体合同，所以不擅自将其改为 `/v1` 或更改凭据/供应商。参考：[StepFun OpenAPI 快速开始](https://platform.stepfun.com/)、[Step Plan](https://platform.stepfun.com/step-plan)、[Step 5 推理 Cookbook](https://platform.stepfun.com/cookbook/step-5-reasoning)。

隔离实测证据位于 `p2-default-multi-case-step-auth-current-20261021-01/`；包括真实任务/冻结快照、物理请求正文与仅含状态码的响应元数据、SDK错误类型、API/Worker日志及最终隔离存储快照，不保存请求头或密钥。新一轮专项仍为 **1 failed，33.36秒**，失败发生在规划首个模型请求，不是用例选取、排序、补全或工具执行。当前仍未完成默认 Step 的 P2 闭环。后续继续不依赖 Step 的模块；默认推理只有在订阅状态变更或供应商确认配置后再做端到端验收，不重发相同失败探针。

### 7.137 P1 设置页模型激活后运行时切换验收（2026-10-21）

依据当前代码，设置页 `ModelSettingsPlugin.vue` 通过 `/settings/models/{model_name}/activate` 激活配置；API 调用 `SettingsService.activate_model_config` 写入 MySQL；`ModelRegistry.get_default_runtime_config()` 每次从同一配置存储读取活动模型。本轮用现有构建版 App 与隔离测试存储验证这条用户操作链，没有修改模型配置生产代码。

测试预置两个仅存在于本次隔离 MySQL 表的 Chat Completions 配置。浏览器在设置页确认 `activation-before` 初始为当前模型，点击激活 `activation-after`；实际 API 返回 HTTP 200，页面 badge 更新，GET 列表显示旧项停用/新项启用。随后用正式 `ModelRuntimeService` 发起模型请求，本地 HTTP fixture 收到 `/v1/chat/completions`，请求 `model` 精确为刚启用的 `activation-after`，运行结果为 `mode=ok`。全程未触碰全局模型配置，也没有请求真实外部模型。

执行 `Agent_Server/tests/test_model_settings_activation_product.py`，最终 **1 passed，38.48秒**；测试内实际运行 `npm run build` 并通过 Vite preview 加载构建版前端；单独 `py_compile` 通过。首次两次运行的失败都在验收断言/证据序列化（响应文本的JSON引号和错误读取了不存在的顶层 `response_id`），页面激活、MySQL切换及模型请求已经实际完成；失败原件分别保留在 `p1-model-settings-runtime-switch-current-20261021-01/`、`-02/`，最终通过证据在 `p1-model-settings-runtime-switch-current-20261021-03/`。

最终浏览器记录有两次 Font Awesome 静态资源 CDN 请求（CSS 与字体）；模型请求和业务 API 全部发往本机隔离服务。此项关闭“设置页激活→运行时读取新模型”配置链路的受控产品验收；它不证明 Agnes/其他外部模型在线成功，也不代表多模块替代模型生成或用户生产环境验收完成。完整 P0—P5 仍继续进行。

### 7.138 P1 默认模板迁移与真实多模块生成质量复验（2026-10-21）

实施前查阅本地《测试数据合成与用例自动生成》和《线上流量闭环与评测集治理》课程。按其版本来源、规则覆盖、失败样本回归和人工评审要求，将本轮验收分成存储/协议合同、真实文档解析与模型生成、逐规则内容审阅三层；草稿生成或数据库写入成功不等于质量通过。

先运行 `tests/test_case_json_templates.py`、`tests/test_test_case_generation_pipeline.py` 和 `tests/test_generation_json_output_contract.py`，复现两项真实失败：实现记录的 Prompt 版本仍为 JSON v5 / document v4，而新增回归期望 JSON v6 / document v5。将生成器记录修正为与本次 Prompt 和先前 v4 真实生成回执对应的 JSON v6、document/incremental v5。随后六个生成、模板、JSON 输出、文档来源/流式和增量来源测试在 `RUN_LIVE_CASE_JSON_TESTS=1` 下 **36 passed、12 skipped**；该目录中的 MySQL 默认模板从旧系统默认值条件迁移、修订冲突拒绝、项目模板隔离及 PostgreSQL 历史记录兼容用例均实际通过。跳过项是未设置独立门控的其他外部存储/真实生成用例，不计为通过。机器结果：`p1-generation-contract-current-20261021.xml`。

再用 `agnes-2.5-flash` 在独立 MySQL 配置表及隔离 PostgreSQL、Redis、RustFS 上，运行正式 API、Markdown 解析 Worker 和两个独立生成 Worker。输入原件包含“当前用户订单隔离”“不存在订单返回明确错误且不写入”“登录用户创建自己的订单、标题必填、成功返回系统编号”两个模块。`tests/test_document_generation_alternate_live.py` **1 passed，70.15秒**：真实 SDK 请求2次，文档解析快照和两份 JSON 草稿均保存，调用终态重投没有再次请求，全局默认模型保持不变。回执与草稿位于 `p1-alternate-document-generation-quality-current-20261021-03/`。

逐项对照原文后，订单创建草稿覆盖了登录前置、成功归属/系统编号和空标题拒绝/不创建，可进入人工评审。订单查询草稿没有通过质量审阅：用“用户B无订单→空列表”代替“查询确定不存在的订单标识→明确错误”，按 ID 查询只作为可选条件；副作用检查步骤还把业务查询与数据库核对合并。该结果证明文档/模型/Worker/持久化链路可用，但不能把生成质量记作通过。逐规则审阅保存在 `p1-alternate-document-generation-quality-current-20261021-03/quality-review.json`。

当前架构依据：`DocumentGenerationScope` 冻结完整文档模块，`GenerationTaskService.execute` 每模块执行一次模型生成并保存原响应；`ModelTestCaseGenerator.parse_frozen` 检查调用终态、JSON、大小和必需 `content_json`，不检查来源规则到步骤预期的语义覆盖。此前三轮真实输出也分别出现互斥动作混写、遗漏订单创建成功路径、把登录和业务操作合并；追加步骤粒度与规则覆盖 Prompt 后，本轮又遗漏不存在订单规则。到此停止叠加 Prompt 特例，判定为 P1 生成质量架构缺口。

该架构缺口随后按§7.140实现为解析来源候选、AI建议映射、服务端结构校验和强制人工评审回执；不新增正式需求实体，不自动启用草稿。候选未超上限只表示切分未截断，不代表语义上找全所有需求。真实输出仍有待评审假设，因此该实现没有把质量状态提升为通过。

Step 账号认证按用户确认完成；认证后的最新隔离多用例验收仍在首次推理请求收到 HTTP 400，最小非流式调用正文为无有效 Step Plan 订阅。未创建计划或 Run。认证与该端点的推理订阅授权需分别记录；不重发相同失败请求，P2 默认模型端到端继续待可用的推理 entitlement。

### 7.139 P1 项目页面模板、Excel/JSON 导入和用例评审全流程复验（2026-10-21）

为补齐先前 UI 结果缺少可核对 pytest 机器回执的问题，使用全新 evidence 目录重跑 `tests/test_case_import_full_product.py`。该测试构建并加载实际项目 App，使用正式项目/用例 API、隔离 MySQL/PostgreSQL/RustFS/Redis 和真实浏览器，不调用模型。结果 **1 passed，64.92秒；0 failed，0 skipped**，机器回执在 `p1-import-full-product-current-20261021-01.xml`，浏览器、网络、页面错误、服务日志和存储快照在同名证据目录。

全流程从项目列表创建两个项目开始。浏览器读取默认模板、编辑并保存项目模板至 MySQL，载入默认内容不会静默覆盖自定义值；实际导入 `.xlsx` 行式、`.xlsx` 用例/步骤分表和 `.json` 各产生一份草稿，保存项目导入选项、下载并逐字节核对原件、预览报告、幂等提交；带错误行的 Excel 被阻止提交。随后在页面读取完整 JSON、提交评审并启用一条用例，追加新 JSON 版本，再修改模板，检查既有启用指针及两个用例版本未变化。重复用例编号产生冲突并禁用提交；第二个项目看不到第一项目的数据或来源；API 重启后模板和三份导入草稿仍可读取。

验收事实：rows、split、JSON 三种格式各提交1条；错误表格预览有3个问题且未提交；页面错误0，非计划 API 错误0，跨项目来源响应404，API 重启恢复通过。重启过渡期间的6个短暂500作为计划窗口单独保存，重启后页面和业务记录正常恢复。`full_P0_P5_complete=false`，本结果只关闭 P1 项目模板弹窗及导入/评审/启用的隔离产品流程；不代表默认模型真实生成质量、生产部署环境或 P0—P5 完成。

### 7.140 P1 文档生成覆盖映射 v2 与真实输出复核（2026-10-21）

真实输出曾把人工步骤序号（例如 `steps[].order=3`）当成数组下标，服务端因而错误报告路径无效。现在生成 Prompt 升至 `test-case-generation-document-v7`，要求模型按 `case_key + step_order + expected_field` 返回建议；服务端按 JSON 中唯一匹配的 `steps[].order` 找步骤，核验对应字段是非空字符串后，才生成实际 JSON 数组路径。无效引用仍保留在回执并带具体校验问题，不会伪造路径。

覆盖候选由已固定解析快照的文本块、原文字符范围和来源位置确定性生成，并绑定快照/文本范围哈希；每模块最多1000条，超过上限会显式标记清单不完整。未超上限只表示候选切分未截断，不证明语义上找全了全部需求。候选只是待评审的原文语句，不是已批准需求。服务端逐项检查候选 ID、原文范围是否仍吻合、AI 是否漏项/重复/返回未知 ID、步骤序号是否唯一存在及预期字段是否有效。回执使用 `document-generation-coverage-v2`，规范化后计算哈希，随生成任务结果收据持久化到 PostgreSQL。前端显示原文片段与位置、AI 的范围判断及理由、用例步骤和 `content_json` 路径、结构问题，并明确标注待人工评审。

`mapping_state=complete` 只表示每个固定候选都有一条处理建议，且模型提交的步骤引用通过结构校验；`out_of_scope`、`uncertain` 可以没有预期路径。它不表示 AI 对模块范围判断正确、语义覆盖充分、需求已批准或测试执行通过。人工核对仍是必需步骤，这个边界写入 UI 和任务警告，避免把结构收据当成质量门。

验证结果：覆盖回执/文档生成后端定向测试 **33 passed**，前端 API 与任务组件测试 **19 passed**，`npm run build` 通过。随后受控正式 API/Worker 和隔离 MySQL、PostgreSQL、Redis、RustFS 的 Markdown、流式及 DOCX/PDF 生成测试 **2 passed，264.68秒**。新目录 `p1-generation-review-controlled-steporder-current-20261021-01/`。

再以隔离配置中的 `agnes-2.5-flash` 重跑两模块真实场景，`tests/test_document_generation_alternate_live.py` **1 passed，80.87秒**，实际 SDK 请求2次，终态重投没有额外模型调用，全局默认模型保持不变。新证据在 `p1-generation-review-agnes-current-20261021-02/`。查询模块2/2候选、创建模块3/3候选均为结构映射 `complete`；完整草稿分别保存，并未启用。人工抽查发现输出仍包含项目原文未指定的测试数据/接口文档前置；查询错误预期把 `404` 作为“例如”写出，而原文没有指定错误码，不能直接转成固定断言。这次只证明映射回执能追溯来源并解析到预期路径，不能将真实用例语义质量判为通过。

本轮未修改 Anthropic/OpenAI 适配器。Anthropic 仍由 `AnthropicMessagesClient` 调用 `client.messages.create(...)`；`await_provider_io` 仅等待传入的 SDK 操作并统一网络 I/O 异常，当前代码没有 `await_provider_response`。OpenAI Responses 仍只在 `OpenAIResponsesClient` 调用 `client.responses.create(...)`。相关适配器文件本轮没有工作树 diff。

### 7.141 P0 DeepSeek 生产录制配置、响应采集与落库实测（2026-10-21）

复核此前独立 CDP 探针发现其没有调用 `configure_capture(...)`；`recorder.js` 仅在冻结配置的 `response_enabled=true` 时采集 `page_response`，所以该探针未采到响应不能证明生产链路故障。生产代码核实：`RecordingCaptureConfigService` 从 MySQL 读取项目配置，缺省回退至全局配置；`RecorderSessionService` 将带 revision 的设置快照写入 Session metadata，并在 driver launch/inject 前调用 `configure_capture`。默认策略为 `recording-dom-response-v1`、`response_enabled=true`，具有有界节点/字符/动作及等待限制。

运行 `RUN_LIVE_DEEPSEEK_RECORDING=1` 下的 `tests/test_recording_deepseek_live.py`，经正式 `/recordings` API、`CdpAttachDriver`、实际 DeepSeek 首页与隔离存储执行，**1 passed，32.27秒**。测试只切换“深度思考”按钮两次、切换“智能搜索”一次并滚动，不登录、不提交对话。实际冻结配置是 `scope_key=global`、`revision=1`、`response_enabled=true`；数据库读回6个连续事件（3 click、1 scroll、1 page_scan、1 page_response），PostgreSQL 对账 `pg_event_count=6`、`pg_step_count=6`、`pg_verified=true`、`reconciled=true`，最终回执无 missing sequence、pending、channel/transport drop 或未确认 frame。质量分析将1/2两个真实重复点击标成 duplicate candidate，但 `removed_event_sequences=[]`；页面响应关联到动作序列1–4，记录10个 fetch/XHR response outcome，未解析关联为0。截图、事件、质量报告和录制回执在 `p0-deepseek-recording-production-current-20261021-01/`，JUnit 在同名 `.xml`。

补充 `tests/test_recording_quality.py tests/test_recording_capture_policy.py tests/test_recording_draft_evidence.py` 回归：**22 passed、2 skipped，4.27秒**。两个跳过项分别要求额外的隔离 MySQL/PostgreSQL/RustFS 配置测试，以及 Chromium/MySQL/PostgreSQL/RustFS/Memgraph 草稿持久化测试；不计为通过。

再运行 `RUN_LIVE_RECORDING_PG=1 tests/test_recording_store.py::test_live_append_events_batch_retry_is_idempotent`：**1 passed，0.65秒**。测试连接经脱敏确认是本机 development PostgreSQL `127.0.0.1:5432/QA_Agent`；创建 UUID 唯一的临时录制，会话先写入10条事件，再重投原10条并附加一次批内重复，返回 `accepted=0, duplicates=11`，重新查询仍只有序号0–9的10行且 `step_count=10`，`finally` 按该临时 recording ID 清理。此项是真实本机 PG 幂等验收，不是隔离数据库测试。

结论只覆盖自动化浏览器在真实 DeepSeek 页面上的生产录制、默认响应策略、发布事件交付与 PostgreSQL 对账。`business_capture_complete` 仍为 `unknown`，因为遗漏分析只能证明已发布事件送达，不能证明页面所有业务事件都被采到；本次也不是人工手动操作，未验收 AI 知识查询/断言、跨文档/iframe/Shadow DOM、场景编辑、别名或失败定位。完整 UI 录制知识链路及 P0—P5 仍未完成。


### 7.142 P3 API Agent 双 Worker 生命周期受控复验与夹具根因修正（2026-10-21）

按本地《接口测试-通用篇》（依赖与鉴权，§2.2、§2.5）、《接口测试-实战篇》（登录后变量传递，§1.3、§1.5）和《接口测试-AI专项篇》（工具调用完整链路，§2.4）复核验收要求：需要实际证明 HTTP 结果、凭据来源与依赖顺序、工具执行结果和重投行为。

重新分析 `p3-api-agent-worker-current-20261020-01` 的失败后，确认两个失败来自验收夹具，未发现需要修改生产 API Agent 逻辑的证据：

- 同进程样本只执行 `/login`，因为受控父模型夹具在收到第一个 `tool` 消息后固定返回最终文本；API 协调器遇到子任务审批会暂停，之后仍需继续调用入口工具完成刷新与资料请求。夹具过早结束了正常协调流程。
- durable 样本在任何模型 HTTP 之前进入 `execution_owner_expired`。测试继承的 `isolated_runtime` 将 Redis `XAUTOCLAIM` idle window 设为 1 秒，而该场景同时启动两个 Agent Worker；原日志显示首个 Worker 在首次模型调用前丢失 delivery 所有权，第二 Worker 随后反复 defer。项目生产默认重领窗口是 120 秒，因此原失败不能证明生产 durable Worker 存在同一缺陷。

仅修改 `tests/test_api_agent_worker_live.py`：受控父模型根据本机目标已观察到的成功端点继续驱动 API 测试入口，三个目标均成功后才结束；多 Worker 场景显式使用生产默认 120 秒重领窗口。生产 API/Worker、模型协议、全局 MySQL 配置和业务数据均未修改。

实际运行正式 API、两个独立 Agent Worker、隔离 MySQL/PostgreSQL/Redis、本机受控模型 HTTP 和本机认证目标。两种生命周期均通过：

| 路径 | 子任务 | 真实目标请求与凭据 | 结果 |
|---|---|---|---|
| 同进程子 Worker | `/login → /auth/refresh → /profile` | 登录无凭据；刷新携旧凭据；资料请求携新凭据；三次 HTTP 200 | 报告 3/3 任务、3/3 检查通过；三个子会话完成 |
| durable 子 Worker | 同上 | 与同进程路径一致 | 报告 3/3 任务、3/3 检查通过；三个子会话完成 |

两场景各有 8 次本机模型 HTTP、4 次审批处理；生成凭据未进入公开结果；API 重启后历史可读；终态任务重投新增业务 HTTP 与模型 HTTP 均为 0。运行结果 **2 passed，119.65 秒**；JUnit 为 `p3-api-agent-worker-live-fixed-20261021-01.xml`，隔离原始证据在 `p3-api-agent-worker-live-fixed-20261021-01/`。

此项关闭固定正向认证链路的同进程/durable Worker 受控验收，不代表 Step 默认模型质量或正式用例 Run 验收。错误凭据、刷新失败、断网/超时恢复、更多依赖形态、前端 API 报告产品流程和 RustFS 报告原件仍未覆盖。Step 账号认证按用户确认记为完成；最新默认推理实测仍为 HTTP 400、无有效 Step Plan 订阅，本次没有发起 Step 请求。完整 P3 和 P0—P5 仍未完成。

### 7.143 P3 受控夹具等待 report_ready 后的复验（2026-10-21）

复核 7.142 之后的夹具改动：父模型测试夹具现在持续调用 `api-test-runner`，直到此前工具结果中的 `phase` 为 `report_ready` 才停止。此条件与 `ApiTestingModeRuntime._build_output()` 的实际终态字段一致；子任务仍只调用一次。durable 场景将 Redis reclaim idle window 设为生产默认 120 秒。没有改生产 API Agent、模型适配器、全局 MySQL 配置或数据库结构。

先运行 `tests/test_api_agent_worker_live.py` 的普通逻辑测试，结果 **1 passed、5 skipped，7.10 秒**；跳过项为独立 API/Worker/真实存储验收门控。`py_compile` 通过。随后使用正式 API、隔离 MySQL/PostgreSQL/Redis/RustFS、受控本机 Chat Completions 服务和本机认证目标，运行同进程及 durable 两个场景，结果 **2 passed，109.65 秒**。受控模型没有访问外部 AI。

两种场景都实测 `POST /login`（无凭据）→ `POST /auth/refresh`（旧凭据）→ `GET /profile`（新凭据），目标端均返回 200；报告均为 **3/3 任务、3/3 检查通过，5/5 验证规则通过**。每场景接收8次本机模型 HTTP，生成凭据未出现在公开结果；API 重启后历史可读；终态重投新增业务 HTTP 与模型 HTTP 都为0。最终 JUnit 与逐场景原始证据位于 `p3-api-agent-worker-live-fixed-20261021-02.xml` 和 `p3-api-agent-worker-live-fixed-20261021-02/`。

这次补验关闭“受控父模型必须等待报告终态，不能因一次工具结果提前结束”这一夹具验收风险，并复核固定正向认证依赖链路。它仍不代表默认 Step AI、错误凭据/刷新失败、超时和断网恢复、更多依赖结构、前端 API 报告与 RustFS 报告原件已通过。Step 账号认证按用户确认已完成；最新默认推理 HTTP 400（无有效 Step Plan 订阅）的证据未变化，本轮没有重复请求。P3 其余项及完整 P0—P5 仍在进行。

### 7.144 P5 项目录制知识页面当前工作树复验（2026-10-21）

实施前核对 `tests/test_recording_knowledge_product.py` 与录制/知识查询服务：该用例以本地 HTML fixture 为被录制页面，通过正式 `/recordings` API 和 CDP driver 采集操作，读取 PostgreSQL/Memgraph 知识投影，并由独立 API/main 与构建版前端访问项目知识页；参数分别覆盖正式 `project_id` 作用域与旧 `graph_scope_key` 兼容绑定。测试不调用模型，也不访问 DeepSeek，因此只用于验证“新录制知识能否进入当前项目并在产品页恢复读取”。

第一次重跑在 Chromium 启动前失败：项目 `.venv` 的 Playwright 1.62.0 需要 Chromium revision 1234，而浏览器自举脚本安装了 CLI Playwright 1.64.1 并清理旧 revision。失败回执保留为 `p5-recording-knowledge-current-20261021-01.xml`，不归类为产品故障。按项目虚拟环境安装器恢复 revision 1234 后，使用全新隔离证据目录重跑，最终 **2 passed，112.63秒，0 skipped**；JUnit 为 `p5-recording-knowledge-current-20261021-02.xml`。

两种作用域各记录8个事件，知识图没有缺失录制/动作节点或悬空关系；构建版知识页能看到录制和动作详情；API 重启后知识响应与 PostgreSQL 事件逐项一致。另一项目读取返回404，浏览器错误和非计划 API 错误为0；计划重启期间每个场景记录2个短暂500。证据目录为 `p5-recording-knowledge-current-20261021-02/`。本次未调用真实模型，没有默认 AI/DeepSeek 知识消费或测试断言。

当前结论：项目知识读取、隔离、产品页显示及 API 重启恢复按上述本地 fixture 范围已有当前工作树功能验收；它不能替代真实 DeepSeek 操作经正式产品持久化后再由默认 AI 引用并完成断言。场景/步骤编辑、别名、失败定位和报告仍按各自既有证据与未完成项单独验收，完整 P0—P5 继续进行。

### 7.145 P3 自动 401 刷新失败到正式报告的 Worker 验收（2026-10-21）

先按本地《接口测试-通用篇》的登录、鉴权、异常响应和依赖数据要求，以及《接口测试-AI专项篇》的工具调用完整链路要求，核对 `auth_refresh.py`、`ApiSubagentCoordinator._recover_pending_auth_refresh()`、`ApiTestingModeRuntime._execute_campaign()`、`ReportBuilder` 和现有 HTTP/Worker 测试。生产逻辑已有明确限制：仅意外 401 的只读目标允许刷新/重放；刷新任务失败后目标进入 `failed`，不再发送目标请求；若 Worker 已结算、没有未定状态，Runtime 仍构建报告并转为 `report_ready`。因此本步骤验证的是之前缺少的 Worker→报告功能闭环，没有证据要求增加生产分支。

为覆盖此前仅由协调器级测试覆盖的负向路径，新增隔离 PostgreSQL、本地 HTTP 目标和实际 `ToolRuntimeService` Worker runner 场景，并让正式 `ApiTestingModeRuntime` 生成结构化 JSON 与 Markdown 报告。正向对照实测 `/login 200 → /profile 401 → /auth/refresh 200 → /profile 200`；刷新失败场景实测 `/login 200 → /profile 401 → /auth/refresh 401`，profile 只尝试一次，检查点为 `refresh_failed`。失败场景仍进入 `report_ready`，报告为 **1 完成、2 失败、0 跳过**，明确列出 profile 和 refresh 失败以及 HTTP 401；公开输出和验证证据均不含生成凭据。两场景由受控本机模型驱动，外部 AI 请求为0。

首次尝试暴露的是验收 Worker 夹具问题：工具运行已更新 PostgreSQL 会话 `state_revision`，夹具却用新建 revision=0 的旧值覆盖，触发 `SessionWriteConflictError`。依据 `src/domain/models.py` 中 `SessionRecord` 是带乐观锁字段的 dataclass，将夹具改为读取当前会话后 `deepcopy` 并追加工具消息，保留最新 revision。没有改生产 API、协调器、数据库 schema 或配置。

最终实际命令：

```powershell
# Agent_Server 目录
$env:RUN_LIVE_API_401_AUTO_WORKER='1'
$env:QA_API_401_AUTO_WORKER_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p3-api-401-refresh-report-current-20261021-06'
..\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_api_401_refresh_automatic_live.py::test_worker_automatic_refresh_uses_same_contract_and_private_results `
  tests/test_api_401_refresh_automatic_live.py::test_worker_refresh_failure_is_reported_without_replaying_target `
  -q --tb=short --junitxml=..\docs\upgrade_2026-10-03\p3-api-401-refresh-report-current-20261021-06.xml
# 2 passed，12.46秒
```

产物为 `p3-api-401-refresh-report-current-20261021-06.xml` 和同名目录中的 `verification.json`。过程中发现原正向测试证据曾把随机夹具凭据直接写入 JSON；已修正写入时的字段脱敏，并将先前生成的临时证据文件原地脱敏、扫描确认无残留。测试文件逻辑回归 `test_api_401_refresh_automatic_live.py`、`test_api_worker_auth_refresh_live.py`、`test_api_agent_worker_live.py`、`test_api_report_credentials_http.py` 共 **21 passed、13 skipped，12.59秒**；跳过项是各自受控存储/Worker门控，不记为通过。

当前仅关闭显式自动刷新成功与失败进入报告的受控 Worker 验收。它不是默认 Step 模型驱动的完整 Agent 流程，也没有验收用户前端中的报告页/RustFS 下载。错误凭据、超时断网恢复、其他依赖拓扑仍待真实验收；已验证的刷新失败路径没有发现需要补生产代码的缺陷，完整 P3 和 P0—P5 继续进行。

### 7.146 P2 Step 认证后默认模型新项目复验（2026-10-21）

用户确认 Step 账号认证已完成后，使用全新隔离项目再次执行默认模型多用例依赖场景。测试只读当前 MySQL 默认模型配置并复制到本次隔离 MySQL；配置仍为 `step-5-preview`、`openai_chat_completions`、`api.stepfun.com/step_plan/v1`，MySQL 中存在已配置 API key；没有输出密钥，也没有改默认配置、请求 profile、token 上限或 Provider 协议。

实测 `tests/test_default_coverage_production.py::test_actual_default_model_plans_and_executes_complete_case`：**1 failed，26.93秒**。首个真实 Step 请求返回 HTTP 400；SDK create 未返回、仅有1个 HTTP attempt、没有流响应；正式计划任务失败，未生成 CasePlan、Run 或业务 HTTP。JUnit 为 `p2-default-multi-case-step-auth-current-20261021-02.xml`，脱敏调用/状态证据位于 `p2-default-multi-case-step-auth-current-20261021-02/verification.json`。该轮物理观察只记录响应状态，未读取供应商响应正文；此前单独的一次只读最小请求曾得到“无有效 Step Plan 订阅”的脱敏正文。当前应区分“账号认证按用户确认完成”与“默认推理请求仍未成功”，不能将本次失败说成凭据认证失败，也不能将其说成规划逻辑或 Agent 执行失败。

本轮后不再重复相同 Step 请求；待供应商推理授权/服务状态变化后，再以新隔离任务完成默认 AI 规划与执行验收。当前默认 Step 多用例闭环仍未通过；P1/P3 受控模型通过不能替代它，完整 P0—P5 继续未完成。

### 7.147 P3 API 报告 RustFS 原件与构建版页面闭环（2026-10-21）

实施前复核本地《接口测试-通用篇》步骤8及报告模板、《接口测试-实战篇》第7章和第9章：验收需呈现测试范围、总量/通过/失败、风险结果及可查看证据；同时检查 `ArtifactStorageService._rewrite_artifact_paths()`：先前对象存储逻辑只处理本地文件路径，API Runtime 在内存中生成的 JSON、Markdown 和任务证据仍把正文保存在 PostgreSQL 的 `__content_text`，违反“运行数据在 PostgreSQL、二进制/原件在 RustFS”的既定边界；报告摘要又只从内联正文读取，所以外置后摘要会消失。按现有对象存储服务扩展内存工件保存，不增加存储组件。API 报告投影从同一 `ToolJob.output_payload.report` 读取结构化统计，避免为了统计重新把正文塞回 PostgreSQL；通用报告仍保留既有内联兼容读取。

为 API 报告页增加明确的任务汇总表和 API 专属 KPI，解决 Markdown 摘要原件路径化后页面只显示“无报告正文”且通用 Worker KPI 显示 0 的实际产品缺口。三个 API 工件以 `rustfs://` URI 返回，PostgreSQL 元数据采用 path-only 形式；同一工件在任务输出、报告和 API 状态投影重复出现时在当前保存批次复用对象。

代码逻辑回归命令（`Agent_Server` 目录）：

```powershell
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_artifact_storage_service.py tests/test_report_service.py tests/test_api_report_credentials_http.py tests/test_performance_runtime_artifacts.py tests/test_xlsx_artifact_download.py -q --tb=short
# 31 passed，10.98秒
```

随后使用隔离数据库/对象存储、正式 API 与 ToolRuntime Worker、本机受控 API 目标和构建版 App/Chromium 执行产品验收。第一次运行确认报告、RustFS 路径和 KPI/表格均正确，但测试选择器把行标题当作普通 `cell`，断言失败；第二次修正语义角色后又确认每行只有一个数据 cell，失败的是索引写成 `nth(1)`。根据浏览器 ARIA 快照修正夹具为 `rowheader` 与唯一数据 `cell` 后，最终全流程 **1 passed，62.43秒**：报告及三项工件真实写入 RustFS，PostgreSQL 工件记录不含内联正文；浏览器能显示 3 项任务、2 完成、1 失败，摘要表显示 3/2/1；JSON 通过页面下载后与 RustFS 原始字节 SHA-256 相同；独立重启 API 后报告和下载仍一致；终态重放新增业务请求为0，浏览器错误及非计划 API 错误为0。

最终 JUnit 为 `p3-api-report-rustfs-product-current-20261021-05.xml`，逐请求与对象摘要、脱敏浏览器网络、计数及下载校验位于 `p3-api-report-rustfs-product-current-20261021-04/verification.json`，页面截图在同目录。本次没有修改数据库 schema 或 MySQL 配置，不调用外部模型。

当前结论只覆盖受控 API 报告工件外置、报告页面统计、原件下载及 API 重启恢复；它不是默认 Step Agent API 完整流程验收。错误凭据、超时/断网、更多依赖拓扑和不同 API 报告组合仍待验收。Step 账号认证按用户确认为完成；默认模型多用例请求仍有历史 HTTP 400、未生成计划或 Run 的证据，不能将认证完成说成默认推理闭环通过。完整 P0—P5 继续进行。
