# LangGraph 与 Deep Agents 全系统接入方案 v2

初稿日期：2026-10-25；最近复核：2026-10-26，本次实现从代码基线 `185225e01` 继续，记录至实施记录 §34。性质：经代码、官方源码和局部运行验证后的迁移设计。第 11–13 节保留此前研究与复核记录，第 1.3、7.3、14 节更新当前实施边界与剩余实施顺序；实际代码与依赖改动、通过结果和待验收项见[迁移实施记录](framework-integration-progress-2026-10-25.md)，不能将设计目标视为已经实现。第 14 节的阶段准入与调用位置已局部落实，安全后继等待办仍未实现。

## 1. 结论与本次修订

附件指出的六类缺口均有代码依据，应补进实施契约。继续采用已经选定的分层：**Deep Agents 提供认知 Harness，LangGraph 承担受控 Agent 工作流，平台拥有任务、审批、执行所有权、测试版本、证据与裁决。**

需要限定三个结论：

- 附件反映方案形成时的缺口，部分已有后续实现，见第 1.3 节；不能将历史缺口写成当前全部缺失，也不能据此声称生产环境已经发生过重复副作用或 checkpoint 覆盖。
- 官方逐项审批、真实 PostgreSQL checkpoint 写入隔离、决定 receipt 与原生 pending writes 同事务已有验证；v2 业务审批/API/续跑调度已在 fake model Harness 接通。已实现已知结果审批的对账/后继及对账自身失租后继；独立进程验证工具/native 结果保存后、业务 applied 前，以及对账提交前/提交后 ACK 前强杀。首次 message、未知副作用等完整恢复出口仍待实施验收。
- “决定已应用 → 工具结算”不能作为统一线性状态机：拒绝不会执行工具，批准后也可能被最新权限或预算阻止；决定交付、框架消费和工具结果应分别记录。

本方案替代上一版接入方案中关于审批恢复、所有权、消息持久化和版本部署的简略约定。系统现状与各模式已有 Harness 的完整调查见[架构报告](system-architecture-and-harness-review-2026-10-25.md)。

### 1.1 证据到问题的对应关系

以下保留 **2026-10-25 方案形成时** 的证据快照；路径相对仓库根目录，行号不代表最新代码位置。F 表示问题，E 表示依据，后续 P 表示实施阶段。最新判断以第 1.3 节及实施记录为准。

| 问题 | 已核实的依据 | 判定与落实位置 |
| --- | --- | --- |
| F1 逐项审批链路冲突 | E1：[审批事务](../Agent_Server/src/runtime/postgres_agent_task_store.py) `_submit_approval_sync`，约 567–717 行，仅接受 `waiting_approval`；[运行时](../Agent_Server/src/application/runtime/runtime_service.py) 1318/1361 行仍解析批量 `action_requests`、等待整批决定 | 成立；第 4 节、P2 同步改提交、排队、领取、恢复、事件和 UI |
| F2 checkpoint 与业务状态存在提交窗口 | E2：[checkpoint provider](../Agent_Server/src/application/deep_agents/checkpoint_provider.py) 使用独立池与 `autocommit=True`；审批/任务/outbox 有自己的事务；ToolJob 和外部请求又有各自边界 | 成立；第 5 节规定恢复协议，不承诺外部 exactly-once |
| F3 所有权保护没有覆盖 checkpoint | E3：[ThreadedPostgresSaver](../Agent_Server/src/application/deep_agents/threaded_postgres_saver.py) 直接 `to_thread` 调官方 saver，无业务 token/epoch 校验 | 成立；第 6 节同时保护 checkpoint 与 pending writes |
| F4 返回官方模型对象会绕过原调用策略 | E4：[ModelRuntimeService](../Agent_Server/src/application/models/model_runtime_service.py) 97 行返回模型，后续不经 `invoke`；[适配器](../Agent_Server/src/application/model_adapters/langchain_model_adapter.py) 174 行拒绝显式 `max_tokens` | 成立；第 7 节将策略接到真实调用，而非只改工厂 |
| F5 完整消息没有覆盖跨 Turn | E5：[消息规范化](../Agent_Server/src/application/deep_agents/runtime_adapter.py) 514 行丢弃部分元数据；[TranscriptHygieneService](../Agent_Server/src/application/context/transcript_hygiene_service.py) 以摘要和最近文本组装；[主图状态](../Agent_Server/src/graph/state.py) 含 `_event_queue` | 成立；第 3 节区分原始 transcript、上下文投影和 checkpoint |
| F6 运行时版本号不提供旧运行环境 | E6：[命令 schema](../Agent_Server/src/schemas/agent_task.py) 固定 `schema_version=1` 且禁止额外字段；[Dispatcher](../Agent_Server/src/application/orchestration/agent_task_dispatcher.py) 无框架版本能力路由；当前默认单任务流 | 成立；第 8 节选择双环境、双队列部署 |
| F7 失租后的恢复入口尚未打通 | E7：[任务领取](../Agent_Server/src/runtime/postgres_agent_task_store.py) `_claim_sync` 986 行起，过期 `running` 标为终态 `recovery_required`，不会直接重放 | 补充发现；第 5、8 节增加对账与后继任务 |
| F8 流式与任意中断恢复尚未实现 | E8：[运行时](../Agent_Server/src/application/runtime/runtime_service.py) 441 行结束后一次回调；464 行起取消/超时明确 `resumable=False` | 附件判断成立；第 5、7 节分别设计 |
| F9 ToolJob 写入也不能笼统视为已被租约保护 | E9：[ToolJob Store](../Agent_Server/src/runtime/postgres_tool_job_store.py) 的领取有 CAS，63/84/119 行附近输出与结算按状态或内容比较，未统一校验执行 owner | 补充发现；保留已有 CAS，第 6 节补 v2 执行所有权，不能只修 saver |

### 1.2 官方依据及采用边界

