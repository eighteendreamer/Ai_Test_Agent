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
| P2 选取 | 自由/按用例依据分离；项目内召回、完整读取、AI 选择、无匹配明确返回 | 已实现独立项目规划服务与页面，完整版本、跨历史模式、项目隔离、固定计划保存及重读已验证；执行上下文与图状态已接入，公开保存计划启动 API/UI 已验证实际存储和浏览器断线重试。持久规划任务仍待完成 |
| P2 补全 | AI 语义检查、项目查证、正式依赖或运行准备、负向条件保留、缺口可见 | 只读查询、正式依赖/准备结构、引用与拓扑校验、有界校验反馈已实现；专项 21 通过。当前真实模型三场景测试 1 通过，实际组件/HTTP/三种存储浏览器 1 通过，不等同实际 Agent 执行 |
| P2 执行 | 固定版本、依赖领取、输出绑定、检查点恢复、计划修订、结果证据与新回归 | 已有依赖、计划创建内部事务及上下文/回执核验。本步骤交付运行服务 Agent 策略、生产初始化注入、RunItem 审批/已保存决定恢复、持久意图/跨 attempt 重放及工具资源身份修复；扩大回归 359 通过、10 跳过、1 个模型项未选择，其中 8 个资源项另补验通过；前端 18 通过及构建通过。Agent 新回归及公开保存计划启动已接通，真实 Redis/Outbox/Worker 类与 Agent 审批/HTTP 结果专项通过，详见下方最新记录；默认模型目标回执验收仍失败，独立 Redis 执行进程/完整启动、其他模式真实工具、步骤级恢复/未知效果核对及计划修订仍待完成 |
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
- 使用真实 JSON 文件、正式隔离项目、MySQL 策略及 PG 草稿/评审/启用/计划记录；fixture 配有 RustFS 客户端，本创建专项未实际上传对象，不能计为 RustFS 文件验收。只使用 UUID 隔离测试表及项目；仅清理本次创建的数据。验证 8 次并发创建、插入套件/运行后异常全部回滚、Outbox 实际约束失败全部回滚、提交响应丢失后幂等重读、事务前停用、版本变化保留固定引用、跨项目拒绝、篡改归属拒绝、共享准备成败及独立项不受影响、终态不可再次领取。快照不含生成模板标记。
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

### P2b 完整执行上下文、提示词及中断记录

研究 RuntimeService、AgentLoop、TurnState、图、PromptAssemblyService、router 和 SessionService 的实际调用链后调整方案：完整执行输入放入既有 ExecutionRequest.context/context_bundle，不在多个状态层另存整份计划。现有 TurnState 重组、RuntimeService 快照及恢复已承载 context_bundle；AgentLoop 自身的中断记录没有保存该字段，新增专项实际复现 KeyError 后补存深拷贝。深拷贝防止后续状态修改改变已保存的恢复依据。

另一确认的缺口是提示词仅列上下文键，不展示任意上下文值。新增类型化 AgentCaseExecutionContext 及构造器，加入专门的冻结执行数据/执行规则段。读取固定 JSON、自身说明、来源和依赖，并核对运行/条目/版本/原计划引用。按已冻结归属提供前置准备、正式执行及后置清理；外部输入直接读取已保存 result.actual 的精确路径，记录 result_id、run_item_id 及路径；本条目内尚未执行的准备保持待解析引用。0/false 有效，必要输入缺失不能用计划示例填充，跨运行结果拒绝。正式用例完整内容只呈现一次，来源段保留身份、版本和哈希；数据继续按既有非可信内容规则包裹，不授予权限。

router 和提示词均区分显式 free/cases：free 不注入正式用例，cases 不再召回当前启用版本替换冻结内容。没有声明依据的旧请求维持原参考召回行为。路由事件记录 execution_basis；上下文日志只含身份与数量，不打印业务 JSON、凭据或正文。新构造器仍是内部能力，本轮未改对外请求 DTO、前端或生产执行适配器。

实际验证：

- 实现前专项 **5 失败、2 跳过**，p2-agent-context-before.xml。复现自由测试仍注入用例、中断丢上下文、冻结执行继续召回，以及新构造器缺失。最初存储专项 **7 通过**，p2-agent-context-storage.xml；随后扩展两种模型传输路径与真实模型专项。
- 完整 JSON 文件包含 8 个步骤、末尾随机标记、负向条件、点号/空键及 0/false/null。实际上传原件到 RustFS 并逐字节读回，经 PG 草稿/评审/启用、规划及运行记录构造执行输入。受控规划回复只用于隔离上下文测试；既有固定计划及运行结果都从真实 PG 读取，未用自由文本项目名。另核对实际前置 false、共享准备的 0、目标失败而准备成功、坏准备/跨运行结果拒绝、准备及清理归属。
- 两种生产模型发送路径（原生 SDK 与 LangChain）使用真实本地 TCP HTTP 服务；每条路径三次组装/发送，正文末尾标记只出现一次，模板和召回替换标记未发送。HTTP 模型响应由受控服务返回，不据此声称外部模型推理或业务工具已执行。相关回归首次 **186 通过、2 个规划模型测试未选择，22.01 秒**；最终加入实际模型专项、运行身份检查后 **186 通过、3 个独立模型项未选择，22.44 秒**，p2-agent-context-regression.xml，无跳过。其中本步骤 9 项全部通过，4 项实际存储（2 项完整文件/HTTP 和 2 项前置结果读取），完整文件/HTTP 的内存与真实存储组合共发送 12 次本地 TCP 请求。
- 真实 step-5-preview 使用当前 MySQL 模型配置，实际读取由原件及 PG 固定版本产生的提示词，精确返回第 8 步的随机标记、保持未登录和 false 原值：**1 通过、9 未选择，10.60 秒**；模型调用本身 6.428 秒。安全汇总为 p2-agent-context-model.json，JUnit 为 p2-agent-context-model.xml。没有伪造模型回复，也没有调用业务工具或将这次读取记为用例通过。
- TurnState 反组装、AgentLoop 中断记录、RuntimeService 的快照构建/恢复及 pending fallback 核对上下文和未知副作用状态保留。这些是状态构造测试，未声称本步骤已经完成真实审批中断后执行业务的联调。

```powershell
# Agent_Server，实际模型读取专项
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_AGENT_CASE_CONTEXT_MODEL='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_context.py -q -s --tb=short -k actual_model_reads --junitxml=../docs/upgrade_2026-10-03/p2-agent-context-model.xml
# 1 passed, 9 deselected in 10.60s
```

本轮复核前一步 fixture 后修正了其 RustFS 验收措辞：前一步实际创建事务测试只配置客户端，未上传对象；本步骤才实际上传/读回原件。此前失败 XML 保留。没有新配置域、SQL 列或数据库迁移；前端没有本步骤契约改动，未重复构建。Python 编译及相关回归实际执行，完整方案和所选 Codex PLAN.md 同步。

下一步研究并接通 Agent 执行适配器与既有 SessionService/ToolRuntime 的受控执行和持久化；准备结果及正式输出须引用实际工具结果，不能把模型口头判定当作工具证据。继续完成真实业务目标调用、运行租约与审批恢复、未知副作用核对、新策略回归及公开入口。其余 P0—P5 范围保持待完成，完整目标仍为 active。

### 用户所贴完整方案的整理与同步（2026-10-03）

本次按用户“那不更新这个方案？”的要求更新文档。重新读取粘贴附件、现有 PLAN.md、上传服务/对象存储/目录模型/生产接线，以及已实现的项目规划与冻结上下文；没有新增产品功能。

PLAN.md 开头改为用户要求与正文设计的对应表，清除多轮修订说明造成的当前状态歧义；主流程和选取流程统一为初排后检测、补入后确定最终顺序。检测说明补充一次实际判断的输入与输出例子，不引入注册/登录固定检查清单。上传独立保存及 Git 可选来源沿用 §3.1—§3.2 的完整设计；附件的历史哈希去重表述以“请求幂等、与当前内容比较、恢复旧内容创建新版本”替换。新增当前项目规划和完整执行上下文的代码链接，明确其已有实现不等于业务工具执行已完成。

