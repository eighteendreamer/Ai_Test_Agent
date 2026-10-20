# 当前默认 Step 执行闭环：配置迁移与生成截断调研

基线 a8bb829e。用户已确认 Step 认证完成，恢复真实默认模型验收；旧403和旧暂缓决定仅是历史。

## 依据与实测

现有 MySQLModelConfigStore.initialize 已有 request_profile 的可空 JSON 列增量迁移，但当前实际 llm_model_config 表尚无该列；直接读取默认模型复现1054。核对初始化的旧归一操作条件均已满足后调用现有初始化两次，8条配置原字段（包括凭据、更新时间）逐项完全一致。仅新增空列，不回填 profile，不更改全局模型。p2-default-current-migration/facts.json 只保存整表投影哈希、行数和安全调用参数。

新建真实需求Markdown、完整八步JSON用例、隔离MySQL/PG/RustFS/Redis，使用正式main和规划CLI。启用已有v2分阶段、操作预读、完整来源、关联文档和证据短引用；不增大180秒总期限或8192输出上限。p2-default-current-staged 实测1失败183秒：两次物理HTTP200，逻辑阶段58.209秒、stop，选择准备两条订单及查询目标；证据阶段98.883秒、length，completion_tokens=8192，正文0，推理字段33970字符。服务保存不完整终态并拒绝创建Run，业务HTTP0。不能称完整成功，也不能再归因于认证或网络超时。SDK探针保存字段计数，不保存私有推理内容。

## 当前策略与下一步试验

项目已存在 ModelRequestProfile、request_mapping 和供应商client。规划请求已声明可选JSON输出；当前profile为空，实际请求仅提示词，response_metadata明确prompt_fallback/format_not_declared。不是缺另一套模型框架。

官方依据（2026-10-11读取）：

- https://platform.stepfun.ai/docs/en/guides/models/step-5-preview：Step5明确支持JSON Mode/Schema、low/medium/high；Chat使用reasoning_effort。
- https://platform.stepfun.ai/docs/en/guides/developer/reasoning：low适合信息提取；更高推理可能更慢，不证明某个业务必然正确。
- https://platform.stepfun.ai/docs/en/guides/developer/json-mode：JSON Mode须response_format=json_object并仍做应用校验；结构约束不能证明业务正确。
- https://platform.stepfun.ai/docs/en/api-reference/chat/chat-completion-create：max_tokens、response_format、reasoning_effort字段。

沿现有适配器，在隔离模型表显式声明json_object、max_tokens及low推理，用新项目/任务试验同一完整用例；保留原来源、工具Schema、180秒和8192，不恢复旧failed任务。测试通过可选文件读取通用ModelRequestProfile，没有按模型名猜字段；模型复制现在明确保留当前profile，避免未来有配置时夹具误丢。

该试验只调整隔离的调用profile，不能称全局无配置默认已经通过。若依然失败，根据保存模型正文/PG/工具返回定位，不直接增加预算、强制成功或重发副作用。

SDK探针及既有profile合同42通过25.38秒。改变夹具后42通过1个存储门控跳过24.83秒，随后正确启用实际存储专项1通过6.55秒；不是将跳过计为通过。

## 显式profile结果与架构复核

p2-default-current-profile **1失败183.42秒**。两次HTTP200。逻辑38.567秒、stop，正式用例自行准备/查询/清理，依赖为空且cleanup_sources引用自己；这种表达符合完整用例，不强行变为独立prepare。证据120.399秒、length，completion_tokens=8192、正文0、推理字段35419字符。实际HTTP请求明确low/json_object/max_tokens=8192，PG映射记录三个mapped；所以不能声称配置未发送，也不能声称low解决了问题。Run/业务HTTP仍0，全局配置前后一致。

停止调整同一路线的模型参数。核对实际effects输入与生产代码：

