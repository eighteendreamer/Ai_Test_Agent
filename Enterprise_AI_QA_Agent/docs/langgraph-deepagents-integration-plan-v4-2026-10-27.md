# LangGraph / Deep Agents 全系统接入方案 v4

日期：2026-10-27。性质：完整修订方案，不是实施完成报告，也不是恢复开发或上线授权。

本方案整合用户提供的 v3、附件六项评审及本轮八项调整，作为后续实施的统一契约；不再要求把 v3 的历史章节逐项相加才能理解目标。旧版本与实施记录保留，不覆盖、不重写历史证据。

- 代码核对基线：`9fc8daefe77e6cb39777e4eedaeb4d6cbbfb005e`。
- 工作区另有原生终态读取等未提交改动，不能当作上述提交已具备的能力；本轮不修改或提交这些草稿。
- 开发目标保持暂停；Docker 保持关闭。本次仅交付方案文档，不启动服务、不运行数据库故障测试、不新增付费请求、不升级依赖、不改生产开关。
- 本文“必须／采用”表达后续目标契约；“已有”仅指注明范围的现有实现；“待验收”不能被理解为已通过。

历史来源：[v3 方案](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/langgraph-deepagents-integration-plan-v3-2026-10-26.md)、[实施记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/framework-integration-progress-2026-10-25.md)、[系统架构与 Harness 初审](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/system-architecture-and-harness-review-2026-10-25.md)。

## 1. 结论、范围与不变量

### 1.1 选型结论

**采用分层组合，不在 LangGraph 和 Deep Agents 之间二选一：Deep Agents 提供自主认知 Harness；直接构建的 LangGraph 承担受控 Agent 工作流；平台继续持有业务执行权与测试事实。**

Deep Agents 的审批使用 LangGraph interrupt，不是另一套独立审批引擎。框架负责暂停和继续，平台负责谁能审批、审批哪个操作、任务如何交付以及工具结果如何裁决。[官方 Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)

| 执行形态 | 使用方式 | 不承担的职责 |
| --- | --- | --- |
| 自主探索、分析、认知规划 | Deep Agents，受限文件、Skills、认知子代理及上下文管理 | 不替代正式测试计划、Worker 调度或业务审批记录 |
| 固定边界、条件回边、正式 AgentCase | 直接构建 LangGraph，显式节点、状态和终止条件 | 不自行判定领域测试通过，不重复建设任务队列 |
| 接口、浏览器、压测、扫描、回放 | 原领域 Runner，经服务端口或治理工具调用 | 不为每个 Runner 增加一层图，不让模型接管其执行事实 |
| 历史等待／中断 Turn | 与原绑定兼容的 legacy 或框架 Worker | 不降级为展示文本重放，不迁移到任意最新依赖执行 |

### 1.2 纳入与排除

纳入：模型与工具边界、运行时绑定、请求装配、原生消息、审批与恢复、根预算、流式与取消、受限认知子代理、现有八类非占位运行形态、正式 AgentCase、Harness、兼容部署与旧实现收敛。

排除：六个占位模式的新功能开发、重写领域测试引擎、外部 OpenAI HTTP 兼容网关、替换 Coordinator、无授权真实目标测试，以及借迁移开展无关重构。

### 1.3 全程不变量

1. 一个 Turn 只有一个顶层模型工具循环所有者；子分支允许受限循环，但都进入同一根预算。
2. 同一消息范围只有一个摘要所有者，旧压缩器与框架摘要器不得重复管理它。
3. Redis 负责投递；PostgreSQL 业务记录负责平台事实；checkpoint 负责图内位置；原生 transcript 负责跨 Turn 历史；展示文本不承担恢复职责。
4. 批准不是执行成功，图完成不是应用收尾完成，应用收尾完成也不是领域测试通过。
5. 已知终态可校验复用；未知发送或未知副作用先查询／对账，不能靠重新执行“补结果”。
6. 后继不重置原 task/attempt 或已完成 RunItem，不覆盖原失败结果；回归创建新运行。
7. 证明只授予明确端口的权限，不建立一个越来越强的全局“恢复门卫”。
8. 本项目是自建执行平台；官方文档中框架托管 Agent Server 的自动持久化能力，不等于本项目已接入该托管服务。

## 2. 当前事实、草稿与证据等级

### 2.1 现状快照

| 能力 | 当前可确认范围 | 仍不能声称完成 |
| --- | --- | --- |
| 依赖与官方模型对象 | 独立 v2 环境九项直接依赖版本与锁定清单一致；有官方 SDK＋本地 HTTP/SSE 契约测试历史 | 真实供应商、全部实际配置、目标部署制品已验收 |
| v2 逐项审批 | 已有 per_call、活跃 Turn 受理、task/outbox、串行续跑、交付／结果对账及前端投影 | 所有中断类型、领域 Runner 未知效果均可恢复 |
| checkpoint 所有权 | 已有 owner-bound saver，同连接短事务保护 checkpoint 和 pending writes | 外部浏览器、压测或扫描进程已经停止／不能继续作用 |
| 模型调用治理 | 已显式接线主代理、具名子代理、摘要及 owner 作用域 DTO；有原请求与结算响应绑定 | 所有图内恢复消费、活动时间、重试组合与真实供应商均通过 |
| A1 应用已提交后的 ACK | 已有明确 scope 的原子提交证明；恢复只确认既有结果 | 可以补全部图后收尾、派生 Memory 或重新执行图 |
| A2-1 原结果与部分投影 | 已有原结果资产、分项回执、唯一 finalizer 及有证明的部分投影后继 | 没有原结果资产也能自动恢复 |
| A2-2 原生终态材料 | 未提交草稿增加只读读取与图返回后强杀审计；尚有审批恢复回归失败 | 已稳定、已提交、已开放图终态到资产空窗恢复 |
| 原生 transcript | 官方消息序列化与初步 Session 导入／发布已提交 | 跨 Turn、legacy 夹层、覆盖锚点、隐私边界与供应商签名均闭环 |
| 请求上下文 | 图外语义输入及适配器入口图输入已有冻结；Deep Agents 仍有独立 pilot Prompt | 已复用完整 PromptAssembly／Memory／Skill 管道 |
| 流式与停止 | 有子代理事件及结束后整段输出；审批可恢复 | 实时 Token 流、取消、超时及任意进程中断均可恢复 |
| 直接受控 LangGraph | 方案与 binding 的运行时类型已表达目标 | v2 Worker 已能执行该路径；当前能力检查主要放行 deepagents |
| 兼容部署 | 有 legacy manifest、独立队列、版本／部分格式能力检查 | 已部署完整双制品环境并通过滚动／回滚演练 |

默认提交入口仍以既有路径为准，不能据可选适配器存在认定全系统已经切换。legacy 主图仍不能用 v2 Deep Agents 的持久化行为代表。

### 2.2 已知失败与历史结果的使用规则

当前必须保留的失败记录：独立 Worker 审批恢复用例 `test_separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling[reconcile_after_commit]`，续接记录报告返回消息与精确原生终态的 `messages` 比较失败。**根因未在本次文档交付中复现或确定。**

续接记录报告：新终态相关组曾为 `114 passed`；另一扩展组为 `1 failed, 47 passed`；随后仍有诊断改动。这些不是本轮运行结果，也不能证明当前草稿全部通过。需在恢复开发后找回原日志并对最终源码重新验收，不能把较早绿色组叠加为“当前全绿”。实施记录 §53 尚未补齐最终回归，v4 不替它虚构通过结论。