实际执行命令（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py -q --tb=short
# 25 passed in 1.13s
```

此验证仍限于现有上传相关行为，不证明新文档版本及真实 RustFS/PostgreSQL 的 V1/V2 功能。方案本地链接、关键约定、文档 diff 及所选 Codex 计划字节一致性另行检查。只提交本轮 PLAN.md 与本记录，保留用户的 .gitignore 修改；原粘贴附件保持原样。本次没有数据库迁移，未重新执行前端构建、生产启动、浏览器、真实模型或业务工具验收，P0—P5 的剩余实施范围保持原记录。

### P2b 工具证据归属、结果回执与真实 HTTP 验证（2026-10-04）

本步骤仍承接完整 P0—P5 目标。先读取用户方案附件、现有运行执行服务、单工具适配器、Runtime/Session、ToolRuntime/ToolJob、审批和治理路径、验证服务、实际 API runner，并查阅本地《Agent测试专项》《Agent轨迹与协议鲁棒性》。确认任意业务 JSON 的多工具结果无法由旧模式参数编译器或工具 completed 状态代替；缺的是实际输出到固定预期的证据关联。先补内部结果契约，没有把旧适配器开放为 Agent 策略。

依据 → 实现：

- `AgentCaseExecutionReceipt` 为每项正式用例和所属准备/清理保存状态、检查及输出引用。预期路径指向固定用例 JSON 或准备说明，实际路径指向本次已保存 ToolJob 完整输出。模型不能直接提供 actual 值；允许字段引用或完整对象引用，两者不能混用。
- 新 `AgentCaseAssessmentService` 逐一核对项目、运行、条目、attempt、会话、turn、call、工具身份及 ToolJob 终态，读取持久化结果；不使用前端摘要、计划示例或模型伪造值。缺失路径、外部运行、未结束工作、遗漏/重复项明确拒绝。null 可作为确定性检查值；必要依赖是否接受 null 仍由运行的既有必要输出契约决定。
- 复用现有 VerificationService 的比较算子。语义检查保留 verdict、理由和实际观察引用，标为 AI 判断，不声称由确定性程序验证业务含义。无检查不能证明通过；固定用例验证点覆盖是否完整仍依赖模型读取全部内容，服务不从任意 JSON 自动推断所有业务断言。准备状态与正式目标独立保存，清理失败使汇总失败并保留目标状态；完整工具记录从数据库重建。
- ToolExecutionContext 统一提供服务端运行身份元数据，普通执行、图审批、治理执行/审批路径同步使用。工具参数中的项目 ID 不能改变归属。没有将历史 ToolJob 自动重写为新身份，也没有将整份业务上下文塞入元数据。
- 提示词仅在 `case_assessment_requested=True` 时加入回执契约。独立只读判定入口不给模型工具；请求可以只包含指定执行项，保持其完整预期和可信工具输出，最终合并后仍须核对所有执行项。解析器核对调用成功、工具调用及输出结束原因，只解析完整 JSON；拒绝截断，不修补模型输出。

确认并修复的产品缺陷：实际 HTTP 响应中的数字 0 被旧 Python 比较判为满足 JSON false。先保存失败回归 `p2-agent-assessment-boolean-before.xml`，再让 equals/not_equals 及列表 contains/not_contains 使用递归 JSON 相等判断；布尔值与数字不同，正常 0/0.0 保持数值相等。新增嵌套对象/数组回归，未顺手改变数值大小比较等无关行为。

实际验证：

- 测试生成含中文预期、false、0、null 和语义状态的 JSON 文件，实际上传原件并逐字节读取；内存与真实 RustFS/PostgreSQL 场景分别验证。经现有草稿/评审/启用、固定规划/运行服务领取并启动条目，再由真实 ToolRuntime 调用已登记 API runner，发送 POST → GET → DELETE 到受控 HTTP 服务。GET/DELETE 的 ID 从已经保存的 POST ToolJob 重新读取；不是计划中的样例 ID。创建、查询、删除的顺序由测试安排，不能称为 Agent 自主规划和执行业务。
- PostgreSQL 场景使用 UUID 隔离业务表和项目，核对完整工具输出、回执到结果记录、准备与正式输出分开保存、终态不能再次领取；仅清理本次测试资源。模型/规划测试 fixture 的受控回复是隔离测试手段，不作为真实模型成功证据。治理与图审批专项验证待批任务归属，没有完成真实审批后继续业务的联调。
- 相关回归 **187 通过、20 跳过、2 个独立实际模型项未选择，35.07 秒**，`p2-agent-assessment-regression.xml`。20 项跳过均因可选 `deepagents` 未安装；保留原 Starlette/AnyIO 弃用警告。随后新增真实 length 样本回归，最终本步骤专项 **40 通过、1 个实际模型项未选择，18.64 秒**，`p2-agent-assessment-storage.xml`，无跳过。未对仅新增测试再次扩大回归范围。
- 新分项/响应解析专项实现前为 **6 失败**，`p2-agent-assessment-split-before.xml`；实现后 6 通过。中间测试将用例标题与完整正文混为重复内容，修正为核对完整预期对象仅一次，不据此描述产品缺陷。旧 storage XML 中的失败来自审批 fixture 误走完整图入口，改为直接调用已有审批解析路径；原文件保存在 `p2-agent-assessment-storage-fixture-before.xml`，最终通过记录另存。
- Python py_compile 实际通过；本步骤没有前端契约改动，未重复前端构建或浏览器验收。

真实模型验证**尚未通过**，失败证据保留：

1. 整份回执出现错误实际输出路径、错误预期来源路径，以及 HTTP 错误/读超时，分别记录在 `p2-agent-assessment-model-before.xml`、`-model-source-error.xml`、`-model-http-error.xml`、`-model-read-timeout.xml`。错误引用安全样本用于回归；不能将模型自己提供的工具状态当作固定预期。传输等待约 180 秒的原因尚未实证定位，不能直接认定是 SDK 重试。
2. 改用现有流式 ModelRuntime 后，整份回执仍返回不完整 JSON：**1 失败，116.29 秒**，原记录另存 `p2-agent-assessment-model-truncated-before.xml`。当时未保存输出结束原因，不能追认截断必然来自预算。
3. 不再重复整份生成路线，改为逐项只读判定并采集实际结束原因/用量。默认 MySQL 配置的 step-5-preview 输出预算为 8192；准备项返回完整回执、finish_reason=stop、6690 completion tokens。目标项返回 finish_reason=length、8192 completion tokens、正文 0 字符，测试 **1 失败、39 未选择，233.17 秒**，`p2-agent-assessment-model.xml`。安全诊断见 `p2-agent-assessment-model-diagnostics.json`，不可变回归样本另存 `p2-agent-assessment-model-length-sample.json`。供应方报告 reasoning_tokens=0，不能据此推测没有正文时的内部思考内容。本轮未自动修改模型配置、提高预算或继续重试。

这一实际模型测试仅让模型核验已执行的 HTTP 工具证据；即使将来通过，也不能代替自主工具执行验收。当前没有成功模型报告 `p2-agent-assessment-model.json`。核验失败不重执行业务，不保存通过结果。配置仍归 MySQL，业务记录归 PostgreSQL，原件归 RustFS；没有新增依赖、可编辑配置、SQL 列或数据库迁移。

交付检查仅去掉三个历史 JUnit 文件中空错误行的尾随空格，失败消息、计数和时间保持；没有修改失败结论。方案本地链接存在、Codex 镜像字节一致、Git diff 检查实际通过。

```powershell
# Agent_Server；相关回归
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_assessment.py tests/test_agent_case_context.py tests/test_prompt_assembly_service.py tests/test_tool_executor_safety_gate.py tests/test_tool_runtime_heartbeat.py tests/test_deep_agent_tool_replay.py tests/test_deep_agent_runtime_adapter.py tests/test_case_execution_adapter.py tests/test_case_execution_verification.py tests/test_case_execution_service.py tests/test_run_item_dependencies.py tests/test_case_plan_run_creation.py tests/test_api_tester_free_text_assertions.py -q --tb=short -rs -k 'not actual_model' --junitxml=../docs/upgrade_2026-10-03/p2-agent-assessment-regression.xml
# 187 passed, 20 skipped, 2 deselected in 35.07s
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_assessment.py -q --tb=short -k 'not actual_model' --junitxml=../docs/upgrade_2026-10-03/p2-agent-assessment-storage.xml
# 40 passed, 1 deselected in 18.64s
$env:RUN_LIVE_AGENT_CASE_ASSESSMENT_MODEL='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_assessment.py -q -s --tb=short -k actual_model --junitxml=../docs/upgrade_2026-10-03/p2-agent-assessment-model.xml
# 1 failed, 39 deselected in 233.17s；之后仅新增捕获样本回归
```

方案同步该结果及限制，上传版本仍独立于 Git，生成模板仍不进入执行。只提交相关源文件、测试和安全证据；用户原五条 .gitignore 规则保留未暂存，不推送、不建 PR。完整目标未完成：接下来继续研究并接通 Agent 执行适配器与现有 Session/Runtime/受控工具、租约、检查点、审批和未知副作用恢复、新运行回归及公开启动入口；模型回执预算兼容性另需真实验收。生产 lifespan、人工 DeepSeek 录制、Docker、模块化生成、P3—P5 与其余业务记录迁移继续保留待完成。

### P2b 冻结用例内部 Session 入口、图审批续执行与快照重放（2026-10-04）

先研究现有 TestRunExecutionService、旧 CaseExecutionAdapter、main.py 初始化顺序、SessionService 的持久租约/审批/终结、RuntimeService/图/提示词、工具权限及本地 Agent 轨迹课程。确认生产运行服务早于 Session/Runtime 初始化；旧执行器的单工具结果与多工具 Agent 回执也不同，不能将新 JSON 转为旧模式参数来完成接入。本步骤先建立服务器内部 Session 能力，未加入生产 Worker 路由。

依据 → 实现：

- 公开 SendMessageRequest 的 context 原本可以携带运行、条目、attempt 和冻结执行上下文，而 ToolJob 会读取这些字段归属证据。实际输入测试复现后，在既有上下文清洗处剥离这些字段及检查点，记录字段名和会话身份；伪造 source 也不能绕过。显式自由测试执行依据仍保留。
- SessionService.execute_case_turn 仅供服务器调用。调用方必须核对 RunItem 租约，并从已保存固定版本构造上下文。入口核对正式项目、非空 attempt，用 run/item/attempt 生成稳定 turn，复用既有会话锁、持久租约/心跳、事件、Runtime/图、权限和终结，正常终结之后返回深拷贝快照。内部入口确定性选择现有模式，不再为已经冻结的目标重复进行语义意图模型请求。
- 同身份返回最新持久快照；等待审批、完成或未知阶段均不自动另起 turn。改变上下文或模式明确拒绝。真实审批续执行测试复现了首次实现返回最旧快照的问题，改为最新优先；保留失败证据 `p2-agent-session-approval-before.xml`。
- 按用例 API agent/worker 提示词改为阅读完整固定内容，按实际前序工具输出执行必要操作并报告证据，避免旧自由测试提示词要求重新建立整套 campaign。自由测试/旧请求保留原契约。其他模式的完整执行验收尚未进行。
- 测试通过原图权限流程完成三次审批并续执行，不设权限直通。会话租约丢失或数据库无法续租时，沿现有路径取消运行、等待清理，不发布完成事件或成功快照；保留租约供既有恢复机制处理。

功能验证：

- 生成带真实本地 URL/方法、中文预期、false/0/null 的完整 JSON 文件，实际 RustFS 上传并逐字节读回。使用既有隔离项目及 PG 草稿、评审、启用、规划、套件/运行/原子领取服务；测试显式调用内部 Session 入口。
- 图内受控模型读取冻结上下文中的方法/URL，从实际前序工具消息取得 ID，经真实权限审批调用已登记 API runner，向本地 HTTP 服务发送 POST→GET→DELETE。模型判断和最终回执受控，不属于外部默认模型推理验收；本地服务提供固定响应，用于验证调用、传值和证据链，不证明实际业务订单状态变更。
- 两次并发调用只首次进入模型；等待每次审批和终态时重复调用均返回最新快照，不新增 HTTP/模型调用。实际 PG 工具任务、消息、审批、快照重读核对 run/item/attempt/session/turn 身份及输出。使用 UUID 隔离测试表和项目，只清理本次创建的数据。
- 回执核验重新读取已保存工具证据，覆盖 HTTP 状态、false、0、null 与受控语义判断；准备和目标分开。最终专项将核验结果交给实际 RunStore 完成并重读结果和证据，验证终态不可再次领取。该完成调用仍由测试负责，生产执行协调尚未接入。
- 最初公开身份伪造和缺少入口测试 **2 失败、3 跳过**，`p2-agent-session-before.xml`。审批重放首次 **1 失败、1 通过、3 跳过**。扩大实际存储测试时 **146 通过、2 失败、2 跳过、1 未选择**，`p2-agent-session-fixture-before.xml`；失败来自 Session fixture 外键误指生产项目表，改为引用同一 fixture 的实际隔离项目表，未更改产品数据库结构。
- 最终扩大相关回归 **251 通过、2 跳过、1 个独立实际模型项未选择，57.65 秒**，`p2-agent-session-regression.xml`。两项跳过为要求 PG 事务/跨进程语义的内存变体，其实际 PG 变体已运行。旧执行服务/适配器/验证/API 会话/上下文相关回归 **72 通过、1 个独立实际模型项未选择，9.33 秒**，`p2-agent-session-legacy-regression.xml`。
- 补充 Session 测试内的结果保存与重读后，重新运行 Session 和回执专项 **48 通过、1 个实际模型项未选择，23.75 秒**，`p2-agent-session-storage-final.xml`；没有新失败或跳过。此前 **43 通过、1 未选择，22.36 秒** 的存储专项保留为 `p2-agent-session-storage.xml`。

最终专项命令（在 Agent_Server）：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_session.py tests/test_agent_case_assessment.py -q --tb=short -rs -k 'not actual_model' --junitxml=../docs/upgrade_2026-10-03/p2-agent-session-storage-final.xml
```

