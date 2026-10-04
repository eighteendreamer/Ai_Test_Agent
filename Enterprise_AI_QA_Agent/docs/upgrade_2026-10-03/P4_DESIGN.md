# P4 需求影响、追踪与增量草稿：查证与实施

研究基线：`4a02ae2`，2026-10-04。本轮先实现并验证三方证据读取；不将证据准备等同于 AI 判断、需求覆盖或测试通过。P0—P5 全部原范围保留。

当前研究基线：`443f046a`。三方输入及步骤 2 的版本治理已提交；本轮增加步骤3的内部固定提交与输入检查点，以下AI批次/候选及步骤4—7仍待实施。生产历史迁移与完整迁移流程单独验收。上传不依赖 Git；影响输入使用系统已保存的固定版本及快照，两种来源都不在分析期间重新抓取最新文件。

## 依据与修正

| 实际依据 | 确认的问题/采用做法 |
|---|---|
| `generation_pipeline.py:118`、`case_service.py:95` | 同一批次 source_refs 附给每条用例，文档级来源仅作候选线索，不自动成为需求覆盖关系。需求/验证点关联必须引用原文并经确认 |
| `api_docs_service.py:109` | 即使读取旧版本，全文读取仍会选该版本最新解析快照。影响分析必须直接读取 source_refs 保存的具体 snapshot_id，不能调用该入口来冒充当时依据 |
| `ApiDocsService.read_asset`、`DocumentParsingService.read_snapshot`、`DocumentDifferenceService.read_snapshot` | 复用对象哈希/大小、项目绑定、固定输入验证；旧文本源按明确资产读取，不再解析，不回退到当前原件或预览 |
| `case_store.py` 的两种 append_version、`planning_sources.py` 的 active 检索 | 追加版本会把整个用例变为 draft，导致旧启用版本退出检索。增量草稿接入前必须分离版本评审与用例启用状态；不能仅保留 active_version_id 就宣称旧版本仍可执行 |
| PG 用例表/版本表已有 active_version_id/latest_version | 单条 SQL 读取项目非归档用例的启用版本及最新草稿，避免分页期间并发变化漏项；完整 JSON 保留，模式不做过滤。显式固定版本可包括历史/归档版本 |
| MySQLApplicationConfigStore、现有项目解析/比较配置 | 输入数量、正文大小和读取预算采用项目配置并冻结，超限明确失败，不截断正文后宣称完成 |
| 本地课程《测试数据合成与用例自动生成》《线上流量闭环与评测集治理》《评测工程化与CICD》 | 采用来源证据、固定版本、审核入库和原失败保留原则；课程中的模型/库版本与比例不当作当前产品事实 |

## 完整闭环与实施顺序

1. **三方输入**：固定比较结果及双方解析快照、固定用例完整版本、各版本当时的源文档/资产/解析快照。缺失、损坏、跨项目、截断及非文档来源以逐引用缺口保存，不使用最新文档代替。未显式选择时分析项目当前启用版本及最新草稿；历史套件/运行版本可显式选择。没有来源的人工/导入用例仍进入分析，结论保留依据不足。
2. **版本评审修正**：在现有 PG 用例 record 内按 version_id 保存评审状态与事件，不把它写入不可变版本正文/哈希。已启用 V1 时追加/评审 V2 保持 V1 的 active 资格；明确启用待评审或已确认版本。无正文的旧提交入口固定当次所选版本；默认优先选择唯一待评审版本，没有待评审时保持当前启用版本，多个则返回歧义。复用用例锁/PG 行锁读取当前记录，追加与评审不覆盖并发新版本；启用、必要的 embedding Job 和 Outbox 同事务保存。重新启用旧版本创建新任务，旧终态任务不重用。逐版本前端动作、可选预期启用指针和请求重放与后端同步；原操作重放返回当前记录，不把指针倒退到当时版本。历史迁移先按项目预览，仅保存基于旧状态/指针的推导，不能伪造旧评审日期；保留停用/归档、版本 JSON/hash、套件、运行及失败。
3. **持久任务与 AI 候选**：复用 PG Outbox、Redis Worker、模型适配与可查询任务机制。新 impact 领域记录明确身份，不把影响记录塞进 planning 或 difference 表。提交固定输入、模型参数、Prompt/Skill、配置、环境依据和请求哈希；未知回执先查询同请求。AI 分批按实际文档结构/差异分析并保留未处理范围，引用已核验字符区间和真实用例版本。不采用标题相似度直接确认同一需求。
4. **稳定需求与评审**：保存 project_id/document_id 下稳定需求身份、不可变需求版本及验证点；AI 的新增/修改/删除/不确定匹配作为候选。评审明确接受/拒绝/重新匹配及原文依据，删除只提出停用，不删除需求、用例或结果。块 ID、章节标题与哈希不是跨版本的需求业务身份。
5. **增量草稿**：确认修改后生成同 case_id 的新版本草稿；新增需求产生新用例草稿，记录模型/Prompt/Skill/模板及差异/需求版本。只生成确认范围，拒绝无来源或不完整结果，失败模块独立恢复。停用候选独立评审。已生效重大影响以对应版本+环境记录执行适用性，不全局停用项目，也不仅凭 AI 变化推测环境已生效。
6. **追踪及执行证据**：矩阵显示具体需求版本/验证点→具体用例版本→运行结果和证据。没有通过证据的设计关联不能称为已验证；旧需求通过不代表新需求通过。尚未生效/轻微影响继续使用适用旧版本；无法确定仅待复核，无明确失效证据不整体阻断。相关运行失败创建新回归运行，保留原失败。
7. **正式页面与完整产品验收**：在现有项目、文档比较、用例评审与报告页面接入查询/分析/评审/矩阵/草稿，所有边界同步类型/API。实际 MD/DOCX/PDF 7→9、删除/新增/仅格式变化、同名不同需求、未来环境、未知回执/重启/多 Worker 等贯通生产 API、独立 Worker、真实存储及 Chromium，再核验默认外部模型。替代模型/存储测试单独标记，不能冒充实际模型或完整产品。

