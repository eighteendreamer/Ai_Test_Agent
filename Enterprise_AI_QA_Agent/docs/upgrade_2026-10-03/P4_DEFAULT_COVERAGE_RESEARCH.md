# 默认自主Agent完整链路调研

源码基线6c345dcb。先读目标原附件、生产专项与main/规划CLI/执行CLI/SDK/模型存储。上一轮是受控模型，不能作为默认模型完成依据；本轮让实际默认模型自行查询、决定计划和执行动作，不提供受控finish或工具回执。

- `MySQLModelConfigStore.get_default_active`读取当前配置；`upsert`及`_serialize_capability_overrides`支持复制已有模型、能力、窗口/输出及鉴权配置。复制到fixture刚创建的隔离表，不修改源表。使用原`model_invocation_parameters`比对原/副本，凭据只在内存/隔离配置表，禁止保存到证据。
- `OpenAIChatClient._make_client`仍创建原AsyncOpenAI，原timeout/max_retries不改。测试探针给返回SDK的HTTPX客户端加request/response事件钩子；原URL/headers/params、SDK序列化和流式读取保留。
- [HTTPX官方事件钩子](https://www.python-httpx.org/advanced/event-hooks/)：异步钩子必须为async；request在发送前调用，response在读取正文前调用。本探针仅记录请求正文及响应状态，不调用response.aread、不改请求或响应。
- [Python3.11 runpy官方文档](https://docs.python.org/3.11/library/runpy.html)：在独立测试进程内执行原模块`__main__`。探针不替换AppContainer、图、模型结果、工具或任务服务；先用本地真实SDK验证钩子透明性。
- 原生命周期夹具的Worker等待60秒、API请求50秒针对受控模型；真实模型应允许原180秒规划预算及原SDK重试完成。仅延长测试观察等待，不改产品预算，不以测试提前杀进程制造模型失败。
- 完整八步目标查询需要真实ID；项目需求文件明确POST创建两条、GET第一条、逐条DELETE。模型自行按项目依据补前置和清理；不预填执行计划。实际两条记录、类型值、清理及原来源分别验收。

旧默认length/空正文失败保留；新协议/新完整输入试验与旧请求不同，不重复相同历史工作流。失败必须保存固定seed、checkpoint、原响应/物理发送、实际副作用和进程日志。未知发送不重启。完整App、共享消费完成、运行修订和其余P0—P5仍保持待实施范围。

## 实际首次终态与发送前契约根因

`p4-default-coverage-first.xml/log`：1失败，156.48秒；规划正常完成，一个正式目标，无额外prepare。模型认为八步用例本身已含准备和清理，这种规划合法，不应强制拆成准备项。实际业务HTTP为0，五份SDK物理请求保留，响应钩子只证明响应头，不证明流完成。

`default-api.stderr`定位到runtime._execute_dispatched_task的ApiTestTask.model_validate：原模型任务含endpoint/method/expected_status，缺task_id/path。registry/tools.py只将task声明为object；真实campaign_state.ApiTestTask要求task_id、method、path。不是业务模式需重建，而是注册输入契约没有同步真实消费方。采用现有Pydantic model_json_schema生成任务契约，并将其$defs放在工具参数根节点，保证嵌套$ref可解析；既有campaign入口参数继续保留。

tool_runtime_service._dispatch仅在handler可靠返回后保存observe_output，所有此前异常按未知执行处理。原ValidationError在创建executor之前产生，明确无业务发送；应由本地API运行时将该确定性错误返回为failed/no-send及安全字段位置/错误类型，再沿现有Job观察和终态保存。仅捕获这处ValidationError，不泛化捕获网络异常，不改变真实丢返回的未知处理、不补写历史失败。

回归使用首次试验保存的实际失败task，而非重新编造样本；核对完整注册/规划/动作关联Schema，实际本地HTTP创建、查询及删除，并在真实PG/MySQL/RustFS fixture验证确定性失败可恢复且没有发送。输入变更继续遵守既有逻辑操作身份约束。默认模型完整链路之后以新契约另验；旧断言要求必须有prepare清理声明，需要研究同一正式用例内部执行清理的证据，不能以没有prepare断言业务必然失败。


## 本轮验证终态

24项契约/实际HTTP/PG回归通过9.86秒，16项模式回归通过7.16秒，3项真实未知回执通过10.36秒，受控独立生产2项通过105.61秒。原夹具失败保留，详见PLAN6.51。

新默认试验207.57秒失败，实际规划截止180秒、无业务HTTP；前两轮已存正文，第三轮interrupted，3次物理发送。第二轮错误地再次搜索已知工具名称，没有读取完整tool接口。这是后续需要研究的查询协议表达/上下文问题，不通过扩大预算或原请求重发掩盖。正式项内部清理的服务端覆盖仍未实现，测试实际对象与PG对账不能替代这一产品能力。
