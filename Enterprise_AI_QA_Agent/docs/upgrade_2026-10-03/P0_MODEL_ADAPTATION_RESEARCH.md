# 多模型适配：依据、复现与实施边界

基线 `3a11b05b`。原目标附件已经完整读取。用户要求保留多模型接入，依据实际实现判断是否需要适配层。

## 现有事实 → 结论

- `ModelInvocationRequest/Result`、`UnifiedMessage/ContentPart` 已统一内部请求和返回；`model_clients.registry` 按传输分发 Chat Completions、Responses、Anthropic Messages、Google Gemini。各客户端已有消息、工具名、输入/输出上限、流式和错误映射。`model_adapters` 已有 LangChain 与旧客户端桥接。应补现有边界，不新建第二套模型框架。
- OpenAI SDK 只处理其协议，不替 Anthropic/Google 或兼容网关解释业务终态。项目 `context/token_usage.py` 已统一三种 usage 键，不能重复创建统计层。
- `planning_workflow`、`generation_pipeline`、`impact_calls` 只识别小写 length/max_tokens；Google 客户端返回 MAX_TOKENS，Responses 返回 incomplete + max_output_tokens。正式回执已经识别其中多数，但仍有自己的集合。重复终态判断导致切换模型可能把非完整响应接受为合法业务结果。
- MySQL 模型库 `_DEFAULT_CAPABILITIES` 将所有能力默认启用；因此 capability=true 目前不能单独证明模型支持 JSON、推理、文件或并行调用。直接全局发送 `response_format` 会产生兼容性风险。刚才未提交的自动 JSON Mode 改动已撤回；34通过/7门控跳过仅证明该试验局部契约，不证明多模型或默认 Step 完整执行。
- 原生 Chat 客户端使用 max_tokens，LangChain 构造使用 max_completion_tokens；实际供应商兼容性需独立查证，不能仅由 SDK 接受参数证明服务端支持。reasoning_effort/thinking、结构化输出及多模态内容也不得统一原样透传。

## 官方依据

- Google GenerateContent：`finishReason=MAX_TOKENS` 表示达到输出上限，`STOP` 为正常结束。https://ai.google.dev/api/generate-content
- OpenAI Responses：`status=incomplete` 与 `incomplete_details.reason=max_output_tokens` 不能作为完成结果。https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses
- Anthropic：`end_turn`、`max_tokens`、`tool_use`、`pause_turn`、`model_context_window_exceeded` 含义不同。https://platform.claude.com/docs/en/api/typescript/messages
- Step JSON Mode 要求显式 response_format，且仍需检查 stop/length。https://platform.stepfun.com/docs/guide/json_mode （搜索抓取可读，直接打开404；不声称当前所有 Step 模型都支持）。
- 本地《Agent轨迹与协议鲁棒性》要求核对轨迹、协议异常、成本及恢复；兼容映射不能把未知结果变成成功。

## 本步可审查方案

先用本地真实 HTTP + 已安装官方 SDK，让四种传输及 LangChain 接收同一内部输入，记录实际出线字段和完整/截断响应。再把真实已返回的正文/终态交给现有持久规划校验，证明差异确实影响业务。

确认后沿现有模型契约集中解释已知终态；保留原 finish_reason/stop_reason/usage/raw_response，不重写历史。规划、生成、影响和回执复用同一解释；已知非完整响应不得变成草稿/计划/通过，不自动加预算或重发。未知终态保持未知，不臆造完整。

这一步不发送提供商专有可选字段，不改变 MySQL 全局能力默认、不升级依赖、无DDL。后续需要独立的配置驱动请求适配矩阵：内部语义（输出格式、推理要求、媒体类型）→ 已验证传输/模型映射；映射版本与配置冻结到PG，原件RustFS。必需而不支持的能力明确错误；可选能力退回原提示词方式并记录原因。不得靠模型名字包含step/openai等猜协议，不以 HTTP400 后换字段盲重发掩盖未知结果。

默认 Step 完整试验 `p2-default-coverage-current` 已确认2次HTTP200，但旧单阶段策略第二轮正文0且180秒截止；未创建Run/业务HTTP0。该失败保留，不能作为最新分阶段策略或 JSON Mode 的反证，也不能声称根因已修。

## 复现 → 根因修复 → 实际验证

同一内部输入经实际 SDK、本地 HTTP 和现有 ModelRuntimeService，覆盖四种传输、LangChain 桥接、同步/流式、完整/截断，共20个协议场景。服务端捕获实际请求字节与响应，随后把 SDK 返回正文和终态交给保存响应阶段的现有规划工作流；终态重读没有调用新模型。

| 传输 | 实际输出上限字段 | 原终态 | 统一解释 |
|---|---|---|---|
| 原生 Chat Completions | max_tokens | stop / length | complete / truncated |
| LangChain Chat | max_completion_tokens | stop / length | complete / truncated |
| Responses | max_output_tokens | completed / incomplete + max_output_tokens | complete / truncated |
| Anthropic Messages | max_tokens | end_turn / max_tokens | complete / truncated |
| Google Gemini | generationConfig.maxOutputTokens | STOP / MAX_TOKENS | complete / truncated |

