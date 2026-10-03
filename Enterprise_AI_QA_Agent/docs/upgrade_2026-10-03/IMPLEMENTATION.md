# 升级实施与验证记录

目标：完整实施用户确认的用例理解、执行补全与需求变更方案。每个步骤必须先研究实际代码和行为，再实施，再进行逻辑、真实功能及受影响回归验证。未验证的项目不计为完成。

方案来源：用户附件 `01122314-2714-413a-8ef5-34242757ff2c/pasted-text-1.txt`。工作区起点为 `652b5c0`，已有 `.gitignore` 的 5 条忽略规则属于用户，本次不提交；仅单独暂存本轮测试与证据的白名单。

## 存储约束

- MySQL：可编辑配置，包括生成模板、解析配置、执行策略及通用设置。使用现有 MySQL 连接设施和服务接线。
- PostgreSQL：项目、用例及固定版本、导入批次、文档版本与解析快照、需求、关联、计划、运行、结果和证据元数据。
- 原件和大工件继续使用现有 RustFS；Redis、向量及图谱作为交付层或可重建投影，不成为业务唯一依据。
- 已冻结用例、解析、执行快照保存当时实际使用的配置内容/版本，属于业务证据，存 PostgreSQL；执行不读取可变生成模板。
- 数据库连接及访问密钥等启动必需信息沿用既有环境变量，不向配置接口或日志公开。

## 全量交付追踪

| 阶段 | 必须交付 | 当前证据/状态 |
|---|---|---|
| P0 消息 | 修复完整历史重复追加、相同原始/归一请求重复拼接；分阶段追踪调用与 HTTP 重试 | 修复前新增回归：6 失败、1 通过；修复后针对测试 37 通过，含真实本地 HTTP SDK 与 LangChain 两条路径。当前真实模型/工具/SSE 测试 1 通过。完整调用追踪待实施 |
| P0 API | 修复端点/任务身份错位；实际依赖执行与传值 | 已修复身份映射、按参数选择来源、写操作假依赖循环及显式绑定优先级；实际文档/RustFS/PostgreSQL/HTTP 验证与相关回归 53 通过。完整 Agent 模式与 GUI 报告待验收 |
| P0 录制 | 验证 DeepSeek 人工录制、响应、清洗、持久化、AI 使用 | 待实际验证 |
| P0 Docker | 验证真实性能/安全工具调用、工件、解析及报告 | Docker 可用；具体测试待执行 |
| 配置 | 所有本次新增可编辑配置使用 MySQL；纠正相关文件配置路径 | MySQL 通用配置存储及通用/通道高级设置迁移已提交，API/Prompt 共用来源；后端 70 通过、前端 4 通过及构建通过。模板已接入工作区实现；解析配置待实施；遗留通道配对业务文件待迁移 PG |
| P1 模板 | 系统默认、项目自定义、指导与示例、模板弹窗 | 本轮提交实现。隔离真实 MySQL 模板保存、修订及项目隔离和组件测试已通过；产品浏览器操作待验收 |
| P1 用例 | 完整任意 JSON、自身说明、版本、来源、前端渲染、旧结构兼容 | 本轮提交实现。JSON 草稿接口、嵌套未知字段、追加版本与旧版本保留、真实 PG 重新读取及组件测试通过；本轮兼容复核通过；完整执行仍待完成 |
| P1 生成 | 按模块生成、来源/模型/Prompt/Skill/模板版本、失败范围及独立重试 | 实际文档上传→当前真实模型/Skill→JSON 草稿→模板快照/来源入 PG 已测试；模块进度、失败范围及独立重试待实施 |
| P1 导入 | Excel 默认两种布局、映射/预览/错误、JSON、幂等、草稿评审 | 上传、MySQL 配置、RustFS 原件、PG 批次与草稿同事务、前端入口已接入。受影响后端回归 141 通过、最终导入/进程专项 21 通过；真实浏览器组件/HTTP/三种存储 3 通过，前端组件 15 通过、构建通过。完整生产启动与项目页全流程待验收 |
| P2 选取 | 自由/按用例依据分离；项目内召回、完整读取、AI 选择、无匹配明确返回 | 已实现独立项目规划服务与页面，完整版本、跨历史模式、项目隔离、固定计划保存及重读已验证。执行请求的依据分离及图状态接入仍待实施 |
| P2 补全 | AI 语义检查、项目查证、正式依赖或运行准备、负向条件保留、缺口可见 | 只读查询、正式依赖/准备结构、引用与拓扑校验、有界校验反馈已实现；专项 21 通过。当前真实模型三场景测试 1 通过，实际组件/HTTP/三种存储浏览器 1 通过，不等同实际 Agent 执行 |
| P2 执行 | 固定版本、依赖领取、输出绑定、检查点恢复、计划修订、结果证据与新回归 | RunItem 固定依赖、必要输出领取检查、启动复核及失败阻塞结果已实现，专项 14 通过（含 5 个真实存储场景）、相关回归 146 通过；固定计划到运行映射与 Agent 执行/传值/准备及恢复仍待接入 |
| P3 上传 | 同一文档 V1/V2、原件与派生分存、重复判定、版本查询、解析失败可见 | 待实施 |
| P3 Git | 独立可选只读入口；明确关联才导入为已有文档版本；上传不依赖 Git | 待实施 |
| P3 解析 | MD/python-docx/pypdf 工厂、统一结构/位置/版本/警告、独立受限进程；Docling 仅扩展入口 | 待实施 |
| P3 差异 | 原文与归一确定性差异、位置映射、历史解析快照保留 | 待实施 |
| P4 影响 | 用例/编写来源/文档变化三方对照；生效状态及分级处置、增量草稿 | 待实施 |
| P4 追踪 | 稳定需求版本、验证点、用例版本、覆盖与执行证据矩阵、待复核 | 待实施 |
| P5 知识 | 场景管理、步骤整理、元素别名、版本查询及失败定位 | 待实施 |
| P5 报告 | API/性能/安全/录制真实结果与证据一致，工具故障与功能失败区分 | 待实施 |
| 上线 | 跨层契约、增量迁移、项目启用、旧运行恢复、配置分库、阶段提交 | 待实施 |

Mock、定时任务、脚本导出仍为后续候选，不作为本次交付要求。现有模式保持，禁止将新用例执行重新强制绑定到模式参数编译器。

## P0 消息研究结论

依据：`prompt_assembly_service.py:build_for_turn` 返回完整历史；`graph/nodes/prompt_assembler.py` 原实现将结果再次追加到原历史。不是同内容去重问题，而是服务返回契约被误用。修复采用完整替换，不删除用户真实重复提问或工具协议消息。