配置留在 MySQL，业务记录与冻结元数据留在 PostgreSQL，原件及大结果留在 RustFS。三方读取服务仅组装输入，不写数据库、不调用模型、不创建草稿或运行；后续持久任务负责冻结并推进完整闭环。

## 持久影响任务：工程设计（内部固定提交/输入及模型依据已验，批次与公开接入待实现）

以下为内部固定输入实施前的设计查证记录：读取 `difference_service.py`、`postgres_document_operation_store.py`、`document_operation_task_handler.py`、`planning_task_service.py`、`planning_service.py`、`planning_workflow.py`、`case_planning_task.py`、`document_impact.py` 和生产初始化/容器引用。当前已实施范围以文末固定输入与模型依据验收节为准；这些设计目标不能当作公开接口已经存在。

### 代码事实与约束

1. `DocumentImpactSources.prepare` 每次读取当前 MySQL 策略；默认选取当前启用/最新草稿，再读取对象，最后返回内存 envelope。它不能原样在重启后的 Worker 调用，否则配置/默认候选可能变化。需分开捕获固定元数据与读取固定内容，增加接受 seed 的读取路径；现有内部直接调用保留兼容。
2. `DocumentImpactPolicy.max_context_chars` 限制整个证据输入容量，不是模型的 Token 窗口；当前没有分批模型引擎。不能把 200 万字符直接当作一轮可发送上下文。证据准备、调用输入、输出预留、总体回合/耗时应分别计量和冻结。
3. `PostgresDocumentOperationStore.claim` 将非空 checkpoint 直接理解为 `result_saved`；`save_snapshot_checkpoint` 只接最终不可改结果。批次响应不是最终 snapshot，不能直接用这个字段承载中间阶段后仍沿用原 finish。
4. `CasePlanningWorkflow` 已有调用意图、逻辑调用ID、响应先保存、未知结果及旧 attempt 写入隔离的同类做法。复用其职责和既有 ModelRuntimeService，不复制其项目查询回合或将 impact 塞入 planning 表。
5. `planning_model_parameters` 已按允许字段保存非敏感模型参数，对端点和额外header只存hash。影响任务采用同样的参数核对；不持久化密钥/认证头。后续是否抽成共享函数以实际两个消费者为依据，不先大范围重构。
6. 生产 `main.py` 当前初始化 parsing/difference/planning，没有 impact 服务/流/公开路由。新任务还必须接生产容器、流登记、独立 CLI、任务池投影和前端消费者，内部单测通过不能证明这些已接。

### 提交和固定输入

提交请求使用创建身份、固定比较结果身份、可选明确用例版本、模型选择和可追溯环境依据。请求哈希不混入随后变化的默认配置：先按创建身份及请求哈希找原任务，再读取新任务需要的当前配置；相同请求重放不重新抓取资料。