本步骤没有新增依赖、可编辑配置、数据库迁移、公开路由或前端契约；不重复前端构建。可编辑配置仍归 MySQL，业务/运行/工具/会话记录归 PostgreSQL，原件归 RustFS。只读检查现有模型元数据：仅 step-5-preview 启用，输出预算 8192；没有新增外部模型调用或配置写入。历史默认模型回执失败结论保持，受控模型测试不能代替这一验收。

方案更新内部已验证能力和生产缺口，并同步选定 Codex 计划；附件保留原样。快照重放尚不能处理快照丢失/被清理、Worker 重启或跨 attempt 的未知副作用，也不提供运行条目审批等待策略。下一步先设计并验证持久执行意图和检查点、租约及资源心跳、审批等待和恢复证据归属，再接入 AgentCaseExecutionAdapter/TestRunExecutionService；最后验证默认模型、回归新运行及公开入口。完整 P0—P5 目标仍未完成。

### 完整方案再次同步及工作区执行接入核对（2026-10-04）

本次响应用户“那不更新这个方案？”：更新 PLAN.md 全文中的当前状态、接口、执行恢复和验收安排，并同步所选 Codex PLAN.md；粘贴附件作为原始需求保留。此次只修改两份仓库文档及外部计划镜像，不新增或提交业务代码。上节的“未接入适配器/运行条目审批”为此前步骤状态，当前情况以本节及顶部追踪表为准。

已重新读取两份用户附件、当前完整方案、上传实现/目录模型、AgentCaseExecutionAdapter、运行执行服务、Session/Runtime 审批路径、生产初始化、前端审批消费方，并解析现有测试证据。上传实际链路仍为上传服务 → 对象存储 → PostgreSQL 目录，无 Git 调用；部分文件先转换为 Markdown 再保存，稳定文档身份及 V1/V2 关联仍未实现。方案继续要求先保存原件、独立上传版本和快照，Git 来源单独接入，不把上传基础测试当作新功能完成。

工作区业务改动的实际范围（此次未提交）：

- Agent 适配器先核对当前运行/条目/attempt 租约和固定版本，按完整上下文计算哈希，持久保存 admitted/running 意图与 snapshot 引用。专属内部会话保留正式来源会话的项目、模式、模型偏好及服务端环境信息。
- 现有执行服务分流 agent 策略，既有审批接口同步返回下一项等待或最终结果；Session 的公开审批不能绕过 RunItem 协调。内存与 PG 持久 turn 租约允许对确切 pending approval 进行受控续执行。前端类型、项目接口、会话 store 和提示同步消费两种回执及两种运行审批来源。
- 原 attempt 的完整工具和快照证据用于恢复，新结果记录当前 attempt 并保留原执行 attempt。已记录执行意图却没有快照、工具效果未知或图未正常终结时明确要求核对，不盲目重做。
- 实际复现了 POST 成功、后续审批快照保存前中断导致恢复再次 POST，失败证据为 `p2-agent-adapter-http-replay-before.xml`（2 失败）。Runtime 续执行现在核对原 ToolJob 身份和精确参数，终态时重建已有记录，只有 waiting_approval 才首次执行。最终专项使用实际父任务取消、持久租约到期及 RunItem 恢复验证，不通过测试内强行改写租约冒充故障。

已核对的现有验证记录（本次方案同步未重新执行）：