无历史时，原实现分别插入原始与归一请求，即使两者相同。只在归一内容非空且不同于原请求时添加归一节。

研究参照：本地课程《Agent轨迹与协议鲁棒性》《接口测试-通用篇》《评测工程化与CICD》要求同时核对过程轨迹、数据依赖、幂等与可追溯测试证据。

新增 `test_prompt_history_http.py` 覆盖多轮组装、用户重复提问、相同/不同归一输入、实际回环 HTTP、生产 SDK 与 LangChain 适配、assistant/tool 配对和每次显式成功调用的实际发送次数。真实外部模型、HTTP 失败重试和整条产品录制不由这些测试证明。

已执行：

```powershell
# Agent_Server 目录
..\.venv\Scripts\python.exe -m pytest tests/test_prompt_history_http.py -q --tb=short
# 修复前：6 failed, 1 passed。
..\.venv\Scripts\python.exe -m pytest tests/test_prompt_history_http.py tests/test_prompt_assembly_service.py tests/test_model_clients.py tests/test_sdk_midstream_retry_boundary.py tests/test_langchain_model_adapter.py -q --tb=short
# 修复后：37 passed。
$env:RUN_LIVE_MODEL_TOOL_TESTS='1'
$env:QA_TEST_GATE_OUTPUT_DIR='<本目录绝对路径>'
..\.venv\Scripts\python.exe -m pytest tests/test_live_model_tool_stream.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p0-live-model.xml
# 1 passed in 9.51s。
```

真实模型验收复用了现有 opt-in 测试：只读获取 MySQL 的活动模型配置，运行实际模型传输、只读 session-history 工具及真实 HTTP SSE，校验流式正文与最终会话结果一致。会话/作业使用内存隔离，该测试不证明 PostgreSQL 持久派发或产品 GUI。机器结果见 `acceptance.json` 与 `p0-live-model.xml`。

## 未完成与验证边界

本记录不会以单元测试通过代替功能验收。上述全量条目没有证据或仅间接证据时仍为待完成；目标保持 active。每一步补充调查结论、真实输入、执行输出、限制及 commit，不覆盖原始失败记录。

## P0 API 依赖研究与验证

根因及依据：

- `PreconditionResolver` 使用 endpoint_id，`DependencyPlanner` 原实现按 task_id 查同一字典，导致依赖没有进入任务图。
- 原资源/参数名子串猜测无法关联 `/orders/{id}` 与 `POST /orders`；现在复用已有集合 POST 辅助分析，按每个占位符所在集合路径寻找唯一来源，再明确转换为运行 task_id。多候选不任意选择。
- 两种协调器原来对每个依赖响应尝试所有参数，嵌套父子 ID 可能错绑。共用参数绑定实现，各参数只读取其规划来源；旧持久任务缺少新字段时保持兼容读取。
- 真实嵌套文档将子创建接口列在父创建接口前，原文档顺序串联写操作与真实依赖形成循环。移除假依赖；两种运行协调器已有写串行及资源锁能力，不靠文档顺序保证串行。
- 新增显式字段验证重现两种协调器先用猜测值替换占位符，随后明确字段映射无法生效（2 failed、1 passed）。改为先应用明确映射，再推断剩余占位符。

真实输入：创建普通订单和嵌套明细的 Markdown 文件，故意将查询写在创建之前、子创建写在父创建之前。用实际上传/目录/解析/执行服务发送 TCP HTTP 请求，确认创建订单 → 创建明细 → 查询明细，并分别传递实际 ID 0 与 19。负向条件语义与正式用例规划不由这条 API 辅助链路解决，仍属于 P2。

启用 `RUN_LIVE_UPGRADE_STORAGE_TESTS=1` 后两种场景实际写入 RustFS 与 PostgreSQL，重新实例化目录存储读取后核对项目和对象地址，并比对原件字节。使用临时 UUID 命名测试表和独立对象，结束后只清理本测试创建的对象、表；不初始化或修改生产文档目录。实际 API 目标为本地受控 HTTP 服务。此测试没有调用 AI，不能据此宣布完整 Agent API 模式或页面报告已经验收。

```powershell
# Agent_Server 目录
$env:RUN_LIVE_UPGRADE_STORAGE_TESTS='1'
..\.venv\Scripts\python.exe -m pytest tests/test_api_document_dependency_http.py tests/test_api_task_executor_recovery.py tests/test_api_mode_session_isolation.py tests/test_api_doc_project_binding.py tests/test_api_request_body_contract.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p0-api-dependencies.xml
# 53 passed in 12.32s。
```

新增任务字段默认空字典；搜索已核对分析、规划、两种协调器、任务模型和持久恢复消费者。前端没有独立消费该任务字段的契约。Python 编译与 diff 空白检查另行执行；未运行前端构建（本步骤无前端改动）。

## 配置存储研究与实施

依据：模型、邮件、通道主配置已有 MySQL 存储，复用 `mysql_raw_connection` 和现有 MySQL upsert 写法，不引入新连接或依赖。原通用设置 API、Prompt 语言读取与通道高级设置分别直接读写 JSON，读取错误会静默变成默认值。根因是配置来源分散，而不是单个文件写入方式。

实施：

- `MySQLApplicationConfigStore` 保存 scope/key、JSON 与修订号；默认表 `system_application_config`。复用原连接设施，参数化业务值，验证表名；按 scope 隔离配置，修订号在事务中增长。
- `WorkspaceSettingsService` 为 API、Prompt、通道策略提供同一来源。启动时只在对应数据库记录缺失时，从原 `src/data/general_settings.json` 与 `channel_advanced_settings.json` 读取并校验后迁移；无文件时播种默认值。竞争初始化只插入缺失项，不覆盖已保存配置。
- 原文件保留不改写。迁移后不再读取文件、静默降级或双写。坏文件首次迁移和坏数据库 JSON 均显式失败并留日志；配置不可用时不假装 Prompt 使用了用户设置。
- 保留原 `/settings/general` 与 `/settings/channels/advanced` 方法、字段、返回结构及 `lastSavedAt` 行为；一般设置模型移动到 schema 并同步生产接线和测试。新增表名可通过现有嵌套环境机制 `DATABASE__APPLICATION_CONFIG_TABLE` 设置，这属于数据库引导信息。
- API 的同步 MySQL 读写使用 `asyncio.to_thread`。当前安装的 LangGraph `_internal/_runnable.py:571` 将同步图节点包装到 `run_in_executor`，Prompt 组装作为同步图节点仍在该既有线程路径运行。

