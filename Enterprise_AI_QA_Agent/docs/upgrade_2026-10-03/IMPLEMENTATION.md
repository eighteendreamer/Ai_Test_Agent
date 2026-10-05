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
| P2 选取 | 自由/按用例依据分离；项目内召回、完整读取、AI 选择、无匹配明确返回 | 已实现独立项目规划服务与页面，完整版本、跨历史模式、项目隔离、固定计划保存及重读已验证；执行上下文与图状态已接入，公开保存计划启动 API/UI 已验证实际存储和浏览器断线重试。持久规划任务、专属 Worker CLI 和任务池已接通，具体范围见下方记录；完整生产启动和默认模型仍待验收 |
| P2 补全 | AI 语义检查、项目查证、正式依赖或运行准备、负向条件保留、缺口可见 | 只读查询、正式依赖/准备结构、引用与拓扑校验、有界校验反馈已实现；专项 21 通过。当前真实模型三场景测试 1 通过，实际组件/HTTP/三种存储浏览器 1 通过，不等同实际 Agent 执行 |
| P2 回归持久规划 | 固定父运行/失败/版本，复用持久任务与共享内核，按原身份找回及保存计划启动 | 本轮后端 92 通过、1 外部模型项跳过，前端 23 通过及构建通过；真实 Chromium 弹窗/ProjectsView 2 通过、概览 provider 补验 1 通过；生产规划 CLI 中断恢复 1 通过。完整 API/测试执行进程及默认模型仍未验收，详见文末 |
| P2 执行 | 固定版本、依赖领取、输出绑定、检查点恢复、计划修订、结果证据与新回归 | 已有依赖、计划创建内部事务及上下文/回执核验。本步骤交付运行服务 Agent 策略、生产初始化注入、RunItem 审批/已保存决定恢复、持久意图/跨 attempt 重放及工具资源身份修复；扩大回归 359 通过、10 跳过、1 个模型项未选择，其中 8 个资源项另补验通过；前端 18 通过及构建通过。Agent 新回归及公开保存计划启动已接通，真实 Redis/Outbox/Worker 类与 Agent 审批/HTTP 结果专项通过，详见下方最新记录；默认模型目标回执验收仍失败；本轮完整 API lifespan/独立执行 CLI 的 API 场景与创建后重启已通过，其他模式真实工具、步骤级恢复/未知效果核对及计划修订仍待完成 |
| P3 上传 | 同一文档 V1/V2、原件与派生分存、重复判定、版本查询、解析失败可见 | 原件/安全/导入最终回归 65 通过，包含递增修订及旧 JSON/PG 目录兼容；实际文档插件 Chromium 1 通过、前端 5 通过及构建通过、完整 API/执行 CLI 1 通过。V1/V2、幂等、历史迁移、解析状态仍待实施，详见文末 |
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

### 通用任务池混合查询、状态与指定计划打开（2026-10-04）

上一轮 `333b7ce` 更新了完整方案，属于进展。按用户完整实施目标继续：重新读取指定输入，核对 Session/规划任务的存储与公开契约、任务池服务/页面、AppContainer/生产初始化、规划弹窗/项目路由及相关测试，读取本地课程《Agent轨迹与协议鲁棒性》《评测工程化与CICD》。确认问题是任务池查询只覆盖 Session，页面仅取前 24 项本地筛选，弹窗默认恢复最近请求；不能用伪造会话、前端合并两份首屏或导航时修改最近任务记录解决。

先将调研结果和设计加入 PLAN §2.6，再用真实 MySQL/PG/RustFS 创建项目规划任务。任务池混合入口返回 404，复现文件为 `p2-task-pool-before.xml`。随后实现：

- `schemas/task_pool.py` 增加带判别类型的 session/case_planning 条目及分页契约；原 `TaskPoolPage` 不变。
- `runtime/postgres_task_pool_store.py` 提供独立只读投影：一次 PG 查询合并既有表，在分页前筛选项目、类型、状态和文字，以更新时间/类型/身份排序。分页后只读取必要公开字段，沿用既有会话摘要映射；不会加载/返回整个规划检查点、输入、响应或租约。
- `TaskPoolService`、路由和 AppContainer 同步注入新增读取服务；`GET /task-pool/entries` 验证参数、返回独立类型并在查询失败时返回 503。旧 `GET /task-pool` 保留；无新业务表、可编辑配置、依赖或迁移。
- `TaskPoolView`、类型、HTTP 客户端及中英文消息同步。支持服务端筛选、前后页、可见页每 5 秒刷新、阶段/回合/失败原因和错误展示；筛选切换不让旧响应覆盖新页，离开页面停止刷新。规划完成仅代表规划终态。
- `ProjectsView` 将任务池导航的 `planning_task_id` 传给实际规划弹窗；弹窗/任务面板增加 `initialTaskId`。指定任务优先查询，核对项目/任务/结果计划；失败可重查原身份，不回退成最近任务。原有提交、刷新恢复和运行启动保持原契约。

验证使用实际 Markdown 原件、启用的完整 JSON 用例、两个项目和真实任务/会话。源文件下载后核对字节；超过 24 项混合分页和过滤不能遗漏任务；负向条件保留，任务状态由实际 Handler 运行改变；同时间排序稳定，归档历史可查，私有输入未返回，旧会话字段和派发信息保持。

| 验证 | 实际结果 | 证据及范围 |
|---|---|---|
| 原始缺口 | 1 失败 | `p2-task-pool-before.xml`，真实已提交任务没有混合查询入口 |
| 初次存储联调 | 2 通过、1 失败 | `p2-task-pool-storage.xml`，夹具用本机时间推断 PG 顺序；后续浏览器又确认 `save_session` 会重写时间。改用现有事务插入保留数据库参考时间，不修改产品时间行为 |
| 存储阶段验证 | 3 通过，4.01 秒 | `p2-task-pool-storage-final.xml`；后续增加同时间排序并纠正夹具的结果纳入最终回归 |
| 受影响回归初跑 | 48 通过、33 跳过，10.22 秒 | `p2-task-pool-regression.xml`；33 项真实 PG 相关跳过随后启用开关补验 |
| 最终真实存储及受影响回归 | 94 通过，44.33 秒 | `p2-task-pool-live-regression.xml`；含会话读写/UTC 契约、混合投影、Worker 启动、规划任务及独立 CLI 恢复，实际使用隔离表。CLI/进程宿主重新产生的 JSON 单独保存为 `p2-task-pool-worker-cli-regression.json`、`p2-task-pool-worker-host-regression.json`，旧阶段 CLI 证据已恢复，未覆盖历史结果 |
| Chromium 初跑 | 1 失败 | `p2-task-pool-browser.xml`，夹具保存方法重写时间，导致目标不在预期第二页；原失败保留 |
| Chromium 最终 | 1 通过，11.57 秒 | `p2-task-pool-browser-final.xml/json/png`；实际 TaskPoolView → ProjectsView → 指定历史任务及固定计划，页码/筛选/完成轮询/失败展示/尾部用例与文档依据可核对，无新增 POST、无页面 JS 错误 |
| 前端 | 5 文件、18 通过；typecheck 与 build 通过，Vite 6.74 秒 | 筛选分页、竞态、刷新停止、错误展示、精确身份和 HTTP 编码/旧接口。首次测试夹具未注册 locale，2 项失败；注册实际中文消息后通过，未改产品 i18n 机制 |

主要实际命令（Agent_Server）：

```powershell
$env:RUN_LIVE_TASK_POOL='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_task_pool_entries.py tests/test_task_pool_service.py tests/test_session_store_limits.py tests/test_session_store_value_contract.py tests/test_planning_worker_runtime.py tests/test_project_regression_frontend_contract.py tests/test_case_planning_tasks.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p2-task-pool-live-regression.xml
$env:RUN_LIVE_TASK_POOL_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_task_pool_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-task-pool-browser-final.xml
```

前端（agent_web）：

```powershell
npm run test -- src/views/TaskPoolView.test.ts src/services/sessions.task-pool.api.test.ts src/features/projects/CasePlanningTaskPanel.test.ts src/features/projects/CasePlanDialog.test.ts src/services/projects.planning-tasks.api.test.ts
npm run build
```

浏览器加载实际生产组件和路由函数，API 为隔离 providers 的宿主，模型决定受控；资源监控明确返回不可用，未伪造统计。因此本轮证明任务池/指定规划读取可用，不宣称完整生产 lifespan、完整项目资源页面、外部模型或完整 Agent 执行全部通过。截图中资源不可用正是此边界。用户 .gitignore 未暂存；必要新测试按现有规则 force-add，原始失败证据保留。方案及 Codex 镜像同步；本轮按项目规范只提交相关实现/测试/证据/文档，不推送、不建 PR。剩余失败回归异步迁移、默认模型空回执、完整生产执行 Worker、步骤级恢复/未知副作用、模块生成、P0 录制/Docker、P3—P5 和业务数据迁移继续保持在完整目标中。

7 个相关 Python 源文件/测试 py_compile 通过，实际 JUnit 成功计数与失败/跳过标记核对通过。源码和文档执行 `git diff --cached --check`；原始失败 XML 内有 pytest 输出的行尾空格，保留原字节，不把机器证据空白当作产品代码问题或为通过检查改写失败输出。XML 另以标准库完整解析检查。密钥模式、暂存范围及 Codex 镜像一致性已核对。


### 直接更新用户粘贴方案并同步工程计划（2026-10-04）

本次用户要求更新所粘贴的完整方案。重新读取附件 `ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt`、当前 PLAN、上传服务/路由/目录模型、对象存储、格式策略、依赖声明、规划共享内核/持久任务、冻结执行上下文、失败回归及任务池入口，并核对本地课程中的过程轨迹、幂等和版本化证据要求。基线为 `c204e6b`。

直接修订该附件，原字节先备份为同目录 `已粘贴的文本.original.txt`。保留六个主题章节：同步选取→初排→补全→最终冻结、模板只生成、项目规划服务复用；上传章节采用已核对的系统版本模型，明确上传不需要 Git、原件先存、DOCX 策略/依赖缺口、请求幂等与历史恢复 V3、父版本冲突、存储/事务失败、迁移及 P3 分步交付；新增当前实现及剩余工作表。同步项目 PLAN 的附件入口和 `c204e6b` 状态，修正实施总表仍称持久规划待完成的旧表述。Codex PLAN 镜像同步项目 PLAN；附件保留较紧凑的原章节结构，不复制历史测试流水。

实际重新运行（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py tests/test_agent_case_context.py tests/test_planning_worker_runtime.py -q --tb=short -rs
```

结果 **35 passed, 4 skipped in 10.79s**。3 个执行上下文项需要隔离真实 PostgreSQL/MySQL/RustFS，1 项需要当前实际模型和真实存储；本次未启用对应开关。通过结果只证明既有上传/目录/存储接口及规划启动边界，不能证明新的文档版本、解析或比较已完成。当前任务池的 94/18/1 通过为此前保存的证据，未混算为本次重新执行。

本次仅更新方案及记录；没有产品代码、依赖、数据库迁移或配置变化，没有重新运行前端构建、浏览器、真实存储版本链、Worker 进程和外部模型。全量 P0—P5 仍未完成。文档关键约定、本地绝对链接、原始备份、镜像一致性及相关 diff 另行检查；只提交本轮 PLAN 与记录，用户 .gitignore 保持未暂存，不推送、不建 PR。

### 失败回归复用持久规划及方案同步（2026-10-04，基线 a56d5e4）

实施前重读用户约束附件和方案，核对原回归验证、Agent 回归同步调用、规划种子、共享工作流、任务存储与计划运行事务。原流程在 HTTP 内等待模型，任务种子缺少固定回归来源；接口缺失用实际失败运行复现为 404，保存在 `p2-regression-planning-before.xml`。先新增 PLAN §2.7，再接通实现。

后端复用 `_prepare_regression` 的父运行、失败候选和版本验证；新增 202 持久规划入口，保存 `RegressionPlanningInput` 到既有计划/任务 JSON。请求哈希覆盖父运行及原提交内容，同一身份先找回既有任务，避免配置变化重新选取。确定性范围裁剪保持必要前置、准备和清理，Worker 不调用模型；版本替换复用同一 AI 内核。检查点冻结回归信息，恢复不能改固定目标、原预算或来源。计划启动由回归信息生成来源映射，复用既有套件/运行/Outbox 事务，原失败和终态条目不重置。普通历史计划哈希排除新增的空回归字段，保持旧启动请求可重放。旧同步接口兼容保留。

前端测试历史和回归中心接通实际 `ProjectsView`→版本选择→持久任务→保存计划启动→运行详情。新旧请求保持明确类型；浏览器只保存请求/任务身份，服务端计划恢复固定版本及模式/会话。改正 `TestRunDetail.results` 为后端真实结果契约，未以类型强转掩盖差异。

本阶段的失败与根因处理：

- 浏览器刷新遗失替换版本任务：找回键包含可变版本选项，改为项目、父运行及请求失败范围；从已保存计划恢复版本。真实失败与修复结果分别保存在 `p2-regression-planning-browser-refresh-before.*` 和 `p2-regression-planning-browser-final.*`。
- 刷新后未提交成功、查询 404：组件测试实际复现版本选项被永久隐藏，9 项中 1 失败；现允许重填原选择、保留原请求身份，并让服务端核对内容。没有自动新建任务；查询 503 保留未确认状态。该失败已记录于工具输出，对应回归用例保留在 `CasePlanDialog.test.ts`。
- 51 个固定失败目标：创建正式用例、保存计划、领取并保存真实失败结果后，提交在候选列表的 50 项校验处失败，见 `p2-regression-planning-scope-before.xml`；固定裁剪复用原有有界请求，完整范围另由固定决策/来源保存，修复结果见 `p2-regression-planning-scope-final.xml`。该范围专项使用内存业务存储和捕获任务提交，验证服务边界；小范围持久提交另由真实 PG 专项覆盖，不宣称 51 项真实数据库端到端验收。AI 重新规划的现有候选/上下文限制未扩大。
- 浏览器初次 JSON 末尾检查未展开详情、项目入口初次 checkbox 定位不唯一，均是测试定位问题，未据此修改产品行为；原失败和截图保留。项目专项补齐真实套件/运行概览 provider，避免缺省 0 被误作实际统计。

实际验证及范围：

| 证据 | 结果 | 实际范围 |
|---|---|---|
| `p2-regression-planning-final-regression.xml` | 92 通过、1 跳过，58.12 秒 | 回归任务、创建事务、既有回归、共享规划、Worker 生命周期及前端契约；真实存储开关已启用；跳过项为当前外部模型测试 |
| 前端 5 个受影响测试文件 | 23 通过 | 原身份找回、版本选择、提交恢复、启动、任务池/API 契约 |
| `npm run build` | 类型检查及构建通过；Vite 6.64 秒 | 当前前端源码 |
| `p2-regression-planning-browser-entries-final.xml` | 2 通过，12.55 秒 | 实际弹窗及 ProjectsView 两种入口；真实 HTTP/MySQL/PG/RustFS，受控规划决定；提交响应丢失、同身份找回、完整末尾步骤、新版本与原失败关联 |
| `p2-regression-planning-browser-complete-providers.xml` | 1 通过、1 未选择，8.24 秒 | 项目页补齐真实套件/运行概览，打开新回归详情；资源配额/占用监控明确 503 |
| `p2-regression-planning-worker-cli.xml/json` | 1 通过，18.54 秒 | 实际生产规划 CLI、MySQL/PG/RustFS/Redis/Outbox/本地模型 HTTP；模型第二回合中断、重启后固定来源及原期限恢复、未知回合占用预算、终态重投不再调用 |
| `p2-regression-planning-upload-check.xml` | 25 通过，1.02 秒 | 既有上传、目录与对象存储接口；不证明新文档 V1/V2 已实现 |

重现命令（Agent_Server）：

```powershell
$env:RUN_LIVE_REGRESSION_PLANNING='1'
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_regression_planning_tasks.py tests/test_case_plan_run_creation.py tests/test_test_run_regression.py tests/test_case_planning.py tests/test_planning_worker_runtime.py tests/test_project_regression_frontend_contract.py tests/test_case_planning_tasks.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p2-regression-planning-final-regression.xml
$env:RUN_LIVE_REGRESSION_BROWSER='1'
$env:QA_REGRESSION_BROWSER_EVIDENCE='p2-regression-planning-browser-entries-final'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_regression_planning_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-regression-planning-browser-entries-final.xml
$env:RUN_LIVE_REGRESSION_PLANNING='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_regression_planning_worker.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-regression-planning-worker-cli.xml
```

前端命令（agent_web）：

```powershell
npm run test -- src/features/projects/CasePlanningTaskPanel.test.ts src/features/projects/CasePlanDialog.test.ts src/services/projects.planning-tasks.api.test.ts src/views/TaskPoolView.test.ts src/services/sessions.task-pool.api.test.ts
npm run build
```

业务记录留在 PostgreSQL JSON，配置仍使用既有 MySQL 域，没有新增依赖、配置或数据库表。派发沿用现有规划流和 Worker。CLI 原始专项在本阶段前半段实际运行；末轮没有重复执行同一 CLI 专项。规划模型决定受控，本阶段未执行整个生产 API lifespan、外部默认模型或目标 Agent 业务测试；资源监控未配置不计为通过。完整 P0—P5 保持未完成，包括真实默认模型回执、测试执行 Worker、步骤级准备恢复/未知副作用、模块生成、P0 录制/Docker、P3—P5 和遗留业务数据迁移。

直接更新用户粘贴方案、项目 PLAN 和实施记录，Codex 计划镜像同步。重新核对上传代码没有 Git 调用；独立原件/版本、解析工厂、确定性差异和可选 Git 的设计保持一致。15 个相关 Python 源码/测试实际 py_compile 通过，仅作为补充检查。共享规划回归测试产生的历史命名证据另存为 `p2-regression-planning-shared-kernel-cli.json` 和 `p2-regression-planning-shared-kernel-probe.json`，原 `p2-planning-worker-cli.json` 从 HEAD 按字节恢复；原始失败 XML 保留，不为消除 pytest 输出空白而改写。

## 完整 API 与执行 Worker CLI 验收（2026-10-04）

本轮先核对 `run_redis_worker.py`、`main.py` 完整生命周期、规划/运行/审批服务、Session 图及工具消费者。执行 Worker 确实需要现有 Agent 图与工具服务；实际启动没有取得要求拆分其生命周期的失败证据，因此保留现有入口。规划 Worker 继续使用既有只读专属生命周期。

新增 `tests/test_execution_worker_cli.py`，实际启动生产 `uvicorn src.main:app` 与 `python -m src.cli.run_redis_worker --once`，不替换生产 AppContainer provider。创建独立 PostgreSQL 数据库（完整启动含固定名称 smoke 表）、隔离 MySQL 配置表及独立 Redis Docker 容器；真实 RustFS 保存上传的 Markdown 文件并比对原件字节。用生产路由创建/评审/启用包含 false、0、null 的完整 JSON 用例、规划并启动固定运行，Outbox 实际发送到 Redis。

验收结果：执行 CLI 首次运行进入真实待审批，尚未产生业务操作；停止 API 后重启，原运行及审批保留。同一创建请求返回原运行。逐项批准后真实 HTTP 创建→查询→删除；创建成功且下一项待审批时再次重启，订单、原审批和工具证据保留，没有再次创建。最终 PostgreSQL 结果为 passed，订单已清理。终态消息再次投递后运行独立 CLI，无新增模型/业务请求，结果保持不变。

| 证据 | 实际结果 | 范围 |
|---|---|---|
| `p2-execution-worker-cli-final.xml/json` | 1 通过，42.74 秒 | 完整 API lifespan、独立执行 CLI、真实 MySQL/PG/RustFS/Redis、Agent 图、三次审批及实际工具 HTTP；5 次模型请求、3 次业务请求，重投新增 0 |
| `p2-execution-worker-cli-regression.xml` | 86 通过、4 跳过，161.38 秒 | 执行、Session、结果核验、派发、入口及生命周期已有测试；启用正式运行/规划真实存储开关 |
| `p2-execution-worker-cli-regression-supplement.xml` | 3 通过、33 未选择，7.22 秒 | 补验上行跳过的真实 Redis Worker 类及 PG/MySQL 池生命周期；剩余 1 项外部默认模型未执行 |
| `p2-execution-worker-cli-probe/` | 保留全部五个子进程 stdout/stderr、输入文件、HTTP 调用及 Docker 输出 | 原始过程证据；只清理本次创建的数据库、配置表、容器和已登记原件对象 |

受控 HTTP 模型提供工具决策与结构化回执，SDK/流式传输和实际工具执行仍使用产品代码。夹具关闭向量召回与语义意图识别，仅用于固定测试输入；没有改生产默认配置。未验收默认外部模型、未知副作用查询、所有步骤级恢复、执行中计划修订、其他模式/完整产品页及全部署。不把此 API 场景扩大为 P0—P5 完成，也不声称已实现新文档 V1/V2、解析或差异。

原始失败 XML 保留：`first` 是夹具误写 LangSmith 字段；`startup` 是夹具 transport 枚举错误；`runtime` 是夹具不支持真实 Agent SSE，500 导致业务运行 error。按实际契约修正夹具后 `approval` 为 1 通过（33.92 秒），再补创建成功后的重启和持久证据输出，最终通过。未据这些夹具错误修改产品代码；临时目录中的早期子进程日志已被 pytest 轮转清理，早期失败以 XML 为留存证据，最终完整日志已保存。

数据库隔离方式依据 [PostgreSQL CREATE DATABASE](https://www.postgresql.org/docs/current/sql-createdatabase.html) 的独立数据库及非事务要求，连接 autocommit 依据 [SQLAlchemy 连接文档](https://docs.sqlalchemy.org/en/20/core/connections.html#setting-isolation-level-or-dbapi-autocommit-for-a-connection)。Docker 使用已缓存镜像中的实际 redis-server，不拉取新镜像。没有新增产品依赖、配置域或业务表，也没有执行生产迁移。

复现命令（Agent_Server）：

```powershell
$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
$env:QA_TEST_GATE_OUTPUT_DIR=(Resolve-Path '..\docs\upgrade_2026-10-03').Path
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_execution_worker_cli.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-execution-worker-cli-final.xml
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_execution.py tests/test_agent_case_session.py tests/test_agent_case_assessment.py tests/test_redis_worker_entrypoint.py tests/test_redis_execution_dispatcher.py tests/test_lifespan_resource_cleanup.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p2-execution-worker-cli-regression.xml
$env:RUN_LIVE_PUBLIC_PLAN_WORKER='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_execution.py tests/test_lifespan_resource_cleanup.py -q --tb=short -rs -k 'public_plan_launch_outbox or last_owners_sql_pool' --junitxml=../docs/upgrade_2026-10-03/p2-execution-worker-cli-regression-supplement.xml
```

本轮只新增功能验收测试、过程证据并更新方案与实施记录。测试文件编译、文档本地链接、镜像一致性、XML 计数和 Git diff 另核对；前端及产品源码没有变更，未重复构建前端。用户 `.gitignore` 保持未暂存，不提交无关文件。完整目标保持 active。

## 工具实际返回的检查点与工件中断恢复（2026-10-04）

先读取 ToolRuntime、ToolJob 服务及两个存储实现、Agent 审批恢复、Session 重放与结果核验，再对照本地《Agent轨迹与协议鲁棒性》的重复执行/中断场景。实际 HTTP POST 返回 201 和订单 ID 后，在工件交付阶段注入错误：原实现将真实输出替换为 error。先运行失败样本并保存 `p2-operation-output-before.xml`（2 失败、2 未选择，4.60 秒），修订 PLAN 后实施。这个缺口发生在工具响应保存顺序，增加 RunItem 快照不能修复。

修改范围及契约：

- `schemas/tool_job.py` 定义输出观察检查点，保存版本、哈希、返回状态和时间，置于既有 Job metadata。
- 内存与 PostgreSQL ToolJobStore 增加原子观察及条件结算：只写入运行中的原任务；结算同时比较原输出及检查点，保留并发取消和其他终态。
- ToolJobService 处理提交回执丢失后的同身份回查、内容校验、正常交付结算及中断恢复。普通运行心跳只更新心跳，不以旧整行覆盖证据。
- ToolRuntime 在现有归一/脱敏之后、工件交付之前保存实际响应。后续失败保留响应并标明交付中断，不将真实 POST 结果换成 error。取消仍传播给原执行流程。
- AgentCaseExecutionAdapter 核对原 session/turn/tool/call/参数与 project/run/item/原 execution attempt，在原审批恢复中重放已观察响应；没有返回检查点继续报告结果未知。

没有新增表、依赖或可编辑配置，复用 PostgreSQL JSONB；MySQL 配置和 RustFS 原件/工件职责保持不变。存储协议的两个实现和服务调用已同步核对；前端 ToolJobRecord 仍使用原 `metadata/output_payload: Record<string, unknown>`，没有新增必填 API 字段或更改其状态枚举。本次未改前端，未重复构建。

| 证据 | 实际结果 | 验证范围 |
|---|---|---|
| `p2-operation-output-first-fix.xml` | 5 通过，40.35 秒 | 初版实际 HTTP 输出保留及恢复、既有心跳 |
| `p2-operation-output-storage.xml` | 5 通过，39.76 秒 | 更新为有真实业务状态的目标；POST 中断恢复后 GET/DELETE，最终按实际 count 不符判失败 |
| `p2-operation-output-contract.xml` | 13 通过，48.24 秒 | 内存和真实隔离 PG/MySQL/RustFS；真实响应、提交回执丢失、新服务读取、输出/检查点损坏、取消终态保护和原审批恢复 |
| `p2-operation-output-regression.xml` | 测试收集失败，1 error，4.53 秒 | `test_schema_migrations.py` 导入失败：`ModuleNotFoundError: No module named 'alembic.config'`；未执行迁移专项，不计通过 |
| `p2-operation-output-regression-final.xml` | 184 通过、21 跳过，173.07 秒 | 去掉缺依赖的迁移专项后，实际 Agent/Session/审批/结果、deepagent 工具重放、工件、安全、性能、CLI 超时和 API 隔离回归；1 项默认外部模型未启用，20 项可选 deepagents 未安装 |
| `p2-operation-output-cli.xml` 及 `p2-operation-output-cli/` | 1 通过，41.22 秒 | 重新启动完整生产 API 与独立执行 Worker CLI，真实隔离四类存储、实际 HTTP 操作、审批/创建后重启及终态重投；受控模型 |
| `p2-operation-output-recovery.json` | 只有一次 POST；目标 failed；清理完成 | 工件阶段中断、真实 Session 租约恢复后读取原订单 ID，不伪造目标通过 |

实际命令（Agent_Server；各命令在独立 shell 中设置所需开关）：

```powershell
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:QA_TEST_GATE_OUTPUT_DIR=(Resolve-Path '..\docs\upgrade_2026-10-03').Path
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_tool_operation_observation.py tests/test_tool_runtime_heartbeat.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-operation-output-contract.xml

$env:RUN_LIVE_PUBLIC_PLAN_WORKER='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_case_execution.py tests/test_agent_case_session.py tests/test_agent_case_assessment.py tests/test_tool_runtime_heartbeat.py tests/test_deep_agent_tool_replay.py tests/test_deep_agent_runtime_adapter.py tests/test_artifact_storage_service.py tests/test_performance_runtime_artifacts.py tests/test_security_output_compaction.py tests/test_security_approval_boundary.py tests/test_security_execution_environment_upgrade.py tests/test_perf_high_level_runners.py tests/test_api_mode_session_isolation.py tests/test_cli_executor_timeout.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p2-operation-output-regression-final.xml

$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
$env:QA_TEST_GATE_OUTPUT_DIR=(Resolve-Path '..\docs\upgrade_2026-10-03\p2-operation-output-cli').Path
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_execution_worker_cli.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p2-operation-output-cli.xml

..\.venv\Scripts\python.exe -X utf8 -m py_compile src/schemas/tool_job.py src/runtime/tool_job_store.py src/runtime/postgres_tool_job_store.py src/application/runtime/tool_job_service.py src/application/runtime/tool_runtime_service.py src/application/test_runs/agent_case_execution.py tests/test_tool_operation_observation.py
```

7 个 Python 文件实际编译通过。完整 CLI 输出使用新的证据子目录，未覆盖前一轮 CLI 原始记录；本轮失败 XML 原样保留。同步修正方案首页、§2.4、第六节和下一步，直接更新用户粘贴方案及 Codex 镜像，并校验链接/一致性与 Git diff。仅暂存本轮文件，保留用户 `.gitignore` 未暂存。

本轮完成范围是**已返回的工具输出**保存与恢复，不承诺请求发送和检查点写入之间没有窗口。真正没有返回证据的业务效果、完整准备步骤归属/状态、执行中计划修订、默认外部模型、其他模式/页面和 P0/P3—P5 仍未完成。没有执行生产数据库迁移；缺少 Alembic 的迁移专项与缺少 deepagents 的可选运行项明确未验收。不扩大受控模型/本地 HTTP 的结论。

## P3.1 原件与派生内容分存及实际下载（2026-10-04）

先核对上传/转换/URL 修改、上传安全、RustFS、目录三个存储实现、生产 DI、HTTP 路由和实际前端组件，再对照用例导入服务的提交后回查与引用清理实现。现有准备步骤缺少可靠的逐步执行归属，不能由工具 call_id 或最终回执反推；该问题保留为 P2 剩余工作，先完成可独立验证的原件基础。修改 PLAN §3.1 后实施。

真实输入与根因：创建带 UTF-8 BOM 的 OpenAPI JSON 和 CRLF Markdown，通过现有上传及更新入口操作。旧实现未保存转换前 JSON，真实 RustFS 中的 Markdown 在更新项目 URL 时被覆盖。内存对象夹具没有重现覆盖行为，因此保留真实存储失败样本。补充历史目录丢失全文地址的样本，原实现将截断预览另存为完整派生文档；真实 PG/RustFS 与内存均复现失败。不是 Git 能力缺失，上传保存和本次验证全程不需要 Git。

修改范围与契约：

- `schemas/api_docs.py` 增加可选原件和派生资产，保存自身 ID、文件信息、字节哈希、对象引用、时间/原因及安全报告；旧记录默认缺失，不伪造历史原件。
- `api_docs_service.py` 先使用既有安全策略扫描并保存原始字节，再转换为独立 Markdown。URL 修改从全文对象读取，另存新派生对象，保留原件与旧派生结果；读取失败明确报错，不由预览重建。
- `ApiDocStore` 的 Protocol、内存、JSON 兼容及 PostgreSQL 实现同步增加可选目录修订条件更新，保留旧调用兼容。首版时间戳方案扩大回归失败，已改为递增 `catalog_revision`；旧 JSON/PG 目录缺字段按 0 读取并实际验证。目录冲突返回 409，丢弃的本次新对象按引用检查清理；并发获胜对象保留。
- 创建或更新提交异常先按原目录 ID 回查。相同候选已经提交时返回已存记录；回查不可用时保留原件和派生对象并报告未确认；确定未引用才清理。既有删除入口清理原件和全部派生对象，失败有日志。
- `api_docs.py` 新增原件/指定资产下载，按文档内资产身份定位，校验实际大小和 SHA256；缺失 404、存储或一致性故障为脱敏 500。文件名采用既有用例导入下载的 UTF-8 header 形式。
- 前端类型、`tools.api.ts` 和实际 `ApiDocsPlugin.vue` 接通 `ApiDocAssets.vue`。展示原件、派生下载、历史缺失和失败重试；修改中英文上传说明，说明保留原件和生成 Markdown。实际浏览器下载已证明对象 URL 清理后文件仍可保存。
- `upload_security_service.py` 将已有转换器及界面支持的 HAR 原件纳入 API 文档格式与文本内容扫描；未关闭扫描、未扩大聊天入口策略。实际 HAR 的转换/原件下载通过，转换会丢弃的危险文本仍被拒绝。

本步只扩展既有 PostgreSQL 目录 JSON 与 RustFS 资产，不新增业务表、配置域或依赖。可编辑配置仍使用 MySQL。本轮未执行生产迁移，不安装 Docling 或解析器；DOCX 格式策略及 `python-docx`/`pypdf` 声明仍待后续查证实施。独立资产历史不是稳定文档身份/V1/V2、解析快照或差异，未将这些目标记作完成。

| 证据 | 实际结果 | 范围 |
|---|---|---|
| `p3-original-assets-before.xml` | 1 失败、1 通过、1 跳过，3.03 秒 | 最初原件缺失样本；内存未重现真实覆盖 |
| `p3-original-assets-live-before.xml` | 3 失败、1 通过，4.33 秒 | 真实 HTTP/PG/RustFS 的 JSON 原件缺失与 Markdown 覆盖 |
| `p3-original-assets-first-fix.xml` | 4 通过，3.88 秒 | 首版真实文件修复专项 |
| `p3-original-assets-storage.xml` | 42 通过，6.30 秒 | 原件下载、派生保留、提交回执丢失、并发目录冲突、缺失/损坏、安全扫描在转换前执行及既有存储回归 |
| `p3-original-assets-preview-before.xml` | 2 失败、4 通过、13 未选择，5.79 秒 | 截断预览重建失败；更新提交回执丢失与不可回查时保留资产已通过 |
| `p3-original-assets-storage-final.xml` | 48 通过，7.43 秒 | 最终内存及真实 PG/RustFS 原件/派生/提交边界，旧目录兼容及对象存储 |
| `p3-original-assets-browser-first.xml` | 1 失败，10.52 秒 | 宿主页遗漏注册现有翻译，夹具修正 |
| `p3-original-assets-browser-labels-before.xml` | 1 失败，36.69 秒 | URL 标签包含说明文本，精确标签定位不匹配；原件/派生下载已执行，修正夹具定位 |
| `p3-original-assets-browser-final.xml/json/png` | 1 通过，6.37 秒 | 实际 ApiDocsPlugin 宿主页、生产文档路由、真实隔离存储；上传 BOM JSON、下载原件/Markdown、两次 URL 修改及旧派生仍可下载；页面错误为 0 |
| `p3-original-assets-regression.xml` | 65 通过、10 跳过，14.11 秒 | 文档发现/导入/安全、生成、完整 JSON 及规划/导入消费者 |
| `p3-original-assets-regression-live.xml` | 9 通过、19 未选择，10.63 秒 | 补验上行 8 个因未开启存储开关的跳过项，并包含原有真实存储专项；剩余 2 项默认外部模型未执行 |
| 前端两文件专项 | 5 通过，1.71 秒 | 资产下载身份、旧原件缺失、错误重试及既有平台导入源逻辑 |
| `npm run build` | 类型检查及构建通过，Vite 6.06 秒 | 当前前端源码 |
| `p3-original-assets-cli-output-before.xml` | 1 失败，44.30 秒 | CLI 场景断言执行后保存证据因输出目录不存在失败；清理数据库/配置/容器在该错误之前完成，未修改产品行为 |
| `p3-original-assets-cli.xml` 及 `p3-original-assets-cli/` | 1 通过，39.73 秒 | 创建输出目录后，完整生产 API lifespan/执行 Worker CLI、真实隔离 MySQL/PG/RustFS/Redis、受控模型、真实创建→查询→清理、审批/创建后重启及终态重投 |

复现命令（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_original_assets.py tests/test_api_docs_bom.py tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-original-assets-storage-final.xml

$env:RUN_LIVE_API_DOC_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_assets_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-original-assets-browser-final.xml

$env:RUN_LIVE_CASE_JSON_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_document_dependency_http.py tests/test_api_docs_origin_security.py tests/test_api_doc_resolution.py tests/test_case_json_templates.py tests/test_case_planning.py tests/test_test_case_generation_pipeline.py tests/test_case_import_service.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p3-original-assets-regression.xml

$env:RUN_LIVE_UPGRADE_STORAGE_TESTS='1'
$env:RUN_LIVE_CASE_IMPORT_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_document_dependency_http.py tests/test_case_import_service.py -q --tb=short -rs -k 'True' --junitxml=../docs/upgrade_2026-10-03/p3-original-assets-regression-live.xml

$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
New-Item -ItemType Directory -Path '../docs/upgrade_2026-10-03/p3-original-assets-cli' -Force | Out-Null
$env:QA_TEST_GATE_OUTPUT_DIR=(Resolve-Path '../docs/upgrade_2026-10-03/p3-original-assets-cli').Path
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_execution_worker_cli.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-original-assets-cli.xml

..\.venv\Scripts\python.exe -X utf8 -m py_compile src/schemas/api_docs.py src/application/documents/api_doc_store.py src/application/documents/api_docs_service.py src/api/routes/api_docs.py tests/test_api_doc_original_assets.py tests/test_api_doc_assets_browser.py
```

