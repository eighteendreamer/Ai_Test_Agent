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
| 配置 | 所有本次新增可编辑配置使用 MySQL；纠正相关文件配置路径 | MySQL 通用配置存储及通用/通道高级设置迁移已实施，API/Prompt 共用来源；后端 70 通过、前端 4 通过及构建通过。后续模板/解析配置仍待实施；遗留通道配对业务文件待迁移 PG |
| P1 模板 | 系统默认、项目自定义、指导与示例、模板弹窗 | 待实施 |
| P1 用例 | 完整任意 JSON、自身说明、版本、来源、前端渲染、旧结构兼容 | 待实施 |
| P1 生成 | 按模块生成、来源/模型/Prompt/Skill/模板版本、失败范围及独立重试 | 待实施 |
| P1 导入 | Excel 默认两种布局、映射/预览/错误、JSON、幂等、草稿评审 | 待实施 |
| P2 选取 | 自由/按用例依据分离；项目内召回、完整读取、AI 选择、无匹配明确返回 | 待实施 |
| P2 补全 | AI 语义检查、项目查证、正式依赖或运行准备、负向条件保留、缺口可见 | 待实施 |
| P2 执行 | 固定版本、依赖领取、输出绑定、检查点恢复、计划修订、结果证据与新回归 | 待实施 |
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
