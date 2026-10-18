# 默认 Step 规划失败的独立请求对照

日期：2026-10-18；源码基线 `7cc48164`。完整 P0—P5 未完成。

## 查证与实验边界

先读取用户附件全文、`P2_EFFECT_TASKS_RESEARCH.md`、实际 live18d 请求和 PG 检查点，以及 `planning_workflow.py`、`planning_effect_tasks.py`、短引用绑定、canonical 效果编译、模型映射和配置存储。

已确认规划当前请求 `ModelOutputFormat(json_object)`，Schema 作为系统提示文本；生成和回执已有可选原生 Schema 的用法。适配层已支持按声明映射 Chat Completions、Responses、Anthropic Messages 和 Gemini，不能把所有模型换成同一协议。Anthropic 当前仍调用 `client.messages.create`，`await_provider_io` 只统一网络错误。

官方依据：[Step 5 结构化输出示例](https://platform.stepfun.com/cookbook/step-5-vision-structured) 提供原生 `json_schema`；[推理档位示例](https://platform.stepfun.com/cookbook/step-5-reasoning) 使用 low/medium/high，并明确档位不是具体思考秒数。不能凭省略 reasoning 参数假定关闭推理。项目本地《Agent轨迹与协议鲁棒性》要求同时核对计划、参数、观察及终态，不能用 HTTP 200 代替业务完成。

另查到 v3 分任务 common 提示比既有 `reference_effects_prompt` 少了明确的路径根说明。`compile_relations` 中 expected_path 相对完整用例 content 或 prepare.instructions；执行时 selector 相对真实工具输入，object/check 相对真实工具返回。Schema 描述中的 output/returned 不是执行 JSON 的额外包装。该说明缺失是静态事实，是否导致实际超时仍须实验，不能预先认定。

新增门控验收 `test_planning_request_contrast_live.py`：读取真实失败请求和 PG 固定逻辑/已接受 bindings，创建**新的独立只读研究调用**。不恢复或重发旧任务，不开放业务工具，不创建 Run，不预填项目答案。模型配置落在隔离 MySQL；PG/Redis 使用既有隔离夹具；没有更改生产业务、配置表或适配器。实际流式响应仍走原 Step Chat Completions SDK，探针只保存公开请求、状态与字段计数，不保存私有推理正文。

## 两个对照结果

源请求来自 `p2-default-coverage-live18d/physical/21b265d4eb1f42b899dc82d9f2104e00.request.json`。两个实验均保留模型、完整业务输入、low、8192 和原阶段剩余窗口 74.87 秒。每次只发一个新逻辑调用。

| 对照 | 唯一实际请求差异 | 结果 |
|---|---|---|
| `p2-produced-native-schema-current2` | `response_format` 从 json_object 改为同一完整 Schema 的 json_schema | pytest 失败，90.62 秒含夹具；HTTP 200，调用在 74.90 秒取消；正文 0，末次观察 completion_tokens 5696，未收到 finish_reason |
| `p2-produced-path-roots-current` | messages 中的 system 补回路径根说明，user 原样保留；仍 json_object | pytest 失败，85.26 秒含夹具；HTTP 200，调用在 74.886 秒取消；正文 0，末次观察 completion_tokens 4628，未收到 finish_reason |

以上均不是 length 终态，也不能把末次流式 usage 当供应商最终账单。两路 reasoning/reasoning_content 计数可能是同一推理的重复表示，不能相加当独立工作量。每批恰好一次 SDK/一次物理 HTTP，没有业务请求；全局默认模型参数在 finally 重新读取且恒等。

首轮 `p2-produced-native-schema-current` 是夹具错误：MySQL upsert 返回公开 DTO，不能用于完整模型参数指纹。按已有 ModelRegistry/get_active 契约修正后再测；该轮 8.14 秒、供应商请求 0，原失败 XML 保留，不算生产缺陷。

## 结论与后续约束

**已有实现**：分供应商映射、分任务固定逻辑/回执、canonical 校验及存储恢复。

**本批补验收**：独立失败输入的两个单因素实际请求对照。它们否定了“仅把 json_object 换成原生 Schema”或“仅补回路径说明已能在当前窗口完成”的判断；不证明两者在所有输入都无价值，也不支持认定唯一根因。

**仍未验收**：默认模型完整项目选取→排序→补全→正式执行；上述两个实验均没有可用 produced 回执，更没有运行或清理业务。

本批不把实验提示和原生 Schema 接入生产规划，也不调整生产期限、Token、canonical 或历史 seed。停止继续同方向提示微调。下一步须从整个阶段职责和原总预算的可执行性重新设计并验证，不能以去掉来源、预填项目业务路径或换协议掩盖问题。其他模块的验收独立推进；全目标保持进行中。

## 2026-10-19 原生 Schema 认证模型复测

用当前认证的 `step-5-preview` 和隔离 MySQL profile，重跑原生 Schema 单因素对照 `tests/test_planning_request_contrast_live.py::test_captured_produced_request_contrast`。本轮源为 `p2-default-coverage-step-certified-profile-20261019` 中保存的 produced checkpoint/request；固定剩余窗口 83.915 秒，保留相同模型、low reasoning、8192 max output、完整业务输入及 Chat Completions transport，仅把 response format 设为已有完整 Schema 的 `json_schema`。旧任务未恢复，工具不可用，业务 HTTP 为0，全局 MySQL 模型参数未变。

pytest **1失败，95.964秒含隔离夹具**。真实 SDK/HTTP 观察证据显示收到 HTTP 200，首片段 2.061 秒，持续收到 2,375 个流片段至 82.714 秒；content 正文 0 字符，reasoning 与 reasoning_content 字段各记录 27,578 字符（同一推理内容的双字段呈现，不能相加），最后观测 completion usage 为 6,324，SDK 未正常结束、没有 `finish_reason`，在 82.762 秒由固定阶段窗口取消。测试栈顶呈现 TLS read 等待超时，但本次已收到 HTTP 200 和持续流事件，因此不能归类为建连失败或据此改网络/重试逻辑；没有完整模型回执，canonical 未运行。

这次认证账号复测与此前两次独立 Schema/路径提示对照一致，仍没有获得可校验 produced 正文。它支持“原生 Schema 单独不能解决当前窗口内的规划完成问题”，但不支持扩大输出限制/期限，也不能证明唯一根因是规划Schema、提示或模型本身。不得将该失败计作功能通过，也不得将读到的 reasoning 字段解释为模型实际思考内容。

**更新后的当前步骤**：P1 普通 Markdown 默认 Step 三模块生成、模块进度、隔离任务恢复和终态不重投已实测；模型适配现有内部统一请求/结果契约与按 provider 原生映射，Anthropic Messages 不变。P2 按用例执行仍停在 `produced`，未创建 Run。最近实验是有 HTTP 200 的 reasoning-only 流并在预算窗口取消，不是新发现的网络层故障。下一步应回到已捕获完整八步及两步单创建/清理输入，从产品职责和原总预算检查“执行前必须声明哪些结果证据”是否与现有 Agent 实际执行、响应到达后的检查职责重复；只有找到可由冻结事实无歧义导出的字段并验证canonical仍拒绝坏/缺来源结果，才改生产代码。模型字段/协议、原预算、来源引用和 canonical 接受条件保持不动。完整 P0—P5 继续未完成。