新任务在元数据准备阶段一次性捕获：比较及双方固定解析元数据、默认选取的全部版本身份/正文hash和来源、当时用例治理快照、实际策略修订、模型非敏感参数、Prompt/Skill版本、环境依据。对默认选择使用现有单查询固定范围，不在多次分页之间改变集合。全文对象读取放 Worker，不在提交 API 调用完整 `prepare` 等待下载/大输入组装。

Worker只按seed读取不可变版本/资产/解析内容，核对项目、身份、hash和大小；原来源缺失保存逐引用缺口，不用latest补位。完整输入、缺口、批次清单保存RustFS，PG保存元数据/hash。完成这个固定输入检查点后才允许模型调用；API/Worker重启不读取新配置或新启用版本替换旧输入。

对象与PG不能跨库原子提交，沿用现有提交回查与补偿。未知提交保留对象；查证未引用后才清理。并发相同创建身份只生成一个业务任务/Outbox，可能的未引用对象由同一补偿规则处理，不以删对象掩盖提交未知。

### 批次和模型预算

先建立确定的处理清单，包含差异/文档范围、全部待分析用例版本、对应来源及hash。文档切分按已保存章节/表格/字符范围，保留边界上下文；跨节定义和引用需要读取额外固定范围。单条用例保持完整JSON/说明，不仅分析前六步或标题。不能仅按source_refs排除人工/无来源用例。

先识别变化规则候选，再与固定用例批次匹配；候选本身也是未确认信息。切分产生的额外读取、规则整合和冲突检查均纳入清单/预算；无法充分读取或超限的项明确未处理/依据不足。摘要帮助安排分析，不代替最终引用的原文。后端只核对结构、身份和引用，不用标题/关键词自动确认业务含义。

冻结证据准备容量、单调用输入限制、模型context_window、预留输出、最大批次/修订次数和总耗时。优先复用实际传输适配器已有计量；无可靠精确Token计量时明确估算边界及模型拒绝，不能把字符数包装成精确Token数。超限拆分或留下未处理项，不静默截断单条用例、不自动扩预算。初期不需要引入新的编排框架或模型SDK。

### 批次状态与恢复

逻辑记录：impact任务、固定输入对象、批次清单、每批次调用记录/响应/核验结果及汇总候选；具体物理表/DTO在实现前按迁移和读写消费者核对。MySQL只存可编辑策略，PG保存上述业务状态，RustFS保存大正文。复用Outbox/Redis及数据库租约，不再造执行通道。

每个批次的检查点：

```text
ready → invoking〔先存逻辑调用ID、输入hash〕
      → response_saved〔先存完整响应与结束原因/用量〕
      → validated〔解析和引用核验〕

调用意图存在而无响应：outcome_unknown，保留该次记录/预算与未处理项
```

保存响应失败不能直接重发模型；先核对原检查点。已存响应恢复只解析/核验；已成功批次不能覆盖或重复计算。未知响应不表示未发送，后续显式重试保存新调用身份，并在冻结预算内执行；不宣称模型调用恰好一次。配置/引擎不兼容时停止使用新参数续写原任务，关联创建新分析并保留原成功/失败范围。

任务租约、batch检查点和终态发布均核对project/task/attempt/worker/token；旧worker迟到响应不能发布。取消/租约丢失传播，不被业务异常捕获后伪装完成；中途错误保留已完成批次和原始响应。业务分析完整性与运行状态分开：进程结束不等于全部范围分析完毕，也不等于评审或测试通过。

### 引用、评审与环境

文档引用保存固定资产/解析快照、字符起止、原文片段；服务对实际切片核对。用例引用保存version_id、正文hash、JSON路径和实际值，不能引用当前版本替代。来源缺失/partial/截断说明随候选传递。字符位置合法只能证明文本存在，语义归属和规则匹配仍需要AI理由及评审。

影响模型只产生候选：新增/修改/删除/不确定规则，具体用例/验证点影响，理由、来源与证据不足，环境生效建议。不得自动创建正式需求身份/覆盖关系、停用项目或启用草稿。稳定身份需明确评审；文档级source_refs、相同标题和相同hash不是确定匹配。

确认重大变化及环境已生效后，适用性记录必须覆盖该版本在该环境的选取、最终冻结和新运行启动，不能只改列表提示。已有运行保留原依据，按明确失效事实记录后续处理及计划修订；尚未生效或不确定不全面阻断。增量只写新草稿/关联，删除仅提出停用候选；原运行失败保持不变。

