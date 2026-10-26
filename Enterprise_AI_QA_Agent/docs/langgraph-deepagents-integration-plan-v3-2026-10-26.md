# LangGraph / Deep Agents 接入方案 v3：附件复核与实施契约

日期：2026-10-26。复核基线：`4d19b2f4a` 加本次开始时已有的工作区改动。本轮交付为方案文档，不实施运行时迁移、不调整依赖或默认开关。已有未提交实现只作为当前工作区证据，不能视为已发布制品。

本文件是对用户所贴初版的集中修订；[v2 详细方案](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/langgraph-deepagents-integration-plan-v2-2026-10-25.md)及[实施记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/framework-integration-progress-2026-10-25.md)保留历史，不把其中不同时点的“待实现”相加作为当前缺口。

后续开发快照更新至实施记录 §42：模型/语义服务及 InputOrchestrator 已有原结算和冻结语义输入的显式消费入口，完整 orchestrating 后继与 Session 恢复调用方仍待接通；下文“本轮复核”保留原文档交付时的证据范围，不能代替后续实施记录。

## 1. 结论及附件意见的处理

**分层组合继续采用：Deep Agents 管自主认知过程，直接构建的 LangGraph 管受控 Agent 工作流，平台管业务执行权和测试事实。附件六类问题均应进入实施契约，但其中部分已有实现。**

