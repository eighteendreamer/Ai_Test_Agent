# 御策天检系统说明：功能、架构与数据契约

本文对应源码基线 e614856，日期 2026-10-02。图、功能目录和测试结果均以当前实现为依据，不能将目录注册、模型回答或模块测试替代实际业务执行证据。

## 系统解决什么问题

系统把测试项目、测试用例及其版本、套件、运行条目、测试结果和证据组织为可追溯的业务对象。会话 Agent 提供自然语言操作入口，负责理解任务、准备上下文、请求审批、调用工具及协调执行。真正的通过/失败结论来自运行器结果与验证，不能仅凭助手说“完成”。

系统有两条互相协作的链路：会话链路负责交互、模型推理和工具执行；用例驱动链路负责固定版本、原子领取、执行结论和新回归运行。任务池及流程图展示执行过程，项目页面展示正式测试资产和统计。

依据：[项目服务](../../Agent_Server/src/application/projects/project_service.py)、[运行服务](../../Agent_Server/src/application/test_runs/run_service.py)、[会话服务](../../Agent_Server/src/application/sessions/session_service.py)。

## 用户从哪里使用系统

前端以 Vue 3、Pinia 和 Vue Router 组织页面，Electron 提供桌面包装。工作台提供会话、实时事件、审批和工具结果；项目页管理项目及用例驱动资产；任务池展示后台任务；知识页展示图谱；工具页管理扩展与文档；报告页读取报告；设置页管理配置。流程和录制窗口使用独立页面壳。

路由文件中有 12 个 path 项，包括两个重定向和项目详情参数路由，并不等于 12 个独立业务页面。前端通过 HTTP 提交/查询，运行事件通过 SSE 订阅，并在 Store 中做消息与事件关联、去重和最终正文对账。

依据：[前端路由](../../agent_web/src/router/index.ts)、[系统入口](../../Agent_Server/src/main.py)、[事件接口](../../Agent_Server/src/api/routes/sessions.py)。

## 会话任务如何执行

1. 用户在工作台选择模式、项目和模型并发送消息。输入编排负责意图、权限及执行请求，不能将模型参数视为授权事实。
2. 正式派发记录保存在 PostgreSQL，Outbox 将待投递事件交给 Relay，Redis Stream 提供投递与消费协调，Worker 执行编排任务。
3. 编排进入模型和工具运行时；工具目录来自注册表、Skill、MCP 或测试模式。需要审批的操作挂起，获得决定后按既有恢复契约继续。
4. 工具任务、工件、消息、事件和执行结果记录为业务证据。SSE 将运行变化送到前端，最终正文与持久化消息核对。
5. 资源不足时进入等待，资源租约与心跳避免重复领取；后台恢复与清理处理过期执行。身份不明或副作用未知的旧 claim 不能直接自动重放。

依据：[任务派发](../../Agent_Server/src/application/orchestration/agent_task_dispatcher.py)、[工具运行时](../../Agent_Server/src/application/runtime/tool_runtime_service.py)、[Outbox](../../Agent_Server/src/runtime/postgres_task_outbox.py)、[Worker](../../Agent_Server/src/runtime/redis_task_worker.py)。

会话状态枚举为 idle、running、waiting_approval、interrupted、completed、failed。审批状态为 pending、approved、denied。状态枚举说明允许表示的状态，不代表任意两种状态都可直接转换。

依据：[会话数据契约](../../Agent_Server/src/schemas/session.py)。

## 用例驱动链路如何保证可追溯

正式顺序为：生成草稿 → 评审 → 启用固定版本 → 套件冻结版本 → 创建运行 → 运行条目原子领取 → 运行器执行 → 结果与证据入库 → 对失败项创建新的回归运行。

用例版本保存前置条件、步骤、断言、测试数据、清理要求、内容哈希、来源引用、模型标识、Prompt 版本和 Skill 版本。套件绑定用例版本，运行条目保存 case_version_id，因此后续改稿不应原地改变已冻结运行的依据。回归创建新的运行，不把已完成条目重置为待执行。