### 产品和验证顺序

1. 固定元数据/输入持久化及创建身份恢复，实际MD/DOCX/PDF和隔离MySQL/PG/RustFS；原请求重放前后修改配置/文档/用例，输入不变化。
2. 批次清单、输入预算、跨节依据/长用例与未处理范围；受控模型核验不完整JSON、错误引用、输出截断、transport失败和取消。
3. 批次持久响应与进程中断、多worker、租约失效、对象/PG提交未知；记录实际模型调用次数。先响应checkpoint后崩溃恢复不得再次调用。
4. 生产API/独立CLI/Outbox/Redis/任务池与正式App分析入口；丢失提交回执/刷新/API及Worker重启、指定历史结果仍可查。
5. 实际默认模型，人工标注规则变化及用例影响基线，报告遗漏/误报/证据不足；与受控模型证据分开。
6. 需求评审→增量草稿→矩阵→环境适用性→新回归，保留旧启用版本与原失败；生产历史迁移另预演和验证。

上述最初为方案查证与细化；本轮内部固定输入实际范围见下一节。没有部署影响Worker、调用影响模型或新增公开契约，全量剩余范围仍按主方案推进。

## 固定提交和输入检查点：实施与实际验证（研究基线443f046a）

根因是`prepare`在每次恢复时重新读当前策略和当前集合，以及旧文档任务层把所有checkpoint当作最终结果。新增`capture`/`prepare_fixed`，保存精确比较metadata、用例治理快照、固定version/content_hash/全record SHA256及实际MySQL策略；提交不下载RustFS。原内部prepare复用同一内核保持兼容。不可变版本body/hash和来源另在Worker阶段读取，改变active/latest或归档不改原分析集合；项目绑定和完整record不符明确拒绝。

新增内部ImpactTaskService与PGImpactStore。影响任务/Outbox同事务；独立input_checkpoint引用RustFS envelope/hash/size，不把输入当最终分析。共同数据库任务/租约职责抽入PostgresDocumentTaskStore，原PostgresDocumentOperationStore子类保留单快照发布、旧表/DTO/解析与比较日志。没有新增任务框架、模型SDK或依赖。影响仅新增一张自身任务表，未生产初始化；后续批次/候选发布再按实际契约扩展。

输入保存先落对象，再保存检查点；未知PG提交保留对象和错误，恢复读原检查点，不能把回执丢失当回滚。当前没有为未知未引用对象增加运维清理入口，仍需后续补偿/核对。已保存输入只下载并核验，不重新prepare。新Store/Service读取实际PG演示响应丢失后的恢复，旧attempt被租约拒绝，原deadline不重置；这个专项不是独立影响进程重启验收。

7项新增真实存储测试包括MD/DOCX/PDF固定范围与配置变化、文档V3和用例后续草稿/归档后仍读原依据；8次并发同请求只1任务/Outbox；关闭派发后重放不重新捕获；检查点成功回执丢失、新Store恢复/旧worker拒绝/禁止改写；Outbox失败回滚、固定record篡改拒绝；错误项目、对象损坏、输入拒绝不标AI成功。输入仍running/input_saved；测试结束的终态失败不重领且保留原输入。实际大文件/来源证据均新目录保存，模型0调用。

首轮6通过/1失败为SQLAlchemy包装CheckViolation成IntegrityError，调整异常断言没有改产品；后续全部输入专项通过。扩大回归60通过/2失败分别为旧解析1秒无心跳执行、旧真实比较0.6秒租约时序。原基线两项2通过6.42秒，当前两项2通过6.58秒；为验证职责将恢复场景lease=30再显式SQL过期、心跳场景lease=3且真实等待3.2秒，仍需至少2次heartbeat并跨完整租约。没有放宽产品租约、取消旧worker保护或删断言。最终相关5模块62通过/0跳过78.97秒；新增分页查询后7项复验通过17.22秒。编译通过；公开类型未改不重复前端构建。证据`p4-fixed-input-*`，失败XML不覆盖。

下一步固定模型非敏感参数、Prompt/Skill、环境、实际调用预算和批次清单/响应检查点，再接AI引用核验/候选与生产API/独立Worker/任务池/正式App。此处为443f046a阶段的历史状态，当时input seed尚无模型参数；当前模型依据实现见文末，不宣称AI分析可用；稳定需求/评审/矩阵/增量草稿/环境适用性仍待完成。