- planning_workflow把checkpoint.linked_sources完整model_dump送入模型。该字段为恢复而保留DocumentContentBasis的完整asset、version.snapshot、上传安全报告、对象地址和重复preview_text；本轮该字段4724字符。完整正文同时已经在document evidence中。元数据应保留在PG，但恢复记录不必全成为推理输入。这是可直接复现的上下文职责混合；尚不能证明它独自导致截断。
- effects同时要求产生/清理声明、来源关系、选择器、输出路径、文档预期锚点和传值映射；输入还包含完整工具模型与导航。本轮evidence8292字符，reference_catalog2469，tool_catalog2769；完整来源/用例/工具契约不能为通过测试而截断。
- 捕获层case_effect_relations/case_effect_evidence已经能在原可靠ToolJob事务校验声明和实际输出；规划声明不应成为另一套可执行业务脚本。模型应选择必要关系与业务标准，确定性路径/身份校验仍沿既有代码。

后续必须先设计并验证版本化规划输入投影：将linked_source_reads作为来源身份、读取状态、警告/错误、case/evidence关联的导航，完整basis继续原PG保存；模型通过同一已读evidence取得完整正文。不减少正式JSON/原文/工具字段，不更改旧seed/检查点的请求重放语义。再评估能否在现有阶段中分离声明任务，保持原轮数/期限和总预算；不得只新增循环或硬编码HTTP方法。该方案仍待实施与真实模型验收，本批只记录已证实职责混合，不宣称截断根因已完全修复。

当前试验仅指定单个候选，并非多候选项目检索质量，也没有进入业务执行；目标选取/排序补全/执行/正式结果/终态重投仍未通过。旧403不是当前状态，其他P0—P5要求继续保持。

## 分阶段证据任务 v3 实际默认模型复验

2026-10-19 在隔离 MySQL/PG/RustFS/Redis、正式 API 与规划 worker 上复验单个创建并清理用例，启用现有分阶段证据任务 v3、来源身份、条目级操作选择、关联来源和证据引用；总期限设为600秒仅为区分超时与生成截断，模型输出上限仍为8192，profile 显式为 `reasoning_style=none`、`json_object`。证据目录：`p2-default-single-staged-timeout600-20261019/`。

pytest **1失败195.08秒**。隔离任务经过3轮并收到3个HTTP 200；第一轮流式调用耗时94.73秒，`finish_reason=length`，`completion_tokens=8192`，SDK观测到31,076个非空reasoning字符、正文0字符。后两轮分别为 `stop`，产生375及1,236个正文字符，但最终任务状态仍为 `failed`，消息为“模型规划输出达到生成上限，响应不完整；已保留响应，不自动重发；未创建测试运行”。总期限尚未耗尽。Run未创建，目标服务HTTP请求数为0，未验证真实执行、清理和结果入库。

这次失败可确定为首阶段触及本地生成上限，但不能据此断定默认调用已发送“关闭推理”。代码契约中 `reasoning_style="none"` 表示不映射统一推理选项；实际HTTP请求键为 `messages/model/max_tokens/response_format/stream/stream_options`，没有 `reasoning_effort`。这意味着服务端按自身默认推理行为运行，而不是收到关闭指令。Step 官方文档当前列出 `low/medium/high` 三档，没有文档化关闭档位；Chat Completions 使用 `reasoning_effort`。模型指南注明 Step 5 Preview 最大输出64K，Chat API `max_tokens` 是生成上限且未传时默认由模型决定。来源：<https://platform.stepfun.ai/docs/en/guides/models/step-5-preview>、<https://platform.stepfun.ai/docs/en/guides/developer/reasoning>、<https://platform.stepfun.ai/docs/en/api-reference/chat/chat-completion-create>。

此前显式 `reasoning_effort=low` 的隔离profile也在8192上限耗尽；因此单改reasoning档位没有消除截断。当前还没有证据判断一次16K隔离上限是否足以让v3 produced阶段完成。本轮不改全局预算、Provider协议或重发策略；下一步只在隔离MySQL模型副本把输出上限从8192改为16384，重复同一真实用例和阶段组合，检查首阶段能否产出完整结构化回执及后续Run/业务HTTP/清理证据。若仍失败，按真实终态和阶段回执继续查，不继续盲目加预算。该实验不验证多候选排序、依赖用例补全、运行内准备步骤或其他测试模式。

