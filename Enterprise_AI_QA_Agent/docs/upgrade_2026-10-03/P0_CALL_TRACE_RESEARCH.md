# P0 模型调用追踪调研

日期：2026-10-18

## 调研基线与当前代码事实

系统已有内部 `ModelInvocationRequest` 和按传输类型选择的 provider client。各传输保留自己的协议与请求映射：Anthropic 调用 `client.messages.create`；OpenAI Responses 独立位于 `OpenAIResponsesClient`；Gemini 使用原生客户端。`await_provider_io` 只等待 SDK 操作，并将网络异常转换为 `ProviderClientError`。

当前存在多种有用但不可互相替代的身份：

- 主图 turn 使用 `session_id`、`turn_id`、`trace_id`；图请求在可用时携带运行、条目和执行尝试上下文。
- 用例规划每个规划轮次持久保存 `logical_call_id`，Worker 使用 `attempt_id`。
- 文档生成以 `request_sha256` 标识冻结请求，生成任务另有 Worker `attempt_id`。
- 需求影响调用持久保存 `call_id` 和 Worker `attempt_id`。
- 回执纠正检查点已有逻辑调用身份。

调研基线时，`TraceContext` 不接受 `logical_call_id`（`extra="forbid"`），模型运行时也没有将统一调用身份传给 provider SDK。7.78 实施后，`TraceContext` 与 runtime 已传播逻辑调用身份，并由各 provider 的注入 client 记录 SDK HTTP 请求尝试和收到的响应；该观察尚未贯通所有逻辑调用入口，也不是精确的物理发送计量。

## Provider 观察入口

对照当前 `uv.lock` 锁定的 SDK，并在该环境读取构造器签名，确认以下注入接口：

| 路径 | 锁定依赖 | HTTP client 注入接口 |
|---|---|---|
| 原生 OpenAI client | `openai==3.14.0`、`httpx2==2.13.0` | `AsyncOpenAI(http_client=...)` |
| Anthropic Messages | `anthropic==1.5.0`、`httpx2==2.13.0` | `AsyncAnthropic(http_client=...)` |
| Google GenAI | `google-genai==2.23.0` | `HttpOptions(httpx_async_client=...)` |
| LangChain OpenAI-compatible | `langchain-openai==1.6.2` | `ChatOpenAI(http_async_client=...)` |

仓库已经分离 provider 请求序列化和响应解析，因此观察层应保持每个 provider 的原生协议。构造器接受注入只证明入口存在；这些锁定版本的 timeout、retry、streaming、关闭及并发语义已通过本地真实 SDK/TCP 故障矩阵首轮验收（见下文），外部供应商完整验收及全阶段追踪仍未完成。

## 决策与下一步

不统一供应商协议，不把 Anthropic Messages 替换为 OpenAI Responses。逻辑 ID 已增加并接入 SDK request attempt/response 观察；这不构成物理网络发送已发生的证明。

## 观察层实施与实测边界

基于上面的 SDK 注入入口，在既有 runtime 调用上下文内实现 `ProviderCallObservation`，沿用/生成 `logical_call_id`，将 httpx2 client 注入 OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini 和 LangChain OpenAI-compatible 调用。日志仅保存 transport、脱敏路由、HTTP method、attempt 序号、响应状态和耗时，不保存凭据、请求体或响应体。成功及 HTTP 错误结果带 `logical_call_id` 和 `http_request_attempt_count`；旧字段 `http_send_count` 已移除，避免误称为物理发送数。

真实 TCP fixture 首轮失败揭示实现根因：锁定的 httpx2 在异步请求链中 `await` request hook，而观察函数最初是同步函数。Google 直接抛出 `TypeError: object NoneType can't be used in 'await' expression`；OpenAI 将同一 hook 错误包装并 SDK 重试，fixture 收到 0 个 HTTP 请求。将 hook 改为 async 后，重跑原 SDK/TCP 故障矩阵 **59通过**；覆盖五条 transport、SDK 对 429/503 的实际重试、403、发送前连接断开、流式截断、超时、取消、endpoint/协议体、并发隔离和回调异常。受影响模型回归 `tests/test_model_clients.py tests/test_model_completion_http.py tests/test_model_sdk_fault_http.py` **95通过**。该证据验证锁定 SDK 下自定义 client 的请求映射、重试、流式与关闭路径；不代表每家外部供应商均已真实调用验收。