前端命令（agent_web）：

```powershell
npm run test -- src/features/tools/plugins/ApiDocAssets.test.ts src/features/tools/plugins/ApiDocsPlugin.importSources.test.ts
npm run build
```

所有失败 XML 原样保留，不为隐藏失败覆盖记录；浏览器 Vite stdout/stderr 保存在 `p3-original-assets-browser-vite.log`，CLI 原始子进程输出保存在独立目录。真实外部默认模型与完整产品页未验收，浏览器明确使用实际插件的隔离宿主。历史原件缺失不自动补造；生产历史数据未迁移。

已直接更新用户粘贴方案原文件，保留原始备份，项目 PLAN 与 Codex 镜像按字节同步。完整 P0—P5 保持未完成，下一步先研究稳定文档版本契约/幂等提交与历史迁移，再接通需求文档格式策略、解析快照和差异；P2 剩余恢复、P0 人工录制与 Docker、P4/P5 继续保留。提交仅包含本轮相关文件，用户 `.gitignore` 保持未暂存。

### 最后兼容检查与并发根因修正

原件先扫描后转换暴露了 HAR 格式冲突。已读取现有 `API_DOC_EXTENSIONS` 与 `_decode_text_preview`，实际 `.har` 被拒绝，见 `p3-original-assets-har-before.xml`（1 失败，3.70 秒）。补充 API 文档 HAR 原件格式和文本内容扫描后 `har-final.xml`（1 通过，3.29 秒），转换/下载字节一致，转换会丢弃的危险扩展文本仍拒绝。

随后扩大回归在内存并发场景抓到 1 失败、62 通过（23.01 秒，`p3-original-assets-har-regression.xml`）。固定 `datetime.now` 后内存/真实 PostgreSQL 都稳定漏检（`clock-before.xml`，2 失败，3.47 秒）：原件读取与两次目录更新可能拿到同一个时间戳，旧条件未识别已被其他请求修改。参照现有 MySQL 配置存储的修订计数，目录 JSON 增加 `catalog_revision`；服务按已读修订递增，三个存储实现比较预期修订。旧 PG 缺字段使用 0，旧 JSON 按模型默认 0；无需新表。它只控制目录并发，不作为业务文档版本。固定时钟修复后 `clock-final.xml`（2 通过，3.23 秒）。

最终证据（以下文件均位于 `docs/upgrade_2026-10-03/`，简写以 `p3-original-assets-` 为前缀）：

| 证据 | 实际结果 | 范围 |
|---|---|---|
| `revision-final.xml` | 65 通过，24.19 秒 | 上述原件/安全/目录/对象专项和用例导入全部启用真实存储；固定时钟并发，以及旧 JSON/PG 目录没有修订字段仍能更新并拒绝陈旧写入 |
| `browser-revision.xml` 及浏览器 JSON/截图 | 1 通过，6.89 秒 | 修订号接通后复验实际文档界面上传、原件/派生下载及两次 URL 修改 |
| 前端测试及 `npm run build` | 5 通过（1.73 秒），类型检查/构建通过（Vite 6.04 秒） | 当前最终前端源码及修订号类型 |
| `cli-revision.xml` 与 `p3-original-assets-cli-revision/` | 1 通过，40.96 秒 | 最终完整 API/执行 CLI，受控模型及真实隔离存储，审批/创建后重启、实际创建/查询/删除与终态重投 |

最终后端命令沿用上文存储专项并增加 `tests/test_case_import_service.py`，设置 `RUN_LIVE_CASE_IMPORT_TESTS=1`，输出 `p3-original-assets-revision-final.xml`。浏览器命令输出 `p3-original-assets-browser-revision.xml`；CLI 提前创建独立 `p3-original-assets-cli-revision/` 并将其设为 `QA_TEST_GATE_OUTPUT_DIR`，输出 `p3-original-assets-cli-revision.xml`，保留旧轮次。7 个受影响 Python 源码/测试重新执行 py_compile。失败 CLI 的早期临时子进程目录已被 pytest 轮转回收，未虚称保存该轮全部日志；原 XML 保留，成功两轮完整日志分别保留在各自证据目录。HAR 与并发失败不计为通过，只有最后修复复验后记录最终状态。

最终方案本地链接、原始备份、镜像字节一致性和 XML 数量已核对，敏感格式扫描没有匹配。代码、测试及 Markdown 的 `git diff --cached --check` 通过；完整检查提示 3 处 pytest 原始失败 XML 的尾部空白，保留原始证据，不改写失败输出。仅暂存本轮 70 个源码/测试/方案/证据文件，用户 `.gitignore` 未暂存。

## P3 独立版本后端开发检查及方案同步（2026-10-04）

本次直接更新用户粘贴方案原文件、项目 PLAN 和 Codex 镜像，将误放在 Git 章节下的“独立版本链的实施查证”移回上传章节。补充开发中接口、真实专项结果与剩余验收，修正“目前只有独立目录记录”的过时现状。上传章节明确不使用 Git 保存、缓存或比较上传文件；模板只参与生成，执行仍按项目选取、初排、AI 衔接判断、必要补全、最终冻结的流程。

版本后端仍在开发：扩展现有目录并新增 PostgreSQL 版本、资产及请求记录，当前指针/版本/提交同事务；用实际 BOM/CRLF Markdown 大文件通过 HTTP 验证 V1→V2、旧原件下载、同请求返回原快照、当前内容相同不增版本、恢复旧内容形成 V3及同名独立上传。不能将这些专项当作前端完整功能交付。

| 证据 | 实际结果 | 说明 |
|---|---|---|
| `p3-doc-versions-before.xml` | 2 失败 | 新版本字段缺失的原始功能样本 |
| `p3-doc-versions-first.xml` | 2 失败 | 功能断言通过后，夹具重复清理同一文档导致失败，未计通过 |
| `p3-doc-versions-contract.xml` | 24 通过、6 失败 | 旧故障替身不接受新增的版本/提交参数，以及直接改写资产哈希与不可覆盖约束冲突 |
| `p3-doc-versions-contract-repaired.xml` | 22 通过、2 失败，9.19 秒 | 故障测试将同一个模型对象交给两处引用，修改后错误比较两个引用；目录实际拒绝修改并保留原哈希 |
| `p3-doc-versions-contract-final.xml` | 24 通过，8.87 秒 | 修正故障替身参数、复制独立对象；正常修改旧资产哈希被拒绝，主动破坏隔离 RustFS 原件字节后 HTTP 返回 500；内存及真实 PG/RustFS 复验通过 |

实际运行（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_versions.py tests/test_api_doc_original_assets.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-doc-versions-contract-final.xml
```

故障注入仅写本轮隔离测试对象；未修改生产对象。首次夹具清理失败残留的 8 个 UUID 命名 PostgreSQL 表和 1 个 MySQL 配置表，已先核对精确名称、当前用户所有权及 `project_key=doc-versions`，再删除该目录登记的测试资产，并按依赖顺序清理明确测试表；未使用 CASCADE。新夹具对文档 ID 去重，清理计划表放入 finally，本次复验正常清理。

后续须完成并发/提交回执故障、全存储不可覆盖契约、默认参数请求归一、项目改绑重放归属、JSON 重启/失败回滚、历史初始化、生成/规划精确来源，以及前端类型/API/版本页面与真实浏览器验收。DOCX、受限解析、快照/差异、Git、P4/P5 保持未完成；完整 P0—P5 目标仍在进行。

本次交付是方案同步和专项证据，版本后端及测试修改留在工作区继续验收，不纳入本次文档提交。不执行生产迁移、不发布新接口。本轮未重新运行前端构建或浏览器，因为版本前端尚未接通；上一轮原件下载页面的结果仅保留为历史证据。原方案上传章节与项目方案逐字一致，Codex 镜像与项目方案逐字节一致，原始备份保留；提交不包含用户 `.gitignore` 或其他开发中代码。

## P3 独立上传版本、实际插件与精确来源（2026-10-04）

承接 `dc90df3` 后的工作区实现，先核对上传路由/目录三个存储实现、历史资产、生成与规划读取、实际插件和文件导入同类实现。原方案中的上传独立于 Git、生成模板不参与执行、项目内选取→初排→补全→最终冻结保持不变。以下替代上节“开发中”的当前状态，原始过程记录保留。

真实输入暴露的根因与修正：

- 省略上传默认参数后，同请求重试误报 409：服务显式归一请求内容，不按调用形状计算身份。
- 固定资产 ID 可挂到其他文档：内存/JSON 与 PostgreSQL 同步检查资产所属文档，PG 更新核对实际影响行数；版本 ID/序号也不可覆盖。
- 生成/规划先取目录再读内容，会混用旧元数据与新文本；两次读取之间项目改绑也可泄露：消费者使用实际读取响应中的文档身份，校验项目并冻结版本、内容资产、全文哈希/地址及目录修订。旧来源保留 updated_at 兼容，历史计划不改写。
- 历史目录初始化与兼容迁移缺失：旧目录显式初始化为 V1，保留旧 ID、对象和原上传时间。已知原件核对字节；无原件证据时保存现存派生内容及缺失状态，不伪造原件，不复制对象。
- JSON→PG 迁移只复制当前目录，会丢历史版本/资产/提交；修复后最新测试仍复现 V1 缺失但 V2 引用它时错误报告 skipped。迁移在既有文档跳过前验证身份键、完整父链/序号、最新指针、资产版本及提交固定版本；损坏报告 invalid，已有 PG 历史不变。同一实际目录分别破坏 9 种引用已验证，未推断或补造缺失历史。

实际修改范围：

- `schemas/api_docs.py`、`api_doc_store.py`、`api_docs_service.py`、`api/routes/api_docs.py`：现有目录增加版本指针；生产 PG 增加按目录表派生的 `_versions`、`_assets`、`_submissions` 表。目录/版本/提交同事务；独立资产持续保存 URL 变更后的旧派生物。首次上传请求身份可选，旧调用仍独立；新版本必须正式项目、请求身份、预期父版本。同请求返回原提交，即使当前已有新版本；内容等于当前版本不增版本，恢复历史内容追加新版本。
- 版本/原提交查询、历史全文和下载、历史初始化接口与前端类型/API 成对接通。下载验证实际大小/哈希，读错误显式返回；资产准备包含转换、预览及构造错误的引用核对/清理，提交结果未知时保留对象。并发同请求只提交一次，不同请求竞争同父版本返回冲突。
- `generation_pipeline.py`、`planning_sources.py`：冻结实际读取的文档/资产来源，拒绝项目变化和读取失败。历史正文有独立查询，不以当前正文替代。
- 实际 `ApiDocsPlugin.vue` 接入 `ApiDocVersions.vue` 和 `documentUploadRequest.ts`，同步中英文说明。sessionStorage 仅保留请求/签名/项目/父版本等，不存字节；首次/追加响应丢失可按原身份找回。重试保留原父版本，明确拒绝且查询原提交为 404 后才由用户显式刷新另建请求。重放旧快照后另取当前目录。未绑定项目先使用既有绑定操作。
- 新增真实文件/存储/迁移和浏览器专项；现有故障替身、清理及旧文档读取 fixture 对齐实际新增契约。浏览器共用宿主加载真实插件与全局 styles.css，不复制产品页面实现。

JSON 目录作为原有兼容存储，历史嵌入同一个目录数组原子写入，失败回滚与重启完整恢复已有专项。正常生产仍用 PostgreSQL/RustFS，可编辑配置用 MySQL；本步不引入依赖/配置域，不安装解析器。新表只在本次隔离存储中验收；没有执行生产数据迁移。兼容目录迁移沿用旧行为：当前归属设为未绑定，历史快照/提交原项目身份保持，显式绑定后才能重放原请求。

| 证据 | 实际结果 | 说明 |
|---|---|---|
| `p3-doc-versions-boundaries-before.xml` / `boundaries-first.xml` | 2 失败、11 通过 → 13 通过 | 默认参数重试错误，修复后边界专项 |
| `p3-doc-versions-sources-before.xml` / `sources-first.xml` | 6 失败 → 21 通过、1 失败 | 资产跨文档、旧元数据/新正文及项目改绑；剩余旧 fixture 未返回服务本来具有的 document envelope，修正测试契约 |
| `p3-doc-history-before.xml` / `first.xml` | 4 失败 → 26 通过 | 显式旧目录初始化缺路由，补齐内存/JSON/真实 PG/RustFS |
| `p3-doc-versions-backend.xml` | 67 通过、2 失败 | 预览故障输入使用空 paths，转换器正确不生成派生物；改用实际 GET 端点后纳入最终回归 |
| `p3-doc-versions-regression.xml` / `regression-final.xml` | 157 通过、1 失败、3 跳过 → 158 通过、3 跳过 | read_error 分支先报告真实读错误再校验文档 envelope |
| `p3-doc-history-catalog-before.xml` / `catalog-first.xml` | 1 失败 → 39 通过 | 原迁移丢版本/历史资产；真实迁移、旧派生下载及提交重放 |
| `p3-doc-history-catalog-integrity-before.xml` | 1 失败，3.70 秒 | 缺 V1 的 V2 历史错误报告 skipped，实际期望 invalid=1，得到 0 |
| `p3-doc-history-catalog-integrity-first.xml` / `integrity-final.xml` | 1 通过 → 39 通过，10.03 秒 | 补完整引用校验，9 种实际目录破坏均被拒绝，原 PG 历史不变 |
| `p3-doc-versions-regression-migration-final.xml` | **159 通过、3 跳过，38.08 秒** | 最终文档/版本/迁移/来源/生成/规划/导入/绑定/存储回归；3 项默认外部模型未执行 |
| `p3-doc-versions-regression-legacy-final.xml` | **160 通过、3 跳过，40.63 秒** | 补验只有原件资产、没有版本字段/history 的旧目录仍可迁移，保留身份/时间/实际下载；最终完整受影响回归 |
| `p3-doc-versions-browser-first.xml` / `browser-final.xml` / `browser-styled.xml` | 2 通过 → 3 通过 → **3 通过，14.92 秒** | 增加陈旧父版本、历史初始化，并补宿主真实全局样式；最后为实际插件/生产路由/隔离真实存储验收 |
| 前端三个文件专项 | **8 通过，1.85 秒** | 版本找回/历史内容、原件下载和既有导入源逻辑；unit 控制 crypto，Chromium 用真实 Web Crypto |
| `npm run build` | **类型检查及构建通过，Vite 5.96 秒** | 初次测试导入 node:crypto 因未声明 Node 类型失败；按已有测试边界控制 crypto，没有新增类型或产品依赖 |
| `p3-doc-versions-cli.xml` 与 `p3-doc-versions-cli/` | **1 通过，44.55 秒** | 完整生产 API lifespan/独立执行 CLI、真实隔离存储和 HTTP、受控模型、审批/创建后重启及终态重投；先于最后迁移校验修正 |

浏览器 JSON/截图及 Vite 日志按 `p3-doc-versions-browser.*`、`p3-doc-history-browser.*`、`p3-doc-assets-version-regression-browser.*` 保存。用实际 BOM/CRLF Markdown 验证旧下载逐字节一致，首次/追加提交后切断响应并刷新找回，不重复创建；其他窗口创建 V3 后旧页面返回 409，明确刷新父版本再恢复旧内容为 V4。同字节新请求不增版本；缺原件旧目录初始化后只有派生物，实际下载和全文正确。截图已人工查看；页面错误为 0。宿主是隔离页面，**未验收完整产品启动后的文档页面**。

最终命令（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_versions.py tests/test_api_doc_project_binding.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-doc-history-catalog-integrity-final.xml

$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:RUN_LIVE_UPGRADE_STORAGE_TESTS='1'
$env:RUN_LIVE_CASE_IMPORT_TESTS='1'
$env:RUN_LIVE_PLAN_BINDING_PATHS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_versions.py tests/test_api_doc_original_assets.py tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_api_docs_bom.py tests/test_api_docs_origin_security.py tests/test_api_doc_resolution.py tests/test_api_document_dependency_http.py tests/test_case_json_templates.py tests/test_case_planning.py tests/test_test_case_generation_pipeline.py tests/test_case_import_service.py tests/test_plan_binding_paths.py tests/test_artifact_storage_service.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p3-doc-versions-regression-migration-final.xml

$env:RUN_LIVE_API_DOC_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_versions_browser.py tests/test_api_doc_assets_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-doc-versions-browser-styled.xml

..\.venv\Scripts\python.exe -X utf8 -m py_compile src/schemas/api_docs.py src/application/documents/api_doc_store.py src/application/documents/api_docs_service.py src/api/routes/api_docs.py src/application/test_cases/generation_pipeline.py src/application/test_cases/planning_sources.py tests/test_api_doc_versions.py tests/test_api_doc_versions_browser.py tests/test_api_doc_assets_browser.py tests/document_plugin_browser.py
```

前端（agent_web）使用 `npm run test -- src/features/tools/plugins/ApiDocVersions.test.ts src/features/tools/plugins/ApiDocAssets.test.ts src/features/tools/plugins/ApiDocsPlugin.importSources.test.ts` 和 `npm run build`。完整 CLI 沿用已核对的执行专项入口，开启 `RUN_LIVE_EXECUTION_CLI_TESTS=1`，提前建立独立 `p3-doc-versions-cli/` 并设为 `QA_TEST_GATE_OUTPUT_DIR`，运行 `tests/test_execution_worker_cli.py`，不覆盖上一轮目录。3 个最终后端跳过明确是实际外部模型生成/规划/绑定，不是存储失败；不声称默认模型自主执行通过。

10 个 Python 文件实际编译通过。原始失败 XML 留存。迁移首次失败清理已丢元数据引用的 2 个对象前，核对精确测试目录、提交集合、对象前缀及哈希；新夹具即使迁移失败也清理本次已登记资产。没有操作生产对象或无关数据。

直接更新用户粘贴方案原文件、项目 PLAN 和所选 Codex 镜像，保留原始备份；正文首页、§3.1、接口、实施顺序、验收和第六节统一到以上实际状态。仅本轮源码/测试/方案/证据提交，用户 `.gitignore` 留在工作区。完整 P0—P5 尚未完成：DOCX 策略、受限解析/快照、差异、Git、P4/P5，P2 步骤归属/未知副作用/计划修订与默认模型，以及人工 UI 录制/Docker 仍需后续调研、实现和验收。

最后兼容检查：新增真实 PG/RustFS 旧目录资产迁移专项后扩大回归 160 通过、3 个默认模型项跳过（40.63 秒）。严格历史校验只拒绝已有历史中的确定性坏引用；没有历史的旧目录仍按既有方式迁移，不自动生成版本。修复日志输出无效项的位置及安全原因，未输出外部正文。方案当前状态已更新到该结果，早一轮 159 项的原证据保留。

交付检查：当前方案本地链接均可定位，粘贴方案上传章节与项目方案一致，所选 Codex PLAN 字节一致，原始备份保留。暂存的 69 个相关文件均经范围检查，`.gitignore` 未暂存；源码/测试/Markdown 的 diff 检查通过。完整 diff 检查有 2 处 `p3-doc-versions-sources-before.xml` 原始失败输出尾空白，保留真实证据，不改写该失败 XML。敏感格式检查初次将旧 task 文件名中的 sk 子串误匹配；核对位置并加词边界后没有真实匹配，未输出任何匹配值。


## 方案文件同步核对（2026-10-04，基线 8c49caf）

本次按用户“更新这个方案”的要求，仅修订项目 PLAN.md、所贴原方案和 Codex 计划镜像，并记录文档核对结果。保留原方案章节编号；生成模板只参与生成、项目内选取/初排/AI 衔接补全/最终冻结，以及独立上传版本与可选只读 Git 的约定不变。

实际检查了 upload_version、原件保存/全文读取、API 文档格式策略、解析依赖及配置/进程服务。修正当前正文中“未声明/未安装解析依赖”的过时状态，补齐持久解析任务、输入冻结、快照、故障恢复、前端/消费者和功能验收的依赖顺序。既有工作区代码未接入生产初始化、解析路由或持久快照，不能计为解析功能交付。

使用本地 importlib.metadata 核对安装版本 python-docx 1.2.0、pypdf 6.19.0；读取已有 p3-document-parsers-config-first.xml，确认 33 项中 2 失败、0 跳过，即 31 通过。两项失败均为测试夹具 AttributeError；本轮未修改或重跑该测试，不报告配置专项已通过。上一阶段版本回归结果沿用其原记录，不声称本轮重新运行。

文档验证包含 git diff --check、三份方案的解析章节一致性、项目 PLAN 与 Codex 镜像字节一致性及本地引用存在性。仅暂存本轮方案文档，既有源码/测试/锁文件和用户 .gitignore 保留工作区状态；不推送、不创建 PR。

## P3 轻量解析、持久任务及快照消费（2026-10-04）

依据先于实现：现有 `_read_record_content` 直接解码对象字节，PDF 上传通过不能证明文字可读；原 `api_document` 不接受 DOCX。既有 `CaseImportProcess`/`import_worker` 已有受限进程；`PostgresPlanningTaskStore`、`PlanningTaskHandler`、专用规划运行时已验证业务任务与传输分离。复用相同的固定输入、项目事务/Outbox、租约 fencing 和结果检查点方式；解析不使用 Session、模型或浏览器启动。查阅本地课程《评测工程化与CICD》《Agent轨迹与协议鲁棒性》的版本/轨迹/幂等要求，以及 python-docx Document 和 pypdf 文本提取官方说明。DOCX 页码/页眉脚/批注/修订和图像、PDF 表格/标题与扫描页的能力限制直接记录，不由 AI 编造补齐。

实际实现：

- 统一解析契约及工厂：原件 SHA/大小、完整文本、块/章节/表格、来源位置、实际选项、解析器/依赖版本、能力、complete/partial/unsupported 与警告。Markdown 保留 BOM 解码后的 CRLF 全文及确定性行位置；DOCX 正文顺序、表格/嵌套/合并提示；PDF 分页文本及结构/OCR 警告。Docling HTTP 只保留能力注册与统一结果转换，未部署/调用容器。
- 共享 `LimitedParserProcess`：Excel 与文档适配器共用固定内部模块入口，独立子进程/线程等待，输入与输出限制、CPU/RSS/时间限制、取消及后代回收；正常失败完整保留 stdout/stderr，洪泛保留有界前缀并明确标记。实际重复取消测试复现清理前返回，改为持续 shield 清理任务，直至其 finally 完成。
- MySQL `document.parsing_settings`：缺失时初始化全局默认、项目覆盖、修订与实际完整策略复制；提交时拒绝归档或不可用解析器。新增项目配置 API，沿用现有 AppContainer 配置存储与日志。
- PostgreSQL：沿用目录表派生 `_parse_tasks`、`_parse_snapshots`，固定文档/原件版本、资产、配置修订/选项及解析器身份；项目事务重新核对归属与原件，任务和 Outbox 同事务。并发同请求只保存一次，改变版本/选项的同身份请求被拒绝。Lease/attempt fencing、检查点及终态不可重领；终态和快照同事务，结果检查点恢复不重新解析，原 deadline 不重置。
- RustFS：原件沿用既有资产；解析结果及失败轨迹分别保存。失败轨迹保留退出码/完整受限 stdout/stderr，不写普通日志。结果写入后先保存检查点，再提交终态；未知提交结果不删除工件。快照读取核对大小/哈希、原件身份、实际选项/解析器与元数据；重解析追加，历史不改写。
- 后端 API、独立 `run_document_parse_worker`、生产初始化与只读规划 Worker 已接线。现有文档全文按固定原件版本读取已保存快照；无快照 PDF/DOCX 明确报错，禁止二进制文本回退。生成和规划保存具体解析快照/哈希/结构/警告；旧 V1 与新 V2 内容各读对应历史。因新业务外键引用而不能删除的文档返回 409，数据库回滚保留原件；实际错误是 SQLAlchemy IntegrityError 包装的 ForeignKeyViolation，按真实包装层识别。
- DOCX 原件使用专用 `requirement_document` 策略，未扩大所有 API 文档准入。前端文件选择、解析契约/API 消费方成对同步；尚未增加完整解析任务/历史/警告面板。

新增精确依赖 python-docx 1.2.0（MIT）、pypdf 6.19.0（BSD-3-Clause），按官方/PyPI 元数据核对与 Python >=3.11 兼容，实际运行 Python 3.11.16。只安装解析依赖及传递的 lxml 6.1.3；根 `uv lock` 更新时补入此前已在 dev 声明但未锁定的 Alembic/Mako/MarkupSafe，已有包版本无变化。README 明确根锁文件为生效锁，后端历史 `uv.lock` 不改。`uv lock --check`、`uv pip check` 实际通过。

安装 lxml 后扩大回归复现 Excel workbook XML 原始错误从 ElementTree ParseError 变为 lxml XMLSyntaxError。检查已安装 openpyxl XML 分支与两种异常 MRO，使用共同 SyntaxError 基类归一同一文件结构错误；未关闭 lxml。实际工作簿 XML 和工作表 XML 故障样本、宏内容及两种解析路径均重新验证。

| 证据 | 实际结果 | 说明 |
|---|---|---|
| `p3-document-parsers-config-first.xml` / `config-fixed.xml` | 31 通过、2 失败 → 33 通过 | 旧配置夹具错误将 tuple 当对象，按已有夹具的真实返回契约修正；真实 MySQL 变体已执行 |
| `p3-document-parser-boundaries-before.xml` / `boundaries-fixed.xml` | 26 通过、3 失败、1 跳过 → 39 通过 | 重复取消提前返回、空/仅页眉 DOCX 误报 complete；开启真实证据生成后无跳过 |
| `p3-document-parser-import-regression.xml` / `import-fixed.xml` | 68 通过、1 失败 → 70 通过 | lxml 改变 openpyxl 异常类型，扩大真实导入回归 |
| `p3-document-parser-xlsx-compatibility-before.xml` / `xlsx-elementtree.xml` | 1 通过、1 失败 → ElementTree 17 通过 | 加入实际损坏工作表样本；lxml 路径随 70 项回归通过 |
| `p3-document-parse-tasks-first.xml` / `tasks-http.xml` / `tasks-cli.xml` | 4 → 5 → 6 通过 | 真实 PG/MySQL/RustFS；Outbox/Redis Worker 类、独立 Worker CLI |
| `p3-document-parse-snapshot-consumers.xml` / `consumers-fixed.xml` / `docx-sources.xml` | 各 1 失败 | 新夹具先误用生成 collect 方法名、再漏必填 objective；修正后实际删除异常又暴露 SQLAlchemy 包装层。按实际签名与失败修正，最终均纳入扩大回归 |
| `p3-document-parse-integration-final.xml` | **163 通过、3 跳过，89.71 秒** | 本轮文件/进程/配置/任务/CLI/导入/版本/资产/生成/规划/生命周期回归。三项跳过：外部默认模型、PG/MySQL 生命周期开关 |
| `p3-document-parse-lifespan-live.xml` | **8 通过，10.63 秒** | 实际开启 PG/MySQL，补验上行两项跳过。该文件含其他生命周期回归，不将 8 项全部重复计入上行总数 |
| `p3-document-parse-cli-logging.xml` / `p3-document-parse-cli/` | **1 通过、7 未选择，11.74 秒** | 按现有 configure_logging 接入日志，实测独立 CLI 输出 processed=1，任务身份及终态日志可查；快照/结果和完整输出留存 |
| `p3-document-parser-import-browser.xml` | **3 通过，12.94 秒** | 真实 Chromium/既有导入组件/真实 API/隔离存储，rows/split/JSON；没有验收新的解析页面 |
| `npm run build` | **类型检查与构建通过，Vite 14.11 秒** | 前端新增契约/API 与 DOCX 文件选择；没有以构建代替解析页面验收 |

实际样本：`p3-document-parser-files/` 内的带正文和表格 DOCX、分页文本 PDF、BOM/CRLF Markdown 及结果 JSON。新增任务专项将这些类型实际上传、读取 RustFS 原件、派发、解析与回读；实际 PDF 内容 7→9 天形成新原件 V2，旧 V1/快照保持 7 天。库升级/选项切换对已读依据不原地改写。CLI 工件引用测试资源，验收结束已清理隔离表、Redis 流和对象，文件工件保留作证据，不宣称该临时地址仍在线。

浏览器测试沿用旧入口会写入 `p1-import-browser-*`；本轮检查结果后另存为 `p3-document-parser-import-browser-*`，恢复旧 P1 证据，避免覆盖历史验收。其截图为原导入组件的隔离宿主，未表示完整产品启动或 DOCX 解析面板已验收。

主要命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
$env:RUN_LIVE_CASE_IMPORT_TESTS='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_DOCUMENT_PARSER_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-parser-files')
$env:QA_DOCUMENT_PARSE_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-parse-cli')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_parsers.py tests/test_document_parser_process.py tests/test_document_parser_config.py tests/test_document_parse_tasks.py tests/test_case_import_process.py tests/test_case_import_parser.py tests/test_case_import_service.py tests/test_api_doc_versions.py tests/test_api_doc_original_assets.py tests/test_api_doc_catalog_atomicity.py tests/test_test_case_generation_pipeline.py tests/test_case_planning.py tests/test_lifespan_resource_cleanup.py -q --tb=short -rs --junitxml=../docs/upgrade_2026-10-03/p3-document-parse-integration-final.xml

