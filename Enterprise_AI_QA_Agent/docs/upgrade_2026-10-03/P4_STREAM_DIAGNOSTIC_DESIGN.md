# 文档影响流式诊断与任务划分研究

研究基线：f8d2f34e，2026-10-05。完整P0—P5尚未完成。

## 实际依据 → 本步做法

- `impact_workflow.py`逐个调用全部原文段、raw/normalized/section/table差异，再判断用例；这些是完整证据身份，不等于必须分别推理的业务任务。v4实测在发现阶段耗尽600秒，三个用例未发送，保留5unknown/6unprocessed。
- `impact_calls.py:invoke`直接调用ModelRuntimeService.invoke，没有安装stream_handler。`model_runtime_service.py:stream_handler/_send_with_client`已经支持按调用上下文切换到现有Provider.stream。
- `planning_workflow.py`先保存调用意图，再依据冻结模型capabilities.streaming安装observe_delta，记录first_text_seconds和收到的字符数。采用现有能力进行诊断，不引入模型SDK、全局配置或新提供商。
- `openai_client.py:stream`设置stream/include_usage，累积文本、finish_reason和usage；只收到文本不代表收到完整响应。普通send耗时与流式响应头耗时不能直接比较。流式诊断同时记录响应头耗时、首次文本、完整耗时、原始delta及最终结果。
- 本地`Agent轨迹与协议鲁棒性.html`强调最终结果、调用轨迹、协议异常、超时和恢复共同验收。本步沿用实际文件/MySQL/PG/RustFS及发送权，不重发旧任务、不修改原失败。

## 实验范围

新建与业务基线相同的Markdown V1/V2及三个完整8步JSON，使用新任务、既有原子调用意图与v4协议；一个声明差异单元附带完整两份原文、全部差异及全部用例，验证既有流式接口能否完成这种实际输入。保留所有JSON字段、说明、False/0/null及来源，不做摘要替代。

在诊断测试中为本次调用安装既有stream_handler，源模型、SDK60秒、输出8192及单调用120秒不变，隔离项目总期限仍600秒。这个诊断是一个完整依据单元，不是自动全范围工作流，不把同一次输入与传输调整解读为超时根因证明。只对新的诊断调用保存结果，不能把它补写为旧unknown的回执。

记录实际SDK客户端send的原始消息与状态、完整模型delta、第一次文字延迟、总耗时、PG调用/核验及RustFS原字节。没有收到完整结果时保留unknown/原delta；结果解析通过仍semantic pending_review/deployment unknown，须另核对业务基线。测试失败不能以文本存在代替完整JSON或事实核验。

## 后续实施判断

流式诊断若通过，只证明当前提供商和实际输入的既有流式能力。正式任务若采用流式，须在新任务冻结传输选择、保留旧任务原选择，并覆盖受控SSE、断流/取消/缺finish/无usage、两适配器、预算/lease、存储失败和独立Worker。不能从全局stream_handler偷偷改变历史调用方式。

任务聚合须单独设计新协调版本：冻结完整证据清单与可验证覆盖映射，再划分规则发现及完整用例判断；旧coordinator-v1结果/请求继续原规则读取。不能直接跳过重复外观的差异、给所有原文段复制同一候选或把业务无规则当作范围已分析。阶段预算也须独立验证，不能只增大超时。

## 首次实际结果与路线调整

首次完整依据流式诊断1失败/0跳过127.79秒。一次物理POST，56250字节，stream/include_usage；HTTP200响应头2.218秒。单调用120.094秒结束，没有任何文本delta，保存dispatch_TimeoutError/outcome_unknown；没有模型响应、合法候选或业务通过结论。原件、固定seed/input/request、PG调用及RustFS对象保留在p4-default-model-stream-diagnostic-first。源默认配置前后相同。这个实验没有采集SSE原字节，不能断言没有网络数据或没有推理片段。

因此不把开启流式实施为正式修复。下一实验以新的任务拆开发现与用例判断：发现只读取完整新旧原文和全部差异；合法规则存在后逐个读取完整固定用例及原文规则，不在发现阶段要求同时完成三个用例的判断。采用当前已存在的discover_rules/case_impacts上下文和规则回执传递。该诊断仍不是自动全范围产品流程，结果只归属新诊断任务。

本地SDK `openai._streaming.AsyncStream._iter_events`实际读取response.aiter_bytes，再交SSEDecoder；据此在诊断send回调中原样转发这些字节，同时保存原始.sse和片段大小/哈希/时间。只保存body，不保存端点、请求头或凭据。不改SDK、超时/重试、全局模型配置；SDK文本回调与原始网络片段分别计数。

第二次诊断1失败/0跳过117.63秒。发现调用111.375秒，实际SSE1460607字节/3116个JSON事件；reasoning与reasoning_content各29772字符，是同一内容的两种字段，不能相加为推理量。正文0字符，最后finish_reason=length、completion_tokens=8192。提供商reasoning_tokens=0与实际reasoning片段不一致，保留原usage，不推导可信推理token计量。PG保存response_saved/truncated，核验ineligible；没有规则，三个用例未发送。证据在p4-default-model-stream-stages-first。

## 输出预算消耗的官方查证与新实验

源码全局搜索没有reasoning_effort的业务配置或客户端处理；当前OpenAIChatClient只传既有max_tokens等参数。实际默认模型配置provider=openai、model_id=step-5-preview，不能因该标签推定它遵守OpenAI模型全部机制。

[Step 5官方模型文档](https://platform.stepfun.ai/docs/en/guides/models/step-5-preview)明确Chat Completions使用reasoning_effort，支持low/medium/high；[官方Chat Completions参数](https://platform.stepfun.ai/docs/en/api-reference/chat/chat-completion-create)说明该字段控制推理深度。安装的OpenAI3.14.0 AsyncCompletions.create实际签名也包含reasoning_effort。最初官方中文主页抓取超时；由官方英文主页API链接取得正文，不以第三方参数表作为采用依据。

采用做法仅为新隔离诊断：将low保存到隔离项目MySQL qa.impact_diagnostic_settings，读出并记录修订；在测试作用域的既有_build_params增加这个已查证参数，仍由实际SDK/提供商发送。完整消息/模型/8192输出/SDK60秒/单调用120秒不变。正式生产调用契约尚未包含该参数，PG seed不伪称已经冻结；实际wire额外参数和诊断配置单独保存，源默认模型不改。这个试验用于核验当前实际网关是否支持参数及事实结果，不能直接标记正式产品修复。

若取得合法判断，再设计正式的配置、请求参数冻结和所有适配器契约同步；不以缩减输出预算、提高全局token限制、重发旧unknown或绕过引用核验作为成功条件。

交付字节检查中，Git将两个原始SSE的事件终止空行报为blank-at-eof。保留原SSE字节，在现有.gitattributes仅对本次stream目录下的.sse允许该协议终止空行；不删除原始事件分隔符。最初链接检查未处理:line后缀、密钥前缀误匹配旧task标签内部字母，也分别按实际链接语义和前缀边界纠正；原文件和失败记录不改。