- `p2-agent-worker-regression-final.xml`：342 项中 **340 通过、2 跳过**，JUnit 计时 116.503 秒；此前执行命令另有 1 个 actual_model 项未选择。跳过为内存变体的 PG 事务排序和跨进程事件交付，对应 PG 变体已执行。新适配器测试覆盖真实原件/RustFS/PG、图审批、本地 HTTP、现有运行条目 HTTP 路由、终态重放及未知任务拒绝重复；模型和回执受控，本地 HTTP 服务提供固定响应，不证明真实订单状态创建/删除。
- `p2-agent-worker-frontend.txt`：四个会话 store 套件 **18 通过**；`p2-agent-worker-frontend-build.txt`：vue-tsc 类型检查和 Vite 构建通过。新审批来源首次测试曾失败（4 通过、1 失败，记录在此前工具输出），修复消费方后通过。
- Dispatcher 专项直接调用现有 handle，queue=None，未启动真实 Redis broker/Worker 进程。lifespan 专项使用真实 PG/MySQL 池但替换业务初始化，不代表完整生产启动。默认模型历史 length/空正文失败没有被受控模型结果替代，本次没有新外部模型请求或配置写入。
- 历史失败证据保留；其中 `p2-agent-worker-regression.xml` 的单项失败来自固定等待尚未达到 PG 租约到期，改用有界观察实际租约/回收器。`p2-agent-worker-parent-fixture-before.xml` 的两项失败来自父会话 fixture 未接入 SessionStore，修正测试接线后最终回归通过，不将它们描述为产品缺陷。

本次实际重新运行上传相关测试（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py -q --tb=short
# 25 passed in 1.18s
```

该结果验证既有上传/目录/存储接口行为，不验证新上传版本、解析快照或真实 V1/V2 联调。另检查方案本地链接、用户关键约定、镜像字节一致和文档 diff；本次未重新执行前端构建、接入回归、生产启动、浏览器、Docker 或真实模型验收。仅暂存及提交 PLAN.md 与 IMPLEMENTATION.md，业务文件、现有测试证据和用户 .gitignore 改动保持原暂存状态，不自动推送或创建 PR。完整 P0—P5 目标继续保持未完成。

### P2b 运行服务接入交付、资源身份及已保存审批决定恢复（2026-10-04）

上一轮仅更新方案，属于进展；本轮继续完整 P0—P5 目标。重新读取指定方案附件及实际工作区，审查 Agent 适配器、运行执行协调、Session 持久 turn 租约、Runtime 审批、ToolJob/回执核验、Redis 资源与浏览器/Docker 创建者、前端审批消费者和本地《Agent测试专项》《Agent轨迹与协议鲁棒性》。本步骤交付此前未提交的 Agent 策略接入，并先复现、再修复审查发现的问题。

研究依据 → 实现：

- 既有 BrowserResourceManager/DockerResourceManager 从 RequestContext 获取归属，负责实际资源创建、绑定、续租和清理；外层资源列表没有图内消费者。新 Agent 策略仅领取条目的 agent 配额，不读取旧 `test_data.runner_arguments` 预占账号、环境或 Docker。旧策略的资源处理保留。
- HTTP 执行/审批入口未提供图内实际运行身份，而 Redis Worker 传入的可能是父会话或当前恢复 attempt；ToolJob 则已有固定服务端身份。Agent 适配器首次执行和审批续执行使用现有 bind_request_context，绑定项目、run/item、原执行 attempt、内部 session/turn 与当前 worker。资源归属与工具证据对应，不把租约列表或令牌加入冻结业务内容。
- SessionStore 已以持久状态区分 pending/approved/denied，并保持已决定事项的原备注；恢复的图快照可以仍在 waiting_approval。适配器按原 pending 顺序读取决定，未决定时返回等待，已批准/拒绝时在运行条目及 turn 租约下续执行原图，保留原决定与备注，直到新的 pending 或终态。未推进同一决定的异常明确要求核对，不形成无限重试。
- 原执行 intent、context hash、turn 和快照身份继续用于恢复；新结果区分原 `execution_attempt_id` 与当前 `result_attempt_id`。终态 ToolJob 重建已有记录、未知任务拒绝重发的规则继续使用。没有将恢复后的 attempt 当作新业务操作，也没有通过模式参数编译任意用例 JSON。
- 后端既有运行条目 execute/approval 路由、RunItem 等待状态、Session 同步续执行、前端响应 union 和两种运行审批来源成对交付；新规划公开启动入口仍待接入。新增类型化检查点复用既有 attempt JSONB，无数据库迁移或新配置域。

先复现的证据：

1. 新 Agent 用例携带旧模式 runner_arguments，真实隔离 Redis 中错误领取了 agent、docker、environment、test_account 四种资源。
2. 用现有 API 图/审批/真实本地 HTTP 执行，工具回调实际领取 Redis environment 逻辑租约；外层刻意带父/错误运行关联时，持久资源中的项目、run/item、attempt 与实际 ToolJob 不一致。首轮断言还误包含 selected_agent/model 字段，表现为测试 KeyError；修正只核对正式身份后重新运行，`p2-agent-resource-identity-before.xml` **1 失败、24 未选择，8.39 秒**，清楚复现产品身份继承缺陷。
3. 从真实运行条目审批入口保存决定和事件后，实际取消父任务，观察持久 turn 租约到期/回收器，再恢复运行、创建恢复 attempt 并走普通 execute；批准和拒绝的内存/实际 PG 四种场景都仍返回原已决定审批，没有继续原图。此前复现合集 `p2-agent-recovery-resource-before.xml` **6 失败、19 未选择，74.29 秒**，其中资源身份项当时是上述测试断言错误，不能将六项全部宣称产品缺陷。

实现后专项 `p2-agent-recovery-resource.xml`：**6 通过、19 未选择，72.68 秒**。资源两项使用真实 Redis；图资源项同时使用实际 PostgreSQL/RustFS、完整固定 JSON、受控模型、三次权限审批及真实 POST/GET/DELETE。回调补充的是测试的逻辑 environment 资源，不是实际容器或测试环境创建，不能据此宣称全部模式的物理资源验收通过。原有“POST 成功、快照滞后”取消样本改为普通 adapter.execute 自动消费已保存决定，后续扩大回归验证它不重发 POST；未知效果及缺快照保持需核对状态。

实际验证及交付范围：

- 扩大后端回归 **359 通过、10 跳过、1 个 actual_model 未选择，189.38 秒**，`p2-agent-integration-regression.xml`。覆盖 Agent 适配器/Session/回执、旧执行与验证、会话所有权/事件/队列、权限安全、Dispatcher/lifespan、资源领取与管理、浏览器 Harness。2 个内存变体因 PG 事务/跨进程语义跳过，对应 PG 变体已执行；8 项既有真实资源测试因开关未开启跳过，随后单独启用并补验。
- 既有真实资源准入专项 `p2-agent-existing-resource-live.xml`：**8 通过，13.20 秒**。实际 Redis/PG、无头 Chromium eval/close、配额/并发/续租失败/重复取消与清理通过。此专项沿用既有测试适配器，不是新 Agent UI 模式验收；没有访问 DeepSeek，也不算人工 UI 录制完成。
- 前端四个会话 store 套件 **18 通过（1.74 秒）**；npm run build 内含 vue-tsc 类型检查，Vite 构建通过（6.61 秒），完整构建输出为 `p2-agent-integration-frontend-build.txt`。新审批来源分流、刷新下一项待批以及旧来源兼容覆盖在 sessionApprovalLifecycle 中。没有浏览器产品页面端到端验收。
- Python py_compile 对本步骤 15 个 Python 源/测试文件实际通过。只去除历史失败 XML 空行尾随空格，失败数量和结论保留；新增测试精确加入 .gitignore 白名单，用户的五条忽略项保持未暂存。敏感值模式扫描未命中，文档链接、镜像一致和 diff 另行检查。

命令（Agent_Server）：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_execution.py tests/test_agent_case_session.py tests/test_agent_case_assessment.py tests/test_case_execution_service.py tests/test_case_execution_adapter.py tests/test_case_execution_verification.py tests/test_session_execution_ownership.py tests/test_session_event_delivery.py tests/test_runtime_interrupt_tool_calls.py tests/test_security_approval_boundary.py tests/test_busy_session_queue_concurrency.py tests/test_intent_safety_routing.py tests/test_redis_execution_dispatcher.py tests/test_lifespan_resource_cleanup.py tests/test_execution_resource_waiting.py tests/test_resource_lease_manager.py tests/test_browser_resource_harness.py tests/test_live_task_admission.py -q --tb=short -rs -k 'not actual_model' --junitxml=../docs/upgrade_2026-10-03/p2-agent-integration-regression.xml
$env:RUN_LIVE_BROWSER_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_live_task_admission.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p2-agent-existing-resource-live.xml
```

前端命令（agent_web）：

```powershell
npm run test -- src/stores/sessionApprovalLifecycle.test.ts src/stores/sessionReceiptDedup.test.ts src/stores/sessionEventRefresh.test.ts src/stores/sessionStreaming.test.ts
npm run build
```

