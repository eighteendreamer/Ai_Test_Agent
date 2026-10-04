# P3.4 文档确定性差异：实际依据与实施边界

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

本轮仍未接比较专属独立CLI、生产启动注入、公开API、任务池和前端；未运行前端构建/浏览器，未迁移生产数据。新表目前仅在隔离真实测试中初始化；后续先接独立CLI及生产注入/API并同步前端类型，再做真实完整页面。Git、P4影响/追踪/增量草稿及其他P0—P5继续保留未完成。

## 比较产品接入的具体实施安排（基线 22be347，待实施）

下面是接下来需要实施的内容，不能视为已存在的公开接口。现有比较 Service/Store/Handler 已可复用，不重新设计比较算法或租约任务平台。

| 实际依据 | 接入做法 | 完成条件 |
|---|---|---|
| [containers.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/containers.py) 的解析配置 provider；[main.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/main.py) 的解析初始化及路由注册 | 增加比较配置 provider；先初始化项目、文档、Outbox 和解析来源表，再初始化比较 Store/Service，绑定到 app.state 并注册独立路由 | 真实生产 API 启动、设置保存、任务提交/查询及重启后的同请求找回均通过 |
| [document_parse_worker_runtime.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/document_parse_worker_runtime.py)、[run_document_parse_worker.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/cli/run_document_parse_worker.py) | 增加比较专属运行时与 CLI；公共资源初始化/关闭复用现有方式，领域服务保留独立初始化。比较运行时只读取解析来源；不派发解析任务，不启动模型、浏览器或业务工具 | 真正启动独立 CLI，经 Outbox→Redis 领取并完成比较；原解析 CLI 同时回归通过 |
| [document_parsing.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/api/routes/document_parsing.py)；[document_differences.py](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/document_differences.py) | 沿用项目 UUID、分页、错误与幂等查询约定，公开现有比较 DTO。按请求查询路由先于 task_id 路由注册 | 错误项目不可读写，输入冲突可辨认，未知提交能按原身份查询；失败轨迹可读 |
| [types.ts](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/types.ts)、[documentParsing.api.ts](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/services/documentParsing.api.ts) 和 API 聚合入口 | 新增比较类型及独立 API 适配器，字段与 Python DTO 同步；版本/快照、任务、结果和警告分别建模 | 适配器测试、类型检查和构建通过；不据此声称页面已实现 |
| 现有文档详情的解析选择、任务轮询、历史与 JSON 下载 | 增加双方版本及解析快照选择、差异视图、设置、历史、失败诊断和完整结果下载 | 实际完整 App/路由/侧栏→文档详情→比较→API 重启→历史/下载通过；原上传和解析入口回归通过 |
| 现有通用任务池只读投影 | 增加 document_difference 类型及按确切任务身份打开历史比较；继续从 PG 读取状态 | 筛选、分页、刷新和打开历史结果通过；不另建状态来源 |

**拟新增公开契约：** 以下路径均位于现有 `/projects/{project_id}` 下；首期不删除或改变解析接口。

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

确定性比较只提供证据。AI 基于双方快照、差异、用例原编写来源和当前用例提出需求匹配与影响候选；实际生效状态需要项目环境依据。没有可靠依据的匹配进入评审。不得仅凭“有文本变化”自动停用用例或覆盖正式版本；确认后的修改生成新草稿，删除只提出停用候选，旧用例和原运行结果保留。