httpx2 request event hook 的执行位置在 transport 发送之前，因此 `model_http_request_attempt_started` 严格表示“SDK 已把请求交给 HTTP client”，不能单独证明字节已到达网络或服务端。response hook 证明收到 HTTP 响应；本地 TCP fixture 的服务端计数证明测试环境中实际收到的请求与 attempt 顺序一致。遇到 DNS/连接建立阶段失败时，生产 attempt 计数可能大于服务端实际收到数。

继续检查锁定 `httpx2==2.13.0` 的本地 `_client.py` 后，发现传入自定义 `transport` 时客户端会设置 `allow_env_proxies = trust_env and transport is None`，也就是显式传输会停用自动环境代理映射；为保留所有默认 mount 再包装 `_transport` 则需要访问客户端私有成员。更关键的是 `AsyncBaseTransport.handle_async_request()` 只表示请求交给 transport/core，DNS 或连接建立仍可能在此之后失败，它也不能证明远端收到。因此 transport wrapper 既会增加私有耦合或改变代理行为，也不能满足“远端实际收到”的证明目标，不采用。

当前 observer 不篡改 SDK 默认 transport、proxy、TLS、连接池或重试。生产侧可可靠报告 SDK request attempt 和收到的 response；精确远端接收需服务端/网关接入日志或供应商侧请求 ID/日志，不能由客户端 pre-send hook 推断。

## 当前决策与下一步

按 provider 保留原生协议；Anthropic 继续 `client.messages.create` 与 `/v1/messages`，OpenAI Responses 仍只属于独立 OpenAI adapter。统一适配层的必要范围是内部调用/结果契约及各 provider 的定制字段映射，不是统一成 Responses 协议。

已完成逻辑调用 ID 到 SDK HTTP request attempt/response 的生产观察接入及本地真实 SDK/TCP 验收。全阶段覆盖仍未完成：普通 turn、持久 Worker、生成、规划、影响分析等 ID 传播要继续逐入口核对；生产日志无法单方面证明远端实际接收。真实发送端/远端接收需服务端或网关证据。本地 SDK/TCP 矩阵保留这种端到端对照。下一步核验并发/请求取消与日志投影中的 call ID 覆盖，再沿各业务逻辑入口检查持久调用标识是否稳定穿过 retry/recovery。

## 发送前失败出口补核

`ModelRuntimeService.invoke` 的模型缺失、参数不支持、认证失败、密钥缺失和运行参数漂移出口会在 provider SDK 之前结束。它们没有 SDK attempt，仍必须保留应用层 `logical_call_id`，否则规划、生成或影响任务的持久调用意图无法与最终失败结果对账。本轮补齐这些结果摘要和结构化日志字段；不把“没有 HTTP attempt”误报为 HTTP 失败，也不改变各 provider 的原生协议。

回归结果：`tests/test_model_clients.py tests/test_model_completion_http.py tests/test_model_sdk_fault_http.py tests/test_sdk_midstream_retry_boundary.py` **102通过**；发送前失败、请求映射和客户端回归 `tests/test_model_failure_lifecycle.py tests/test_model_request_profile.py tests/test_model_clients.py` **67通过**。持久需求影响、回执纠正和规划的真实存储测试在未设置对应 `RUN_LIVE_*` 门控时仍跳过，不能据此声称全阶段 retry/recovery 已验收。

## 持久规划 Worker 的实际恢复核验

