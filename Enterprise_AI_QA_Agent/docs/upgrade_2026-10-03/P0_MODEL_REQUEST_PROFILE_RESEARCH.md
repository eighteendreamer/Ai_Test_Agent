# 配置驱动的模型请求适配

基线 `06a66a17`。沿现有 ModelInvocationRequest、四个 ProviderClient 和 LangChainModelAdapter 扩展，不另建模型调用框架。

## 依据 → 做法

- MySQLModelConfigStore 已使用 nullable JSON 与幂等列迁移；同样增加 request_profile。配置内容和 revision 随现有 model_invocation_parameters 固定到任务，不写业务数据到 MySQL。不修改旧配置和旧冻结哈希。
- 原生 Chat `_build_params` 用 max_tokens；LangChain 已安装源码 `_get_request_payload` 会重命名成 max_completion_tokens。显式要求 max_tokens 时选现有原生客户端，运行参数冻结实际适配器选择；不 monkeypatch SDK。
- [OpenAI 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)：Chat response_format 与 Responses text.format 分别转换。JSON mode 不证明业务 Schema 正确，继续既有业务校验。
- [Anthropic 结构化输出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)：output_config.format 的 JSON Schema 映射；[扩展推理](https://platform.claude.com/docs/en/build-with-claude/extended-thinking) 的 thinking 预算，启用时至少1024且小于输出上限。
- [Google 官方 Python SDK](https://googleapis.github.io/python-genai/#json-response-schema)：generate_content 的 response_mime_type/response_json_schema 与 thinking_config。不能复制 Interactions 的另一套协议。
- 本地《Agent轨迹与协议鲁棒性》与《测试数据合成与用例自动生成》：固定配置/请求/来源、记录协议失败；原始发送字段经实际 SDK/本地 HTTP 检查，不将 DTO 测试当作服务端能力证明。

## 本轮实施边界

内部增加可选输出格式（JSON object/Schema）和推理语义（effort 或预算）。严格的 request_profile 只声明允许的格式、推理种类/范围、默认推理和 Chat token 字段，无供应商任意 extra_body。映射仅在明确声明时启用；必需选项不支持则发送前返回可检索失败，可选选项退回原提示词并记录原因。Schema 保留原文，不为兼容任意删字段。跨模型媒体、自动能力探测与更多推理协议另行验证。

新字段未设置时序列化省略，保留旧任务请求哈希。配置接口遗漏 profile 保留原值，显式 null 清除。前端编辑、公开 DTO、MySQL 重载、模型参数冻结同步更新。全量生成首先携带既有业务 Schema；其他业务调用逐步接入并如实标记范围。

先验证四种传输和 LangChain 的实际 HTTP 字段、无配置兼容、必需不支持零发送、可选降级和 HTTP400 不盲重发，再验证隔离 MySQL 保存/遗漏/清除/重启与任务配置变化拒绝。真实外部供应商和默认 Agent/App 完整验收仍是独立项目。

## 实际实施与验证

MySQL增加nullable request_profile JSON，公开/更新DTO、API错误返回、设置页面JSON编辑及三种语言同步。遗漏字段保留、显式null清除，未知字段拒绝；传输与默认预算不兼容不能提交。旧行不回填，既有全true能力保留兼容，不能替代新声明。revision与完整配置/哈希、mapping版本进入原任务固定参数；新request的格式/推理内容随原请求固定，旧None字段省略。生成携带既有Schema，规划/影响携带可选JSON object，服务端业务校验仍负责完整性。

| 内部语义 | Chat | Responses | Anthropic manual | Gemini generateContent |
|---|---|---|---|---|
| JSON | response_format | text.format | output_config.format，仅Schema | response_mime_type及response_json_schema |
| 推理 | reasoning_effort | reasoning.effort | thinking.enabled/disabled及budget_tokens | thinking_config.thinking_budget |
| 输出上限 | 明确max_tokens或max_completion_tokens | max_output_tokens | max_tokens | max_output_tokens→maxOutputTokens |

LangChain强制改写max_tokens时选既有原生Chat；独立LangChain harness显式拒绝此配置，支持的默认推理随导出模型发送。正常调用在发送前核验配置/运行冻结，并保存映射/可选降级原因；unsupported_request沿已有失败生命周期，不能变成已完成回合。

Google首轮4项断言失败：实际SDK models.py直接传ThinkingConfig，convert_to_dict使用snake_case，出线为thinkingConfig.thinking_budget。[ProtoJSON规范](https://protobuf.dev/programming-guides/json/)允许原proto字段名；改的是测试断言，没有为SDK造补丁。另1项为空contents夹具错误。首轮30通过5失败、修正后35通过；该事实不证明真实Google模型支持预算。

扩大回归最初229通过/2失败/41门控跳过：适配器选择新函数漏保留原getattr默认，修复后234通过/41门控跳过。实际App最初已完成编辑/重载，但列表断言误用items封装，失败78.63秒；按真实list契约修正后完整1通过88.63秒。存储最初生成与RustFS重读已通过，但夹具重复传generation_metadata，修正后1通过17.84秒，最终补验1通过10.83秒。所有失败独立保留，夹具错误不能标成产品根因。

追加工具组合发现实际LangChain错误：response_format顶层触发SDK parse/stream辅助方法，原工具非strict被拒绝，0HTTP。源码base.py对应分支和OpenAI _base_client.py额外请求体合并已经查证；本适配器使用已支持的extra_body发送唯一允许的response_format，实际线上仍是标准顶层字段，无任意配置透传，不改变工具Schema或调用权限。native/桥接同步和流式组合均补验，原wire-final38通过1失败、tools-before1通过1失败保存；修复后最终wire-delivery **63通过27.58秒**，其中新请求矩阵41项、既有适配器和失败生命周期22项。HTTP400保持1次发送；正常工具声明保留，未宣称实际执行业务工具。

完整App验证真实MySQL旧表新增列/重复初始化、编辑错误反馈、保存/页面刷新、API重启、遗漏保留/null清除、配置变更拒绝；独立production API、built App/Chromium使用owned数据库和缓存Redis容器。没有替换生产容器组件或调用外部模型。前端3项回归通过，实际build含vue-tsc及Vite通过，页面错误0。

PG/RustFS专项创建新Markdown→生成带完整八步/404负向/0/false/null扩展的JSON→正式草稿入PG→重新读取；source_refs与固定请求/映射记录。真实SDK受控HTTP1次；PG规划seed固定profile1，MySQL改成2后原生成/规划发送均拒绝，新增HTTP0，原seed不变。RustFS请求与响应重读逐字节一致；原上轮生成scope快照原哈希仍能校验。这个专项未执行用例、评审启用或默认AI质量验收。

仍未证明：多家外部模型服务端能力、默认Agent完整流程、跨模型文件/媒体、Google thinking_level、Anthropic adaptive/effort、独立invoke_structured路径、自动能力发现和兼容依据的评审治理、生产历史迁移及并发启动DDL。初始化迁移沿既有方式需要ALTER权限。详见本批validation与方案7.13，完整P0—P5继续未完成。
