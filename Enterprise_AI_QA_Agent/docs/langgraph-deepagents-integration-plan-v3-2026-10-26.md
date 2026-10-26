# LangGraph / Deep Agents 接入方案 v3：附件复核与实施契约

日期：2026-10-26。第 1–13 节续核基线为 `de175ecab`；第 14 节再次核验基线为 `64d0c9ff2` 及其当前未提交工作区。交付只更新本方案，不修改或提交运行时代码，不调整依赖、数据库结构或默认开关。未提交草稿只作为风险核验对象，不能视为已发布制品。

本文件是对用户所贴初版的集中修订；[v2 详细方案](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/langgraph-deepagents-integration-plan-v2-2026-10-25.md)及[实施记录](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/framework-integration-progress-2026-10-25.md)保留历史，不把其中不同时点的“待实现”相加作为当前缺口。

后续开发已到实施记录 §44：orchestrating 的安全后继及 Session 调用方、图输入资产冻结已经接通；HEAD 另有 runtime_submitted 的受限只读投影入口。**后者仍被本次探针证实的准入条件限制，不能认定真实框架回合的完成补偿已闭环。** 第 11 节保留原文档交付时的历史验证，第 12–13 节是本次续核新增的契约和证据；发生时点差异时，以本次明确限定的结论为准。

## 1. 结论及附件意见的处理

**分层组合继续采用：Deep Agents 管自主认知过程，直接构建的 LangGraph 管受控 Agent 工作流，平台管业务执行权和测试事实。附件六类问题均应进入实施契约，但其中部分已有实现。**