$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_lifespan_resource_cleanup.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-parse-lifespan-live.xml
```

独立 CLI 日志专项使用同一真实开关与 `QA_DOCUMENT_PARSE_TASK_EVIDENCE_DIR`，运行 `tests/test_document_parse_tasks.py -k independent_worker`；导入浏览器开启 `RUN_LIVE_CASE_IMPORT_BROWSER=1`。ElementTree 专项只在对应测试进程设 `OPENPYXL_LXML=False`，未修改产品配置。新/改解析 Python 文件已实际 py_compile；功能验收以前述真实文件/存储/进程为准。

本阶段是完整目标中的解析后端与来源基础。待完成：解析页面、任务/快照列表和警告/历史操作、更多响应丢失/事务/网络故障边界及完整生产 API 启动；确定性差异、Git 来源、需求影响与追踪/增量草稿；P2 步骤归属/未知副作用/计划修订/默认模型，以及人工 UI 录制、Docker 真实证据、模块化生成和 P5。未运行生产历史迁移，未部署 Docling，未调用默认外部模型，本轮不宣称 P3 或 P0—P5 完成。

收尾核对：已直接更新用户所贴方案原文件及项目 PLAN，并同步所选 Codex 计划；原始附件备份保留。修正上传章节、接口和第六节中仍把解析快照/来源消费写为待实现的过时表述，加入解析专项状态，上一阶段 160 项版本回归与当前 163 项解析回归分别标注。当前正文明确后端已接通、解析页面/完整生产和差异/追踪未完成；原件保存与版本比较不依赖 Git，生成模板不参与执行的约定保持一致。

实际执行三份方案的一致性、全部绝对本地链接存在性及 XML 结果核对：项目 PLAN 与 Codex 镜像逐字节一致，两份方案的解析章节一致，最终结果为 163/8/1/3/17 项通过且对应 XML 无失败。该步骤读取此前真实测试记录，没有重新运行这五组功能测试。人工查看本轮导入截图，确认显示已提交批次、完整 JSON 和未登录负向条件；截图是既有导入组件的隔离宿主，不属于新解析页面验收。

交付文件范围检查为 75 个相关文件，用户 `.gitignore` 排除；对源码、测试、Markdown、JSON/XML 和 CLI 文本工件做敏感格式扫描，无匹配，未输出匹配值。29 个本轮 Python 文件实际 py_compile 通过；`uv lock --check`、`uv pip check` 和已跟踪文件 `git diff --check` 通过。根锁仍是生效锁，生产历史数据未迁移。仅提交本轮相关实现、测试、方案和原始证据，不推送、不创建 PR。

暂存后实际发现 PDF 样本检出会从 897 字节变成 978 字节：`git show :路径` 与原件一致，`git cat-file --filters` 与原件不一致。依照 [Git 官方属性说明](https://git-scm.com/docs/gitattributes) 中 `-text` 禁止检入/检出换行转换的规则，只在本轮样本目录增加针对 `requirements.pdf` 的属性，不修改 Git 配置。修复后原件、暂存对象及检出字节相同，均为 897 字节；pypdf 实际读取检出字节，文本与原件相同，证据 JSON 的来源哈希匹配。最终相关文件数为 76；源码/文档的暂存 diff 检查通过，完整 diff 检查仍有原失败 XML 的两处尾空白，保留原始测试输出。

## P3 解析产品入口、默认快照及生产解析进程（2026-10-04，基线 9be9aa7）

先查证：已有解析提交/单任务/单快照 API，没有分页目录或操作面板；新版本选择没有 DOCX。真实 PG/MySQL/RustFS 的两次 PDF 解析复现“旧 plain 任务晚完成覆盖新 layout 默认依据”，目录 HTTP 返回405，原始证据 `p3-document-parse-catalog-before.xml` 为2失败。沿既有规划分页契约补目录；默认快照按 PostgreSQL 在项目/文档锁后记录的任务提交时间及身份选取，列表采用相同顺序，不依赖 Worker 完成时间。历史来源继续引用固定 snapshot。新任务失败或排队时展示其状态，保留已保存结果。

实际修改：

- 后端契约、路由、应用服务、PG存储同步增加能力目录、项目/文档/固定版本分页、失败诊断读取。失败工件核验大小/SHA/退出码后返回实际错误、stdout/stderr；完成任务不伪造失败诊断，损坏工件明确返回409。目录查询仍核对项目/文档及可选历史版本。
- `ApiDocsPlugin.vue` 接入独立 `ApiDocParsing.vue`、`DocumentParsingSettings.vue`、`DocumentParseSnapshot.vue`。支持固定原件选择、提交/同请求找回、轮询、分页历史、警告/全文/结构/位置、完整JSON下载及失败诊断。MySQL设置控制后续新任务；能力目录来自实际工厂，未支持格式和缺原件可见。新版本上传选择补DOCX。
- 浏览器保存非敏感请求身份/固定版本；响应未知不能换请求，缓存终态重新查服务端，显式选中的历史视图不会被后台完成覆盖。身份变更/关闭阻止旧返回并停止轮询；MySQL配置不可用时PG历史仍可读。归档历史可读，新提交与配置修改禁止。
- 新增真实目录、实际插件浏览器、生产API/独立解析CLI三组测试；原件使用本轮目录的Git属性保持字节。生产专项复用既有隔离完整运行时/进程夹具，模型配置仅供API启动，解析实际模型调用0。

| 验证及原始证据 | 实际结果与边界 |
|---|---|
| `p3-document-parse-catalog-before.xml` / `catalog-first.xml` / `catalog-fixed.xml` | 2失败 → 2通过1失败 → **3通过，12.03秒**。中间夹具错误认为加密错误必在stderr；实际固定Worker通过结果文件保存error，stdout/stderr可空，按真实契约修正。后续能力目录断言随扩大回归通过 |
| `p3-document-parse-ui-regression.xml` | **129通过、2跳过，62.36秒**。含真实PG/MySQL/RustFS、目录/进程/版本/来源及受影响回归；跳过为实际文件证据目录未设置与外部默认模型 |
| `p3-document-parse-ui-parser-files.xml` / `ui-parser-files-verified.xml` | 首次筛选名称错误，16未选择，不能算通过；设置真实样本目录后正确筛选 **1通过、15未选择，0.51秒**，三个格式原件及结果实际生成。上行存储/文件项已补验，外部默认模型未验 |
| `p3-document-parse-browser-first.xml` / `browser-fixed.xml` | 1通过1失败 → **2通过，22.80秒**。响应中断夹具在HTTP尚未完成前检查捕获结果；改为等待重试按钮可用，未改产品逻辑 |
| `p3-document-parse-browser-final.xml` / `browser-safe-final.xml` | 各2通过1失败。新增Markdown夹具中的脚本被既有扫描拒绝，保留扫描并断言拒绝，用普通标签验证文本呈现；第二次夹具误把Playwright page.url属性当函数，按实际API修正 |
| `p3-document-parse-browser-verified.xml` | **3通过，32.86秒**。实际ApiDocsPlugin/样式/生产路由、隔离PG/MySQL/RustFS/Redis、Worker类与独立解析进程。实际浏览器，并非完整产品App启动 |
| `p3-document-parse-production-first.xml` / `production-final.xml` | **1通过，41.09秒** → 补实际能力API后 **1通过，43.36秒**。真正uvicorn `src.main:app` 全生命周期、独立解析Worker CLI及真实隔离存储；不调用默认外部模型 |
| 受影响前端四文件与 `npm run build` | **15通过，3.37秒**；类型检查与构建通过，Vite **8.94秒**。首次类型检查发现组件名与类型名重复，类型别名修正后通过 |

实际浏览器场景：DOCX先上传带表格的7天原件，POST实际提交后中断响应，刷新原请求找回，只有1任务/attempt1；新原件9天形成V2，新解析后旧V1和快照仍7天，完整JSON下载实际读取比对。PDF通过设置面板将plain存为layout，形成新快照，旧快照保持；加密PDF真实失败，无快照，不解码二进制回退，失败诊断忠实显示空stdout/stderr。Markdown原件BOM/CRLF逐字节保留，标签作为文本；危险脚本被既有扫描拒绝；归档后历史仍可读，界面无新提交/保存入口，实际HTTP新解析409。人工查看三份截图，核对警告、原件/快照身份、失败诊断和归档文本展示。

生产专项：真实DOCX上传核对RustFS字节；提交后MySQL超时60改80；API重启仍同请求同任务、Outbox1；独立CLI执行冻结60的策略，正文/表格7天，历史全文引用实际快照。终态重投没有重新解析、attempt1，模型调用0。完整API和CLI stdout/stderr、summary保存于 `p3-document-parse-production/`。结束后清理本次隔离PG数据库、MySQL表、Redis容器及RustFS对象；从检查点/失败记录补查工件以避免失败断言漏清理，没有执行生产历史迁移。

关键重现命令（Agent_Server；依赖现有测试数据库/对象存储与Docker环境）：

```powershell
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_parse_catalog.py -q --tb=short

$env:RUN_LIVE_DOCUMENT_PARSE_BROWSER='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_parsing_browser.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-parse-browser-verified.xml

$env:RUN_LIVE_DOCUMENT_PARSE_PRODUCTION='1'
$env:QA_DOCUMENT_PARSE_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-parse-production')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_parse_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-parse-production-final.xml
```

前端（agent_web）命令：`npm run test -- src/features/tools/plugins/ApiDocParsing.test.ts src/features/tools/plugins/ApiDocVersions.test.ts src/features/tools/plugins/ApiDocAssets.test.ts src/features/tools/plugins/ApiDocsPlugin.importSources.test.ts`、`npm run build`。表中已有终态结果不为文档同步重复运行。

直接同步项目PLAN、用户粘贴原文件及Codex镜像，保留附件章节结构与原始备份。首页/§3.1/§3.3/接口/第六节统一到实际解析入口及生产专项状态；旧阶段的163项后端记录保留为历史，不与本轮129项相加。上传不使用Git、模板仅生成、项目内选取→初排→补全→冻结保持一致。

本轮交付是完整P0—P5中的解析入口与生产解析进程专项。完整产品App/导航/项目页、更多事务/网络故障、生产历史迁移未验；确定性差异、可选Git、影响/追踪/增量草稿仍待实现。P2步骤归属/未知副作用/计划修订/默认模型、模块化生成、DeepSeek人工录制链、Docker实际性能/安全证据及P5保持未完成。未部署Docling，不标记整个目标完成。

收尾实际核对：五份最终XML的计数/失败/跳过与正文一致；项目PLAN和Codex镜像字节一致，附件上传/解析章节与正式方案一致且保留原编号，绝对本地链接均存在。前后端新增能力/目录/诊断契约引用核对通过，13个相关Python文件实际编译通过，源码/测试/Markdown及文本证据敏感格式扫描无匹配；未输出可能的匹配值。仅本轮相关文件进入提交，用户 `.gitignore` 留在工作区；三个新增被忽略的后端测试显式纳入提交。

暂存范围实际为65个本轮相关文件；8份Markdown/PDF/DOCX原件的工作区、暂存对象与Git过滤后检出字节全部一致，结果JSON中的来源哈希匹配。源码/测试/方案的暂存diff检查通过。完整diff检查报告6处：两个实际BOM/CRLF Markdown原件的5行CR字符，以及 `p3-document-parse-catalog-before.xml` 原失败输出1处尾空白；为保留真实原件哈希及失败证据未改写这些工件，不将完整diff检查报告为全通过。仅本地中文commit，不推送、不创建PR。

## 完整产品文档路径实际验收（2026-10-04，基线 96d8f44）

上一目标轮属于实质进展：解析入口、默认快照与方案已提交。继续先读目标附件及工作区，确认仅用户 `.gitignore` 未提交。查证此前 `document_plugin_browser.py` 生成插件宿主页，生产解析专项只用HTTP；它们未验证正式 `index.html`/main/App/Pinia/router、侧栏和ToolsView事件。核对App初始化的Agent/工具/模式查询、会话创建/SSE，ProjectsView创建表单和ToolsView文档Tab。安装的Vite `resolvePreviewOptions` 明确preview继承server.proxy；无需造入口或改代理配置。

先写入PLAN的实际验收设计，再新增 `tests/test_document_full_product.py`，复用已验证的 `isolated_runtime`、生产进程、受控模型启动配置和解析Worker CLI。独立PG数据库隔离所有业务表，MySQL使用专属配置表，Redis为本次容器，原件/解析结果真存RustFS。前端实际执行项目 `npm run build`，用既有Vite配置启动preview，浏览器打开正式首页并操作真实侧栏/路由。没有替换AppContainer服务、伪造API响应或生成插件宿主。

| 原始证据 | 结果与处理 |
|---|---|
| `p3-document-full-product-first.xml`、`p3-document-full-product/` | **1失败，89.34秒**。页面及项目API均成功；测试使用完整无图标名称定位“新建项目”超时。按真实模板改用对应页面按钮范围及文本。截图、网络、构建/API输出保留 |
| `p3-document-full-product-fixed.xml`、`p3-document-full-product-fixed/` | **1失败，59.12秒**。已界面创建项目、上传原件、MySQL保存超时70、独立CLI解析V1并上传V2；测试错误按直接文档读取版本回执的original_asset。按 `ApiDocVersionResult.document` 实际契约读取，使用Pydantic验证。没有改产品代码 |
| `p3-document-full-product-verified.xml`、`p3-document-full-product-verified/` | **1通过，62.08秒**。实际正式前端构建/App、生产API、独立解析CLI与真实隔离存储完整文档流程；类型检查与构建通过，Vite **6.56秒** |

最终证明：浏览器从首页侧栏创建正式项目；工具文档Tab上传正文/表格DOCX7天；设置面板保存项目超时70，直接MySQL读取内容和修订一致；CLI V1成功，另上传表格9天V2并CLI解析成功；离开文档页进入项目页，停止并重启API，刷新真实App再进入文档页，旧V1快照仍7天，下载的完整JSON与原结果逐项一致。PG直接核对项目名称、2个原件版本和2个快照，任务列表正好2项。无页面错误、解析模型调用0。网络记录和built_assets证明浏览器加载正式构建资产，未使用临时探针入口。

故障记录不抹除：计划内停止API的窗口出现一次会话轮询代理500，preview还记录SSE ECONNRESET和连接ECONNREFUSED。测试明确用停机起止范围区分已注入故障与正常业务请求；正常及恢复后API请求无错误，页面状态恢复，不能声称整个过程中HTTP零失败。完整stdout/stderr留存，空stderr仍按原样保存。

第二次夹具在读取回执时失败，V2已提交但尚未加入owned列表。隔离数据库已清理后，依据截图中确切文档/版本身份和实际上传路径，读取唯一V2对象并与临时DOCX逐字节比对后删除；`asset-cleanup.json`保存大小/哈希及动作，未操作其他对象。新夹具从本次隔离PG资产及解析检查点/失败记录补查所有工件，断言失败也不会丢失清理依据；最终隔离数据库/表/容器/对象均由原运行时清理。历史工件引用临时地址，不保证仍在线。

本轮主命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-full-product-verified')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-full-product-verified.xml
```

两次失败分别使用first/fixed输出目录和XML，不覆盖历史。本轮没有更改产品源码、依赖或生产配置；新增的是实际产品验收及方案/证据。最终类型检查/构建已经由该测试实际运行；既有后端129项/插件3项未在本轮重跑，不重复计为本轮通过。人工查看完整产品截图，确认正式壳、Tools文档入口、不可变历史快照及原文表格7天可读。

更新项目PLAN、用户所贴原方案及Codex镜像，保留原附件章节与备份；仅文档主流程的完整产品入口由待验改为本轮已验。未操作的其他项目/用例/模板/导入/Agent业务页面、更多事务/网络故障组合、生产历史迁移、默认外部模型、P3.4确定性差异、Git和P4/P5仍未完成。全文目标不缩小，不标记完整P0—P5完成。

交付核对：本轮51个相关文件（新增实际验收测试、方案与原始证据），用户 `.gitignore` 不暂存。新测试实际py_compile通过；原附件上传/解析章节和正式方案一致，Codex镜像逐字节一致，绝对本地链接均可定位。三份XML的失败/终态计数、实际来源哈希、项目设置/版本/快照身份及计划内500记录与正文一致。源码/方案/文本工件敏感格式扫描无匹配，两个DOCX的工作区、暂存和过滤后检出字节一致。源码及方案暂存diff检查通过；完整diff检查保留两份原始 `last-page.txt` 在“选择新版本文件”处的实际尾空格，不改写页面采集证据，不报告完整diff全通过。只创建本地中文commit，不推送或建PR。

## P3.4 确定性差异内核与真实固定输入（2026-10-04，基线 44f6044）

承接完整产品文档验收，先读目标原附件并核对工作区仅用户 `.gitignore`；不把完整P0—P5缩成文档专项。核对解析schema、工厂、快照读取/完整性验证、受限进程、PostgreSQL任务租约及已有真实存储夹具。参考项目 `analyze_document_diff` 分别截取新旧前6000字后交模型分类，不能直接采用为确定性全文比较；查证Python3.11官方difflib的二次复杂度、autojunk及opcode契约。研究结果先写入 `P3_4_DESIGN.md`，同步正式PLAN、原用户粘贴附件及Codex镜像，随后实现。

新增文件：

- `schemas/document_differences.py`：两侧固定引用、实际比较策略、raw/normalized行和字符区间、章节/表格、来源及警告；结果保留实际预算，空侧不能伪造来源，区间与原文长度一致。
- `application/documents/difference_engine.py`：标准库SequenceMatcher，明确关闭autojunk，先去除完全相同前后段；仅归一CRLF/CR→LF，保存回映射。完整行差异保留，字符细化超预算显式omitted；差异/输入边界拒绝不返回截断成功。ordinal结构ID只在本快照内引用，章节按标题/层级/父标题路径、表格按完整行列内容比较。partial及能力警告保留，unsupported拒绝；解析依据改变不自动判为需求改变。
- `difference_process.py`、`difference_worker.py`：复用既有LimitedParserProcess，不复制监控或任务平台；JSON准备/验证在线程，昂贵计算在固定内部子进程。资源限制、实际退出码和stdout/stderr走已有设施，取消等待进程/临时目录回收。尚未配置新的MySQL持久比较设置或公开接口。
- `tests/test_document_differences.py`、`tests/test_document_difference_storage.py`：真实文件/进程及真实MySQL/PostgreSQL/RustFS固定输入；不将只验证JSON形状算成功，按完整差异重建新文本并核对每段源位置。

| 原始验证记录 | 实际结果 |
|---|---|
| `p3-document-difference-core-first.xml` | **21通过、1失败，6.43秒**；DOCX夹具错误期待列0，实际 `parser_factory.table_rows` 使用 `[body,row,column]`，第二行第二列为 `[2,1,1]`。按代码和实际结果修正断言，无产品补丁；该轮DOCX失败前未写输出工件，后续改为断言前保存工件 |
| `p3-document-difference-core-verified.xml` | **51通过、1跳过，8.91秒**，当时比较及既有解析回归；实际文件输出项缺专属目录，明确跳过 |
| `p3-document-difference-storage-first.xml` | **3通过，14.19秒**；三种格式的真实上传版本、MySQL设置、PG/RustFS快照和比较 |
| `p3-document-difference-final.xml` | **55通过、1跳过，24.34秒**；增加2万行相同重复内容及策略/范围校验 |
| `p3-document-difference-parser-files.xml` | 上述实际文件输出项单独补验 **1通过，0.58秒** |
| `p3-document-difference-reconstruction.xml` | **26通过，40.47秒**；raw及normalized完整差异和字符细化重建，真实存储复验 |
| `p3-document-difference-delivery.xml` | **27通过，23.80秒**；增加实际无文本PDF解析后比较拒绝、结果内固定实际比较策略 |
| `p3-document-difference-line-before.xml` | **1失败，0.54秒**；真实复现Unicode分行错位：比较器自写CR/LF拆行，Markdown工厂用splitlines，来源为2行而差异显示1行 |
| `p3-document-difference-final-delivery.xml` | **57通过、0跳过，31.07秒**；改为与工厂相同splitlines(keepends=True)，保留Unicode字符；全部新增及既有解析器/进程回归通过 |

最终真实存储专项：正式项目上传两版真实MD/DOCX/PDF，MySQL配置超时65后改80，PG任务及解析快照保存各自固定修订；RustFS原件字节及解析JSON哈希核对。现有快照读取服务验证后交比较进程，得出正文/表格/分页的7→9，输入引用准确；配置只有任务超时改变不产生解析依据变化。比较前后的PG快照记录与RustFS原件/快照JSON一致，旧V1仍可读；错误项目不能读取输入。既有解析任务直接由测试内服务领取并启动真实子进程，不把此测试称为新的比较Outbox/Redis/独立CLI验收。隔离对象/业务表继续按已有夹具清理，工件URI不保证仍在线。

最终主命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_DIFFERENCES='1'
$env:QA_DOCUMENT_DIFFERENCE_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-difference-core-final-delivery')
$env:QA_DOCUMENT_DIFFERENCE_STORAGE_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-difference-storage-final-delivery')
$env:QA_DOCUMENT_PARSER_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-difference-parser-final-delivery')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_differences.py tests/test_document_difference_storage.py tests/test_document_parsers.py tests/test_document_parser_process.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-difference-final-delivery.xml
```

每轮工件使用独立目录/XML，保留首轮夹具失败及真实分行失败，不覆盖历史；证据目录内 `.gitattributes` 保留MD BOM/CRLF及DOCX/PDF原始字节。历史JSON为当时内核契约记录，最终交付以final-delivery为准，不把多次重跑相加为覆盖数。

直接更新用户所贴方案而非只在回复里更正；保留附件编号、原始备份，Codex镜像与正式方案同步。上传不依赖Git、模板只生成、项目选取→初排→补全→最终冻结的既有约定保持。新增研究文档记录依据、计算/来源边界、后续任务/页面接入与逐步验收。

本轮没有改前端、公开API、业务存储schema、依赖或生产配置；因此没有运行前端构建/浏览器，也没有声称比较入口已可用。比较持久任务/配置/结果存储、Outbox/Redis/CLI、公开API/前端、Git及P4影响/追踪/增量草稿继续实施；其他P2/P0/P5及生产历史迁移仍待验，Docling不部署，完整目标未完成。下一步先研究共同持久任务生命周期，不能复制第二套平台或把比较伪装成解析快照。

收尾实际核对：正式PLAN与Codex镜像逐字节一致，原附件差异章节内容一致且保持原编号；模板仅生成、初排后补全、上传无Git依赖的约定及绝对文件链接已检查。6个新增Python文件实际py_compile通过，6组最终core/storage的真实原件哈希/大小、解析快照哈希和7→9结果与XML计数一致。敏感格式扫描133个文本/源码文件无匹配；初始无边界模式把旧 `task-pool-*.xml` 文件名中的 `sk-` 子串当匹配，改用token边界重新扫描，无真实凭据匹配。

本轮仅171个相关文件暂存，两个被忽略的新后端测试显式 `git add -f`，用户 `.gitignore` 未暂存。58份原件的工作区、暂存及Git过滤后检出字节完全一致。源码/测试/方案的暂存diff检查通过；全范围检查保留 `p3-document-difference-core-first.xml` 首轮真实失败输出中的一处 `E` 行尾空白，不改写原始失败记录，不报告全范围diff检查通过。只创建本地中文commit，不推送或建PR。

## P3.4 持久比较服务与共用租约Worker（2026-10-04，基线 b701b9d）

前一轮属于实质进展：比较内核、真实固定输入及方案已提交。继续先读目标原附件，核对工作区只剩用户 `.gitignore`；不结束完整P0—P5目标。深读解析schema、PostgresDocumentParseStore全生命周期、MySQL解析配置、解析快照读取、DocumentParseTaskHandler、RedisTaskWorker、独立解析运行时/CLI和事务失败样本，再写入 `P3_4_DESIGN.md` 的实现前结论。

采用根因层的共用实现：原解析Store含通用项目锁、幂等、任务/Outbox同事务、数据库截止时间、fencing、检查点、终态与列表查询，将其抽成内部 `PostgresDocumentOperationStore`；原解析Store仅保留原有DTO/表名/phase/log及原件/解析器校验。不是再复制一套任务平台，也不将比较塞进解析DTO。领取/心跳/终态重投抽成 `DocumentOperationTaskHandler`，解析和比较各自的Command、task_type、日志身份保持清楚。研究记录先于对应源码变更。

新增比较Store使用独立 `_diff_tasks/_diff_snapshots`。两侧source snapshot外键、固定完整元数据及MySQL策略修订、算法/Python版本保存到seed；正文不进任务表。`version_id`只作新侧版本筛选，旧/新snapshot字段始终可查。通过现有解析service新增metadata读取方法；提交不下载大对象，事务内重新核对两侧元数据及项目归属。MySQL `document.difference_settings` 提供全局默认和项目覆盖；历史读取/同请求重放不读取当前设置。

比较Service执行读取已固定并验证哈希的两侧对象，调用上轮已验受限进程。输入读取/计算受原数据库预算控制，恢复不重置deadline；policy记录实际剩余超时、不能放宽其他限制。结果先RustFS、再PG检查点、最后发布不可变结果；已保存检查点恢复核对结果后完成，不再读原输入/比较/当前设置。成功结果核对固定侧引用、hash、summary、warnings、实际policy和算法身份；失败进程完整受限stdout/stderr/退出码另存RustFS可读轨迹。

版本身份使用有界字符串，schema版本继续固定。旧引擎/Python身份仍可读取和领取，执行前明确失败，不因更改Literal枚举反复领取报错；已有结果可继续读取。日志补phase、input_snapshot_id和内部校验原因，Pydantic可能含正文的错误仅记录类型。文件改绑在读前和发布事务均核对，不能发布到错误项目。

| 真实验证记录 | 结果 |
|---|---|
| `p3-document-operation-extraction-first.xml` | **11通过，28.62秒**；共用SQL抽取后的既有真实解析任务/恢复/目录/默认快照及独立解析CLI |
| `p3-document-difference-tasks-first.xml` | **7通过、1失败，26.46秒**；真实外键已拒绝删除，夹具错期待底层psycopg ForeignKeyViolation。查现有SQLAlchemyCursor及test_case_plan_run_creation后改为IntegrityError并核对 `.orig` |
| `p3-document-difference-tasks-verified.xml` | **10通过，29.31秒**；事务失败回滚、输入损坏等已补验 |
| `p3-document-difference-durable-final.xml` | **80通过、0跳过，68.72秒**；增加运行版本改变及既有解析/内核回归 |
| `p3-document-difference-worker-first.xml` | **2通过、1失败，10.00秒**；真实Redis续租重投、心跳取消已过；改绑夹具错用DTO作为update_document的位置参数。读取实际 `update_document(doc_id, **changes)` 及现有版本测试，改用project_id关键字 |
| `p3-document-difference-worker-verified.xml` | **3通过、12未选中，9.41秒**；含发布前改绑验证，未选中不是skip |
| `p3-document-difference-durable-delivery.xml` | **84通过、0跳过，68.57秒**；16项新持久比较/Worker及既有解析任务、CLI、内核、真实固定输入、工厂/进程回归 |
| `p3-document-difference-durable-diagnostics.xml` | **3通过、13未选中，9.72秒**；最终诊断阶段/原因日志变动、真实进程失败轨迹和RedisWorker工件保存后专项 |

实际V1/V2输入为Markdown BOM/CRLF、DOCX两行表格、两页文本PDF，均7→9。浏览器不参与本步骤；正式项目、MySQL策略70→80、PG任务固定70、RustFS来源及结果都实际使用。同请求并发一次、四Worker原子领取仅一个、有效租约重复投递TaskDeferred、错误Command类型不领取；错误项目/归档不能新建，归档历史可读。两侧源快照外键防丢失；只有一个新侧筛选列不能覆盖旧侧身份。

以现有实际约束注入方式使本次隔离Outbox拒绝document_difference，任务和派发均0行，解除约束后同请求可提交。结果检查点保存后注入取消，新attempt恢复完成、旧attempt写入拒绝、已保存检查点不能替换；过程中未再次比较或读当前MySQL策略。固定输入传输损坏、持久结果summary损坏、输入读取耗尽预算、运行版本改变均按具体分支验证；只操作明确隔离记录，损坏元数据恢复，原来源对象保持原状。

真实Outbox relay→Redis stream→RedisTaskWorker类→typed比较Handler→实际受限进程计算完成。人为等待0.8秒、PG租约0.6秒，实际多次续租；终态再次Redis投递正常ACK、attempt仍1、比较调用仅1。`p3-document-difference-durable-diagnostics/redis-worker/summary.json`保存实际次数，task/snapshot/result JSON可查。该Worker使用测试进程内真实类的 `run_once`，**不是比较专属CLI或生产API**。心跳失效专项注入租约异常，计算协程被取消、无结果发布；取消真实子进程回收由既有内核专项覆盖，不将该受控等待协程称为OS进程异常验收。

最终扩大回归命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS='1'
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
$env:RUN_LIVE_DOCUMENT_DIFFERENCES='1'
$env:QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-difference-durable-delivery')
$env:QA_DOCUMENT_PARSER_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-operation-parser-delivery')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_difference_tasks.py tests/test_document_parse_tasks.py tests/test_document_parse_catalog.py tests/test_document_difference_storage.py tests/test_document_differences.py tests/test_document_parsers.py tests/test_document_parser_process.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-difference-durable-delivery.xml
```

随后把比较evidence目录改为 `p3-document-difference-durable-diagnostics`，实际运行 `tests/test_document_difference_tasks.py -k 'transport_corruption or process_failure or heartbeats'`；原夹具失败及所有中间XML独立保留，不把重跑累计为覆盖数。隔离对象由显式追踪和PG检查点补查清理；新diff表先删除，再让已有解析夹具删除来源表，未操作生产业务库。原件MD/DOCX/PDF用证据本地attributes保存字节，工件地址不保证仍在线。

直接同步正式PLAN、原用户粘贴附件及Codex镜像，更新本轮已有/剩余范围。当前新增比较Service/Store/Handler尚未注册到生产容器或公开路由；没有改前端、公开API、生产表或依赖，因此本轮未运行前端构建/浏览器。新diff表只在隔离测试初始化，原解析表schema无变化；比较专属CLI、生产初始化/API、任务池/前端和部署迁移仍待继续。Git、P4影响/追踪/增量草稿、其他P0/P2/P5及默认模型/生产历史迁移均保持未完成，不标记完整目标完成。

交付实际核对：12个相关Python文件py_compile通过；正式PLAN/Codex镜像逐字节相同，原附件差异章节相同并保留编号；原件/固定解析JSON/比较正文哈希、结果身份与策略及所有XML计数一致。最终Redis工件实际记录5次续租、1次比较、1次原执行及1次终态重投，attempt始终1；进程失败trace哈希匹配任务工件。112个源码/方案/文本工件敏感格式扫描无匹配。仅132个相关文件暂存，新的被忽略后端测试显式加入，用户 `.gitignore` 不暂存；30份原件工作区、暂存和过滤后检出字节一致，源码/测试/方案及全范围暂存diff检查均通过。只创建本地中文commit，不推送或建PR。

## 方案当前状态统一与比较产品接入细化（2026-10-04，基线 22be347）

响应“更新这个方案”，直接修订原用户粘贴附件、项目 PLAN 与所选 Codex 镜像，保留原附件备份及章节编号；同步 P3_4_DESIGN。此次没有新增产品代码、公开契约、表、依赖或生产配置，没有执行生产迁移。

先核对 upload_document/upload_version、对象存储保存、比较 Service/Store/DTO/配置、production main/container、现有解析独立运行时/路由/前端 API 约定。确认上传不调用 Git，比较持久组件已提交到 22be347，但比较生产 main/container 尚未注册。修正正文与状态表中“解析页面未实现”“比较内核待实现”等过时状态；保留旧基线的历史验收范围，当前状态按首页/第六节读取。

补充比较配置 provider、生产初始化顺序、独立 CLI/运行时、拟新增公开路由、前端类型/适配器、完整页面及任务池的依据→接入做法→完成条件。页面仅推荐相邻双方可用快照，明确提交固定输入的比较任务后才产生比较状态；上传或解析成功不能代替比较成功。保持生成模板只用于生成、项目内选取→初排→AI补全→最终冻结、上传独立于Git、旧版本/原失败保留以及完整P0—P5范围。P4只提出有引用依据的影响候选，确定性差异不能直接停用正式用例。