功能验证：创建包含语言与通道配置的实际 JSON 文件，迁移到隔离的真实 MySQL 表；启动实际 HTTP 服务使用 GET/PUT 保存并重新读取；重新实例化存储与服务后确认保存数据优先于旧文件；通过生产容器的 Prompt 接线确认最新语言进入实际提示文本。并发更新修订号分别为 2/3/4/5；并发播种不覆写已存在配置；不同 scope 读取隔离；人为破坏测试记录的 JSON 后确认错误可见。测试只清理自身创建的 UUID 命名表，不修改生产设置。

```powershell
# Agent_Server 目录
$env:RUN_LIVE_UPGRADE_CONFIG_TESTS='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -m pytest tests/test_workspace_settings.py tests/test_channel_config.py tests/test_prompt_assembly_service.py tests/test_prompt_history_http.py tests/test_model_settings_routes.py tests/test_settings_event_loop.py tests/test_channel_gateway_policy.py tests/test_settings_data_import.py tests/test_settings_cleanup_contract.py tests/test_settings_data_export.py tests/test_lifespan_resource_cleanup.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/config-mysql.xml
# 70 passed, 1 warning in 7.75s；没有跳过。
# agent_web 目录
npm run test -- src/features/settings/plugins/GeneralSettingsPlugin.cleanupContract.test.ts src/features/settings/plugins/GeneralSettingsPlugin.exportLifecycle.test.ts
# 2 test files / 4 tests passed。
npm run build
# vue-tsc 类型检查通过；Vite 构建通过。
```

已有 Starlette/anyio 的 BlockingPortal 弃用警告保留；本步骤不擅自升级依赖。验证仅覆盖隔离配置表及实际设置 HTTP 接口，不证明完整产品 GUI、生产 lifespan 或所有配置域已经迁移。生产首次启动需要现有 MySQL 连接具备新配置表创建权限；坏遗留文件会使迁移失败，修复后重试。回退旧二进制后不会自动把 MySQL 变化写回旧文件，恢复前需明确导出所需配置；禁止同时运行旧文件写入服务与新数据库写入服务作为同一配置源。

仍需按全量追踪继续实施：新增模板及解析配置使用本存储；通道配对请求属于业务记录，其遗留 JSON 文件与 Redis 会话不能计为已完成 PostgreSQL 迁移。完整应用启动、录制、Docker、P1—P5 仍未完成。

## P1 模板及 JSON：实现与验证记录

调研依据：原用例 schema 只接受固定步骤/断言字段，原执行适配器直接按模式编译步骤，项目页面没有完整版本内容入口。当前工作区增加 `content_json`、自身说明与生成记录；保留旧字段及原哈希构造兼容。项目模板复用 MySQL 配置服务；生成只读取一次模板，冻结到 PostgreSQL 用例版本中。编辑追加新草稿版本，不修改历史版本。此记录是当前进度，不能解释为完整 P1 或 P2 已完成。

实际输入包含任意嵌套 JSON、未知字段、0/false/null、中文及 emoji；另创建订单需求 Markdown 原件，经实际 RustFS 上传、当前 MySQL 模型配置、现有生成 Skill 与模型请求生成两条草稿，检查来源、模板快照及 PostgreSQL 重新读取。真实数据库使用隔离 UUID 表，测试结束只清理自身创建的对象与表。

```powershell
# Agent_Server 目录；此前已实际执行
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:RUN_LIVE_CASE_GENERATION_TESTS='1'
..\.venv\Scripts\python.exe -m pytest tests/test_case_json_templates.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p1-json-templates.xml
# 4 passed in 102.18s；机器结果见 p1-json-templates.xml。
# agent_web 目录；此前已实际执行
npm run test -- src/features/projects/TestCaseAuthoring.test.ts src/features/projects/ProjectExecutionDashboard.test.ts
# 2 files / 10 tests passed。
```

边界：JSON 接口测试使用 ASGI HTTP 传输；组件测试替代了 API，不证明浏览器和生产服务端联调。真实模型测试验证生成与保存，不证明负向条件的语义正确性或后续执行。此前相关后端回归 68 passed、1 skipped；跳过项尚须核对，不计为通过。前端构建此前通过，此后还有小幅编辑，应在代码提交前再次执行。

旧执行适配器不能处理任意 JSON，工作区对此返回明确阻塞原因。Agent 新执行策略尚未实现，不能把此阻塞处理当作用户要求的执行能力，更不能回到“用例参数对应测试模式”的设计。Excel/JSON 文件导入、模块化生成及 P2—P5 仍须按计划继续推进。

本轮用户要求更新方案，因此将以上现状及未完成项同步到 PLAN.md，并同步用户此前选中的计划文件；此次方案提交不包含尚在复核中的产品代码。


### 兼容复核与本轮交付

复现了新共享 schema 把草稿步骤连续性校验带到历史读取的问题：旧记录允许的编号被拒绝（1 failed）。将写入校验放回草稿请求，保留历史记录读取契约。用旧提交 c4005ba 的 `_build_version` 实际执行捕获固定哈希，再用新服务创建相同旧结构草稿核对哈希；实际 PG 测试写入不含新字段的历史记录，重新读取后核对数据库原 JSON 与哈希列均未改变。