配置继续归 MySQL，业务/检查点/工具/会话归 PostgreSQL，原件归 RustFS，Redis 只管理交付和租约。未新增依赖、持久配置写入或外部模型请求；历史默认模型目标回执 length/空正文失败继续保留。代码、测试和安全证据随本步骤提交，完整 P0—P5 仍未完成：接下来研究新策略回归与公开计划启动，继续实际 Redis 执行 Worker/完整 lifespan、各模式自主工具/资源、步骤级恢复/未知业务效果及持久规划任务；模块化生成、人工录制、Docker 结果、P3—P5 和其余业务记录迁移仍待完成。

### 根据现有代码再次更新完整方案（2026-10-04）

响应用户“那不更新这个方案？”，本次更新 PLAN.md 并同步所选 Codex 计划，附件保留为原始输入。重新读取两份附件、现行方案及最新实施记录，以 `c7e0500` 为代码基线核对上传、对象存储、目录模型、计划运行服务、公开规划路由、失败回归创建和运行模型。只修改方案与本记录，不实施业务代码或数据库迁移。

方案顶部明确此前 Agent 接入已提交，并在 §2.5 增加当前代码缺口、采用做法和验收条件：内部计划运行事务服务尚未通过公开路由启动；现有 create_regression 没有填写 Agent 策略和计划快照，条目也未携带计划身份及依赖，运行模型默认策略为 legacy。后一项为静态调用链核对结果，本轮未使用实际 Agent 父运行复现或修复。后续实施须先复现，再保存新回归固定计划、必要前置、实际数据绑定及来源结果；新启用版本不能仅替换版本 ID 后保留旧绑定。

上传设计继续按 §3.1 独立实施：原件和工件存 RustFS，版本及解析快照业务记录存 PostgreSQL，解析器等可编辑配置存 MySQL；上传、保存历史和比较均无需 Git。当前 ApiDocsService 仍先转换部分文件再保存，目录没有正式版本关联，新版原件链和差异能力仍待实现。现有上传可行性测试不能代替真实 V1/V2 验收。

本轮在 Agent_Server 实际执行上传相关既有测试：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py -q --tb=short
# 25 passed in 1.07s
```

另检查方案本地链接、核心约定、镜像字节一致性和文档 diff。本轮未重跑前端构建、Agent 执行集成、实际模型、真实 RustFS/PostgreSQL V1/V2、Worker 或完整生产启动；没有相关产品代码改动，既有验证结果沿用上节证据，不能记为本轮重新验收。提交范围仅 PLAN.md 与 IMPLEMENTATION.md，用户 .gitignore 改动保留未暂存；不推送、不创建 PR。完整 P0—P5 目标仍未完成。

### P2b Agent 失败回归保留固定计划及重新执行前置（2026-10-04）

上一轮方案提交 `4625d6c` 属于进展，本轮继续完整 P0—P5 目标。重新读取指定附件、当前方案、回归创建与运行模型、固定计划映射、计划运行事务、规划来源/服务、生产初始化、前端类型、既有回归与 Agent 图测试，以及本地《Agent轨迹与协议鲁棒性》的轨迹、重复执行和幂等说明。问题属于回归创建与新执行策略的契约缺失，不能只给原回归加一个策略字段而丢失前置及实际输出。

先复现：通过项目所属保存计划创建 Agent 父运行，使用既有领取和结果保存服务产生失败项，调用原 create_regression；内存与实际 PostgreSQL 均得到 legacy 新运行。`p2-agent-regression-before.xml` 为 **2 失败、17 未选择，4.19 秒**。该复现的业务结果由测试构造，用于证明真实存储与回归服务的策略丢失；后续状态型 HTTP 专项另证明真实工具操作及目标失败。

依据 → 实现：

- 新 AgentRegressionService 读取父运行固定计划及来源失败结果，无版本替换时保留选中项的声明依赖祖先和相关后置准备，重新分配准备归属；不复制旧运行产生的临时数据，不重跑无关目标。清理的等待边可随本轮范围裁剪，操作说明和数据来源不改写；省略分支仍被清理数据引用时交给现有规划服务判断。
- 明确指定新启用版本时，复用 CasePlanningService，提供完整固定版本、原计划及失败验证信息；服务端指定必须保留的目标和版本，模型遗漏目标时收到校验反馈后修正。未指定替换的选中目标仍读取原版本，即使该用例已启用更新版本。新 Prompt 为 case-planning-v4，旧计划不重写。
- RegressionPlanSource 存父运行、来源计划、目标结果映射及明确版本替换，加入既有 CasePlanExecutionSnapshot JSONB。计划映射按同一来源重建 suite/run/items，并把来源加入创建哈希；原始与回归运行都经过完整映射一致性校验，不绕过原事务验证。
- 内存存储在现有锁内核对来源，PostgreSQL 事务重新核对项目、父运行终态、结果所属和版本；替换版本提交前须仍为启用版本。套件、运行、条目和 Outbox 同事务；固定计划先独立保存，因此运行事务失败可能保留没有运行的计划，但不会留下半套运行或任务。回归创建仍沿用每次请求创建一轮的既有语义，没有新增跨请求幂等参数。
- TestRunService 按父运行策略分流，保留旧回归行为；缺少 Agent 回归服务时明确报错，不降级 legacy。main 注入计划运行及 Agent 回归服务，前端类型同步可选来源字段；保存计划公开启动路由及按钮仍待接入。

实际功能证据：

- 计划专项最初 **4 通过、17 未选择，4.54 秒**，`p2-agent-regression-plan.xml`。覆盖真实 MySQL 配置、PG 用例/计划/运行和实际文件/存储 fixture；只有创建、领取及输出记录的测试数据受控，没有执行业务工具。
- HTTP 专项最初 **2 通过、25 未选择，10.37 秒**，`p2-agent-regression-http.xml`。新本地服务实际保存和删除订单，每次创建分配不同 ID，第一轮查询 count=1 与完整冻结 JSON 的预期 0 不符，回执核验据真实输出保存 failed；第二轮新回归重新创建数据，返回 count=0，保存 passed。实际执行项目原件上传/读回、Session/图、三个权限审批、POST→GET→DELETE、运行及回归 HTTP 路由、结果/证据重读和终态不重领。原失败 RunDetail 逐项保持原状，模型决策与语义回执受控，不能证明外部默认模型自主执行。
- 加入实际 PostgreSQL 中断后，功能专项 **6 通过、42 未选择，11.41 秒**，`p2-agent-regression-functional.xml`；后续补充模型遗漏回归目标的反馈修正及来源记录精简，最终专项 **6 通过、42 未选择，11.67 秒**，`p2-agent-regression-functional-final.xml`。中断发生在真实 suite/run/items 插入之后，核对套件、运行、条目和 Outbox 数量全部回滚，再成功创建并执行新回归。原计划及失败记录保留。
- 扩大后端回归 **258 通过、4 跳过、5 未选择，187.69 秒**，`p2-agent-regression-integration.xml`。4 项为规划/路径专项的真实存储开关未打开；随后启用正确开关，连同最新回归及版本提交前校验补验 **29 通过、51 未选择，93.67 秒**，`p2-agent-regression-storage-final.xml`，无失败或跳过。第一次扩大命令误引用不存在的 test_regression_api_frontend_contract.py，未执行任何测试；核对实际文件清单后修正命令，没有将该次误调用计为产品失败或通过。
- 扩大测试的 5 个未选择项包含 4 个外部模型专项及一个历史真实模型输出的本地解析样本。该本地样本随后单独执行 **1 通过，2.30 秒**，未发起模型请求。历史默认模型 length/空正文失败保持，未修改其 MySQL 配置或重试相同外部请求。
- 前端 CasePlanDialog 和四个 Session store 套件 **21 通过，1.91 秒**；npm run build 的 vue-tsc 类型检查及 Vite 构建通过（6.87 秒），`p2-agent-regression-frontend-build.txt`。11 个 Python 源/测试文件 py_compile 实际通过。没有产品页面浏览器验收或完整生产 lifespan/Worker 进程验收。

后端扩大命令（Agent_Server）：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_plan_run_creation.py tests/test_case_planning.py tests/test_plan_binding_paths.py tests/test_run_item_dependencies.py tests/test_agent_case_execution.py tests/test_agent_case_session.py tests/test_agent_case_assessment.py tests/test_agent_case_context.py tests/test_test_run_regression.py tests/test_test_run_lifecycle.py tests/test_case_execution_service.py tests/test_session_execution_ownership.py tests/test_runtime_interrupt_tool_calls.py tests/test_project_regression_frontend_contract.py tests/test_lifespan_resource_cleanup.py -q --tb=short -rs -k 'not actual_model' --junitxml=../docs/upgrade_2026-10-03/p2-agent-regression-integration.xml
```