`before` 6失败14通过，其中2个是 HTTP 夹具漏写 Anthropic SSE event 名；修正受控服务后 `before2` 4失败16通过，四项全部是 Responses/Gemini 同步及流式截断结果被业务规划接受。真实原件保留，未靠自造返回值绕过 SDK。

沿现有 ProviderClient 的 finish/stop 契约增加 `model_clients/completion.py`，统一解释 complete、tool_calls、truncated、incomplete、blocked、failed、unknown。运行服务及两种桥接返回追加 completion_status；规划、生成、影响、正式判定和纠正复用解释。业务核验仍从原 finish/stop 推导，不信任外部提供的 completion_status。已知不完整不能成为草稿、有效计划或通过回执，不自动重发。旧缺字段保持 unknown 与原兼容行为；unknown 不能宣称已证明完整。原供应商字段、响应、历史哈希保持。

进一步核对既有 normalize_usage 后，实际 Anthropic SDK 流式两项失败：首块 input_tokens=7，最终 MessageDeltaUsage 把未提供的可选字段展开为 None，原浅合并覆盖了首块事实。检查已安装 SDK 类型及真实 HTTP 原件后，仅忽略增量中的 None，显式0仍保留。`usage-before` 两失败原件保留；新增实际 SDK 显式0场景验证最终输入0、输出3、合计3。

| 批次 | 实际结果 | 范围与限制 |
|---|---|---|
| p0-model-completion-verified | 20通过，20.50秒 | 首轮终态修复，尚未扩大 usage 断言 |
| p0-model-completion-regression | 158通过、48门控跳过，44.45秒 | 十模块；默认/外部 live 门控未启用 |
| p0-model-completion-live | 8通过，65.31秒 | 真实隔离存储；规划恢复原件已导出，生成/影响首次导出环境名错误，仅保存日志/XML |
| p0-model-completion-storage-final | 6通过，53.84秒 | 正确导出生成固定依据与五项影响失败/原件；真实 PG/RustFS/MySQL、实际 SDK/受控 HTTP |
| p0-model-completion-final | 2失败75通过，20.42秒 | 扩大 usage 后发现 Anthropic 输入计数丢失，保留失败 |
| p0-model-completion-delivery | 78通过，25.65秒 | 20协议场景、显式0、30真实样本派生合同及既有客户端/桥接测试；最终两处修复已验 |

两项规划恢复使用真实 PG，响应提交后模拟确认丢失；恢复沿保存响应和原截止时间，终态重投没有第三次调用。生成守卫实际调用一次，来源/配置/请求损坏及保存的截断样本检查零新增发送；该用例不提交草稿，不等于完整生成产品验收。五项影响分别保留截断、空、工具、HTTP失败、超大结果；HTTP500在一次逻辑调用内 SDK 实际尝试3次，其余1次，不能写成所有场景零重试。协议矩阵使用受控计数7/3，不代表真实供应商计费。最终 compileall 退出0。

## 后续请求适配的工程方案〔尚未实施〕

1. MySQL 中为现有模型配置补显式、版本化的能力与请求映射。按正式 provider/transport、登记的具体模型兼容依据配置；字段默认不发送，不能将当前全 true 能力当作验证结果。
2. 在现有 ModelInvocationRequest 和 UnifiedMessage 中表达输出格式、推理要求、工具及媒体语义。已有 clients/adapters 各自转换：Chat 的 response_format、Responses 的 text.format、Anthropic/Google 的各自协议，逐项查证后登记；具体模型的 token 参数差异也进入映射。此表是待查证任务，不是宣称四家参数已实现。
3. 必需能力无已验证映射时返回具体不支持原因；可选能力可采用原提示词策略并记录决策。预算、超时和业务失败语义沿原策略，不用换字段/加预算盲重发。
4. 提交任务时在 PG 固定 profile版本/哈希、运行适配器/SDK、预算及实际参数；请求响应原件入 RustFS。新增字段对旧快照默认省略，旧冻结哈希及失败结果不改写。
5. 配置库、Schema/API、前端编辑与展示、各适配器及任务消费方同步。先实际 HTTP 协议矩阵，再选已授权真实模型做服务端验收；结构化内容还须业务 Schema 核验，不能把 JSON 可解析当作业务正确。

本批没有配置/表迁移、依赖或前端变更，也没有完成上述请求 profile、JSON/推理/媒体能力治理。没有多家真实外部模型或默认 Agent/App 完整成功证据。默认 Step 的旧单阶段失败继续跟踪；人工 DeepSeek、录制业务管理/清洗遗漏、API续期、性能容量/AI流式、安全其他工具、准备/未知副作用/计划修订、需求业务质量与生产历史迁移仍未完成。