启用隔离 MySQL/PostgreSQL/RustFS 和本地 HTTP provider 后，运行 `tests/test_planning_response_completion.py` 的三个真实场景：非流式截断、流式截断、流式缺少终态。每个场景的 PostgreSQL 计划保存两个不同的 `logical_call_id`，第二个响应已持久化但确认回执丢失；新 Worker 从 `phase=response` 读取原响应，模型调用数和 HTTP 请求数均不增加，终态重投也不增加调用。三场景实际测试通过；证据目录为 `p0-planning-completion-current/{native,stream,missing-terminal}`。同轮 `tests/test_document_impact_calls.py` 的 19 个真实存储/本地 HTTP 场景也通过。

这一步确认的是“已持久响应的规划恢复”调用身份和不重发边界。仍未证明：调用意图已保存但响应未持久化时的所有业务入口都能安全恢复、回执纠正请求发送后进程中断、供应商响应持久化后进程中断，以及默认模型按用例执行的完整闭环。默认 AI 真实质量失败仍停在 `produced` 规划阶段，不能用本轮受控 provider 结果替代。

## LangSmith 节点关联字段补齐（2026-10-19）

继续沿 `ModelRuntimeService.invoke → _send_with_client → LangSmithObservabilityAdapter.context_from_mapping` 核对持久调用身份。发现 `TraceContext` 已定义 `logical_call_id`，请求和 HTTP observer 也已携带该字段，但 `context_from_mapping()` 漏掉了映射；因此启用 LangSmith 时，模型节点 span 的业务元数据无法和同一调用的 SDK attempt、响应及结果回执关联。

本轮只补齐该字段映射，不改变模型协议、重试、超时或数据库边界。新增测试用真实 `LangSmithObservabilityAdapter` 与记录客户端验证：从模型请求映射出的 `TraceContext` 进入 turn/node trace 后，节点元数据保留同一个 `logical_call_id`。`tests/test_model_completion_http.py tests/test_model_sdk_fault_http.py tests/test_observability_contracts.py` 实际 **110通过、105.66秒**；`compileall` 和 `git diff --check` 通过。

当前仍只证明应用逻辑调用到 provider SDK attempt/response 及 LangSmith 节点元数据的关联；持久 Worker 所有入口、生产远端接收和默认 AI 按用例完整执行仍未完成。

## 持久规划 Worker 的 trace/attempt 传播补齐（2026-10-19）

继续审计 `PlanningTaskHandler` 的真实调用链：Worker 在 `bind_request_context()` 中已经有任务 `trace_id=task_id` 和租约 `attempt_id`，但 `CasePlanningWorkflow` 的持久分支此前只把 `logical_call_id` 放进 `ModelInvocationRequest.trace_context`。因此模型结果能保存逻辑调用身份，却不能把 HTTP observer 的请求尝试关联到具体规划任务和恢复尝试。

本轮从现有请求上下文读取真实 `trace_id/attempt_id`，并将它们与规划轮次 `turn_id`、项目和 `logical_call_id` 一起传给模型请求；没有伪造用户 Session，也没有改变模型协议、重试、租约或数据库边界。真实隔离 PostgreSQL/MySQL/RustFS Worker 恢复场景 `test_worker_restarts_after_saved_response_without_repeating_model_or_changing_versions` **1通过、9.16秒**：首次调用与恢复后的第二次调用均保留同一任务 `trace_id`，各自保留对应 Worker attempt 和规划轮次的 `logical_call_id`；全规划测试 `20通过、15跳过、5.12秒`。

这证明持久规划入口的任务/尝试/逻辑调用身份已贯通；仍不证明所有生成、影响分析、回执和默认按用例执行入口都具备同等传播，也不把客户端 attempt 当作远端实际接收证据。

## 模型适配层当前源码与真实 SDK HTTP 复核（2026-10-20）

针对“是否需要新增统一模型适配层”重新核对了当前实现，而不是依据 SDK 名称推断：