运行总体状态为 queued、running、completed、cancelled；条目还有 claimed、waiting_resource、waiting_approval，以及 passed、failed、error、blocked、skipped 等执行结论。总体 completed 不能解释为全部用例通过，需查看条目统计和结果。

依据：[用例契约](../../Agent_Server/src/schemas/case_management.py)、[套件服务](../../Agent_Server/src/application/test_suites/suite_service.py)、[运行契约](../../Agent_Server/src/schemas/run_management.py)、[执行服务](../../Agent_Server/src/application/test_runs/execution_service.py)。

## 模式能力的实际范围

|运行模式|主要责任|验收编号|
|---|---|---|
|默认|通用 Agent、模型和工具协议|C01|
|代码治理|源码检查及 SSH 源码边界|C02|
|API 测试|文档、请求、断言、执行与恢复|C03|
|性能测试|性能方案、多引擎执行、指标和报告|C04|
|安全测试|目标授权、执行环境、证据与缺陷复测|C05|
|兼容性测试|CLI runner、任务领取、回执、结果与工件|C06|
|冒烟测试|批准方案、固定版本与执行|C07|
|UI 自动化|录制、审批、回放、断言及用例草稿|C08|

集成、单元/组件、移动、视觉回归、可访问性、可靠性六种模式目前属于目录占位。目录 manifest 或关联 Skill 存在不能证明独立完整运行器已实现。这些能力在 I01 单独记录。

依据：[模式目录](../../Agent_Server/src/modes)、[模式注册表](../../Agent_Server/src/registry/modes.py)。

## 为什么使用多种存储

|组件|责任与边界|
|---|---|
|PostgreSQL|会话、消息、事件、任务、attempt、Outbox、审批、工具工件元数据、项目及测试运行事实；也包含记忆/向量相关持久数据|
|Redis|任务 Stream、待确认投递、缓存、资源租约、热记忆与向量副本；不能用缓存状态替代正式业务事实|
|Memgraph|项目知识与 UI 录制图关系；真实大图查询容量需单独验证|
|MySQL|模型、邮件及渠道等配置；与 PostgreSQL 业务写入不是天然跨库原子事务|
|RustFS|工件及上传安全隔离对象；接口替身测试不等于真实对象存储验收|
|本地文件|文档目录、临时工作区及兼容性 CLI 持久状态；不能把单机 JSON 状态推定为跨主机协调协议|

依据：[入口中的组件装配](../../Agent_Server/src/main.py)、[图谱服务](../../Agent_Server/src/application/knowledge/knowledge_graph_service.py)、[工件服务](../../Agent_Server/src/application/artifacts/artifact_storage_service.py)。

当前 Alembic 已接入 Outbox、工具任务/工件及压缩记录等部分表与三个 revision，不代表全库 schema 迁移已经完成。数据库版本治理、历史迁移及运行时 DDL 收敛在剩余工作中明确保留。

## 如何理解这轮测试结果

功能图叶子对应 A01–I02 共 48 项，目录列出相关源码和现有测试文件。执行记录引用每个 case nodeid，并保留 setup/call/teardown 结果与跳过原因。相同断言在不同分组重复执行时分别保存，不能把总数当作独立功能数。

模块契约、真实数据库/网络集成、生产组件浏览器探针和完整工作台 UI 是不同层级。真实模型经正式 HTTP、持久派发、工具执行、SSE 到 Chromium 组件的测试，只证明该只读工具轨迹及相关正文/事件断言，不证明邮件发送、容器安全测试、全工作台按钮或供应商故障组合都通过。

项目 UI 验收使用真实前端构建、正式项目 API/服务和隔离内存存储；辅助目录采用空夹具，资源监控未装配。记录中的未装配接口属于验收环境缺口，不应编造成产品缺陷。测试发现问题只归档，本轮不修改产品实现。

查看：[验收计划与两张图](README.md)、[剩余工作](remaining-work.md)、[最近验收](latest-report.md)。
