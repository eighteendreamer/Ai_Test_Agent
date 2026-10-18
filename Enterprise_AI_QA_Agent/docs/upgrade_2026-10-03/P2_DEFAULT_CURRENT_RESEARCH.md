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

这再次把当前失败收窄为阶段输出达到模型生成上限；`reasoning_style=none` 的隔离profile没有阻止首轮返回大量reasoning，也没有让本例完成。现有证据不支持扩大期限、切换Provider协议或自动重发请求，也不能单独证明某个提示词/输入投影是根因。需继续按已有架构复核规划阶段的输入与输出契约，保留未完成响应及失败任务，不覆盖历史结果。该实验不验证多候选排序、依赖用例补全、运行内准备步骤或其他测试模式。