- 统一内部契约是 `ModelInvocationRequest` / `UnifiedMessage` / `ModelInvocationResult`；`resolve_client` 按明确 `transport` 选择 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 或 Gemini 原生客户端。OpenAI-compatible vendors 只在声明其 OpenAI-compatible transport 时使用 OpenAI SDK。
- `map_model_request` 按每个 MySQL 模型配置中的 `request_profile` 映射 JSON 输出、推理方式及 Chat token 字段；profile 未声明的必需字段在发送前拒绝，可选字段记录 fallback 决策。各 provider client 仍分别序列化 system/messages/tools/token 字段并解析其原生响应/流事件，避免把不同 wire protocol 混成一个。
- 另有可选 `LangChainModelAdapter`，运行时只在 `openai_chat_completions` transport 下启用；它复用同一 `map_model_request`，并有独立的 LangChain→OpenAI HTTP 夹具验证。Anthropic 两处仍使用 `client.messages.create`；Responses 只存在于 `OpenAIResponsesClient`。

当前源码复验使用官方 SDK 发往本地真实 TCP/HTTP fixture，直接检查收到的 endpoint 和 JSON 请求体，不只是调用映射函数：

| Agent_Server 命令 | 结果 | 实际覆盖 |
|---|---|---|
| `..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/test_model_clients.py tests/test_model_completion_http.py tests/test_model_request_profile.py tests/test_model_sdk_fault_http.py -q --tb=short` | **141 passed，126.37s** | transport 分派、四原生协议及 LangChain 请求/解析、真实 `/v1/messages` 与 `/v1/responses` 等路由、profile 字段映射、结构化输出、推理/token映射、429/503重试、403、连接/流中断、超时/取消及请求/响应观察 |
| `..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/test_model_stream_terminal_http.py tests/test_model_completion_contract.py -q --tb=short` | **45 passed，14.68s** | 流终态缺失、截断状态和各传输统一完成结果契约 |

结论：**模型适配层是必要的，但当前系统已经具备正确的分层形态；本次没有发现值得再造一层或切换统一协议的代码缺口。** 应保留统一内部 DTO、按 provider/transport 的原生客户端及逐模型显式 profile 映射。后来新增的供应商字段仍需有明确内部语义、profile 能力约束和对应 wire/结果契约测试；不得把未知字段透传到所有模型，也不得把 Anthropic Messages 改为 OpenAI Responses。

验证边界：这些功能验收使用锁定版本的官方 SDK 和本地受控 HTTP fixture，可证明 SDK 序列化、路由、解析及我们设置的错误边界；没有对所有线上供应商/模型账号逐一发送真实业务调用，不能据此宣称外部供应商兼容矩阵全部通过，也不替代 Step 默认模型完整按用例执行验收。

## 2026-10-20 并发首轮语义调用重复的真实 HTTP 复核

继续按上节计划沿入口查证，实际追踪 `SessionService.send_message → InputOrchestratorService.orchestrate_async → SemanticIntentService.enrich → ModelRuntimeService.invoke → SDK HTTP`。发现会话锁已经被另一条首轮占用，但持久 Session 状态尚未切到 `running` 时，第二条输入先执行锁外完整编排；该状态下 `_handle_busy_submission` 返回 `None`，之后取锁又从头编排。修复前确定性回归实际观察到第二条输入的模型运行时调用两次，属于应用重复调用，不是 SDK 对同一调用的重试。

生产修复将锁外预判限制为持久状态已是 `running`、`waiting_approval` 或 `interrupted` 的场景。状态非忙但锁已占用时只记录安全状态日志并等待，取锁后按最新 Session 编排一次。保留真实忙状态的原排队/拒绝策略。

修复回归在并发第二条抵达时暂停第一条语义调用，使用锁定的官方 OpenAI SDK 和本地 HTTP 服务端；修复后服务端核对两条不同输入各接收一次。把同一回归临时对照旧分支，实际 **1 failed、1 skipped、17 deselected**，本地服务端请求体依次为 first/second/second，确认旧分支对第二条输入实际多发了一次本地 HTTP 请求。恢复修复后组合回归 **31 passed、27 skipped，13.37秒**；跳过来自没有启用的 PostgreSQL fixture。此证据闭合一个已确认的应用级重复请求根因；它证明的是本地 HTTP fixture 收到的请求，不扩展为远端生产服务接收证据，也不把该错误误判成 SDK 重试。