本轮实际执行（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_JSON_TESTS='1'
..\.venv\Scripts\python.exe -m pytest tests/test_case_json_templates.py -q --tb=short -k 'not actual_document_model' --junitxml=../docs/upgrade_2026-10-03/p1-json-compatibility.xml
# 6 passed, 1 deselected in 3.43s；此前已通过的实际模型生成不重复调用。
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -m pytest tests/test_test_case_lifecycle.py tests/test_test_case_generation_pipeline.py tests/test_test_case_embedding_pipeline.py tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_recording_case_draft.py tests/test_lifespan_resource_cleanup.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p1-json-regression.xml
# 77 passed in 5.51s；本轮无跳过。
```

前端再次执行上述两个文件：10 tests passed；`npm run build` 的 vue-tsc 与 Vite 均通过（Vite 5.89s）。Python compileall、跨层引用核对及 diff 空白检查通过。本轮提交模板、JSON 版本和渲染/编辑入口及兼容修正；`.gitignore` 只提交测试白名单，用户已有 5 条规则保留未暂存。此前“尚未提交”和旧回归跳过数是历史进度，以上结果为当前交付依据。实际模型生成、JSON 保存不证明新 Agent 执行可用，P1 文件导入及 P2—P5 仍未完成。

对应代码提交：`0baafec`。

## P1 文件导入调研与解析内核

依据来源 → 采用做法：

- 当前 `ToolRuntimeService._run_test_case_xlsx_exporter` 已使用 openpyxl，项目已声明该依赖 → 复用 Workbook 生成“每行用例”和“用例/步骤分表”两种模板，不增加 Excel 依赖。
- 本地参考 `项目借鉴/ai_test_agent/tools/document_parser.py:_parse_xlsx` 只将工作表转成文本，接口导入只处理 Postman/HAR → 不能直接作为正式用例导入，新增从列映射到现有草稿契约的纯解析模块。
- [官方读取接口](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html) 与 [只读模式说明](https://openpyxl.readthedocs.io/en/stable/optimized.html) → 使用 read_only、data_only=False、keep_links=False，并关闭工作簿；重置不可靠的工作表尺寸，按实际读取行列执行限制，避免漏读尾部步骤。公式保留原文并提示，不计算或使用缓存值。
- 本地课程《测试数据合成与用例自动生成》《线上流量闭环与评测集治理》 → 保存来源、原件哈希、映射与行号，导入形成草稿，不能自动启用或执行。
- `UploadSecurityService.secure_store_upload` 已有 Office 结构及上传对象流转；`PostgresTestCaseStore._create_many_sync` 只有草稿事务；旧 Smoke 导入已有账本与资产同事务写法 → 后续接线复用上传设施和同事务思想，不能先写草稿再单独记录批次作为幂等方案。

当前实现 `CaseImportParser` 支持 `.xlsx` 与 UTF-8 JSON；两个默认 Excel 布局、表头建议、明确映射、步骤关联排序、自定义完整 JSON、未知字段与原列内容追溯、重复编号及逐行错误均可返回。公式、汇总预期、负向条件保持原文；关联步骤错误使对应候选不可导入，不能删除该步骤后生成看似完整的草稿。JSON 数组与 cases 包装均可读；手工导入不伪造模型或 Skill 调用，治理字段明确标记 not-used，解析器版本独立保存。

实际输入由 openpyxl 创建并保存 `.xlsx`，再读文件字节进入解析器；另创建 UTF-8 BOM JSON。覆盖两种布局、自定义表头、嵌套 0/false/null/中文/emoji、无效序号、重复和孤立步骤、公式原文、错误尺寸、缺表头、坏 JSON/XML、宏内容及数量限制。另保存两份可查阅示例及实际解析预览，见 `import-samples/rows.xlsx`、`split.xlsx` 与对应 preview JSON；它们仅说明导入格式，不是实际项目功能测试通过的证明。

```powershell
# Agent_Server 目录
..\.venv\Scripts\python.exe -m pytest tests/test_case_import_parser.py tests/test_test_case_lifecycle.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p1-import-parser.xml
# 21 passed in 1.25s（解析 15 项 + 既有用例生命周期 6 项）。
```

Python compileall 和 diff 检查同时通过；本步骤无前端改动，未重复前端构建。此内核不写配置文件、不写业务数据库。下一步接入 MySQL 的导入策略/映射配置、RustFS 原件、PG 上传来源与不可变预览批次，使同一工作簿能选择不同工作表；确认提交时批次与草稿同事务保存，重复提交返回同一结果、编号冲突不覆盖。原件保存后的失败补偿、进程超时/资源限制、接口及前端入口和真实存储联调均尚未实现，不宣称完整 Excel 导入可用。


## P1 文件导入：存储、接口与浏览器验证

现有原件处理复用 `UploadSecurityService.secure_store_upload` 与 `ArtifactStorageService`；MySQL 复用版本化配置存储；PG 批次借鉴既有 Smoke 导入事务，抽出原用例批量 INSERT，普通创建与导入共用 SQL，避免重复业务写法。原件保存与数据库提交独立，提交响应丢失先核对目录，再决定是否清理本次对象。CPU/RSS 监控使用项目已有 psutil 与进程回收方式；解析只允许固定 worker，不接受用户自选程序。

接口挂到现有 API 前缀下的 `/projects/{project_id}/test-case-imports`：配置读取/保存、两种 Excel 模板下载、来源上传/原件下载/表头读取、不可变预览与提交。项目页面增加导入弹窗。原件唯一性按项目和字节哈希管理；同一个原件可选择不同工作表或配置产生新预览；批次与所有草稿在同一 PG 事务提交，编号冲突整批回滚，不覆盖历史用例。批次固定配置快照及来源证据，提交后仍处于草稿，不自动评审启用。

调研和功能测试确认并修正了两项缺陷：

- 前端删除空映射、后端恢复自动识别，使“不映射”无效。真实 Excel 与组件回归先复现失败，再统一约定：省略键表示自动识别，空字符串表示显式不映射；必填列取消后明确报错，原始列仍保存为来源。
- 父进程轮询可能错过快速结束的 CPU 超限进程。回归实际出现一次未拒绝；worker 在发布结果前补核对 CPU/RSS，父进程仍负责运行中限制及进程树回收。

实际测试还覆盖项目隔离、两种 Excel 布局、未知 JSON、原件哈希核对、配置快照不随当前设置变化、重复提交、并发提交、真实晚到编号冲突回滚、并发上传只留一个来源、目录提交响应丢失仍保留已引用原件、失败时仅清理未引用对象、记住表头行、降低限制后的错误预览、超时/CPU/RSS/输出大小、取消与完整 stdout/stderr/退出码。

本轮实际执行：

```powershell
# Agent_Server，启用隔离真实数据库及对象存储测试
$env:RUN_LIVE_CASE_IMPORT_TESTS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_import_parser.py tests/test_case_import_process.py tests/test_case_import_service.py tests/test_case_json_templates.py tests/test_test_case_lifecycle.py tests/test_test_case_generation_pipeline.py tests/test_test_case_embedding_pipeline.py tests/test_case_embedding_index_lifecycle.py tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_case_execution_verification.py tests/test_recording_case_draft.py tests/test_lifespan_resource_cleanup.py -q --tb=short -k 'not actual_document_model_skill' --junitxml=../docs/upgrade_2026-10-03/p1-import-regression.xml
# 141 passed, 1 deselected in 20.31s。
# 未重复调用此前已通过的真实模型生成；此轮不修改生成 Prompt 或模型调用。

# 最后补充记住表头与策略变更错误报告之后，重跑受影响导入/进程模块
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_import_process.py tests/test_case_import_service.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p1-import-storage.xml
# 21 passed in 17.73s。

$env:RUN_LIVE_CASE_IMPORT_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_import_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p1-import-browser.xml
# 3 passed in 11.75s。