## 三方读取的验收

用真实文件创建 V1/V2/V3；先保存 V1 的用例来源与旧快照，然后重解析 V1/修改当前文档。必须仍读当时快照和资产。覆盖启用/最新草稿同时存在、跨模式、历史版本显式选择、人工来源、无来源版本身份、部分解析、原上下文截断、错误项目/哈希、读取失败及预算。超限拒绝且不返回截断输入。记录实际 MySQL 配置、PG 用例/文档/快照/比较和 RustFS 字节；不证明尚未接入的 AI/评审/追踪/增量草稿可用。

实际结果：内部专项7通过（15.48秒），扩大现有回归94通过、2项默认外部模型跳过（64.04秒）。`p4-impact-input-final/` 保留MD/DOCX/PDF原件及完整输入、实际图片DOCX部分解析和既有生命周期缺口；没有接入P4生产初始化/API/Worker，也没有调用P4模型、创建需求或增量草稿。后续步骤继续按完整闭环实施。

## 版本评审修正的验收要求与实际范围

| 真实触发/已有依据 | 必须证明 |
|---|---|
| `p4-impact-input-final/{md,docx,pdf}/existing-lifecycle-gap.json` 的 active V1→V2 草稿 | V1 仍在 active 候选中；规划完整读取 V1，按 V1 创建运行，旧 JSON/hash 与冻结套件保持一致 |
| V2 提交评审之后再追加 V3 | 明确启用 V2，V3 草稿不能启用；旧默认入口不得选择 V3；多个待评审版本不能静默猜测 |
| 同时追加、提交评审、启用及归档 | 当前最新版本、评审事件与启用指针不被旧副本覆盖；归档不能被迟到操作恢复为 active |
| 启用提交成功但响应丢失，之后已启用其他版本 | 重放原操作不回退；同一操作身份改变参数明确拒绝；前端重载后显示当前状态 |
| PG embedding/outbox 写入失败、重新启用旧已确认版本 | 失败回滚状态与事件；重新启用使用新 Job/Outbox，原完成任务保持终态 |
| 旧记录迁移预览及执行 | 预览不写数据；历史推导有标记；正文/hash/套件/运行/原失败保留，disabled/archived 不变，重复迁移无新变化 |
| 正式项目页/用例弹窗/生产 API/真实隔离 PG | 页面能选择具体版本、查看评审状态并完成上述操作；组件替代存储测试和真实完整流程分开记录 |

已同步后端 schema、路由、CaseService、内存/PG Store，以及前端类型/API、用例弹窗和 ProjectsView。评审元数据保存在现有 PG case record，未新增表/依赖。操作身份按同一用例及动作检查，跨版本改参数也拒绝；原请求返回当前记录，不回退。用例弹窗保存原请求身份及预期启用指针，重开只读核对已保存事件；已完成不重发。评审/启用统一到该弹窗，避免旧列表动作只看整个用例状态或默认选旧指针。当前版本的状态、启用标记及评审事件可查。

最终后端 **106 通过、2 个默认外部模型跳过（34.62 秒）**；新版本治理专项含 5 项内存、5 项真实隔离 MySQL/PG/RustFS、1 项真实 PG Job/Outbox 回滚/旧终态保留。实际 MD/DOCX/PDF 来源专项复验了 V2 草稿期间 V1 仍在 active 候选，写入新 lifecycle-retained.json，原 existing-lifecycle-gap.json 保留。固定计划在草稿追加后仍创建原版本运行。前端相关 **37 通过**，最终 typecheck/构建通过（Vite 6.51 秒）。

正式 App/生产 uvicorn/实际 Chromium/隔离 MySQL、PG、RustFS、Redis 完整场景 **1 通过（44.45 秒）**：实际 JSON→V1 启用→页面编辑 V2→V1 候选与固定套件/运行→页面评审 V2→追加 V3并拒绝其未评审启用→V2 启用回执丢失→另一操作者启用 V3→刷新只读恢复原请求→API 进程终止/新进程读取→原请求重放仍显示 V3，Job 数保持 3，V1 原文/hash 和运行引用保留；浏览器 JS 错误为空，模型调用 0。这是版本治理验收，未执行该保留期限业务用例、运行 embedding 模型或 P4 影响模型。