证据登记统一保存：代码提交／工作区摘要、测试选择、真实命令、环境、时间、passed/failed/skipped/deselected、替身范围及原日志位置。实施记录追加事实，不用新记录抹掉旧失败。

## 3. 总体架构、模块边界与编排权

```text
API / Session / TestRun
        │ 受理事务：原输入、绑定、根预算、AgentTask、outbox
        ▼
平台控制平面：项目授权、正式计划、审批、证据、测试结果
        │ Redis 投递；Worker / Coordinator 领取、租约、心跳、执行代次
        ▼
RuntimeService：按不可变绑定选择运行时及允许的恢复动作
        ├─ legacy 兼容路径
        ├─ Deep Agents：自主认知 Harness
        └─ 直接 LangGraph：受控 AgentCase 工作流（待实施）
        │
        ├─ 共享模型策略 → 官方模型适配器 → 供应商原生 API
        └─ 统一工具治理 → ToolJob → 原领域 Runner / 目标系统
        │
        ▼
原应用结果资产 → 唯一 finalizer → 分项回执 / 应用提交 → 任务结算
        │
        └─ Memory 派生副本 / 资源清理：独立记录，不篡改原测试结果
```

| 模块 | 唯一职责／拥有的事实 | 明确不做 |
| --- | --- | --- |
| API／Session | 输入、项目身份、活跃 Turn、幂等、业务消息和事件读模型 | HTTP 202 不表示工具或测试已完成 |
| Task／outbox／Worker／Coordinator | 持久任务、投递、领取顺序、租约、代次与持久业务子任务 | 不从模型 task/todo 自动构造另一套调度体系 |
| RuntimeService | 运行时路由、启动／继续／停止、结果适配和恢复分流 | 不同时叠加旧 AgentLoop 与新的顶层循环 |
| 上下文／transcript 服务 | 一次装配、来源冻结、历史覆盖锚点、摘要归属和兼容投影 | 不让 Runtime 与 Session 各写一套历史拼接规则 |
| Deep Agents／受控图 | 图内推理、工具选择、条件边、子分支及框架状态 | 不持有平台授权、正式用例版本或最终测试裁决权 |
| 模型工厂／调用策略 | 配置鉴权、官方对象、每次请求的身份／资产／预算／错误 | 不因返回了模型对象就假定调用已经受管 |
| 工具治理／ToolJob | 当前权限、安全范围、审批匹配、执行权、结果与证据 | 不信任模型文字中的“已完成”，不盲重派未知效果 |
| Runner／TestRun／Verification | 冻结版本、运行条目、目标执行、断言、证据及回归 | 不用认知 todo 或模型总结替代正式计划和断言 |
| 存储／事件投影 | 图状态、业务提交、原生档案、请求响应资产各自有责 | SSE/UI 不是恢复真相，哈希不是来源授权或恢复原件 |

临时认知子代理负责单 Turn 分解，持久业务 Worker 继续由 Coordinator 调度。工具重试、审批恢复和子代理失败不得在多层重复创建同一个外部操作。

## 4. 每轮请求：唯一装配来源与执行接口

### 4.1 新 Turn 的组装流程

1. API 在既有受理事务保存原用户消息、正式 `project_id`、Turn、运行时绑定、根预算、任务及 outbox。普通新输入遵守原顺序，不挤占活跃 Turn 的审批续跑。
2. 进入图前编排，冻结语义分类的真实输入或显式 skip；分类、确定性路由及合并结果均可追溯。已冻结编排结果的后继不重新运行全部编排。
3. 上下文服务读取稳定业务消息截止锚点，合并原生历史、合法 legacy 夹层消息、当前输入、项目与用例上下文、检索结果、Prompt、Skills 和可见工具。
4. 同一装配产物生成冻结输入资产并直接送入运行时，不能分别在“保存证据”和“实际调用”处重新拼装。Deep Agents 系统能力说明与平台 Prompt 各注入一次。
5. Runtime 按绑定构造框架，框架进入每一轮模型请求前，经共享策略核对 owner、根预算、模型配置和工具集合，登记稳定调用位置，保存真实输入并取得发送许可。
6. 官方模型对象调用原生协议；请求／响应与结算身份绑定，usage 和错误明确保存。框架维护本分支完整消息，工具只经治理边界执行。
7. 图结束后依据终态语义形成原应用结果资产，唯一 finalizer 消费它，补齐业务投影并完成任务；不能从最后一段文字推导整个链路完成。

冻结范围包括：原输入、消息覆盖、ExecutionRequest、项目／用例固定版本、实际 Prompt／Skill 内容或不可变引用、Memory／检索命中原件、模型语义配置、完整工具 schema、能力开关与执行制品。只有摘要不能恢复正文；版本字符串不能恢复制品。凭据仅保存安全引用，不把旧授权冻结成永久执行权。

### 4.2 接口与绑定

统一“启动、继续、应用审批决定、取消”的运行时端口，对上保留 `RuntimeTurnResult`。这些是目标能力，不代表本轮新增了任何公共 API 或方法签名；实现时映射既有 Session／Worker／Runtime 入口并同步消费方。

- 新 Turn 固定 runtime kind/version、`thread_id=turn_id`、物理 checkpoint namespace、状态／资产格式、Prompt／Skill／工具／模型配置与依赖、制品引用。
- 每个绑定生命周期内不能切换运行时或隐式采用最新语义配置；没有绑定的历史任务按 legacy 兼容规则处理。
- `pending_turn` 保存业务续跑引用；checkpoint 保存新框架的执行位置与完整图内消息，两者相互引用但不互为替身。
- 逻辑根 namespace 与物理存储前缀分别记录，沿现有 fenced saver 映射；不把顶层 namespace 参数当作天然隔离保证。
- 新框架的条件回边在图内；路径切换后取消新路径的外层重复循环，历史路径保留至排空。

依据：[RuntimeService](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/runtime/runtime_service.py)、[运行时绑定](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/runtime_binding.py)。

## 5. 原生消息、跨 Turn 与隐私

| 存储 | 保存内容 | 用途 |
| --- | --- | --- |
| checkpoint | 图状态、原生消息、执行位置、interrupt、pending writes | 同 Turn 继续，不能重复导入历史 |
| 原生 transcript | 完整官方消息序列化、provider/transport/serializer、消息身份及覆盖锚点 | 跨 Turn 的可追溯来源 |
| 展示／业务投影 | 对话文本、工具摘要、审批进度、事件 | UI/SSE、检索和业务读模型，不反向构造原生消息 |