在 Agent_Server 实际执行：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_project_binding.py tests/test_artifact_storage_service.py tests/test_document_differences.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-plan-refresh-22be347.xml
```

结果 **49 passed in 10.51s，0失败、0错误、0跳过**。差异内核包含实际MD/DOCX/PDF及真实解析/比较受限进程、来源与资源边界；上传相关项使用已有替代存储夹具。没有重新运行真实MySQL/PostgreSQL/RustFS/Redis、生产API/CLI或浏览器、默认外部模型验收；历史84项/诊断3项仅解析原XML核对，未算作本轮新通过。

实际文档校验通过：67个绝对本地链接可定位，原附件与正式方案的新接入章节相同，Codex镜像与正式方案逐字节相同；原验收XML与当前49项XML计数一致。相关文档diff检查通过。仅提交本轮方案、设计、验收记录和XML，用户.gitignore保持未暂存。完整升级目标仍未完成；接下来按新接入安排推进，不重复实施已验比较内核和持久服务。

## P3.4 独立比较 CLI、生产 API 与前端契约（2026-10-04，查证基线 6f4d227）

前一轮直接修订方案并提交6f4d227，属于实际进展；完整P0—P5目标继续。先重读用户指定原附件，核对HEAD/worktree（仅用户.gitignore），再读取生产main/container、解析运行时/CLI/路由、比较schema/service/store/handler、前端类型/API聚合与现有生产测试。调研结论先写P3_4_DESIGN，再实施；没有复制新的任务平台或增加默认模型判断。

比较服务的stream=None已支持历史/原请求读取并拒绝新任务，直接复用。新增比较配置provider，main在解析来源表初始化后创建比较Store/Service、app.state及公开路由。MySQL仍只保存可编辑策略，PG保存固定输入/任务/结果元数据，RustFS保存正文与失败轨迹；生产启动新增比较表初始化，旧解析schema和公开契约保持。

将解析Worker的项目/配置/文档/Outbox/SQLAlchemy/对象/Redis初始化与关闭抽为内部document_operation_worker_runtime。解析包装保留旧dataclass、事件名、消费组和CLI输出；比较包装独立创建Store/Service，先初始化stream=None的解析来源，仅读取已保存快照，不派发解析、不运行模型/工具或浏览器。新增run_document_difference_worker，使用原RedisTaskWorker/typed Handler，支持once/worker-id/已登记stream。启动异常/取消及队列连接失败时实际验证已取得资源关闭；两个CLI导入不启动生产API。

解析/比较路由共用内部document_operation_calls，保持原404/409/503映射、项目UUID/分页及未知提交找回提示，解析本地_call包装保留。比较settings、提交/by-request/任务/失败/结果分页与固定结果读取已注册生产router；by-request排在task_id之前。新增前端完整比较DTO和documentDifferences.api，并接API聚合。README同步流登记、独立CLI、请求/查询及限制；比较页面和任务池尚未实现。

新增tests/test_document_difference_production.py，复用现有独立PG数据库、隔离MySQL配置表、实际RustFS、Docker Redis及Processes夹具，运行完整生产uvicorn和独立CLI，不替换AppContainer provider。受控模型配置仅用于满足既有启动要求，整个文档场景实际模型调用为0。MD含BOM/CRLF、DOCX含表格、PDF有两页，真实V1=7/V2=9；通过产品上传路由保存原件及新版本，经两次独立解析CLI取得双方快照，再提交比较Outbox/Redis并运行独立比较CLI。全部stdout/stderr按子进程原样保存。

| 实际记录 | 结果 |
|---|---|
| p3-document-difference-production-first.xml | 首轮DOCX真实全链路1通过、2未选中，54.11秒；没有把未选中算跳过 |
| p3-document-worker-runtime-first.xml | CLI导入及启动失败/取消资源回收5通过，15.70秒 |
| p3-document-difference-wiring-regression.xml | 既有持久比较/解析/内核/真实来源、独立解析CLI及新增runtime回收89通过、0跳过，79.16秒 |
| p3-document-difference-production-verified.xml | MD/DOCX/PDF完整API+独立解析/比较CLI3项及原解析生产回归1项，共4通过、0跳过，175.88秒 |
| 前端3份API测试 | 6通过：新比较适配器及既有规划/任务池API，实际执行npm run test；HTTP替代响应不作为浏览器或真实服务端证明 |
| npm run build | 类型检查与构建通过，Vite9.82秒；没有比较UI，因此未执行比较浏览器 |

生产专项核对配置70秒/1000行固定于任务，修改当前设置80秒/2000行及API重启后同请求仍返回同任务/Outbox仅1条；双方版本/快照准确，raw/normalized完整差异可还原新文本，源位置与原件/解析JSON/比较结果哈希可核对。成功终态再次投Redis并运行独立CLI，attempt保持1且结果相同。列表按新侧版本筛选、分页/offset/参数拒绝/错误项目隔离及未知请求均实际HTTP测试。限制CPU触发实际子进程失败，条目failed、exit_code/完整stdout/stderr保存RustFS并经API读取，trace哈希匹配；Worker退出0表示消息处理完成，不表示比较成功。关闭派发后再次启动生产API，原终态请求/历史/失败可读，新身份503且没有第三个任务。

每种格式在p3-document-difference-production-verified/{md,docx,pdf}/保留原件、两份来源JSON、比较JSON、failure JSON、summary、Docker输出及16份进程日志。初次DOCX证据独立保留，旧解析回归在p3-document-parse-wiring-verified/保留日志和summary。夹具只清理自己新建的DB/MySQL表/Redis容器；结束前查询解析和比较检查点/失败与资产表补充对象归属，避免断言前已提交对象漏清。新证据目录attributes保留原件、JSON及日志字节；存储地址不保证清理后仍在线。

实际生产命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_PRODUCTION='1'
$env:RUN_LIVE_DOCUMENT_PARSE_PRODUCTION='1'
$env:QA_DOCUMENT_DIFFERENCE_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-difference-production-verified')
$env:QA_DOCUMENT_PARSE_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-document-parse-wiring-verified')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_difference_production.py tests/test_document_parse_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-document-difference-production-verified.xml
```

扩大回归启用RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS、RUN_LIVE_DOCUMENT_PARSERS、RUN_LIVE_DOCUMENT_DIFFERENCES，运行test_document_worker_runtime、test_document_difference_tasks、test_document_parse_tasks、test_document_parse_catalog、test_document_difference_storage、test_document_differences、test_document_parsers、test_document_parser_process；QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR指向p3-document-difference-wiring-tasks，QA_DOCUMENT_PARSER_EVIDENCE_DIR指向p3-document-difference-wiring-parser。前端在agent_web执行npm run test -- src/services/documentDifferences.api.test.ts src/services/projects.planning-tasks.api.test.ts src/services/sessions.task-pool.api.test.ts，然后npm run build。

本轮没有新增依赖或修改生产配置，不运行生产历史迁移、默认外部模型或Docling容器。比较页面/任务池/完整浏览器、Git来源、P4影响/追踪/增量草稿及P0/P2/P5剩余工作继续；不能以此文档专项宣称整体目标完成。正式PLAN/原用户粘贴附件/Codex镜像与实际结果同步；本轮相关中文commit不包含用户.gitignore，不推送或创建PR。

收尾核对：11份相关Python源码/测试py_compile通过；22种Python/TypeScript公开DTO字段逐项一致；73个本地链接可定位，正式PLAN与Codex镜像逐字节一致，原附件交付与接入章节相同；真实三格式原件/比较JSON/失败轨迹哈希及XML计数已实际核对。

提交核对：164个本轮相关文件；152份源码/方案/文本工件凭据格式扫描无匹配。136份实际工件工作区/暂存字节一致，17份原件经Git过滤后检出仍逐字节一致；完整暂存diff检查通过，用户.gitignore未暂存。新后端测试按既有忽略策略显式加入。交付字节校验脚本曾混用git diff和ls-files的路径基准而报KeyError，改为统一仓库根目录后通过；未据此改变产品代码或证据。


## P3.4 历史比较页面与任务池交付（2026-10-04；研究基线6dd162b）

先读取原目标附件、任务池SQL/DTO、实际router/App、解析页面、生产与浏览器夹具，再在P3_4_DESIGN.md记录决定。任务池按原PG单次UNION分页增加document_difference；白名单公开字段，不序列化整个任务行。文档当前项目与任务不符时仅使用通用标题，防止改绑后新项目标题外泄。前后端判别联合、筛选、标题、阶段、统计、状态及动作同步更新，旧session与case_planning分支保持。

正式历史路由 /projects/:projectId/documents/:documentId/differences/:taskId 使用三个固定身份；DocumentDifferenceTask/DocumentDifferenceSnapshot可供后续文档详情复用。读取任务指定结果，核对双方project/document/version/snapshot/原件与解析哈希及task_id；切换、卸载与较晚响应有代次保护。只读历史不加载MySQL当前策略、不重算/新提交，失败和读取错误可检索/刷新。展示raw/normalized、字符细节省略状态、章节/表格、来源、警告、固定策略，有界展开和完整JSON下载。新入口不是文档比较提交表单，下一步仍需双方版本/快照选择、设置与未知提交身份恢复。

实际验证：

- p3-comparison-task-pool-first.xml：在仓库根误跑，2项收集错误，src不在导入路径。按已有命令回到Agent_Server执行，p3-comparison-task-pool-first-corrected.xml为3通过/0跳过（9.44秒），真实DOCX/解析子进程/MySQL/PG/RustFS；分页跨24条、状态/项目/查询、公开字段、同时间排序、文档改绑标题隔离及只读attempt。
- 首轮前端14通过/1失败，夹具误把failure.json诊断正文当公开失败DTO；读取实际summary.failed_task.failure_artifact修正。p3-comparison-history-frontend.xml为22通过，最终p3-comparison-history-frontend-final.xml为25通过/0失败：实际已保存DOCX样本，项目/任务/结果/双方引用/哈希拒绝、切换较晚响应、卸载停止轮询、真实失败轨迹、原身份读取重试、文字转义、部分提取、预算细节省略、有界展示、JSON下载及原解析/任务池/比较适配器回归。组件单元HTTP使用替代响应，不能代替真实浏览器/API。
- p3-comparison-history-regression.xml：28通过/0跳过（59.07秒），真实规划任务池/浏览器、解析/比较任务及新投影。既有规划浏览器默认证据路径覆盖了历史工件；本轮另存新字节并从HEAD恢复原JSON/PNG，新增可选QA_TASK_POOL_BROWSER_EVIDENCE_DIR后实际复验，p3-comparison-history-planning-regression-verified.xml为1通过（12.12秒）。该宿主资源监控仍明确503，不冒充完整规划产品验收。
- p3-comparison-history-product-first.xml：1失败（81.40秒），浏览器测试定位器写“类型”，实际aria标签是“任务类型”。另在继续运行前核对本地Playwright签名，URL断言采用现有正则写法；修订定位器后p3-comparison-history-product-second.xml为1通过（69.42秒）。没有为测试错误添加产品特判。
- p3-comparison-history-product-verified.xml：2通过/0跳过（123.12秒），最终实际代码完整App/侧栏/任务池/正式历史路由、生产uvicorn、独立解析/比较CLI和MySQL/PG/RustFS/Redis。真实DOCX V1/V2=7/9，新的反向任务存在时选中旧历史；等待任务轮询完成；真实CPU超限失败轨迹与退出码可见；关闭派发并重启API后原路由刷新与JSON下载逐字段相同；错误项目404无结果。浏览器page_errors为空、比较历史不请求当前设置、模型调用0。同时原完整产品DOCX上传/版本/解析/设置/导航/重启/历史下载回归通过。

最终比较产品证据p3-comparison-history-product-verified/，原解析产品回归p3-comparison-history-parse-product-regression/，各自保留真实原件、截图、aria、网络、完整JSON与进程stdout/stderr。产品浏览器夹具实际npm build包括typecheck，比较构建Vite6.81秒；两个产品夹具顺序运行，不并发覆盖dist。隔离存储结束后对象被清理，历史URI不是在线交付地址，原件与结果内容在Git证据目录保存。

关键命令（Agent_Server）：

```powershell
$env:RUN_LIVE_TASK_POOL='1'
$env:RUN_LIVE_TASK_POOL_BROWSER='1'
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS='1'
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_task_pool_document_differences.py tests/test_task_pool_entries.py tests/test_task_pool_browser.py tests/test_document_difference_tasks.py tests/test_document_parse_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-comparison-history-regression.xml
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_FULL_PRODUCT='1'
$env:RUN_LIVE_DOCUMENT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_DIFFERENCE_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-comparison-history-product-verified')
$env:QA_DOCUMENT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-comparison-history-parse-product-regression')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_difference_full_product.py tests/test_document_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-comparison-history-product-verified.xml
```

前端agent_web执行npm run test -- src/views/TaskPoolView.test.ts src/features/tools/plugins/DocumentDifferenceTask.test.ts src/features/tools/plugins/ApiDocParsing.test.ts src/services/sessions.task-pool.api.test.ts src/services/documentDifferences.api.test.ts --reporter=junit --outputFile=../docs/upgrade_2026-10-03/p3-comparison-history-frontend-final.xml。新增配置仍沿用既有MySQL；本轮无新业务表或依赖，PG比较表必须由既有生产初始化建立，隔离测试不代表生产历史迁移。只提交本轮相关文件，不包含用户.gitignore，不推送/建PR。PLAN、原附件与Codex镜像已同步，完整P0—P5未完成，下一步继续比较提交产品入口和剩余范围。


### 收尾补强：网络断言与全局提示框溢出根因

复查发现网络事件是包含url/method/status的对象，旧断言误按字符串判定，未证明“没有设置读取/比较提交”。保存的实际记录含16次比较读取、0次设置读取；修正为读取event.url，要求确切任务/结果GET存在并禁止比较POST，再执行完整生产场景。不能以旧空列表当作该项通过。

截图审查增加真实布局检查后，p3-comparison-history-product-network-verified、product-layout-first、product-layout-diagnosis、product-content-diagnosis分别1失败（57.20/54.97/54.28/57.59秒）。最小宽度调整无效，已撤回任务池改动；换为短页面诊断，再核对实际App溢出元素。真实数据证明隐藏service-tooltip-meta的264px内容框产生469px滚动宽度，经顶部状态栏传播到全局shell；Playwright正常滚动定位使shellScrollLeft=101、侧栏left=-101。健康接口已有hide_password=True，不是密码明文泄露。短诊断首次脚本漏tests导入路径，修正后执行；替代HTTP/无长地址场景不复现，不能代替生产证据。

p3-comparison-layout-long-target-before使用健康DTO的掩码长地址复现相同101px偏移，1失败（24.27秒）。根因修复仅在既有service-tooltip-meta增加overflow-wrap:anywhere；未重置scrollLeft、隐藏溢出文本或增加业务特判。最终p3-comparison-history-product-final.xml **3通过、0跳过（138.66秒）**，其中**2项真实生产完整产品比较/原上传解析流程，1项替代HTTP的完整App布局回归**；不将替代HTTP称为真实存储验收。最终真实layout.json为shellWidth=viewport=1440、shellScrollLeft=0、sidebarLeft=0、contentOverflow为空。实际网络记录存在指定历史任务和结果GET，没有比较POST或当前设置请求；关闭派发、API重启、失败轨迹、错误项目与完整JSON下载继续通过。重启窗口App健康/会话轮询出现预期连接拒绝，stdout/stderr与网络记录保留；重启后加载恢复，浏览器JS错误为空。

最终证据以p3-comparison-history-product-final/、p3-comparison-history-parse-product-final/和p3-comparison-layout-long-target-verified/为准，原失败与中间结果保留，次数不累加为覆盖数。最终三个产品/布局夹具均实际构建并含typecheck。18项公开比较任务字段与TypeScript契约、PG任务池判别联合及当前5个后端测试模块编译另核对通过。完整P0—P5仍未完成；下一步仍是文档详情比较提交/双侧版本与快照选择/策略/未知提交恢复，随后继续Git、P4及其余范围。

交付核对：暂存相关文件327份（报告自身与空的源文件diff检查工件随后加入）；266份源码/方案/文本工件凭据格式扫描，唯一匹配为原有英文翻译Password标签，核对后无凭据格式匹配。304份原始工件工作区/暂存字节一致；最终下载/真实网络GET/零比较POST/布局和镜像/附件章节一致性实际核对。直接对原始工件运行git diff --check出现CRLF/原始空白提示，未改写证据；改为源码、类型、Vue、CSS及方案的diff检查通过，原件/JSON/进程日志另做字节核验。新增后端测试显式加入，用户.gitignore未暂存。


## 本次直接更新用户方案（2026-10-04，核对基线 6339f7f）

用户要求更新其粘贴方案。本次直接修改原附件、项目 PLAN 和 Codex PLAN 镜像，保留原附件备份及各阶段历史记录。修订当前状态、§3.4 提交入口的依据/实施安排/验收条件及第六节；纠正正文仍称比较 CLI/公开 API 未完成和下一步重复接任务池的旧表述。

重新读取实际上传/版本服务、比较 submit/replay、前端版本/快照/比较 API 及当前组件草稿。上传不使用 Git；模板只生成，执行为项目内选取→初排→AI 补全→最终冻结。文档详情提交/双方选择/设置/恢复已有未验收工作区草稿，不将其写成已交付。本次提交只含方案和记录，保留已有产品代码与用户 .gitignore 的工作区状态。

实际运行（Agent_Server）：`..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_differences.py -q --tb=short`，24 通过，7.36 秒，实际 MD/DOCX/PDF 与受限进程。没有重跑真实 MySQL/PG/RustFS/Redis、生产 API/CLI、浏览器或外部模型；先前产品结果只核对保存的证据，不宣称本次重新验收。

方案同步另检查镜像字节、两份 §3.4 实施段一致性、当前状态与原附件备份、历史 XML 计数及文档 diff；未修改依赖、数据库、部署或产品契约。完整 P0—P5 仍未完成。


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

交付收尾核对：项目PLAN与Codex镜像字节一致，附件实施段一致且原备份存在；三份最终XML计数、三个产品build、原请求网络GET、503、新旧固定输入/策略及下载JSON实际核对。只暂存本轮相关文件，原始工件按-text字节校验，源码/测试/Markdown的diff检查通过；敏感格式扫描无匹配。扫描首轮将历史task-pool文件名中的sk-子串误识别为凭据，核对文件名并补词边界后通过，未改写历史证据。用户.gitignore未暂存。


## 直接修订所贴方案与 Git 来源边界（2026-10-04，基线 bf84f84）

本次按用户“更新这个方案”的要求直接修订原附件、项目 PLAN 和所选 Codex PLAN。首页明确当前约定及验收范围，旧首页移到文件末尾保留。修正文档来源正文、接口说明、P3 顺序及第六节的旧状态；Git 设计同步标注未验收。模板只生成；项目内选取→初排→AI 按完整步骤补缺口→最终冻结→现有 Agent 执行保持不变。

重新核对公开上传/版本路由、上传/原提交/父版本服务、代码审查 Git 读取和当前 Git 草稿引用。普通上传不携带 Git 来源参数；Git DTO/进程/内部适配未在生产容器/入口/公开 API/前端接线。方案因此将上传版本可行性与可选 Git 的待实施范围分开，不将上传验收扩大到 Git。

本次实际命令（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_doc_versions.py tests/test_document_differences.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/plan-sync-2026-10-04-upload-diff.xml
```

结果 **37 通过、14 跳过，11.18 秒**。版本用真实文件/替代存储夹具；差异用实际 MD/DOCX/PDF 与受限进程。14 项为未启用的真实存储变体。stdout/stderr 保存在同名 log；本次没有运行真实三种存储部署、生产 API/CLI、完整浏览器、Git 读取或外部模型。此前 bf84f84 的前端 59/真实后端 27/完整产品 3 仅核对保存的最终 XML，不写成本次重新验收。

本轮只提交方案、设计状态及本次验证记录；已有未验收 Git 产品草稿和用户 .gitignore 留在工作区，不纳入文档提交。不升级依赖、不迁移数据、不部署、不推送/建 PR。完整 P0—P5 仍未完成。


## P3.5 Git 可选来源实际接入（2026-10-04，研究基线 5500367）

先重读目标附件、版本/提交/补偿事务、配置接口、代码审查本地 Git、受限进程和正式页面/生产夹具；核对 Git 官方 rev-parse、ls-tree、cat-file、git、init、update-index、replace 文档。实际安装为 Git 2.54.0.windows.1。读取使用完整 commit/blob、精确字面路径、原字节校验、local-only flags；不执行 checkout/fetch/filter，不读脏工作区。

新增 GitDocumentService、项目 MySQL 仓库/策略、resolve/import/submissions 路由与容器/main 接线。原件保存和版本/提交事务继续使用 ApiDocsService；来源请求签名参与普通重放和事务重放，回执未知先查同项目/文档/请求。GitDocumentOrigin 随现有 PG JSON 保存，原件存 RustFS，旧上传 hash 和 optional 字段兼容；没有新增版本表、数据库迁移、依赖、配置文件或第二套任务平台。

前端完整类型/API 与 DocumentGitImport 接入 ApiDocsPlugin；分别提供新建和明确更新已有文档。五项策略可编辑；固定 commit/blob 才提交；浏览器保留固定原请求，不存原件/凭据。恢复原 V1 提交后另读当前目录，页面不会退回 V1。可查固定来源，归档项目仍能找回原提交。

实际失败及纠正均保留原 XML/工件：

- source-first：2 失败/7 通过/1 未启用真实存储。测试仓库受 autocrlf 影响，预期 CRLF 与实际已提交 LF 不同；将测试 add 显式使用 core.autocrlf=false，不改产品原字节读取。
- source-final：3 失败/22 通过，分别是删除 Windows 只读 Git 对象、两种存储变体调用 update_document 时违反实际 keyword-only 签名。检查自有路径后调整夹具，source-fixed 为25通过；生产没有增加特判。
- full-product-first：MD/DOCX 均失败，复现新菜单被 modal backdrop 遮挡。沿用旧上传 to=false 后，full-product-fixed 复现 min=0.000001 与默认 step=1 让默认30秒不合法，原生表单未发出 PUT。分开秒数 step=any 与字节 step=1/min=1，并补 native checkValidity 回归。
- full-product-verified：两种文件均已走到重启后，重新进入的小 modal 裁剪菜单，定位器落到背景项目文本。改用原生项目选择及明确的项目ID选择，不 force click、不调遮罩/全局滚动。完整功能与验收范围保留。

最终实际结果：

| 证据 | 结果 | 范围 |
|---|---|---|
| p3-git-source-fixed.xml | 25通过，36.81秒 | 实际临时Git、普通/bare、SHA1/SHA256、replace、特殊文件类型、缺失blob、原字节/脏工作区、资源/取消；真实MySQL/PG/RustFS的并发、同字节来源、配置删除/归档/改绑恢复 |
| p3-git-regression-final.xml | **132通过、0跳过，56.76秒** | 最后Git日志/返回路径后复验上述25项，并回归版本、项目绑定、目录原子性、原件/派生、受限解析及差异；真实存储开关已启用 |
| p3-git-frontend-final-render.xml | **58通过、0失败** | 来源/API契约、原请求恢复、晚响应、只读、当前投影与原生数字有效性，以及已有上传/版本/解析/比较回归 |
| p3-git-full-product-final.xml / p3-git-full-product-final/{md,docx} | **2通过、0跳过，109.00秒** | 正式App/真实Chromium、生产uvicorn、独立解析和比较CLI、隔离MySQL/PG/RustFS/Redis；页面登记/新建/明确追加、丢已提交回执、重启恢复、实际7→9、普通上传；模型调用均0，仓库字节不变，JS错误为空，构建含typecheck |

生产来源 API/CLI 两种文件先前独立验收为2通过（67.56秒，p3-git-production-verified.xml）；这是独立后端范围，不与上述两个完整产品场景相加。最终完整产品后只补成功日志和当前来源文字展示，最后后端132与前端58重新运行；完整产品证据证明实际业务与恢复，最终独立构建另保存。

关键命令（Agent_Server）：

```powershell
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_git.py tests/test_api_doc_versions.py tests/test_api_doc_project_binding.py tests/test_api_doc_catalog_atomicity.py tests/test_api_doc_original_assets.py tests/test_document_parser_process.py tests/test_document_differences.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-git-regression-final.xml
$env:RUN_LIVE_DOCUMENT_GIT_PRODUCTION='1'
$env:QA_DOCUMENT_GIT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p3-git-full-product-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_git_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p3-git-full-product-final.xml
```

前端运行8个相关测试文件（58项），实际typecheck和正式产品构建已通过；stdout/stderr、原件、网络、截图、来源/结果JSON和失败XML保存。原附件、项目PLAN、Codex镜像、P3_5_DESIGN已同步。只提交相关文件，用户.gitignore保留；不推送/建PR。

本步已有专项并不等于P0—P5完成。生产历史迁移未执行，Git LFS/子模块/符号链接明确不作为原件，扫描PDF/OCR和Docling不扩张。下一步继续P4深度查证，再做影响/追踪/增量草稿；P2未知副作用/准备状态/计划修订/默认模型、模块化生成、其他完整产品流程、DeepSeek人工录制、Docker工具证据及P5保持原目标。

收尾复核：最终独立 `npm run build` 含类型检查通过（Vite 5.85秒）。新增故障注入复现二进制 TimeoutExpired.stdout 被日志明文输出，`p3-git-timeout-diagnostic-before.xml` 为1失败；超时分支按既有二进制成功/失败诊断记录大小/SHA256，并保留stderr。最终后端132通过（56.76秒）。最后完整产品2项未因该诊断改动重跑，最终前端58项仍对应实际页面来源文字与数字约束。原方案中仍称Git待接入的当前状态已同步修正；历史阶段证据保留。

交付检查：293份相关文件中268份文本进行了凭据格式扫描，URL凭据另核对，无匹配；271份原始证据工作区与暂存字节相同，源码/测试/方案diff检查通过。项目PLAN/Codex镜像字节一致，原附件Git小节正文一致，原附件备份存在。用户.gitignore未暂存；无生产数据迁移，无新依赖，未推送或创建PR。

## P4.1 三方输入实际读取（2026-10-04，基线 4a02ae2）

上一轮为实际进展：Git来源接入/实测/方案同步及提交4a02ae2。本轮重读目标附件、生成来源/完整版本/用例Store/精确资产/解析/比较服务，以及本地数据治理/评测课程，形成P4_DESIGN。未凭文档变化或source_refs数量认定覆盖。确认两项现有错位：旧版本全文入口选择该版本最新解析；追加版本使全用例draft，旧active_version_id保留却退出候选，已有测试还断言此状态。后一缺口本轮实测保存在existing-lifecycle-gap.json，不宣称已修复。

新增内部DocumentImpactSources、输入契约与MySQL配置服务；CaseStore/Service增加单语句项目当前版本读取（内存实现用同一锁），同时读取启用与最新草稿，不按mode过滤；文档服务增加精确文本资产读取，复用原字节验证/解码。默认非归档当前范围，历史/归档版本可显式选择。解析来源按当时快照及完整元数据核对，文本来源按asset/version/hash读取；跨项目/缺失/损坏/部分解析/原生成截断分别留痕，没有最新/预览fallback。保留完整自定义JSON和canonical hash，数量/正文/时间预算从MySQL冻结，超限不截断。

首轮4通过（14.53秒）；补真实图片DOCX及两种Store查询后7通过（16.21秒）；最后补生命周期实测记录后7通过（15.48秒）。扩大回归94通过、2跳过（64.04秒），跳过为当前默认外部模型生成与规划测试。新核心功能用真实MD/DOCX/PDF与真实隔离MySQL/PG/RustFS验收；扩大回归包含既有解析/比较Outbox、Redis与独立CLI。没有运行P4模型、生产P4API/Worker、浏览器或前端构建；无前端/公开契约变更。输入返回内存契约，未冒充已写入影响业务记录；生产初始化、持久影响任务及公开入口尚待接通。

实际命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS='1'
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-impact-input-regression')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_sources.py tests/test_test_case_lifecycle.py tests/test_case_json_templates.py tests/test_test_case_generation_pipeline.py tests/test_case_planning.py tests/test_api_doc_versions.py tests/test_document_parse_tasks.py tests/test_document_difference_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-impact-input-regression.xml
```

最后单独运行新模块，QA_DOCUMENT_IMPACT_EVIDENCE_DIR指向p4-impact-input-final，XML及stdout/stderr合并日志同名保存。原件、输入、生命周期事实保留。方案与原附件/Codex镜像同步。没有新增依赖/业务表/生产迁移，不推送/建PR；用户.gitignore保留。完整P0—P5未完成；下一步优先解决版本评审/启用状态，接P4持久任务、AI候选、需求评审/追踪与增量草稿，并继续原有其余范围。

交付核对：7份Python实际编译通过；42份文本凭据格式扫描无匹配；39份原始工件与暂存字节一致，4份最终输入canonical hash重算一致；源码/测试/方案diff检查通过。共50份相关文件，用户.gitignore未暂存。项目PLAN与Codex镜像字节一致，原附件已同步当前P4边界，原备份保留。

## 直接更新原方案及当前实施顺序（2026-10-04，基线 20e4ebb）

本次响应“那不更新这个方案”，直接更新用户粘贴原方案、项目 PLAN 和 Codex 镜像，补充 §2.1.1 版本评审治理、当前状态表与 P4 先后顺序。P4_DESIGN 明确先修正旧启用版本被新草稿排除的问题，再接持久影响/AI 候选、需求评审、矩阵和增量草稿。上传始终使用 RustFS 原件和 PostgreSQL 版本，无 Git 缓存；执行先项目内选取、初排、AI 衔接补全，最终冻结完整用例，模板只参与生成。P0—P5 剩余范围保留。

核对原件上传/追加方法、规划候选及已提交三方输入和实际生命周期缺口；现有工作区有后端版本评审修正，但尚未完成前端与功能验收。因此本次仅交付方案更新，不把这些代码标为完成或提交；用户 .gitignore 保留。历史 7 通过、94 通过/2 跳过仅重读 XML 核对，不冒充新运行。

实际执行 Python 文档一致性校验：42 个不同本地引用路径均存在；项目 PLAN 与 Codex 镜像字节一致；原附件与项目方案的当前修订及 §2.1.1 内容一致；原附件备份哈希未变。AST 读取现有 upload_document/upload_version，未发现调用 Git；这只是源码调用核对，不能替代实际功能验收。真实旧记录再次核对到 active_version_id 保留而 lifecycle_status=draft、候选为空。验证元数据保存于 plan-review-2026-10-04-verification.json。

本次没有修改产品代码，没有运行产品测试、前端构建、外部模型或生产迁移；只暂存相关方案和校验记录。接下来完成 §2.1.1 前后端、真实 PG/Outbox、召回/固定运行/并发/重放和正式页面的验收，再继续完整目标。此前原件、失败记录和测试结果不改写，不推送或创建 PR。

## P4 版本评审与旧启用版本保留（2026-10-04，基线 1dab94e）

先重新读取目标附件、当前未提交修改、原件/来源及生命周期记录、用例/套件/运行/向量门槛、生产初始化、前端操作与本地评测课程。根因是用例可执行状态与版本评审状态混用，以及整行旧副本覆盖新版本。采用现有 PG case record 的 version_reviews 保存逐版本状态/操作事件，immutable version 的正文/hash 不变。新增共享 version_reviews 转移内核，内存/PG 追加与评审在现有锁内处理；归档只合并当前行生命周期字段。启用、必要的新 embedding Job 与 Outbox 同事务，重启用旧版本不用旧终态 Job。按 case/action 核对 request_id 与完整参数签名；明示空预期指针与未提供字段区分，重复请求不回退。

API 保留旧无正文提交/默认启用入口，增加明确版本、操作请求和可选 CAS。前端类型/API成对更新；ProjectsView 的评审入口统一进入完整用例弹窗，展示逐版本状态/启用标记及事件，使用 sessionStorage 保存原操作（不存正文/凭据）；重开只读核对事件，已完成不再提交。明确拒绝的请求可清除后重新核对；网络未知保留同请求。已有草稿保存成功而状态刷新失败时明确告知已保存，避免因新增加的 GET 错误提示诱发重复保存。

实际验证与原失败证据均保留，不叠加覆盖数：

| 证据 | 实际结果与范围 |
|---|---|
| p4-version-reviews-initial.xml | 3 失败、13 通过：旧生命周期断言及两项旧内部方法夹具未同步 |
| p4-version-reviews-first.xml | 2 失败、25 通过：仅归档报错英文 archived 与新测试中文匹配不一致；修正测试，不改产品错误文案 |
| p4-version-reviews-regression-final.xml/.log | **106 通过、2 跳过，34.62 秒**；真实存储开关开启，覆盖新治理、实际 MD/DOCX/PDF 三方输入、生成/模板/规划、固定计划/运行及 embedding；跳过为默认外部模型生成/规划 |
| p4-version-reviews-frontend-final.xml/.log | **37 通过**：6 个实际相关文件，版本内容/评审、导入、计划/持久任务、项目执行展示及 HTTP 契约 |
| p4-version-reviews-build-final.log | 实际 npm run build（含 vue-tsc）通过，Vite 6.51 秒 |
| p4-version-reviews-product-registered.xml/.log/ | **1 通过，44.45 秒**：正式 App/实际 Chromium、完整生产 API、新进程重启读取、隔离 MySQL/PG/RustFS/Redis；真实 JSON 新版本、旧 V1 候选/套件/运行、V2 精确评审、V3 草稿拒绝、实际提交成功回执丢失、另一操作者启用 V3后原请求恢复及重放；Job 3且不增加，JS 错误空、模型调用 0 |