失败 XML/工件保留：旧断言/内部接口未同步；新专项错误匹配中文而原实现返回 archived；产品夹具误向旧 run 接口传 creation_key、未等待实际提交完成便刷新、把 Windows 主动终止码 1 当正常退出失败，以及未登记 embedding 流却预期 3 个 Job。均按实际 schema、请求处理事件、进程终态与生产初始化配置修正夹具，没有给产品增加特判。最终证据为 p4-version-reviews-regression-final、frontend-final、build-final、product-registered。

历史迁移的按项目内部预览/执行、正文/hash 不变和停用/归档保持已在隔离环境验证；没有增加公开迁移入口，没有迁移生产历史数据，没有完整验收旧套件/原失败结果的迁移流程。后续要补运维入口/投影重建与完整迁移证据。默认模型、持久影响任务、需求评审、矩阵、增量草稿及 P0—P5 其他剩余项均保持未完成。

## 模型、Prompt、Skill及环境声明冻结（研究基线f5111e9e）

读取实际ModelRuntimeService、MySQLModelConfigStore/ModelRegistry、planning_model_parameters/规划工作流、SkillRegistry/SkillRuntimeService及项目schema；继续核对本地合成数据/评测/Agent协议课程。发现两个边界：runtime从planning schema导入通用模型快照职责倒挂；项目没有正式部署版本或能证明生效的环境实体。采用已有非敏感允许清单，不新建模型SDK；函数移到model_config，planning旧名作为兼容导入。环境只冻结用户声明，部署状态unknown。

新增DocumentImpactAnalysisContexts及独立analysis schema。MySQL的document.impact_analysis_settings保存模型选择、可编辑分析指导和输入字节上限；提交冻结修订、实际模型参数、完整Prompt/Skill块及hash、项目信息指纹。PG seed保存小型固定依据，原件/完整输入继续RustFS。没有修改旧DocumentImpactInput及旧策略结构；历史三份已提交MD/DOCX/PDF对象以新schema重算canonical/object hash均不变。旧seed没有analysis_context保持None，不能默认接当前模型。

SkillRuntimeService.capture_prompt_blocks复用原全文组装，捕获effective block和完整SHA-256，缺失/空文件不降级成元数据；原build_prompt_blocks行为保留。产品新增analyze-document-impact Skill，只提出候选，不执行或修改正式记录。新创建必须注入模型依据服务，读取/同请求恢复仍可无该服务；新增可选model/environment/release字段未填写时保持旧请求hash，有值时参与幂等身份。

固定输入检查点后才可prepare_analysis_request，完整旧/新/当时来源与12步JSON均进入请求，不重新读取配置/Skill。max_input_bytes计canonical Prompt/messages UTF-8内容，不是Token计量，不涵盖所有provider封装；超限拒绝、不截断。窗口/输出预算已冻结并拒绝输出>=窗口，但实际Tokenizer/估算、批次/跨节引用清单、总体预算和持久调用响应仍待实现。该构造函数不发送模型、不把输入标为分析完成。

实测首轮12通过47.37秒；扩大首次75通过1失败91.20秒，失败仅在独立规划CLI测试finally：production planning runtime初始化parse表，旧夹具没登记/清理。按启动前to_regclass验证新增表归属，按快照→任务→文档历史顺序清理，不CASCADE，不改产品初始化。首次留下的9张PG与1张MySQL隔离表核对无外部FK后清理并再次确认不存在；清理元数据首写因RowMapping不能JSON序列化失败，清理已经提交，随后只读复核后保存记录。CLI历史文件首次被原夹具覆盖，实际新证据已另存并恢复HEAD原字节；后续通过QA_PLANNING_TASK_EVIDENCE_DIR写新目录。

最终相关9模块76通过0跳过89.95秒。新专项实际MD/DOCX/PDF、MySQL模型/配置、PG任务/恢复、RustFS输入；每种格式实际HTTP一次发送固定Prompt/完整证据，改输出参数时HTTP增加0。修改指导/模型/项目并移除Skill后新Store恢复同依据。旧hash/seed回放、不伪造模型、坏Prompt hash、缺模型/Skill、未注入、输出>=窗口及1字节预算拒绝均验。原规划独立CLI实际进程中断/恢复和终态重投继续通过；这不证明独立影响Worker进程已实现，也不是默认模型影响判断。

