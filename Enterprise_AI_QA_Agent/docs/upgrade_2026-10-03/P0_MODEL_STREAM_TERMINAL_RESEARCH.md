# 多模型流式协议结束与后续请求映射

基线443f5af7。原目标附件325行已分段读取，保留完整P0—P5与多模型接入范围。本步先查证请求映射，发现 LangChain 1.6.2 的 ChatOpenAI `_default_params/_get_request_payload` 强制把 max_tokens 改为 max_completion_tokens；不能通过简单 bind 保证旧字段。后续MySQL profile必须记录实际适配器及字段映射。尚未写入或迁移全局配置。

## 请求映射调研〔尚未实施〕

当前DTO/四SDK及桥接已有内部统一层。已安装SDK实际签名支持：Chat response_format/reasoning_effort，Responses text/reasoning，Anthropic output_config/thinking，Google GenerateContentConfig response_mime_type/response_json_schema/thinking_config。不能仅因SDK接受字段就认为具体模型支持。

官方来源：

- [OpenAI结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)：Chat response_format与Responses text.format；[推理配置](https://developers.openai.com/api/docs/guides/reasoning?api-mode=responses)的effort支持值因模型而异。
- [Anthropic结构化输出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)：output_config.format使用json_schema；[extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)：budget最少1024且通常小于max_tokens，不能按统一低值盲发送。
- [Google结构化输出](https://ai.google.dev/gemini-api/docs/structured-output)：当前指南主示例已转Interactions，而本系统采用GenerateContent；应对照实际已装SDK和对应API，不能照抄另一个协议。

采用明确、版本化MySQL请求profile，规范化格式/推理/媒体/工具语义，四客户端各自映射，PG冻结配置与运行参数，RustFS原件，前端/Schema/配置及消费同步；无已验证必需能力映射明确错误。旧None字段省略、不改变旧哈希。配置全true不作为能力证明。此方案保持7.11，当前仍待实施。

## 流式结束观察 → 实际复现

查证发现四个流式循环均可在HTTP EOF后返回已收正文，finish/stop缺失不报错；Responses未收到terminal事件时合并文本返回空终态，Anthropic循环没有读取message_stop。上一批共享终态保持历史unknown兼容，因此新流式缺少结束事件可能进入业务成功。先用真实SDK/受控HTTP复现，再决定修改，不改变旧无字段历史的兼容规则。

[Anthropic流式协议](https://platform.claude.com/docs/en/build-with-claude/streaming)说明message_delta之后message_stop结束；Responses客户端及已装SDK以response.completed/incomplete/failed为终态；Chat/Gemini的finish字段应以实际响应观察。协议结束、输出完整、业务断言正确分别核验。JSON可解析或HTTP200均不足以证明完整。

新增实际HTTP夹具仅删除指定结束事件/finish字段，保留可解析的完整JSON正文；捕获请求、响应字节、SDK结果和保存响应阶段业务计划。分别覆盖四传输、LangChain及Anthropic仅缺message_stop。原正常夹具默认行为保持。后续结果追加到本文件，不能提前声称已经修复或验收。

## 实际结果与根因处理

原 `before` 6失败11.96秒：全部HTTP200且有可解析JSON，仍进入no_match。协议循环增加明确的stream_terminal_received观察：Chat/Gemini读取finish，Responses读取携带final_response的terminal事件，Anthropic同时读取stop_reason及message_stop；LangChain从实际chunk元数据观察finish，而不是从HTTP EOF或空尾块推断。字段仅在新流式观察出现，原finish/stop不改写，旧无字段仍unknown兼容。

最初修复 `verified` 6通过13.50秒，扩展客户端/生命周期93通过24.87秒；初版真实存储恢复3通过17.07秒。然而继续核对发现业务层已拒绝length，通用Agent仍因mode=ok走到assistant_final。`limit-before` 5失败9.32秒含新夹具未提供responder字段的错误；补齐正常分支所需字段后 `limit-before2` 5失败9.17秒，保存实际graph.execution_completed及assistant_final。不能把夹具失败当成完成了复现。

根因是模型结果存在两套成功判定：业务读finish/stop，Agent只读mode。现在现有completion解释决定统一mode：已知truncated/incomplete/blocked/failed映为incomplete_response并进入既有失败生命周期，complete/tool_calls和旧unknown保留原行为。规划/生成/影响/回执使用原具体终态区分原因，尤其影响中的truncated不降成泛化provider_failure；未知没有伪造完整。日志记录状态，缺失终态警告；新记录追加观察，不改历史快照或哈希。

两条真实SDK工具响应进一步复现路由遗漏：`tool-before` 2失败8.31秒，native与LangChain已标incomplete_response但route仍仅按非空tool_calls分发到tool_executor。修复现有route_after_model_invoker在失败时进入finalizer，保留原工具调用以供查证，不清空证据。正常tool_calls对照仍进入原tool_executor。测试只核对真实SDK返回及实际路由，不声称执行了该工具或完整App。

## 验收批次与范围

- `delivery` 204通过、48门控跳过62.61秒：13模块、15实际HTTP协议/Agent节点场景、20正常/截断同步及流式矩阵、Anthropic显式0及既有业务合同。包括6缺结束证据、5已知截断、2不完整工具响应、2正常工具路由对照。日志字段补强后的最终trace批次另记。
- `recovery-final` 3通过、7未选中14.76秒：真实独立MySQL模型配置、PG任务/检查点和RustFS新Markdown原件，完整八步用例、False/0/null；实际SDK/HTTP先查询原件再返回截断或没有结束字段的原失败正文，PG响应提交确认丢失后沿原deadline恢复。2次HTTP，终态及同key提交零新增调用。配置策略后来变为max_rounds=1，原seed仍5。它是确认丢失/租约恢复，不是kill进程或默认外部模型完整执行。
- `storage` 6通过、14未选中49.19秒：受影响生成守卫和五种影响失败、实际MySQL/PG/RustFS/SDK/受控HTTP。截断仍分类truncated，空/工具/500/超大仍保留各自原件；500在一次逻辑调用内SDK实际尝试3次。生成守卫零新增发送但未提交草稿。

本批无MySQL全局配置改动、DDL、依赖或前端改动。新model mode复用MODEL_FAILURE_MODES，跨消费者包括图路由/完成、业务规划/生成/影响、回执/纠正、上下文压缩与转录、会话状态和恢复。前端仍展示既有interrupted状态，未以build代替实测，也未重跑无改动前端。

仍未证明真实供应商多模型、默认Agent/App完整成功，未补请求profile/JSON/推理/媒体能力治理、未知新供应商终态语义和Deep Agents pilot等其他模型路径。完整P0—P5的录制业务管理/清洗遗漏、API续期、性能容量/AI流式、安全其他工具、准备/未知副作用/计划修订、需求业务质量和生产历史迁移继续推进，不缩小原目标。

最终trace-final **24通过18.04秒，0跳过**：15实际SDK/HTTP及图节点场景和9项既有模型失败生命周期，核对失败事件携带completion_status。最终compileall退出0；它只补语法检查。原失败及前述通过批次独立保存。证据哈希与三份同步见p0-model-stream-terminal-validation.json，历史审计只核对工件，不要求有意修改的源码与旧哈希相同。