## 隔离 16K 输出上限复验

先按相同测试入口运行，但初次策略环境变量组合遗漏 `QA_EVIDENCE_REFERENCES=1`、`QA_ITEM_OPERATION_SELECTION=1` 和固定的关联协议。`CasePlanningPolicy` 在创建规划任务前拒绝 `case-planning-staged-evidence-v3`，测试 **1失败，17.71秒**；证据目录 `p2-default-single-staged-16k-20261019/physical/` 为空，没有模型或业务 HTTP 请求。这是夹具配置失败，不计为模型样本。

修正组合后使用正式 API、规划 Worker、隔离 MySQL/PostgreSQL/Redis/RustFS，以及新的单订单创建/清理业务目标复跑。只在隔离模型记录把 `max_output_tokens` 从8192改为16384；全局默认配置不变，`reasoning_style=none`、JSON模式、600秒诊断期限、`case-planning-staged-evidence-v3`、执行项操作选择、关联来源和证据引用保持固定。证据位于 `p2-default-single-staged-16k-corrected-20261019/`。

pytest **1失败，233.72秒**。规划任务运行3轮，SDK物理请求均携带 `max_tokens=16384`。脱敏流观测显示两次调用完整返回 `finish_reason=stop`（分别约94.66秒/5,979 completion tokens/351正文字符，以及25.92秒/2,295 tokens/1,042正文字符）；另一次流式调用在约90.12秒以 `APIConnectionError` 结束，已观察到5,742 completion tokens及reasoning delta，但没有完成事件或正文。最终阶段状态为 `effects`、响应 `http_error`，任务失败，未创建Run，业务HTTP为0；原件已保存，未自动重发。测试未保存私有正文或response body。

因此16K实验证明此前8192首轮 `length` 截断可被更高的隔离输出上限避开，但未完成规划闭环；本次终态的直接阻塞是另一次流式连接中断，不能把它归因于模型结构化输出错误或继续加大预算。也不能把“两个stop”说成合法规划成功，因整体任务仍失败且缺少完整终态计划。下一步应把该SDK调用、物理HTTP attempt、Worker错误和checkpoint阶段身份对齐，查连接中断来自服务端关闭、网络传输还是进程/租约边界；然后用受控失败回归验证任务恢复是否保留固定输入且不重复业务副作用。不要重放该已失败任务，也不修改全局预算、协议或重试策略。该用例仅测单用例创建/清理，不证明项目多候选选择、排序、依赖补全或完整按用例执行。

## 2026-10-19 新任务诊断探针与默认模型完整单用例复验

为定位前次流中断，在仅供测试的 `default_model_probe` 增加异常因果链**类名**采集；不保存异常消息、HTTP响应正文、凭据或模型内容，不改生产SDK、重试和超时策略。受控真实HTTP SDK测试覆盖Content-Length中途截断与取消，`test_sdk_stream_observation.py` **9通过，11.33秒**，验证异常链与原发送/响应语义保持。

未恢复旧失败任务。使用新的项目、新的需求文件、新的正式用例和新 planning task，在隔离MySQL/PostgreSQL/Redis/RustFS、正式API/Worker及本地受控订单服务中，以实际Step默认模型重新验收；隔离模型记录使用 `max_output_tokens=16384`、`json_object`、`reasoning_style=none` 和600秒诊断期限，全局配置未改。策略冻结为分阶段证据任务v3、操作预读、条目操作选择、来源身份、关联文档及证据引用。证据目录 `p2-default-single-staged-diagnostic-20261019/`，pytest XML `p2-default-single-staged-diagnostic-20261019.xml`。

专项**1通过，343.03秒**。8次SDK流调用全部收到HTTP 200并以 `finish_reason=stop` 正常结束；无异常链产生，因此这次没有复现APIConnectionError，原故障根因仍未确定。默认模型规划完成，建立正式Run；Run、唯一RunItem和正式结果均为 `passed`。工具任务实际POST创建一条订单，随后DELETE使用POST实际返回的 `order.id`；两个工具任务状态均为completed，剩余订单为0。终态Outbox重投后新增物理模型请求为0、业务副作用未重复。保存了请求/响应安全投影、SDK流计数、PG规划和工具任务、正式Run、退出码及XML。