产品失败夹具按根因纠正：first 为旧 run schema 不接受 creation_key；verified 在请求发出前观察到“待核对”便 reload，已改等待实际 handler 完成与错误显示；final 为 Windows 显式 terminate 返回 1，改为验证进程终态、新 API就绪和持久业务重读；recovery 未登记 embedding 流，生产正确不注入 Outbox，改为按 main.py 登记流再验收 3 个 Job。没有采用产品特判、force click 或放宽业务断言。最终 product-registered 使用最终产品代码和实际构建；不将旧失败 XML改成通过。主动终止码 1另记录在结果中，不声称干净退出。

关键实际命令（Agent_Server，目录均新建隔离，stdout/stderr保存）：

```powershell
$env:RUN_LIVE_CASE_VERSION_REVIEWS='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:RUN_LIVE_CASE_PLANNING_TESTS='1'
$env:RUN_LIVE_PLAN_RUN_CREATION='1'
$env:RUN_LIVE_RUN_DEPENDENCIES='1'
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-version-reviews-input-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_version_reviews.py tests/test_test_case_lifecycle.py tests/test_test_case_embedding_pipeline.py tests/test_document_impact_sources.py tests/test_case_json_templates.py tests/test_test_case_generation_pipeline.py tests/test_case_planning.py tests/test_case_plan_run_creation.py tests/test_test_run_lifecycle.py tests/test_case_embedding_index_lifecycle.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-version-reviews-regression-final.xml
$env:RUN_LIVE_CASE_VERSION_REVIEWS_PRODUCT='1'
$env:QA_CASE_VERSION_REVIEWS_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-version-reviews-product-registered')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_version_reviews_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-version-reviews-product-registered.xml
```

新核心专项 11 项包含 5 个内存、5 个真实隔离存储和 1 个真实 PG/Outbox事务；初版创建旧草稿的 actual JSON 来自既有捕获样本，保留 12 步和嵌套字段。正式产品也实际读写该 JSON 文件。P4来源复验另保存 lifecycle-retained.json，原 existing-lifecycle-gap.json 不覆盖。编译/跨边界引用/暂存范围另核对；新增后端测试因项目 ignore 显式强制加入，用户 .gitignore保持不提交。

没有新依赖/表或可编辑配置；评审业务记录在 PG，现有配置仍在 MySQL、原件/大工件在 RustFS。没有运行真实模型判断、embedding 模型或该业务用例，没有生产历史迁移；历史迁移内部预览/执行和旧正文/hash、停用/归档仅隔离专项，运维入口/投影重建、旧套件/原失败完整迁移流程继续。方案、原附件和镜像同步；完整 P4 影响任务/AI/需求评审/矩阵/增量草稿、P2 恢复/默认模型、模块化生成、人工录制、Docker 和 P5 保持未完成，不推送/建 PR。

交付复核：暂存范围 140 份相关文件，120 份原始证据/验证元数据与暂存字节一致，122 份可解码文本凭据格式扫描无匹配；源码/测试/方案 diff 检查通过。42 个本地引用路径存在，项目 PLAN/Codex镜像字节一致，原附件当前验收及版本治理正文一致，原备份保持不变。用户 .gitignore 未暂存。最终结果逐项从 XML、实际 result.json、浏览器错误与 Job/固定运行记录核对，未将其他 P0—P5 未验证部分标为完成。

## 原方案整理、三份全文同步与P4持久设计细化（2026-10-04，基线918cdace）

本次回应“更新这个方案”：直接整理原附件、项目PLAN及Codex镜像，全文三份一致。旧项目方案正文中混入大量实施历史，多处历史“工作区开发中”“当前尚缺”混在主方案中；保留原字节到PLAN_HISTORY_918cdace.md，原附件当前版本另保存已粘贴的文本.history_918cdace.txt，已有.original.txt不动。当前方案独立列出代码依据、完整目标行为、P0—P5待办和已验范围；不累计历史测试数字、不缩小全量目标。

查证原目标附件、对照报告、当前版本/规划/文档及来源调用链、本地课程、租约和模型调用检查点、main.py/containers.py。确认上传不依赖Git，执行不读生成模板；仍按当前项目选取→初排→AI以条件来源检查衔接→补前置→最终冻结执行。将已经提交的版本治理如实标记为已有验收，生产历史迁移另验。

P4_DESIGN补齐待实现的持久分析：元数据提交先固定身份/默认选取/参数，耗时对象读取放Worker，扩展固定seed读取避免重启读当前配置/用例；证据容量与模型Token预算分开；分批范围/跨节引用/完整用例、调用意图/先存响应、未知响应和旧worker隔离、引用核验、候选评审及环境生效规则。现有document operation的checkpoint只代表最终快照，不能原样充当批次状态；复用既有Outbox/Redis/租约及模型适配，新增自身领域数据而非另造执行框架。本轮没有实施impact产品代码或新公开契约。

实际复验（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'plan-current-input-verification')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_sources.py tests/test_document_differences.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/plan-current-input-verification.xml
```

**31通过、0跳过，终端22.66秒**。实际MD/DOCX/PDF、受限解析/比较进程和隔离MySQL/PG/RustFS，固定三方输入/旧来源/哈希/缺口/预算及V1启用/V2草稿保留均通过；新证据目录包含真实原件、input.json、lifecycle-retained.json和部分DOCX输入，旧失败证据没有覆盖。没有影响模型调用、前端代码变更，故不重复前端构建/浏览器；没有生产迁移、人工DeepSeek录制或Docker测试。

三份全文SHA256、原项目历史字节、链接存在性、XML实际结果、完整阶段与核心约束、原件/输入hash另核对，结果保存在plan-current-validation.json；Git仅提交本轮方案/设计/记录及新证据，不提交用户.gitignore，不推送或建PR。完整P0—P5和P4业务闭环继续未完成。

交付核对：21份暂存文件均在本轮文档目录，17份原件/输入/验证与历史备份的暂存字节一致；三份方案全文一致，21个本地链接存在，4份固定输入canonical SHA256一致，XML为31项/0错误/0失败/0跳过。可解码暂存文本凭据格式扫描无匹配，方案/设计/记录/属性diff检查通过；用户.gitignore未暂存。

## P4固定提交与输入检查点（2026-10-04，研究基线443f046a）

先读原目标附件、当前主方案/P4设计、来源/版本/比较/解析/规划/租约/数据库适配及真实测试夹具。确认旧来源prepare每次读取当前配置/候选，不适合恢复；共享document operation强制最终快照，不适合输入和批次阶段。新增元数据capture、固定prepare和全record hash，比较get_snapshot只读metadata；原prepare仍共用内核。对已选version捕获治理快照及完整record hash，不更换当前启用/最新版本，不重新下载模板。

PostgresDocumentTaskStore抽取既有提交/Outbox/租约/查询/失败职责，单结果PostgresDocumentOperationStore保持旧解析/比较表、DTO与结果语义。新增内部ImpactStore自己的任务表/input_checkpoint，ImpactTaskService元数据提交、原请求查询、固定输入准备/读取和分页。任务/Outbox同事务；正文RustFS、元数据PG，配置MySQL。未知提交保留对象/错误，不在input_saved状态标AI完成；后续模型参数/Prompt/Skill/环境、批次/响应/结果、API/Worker/页面仍未接，不伪装公开能力。

实际验证（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS='1'
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-fixed-input-tasks-final')
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-fixed-input-sources-final')
$env:QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-fixed-input-difference-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_tasks.py tests/test_document_impact_sources.py tests/test_document_parse_tasks.py tests/test_document_difference_tasks.py tests/test_document_differences.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-fixed-input-regression-final.xml
```

**62通过、0跳过，78.97秒**。新7项真实MD/DOCX/PDF、MySQL/PG/RustFS专项：提交不下载；随后改策略为拒绝级预算、文档追加V3、用例追加/归档及新用例后仍读取固定范围；8次并发只1任务/Outbox；检查点实际提交成功但回执丢失，新Store/Service读取同PG恢复而不prepare，旧worker和检查点改写拒绝；Outbox约束失败回滚、完整record篡改拒绝；错误项目/坏对象和准备拒绝不报告分析成功。输入后仍running/input_saved；人工测试终态failed保留输入且不可重领。模型0调用。随后新增版本筛选/分页并专项复验 **7通过、0跳过17.22秒**，证据query-final，不改写此前final结果。编译实际通过。

首轮7项6通过/1失败是只捕获底层CheckViolation，实际SQLAlchemy包装为IntegrityError；修正测试断言，产品未吞错。扩大首轮60通过/2失败分别为原解析1秒租约下无heartbeat直接execute、原比较0.6秒真实Worker租约时序。以原443f046a文件临时隔离复验两项2通过6.42秒，随后恢复工作区字节，当前两项2通过6.58秒。恢复测试改为lease=30并显式SQL过期，心跳测试lease=3/真实等待3.2秒且至少2次续租，仍验证跨整个lease及终态重投；没有给产品放宽租约、取消写入保护或弱化业务断言。最终扩大回归全过；所有失败XML保留。

共享抽取只移动原任务/最终结果职责并加入领域阶段与配置timeout路径，未更改公开DTO/原解析比较API；现有真实解析CLI、比较Redis Worker、结果固定/故障/恢复也在62项内通过。本轮没有独立impact进程/真实影响模型/正式App验收，没有生产初始化/DDL迁移、前端改动或新依赖。新源码仍内部能力，未知未引用对象的运维核对/补偿入口后续完善。完整P0—P5未完成。

方案/原附件/镜像全文同步，实际XML和3个MD/DOCX/PDF持久输入canonical/object哈希/大小/状态核对见p4-fixed-input-validation.json。新增测试受项目ignore须显式加入；用户.gitignore不改不提交，无推送/PR。

交付复核：147份相关暂存文件，134份原始证据字节与暂存一致；文本凭据格式扫描无匹配，3份方案全文一致，20个本地链接存在。当时已跟踪文件的 diff 检查通过，但最终暂存检查实际报告 `postgres_document_impact_store.py:102: new blank line at EOF`，提交仍继续执行；因此不能声称最终 diff 检查全部通过。源码编译实际通过，没有活跃测试会话遗留。末尾空行及检查记录在后续方案复核中修正，见下一节。公开前后端契约未改，新的内部任务DTO仅由本轮服务/Store/测试消费，消费者引用另逐项核对。没有生产迁移；部署接入与模型验收继续。

## 原方案同步复核与检查记录纠正（2026-10-04，基线e152796c）

直接更新用户粘贴附件、项目PLAN及Codex计划镜像，三份全文相同。按实际upload_document/upload_version与对象/目录存储调用说明上传不使用Git缓存；版本是PG业务版本，原件在RustFS，Git只作为独立可选来源。修正共享PostgresDocumentTaskStore/最终快照子类/影响输入检查点的职责说明，阶段表区分已验内部输入与待接模型、批次、生产入口。保留模板只生成、项目选取→初排→AI衔接补全→最终冻结执行和完整P0—P5范围。

上次提交e152796c的最终暂存diff检查实际报告新文件末尾空行，但未阻止提交；此前“diff检查通过”的交付记录不准确，已在原记录中纠正。此次仅删除postgres_document_impact_store.py末尾多余空行，没有修改执行逻辑或公开契约。

实际命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'plan-sync-e152-impact-verification')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/plan-sync-e152-impact-verification.xml
```

本次实测 **7通过、0跳过，17.69秒**；使用真实MD/DOCX/PDF与隔离MySQL/PG/RustFS，覆盖固定提交/输入、后续文档与用例变化、并发幂等、输入回执丢失后的新Store恢复、旧worker拒绝、终态不可重领、Outbox回滚、完整版本篡改拒绝及固定版本分页。模型调用0；不是独立影响进程重启或生产历史迁移。stdout/stderr合并保存于plan-sync-e152-impact-verification.log，XML与14份原件/业务证据保留；该证据不替代此前62项回归。未重复前端构建/浏览器，因为前端和公开契约没有变化；没有调用影响模型或部署新Worker。

三份方案字节、20个本地引用、XML和凭据格式核对记录见plan-sync-e152-validation.json。提交前按相关文件核对diff与暂存字节；用户.gitignore不改不提交，无推送/PR。完整升级及P4模型/需求/矩阵/增量闭环仍未完成。

此次首轮暂存diff检查将保留原字节的Windows证据CRLF识别为行尾空白，检查失败并停止提交。第二次检查确认CRLF问题已消除，但真实Markdown换行的两个空格和PDF交叉引用表的空格仍被报出。证据属性明确保留原字节，CRLF按真实格式检查；PDF声明binary，只有Markdown原件允许其有意义的行尾空格。源码与方案仍执行完整空白检查，原始工件不改写；最终检查通过后提交。

## P4固定模型、Prompt、Skill与环境声明（2026-10-04，研究基线f5111e9e）

按目标附件重新研究实际模型schema/runtime/MySQL store/registry、规划seed/检查点、Skill全文装配及项目schema，读取本地用例生成、评测工程化、Agent轨迹课程。模型参数快照从规划schema移至模型契约层，旧planning_model_parameters导入兼容；ModelRuntimeService不再反向依赖规划领域。新增影响AnalysisContexts与typed context、产品分析Skill，复用原Skill全文装配捕获effective块和SHA-256。仅新任务提交需要注入依据服务；同请求恢复先回查，不抓新配置。

可编辑model_key/analysis_guidance/max_input_bytes保存MySQL独立配置document.impact_analysis_settings；冻结小型context随PG seed，完整证据仍RustFS。独立配置避免旧输入schema新增默认值改变历史canonical hash。模型密钥/OAuth及header/endpoint原值不进seed；后两者只hash，项目URL/description也只保存指纹。环境/目标发布版本仅user_declared、部署状态unknown，没有凭标签假定已生效。旧seed/请求hash保持兼容，不补造旧模型；新增非空选项参与创建身份。

输入检查点后prepare_analysis_request核对固定输入、构造完整证据/JSON/原来源及固定Prompt。max_input_bytes只计算canonical Prompt/messages UTF-8内容，不是假Tokenizer、精确模型窗口计量或完整provider payload大小。超限拒绝、不截断；输出>=窗口、缺配置/Skill/注入与坏Prompt hash明确拒绝。构造请求没有模型dispatch/响应持久化，不把任务input_saved当分析成功。

实际新专项先12通过0跳过47.37秒；扩大第一次75通过1失败91.20秒：规划独立CLI的所有业务断言已走完，finally因parse FK表没有登记清理失败。按实际planning_worker_runtime补夹具启动前归属检查和快照→任务清理，不改产品或业务断言。首次9张PG与1张MySQL隔离表查证仅内部FK后清理，无CASCADE；清理记录因SQLAlchemy RowMapping不能JSON序列化首写失败，清理已提交，后续重新只读核对全表不存在并保存记录。原规划测试写固定文件名覆盖了旧CLI证据，已保存本次新字节到p4-analysis-context-regression-first-planning，再恢复HEAD原字节；新QA_PLANNING_TASK_EVIDENCE_DIR防止再次覆盖。

最终以下命令9模块 **76通过、0跳过，89.95秒**（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-analysis-context-verified')
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-analysis-context-sources-verified')
$env:QA_PLANNING_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-analysis-context-planning-verified')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_document_impact_sources.py tests/test_case_planning_tasks.py tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_skill_registry_reload.py tests/test_skill_management_boundaries.py tests/test_test_case_generation_pipeline.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-analysis-context-regression-verified.xml
```

新5项专项含真实MD/DOCX/PDF、MySQL模型/分析配置、PG任务/新Store恢复、RustFS完整输入和3次受控HTTP（每格式1次）；旧12步嵌套JSON、原来源、system prompt和输出参数进入实际请求。修改策略为1字节、项目、模型参数及移除Skill后仍恢复旧request；参数变化拒绝，HTTP增加0。凭据单独轮换不改变非敏感快照。历史seed/hash回放、缺配置/Skill、未注入与不可能输出预算、坏Prompt哈希及字节超限均有实际断言。单输入大小限制不是已实现分批；3次HTTP的响应是受控固定文本，不是实际AI影响判断，未提交候选。

独立规划CLI的真实中断/重启/终态重投在76项内通过；影响恢复仍只新Store/Service读同PG，不冒充独立影响进程。实际当前schema另外读取p4-fixed-input-tasks-query-final三份已提交输入，canonical/object hash未变且旧模型context保持None。编译核对首轮按utf-8字符串读取含BOM源码导致验证脚本SyntaxError，随后使用真实源码字节compile正确处理编码标记，并通过；产品pytest导入没有此错误。

无新依赖、生产迁移或公开前后端契约变更；故未重复前端构建/浏览器。没有生产impact初始化/流/独立Worker/公开API，也没有调用真实默认影响模型或执行业务测试。候选/引用核验、需求评审/矩阵/增量、环境部署证据与P0—P5其余范围仍未完成。下一步批次清单/跨节读取/真实预算和持久意图/响应/未知恢复，之后才接结果与正式产品验收。

原方案/项目PLAN/Codex镜像全文同步，原失败/历史证据保留，校验记录见p4-analysis-context-validation.json。相关源码与测试实际引用已检索，新增测试因ignore显式加入；用户.gitignore不改不提交，不推送或建PR。

交付暂存复核：114份相关文件，95份原始工件/验证元数据与暂存字节一致；3份方案全文一致、20个本地引用存在、14份源码字节编译通过，凭据格式扫描无匹配。最终暂存diff检查通过，历史CLI工件与HEAD原字节一致，用户.gitignore未暂存且哈希保持原值。测试会话均已终止，未推送/建PR；完整目标继续。

## 原方案再次更新与上传说明复核（2026-10-04，基线e033b14f）

按用户“那不更新这个方案”的要求，直接更新项目PLAN、原附件与Codex计划镜像。重新阅读upload_document/upload_version、原件存储与版本提交路径、GitDocumentService和DocumentDifferenceService.submit：上传保存RustFS原件及PG版本；Git入口先读取固定commit文件，再调用文档服务，上传入口不反向调用Git。方案补充同一文档V1→V2与普通入口同名独立上传的例子，并列明真实方法依据。保留生成模板只生成、当前项目选取→初排→AI检查衔接/补全→最终冻结执行的设计，没有新增固定业务检查清单。

方案基线更新为已提交e033b14f；第六节明确76项通过是上次实施阶段的历史验证，本次文档更新没有重跑。三份方案同步及本地链接实际核对，提交前检查工作区和暂存diff。本次没有产品源码、公开契约或依赖变化，未运行功能测试、前端构建、浏览器或生产迁移；不将文档同步报告为完整升级完成。用户.gitignore保持原字节且不提交；只提交本轮PLAN和实施记录，不推送、不建PR。

同步脚本首轮git show使用错误的仓库相对路径，exit 128，未写镜像；改用git rev-parse --show-prefix取得实际父仓库前缀后验证通过：三份方案字节相同，20个本地链接存在，既有XML确认为76通过/0失败/0跳过。方案SHA-256为a43588e9070b5b538bf856f9247a5dfe43b7845517eeb4e166c806c102a2c8f9。未改原XML或业务证据。

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

交付暂存复核：122份相关文件，110份原始证据/验证元数据与暂存字节相同；3份方案全文相同、20个本地链接存在、8份源码字节编译通过。旧三份固定输入canonical hash及旧规划CLI原件保持不变；凭据格式扫描无匹配。最终暂存diff检查通过；用户.gitignore原SHA-256保持不变且未暂存。测试会话均已结束，无生产迁移/默认模型/前端改动，完整目标仍未完成。

## 固定单元调用、预算及持久响应（2026-10-04，研究基线3d184d5d）

继续按目标附件研究ModelRuntimeService/原生与LangChain适配器、planning意图/响应、impact固定范围/租约/对象补偿及本地Agent协议课程。新增独立调用schema/service与组合式PG调用Store，MySQL独立策略document.impact_invocation_settings，RustFS存请求/完整成功SDK响应，PG存immutable引用/hash/大小/预算/状态。没有往旧Input或策略补默认字段，历史seed无快照保持None；新任务才冻结调用策略和实际SDK/适配器/超时。只固定实际使用的SDK，无关提供商依赖变化不阻断。原模型invoke可选运行参数核对，旧消费者兼容。

固定segment/difference/case请求支持显式追加固定读及完整消息/响应续读，不截断完整JSON。计量默认字节估算，显式tiktoken只证明选定编码内容，封装/模型映射未证，总预算仍estimate。ready原子占预留和逻辑调用额度，输出/窗口/字节限制核对；不退款，不伪装精确计费。ready→invoking意图先存，唯一send赢家；response_saved先存回执再解析。未知提交/取消保留意图，新租约标未知，显式重试新身份引用旧调用且仍占预算，原deadline不变。当前没有自动协调/候选解析/独立影响Worker。

实际PG准备/意图/响应提交后丢回执不再授权发送；RustFS响应写失败保留invoking，恢复标未知不重发。竞争丢失/确认拒绝的请求对象经PG无引用检查才清理，未知提交保留；错误留下结构化阶段日志。供应商失败诊断省略可能含凭据的原body/message，保留允许元数据；成功SDK原响应保留。截断/空/工具/超响应限制有明确分类，不执行工具或当候选成功。SDK重试沿用既有配置，HTTP500真实1逻辑意图3次HTTP；记录not_observed而非伪造物理发送计数。

首轮30通过0跳过132.97秒，扩大11模块101通过0跳过181.62秒；扩大进程启动后有运行参数调整及最后测试新增，所以保留其证据但不将它作为最终源码验证。收紧仅实际模型SDK并补响应对象失败测试后的最终命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-calls-verified')
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-calls-sources-verified')
$env:QA_PLANNING_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-calls-planning-verified')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_calls.py tests/test_document_impact_batches.py tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_document_impact_sources.py tests/test_case_planning_tasks.py tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_skill_registry_reload.py tests/test_skill_management_boundaries.py tests/test_test_case_generation_pipeline.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-calls-verified.xml *> ../docs/upgrade_2026-10-03/p4-calls-verified.log
```

最终101通过0跳过187.90秒。新增19项含MD/DOCX/PDF×两适配器6组HTTP/完整12步JSON、6路并发、完整跨节续读、真实在途HTTP取消/未知恢复/显式重试、PG实际提交回执丢失、失败/部分/超大回执、内容计数、模型/SDK/超时核对、累计额度/对象损坏/旧快照以及响应对象失败。相同单元不能用新key隐式重发；旧attempt写入和终态重领取拒绝。独立规划CLI真实kill/restart在相关回归内通过；impact恢复只是新Store/Service，同PG/RustFS，不冒充独立影响进程。受控HTTP固定文本不等于AI业务判断；没有默认模型、生产impact初始化/入口/Worker/前端或生产迁移验收，完整P0—P5仍未完成。

按用户“那不更新这个方案”，补充PLAN最新状态、实际验证和待实施内容，并全文同步原附件与Codex镜像。复核上传代码仍直接对象存储/版本事务，不调用Git；上传不要求Git缓存的说明与V1/V2例子保留。查attributes最初误查项目根与父根，找到实际docs目录后只追加新证据raw-byte规则；Git源码路径误用git_document_service，实际为git_service，定位后复核。新测试显式force-add，不修改用户.gitignore。源码字节编译/历史hash/三份方案/本地引用/凭据格式与暂存原件字节结果见p4-calls-validation.json。无公开前后端契约改变，因此没有重复前端构建/浏览器；无推送或PR。

凭据格式扫描首轮把历史工件名中的task-子串误判为sk-格式；脱敏上下文复核确认是文件名，增加前置字母数字边界后重新扫描。没有输出匹配原文或更改历史证据。

最终交付核对：245份相关文件，230份原始证据逐份与暂存字节一致；验证元数据另核字节，不包含自引用哈希。三份方案全文一致、21个本地链接存在、10份Python源码/测试字节编译通过；旧三份输入canonical/object hash及旧规划CLI原件不变。最终暂存diff检查通过；用户.gitignore保持原SHA-256且未暂存。最终101项测试进程已退出，未进行生产迁移/默认模型/前端验收，完整目标仍在推进。

## 固定响应协议、单元候选与持久核验（2026-10-04，研究基线232e6ca6）

先读目标附件、现有PlanningDecision/_parse/system prompt及workflow响应checkpoint、impact完整输入/范围/请求/响应、原文与归一差异坐标和PG租约；读本地Agent协议/评测工程化课程，核对标准库JSONDecoder的pairs/constant hooks与实际行为。最初错误查了test_case_planning、Skill/共享任务文件名及在Windows给rg传glob路径，改用实际case_planning、analyze-document-impact、postgres_document_operation_store和目录配-g过滤；不新增依赖或改环境。实施前独立查证记录P4_RESPONSES_DESIGN.md。

新增typed impact-response-v1决定、read/finish及局部规则/用例影响候选，Schema/指令在新任务seed冻结/hash后准备请求原样使用。最初准备时读取当前Schema，首轮通过后复核指出会让代码升级改变同任务的不同单元，改为提交阶段快照；历史seed协议None保留，已存调用及其显式未知重试恢复不变，新单元/新续读另建任务。DocumentImpactInput/旧策略及旧调用schema未改；六份上轮真实文件×适配器请求canonical hash实际回放不变。目录提供固定来源/用例/差异身份，目录不算正文。

核验只读取已存响应，不发送：核对结构/原单元、固定原文切片/hash和完整用例的字面JSON路径/实际值，再核对该调用真正获得的primary/additional_fixed_reads及完整旧消息。normalized坐标不冒充原文；raw/章节/表格范围可作实际提供证据。未读引用或版本拒绝并保留原响应；补读决定验证固定身份和既有读取限制。拒绝重复键/NaN/多对象/损坏外层，不从坏对象中抽取嵌套候选。错误只留安全代码/说明；不复述原响应到日志。来源缺口/解析及差异警告后端传递，候选生效建议不改变unknown部署或pending_review语义状态，不改需求/用例/运行。

RustFS存核验正文，PG独立impact_response_checks存关联原请求/响应/manifest哈希的不可改对象检查点，唯一call/validator version；核验本身不改原call的response_saved。组合Store登记初始化与清理次序，写入复用任务行锁/固定seed/项目/活租约及attempt/worker/token。并发回查返回同检查点，竞争对象仅确知无引用后清理；未知PG/object回执保留对象并传播异常，已存核验恢复不解析或重发。原共用对象写入/未知提交处理提取impact_checkpoint_io，保留旧异常导入兼容；不让响应服务访问调用服务的私有写入方法。

首轮新专项31通过0跳过88.02秒，真实MD/DOCX/PDF×原生/LangChain两适配器、12步JSON/False/0/null/最后一步、read→显式接续、引用存在但未读/归一位置/未知版本/错误路径/坏JSON、并发5路、真实PG已提交后丢回执及新Store恢复、截断/空/工具回执和旧attempt/终态读写边界。首轮尚无协议seed冻结/最后补充旧hash及未知对象写入测试，不当作最终源码验证。

扩大12模块首轮138通过3失败284.92秒：三失败是旧提交测试在整个seed字符串查content_json，响应Schema合法出现字段名导致误判。按实际DocumentImpactCaseReference核对字段集合/哈希，原禁止下载钩子及变更后固定输入断言保留；不放宽产品。失败p4-responses-verified.xml/.log与目录保留，最终回归另记。

当前没有自动单元协调/全范围候选发布、独立影响Worker/CLI、正式API/前端或默认模型判断；新Store/Service读同数据库不能冒充独立impact进程重启。已有独立规划CLI作为相关回归保留。公开前后端契约未改，未重复前端构建/浏览器，不进行生产迁移。所有可编辑策略仍MySQL，业务seed/检查点PG，大正文RustFS；没有新策略依赖。原附件/项目PLAN/计划镜像待最终结果后全文同步；用户.gitignore原字节不改不提交，不推送/PR。

最终命令（Agent_Server，stdout/stderr合并保存）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:RUN_LIVE_DOCUMENT_IMPACT_SOURCES='1'
$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-responses-final')
$env:QA_DOCUMENT_IMPACT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-responses-sources-final')
$env:QA_PLANNING_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03').Path 'p4-responses-planning-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_batches.py tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_document_impact_sources.py tests/test_case_planning_tasks.py tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_skill_registry_reload.py tests/test_skill_management_boundaries.py tests/test_test_case_generation_pipeline.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-responses-final.xml *> ../docs/upgrade_2026-10-03/p4-responses-final.log
```

最终141通过0跳过291.57秒；新专项40项（16项解析/历史6请求回放、24项真实文件/HTTP/存储）。6组MD/DOCX/PDF×两适配器真实HTTP仅各1次，核验/恢复不追加发送；固定候选引用12步及False/0/null、来源缺口/部署未知保留。5路核验竞争只保留同检查点；PG实际提交丢回执新Store恢复不重解析；对象实际保存丢回执保留未引用原件并仅重做核验，不重发模型。原调用记录不改，补读由显式调用方接续而非自动协调器。当前Schema被替换为失败函数后准备请求仍用提交快照；旧输入/6份旧请求hash复验不变。

所有源文件/测试与契约实际引用已检索（改用目录+glob过滤，修正Windows路径匹配），12份Python字节编译通过；p4-responses-validation.json记录历史hash、最终XML、方案/链接、凭据格式和暂存原件核对。三份方案全文同步，保留失败证据及历史CLI原字节。新增测试按既有ignore显式加入，用户.gitignore不改不提交。源码/公开契约边界与上述研究一致，无新依赖/生产迁移/默认模型/前端验收；完整目标保持推进。

首轮暂存diff检查失败并停止：原pytest失败log/XML保留的空traceback标记E后含空格。只为这两份失败证据增加允许原行尾空白的属性，原件不改写；源码/方案/其他JSON的完整检查保留。

最终交付核对：305份相关文件，287份原始证据与暂存字节逐份一致，验证元数据另核字节且不自引用hash；三份方案正文一致、23个本地链接存在、12份Python源码/测试字节编译通过。旧三份输入/六份请求canonical hash和旧规划CLI原件不变；凭据格式扫描无匹配，最终暂存diff检查通过。用户.gitignore原SHA-256未变且未暂存，最终pytest已退出。未推送/PR，没有生产迁移/默认影响模型/前端或独立impact进程验收，完整目标继续。

## 方案同步与当前实现边界核对（2026-10-05，基线7f7fb743）

响应用户“那不更新这个方案”，本次只修改方案与核对记录，原附件及Codex计划镜像全文同步。重新读取上传/版本/原件、项目规划候选召回/Prompt/最终依赖排序、影响固定请求/续读/输入服务及共享Worker领取/心跳/资源生命周期。上传可选Git来源参数不构成普通上传的Git调用；对象与PG版本保存独立。规划目前同一query/finish循环完成语义选择与补全，TopologicalSorter负责最终排序，不能宣称已单独保存初排阶段。

PLAN基线更新为已提交7f7fb743，纠正依据表中单元候选仍未实现的过时表述，保留全范围候选/生产API/独立影响Worker/页面未完成状态。补充自动补读、规则候选向用例传递、覆盖/冲突及不可变结果的待实施方案，明确具体跨层协议仍需研究，不能只遍历单元就宣称全范围影响成立。新版本协议不能重写历史输入/请求/响应。

实际执行标准库文档核对脚本：三份方案字节一致、本地引用存在、此前287份原始证据大小/hash未变、历史最终JUnit141项且失败/错误/跳过为0；AST确认当前影响TaskService尚无execute流程。用户.gitignore原SHA-256保持不变。结果见plan-sync-7f7fb743-validation.json。历史141通过不是本次重跑；产品代码未改，未重跑后端功能测试、前端构建、浏览器、真实模型或生产迁移。全P0—P5目标仍未完成，不推送、不建PR。

## 自动补读、规则传递与全范围候选快照（2026-10-05，基线9be9bfd8）

先读目标原附件、当前状态、影响seed/范围/调用/核验、共享快照Store与Worker生命周期、本地Agent协议及评测工程化课程；设计先写P4_WORKFLOW_DESIGN.md。确认同单元续读不提供跨单元规则传递，采用固定规则集合加实际原文/完整用例输入，不能只for循环单元。实现impact-coordinator-v1指导/hash冻结、带原check来源的候选分组与潜在重叠、read/finish自动接续、当前用例逐规则覆盖及全范围未处理状态；规则来源核对实际PG核验/对象，最终读取再核对每个已存调用实际接收的集合。

影响Store改为复用PostgresDocumentOperationStore已有checkpoint/result_saved/finish与新增快照表；输入/范围/调用/核验保持独立，旧Input/CallInput/CallSelection/响应Schema不改。新调用user payload可带coordination_context，旧不带字段。旧seed工作流None不补造；旧调用/检查点查询不变。预算用MySQL原策略，代码协议冻结PG，完整请求/结果RustFS；typed预算异常仍继承ValueError，旧消费方契约保持。新增薄型Handler复用实际领取/心跳/取消/终态重投，无新增任务平台、依赖或editable参数。