最新存储补验：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_plan_run_creation.py tests/test_agent_case_execution.py tests/test_case_planning.py tests/test_plan_binding_paths.py -q --tb=short -rs -k 'agent_regression or True' --junitxml=../docs/upgrade_2026-10-03/p2-agent-regression-storage-final.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py::test_actual_model_json_with_trailing_text_keeps_preparation_and_fixed_references -q --tb=short
```

前端（agent_web）：

```powershell
npm run test -- src/features/projects/CasePlanDialog.test.ts src/stores/sessionApprovalLifecycle.test.ts src/stores/sessionReceiptDedup.test.ts src/stores/sessionEventRefresh.test.ts src/stores/sessionStreaming.test.ts
npm run build
```

本步骤不新增依赖、配置域或数据库列；配置仍在 MySQL，计划/运行/业务证据在 PostgreSQL，原件沿用 RustFS。源码、测试、实际证据和方案同步提交，用户五条 .gitignore 改动不暂存。下一步接通公开保存计划启动并验收，再继续持久规划任务、真实 Redis Worker/完整生产启动、默认模型及其他模式工具、步骤级恢复和完整 P0—P5 的剩余功能。该步骤通过不代表整个升级完成。

### P2b 公开保存计划启动、响应丢失重试及真实 Redis 执行（2026-10-04）

上一轮方案提交 `3e4f161` 属于进展。本轮重新读取指定方案附件、当前源码与测试、本地《Agent轨迹与协议鲁棒性》《评测工程化与CICD》，沿项目规划路由、固定计划映射、创建事务、模式注册、项目页弹窗、运行详情/审批、Outbox relay 和 Redis Worker 调研。确认保存计划已在 PostgreSQL，启动服务不需要再次调用规划模型。因此修正方案中的前置安排：公开保存计划启动可独立接通，耗时规划请求的持久化继续作为剩余交付项，不能把它当作已实现。

先复现：新增启动请求测试，旧代码返回 404，不能接收正式启动参数。`p2-public-launch-before.xml` 为 **1 失败、1 跳过、21 未选择，3.99 秒**；跳过为真实存储开关未启用。修复不是另建执行引擎，而是将已有的冻结事务服务接到公开入口。

依据 → 实现：

- `POST /projects/{project_id}/case-plans/{plan_id}/runs` 复用 `CasePlanRunService.create`，返回正式 `TestRunDetail`。请求只包含创建请求身份、注册测试模式及可选同项目会话；额外冻结内容被 schema 拒绝。新创建要求配置持久派发和模式注册服务，生产初始化注入已有注册表；代码审查仍可选，不按生成的 `exempt` 策略或用例历史模式过滤执行。
- 已提交请求按既有固定 UUID/创建哈希返回原运行，先重放再检查新建所需派发配置；派发暂时停用后仍可确认已保存或终态运行。参数冲突明确返回 409，额外字段返回 422。提交异常返回“结果尚不能确认，保留相同请求重试”，不把响应丢失说成事务未提交。结构化日志带项目、计划、请求身份和模式。
- 前端增加模式、可选会话、启动、重试、查看原运行及明确另建操作。按项目/计划在 `sessionStorage` 保存创建请求和运行身份，不保存用例正文、运行数据或凭据；未知结果保留原参数和身份，关闭/重开组件或刷新后重新查看计划仍使用相同请求。明确的输入拒绝允许修正；浏览器存储失败显示可见提示，当前组件保留原请求。查看缓存运行通过真实 API 重读，并核对项目和计划归属。`HttpError` 保留 HTTP 状态，仍兼容既有 `Error` 消费方。
- `ProjectsView` 传入全部注册模式，保存成功后打开现有运行详情和审批操作，并刷新运行及概览。界面明确计划生成、运行保存及实际结果各自含义，未知启动结果不显示“测试尚未开始”。没有新增模式参数编译、配置域、数据库列或依赖；业务记录继续在 PostgreSQL，已有可编辑策略继续在 MySQL。

实际功能与回归证据：

| 验证 | 实际结果 | 范围 |
|---|---|---|
| 公开 API 初验 | 1 通过、1 跳过、21 未选择，2.65 秒；`p2-public-launch-api.xml` | 内存运行/计划服务，经实际 ASGI 路由；未计为真实存储验收 |
| 固定计划及公开启动实际存储 | 23 通过，12.46 秒；`p2-public-launch-storage.xml` | 隔离 MySQL/PG/RustFS；项目/会话/模式拒绝、额外注入、提交后响应丢失、派发停用时重放、另建、并发、冻结版本及事务回滚；会话拒绝样本使用受控会话读取，真实 Session 执行见 Worker 专项 |
| 实际 Chromium 组件与 HTTP | 最终 1 通过，6.49 秒；`p2-public-launch-browser-final.xml`、`p2-public-launch-browser.json/png` | 创建真实需求文件并上传 RustFS，PG 用例/计划及 MySQL 策略；真实启动路由提交后通过浏览器截断响应，刷新后重试同一请求，Outbox 仍 1 条；明确另建后为 2 条，再刷新并查看真实运行。模型规划受控，宿主页加载实际组件，不代表完整 ProjectsView 或生产 lifespan |
| 实际 Redis → Worker → Agent → HTTP | 1 通过、27 未选择，6.89 秒；`p2-public-launch-worker.xml` | 公开启动保存 PostgreSQL Outbox，实际 relay 投递 Redis，生产 `RedisTaskWorker.run_once` 调用 Dispatcher/Agent；三次运行条目审批后实际 POST 创建、GET 查询、DELETE 删除。响应 count=1 与冻结预期 0 不符，结果保存 failed；真实订单已清理。终态重投 ACK 且不增加 3 次业务请求，公开同请求重试返回原终态。使用受控模型/回执和进程内真实 Worker 类，未启动独立 CLI 或完整生产应用 |
| 扩大后端回归 | 141 通过、46 跳过，80.02 秒；`p2-public-launch-integration.xml` | 规划、路径、冻结、依赖、Agent 上下文/核验/Session/恢复、回归、Outbox、Dispatcher 和前端契约；开关关闭导致真实存储及外部模型项目跳过，随后按测试身份逐一对账补验 |
| 补验真实存储 | 32 通过、101 未选择，97.44 秒；`p2-public-launch-live-regression.xml`；另 2 通过、12 未选择，3.59 秒，`p2-public-launch-dependency-atomicity.xml` | 启用规划、路径、依赖及 Agent 存储 fixture 开关；补验 PostgreSQL blocked 结果与统计回滚、字面量路径。结合 23 项创建专项及 Worker 专项，扩大回归的 46 个跳过中有 42 个同身份测试随后通过；剩余 4 个外部模型专项本轮未执行 |
| 前端及构建 | 7 个测试文件、23 通过，1.61 秒；类型检查与构建通过，Vite 6.17 秒；`p2-public-launch-frontend-build.txt` | 模式选项、请求白名单、未知请求关闭/重开重试、项目隔离、明确拒绝、浏览器存储失败、另建及查看归属；通用设置/Session API 和四个 Session store 回归。实际项目页全流程仍待验收 |

6 个变更 Python 源码/测试文件的 `py_compile` 实际通过。对 46 个跳过按 JUnit 的 `classname/name` 与后续通过记录逐项核对，未将未选择测试算为通过。剩余 4 个外部模型专项为语义规划、字面量路径、完整上下文读取及完整回执；已有默认模型 length/空正文失败保持，本轮没有重复相同请求或修改 MySQL 模型配置。

主要实际命令（Agent_Server）：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_plan_run_creation.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-public-launch-storage.xml
$env:RUN_LIVE_CASE_PLANNING_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-public-launch-browser-final.xml
$env:RUN_LIVE_PUBLIC_PLAN_WORKER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_execution.py -k public_plan_launch_outbox -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-public-launch-worker.xml
```

扩大后端命令在未启用上述专项开关的独立 Shell 中执行：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py tests/test_plan_binding_paths.py tests/test_case_plan_run_creation.py tests/test_run_item_dependencies.py tests/test_agent_case_context.py tests/test_agent_case_assessment.py tests/test_agent_case_session.py tests/test_agent_case_execution.py tests/test_test_run_regression.py tests/test_test_run_outbox_transaction.py tests/test_redis_execution_dispatcher.py tests/test_postgres_task_outbox_relay.py tests/test_project_regression_frontend_contract.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-public-launch-integration.xml
```

真实存储补验：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py tests/test_plan_binding_paths.py tests/test_run_item_dependencies.py tests/test_agent_case_context.py tests/test_agent_case_assessment.py tests/test_agent_case_session.py tests/test_agent_case_execution.py -k True -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-public-launch-live-regression.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_item_dependencies.py -k 'blocked_receipt_and_stats_rollback or literal_output_paths_match' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-public-launch-dependency-atomicity.xml
```

前端（agent_web）：