| 官方依据 | 本方案采用方式 |
| --- | --- |
| [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) | 使用 `per_call` 和 interrupt ID；平台继续管理审批主体、权限与审计 |
| [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) | 使用官方 checkpoint/pending writes；选择 `durability="sync"`；应用负责业务一致性 |
| [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | thread 内恢复与跨 thread memory 分工；不把 memory store 当执行游标 |
| [Deep Agents Customization](https://docs.langchain.com/oss/python/deepagents/customization) | 通过中间件和显式子代理接入；核对最终中间件栈，不假定主代理配置自动覆盖每个子代理 |
| [Custom Middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom) | 通过模型/工具 wrap hook 接入策略；摘要的独立模型调用单独接入 |
| [PostgreSQL 行锁](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS) | 用同一数据库事务内的 owner 行锁和 epoch 检查构造应用侧写入隔离 |
| [Python Future.cancel](https://docs.python.org/3.11/library/concurrent.futures.html#concurrent.futures.Future.cancel) | 不以协程取消推断正在运行的同步线程已经停止 |

额外核验了临时目录中实际下载的 `deepagents==0.7.13`、`langchain==1.4.6`、`langgraph==1.2.12`、`langgraph-checkpoint-postgres==3.1.2` 源码，重点为 `deepagents/graph.py`、`human_in_the_loop.py`、`langgraph/types.py`、`pregel/_loop.py` 和 `checkpoint/postgres/__init__.py`。网页可能更新，实施验收必须同时锁定包版本和制品摘要。

### 1.3 附件六项意见的当前判定（2026-10-26）

| 意见 | 当前代码事实 | 剩余实施边界 |
| --- | --- | --- |
| F1 逐项审批桥接必须整体改 | 官方 per-call、原子受理、调用身份投影、指定决定/None 继续及原生结果 proof 已接通 Runtime/Session、Dispatcher、HTTP/outbox/v2 Worker；支持 `message`、`approval_resume`、`framework_reconcile`、`approval_recover`。持久进度按最新原始/后继审批任务投影，见实施记录 §12–23 | 已知结果强杀窗口已验证 B 不重执行、另一 Worker 应用 A 的拒绝决定并闭合 Turn；其它故障窗口与真实浏览器/供应商仍待验收。批量旧路径和专用领域审批继续保留 |
| F2 checkpoint/业务/副作用不在一个事务 | owner、官方 pending writes 与 delivery proof 共事务；决定/receipt/任务/outbox 共事务；原生结果与 applied proof 共业务投影事务。已知结果对账在单个业务事务中保存 proof、后继/outbox 和对账完成状态；对账运行失租可建立新对账，提交前/后强杀已验证，见 §24 | 多个事务及外部效果之间仍有窗口；未知副作用阻止自动后继，确定性拒绝/返回数据库错误不自动重试；不承诺外部 exactly-once |
| F3 checkpoint 缺失租约隔离 | v2 owner-bound saver 已保护 checkpoint/pending writes；现有中断事务即时撤销 owner；同步 ToolJob 的领取、派发准入、输出、结算及附件写入校验捕获 owner，迟到响应保存非权威观察；已知结果审批的独立 Worker 接管见实施记录 §21–23 | 无绑定旧试点仍用原 saver。同步工具的校验不能撤销已准入外部请求；领域 Runner 独立执行权及其余跨进程窗口仍未完成 |
| F4 模型对象不会自动继承调用策略 | 四传输官方模型、主/具名子代理/摘要及 owner 作用域内 DTO 请求共用根模型策略；受理预算、Worker 检查与前置语义分类已接线。应用层请求/响应资产、迟到观察及 OpenAI Chat/Responses、Gemini 图内 wire 注入已有局部验证；§34 补入稳定模型位置、意图先于资产和输入漂移拒绝，见 §7.3/14.8 | 自动生成绑定/预算及版本实物、活动时间、完整动态参数/错误/显式重试、Anthropic 图内 wire、多次 HTTP attempt 独立证据、真实对象存储/供应商仍待验收。资产保存不证明首次 message 可重放；无 owner 的调用不自动纳入根预算 |
| F5 完整消息必须覆盖跨 Turn | `framework_transcript.py` 已使用官方消息序列化，Runtime 保存 `framework_messages`；事件资源已移出图状态 | 完整的跨 Turn 导入、摘要覆盖范围、版本实物校验及供应商真实签名恢复仍未验收；序列化函数通过不等于上下文管道完成 |
| F6 版本识别不等于旧环境存在 | 已有 v2 schema、独立 stream/CLI、绑定复核与 lock 摘要能力校验，另有 legacy manifest | 真实双制品部署、存量审批继续、排空与回滚未验收；lock 文件哈希不证明运行环境已安装正确依赖 |

附件末尾“本地未安装 Deep Agents”、仅完成 dry-run 的描述属于旧快照。当前独立 `.venv-framework-v2` 已安装锁定依赖，已有真实数据库局部测试。该进展仍不足以开放生产 v2 路由。

#### 实现过程记录（实施记录 §12–17 时点）

以下保留这些阶段的机制与当时待接线项；后续 Runtime/Session/Worker、API/UI 进展以本节当前判定表及实施记录 §18–24 为准。

本次复核发现的 `sync` 缺口已在 adapter 层落实：所有带 checkpointer 的启动、指定决定恢复、`continue_from_checkpoint=True` 的 `ainvoke(None)` 和子代理事件入口显式使用 `durability="sync"`。无持久化旧试点不传 durability；固定 LangGraph 1.2.12 对无 saver 的 sync 调用会访问不存在的 `_put_checkpoint_fut`，已由真实框架回归复现。无新决定继续仅开放内部 adapter 协议，仍需 Session/Worker 根据交付凭证和 ToolJob 对账选择，不能对未判定的执行直接继续。`sync` 不保证外部副作用 exactly-once，也不保证工具开始前 resume receipt 已落库。

原生身份提取采用 `agent.aget_state(..., subgraphs=True)` 还原增量消息，递归已注册子图，并在 owner 事务下读取同 checkpoint/task 的 interrupt、resume 与真实 `task_path`。源调用仅按当前原生 AI 消息的显式 call ID 匹配，并核对工具名和参数；不搜索历史同名调用。固定版本的 StateSnapshot 会包含已完成兄弟任务的旧 interrupt，需由同任务匹配的原生 ToolMessage 及 resume 事实排除；只存在已消费 resume、没有匹配任务结果时要求对账，不能创建下一审批。该 collector 限于官方 per-call `tools` 任务，不代替任意自定义图的审批映射。

事件流同时订阅 `updates` 与 `values`：前者采集 root interrupt，后者保留完整最终状态；不能仅用 `on_chain_end.output` 推断没有审批，因为固定版本的该事件输出不包含 interrupt。实际 nested Deep Agents 及 root 事件流回归见实施记录 §13。

新增业务投影见实施记录 §14：准备阶段不写数据库，投影阶段在同一 owner 事务内创建/复用等待 ToolJob、Approval、native link 和稳定创建事件。内部调用键包含 binding、Turn、图 namespace、源 AI 消息和供应商 call ID；界面仍保留原始 call ID。重投影和 Session 收尾保留已提交决定与终态结果；原生 link 的可选 `business_call_id` 存于既有 JSONB，防止移除原生元数据后降级为 legacy 校验。

工具桥的后续接线见实施记录 §15：使用官方 ToolRuntime.state 与 execution_info，保留子图祖先 namespace，只移除当前 tools 任务分量；HITL 门控身份在图执行前异步从持久化 link 还原，业务执行时重新读取当前决定。当前治理阻止时，在 owner 事务内保留终态 ToolJob，只结算未启动的 waiting_approval，running 要求对账。原生 reject 会跳过业务 callback，其 ToolJob 拒绝结果和 applied 投影仍须由 continuation 完成；真实局部治理恢复不等于 Worker/API 接线通过，不能据此开放 v2 approval continuation。

续跑准备的独立端口见实施记录 §16：`FrameworkApprovalContinuation.prepare` 在有效 owner 事务内重新核对实际 running approval_resume 任务及其冻结决定、业务审批/link、原 ToolJob 和保留的原生 interrupt/resume。只有校验通过才将 queued 改为 sending；已消费决定修复 delivery 投影并选择 None，不能仅凭 delivered 字段跳过原生证据校验。未知已启动效果、原生事实缺失及决定/工具状态矛盾要求对账。准备阶段不调用图、模型或工具，不设置 applied_at。

结果投影端口见实施记录 §17：`settle` 复用同一事务内的准备校验，再读取原 checkpoint/task 的持久化 messages 输出；匹配一条 ToolMessage 的 call ID、工具名、task_path，确认没有任务 error。原生 reject 将尚未启动的等待 ToolJob 结算为 denied；已批准调用要求原生治理响应状态与终态 Job 一致。实际原生消息与 Job 输出的哈希、任务位置及决定哈希保存到既有 receipt.proof.application，和 applied_at、稳定 applied 事件同事务写入；重复投影验证原证据且保持时间不变，delivery 重放保留 application。这里的 applied 不表示领域测试通过或副作用成功。Runtime/Session/Worker 消费以上端口仍待后续接线。

## 2. 目标架构与模块边界

```text
API / Session / TestRun
       │
平台控制平面：正式项目与用例、任务/outbox、租约、审批、证据、结果
       │
不可变 RuntimeBinding → 按版本路由 → 对应 Worker 环境
       ├─ Deep Agents：探索、认知计划、工具选择、受限认知子代理
       ├─ LangGraph：正式 AgentCase 的显式步骤、循环与恢复
       └─ 领域 Runner：API、浏览器、兼容性、压测、安全执行
       │
共享模型调用策略 / 统一工具治理 / transcript / 事件投影
       │
业务 PostgreSQL + 官方 checkpoint schema + Redis 通知 + 证据存储
```

| 边界 | 拥有的事实与职责 | 交接契约 |
| --- | --- | --- |
| 平台控制平面 | 正式 `project_id`、用例/套件冻结版本、任务及租约、审批决定、结果 | 不让框架消息改写正式计划或把自然语言当通过证据 |
| Deep Agents Harness | 当前认知分支的模型工具循环、认知计划、上下文压缩、受限委派 | 工具通过治理端口；认知计划不能直接成为已批准的压测/安全计划 |
| 受控 LangGraph | AgentCase 的节点、条件边和预算终止 | 与 Deep Agents 是两种顶层运行时，不在模型节点里再启动完整自主循环 |
| 领域服务/Runner | 请求、浏览器租约与回放、加压、扫描、断言和领域证据 | 复用已有服务；持久运行提交正式任务，不交给临时认知子代理调度 |
| 工具治理 | 当前权限、安全/范围、审批匹配、ToolJob 去重、结果回放 | 框架批准只是一项输入，执行前仍要重新校验 |
| 存储与投影 | checkpoint 是执行游标；业务表是治理与测试事实；事件是可重建展示 | Redis 是投递通道，不是决定或执行进度的唯一来源 |

“一个 Turn 一个循环”修订为：**一个 Turn 只有一个顶层模型工具循环所有者**。显式子代理可以有受限子循环；它们共享根预算并有稳定身份。一个分支只有一个摘要所有者，禁止旧的外层循环和新框架循环、两套摘要同时管理同一段消息。

临时认知子代理返回有来源的发现，不拥有独立业务租约、正式测试运行或自动审批权限。已有 `CoordinatorRuntimeService` 和各模式 Coordinator 继续调度持久业务 Worker。

## 3. 运行时、每轮模型输入与消息契约

### 3.1 统一运行时接口及版本绑定

在现有 `RuntimeService` 边界引入启动、继续执行、提交指定审批决定、取消及检查进度接口，继续输出 `RuntimeTurnResult`。这是拟新增应用接口，不假称为框架原生接口。

Turn 首次受理时，和 AgentTask/outbox **同事务**保存 `RuntimeBinding`：

- `binding_id / session_id / turn_id / runtime_kind / runtime_version / state_schema_version`。
- `thread_id=turn_id`；checkpoint 数据库/schema 与顶层 namespace 配置；保留子图真实 namespace。
- 代码制品、依赖锁摘要、Prompt 版本、Skill 内容摘要、工具 schema/治理策略版本、模型语义配置摘要。
- 正式项目及可选的 `run_id / run_item_id / execution_attempt_id`。

上述是完整目标契约。当前 `RuntimeBinding` 已保存主要版本、模型指纹和 lock 摘要，但尚不包含这里列出的全部代码制品、checkpoint 数据库/schema、治理策略与领域引用。后续应以兼容新增字段或版本化 manifest 补齐并验证解析方；不得将“已有版本字符串”视为这些实物已经冻结。

同 Turn 恢复必须使用该绑定。鉴权凭据按引用解析，允许合规轮换；权限与安全策略执行时重新判断。模型、端点、协议、Prompt 或工具 schema 的语义变化不能静默续入原 Turn。

`pending_turn` 继续保存业务引用、控制原因及恢复资格；完整框架消息和执行位置由 checkpoint 负责。新运行时不能从 UI 文本或旧快照重新拼出一份模型历史再追加到 checkpoint。缺少绑定的历史记录固定走 legacy，不能按当前功能开关猜测。

### 3.2 每次模型请求的组装顺序

沿用[PromptAssemblyService](../Agent_Server/src/application/prompting/prompt_assembly_service.py)、项目上下文、Memory、SkillRegistry 和模式 Prompt 契约，替换目前独立试点 Prompt。新 Turn 的输入先冻结来源清单，再交给对应 Harness：

1. 解析正式项目、冻结用例/计划、用户输入及模型配置。
2. 取得系统/模式 Prompt、策略边界和 Skill 版本；标记外部内容来源及信任边界。
3. 从 transcript 构造预算内的历史投影；加入 Memory 检索与证据引用。
4. 为当前分支装配实际可用的工具 schema；子代理使用更窄的能力集合。
5. Harness 维护本分支消息及工具结果；每次模型调用前追加必要的当前上下文，并通过共享模型策略。
6. 官方模型适配器把完整框架消息转换为供应商原生请求；记录脱敏请求摘要、配置指纹、调用身份和结果。

恢复时执行第 5、6 步所需的动态校验，不重新运行新 Turn 的整段历史导入。Prompt 的平台部分与 Deep Agents 内建能力说明分别有唯一组装位置，并对最终实际输入做契约测试，避免系统指令重复。

### 3.3 三类消息数据与跨 Turn

| 数据 | 保存内容 | 用途 |
| --- | --- | --- |
| 原始 transcript | 带 serializer/schema/provider 版本的完整框架消息；id、tool_call_id、内容块、原生签名、usage、元数据、来源及 artifact 引用 | 审计、同供应商兼容历史装配；追加保存并按消息身份去重 |
| checkpoint | 图状态、内部消息/摘要事件、任务与 pending writes | 同 Turn 继续执行；不由展示消息重建 |
| UI 消息投影 | 展示文本、工具摘要、审批状态、证据入口 | 前端渲染和事件订阅，不作为原生消息档案 |

使用官方 `messages_to_dict/messages_from_dict` 作为框架消息序列化起点，补应用版本封套；不能继续把 `_normalize_messages` 或简化 `UnifiedMessage` 当无损档案。大对象走证据存储，引用必须可持久读取；不把运行期临时路径当跨进程工件。

本轮 synthetic fixture 已证明官方序列化可保留这些字段，而当前投影丢失 id、additional_kwargs、response_metadata、usage_metadata。此验证不等于真实供应商签名已经完成端到端恢复验收。[Gemini 官方集成](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai)单列了 thought signatures，后续必须用真实响应覆盖。

由于新 Turn 创建新 thread，必须有 `本 Turn transcript → 下一 Turn 上下文投影` 的显式管道：

- 只按完整工具批次保留/压缩：AI 工具调用和每个对应结果一起保留，不能只截取最近若干条文本。
- 未决 interrupt 不跨 Turn 搬迁；先完成、明确取消或进入需对账状态，新输入排队。
- 同供应商兼容时按版本验证后复用原生块；切换供应商/不兼容模型时，保留原档案，向新 Turn 提供兼容文本、证据与已闭合摘要。无法合法转换的必需状态返回明确错误。
- 摘要引用覆盖范围与原始消息身份，压缩失败保留原记录并报告原因。父分支和子分支分别压缩，根预算统一核算。

主图接入 checkpointer 前，把 `_event_queue`、callback、数据库连接、模型/工具服务和进程内控制对象移入运行时 context。状态只保存可序列化的业务值/引用；本轮实际验证 `asyncio.Queue` 不能被默认 JsonPlusSerializer 序列化。不得用 pickle 保存活连接和队列来绕过此边界。

## 4. 逐项审批的完整契约

### 4.1 唯一审批点与调用身份

对新 Deep Agents 路径显式安装官方 `HumanInTheLoopMiddleware`，采用 `interrupt_mode="per_call"`，只允许 `approve/reject`。官方此能力要求 `langchain>=1.4.6`；当前 v2 环境已锁定并安装 1.4.6，旧环境不能据此获得该能力。默认批量 HITL 通过工厂配置/同名中间件替换机制消除，并检查文件权限自动生成的 HITL 及每个子代理的最终栈。认知子代理默认仅有只读工具，无业务审批；未来增加业务工具时必须显式配置同等协议。

治理规则先决定是否需要审批；HITL 负责真正暂停；平台负责审批记录和授权。批准后的工具包装器验证已消费的决定并继续治理，不再次创建另一套普通审批。当前权限若变严导致批准范围失效，返回可解释的阻止结果；如需重新申请，建立有明确父引用的新审批身份，不能把旧批准解释成新授权。

稳定调用键：`(binding_id, turn_id, checkpoint_ns, source_message_id, tool_call_id)`，其中源 AI 消息身份随 checkpoint 保存；跨模型轮次复用同一供应商 call ID 时也不能误认旧调用。interrupt 映射另存 `interrupt_id / graph_task_id / task_path / checkpoint_id / 工具 schema 版本 / 规范化参数哈希 / 审批范围哈希 / tool_job_id`。平台 ToolJob 使用完整调用键派生的内部 call 身份，返回框架时保留原 tool_call_id。

工具名和参数相同仍可能是两次不同调用，不能靠 name+args 匹配。子代理 call ID 需 namespace 隔离。已持久化 interrupt 的重建保持同一个审批 ID；映射不一致时进入对账，不覆盖原决定。治理策略和参数冻结用于审计，当前权限仍实时检查。

**审批门控在同一次调用的恢复中保持稳定。** 官方 HITL 文档及固定 1.4.6 的 `human_in_the_loop.py` 明确：可选 `when` 在恢复时会再次求值；若从 true 变成 false，可能直接执行工具并忽略原拒绝。本项目不将实时权限、预算或 Session 状态变化作为跳过已有 interrupt 的条件。需要条件门控时，按稳定调用身份保存最初门控判定，恢复沿用；最新权限和预算在治理包装器里独立校验，只能允许当前授权操作或返回阻止结果。此项为迁移约束，尚未声明当前使用了动态 `when` 或已出现绕过。验收必须覆盖“拒绝后策略变化仍不执行”和“批准后权限收紧返回阻止”。[官方条件审批说明](https://docs.langchain.com/oss/python/langchain/human-in-the-loop#conditional-interrupts)

### 4.2 分离三组状态

| 维度 | 状态/记录 | 含义 |
| --- | --- | --- |
| 人的决定 | 沿用审批 `pending / approved / denied`，附 `decision_id、decided_at、actor、decision_hash` | 一次有效决定不可改写；取消/过期有效性用独立字段记录，避免擅改旧枚举 |
| 决定交付 | 新增 receipt：`queued / sending / delivered`，加 `applied_at / proof_ref / last_error` | delivered 表示决定随 `__resume__` 持久写入；applied 表示已观察到该决定产生的治理/工具结果，不等于副作用成功 |
| 工具执行 | 复用 ToolJob 状态与执行证据，另有对账标记 | 批准后可成功、失败、被阻止或结果未知；拒绝返回匹配的 ToolMessage，不执行工具 |

`sending` 只表示一次可失租的发送尝试，不作为“框架肯定收到”的证据。最终展示由三组状态合成：“决定已提交”“工具执行中”“仍有待审批项”“执行需核对”等。API 保留 202 和现有 task 回执，以新增可选字段表达详情。

当前 `_resume_deep_agent_after_approval` 在实际提交框架之前便发出 `approval.decision_applied` 事件；此历史事件不能作为 applied 证明。v2 在决定事务提交后投影“决定已记录”，在 receipt/proof 持久化后投影 delivered，观察到匹配的拒绝/治理阻止/工具结果后才写 applied；同步核对事件消费方，保留必要的旧事件兼容。

项目事实来源是 `schemas/session.py` 的 `ToolApprovalStatus`：业务拒绝存为 `denied`；交给官方 HITL 时映射为决定类型 `reject`。`rejected` 不是当前存储枚举，不能因框架术语修改旧状态值。

### 4.3 提交、排队和串行继续

1. 提交事务锁定 Session、运行绑定、审批，验证归属及活跃 Turn。审批有效且待决定时，即使另一个审批正在执行、Session 为 `running`，也能记录决定。
2. 同一审批同一决定重复提交返回原回执；不同 `client_request_id` 也不能创建第二次应用。相反决定返回冲突；原因文本修改不改变决定语义。已取消/换 Turn/无效范围不能继续提交。
3. 审批决定、唯一 continuation 意图、AgentTask 与 outbox 同事务提交，复用已有机制。Worker 不信任 Redis payload 中的状态，重新读取数据库。
4. 同 thread 同时只能有一个有权写入的执行者。其它决定可排队，不能并发 `invoke/ainvoke` 同一 thread。
5. 领取时先处理当前活跃 Turn 的继续/对账任务；后续普通新消息仍按其顺序等待。必须修改当前全 Session FIFO 的阻塞条件，防止排在前面的新消息堵住当前 Turn 的审批续跑。
6. 每次只提交一个有效 interrupt 的决定：`Command(resume={interrupt_id: {"type": "approve"}})`；拒绝使用 `{"type":"reject", "message": ...}`。不要使用批量 `decisions`，不要把已交付的旧决定附在下次请求里。未交付但已启动过工具时，先完成第 5 节对账；不能跳过未知效果核对直接恢复。
7. 一项执行完，框架可以继续返回其它 interrupt。尚未结束的并行工具结果保存在 pending writes；对账时同时检查 StateSnapshot、任务和 pending writes，不能只读取顶层 `values.messages`。
8. 只有本轮工具批次均有合法结果，才能进入下一次模型调用。部分决定不表示允许绕过其余等待项。

API 接线还需拆开受理与执行的进程内锁：当前 `SessionService.resolve_approval` 和 `execute_claimed_approval` 都持有 `_session_locks[session_id]`。在同进程部署或 Harness 中，直接复用这段锁会使 A 的提交等待 B 执行结束，即使数据库端口已经允许 running 时受理。v2 普通决定的短事务受理必须在长执行锁之外，执行仍保留独占；用真实 API 请求与被阻塞的 B Worker 验证 A 及时返回 202。单独调用 store 的通过结果不能代替此验收。

逐项审批不定义业务依赖。需要 A 成功才能做 B 的场景，由受控图条件边或冻结执行计划表示；不能把它们作为同批独立工具调用，期待点击顺序实现依赖。

录制、代理子审批、TestRun 审批仍走现有专用入口，保持 project/run/lease 校验；共用内部决定与恢复端口，不把所有审批强行导入普通 Session endpoint。

## 5. checkpoint、业务状态和副作用的恢复协议

### 5.1 决定交付证据

固定 LangGraph 1.2.12 源码中，`interrupt()` 消费 resume 值后会发送 `__resume__` 写入，运行时会把它交给 saver。一次调用返回之前杀进程，也可能已经有该写入。因此，不能用“continuation 没返回”或“仍看到 interrupt”推断决定未交付。

**实际格式与时机必须按以下事实实施：**

- `Command(resume={interrupt_id: decision})` 是调用输入。`pregel/_loop.py` 对合法 interrupt ID 映射放入执行配置，过滤该映射的全局 resume 写入；不能假定 `checkpoint_writes` 中直接存了该字典。
- `types.py::interrupt()` 写的是图任务自身按 interrupt 调用顺序积累的列表，例如 `[decision_0, decision_1]` 或已消费前缀。`resume_index` 表示该列表内的位置，和数据库 `checkpoint_writes.idx` 不是同一个概念；后者由 saver 的 channel/index 映射决定。
- `pregel/_runner.py::commit()` 在图任务成功、出错、取消或再次 interrupt 时提交任务写入。调用 `interrupt()` 返回，不代表该 resume 立即持久化；工具可能已经启动甚至结算，而 receipt 仍未 delivered。无证明不能推断“工具未执行”。
- 官方 PostgreSQL saver 按写入通道选择 upsert 或 `ON CONFLICT DO NOTHING`。适配器不能只核对传入 `put_writes` 的参数后就标 delivered；必须在同一事务内确认实际保存的目标 resume 列表包含匹配位置和内容。冲突保留旧值或身份不一致时，事务失败并进入对账，不制造交付证明。

**P2.1 实测补充**：同一任务依次经过两个 interrupt 时，固定 runner 的一个实际混合批次含 `__resume__=[decision_0]` 和 `__resume__=[decision_0, decision_1]` 两次写入。将该真实批次原样交给 PostgreSQL saver 3.1.2，`DO NOTHING` 只保留较短前缀；此前 invocation 的前缀也可能阻止增长。已增加 owner-bound 适配：仅对已关联业务审批的任务，在同一事务内验证已存/传入的各前缀相互兼容，以最长前缀调用官方 saver 的特殊通道 upsert，然后核对实际保存值并写 proof。相反决定、路径或内容矛盾仍回滚，重放短前缀不能截断已保存的长前缀。未映射任务和官方全局 scalar resume 保持原契约。该适配依赖固定框架版本，升级时必须运行相同真实任务回归，核对上游是否已修复。

为绑定的候选版本增加受测的 saver 边界适配：在 `put_writes/aput_writes` 保存目标任务的 `__resume__` 时，将对应 receipt 的 `delivered` 证据与官方 pending writes 放进**同一连接、同一事务**。匹配依据为 namespace、graph_task_id、interrupt 映射、resume 位置及决定内容哈希；不得把无关任务或旧决定的写入算成本次交付。重放相同证明幂等。

证明契约如下。当前已在 AgentTask 初始化中新增 `<agent_task_table>_interrupt`、`<agent_task_table>_decision`，并提供调用方事务端口；业务 API 自动创建 link/receipt、提交决定与 task/outbox 的贯通仍属 P2.2：

| 记录 | 身份及不变量 |
| --- | --- |
| interrupt link | `binding_id + logical/physical namespace + checkpoint_id + graph_task_id + task_path + interrupt_id + resume_index`，关联源 AI 消息、原 tool_call_id、Approval、ToolJob；投影重建不改变已有映射 |
| decision receipt | 一个 `decision_id` 唯一，保存规范化决定的 hash、queued/sending/delivered、applied_at；同一审批的相反决定不可覆盖 |
| delivery proof | 引用真实 checkpoint write 的 thread/物理 namespace/checkpoint/task/channel/write index、列表内 resume_index、匹配的 decision_hash、owner epoch 和证明格式版本；记录不可变身份，不保存凭据 token |

proof 另记 `evidence_kind=atomic_write/reconciled`。原子写证明与 pending writes 同提交；对账只读取匹配的已存事实并补投影。`owner_epoch` 表示本次证明校验的执行代次；reconciled 证明不冒称原生写入的原始 owner。已有证明的时间、代次与来源保持不变。两表沿当前业务表位于 public；checkpoint 继续使用配置的独立 schema，部署需要同数据库事务及相应 schema 权限。

平台先持久化 interrupt link，才允许发送决定；对每项 per-call，必须从固定框架版本的任务/interrupt 结构及实际写入建立确定映射，不能将所有位置硬编码为 0。重复证明返回已有记录；已 delivered 的证明冲突不可改写。证明及原始写入的保留期限至少覆盖 Turn 的恢复期，清理前必须保证不再依赖它们继续执行。

这是应用新增的恢复日志协议，**官方 saver 不自动提供该 receipt 或业务 exactly-once**。对框架内部 resume 写入格式的依赖必须版本化，并有候选版本故障测试。没有证明且状态矛盾时进入 `recovery_required`，不猜测已交付。读取真实 pending writes 可修复历史 receipt；不得仅凭 interrupt 消失推断应用成功。

已 delivered 而未完成的执行，通过最新合法 checkpoint 继续（无新决定时使用 `invoke(None, config)` 对应的异步路径），保留已保存的 resume 值。正常恢复不指定历史 checkpoint 做 time travel；回放调试走隔离 thread，不能在真实副作用 thread 上当作重试。

### 5.2 故障窗口与处理动作

| 持久事实/故障窗口 | 恢复动作 | 明确禁止 |
| --- | --- | --- |
| interrupt 已保存，Approval/等待 ToolJob 尚未投影 | 根据稳定调用键 upsert；终态 ToolJob 直接引用结果 | 每次查询都新建审批或按工具名合并 |
| 决定已记录，未交付 | outbox 重投/后继 continuation；领取 owner 后核对 checkpoint，再发送这一项 | 整批重发所有已决定项 |
| receipt 未 delivered，但 ToolJob 已启动/结算 | 先按稳定操作身份对账；终态结果回放，未知效果保持阻塞；证明可安全继续后才重送尚未持久化的决定 | 把“未 delivered”解释成“没有副作用” |
| `__resume__` 已保存，工具任务尚未完成 | 修复 delivered receipt，继续已有执行；检查 ToolJob 是否启动/未知 | 因 API 超时重发已持久化决定 |
| ToolJob 已终态，checkpoint 未收齐结果 | 返回已保存的原始工具结果和证据，保持 tool_call_id；让图收敛 | 重做外部操作，或重新触发审批准备逻辑 |
| checkpoint 已推进，业务/UI 投影未落库 | 按 checkpoint 版本、工具结果和 receipt 补投影，事件用稳定 key 去重 | 再次提交已消费决定或追加重复模型历史 |
| ToolJob 已启动、目标返回未知 | 查外部操作 ID/幂等键/Runner journal，记录查询证据；无法判明时人工对账 | 把未知当失败后自动再执行 |
| 模型已发出、响应或用量未知 | 记录 unknown 调用和预算保留；仅按受测恢复策略重新请求，并计入新尝试 | 将失联调用记成 0 次/0 Token |
| owner 失效或新 epoch 接管 | 拒绝旧代 checkpoint/pending writes/业务结算；新 owner 先对账 | 取消旧协程后直接认定旧线程停了 |
| 首次输入意图存在、没有可用 checkpoint | 结合 ToolJob/调用账本判断；无法证明安全时维持 recovery_required | 从最初用户输入盲跑整个 Turn |

外部 API 支持幂等键时，以稳定操作身份绑定，续跑不生成新键。不支持幂等或查询结果的副作用，保留未知状态并提供人工核对入口。此处目标是**可追溯、不盲重跑、能安全继续或明确阻塞**。

### 5.3 暂停、取消、超时、崩溃分开处理

- **等待审批**：原生 interrupt/checkpoint，释放执行租约；等待本身不持有 Worker 或浏览器等昂贵资源，资源保留遵循已有领域租约规则。
- **用户取消**：落库取消意图和撤销所有权，停止新增模型/工具派发；尽力取消外部任务。无法停止的效果进入核对，取消状态不能伪装成已撤销副作用；不自动恢复。
- **暂停**：只在明确定义的安全边界允许；没有安全 checkpoint 的状态返回不可恢复原因。
- **请求/工具超时、Worker 崩溃**：原任务按现有语义进入 `recovery_required`，创建引用原任务/attempt 的对账任务；对账证明可继续后，建立后继执行任务，保留原终态记录。
- **TestRun 恢复**：沿既有 attempt 恢复链和租约规范执行；不得把已完成 RunItem 重置排队。失败回归建立新运行，保留原失败证据。

对账任务是 v2 明确的新操作类型，不能让旧 schema 的普通 `manual_resume` 在未知副作用时自动重放。自动对账只做确定性的状态补齐和目标查询；无法证明安全则明确等待人工处理。

#### 已实现的审批后继协议（实施记录 §23–24）

```text
approval_resume / approval_recover 原执行 failed 或 recovery_required（保持终态）
    → framework_reconcile（确定性检查，不调用图、模型或工具）
    → approval_recover（引用对账 proof，重新领取 owner 后走原审批继续链）
```

- 原任务终态重投或审批执行正常失败时，通过既有任务/outbox 事务受理对账。同 Session 锁、确定性 task ID 和既有唯一约束保证同一原 task/attempt 不重复创建。
- 对账复核原 task/attempt、binding、活跃 pending Turn、冻结决定、receipt、原生 interrupt/resume 和全部同 Turn ToolJob。只有终态 Job 或真正未启动的 waiting_approval 可自动继续；running/未知效果保持阻塞，即使已有非权威迟到观察也不能自动提升为结果。
- proof、后继任务/outbox、对账完成及 Session 控制投影同事务提交。领取与 prepare 均核对后继和已完成对账的 proof；原生实际消费事实仍重新读取，未消费决定发指定 Command，已消费决定用 None 继续。
- 对账链优先于同 Turn 排队 sibling；后续普通消息不能替换仍未结清的绑定 Turn。用户显式停止撤销 owner，禁止自动恢复。内部对账/后继输入不作为用户待发送消息展示；Coordinator 查找业务父任务识别 approval_recover，不识别对账任务。
- 对账自身运行失租时，只有持久终态为 recovery_required、error_code 为 execution_owner_expired 且存在原 attempt 的任务，才受理新 framework_reconcile。新任务 reference_id 指向原审批执行，parent_task_id 指向刚失租的对账；JSONB 冻结原审批和前驱 task/attempt。受理及领取复核绑定、活跃 Turn 和当前 owner；同一前驱的重投/并发受理复用一个后继。
- 确定性拒绝、未知效果、明确返回的数据库错误或用户停止不生成对账重试链。对账提交前强杀后事务全部回滚，失租后新建任务；提交后 ACK 前强杀时保留已完成对账和唯一审批后继，重投只确认终态。原任务/attempt 均不重置；实际强杀与旧代写入拒绝见 §24。
- 当前仅覆盖审批执行的已知结果恢复及对账自身失租。首次 message 失败、目标查询/人工对账、其它提交窗口仍待实现；不能把这条子链路描述成任意崩溃恢复，也不能开放生产默认路由。

## 6. 执行所有权：同时保护 checkpoint 与业务写入

### 6.1 采用的数据库隔离方式

选择**同一 PostgreSQL 数据库内、短事务行锁加 epoch**的方案。新增每个绑定的 owner 记录，和 Session 现有租约关联；不依赖 Redis 锁作为最终写入权依据。

owner 含 `binding_id / owner_epoch / owner_token / attempt_id / worker_id / expires_at / execution_state`。每次领取/接管，原子更新 Session 租约及 owner；epoch 单调递增。调用开始时捕获不可变 epoch/token，不能每次写入读取最新 token 后冒充新 owner。

checkpoint 写入适配器的单次数据库操作：

1. 从池取一个连接，打开显式事务。
2. 按统一锁顺序锁 Session → runtime owner → 需要变更的任务/receipt/ToolJob 行；核验活跃 Turn、epoch、token 和数据库当前时间下的有效租约。
3. 使用 **`PostgresSaver(该连接)`**，而非 `PostgresSaver(连接池)`，调用官方 `put` 或 `put_writes`。需要写交付证明时在这一事务内同步写 receipt。
4. 提交事务后才算写入成功；不在事务内等待模型或外部工具。

接管和撤销也锁同一 owner。旧代操作若先获得锁，先线性化完成；新代接管必须等它提交。新代接管提交后，旧代任何新写入都会 epoch 不匹配而失败。锁等待后用实际数据库时钟核验过期，不能使用等待前的缓存判定。

已核对 `langgraph-checkpoint-postgres==3.1.2`：官方 `_cursor` 对显式连接使用同一连接，`put/put_writes` 不另开独立池连接；pipeline 在该连接内执行。因此上述事务边界有源码依据。方案形成时尚未通过真实数据库验证；2026-10-26 的同进程真实数据库事务、回滚、取消及连接归还验证见[实施记录第 8 节](framework-integration-progress-2026-10-25.md#8-owner-与-checkpoint-写入事务2026-10-26)。决定 receipt 与 pending writes 同事务见第 10 节；业务决定受理与串行继续已有 §18–20 的局部 Harness，独立 Worker 故障见 §21，已知结果接管/后继见 §23。其余强杀窗口仍是 P2 待过项。部署 setup/migration 单独执行，不在业务事务里反复 `setup()`。

**namespace 落实修订（固定版本源码与实测依据）**：LangGraph 1.2.12 的 `pregel/_loop.py` 会把顶层调用的非空 `checkpoint_ns` 重置为 `""`，仅向 `invoke` 传入绑定 namespace 不构成存储隔离。v2 owner saver 将图的逻辑根 `""` 映射为绑定的物理 namespace，逻辑子图路径映射为 `绑定 namespace|逻辑路径`；读取时逆向映射 config/parent_config。thread_id 保持 turn_id，完整保留图内部路径。该适配必须覆盖根/嵌套 namespace、历史枚举、pending writes、原生审批继续与 delta channel 重建；不以篡改框架内部 task ID 实现隔离。

保护范围包含 `put/aput`、`put_writes/aput_writes`、嵌套 namespace、清理/删除等所有可变操作。普通运行时不提供删除 thread 权限；管理清理也必须验证绑定终态和没有有效 owner。适配器还需保留官方 serializer、channel version、delta channel 及读取能力，不能仅实现两个写方法就丢掉 saver 的其它契约。读取可以提供只读检查接口，只有有效 owner 能继续执行或改变状态。

保留 Windows 同步 saver 加 `to_thread` 的适配方向，但正确性由数据库隔离保证。取消 await 后仍可能运行的旧线程，也必须接受同样的 epoch 判定。给连接/锁/语句设置有限超时，防止接管无限等锁；这不替代所有权检查。

### 6.2 ToolJob 与领域 Runner 的所有权

**同步工具当前实现（实施记录 §22）**：沿用 ToolJob CAS，在实际写入事务中按 Session → runtime owner → ToolJob 加锁并核验数据库时钟下的租约。原子领取冻结 `framework_binding_id` 和不含 token 的 `framework_execution_owner`（binding、epoch、task、attempt、worker）；心跳、输出观察、结算、finish、整行保存和附件写入都验证捕获的原执行身份。missing owner 或下一协调代次不能结算旧执行。旧的已启动绑定 Job 若没有执行引用，需要对账，不能根据当前 owner 补猜原身份。普通 stale reaper 不改变绑定工具。

实际 handler 前有一次短事务派发准入，提交后不持锁等待外部调用。它能拒绝准入前已撤销的 owner，不能消除“准入已提交，外部请求尚未发出”窗口，也不能撤销已准入操作的外部效果。框架工具抛错且无可靠返回检查点时，保留未知事实并停止图，不转成可继续推理的普通失败结果。迟到返回使用既有 inline ToolArtifact，以原执行引用和内容哈希去重追加 `framework_late_output`，标记 `authoritative=False`；不修改 Job、决定或 applied proof，不作为通过证据。后台 Runner 独立执行权及安全对账仍是下述待实施契约。

现有 once-per-call、原子领取、终态回放和输出 CAS 继续保留，但 v2 工具调用还需携带 owner 信息。普通同步工具在派发和权威结算时校验执行权。

持久后台 Runner 的执行权用 **ToolJob/资源自身的 owner epoch**，不能简单随父 Turn 的一次 HTTP 续跑结束而失效。父图拥有派发与读取结果的权利；实际 Runner 拥有该操作的执行与结算权。接管只转移协调权，未知副作用不能因此重新执行。

旧 owner 的迟到结果可作为带原 epoch 的追加观察证据入库，由新 owner 对账；不能直接覆盖当前权威结果。修订输出观察、结算和 finish 的写入谓词，避免仅凭 `status=running` 写入。领域已有独立 fencing 时复用其机制并验证映射。

这些属于应用与官方 saver/Runner 的集成设计，不是 LangGraph 内建的业务租约功能。实施时注明官方边界和新增理由，并由延迟旧写入测试证明，而不是只加一次前置 lease 检查。

## 7. 官方模型适配器与真实调用治理

### 7.1 模型工厂与兼容矩阵

| 传输 | 框架对象 | 必验兼容边界 |
| --- | --- | --- |
| OpenAI Chat Completions | `ChatOpenAI`，显式保留 Chat 路径 | 实际 endpoint/header、工具 ID、`max_tokens` 与 `max_completion_tokens` 配置、流式 usage |
| OpenAI Responses | `ChatOpenAI` Responses 路径 | 原生内容块/关联 ID、工具回传、reasoning、流式事件；不能意外路由 Chat |
| Anthropic Messages | `ChatAnthropic` | 原生 block、tool_use/tool_result、签名、stop reason 与 usage |
| Gemini GenerateContent | `ChatGoogleGenerativeAI` | API backend、端点/header、自定义参数、thought signatures 与工具结果 |

继续调用供应商原生 API，不新增 OpenAI HTTP 网关。模型工厂复用现有数据库配置和凭据解析；[Anthropic 官方集成](https://docs.langchain.com/oss/python/integrations/chat/anthropic)与前述 Gemini 集成提供框架端点。

方案形成时的框架入口只放行 Chat 且拒绝显式 `max_tokens`；当前 `OfficialChatModelFactory` 已支持四传输，并通过本地 HTTP/SSE 与真实官方 SDK 的请求捕获测试，详见实施记录。真实供应商与部署配置仍需验收：能正确映射则支持，不能映射则在受理前明确报告不支持。不能默默换参数或把“包已导入”视为四协议兼容。

### 7.2 每次真实调用的接入点

从 `ModelRuntimeService.invoke()` 提取共享 `ModelCallPolicy`：配置解析/指纹检查、参数映射、能力校验、超时、重试、错误分类、预算预留、usage 结算和 Trace。普通调用和框架调用共用策略，保留各自官方传输适配。

- 主代理、受控图和显式子代理的模型 wrap hook 都调用同一策略；`before_model` 只做输入装配，不能拿 Turn 开始的一次计数替代真实调用计数。
- 显式注入每个子代理的策略中间件；不假定具名子代理和默认 general-purpose 自动继承。试点只开放已装配并受测的只读认知子代理。
- 默认摘要可能直接调用模型，不能假定会经过主模型 wrap hook。替换同名摘要中间件，使其独立调用也走共享策略，并保持唯一摘要所有者。自动生成子代理和其它隐含模型调用在受治理前禁用。
- 默认只保留一层应用重试，关闭 SDK/其它中间件的隐式重复重试；各传输须验证关闭选项确实生效。每次实际 HTTP 尝试有子记录；恢复重试也计入原 Turn。
- 严格区分逻辑调用 ID、尝试 ID、父/子分支 ID。并行子代理在数据库内原子预留根预算，实际 usage 只结算一次；未知响应保留 unknown/保守预留，不因重启清零。
- 预算冻结调用上限、Token 上限、活动执行时长及绝对截止时间。审批等待不消耗活动执行时长，但不延长绝对截止时间；审批后重新判断剩余预算。

流式改为消费官方实际模型 chunk/event，统一投影已有事件接口，区分子代理/摘要与最终答复；按流身份和序号去重。流的最后一块、usage、工具参数拼接、断流、取消和结束事件分别验收。当前结束后一次回调只作为旧路径行为保留。

模型已发出而失联，不能承诺供应商端“只调用一次”。应用账本需保留不确定尝试，保证预算和审计不将其遗漏。

### 7.3 已实现的账本、请求策略与受理接线（实施记录 §25–31、34）

`PostgresFrameworkModelCalls` 与 `framework_model_call.py` 提供根预算及尝试存储端口，复用 Session → owner 的事务锁与数据库时钟。`FrameworkModelCallPolicy` 已连接官方 Deep Agents 主代理、现有具名只读子代理、自动摘要及 owner 作用域内 `ModelRuntimeService.invoke`；bootstrap 初始化账本并把同一策略注入模型服务和 bound adapter。`submit_v2(..., model_budget=...)` 可在首次任务受理事务中冻结显式预算。受控 AgentCase 图、自动生成绑定/预算并路由新 Turn 仍未接线；此阶段不能据此自动恢复首次消息或开放生产路由。

- 一个 binding 冻结调用上限、Token 准入额度、绝对截止时间及允许的模型配置指纹映射。后继 Worker 读取原预算，不能重新冻结更大的额度或延后的截止时间。
- 每条尝试保存 branch/parent、logical_call_id、独立 attempt_id、用途、传输、模型/请求摘要及输入估算与输出预留；不保存凭据、owner token 或完整 Prompt。模型传输类型复用现有 `ModelTransport`。
- `prepare` 原子占用根额度；`start` 只向原执行 owner 发放一次发送许可，并再次检查时限及实际用量超估后的根余额。重放相同 attempt 不能重新发送；事务异常回滚保留原状态。此许可是应用发送意图，不是供应商端幂等保证。
- `ready → invoking → completed / unknown` 是本阶段尝试生命周期。缺 usage 的 completed 与 unknown 都保留原 Token 预留；实际 usage 可超过估算，必须真实入账，并阻止已超额后新增或尚未发送的调用。`actual_tokens=0` 不代表费用为零，应同时读取 charged_tokens、unmeasured_calls 和 unknown_calls。
- 同一 branch/logical_call 不允许换 attempt 暗中重跑。显式重试仅接受同请求的唯一 unknown 前驱，新增 call 和 Token 预留；存储端口不自行触发重试，也不代替后续恢复策略授权。
- 失租 owner 不得推进尝试阶段或结算 usage；新的有效 owner 可将旧 invoking 分类为 unknown，不能冒充原请求的响应观察者。尚未发送的旧 owner 预留保持占额，不能由后继直接发送。§29 新增的原观察者证据追加端口允许失租迟到返回归档，但不授予阶段、usage 或预算的修改权；旧预留释放仍未实现。

新增 `<agent_task_table>_model_budget` 与 `_model_call` 两张表；bootstrap 在初始化既有任务/owner 表后调用账本 setup，沿用本项目 startup 初始化风格，不在请求中建表。真实部署库启动升级尚未验收。Token 准入使用“已知实际用量 + 未结算/缺失用量预留”，未知实际费用可能高于估算，不能声称这是供应商实际 Token 的绝对硬上限。

受理与 Worker 接线（实施记录 §27）：

- 新的显式 v2 受理可带 `FrameworkModelBudget`；Session → owner 锁内冻结，与初始任务、输入消息和 outbox 共事务。预算纳入受理幂等内容摘要，重试不能修改额度/截止时间；预算或后续 outbox 写入失败时整体回滚。冻结保存逻辑与 owner 端口复用，不另建一套预算表。
- 生产 Dispatcher 在领取非终态、可能执行图的 v2 任务前读取原冻结预算；历史缺预算任务返回 `framework_model_budget_unavailable` 并保持未领取，不按当前配置补额。终态重复投递与纯事实对账不受该准入检查阻塞。后继审批任务读取同 binding 的原预算。
- 生产 RuntimeService 按不可变 binding 选择受治理的 bound adapter；无绑定旧试点使用原 adapter。两者共用现有 checkpoint provider 和能力配置，不新增另一个模型工具循环。直接构造 RuntimeService 的现有测试调用者保留显式注入方式；生产 bootstrap 明确组装两条依赖路径。
- 已用官方 SDK、本机合成响应和真实 PostgreSQL/Redis 验证初始 Worker 模型调用；另验证 A/B 逐项审批：初始调用一次，批准 B 后只执行 B 且不增加模型调用，拒绝 A 后才发第二次模型调用；不同执行 owner 共用同根额度。

这些接线没有启用新 Turn 路由。旧 v2 消费者仍须排空并统一升级；缺少历史用量的已执行 Turn 不能由新 Worker 自动生成预算后继续。自动受理与全部版本资产尚未接通前，不能宣称全系统迁移完成。

普通 DTO 与前置模型调用（实施记录 §28）：

- `SessionService.execute_claimed_message` 从输入编排开始建立捕获的 owner 作用域，语义分类实际 HTTP 请求也必须经过根账本。模型服务只从进程内 ContextVar 获取 owner，不接受公开 context 中的身份作为执行权；无绑定/无 owner 的旧调用继续原路径。
- DTO 与官方图消息分别构造请求封套和用量读取器，共用账本准入/start/send/complete/unknown 流程。§34 将新调用升级为 `position_version=1`：DTO 身份只含版本标记、binding 与调用方持久 logical_call_id；图内身份包含版本标记、binding、branch、namespace、原生 task 与 purpose。请求摘要校验该位置的不可变输入，不参与生成位置；owner-bound DTO 缺位置明确拒绝。历史 `position_version=None` 不补造位置，新旧记录不能据此自动混跑，详见 §14.8。
- 四种原生传输及既有 LangChain Chat 路径在 owner 下显式关闭 SDK 重试，非零隐式重试请求明确拒绝；缓存也在实际 LangChain DTO 模型对象关闭。HTTP 错误先记 unknown，再按旧 DTO 契约返回错误结果；缺失/不完整 usage 继续保留预留。Google 原生 SDK 使用 `HttpRetryOptions(attempts=max_retries+1)`；Anthropic 原生 usage 的输入包含缓存读取及创建 Token，原生响应摘要不改写。
- 输入分类的框架 owner 缺失 policy 时在 HTTP 前失败。语义增强保留其现有可检索降级日志；任何后续实际模型调用仍受同根预算约束。后台独立领域任务与无 owner 的旧调用未被迁移为 v2。

显式注入路径的实际行为：

- 官方模型对象携带构建时的非秘密配置指纹；每次请求重新读取配置，对比对象快照及冻结根策略。请求摘要覆盖完整框架消息、工具 schema、模型设置及结构化输出描述，逻辑身份还绑定原生 task/namespace。当前为应用调用封套摘要，不能代替供应商实际 wire 请求资产。
- 每次主代理/子代理调用在 SDK 之前 prepare/start；官方 usage 结算根预算。无 usage 保留预留，发送异常或取消保留 unknown；完成结算的确认失败不改写持久阶段、不自动重发，只记录可检索事件并要求对账。配置指纹变化在 HTTP 前拒绝。
- 默认 LangChain 摘要使用 `model.with_retry`，不经过主模型 wrap。采用固定版本官方摘要的 `summarizer` 扩展点，保留官方裁剪、Prompt、摘要状态和 backend 行为，单独将实际 `ainvoke` 接入同一策略；每个分支仍只有一个摘要中间件。SDK 重试保持关闭，策略本身不自动重试。
- 受治理主/子代理和摘要请求显式关闭本地 LangChain 模型缓存，以免缓存命中被记为新的物理请求。供应商 prompt cache 仍由其实际 usage 表达。
- Deep Agents 0.7.13 的 profile 注册会合并 excluded_tools，不能按 Turn 更新全局排除集合。全局 profile 只保留禁用默认 GP 的不变配置；每次建图使用独立的官方工具排除中间件。该中间件是固定版本的私有导入兼容点，升级时必须验收。已有进程若注册过旧排除集合，应排空并重启，不能靠新注册清除旧集合。[官方 Profile 合并语义](https://docs.langchain.com/oss/python/deepagents/profiles#merge-semantics)

模型资产与 wire 观察（实施记录 §29–31）：

- `FrameworkModelEvidence` 使用既有 ArtifactStorageService 保存 canonical 请求和完整应用层响应；账本只挂 URI、SHA-256、长度与版本身份。原执行者可追加非权威迟到观察，不能用它改 usage、推进图或判测试通过。未启用 RustFS 时 bootstrap 不注入该证据服务；框架路由仍关闭，不能默认假设每条历史调用都有完整资产。
- OpenAI Chat/Responses 通过官方模型公开客户端字段接入 wire，Gemini 使用公开 `HttpOptions.httpx_async_client` 和模型 `client` 字段。固定 Anthropic 集成缺少公开 per-instance HTTP 客户端字段，当前图内仅有应用层证据；普通 DTO 的传输能力与图内注入能力须分别验收。
- HTTPX/HTTPX2 观察区分请求体、JSON/SSE 响应体、截断和完整标志，不保存认证头。多次 SDK retry 尚未逐次保存独立 wire 对象；大响应截断、断流对象不能用于完整恢复。摘要接入根预算不等于摘要 wire 已覆盖。
- 对象存储与账本引用不是共同事务。对象或引用确认失败时保留不确定性，不重发；孤儿资产的发现、认领/清理及保留策略仍需实施。当前对应 Harness 使用对象存储替身、本机 HTTP 合成响应，不能替代真实 RustFS/供应商验收。

后续必做：自动受理时生成/冻结完整绑定与预算、受控图接线、完整动态参数/超时/错误与应用重试策略、资产保留/孤儿对账与剩余 wire 能力、已知失败及未发送尝试的结算策略、活动执行时长、真实供应商/其余流式故障验收。官方 [自定义 middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom) 提供 wrap 接入点；不能将当前 owner 作用域内路径外推为所有独立领域模型调用均受治理。

## 8. 新旧版本部署与数据迁移

### 8.1 选择双环境、双队列

默认采用可回滚的并行部署，避免将唯一 Python 环境升级后再宣称保留旧运行时：

| 项目 | legacy | v2 |
| --- | --- | --- |
| Worker 制品 | 冻结原代码、依赖锁和环境 | 单独镜像/虚拟环境，候选依赖及新代码 |
| Redis stream | 保留当前 `qa:tasks:agent` | 拟新增 `qa:tasks:agent:v2`，作为应用配置项 |
| 命令 | 保留 schema v1 | 新 schema v2，带 binding/version/operation |
| checkpoint | 旧 schema 与旧序列化契约 | 独立 v2 schema，按绑定定位 |
| 审批和恢复 | 原 endpoint 语义，路由旧 Worker | 新契约；通用 API 根据绑定选择消费者 |

当前已有 v2 stream、命令及独立 CLI；Worker 已支持初始 message、approval_resume，以及 §5.3 的框架对账/审批后继。上表描述必须完成的部署边界，legacy manifest 和本地虚拟环境不代表已部署两个可服务制品。

**本轮 operation 扩展的发布限制**：新增 framework_reconcile / approval_recover 复用 schema v2，runtime_version 仍为 v2.0.0。较早 v2 二进制的严格 Literal 可能在能力检查前解析失败，因此不能宣称同 stream 下新旧 v2 Worker 混跑安全。默认 v2 路由仍关闭；启用该链前须排空/停止旧 v2 消费者、更新所有 v2 Worker，或先实施受测的制品/版本隔离路由。未完成真实双制品发布与回滚验收。

API/outbox relay 先成为兼容两类命令的过渡版本；outbox 每条记录固定目标 stream 和 schema，不在重试时按最新开关重算。新旧 Worker 只消费各自 stream，并在领取前复核数据库绑定。v2 Worker 缺少能力时 defer/告警，不能改用 legacy 运行。

普通新 Turn 按模式路由开关选择版本；已经创建的 Turn、审批 continuation、Coordinator 子任务及对账任务都沿原绑定。Session 级租约仍跨版本互斥，v2 owner 与 Session 租约同事务维护。

数据库迁移先加表/可空字段/索引，兼容旧代码；v2 新 operation 不投递旧 schema。旧解析器、状态枚举、索引与 `SELECT *` 消费方逐一验证；不在 legacy 仍使用的字段中加入其无法解析的新状态。

### 8.2 最小新增数据契约

以下是完整目标逻辑记录，不能将表中全部字段视为已实现。当前已存在 RuntimeBinding/owner、v2 task/outbox、原生消息封套及 interrupt/decision journal，另有可显式初始化的模型账本基础（§7.3，尚未生产接线）；其生产接线与剩余字段分别按实施记录验收。尚缺契约沿项目既有迁移风格补齐：

| 记录 | 最小内容与约束 |
| --- | --- |
| RuntimeBinding | 第 3 节冻结项；一个 Turn 一个绑定；派发前创建 |
| Runtime owner | binding 唯一；epoch/token/attempt/expiry；领取、续租、撤销和 saver 共用 |
| Framework call/interrupt link | 按第 4 节完整调用键唯一；interrupt 身份单独映射，ToolJob/Approval 外键；参数/范围 hash 只用于校验 |
| Approval decision/receipt | 一个审批一个有效决定；一个决定一个续跑意图；delivered/applied 的持久证明和尝试记录 |
| Native transcript | 消息身份、内容封套、来源/版本、归属和摘要覆盖范围；追加去重 |
| Model call ledger | 根预算、分支/逻辑调用/实际尝试、预留/实际/未知用量、配置摘要；并发原子更新 |
| Task v2 绑定信息 | runtime binding、版本、后继/对账引用；outbox stream 与 command schema 固定 |

不复制领域用例、TestRun、审批主体和 ToolJob 业务事实。跨层改动同时更新 schema、路由、store、Dispatcher、Worker、API 回执、前端状态、事件和测试。

### 8.3 切换、排空与回滚

1. 上线兼容数据库及 API/outbox 路由，保留 legacy Worker。
2. 先部署 v2 Worker，通过 P0–P3 验收；仅对代码审查新 Turn 启用。
3. 每个模式独立放量，观察质量与恢复指标。旧回合继续由旧制品处理；不原地升级其 checkpoint。
4. 回滚时新 Turn 路由回 legacy；v2 已有回合继续由兼容 v2 Worker 结清，不能把已有 checkpoint 交旧环境。
5. 按绑定统计 queued/running/waiting approval/recovery required/children/未知工具任务。全部结清或有明确终止、对账记录后，才能下线对应 Worker 和旧恢复代码。

若无法保留双环境，则需要单独批准“先排空再升级”的发布策略；不能仅写 `runtime_version` 而声称完成兼容。当前方案已选择双环境作为实施默认。

## 9. 各模式迁移与 Harness 边界

先接公共运行时，再迁移现有能力。各模式都需要公共契约测试和领域裁决测试；表中是目标验收，不表示当前所有项已经具备。

| 模式 | Deep Agents/受控图负责 | 领域服务与模式 Harness 必验项 |
| --- | --- | --- |
| 默认协作 | 对话、认知计划、工具选择 | 治理、预算、完整工具轨迹；不能凭答复声称操作成功 |
| 代码审查（首个试点） | 只读探索、证据分析、受限认知子代理 | 真实 Prompt/SkillRegistry、项目目录边界、发现与行证据、报告；写/删/命令能力隐藏 |
| API 测试 | 探索、候选场景、失败分析 | 契约、正式用例/套件冻结、实际请求、Verification；无断言不得判通过 |
| UI 自动化 | 页面探索与操作推理 | 录制专用审批、浏览器资源租约、回放、断言与截图证据 |
| 安全测试 | 授权范围内分析 | 授权、Campaign、受控工具、范围变化后阻止、证据裁决 |
| 性能测试 | 计划草稿、结果解释 | 正式方案审批、逐级加压、采样、阈值；吞吐/错误率/P50/P95/P99，流式目标额外 TTFT/Token 吞吐 |
| 冒烟测试 | 用例草稿与失败解释 | 固定版本计划、Runner、断言、结果入库及新回归运行 |
| 兼容性测试 | 矩阵建议与差异分析 | 矩阵冻结、环境调度、Runner journal、结果身份与差异证据 |
| 六个占位模式：单元/组件、可访问性、视觉回归、移动、可靠性、集成测试 | 按当前模式注册表维持既有占位边界 | 对应 `unit_component_testing / accessibility_testing / visual_regression_testing / mobile_testing / reliability_testing / integration_testing`；不以接框架宣称测试引擎已实现 |

正式 `AgentCaseExecution` 使用直接构建的受控 LangGraph：上下文准备 → 模型调用 → 工具治理/审批 → 结果汇入 → 预算/终止判断 → 证据评估。循环条件进入图，取消新路径的外层重复模型循环。结果评估仍由既有 Assessment/Verification 领域服务负责；不把模型“完成”当作测试通过。

原领域 Runner 作为服务或治理工具调用，不逐个套图。用例驱动链路保持：草稿 → 评审 → 启用固定版本 → 套件冻结 → 原子领取运行条目 → 结果/证据 → 新失败回归运行。

测试设计参照本项目[Agent 测试课程](../项目借鉴/llm-testing-course/Agent测试专项.html)、[轨迹与协议鲁棒性](../项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html)、[评测工程化与 CICD](../项目借鉴/llm-testing-course/评测工程化与CICD.html)，落实到工具轨迹、状态迁移、异常协议、恢复和可追溯评测数据。

## 10. 分阶段实施与验收

### 10.1 阶段、依据、改动边界

| 阶段 | 依据 → 做法 | 主要现有改动位置 | 出口 |
| --- | --- | --- | --- |
| P0 依赖/模型契约 | 官方版本与 E4 → 独立 v2 环境、完整锁、实际配置矩阵 | `Agent_Server/pyproject.toml`、根 `uv.lock`、`application/models`、`application/model_adapters` | 实际导入、四传输请求/流/工具/原生消息测试；未通过的配置明确不支持 |
| P1 状态/输入/预算/绑定 | E5/E6 → 可序列化状态、transcript、冻结绑定、共享预算与双队列协议 | `schemas`、`graph/state.py`、`application/context`、`application/prompting`、任务/outbox/migrations | 跨 Turn 不丢消息；失租不清预算；旧环境不能领取新任务 |
| P2 审批/所有权/对账 | E1/E2/E3/E7/E9 → 第 4–6 节一起实施 | `runtime_service.py`、治理/ToolJob store、任务 store、checkpoint provider/saver、前端审批消费者 | 真实 DB/Redis 跨进程故障链通过；两类写入均拒绝旧 owner |
| P3 代码审查实链路 | E8 与现有试点 → 接真实 Prompt/Skills/只读文件/子代理/流式事件 | `application/deep_agents`、code_review 模式、事件投影、模式 UI | 固定评测集与实际项目审查通过，恢复与证据可追溯后才能推广 |
| P4 逐模式迁移 | 既有领域契约 → 公共运行时加各模式 Harness | `application/test_runs`、各 modes/Runner 边界 | 每模式独立验收/切换，保留未结旧回合 |
| P5 收敛旧实现 | 版本绑定盘点 → 排空后移除重复循环/摘要/桥接 | 原 legacy 分支、部署配置 | 无未结绑定和未知副作用；回滚路径验证通过 |

每阶段先出小范围实现 diff 再验证并提交，不能将 P0–P3 全部混在一次升级中才找问题。新增应用机制注明所核对的官方边界；不重写现有领域引擎。

候选直接版本保持上一版选择：

```text
deepagents==0.7.13
langchain==1.4.6
langchain-core==1.6.10
langgraph==1.2.12
langchain-openai==1.6.2
langchain-anthropic==1.7.1
langchain-google-genai==4.4.0
langgraph-checkpoint-postgres==3.1.2
psycopg-pool==3.3.1
```

这不是完整 transitive lock。本轮临时安装另外补入 bracex/filetype/wcmatch，复用现有 SDK 依赖；P0 仍需在目标操作系统、Python 版本和全新环境验证根锁与依赖一致性，并检查直接/间接依赖的维护状态、许可证与部署兼容性。

### 10.2 必过测试矩阵

| 层次 | 必须覆盖 | 通过判据 |
| --- | --- | --- |
| 模型协议 | 四传输实际配置、原生 endpoint/header/参数、分片工具调用、流式终止/usage、错误/重试、元数据往返 | 捕获真实请求和消息序列，不只测试返回字符串；无静默降级 |
| 审批契约 | 任意先后决定、相同名/参数不同 call ID、重复/相反点击、running 时提交、并发、权限变化、门控恢复稳定性、取消后点击、专用入口 | 一项执行、其它保持等待；调用身份不变；相反决定冲突；原拒绝不因动态门控变化失效；无额外执行 |
| 恢复协议 | 第 5 节每个提交窗口，尤其 resume pending write 已保存但调用未返回 | 对账动作明确；不重发已交付决定；终态 ToolJob 原结果回放 |
| 所有权 | 旧 `put`、旧 `put_writes`、ToolJob 迟到结算、to_thread 取消后延迟提交 | 新 owner 接管后旧权威写入拒绝；失败事务不留下 delivered 假证明 |
| 状态与预算 | 跨 Turn 原生消息、摘要完整批次、子代理/摘要调用、并行预算、未知用量、模型配置变更 | 工具调用/结果完整配对；预算不因恢复重置或并发超支 |
| 调度与发布 | 审批不被新消息 FIFO 堵塞、v1/v2 隔离、回滚、新旧等待回合、recovery_required 后继任务 | 相同 thread 串行；旧运行制品可用；终态任务/RunItem 不重领 |
| 领域裁决 | 固定版本、正式项目、真实断言/证据、终态保护、新回归运行 | 没证据不判通过，不覆盖原失败 |

P2/P3 的关键真实链路：

1. 官方 Deep Agents + 实际 PostgreSQL saver + Redis + 受治理工具，产生 A/B 两个独立审批。
2. 先批准 B；B 外部计数一次，A 仍 pending，模型尚未进入下一轮。
3. 在 B 结果持久后、全部 checkpoint/业务投影完成前杀掉 Worker；分别覆盖多个提交窗口。
4. 新 Worker 获取更高 epoch，先对账，再处理 A 的决定；B 复用原结果，不重新操作。
5. 恢复一个被延迟的旧 Worker 写入；`put`、`put_writes` 与 ToolJob 权威结算都不能覆盖新代状态。
6. 最终模型收到匹配且完整的 A/B 结果，任务/审批/ToolJob/证据投影一致。

另测原生受控 LangGraph 路径，不以 Deep Agents 路径通过替代。必需集成项缺环境、skip、xfail 都不能算此出口通过。

固定评测集记录数据/评分/基线、Prompt、Skill、模型、runtime、依赖与环境版本；对比迁移前后质量、调用次数、Token、活动耗时、TTFT 和失败轨迹。阈值沿用已有模式质量门；没有基线的模式先生成并冻结基线再切换。

### 10.3 P2 的实施依赖与 TDD 出口

按下面顺序交付小阶段，不把新增 receipt 表本身视为审批迁移完成。所有“先失败”均需实际运行并保留失败输入，再实现和回归。

| 步骤 | 改动与依据 | 先失败的用例及出口 |
| --- | --- | --- |
| P2.0 owner 与物理 namespace | 第 6 节；当前已实现 owner-bound saver，同进程真实数据库验证已过 | 已有旧代写入、to_thread 迟到、事务回滚、锁等待和 DeltaChannel 测试；跨进程出口仍由 P2.3 承担 |
| P2.1 决定交付证明 | 第 5.1 节；link/receipt 存储与 fenced saver 同事务证明已实现，通过同进程真实数据库 Harness | 已验证官方 per-call 真实 pending write、多位置前缀、无关任务/namespace、内容冲突、`DO NOTHING` 保留旧值、重复证明及双向回滚；不替代 P2.2 生产接线或 P2.3 跨进程验收 |
| P2.2 v2 决定受理与串行继续 | 第 4.3、8 节；Runtime/Session、Dispatcher/API、outbox 与 v2 Worker 已接通，决定受理已从长 Session 执行锁分离；持久审批进度 DTO 与逐项前端投影已接通，详见实施记录 §12–20 | Harness 已验证真实 Runtime 初始 A/B、B 执行时 HTTP 提交 A、202/409/闭合后幂等回执、真实 Redis Worker 的逐项完成与重复投递不重执行，以及前端 jsdom 刷新/重连/并发响应；真实浏览器验收与 P2.3 跨进程出口仍待执行 |
| P2.3 崩溃对账与后继任务 | 第 5.2–5.3、6.2 节；已实现中断撤销、同步 ToolJob 执行权、未知返回阻断、迟到观察、已知结果审批的幂等对账/安全后继及对账自身失租后继；见实施记录 §21–24 | 独立 Worker 在工具/native 结果保存后、业务 applied 前，以及对账提交前/提交后 ACK 前强杀，后继继续并应用 A 拒绝，B 计数为 1。未知效果及显式停止阻止后继；首次 message、其它 resume/proof 窗口、领域 Runner 独立执行权仍待实施验收，P2.3 未完成 |

P2.1 的只读投影重建可独立验收；v2 支持 message、approval_resume 和 §5.3 的 framework_reconcile / approval_recover。旧命令仍不能领取绑定任务，原生身份缺失不能降级走旧恢复。前端持久投影已通过 jsdom/Vue/Pinia 和构建检查，真实浏览器验收仍待执行；P2 整体及 P3 试点放量继续以该验收与 P2.3 出口为前提。

P1 的共享预算生产接线与活动时间计量、跨 Turn transcript、版本实物校验也需完成才能放量，不能因为 P2 通过或账本存储端口已有 Harness 就忽略这些前置条件。代码审查完成后，再按第 9 节对各模式逐个冻结基线与验收，六个占位模式仍保持原边界。

## 11. 本轮实际验证与局限

本轮新增[离线复现脚本](verify_framework_contracts_2026_10_25.py)，不修改业务数据库或项目依赖。

| 验证 | 实际结果 | 能证明的范围 |
| --- | --- | --- |
| 当前环境消息往返 | 通过；当前投影确实丢失四类字段，官方消息封套保留 synthetic fixture | 支持 F5；不替代真实供应商签名测试 |
| Queue 序列化 | 默认 JsonPlusSerializer 抛 `TypeError` | 支持主图移除进程内状态 |
| to_thread 取消实验 | await 已取消后，同步线程仍完成 | 支持 F3 的隔离需求；不是数据库竞争复现 |
| 候选包导入 | 九个候选直接版本实际导入成功 | 临时 overlay 基于现有 SDK，非全新锁环境认证 |
| 官方 per_call Harness | 四种场景通过：不同参数/相同参数分别覆盖先批 B、再批 A / 拒 A；中间无新决定继续也不重跑 B；结果 ID 完整，每场景模型调用两次 | 同步调用、fake model + InMemorySaver；重建同进程 Harness，非异步平台/跨进程/数据库恢复 |
| 项目既有五个相关测试文件 | **43 passed, 42 skipped，9.03 秒** | 当前原环境测试；skip 缺 Deep Agents 或未开启真实服务，不计为集成通过 |

实际当前环境命令（仓库根目录，PowerShell）：

```powershell
.venv/Scripts/python.exe docs/verify_framework_contracts_2026_10_25.py
```

此次候选目录是 Windows Temp 下随机命名 overlay，未纳入仓库。可按本轮相同方式复现安装与脚本调用（需要已有根环境提供底层 SDK；不等于全新依赖验收）：

```powershell
$frameworkContractDir = Join-Path ([System.IO.Path]::GetTempPath()) ('qa-framework-contract-' + [guid]::NewGuid().ToString('N'))
$frameworkContractPackages = @(
  'deepagents==0.7.13', 'langchain==1.4.6', 'langchain-core==1.6.10',
  'langgraph==1.2.12', 'langchain-anthropic==1.7.1',
  'langchain-google-genai==4.4.0', 'langgraph-checkpoint-postgres==3.1.2',
  'psycopg-pool==3.3.1', 'bracex==3.2', 'filetype==1.2.0', 'wcmatch==11.2'
)
uv pip install --target $frameworkContractDir --python .venv/Scripts/python.exe --no-deps --default-index https://pypi.org/simple @frameworkContractPackages
.venv/Scripts/python.exe docs/verify_framework_contracts_2026_10_25.py --candidate --dependency-dir $frameworkContractDir
```

脚本不会自行安装；OpenAI 包复用原环境 1.6.2。首次全量隔离 `uv run --isolated` 因下载反复重试被中止，之后改为此最小方式；没有将下载中止记作运行通过。

项目回归命令（工作目录 `Agent_Server`）：

```powershell
../.venv/Scripts/python.exe -m pytest tests/test_approval_checkpoint_consistency.py tests/test_approval_model_budget.py tests/test_deep_agent_tool_replay.py tests/test_deep_agent_runtime_adapter.py tests/test_agent_task_dispatch.py -q -rs --tb=short
```

本轮未执行：真实 PostgreSQL/Redis 故障注入、跨进程 kill/recovery、真实供应商四协议调用、浏览器/压测/安全目标、前端构建和全量测试。它们需要实现后在隔离集成环境验收，不能从当前离线实验推出已通过。

本轮交付范围仅为本修订方案、可复现的离线研究脚本及相应 Git 忽略例外；不修改运行代码、依赖声明、锁文件或生产配置。占位模式开发、领域引擎重写和外部模型兼容网关继续不在本次迁移范围内。

## 12. 2026-10-26 复核记录与边界

本节及第 1.3 节更新当前事实；第 11 节的“本轮”指 2026-10-25 最初方案研究，不代表后续仓库没有实施。

本次重新读取附件、当前业务审批/Worker 能力边界、官方模型工厂、原生消息保存、owner-bound saver，以及已安装固定版本的 `types.py`、`pregel/_loop.py`、`pregel/_runner.py`、PostgreSQL saver 实现；在线重新核对官方 [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) 和 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。Deep Agents 审批建立在 LangGraph interrupt 上；应用 receipt、Session 租约、ToolJob 和测试裁决均不是框架自动提供的业务语义。

实际重跑命令（工作目录 `Agent_Server`）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_binding_persistence.py tests/test_framework_checkpoint_fencing.py tests/test_agent_v2_worker_contract.py -q -rs --tb=short
```

结果：**39 passed，70.96 秒，无 skip**。使用隔离测试表/schema，覆盖已有绑定持久化、Worker 边界、真实 checkpoint 事务与官方 per-call adapter；不覆盖尚未实现的业务 v2 审批/receipt、独立进程 kill/recovery、供应商真实请求及模式迁移。本次只更新方案和实施记录，不修改运行代码或依赖，不将设计补齐视为迁移完成。

## 13. 基线 bde82d38b 的附件复核与实施清单

本节更新第 12 节之后的事实。附件的六类风险继续成立；其中“仍只有批量桥接”“v2 saver 没有所有权保护”“没有决定交付证明”“本地未安装 Deep Agents”不能作为当前状态引用。它们分别已有适配器或存储层实现，完整业务链仍未接通。

### 13.1 先实现哪些边界

| 顺序 | 明确改动 | 必须先写的失败用例 | 验收前保持的边界 |
| --- | --- | --- | --- |
| 1. P2.2 决定受理事务 | 在现有任务 store 的事务设施上建立独立、内聚的 v2 提交模块；校验调用主体与当前活跃 Turn；原子保存业务决定、receipt、唯一 continuation task 和 outbox | B 正在续跑时提交 A；任一步落库失败全部回滚；不同请求 ID 的重复点击复用原回执；相反决定冲突 | `queue_decision_in_transaction` 只读取已受理决定，不代替 API 授权；legacy 绑定审批冲突保护保留 |
| 2. P2.2 调度与原生调用映射 | Runtime 从原始 AI 消息、StateSnapshot 任务及 interrupt 提取稳定身份；Approval/等待 ToolJob/link 按调用身份投影；活跃 Turn 的 continuation 优先于后续普通消息，同 thread 串行领取 | 同名同参数不同消息/call 不合并；投影中断后身份不变；普通消息排前也不堵塞续跑；并发只有一个有效 owner | 不用 name+args 推测身份；不以测试准备函数手工建 link 冒充生产接线 |
| 3. P2.2 继续执行与投影 | 成对接通 Dispatcher、Worker、SessionService、Runtime、adapter；未交付决定先对账再发指定 Command，已交付无新决定时 `ainvoke(None)`；所有执行入口显式 `durability="sync"`；API/事件/UI 同步 | 恢复不重新导入历史；B 只执行一次、A 保持等待；拒绝/治理阻止都有对应结果；决定已记录、delivered、applied 不提前展示 | 只有端到端用例通过才声明 Worker 支持 approval continuation；专用录制/代理子审批/TestRun 入口保留领域校验 |
| 4. P2.3 崩溃与副作用对账 | 失租任务保留 `recovery_required` 终态；增加幂等对账及安全后继任务；即时撤销取消 owner；贯通 framework owner、ToolJob 和 Runner 执行权 | 独立 Worker 被杀及旧 Worker 延迟写入；工具已结算但 checkpoint 未推进；外部结果未知；新 Worker 接管后 B 计数仍为一次 | 无结果查询/幂等支持的未知效果明确阻塞；终态 AgentTask/RunItem 不重置重领 |
| 5. P1 剩余项、P3–P5 | 共享真实调用预算、跨 Turn transcript/context、版本实物校验、双制品部署；代码审查实链路及模式 Harness 逐个验收后放量，最终排空旧路径 | 并行子代理预算、摘要调用计数、原生消息跨 Turn、旧环境不可领取新任务、存量审批与回滚；每模式冻结评测基线 | P2 局部通过不替代 P1/P3；占位模式保持占位；现有 Runner 与正式测试版本链继续使用 |

以上细化既定方案的实施顺序，覆盖全系统迁移的原始范围。每个实现阶段先复现失败、再做根因改动、运行相关回归并提交。

### 13.2 本次核验的依据与结果

重新读取[逐项适配器](../Agent_Server/src/application/deep_agents/runtime_adapter.py)、[任务 store](../Agent_Server/src/runtime/postgres_agent_task_store.py)、[Worker 能力](../Agent_Server/src/runtime/framework_worker_capabilities.py)、[官方模型工厂](../Agent_Server/src/application/model_adapters/official_chat_model_factory.py)、[fenced saver](../Agent_Server/src/application/deep_agents/fenced_postgres_saver.py)、[决定 journal](../Agent_Server/src/runtime/framework_approval_journal.py)、[Session 落库](../Agent_Server/src/application/sessions/session_service.py)及其调用边界；在线核对第 1.2 节官方资料，并确认固定 1.4.6 的 HITL 条件恢复行为。当前 adapter 仍缺显式 sync durability 和无新决定继续入口，Worker 仍只接受 v2 message，Session 仍逐条保存普通审批；这些均为待实施事实。

实际重跑（工作目录 `Agent_Server`）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_receipts.py -q -rs --tb=short
```

结果：**27 passed，68.71 秒，无 skip**。真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph、fake model 验证决定 proof、pending writes、回滚、前缀增长及已有对账端口；不覆盖业务 v2 API、跨进程 kill、供应商模型或领域 Runner。通过 `importlib.metadata.version` 和实际 HITL 导入核对九个直接依赖均与第 10.1 节版本一致，签名包含 `interrupt_mode`；这不替代全新环境构建或供应商验收。

本轮仅更新本方案和实施记录；未安装/升级依赖、修改业务代码或开放路由。未运行全量测试、独立 lint/type-check、前端构建、真实供应商和跨进程故障验收，原因是本轮交付为方案复核，且这些完整迁移出口尚未具备实现。

## 14. 基线 b8ebb12d6：附件再次复核与剩余实施契约

本节是对第 1.3、5、7、10 节的当前补充。附件评审适用于原方案形成时；当前已有 §12–31 的实现，必须按实际入口区分 legacy、显式绑定的 v2 和未来目标架构。此次交付为方案修订，下面标注“待实施”的动作均不能作为已有恢复能力。

### 14.1 六项意见的处理决定

| 附件意见 | 本次处理决定与当前依据 |
| --- | --- |
| 逐项审批改全链路 | 采纳。v2 已使用 per-call、短事务受理、串行 continuation 和稳定 link；旧 `_submit_approval_sync` 的 waiting_approval 限制与旧批量桥接仍存在，但不代表 v2 入口仍按旧方式运行。保持三维事实：人的决定、交付 receipt、ToolJob 结果；不采用附件的统一线性生命周期。见 `framework_approval_submission.py`、`framework_approval_continuation.py` 和 `runtime_adapter.py` |
| checkpoint 与业务写入一致性 | 采纳。已建立局部事务与 proof，并未建立外部共同事务。`FrameworkApprovalRecovery` 仅为已知结果的审批执行和规定的失租对账创建后继，不能恢复首次 message，也不能把迟到观察升级为权威结果 |
| checkpoint 所有权隔离 | 采纳。`FencedPostgresSaver._write_transaction` 在同一数据库事务持有 owner 锁，再委托官方 saver；put/pending writes 都校验捕获的原 owner。物理 namespace 映射和 DeltaChannel 重建不可省略。无绑定旧试点、独立领域 Runner 的能力另计 |
| 官方模型对象共享治理 | 采纳。`FrameworkModelCallPolicy` 已接主/具名子代理/摘要和 owner 内 DTO，不能把工厂本身视为治理。附件的 max_tokens 描述只适用于原 DTO 适配入口；当前官方工厂使用 extra_body 保留显式 Chat 字段，已有 wire 用例。每条入口/配置分别验收，不能从旧入口的拒绝推断新工厂也拒绝 |
| 完整消息跨 Turn | 采纳且仍是放量前出口。`framework_transcript.py` 无损序列化和 Runtime 的 framework_messages 保存已存在；初始状态仍将其设为空，不能据此声称下一 Turn 已导入完整档案。展示消息、checkpoint、跨 Turn 原生档案各有职责 |
| 版本与部署 | 采纳且仍是放量前出口。`FrameworkWorkerCapabilities` 只接受已支持 kind/version/schema/lock/operation；实际仅有 Deep Agents v2，`runtime_kind=langgraph` 不能执行。独立 CLI/stream 和 lock 摘要不能证明旧 Worker 制品、实际依赖和回滚已验证 |

官方依据：本次重新核对 [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) 的 per-call 及恢复时 when 重算约束、[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop) 的 LangGraph interrupt 实现基础、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 的节点从头重入、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) 的 pending writes 与 sync durability、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 的 thread/store 分工。在线文档已有新流接口示例，不将其直接移植到固定版本；以本地已安装版本签名和 Harness 为准。

### 14.2 首次 message 的恢复必须补输入准入，而非重调整个 Turn（诊断、阶段准入及调用位置已实现；安全后继待实施）

当前事实：`submit_v2` 已同事务保存原始输入、用户消息、binding、显式预算、阶段准入和 outbox。`execute_claimed_message` 从输入编排起设置 owner 作用域，在编排前冻结来源摘要清单，编排后冻结完整 ExecutionRequest；模型新逻辑位置及其原尝试已可持久查询。阶段推进、执行与诊断共用资产摘要校验，实际执行从冻结 JSON 重建请求。初始图状态、外部 Prompt/Skill/Memory 实物与安全后继尚未形成完整协议。`_run_owned_submission_locked` 清空 pending_turn 后才执行 Runtime，单凭“没有 checkpoint/ToolJob”仍不能证明没有发生模型或外部效果。

实施拆为两步，每步独立验收：

1. **先提供只读事实诊断（已实现内部端口，见 §14.7）**。定位原 task/attempt/binding，联合输出输入准入、owner/租约、模型尝试、最新 checkpoint/pending writes、ToolJob 和现有投影的证据及缺口。诊断不调用模型/图/工具、不发 outbox，不修改终态或补造 usage。未知/缺失事实明确显示 recovery_required 的原因。
2. **再建立阶段准入与安全后继**。阶段准入、来源清单、ExecutionRequest JSON 与摘要、稳定模型位置和先持久意图后上传资产的顺序已实现（§14.8）；阶段不允许重入，已发送/未知调用不自动重发。后续仍需冻结实际外部资产及版本，明确安全后继操作、原 ready 预留转移和原响应导入。显式新尝试须记录前驱、授权和新增预算；现有账本存储端口不表示这些恢复策略已经接通。

| 观察到的事实 | 恢复判定 |
| --- | --- |
| 仅原始输入存在，尚无 checkpoint | 当前保持 recovery_required。只有新增准入协议能证明尚未越过任何发送/工具派发边界、且输入与版本可还原时，未来才允许创建新后继；“没有账本行”本身不足以证明安全 |
| 模型 ready，未发发送许可 | 保留原预留及身份；后继不能冒用旧 owner 发送。未发送预留的释放/转移须单独实现和验证，不通过重建预算绕过 |
| 模型 invoking/unknown，缺响应或 usage | 禁止自动重发。HTTP 已发送与 start 后尚未发送无法仅从账本区分；即使没有业务副作用也可能重复计费 |
| 完整响应已保存，checkpoint 尚未接收结果 | 先对账原尝试、资产身份、完整性和官方任务位置。非权威观察不自动变成任务输出；在结果导入协议通过故障 Harness 前保持需对账，不重发 |
| checkpoint 存在 | 必须核对实际待运行节点、原生结果、模型尝试与 ToolJob。checkpoint 存在不等于可以无条件 ainvoke(None)，部分节点可能从头执行 |
| checkpoint 完成，Session/UI 尚未完成 | 按原稳定消息/事件身份补投影；不再调用模型、重导入历史或新建工具操作 |

首轮红测固定覆盖：输入已入库但无 checkpoint；前置语义调用后崩溃；start 提交后崩溃；HTTP 已发但响应/usage 未保存；响应资产写后账本确认丢失；图完成后 Session 保存失败。Redis 重投及独立 Worker 接管必须核对实际 HTTP 次数、ToolJob 效果次数与原终态保留。历史缺少新准入事实的 Turn 不反向认定安全。

### 14.3 活动时间、资产与跨 Turn 输入（待实施部分）

- **时间预算**：现有账本有绝对 deadline，没有持久活动执行时间。增加按 owner/task/attempt 的活动片段，使用数据库时钟和已有租约约束结算；并行分支的根活动时长按时间并集计，不能把并发耗时相加当 wall time。另需统计各调用耗时。审批排队/等待不计活动时间，崩溃片段须有明确保守上界，重新领取不能重新获得完整时长。冻结该口径和故障用例后再扩展预算 DTO。
- **资产完整性**：应用响应、wire、usage、任务结果分别记录。Anthropic 图内 wire、摘要 wire、多次 HTTP retry 独立对象、大响应截断和孤儿对象对账均需单列能力；任何缺失都不得显示“完整可重放”。模型资产保留周期覆盖未结 Turn/审批/对账，清理前检查引用；真实 RustFS 必过验收。
- **跨 Turn**：在 Session 快照已有 framework_messages 基础上定义可检索的原生档案入口和版本封套，首次新 Turn 明确导入，原 Turn 恢复只读 checkpoint。测试完整 AI/tool 组、摘要覆盖范围、子代理隔离、消息去重、Gemini thought signature/Anthropic 内容块及模型切换。Memory store 只保存选择的长期事实，不充当完整 transcript。
- **真实流式**：SDK 的 stream/usage 测试不等于前端 Token 流。本基线 Runtime 仍在 adapter 完成后调用 on_model_chunk(result.output_text)。后续单独接官方 chunk 到事件投影，覆盖最终答复、子代理/摘要流隔离、断流、取消、TTFT 和终止；不以结束后的一次回调宣称通过。

### 14.4 未知工具效果与领域 Runner（待实施）

复用治理/ToolJob/领域执行身份，不在 Deep Agents 内再建持久业务 Worker。对不同工具在现有注册契约上明确结果查询及幂等支持：

- 可查询目标：使用原操作身份只读查询，并记录目标证据与时间；匹配后经受治理对账形成权威结果，再补图/业务投影。
- 有幂等契约：只在目标明确保证的范围和有效期内复用原键；重试仍是新尝试，保留原未知历史。
- 目标不可查询且没有幂等保证：转人工确认，记录操作者、证据和处理结论；保持原任务终态，未证明前不派发原动作。

Runner 领取/heartbeat/结果结算须与自身领域租约及 framework owner 建立可验证引用。框架取消只撤销本系统权威写入，不能声称远端浏览器、压测或扫描已经停止。资源退租、实际取消与结果查询各自返回事实；领域终态 RunItem 不得重置重领。人工对账的公共入口及权限仍待设计，不能把当前没有自动后继称为已经具备人工处理能力。

### 14.5 余下阶段的实施顺序与出口

继续沿第 10 节阶段，不把已有能力重做一遍：

| 顺序 | 交付边界 | 必须看到的验收证据 |
| --- | --- | --- |
| A：首次 message 与活动时间 | §14.2–14.3；诊断、阶段/尝试身份已实现，再做安全后继和活动时间预算 | 上述六个崩溃窗口；HTTP 不盲重发、原预算/终态不重置、旧 owner 写入拒绝、等待不耗活动时长 |
| B：原生上下文与制品冻结 | 跨 Turn 档案导入、Prompt/Skill/工具/模型及实际运行制品 manifest；补齐剩余资产契约 | 真实供应商消息往返、完整工具组/签名、漂移拒绝、历史缺资产可解释阻塞 |
| C：P2.3 剩余恢复出口 | 未知目标查询/人工对账、领域 Runner 所有权、其余提交窗口 | 真正的独立 Worker kill、旧 Worker 延迟返回、一次目标效果与可核验证据；不能只造终态记录 |
| D：P3 代码审查 | 接现有 PromptAssembly/项目/Memory/SkillRegistry，文件只读、受限子代理、真实流事件 | 固定仓库/提交样本的发现和行证据、目录边界、原根预算、审批/取消/恢复、真实浏览器展示 |
| E：P4 受控图与模式 | AgentCase 的模型循环进入 LangGraph；第 9 节每个非占位模式分别接领域 Harness | 原固定用例/套件/RunItem 链、实际 Runner、Verification/Evaluation、无证据不通过及新回归运行 |
| F：P5 部署与收敛 | 双制品/双队列、兼容数据库升级、存量回合结清、回滚和旧代码移除 | 旧 Worker 不能领新操作；新路由回滚后已有 v2 checkpoint 仍由兼容制品结清；无未知执行遗留 |

D 的本地开发可以在 A–C 期间推进；开启任何生产新 Turn 前须满足其相关 P0–P3 出口。E 中各模式独立验收与开关，不因代码审查通过自动切换。默认协作/代码审查不天然形成测试结果；API/UI/安全/性能/冒烟/兼容性的执行与裁决继续使用第 9 节领域服务。六个占位模式仍不计功能完成。

公共 Harness 负责模型协议、工具轨迹、审批与所有权、预算、上下文和故障恢复；模式 Harness 负责该模式输入、Runner、断言、证据、评价和清理。每个模式记录可复现入口、fixture/环境、实际依赖版本、必过命令和失败样本，报告通过/失败/skip/未执行；不以模块名、harness_key 或通用测试绿灯代替模式验收。此口径沿用本地《Agent轨迹与协议鲁棒性》§7 的轨迹、Schema 漂移、异常注入和成本要求。

### 14.6 本次验证的记录方式

本次重新核对九个固定包实际安装版本与 HITL 构造签名，确认包含 interrupt_mode；同时确认固定 ChatAnthropic 的公开模型字段没有 HTTP client 注入项。测试命令与最终结果记录在[实施记录 §32](framework-integration-progress-2026-10-25.md#32-附件复核与剩余实施契约2026-10-26)。当前只修改方案及记录，没有修改运行代码、依赖、数据库结构或开关；未把历史回归通过数累计为本次结果。

### 14.7 首次 message 只读诊断端口（2026-10-26）

`PostgresAgentTaskStore.diagnose_framework_message(task_id, session_id=...)` 提供内部只读端口，由 `FrameworkMessageDiagnostics` 联合读取，不接入原审批恢复分支。调用方提供已经限定权限的 Session；不增加 HTTP 路由、Worker 自动恢复或人工操作入口。无绑定或非首次 message 的任务明确拒绝，Session 不匹配按任务不存在处理；task 与 binding 的 Session/Turn 身份矛盾时，在读取账本/checkpoint 前报错，禁止损坏记录将诊断重定向到另一 Turn。

- 同一 PostgreSQL 连接先设置 `REPEATABLE READ READ ONLY`，在一个 MVCC 快照读取 task/attempt、原输入及用户消息、owner/租约、模型账本、官方 checkpoint/pending writes、ToolJob、原 Turn 的 SessionSnapshot 和 pending_turn。返回数据库时间与快照标识；它们用于解释本次读取，不能作为之后执行的授权。遵循 [PostgreSQL 事务特征](https://www.postgresql.org/docs/current/sql-set-transaction.html)，不取得 owner 行锁，不调用会执行 setup 的 checkpoint provider。
- 模型账本保持 ready/invoking/completed/unknown 和原预算；响应与 wire 资产仅返回原有引用及非权威/截断/完整性标记，不读取对象正文或补造 usage。缺表、缺预算及坏记录分别列出；账本无法完整解析时调用数和 Token 汇总返回未知，而非零。`evidence_objects_verified=false` 明示本次没有核验 RustFS 实物。
- 输入摘要复用 v2 准入的同一个计算入口，包含 binding fingerprint 与准入时的可选冻结预算；同时识别历史无预算准入。无法重建摘要时报告校验缺口，不改写原摘要。展示消息检查身份和内容一致性，但不返回正文。
- checkpoint 只读指定 thread 与确切物理 namespace/子 namespace 的最新身份、父 checkpoint、step 及 pending write 身份/channel；不解码 Prompt 或工具内容，也不推断图已完成或可以 `ainvoke(None)`。ToolJob 返回状态、执行 task/attempt/epoch 和结果存在性，保留未知执行待对账；SessionSnapshot 按原 Turn 选择，不把另一 Turn 的最新投影当原执行结果。
- 返回的 `recovery_status=recovery_required` 和 `automatic_replay_allowed=false` 是本端口对自动重放的判定，不修改任务实际状态。§34 新准入记录返回阶段及摘要白名单，并核对冻结 JSON 的摘要；记录合法时标记 `stage_recovery_not_implemented`，缺失时标记 `stage_admission_unavailable`，损坏时额外报告 invalid/identity mismatch。活动时间、安全后继、完整消息位置重建及外部对象验真仍未由此端口实现。

新增 Harness 和本次联合回归见[实施记录 §33](framework-integration-progress-2026-10-25.md#33-首次-message-只读事实诊断2026-10-26)。六个真实崩溃窗口与 Redis 重投的供应商 HTTP 次数验证仍属于 §14.2 第二步，不能用诊断测试替代。

### 14.8 首次 message 阶段与稳定模型调用位置（2026-10-26）

新增 `<agent_task_table>_message_stage` 表，随 task store 初始化显式创建；外键关联原 task/binding，在首次 v2 提交的同一个事务写入，失败整体回滚输入、预算、binding 和 outbox。历史缺表/缺记录不能反向认定为“从未执行”，升级前须盘点未结 v2 回合并保留兼容 Worker；没有为历史回合伪造准入。

```text
submitted → orchestrating → orchestrated → runtime_submitted → projected
```

- `orchestrating` 前冻结 Session revision、可见消息 ID/摘要、payload/metadata 摘要及绑定身份；排除租约凭据。这是来源清单，不能据此还原变化后的外部 Memory/Prompt/Skill 服务。
- `orchestrated` 保存原 ExecutionRequest 完整 JSON 与摘要；`runtime_submitted` 在进入 `_run_owned_submission_locked` 前保存，表示应用即将提交 Runtime，不证明任何 provider HTTP 已发出。实际执行从冻结 JSON 重建，避免原 mutable request 后续变化。
- 每次推进在 Session → owner → task → stage 的数据库锁和 owner fence 内校验原任务、阶段及来源/执行摘要；失租、重入或损坏均要求对账。`projected` 仅保存首次 message 返回的 output/snapshot ID，等待审批也可能到该阶段，不能等同于框架 Turn 全部完成。
- 图与 DTO 请求先 `prepare_initial` 保存不可变意图/预算，再保存请求资产、绑定引用，最后取得唯一 start 许可。相同逻辑位置复用原 attempt；输入漂移明确拒绝。原 owner 的 ready 记录可获得尚未发出的唯一许可；invoking/completed/unknown 或不同 owner 不重发、不重置预算，也不自动导入结果。
- 前置语义分类使用固定 `input_orchestration:semantic_intent:v1` 位置；其恢复异常向上抛出，不能降级成一次新的正常编排。历史模型记录缺少 position_version 时保留原事实并阻止新位置自动发送。

Harness 使用真实 PostgreSQL、Redis、官方 SDK 与本地 HTTP 合成供应商：分别在 start 前、start 已提交而 HTTP 未发、HTTP 已返回而结算未确认处强杀独立 Worker，再回收未 ACK 消息，观察 HTTP 次数分别保持 0/0/1，原 attempt/预算/阶段不变，原任务终态保留。它们验证阻止盲重发，没有创建安全后继；不代替真实付费供应商、真实 RustFS、响应导入、图完成后业务投影失败及其余 §14.2 出口。命令和实际结果见[实施记录 §34](framework-integration-progress-2026-10-25.md#34-首次-message-阶段准入与稳定模型位置2026-10-26)。依赖及生产默认开关未改变。