# agent_web
npm test -- src/features/projects/CaseImportDialog.test.ts src/features/projects/TestCaseAuthoring.test.ts src/features/projects/ProjectExecutionDashboard.test.ts
# 3 files / 15 tests passed。
npm run build
# vue-tsc 与 Vite 构建通过（6.35s）。
```

浏览器使用 `browser-automation` 技能及现有 Playwright/Chromium。技能所指相邻 tool-index.md 未找到；直接核对项目已安装依赖、实际浏览器路径及现有浏览器验收脚本后使用，不新增工具或依赖。创建实际 Excel/JSON 文件，用实际 Vue 导入组件和服务方法，经 Vite 代理到实际 HTTP API，再写隔离 MySQL/PG/RustFS。逐字节核对下载原件、检查预览和保存内容一致、分表排序及任意 JSON 保留、配置重新读取、草稿未自动启用和重复提交只有一条用例。保存 DOM 快照、报告及截图 `p1-import-browser-{rows,split,json}.json`、`p1-import-browser.png`。测试结束关闭浏览器、Vite 和 HTTP 服务，清理本次 UUID 表、对象与临时宿主页。

浏览器首次宿主页直接引入预构建 Vue 时，文件输入持续禁用；改为由 Vite 解析 Vue 依赖后恢复。初次未保留完整控制台轨迹，不能断言具体的 Vue 内部原因。随后修正测试的布局定位器，最终三种输入通过。以上属于验收宿主页问题，不能冒称生产故障已修复。

边界：浏览器宿主页加载的是正式导入组件，未启动整个生产 lifespan 或完整 ProjectsView 页面；生产首次接入需现有 MySQL/PG 建表权限。新启动表名为 `agent_case_import_sources`、`agent_case_import_batches`，可编辑策略键为 MySQL `test_case.import_settings`。本轮不验证草稿之后的 Agent 执行，P2—P5、模块化生成、P0 录制/Docker 和生产入口验收仍需继续。

## P2a 项目内选取、只读规划及固定计划

依据来源 → 采用做法：

- `embedding_service.retrieve` 支持正式 project_id 及可选 mode_key；摘要只包含部分步骤 → 新服务传 mode_key=None，召回后通过 TestCaseService 重新读取已启用完整版本。标题/编号查询和明确用例身份查询使用现有项目列表与版本接口，候选之外的必要前置从项目目录发现。
- `ApiDocsService.search_documents` 保留旧项目名查询行为；正式列表支持 project_id，内容读取可能返回 read_error 和旧预览 → 使用正式项目目录、直接身份核对及完整读取；失败时不能把旧预览作为原文。范围和内容哈希随计划保存，新的文档版本身份与解析快照仍待 P3。
- 正式 ProjectRecord 无 metadata 字段；录制图查询返回 status=success → 使用实际项目字段及录制契约，不臆造读取字段。生产接线复用已有 recording_store/recording_graph_store；当前测试只验证录制查询契约，未证明人工录制或真实 AI 使用可用。
- 现有 ModelRuntimeService 支持配置能力及 stream_handler → 规划复用当前模型适配器，流式配置通过现有流式调用接收结果，记录首文本耗时和字符数；不修改全局模型配置。规划不提供业务执行工具，不建立第二套 HTTP 测试执行器。
- 当前生成服务 `_parse_payload` 已支持 JSONDecoder.raw_decode → 同样接受完整 JSON 周围的模型说明，保持严格 typed DTO 校验，不删除非法字段或改写正式步骤。
- 现有工具执行器将参数校验错误作为 Agent 可见结果 → 规划将结构/引用错误返回下一回合。该反馈共用原规划回合与超时限制；已读依据 ID 必须精确引用，后端不做模糊替换。
- 本地课程《Agent测试专项》《Agent轨迹与协议鲁棒性》《评测工程化与CICD》 → 保存完整固定依据、回合轨迹、模型与 Prompt 版本；合法 JSON 只是必要条件，另用实际模型核对目标、依赖、负向条件和已有数据场景。

新增 MySQL 配置键 `test_case.planning_policy`，全局默认仅缺失时创建，项目覆盖可修改；计划冻结实际配置修订和内容。PostgreSQL 新表 `agent_case_plans` 保存按 project_id 隔离的不可变计划、完整正式用例 JSON/自身说明/来源、按需读取的原文或录制依据、模型和 Prompt 版本、回合及失败状态。RustFS 沿用原件存储。表名引导配置 `DATABASE__POSTGRES_CASE_PLAN_TABLE` 沿用既有设置机制；启动需要现有数据库建表权限。

接口为 `/api/v1/projects/{project_id}/case-plans` 的生成与按计划身份读取，以及 `/policy` 的读取和项目设置保存。前端增加“用例执行规划”弹窗，展示目标、正式依赖、运行内准备、完整固定 JSON、自身说明、数据来源、原文和具体缺口；明确提示尚未开始测试。选中的正式用例只是候选，不强制全部作为目标。正式执行项只引用 case/version，准备项独立保存 instructions；后端和前端使用同一按 kind 区分的契约。

实际失败与根因（失败 XML 及样本保留）：

1. 默认 180 秒的非流式调用超时；放宽隔离项目策略后仍收到 http_error。后续按模型已有 streaming 能力调用，成功取得完整响应。未改 SDK 的默认重试或全局模型设置，不将该结果当作重复发送排查完成。
2. 只有目标候选而没有项目正式用例目录时，模型漏补可用正式前置。增加项目目录和按身份完整读取；目录分页显式可见，不能把首次候选当全部用例。
3. 首版 JSON Schema 允许正式项 instructions，后端却禁止；实际模型给正式依赖复制了步骤。改为 FormalCasePlanItem/PreparationPlanItem 区分结构，Schema 与执行引用约定一致。原输出 `p2-rejected-formal-instructions.json` 回归仍应被拒绝，不能通过删字段伪造成功。
4. 实际准备计划 JSON 后附带说明和相同 JSON，严格 json.loads 报 Extra data。复用已有生成解析方式，保留原样 `p2-model-output-with-trailing-text.txt` 验证提取后的准备、依赖和正式版本不变。
5. 后续实际模型正确形成依赖链，但文档依据哈希引用多抄一位，冻结被拒绝。保存 `p2-rejected-evidence-reference.json`；新增下一回合校验反馈。回归确认第一次确实拒绝、第二回合自行改正才保存；连续错误达到回合上限仍失败，无静默纠正。

本轮实际执行（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py -q --tb=short -k 'not actual_model_selects' --junitxml=../docs/upgrade_2026-10-03/case-planning-storage.xml
# 21 passed, 1 deselected in 7.25s。
# 包含隔离真实 MySQL/PG/RustFS 的两个参数化场景；模型以受控回复检验确定性契约。

$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py tests/test_case_json_templates.py tests/test_test_case_generation_pipeline.py tests/test_test_case_embedding_pipeline.py tests/test_test_suite_version_freeze.py tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_recording_graph_store.py tests/test_lifespan_resource_cleanup.py -q --tb=short -rs -k 'not actual_model_selects and not actual_document_model_skill' --junitxml=../docs/upgrade_2026-10-03/p2-planning-regression.xml
# 104 passed, 1 skipped, 2 deselected in 8.03s。
# 未启用真实 Memgraph 图固化项；实际规划模型单独运行；此前生成模型未重复调用。

$env:RUN_LIVE_CASE_PLANNING_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/case-planning-browser.xml
# 1 passed in 5.64s。

# agent_web
npm test -- src/features/projects/CasePlanDialog.test.ts src/features/projects/CaseImportDialog.test.ts src/features/projects/TestCaseAuthoring.test.ts src/features/projects/ProjectExecutionDashboard.test.ts
# 4 files / 18 tests passed。
```

