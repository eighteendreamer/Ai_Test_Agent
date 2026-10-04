# P3.4 文档确定性差异：实际依据与实施边界

## 文档详情提交入口实施前复核（基线6339f7f）

实际比较请求只包含creation_key和两侧固定snapshot_id；服务端已完成项目/文档、输入、幂等、MySQL策略冻结和不可调度拒绝。前端不新增版本合并规则或配置修订请求字段。文档详情复用已有版本列表，默认推荐当前原件和紧邻前一原件；无解析结果显式指出缺口，不自动提交。允许用户选择其他历史或同一原件的不同解析快照。两个来源选择器共用组件，各自按版本分页，核对project/document/version并丢弃过期响应；partial可选且展示警告，unsupported不作为可用输入。版本元数据中的旧项目绑定不是解析快照的授权依据，仍以当前正式project_id的服务端查询为准。

提交表单保留业务选取、分页和sessionStorage原请求恢复；轮询/结果/失败仍由已验收DocumentDifferenceTask承担，不复制解析页面或新建通用任务平台。子组件新增可选原请求校验与已验证任务事件，历史路由不带请求仍兼容。提交前保存原creation_key/两侧ID；响应未知、页面重新进入、当前版本/策略改变均先按原请求查询，原身份重试不重新取选框值。缓存终态不算服务端证据。确认过的终态才允许新请求；历史任务选择不改写未确认请求，按原请求查询可重新跟踪。浏览器存储失败提示保留编号/任务池找回，不吞错。

MySQL比较设置沿用现有策略DTO所有11项预算，独立设置组件只改当前项目，晚返回/项目切换不能覆盖新上下文。设置读取失败不妨碍历史列表、固定任务/结果读取；新提交仍使用服务端实际策略，任务/结果显示真实固定修订。关闭派发后的新提交显示503并保持原请求，不冒充开始执行。新增入口接在ApiDocsPlugin现有解析组件后；不修改生成模板、用例执行、Git或旧解析协议。

验收先跑组件实际失败/晚响应/缓存回入/双方固定引用/分页/partial与unsupported/只读项目/配置故障，再用完整App/真实API/MySQL/PG/RustFS/Redis/独立CLI与真实DOCX提交。浏览器丢弃已提交POST回执后重新打开详情，改变当前选择和配置并恢复原身份；确认只一个任务/Outbox，Worker完成固定输入，旧历史/任务池及JSON下载一致。原上传/解析/历史路由回归并记录真实失败；P3.4之外完整目标仍继续。

修订日期：2026-10-04，研究基线：`44f6044`。此文细化方案 §3.4，不替代完整 P0—P5。

## 依据与决定