该实测把“默认AI单个完整用例从规划到执行的一次闭环”从未验收更新为**已验收一次**，但不证明多候选项目检索、用例排序、跨正式用例补全、运行内准备步骤、负向用例和其他测试模式。单次成功也不消除此前真实流故障证据；后续应通过持续运行积累失败链样本，或在新故障发生时用新增探针取证，不为追求复现而盲目重发旧任务。

## 2026-10-19 多候选自动召回路径复核

继续检查项目页面、规划提交契约、召回服务、规划 Worker 和计划转正式 Run 的实际代码。项目页面把当前勾选的启用用例 ID 作为 `candidate_case_ids` 传入；勾选项是 AI 候选池，不会被强制全选为目标。没有勾选时提交空列表，后端 `_recall` 调用项目范围向量召回；召回不足或没有命中时再从同一项目的启用用例目录补候选，随后仍由 AI 阅读完整固定版本决定目标和前置。历史 `mode_key` 不作为硬过滤。向量服务失败与没有匹配分别保存为检索失败和无候选/目录回退状态。候选与计划通过 project_id 校验；正式 Run 冻结计划中的用例版本，并继续使用现有 suite、RunItem、Outbox 和 Worker 执行链路。

当前线上 MySQL 配置存在 `embedding_retrieval` 模型记录（OpenAI Chat Completions transport，Qwen3-VL-Embedding-8B）；此次只读检查未调用它，也未证明新激活用例已被真实 embedding Worker 建索引。已有真实默认模型多目标规划专项明确提供了三个目标用例 ID，因此它验收了 AI 依赖发现、排序、输出绑定、已有数据和负向条件，但没有验收自动候选召回或规划后执行。产品页面规划弹窗与任务面板回归 **92 项通过**；后端 `test_case_planning.py`、`test_case_planning_tasks.py` 和 `test_case_plan_run_creation.py` 为 **31 项通过、28 项因隔离数据库/对象存储/真实模型条件而跳过**。这些结果证明代码逻辑与页面契约回归，不是实际默认 AI 的自动召回或多用例执行证据。

补充空候选前端契约后，`CasePlanDialog.test.ts` **85 项通过**，确认页面把空 `candidate_case_ids` 原样提交给服务端。`RUN_LIVE_TEST_CASE_EMBEDDING_TESTS=1 ..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/test_live_test_case_embedding_pipeline.py -q --tb=short -rs` 在真实 PostgreSQL/Redis 上 **1 项通过**，验证项目范围、pgvector 索引、Redis 投影及检索返回；该专项以固定向量和模拟 embedding runtime 作为输入，未调用 MySQL 中配置的真实 embedding 模型，也未覆盖激活 Outbox 到 Worker 的实测。

本轮未发现需要先行补写新的候选选择算法；自动召回、项目隔离、空召回目录回退及 AI 目标/前置区分均已有实现。当前缺口明确为端到端验收：在隔离项目创建多个正式启用用例及区分性干扰用例，完成真实 embedding 索引并核验 PostgreSQL/Redis 投影；提交不指定 `candidate_case_ids` 的新规划任务，核验召回候选确属当前项目、模型选择正确目标并读取遗漏的必要前置；再冻结计划、创建正式 Run，由 Worker 执行并核验实际输出绑定、每条结果和重投无重复副作用。不得把固定候选的规划测试或单个自包含用例成功扩大为该闭环通过。当前无生产实现修改。

## 2026-10-19 真实 embedding 服务连通性检查

