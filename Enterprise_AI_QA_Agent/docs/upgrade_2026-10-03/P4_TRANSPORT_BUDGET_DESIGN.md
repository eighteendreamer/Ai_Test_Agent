# 文档影响调用的传输预算与单次发送

研究基线845287bb，完整P0—P5继续保持未完成。

## 依据来源 → 采用做法

- p4-default-model-grouped-v6-contract/physical-http.json实际发现请求60.156秒ReadTimeout，随后相同请求重试59.016秒，被120秒逻辑期限取消；4逻辑/6物理发送，8unknown/3依据不足。分组成功不能消除这类传输重复。
- impact_calls.invoke已冻结call_timeout_seconds和ModelRuntimeService运行参数，并有意图、unknown与终态恢复。逻辑期限不能只用于取消整个包含SDK重试的操作；增加项目可选single_attempt，让一次请求使用既有逻辑期限、SDK max_retries=0，任务总期限仍为外层约束。不是增大全局模型输出或任务超时。
- model_runtime_service.invoke每次创建原生客户端/适配器，registry及OpenAIChatClient/OpenAIResponsesClient/AnthropicMessagesClient、LangChainModelAdapter已有timeout_seconds/max_retries构造参数。沿用同一工厂传递不可变的非敏感transport options；Google客户端没有已核验的同等重试控制，本期不冒称支持single_attempt，提交冻结阶段明确拒绝该组合。默认provider_defaults及其他调用保持原行为。
- 安装OpenAI3.14.0的AsyncOpenAI实际签名包含timeout、max_retries=2；安装ChatOpenAI字段含request_timeout与max_retries。已核对[官方SDK重试/超时](https://github.com/openai/openai-python#retries)与[LangChain官方初始化](https://docs.langchain.com/oss/python/integrations/chat/openai)。当前上游文档可能采用比本地更新的HTTP底层实现，因此调用参数以已安装签名和项目既有构造方式为准，不升级依赖。
- ModelInvocationRequest及固定请求hash保持原格式，transport options通过运行参数与任务调用策略冻结，不放进模型消息。新增可选keyword给共用Runtime，默认不传，原调用消费者保留。
- 原生Runtime将供应商错误转换成http_error；若没有HTTP状态，不能证明供应商拒绝且未执行。single_attempt新任务对无状态传输失败保留unknown；收到实际HTTP500仍provider_failure。历史已保存回执不重新分类。
- 本地Agent轨迹与协议鲁棒性课程要求验证轨迹、重试/超时及恢复。验收使用实际本地HTTP慢响应、HTTP500、断连接、取消/租约以及真实文件/配置/PG/RustFS，再验生产API/Worker、前端配置和真实默认模型完整3条用例，不能用构造参数单测替代发送次数/业务正确性。

## 采用边界

扩展现有MySQL document.impact_invocation_settings的transport_policy，缺失按provider_defaults兼容。single_attempt由既有call_timeout_seconds派生SDK timeout，max_retries=0；不增加另一份独立超时配置。提交冻结策略及Runtime中的有效options，准备/发送/恢复核对原参数。项目界面可选，任务结果说明实际策略。全局模型、Prompt/Skill、输出8192及其他测试模式不随此设置改变。

仅关闭SDK重试不保证网关或上游内部没有重试；目标是在本系统可观测的SDK发送层核验。逻辑调用仍可能主动补读，此类有不同固定请求及父调用身份，不按正文相同去重。未取得完整响应保持unknown，不从收到响应头或文本片段推断完成，不自动重发历史unknown。

## 实测结果与下一步边界

本轮新增17实际功能、最终九模块156、独立生产5、完整App1均通过；模型原回归26通过，前端24通过。第一次构建TS2339为测试Element.value类型缺失，改HTMLSelectElement后vue-tsc/Vite通过，原失败保留。三种原生构造参数已验，但实际慢响应/错误HTTP及完整文件流程仅验证Chat原生/LangChain两种，不声称Responses/Anthropic实际发送已验。

默认模型完整断言仍失败：468.08秒，5逻辑/5物理，SDK实际120秒、max_retries冻结0。发现73.063秒成功三规则/八声明；一用例引用content_json.title不存在而拒绝，另两条unknown。促销用例补读另外两条固定用例形成109708字节的独立续读，不能当重复SDK请求。源默认配置未改，全局SDK仍60秒；有效项目选项与全局来源分别记录。

后续先查已有case metadata/content_json引用根与模型实际错引、阶段补读和完整续读大小，再决定是否需要版本化字段锚点或修正轮次；不代修原输出，不复发unknown，不绕过原核验。完整目标仍未完成。具体命令与失败见IMPLEMENTATION.md及PLAN.md 6.11，原对象/消息/计划/旧证据检查见p4-transport-validation.json。