| 实际依据 | 采用做法 |
|---|---|
| `application/documents/parsing_service.py:read_snapshot/_read_snapshot_result` 已核对项目、文档、结果对象大小/哈希和固定解析依据 | 比较使用用户选定的两个固定解析快照；服务端读取后再交给计算进程，不在比较时重解析或改用最新快照 |
| `schemas/document_parsing.py` 的块保存抽取文本字符区间，Markdown 保存源行，DOCX 保存正文/表格路径，PDF 保存页码 | 差异字符位置属于抽取文本；附带已有源位置，PDF 不承诺原件字符坐标，DOCX 不承诺页码 |
| `runtime/limited_parser_process.py` 已有线程桥接、固定内部进程入口、超时/CPU/RSS/输出限制和取消回收 | 比较适配器复用进程管理；不能在异步 API 中直接运行 SequenceMatcher，也不复制第二套进程监控 |
| [Python difflib 官方说明](https://docs.python.org/3.11/library/difflib.html#difflib.SequenceMatcher)：最坏二次复杂度；autojunk 对频繁重复项采用启发式 | 行和字符比较明确关闭 autojunk，先去除完全一致的前后段；输入/输出/行数/变更数有界，超限可见失败；较大替换块不做昂贵字符细化并明确提示，完整行差异仍保留 |
| `项目借鉴/ai_test_agent/skills/ai_case_generator.py:436` 的 analyze_document_diff 将两版分别截取前 6000 字符后交模型分类 | 只借鉴增量分类的后续用途；确定性比较不截取前缀、不由 AI 生成原文差异，不用模型判断替代证据 |
| 原件/版本已经独立存 RustFS/PostgreSQL | 比较无 Git 前提；原件字节哈希是否相同与抽取文本是否相同分别表达 |

## 计算契约

1. 输入包含同一正式项目、同一文档的旧/新解析快照及完整结果。固定原件版本、snapshot ID、结果哈希、原件哈希、解析器/依赖/选项及实际比较策略。顺序由用户选择，允许同版本的两个不同解析快照。
2. `raw` 是未改写的**抽取文本**，逐行比较，保留换行和尾空格；原件一直可以下载。Markdown 原件编码/BOM 不由文本差异证明，须看原件哈希。
3. `normalized` 首版只统一 CRLF/CR 为 LF；不裁剪空格、不折叠缩进、不改数字/标点/大小写/Unicode。归一字符边界映射回原抽取文本。不能将归一后无变化解释为原件字节相同或业务必然无影响。
4. 每条增加/删除/替换记录两侧原抽取文本区间及原文引用，记录相交块的来源位置。增加或删除一侧是空区间，不伪造该侧发生变化的来源。字符细化保存独立区间；超过细化预算明确记录 omitted。
5. 章节比较标题、层级与父标题路径；表格比较完整单元格内容和结构，保留具体解析对象/源位置。解析器的 ordinal ID 只在本快照内引用，不能当跨版本稳定需求身份。重复章节或移动先展示确定性变化，稳定需求匹配在 P4 评审。
6. 输入解析为 partial 时完整保留警告；unsupported 不产生“无差异成功”。解析器、选项或依赖变化明确提示“解析依据变化”，不直接将这些差异判作需求变化。
7. 抽取行定义复用 Markdown 解析器的 splitlines(keepends=True)，原始换行字符始终保留；章节/表格来自原抽取结构，与 normalized 文本视图分别表达。比较记录不修改原件、解析快照、用例和运行。仅有确定性差异内核不算比较产品交付，更不算 AI 影响分析已实现。

## 后续持久任务与产品接入（须继续核对后实施）

- 可编辑比较策略在 MySQL；固定请求、任务、租约/检查点和结果身份在 PostgreSQL；完整结果及受限失败轨迹在 RustFS。
- 复用现有 Outbox、Redis、任务查询、租约 fencing 与独立 Worker 运行方式。解析任务 DTO/历史含义保持清楚，不能把比较结果塞进解析快照或伪造解析任务。先研究共同生命周期能否抽取，避免复制第二套任务平台。
- 提交核对两个快照同属当前项目文档，并固定输入和策略；结果读取核对对象哈希和输入身份。恢复保留原请求和固定输入；已完成任务不能重领。归档项目可读历史但不能新建任务。
- 文档详情选择旧/新版本及明确解析快照，提供原文/归一视图、章节/表格及来源、警告、任务查询与完整 JSON 下载。响应丢失按原请求回查；不能悄悄换新的默认快照。
- 独立上传入口照常工作；Git 为后续只读来源。解析成功后的“默认相邻比较”属于后续产品策略，只有选定双方可用快照并提交任务后才有比较状态；不因上传成功显示比较完成。
- P4 再基于已保存差异、原用例编写来源、现有用例及环境生效证据提出影响/需求匹配候选，评审后增量草稿。删除只提停用候选，保留历史与失败结果。

## 分步验证

先用真实 Markdown（BOM/CRLF/尾空格/长重复内容）、DOCX 表格 7→9 天、分页 PDF 7→9 天验证内核、源位置与真实受限进程；检查归一换行但不抹缩进、重复/移动/删除/空侧、输入不可变及资源/取消回收。

随后单独验证 MySQL 固定配置、PG/RustFS/Redis 任务恢复与项目隔离，再用生产 API、独立 CLI 和实际完整产品页面验证提交、查询、选择历史、重启与结果下载。真实存储和浏览器未测前，状态只写“内核专项通过/任务和页面待实现”。

## 此前内核实施与验证状态（交付 b701b9d）

已新增比较 DTO、确定性内核、受限进程适配器和固定内部 Worker；未新增公开比较路由或第二套任务平台。raw/normalized、字符映射、章节/表格、输入身份及实际比较策略随结果返回。两侧已验证快照由现有 `DocumentParsingService.read_snapshot` 获取，计算内核本身不读取数据库或替代对象哈希核验。

最终 `p3-document-difference-final-delivery.xml` **57通过、0跳过，31.07秒**：包含真实 Markdown/DOCX/PDF V1/V2、真实解析及比较子进程、完整差异重建新文本、真实 MySQL 配置和 PostgreSQL/RustFS 历史输入、项目隔离、原件/快照不可变、CRLF 映射且不抹缩进、6000字之后的变化、2万行相同重复内容、无文本PDF拒绝、资源限制和取消回收，以及既有解析器/进程回归。

原失败保留：首轮 **21通过、1失败** 为 DOCX 夹具将列号写错，按实际 `[正文元素,行,列]` 修正；另复现 Unicode 分行与既有解析器不一致（`p3-document-difference-line-before.xml` **1失败**），内核改为同样的 `splitlines(keepends=True)`，Unicode字符保持原样。最终回归已包含修复样本。其他中间结果仅为历史，不与最终57项相加。

此交付证明比较计算与真实固定输入，**不代表比较持久任务/Redis投递、API/CLI、前端页面、AI影响或需求追踪已经完成**。本轮未运行前端构建/浏览器，因为没有修改前端或公开契约；现有文档页面仍没有比较入口。未部署Docling、未执行生产历史迁移，完整目标保持未完成。下一步先研究共同持久任务生命周期，再接配置/结果持久化及产品入口。

## 持久任务实施前的代码结论（基线 b701b9d）

`PostgresDocumentParseStore` 的项目/文档锁、请求哈希、任务+Outbox事务、数据库截止时间、attempt/lease fencing、结果检查点及终态发布可以共用；其原件验证、解析器验证及公开DTO属于解析业务，不能复用于比较身份。采用内部 `PostgresDocumentOperationStore` 抽取这些共同SQL行为，原解析Store保留自己的表名、DTO、phase和日志事件，仅保留原件/解析器验证钩子。比较Store使用独立diff表、DTO与comparing phase，在同一事务核对两侧解析快照记录；不是创建伪解析任务。当前解析表与旧记录不迁移、不改字段语义。

比较任务输入仅保存两侧完整快照**元数据**、明确旧/新次序、MySQL比较策略修订、算法与Python版本；提交不下载大对象、不再次解析、不冻结临时内存结果。`version_id`明确表示新侧原件版本，用于任务筛选；旧侧版本及两侧snapshot ID始终保留在seed，不能将单列当作全部比较依据。

Worker执行时通过现有固定快照读取核对RustFS对象，再核对返回元数据仍等于seed；随后调用已验收受限比较进程。截止时间覆盖输入读取与计算，恢复不重置；进程结果先存RustFS，再保存PG检查点，最后发布不可变比较结果。已保存检查点恢复只核对已存结果后完成，不能再次比较；数据库提交回执未知保留对象与请求身份。配置变化只影响新请求，终态不可重领。只读历史不需要读取当前MySQL比较设置。

本步骤先实现并实际验收MySQL设置、PG共用生命周期/独立比较Store、RustFS结果与服务；Worker/CLI、公开API、任务池及前端接入继续单独同步和验收，不用服务直接执行替代那些层的真实结果。重构后必须复验既有解析租约、恢复、默认快照与独立解析CLI，不能只跑新增比较测试。

实际检查 `DocumentParseTaskHandler` 和 `RedisTaskWorker` 后，领取/心跳/取消/终态重投也能复用：抽取内部 `DocumentOperationTaskHandler`，两个业务各自保留明确Command DTO和task_type、日志前缀。先用真实Outbox→Redis→现有Worker类→比较受限进程验证，并记录该范围；比较专属独立CLI/生产API/完整页面仍另验。算法实现版本沿用解析器的有界字符串做法，让旧版本任务可以被读取并明确终止，不能因Literal版本枚举改变而反复领取失败；schema_version仍固定。

## 持久服务及 Worker 实际结果（交付 22be347）

先核对实际解析Store/Handler、MySQL配置和RedisWorker，再抽取 `PostgresDocumentOperationStore` 与 `DocumentOperationTaskHandler` 共用生命周期；解析业务保留旧表、DTO、phase、校验与日志身份。比较使用独立 `_diff_tasks/_diff_snapshots`，两侧解析快照外键和固定元数据、MySQL策略修订、算法/Python身份共同保存；version_id仅表示新侧版本，完整两侧始终可查。

已接通比较设置服务、持久Store、比较服务及typed Handler。任务与Outbox同事务，输入提交不下载正文；执行读取已固定且哈希验证的两侧结果，真实受限进程计算，RustFS结果→PG检查点→不可变结果发布。恢复读已保存结果，不再比较或加载当前配置；原截止时间保留，失败诊断和租约fencing可查。历史查询不依赖当前MySQL比较设置。

实际扩大回归 `p3-document-difference-durable-delivery.xml` **84通过、0跳过，68.57秒**，包含16项持久比较/Worker和既有解析/内核专项：真实三格式V1/V2、MySQL策略70→80且任务仍用70、PG/RustFS结果、请求重放/并发、Outbox失败整事务回滚、源快照外键、检查点恢复/旧attempt拒绝、终态不重领、错误项目/归档、输入和结果损坏、输入读取超时、运行版本改变、真实Outbox/Redis/Worker续租重投、心跳失效取消、文档改绑阻止发布。既有解析默认快照/恢复及独立CLI也实际复验。

首轮存储测试 **7通过、1失败** 是夹具错期待底层psycopg异常，项目实际SQLAlchemy包装IntegrityError；核对既有事务失败测试后修正，确认底层确为外键拒绝。首轮Worker **2通过、1失败** 是夹具未按实际 `update_document(doc_id, **changes)` 调用，修正为关键字。原失败XML保留；产品未为夹具错误加兼容特判。诊断日志随后补phase/input_snapshot_id/内部原因，相关3项实际复验通过，Redis次数/失败完整trace另存工件。历史重跑数不累加为覆盖数。

此前22be347交付仍未接比较专属独立CLI、生产启动注入、公开API、任务池和前端；未运行前端构建/浏览器，未迁移生产数据。该轮新表仅在隔离真实测试中初始化；后续先接独立CLI及生产注入/API并同步前端类型，再做真实完整页面。Git、P4影响/追踪/增量草稿及其他P0—P5继续保留未完成。

## 比较产品接入安排（CLI/API/前端契约已接，页面/任务池待实施）

下面前四项本轮已接通并有专项；后两项及完整产品比较浏览器验收待实施。复用现有比较 Service/Store/Handler，不重新设计比较算法或租约任务平台。

| 实际依据 | 接入做法 | 完成条件 |
|---|---|---|
| [containers.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/containers.py) 的解析配置 provider；[main.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/main.py) 的解析初始化及路由注册 | 增加比较配置 provider；先初始化项目、文档、Outbox 和解析来源表，再初始化比较 Store/Service，绑定到 app.state 并注册独立路由 | 真实生产 API 启动、设置保存、任务提交/查询及重启后的同请求找回均通过 |
| [document_parse_worker_runtime.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/document_parse_worker_runtime.py)、[run_document_parse_worker.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/cli/run_document_parse_worker.py) | 增加比较专属运行时与 CLI；公共资源初始化/关闭复用现有方式，领域服务保留独立初始化。比较运行时只读取解析来源；不派发解析任务，不启动模型、浏览器或业务工具 | 真正启动独立 CLI，经 Outbox→Redis 领取并完成比较；原解析 CLI 同时回归通过 |
| [document_parsing.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/document_parsing.py)；[document_differences.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/document_differences.py) | 沿用项目 UUID、分页、错误与幂等查询约定，公开现有比较 DTO。按请求查询路由先于 task_id 路由注册 | 错误项目不可读写，输入冲突可辨认，未知提交能按原身份查询；失败轨迹可读 |
| [types.ts](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/types.ts)、[documentParsing.api.ts](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/services/documentParsing.api.ts) 和 API 聚合入口 | 新增比较类型及独立 API 适配器，字段与 Python DTO 同步；版本/快照、任务、结果和警告分别建模 | 适配器测试、类型检查和构建通过；不据此声称页面已实现 |
| 现有文档详情的解析选择、任务轮询、历史与 JSON 下载 | 增加双方版本及解析快照选择、差异视图、设置、历史、失败诊断和完整结果下载 | 实际完整 App/路由/侧栏→文档详情→比较→API 重启→历史/下载通过；原上传和解析入口回归通过 |
| 现有通用任务池只读投影 | 增加 document_difference 类型及按确切任务身份打开历史比较；继续从 PG 读取状态 | 筛选、分页、刷新和打开历史结果通过；不另建状态来源 |

**本轮已接通公开契约：** 以下路径均位于现有 `/projects/{project_id}` 下；首期不删除或改变解析接口。

| 路径及方法 | 语义 |
|---|---|
| `GET/PUT /document-difference-settings` | 读取/修改 MySQL 项目比较策略；修改只影响新提交 |
| `POST /documents/{document_id}/difference-tasks` | 接收现有 creation_key、old_snapshot_id、new_snapshot_id，返回固定任务，HTTP 202 |
| `GET /documents/{document_id}/difference-tasks/by-request` | 按 creation_key 找回原提交，不能改用最新快照另建任务 |
| `GET /documents/{document_id}/difference-tasks`、`GET .../{task_id}`、`GET .../{task_id}/failure` | 分页任务、查询状态和读取校验后的失败轨迹 |
| `GET /documents/{document_id}/difference-snapshots`、`GET .../{snapshot_id}` | 分页不可变历史结果；读取两侧固定引用、summary、warnings、effective_policy 及完整结果 |

任务/结果列表的 version_id 只筛选**新侧原件版本**；旧侧版本和双方 snapshot_id 始终显示。现有 `qa:tasks:document_difference` 常量用于独立流，在已有 Outbox 流配置中登记，复用消费组、连接与保留设置。未启用派发时仍能查询历史；提交入口明确显示不可调度状态，不能报告已开始执行。新表初始化的部署权限与已有解析来源外键须实际验收，不以隔离测试替代生产数据迁移。

上传、解析和比较是三个独立状态。解析完成后，页面默认**推荐当前新版本与前一版本的可用解析快照**，允许选择历史及同一原件的不同解析快照；双方没有可用快照时提示解析缺口。只有提交比较任务后才出现比较状态，上传成功或解析成功不等于已比较。提交响应未知时保留原请求身份和双方引用，先回查；历史查看不读取当前比较设置，不替换为当前默认快照。

**真实验收顺序：** 用真实 MD/DOCX/PDF V1/V2 及隔离 MySQL/PostgreSQL/RustFS/Redis，先运行生产 API 与独立解析 CLI 生成双方快照，再通过生产 API 提交比较、修改当前设置、重启 API、重放同请求，最后运行独立比较 CLI 并核对正文、来源、固定策略、历史/失败读取与终态重投。保留原件和子进程 stdout/stderr，核对仅一次计算、不可覆盖输入以及正确回收。之后再运行完整产品浏览器流程和任务池专项；默认外部模型、Git、P4 影响分析均不算该项通过。

## AI 影响判断与增量更新（P4，待实施）

### 历史比较产品入口实施前复核（基线 6dd162b）

实际任务池使用PG单次UNION分页，只投影公开DTO；现有前端把所有非session项当作case_planning。扩展第三种document_difference时同时更新联合类型、筛选、标题、阶段、统计与动作，并显式白名单投影DocumentDifferenceTaskRecord字段，不能输出seed/checkpoint/lease。文档标题仅在当前文档仍绑定原项目时读取；重新绑定后不得把新项目标题带入旧项目任务。

历史入口采用正式路由 `/projects/:projectId/documents/:documentId/differences/:taskId`，由任务池携带三个固定身份打开。独立历史页面复用可嵌入文档详情的任务/结果组件，不改Tools插件的大量上传状态，也不复制解析页面的提交恢复逻辑。组件查询指定任务，校验返回项目、文档、任务和双方快照；完成后只读取任务指定的结果，核对result与seed的双方引用和哈希。切换路由、卸载、较晚响应及轮询均使用上下文代次保护。只读历史不调用当前MySQL设置，不提交或重算任务。支持失败轨迹、原始/换行归一差异、字符细节、章节/表格、源位置、警告、有界展开与完整JSON下载。

此步骤验收任务池真实PG筛选/分页/状态/隐私、完整App/侧栏/正式路由→指定历史比较→API重启→刷新/下载与错误项目；独立解析和比较CLI提供真实DOCX输入及结果。比较提交表单、双方历史快照选择、设置与未知提交恢复仍是下一步，不能用只读入口声称比较页面已全部完成。既有解析/任务池接口同时回归。上传不经过Git，模板不进入执行输入，完整P0—P5目标不变。

确定性比较只提供证据。AI 基于双方快照、差异、用例原编写来源和当前用例提出需求匹配与影响候选；实际生效状态需要项目环境依据。没有可靠依据的匹配进入评审。不得仅凭“有文本变化”自动停用用例或覆盖正式版本；确认后的修改生成新草稿，删除只提出停用候选，旧用例和原运行结果保留。

## 独立 CLI 与生产 API 实施前复核（基线 6f4d227）

重新读取目标原附件、main/container、解析运行时/CLI、解析路由及生产验收夹具。比较服务的 stream=None 已明确拒绝新任务，但允许原请求找回和历史读取；直接沿用，不新增兼容分支。main 在解析服务初始化后创建比较 Store/Service，保证双方源快照表已存在，独立配置 provider 使用现有 MySQL 配置服务。

解析运行时包含项目、配置、文档、Outbox、SQLAlchemy/对象/Redis 回收及逐资源日志。抽取这段共同生命周期为内部 document_operation_worker_runtime，领域包装独立创建服务和 Store；解析的公开运行时类型、日志身份、消费组和 CLI 输出保持。比较包装先初始化 stream=None 的解析来源服务，再初始化比较服务；不登记解析派发、不运行解析子进程、不创建模型或工具服务。使用现有 RedisTaskWorker 与类型明确的比较 Handler，不建立第二种任务执行机制。

解析路由的项目绑定调用、404/409/503 映射和未知提交提示可与比较共用内部调用函数；保留解析本地 _call 包装及现有行为，比较路由使用自己的服务/日志身份。比较公开 DTO 与现有 schemas 保持一致，前端完整类型及独立 API 适配器同期接入；页面和任务池另一步验收。

生产验收复用 test_document_parse_production 与 test_execution_worker_cli 的独立数据库、配置表、Docker Redis、RustFS 和子进程工具。真实文件 V1/V2 通过生产上传路由入库，用独立解析 CLI 产生来源，再经生产比较路由提交并由独立比较 CLI 完成。核对固定策略、双方身份、原件/结果哈希、请求重放、API 重启、历史分页、错误项目、参数拒绝和终态重投；另通过小资源预算触发真实比较子进程失败并读取 stdout/stderr。清理时查询解析和比较的检查点/失败引用，保留本次独立进程日志和原件，不操作生产数据。既有解析独立 CLI 必须实际回归，不以新增比较通过代替。

## 比较独立 CLI 与生产 API 的实际交付（查证基线 6f4d227）

先复核方案与现有入口，再实现 [独立比较 CLI](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/cli/run_document_difference_worker.py)、[比较生产路由](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/document_differences.py)、容器/main 注入及前端类型/API。共用内部 document_operation_worker_runtime 和 document_operation_calls，领域初始化及公共解析契约保留，未复制租约/任务平台。比较运行时用 stream=None 初始化解析来源，读取固定快照；不派发解析、不调用模型或业务工具。

| 证据 | 结果及范围 |
|---|---|
| `p3-document-difference-production-first.xml` | 首轮 DOCX 完整 API/独立 CLI **1 通过、2 未选中（54.11 秒）**；首次即通过，未选中不是跳过 |
| `p3-document-difference-production-verified.xml` | 三种文档比较及既有解析生产专项 **4 通过、0 跳过（175.88 秒）**；独立进程、实际 MySQL/PG/RustFS/Redis、两次 API 重启、真实 CPU 失败轨迹和关闭派发后读取 |
| `p3-document-difference-wiring-regression.xml` | 比较/解析/内核/来源/资源/独立解析 CLI 及启动失败回收 **89 通过、0 跳过（79.16 秒）** |
| 前端 API 相关测试及构建 | 新比较适配器和既有规划/任务池 API 共 **6 通过**；类型检查与构建通过。此项使用替代 HTTP 响应，真实服务端 HTTP 由生产专项覆盖；未代表比较页面或浏览器验收 |

每种格式的原件、完整解析 JSON、比较 JSON、CPU 失败轨迹及 16 份进程 stdout/stderr 保存于 `p3-document-difference-production-verified/{md,docx,pdf}/`。固定设置70秒/1000行不受当前80秒/2000行改变影响；实际有效剩余超时单独保存。按来源重建新文本并核对原件、结果与失败轨迹哈希；双方解析快照和原件保持不变。成功终态重投 attempt 仍为1；关闭派发后原请求返回原终态，旧结果和失败轨迹可读，新身份返回503且无新增任务。故意 CPU 失败的条目保存为 failed；Worker 退出0仅表示消息正常处理，不能当作比较通过。

本轮公开路由及前端契约已接通；比较页面、任务池和完整产品浏览器仍待下一步。API 新增比较表初始化依赖现有解析来源表及建表权限，旧解析表/字段语义不变；仅在隔离数据库验收，不执行生产历史迁移。不改默认模型、依赖或生产配置；不部署Docling，不缩小完整P0—P5目标。

## 历史比较页面与任务池实际交付（查证基线 6dd162b）

已扩展三类PG只读任务投影和前后端判别联合，新增固定历史路由及可复用任务/结果组件。双方原件版本、解析快照/哈希、原始/换行归一文本、字符变化、章节/表格、源位置、解析警告、固定策略和失败轨迹可查，完整JSON可下载。每次只按任务指定结果读取；切换/卸载丢弃较晚响应，未知读取错误可刷新原任务。历史页面不调用当前比较设置、不重算、不提交；文档改绑后旧任务标题不泄露新项目资料。未新建任务状态表、配置域或依赖。

真实PG/文档专项首轮 **3通过（9.44秒）**，扩大解析/比较/规划任务池回归 **28通过、0跳过（59.07秒）**。最终完整产品专项 **2通过、0跳过（123.12秒）**：正式App/侧栏/任务池/路由、生产uvicorn、独立解析/比较CLI、MySQL/PG/RustFS/Redis均实际运行；实际DOCX 7→9，较新的反向任务存在时仍打开指定旧任务，等待任务轮询到完成，真实CPU失败诊断可见，关闭派发并重启API后刷新原历史并下载逐字段相同的JSON，错误项目无结果。浏览器模型调用0，比较历史读取不请求当前设置；同时复验原DOCX上传/版本/解析/设置与历史下载主流程。新证据位于 `p3-comparison-history-product-verified/` 和 `p3-comparison-history-parse-product-regression/`。

前端相关 **25通过、0失败**，实际产品构建包括类型检查通过（比较产品构建Vite6.81秒）。既有规划隔离宿主浏览器在新增证据目录复验 **1通过（12.12秒）**，不扩大成完整规划产品验收。原测试失败如实保留：首次后端从错误目录运行导致2项收集错误；前端真实失败样本夹具误把诊断正文当完整公开DTO，首轮14通过/1失败，按实际summary中的failed_task工件修正；完整比较浏览器首轮定位器误用“类型”，实际标签“任务类型”，1失败81.40秒，修正后1通过69.42秒，最终连同解析主流程2通过。产品没有为测试夹具加兼容特判。历史证据曾被既有测试默认目录覆盖，已将新证据另存、从HEAD恢复旧字节，并增加可选证据目录后实际复验；旧证据不作为新验收覆盖。

当前仍待文档详情比较提交表单、双方版本/快照分页选择、策略设置、提交响应未知恢复及这些入口的完整产品验收；这一步只完成历史查看与任务池，不等于整个比较页面/P3完成。Git可选来源、P4影响/追踪/增量草稿及其他P0—P5剩余项继续；生产历史迁移、默认外部模型、Docling部署未执行。


### 收尾补强：网络断言与全局提示框溢出根因

复查发现网络事件是包含url/method/status的对象，旧断言误按字符串判定，未证明“没有设置读取/比较提交”。保存的实际记录含16次比较读取、0次设置读取；修正为读取event.url，要求确切任务/结果GET存在并禁止比较POST，再执行完整生产场景。不能以旧空列表当作该项通过。

截图审查增加真实布局检查后，p3-comparison-history-product-network-verified、product-layout-first、product-layout-diagnosis、product-content-diagnosis分别1失败（57.20/54.97/54.28/57.59秒）。最小宽度调整无效，已撤回任务池改动；换为短页面诊断，再核对实际App溢出元素。真实数据证明隐藏service-tooltip-meta的264px内容框产生469px滚动宽度，经顶部状态栏传播到全局shell；Playwright正常滚动定位使shellScrollLeft=101、侧栏left=-101。健康接口已有hide_password=True，不是密码明文泄露。短诊断首次脚本漏tests导入路径，修正后执行；替代HTTP/无长地址场景不复现，不能代替生产证据。

p3-comparison-layout-long-target-before使用健康DTO的掩码长地址复现相同101px偏移，1失败（24.27秒）。根因修复仅在既有service-tooltip-meta增加overflow-wrap:anywhere；未重置scrollLeft、隐藏溢出文本或增加业务特判。最终p3-comparison-history-product-final.xml **3通过、0跳过（138.66秒）**，其中**2项真实生产完整产品比较/原上传解析流程，1项替代HTTP的完整App布局回归**；不将替代HTTP称为真实存储验收。最终真实layout.json为shellWidth=viewport=1440、shellScrollLeft=0、sidebarLeft=0、contentOverflow为空。实际网络记录存在指定历史任务和结果GET，没有比较POST或当前设置请求；关闭派发、API重启、失败轨迹、错误项目与完整JSON下载继续通过。重启窗口App健康/会话轮询出现预期连接拒绝，stdout/stderr与网络记录保留；重启后加载恢复，浏览器JS错误为空。

最终证据以p3-comparison-history-product-final/、p3-comparison-history-parse-product-final/和p3-comparison-layout-long-target-verified/为准，原失败与中间结果保留，次数不累加为覆盖数。最终三个产品/布局夹具均实际构建并含typecheck。18项公开比较任务字段与TypeScript契约、PG任务池判别联合及当前5个后端测试模块编译另核对通过。完整P0—P5仍未完成；下一步仍是文档详情比较提交/双侧版本与快照选择/策略/未知提交恢复，随后继续Git、P4及其余范围。

交付核对：暂存相关文件327份（报告自身与空的源文件diff检查工件随后加入）；266份源码/方案/文本工件凭据格式扫描，唯一匹配为原有英文翻译Password标签，核对后无凭据格式匹配。304份原始工件工作区/暂存字节一致；最终下载/真实网络GET/零比较POST/布局和镜像/附件章节一致性实际核对。直接对原始工件运行git diff --check出现CRLF/原始空白提示，未改写证据；改为源码、类型、Vue、CSS及方案的diff检查通过，原件/JSON/进程日志另做字节核验。新增后端测试显式加入，用户.gitignore未暂存。


## P3.4 文档详情比较提交与恢复实际交付（2026-10-04，研究基线 e2a112c）

本轮先重读目标附件、实际上传/比较/解析服务、API与配置DTO、现有任务组件和完整产品夹具。比较请求仅保留creation_key和双方固定快照；当前设置由服务端冻结。前端复用既有版本/快照分页、已验收任务展示与生产API，不增加第二套Worker、任务框架或存储。设置全部11项保存在现有MySQL配置域；任务/输入/Outbox/结果元数据保持PG，原件/结果保持RustFS。

ApiDocsPlugin文档详情增加ApiDocDifferences；两侧来源复用DocumentSnapshotSelector，设置由DocumentDifferenceSettings保存；DocumentDifferenceTask增加可选原请求校验和验证后的updated事件。正式历史路由不需要新prop，仍按原身份读取。unknown提交先回查/重试原请求，终态缓存不解锁新提交，历史查看不改变未知请求；只读项目仍可读历史，配置失败不影响固定结果。

实际问题与修复证据：

- frontend-before.xml：21项，6失败/15通过。4项为新夹具未命名的任务stub和实际样本本来就是70秒；修正夹具后frontend-reproduction.xml保留2项真实失败：刷新快照替换已选历史，以及任务页身份异常隐藏有效结果页。
- 快照刷新现在保留已选不可变输入，包括不在新的第一页的历史快照；分页偏移仍取服务端页数据长度。两个历史目录分别校验/报错，保留成功部分；不吞错。frontend-fixed.xml为21通过，扩大最终frontend-final.xml为59通过/0失败。测试还覆盖丢回执/回入/404原重试、缓存终态、项目和引用错配、晚响应、双侧原件/快照与任务/结果分页、partial/unsupported、缓存故障、只读、设置11字段及项目切换，使用已保存真实DOCX结果作样本。
- typecheck首轮发现新测试使用element.value类型和.at超出现有TS目标，按HTMLSelectElement和既有slice修正测试；产品契约不变。最终typecheck.stdout/stderr通过；实际产品三次build均包含typecheck。
- product-first.xml：1失败（59.77秒），测试误以进程有效预算应严格等于70；实际difference_service按剩余截止时间缩小到69.999769，固定seed配置仍为70。根据现有实现校验0<有效预算<=70且等于snapshot.effective_policy，保留原失败，未改变产品预算。product-verified.xml为1通过（60.70秒）。
- product-final.xml：3通过、0跳过（173.38秒），正式App/侧栏/文档详情、生产uvicorn、独立解析及比较CLI、真实MySQL/PG/RustFS/Redis。DOCX V1/V2 7→9；页面保存70秒配置；POST服务端提交后丢回执；修改当前设置80及选择、关闭/重新进入、原请求查询，PG核对仅一个task/Outbox；CLI结果保留原双方及70配置，完整JSON逐字段相同。派发停用/API重启历史可读、新提交503；归档隐藏提交/保存且旧结果可读。另两项完整产品复验指定历史/轮询/真实CPU失败/错误项目/下载与原DOCX上传/版本/解析/设置/导航/重启流程。三个场景模型调用均为0，浏览器JS错误为空。
- backend-regression.xml：27通过、0跳过（49.96秒），真实解析/比较任务、PG任务池及项目隔离，未重复执行同类未受影响测试。

证据目录p3-comparison-submit-evidence/保留真实原件、结果、截图、aria、网络、失败XML和生产/CLI stdout/stderr；.gitattributes以-text保留原始字节。隔离对象已按本次数据库资产/检查点引用清理，历史URI不是在线访问地址。截图实际查看，结果差异可见，归档表单按钮隐藏；浏览器网络核对原请求GET和503。原上传路径仍无Git，生成模板仍不进入执行。

关键命令：agent_web中npm run test -- [ApiDocDifferences/DocumentDifferenceTask/ApiDocParsing/ApiDocVersions/ApiDocAssets/ApiDocsPlugin.importSources/documentDifferences.api/TaskPoolView/sessions.task-pool.api] --reporter=junit --outputFile=../docs/upgrade_2026-10-03/p3-comparison-submit-evidence/frontend-final.xml；npm run typecheck。Agent_Server中开启RUN_LIVE_DOCUMENT_DIFFERENCE_SUBMISSION_PRODUCT、RUN_LIVE_DOCUMENT_DIFFERENCE_FULL_PRODUCT、RUN_LIVE_DOCUMENT_FULL_PRODUCT，再运行..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_difference_submission_product.py tests/test_document_difference_full_product.py tests/test_document_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-comparison-submit-evidence/product-final.xml，三个QA_*_EVIDENCE_DIR均指向本轮独立目录。后端回归开启RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS、RUN_LIVE_DOCUMENT_PARSERS、RUN_LIVE_TASK_POOL，运行test_document_difference_tasks/test_document_parse_tasks/test_task_pool_document_differences/test_task_pool_entries。

PLAN、用户粘贴原文件和Codex镜像直接同步，原备份保留。本轮不新增依赖/数据库表/配置键，不执行生产历史迁移、不部署Docling、不推送/建PR；用户.gitignore不提交。完整P0—P5仍未完成，下一步先调研Git只读来源；P4影响/需求追踪/增量草稿与P2剩余恢复/真实默认模型、模块化生成、其他完整产品流程、人工录制和Docker证据及P5继续。