真实测试首轮12失败46.54秒：把仅支持BaseModel的记录hash函数用于dict；改为既有json canonical方法。次轮11失败1通过50.14秒：第一次领取的旧claim不知道输入已保存，重复准备试图改写检查点；根因修正prepare_input读取权威当前记录，不能只在workflow绕过。两轮失败日志和XML保留。修正后12通过78.07秒。

扩大13模块161通过414.12秒（1条测试类型导入收集警告）。最终改预算细分原因、测试类型别名及两项候选来源篡改拒绝，当前源码163通过0跳过422.81秒。新22项均为真实文件/HTTP/隔离MySQL/PG/RustFS。覆盖6个文件×适配器完整流程、漏规则/无规则/来源缺失/潜在冲突、逻辑预算及回执分类、结果实际PG提交后丢回执、新Store恢复、对象实际保存后丢回执、截止时间耗尽后重读已存请求/响应/核验、不重发实际在途取消的未知调用、共享Handler实际3秒租约心跳保持及数据库显式失效后的取消、篡改源check/候选拒绝且HTTP次数不增加。固定最终XML/log/原件和结果分别保存，161结果不是最终源码验证。

最终命令在Agent_Server执行，设置RUN_LIVE_DOCUMENT_IMPACT_TASKS、RUN_LIVE_DOCUMENT_IMPACT_SOURCES、RUN_LIVE_CASE_PLANNING_TASKS、RUN_LIVE_CASE_JSON_TESTS为1，QA_IMPACT_TASK_EVIDENCE_DIR=p4-workflow-validated、QA_DOCUMENT_IMPACT_EVIDENCE_DIR=p4-workflow-sources-validated、QA_PLANNING_TASK_EVIDENCE_DIR=p4-workflow-planning-validated（均在docs/upgrade_2026-10-03）。

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_workflow.py tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_batches.py tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_document_impact_sources.py tests/test_case_planning_tasks.py tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_skill_registry_reload.py tests/test_skill_management_boundaries.py tests/test_test_case_generation_pipeline.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-workflow-validated.xml *> ../docs/upgrade_2026-10-03/p4-workflow-validated.log
```

方案三个正文同步，本轮源码/历史hash/引用/旧CLI/实际原件字节与凭据格式核对见p4-workflow-validation.json。修改属性只保留新原件和失败traceback原始空白，源码检查不放宽。最初查固定输入regression-final目录不存在，改按实际artifact/hash查找；未重跑、覆盖或生成历史记录。pytest最终进程已退出，没有默认外部模型、正式影响API/前端、独立影响进程或生产迁移验收；已有规划CLI仍属其他链路。生产初始化/API/独立Worker、真实模型及需求/评审/矩阵/增量和完整P0—P5尚需继续。用户.gitignore未改未提交，不推送/PR。

最终交付核对：388份本轮相关文件，373份新原始证据与Git暂存字节逐份一致，验证元数据不自引用hash；旧287份响应证据未变，旧3份输入/6份请求canonical hash及旧规划CLI原字节一致。三份方案全文一致、26个本地链接存在、10份Python源码/测试字节编译通过；凭据格式扫描无匹配，最终cached diff检查通过。用户.gitignore原SHA-256保持且未暂存；所有pytest进程已退出，没有推送/PR或生产/默认模型验收。

## 正式影响 API、共用装配与独立 Worker（2026-10-05，基线d4a3d636）

核对原目标附件、上传/比较服务、AppContainer/main初始化、全局Bearer及项目/文档归属、文档路由共用错误、Outbox relay、共享Worker租约/资源和本地Agent轨迹课程。API与Worker采用同一build_document_impact_runtime，不复制影响分析业务；既有比较来源构造抽为共用函数，影响Worker上游stream=None只读。新增MySQL影响配置Provider及正式初始化、四类settings GET/PUT、任务提交/原请求/分页/查询、任务保存结果和快照历史查询。PG仅初始化原影响业务表；大对象仍RustFS；无新增依赖或可编辑环境策略。模板.env.example登记三类文档stream，用户实际.env与.gitignore未改。

新增run_document_impact_worker独立CLI复用DocumentImpactTaskHandler和RedisTaskWorker，不启动API/浏览器/录制/执行器。结果公开读取复用全量对象/hash/输入/来源/规则交付事实核验，查询不重发模型。新增前端独立API/类型和边界测试；未接正式页面或任务池，完整产品流程另验。

首轮1失败28.97秒，源于测试空来源不满足既有draft契约；实际API422证明拒绝有效，改样本人工来源。次轮4失败1通过208.54秒，测试错误预设正常流程必须complete；实际比较保留抽取坐标警告，DOCX/PDF有布局/结构限制，原协调规则因此partial。修正验收为确切限制、声明单元均analyzed且没有缺口，未改生产规则；中断恢复当时已通过。原始失败XML/log和所有进程输出保留。

首批正式20项18通过2默认live跳过286.07秒；额外启用两SQL开关后既有池生命周期8通过3.75秒。补充实际HTTP和PG引用的原始对象证据后，当前源正式/资源专项20通过0跳过281.38秒；相关影响全链及三种格式正式比较回归135通过0跳过542.48秒。前端API新增与比较6通过；npm run build含vue-tsc通过。正式命令在Agent_Server运行：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-production-validated')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_production.py tests/test_document_worker_runtime.py tests/test_lifespan_resource_cleanup.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-production-validated.xml *> ../docs/upgrade_2026-10-03/p4-production-validated.log
```

相关回归启用RUN_LIVE_DOCUMENT_IMPACT_TASKS、RUN_LIVE_DOCUMENT_IMPACT_SOURCES、RUN_LIVE_DOCUMENT_DIFFERENCE_PRODUCTION，各自QA证据目录为p4-production-regression、p4-production-sources-regression、p4-production-differences-regression。实际命令：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_workflow.py tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_batches.py tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_document_impact_sources.py tests/test_document_difference_production.py tests/test_model_clients.py tests/test_langchain_model_adapter.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-production-regression.xml *> ../docs/upgrade_2026-10-03/p4-production-regression.log
```

五个生产场景实际启动三次API、独立解析/比较、独立Outbox relay、影响Worker/终态进程；MD另真实终止在途Worker，原Redis pending与PG任务由新进程恢复，第一次调用实际仅一次发送且unknown，其他单元继续，结果partial。后续MySQL指导/新草稿不替换固定输入；跨模式完整JSON和False/0/null保留。API错误Bearer401、跨项目404、参数422、原请求冲突409、关闭派发新请求503分别实测。RustFS原件不变；原HTTP消息与完整固定请求匹配；PG引用的输入/清单/请求/响应/核验对象按大小/hash保存原字节。失败、前轮与最终证据目录分开，原文件不重写。

源码字节编译、三份完整方案/本地链接、此前工作流原始证据hash及Git暂存原始字节核对见p4-production-validation.json。既有工作流协议/旧Input/CallInput/CallSelection/响应Schema未改，未知调用和terminal行为未放宽。未验证默认外部模型业务判断、正式App/任务池/浏览器或生产历史迁移。完整P0—P5保持未完成；下一步继续正式入口与真实AI/需求闭环。中文相关commit不推送、不建PR。

最终审阅仅将本轮新增配置import放回既有documents分组；独立Worker导入/未登记流/资源回收7项复验通过17.25秒（p4-production-imports.xml/.log），生产行为未变。离线核对5场景101个实际对象引用及完整冻结消息/物理HTTP匹配、374个此前工作流原始证据、3旧输入和6旧请求hash、三份方案全文及28个本地链接。新增原始证据734文件的限定凭据格式扫描无命中，不代表穷尽泄露检查。

## 2026-10-05：影响正式App、固定提交恢复和任务池

研究基线3ae0d9d9；先重读原目标附件、当前方案、影响API/DTO与任务服务、比较页面/恢复机制、任务池SQL/公开投影、正式App/router测试及本地Agent轨迹课程。研究记录P4_PRODUCT_DESIGN.md。新增专用影响公开摘要，联合读取PG权威表，保留确定分页/筛选/相同时序排序/同项目标题；不把包含input_checkpoint的完整任务DTO直接投影。共享真实池fixture初始化已存在影响表并按所有权回收输入对象；无缺表静默降级。

正式比较结果内接DocumentImpactPanel，聚合既有影响客户端。按项目当前范围或明确版本提交，不过滤测试模式；保存creation_key/完整请求后发送，未知回执重放保留原参数，晚响应不能改写新文档/历史。四类设置复用现有MySQL API/配置；没有新环境策略或依赖。结果独立显示文档规则与case单元判断，保留所有单元未完成原因、引用/验证点、潜在冲突、partial/pending_review/deployment unknown；归档只读、不启用/停用/执行。taskpool前后端联合类型、两个语言包（其余语言沿既有fallback）、路由及消费者同步。

真实池命令（Agent_Server）与结果：

```powershell
$env:RUN_LIVE_TASK_POOL='1'
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:RUN_LIVE_MYSQL_TESTS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_task_pool_document_impacts.py tests/test_task_pool_entries.py tests/test_task_pool_document_differences.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-product-pool-first.xml
```

4通过0跳过10.70秒；真实DOCX/进程、MySQL/PG/RustFS输入检查点/混合历史分页/项目隔离/标题重新绑定/只读查询实测。该专项直接提交source capture阶段用于投影验证，未冒充冻结模型与完整正式API产品验收。

前端命令（agent_web）：

```powershell
npm run test -- src/features/tools/plugins/DocumentImpactProduct.test.ts src/features/tools/plugins/DocumentDifferenceTask.test.ts src/features/tools/plugins/ApiDocDifferences.test.ts src/views/TaskPoolView.test.ts src/services/documentImpacts.api.test.ts
npm run build
```

最终62通过无未处理错误，构建含vue-tsc通过。引用数据来自此前真实生产正常/中断样本；核对未知回执、原身份重试、历史/项目切换、终态停止轮询、四类配置、字面JSON及错误呈现。前端首轮58断言通过但2项未处理错误来自旧比较测试直接挂载新增子组件、mock未配置API。隔离子组件后58通过，再增加真实中断/转义/失败等4项，最终62通过；原log保留，不把首轮写成全绿。

正式产品命令（Agent_Server）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-product-browser-preparation')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-product-browser-preparation.xml
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_FULL_PRODUCT='1'
$env:QA_DOCUMENT_DIFFERENCE_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-product-comparison-regression')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_difference_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-product-comparison-regression.xml
```

影响1通过0跳过67.93秒，比较回归1通过0跳过65.68秒。实际构建App/router/生产API（没有Provider替换）和独立解析/比较/影响CLI，隔离MySQL/PG/Redis与实际RustFS/HTTP。四类配置在页面保存并查询实际MySQL；真实DOCX7→9及12步跨模式JSON，明确选择V1，服务器202后真实丢弃回执，刷新只GET原提交，提交后配置/新草稿不替换原seed。容量样本真实进入Worker后准备失败，原成功结果保留，不增加HTTP。任务池选择指定历史/失败、关闭派发重启API后读取与下载完全相等。保留7次物理HTTP与完整冻结系统消息/全部续读消息对照、29份对象原始字节、PGseed/调用/MySQL配置、JSON下载、截图及进程输出；页面无pageerror，1440宽视口正文无横向溢出。

四轮原失败分开保留：browser-first54.34秒为拼入/home的导航错误；browser-verified47.10秒为未等待POST路由完成就断言；browser-final86.83秒为刷新后设置已折叠；browser-validated60.60秒为容量1在capture就拒绝、没有创建任务。最后依据实际capture/prepare分别校验的实现重新设计失败样本。未改生产状态或放宽上限掩盖错误。

三份完整方案同步；限定格式敏感扫描、字节/引用/链接及相关暂存核对记录另见p4-product-validation.json。用户.gitignore未编辑或提交。没有生产历史迁移、真实默认外部模型语义判断及其他格式影响浏览器场景；需求评审/矩阵/增量/适用性与P0—P5剩余项继续，不能标完整目标完成。

## 2026-10-05：真实默认模型诊断与引用依据修复

研究基线0aa271f1，重读原目标、方案、影响调用/响应/协调及配置、实际模型注册和已有真实模型规划/失败证据、本地Agent轨迹协议课程。只读默认MySQL模型元数据确认为step-5-preview/8192输出/原生适配器/SDK60秒。研究与范围见P4_DEFAULT_MODEL_DESIGN.md。

三轮实际文件及存储的复杂单元探针分别1失败126.06/125.80/126.86秒：V1/V2修改保留期、新增导出、删除优惠、不变负向条件，三个完整8步用例，最后一步业务预期及false/0/null均进入完整冻结请求；第二/三轮物理请求证明实际发送。第一轮httpx取证未覆盖OpenAI3.14.0实际httpx2客户端，不能将0截获当未发送；第二轮修正到实际SDK类send，截获两次同正文POST，第一次60.156秒ReadTimeout、第二次59.078秒时取消。第三轮新v3同样超时，第二次显式记录cancelled_without_response。都保存PG意图/unknown与原件、解析、输入、清单、请求及对象原字节，没有响应或语义结论；原目录/XML/log不重写，原未知调用不自动重发。

首轮真实固定请求逐值核对：Schema要求version_record_sha256，但三个用例的该值都未交给模型。修复新提交capture为impact-call-v3及复制引用指导，给出清单已有的记录哈希及正文哈希；保留旧v2默认/合法Schema、原目录形状和旧请求消息。引用验证没有放宽，目录不代替完整用例读取。受控HTTP专项的引用改为使用实际发送目录，避免fixture另算模型不可得的哈希。新增失败输入回放证明旧请求完整等于重建、新v3包括正确hash、False/0/null路径与content_hash混用拒绝，以及SDK实际消息/固定输出预算。

复杂探针命令（Agent_Server；每轮分别指定新证据目录和同名XML/log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-citation-v3')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_model.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-citation-v3.xml
```

三次失败后停止重复复杂请求，直接执行现有SettingsService._test_api_key_connection的ping/pong（仅_settings依赖按现有方法设置，不替换Provider）：一次POST2037ms、返回pong，源模型参数前后不变；记录p4-default-model-health-diagnostic/及.log。仅为只读连接诊断，没有经产品API，不证明复杂影响、自动全范围或部署语义。没有修改8192输出、SDK60秒或重试；仅隔离项目MySQL总任务期限600秒。后续研究实际自动协调的单元输入、跨节补读和耗时，不能盲重跑或归因未经证明。

引用修复命令（Agent_Server）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-citation-basis-replay.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-citation-basis-live')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_workflow.py tests/test_document_impact_batches.py tests/test_document_impact_analysis_context.py tests/test_document_impact_tasks.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-citation-basis-live.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-citation-basis-production')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-citation-basis-production.xml
```

回放3通过0跳过0.39秒；相关7模块102通过0跳过389.54秒（包含3回放）；独立生产API/解析/比较/影响CLI1通过0跳过68.54秒。后者实际隔离MySQL/PG/Redis、RustFS和HTTP，无Provider替换，6物理调用全文匹配固定请求、均v3与给定哈希。结果仍partial/pending_review/deployment unknown，不把受控模型当默认语义。未知、未读、旧输入/响应恢复及终态规则保留。

没有新增依赖/数据库Schema/公开API或前端契约，本轮不重复前端构建/浏览器、没有生产迁移。资源只清理已验证归属的表及对象，用户.gitignore保持原字节并排除提交。三份方案同步；原始字节、限定凭据格式扫描、链接及暂存核对另记p4-citation-basis-validation.json。完整P0—P5未完成，下一步继续真实自动协调语义及原目标的需求/评审/增量/矩阵、P2/P1/P0/P5和迁移。

## 2026-10-05：更新方案并实测真实默认模型自动流程

研究基线770f7159；先重读原目标和方案，再读impact_workflow完整execute/_unit、impact_calls请求/发送/续读、impact_batches范围及差异、共享租约Handler、原真实HTTP协调测试与实际模型客户端。对照planning_workflow已有validation_feedback/轮次和检查点。没有使用子Agent或个人Skill。

本轮新增test_document_impact_default_workflow.py，复用真实原件和存储场景，实际执行默认模型自动协调与共享Handler，而非单次全资料探针。配置在隔离MySQL、业务任务/版本/结果在隔离PG、原件及大对象在真实RustFS；源默认模型只读。实际SDK类send仅原样取证，不记录端点/认证头，不替换响应。普通上传仍不依赖Git，生成模板不参与执行。

命令在Agent_Server执行；若再次执行必须使用新的证据目录/XML/log，原失败及未知调用不重发：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-automatic-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-automatic-first.xml 2>&1 | Tee-Object -FilePath ..\docs\upgrade_2026-10-03\p4-default-model-automatic-first.log
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-automatic-replay.xml 2>&1 | Tee-Object -FilePath ..\docs\upgrade_2026-10-03\p4-default-model-automatic-replay.log
```

自动流程1失败0跳过607.72秒：Handler运行601.281秒；600秒总预算仅写隔离项目策略，SDK60秒、单调用120秒、输出8192及重试原值不变。清单2原文段、6差异、3完整8步用例，共11单元。6逻辑调用实际11物理POST，其中5ReadTimeout、2完整HTTP200响应、4取消无响应。首响应误写当前单元ID中d27为4b27，被unit_mismatch拒绝；次响应合法read进入自动补读，续读随后超时。结束1rejected/4unknown/6task_deadline未处理、规则0，发布partial/pending_review/deployment unknown。三个用例均未实际进入模型判断，规则0不能解释为无变化。

真实失败断言为：

```text
test_default_model_automatic_fixed_scope_and_terminal_history
assert all(item.status == "analyzed" and not item.reason_codes and not item.unresolved for item in result.units)
AssertionError: units contain rejected / unknown / unprocessed
1 failed in 607.72s
```

同测试此前已经核对PG/RustFS持久结果、固定11单元、终态重投及历史读取不发模型/重建/上传、终态不可重领；不能把这些断言写成整项测试通过。源模型前后参数一致；原件、请求、响应、核验和对象原字节分别保存，测试结束后仅清理验证归属的隔离资源。兼容回放另3通过0跳过0.36秒。

方案修正：去除当前表格及3.5内过时的“正式App/最终结果待接入”；新增3.5.9待实施设计和6.5实际失败。后端固定身份绑定/短引用转换、证据清单与推理任务职责、预算/调度/反馈、新协议兼容须先验证再实施。当前核验未放宽，旧v2/v3和coordinator-v1未改写。本轮只有测试与方案/研究记录变更，没有新生产协议、依赖、Schema、公开API或前端契约；未重跑前端/浏览器、独立生产Worker或生产迁移。完整P0—P5仍未完成。

三份方案全文同步；原字节、引用、限定凭据扫描及暂存核对另记p4-default-model-automatic-validation.json。用户.gitignore不修改、不提交。本轮提交仅包含方案、测试和原始证据，不推送、不建PR。

## 2026-10-05：实施v4固定身份绑定，保留原事实核验

研究基线3d71c589。先重读原目标附件、当前方案和实际上一轮请求/响应，再读调用构造、补读/响应核验、清单/原文/完整用例读取、共享Handler、生产装配/测试和前端结果类型。参考本地Agent轨迹与协议鲁棒性课程的结果与轨迹、版本漂移及严格契约原则，不采用隐式错误ID修正。具体采用做法及边界先写P4_BOUND_PROTOCOL_DESIGN.md，再实施。

新增document_impact_bound_responses.py与impact_identity_bindings.py分别负责模型wire DTO和固定身份转换；v4不要求模型返回当前单元、正式ID或哈希，S/P/C/D来自固定清单、R来自已核验规则。用例阶段只判断rule_ref与case_impacts，后端保留固定规则正文及来源。还原成原DocumentImpactDecision后继续范围/未读/切片/哈希/字面JSON值核验，原wire与规范核验分别保存。响应日志记录绑定版本和静态问题代码，不输出源正文或凭据。

capture默认新任务为impact-call-v4，保留v3兼容工厂及旧v2 seed；TaskService注入工厂仅用于明确测试/装配，不新增可编辑配置。旧v3专项显式使用旧工厂；新专项和生产API默认使用v4，受控响应从实际请求目录生成wire并核对原固定身份，不伪装真实模型。既有前端seed为通用记录，公开路由和规范结果字段没有改变；核对所有协议消费者，无需修改页面或类型。无新依赖、SQL表/列或迁移，配置MySQL、任务/业务PG、大对象RustFS约定保持。

命令在Agent_Server执行，各轮目录和XML/log分别保留：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_bound_responses.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-bound-protocol-replay-first.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-bound-protocol-live-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_bound_responses.py tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_workflow.py tests/test_document_impact_tasks.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-bound-protocol-live-first.xml
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-bound-protocol-live-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_bound_responses.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-bound-protocol-live-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-bound-protocol-production-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-bound-protocol-production-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-bound-v4')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-bound-v4.xml
```

实际命令输出同时用Tee-Object保存到对应.log，没有改写原XML/log/对象。若重跑必须新目录，不重发旧未知任务。初始回放21通过/6跳过3.50秒，跳过为未启用live；随后6模块115通过0跳过399.76秒，包含新24项和旧v3、真实失败请求、租约/未知/恢复。补核验日志后当前源码定向27通过0跳过46.76秒；27属于同一功能范围，不累加为142覆盖。

新6组真实MD/DOCX/PDF×原生/LangChain、HTTP/MySQL/PG/RustFS，证明自动短引用补读、完整12步JSON/自身说明、False/0/null、规则R1还原、精确来源和终态只读。实际失败回放仍拒绝原v3误写，新的v4响应绑定到精确原单元；未知引用、UUID/哈希覆盖、目录篡改、原文/路径/值错误和未读引用仍拒绝。

生产API/独立解析、比较、影响CLI与隔离MySQL/PG/Redis、真实RustFS和HTTP首次1通过60.78秒；补日志后最终源码1通过0跳过54.12秒。最终6物理消息均v4，保存完整HTTP、PG检查点及对象字节，部分解析/坐标限制仍为partial，semantic pending_review、deployment unknown。这不是默认模型语义验收。两次生产证据分别保留。

真实默认模型新任务1失败0跳过607.68秒，Handler运行600.953秒：7逻辑调用/11POST，两份完整impact-response-v2均read_requested/impact-bindings-v1，后续仍超时。最终5unknown/6unprocessed、规则0，三个完整8步用例未进入模型判断。600秒只写隔离项目MySQL；源模型8192输出、SDK60秒、单调用120秒、适配器及重试未改，前后参数相同。原11单元、wire、规范核验、unknown和未处理范围全部保留，终态历史读取/重投不发模型，不重领。

实际失败输出：

```text
test_default_model_automatic_fixed_scope_and_terminal_history
assert all(item.status == "analyzed" and not item.reason_codes and not item.unresolved for item in result.units)
AssertionError: units contain unknown / unprocessed
1 failed in 607.68s
```

身份绑定在实际默认响应中有效，不等于影响语义闭环完成；不把规则0解释为没有变化。不再原样重跑该完整任务，接下来研究证据清单与推理任务分工、阶段预算及已有stream机制，并保留已确认需求评审/增量/矩阵、P2/P1/P0/P5和迁移范围。未重跑前端构建/浏览器（页面及契约未改）、未执行生产历史迁移，不改用户.gitignore、不推送或建PR。三份方案全文同步；字节、源码、固定消息、对象及限定凭据扫描另记p4-bound-protocol-validation.json。完整P0—P5仍未完成。

## 2026-10-05：流式与输出预算实际查证，更新正式方案

先完成上述交付的暂存/范围/原始字节核对：345份原始证据逐一等于Git index及原SHA，三份方案一致、用户.gitignore不变，git diff --cached --check通过。授权提交f8d2f34e【fix】固定文档影响响应身份并保留旧协议。没有推送或PR，完整目标仍active。

接着读原目标附件、当前上传/用例/影响代码、默认失败输入、ModelRuntime/OpenAIClient/LangChain和planning_workflow实际调用。发现规划按冻结模型能力使用现有stream_handler，影响调用没有该上下文。SDK3.14.0实际AsyncStream._iter_events读取response.aiter_bytes；依据这条实际机制采集原始SSE，不拦截、更改或重建提供商响应。先写P4_STREAM_DIAGNOSTIC_DESIGN，再新增诊断测试。

新诊断仍复用real_scenario创建真实Markdown V1/V2、三个完整8步JSON、解析/比较及隔离MySQL/PG/RustFS；源默认模型只读、SDK60秒、单调用120秒、输出8192、重试原值不改。每项用新任务，新未知不重发，原始证据用新目录保留。完整依据流式单元、规则/用例阶段分离、隔离low参数三项结果分别1失败/0跳过127.79、117.63、124.85秒，详见PLAN6.7。两次所谓“阶段分离”诊断未取得规则，因此没有运行到用例阶段，不能将准备过的完整用例当作实际已发送。

命令在Agent_Server执行，每项同时通过Tee-Object保存实际.log，目录不得覆盖：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-stream-diagnostic-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_stream_diagnostic.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-stream-diagnostic-first.xml
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-stream-stages-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_stream_diagnostic.py::test_actual_default_stream_discovery_then_complete_cases -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-stream-stages-first.xml
$env:QA_IMPACT_DIAGNOSTIC_REASONING_EFFORT='low'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-stream-low-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_stream_diagnostic.py::test_actual_default_stream_discovery_then_complete_cases -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-stream-low-first.xml
```

首次运行时模块仅包含完整依据测试；随后按实际失败新增阶段诊断。阶段首轮没有设置effort，low只用于最后一个新任务。最后的参数在隔离项目MySQL qa.impact_diagnostic_settings读写并保存修订，测试作用域既有_build_params增加官方定义的reasoning_effort，实际SDK发送没有替换提供商。正式PG seed/request尚不包含该参数，诊断记录明确这个边界，不伪造已完成的正式配置冻结。

失败输出摘要，原XML/log保持：

```text
assert saved.phase == "response_saved"
AssertionError: {'phase': 'outcome_unknown', 'outcome': 'dispatch_TimeoutError'}
1 failed in 127.79s
assert response.metadata["finish_reason"] == "stop"
AssertionError: 'length' == 'stop'
1 failed in 117.63s
assert response.metadata["finish_reason"] == "stop"
AssertionError: 'length' == 'stop'
1 failed in 124.85s
```

发现阶段SSE实测持续有reasoning/同内容reasoning_content，但正文0；两次最后usage completion_tokens8192，finish_reason=length。reasoning_tokens=0矛盾保留、不伪造可信推理token。两个别名不相加。原1261/1680网络片段按大小/SHA/顺序还原成原SSE验证通过。首次中间摘要误输出全部usage数组导致工具输出截断，随后只保留最后usage和汇总；没有因此重发模型或改SSE。

官方中文主页抓取超时；由官方英文首页进入Step 5模型文档和Chat Completions API，查证reasoning_effort=low/medium/high；安装SDK签名也查得同字段。官方依据链接保存在设计记录，不依赖第三方参数表。实际low仍截断，不能声称网关已有效控制深度。当前问题有实际输出预算消耗证据，不能把过去所有超时一并判成此根因。

方案3.5.11/6.7纠正“先接流式即可修复”的推断，明确证据清单与推理任务分别冻结、系统提供引用位置、规则再到完整用例、传输/预算契约及旧协议兼容；这些后续仍待验证实施。保留上传不经Git、模板只用于生成、项目隔离和原失败；三份方案全文同步。没有正式生产流式/effort改动、SQL迁移、依赖升级、前端字段变化或全范围语义成功；未重跑前端/浏览器/独立Worker或生产历史迁移。全目标继续active。

## 2026-10-05：系统绑定实际原文引用，v5实测及方案同步

研究基线1d42f7ce。先查固定read_range/read_segment、raw/章节/表格原文范围、响应事实核验、v4短引用、实际失败请求及前端seed消费，再写P4_TEXT_ANCHORS_DESIGN。实现impact_text_evidence只从实际user消息primary/additional读取累计T目录；模型选T，后端还原来源/位置/quote/hash，再走原事实核验。normalized/未读/空内容和case目录不作原文证据。局部解析块裁成实际读到的切片，续读稳定编号，同文字不同版本身份不合并。

新capture为impact-call-v5/wire impact-response-v3；v2/v3/v4旧输入/终态保留。规范DocumentImpactDecision仍v1，公开API/前端结果及coordinator-v1不变。新增独立锚点schema与helper不形成循环导入，不增加SQL字段或可编辑配置。原生/LangChain受控响应助手按实际协议生成wire，只用于测试，不替换默认模型。

实际命令在Agent_Server执行，输出同时Tee-Object保存同名.log：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_text_anchors.py tests/test_document_impact_bound_responses.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-text-anchors-replay-first.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-text-anchors-live-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_text_anchors.py tests/test_document_impact_bound_responses.py tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_workflow.py tests/test_document_impact_tasks.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-text-anchors-live-first.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-text-anchors-production-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-text-anchors-production-first.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_text_anchors.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-text-anchors-edge-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-text-v5-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-text-v5-first.xml
```

不同命令独立exec进程；edge没有live开关，13通过/6跳过3.70秒，6个live场景已在132内通过，包含11旧检查及2新增边界，不算额外13覆盖。最初回放32通过/12跳过4.10秒；完整7模块132通过0跳过441.18秒。独立生产1通过0跳过59.26秒，6物理消息均等于原完整请求且v5；未杀Worker，实际正常场景不冒充故障恢复。源文件py_compile和diff检查通过；前端通用seed/规范结果消费者查证未变，没有重跑前端/浏览器。

真实默认新任务实际失败：

```text
assert all(item.status == "analyzed" and not item.reason_codes and not item.unresolved for item in result.units)
AssertionError: units contain unknown / unprocessed
1 failed in 608.01s
```

Handler601.219秒、10逻辑调用/13物理POST。8份完整响应通过impact-text-bindings-v1，其中4补读、4结束；11单元为4 analyzed/2 unknown/5 unprocessed，三条完整8步用例实际调用0，最终partial/pending_review/deployment unknown。10候选（modified3/removed3/added4）含三类实际业务变化，但重复证据产生独立候选，不当作10条不同需求。未知/失败及未处理范围保留，终态查询/重投不发HTTP、不重建/上传，不重领；源模型前后参数相同。8192输出、SDK60秒、单调用120秒不变，600秒总期限只在隔离项目MySQL。

实际请求与固定范围研究另存p4-text-anchors-request-sizing.json：126/139字符原文被分2原文+6差异发现单元；Skill三方读取、协调单元和wire指令需阶段对齐，不能仅凭此归因全部超时。新引用降低模型重复填写系统位置的职责，真实规则有进展，完整业务尚未通过。后续研究证据任务聚合/覆盖映射及阶段契约，不原样重发此任务、不复制一个答案标多个单元完成、不放宽事实核验。

PLAN3.5.12/6.8、当前进度表及两个外部副本全文同步，上传不经Git、模板仅生成及全P0—P5范围保留。之前345份v4与85份流式原始证据SHA/大小核对相同、用户.gitignore哈希未变。原件/对象/冻结消息/引用目录、三份方案及限定凭据扫描详见p4-text-anchors-validation.json；.gitattributes按现有方式保留新证据原字节。本轮无SQL表/列、新依赖、生产历史迁移或正式stream/effort配置改动。完整目标继续active，不能宣称交付完成。


## 2026-10-05 固定推理计划基础及方案同步

研究基线a0610ba3。新设计P4_REASONING_PLAN_DESIGN先核对实际v5失败范围、逐证据协调/候选hash、固定读取、配置冻结、租约及检查点。新增DocumentImpactReasoningPlans/PlanService和独立Schema、PG计划表，MySQL独立document.impact_reasoning_settings在新提交冻结；完整清单不变。对象存RustFS、检查点PG，同内容并发只保留原计划，未知回执保留对象，旧seed仍可读。计划不是模型响应，不推断任务终态或业务成功。生产Worker尚未准备/消费此计划，设置API/页面尚未接。