公开契约、生产容器/流/Worker/前端未接，无新依赖或生产迁移。输入任务仍running/input_saved，候选/引用核验、需求/评审/矩阵/增量与P0—P5其余目标保持未完成。下一步实现可追溯批次清单、调用预算、持久意图/响应与未知结果恢复，然后再接候选及真实默认模型。

## 固定范围清单、来源读取和引用位置核验（2026-10-04，研究基线918cf08a）

查实际document_parsing的半开Python字符位置、差异raw/normalized/section/table、完整impact输入与来源、PG共享租约及规划检查点；查本地评测工程化/Agent协议课程。锁文件与实际安装均有tiktoken 0.14.0（langchain-openai依赖），其本地model.py明确未知模型可能无映射，且前缀映射不能证明真实模型存在；因此本轮没有将任意模型字符串套为精确Token计量。课程读取首用未安装bs4失败，改标准库HTMLParser成功，不添加依赖。

新增独立批次schema/DocumentImpactBatches、MySQL配置document.impact_batch_settings，提交冻结可选batch_settings；旧seed缺失保持None，DocumentImpactInput/旧策略未改，旧三份MD/DOCX/PDF输入canonical hash实际复验一致。策略控制段长、总分段数、边界上下文与读取字节上限，不是模型Token预算。

清单枚举全部来源角色/资产/快照/原件与文本hash、章节/警告、用例完整版本hash、四类差异与来源缺口。相同固定快照合并但角色保留；按已有章节起点/块终点划分，超大块分段留split标记；分隔与末尾不遗漏，核心范围无重叠。块范围用bisect及章节活动范围查找，避免每段扫描所有块；清单校验与固定输入重建相等。空来源保留空段，不把它说成读取或分析成功。

固定读取保留原字符位置/来源/相关章节/表格，支持跨节任意合法范围；用例读取保留全部JSON/说明/来源。超过读取字节或分段数量拒绝，不截断；大表格/用例仍可能需要新任务调策略。引用对固定原文hash+切片或用例版本hash+字面路径和值核对；键中点号、数组索引、false/0/null区分，负索引、未知版本、引用篡改拒绝。位置一致不是语义成立，AI和评审另验。

RustFS保存manifest正文；PG新增独立impact_manifests表（task FK、不可改对象检查点），不用向旧任务表加可变列。当前attempt/worker/token/租约以及fixed input hash核验后写；task阶段analyzing表示清单就绪而非模型成功。有清单后claim维持阶段，原deadline不变；未知PG响应异常传播保留对象，新Store/Service读取原对象，不重抓配置、不重新上传。当前无模型请求批次/响应状态机、独立影响Worker或公开入口，仍是内部范围准备。

真实功能专项首轮15通过/3失败64.27秒；失败都是新夹具缺TestCaseVersionCreateRequest必填model_key/prompt_version/skill_versions。按已读schema补齐原来源，无产品放宽；第二轮18通过65.91秒。新增清单来源缺口后最终10模块82通过0跳过108.09秒。测试实际创建/上传/解析/比较MD/DOCX/PDF；长MD23986字符，两来源共82段，逐段正文重建相等及Final跨节读取通过。MySQL配置、PG固定seed/清单/丢失回执后恢复/5路同检查点写入/旧attempt拒绝/終态保存、RustFS对象哈希/损坏拒绝均通过。