新剩余项：SemanticIntentService 在 `InputOrchestratorService.orchestrate_async` 中先于 `ExecutionRequest.turn_id` 生成执行，因此该调用的 provider observation 没有稳定 `turn_id/trace_id` 与后续 Agent turn 汇合。需先设计不改变执行顺序与授权语义的关联字段，再覆盖意图识别、主图每轮模型调用、工具观察后的再推理、provider attempt/response 和 durable-worker 恢复。客户端 attempt 仍不等于远端已接收；本地 HTTP server 的计数只证明夹具实际收到。

## 2026-10-20 意图判定与 Agent turn 身份贯通、工具调用关联补齐

重新沿实际消费者核对上述缺口，采用“在意图判断之前创建 turn/trace，再把同一身份交给 Runtime”的方式，不重排意图判定/路由/权限流程。`InputOrchestratorService` 现在先生成或沿用 `turn_id/trace_id`，传入语义请求；`ExecutionRequest` 沿用相同身份；`SessionService` 的 durable task、待处理输入元数据、队列排空/恢复以及 `AgentTaskDispatcher` 保留该身份；`RuntimeService` 主图不再为同一请求另建 trace。每个 `model_invoker` admission 仍生成自己的 `logical_call_id`，provider observer 另计 SDK HTTP attempt。

消费端检查确认 `append_graph_event` 本来就为图事件统一加 turn/trace；工具事件以 `call_id` 标记执行。缺少的是响应侧 `tool_call_ids`，不是新的 trace ID。现在 `model.response_received` 加入模型响应实际返回的工具 ID，保持 `model.tool_calls_received`、具体 tool execution 和后续 tool-result message 使用的 ID 可连接。事件 `payload` 在 `ExecutionEvent` 中是通用字典，当前浏览器状态页仍按已有工具事件显示，因此无需新建 UI 或 API schema。

实际验证：

- `tests/test_semantic_intent_trace_http.py` 使用真实 OpenAI SDK 和本地 TCP/HTTP fixture；fixture 收到恰好一个 `/v1/chat/completions` POST。semantic-intent 开始/完成、intent classified、物理 HTTP attempt 使用相同 trace/turn/logical-call ID。该测试证明本地服务端收到一次请求，不证明外部 Step 在线可用。
- `tests/test_model_trace_project_binding.py` 运行两个模型节点调用：两个请求共享 trace/turn，有两个不同 logical-call ID；第一个响应公布 `trace-tool-call-1`，下一模型输入的 tool message 引用该 ID，第二响应公布自己的 ID。测试也覆盖恢复状态的初始 trace。
- `tests/test_runtime_interrupt_tool_calls.py` 核对实际工具执行上下文收到稳定 trace/turn；事件原有 call IDs 在中断、补齐 tool protocol 和持久快照中保持一致。
- 最终回归命令覆盖上述文件、意图安全、忙会话并发、会话执行所有权、Agent task dispatch、场景知识与 opt-in live-test 收集：**154 passed、47 skipped、30.67s**。跳过包含未开启的 PostgreSQL 与真实模型门控，按未执行记账。

另在默认 Step 当前认证配置下，真实运行 opt-in `test_live_model_tool_stream.py::test_current_model_executes_read_only_tool_and_stream_matches_final_transcript`。由于该测试之前只在成功时保存摘要，本轮将其加强为无正文/无密钥的阶段轨迹证据：只保存事件类型、turn/trace/logical-call/tool-call IDs、HTTP attempt 数和状态码。最新隔离会话结果在 `p0-live-model-trace-auth-current-20261020-143020/acceptance.json`：`step-5-preview` 一次逻辑调用、一次 HTTP attempt，收到400后 `interrupted`；没有响应完成、工具调用或 post-tool reasoning。测试失败不是认证未通过：相同默认凭据的模型目录查询已经成功；此前最小 chat 请求的供应商错误分类为订阅/计划 entitlement。也不能因 API 调用了就说远端成功执行了模型内容，只有 HTTP 错误响应被实际收到。