LangGraph 的 `interrupt` 是暂停/恢复原语；Deep Agents 的 HITL 使用这个机制。两者都能支持审批，框架不负责本项目审批主体、项目授权、任务队列或 ToolJob 的业务裁决。[官方 Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[官方 Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

| 附件意见 | 当前证据与判定 | 本方案落实 |
| --- | --- | --- |
| F1 逐项审批需改整个桥接链 | 原 legacy 批量路径仍在；v2 已有按活跃 Turn 受理、逐项 interrupt、任务/outbox、串行领取及结果投影。不能用旧分支判断 v2 尚未接线 | §4：保留两条路径，迁移绑定明确选路；API、Worker、框架和 UI 一起验收 |
| F2 三类持久化不自动原子 | 成立。当前若干局部事务已合并，但业务受理、图推进、ToolJob/外部副作用仍不能整体原子提交 | §5：逐窗口对账；不承诺任意外部效果 exactly-once |
| F3 租约不自动保护 checkpoint | 原试点成立；v2 已由 owner-bound saver 在同连接事务内加锁校验并保存 checkpoint/pending writes | §6：验证旧 owner 延迟写入；独立 Runner 另补执行权协议 |
| F4 官方模型对象绕过原调用策略 | 原风险成立；当前已显式接入主代理、具名子代理、摘要及 owner 作用域中的 DTO 调用 | §7：区分调用入口，补活动时间、真实配置、重试尝试及供应商验收 |
| F5 仅保存 checkpoint 不足以跨 Turn | 成立；已提交代码有官方消息序列化和图输入资产；工作区草稿开始从 Session metadata 导入原生历史，但覆盖范围、未完成调用及私有资产保护仍不完整 | §3、§12.2：保留完整跨 Turn 独立出口，不以 helper 测试代替链路验收 |
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

这是目标契约。当前 Deep Agents Runtime 仍使用独立 code review pilot system prompt；尚未证明已复用完整 PromptAssembly/Memory 管道。图外语义实际输入、编排代码指纹和适配器入口的 FrameworkGraphInputAsset 已冻结；指纹不是代码归档，入口资产也不等于每次物理模型请求、工具完整 schema 或可恢复的制品实物。后续接统一 Prompt 时必须复用同一装配结果生成资产和实际请求，避免两处独立拼装产生漂移。

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
| orchestrating | 已有安全后继准入、编排指纹及 Session 冻结输入消费；ready 转移、completed 只读读取、unknown 拒绝重发 | 继续核验制品实物与实际环境；不能重跑整个 orchestrate_async |
| orchestrated | 已有冻结 ExecutionRequest 的后继，跳过重新编排；适配器入口图输入资产已落库 | 补版本实物及统一 Prompt；不能推广到其它阶段 |
| runtime_submitted | 新增“已有业务快照”的只读入口；当前准入仍拒绝该 Turn 的 checkpoint/ToolJob 事实，见 §12.1 | 按执行阶段重新设计准入；用账本、图 pending writes 与投影提交证明判定继续、导入或对账 |
| 图完成待业务投影 | 原结果到完整业务提交的补偿出口未闭合；已有快照不证明消息、Session、验证和 Memory 均已提交 | 持久化原应用结果及幂等投影提交事实，只补缺失部分，不调用模型/工具 |

已提交的 `settled_response_id` 将已完成调用绑定到唯一响应观察；`restore_completed_dto` 校验冻结输入、响应内容/格式/用量，并在异步对象读取后再次校验 owner。模型、语义服务和 orchestrating 的应用调用方已接通该只读消费；它不补造历史结算、不把任意 observation 升格为结果，**也不代表图内模型结果导入或全部业务投影补偿已接通**。

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
| A | 图前恢复已有实现，图后准入仍有错位 → 先闭合 §12.1，保留未知发送拒绝 | Session/Runtime、message recovery、结果资产/业务投影 | 真实 checkpoint 与已提交投影共存可恢复；未提交部分补齐；模型/工具调用不增加；原终态不变 |
| B | 图入口资产已有实现，跨 Turn 仍不完整 → 接统一 Prompt，按 §12.2 完成消息所有权 | PromptAssembly/context、Runtime、framework_transcript、binding/制品 | legacy 夹层不丢失、当前用户不重复、等待调用不发布、资产不可由 API 改写、实际请求块/工具配对正确 |
| C | 已有审批恢复与模型账本 → 补其余故障窗口、活动时间与 Runner 执行权 | saver/journal、模型策略、ToolJob、领域 Runner | 旧 owner 延迟写拒绝；未知效果可核对；根活动预算跨接管不归零；独立 Runner 不被盲重派 |
| D | 当前独立 pilot → 代码审查真实链路 | code review、只读 backend、Skills、事件/UI | 实际评测集、真实文件、Token 流式、取消/超时、完整恢复 |
| E | 公共契约稳定 → 受控 AgentCase、逐模式迁移 | graph、TestRun/Verification、各模式 | 每模式独立 Harness 通过；无断言/证据不能判通过 |
| F | 版本绑定与兼容读者 → 双制品切换、排空、回滚及收敛 | Worker capabilities、部署、数据库迁移/保留策略 | 旧等待回合恢复、新旧任务不串领；演练后再移除旧循环/摘要/审批代码 |

A/B/C 都是架构边界补齐，不能在入口加几层 try/except 当作完成。依赖和基础审批已有实现，后续从剩余出口推进，不重新开展一轮相同依赖升级。每次变更给出小范围 diff、真实验证和独立提交。

## 11. 本轮证据、验证和限制

### 11.1 Evidence → Finding → Path（原文档交付时的历史快照）

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

### 11.2 原文档交付时实际验证（不计入第 13 节本次运行）

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

## 12. 本次补全：必须先闭合的应用契约

本节是应用设计，不是新增的框架 API，也不表示已经完成实现。依据是当前代码、下述运行探针及官方固定版本行为；复用已有阶段/账本/任务体系，不建立第二套调度器。

### 12.1 按阶段准入，不能沿用“图前不得有执行事实”的统一条件

**本次新增发现 F10：只读投影入口已存在，但真实 checkpoint 会阻止它被领取。**

[FrameworkMessageRecovery 的 `_recoverable`](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/framework_message_recovery.py) 在 runtime_submitted 分支读取业务快照后，仍进入函数末尾的统一检查：只要该 Turn 有 ToolJob、checkpoint 或 pending writes，就以 `Pre-runtime message has tool or checkpoint execution facts` 拒绝后继。这一条件适用于图前阶段，不能作为图已执行阶段的完成准入。本次用官方 LangGraph、owner-bound PostgreSQL saver 跑完单节点图，再保存匹配绑定的 completed 快照，确实得到上述拒绝；不是推测已发生生产故障。

**同时存在 F11：业务快照提交不等于整个应用收尾已经提交。** [SessionService 的 `_finalize_runtime_result`](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/sessions/session_service.py) 先 `save_snapshot`，后续才保存 Session、消息和最终事件等；`_recover_projected_framework_message` 只读快照并推进 projected，不能据此证明其余步骤齐备。现有成功用例注入的是精简快照，没有制造真实 checkpoint，也没有覆盖完整收尾中途强杀。

采用以下分支，而不是简单删除所有保护：

| 持久事实 | 准入与动作 |
| --- | --- |
| submitted / orchestrating / orchestrated | 保留当前图前执行事实检查，执行事实矛盾时不自动重新启动图 |
| runtime_submitted，图未完成或存在待审批 | 读取原 checkpoint/interrupt/ToolJob，走对应运行中或审批恢复协议；不能当成完成投影 |
| 图已完成，原应用结果资产已保存，部分业务投影未提交 | 校验绑定、原消息/工具配对、模型结算、结果摘要与所有权；按原结果只补缺失投影 |
| 完整业务提交已有凭证，阶段 ACK 未保存 | 核对原提交引用，只推进阶段；模型、工具、消息和事件均不重建 |
| 模型发送/工具副作用未知，或原结果/证明缺失 | 进入明确对账状态；不从展示文本猜结果、不重新 invoke 图 |

实施边界：

1. Runtime 导出可序列化的原应用结果资产，足以重建 RuntimeTurnResult 的业务投影；checkpoint 不是这份资产的替代物。资产绑定 Turn、运行时/格式、原调用结果和内容摘要。
2. 复用当前 Session 收尾逻辑为唯一投影所有者。支持正常执行和原结果恢复两个入口，不复制一份审批、消息、验证或 Memory 写入规则。
3. 将数据库内的 Session/消息/事件/结果引用尽可能纳入现有 fenced 事务；无法同事务的 Memory/外部投影使用稳定操作身份、分项完成事实和补偿。**提交凭证只能证明其明确列出的提交范围**，不能用 snapshot.saved 代称“全部完成”。
4. waiting_approval 是合法业务投影，不是最终完成。它继续保留原 pending_turn 与审批身份；拒绝、取消、超时和完成分别判定。
5. 后继保持原 user_message_id、Turn、根预算和任务谱系。旧 task/attempt 不重置，正式 RunItem 不重领；回归仍建立新运行。

开发先补正式红测：把现有 `test_runtime_submitted_with_committed_projection_only_advances_stage` 扩展为真实 saver/图及完整应用提交；再分别在快照后、消息提交前、提交后 ACK 前强杀。断言缺失投影补齐、已有投影不重复、模型/工具计数不增加。单纯放宽 guard 而不补提交证明不是根因修复。

### 12.2 跨 Turn transcript 的所有权、覆盖锚点和上下文导入

当前未提交草稿新增 `FrameworkTranscriptAsset`、`append_turn_input`、Runtime 初始读取和 Session metadata 写回。它证明开始接线，**不证明跨 Turn 已闭环**。本次只审阅，不修改或提交草稿。

| 草稿的真实边界 | 必须补入的契约 |
| --- | --- |
| 读取旧原生序列后只追加当前输入；adapter 优先选择该序列，忽略文本 runtime_messages | 资产必须记录所覆盖的稳定业务消息锚点/序列。保留原生前缀，追加锚点之后的合法业务消息，再加入当前用户输入；不能丢失中间 legacy Turn |
| 资产没有来源业务消息映射 | 保存 source_message_id/turn_id 与覆盖区间；当前用户已入库时引用原身份，内部无已落消息入口才建立确定性输入身份，不能按文本相等去重 |
| 非空 framework_messages 即可发布，未限定完成态 | 跨 Turn 可导入资产只发布已完成且协议闭合的消息范围。未决工具和审批停留于本 Turn checkpoint；若需保留部分完成历史，必须明确完整边界而非任意切片 |
| 新 metadata key 尚未加入服务端保护/内部隐藏集合 | 原生资产由服务端生成；Create/Update 拒绝写入，SessionDetail 默认不展示，快照/导出也检查权限。SHA256 只能检测内容一致性，不能证明来源可信 |
| 只有 runtime_version，没有模型/协议身份 | 绑定 provider、transport、模型语义配置和 serializer/格式版本。兼容性未经验证时明确不支持，不能悄悄丢签名或改写成文本继续 |
| 独立原生前缀绕开旧 context_summary | 摘要必须有稳定消息覆盖范围，与资产锚点对齐；同一段消息只有一个摘要所有者，禁止重复摘要、重复导入或拆散工具调用/结果 |

对应依据：[transcript 序列化与追加](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/deep_agents/framework_transcript.py)、[草稿资产格式](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/framework_transcript.py)、[Runtime 初始组装](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/runtime/runtime_service.py)、[Session metadata 边界](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/sessions/session_service.py)、[既有上下文资格规则](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/context/transcript_hygiene_service.py)。schema 文件为当前未跟踪草稿，不是已提交公共契约。

建议将资产发布、覆盖锚点及导入放在一个应用 transcript 服务内，Runtime 只请求上下文投影，Session 只调用发布端口；不要在二者继续散落 provider/去重/摘要特判。无原生资产的历史维持旧文本管道，不伪造历史 provider 消息。读取不可用资产应返回明确的不兼容/需迁移状态，不静默降级导致上下文丢失。

新 Turn 的目标组装顺序：

```text
原生资产及其覆盖锚点
  + 锚点之后、截至当前已受理输入的 context_eligible 业务消息
  + 当前输入的稳定身份（只出现一次）
  → 按完整工具配对/摘要边界选择上下文
  → 当前冻结 Prompt / Skills / 项目及检索资产
  → FrameworkGraphInputAsset
  → 官方 Harness / 共享模型调用策略 / 原生 API
```

同 Turn resume 不走这条导入链，仍从 checkpoint 读取，提交一个指定决定或经证据允许的 None 继续。新 Turn 不导入尚在排队、`context_eligible=False` 或将来的用户输入。不能为了跨 Turn 省事把 thread_id 改为 session_id，否则改变审批、预算与绑定隔离语义。

必过 Harness：完成框架 Turn → 持久落库 → 重建服务 → legacy 中间 Turn → 新框架 Turn；检查实际模型输入包含中间消息、当前用户恰好一次、原生块及工具配对完整。另测等待审批不发布、伪造 metadata 被拒绝、摘要截断边界、跨供应商不兼容显式报错及并发后续消息不被覆盖。供应商真实签名恢复必须单独验收，合成签名往返不算通过。

### 12.3 审批、所有权、模型治理的剩余边界

- **逐项审批**：继续沿用当前 v2 原子受理和串行恢复，不退回整批 decisions。人的决定、框架交付/应用、ToolJob 三组事实分开；已批准但最新权限失效可以被治理拒绝，不能伪造执行成功。官方也提示多个回答混发时可能出现部分工具已执行而其它答案失败，因此本系统坚持逐项交付并按原生事实对账。[官方 per-call 与中间件顺序](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- **执行所有权**：保持 owner 行锁与 saver 原生 checkpoint/pending writes 同连接短事务。领域 Runner 的 execution_id、代次、资源租约、远端查询/停止回执和未知效果对账另行接通；协程 cancel、ToolJob fence 和图 checkpoint 均不能证明外部进程已停止。
- **模型策略**：不重新建设一套模型入口。复用已提交主代理/具名子代理/摘要/图外分类的调用账本，补图内原结算导入、每次物理 HTTP attempt、错误/流式 usage 和真实配置组合。当前 `max_tokens` 新工厂映射存在，不能把旧入口的拒绝当成新入口仍缺失。
- **活动时间**：区分整体截止时间、根 Turn 实际活动时间和模型耗时。活动片段按稳定 owner/区间持久化，排队/审批等待单独统计，接管不重新给予完整预算；并发子分支调用/Token 累计到根，墙钟活动区间不能直接把重叠分支耗时相加。崩溃尾段的计量策略需明确并有测试，不能按恢复时刻猜零。
- **流式与取消**：当前仍有结束后整段回调；子代理事件流不等于 Token 实时流。补事件关联、Token/usage 汇总和客户端断线重连投影；客户端断线不自行撤销任务。显式取消、超时、暂停和崩溃分别定义准入，取消的任务不被自动后继重启。

### 12.4 版本化部署与各模式发布门

当前独立 v2 环境的九项版本已经实际导入核对，不需要重做依赖升级。保留双制品/独立队列方案，并补以下发布约束：

1. Worker claim 前声明并校验实际可读写的 binding/command/stage/model-call/receipt/transcript 格式；当前能力检查只覆盖部分字段/operation，不等于已经支持全部格式。旧严格模型遇到新 JSON 字段也可能拒绝。
2. 不可变发布物保存代码、实际安装清单、Prompt/Skill/工具 schema/Memory 检索资产或可读取不可变引用。锁摘要、源码指纹不替代制品本身，凭据和实时授权不冻结为旧权限。
3. 真实演练旧等待审批回合继续、新旧任务不串领、回滚只影响新 Turn、旧绑定仍有兼容 Worker。checkpoint、业务库及对象资产按一致的恢复点和保留策略备份，禁止独立回滚其中一份。
4. 公共契约先通过，再用代码审查做首个真实模式闭环，随后逐模式推进第 8 节 Harness。正式 AgentCase 的回边迁入图后，再移除新路径外层循环；不要先删历史回合需要的代码。
5. 模式 Harness 必须同时有环境/资源、冻结输入、工具轨迹、状态断言、证据裁决和清理。存在 harness_key、Prompt 或 Deep Agents 运行循环都不足以代表模式可发布。占位模式保持占位，代码审查结论也不能代替领域测试通过。

这是架构缺口的收敛顺序，不授权扩大到占位功能开发、测试引擎重写或外部 OpenAI 兼容网关。

## 13. 本次续核证据与实际验证

### 13.1 查证范围

重新读取附件六项评审、现有 v2/v3/实施记录、审批受理/恢复、owner saver、适配器/模型策略、消息管道、Session 收尾、能力路由及相关测试；另读本地《Agent轨迹与协议鲁棒性》《评测工程化与CICD》关于工具轨迹、权限/幂等和版本化质量门的段落。课程示例阈值不是本项目默认验收阈值，正式基线应另行冻结。

在线核验：[LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Customization](https://docs.langchain.com/oss/python/deepagents/customization)。在线页面可能包含更新 API；实际行为仍以本地固定版本和运行测试为准，不照抄新版示例升级依赖。同步持久化不能使业务表、checkpoint 和外部副作用天然成为一个事务。

实际导入独立解释器并检查 HumanInTheLoopMiddleware 签名：deepagents 0.7.13、langchain 1.4.6、langchain-core 1.6.10、langgraph 1.2.12、langchain-openai 1.6.2、langchain-anthropic 1.7.1、langchain-google-genai 4.4.0、langgraph-checkpoint-postgres 3.1.2、psycopg-pool 3.3.1；签名包含默认 batched 的 interrupt_mode 参数。本次未安装任何依赖。

### 13.2 本次公共契约回归

在 Agent_Server 使用独立解释器实际执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
& ../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_dependencies.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_approval_submission.py tests/test_framework_chat_models.py tests/test_framework_graph_input_asset.py -q -x -rs --tb=short
```

结果 **109 passed，161.35 秒，无 skip**。覆盖固定依赖、checkpoint/pending writes fence、原生逐项 interrupt、审批受理、官方模型配置/协议的合成 HTTP 验证及图输入资产。使用真实 PostgreSQL/Redis、固定官方依赖；模型为受控替身或官方 SDK 加合成 HTTP，不代表真实供应商验收。运行时含开始时已有的未提交草稿，不能标记为纯 HEAD 制品的发布测试。

首次命令误引用不存在的 `tests/test_framework_message_projection.py`，pytest 报 file not found、没有运行测试；核对实际文件后执行上面命令。该失败不计入通过数量。

另实际执行并发审批与独立 Worker 强杀回归：

```powershell
& ../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py::test_separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling tests/test_framework_approval_http.py -k 'separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling or http_accepts_sibling_decision_while_session_is_executing' -q -x -rs --tb=short
```

结果 **4 passed、10 deselected，80.93 秒，无 skip**。三个强杀参数验证已知工具/native 结果后的接管，以及对账提交前/提交后 ACK 前窗口；另一个用例验证同 Turn 正在执行时仍可受理兄弟审批。它们是既有受控工具/真实进程与数据库 Harness，不是浏览器或真实供应商验收，也不覆盖 §12.1 新发现的 message 完成投影窗口。

### 13.3 新增限制的临时运行探针

在系统临时目录创建 review probe，不写入仓库实现或正式测试。复用现有 admission/Session Harness，步骤为：受理并推进 runtime_submitted → 以捕获 owner 的官方 saver 完成单节点 LangGraph，确认 next 为空 → 保存匹配绑定的 completed 业务快照 → 失租 → 请求 message recovery。

结果 **1 passed，7.14 秒**，断言当前后继为 None，日志原因正是 §12.1 的 pre-runtime guard。这里的“通过”表示成功复现限制，**不是恢复功能通过**。图使用官方 LangGraph/真实 PostgreSQL，但业务快照仍为受控精简输入；未证明真实 Deep Agents 全部业务收尾已经完成。正式修复前需要把该场景纳入仓库红测，并补完整 Session 投影及强杀窗口。

该探针和静态读取共同支持 F10/F11 的方案补充，不据此声称发生过生产数据丢失。未提交 transcript 的其余风险来自代码路径审阅，未执行其跨 Turn 真实供应商端到端验收。

### 13.4 交付边界

本次仅更新本文件，保留原有五个实现草稿原状且不纳入提交。仍未执行全量测试、前端、独立 lint/typecheck、官方 saver conformance、真实模型/对象存储/浏览器/压测/扫描 Runner、双制品部署、跨 Turn 供应商签名验收。默认 Deep Agents/checkpoint 开关保持关闭；方案补全和局部测试通过不构成生产切流许可。

## 14. 再次核验：已实现的边界、工作区失败与最终实施门

本节响应用户再次提供的评审附件与接续摘要。前文的历史测试仍保留；本节只记录这次实际观察，不把历史通过数累计为新的覆盖数。开发 goal 尚未完成；本次按用户要求只做方案核验，不继续修复工作区实现。

### 14.1 六项意见的最终判定

附件的六项**架构风险均成立，但它们描述的早期代码现状不能直接套用到现在的 v2**：

1. **审批桥接**：legacy 仍是批量语义；v2 的 `FrameworkApprovalSubmission` 已按有效 Turn 受理，允许 Session 为 running/waiting_approval。adapter 已替换为单个 per_call HITL，恢复发送按 interrupt ID 的决定。后续工作是补齐故障与部署验收，不是再造一套审批服务。
2. **一致性**：审批受理/任务/outbox、resume journal/pending writes 分别已有局部原子边界；它们与工具外部效果、Session 收尾仍非整体事务。第 12.1 节的原结果资产和部分投影补偿仍是必需出口。
3. **执行权**：v2 `FencedPostgresSaver` 已捕获不可变 owner，并在同连接事务中验证租约、写 checkpoint/pending writes。旧 `ThreadedPostgresSaver` 不代表 v2。独立 Runner 的远端执行权和停止回执仍不能由 saver 推导。
4. **模型治理**：当前 `FrameworkModelCallMiddleware`、摘要模型包装和 DTO 策略已有显式接线。仍需真实协议配置、物理请求 attempt、图内结算读取及活动时间验收；模型工厂构造成功不是这些能力通过的证明。
5. **消息与状态**：官方序列化和图输入资产已存在，主图仍 `compile()`、未绑定 checkpointer；跨 Turn 草稿不足以证明完整应用链路。继续执行第 12.2 节锚点、权限、供应商兼容与摘要所有权契约。
6. **版本部署**：独立环境、v2 stream 和 Worker binding 能力校验已存在；尚未覆盖所有 stage/model-call/receipt/transcript 格式。仅保存 runtime_version 不构成旧制品环境，双制品演练仍待验收。

本次实际导入的九项版本仍与初版锁定组合一致，`HumanInTheLoopMiddleware` 签名确含 `interrupt_mode`，默认值仍为 batched，项目必须显式选择 per_call。在线文档确认 per_call 要求 langchain>=1.4.6；Deep Agents 的审批依赖 LangGraph interrupt，**不是替代平台审批、租约和授权的第二套控制平面**。[LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)

同 thread 恢复必须复用 thread_id，节点会从头重入；checkpoint 保存图内游标，store 保存跨 thread 数据。它们不会自动完成本项目跨 Turn transcript 导入、业务事务或外部幂等。[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

### 14.2 当前提交凭证草稿：方向正确，但不能计为通过

工作区新增 `FrameworkProjectionCommit` / `framework_application_projection.py`，在 `save_session_with_events` 的同一事务中保存凭证，意图闭合“业务已提交、阶段 ACK 丢失”的窗口。它仅声明 `session_messages_completion_events`，不包含 Memory、清理或外部效果；这是正确的范围限制。**阶段 Schema 6 尚未提交，不能按已发布协议使用。**

本次接续取得真实回归结果：

```powershell
# 工作目录 Agent_Server；真实 PostgreSQL/Redis
$env:RUN_LIVE_POSTGRES_TESTS='1'
& ../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_application_projection.py tests/test_framework_message_stages.py tests/test_framework_message_recovery.py -q -x -rs --tb=short
```

结果 **1 failed、37 passed，130.74 秒**。失败为 `test_independent_worker_kill_before_orchestration_recovers_through_official_graph_once`，新凭证 `_model_hash` 抛出：

```text
FrameworkMessageStageRecoveryRequired: Application projection has unsettled model facts
```

依据不是“官方恢复失效”，而是**新增提交前置条件与现有模型证据能力契约错位**：该 Harness 的官方 Deep Agents/SDK 真实请求通过 `FrameworkModelCallPolicy` 结算，但没有注入可选 evidence 服务；当前策略允许 completed 且 settled_response_id 为空。新凭证把“所有 completed 必须有可读原响应”变为正常 Session 最终事务的必需条件，因此使原可完成路径回滚。失败发生在本地合成供应商，并非真实付费端点。

应采用的根因方案，而非删除检查或修改 fixture 掩盖失败：

- **先冻结能力契约，再决定准入**。需要自动恢复的新版绑定，在首次模型发送前检查证据服务及对象资产能力，确保每个请求/唯一结算响应均可持久读取。没有这种能力的历史记录不补造证明。
- **区分业务正常提交与安全自动恢复资格**。对已有允许无证据结算的绑定，明确保留旧兼容行为及“不具备自动恢复资格”的原因，或经版本化迁移拒绝新任务准入；不能在模型/工具已执行后才把未声明的新条件加入最终提交。
- **恢复要求不放宽**。invoking/unknown、缺少唯一响应或缺原结果资产仍进入对账；不能因为需要兼容正常完成路径，就允许缺证据的历史图自动重跑。
- **测试使用两类配置**：无 evidence 的历史兼容模式，以及有持久 evidence 的新恢复模式；后者补真实对象存储及完整图内模型/工具链，不能用零模型单节点图替代。

另已复现序列化缺陷：`_tool_hash` 把 `cur.fetchall()` 的 RowMapping 直接传入 `_hash`，而 `make_json_safe` 只转换 dict/list 等，不转换 RowMapping。使用 SQLAlchemy 内存 SQLite 查询得到真实 RowMapping 后，`_hash([row])` 报 `TypeError: Object of type RowMapping is not JSON serializable`；同一行转换 dict 后可计算摘要。此探针无目标系统请求、不写仓库文件，证明的是序列化缺陷，**还不是完整真实 ToolJob 恢复测试**。后续应在统一行规范化边界修复，并添加非空真实 ToolJob 样本。

静态核对还确认 `VerificationResult` 确有 turn_id 字段，不能把此处标成“字段可能不存在”。仍须补：空输出、取消/拒绝/失败工具、事件/消息角色关联、摘要漂移、提交事务回滚、二次失租与 claim 后再检查。ToolJob 的 cancelled 状态不是 completed，更不是“外部效果确定未发生”；其恢复资格必须依靠事实，不扩大终态白名单了事。

### 14.3 补全后的交付切分与不可越过的门

保留“Deep Agents 认知 Harness + 受控 LangGraph + 平台业务控制平面”的选型。不是全系统一律套 Deep Agents，也不是把每个 Runner 再包成一个图。接下来按以下可独立验证的交付推进：

| 交付 | 复用依据与做法 | 必过验收；未过不得宣称完成 |
| --- | --- | --- |
| A1：能力契约与原子提交凭证 | 复用模型账本/证据服务、Session 事件事务；明确凭证作用域和格式能力 | 本节失败回归转绿；有模型及非空 ToolJob；无凭证裸快照不恢复；旧 owner 不能提交；事务失败整体回滚 |
| A2：原结果资产与部分投影补偿 | 复用唯一 Session 收尾规则；保存可导入 RuntimeTurnResult，不从最后 assistant 或最新快照猜结果 | 图完成后、快照后、最终提交前、提交后 ACK 前分别强杀；缺失投影补齐，已有消息/验证/事件不重复，HTTP/工具次数不增加 |
| A3：完整收尾与未知副作用 | 用稳定操作身份记录 Memory/清理/领域 Runner 的分项事实，复用 ToolJob 对账 | 明确哪些项目影响业务完成、哪些只影响资源回收；未知效果不重派，取消不自动重启，根预算与原终态不改写 |
| B：统一上下文与跨 Turn | 复用 PromptAssembly、SkillRegistry、Memory 检索、完整 transcript 和覆盖锚点 | 完成框架 Turn→重建服务→legacy 中间 Turn→新框架 Turn；实际请求不漏历史、不重复输入，待审批不发布，私有资产不可由 API 写入 |
| C：运行完整性与代码审查首个闭环 | 复用既有 per_call、saver fence、模型策略；补活动片段和真实流式/取消；接项目已有 Prompt | 两待审工具，任意先批一项→执行→强杀→新 Worker 处理另一项；工具不重执、旧 owner 写入拒绝、工具消息配对、共享预算；代码审查正式评测集通过 |
| D：受控 AgentCase 与逐模式 Harness | 回边移入 LangGraph，领域 Runner/Verification 保持裁决权；沿第 8 节每模式独立迁移 | 用例固定版本、原子领取、证据与断言、终态不可重领；真实对应 Runner 验收后才开放该模式；六占位模式仍不实现 |
| E：兼容部署与收敛 | 独立制品/队列，claim 前校验全部持久格式能力；先兼容读者后新写者 | 真实旧等待回合恢复、不串领、回滚演练及备份恢复通过；再移除不再被活跃绑定需要的旧循环/摘要/审批代码 |

A1 不代表 A2/A3；审批强杀通过不代表首次 message 完成投影通过；公共 Harness 通过不代表每个领域 Runner 通过。每项独立红测、实现、回归和提交，失败保持可见，不通过增加并行运行时或散落 try/except 解决。

### 14.4 本次额外回归与交付范围

本次重新运行第 13.2 节的两条命令（均显式设置 RUN_LIVE_POSTGRES_TESTS=1），实际结果分别为：

- 公共契约六文件：**109 passed，193.16 秒，无 skip**。
- 并发审批/独立 Worker 强杀：**4 passed、10 deselected，97.00 秒，无 skip**。

它们不覆盖或消除第 14.2 节的失败。数据库/队列为真实 PostgreSQL/Redis，模型为替身或官方 SDK 加本地合成 HTTP，工具为受控测试工具；不是付费供应商或真实领域 Runner 验收。所有测试针对当前未提交工作区，不标记为纯 HEAD 或可发布制品验证。另已实际导入九项固定依赖、检查 HITL 签名及运行 RowMapping 序列化探针，未安装依赖。

本轮仅更新该方案；未修改、暂存或提交已有运行时代码/测试草稿，未安装或升级依赖。全量、独立 lint/typecheck、前端、官方 saver conformance、真实付费供应商/对象存储/领域 Runner、跨 Turn 实际签名及双制品部署未执行。Deep Agents 默认开关保持关闭，整体开发 goal 不标完成。