```powershell
npm run test -- src/features/projects/CasePlanDialog.test.ts src/services/settings.api.test.ts src/services/sessions.api.test.ts src/stores/sessionApprovalLifecycle.test.ts src/stores/sessionQueue.test.ts src/stores/sessionReceiptDedup.test.ts src/stores/sessionMessageOrdering.test.ts
npm run build
```

已读取并视觉检查真实浏览器截图，方案本地链接及镜像一致性另检查。测试表、项目、对象和 Redis 流均为隔离 fixture 所有，清理只针对本次确切身份；用户 `.gitignore` 保持未暂存。源码、测试、证据及文档按本步骤提交，不推送或建 PR。

完整 P0—P5 目标继续保持未完成。下一步研究持久规划任务与生产进程接线，继续默认模型回执、其他模式工具、步骤级恢复及未知副作用核对；P0 人工 DeepSeek 录制/Docker、P1 模块生成、P3 文档版本/解析/差异、P4 影响/追踪、P5 知识/报告及业务数据迁移均按原范围保留。

### 方案同步与持久规划设计核对（2026-10-04）

用户要求更新所贴方案，本次核对代码基线 `2371d0b`，只更新 PLAN.md、本文及所选 Codex 计划镜像。原附件保留。方案开头、代码依据和下一步安排改为当前状态：公开保存计划启动已经接通，有此前实际存储/浏览器/Worker 类专项证据；独立进程和完整生产验收仍未完成，不再重复列为“待接通接口”。

重新读取规划路由、`CasePlanningService.create`、`PostgresCasePlanStore`、Embedding 任务存储与 Handler、Agent 任务身份/租约校验、Outbox relay、任务池路由和流配置，并查阅本地课程的版本化、轨迹与重试要求。确认规划当前在请求中执行，完成后才保存最终计划；现有计划 `save` 是插入式不可变存储，不能用它保存每轮进度。Agent 任务与真实 session/turn 绑定，不能伪造会话作为通用规划任务。

据此增加 PLAN.md §2.6：项目异步规划提交/查询，任务与 Outbox 同事务、原请求重放、固定输入和逐轮检查点、attempt/租约隔离、剩余预算恢复、最终计划与终态同事务，以及前端刷新找回任务。该节明确是待实施设计；新增任务存储、路由、迁移、Worker 接线和实际恢复尚未完成。模板只参与生成，执行仍按项目选取→初排→补全→最终冻结；上传、版本保存和比较独立于 Git，Git 只是可选只读来源。

实际重新执行（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py -q --tb=short
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py tests/test_case_plan_run_creation.py -q --tb=short -rs -k 'not actual_model'
```

结果为 **25 通过，1.12 秒**；**28 通过、15 跳过、2 未选择，3.92 秒**。真实存储开关未启用导致 15 个专项跳过，2 个模型相关项未选择；不计为本次真实存储或模型验收。浏览器、独立 Redis Worker、生产 lifespan、外部模型及新文档版本链本次未重验，因为本轮范围是方案更新，没有修改产品代码。文档链接、diff 和镜像一致性实际检查；只提交两份项目文档，用户 `.gitignore` 保持未暂存。完整升级目标仍未完成，下一步按 §2.6 实施持久规划，其他 P0—P5 范围继续保留。

### 持久规划任务实施与实际恢复验证（2026-10-04）

承接 `5d2f258` 的 §2.6 设计，重新核对旧同步规划、不可变计划存储、Embedding 任务/Outbox、Agent 会话命令及 Redis Worker。根因是请求内执行没有持久进度；不能把最终计划 `save` 当作检查点，也不能伪造 Session 接入会话任务。新增项目规划任务，复用既有数据库、Outbox 和 Worker；同步入口与内部回归保留，共享 `CasePlanningWorkflow`。

实际变更：

- 项目所属 `case-planning-tasks` 提交/按任务查询/按请求找回/分页列表。202 返回前只保存任务与 Outbox，不调用模型。项目行锁下复核归档状态；相同请求重放在读当前配置和派发开关前完成，不同内容使用同身份返回 409，额外字段 422。提交确认不明返回 503 并提示按原身份查证。
- `agent_case_planning_tasks` 属于 PostgreSQL 业务任务。独立 seed 固定输入、项目、Prompt、策略与非敏感模型参数；检查点保存完整候选版本、目录、逐轮响应/查询/校验。模型凭据不复制，来源 URL/请求头仅保存哈希；实际凭据按现有 MySQL 配置读取。Worker 及 ModelRuntime 实际发送前检查参数未变。
- 每次领取生成 attempt 和租约令牌，保留历史，心跳/检查点/最终写入均核对有效身份。已读依据不能改写；旧回合不能重置；终态任务不能重领。最终计划与任务终态同事务，不确定写入不改成另一份业务失败，交由原任务重查。
- 调用意图先保存，响应再保存，随后解析。只有意图没有响应记 outcome_unknown 并消耗原回合，剩余回合继续；已有响应恢复不重发。首次领取确定总期限，PG 返回剩余秒数，避免主机与数据库时钟偏差误判；重启不重置预算。
- 生产初始化已注册任务服务/表和独立 Worker CLI；示例流列表增加 `qa:tasks:case_planning`，新提交要求现有派发开关开启且流已登记。任务完成只提供计划，正式运行继续由公开保存计划启动入口创建。
- 实际规划弹窗切换到持久面板，sessionStorage 仅保存身份。当前页面内存保留原请求用于不确定提交重试；刷新按原请求找回，未提交成功的原输入需重新填写。存储故障可从项目列表找回；终态停止轮询，“另建”明确生成新身份。

验证均使用隔离项目、表、对象及 Redis 流，不执行生产迁移，不改默认外部模型配置：

| 验证 | 实际结果 | 证据与边界 |
|---|---|---|
| 新任务入口早期复现 | 1 失败：旧路由 404 | `p2-planning-task-before.xml`，保留原始失败 |
| 实际存储与故障恢复最终专项 | 12 通过，21.16 秒 | `p2-planning-task-storage-final.xml`；真实 MySQL/PG/RustFS，含并发提交、响应丢失、Outbox/最终提交回滚、旧租约拒绝、已保存响应恢复、启用新用例版本不替换已读版本、预算耗尽、模型参数变化和检查点确认丢失 |
| 独立进程恢复 | 最终 12 项专项包含此场景；此前单项 1 通过，15.68 秒 | `p2-planning-task-process.xml/json`；实际 Redis/PG/MySQL 配置、RustFS 原文与本地 OpenAI 兼容 HTTP，强制结束第二回合进程，新进程沿用原文/用例版本/期限，第三回合完成，终态重投不增加模型调用。使用生产 Worker/Handler 的隔离宿主；未运行完整生产 CLI/lifespan |
| 实际 Chromium | 1 通过，10.55 秒 | `p2-planning-task-browser.xml/json/png`；真实文件上传、三种存储、提交响应截断、刷新找回同一任务、准备和负向条件、保存计划公开启动和原运行重试。模型受控，Worker 是进程内 Handler 轮询宿主，页面是实际组件的隔离 Vite 宿主页 |
| 受影响后端扩大回归 | 161 通过、44 跳过、5 未选择，78.48 秒 | `p2-planning-task-regression-final.xml`；未启用真实存储开关，跳过不计通过；后续启用开关补验见下行 |
| 真实存储与执行相关回归 | 150 通过、6 未选择，181.31 秒 | `p2-planning-task-live-regression.xml`；规划、字面量绑定路径、计划运行事务、依赖领取、Agent 完整上下文/核验/Session/恢复。筛除外部模型及已有公开 Worker 专项，不代表默认模型或完整生产验收 |
| 前端 | 3 个文件，12 通过，1.37 秒；typecheck 与 Vite build 通过，6.52 秒 | 实际执行项目脚本；测试覆盖未知提交原请求、刷新、项目隔离、终态停轮询及存储故障。先前 disabled 表达式产生 boolean/null 导致 TS2322，显式布尔化后构建通过 |
| 源码与 CLI 入口 | 14 个 Python 文件 py_compile 通过；Worker --help 正常列出 once/worker-id | 只验证源码和参数导入，不宣称完整 CLI 实际启动通过 |

原始 `p2-planning-task-storage.xml` 保留 **11 通过、1 失败**：独立进程单次 `run_once` 返回 processed=0。单项重跑及完整专项随后通过；未取得足够证据把它归因到某个数据库或心跳缺陷。隔离宿主改为有 20 秒上限的正常轮询，因为 Worker 单次轮询允许返回 0；该修改不重置业务任务或调用额度，不修改产品重试行为。最终专项保留单独文件，不覆盖原失败。

主要命令（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-planning-task-storage-final.xml
$env:RUN_LIVE_CASE_PLANNING_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-planning-task-browser.xml
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py tests/test_plan_binding_paths.py tests/test_case_plan_run_creation.py tests/test_run_item_dependencies.py tests/test_agent_case_context.py tests/test_agent_case_assessment.py tests/test_agent_case_session.py tests/test_agent_case_execution.py -q --tb=short -rs -k 'not actual_model and not public_plan_launch_outbox' --junitxml=../docs/upgrade_2026-10-03/p2-planning-task-live-regression.xml
..\.venv\Scripts\python.exe -X utf8 -m src.cli.run_case_planning_worker --help
```