当前结论：SemanticIntent 至主 Agent turn 的 IDs 已贯通；模型响应到实际工具事件的 tool-call ID 可对账；本地 SDK/HTTP 与 deterministic test 支持这些实现。Step 当前状态应表述为“凭据认证已确认，生成 API 当前仍返回400 entitlement，真实 Agent loop未启动”。尚未证明默认 Step 正常返回工具调用后的完整链路；持久 Worker 的其他业务入口、生成/规划/影响调用各自的 attempt/recovery、断网和进程故障仍需单独验收。客户端 attempt 与远端收到的请求保持区分；生产端远端接收仍需网关或提供商证据。

## 2026-10-20 持久生成 Worker 的模型调用 trace/attempt 补齐

按上一节的持久入口清单继续检查 `DocumentOperationTaskHandler → GenerationTaskService.execute → ModelTestCaseGenerator.invoke_frozen → ModelRuntimeService.invoke → provider HTTP client`。Worker 已绑定 `trace_id=task_id` 和真实租约 `attempt_id`；生成服务也已把任务、模块和 attempt 编入 `logical_call_id`。但 `invoke_frozen()` 原先只向冻结请求补 `logical_call_id`，因此 SDK HTTP 观察记录的 `trace_id` 为空，物理请求记录也没有显式 `attempt_id`。

使用实际 Redis Worker、真实 MySQL/PostgreSQL/RustFS、Markdown 解析任务和本地 TCP provider 先复现：两模块都真实发送并提交草稿，但新增断言失败，HTTP request observer 的 `trace_id` 为空。修复只从持久生成任务已有身份传入 `trace_id=task_id`、`project_id` 和当前 claim 的 `attempt_id`；`invoke_frozen()` 在冻结请求副本上合并这组调用期元数据。Provider observer 将 `attempt_id` 与已有 trace、logical call、transport、attempt 序号同时写入 request-start/response 结构化日志。没有为后台生成捏造用户 session/turn，也没有改变请求字段、provider 协议、重试策略、生成检查点或数据库 schema。

验证：

| Agent_Server 命令 | 结果 | 证明范围 |
|---|---|---|
| `$env:RUN_LIVE_DOCUMENT_IMPACT_TASKS='1'; $env:PYTHONPATH='.'; uv run --project . python -m pytest -q tests/test_generation_tasks.py::test_actual_redis_worker_updates_and_creates_drafts_with_atomic_receipts[md]` | 修复前新增断言复现 trace 丢失；修复后 **1 passed，21.73s** | 增量生成模块 Worker 的共享 `GenerationTaskService.execute → invoke_frozen` 路径：实际 Redis 领取两个模块，实际 MySQL 配置/PG 任务回执/RustFS 范围与响应对象、官方 OpenAI SDK 到本地 TCP provider 的两个请求；每条模型逻辑调用与 request/response HTTP observation 共享任务 trace 和 claim attempt。终态重投没有新增请求。单独的普通文档默认 Step 验收另行记录。 |
| `$env:PYTHONPATH='.'; uv run --project . python -m pytest -q tests/test_model_sdk_fault_http.py -k "real_sdk_faults and unavailable"` | **10 passed，23.91s** | 五种 provider/adapter 路径的真实 SDK/TCP 503 场景；SDK 实际 retry 的路径中，同一逻辑调用下各物理 request 与 response 按 attempt 序号携带相同 trace/attempt。Google 的原生重试行为保持其既有测试预期。 |
| `$env:PYTHONPATH='.'; uv run --project . python -m pytest -q tests/test_document_generation_stream.py tests/test_generation_json_output_contract.py tests/test_model_clients.py` | **27 passed，11.24s** | 冻结请求、流式及 JSON 生成契约、provider 客户端回归。 |