浏览器使用此前已读取的 browser-automation 技能与项目已有 Playwright/Chromium，实际 Vue 组件经 Vite 代理调用真实 HTTP 服务，再访问隔离 MySQL/PG/RustFS。以实际 JSON 文件创建并评审启用用例，以实际 Markdown 上传保存原文；验证已有数据不多补步骤、缺数据按文档准备、负向条件不补登录、完整第七步显示、项目策略重读、旧计划配置冻结及其他项目不能读取。报告和截图 `p2-planning-browser.json/png` 已检查，页面无运行时错误。浏览器采用受控模型回复；真实模型语义另测。测试结束关闭本次浏览器和进程，清理本次项目表、对象和临时宿主。

验收命令曾写错不存在的 suite 测试路径，结果 no tests ran；通过 rg --files 查到真实 test_test_suite_version_freeze.py 后执行了上面的实际回归，不把错误命令计为通过。

边界：当前规划 POST 等待异步规划完成，没有可恢复的后台任务身份；完整生产 lifespan 和 ProjectsView 页面未验收。正式执行仍由旧模式编译适配器处理，运行创建仍有历史模式检查，SQL 领取只按位置，没有依赖门槛。下一步必须接入执行依据/图/TurnState、套件及运行 Outbox 事务、依赖领取、真实输出、受控工具、检查点与审批恢复，不能用当前独立规划服务替代完整 P2。P3—P5、模块化生成、人工录制、Docker 和完整 HTTP 重试追踪仍未完成。

### P2a 最终实际模型验收与构建

```powershell
# Agent_Server
$env:RUN_LIVE_CASE_PLANNING_MODEL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py -q -s --tb=short -k 'actual_model_selects' --junitxml=../docs/upgrade_2026-10-03/case-planning-model.xml
# 1 passed, 21 deselected in 99.36s。
# agent_web
npm run build
# vue-tsc 与 Vite 通过，Vite 6.99s。
```

实际模型使用当前配置的 step-5-preview，未替代模型回复；实际 Markdown 经 RustFS 保存，正式 JSON 用例经 PG 草稿、评审及启用，隔离项目策略从 MySQL 读取。五次合法模型调用分别耗时 16.594、24.277、17.742、13.704、23.094 秒，最终验证：三个目标与两条正式依赖，actor-register→order-create→order-detail 的数据绑定；不存在订单与未登录的负向目标无额外前置；已有 order_id 时仅目标项；归档前置正式用例后，按实际需求原文补运行内准备，无伪造 case_id，保存后从 PG 重读一致。模板指导与其他项目的标记均没有进入模型上下文。安全汇总见 p2-model-planning.json。

该测试只运行规划，没有发送 POST /actors、POST /orders 或查询业务接口，不能证明输出提取、依赖领取或 Agent 执行已通过。隔离复杂场景的项目策略为 480 秒，默认策略仍为 180 秒；结果显示模型本轮耗时在默认内，但不保证其他模型/目标的延迟。此前失败结果分别保留，最终成功不覆盖失败样本；Prompt 改为 case-planning-v2，计划保存实际模型和 Prompt 身份。

Python 编译、跨层引用、方案同步与 Git diff 检查在提交前实际核对。用户此前五条 .gitignore 规则保留未暂存，仅提交新增规划测试白名单。不推送、不创建 PR。完整目标仍未完成，下一步骤为 P2b 执行接入，不能将本阶段通过计为 P0—P5 完成。

## P2b 运行依赖领取基础与方案同步

本步骤解决既有领取链缺少依赖约束的架构缺口。不是增加注册、登录等业务检查清单；AI 仍负责识别业务前后条件，存储层只执行已经声明的依赖与必要输出约束。已更新完整 PLAN.md 的 §2.4 和第六节，并补充 P2b 接入顺序；上传独立版本、Git 可选来源、模板仅用于生成的约定保留。原粘贴附件保留，同步的是项目方案与所选 Codex PLAN.md。

依据来源 → 采用做法：