LangGraph 的 `interrupt` 是暂停/恢复原语；Deep Agents 的 HITL 使用这个机制。两者都能支持审批，框架不负责本项目审批主体、项目授权、任务队列或 ToolJob 的业务裁决。[官方 Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[官方 Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

| 附件意见 | 当前证据与判定 | 本方案落实 |
| --- | --- | --- |
| F1 逐项审批需改整个桥接链 | 原 legacy 批量路径仍在；v2 已有按活跃 Turn 受理、逐项 interrupt、任务/outbox、串行领取及结果投影。不能用旧分支判断 v2 尚未接线 | §4：保留两条路径，迁移绑定明确选路；API、Worker、框架和 UI 一起验收 |
| F2 三类持久化不自动原子 | 成立。当前若干局部事务已合并，但业务受理、图推进、ToolJob/外部副作用仍不能整体原子提交 | §5：逐窗口对账；不承诺任意外部效果 exactly-once |
| F3 租约不自动保护 checkpoint | 原试点成立；v2 已由 owner-bound saver 在同连接事务内加锁校验并保存 checkpoint/pending writes | §6：验证旧 owner 延迟写入；独立 Runner 另补执行权协议 |
| F4 官方模型对象绕过原调用策略 | 原风险成立；当前已显式接入主代理、具名子代理、摘要及 owner 作用域中的 DTO 调用 | §7：区分调用入口，补活动时间、真实配置、重试尝试及供应商验收 |
| F5 仅保存 checkpoint 不足以跨 Turn | 成立；现有官方消息序列化已实现，但新 Turn 初始状态仍将 framework_messages 置空，完整导入链未闭合 | §3：分开档案、checkpoint、展示投影；完整跨 Turn 作为独立出口 |
| F6 runtime_version 不等于保留旧环境 | 成立；已有独立 stream、Worker 能力校验、legacy manifest，尚无真实双制品部署的完整验收 | §9：制品和持久格式能力路由，保留兼容 Worker |

附件另有两处需要修正：

- “本地未安装 Deep Agents、仅 dry-run”已过时：本轮实际导入独立 v2 环境，九项直接依赖版本与原锁定清单一致。
- “当前 OpenAI 适配器不支持显式 max_tokens”需要限定入口：旧 `LangChainModelAdapter._build_model` 仍拒绝；框架 `build_model` 转入 `OfficialChatModelFactory`，后者已有 `extra_body` 映射。不能据旧入口拒绝推导新工厂也不支持，更不能据映射存在推导所有真实端点均兼容。

附件建议的线性审批链也需要调整：拒绝不执行工具；工具可能先结算，resume 写入随后才持久化。应分别记录三组事实，不能强制一个统一先后顺序。

## 2. 模块边界及编排权

| 模块 | 拥有的事实/职责 | 对接契约 |
| --- | --- | --- |
| API / Session / AgentTask | 输入受理、活跃 Turn、幂等、任务及 outbox、前端事件 | HTTP 202 表示受理；执行结果从持久读模型获取 |
| Worker / Coordinator | 领取、租约、心跳、执行代次、持久业务子任务 | 每个绑定一次只有一个有效图写入者；持久 Worker 继续由平台调度 |
| RuntimeService | 按绑定启动/继续/取消，选择恢复协议，投影 RuntimeTurnResult | 不同时运行旧 AgentLoop 与新顶层模型工具循环 |
| Deep Agents | 探索、只读文件、Skills、认知计划、受限认知子代理、上下文压缩 | 业务工具只能调用应用治理包装；认知待办不能替代正式测试计划 |
| 受控 LangGraph | 正式 AgentCase 的节点、条件边、工具结果汇合和预算终止 | 条件循环在图内；测试通过仍由领域 Assessment/Verification 判断 |
| 模型调用策略 | 输入/响应资产、稳定调用身份、根预算、错误与用量 | 每个真实请求均接入；模型对象构造本身不算调用治理 |
| 工具治理 / ToolJob | 当前权限、安全范围、审批身份、执行状态、结果复用和证据 | 已知终态复用；未知效果查询/对账；不从模型文本推导成功 |
| 领域 Runner / TestRun | 冻结用例、运行条目、目标执行、断言、证据和回归 | Runner 可作为服务或治理工具；不为每个 Runner 增加一层图 |
| 存储与投影 | checkpoint 管图游标，业务库管平台事实，transcript 管原生消息档案 | Redis 是投递通道；SSE/UI 是可重建投影 |

一个 Turn 只有一个**顶层**模型工具循环所有者。认知子代理可以有受限子循环；它们使用独立分支身份，共享根预算，每个分支只有一个摘要所有者。禁止同一消息段同时被旧压缩器和 Deep Agents 摘要器管理。

当前 legacy 主图仍 `compile()` 且未绑定 checkpointer，循环位于 `AgentLoop`。v2 Deep Agents 的持久恢复不能代表主图的运行方式。当前图状态已移除 `_event_queue`；不能继续将附件中的该历史问题描述为尚未改动。

## 3. 每轮请求、输入冻结及跨 Turn 消息

### 3.1 新 Turn 的实际组装契约

1. 受理时在任务/outbox 同事务保存绑定、用户输入及根预算；正式项目使用 `project_id`。
2. 输入编排先冻结实际语义分类请求或明确 skip，再执行分类、确定性路由和解析合并。该阶段在图外，不能靠图 checkpoint 恢复。
3. 冻结 ExecutionRequest 与来源清单：项目/用例版本、模型语义配置、Prompt、Skill、Memory 检索内容或不可变引用、工具 schema、执行代码制品。**摘要仅证明一致性，不能替代原文/实物恢复资产。**
4. 新 Turn 从原生 transcript 构造预算内历史投影，再加入当前输入及当前模式上下文。平台 Prompt 和 Deep Agents 能力说明分别只装配一次。
5. Harness 维护本分支模型/工具消息；调用前校验 owner、配置、根预算、工具集合，通过共享策略预留稳定调用位置并保存实际输入。
6. 官方模型对象发原生协议请求；保存响应资产、结算用量，再投影展示内容。失败和未知发送状态保持可检索，不以空文本伪装完成。

这是目标契约。当前 Deep Agents Runtime 仍使用独立 code review pilot system prompt；尚未证明已复用完整 PromptAssembly/Memory 管道。图外语义实际输入已冻结，完整图输入资产与代码制品仍待补齐。

### 3.2 三种消息存储

| 存储 | 内容 | 使用方式 |
| --- | --- | --- |
| 原生 transcript | 完整 BaseMessage 序列化、provider/serializer 版本、内容块、签名、工具 ID、usage、来源及资产引用 | 跨 Turn 的可追溯来源；按稳定消息身份追加去重 |
| checkpoint | 图内消息、任务、执行位置、pending writes、框架摘要状态 | 同 Turn 恢复读取它，不再次注入整段历史 |
| 展示投影 | 文本、进度、工具摘要、审批状态 | UI/SSE；不能用于重建供应商消息 |

沿用当前 `thread_id=turn_id`，不改为 session_id。新 Turn 需要显式导入 transcript；Memory store 可以跨 thread 保存知识，不能自动替代 transcript 或执行游标。[官方 Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

摘要记录覆盖的消息身份和范围，不能拆散保留的工具调用/结果，也不能代替仍待恢复的原生内容块。跨模型或不兼容供应商的历史要走明确的投影策略，不能声称所有签名均可跨供应商复用。验证须比较实际请求字节中的内容、工具配对和签名，而不只做 Python 对象序列化往返。

## 4. 逐项审批契约

### 4.1 框架装配及稳定身份

固定 `langchain==1.4.6` 支持 `HumanInTheLoopMiddleware(interrupt_mode="per_call")`，按 interrupt ID 提交单个决定，未回答项继续等待。Deep Agents 0.7.13 默认因 `interrupt_on` 加入批量 HITL；当前 adapter 通过同名中间件替换为 per_call。每次升级验证最终栈及子代理栈，不能同时保留两个审批门。[官方逐项 HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)

当前 API 保留 approved/denied，映射官方 approve/reject。示意调用形状如下；它不是绕过业务受理的公开接口：

```python
Command(resume={interrupt_id: {"type": "approve"}})
Command(resume={interrupt_id: {"type": "reject", "message": reason}})
```

调用身份至少绑定 `(binding_id, turn_id, namespace, source_message_id, tool_call_id)`；另保存 interrupt/task/checkpoint/path、参数与范围摘要、ToolJob ID。参数哈希只作校验；同名同参数两次调用仍是两个身份。重建审批必须复用原身份，不能重置已有决定。

恢复时已有门控判定保持稳定；最新权限在业务工具包装中复核。中间件顺序必须让 HITL 包住工具重试/错误处理，interrupt 之前的代码可重入且无非幂等副作用。依赖固定版本的这些约束要有实际栈测试，不能凭“默认配置会继承”推断子代理受管。

### 4.2 三组事实及受理/执行顺序

| 事实 | 记录 | 推进依据 |
| --- | --- | --- |
| 人的决定 | pending/approved/denied、操作者、decision_id/hash | 当前有效 Turn 的待决定审批；重复同决定返回原回执，相反决定冲突 |
| 交付与业务应用 | queued/sending/delivered、独立 applied_at 和 proof | delivered 需匹配真实原生 resume；applied 需匹配原生拒绝/治理结果与 ToolJob |
| 工具执行 | 原 ToolJob 状态、结果、证据及对账标记 | 当前执行权和授权；批准不等于执行成功或测试通过 |

受理短事务原子保存决定、receipt、continuation task 和 outbox。它不持有整个图执行期间的进程内锁，Session 为 running 时仍能受理同 Turn 另一个有效审批。Worker 领取后复核所有事实，同 thread 串行继续，后续普通消息不能堵住当前 Turn 的续跑。

一次只发送一个未消费决定。若持久事实证明已消费，先对账，再选择无新输入继续；不得补发整批旧决定。尚未完成的工具批次不能进入下一次模型调用；有先后依赖的工具应拆图节点或计划步骤，不依赖用户点击顺序。

录制、代理子审批、TestRun 审批保留专用入口和各自 project/run/lease 校验，不强行改走普通 Session 审批。

## 5. 恢复协议：按故障事实选择动作

checkpointer、业务库和目标系统不构成天然分布式事务。`durability="sync"` 用于持久路径，但不证明任意外部请求只执行一次。节点恢复会重入，前置副作用必须幂等或拆开；不能从“invoke 没返回”推断“没有执行”。[官方 Interrupts 重入约束](https://docs.langchain.com/oss/python/langgraph/interrupts#resuming-interrupts)

| 故障窗口/持久事实 | 允许的恢复动作 | 禁止 |
| --- | --- | --- |
| interrupt 已保存，业务审批未投影 | 根据稳定调用身份补建/对账 | 按工具名猜测映射或新建不同审批 |
| 决定已入库，尚无交付证明 | 合法 owner 核查任务、pending writes 和 ToolJob；证明未消费后发送 | 只看 queued/sending 就认定未执行 |
| 工具终态已知，checkpoint/receipt 未推进 | 验证原调用与结果，复用 ToolJob，补证明并从合法 checkpoint 继续 | 再执行工具 |
| checkpoint 已推进，业务 applied/事件未落库 | 补业务投影；保持原消息和事件身份 | 重发已消费决定 |
| 工具已派发，效果未知 | 查询目标操作 ID/Runner journal，必要时人工对账 | 盲目重试外部请求 |
| 同步响应迟到、原 owner 已丢失 | 保存非权威观察，由新 owner 校验证明后处理 | 旧 owner 改写权威结果 |
| 图完成但 Session/Turn 未投影 | 核对完整终态后只补业务状态 | 再次运行图；将 waiting_approval 当完成 |

决定 receipt 与目标 `__resume__` pending write 要在同连接事务内保存并读回核验。固定版本实际写入是任务内 resume 列表，不是原始 Command 字典；其列表位置不是 SQL 写入 idx。当前已有最长兼容前缀处理，该适配依赖固定框架内部格式，升级必须跑对应真实任务测试。

已知审批结果的恢复已有局部实现；未知外部效果仍需各 Runner 的操作查询和证据协议。支持目标幂等键时绑定稳定操作身份；不支持时明确保留结果未知，不能承诺外部 exactly-once。

### 5.1 首次请求必须分阶段恢复

| 阶段 | 当前范围 | 下一出口 |
| --- | --- | --- |
| submitted | 已有未开始编排的安全后继 | 保持原输入、根预算、原 task/attempt 终态 |
| orchestrating | 已有实际语义输入冻结；未发送预留转移、模型/语义服务的已结算 DTO 消费入口；InputOrchestrator 可从冻结准备完成应用投影 | 接入完整后继准入及 Session 恢复调用方；冻结确定性解析/合并制品；不能重跑整个 orchestrate_async |
| orchestrated | 已有冻结 ExecutionRequest 的后继，跳过重新编排 | 补完整初始图资产和版本实物；不能推广到其它阶段 |
| runtime_submitted | 图内模型发送/资产/结算各窗口尚需完整闭合 | 用调用账本与图 pending writes 判定继续、导入或对账 |
| 图完成待业务投影 | 完整补偿出口未验收 | 使用原结果补投影，不调用模型/工具 |

当前工作区新增的 `settled_response_id` 将已完成调用绑定到唯一响应观察；`restore_completed_dto` 校验冻结输入、响应内容/格式/用量，并在异步对象读取后再次校验 owner。这与用户附带摘要相关，但只证明**原已结算 DTO 的只读读取**：不补造历史结算、不把任意 observation 升格为结果、不代表 orchestrating 调用方或图内结果导入已接通。

取消、超时、用户暂停、Worker 崩溃分别建恢复资格。审批可恢复不等于任意取消可恢复；显式取消不能被租约回收器重新执行。恢复建立后继 task/attempt 谱系，不重置原终态记录。

## 6. 执行所有权和 checkpoint 隔离

采用当前 v2 方案：捕获不可变 owner，包含 binding、task/attempt、worker、epoch/token；checkpoint 与 pending writes 写入在同一数据库连接事务内先锁 Session/owner 行，再核验最新租约并调用官方 saver。锁持有到提交，不能在事务外预检查一次后写入。[PostgreSQL 行锁](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS)

当前 `FencedPostgresSaver` 已按这个方向实现，包含 namespace 隔离、写入拒绝日志和事务回滚。原 `ThreadedPostgresSaver` 仍服务无 owner 旧试点，不能据其存在推断 v2 没有 fence。

图执行期间不持有长数据库事务。接管与 checkpoint 写入通过短事务形成有效提交顺序：原 owner 已合法持锁的事务可能先提交，新 owner 在其后接管；接管后旧 owner 延迟写入必须失败。取消 asyncio 协程不证明 `to_thread` 中的同步操作已停止。

此保护不能撤销已经发给外部系统的请求。工具派发/结果/证据要校验捕获 owner，独立 Runner 还需自己的 execution_id、执行代次、租约和目标操作日志。不要将业务表 fence 推导成浏览器、压测进程或远端扫描进程已被终止。

## 7. 模型协议、预算与调用治理

保持九项直接依赖版本及根锁，不因在线文档增加新 API 就升级依赖。四种传输分别走官方 OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini GenerateContent；无需 OpenAI HTTP 兼容网关。

每个真实请求使用稳定 `(branch, checkpoint/task position, purpose, attempt)` 身份，先登记意图/预算，再保存实际输入资产，随后取得发送许可。身份不能仅来自内容哈希，相同文本在不同节点可以合法调用两次。

主代理、具名子代理、摘要及前置语义分类分别接共享策略；摘要可能直接调用模型，不能假定 wrap_model_call 自动覆盖它。当前已有显式接线，仍须按实际提供业务能力的分支逐项验收。

| 预算/策略 | 必须遵守的规则 | 尚需补齐的证据 |
| --- | --- | --- |
| 调用数与 Token | 所有分支累计到原 Turn；预留与结算原子；失租/重启不归零 | 真实端点流式 usage、未知响应及各类重试 |
| 重试 | 每次物理请求有可追溯 attempt；禁止 SDK、摘要器和应用叠加隐式重试 | 当前零隐式重试路径之外的显式尝试模型 |
| 活动时间 | 持久化实际活动片段；审批等待与排队时间分开，不用恢复时重新计时 | 此项仍是待实现/验收出口 |
| 参数与错误 | 真实配置映射、超时、传输、能力差异明确；不可支持的配置返回明确错误 | 四传输真实供应商、推理/结构化输出/工具组合、Anthropic 图内 wire |
| 证据资产 | 请求/响应绑定原身份和校验和；日志脱敏，正文受现有存储权限与保留策略保护 | 真实对象存储及读取恢复；当前部分用例使用内存存储替身 |

禁止将旧 owner 的已发送请求重新发送来“修复”预算。发送状态 invoking/unknown 必须先查证；已知 completed 只读恢复不能重复计费/计数，不把缺失 usage 猜成零。

## 8. 各模式及两类 Harness

Deep Agents 的运行 Harness 管模型工具循环与认知上下文；测试 Harness 管输入 fixture、资源、故障注入、断言和证据。接入运行 Harness 不等于已有各模式验收。

| 模式 | Agent 边界 | 必过模式 Harness |
| --- | --- | --- |
| 默认协作 | 对话、工具选择、认知委派 | 上下文、权限、预算、审批、取消与事件 |
| 代码审查 | 只读探索、分析、引用证据 | 真实项目、Prompt/Skills、文件范围、结论证据、恢复及质量基线 |
| API 测试 | 探索、候选场景、失败解释 | 固定契约/用例、请求执行、鉴权、幂等、断言及证据 |
| UI 自动化 | 页面探索与方案 | 录制、浏览器租约、恢复、回放与断言；真实浏览器 |
| 安全测试 | 授权范围内的分析 | 授权/Campaign、受控工具、目标范围、证据裁决 |
| 性能测试 | 计划草稿、结果解释 | 批准后的冻结方案、逐级加压、采样、P50/P95/P99 与阈值 |
| 冒烟测试 | 用例草稿、失败解释 | 固定计划、Runner、证据及失败生成新回归运行 |
| 兼容性测试 | 矩阵建议、差异分析 | 矩阵冻结、环境领取、Runner journal、结果身份及差异 |
| 六个占位模式 | 维持占位 | 单元/组件、可访问性、视觉、移动、可靠性、集成不因接框架宣称实现 |

正式 AgentCase：上下文 → 模型 → 治理/审批 → 结果汇合 → 预算/终止判断 → 条件回边或证据评估。迁移时把回边放图内，再移除新路径外层重复循环；平台原固定版本、RunItem 原子领取和终态保护继续使用。

模式 Harness 按本地[Agent 轨迹课程](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html)和[评测工程化课程](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/项目借鉴/llm-testing-course/评测工程化与CICD.html)记录输入、工具轨迹、状态、环境、评分和失败样本；失败回流先脱敏/去重，不覆盖原失败结果。质量基线包括模型、Prompt、Skill、工具、运行时版本及调用数/Token/耗时，不能只有最终答案评分。

## 9. 版本、数据迁移与回滚

选择双制品/独立队列路线，沿现有 legacy manifest 和 v2 stream 扩展。runtime_version、lock 摘要只说明声明身份；发布还必须保存实际代码制品、Python/安装依赖、配置及可读写的持久格式能力范围。

1. 绑定保存不可变代码/依赖/Prompt/Skill/模型语义配置/工具 schema/状态格式引用。凭据按引用轮换，当前权限实时判断。
2. Worker **claim 前**检查 runtime kind/version、operation、binding/command/stage/model-call/receipt/proof 的格式范围；旧代码不能先领取新任务再失败。
3. 当前存在 `extra="forbid"` 的严格模型；新增 JSON 字段也可能是部署不兼容，不能因无 DDL 宣称可滚动升级。需要先发布兼容读者或隔离消费者，并用真实历史记录恢复验证。
4. 同连接 proof 协议要求 checkpoint 与业务表在同 PostgreSQL 数据库并具备相关 schema 权限；备份、恢复和保留期覆盖两者及对象资产，不能独立还原成互相矛盾的时间点。
5. 回滚只改新 Turn 路由；已有 v2 thread 交兼容 Worker。不能降级到 legacy 文本快照重放。未知效果、待审批和全部未结绑定清点完，才排空旧 Worker/清理资产。

当前 v2 默认开关仍关闭；真实双制品部署、存量审批恢复及回滚演练尚未验收。没有把“manifest 存在”视为旧运行环境已经部署。

## 10. 实施顺序与可验收出口

| 顺序 | 依据 → 做法 | 主要改动位置 | 通过条件 |
| --- | --- | --- | --- |
| A | 当前首次 message 阶段日志 → 接通 orchestrating 恢复调用方，补活动时间 | Session/InputOrchestrator/SemanticIntent、模型策略/账本、message recovery | 未发送显式转移、completed 只读导入、unknown 不重发；原输入/预算/终态不变 |
| B | transcript 与实际输入尚未贯通 → 冻结图资产、接统一 Prompt、完成跨 Turn | PromptAssembly/context、Runtime、framework_transcript、binding/制品 | 原生块/签名及工具结果完整；恢复不重拼；下一 Turn 实际请求可验证 |
| C | 已有审批恢复 → 补其余窗口和 Runner 执行权 | saver/journal、ToolJob、领域 Runner、业务投影 | 旧 owner 延迟写拒绝；未知效果可核对；图完成仅补投影 |
| D | 当前独立 pilot → 代码审查真实链路 | code review、只读 backend、Skills、事件/UI | 实际评测集、真实文件、Token 流式、取消/超时、完整恢复 |
| E | 公共契约稳定 → 受控 AgentCase、逐模式迁移 | graph、TestRun/Verification、各模式 | 每模式独立 Harness 通过；无断言/证据不能判通过 |
| F | 版本绑定与兼容读者 → 双制品切换、排空、回滚及收敛 | Worker capabilities、部署、数据库迁移/保留策略 | 旧等待回合恢复、新旧任务不串领；演练后再移除旧循环/摘要/审批代码 |

A/B/C 都是架构边界补齐，不能在入口加几层 try/except 当作完成。依赖和基础审批已有实现，后续从剩余出口推进，不重新开展一轮相同依赖升级。每次变更给出小范围 diff、真实验证和独立提交。

## 11. 本轮证据、验证和限制

### 11.1 Evidence → Finding → Path

| Evidence：本轮读取的实现 | Finding：可据此确认 | Path：实施去向 |
| --- | --- | --- |
| [审批受理](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_approval_submission.py) `assert_active_approval` | v2 接受 running/waiting_approval 的活跃 Turn；旧事务条件不能代表 v2 | §4、A/C 的 API/Worker 回归 |
| [owner saver](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/fenced_postgres_saver.py) `_write_transaction/put_writes` | owner 锁、原生写、交付 proof 有明确事务边界 | §5/6、C 的跨进程故障矩阵 |
| [运行适配器](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/runtime_adapter.py) | per_call、Command/None、sync 已实现；不证明 Token 实时输出 | §4、D；以实际最终中间件栈验收 |
| [RuntimeService](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/runtime/runtime_service.py) `_execute_deep_agent_turn/_build_initial_state` | pilot Prompt、结束后整段回调、初始 framework_messages 为空 | §3、B/D，不能把序列化测试当跨 Turn 完成 |
| [官方模型工厂](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/model_adapters/official_chat_model_factory.py) | 新框架入口四传输及 max_tokens 映射，与旧 _build_model 不同 | §7、真实配置矩阵 |
| [模型调用策略](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/models/framework_model_call_policy.py) `restore_completed_dto` | 已结算 DTO 可校验后读取，未等同应用/图内导入 | §5.1、A 的恢复消费端 |
| [Worker 能力](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_worker_capabilities.py) | 版本/锁/operation 校验已有；代码及所有持久格式能力仍不足 | §9、F 的制品验收 |

复现方式：在本仓库读取上述符号，按下列命令运行本轮选定测试；在线文档检索日为 2026-10-26。安装版本与签名也使用 `.venv-framework-v2/Scripts/python.exe` 实际导入核对，未安装新依赖。

### 11.2 本轮实际验证

工作目录为 `Agent_Server`，解释器为仓库独立 v2 环境。实际执行第一组：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
& ../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_dependencies.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_approval_submission.py tests/test_framework_model_result_import.py tests/test_framework_message_diagnostics.py -q -x -rs --tb=short
```

结果 **111 passed，265.96 秒，无 skip**。覆盖依赖锁、checkpoint/pending writes 的 owner 事务隔离、延迟同步线程写入、DeltaChannel、官方框架逐项审批身份、running 时受理另一决定、模型结算响应绑定/只读读取和诊断。

第二组在 HTTP/recovery/chat-model 三个文件上按最终 `-k` 表达式筛选，以下为等价的单表达式复现命令（执行时重复的 `-k` 以前面的值被最后一个覆盖）：

```powershell
& ../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_http.py tests/test_framework_approval_recovery.py tests/test_framework_chat_models.py -k 'http_accepts_sibling_decision_while_session_is_executing or separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling or selected_native_transport or token_parameter' -q -x -rs --tb=short
```

结果 **4 passed、68 deselected，103.51 秒，无 skip**。实际选中 HTTP 并发审批 1 项及独立 Worker 强杀恢复 3 项；此命令没有选中 chat-model 测试，不能据两个未匹配的筛选词声明传输参数验收。

HTTP 用例在 B 工具被阻塞时，通过 ASGI 请求提交 A 拒绝，2 秒内得到 202，并确认 A 暂时不能并发领取。三个强杀参数分别为工具/native 结果已保存后的正常对账、对账提交前强杀、提交后 ACK 前强杀。新 Worker 最终处理 A，B 的受控效果计数保持 1，原任务终态不变。

为验证入口差异，另独立执行：

```powershell
& ../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_chat_models.py::test_explicit_chat_token_profile_is_preserved_on_the_wire -q -x -rs --tb=short
```

结果 **2 passed，5.59 秒，无 skip**，检查 max_tokens/max_completion_tokens 两种配置的实际 HTTP 请求，并断言另一参数不被混入。

证据等级：上述数据库/任务用例使用真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph/saver；部分模型为 fake chat model，DTO 与参数用例为官方 SDK 加本地合成 HTTP，响应资产部分使用内存 RustFS client 替身。独立 Worker 是真实进程，工具为可计数的受控测试工具。不能将这些结果标记为真实供应商、真实 RustFS 或实际浏览器/压测/扫描验收。

九项直接版本实际导入与原锁定版本全部一致；文档 11 个本地链接存在、代码围栏配对。此前摘要“65 项”不作为本轮运行结果。本轮仅新增本文件，原八个已修改文件和已有未跟踪测试保持原状，不纳入本轮提交。

### 11.3 发布必过、当前不能以局部回归替代的项目

- 公共契约：四传输真实配置/工具/流式/usage/原生消息；主/子代理/摘要/图外分类预算；完整跨 Turn；版本兼容。
- 关键故障轨迹：两工具待审批 → 批准 B 执行 → 强杀 Worker → 新 owner 处理 A → B 不重执行，旧 owner 不能覆盖 checkpoint，最终模型收到完整匹配结果。已有强杀 Harness 的历史结果保留在实施记录；本轮单独列明实际重跑范围。
- Saver：当前 fence/DeltaChannel 测试以外，补兼容固定版本的官方 conformance suite；运行时禁止的删除/管理能力独立声明，不为测试开放生产权限。[官方 conformance 指引](https://docs.langchain.com/oss/python/langgraph/checkpointers#testing-with-the-conformance-suite)
- 真正外部依赖：真实供应商、证据对象存储、相应浏览器/压测/扫描 Runner、双制品部署。模拟 HTTP 和内存存储替身不能标为这些集成已通过。

本轮不声称全量测试、前端、lint/typecheck、官方 conformance、全部领域模式或生产发布通过。方案补全和局部测试通过，仅构成下一阶段实施依据。