`git diff --check` 通过。上述生成 Worker 测试使用受控本地 provider，不证明本轮默认 Step 的联网调用或生成质量；默认模型的上一次真实三模块验收见 `P1_DEFAULT_GENERATION_RESEARCH.md`，当前用户确认账号已认证后仍需独立重跑默认 Agent/API 场景。当前本地 TCP 服务端实际接收数量可核对 fixture 请求；生产 HTTP hook 仍只表示 SDK 请求尝试，不能代替供应商侧收到请求的证据。

此处闭合的是持久生成 Worker 共用模型调用入口的 trace 传播，并用增量任务实际链路验收。普通文档默认模型完整生成、影响分析 Worker、回执纠正、其它生成恢复场景以及断网/进程中断仍需逐项审计；全阶段调用追踪和 P0—P5 仍未完成。

## 2026-10-20 默认 Step 实时工具/SSE复验

用户确认 Step 账号已认证后，重新执行 opt-in `tests/test_live_model_tool_stream.py::test_current_model_executes_read_only_tool_and_stream_matches_final_transcript`。本轮从 MySQL 读取当前默认模型 `step-5-preview`，用合成只读提示创建隔离内存会话并经真实 SDK 发起一次请求。结果为 **1 failed，13.20s**：收到一个 HTTP 400；Session 终态为 `interrupted`，事件只有一个 `model.request_prepared` 和一个 `model.invocation_failed`，没有模型响应、工具调用、工具执行或 post-tool reasoning。供应商错误正文未保存在脱敏 acceptance 文件中，因此仅凭本轮证据不能断言 400 的具体原因；旧的 14:30 记录有订阅/计划 entitlement 分类，但本轮只确认相同状态码仍出现。

脱敏轨迹：`p0-live-model-tool-auth-followup-20261020/acceptance.json`；JUnit：`p0-live-model-tool-auth-followup-20261020.xml`。该测试的入口是 `SessionService` compatibility 路径，Session storage 为隔离内存；不覆盖 durable dispatcher、浏览器产品流程或默认模型成功调用。当前状态应分别记录为：**账号认证由用户确认；本轮模型请求确实发出并收到 400；真实默认模型工具闭环尚未通过。** 同一请求未重复重跑。

## Step账号认证确认后的复验与400根因（2026-10-20）

用户确认 Step 账号已完成认证后，重新执行同一隔离默认模型工具/SSE验收：`p0-live-step-post-auth-20261020-151801/acceptance.json`，JUnit 为同名 XML。仍是一次逻辑调用、一次 HTTP attempt，HTTP 400，Session `interrupted`；没有响应、工具调用或 SSE 最终回答。此证据不表示认证失败，也不证明工具链执行失败，模型尚未返回到工具阶段。

为避免仅凭状态码推断，随后用 `ModelRuntimeService.invoke` 对 MySQL 中当前默认配置做一次无工具、单条短消息、`max_retries=0` 的诊断调用，并只持久化脱敏错误字段。记录在 `p0-step-postauth-probe-20261020-152146/diagnostic.json`：`step-5-preview` 使用 `provider=openai`、`transport=openai_chat_completions`，请求发往 `api.stepfun.com`；MySQL 中有凭据配置，当前实际选择 native OpenAI SDK（LangChain 适配开关关闭），未配置 `request_profile`。SDK收到一次 HTTP 400，供应商 JSON 错误 `type=request_params_invalid`，错误消息为 `you have no active step plan subscription`。诊断未保存 API key、请求头、原始错误正文或模型回复。

官方 Step 5 文档的快速开始使用 `https://api.stepfun.com/v1` 和 `client.chat.completions.create`；Step 5 工具调用 Cookbook 也给出 `tools`、`tool_choice` 和 `max_tokens` 的 Chat Completions 流程（https://platform.stepfun.com/cookbook/step-5-tool-calling）。因此现有 Step transport 与官方公开协议形态一致；当前明确失败原因为供应商报告没有 active Step Plan subscription。**不改全局模型配置、不把 Step 转为 Responses，也不把认证失败写入产品错误分类。** Step 的真实默认 Agent/SSE和P2闭环需在 API 套餐状态恢复后重测；此前已完成的确定性、隔离存储和本地 HTTP 测试仍按各自范围记账。