从当前 MySQL 只读解析 `embedding_retrieval` 激活记录，得到模型 `Qwen/Qwen3-VL-Embedding-8B`、`openai_chat_completions` transport 配置；未输出密钥或 endpoint。为绕过进程内及 Redis embedding cache，直接调用现有 `EmbeddingRuntimeService.embed_texts(..., use_cache=False)` 对一条短临时查询执行真实请求。供应商返回 HTTP 402，错误码 `30001`，提示账户余额不足。请求到达真实 embedding SDK/provider 错误响应路径；没有生成向量，也没有写 PostgreSQL/Redis 索引。

因此目前不能完成“激活用例 → Outbox → 正式 embedding Worker → PostgreSQL/Redis → 空候选项目召回”的真实模型验收。没有更换全局 embedding 模型、修改 MySQL 配置或重试请求。继续条件是该 embedding 账户可用后，在新的隔离项目和新内容上重新开始完整链路验收；Step 默认模型已认证并不代表此独立 embedding provider 的账户可用。P2 自动召回产品实现仍待功能级验收，不能以这次 402 或既有固定向量专项标记通过。



## 2026-10-20 Step 已认证，但当前默认模型与 Step Plan 目录不匹配

按用户确认继续真实 Step 验收后，先只读 MySQL 默认模型配置，再用同一凭据访问其已配置的 `https://api.stepfun.com/step_plan/v1` 目录。`GET /models` 实际返回成功，目录包含 `step-3.5-flash` 与 `step-3.5-flash-2603`，但不包含配置中的 `step-5-preview`。使用 `model_invocation_parameters()` 的 JSON 序列化口径计算后，当前 MySQL `api_base_url_hash=eecc0149...b8db6ea` 与新隔离规划任务保存的 endpoint hash 完全相同，确认这不是跨时间配置变化造成的误判。凭据未保存或输出。当前观测证明认证和 Step Plan 路由可用，不支持再将失败归因于认证。

在新隔离项目执行完整多用例测试，规划 Worker 只发出一次 SDK/HTTP 请求即收到 HTTP 400；探针仅保存安全元数据 `type=request_params_invalid`，没有保存响应正文。为区分参数问题，又用官方 OpenAI SDK 对同一 MySQL 默认端点执行三条极短只读模型请求：`step-5-preview` 分别搭配 `max_tokens=64`、`max_tokens=16384`，并对照 `stream_options.include_usage` 开/关；三次均为 HTTP 400、`request_params_invalid`，未保存响应文本。由此排除完整规划提示长度、16K预算及 `stream_options` 单独造成该错误的解释。安全结果见 `p2-default-multi-case-safe-error-20261020/step-parameter-probe.json` 与 `step-model-catalog.json`；完整新任务错误元数据见同目录 `physical/*.sdk-create.json`。

[Step 官方 Chat Completions API 文档](https://platform.stepfun.ai/docs/en/api-reference/chat/chat-completion-create)将 Step 5 Preview 列为支持模型，并说明标准端点为 `/v1/chat/completions`，`max_tokens`、`stream` 为请求参数；它没有提供证据证明 `step-5-preview` 在当前 `/step_plan/v1` 目录可用。结合真实目录不含该模型和小请求持续 400，当前根因最符合“Step Plan 凭据/端点正常，但模型 ID 属于另一服务目录或未开放给此订阅”的配置不匹配；这属于强证据推断，未证明 Step5 在其他端点对该凭据可用。不得通过生产代码改协议，也不得改写全局 MySQL 默认配置来绕开。

测试探针新增只记录 SDK 的 `status_code/code/param/type` 安全字段，不记录错误消息/响应体；本地官方 SDK + 真实 HTTP 回归 `tests/test_sdk_stream_observation.py` 与 SDK 事件钩子专项 **11 passed，21.72 秒**。新多用例默认测试 **1 failed，46.33 秒**：规划未完成、Run 未创建、业务 HTTP 为0。P2 默认 Step 多用例仍未闭环。继续条件是确认要在 MySQL 中使用的实际模型/服务端点组合；此配置选择未确定前，不把目录中其他 Step 模型的隔离测试冒称为当前 `step-5-preview` 默认验收。自动 embedding 召回仍另受 HTTP 402 账户余额问题阻挡，与 Step 认证无关。