- `run_store.py` 原领取查询按位置使用 `FOR UPDATE SKIP LOCKED` → 在同一事务、LIMIT 前检查必要前置，防止仅排序后被多个 Worker 提前领取；内存实现在原锁内保持一致，启动时再核对。
- `run_management.py` 的运行条目保存完整 JSONB record，历史项无依赖字段 → 增加默认空 dependencies，旧数据兼容读取；不新增 SQL 列、不迁移历史结果。创建运行及 Outbox 共用 `_insert_run_and_items`，在写入前校验同一运行引用、重复、自引用及循环。
- 现有结果/尝试记录、完成哈希和状态统计 → 前置终态失败或输出缺失时原子保存 blocked 条目、尝试及结果，明确 `business_execution_started=false`、没有 started_at，引用前置结果。它是未执行业务的终态回执，不是通过证据；逐层处理受影响条目，独立项继续，终态不重新领取。
- 现有 subagent_coordinator 的 dict/list 路径访问、存储层 JSONB 用法，以及 PostgreSQL 官方 [JSON 操作](https://www.postgresql.org/docs/current/functions-json.html)和 [SELECT/锁](https://www.postgresql.org/docs/current/sql-select.html) → 使用相对 result.actual 的字面量键/索引数组，保留点号键、空键、根路径及负索引；0/false 可用，缺失/null 不可用。用真实 PostgreSQL 18.6 核对 Python 与 `#>` 的行为。
- 既有 item→run 加锁顺序 → 所有候选处理完再一次更新 run 统计，避免中途锁 run 后继续锁 item；数据库 blocked 日志在事务提交后记录，回滚不误报成功。

修改契约同步到前端 TestRunItemRecord 类型。新增依赖模块、运行存储接线及专项测试；没有新增可编辑配置，不改变 MySQL/PG/RustFS 的分工。尚未增加用户录入依赖的接口；规划服务到运行的映射、Agent 策略及真实输出提取仍未接入。

实际失败及验证：

- 改动前专项：8 失败、2 跳过，`p2-dependencies-before.xml` 保留。复现提前领取，以及无效引用和循环没有拒绝等行为。
- 内存与原存储相关测试：39 通过、2 跳过，`p2-dependencies-memory.xml`；当时未开启真实存储项。
- 首次真实验收 fixture 使用了不存在的 `postgres_test_result_table`，实际 DatabaseConfig 拒绝，2 项失败；查证后改为现有 `postgres_test_case_result_table`。这是测试配置错误，不是产品故障；当时尚未创建本步骤表。
- 真实存储启用后的专项：14 通过，其中 9 个内存场景、4 个实际服务及 MySQL/PG/RustFS 场景、1 个真实 PG JSON 操作核对。创建实际 JSON 文件，以正式 project_id 完成草稿、评审、启用和套件冻结，再验证领取与存储。每次使用 UUID 隔离表及对象，清理仅限本次创建的数据。
- 8 个并发领取者及倒序多层依赖验证失败回执只写一次，独立项完成，原失败结果不变。注入实际 PG 结果/尝试写入后的异常，确认条目、结果、尝试及统计一起回滚；重试只产生一份回执。过期恢复验证依赖元数据与检查点保留；没有执行业务副作用。

```powershell
# Agent_Server，专项实际存储
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_item_dependencies.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-dependencies-storage.xml
# 14 passed in 6.15s；包含内存与真实存储，未发送业务 HTTP。

# 相关运行、Outbox、时长、回归、旧适配器、冻结与审批恢复
$env:RUN_LIVE_POSTGRES_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_item_dependencies.py tests/test_test_run_lifecycle.py tests/test_test_run_postgres_claim.py tests/test_test_run_outbox_transaction.py tests/test_test_run_timing.py tests/test_test_run_duration_transitions.py tests/test_test_run_regression.py tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_case_execution_verification.py tests/test_test_suite_version_freeze.py tests/test_cancelled_run_missing_approval_recovery.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p2-dependencies-regression.xml
# 146 passed in 14.16s，无跳过。

# 最后将日志移至提交后、补齐返回类型后复核受影响项
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_item_dependencies.py tests/test_test_run_postgres_claim.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-dependencies-final.xml
# 29 passed in 5.64s，无跳过。

# agent_web
npm run build
# vue-tsc 与 Vite 通过，Vite 6.26s。
```

边界：本步骤只验证运行存储和调度条件，不调用实际模型、业务 HTTP、Docker 或人工录制，也未启动完整生产 lifespan。前端本次仅增加兼容类型，没有新增组件行为，未重复浏览器验收。blocked 结果可经既有运行/结果接口读取，但专用会话完成事件和完整报告仍待接入。既有模式限制和旧用例编译器仍存在，不能把 146 项通过表述为新的 Agent 按用例执行已完成。

后续按 PLAN.md 的 P2b 顺序，先研究并接通固定计划→现有套件/运行/Outbox，再接通执行依据、图及 TurnState、实际工具与输出绑定、准备步骤、检查点/审批恢复、报告及新回归。每步仍须用真实功能验收，不以语法或内存断言替代。P3—P5、模块化生成、P0 人工录制/Docker、完整 HTTP 重试追踪和生产入口验收继续保留待实施状态。

### P2b 精确输出路径与接入前研究

沿当前套件、运行、Outbox 和执行服务逐层核对：TestSuiteService.create 先独立提交套件，TestRunService 的 `_persist_run_with_task` 只覆盖 run/items/outbox；CaseExecutionAdapter 仍拒绝任意 content_json，RedisExecutionDispatcher 则直接调用现有执行服务。不能仅加计划启动按钮，把这些 JSON 用例送到旧编译器后报告接入完成。准备项还包含目标之后的清理，现有固定计划未声明持久化执行归属，不能把所有 prepare 都当成目标之前的步骤。套件/运行事务和准备归属接入继续待实施，未新增对外启动入口。

另一个已确认的契约缺口：PlanInputBinding 只有点分字符串 output_path，RunItemDependency 使用字面量键/索引数组。同一 `body.order.id` 无法区分嵌套对象与 body 下的 `order.id` 完整键，直接 split 会改变数据来源。本次先补齐这个接入前提，保持完整目标与后续执行步骤不变。

依据 → 实现：已读取现有 PlanInputBinding、规划 typed Schema/校验反馈、运行依赖 output_at_path、前端 CasePlanInputBinding 与通用 JSON 展示，以及本地《Agent轨迹与协议鲁棒性》的参数语义和证据要求。增加可选 `output_path_tokens` 字面量数组与统一 path_tokens 读取属性；旧 output_path 仍按点分读取。新数组与非空旧路径同时指定时明确拒绝，不猜测哪个正确。空键、点号键、数组负索引、根对象、0/false 保留；不增加表达式执行或新依赖。前后端类型同步，原页面通用 JSON 树直接展示新字段，无须新增字段转换。新 Prompt 为 case-planning-v3，历史记录仍保持原 Prompt，不批量重写。

实际验证：

- 实现前新增专项 6 失败、1 跳过、1 未选择，保存 p2-binding-before.xml。模型受控回复包含精确键数组时，旧契约拒绝并最终 planning_failed；单项测试也证明没有精确路径读取能力。
- 实际 JSON 文件创建正式用例，HTTP 创建计划并经 MySQL 策略、PG 业务记录保存/重读；旧 v2 计划没有新字段仍能读取，版本标记不变。初次专项及规划回归 25 通过、2 跳过、3 未选择，p2-binding-storage.xml；未开启旧规划的实际存储项和两项实际模型项。
- 后续开启所有相关实际存储：58 通过、3 个模型项未选择，p2-binding-regression.xml，无跳过。最终补充实际 JSON 原件上传到 RustFS 及读回内容核对后，58 项再次通过（8.42 秒）。包含实际计划路径显式映射到既有 RunItem 依赖、原子领取等待、真实 PG 结果持久化后读取 `body["order.id"]` 与 false，再领取消费项。业务输出由测试写入，未发 POST/GET；这证明存储及路径衔接，不证明 Agent 实际业务传值。
- 独立实际模型验收 1 通过（101.13 秒），当前 step-5-preview 经 3 回合生成一条正式前置与目标、正确的 ["body", "order.id"] 引用，并从 PG 重读一致。安全汇总 p2-binding-model.json 和 JUnit p2-binding-model.xml；模型读完整用例，没有测试回复替代。该验收只规划，不调用业务接口。
- 前端规划组件 3 通过；npm run build 中 vue-tsc 与 Vite 通过，Vite 5.82 秒。本轮仅类型扩展，未重复浏览器全流程。

```powershell
# Agent_Server
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_plan_binding_paths.py tests/test_case_planning.py tests/test_run_item_dependencies.py tests/test_test_run_postgres_claim.py -q --tb=short -k 'not actual_model' --junitxml=../docs/upgrade_2026-10-03/p2-binding-regression.xml
# 58 passed, 3 deselected in 8.42s（最终增加原件上传与读回核对后）。
$env:RUN_LIVE_PLAN_BINDING_MODEL='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_plan_binding_paths.py -q -s --tb=short -k actual_model --junitxml=../docs/upgrade_2026-10-03/p2-binding-model.xml
# 1 passed, 7 deselected in 101.13s；随后增加两个参数化存储场景，不改变产品实现。
# agent_web
npm run build
npm test -- src/features/projects/CasePlanDialog.test.ts
```

本轮没有新增可编辑配置、SQL 列或迁移，继续使用 MySQL 配置、PG 业务记录与 RustFS 原件。完整方案及所选 Codex PLAN.md 同步；相关新测试白名单单独提交，用户原五条忽略规则保留未暂存。固定计划到运行事务、实际 Agent 执行、共享准备归属/清理、检查点恢复及其余 P0—P5 项仍未完成。

### P2b 固定计划到套件、运行及 Outbox 的内部事务

先研究现有 TestSuiteStore、TestRunStore、Outbox、规划证据及课程《Agent测试专项》《Agent轨迹与协议鲁棒性》，确认缺口是套件与首个运行缺少共享事务、准备项没有正式运行归属，而非业务用例缺少模式参数。新增 plan_mapping、CasePlanRunService，并扩展原运行存储；不另建执行系统。准备归属的设计依据及无现成对应实现已在源代码注释说明。

仅正式目标和依赖创建套件条目、RunItem。准备项选择依赖图中的正式消费项作为执行前拥有者；末尾清理选择正式前置中的最后条目作为执行后拥有者。快照保存完整原计划、真实 RunItem 映射及归属。无正式关联的准备拒绝，不伪造正式 case_id。准备跨条目共享必须在冻结计划声明；当前共享消费项等待拥有者终态，未优化为准备一完成就并发释放消费者。

重要语义修正：正式业务前置默认要求 passed；共享准备的拥有者只需 terminal，但准备自己的状态须 passed，声明的实际输出须存在。拥有者目标失败并不使已经成功的准备失效；清理等待消费项终态，不能要求所有目标通过才清理。Python 和 PostgreSQL 的领取、启动、blocked 判断同步；旧记录仍默认 passed、无准备要求。

结果适配契约为 actual.case_output 保存正式输出，actual.preparations[准备身份] 分别保存 status、output；字面量键/数组索引路径加相应前缀。当前专项由测试写入受控结果，不声称该输出适配已经由 Agent 或业务工具执行。

PostgreSQL 单事务依次锁定项目、重读已保存计划、按稳定顺序读取正式用例并保护生命周期、校验精确冻结版本，然后保存套件、条目、运行及既有格式 Outbox 派发意图。复用原套件插入和运行插入方法。新启用版本不替换冻结版本；归档竞态在事务内重新检查。参考官方 [PostgreSQL 显式锁](https://www.postgresql.org/docs/current/explicit-locking.html)，使用 FOR SHARE 保护用例状态，避免仅 KEY SHARE 无法阻止普通更新；没有网络或模型调用持有这些锁。

creation_key 派生稳定套件/运行身份，creation_hash 覆盖计划及请求。并发相同请求只落一份套件、运行和意图；同 key 改计划/模式/会话明确冲突。已提交但响应丢失时重读原运行，包括已经终态或用例后来停用的重试，不能重新执行。内存实现沿原锁验证同类行为，不具备持久 Outbox，真实持久性只按 PostgreSQL 测试证明。

实际验证与失败证据：

- 新增专项实现前为 3 失败、4 跳过，p2-plan-run-before.xml 保留；内存阶段 3 通过、4 跳过，p2-plan-run-memory.xml 保留。
- 早期真实回归 69 通过、1 失败，p2-plan-run-outbox-before.xml 保留。失败是测试预期直接收到 psycopg CheckViolation，而项目现有 SQLAlchemy 门面包装为 IntegrityError；修正测试核对 .orig 是 CheckViolation，没有将这项描述为产品修复。此前 fixture 的项目查询也先依据现有 get(project_id) 契约修正，才保存实现前证据。
- 真实事务阶段 70 通过，p2-plan-run-storage.xml；最终相关回归 **193 通过、2 个实际模型测试未选择，21.71 秒，无跳过**，p2-plan-run-regression.xml。其中新增创建专项共 17 项，含 10 项真实 PostgreSQL 场景。
- 使用真实 JSON 文件、正式隔离项目、MySQL 策略与模板、RustFS 文件以及 PG 草稿/评审/启用/计划记录。只使用 UUID 隔离测试表、对象和项目；仅清理本次创建的数据。验证 8 次并发创建、插入套件/运行后异常全部回滚、Outbox 实际约束失败全部回滚、提交响应丢失后幂等重读、事务前停用、版本变化保留固定引用、跨项目拒绝、篡改归属拒绝、共享准备成败及独立项不受影响、终态不可再次领取。快照不含生成模板标记。
- 旧适配器新增明确拒绝 agent 策略，专项证明工具解析器未被调用。前端契约同步 execution_strategy、plan_snapshot、plan_item_key 及依赖条件；npm run build 的 vue-tsc 与 Vite 实际通过（本轮复核 Vite 5.71 秒），Python 编译通过。

```powershell
# Agent_Server，最终相关回归（所有相关真实存储开关启用）
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_plan_run_creation.py tests/test_run_item_dependencies.py tests/test_plan_binding_paths.py tests/test_case_planning.py tests/test_test_run_lifecycle.py tests/test_test_run_postgres_claim.py tests/test_test_run_outbox_transaction.py tests/test_test_run_timing.py tests/test_test_run_duration_transitions.py tests/test_test_run_regression.py tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_case_execution_verification.py tests/test_test_suite_version_freeze.py tests/test_cancelled_run_missing_approval_recovery.py -q --tb=short -rs -k 'not actual_model_selects_dependencies and not actual_model_uses_literal_path' --junitxml=../docs/upgrade_2026-10-03/p2-plan-run-regression.xml
# 193 passed, 2 deselected in 21.71s
```

本步骤没有新增可编辑配置、SQL 列或数据库迁移；兼容字段仍存 PostgreSQL JSONB，旧记录使用默认值。本次没有实际模型、业务 HTTP、人工录制、Docker 或生产 lifespan 验收，也没有新增公开启动 API/UI或生产依赖注入。完整目标仍未完成：下一步接通现有 RuntimeService/图/TurnState、受控工具、准备执行与真实输出提取、检查点/审批恢复、新策略回归，再开放启动入口。P0、模块生成、P3—P5 和完整生产验收继续保留。