实际命令均在Agent_Server执行，输出同时Tee-Object存同名.log；最初离线命令与修正后相同，仅输出目录名不同：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_reasoning_plan.py tests/test_document_impact_text_anchors.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-reasoning-plan-replay-first.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_reasoning_plan.py tests/test_document_impact_text_anchors.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-reasoning-plan-replay-fixed.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-reasoning-plan-live-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_reasoning_plan.py tests/test_document_impact_text_anchors.py tests/test_document_impact_bound_responses.py tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_workflow.py tests/test_document_impact_tasks.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-reasoning-plan-live-first.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-reasoning-plan-production-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-reasoning-plan-production-first.xml
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-reasoning-plan-race-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_reasoning_plan.py::test_actual_concurrent_preparation_persists_one_plan -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-reasoning-plan-race-first.xml
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-reasoning-plan-segments-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_reasoning_plan.py::test_actual_multi_segment_scope_and_missing_source_are_preserved -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-reasoning-plan-segments-first.xml
```

首次回放5失败/14通过/11跳过5.38秒，错误是又把仅接受模型记录的规范hash函数用于dict。读取真实实现后沿用impact_batches/impact_calls规范字典序列化，不改Pydantic hash契约；修正19通过/11跳过3.89秒。11跳过为live开关缺失。完整8模块145通过0跳过460.29秒；此时两个后来增加的边界尚未收集，实际4路首次并发及多段/来源缺失各1通过8.04/6.73秒。丢回执场景实际先提交PG再报错，恢复不调用模型/当前配置/上传；MD/DOCX/PDF的真实原件及reasoning-plan.json已保存。

独立生产MD正常场景1通过0跳过60.02秒，验证新表/seed不会破坏旧v5自动流程；不是分组协调器验收，也未中断影响Worker。生产消息与冻结请求、对象与SHA/大小另按validation核对。所有plan新专项HTTP0、not_started、full_model_workflow=False；未重跑默认模型未改的旧流程，原v5失败保留。

源10文件py_compile和diff检查通过。公开路由/前端结果未改，未跑浏览器/前端构建，未执行生产历史迁移。三份方案3.5.13/6.9及当前进度同步；完整P0—P5仍未完成。p4-reasoning-plan-validation.json保存字节/原件/检查点/真实消息、旧345份v4+85份流式+328份v5证据、用户.gitignore及方案一致性检查。下一步为版本化分组协调与逐证据覆盖响应、阶段Skill、真实默认模型完整判断。

证据验证脚本最初两次断言失败：误把request对象的impact-call-input-v1当分析协议版本，又漏算独立system_prompt。核对实际消息构造后按analysis_protocol.version及system_prompt+messages逐条计数匹配，最终257份新证据、758份历史证据、三份真实计划对象和6条生产消息均核对通过；没有修改业务结果或放宽事实核验。

## 分组生产协调与逐证据覆盖〔研究基线f5705aef〕

先阅读v1协调/结果原核验、固定推理计划、调用租约/补读/预算、response checkpoint版本、Skill及前端/API消费者，并参考本地Agent轨迹与协议、CI课程。设计见P4_GROUPED_COORDINATION_DESIGN.md。新增coordinator-v2任务结果和逐证据声明，消费现有PG/RustFS固定计划；复用共享_unit调用循环、unknown/恢复/发布，旧任务走原v1。v6 model-facing v4、canonical-v2和validator-v2绑定真实T/P/D/R，证据候选不复制；用例一次预读完整JSON/自身说明/原来源和规则引用。MySQL项目设置增加公开API/正式页面，提交冻结后不读当前配置替换依据。无依赖升级，无本轮新表或历史迁移。

实际命令（工作目录Agent_Server，各轮独立证据目录，完整原命令输出见同名.log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-grouped-regression-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_grouped_workflow.py tests/test_document_impact_reasoning_plan.py tests/test_document_impact_text_anchors.py tests/test_document_impact_bound_responses.py tests/test_document_impact_responses.py tests/test_document_impact_calls.py tests/test_document_impact_workflow.py tests/test_document_impact_tasks.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-grouped-regression-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-grouped-production-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-grouped-production-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-grouped-full-product-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-grouped-full-product-first.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-grouped-v6-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-grouped-v6-first.xml
```

回放首轮6失败/19通过/21跳过6.64秒，测试误拆CallInput；修正25通过/21跳过4.84秒，live开关未开。真实首轮7失败/7通过60.52秒，夹具把完整未知字段漏算，供应商断言导致真实SDK重试；按全部原JSON验证后14通过0跳过52.67秒。边界首轮1失败/3通过25.99秒，HTTP500是供应商失败ineligible，测试错认为unknown；另增加真正HTTP在途取消/过期恢复，原意图不重发。失败原件/XML/HTTP均保留，未改生产分类迁就夹具。

九模块166通过0跳过576.99秒。6组MD/DOCX/PDF×两适配器保留全部证据、每成员reason、完整12步JSON/false/0/null/未知字段/原来源，正常发现1+用例1两次HTTP；多组、遗漏/重复/外项目/重排/未读、依据不足、超限单项、供应商失败、PG结果丢回执、unknown及终态恢复已验。分组没有把候选复制进成员，结果unit_count仍是完整范围。

生产首轮1失败30.77秒为旧v1断言；次轮缩进收集错误0.26秒，修正独立MD1通过53.61秒。加入真实配置API/MySQL后，最终生产MD/DOCX正常与实际杀进程恢复3通过186.01秒，正常各2HTTP、unknown恢复不重发；完整App/Chromium/DOCX/API/独立CLI/真实数据库1通过86.35秒，配置保存、冻结、未知提交回执恢复、失败报告、任务池与关闭派发后的历史下载/实际API重启均验。模型为受控供应商，不是默认模型语义成功。

agent_web实际命令`npm run test -- src/services/documentImpacts.api.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts`最后24通过；`npm run build`含vue-tsc/Vite通过，Vite3240模块7.61秒。正式App另构建并保存浏览器网络、页面错误、截图/布局、真实对象及MySQL配置。公开新设置、result union、validator版本、API和所有消费者已搜索核对。

首次真实默认全流程1失败291.49秒，Handler284.390秒、4逻辑/6物理发送。发现响应三候选/八声明但unit_outcome=null，模型Schema/提示与canonical结束校验冲突；真实正文保存，未从声明代填，最终8rejected/1unknown/2insufficient、规则0、partial。三条完整8步用例均进入模型，但发现失败，无合法规则。此处是契约缺失，沿两侧动作校验/字段说明/冻结提示修正；旧响应/核验仍不改写。

最终修正验证命令：

```powershell
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-grouped-contract-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_grouped_workflow.py tests/test_document_impact_text_anchors.py tests/test_document_impact_bound_responses.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-grouped-contract-final.xml
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-grouped-contract-production')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-grouped-contract-production.xml
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-grouped-v6-contract')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-grouped-v6-contract.xml
```

测试入口最初误写bound_protocol文件及生产函数名，无测试运行；两条命令错误log保留，按实际文件/函数改正。另有工具诊断误用根目录的../.venv、Windows rg路径通配，改为已查到的工作区.venv及glob参数后执行，非功能通过依据。最终63通过174.49秒、独立生产MD1通过58.60秒；首次166回归/3生产/完整App发生在动作说明修正之前，最后63/1验证修正后源码，前端契约未再改。

第二次默认任务仍1失败313.21秒，Handler306.656秒、4逻辑/6物理。发现60.156秒SDK超时，SDK重试59.016秒到逻辑120秒取消，unknown；三条完整用例都实际发送并返回依据不足，通过结构核验。最终8unknown/3insufficient、规则0、partial/pending_review/deployment unknown，不能宣称真实完整业务通过，也未取得发现响应来验证新说明的模型遵从。源step-5-preview、8192输出、SDK60秒/调用120秒不变，终态查询不发新HTTP、不可重领；不继续原样重复失败任务。

源码18文件AST/字节compile、跨契约搜索及diff检查实际通过。三份方案3.5.14/6.10同步，p4-grouped-validation.json保存所有原件/证据SHA/大小、真实物理消息对固定请求、计划/范围/来源/状态、旧345+85+328+257证据及用户.gitignore。完整P0—P5、默认模型语义、需求评审/矩阵/增量、准备状态/未知副作用、模块生成/正式导入、人工录制/Docker/报告及生产迁移仍未完成；继续按原目标逐步查证实施。

交付验证脚本首次把所有专项都当作保存了独立计划对象文件，在真实专项result.json断言失败。查证该专项实际由服务读计划并将完整plan保存于verification.json；按现有规范字典序列化重算hash/大小与检查点一致，明确区分服务读取元数据与另存原始对象字节。最终723份本轮原证据、1015份历史原证据、12条生产/正式App物理消息和12条默认模型物理消息、计划/完整范围及三份方案均核对通过；没有补造未保存的对象或更改模型答复。

暂存检查首次报告四个原日志末尾空行；按原始证据保留规则在.gitattributes明确允许p4-grouped日志末尾空行，没有裁剪日志。最终754份相关文件范围核对、723份原证据暂存字节与SHA/大小核对、暂存diff检查通过，用户.gitignore未改动/未暂存。默认模型失败仍按失败交付，本提交不是完整升级完成。

## 项目SDK策略及真实单次发送〔研究基线845287bb〕

调研及采用方式见P4_TRANSPORT_BUDGET_DESIGN.md。先核对原默认模型60秒超时和SDK重复、现有不可变调用预算、工厂参数、安装签名及官方文档，再实现可选transport_policy、冻结有效options、现有Runtime keyword、无状态传输unknown与正式页面配置；全局模型/其他调用行为保留。默认配置缺失新字段按provider_defaults，原ModelInvocationRequest不变。Google单次选项提交前拒绝，Responses/Anthropic参数构造已验而实际HTTP尚未验。

实际命令（后端工作目录Agent_Server，输出重定向同名前缀.log并保留；前端工作目录agent_web）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_clients.py tests/test_langchain_model_adapter.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-transport-model-regression.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-transport-live-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_transport.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-transport-live-first.xml
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-transport-regression-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_transport.py tests/test_document_impact_calls.py tests/test_document_impact_responses.py tests/test_document_impact_grouped_workflow.py tests/test_document_impact_workflow.py tests/test_document_impact_tasks.py tests/test_document_impact_analysis_context.py tests/test_model_clients.py tests/test_langchain_model_adapter.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-transport-regression-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-transport-production-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-transport-production-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-transport-full-product-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-transport-full-product-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_SINGLE_ATTEMPT='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-single-attempt-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-single-attempt-first.xml
npm run test -- src/services/documentImpacts.api.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts
npm run build
```

模型26通过2.89秒、新真实功能17通过71.37秒、最终九模块156通过529.86秒、独立生产全五项294.62秒、正式App一项85.06秒，全部0跳过。生产包括MD/DOCX/PDF原生、DOCX LangChain正常及MD实际杀进程恢复。单次慢响应专项实际SDK4秒/重试0、HTTP0.7秒延迟而原全局0.25秒，六组每组两次调用成功；HTTP500/断连接/取消×两适配器每项一次发送，当前MySQL配置变化及新租约恢复不重发。旧四份v6请求hash不变，完整12步JSON/未知字段/来源保留。

前端24通过；第一build退出2，TS2339是测试Element.value类型错误。修正标准HTMLSelectElement类型后build退出0，vue-tsc/Vite通过，Vite6.67秒；未放宽业务断言，首失败log保留。

真实默认模型1失败468.08秒，Handler460.515秒、5逻辑/5物理，冻结SDK120秒/重试0，实际四种HTTP timeout均120。源默认step-5-preview、8192/globalSDK60/call120/isolatedTask600不变。发现73.063秒得到合法三规则/八声明；保留期用例119.906秒回应但content_json.title不存在，invalid_case_citation拒绝；促销23.437秒请求另两条固定用例，续读109708字节/4消息120秒取消，未鉴权52429字节/2消息120秒取消。全部三条8步用例实际发送；最后8 analyzed/1 rejected/2unknown、3rules、partial/pending_review/deployment unknown。仍失败，任务completed仅是生命周期终态。

没有改写原错误引用/unknown，没有原样重跑失败默认任务。终态不重领、历史查询不HTTP/上传/重建，源模型前后相同。本轮代码/文档范围不含用户.gitignore；三份方案、原证据、消息、固定options及旧345+85+328+257+723原文件核对另记p4-transport-validation.json。无依赖或新SQL表，未执行生产历史迁移。完整P0—P5保持未完成，后续按真实样本查证引用和补读阶段边界，再完成需求评审/矩阵/增量及其他原定模块。

交付核对已实际完成：11份Python源码/测试AST与字节编译、跨层引用及diff检查通过；522份本轮原文件、1738份历史原文件、12条生产/正式App消息和5条默认消息逐字节/固定请求核对，三份方案一致。首次验证脚本把调用phase的outcome_unknown错当结果单元status；读取真实Schema后使用unknown，原业务记录和源码未改，核对脚本复验通过。方案正文最初多留文件末尾空行，去除后diff检查通过；未裁剪原始日志。

最终暂存541份相关文件，522份原始证据暂存字节与记录SHA/大小相同，暂存diff检查通过，用户.gitignore未改/未暂存。首次一次性暂存参数超过Windows命令行长度（WinError 206），改为顺序分批暂存，范围不变；没有删除或裁剪证据。


## 完整用例字段引用及当前阶段缺口〔研究基线6f4bb0ea〕

先查真实v6错误title引用、case metadata/version边界、现有T绑定/字面路径核验、补读与PG核验版本/API/前端消费方，再新增F完整节点索引、v7/v5/v3及固定读取；复用v2协调/结果。没有新表、依赖或配置副本，MySQL可编辑设置/PG检查点/RustFS对象沿原职责。完整调研见P4_CASE_FIELD_REFERENCES_DESIGN.md；功能、失败及验收边界见PLAN.md 3.5.16/6.12。

实际命令（后端目录Agent_Server，输出重定向同名前缀.log；前端目录agent_web）：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_case_anchors.py tests/test_document_impact_text_anchors.py tests/test_document_impact_grouped_workflow.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-replay-first.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_case_anchors.py tests/test_document_impact_text_anchors.py tests/test_document_impact_grouped_workflow.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-replay-fixed.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-case-anchors-live-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_case_anchors.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-live-first.xml
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-case-anchors-regression-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_case_anchors.py tests/test_document_impact_text_anchors.py tests/test_document_impact_bound_responses.py tests/test_document_impact_grouped_workflow.py tests/test_document_impact_calls.py tests/test_document_impact_responses.py tests/test_document_impact_workflow.py tests/test_document_impact_tasks.py tests/test_document_impact_transport.py tests/test_document_impact_reasoning_plan.py tests/test_document_impact_citation_basis.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-regression-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-case-anchors-production-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-production-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-case-anchors-full-product-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-full-product-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_SINGLE_ATTEMPT='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-case-v7-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-case-v7-first.xml
npm run test -- src/services/documentImpacts.api.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts
npm run build
```

第一次回放8失败/22通过/25跳过9.50秒，漏索引已有合法expected容器路径；按原契约补全部节点后30通过/25跳过5.00秒，live未开。真实专项16通过43.61秒，11模块200通过683.65秒，独立生产5通过288.04秒，正式App1通过88.67秒，前端24通过及vue-tsc/Vite8.43秒通过，真实轮均0跳过。保存原件、实际HTTP、PG/RustFS结果和终态恢复；受控供应商与真实默认模型分别说明，未裁剪失败记录。

首次默认任务1失败207.39秒，Handler200.015秒、4逻辑/4物理，11项analyzed，规则3及九判断均符合固定样本。但unresolved包含部署/评审/其他任务说明，严格断言仍失败，位置警告及snapshot.partial保留。读取实际Skill和Schema后确认字段语义缺失；新提交冻结当前阶段描述与指引，真正缺失证据仍保留，不清洗旧字符串或抹去warning，不改全局Skill。首轮原Schema/hash/响应/结果仍可按固定seed恢复。

澄清后实际命令：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_IMPACT_TASK_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-case-anchors-stage-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_case_anchors.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-stage-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-case-anchors-stage-production')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-stage-production.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_DEFAULT_MODEL='1'
$env:QA_DOCUMENT_IMPACT_SINGLE_ATTEMPT='1'
$env:QA_DOCUMENT_IMPACT_DEFAULT_MODEL_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-default-model-case-v7-stage')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_default_workflow.py -q -s --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-default-model-case-v7-stage.xml
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_impact_case_anchors.py::test_actual_successful_v7_stage_matches_strengthened_business_oracle_without_new_http -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-case-anchors-business-replay.xml
```

最新专项18通过44.19秒（含首轮v7原请求重建与九判断实际回放），独立MD1通过58.86秒，均0跳过。首次200/5/正式App在指引澄清前，后18/1和新默认任务验证澄清后源码，前端及wire字段结构未再改。

新默认严格验收1通过329.88秒，Handler322.734秒，4逻辑/4物理，四条200，发现61.797秒/三用例100.687、116.813、38.844秒，无补读或重复。11项analyzed、无reason_codes/unresolved，三规则7→9天/删除优惠码/新增倒序导出，三条完整8步用例均发送。九关系独立读取与样本基线吻合，保存semantic-stage.json；pending_review、deploymentunknown、extracted_text_positions警告和snapshot.partial保留，不把模型分析当实际执行成功。

随后把九项业务oracle接入未来默认集成验收，使用此次实际结果回放1通过4.68秒，0新增HTTP；最新默认XML在加入oracle函数前执行，实际结果的离线加强验证另记business-replay.xml，未原样重复外发。保留修改/删除的重大影响及未登录负向条件，新增导出未误认已覆盖，其他关系none。源默认模型/8192/globalSDK60/call120/task600前后相同，终态历史不发HTTP/重建/上传/领取。

三份方案、原对象/版本hash/F路径和值、14条生产/正式App物理消息、8条默认消息、旧2260份原文件及用户.gitignore核对见p4-case-anchors-validation.json。没有生产历史迁移，完整P0—P5仍未完成；后续接稳定需求身份/评审/验证点/追踪/增量，并继续准备状态、未知副作用、正式生成导入、人工录制/Docker/报告。

## 正式需求评审与设计矩阵API〔研究基线6abe525f〕

实施前读取固定规则/逐用例响应、快照完整核验、case逐版本治理、PG事务/锁、正式API恢复和本地版本/证据课程。依据到做法见P4_REQUIREMENT_REVIEW_DESIGN.md。新增三张PG需求/版本/评审表，稳定需求/验证点、明确接受/拒绝/暂缓、原请求恢复、父版本与候选最新评审冲突、指定历史设计矩阵；生产runtime/main初始化与路由、前端DTO/客户端一起接入。没有新增依赖、MySQL配置或改写原影响/用例契约。

实际命令（Agent_Server目录；各log/XML及目录独立保留）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_REQUIREMENT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-live-first')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_reviews.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-live-first.xml
$env:QA_REQUIREMENT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-live-fixed')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_reviews.py tests/test_document_impact_case_anchors.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-live-fixed.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-production')
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_impact_production.py::test_production_fixed_analysis_history_and_process_recovery[md-False-False]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-production.xml
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-api-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_reviews_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-api-final.xml
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-api-fixed')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_reviews_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-api-fixed.xml
```

首轮3失败/5通过37.69秒是测试误把current_version_id当历史不变量；修正后追加业务故障与并发，9项新需求+19项既有字段引用=28通过91.34秒，0跳过。真实MD/DOCX/PDF/MySQL/PG/RustFS/进程/HTTP/ASGI，固定rule/完整case判断及checkpoints保存，V1/V2/V3/验证点、历史关系、明确设计覆盖与待复核、原用例启用指针和原影响对象保留。丢真实PG提交回执503后四路恢复一个决定；首次四路提交也只产生一个版本，两个不同请求竞争同父版本仅一个成功。拒绝/暂缓不创建需求，移除后恢复验证点UUID；损坏真实PG正文被hash拒绝，原测试记录已恢复。归档后重放/历史禁止对象读取、上传、模型和配置；新请求409。

独立API/三类CLI/真实存储原MD正常场景1通过71.37秒。新增生产HTTP评审测试首次因我漏写列表括号收集1错误0.33秒，无功能执行；修正后1通过61.59秒，真实HTTP提交/读取版本和矩阵、API停止再以关闭派发配置启动、按原请求找回/重放均验证，原物理模型仍2次，不读取进程内存恢复决定。

agent_web目录执行`npm run test -- src/services/requirements.api.test.ts src/services/documentImpacts.api.test.ts`为6通过；`npm run build`含vue-tsc/Vite通过8.89秒。没有新需求页面或浏览器流程，未重复外部默认模型；受控响应/测试评审只验链路，不能冒充用户业务审批。design_covered只表达明确设计关联，execution_status=not_assessed、deployment_status=unknown；增量生成、环境适用、运行证据、完整App和P0—P5其他剩余项仍待完成。

三表真实启动初始化已验，未自动回填历史需求，未修改原业务表/版本正文。相关原始证据、各次失败、hash/完整来源/消息、旧2974原文件、用户.gitignore及三份方案核对见p4-requirement-reviews-validation.json。后续先补正式评审/矩阵App和确认范围的增量生成，不把设计矩阵称为执行验证闭环。

末尾为需求分页增加project/document/id索引，短标识名称从实际配置表名hash生成，避免超过项目既有63字节标识限制。最终源码实际复验：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_REQUIREMENT_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-final')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_reviews.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Resolve-Path '..\docs\upgrade_2026-10-03') 'p4-requirement-reviews-api-validated')
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_requirement_reviews_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-reviews-api-validated.xml
```

新需求9通过39.44秒，独立生产评审/重启1通过56.39秒，均0跳过；初始化索引可重复，真实API重启/历史不丢失。此前28及第一次生产证据在索引前，不合计新覆盖。前端文件没再改，未重复前端构建。最终AST/编译和diff、raw bytes及方案一致性验收单独保留于validation。

## 正式需求评审与矩阵App〔研究基线fbf8bffb〕

先读原目标附件、当前代码/计划、固定快照展示与任务身份、sessionStorage原提交恢复、HttpError分类、需求API/DTO/PG最新决定与分页排序、不可变版本/稳定点及本地课程，再实施；设计与采用依据见P4_REQUIREMENT_PRODUCT_DESIGN.md。新增RequirementReviewPanel、useRequirementReview、RequirementMatrix及小型身份/分页辅助，显式打开原影响结果的正式入口后才调用需求API。没有新依赖/配置/表、没有改原影响响应协议；原可编辑设置在MySQL、评审/版本仍PG、大对象仍RustFS。

表单接受/拒绝/暂缓，明确新建/已有需求、验证点key/说明及固定用例版本关系，不从AI等级推导覆盖，不复制旧关联。完整分页读取后核对最新决定；冻结原请求、查询/原参数重试、明确冲突重读、归档历史和晚响应均实测。复查修正为捕获表单编辑父版本，刷新目录不自动把旧正文改挂新父版本；显式重读或成功保存后才初始化新编辑依据。

实际前端命令（agent_web目录）：

```powershell
npm run build *> ../docs/upgrade_2026-10-03/p4-requirement-product-build-first.log
npm run test -- src/features/tools/plugins/RequirementProduct.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts src/services/requirements.api.test.ts src/services/documentImpacts.api.test.ts *> ../docs/upgrade_2026-10-03/p4-requirement-product-frontend-first.log
# 按真实保存时点修正V1指针断言后，同一命令保存到frontend-fixed.log。
# 补编辑父版本和矩阵晚响应后，同一命令保存到frontend-final.log。
```

构建通过8.58秒；初次1失败/37通过2.40秒，为我误读V2创建前V1矩阵指针的测试断言。原失败保存，不改原fixture；修正38通过1.86秒，最终40通过2.06秒（新增14项，含既有影响/客户端回归，不累计三轮为覆盖数）。

实际产品命令（Agent_Server目录）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '../docs/upgrade_2026-10-03') 'p4-requirement-product-browser-first')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_requirement_reviews_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-product-browser-first.xml
# 首次完整流程通过后，补真实竞争评审与编辑父版本冻结：
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR=(Join-Path (Resolve-Path '../docs/upgrade_2026-10-03') 'p4-requirement-product-browser-final')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_requirement_reviews_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-product-browser-final.xml
```

首次1通过96.96秒、最终1通过107.54秒，均0跳过，各自真实构建9.51/10.49秒。真实DOCX/独立API及解析/比较/影响CLI/MySQL/PG/RustFS/Redis/Chromium正式App完整操作：接受后已提交响应丢失，刷新原请求恢复；V2不继承覆盖，V1正文/关系固定；拒绝/暂缓不产生版本；移除后历史保留；API重启/派发关闭与归档原请求恢复。最终加入真实API竞争决定后旧表单409，无自动重发或版本覆盖，显式读取后再评审。最终6评审/3需求版本与原PG一致，模型仍仅原影响2个物理POST，无新外部默认请求。原失败任务、任务池、下载和原影响全文也核验，pageerror为空、1440宽无正文横向溢出。

全部原件/请求/PG决定/版本/矩阵、HTTP、对象、日志/XML及截图在p4-requirement-product-*；hash/历史证据/方案/暂存原始字节核对见p4-requirement-product-validation.json。测试评审不是用户业务审批，设计覆盖不证明执行通过，deployment未知/执行未关联仍显示。无生产历史迁移，增量草稿/环境适用/运行证据及其他P0—P5仍未完成。下一步研究确认范围进入现有生成任务和不可变用例版本的契约，再实施增量更新。

## 确认范围与冻结生成基础〔研究基线e88add3a〕

先读原目标附件、当前生成/模板/Skill捕获、模型白名单及runtime guard、固定影响输入、需求决定/版本、导入事务/持久规划和本地课程，研究结论见P4_INCREMENTAL_GENERATION_DESIGN.md。旧generate不能承担固定需求更新/恢复：它重读当前预览/Skill，只create_many新case。抽出共用请求构造保留旧行为，新增明确范围、每case基线模块、一次模板/authoring快照、固定生成及原响应解析；按review_id读取原接受决定并核对来源/hash。

Agent_Server目录的实际命令：

```powershell
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_test_case_generation_pipeline.py tests/test_case_json_templates.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-incremental-sources-legacy-first.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_INCREMENTAL_SOURCES_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-incremental-sources-live-first')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_incremental_generation_sources.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-incremental-sources-live-first.xml
# 修正首次测试错误后，同模块结果分别保存到live-fixed.log/xml与live-fixed目录。
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_INCREMENTAL_SOURCES_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-incremental-sources-live-final')
$env:QA_REQUIREMENT_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-incremental-sources-reviews-final')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_incremental_generation_sources.py tests/test_requirement_reviews.py tests/test_test_case_generation_pipeline.py tests/test_case_json_templates.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-incremental-sources-live-final.xml
# 增加实际LangChain和PG/RustFS损坏后再验最终新增模块：
$env:QA_INCREMENTAL_SOURCES_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-incremental-sources-boundaries-final')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_incremental_generation_sources.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-incremental-sources-boundaries-final.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-incremental-sources-production')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_requirement_reviews_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-incremental-sources-production.xml
```

各命令stdout/stderr通过Tee-Object保存同名前缀log；以实际保存XML/log为准，最终测试文件包含后增加的两项，不把旧收集数量描述为当前全模块数量。旧生成首轮7通过/3跳过7.89秒；新增首轮5失败/4通过58.63秒是我漏传上传父版本、用了不生效温度字段并误比较启用前case。按实际DTO/DB/启用记录修正后3失败/6通过59.54秒，误把create_version返回的版本记录当bundle；改为版本记录的version。两次失败保留，没有把原场景跳过。

四模块回归31通过/1跳过152.89秒，唯一跳过需要外部当前默认模型的旧生成场景。新增模块最终15通过/0跳过107.77秒；11实际场景和4请求校验，与前述13重叠不累加。真实MD/DOCX/PDF原件/解析比较进程/受控影响HTTP/PG需求接受/完整固定输入/MySQL模板及模型，范围与整个原响应存RustFS后重读。三原生格式及MD LangChain每模块一POST，完整12步JSON/False/0/null/未知字段、自身说明、确认点、旧新原文/差异及当时来源实际到达。修改模板/Skill文件/当前文档、新case草稿及新需求版本不改原请求；旧启用指针和原case版本保留。

真实模型max_tokens/SDK适配器变更在HTTP前拒绝；项目/版本范围/hash拒绝。PG原评审请求或来源的实际损坏被hash/固定影响核验拒绝并恢复；独占测试任务的实际RustFS输入改字节被size/SHA拒绝，随后恢复原字节并核对。移除候选无authoring/模板读取、无生成、不停用。真实HTTP工具调用、length、空响应及500每种1POST，返回整个ModelInvocationResult先存RustFS再解析拒绝；补实际保存响应的越界/错误case_key/终止标记检查。最终11场景生成13POST，受控结果不冒充默认模型语义评审。

独立生产API/三类CLI/Outbox/MySQL/PG/Redis/RustFS/HTTP需求评审与API重启/关闭派发恢复1通过102.20秒；原影响物理POST2，原消息/对象核对另见validation。证明现有生产兼容，没有调用新增增量任务，本轮未接生产模块检查点或提交草稿。无新依赖、配置键、SQL表/列、公开路由或前端契约；不重复前端构建/浏览器、外部默认模型和生产历史迁移。

原失败/通过log/XML、文件、范围、响应/候选、PG需求及生产进程证据在p4-incremental-sources-*，SHA/完整来源/消息、旧3264原文件、用户.gitignore及三份方案核对见p4-incremental-sources-validation.json。下一步查证并实施持久模块/调用unknown/原响应恢复、同事务草稿收据/Worker/API/UI，再继续运行/环境及P2/P1/P0/P5；完整P0—P5未完成。

## 2026-10-05 增量持久模块、原响应与同事务草稿〔研究基线5e6cf20f〕

先读取用户目标原附件、工作区和新任务设计，核对现有文档TaskStore/Worker、导入事务、用例版本锁及原接受决定/影响输入。复用共享任务插入/Outbox和版本追加SQL，配置新增MySQL键test_case.incremental_generation_settings；业务组、模块任务、检查点、草稿和收据存PG，完整范围及原响应存RustFS。7份生产源文件、新增实际测试及设计已实现；没有新依赖，也没有把生成模板带入执行。

任务组与所有模块/Outbox同事务；当前project_id、原需求版本/review、固定影响输入及原case版本再次核验。每模块单独领取；意图先存，收到响应保存原件后解析。已存响应的新租约只读恢复，只有意图没有响应标unknown不重发。草稿及收据/终态同事务，更新保持原case身份和启用指针；重复编号、并发父版本冲突、行锁导致租约/总期限过期整笔回滚。移除仅形成候选，不自动停用。

原首轮6通过82.42秒；扩大后8通过/1失败104.76秒。失败是新测试SQL的LIKE字面百分号触发psycopg参数语法错误，按已有参数化写法修复，失败XML/log保留。进一步研究查到真正的期限缺口：_owned只查租约，草稿等待锁后未查原deadline。用真实原case行锁/1秒总期限/30秒租约，旧行为在到期后仍完成V2：1失败、9 deselected23.62秒。原收据、对象和case V2证据保存。随后恢复修正源，发送意图/草稿事务开始/写入后均查原数据库期限；最后的期限和租约fence保证事务回滚，失败终态和原响应保留。

实际执行（Agent_Server目录，stdout/stderr分别通过Tee-Object保存对应log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:PYTHONUNBUFFERED='1'
$env:QA_GENERATION_TASK_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-tasks-final')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_generation_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-tasks-final.xml
# 后增加关闭派发验收：
$env:QA_GENERATION_TASK_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-tasks-dispatch-final')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_generation_tasks.py -q -k disabled_dispatch --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-tasks-dispatch-final.xml
$env:RUN_LIVE_DOCUMENT_PARSERS='1'
$env:RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS='1'
$env:RUN_LIVE_CASE_VERSION_REVIEWS='1'
$env:RUN_LIVE_CASE_IMPORT_TESTS='1'
$env:QA_DOCUMENT_PARSE_TASK_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-tasks-shared/parse')
$env:QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-tasks-shared/difference')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_document_parse_tasks.py tests/test_document_difference_tasks.py tests/test_case_version_reviews.py tests/test_case_import_service.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-tasks-shared.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_requirement_reviews_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-tasks-production-compatibility.xml
```

新模块14通过168.72秒；后增关闭派发1通过/14 deselected17.76秒。当前15项实际覆盖更新/新建/移除、PG提交回执丢失、响应恢复禁止重发、实际在途取消unknown、父版本冲突、四路提交/领取、独立模块失败、租约/原期限回滚、第二条编号冲突导致整批回滚、seed替换拒绝、模型配置变化与scope/response对象真实损坏后恢复。MD/DOCX/PDF正常场景发送完整12步JSON及False/0/null/未知字段，落库草稿保留旧启用指针；仅正常三场景用了同进程Redis Worker，其他是实际租约服务，未冒充独立生产CLI。

共享四模块49通过107.73秒，独立生产API/三类CLI/需求评审与关闭派发历史恢复1通过120.66秒，原影响POST2。生产命令漏设置其实际证据变量QA_IMPACT_PRODUCTION_EVIDENCE_DIR，因此先写测试临时目录；终态后从该独占fixture复制原39份文件至p4-generation-tasks-production-compatibility/md-native-finish，未重写字节。这个兼容场景没有用新增生成模块，不能当作增量产品验收。

原始失败及最终对象/文件/收据/HTTP证据在p4-generation-tasks-*。旧actual_module_worker标记只表示内部执行，当前证据已分清actual_leased_module/actual_redis_worker/independent_worker_process，旧字节保留。当前新增内部任务的配置键和两张业务表需正式启动入口接入；尚未接公开API、生产生成Worker/CLI、前端DTO/页面及失败显式新任务重试。未重复外部默认模型、前端构建/浏览器或生产历史迁移；本阶段没改公开/前端契约。下一步研究并接正式产品链路，再推进运行/环境、P2/P1/P0/P5和迁移，完整目标继续未完成。


## 增量生成正式API、独立Worker与前端契约（研究基线80dc609b）

研究及实现见P4_INCREMENTAL_ENTRY_DESIGN.md、PLAN3.6.5/6.18。正式生成router/main共用generation_runtime；独立generation_worker_runtime/CLI复用共享资源生命周期和Handler。新增项目MySQL设置、文档级提交/按原请求/分页组/模块报告、固定范围/原响应；公开typed receipt不改变内部冻结hash。模板.env.example登记qa:tasks:case_generation和CLI；不修改用户实际.env、不自动启用dispatch。生成completed仅表示草稿/候选待评审。

实际命令（Agent_Server目录，各轮stdout/stderr存对应log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_GENERATION_TASK_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-entry-api-first')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_generation_api.py tests/test_generation_tasks.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-entry-api-first.xml
$env:QA_GENERATION_TASK_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-entry-retirement')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_generation_api.py -q -k retirement --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-entry-retirement.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-entry-production-first')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_generation_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-entry-production-first.xml
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR=(Join-Path (Get-Location).Path '../docs/upgrade_2026-10-03/p4-generation-entry-compatibility')
../.venv/Scripts/python.exe -X utf8 -m pytest tests/test_requirement_reviews_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-entry-compatibility.xml
```

