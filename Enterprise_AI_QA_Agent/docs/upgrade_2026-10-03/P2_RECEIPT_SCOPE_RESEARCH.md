# 正式回执的执行项身份：先保存实际回复再判断根因

基线ef28497f。完整P0—P5未完成，本批先查证正式回执，不修改测试模式、计划选择逻辑、权限、预算或原历史任务。

## 现有契约和实际失败

已读agent_case_contract、agent_case_context、agent_case_assessment、agent_case_receipt、case_execution/scene_receipt、PromptAssemblyService与default_coverage_production。AgentCaseExecutionContext.steps每项是固定计划项，身份是step.item.key；content_json.steps是用例自身业务内容，可以有八步，但不是八个独立计划项。AgentCaseExecutionReceipt.steps逐项对应固定身份；assess同时检查数量、唯一键集合、target_key，随后按持久ToolJob证据校验检查与输出。不能为了通过而丢弃多余项、放松数量校验或把工具成功当目标通过。

既有case_result_contract默认v4写“every requested step/all owned steps”，未明确说明内部content_json.steps不是回执项；完整上下文仍有精确身份。只读assessment另有requested_item_keys和execution_items，原回执纠正已有冻结请求/响应和预算恢复机制。此时只能确认措辞歧义，不能证明它是旧失败根因。

历史p2-effect-tasks-default实际规划和Agent执行：创建两条、查询第一条、逐条删除两条；最终error为“执行回执必须逐项对应固定用例及其准备和清理，不得遗漏或重复”。final-run/results保留错误和execution_checkpoint身份，但该批未导出最终公开model_response_text；物理探针只保存流式字段计数，不能从计数恢复原正文。不得伪造旧回执。

本地课程Agent轨迹与协议鲁棒性及Agent测试专项要求验证过程、协议、幂等与终态，最终工具成功不足以证明通过。本次使用新隔离任务、实际默认Step、独立规划/执行CLI与真实本地业务HTTP，按现有夹具导出公开终态及固定计划项；配置仍隔离MySQL，任务/ToolJob/快照PG，原件RustFS。诊断采用历史已支持的显式low/JSON隔离profile，不称全局原默认profile质量通过。

## 核验方法

核对实际公开回执的schema_version、steps数量及item_key，与同一快照的context.steps.item.key逐项比较；区分内部用例步骤、目标/准备/清理项及工具调用身份。合法回执继续严格校验checks、期望来源、实际证据所有权及清理事实；错误回执不能改为passed。

只有新的实际回复或可复现真实样本证明问题后才改对应根因。保存失败任务及原请求，不重置终态，不重跑未知业务副作用。新请求改善后的本地/实际存储回归与外部Step验证各自呈现，范围不得互相替代。


## 本批新增查证：结构化输出的边界与预算耗尽

实际默认 Step 配置已确认支持 `json_object`，统一模型请求层已有 `output_format` 到各传输适配器的映射。尝试把 JSON 输出格式直接加到普通 Agent 工具循环后，Step 返回了结构化的空 `steps`，没有执行任何业务工具；该方案会破坏现有工具调用协议，已撤回。普通执行仍使用既有工具调用协议，不能用一次 JSON 响应替代工具循环。

结构化输出只放在工具执行完成后的只读 `AgentCaseAssessmentService.build_assessment_request`。该请求没有工具，使用 `agent_case_receipt` JSON Schema，且 `required=False`，这样只有声明支持 JSON 的模型才映射为结构化请求，旧模型仍由严格业务解析和纠正门禁保护。新增单测已核对普通图请求的 `output_format is None` 与只读评估请求的 schema 身份。

受控真实运行使用实际默认 Step、真实 Agent 工具循环、独立本地业务 HTTP 和隔离 MySQL/PostgreSQL/RustFS。规划完成，实际发生两次创建、一次查询、两次清理，业务记录已清空；公开快照保留了每次工具成功证据。由于原图 `max_iterations=8`，第 8 次工具循环后进入 `mode=budget_exhausted`，没有再发送模型请求；因此只读回执纠正按设计拒绝，终态明确记录“原模型或上下文预算已耗尽，不新增发送”。这证明了不重复业务操作和不跨原预算发送的安全门禁，但尚未证明默认 Step 在预算充足时能完成正式回执纠正。

旧批次另有一次真实只读纠正已送达 Step，但受当时传输超时和重试策略影响失败；该证据只能证明纠正请求被固定身份持久化并尝试发送，不能证明纠正成功。后续应在不改变普通工具循环预算、不重跑有副作用业务的前提下，用新的隔离任务验证“留有纠正额度”的完整链路。