前端（agent_web）：

```powershell
npm run test -- src/features/projects/CasePlanningTaskPanel.test.ts src/features/projects/CasePlanDialog.test.ts src/services/projects.planning-tasks.api.test.ts
npm run build
```

部署需在既有基础配置中启用 `ORCHESTRATION__REDIS_TASK_DISPATCH_ENABLED`，并向 `ORCHESTRATION__REDIS_TASK_OUTBOX_STREAMS` 保留原列表的同时登记一个 `:case_planning` 流。新增表名引导项为 `DATABASE__POSTGRES_CASE_PLANNING_TASK_TABLE`，默认 `agent_case_planning_tasks`。业务规划策略沿用 MySQL，没有引入第二份可编辑策略。生产 API 与 Worker 需使用相同配置，另运行既有 relay：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m src.cli.run_task_outbox_relay
..\.venv\Scripts\python.exe -X utf8 -m src.cli.run_case_planning_worker
```

以上部署命令已核对源码参数，完整生产启动本轮未执行。独立任务表按现有 initialize 增量创建；关闭新派发入口保留任务/计划，旧同步 API 保持兼容。通用任务池现为 Session 投影，本轮提供项目规划列表，尚未合并投影/事件；显式新任务与旧失败任务的重试关联、内部回归异步迁移及规划专属 LangSmith span 也待完成。持久 Worker 使用现有请求上下文及逻辑调用记录，旧同步调用保留原 tracing 契约。

本轮更新 PLAN 正文、§2.6、阶段表及剩余工作，并同步所选 Codex 镜像。上传版本仍由 RustFS 原件和 PG 记录管理，不调用 Git；文档 V1/V2 功能仍待实现。模板仅用于生成，执行仍读取固定完整用例。完整 P0—P5 目标未完成，默认模型空正文失败、生产/完整项目页面、步骤级准备与未知副作用、P0 录制/Docker、P1 模块生成、P3—P5 闭环及业务数据迁移继续保留。用户 .gitignore 保持未暂存；本轮新测试因该规则忽略 tests 目录而显式 force-add，不修改忽略规则。

### 规划 Worker 专属启动及真实 CLI 验收（2026-10-04）

上一阶段 `c9581bc` 为实际进展。继续读取用户输入、PLAN §2.6、main.py、AppContainer、Embedding/Agent Worker、规划来源、SQL 池生命周期及本地课程《Agent轨迹与协议鲁棒性》《评测工程化与CICD》。确认旧规划 CLI 直接进入整个 API lifespan，会启动会话恢复、MCP、浏览器配额、录制编排和无关配置域；这是 Worker 的启动职责混在 API 中，采用专属资源生命周期修正边界，而不是给无关服务启动错误加 fallback。

修改仅涉及规划 Worker：新增 `planning_worker_runtime.py`，复用现有 AppContainer 的同一批 provider，初始化 MySQL 模型/应用配置，PG 项目/用例/文档/录制/向量/计划/任务/Outbox，连接已配置 Redis 向量及任务队列；录制图读取按现有来源接线。模型 OAuth 和向量召回能力保留。各资源初始化前登记关闭，AsyncExitStack 最后释放 SQL 池，部分启动失败和关闭失败仍继续释放已获取的其他资源。启动日志按现有 request context 标识资源/阶段，CLI 配置统一日志并保留 once/worker-id 参数，once 输出本次 ACK 数；不把 ACK 当作目标通过。

规划 CLI 不再导入 src.main，未修改其他 Worker 或整个 API lifespan。AppContainer 中用例读取服务的生成依赖仍按现有 provider 构造，但不启动生成、工具或会话业务。没有新增配置域、依赖、表字段或 API 契约；可编辑配置继续 MySQL，业务数据继续 PostgreSQL，原文继续 RustFS。API 的完整生产启动依然需要后续验收。

证据：

| 验证 | 实际结果 | 范围 |
|---|---|---|
| 启动边界初始复现 | 1 失败，4.35 秒 | `p2-planning-worker-bootstrap-before.xml`；独立进程实际导入旧 CLI 后发现 src.main 已加载，保留原失败 |
| 启动、回收及既有 Worker 回归 | 17 通过，7.54 秒 | `p2-planning-worker-runtime-regression.xml`；无 API 启动导入、向量/模型启动失败、关闭失败时仍回收其他资源及池；启用既有 live PG/MySQL 池测试，Redis Worker/参数回归 |
| 全部规划任务专项 | 13 通过，33.47 秒 | `p2-planning-worker-task-regression.xml`；保留原 11 项与隔离宿主进程，新增真实 CLI 参数化场景 |
| 真实生产规划 CLI 最终专项 | 1 通过、12 未选择，17.39 秒 | `p2-planning-worker-cli.xml/json`；使用 python -m src.cli.run_case_planning_worker --once --worker-id，实际读取环境配置并运行专属完整 startup/shutdown；真实 MySQL/PG/Redis/RustFS、本地 OpenAI HTTP，强制结束第二回合后新进程第三回合完成，固定文档/版本/总期限保留，终态重投不增模型调用 |
| 源码 | 4 个变更 Python 文件 py_compile 通过 | 额外检查，不替代上述功能验收 |

CLI 测试通过子进程环境传入隔离 Settings；未打印环境值，也没有写用户 .env 或改默认模型配置。新 PG 录制/事件/向量表以 UUID 独立命名并先检查不存在；只清理本 fixture 拥有的表、对象和流。Windows stdout/stderr 在受控 HTTP 等待前由 communicate 持续读取，避免日志填满管道导致进程阻塞。完整任务专项在增加启动日志前通过；增加日志后又执行真实 CLI 最终专项及带 live 池的回收测试。两个阶段的证据分开保存，原来的 p2-planning-task-process.json 保留。

主要实际命令（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-planning-worker-task-regression.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_tasks.py -k 'independent_worker and True' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-planning-worker-cli.xml
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_planning_worker_runtime.py tests/test_redis_task_worker.py tests/test_redis_worker_entrypoint.py tests/test_lifespan_resource_cleanup.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-planning-worker-runtime-regression.xml
```

CLI 连接并初始化真实向量 store，读取真实录制目录；本专项指定目标用例，未调用真实 embedding 模型，也未读取已有固化录制子图。不能将启动接线验收宣称这些语义读取场景全部通过。外部默认模型、完整项目页面、API lifespan、测试执行 Worker 仍未验收；通用任务池、回归异步迁移、未知业务副作用/步骤恢复及 P0—P5 其他原范围继续保留。前端与公开契约没有修改，不重复上一阶段已通过的前端构建或浏览器测试。PLAN 正文及所选 Codex 镜像同步；用户 .gitignore 仍未暂存，新必要测试显式 force-add，中文 commit 不推送/不建 PR。

### 用户所贴完整方案再次更新（2026-10-04）

用户要求更新所贴方案。本次按 `e9209f4` 读取原粘贴附件、PLAN、上传服务与格式策略、ApiDocRecord、对象存储、依赖声明、规划接口、冻结上下文和 Worker 专属运行生命周期。确认上传没有 Git 依赖；当前接口每次创建独立记录，部分格式先转换后保存，尚没有文档版本关联。方案首页引用的旧基线及异步规划“待新增”表述与现状不符，已修正。

本次修改 PLAN 的基线、流程摘要、上传与可选 Git 边界、接口和状态表，记录本次验证范围，并同步所选 Codex PLAN。执行主流程仍为理解需求、项目内选取、初排、AI 衔接判断与补全、最终冻结、Agent 执行；模板只用于生成。上传版本和差异属于待实施设计，Git 仅是可选只读来源；未实施 P3，也未把通用任务池研究当成已接入。原附件保持为输入记录，最新完整方案由仓库 PLAN 和 Codex 镜像提供。

实际命令（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py tests/test_agent_case_context.py tests/test_planning_worker_runtime.py -q --tb=short -rs
```

实际结果 **35 passed, 4 skipped in 9.22s**。4 个执行上下文项因真实隔离存储或当前实际模型开关未启用跳过；通过项只作为现有接口与启动边界的回归证据。本次未修改产品代码、安装依赖、迁移数据库或重新运行浏览器、真实 Worker 进程及外部模型验收。文档本地链接、关键约定、diff 和镜像一致性另做检查。本次提交仅包含 PLAN 与本记录；用户 .gitignore 保持未暂存，不推送、不建 PR。