API+内部19通过178.72秒，补公共删除候选1通过/4 deselected13.75秒；独立正式增量三格式3通过253.67秒，每场景原影响2+生成2次POST，完整12步/扩展JSON与原固定策略验证。目标case V1保留、新增V3草稿、旧启用指针保留，新case待评审；终态重投没有新发送、API重启关闭派发后原请求/报告/范围仍可读。旧生产需求兼容1通过77.80秒、只有原影响2次。最初兼容命令用WindowsApps python别名启动失败StandardOutputEncoding，没有跑pytest；使用项目.venv后实际运行。新测试没有失败或skip，旧失败和原始证据不改写。

agent_web目录实际运行npm run test -- src/services/caseGeneration.api.test.ts src/services/requirements.api.test.ts src/services/documentImpacts.api.test.ts，8通过/3文件326毫秒；npm run build的vue-tsc/Vite通过10.58秒。没有生成页面或浏览器验收，未重跑外部默认模型/历史迁移；前端客户端不冒充完整App。原scope/response对象、完整文件/草稿/收据、物理请求、独立worker日志及MySQL保留在p4-generation-entry-*；与3723份既有原始证据逐字节核对，方案三份同步。源AST/编译和diff作为补充验证，不能替代以上实际流程。

下一步研究正式生成页面及原请求恢复，然后接失败模块显式新任务；保留原范围、父失败和未知发送，不重置完成/失败条目。运行/环境、生产历史迁移和其他P0—P5仍未完成。

独立CLI未登记流实际启动退出1/ValueError，未进入资源初始化，原stdout/stderr保存。交付校验脚本两处误读（目标引用的version属性、构建命令文本）按实际Schema/package.json修正后全量核对通过；没有改生产逻辑或覆盖旧证据。

## 正式增量App、历史下载与请求恢复〔研究基线51541987〕

按3.6.6研究实际需求评审/固定生成契约、请求缓存、完整用例、模式注册表、设置表单和正式浏览器流程后实施6份前端源/测试文件，并将旧正式产品测试扩成默认关闭的可选增量场景。完整目标原附件重新读取；没有新后端生产修改、依赖、SQL表/配置键、实际.env或用户.gitignore改动。新增实际功能设计见P4_INCREMENTAL_PRODUCT_DESIGN.md。

原失败保留：组件首轮1失败/51通过，原因是测试直接查找JSON转义后的文本，改为解析实际pre的完整对象。实际构建随后失败，暴露我使用了不存在的设置API导出名，宽松mock掩盖了错误；查实际客户端后修正并约束mock导出集合，类型检查/Vite通过12.33秒。最终独立浏览器每次重新构建最新App，不能用组件通过替代类型检查。

完整浏览器首轮 **1失败（150.75秒）**：增量正常、容量失败及停用候选均已完成，但旧影响失败场景的预算还按原V1/V2计算，新生成V3和新case后在提交阶段就拒绝。修正测试从实际PG当前版本查询计算原预算，保留捕获通过/准备失败的本来场景。第二轮 **1失败（152.05秒）** 发现真实页面缺陷：切到正常历史组后读取尚未结束，下载却导出之前的停用候选范围。保存的错误下载SHA与候选组一致，和正常组不同。修正页面切换期间的下载/操作状态、控制器下载保护和晚响应隔离，组件回归使用这个实际错误样本。

完整App第三轮 **1通过、0跳过（150.39秒）**，再复查本地请求边界：最大新建数量无效时旧控制器提前保存pending，虽然没有POST却显示未知；缺少crypto.randomUUID时无法提交。先以实际组件复现 **2失败/18通过（2.08秒）**，再改为校验通过后才保存pending、复用项目现有请求编号fallback。最终组件及旧需求/影响/生成客户端 **56通过、4文件（2.41秒）**；20项增量组件与旧36项回归，重叠轮次不累加。

最新交付浏览器 **1通过、0跳过（135.15秒）**，真实App类型/Vite构建8.61秒，浏览器实际移除randomUUID后通过fallback提交。真实DOCX、生产API与独立生成CLI形成正常2模块、失败1模块、停用候选1模块，共3组/4模块；原影响2次HTTP、增量2次HTTP，物理消息逐条匹配原冻结请求。PG原收据/任务状态、RustFS原范围与完整响应SHA/大小、下载字节、完整12步JSON/False/0/null/扩展字段与自身说明均核对。V1仍启用，同case的V3及新case保持草稿。MySQL当前容量改成1字节后原组仍使用原256000字节策略；另一个新组在发送前明确失败，原正常组不变。完成模块独立CLI重投没有第5次发送；停用候选没有生成响应或自动停用。

实际POST已提交再丢回执，刷新页面按原编号及完整原参数恢复同组；原scope/response下载一致。API重启并关闭派发、归档项目后正常/失败/停用历史可查，原请求可重放，新编号503。pageerror为空，1440宽无正文横向溢出，结果截图留存。另用默认关闭增量面板的旧正式需求App **1通过、0跳过（93.70秒）**，仍只有原影响2次HTTP，没有增量调用；共享失败预算和旧历史恢复继续通过。

实际文件、原物理请求、完整范围/响应对象、原PG收据、草稿、MySQL策略、失败下载及各轮log/XML/截图保存于p4-generation-product-*；旧4043份原文件与新证据、用户.gitignore、三份方案及暂存字节按p4-generation-product-validation.json核对。失败轮原文件保留，未把未到达的后续断言称为通过。命令见IMPLEMENTATION.md。

这些受控模型场景验证实际链路与固定依据，未调用外部默认生成模型，不代替用户业务评审；草稿生成成功不表示矩阵已正式关联、测试通过或需求已部署。失败显式新任务重试、任务池投影、草稿评审后的关系、精确Token、运行/环境、P2/P1/P0/P5剩余项及生产历史迁移仍未完成。完整目标继续保持未完成。

实际命令（Agent_Server目录，stdout/stderr合并保存在对应log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-generation-product-browser-delivery'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_generation_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-product-browser-delivery.xml
# 旧需求App兼容使用compatibility证据目录及XML：
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_requirement_reviews_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-product-compatibility.xml
# agent_web目录：
npm run test -- src/features/tools/plugins/CaseGenerationProduct.test.ts src/features/tools/plugins/RequirementProduct.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts src/services/caseGeneration.api.test.ts
```

首次和修正轮使用同一实际浏览器测试、不同证据目录，不重写旧结果；完整浏览器harness实际执行npm run build，包含vue-tsc检查。外部交付脚本generation_product_delivery_20261005.py核对原件/对象/消息、三份方案、旧证据及暂存范围，语法/diff检查是补充。没有自动推送或PR。

## 失败模块新任务与原响应复用〔研究基线12481ef2〕

上一轮12481ef2属于实际进展：正式增量页面、真实DOCX完整App和旧需求App兼容已验证。本轮重新读目标原附件、当前源文件、固定hash、共享租约/Outbox、Agent新回归和本地Agent课程幂等段，研究后采用3.6.7。修改5份后端生产文件、同步前端类型/客户端/现有mock及新客户端测试，并增加实际模块/API与独立生产流程；没有新依赖/MySQL配置键、实际.env或用户.gitignore改动。

首次真实新增模块 **7失败/2通过（77.65秒）**：我漏了内部GenerationTaskRecord到公开GenerationTaskView的显式转换，7项在新任务已保存后结果转换失败；不是未提交。原日志/XML保留，unknown拒绝路径与旧组hash读取通过。按既有公开结果方式转换完整model_dump后 **9通过、0跳过（74.21秒）**，实际MD/DOCX/PDF及MySQL/PG/RustFS/受控HTTP。之后新增真实父任务编号/模型改变、PG响应引用替换、RustFS原响应损坏与停用候选期限失败场景。

新增模块、原持久模块和公共API三模块回归 **33通过、0跳过（267.30秒）**，其中新增13项和旧20项。复查证据发现新helper把直接服务场景泛标为公共API，并与旧父冲突保存目录重名；原文件保留，旧混合目录不作为新增父冲突的完整依据。修正测试标记与独立目录，并将真实数据库重试提交回执丢失通过公共POST实际验证503，再按原编号恢复。标记/目录修正轮新增模块 **13通过、0跳过（104.70秒）**，与上述13项重叠，不累加。交付校验随后发现SQLAlchemy RowMapping被default=str写成字符串：测试中的SQL核对已通过，但保存文件不便按JSON验证。将SQL行明确转为dict并移除这份文件的字符串兜底后，最新交付轮 **13通过、0跳过（111.91秒）**，原旧文件保留；最新SQL字段/Outbox证据使用delivery目录。

最新范围包括旧组hash、3格式发送前预算失败/新策略/原完整消息/草稿，持久响应超过原预算后无新HTTP完成，实际在途取消unknown的公共409，四路同编号提交/四路领取、PG提交回执丢失503、分页、同编号改父任务409、当前模型变化发送前失败、原用例父冲突继续失败、PG子响应引用替换拒绝后恢复、实际RustFS损坏拒绝后恢复、项目越界404/归档/关闭派发历史，以及原停用候选期限失败后仅形成候选。原失败/终态/原group/scope保持；完整12步JSON及False/0/null/扩展字段、自身说明实际送达，草稿记录新retry引用。实际新task/不可变retry/Outbox同事务并通过SQL对照，原版本继续启用。

独立生产API/解析、比较、影响及生成CLI，真实DOCX、MySQL/PG/Redis/RustFS流程 **1通过、0跳过（135.10秒）**。原影响2次HTTP、原正常生成2次HTTP，另验证发送前失败后执行原请求1次和响应预算失败时原发送1次，总计6个物理POST。复用原响应的新任务没有第7次调用；两类原失败继续在原组显示。子任务完成后连同原父任务分别由独立CLI终态重投，没有新发送。API重启关闭派发后两类原请求/新task/原响应可读可重放，新编号503。所有物理消息按完整冻结请求核对；内容相同的明确新任务仍保留各自身份，不按文本误去重。

前端实际客户端与旧增量/需求/影响组件 **57通过、4文件（3.16秒）**；vue-tsc/Vite构建通过10.77秒。本轮没有正式重试按钮/缓存恢复页面的浏览器验收，没有调用外部默认生成模型、执行生产历史迁移或证明用户业务评审。受控模型证明链路及固定依据，不能证明需求已部署或测试通过。

原件、scope/response、PG重试关系/seed/收据/Outbox、实际物理消息、独立进程、首轮失败和各轮日志/XML在p4-generation-retry-*；最新功能依据使用delivery独立目录与production-first，旧4394份原文件及新证据、三份方案、用户.gitignore和暂存字节由p4-generation-retry-validation.json核对。具体命令见IMPLEMENTATION.md。

下一步实施正式重试页面与完整App恢复，继续任务池投影、草稿评审后精确关联、运行/环境适用性及P2/P1/P0/P5剩余项；完整P0—P5未完成，目标保持active。

实际命令（Agent_Server目录，stdout/stderr保存对应log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:QA_GENERATION_TASK_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-generation-retry-final'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_generation_retries.py tests/test_generation_tasks.py tests/test_generation_api.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-retry-final.xml
# 校正证据标记/目录及公共503后，使用acceptance目录：
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_generation_retries.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-retry-acceptance.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-generation-retry-production-first'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_generation_retries_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-retry-production-first.xml
# agent_web目录：
npm run test -- src/features/tools/plugins/CaseGenerationProduct.test.ts src/features/tools/plugins/RequirementProduct.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts src/services/caseGeneration.api.test.ts
npm run build
```

首轮/修正轮同新增模块、独立不同证据目录；父版本/响应故障使用实际文件与存储、完成后恢复独占测试数据。旧20项没有重新定义成功。外部generation_retry_delivery_20261005.py核对原件/对象/物理消息、SQL收据、三份方案、旧证据及暂存；源码py_compile/diff为补充。没有推送或PR，正式重试页面及全目标保持未完成。

最终证据序列化修正轮使用p4-generation-retry-delivery目录和对应XML，仍执行tests/test_generation_retries.py，13项实际功能及结构化SQL证据全部通过。首次交付校验失败为RowMapping字符串索引TypeError；原acceptance文件不改写，不将字符串当结构化SQL字典。后端生产源码在33项回归/独立生产之后没有变动，仅测试证据序列化改动。

### 6.22 正式重试App与完整历史恢复〔研究基线7d560c73〕

上一轮7d560c73仅修订完整方案及附件，属于文档进展。本轮重新读取目标原附件及当前源码，按3.6.8研究后实施：新增独立重试组件/控制器/契约核对和实际样本组件测试，原生成页面接入；新增实际浏览器流程，将共享完整产品测试扩成默认关闭的可选重试场景。没有修改后端生产实现、依赖、数据库/配置键、实际.env或用户.gitignore。

原失败保留：组件首轮1失败/55通过，选择器同时匹配父/子两个面板，测试误期待1；修正断言并补子缓存重开回查。首轮类型构建失败为测试JSON样本过宽的状态/策略类型；明确类型转换后，完整浏览器首轮又在构建阶段失败1项（51.66秒），暴露泛型mount辅助函数读取props时类型为never。改为实际按钮选择，类型检查通过；该轮未进入重试功能，不能算功能验证。首轮日志/XML与原件、API/解析进程输出均保留。

最终新重试15项与旧增量/需求/影响/客户端合计 **72通过、5文件（2.91秒）**。新项覆盖真实失败/unknown/响应/完整草稿、只提交原编号、丢回执重开、404保留/同编号重放、父/范围/module/key错误、saved_response替换拒绝、草稿hash拒绝、明确拒绝后结束编号、项目切换晚返回、下载等待、非法缓存/跨项目、分页停滞/重复、父组读取等待、失败子面板展开及独立pending恢复、无randomUUID与存储不可用。前一56项轮次重叠，不累计。

真实DOCX、built App、生产API和独立解析/比较/影响/生成CLI，实际MySQL/PG/Redis/RustFS流程 **1通过、0跳过（175.24秒）**；其vue-tsc/Vite构建通过9.09秒。页面先完成原正常生成、失败与停用候选，再从原输入预算失败模块显式创建子任务，POST已持久后故意丢回执；刷新按原父和编号恢复同task，没有再次POST。改当前MySQL策略为1字节后原子task仍使用已冻结256000字节策略，原完整请求生成V4草稿，原组失败不变。另通过正式页面制造响应预算失败，扩大响应容量、输入保持1字节，新task复用同RustFS原响应形成V5，不调用模型。旧V1仍启用，完整12步JSON/False/0/null/扩展字段和自身说明保留，草稿retry引用与收据一致。

两类retry关系与实际PG record/task seed/checkpoint一致；原scope/response对象SHA及大小、原固定消息和实际下载逐项核对。原失败父task和完成子task分别独立CLI终态重投，没有新发送。总6个物理HTTP：原影响2、原正常生成2、执行原请求1、响应预算失败的原发送1；响应复用没有第7次。API重启并关闭派发、归档项目后，两类原pending缓存恢复同关系/结果，历史下载一致，原POST可重放，新编号503。pageerror为空，1440宽页面无正文横向溢出，结果截图留存。

旧正式增量完整App **1通过、0跳过（136.10秒）**，最新App类型/Vite构建8.43秒；未启用新增测试重试流程，仍为原4次物理调用。原生成/需求历史、unknown提交恢复、停用候选、原预算失败、重启关闭派发/归档与下载继续通过。没有因为新功能已通过而跳过旧产品验收。

原件、scope/response、PG关系/seed/收据、实际物理消息、下载、截图和各轮失败/log/XML在p4-generation-retry-product-*。旧4840份原始证据与新文件、三份方案、用户.gitignore及暂存字节按p4-generation-retry-product-validation.json核对。命令见IMPLEMENTATION.md。受控模型验证链路和固定依据，没有调用外部默认生成模型、执行生产历史迁移或代替用户业务评审；草稿不直接成为矩阵覆盖、测试通过或已部署需求。

继续研究生成草稿评审后的精确需求/验证点关联，再接任务池投影、运行/环境适用性和P2/P1/P0/P5剩余项。完整P0—P5未完成，目标保持active。

实际命令（Agent_Server目录，完整stdout/stderr保留对应log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-generation-retry-product-browser-fixed'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_generation_retries_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-retry-product-browser-fixed.xml
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-generation-retry-product-compatibility'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_generation_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-retry-product-compatibility.xml
# agent_web目录：
npm run test -- src/features/tools/plugins/GenerationRetryProduct.test.ts src/features/tools/plugins/CaseGenerationProduct.test.ts src/features/tools/plugins/RequirementProduct.test.ts src/features/tools/plugins/DocumentImpactProduct.test.ts src/services/caseGeneration.api.test.ts
npm run typecheck
```

完整产品harness每次实际npm run build，保存product-build.stdout/stderr。首次浏览器失败目录browser-first，最终browser-fixed和旧compatibility分别独立保存；没有重写失败原文件。外部generation_retry_product_delivery_20261005.py核对原件/对象/物理消息、实际SQL引用、真实下载、三份方案、旧证据和暂存字节。没有自动推送或PR；完整升级仍未完成。

交付暂存检查首次报告设计文档EOF多余空行，移除后重新核对；该失败属于文档格式，不改变已验证业务源码或原始功能证据。

## 独立需求覆盖确认（2026-10-05，研究基线c2db4a4）

### 6.24 独立覆盖确认、真实文档与生产进程〔研究基线c2db4a4〕

上一轮c2db4a4修订完整方案，属于文档进展。本轮重读目标原附件，核对需求评审固定范围、不可变版本/hash、逐版本评审与生成固定收据、共享装配和本地版本化评测/治理课程后实施3.6.9。新增PG覆盖表及append-only记录，复用项目锁和需求服务；原需求评审约束保留。抽出原用例canonical算法和共享GenerationObject/Receipt，保持旧hash与导入位置兼容，避免循环Schema依赖。没有新配置键、依赖或实际用户.env变更。

新覆盖8项（1项输入校验、7项真实存储场景）与旧JSON/模板专项合计14通过、1跳过（74.15秒）；唯一跳过是未启用外部默认生成模型的旧测试。真实MD/DOCX/PDF上传、解析/比较进程、影响及两模块生成后，用公共ASGI路由确认更新/新建完整12步JSON及False/0/null/扩展字段，原需求/来源/hash不改写，未确认点仍未覆盖，执行not_assessed/部署unknown。实际提交评审后记录pending_review，显式启用后新确认记录approved，旧确认仍冻结原状态；initial/明确历史、分页/撤销与后续需求needs_review均核对。

实际PG提交后丢回执503，四路原请求恢复同记录，归档后原请求仍返回，禁止读取当前case/配置/模型/对象仍通过；四路同编号首次提交只产生一条，不同编号竞争同预期确认一成功一409。跨项目、未知点/错误哈希/生成来源、移除版本、重复版本/点拒绝。实际修改孤立PG收据和case正文、覆盖正文分别拒绝并恢复原字节。saved_response重试草稿可确认，原失败保留，不新增HTTP；其他模式的人工版本可显式关联，缺失旧评审状态标unavailable，不伪造approved。

旧需求评审、增量任务、失败重试、公开API及用例版本评审五模块53通过、0跳过（342.83秒）。前端客户端与旧需求/生成/重试组件52通过、4文件（2.91秒），vue-tsc/Vite构建通过10.82秒。没有新增覆盖编辑页面或浏览器功能验收；客户端与旧组件不是页面完成证据。

三格式生产API及独立解析/比较/影响/生成CLI3通过、0跳过（347.41秒）。真实MySQL/PG/Redis/RustFS完成固定文档、生成、明确版本评审/启用、覆盖修订、原始与历史矩阵、SQL记录/hash核对；DOCX额外实际重试两种策略并确认原响应重试草稿，原父失败仍保留。API重启关闭派发，四路原POST恢复、按编号GET及历史矩阵仍相同，归档后原请求可读/重放、新确认409。MD/PDF各4次物理模型发送，DOCX6次；覆盖确认0次，终态重投不发送。旧正常增量生产流程单独1通过、0跳过（88.55秒），仍为原4次发送，没有启用新增覆盖场景。

原件、完整scope/response、物理消息、SQL覆盖/需求/用例记录、版本hash、独立进程stdout/stderr及XML/log保存在p4-requirement-coverage-*，旧5062份原始证据不改写；三份方案、原件/对象/物理消息及暂存字节见p4-requirement-coverage-validation.json。没有调用外部默认生成模型、执行生产历史迁移或替代用户业务评审。正式覆盖确认页面、任务池投影、运行/环境及P2/P1/P0/P5剩余目标继续未完成，完整目标保持active。

实际命令（Agent_Server目录，stdout/stderr均保留对应log）：

```powershell
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'
$env:RUN_LIVE_CASE_JSON_TESTS='1'
$env:QA_GENERATION_TASK_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-requirement-coverage-first'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_requirement_coverage.py tests/test_case_json_templates.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-coverage-first.xml
$env:RUN_LIVE_CASE_VERSION_REVIEWS='1'
$env:QA_GENERATION_TASK_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-requirement-coverage-regression'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_requirement_reviews.py tests/test_generation_tasks.py tests/test_generation_retries.py tests/test_generation_api.py tests/test_case_version_reviews.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-coverage-regression.xml
$env:RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION='1'
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-requirement-coverage-production-first'
../.venv/Scripts/python.exe -Xutf8 -m pytest tests/test_requirement_coverage_production.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-coverage-production-first.xml
$env:QA_IMPACT_PRODUCTION_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-requirement-coverage-compatibility'
../.venv/Scripts/python.exe -Xutf8 -m pytest 'tests/test_generation_production.py::test_production_incremental_generation_drafts_and_disabled_recovery[md]' -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-requirement-coverage-compatibility.xml
# agent_web目录：
npm run test -- src/services/requirements.api.test.ts src/features/tools/plugins/RequirementProduct.test.ts src/features/tools/plugins/CaseGenerationProduct.test.ts src/features/tools/plugins/GenerationRetryProduct.test.ts
npm run build
```

新生产/测试13个Python文件py_compile及受影响diff检查通过，作为上述真实流程的补充。源码在真实测试开始后未再修改；文档和原始证据验收后提交相关文件，不自动推送或建PR。

交付暂存首轮一次传入全部证据文件，Windows CreateProcess在Git启动前返回WinError 206（文件名或扩展名太长）；没有执行该批暂存。改为每批不超过50项并逐批检查退出码，随后核对精确暂存范围与每份原始证据的Git blob字节。该错误属于交付命令长度，业务测试及源码不变，记录在p4-requirement-coverage-staging-first.log。

暂存检查报告原始JSON的CRLF尾部空白：新证据目录的-text规则漏配已有证据规则使用的whitespace=cr-at-eol。检查的实际退出码为2，保存脚本误期待1导致自身断言失败；随后读取真实退出码，完整stdout/stderr压缩保存在p4-requirement-coverage-staging-check-first.log.gz。补齐目录属性后重查，原始文件字节不改写，业务源码和测试不变。

## 2026-10-05 完整方案当前状态与两份副本更新

按用户要求仅更新文档，源码核对基线b908fe29。重新读取普通上传/追加版本、项目规划Prompt与query/finish、覆盖确认API和现有矩阵组件；上传不依赖Git，模板只指导生成，缺口检测由AI阅读完整用例及条件来源完成。

PLAN首页和5.2/第六节同步最新进度，将独立覆盖确认API/历史矩阵列入已验证范围；补3.6.10正式覆盖管理页面的接入、未知回执恢复、历史选择及真实DOCX浏览器验收方案，明确尚未实施。历史P0—P5范围、失败和原始证据保留。

实际执行update_upgrade_plan_20261005_current.py：三份方案逐字节同步；本地引用文件存在；6.24审计列出的472份原始文件大小/SHA逐项一致；读取上传比较、Git及覆盖确认历史XML统计。文档diff检查另执行git diff --check。没有重新运行业务测试、前端构建、浏览器、外部模型或迁移；历史测试不记为本轮运行。用户.gitignore原字节保持，提交仅包含PLAN和本实施记录，不推送、不建PR。

## 2026-10-06 正式用例覆盖确认页面与真实产品验证

研究基线028dfca8，用户原目标与实际调用链重新读取后，按P4_REQUIREMENT_COVERAGE_PRODUCT_DESIGN.md实施独立页面/控制器/身份核对及矩阵入口，复用JSON树/评审对话框。初始版本和只读属性为可选默认兼容，不新增API、配置或业务表；MySQL配置、PG业务、RustFS原件/范围职责保持。

实际失败：ES2020的Array.at不支持；矩阵回写触发数组watch反复初始化，原组件进程中断exit1，按稳定身份字符串修复；40项中1项真实第三次历史夹具漏列；真实App首轮176.80秒失败为旧创建夹具空步骤与新场景要求冲突，只在覆盖开关下提供完整创建样本。全部日志/失败XML保留，没有改写原失败。

最终组件84通过/0跳过，1.91秒；真实DOCX完整App最终1通过/0跳过，206.41秒，构建/实际独立API和Workers、MySQL/PG/Redis/RustFS全部运行。五条覆盖记录、4份完整草稿版本/hash和真实发送消息核对，覆盖0次模型调用；6次物理发送分别为影响/正常生成/重试各2。最终新增归档完整关联读取实测，保留写禁用。旧正常增量App单独1通过/0跳过，147.24秒，原4次发送；覆盖关闭时不使用新受控夹具，旧页面不加载覆盖管理。

实际命令（stdout/stderr保存对应log，原件/对象/截图/SQL在同前缀目录）：

```powershell
# agent_web目录：
npm run test -- src/features/tools/plugins/RequirementCoverageProduct.test.ts src/features/tools/plugins/RequirementProduct.test.ts src/features/projects/TestCaseAuthoring.test.ts src/features/tools/plugins/CaseGenerationProduct.test.ts src/features/tools/plugins/GenerationRetryProduct.test.ts src/services/requirements.api.test.ts --reporter=junit --outputFile=../docs/upgrade_2026-10-03/p4-coverage-product-components-verified.xml
npm run build
# Agent_Server目录：每轮使用独立QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR。
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT='1'
$env:QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-coverage-product-app-verified'
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_requirement_coverage_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-coverage-product-app-verified.xml
# 旧App回归使用p4-coverage-product-app-compatibility目录：
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_generation_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-coverage-product-app-compatibility.xml
```

另实际py_compile三个测试文件。早期读取运行中的product-build.stdout提示不存在，该文件在harness finally才复制；随后以终态实际构建日志核对，不据观察缺失重启任务。5534份旧原始证据、当前原件/对象/完整version canonical hash/PG确认hash、物理消息与暂存blob通过coverage_product_delivery_20261006.py审计，三份方案逐字节一致，用户.gitignore保持；只提交相关文件，不推送/建PR。未验证外部默认模型业务质量、用户业务评审、任务池/运行环境/生产历史迁移及其余P0—P5，不报告全目标完成。

## 2026-10-06 更新用户所贴方案与当前基线

源码核对基线5966d833。本次仅更新PLAN正文及附件/选中计划两份副本，不继续实现功能。修正首页基线和5.1旧进度，区分普通文档生成与已验P4增量模块，按实际PG查询/生成组/模块/重试关系和前端路由补充待实施的任务池入口方案；模板、简单补全、上传独立于Git及轻量解析约定保留。

实际执行plan_sync_20261006_current.py：三份计划逐字节一致、本地链接存在、五份历史XML计数与成功状态核对、用户.gitignore hash不变；另执行git diff --check及暂存范围检查。历史XML核对不算本次功能测试；未重新运行后端/前端/浏览器/模型，未迁移数据。历史证据不改写；审计保存在p4-plan-current-sync-validation.json，只提交本次方案/实施记录/审计，不推送或建PR。

## 2026-10-06 增量生成任务池与固定详情


重新读取目标附件，核对任务池联合分页、生成/重试权威表、固定读取及本地课程后实施。新增公开case_generation类型和白名单摘要，前后端消费方同步；固定详情复用现有生成/重试API和JSON树，不增加依赖、配置键或业务表。MySQL配置、PG业务和RustFS原件/大依据职责保持。

真实MD/DOCX/PDF上传、解析/比较进程、影响及生成/重试形成实际记录。最终新目录专项和旧四类兼容 **10通过、0跳过（46.58秒）**；另外旧任务池首轮5通过。验证queued/running/completed/failed、字面量搜索、项目/状态筛选、稳定分页、两种重试关系、终态不重领、归档与重建读取。实际发送取消后租约恢复保留unknown，不创建新重试或额外发送。最终组件 **108通过、0跳过，9文件**，包括15项新固定详情场景、旧任务池/生成/重试/需求/覆盖/评审/API。

失败原样保留：测试夹具将checkpoint赋空触发TS2322，改为正式类型；浏览器owned context不能新增page，改显式独立context。分页测试误改业务更新时间，改为创建同时间会话而不改冻结任务历史，最终10项通过。完整App先错误要求8条任务；转用真实存储的独立浏览器诊断（4及8条均通过），再保存正式App响应与DOM，确认同为7条，任务身份无遗漏。8份来源引用中原失败任务出现两次，验收按唯一身份计7；此前“API8条/DOM7条”判断已纠正。最后一次URL即时断言在异步导航完成前失败131.99秒，按项目已有expect.to_have_url等待实际路由，保留原失败。

最终正式DOCX built App、独立生产API/解析/比较/影响/生成CLI及MySQL/PG/Redis/RustFS **1通过、0跳过（169.57秒）**。任务池读取7个唯一任务、8份来源引用；进入正常/失败/停用/两策略子任务六项详情，查看完整草稿/自身说明/原响应，再刷新URL。API重启关闭派发、归档后重复读取同一PG任务/关系及完整历史，禁新重试。全过程6次物理模型发送，列表/导航/恢复0次新发送；原失败与unknown不重写。截图、DOM、响应及SQL留存。

旧正式增量App单独 **1通过、0跳过（111.36秒）**，池场景开关关闭，仍原4次发送，未采用新增完整创建夹具。实际构建由两次正式App harness完成，8个受影响Python文件py_compile及diff检查另作补充。所有失败/log/XML/原件/对象/消息/截图在p4-generation-pool-*；审计见p4-generation-pool-validation.json，5953份旧原始证据不改写，三份方案同步，用户.gitignore保持。

受控模型验证流程和固定依据，未调用外部默认生成模型、执行生产历史迁移或代替用户业务评审。运行/环境/回归闭环及P2/P1/P0/P5剩余目标未完成，完整目标保持active。

实际命令：

```powershell
# Agent_Server目录，输出分别保存同名前缀log：
$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS="1"
$env:RUN_LIVE_TASK_POOL="1"
$env:QA_GENERATION_TASK_EVIDENCE_DIR="G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p4-generation-pool-live-delivery"
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_task_pool_generations.py tests/test_task_pool_entries.py tests/test_task_pool_service.py tests/test_task_pool_document_differences.py tests/test_task_pool_document_impacts.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-pool-live-delivery.xml
# 各完整App轮次使用独立QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR：
$env:RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT="1"
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_generation_pool_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-pool-app-navigation.xml
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_generation_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p4-generation-pool-app-compatibility.xml
# agent_web组件命令完整保存在p4-generation-pool-components-verified.log。
```

交付审计首轮把实际SQL映射行按JSON对象读取失败：原诊断使用default=str保留了UUID行的repr；改为仅识别UUID字面量并用ast.literal_eval读取原数据，不执行原文。随后直接重算浏览器JSON.stringify文本的hash失败：浏览器将30.0呈现为30，不能用展示文本重算Python固定版本hash。改为从实际API保存的完整版本核验canonical hash，再逐值核对页面JSON；没有修改历史hash、SQL记录或原证据。最终5953份旧证据与683份新原始文件核验通过，两种App实际请求分别6次及4次，三份方案和用户.gitignore同时核对。