命令（Agent_Server，stdout/stderr合并保存）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-manifest-final')
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-manifest-sources-final')
$env:QA_PLANNING_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-manifest-planning-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_batches.py tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_document_impact_sources.py tests/test_case_planning_tasks.py tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_skill_registry_reload.py tests/test_skill_management_boundaries.py tests/test_test_case_generation_pipeline.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-manifest-final.xml
```

本次输出重定向到p4-manifest-final.log；实际汇总108.09秒。8份源码字节编译通过。原模型HTTP专项3次受控请求仍验，新的清单专项模型调用0；独立规划CLI在最终回归内真实进程中断/重启通过，但清单恢复只是新Store/Service而非独立影响进程。前端/公开契约未改，未重复构建/浏览器，没有默认影响模型或生产历史迁移。新增PG表仅隔离验收；生产初始化/运维迁移仍须接入。

方案/原附件/Codex镜像同步；证据first/verified/final保留，未覆盖历史原件/失败。两次文档patch因段落匹配失败未写入，读取核对后采用精确替换成功。新增测试因现有ignore显式加入；用户.gitignore保持原字节，不提交、不推送、不建PR。P4模型批次/预算/持久响应/候选、需求评审/矩阵/增量及P0—P5其余范围继续。

## 单元模型调用与持久响应〔2026-10-04，基线3d184d5d，内部已验〕

范围清单之后新增DocumentImpactCallService与组合式PostgresDocumentImpactCalls，按固定segment/difference/case准备一次明确单元的请求。它是自动协调器的基础：当前调用方须显式给定单元和固定读取，尚不自动分析全部范围或发布候选。完整用例JSON/说明与来源保留；续读保留全部原消息和完整响应，再追加固定范围。固定输入/清单哈希先核验，超容量拒绝，不删历史或替换旧依据。

独立MySQL配置document.impact_invocation_settings冻结到新任务seed：逻辑调用额度、累计预留估算量、单调用超时、响应字节限制、内容计量方式及封装预留。历史seed保持invocation_snapshot=None，原Input/策略与历史canonical hash不改。默认UTF-8字节估算；显式tiktoken_content只证明选定编码的内容计数，总输入仍estimate，不能证明提供商模型映射/封装或计费。预留输出/封装与窗口核对；每个ready记录占预算，不退款；usage作为供应商回执独立保留。

冻结所选模型实际运行参数：OpenAI原生只openai，LangChain OpenAI增加langchain-openai/core，Anthropic/Google只各自SDK；无关SDK变化不拒绝。复用ModelRuntimeService可选expected_runtime_parameters，既有调用不传时行为保持。调用服务预检和最终dispatch都核对模型、适配器、SDK与超时；已保存响应读取不需要当前模型，原任务deadline不重置。

PG新增独立impact_calls表，task_id关联固定manifest，唯一(task_id,call_key)，记录完整请求/响应的RustFS引用/hash/大小和预算。准备及写入核对固定seed、项目、worker/attempt/token/活租约；任务行锁内预留预算。ready→invoking先提交意图，只有赢家可发送；同初始单元不允许换key悄悄重发。response_saved仅表示回执已持久化，不是validated。意图存在而无响应，新租约标outcome_unknown；超大响应标response_unavailable。显式重试使用新身份、关联旧未知调用、原固定请求与剩余原预算。

PG回执或对象写入不确定传播ImpactCallCheckpointUnavailable，不能赋予发送权。确认拒绝/竞争丢失的请求对象仅在实际PG无引用查询后清理；未知提交保留对象。响应先保存再解析：供应商失败诊断可能含凭据，省略原错误body/message并保存允许元数据/原因；成功SDK原响应保留。截断、空正文、意外工具调用均分类，不执行工具，也不作为有效候选。Worker仍须维持任务租约；该组件没有独立心跳/生产impact Worker。

实测新专项19项，最终11模块101通过0跳过187.90秒，实际MD/DOCX/PDF、MySQL/PG/RustFS和受控本地HTTP。覆盖6组文件×适配器、6协程竞争、完整续读、真实HTTP取消、PG实际提交后丢回执、响应对象失败、预算/历史兼容及失败回执。HTTP500沿用SDK重试，1逻辑意图实际3次HTTP，transport_attempts仍not_observed，生产物理发送追踪归P0。新Store恢复不是独立impact进程恢复；受控固定响应不是默认AI业务影响判断。

下一步按固定清单实现自动协调/跨节读取请求协议、结构化候选与引用核验、覆盖记录与汇总结果；再接生产初始化/API/独立Worker/Outbox/前端及实际默认模型。需求身份/评审/矩阵/增量和环境证据仍待实施。上传无Git依赖及执行模板只生成的约定不变；没有生产迁移或新依赖。

## 结构化单元响应与独立核验〔2026-10-04，基线232e6ca6〕

本步细节见[P4_RESPONSES_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P4_RESPONSES_DESIGN.md)。新增typed read/finish协议、局部规则/用例影响候选与实际已提供内容的引用事实核验。新提交冻结Schema/指令/hash，准备调用不读当前Schema；旧请求/hash不改，旧seed不补造协议。独立PG impact_response_checks与RustFS核验对象不可改关联原调用/响应/清单，原call保持response_saved。已存核验恢复只读；来源缺口/警告后端带入，deployment unknown、semantic pending_review不根据模型建议改变。

当前仅有单元候选/核验组件；自动补读循环、全范围覆盖/候选汇总、默认模型、独立影响进程及正式产品未接。首轮31项真实功能通过后，协议冻结/旧hash/未知对象写入等进入最终相关回归；验证和失败详见实施记录与PLAN第六节。