checkpointer 是 thread 内状态，store 是应用定义的跨 thread 数据；跨线程 Memory 不自动等于完整对话历史。[官方 Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

### 5.1 跨 Turn 发布与导入

1. 仅发布已完成且协议闭合的可导入消息范围。待工具、待审批消息保留在原 Turn，不任意截取半个调用批次。
2. 档案记录稳定业务消息锚点、来源 Turn／消息映射、覆盖区间，以及模型协议和序列化格式。
3. 新 Turn 保留原生前缀，追加锚点之后、截至当前受理输入的 `context_eligible` 业务消息，再引用当前用户的原身份，当前输入恰好一次；不能按内容相等去重。
4. 排队的未来输入、不可用于上下文的内容不导入；legacy 夹层不能因原生资产优先而丢失。没有原生资产的历史沿已有文本管道投影，不伪造旧 provider 消息。
5. 摘要必须记录覆盖身份／范围，不能拆散保留的工具配对；不同时使用旧 summary 与 Deep Agents summary 覆盖同一段。
6. 跨 provider／模型兼容性显式检查；不兼容时明确报错或使用经过验收的投影策略，不静默丢签名或改成文本继续。

同 Turn 审批恢复直接读取 checkpoint，只交付指定决定；经证据允许的无新输入继续也不走新 Turn 拼接链。

### 5.2 来源与访问控制

原生资产由服务端发布，Session Create／Update 不得接受用户伪造或改写；SessionDetail、快照、导出、日志与追踪分别检查权限和脱敏。资产引用、摘要只能辅助一致性，不能代替来源授权。请求／响应正文使用现有安全存储与保留策略，不在日志打印密钥、全文或隐私内容。

依据：[原生消息服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/framework_transcript.py)、[transcript 资产](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/framework_transcript.py)、[上下文资格规则](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/context/transcript_hygiene_service.py)。

## 6. 官方模型适配器、请求策略与根预算

### 6.1 四种传输与固定版本

| 传输 | 框架对象／路径 | 约束 |
| --- | --- | --- |
| OpenAI Chat Completions | `ChatOpenAI`，显式 Chat 协议 | 不因支持 Responses 而擅自改协议 |
| OpenAI Responses | `ChatOpenAI` 的 Responses 路径 | 原生块及会话引用按实际协议验证 |
| Anthropic Messages | `ChatAnthropic` | 保留原生内容块、签名及 usage 语义 |
| Gemini GenerateContent | `ChatGoogleGenerativeAI` | 按 Gemini 参数、签名及 usage 规则处理 |

采用已有官方工厂，复用数据库配置、鉴权、地址、请求头和能力声明，不建设 HTTP 兼容网关。[OpenAI 集成](https://docs.langchain.com/oss/python/integrations/chat/openai)、[Anthropic 集成](https://docs.langchain.com/oss/python/integrations/chat/anthropic)、[Gemini 集成](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai)

固定直接依赖如下；完整间接依赖以根锁为准，本方案不再次升级：

| 包 | 版本 | 包 | 版本 |
| --- | --- | --- | --- |
| deepagents | 0.7.13 | langchain | 1.4.6 |
| langchain-core | 1.6.10 | langgraph | 1.2.12 |
| langchain-openai | 1.6.2 | langchain-anthropic | 1.7.1 |
| langchain-google-genai | 4.4.0 | langgraph-checkpoint-postgres | 3.1.2 |
| psycopg-pool | 3.3.1 | — | — |

在线文档持续更新；实际签名、参数、默认栈和持久格式以安装版与真实测试为准。不能照抄新版流接口升级依赖。已有 Anthropic usage 兼容修正和 Google 重试参数映射保留其受测范围，升级时重验并评估能否移除本地修正。

### 6.2 每次真实调用必须受管

- 主代理、认知子代理、摘要、图外语义分类及 owner 作用域 DTO 分别接入共享策略；不假定一个 middleware 自动覆盖摘要的直接调用。
- 调用身份采用根绑定、分支、checkpoint/task 位置、purpose、attempt，不能仅用内容哈希；相同请求内容可以在不同位置合法出现。
- 先登记调用意图与预算，再保存输入，随后取得发送许可；响应保存后绑定唯一结算身份与原请求。逐次物理尝试可追溯，禁止 SDK、应用和摘要器叠加隐式重试。
- `ready` 且有未发送证明才允许按原协议发送／转移；`invoking/unknown` 不重发；`completed` 只允许校验后的原响应读取。DTO 已有只读恢复不代表图内模型节点已经具备同样消费能力。
- 不支持的参数／能力明确报错；不把工具错误、流中断或未知 usage 当成功／零用量。Chat token 参数映射以新工厂真实 wire 为准，不混用旧 `_build_model` 的限制。

### 6.3 根预算与活动时间

| 项目 | 规则 |
| --- | --- |
| 调用／Token | 所有分支、摘要和分类累计到原 Turn；预留／结算有稳定身份，恢复不得再次结算或重置 |
| 框架运行计数 | `UntrackedValue` 等运行期计数不作根预算真相；恢复前根据账本限制剩余额度，不能因新一次 invoke 又获得完整配额 |
| 时间 | 区分总体截止、根活动时间、模型耗时、排队与审批等待；根活动时间按活动区间并集，不能累加重叠子分支墙钟时间 |
| 崩溃尾段 | 复用现有 owner 心跳／租约事实，明确未关闭区间的结算与保守上界规则，恢复前处理；不凭恢复时刻猜成零、不另造租约调度器 |
| 重试 | 显式声明哪些错误可重试、次数和物理 attempt；未确认发送状态不得换 attempt 盲重试 |

时间区间方案属于应用计量设计，数值限额沿现有配置；实现前冻结未关闭尾段规则和回归，不宣称框架已自动提供。预算耗尽必须有明确终止原因，不因图退出就报告领域测试通过。

依据：[模型服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/models/model_runtime_service.py)、[官方工厂](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/model_adapters/official_chat_model_factory.py)、[调用策略](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/models/framework_model_call_policy.py)、[主／子代理与摘要接线](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/model_call_middleware.py)。

## 7. 逐项审批与统一工具治理

### 7.1 框架配置

使用固定版本官方 `HumanInTheLoopMiddleware(interrupt_mode="per_call")`，每个待审工具调用对应独立 interrupt，按 ID 提交一个决定。Deep Agents 默认 `interrupt_on` 会装配 HITL；现有同名替换方式继续由最终栈测试约束，不同时保留批量与逐项两个门。

以下只是配置形状，不是绕过业务 API 的执行入口：

```python
HumanInTheLoopMiddleware(
    interrupt_on={
        "governed_tool": {"allowed_decisions": ["approve", "reject"]},
    },
    interrupt_mode="per_call",
)
Command(resume={interrupt_id: {"type": "approve"}})
Command(resume={interrupt_id: {"type": "reject", "message": reason}})
```

平台 API 保留 approved/denied；不开放 edit/respond，不增加未授权产品能力。固定版本的 per_call 支持与答案形状见[官方逐项 HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)。

### 7.2 三组事实，不强制线性生命周期

| 事实 | 记录与含义 |
| --- | --- |
| 人的决定 | pending/approved/denied、操作者、decision ID／摘要；相同决定幂等返回，相反决定冲突 |
| 框架交付／业务应用 | queued/sending/delivered、应用事实及 proof；交付不等于工具成功，状态标签不能代替原生证据 |
| 工具执行 | 原 ToolJob 身份、执行代次、结果、证据及 unknown／待对账标记；拒绝不执行工具 |

调用身份绑定 binding、Turn、namespace、source message、tool call ID，另存 interrupt/task/checkpoint/path、原参数／范围摘要与 ToolJob。参数相同的两次调用仍是两个操作，不以名称／哈希去重。

### 7.3 受理与继续

1. 短事务核对审批属于当前有效 Turn，原子保存决定、receipt、续跑任务和 outbox。Session 为 running 时仍可受理同 Turn 其他待审批项。
2. 既有 Worker／owner 协议保证同 thread 串行继续；一项执行中，另一项可以受理但不能并发推进同图。
3. Worker 先核对原生任务、pending writes、receipt 和 ToolJob，再交付一个未消费决定。已消费则先对账，不重发整批决定。
4. 其他待审批项保留原身份；同一工具批次结果未闭合前不得进入下一轮模型。有先后依赖的业务操作由图边或领域计划表达，不依赖用户点击顺序。
5. 工具真正执行前再次核对当前权限、安全范围、原参数、审批与 ToolJob；批准后的授权失效可以被治理拒绝，不能伪造执行成功。
6. 门控谓词在恢复时保持稳定，实时授权放在执行边界复核。HITL 与工具错误处理／重试的先后由固定版本最终栈验收；不得吞掉 interrupt。

录制、代理子审批、TestRun 专用审批继续保留自己的 project/run/lease 规则，不强行改走普通 Session 审批。UI 分别展示决定受理、排队、工具执行、其他待审批和待对账，202 不是“执行成功”。

### 7.4 统一治理与未知效果

业务工具均经既有权限、安全、审批范围、ToolJob 和证据链路。Deep Agents 项目文件能力保持受限只读；默认文件写／删、命令执行及未治理通用代理不开放。Skills 内容作为任务资料，不可授权扩大文件或工具范围。

外部支持幂等键时绑定稳定操作 ID；不支持时必须查询目标／Runner journal 或进入人工对账。已知终态复用原结果，已启动但效果未知禁止盲重派。框架生成的拒绝／取消工具消息可用于协议闭合，但不是外部效果已发生的证据。

依据：[审批受理](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_approval_submission.py)、[运行适配器](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/runtime_adapter.py)。

## 8. 所有权、原生终态与校验边界

### 8.1 数据库写入与外部执行权

捕获不可变 owner：binding、task/attempt、worker、epoch/token。checkpoint 与 pending writes 在同 PostgreSQL 连接短事务内，先锁 owner／Session 行、复核租约，再调用官方 saver，持锁至提交；不在图运行期间持有长事务。

接管与写入依靠短事务建立提交顺序：已合法持锁的旧事务可以先提交，接管完成后旧 owner 的延迟写必须失败。取消协程不证明 `to_thread` 中同步数据库操作停止。

resume receipt 与真实 `__resume__` 写入沿已有同连接协议验证。该映射依赖固定版本原生任务格式，升级必须重验，不能把列表下标当 SQL 写入 idx。

领域 Runner 另须 execution ID、执行代次、资源租约、目标操作日志和停止／查询回执；owner fence 不能撤销已发送外部请求。迟到结果先作为非权威观察保存，由当前 owner 验证，不允许旧 owner 改写权威结果。

依据：[owner-bound saver](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/fenced_postgres_saver.py)。

### 8.2 原生终态不等于应用完成

原生终态证据应记录精确绑定、逻辑／物理位置、checkpoint ID、官方通道物化结果及格式／serializer；必须区分真正结束、等待审批、错误任务和人工修改的状态。`next` 为空或最后有 assistant 文本都不足以单独证明应用可恢复。

读取终态只调用只读端口，不 invoke、不重发 Command、不创建后继、不导入模型响应。其 scope 仅证明原生图终态，模型结算、ToolJob、应用投影和 Memory 各有自己的证明。

### 8.3 正常完成与恢复比较的明确契约

当前草稿对返回持久字段与恢复 state 直接比较，已有合法审批恢复样本失败。后续先查根因，再冻结以下规则，不预设“换数据源”或“删除比较”就是修复：

| 内容 | 契约 |
| --- | --- |
| 原生消息 | 保留顺序、稳定身份、tool call/result 配对、原生内容块／签名与必要 metadata；不能只比较文本 |
| Delta 通道 | 使用官方通道恢复语义，不直接索引单条 checkpoint 的 messages，也不自己拼 delta |
| UntrackedValue | 按真实图 schema 声明为非持久运行数据，不宣称它能恢复；根预算另验 |
| 持久业务字段 | 明确输出 schema 与持久状态的映射，以验证过的规范表示比较，保留原件可追溯 |
| 未知字段／格式 | 明确报不支持或待审查，不悄悄忽略，不扩展为任意字段免验 |
| 摘要 | 标明哈希对象、codec 与版本；checkpoint 摘要、物化 state 摘要和应用资产摘要不混用 |

正常路径与恢复路径最终消费相同的投影语义，但不能要求它们具有相同的恢复授权条件。某项恢复证据缺失不自动证明正常输出错误；也不能因正常执行成功就取消恢复所需校验。只允许已证明的确定性矛盾阻断，语义未知先记录和复现，不通过特判掩盖。

验收：保留当前真实失败样本；增加合法审批／子代理／摘要／运行计数正例，以及消息缺失、签名／身份漂移、未知字段负例。查清差异前不推广原生终态草稿，也不把它作为上线依据。

官方 checkpoint 解释覆盖同步持久化、Delta 通道与状态物化；这些机制不代替本项目结果等价契约。[官方 Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)

## 9. 应用结果资产与唯一投影链路

### 9.1 数据分层与设计项

```text
冻结输入／执行前投影清单
        + 原生图终态
        + 原模型结算／ToolJob 事实
        + 必需事件与子分支事实
        ↓ 同一确定性结果投影器
不可变 RuntimeTurnResult 结果资产
        ↓ 唯一 Session finalizer
快照／事件／观察／Verification／消息等分项回执
        ↓ 应用最终事务提交证明
阶段 ACK → AgentTask 结算
```

“执行前投影清单”“确定性结果投影器”是本轮补齐的设计职责，不是已经存在的新类、公开 API 或已实施 schema。实现时扩展现有资产与 Session 收尾，不另造调度层或恢复 finalizer；新增格式必须先有兼容读者。

### 9.2 必要材料与生成时机

| 材料 | 保存时机／来源 | 恢复规则 |
| --- | --- | --- |
| 原用户消息、项目、绑定、根预算、请求／上下文原件 | 受理／图前冻结 | 不重分类、不重新检索替换原事实 |
| assistant 与结果／snapshot 身份 | 首次生成时保存；未生成前可用已冻结的确定性分配规则 | 已生成身份绝不随机重建；不倒填伪造发生时间 |
| 时间、工具展示身份及映射 | 实际生成时记录；需跨图返回空窗使用的分配须前移或确定性化 | 用原值或明确可派生值，不用恢复时的“现在”替换 |
| 原生终态／完整消息 | 精确 checkpoint 及官方物化／档案原件 | 不从最后文本或简化 UnifiedMessage 重建 |
| 模型响应、ToolJob、Runner 操作 | 唯一结算引用、原请求与结果资产／journal | 已知结果复用，未知保持对账 |
| 必需审计事件、子代理业务元数据 | 产生时沿既有事件设施可靠保存身份、顺序、内容引用与完成边界 | callback／进程内缓冲不作唯一来源；不伪造“原事件” |
| UI 临时进度／Token 展示 | 可按明确产品契约暂存或重建 | 允许丢失的范围单列，不影响正式审计与结果 |
| Verification、观察与正式结果 | 依据原证据和固定规则／版本准备并冻结 | 有原结果则复用；未生成时只按原输入及确定规则生成，不重新让模型判断 |
| 实际数据库行头 | 首次写入的实际回执，如 snapshot version／created_at | 区分原写入意图和数据库分配值，重入校验实际行 |

关键约束：如果审计事件溢出后只在 Runtime.event_log，或子代理必要 callback 只在内存，则 A2-2 不具备自动恢复资格。先把这些材料的可靠落点补齐；缩小恢复范围必须明确列出，不能把缺材料等同“已支持所有中断”。

### 9.3 结果语义与空输出

框架退出、预算终止、工具失败、用户取消、等待审批与正常完成分别映射结果；不能统一设 completed。是否允许空文本完成由运行时结果契约确定：若存在合法结构化业务结果，可以无 assistant 文本，但必须有明确终态和证据。

当前结果资产可表达空串，适配器仍有非空 assistant 要求；这是一项必须对齐并回归的现存边界，不代表本轮已改变行为。不能因空串过滤掉整个合法结果，也不能放行没有任何完成依据的空响应。

### 9.4 唯一 finalizer 的重入规则

1. 原结果资产绑定原输入、源数据、绑定、原 owner／谱系、模型结算、ToolJob 及内容摘要；恢复 owner 通过独立准入接管投影权，不篡改原结果归属。
2. 正常／恢复都加载同一资产进入现有 `_finalize_runtime_result`。已存在且摘要一致的分项复用，缺失才补，冲突转对账。
3. snapshot、事件、观察、Verification、最终消息分别核对原意图、实际行和回执；不能仅凭 ID 存在认为完成，也不能要求预期行头与数据库合法分配值一致。
4. Session 合并遵守已有 rebase／并发规则，不覆盖后续输入或无关 metadata；不重复发布 completion、工具展示消息、观察或测试结论。
5. 最终 fenced 应用事务内再次校验原资产和实际分项，提交凭证只证明其 scope；异步读取／计算之后复核 owner，防止检查后失租。
6. 已有完整应用提交凭证时仅补 ACK／允许的任务收尾，不再运行图、模型、分类、Verification builder 或补派生 Memory。

依据：[结果资产格式](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/framework_runtime_result.py)、[Session finalizer](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/sessions/session_service.py)。

## 10. 故障恢复矩阵与证明权限

### 10.1 按持久事实选择恢复动作

| 故障／持久事实 | 允许动作 | 禁止／当前出口 |
| --- | --- | --- |
| submitted，尚无执行事实 | 按原输入／预算创建有谱系的图前后继 | 不重置原终态，矛盾事实先对账 |
| orchestrating，原输入与调用状态可证 | ready 按原协议安全转移；completed 读取原结算；保留确定性编排消费 | invoking/unknown 不重发，不重跑整个编排 |
| orchestrated，有冻结 ExecutionRequest | 跳过分类编排，消费原冻结请求 | 不换最新 Prompt／配置或重新分配 Turn |
| interrupt 已存，业务审批未投影 | 按稳定调用身份补建／对账 | 不按工具名猜匹配 |
| 决定已受理，尚无消费证明 | 合法 owner 核对原生任务／ToolJob 后交付指定决定 | 不能只看 queued/sending 推断未执行 |
| 工具已结算，checkpoint 未推进 | 原调用／终态校验后复用结果，按原生任务协议继续 | 不再次执行工具 |
| checkpoint 已推进，业务 applied／事件未写 | 补缺失业务投影与证明 | 不重发已消费决定 |
| 图未完成、非审批位置崩溃 | 用精确 checkpoint、模型／工具状态和 owner 判定安全继续；不具资格则对账 | 不从裸快照或最后文字获得继续权；完整图内恢复仍待实现／验收 |
| 图已完成、原结果资产不存在（A2-2） | 只有第 8–9 节材料完备后才能只读导出同一应用结果 | 当前未开放；缺材料不 invoke 图补造结果 |
| 原结果存在、应用部分投影缺失（A2-1） | 校验原意图／谱系／分项，合法后继进入唯一 finalizer 只补缺项 | 不重新执行图、模型或工具 |
| 应用最终事务已提交、阶段 ACK 缺失（A1） | 校验原子提交证明与精确引用，只补 ACK | 不要求派生 Memory 当前仍存在，不扩张到结果重建 |
| projected 已保存、任务未 finish（A2-3） | 核对应用提交与阶段，只补原结算或既有任务机制允许的结算后继／ACK | 当前准入未闭合；不得回退执行阶段或重置终态 |
| Memory 副本／资源清理未完（A3） | 按稳定操作身份补偿，记录范围与结果 | 不回滚正式测试结果，不重跑图；未知 embedding／外部操作先对账 |
| 工具／模型已发送、效果或响应未知 | 查询目标／供应商操作／Runner journal，或人工对账 | 不凭超时认定未发生、不盲目重试 |
| 旧 owner 响应迟到 | 只保留非权威观察，由当前 owner 核验 | 旧 owner 不提交权威结果或覆盖 checkpoint |

A2-3 的结算协议补充如下，避免“只补 finish”在原任务已终态时变成重置记录：

1. 原任务仍由合法 owner 持有且允许结算时，校验应用提交与 projected 引用，幂等完成原任务。
2. 原任务已失租并进入终态时，保留该 task/attempt 的原状态，通过既有 `message_recover`／outbox 建立仅结算资格的后继；不将原失败改为 completed，不运行 Runtime 或 finalizer。
3. “仅结算资格”是待实现的恢复语义，不是当前已存在的 resume_phase 枚举。新格式、阶段读者和 Worker claim 能力必须同步扩展并先通过兼容测试，不借用图前恢复资格绕过。
4. 后继只引用原应用提交／结果和原谱系，在 owner 短事务内领取、复核并记录结算；重复投递只确认原回执，阶段不倒退。
5. 在 projected 后、后继领取后、结算提交后 ACK 前分别强杀，验证仅有一次有效结算且原终态不变。

`durability="sync"` 继续用于已有持久框架路径，但它不使 checkpoint、业务记录和外部效果成为整体事务。interrupt 恢复会重新进入相应节点；副作用需幂等或拆为独立步骤。[官方 Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

### 10.2 证明只授予对应权限

| 证明 | 可以 | 不可以 |
| --- | --- | --- |
| resume 交付 proof | 确认指定决定进入原生任务 | 推断工具成功、重复发送决定 |
| 原 ToolJob／Runner 终态 | 校验后复用该操作结果 | 推断未知操作未发生、裁决整个测试通过 |
| 唯一结算模型响应及原输入 | 按原调用位置只读消费 | 升格任意 observation、重发 unknown |
| 原生图终态 | 指向精确原生结束状态 | 证明应用结果资产／业务事务／Memory 已完成 |
| 原结果资产＋部分投影准入 | 只补原结果缺失投影 | 重新 invoke 图；仅凭资产存在创建任何后继 |
| application_projection_commit | 确认 scope 内已提交结果，补 ACK | 推进图／模型、补造响应或证明所有派生效果完成 |

无证明、格式不兼容、输入／结果漂移、未知副作用分别保留可检索原因。`recovery_required` 不能被自动循环重派当成“暂时失败再试”；普通程序异常也不能一律转待对账来吞错。

观察数据如果已列入应用提交 scope，仍按原分项回执校验，不因为存放在 Memory 服务中就降为可丢失副本。A3 指明确排除在该 scope 外的派生效果／清理；两者按实际业务职责划分，不能只按存储服务名称划分。

依据：[阶段校验](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_message_stages.py)、[消息恢复准入](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_message_recovery.py)。

## 11. 流式、取消、超时与资源生命周期

| 情况 | 目标行为 | 不允许 |
| --- | --- | --- |
| 审批等待 | 原 checkpoint 与审批身份保留，等待不计根活动执行时长 | 当作完成或重开一个无关 Turn |
| Worker 崩溃／失租 | 依据第 10 节事实接管、补投影或对账 | 统一重新运行整个 Turn |
| 用户显式取消 | 记录停止意图、阻止新派发，尽力停止外部操作，保留残留状态 | 租约回收器自动重启取消任务；谎称已撤销外部副作用 |
| 超时 | 区分模型、工具、总体截止与资源超时，先核对已发送操作 | 超时即认定无效果并重试 |
| 用户暂停 | 明确暂停位置、资源保留与继续资格；不能与取消共用终态含义 | 没有持久暂停点却宣称任意恢复 |
| 客户端断线 | 后台任务按既定政策继续，重连从持久事件读取 | 断线自动等于业务取消 |

实时输出按 token／工具／审批／子分支事件关联到原 Turn、模型调用与稳定业务消息；终态 usage 单独结算。临时 Token 展示可按声明策略降采样，正式审计事件不可静默丢失。子代理事件流不等于实时 Token 流，结束后整段回调也不能标成流式完成。

复用已有 SSE、事件 sink 和前端模型；新增字段／事件时同步后端、前端类型、投影、重连和测试。取消后关闭流与连接，跟踪 Runner 停止回执；清理失败单独可见，不把正式结果改成另一结论。当前取消／超时不可恢复范围在实现和验收前保持原行为。

## 12. 受控 AgentCase 与逐模式迁移

### 12.1 正式 AgentCase

```text
固定用例／上下文准备 → 模型调用 → 治理／审批 → 工具结果汇合
         ↑                                  │
         └──────── 预算内且未达终止条件 ──────┤
                                            ▼
                                  证据评估／Verification
```

将条件回边迁入图后，再移除新路径的外层重复循环。正式链路保持：生成草稿 → 评审 → 启用固定版本 → 套件冻结 → RunItem 原子领取 → 结果与证据入库 → 失败创建新回归运行。没有断言／证据不能判通过，模型解释不替代 Assessment／Verification。

### 12.2 每模式边界与验收要求

| 模式 | Deep Agents／图职责 | 原领域服务与必过 Harness |
| --- | --- | --- |
| 默认协作 | 对话、工具选择、受限认知委派 | 当前上下文／授权／根预算／审批／取消／持久事件闭环 |
| 代码审查 | 只读文件探索、Skills、分析与认知计划；首个完整迁移模式 | 真实固定项目、文件范围／路径隔离、证据引用、问题质量基线、完整恢复与流式 |
| API 测试 | 探索、候选场景、失败解释 | 固定契约与用例版本、真实请求、正常／异常／边界／鉴权／幂等／并发、独立断言与证据 |
| UI 自动化 | 页面探索、操作方案与失败分析 | 录制、真实浏览器、资源租约、回放与断言；不能仅凭截图或模型文字通过 |
| 安全测试 | 授权范围内分析、候选策略与证据解释 | 项目／目标授权、Campaign、受控工具、范围限制、真实证据及阴性样本；无授权不发起扫描 |
| 性能测试 | 计划草稿与结果解释 | 审批后冻结方案、逐级加压、吞吐／错误率／P50/P95/P99；AI 流式另测 TTFT／Token 吞吐／阶段耗时 |
| 冒烟测试 | 用例草稿与失败解释 | 固定版本计划、Runner、明确断言、原失败保留及新回归运行 |
| 兼容性测试 | 矩阵建议与差异分析 | 矩阵冻结、环境调度／租约、Runner journal、结果身份与差异裁决 |
| 六个占位模式 | 不新增已实现声明 | 单元／组件、可访问性、视觉、移动、可靠性、集成维持占位 |

已有领域测试继续复用，不因为迁移重写所有 Runner。先完成公共契约，再完成代码审查单模式闭环，最后逐模式切换新 Turn；一个模式通过不授权其他模式默认开放。

## 13. 两类 Harness、证据等级与必跑矩阵

### 13.1 两类 Harness 不混淆

- 运行 Harness：Deep Agents／受控图负责推理、模型工具循环、认知上下文与子分支。
- 测试 Harness：项目 fixture、资源、执行入口、故障注入、断言、证据、清理及基线。

每个模式建立验收清单：模式／运行时版本、生产入口、环境资源、冻结输入、工具轨迹、状态期望、断言与证据 oracle、故障点、清理责任、必跑／可选标志、命令、实际结果及证据等级。概念字段不是本轮新增数据库 schema。

测试工程依据：本地[Agent 轨迹与协议鲁棒性](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html)、[评测工程化与 CICD](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/项目借鉴/llm-testing-course/评测工程化与CICD.html)。课程阈值不直接当本项目发布阈值，正式质量门另以版本化基线确定。

### 13.2 证据分级

| 等级 | 能证明什么 | 不能代替什么 |
| --- | --- | --- |
| E0 单元／替身 | 纯逻辑、接口映射和局部反例 | 官方框架行为、数据库／真实目标集成 |
| E1 官方框架／SDK＋本地合成服务 | 固定版本协议、中间件、HTTP/SSE 请求与响应转换 | 真实供应商特性、签名／限流、实际业务目标 |
| E2 官方框架＋真实 PostgreSQL／Redis＋独立进程 | 持久化、租约、强杀、投递与接管 | 真实 RustFS／供应商／浏览器／压测／扫描验收 |
| E3 目标依赖与模式链路 | 对应真实供应商／对象存储／领域 Runner 的实际兼容 | 双制品发布、历史恢复与生产回滚演练 |
| E4 部署与运维演练 | 新旧制品并存、兼容领取、备份恢复、灰度／回滚／排空 | 未单独验证的模式质量 |

本地跨进程对象后端即使使用 `rustfs://` 测试 URI，也不是 RustFS 集成。测试进程真实强杀，也不代表其工具是真实浏览器或真实压测器。必需项目 skip／deselect／not_run 不算通过；运行受限则报告待验收，而非擅自使用付费资源补齐。

### 13.3 公共必跑契约

| ID | 场景 | 必须断言 |
| --- | --- | --- |
| H01 | 四种传输、实际参数／工具／流式／错误 | endpoint、headers、参数不静默丢失、调用／结果配对、原生元数据／usage 及终止原因 |
| H02 | 两项审批，任意先批／反序／混合拒绝 | 已决定项可执行，其他项仍等待，同 thread 不并行，模型不提前进入下一轮 |
| H03 | B 执行期间提交 A；B 后强杀；新 Worker 处理 A | 202 及时受理；B 效果一次；原决定／ToolJob 不变；旧 owner 写入失败 |
| H04 | interrupt 保存前后、决定受理／交付／应用边界强杀 | 原生任务与 receipt 可核对，不能重发已消费决定 |
| H05 | 图终态、资产前后、snapshot／事件／观察／Verification 后、最终事务前后强杀 | 原模型／工具不增加，补缺不重建，原身份／时间与已判结果不变 |
| H06 | projected 后 finish 前、Memory／清理中途强杀 | 仅补允许的结算／派生动作，原终态不重置，无副作用盲重派 |
| H07 | 旧 owner 延迟写、第二次失租、重复投递、并发后续输入 | checkpoint／pending writes／结果均受 fence，谱系有效，不覆盖新输入 |
| H08 | 框架完成 Turn → 服务重建 → legacy 夹层 → 新框架 Turn | 实际输入不丢夹层消息，当前用户一次，签名／工具配对保留，等待审批不发布 |
| H09 | 摘要／子代理／分类／恢复／预算耗尽 | 根调用／Token／时间不归零，运行期计数不冒充持久预算，无隐式重试 |
| H10 | 实时流、断线重连、显式取消、超时、暂停 | 事件关联正确，终态不重复，取消不被接管，未知请求不重发 |
| H11 | 缺资产、漂移、未知格式、伪造 metadata、合法空结果 | 明确失败／待对账，不吞错；正常合法分支不被恢复门误杀 |
| H12 | saver 契约、Delta 祖先链、备份恢复与旧记录 | 官方通道正确恢复，必要能力合规；备份恢复后原审批可继续 |

H12 应运行与固定版本兼容的官方 saver conformance，并明确运行时禁止的删除／管理能力不在应用 owner 权限内。不能为跑测试把生产删除能力开放。

### 13.4 质量基线与命令

冻结评测集、评分标准、模型、Prompt、Skill、工具、运行时、环境和基线版本；比较证据正确性、完成率、误报／漏报、工具轨迹、调用数、Token、耗时和失败类型。确定性协议／幂等不变量不以平均分豁免；质量／成本／性能容差先冻结再对比，不虚构百分比。

下列是已有测试入口，供恢复开发后复验；**不是本次已执行命令，也不是完整验收矩阵的替代物**。数据库组必须先准备经授权的独立测试资源，不能在当前关闭 Docker 的状态下空跑或启动生产服务：

```powershell
# 工作目录：G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server
$env:RUN_LIVE_POSTGRES_TESTS = '1'
$env:PYTHONIOENCODING = 'utf-8'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_native_terminal.py tests/test_framework_terminal_gap.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_native_interrupts.py tests/test_framework_checkpoint_fencing.py -q -x -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_policy.py tests/test_framework_approval_recovery.py tests/test_framework_application_projection.py tests/test_framework_partial_projection_recovery.py -q -x -rs --tb=short
```

第一组包含尚未提交的终态草稿，复验必须记录准确工作区。后续新实现有新增回归，不能只跑这两组宣布完成。前端命令依据现有 `package.json`，按变更范围执行且保留失败：

```powershell
# 工作目录：G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\agent_web
npm run typecheck
npm run test
npm run build
```

后端语法／diff 检查不能替代运行测试；缺少 lint／类型检查工具须明确报告，不擅自安装。源码测试不替代 workspace wheel／部署构建。

## 14. 版本、数据迁移、备份与回滚

### 14.1 不可变制品与 claim 前兼容

每个发布物保存实际代码／镜像或安装制品、Python／依赖清单、根锁、Prompt／Skill／工具 schema 及状态／资产格式能力范围，绑定能够解析到可读取实物。代码指纹与 lock 摘要是校验，不是制品归档。

Worker 在 claim 前核对 runtime kind/version、operation、binding/command/stage/model-call/receipt/proof/transcript／结果资产格式；不支持则留给兼容消费者，不能先领取再失败。当前 message-stage 读者上界为 11，不代表未来格式已支持，也不代表其他所有格式能力已经检查完。

`extra="forbid"` 下新增 JSON 字段同样可能破坏旧读者；“没有 DDL”不等于可滚动升级。新格式写者前先部署兼容读者或隔离队列／消费者。历史记录按明确兼容转换读取，不在原记录上无痕改语义。

### 14.2 回滚控制必须拆开语义

| 控制 | 职责 |
| --- | --- |
| 新 Turn 路由／绑定选择 | 决定新请求进入 legacy、Deep Agents 或受控图，可灰度／停止新接入 |
| 绑定运行时执行能力 | 兼容 Worker 继续处理已存在的审批／恢复绑定，保留所需配置／模型能力 |
| 运维紧急停止 | 明确停止哪些任务／外部操作；属于停止契约，不伪装成普通路由回滚 |

当前 `_should_use_deep_agent` 对 bound Turn 也检查 `_deep_agent_enabled`，所以**不能用全局 `deep_agents.enabled=false` 直接代替“只停止新接入”的回滚**。实现可以沿现有角色配置／路由隔离完成，不要求为了该语义另造调度器。

回滚只影响新 Turn；已有 v2 绑定仍由兼容制品消费。待审批、未知效果和全部未结绑定清点后才排空旧 Worker；不能将 v2 checkpoint 转成 legacy 文本快照重放。

### 14.3 数据迁移与一致备份

- 同连接 owner／proof 协议要求 checkpoint 与业务表处于可参与既有事务的同一 PostgreSQL 数据库，并有明确 schema 权限；部署时实际验证，不因都叫 PostgreSQL 就推定可用。
- 业务库、checkpoint／pending writes、Delta 祖先 writes／快照、transcript、请求响应／结果资产及制品有一致恢复点、完整引用和保留期。不能只复制一个最终 checkpoint blob，或独立回滚其中一份。
- Delta 恢复依赖祖先写入链；维护／归档／清理必须保留所有存活 checkpoint 的依赖闭包。活动绑定、待审批和未知效果需要的资产不能被保留策略提前回收。[官方 Delta 清理约束](https://docs.langchain.com/oss/python/langgraph/checkpointers#pruning-with-delta-channels)
- 保留期限、备份位置与恢复目标按部署环境冻结，不在方案中编造数值。发布前实际恢复旧审批、原结果资产和新旧制品，验证恢复点一致。
- 私有资产、凭据引用和维护权限分离；运行时 owner 不开放任意删 thread／管理表权限。

依据：[Worker 能力](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_worker_capabilities.py)。

## 15. 实施阶段、依赖与交付出口

本节是恢复开发后的顺序，不改变当前暂停状态。A1 等已有实现复用，不重复升级依赖或从头重造审批。

| 阶段 | 主要工作与依据 → 做法 | 验收出口 |
| --- | --- | --- |
| P0 契约与失败收敛 | 现有终态比较失败 → 定位原生输出／状态差异，冻结第 8–9 节比较与材料契约；整理证据基线 | 真实失败样本有已验证根因；合法分支与漂移负例通过；不以忽略 messages 通过 |
| A1 既有完成确认 | 原应用提交证明 → 保持 ACK-only 的窄权限并回归 | 不增加模型／工具／消息／事件；派生数据变化不扩大恢复权 |
| A2-1 既有部分投影 | 原资产＋分项回执 → 继续复用唯一 finalizer，补齐新字段／边界回归 | 原输入／谱系／实际分项复核，缺失补齐、冲突对账，不重执 |
| A2-2 图终态到资产 | 材料审计 → 前移必要身份／事实保存，正常与恢复共用确定性投影器 | 真实官方框架／PG／Redis／独立 Worker 在资产前强杀后，能恢复原结果而不调用模型／工具／图 |
| A2-3 projected 到 finish | 应用提交＋阶段证明 → 沿既有任务体系补结算／ACK | projected 后强杀、重复投递／再次失租不回退执行，不重置原终态 |
| A3 派生与清理 | 已有 Memory／资源／Runner → 分项身份、回执和未知效果查询／补偿 | 副本可补、残留可见，必需事实与派生副本分离，不篡改原测试结果 |
| B 统一上下文 | PromptAssembly／transcript 现有入口 → 单一装配、覆盖锚点、服务端发布保护、固定资产消费 | 完整跨 Turn 实际请求、legacy 夹层、摘要／签名边界与隐私负例通过 |
| C 公共故障／预算 | owner／模型账本／ToolJob → 图内安全继续、活动时间、物理 attempt 和 Runner 执行权 | 各类停止语义、未知效果、运行计数、旧 owner／多次接管均有真实轨迹证据 |
| D 代码审查首模式 | 现有只读／Skills／事件 → 完整上下文、受治理子代理、Token 流、取消和质量集 | 真实项目链路、评测基线、完整恢复与资源清理通过，方可该模式灰度 |
| E 受控 AgentCase／逐模式 | 既有正式版本／Runner → 受控图条件回边、每模式公共与领域 Harness | 每模式独立验收，非空答案不是通过，失败创建新回归运行 |
| F 切换／排空／收敛 | 实际制品与兼容领取 → 双制品部署、备份恢复、灰度、回滚、旧绑定清点 | E4 演练与存量恢复通过，旧回合结清后才移除相应循环／摘要／审批代码 |

依赖约束：P0 先于扩展终态草稿；公共恢复、B/C 与所需 E3 先于首模式开放；兼容读者、制品归档和能力路由先于任何新格式写入上线，不能等 F 才开始准备。A2/A3/B/C 可按实际依赖拆小步，但不能用某一子步完成替代完整范围。

每个小步：读相关实现 → 真实失败样本或明确反例 → 最小改动 → 受影响回归 → 跨层引用核对 → 实施记录 → 仅相关文件的中文 commit，不推送／不建 PR。

## 16. 代码落点与跨边界契约清单

不预设新增类名／schema 版本号；实现时优先扩展下列已有位置，并逐项同步消费方：

| 契约／改动 | 现有落点 | 必须同步 |
| --- | --- | --- |
| 请求装配、运行入口、停止与结果适配 | [RuntimeService](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/runtime/runtime_service.py) | Context／Prompt、Session、Worker、绑定与测试 |
| 原生档案／覆盖锚点／发布保护 | [framework_transcript](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/framework_transcript.py) 与其 schema | Session API、详情／快照／导出、上下文资格、供应商投影 |
| 框架装配／子代理／HITL／事件 | [runtime_adapter](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/runtime_adapter.py) | 固定 SDK 栈、模型策略、受限 backend、前端事件 |
| 原结果／投影清单／分项回执 | [framework_runtime_result schema](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/framework_runtime_result.py) 与 Session finalizer | 资产存储、阶段、恢复、应用事务、能力读者与测试 |
| owner／阶段／审批恢复 | [message recovery](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_message_recovery.py)、fenced saver、审批 journal | Task/outbox、Worker、数据库初始化／迁移与旧记录 |
| 根模型预算／调用资产 | [framework_model_call_policy](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/models/framework_model_call_policy.py) | 主／子代理／摘要／DTO、错误与 Trace、真实 wire |
| 直接受控图／正式用例 | [现有图 builder](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/graph/builder.py)、AgentCase／TestRun／Verification | 新运行时能力、图回边、正式用例和结果契约、模式 Harness |
| 制品／格式能力／回滚 | [framework_worker_capabilities](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_worker_capabilities.py) 与 deployment | 生产者、绑定、旧／新 Worker、安装环境、保留策略 |

若公共 API、DTO、事件或 schema 发生破坏性变更，先单独说明影响并确认；新增内部字段同样核对旧读者，不用“内部实现”跳过兼容性验证。

## 17. 发布门、交付物与需要用户确认的边界

### 17.1 每阶段交付物

- 相关代码／测试与不可变版本身份，必要的格式／迁移说明。
- 一次装配、审批／结果／预算／所有权的实际轨迹和失败证据，不包含密钥或敏感正文。
- 真实测试命令、完整结果、skip 与替身范围、已知局限；不把历史日志改成当前测试结果。
- 模式验收清单、评测基线／差异、部署与回滚演练记录。
- 小范围中文 commit；不自动推送、不创建 PR。

### 17.2 发布门

1. 该模式依赖的公共 H01–H12 契约通过，未完成项保持关闭；不能只用局部 fake-model 用例授权整系统切换。
2. 对应 E3 的真实模型／对象存储／Runner 验收完成，必需测试无 skip；有限范围发布必须把未支持能力显式关闭。
3. 正式用例、运行条目、证据及结果身份完整，终态不可重领，失败回流脱敏／去重并建立新版本／新回归运行。
4. E4 制品兼容、旧回合恢复、备份恢复、回滚及资源清理通过；灰度模式／人群／窗口和撤回条件明确。
5. 未知效果、存量审批及未结绑定有可追溯处理，不能靠删除记录／资产达成排空。

### 17.3 何时需要用户确认

本轮只交付文档，不等于确认恢复开发。后续只有下列事项到达执行阶段时需明确确认：恢复实施；真实供应商／embedding 端点与费用上限；真实浏览器／压测／安全目标和授权窗口；生产灰度与回滚条件；破坏性 API／数据迁移或清理。

技术根因、投影等价、幂等、状态机和 Harness 设计由实施方根据代码及验证负责，不反复要求用户代为选择技术细节。未得到确认前不新增付费调用、不发起真实目标测试、不启动生产路由；当前 Docker 关闭状态也不因文档中的命令而改变。

## 18. v3 → v4 对照与本次交付边界

| v3 内容 | v4 收敛位置／修订 |
| --- | --- |
| §1–2 选型与职责 | §1–3 保留，新增唯一所有者／事实边界和现状快照 |
| §3、§12.2 请求与跨 Turn | §4–5：单一装配、覆盖锚点、来源冻结与私有资产保护 |
| §4、§12.3 审批 | §7–8：保留已有逐项桥接，明确三组事实与最终中间件栈 |
| §5–6、§12.1 恢复 | §8–10：等价校验契约、材料清单、唯一投影器与故障／证明矩阵 |
| §7 模型策略 | §6：保留已有工厂／账本，补运行计数／根预算与活动时间出口 |
| §8、§12.4 Harness | §12–13：逐模式实际入口／裁决、证据分级及 H01–H12 |
| §9、§12.4 部署 | §14：新接入与绑定能力分离，制品实物、Delta 依赖链与一致备份 |
| §10、§14.3 阶段 | §15–17：保持完整 A/B/C/D/E/F，新增 P0 根因出口及清晰发布门 |
| §11、§13–24 历史实现／测试 | §2 只保留当前事实索引；原文继续留在 v3／实施记录，不复制为已验收声明 |

本次文档交付仅校验文档结构、链接、引用与变更范围；不运行后端／前端测试，不启动 PostgreSQL／Redis／Docker，不做依赖／schema／开关变更，不修复现有审批回归，不替完整实施目标标记完成。当前原生终态草稿及旧文档的八个既有变更保持原样，不纳入本次文档提交。
