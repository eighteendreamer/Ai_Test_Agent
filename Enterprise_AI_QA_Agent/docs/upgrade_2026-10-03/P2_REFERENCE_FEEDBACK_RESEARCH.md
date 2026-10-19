# P2 原文引用反馈调研

2026-10-19，基线7d8896dd。完整P0—P5未完成。

## 依据与结论

核对live6 PG-planning原件的第4/5轮及当轮实际请求：第4轮T5包含GET/DELETE各一个200，唯一性拒绝正确；第5轮把excerpt放入T引用，实际下发Schema和解析器都拒绝，非协议不一致。原模型失误必须保留。

现有planning_evidence_references.reference_effects_prompt明确说明“T的literal须唯一；歧义用E+唯一excerpt”。但拆分任务planning_effect_tasks.effect_task_prompts另写一套引用指导，仅给出T例子，遗漏唯一性和E细化办法，未复用已有指导。底层referenced_expected_value把非文本、找不到摘录、摘录重复、literal重复四种问题合为同一句错误；compile_relations未附声明身份及checks/select_when位置。真实第4轮反馈仅“执行项default-query的原文摘录或字面值不存在或有歧义”，无法定位检查；模型在第5轮改动了本来合法的DELETE选择条件。这是拆分后指导漂移及诊断上下文不足，不能靠增加轮数、改写失败候选或放宽唯一性处理。

采用既有能力：两个Prompt入口复用同一段既有原文引用规则，任务仍提供现有E/T Schema和示例；只修正新任务Prompt修订号，恢复使用冻结旧文本。底层抽取明确是哪种计数失败，关系编译器附声明/字段/序号，不输出原文、字面值或私有数据。无新引用协议、无模型传输改动、无新增配置/表；E+唯一excerpt已在当前v1/v2支持。用真实第4轮验证E细化后可接受，原第4/5轮仍应被拒绝。

专业依据：项目本地《Agent轨迹与协议鲁棒性》与《Agent测试专项》的参数Schema、错误恢复、检查点及工具轨迹要求；具体实现沿本项目既有document_expected_value和任务冻结/反馈链路。

## 验证要求

保存真实失败的公开用例/文档/工具证据和前两份已接受回执；在原输入上复现拒绝，核验不同合法来源形式获得同一值与精确位置，错误来源和歧义仍拒绝。实际上传含两个200的Markdown，走MySQL策略、RustFS原件、PG规划任务及响应保存后新Store恢复；一轮拒绝后使用既有E细化形式，原轮数/期限不增加，已接受任务不重发，终态重投不新增模型调用。再执行默认Step的新任务验证，单目标通过仍不能证明多候选/补全/完整P2已完成。

## 实施与实际验证

真实失败样本保存为tests/fixtures/planning_cleanup_ambiguous_text.json，仅含公开证据、固定逻辑、前两份回执和原始第4/5轮回复；没有保存租约、认证头或模型私有推理。修改前tests/test_planning_reference_feedback.py为 **3 failed、5 passed，1.06秒**：精确E引用已经成功，失败为声明/检查定位缺失和两种协议任务指导遗漏。

实现抽出reference_expected_prompt，由原整阶段和拆分任务共用；原整阶段v1/v2文本与HEAD逐字符比较相同。新拆分任务快照修订为3，旧revision1/2仍可读；实际live6的seed/checkpoint逐字段原样往返，仍显示effect-tasks-v2.r2。没有改引用Schema、目录、原文、唯一性或真实模型配置。抽取错误仅记录来源身份/路径/类型及计数；多效果编译附声明、checks/select_when和序号，日志不输出原文或literal。

首次相关回归 **49 passed、22 skipped，9.39秒**。随后设置RUN_LIVE_CASE_PLANNING_TASKS=1，运行test_planning_effect_tasks_live.py、test_planning_reference_feedback.py、test_planning_effect_tasks.py、test_planning_reference_schema.py、test_planning_evidence_references.py：**64 passed，35.10秒**。新增v1/v2的真实原文上传、一次歧义拒绝后精确E引用、修正响应提交回执丢失/新Store恢复，均共用原5轮及原deadline，已保存bindings/produced不重发，终态不新增模型调用。证据p2-reference-feedback-recovery-20261019/；模型受控，不能当作默认AI通过。

设置RUN_LIVE_CASE_EFFECT_RELATIONS=1，运行test_case_effect_relations.py、test_case_effect_expected_sources.py、test_case_non_execution.py：**45 passed、3 skipped，52.59秒**，包括真实PG/RustFS及HTTP正式关系核验；3项未执行的是另需RUN_LIVE_PLAN_RUN_CREATION的原生输出恢复，本轮不计通过（上一批已有专项证据）。该命令最初误写不存在的test_case_effect_sources.py，0项执行，改正后才得到上述结果。

补充日志与旧阶段回归：test_planning_reference_feedback.py、test_case_planning_stages.py、test_planning_linked_sources.py、test_case_effect_expected_sources.py为 **32 passed、22 skipped，8.71秒**，跳过项不计通过。前端契约未改变，task_prompt_revision仅由后端任务seed持有；全局引用搜索未发现前端该字段消费者。

真实默认Step新任务p2-default-full-reference-feedback-8192-live7-20261019/结束：**1 failed，281.16秒**；Worker正常退出，规划任务failed。当前MySQL模型与8192输出额度，隔离600秒规划期限，无profile/输出额度覆盖。逻辑和bindings通过，分别80.269/59.507秒；produced在103.183秒后以finish_reason=length结束，completion_tokens=8192，正文0字符。物理HTTP共3次，均已收到200，非鉴权失败或规划总期限到达；两份reasoning字段可能表示同一流，不相加，也不保存或解释其正文。该轮未到cleanup，没有创建Run和业务HTTP，不能证明默认模型清理引用已通过。

复核P2_PLANNING_CONTRAST_CURRENT_RESEARCH.md及P2_EFFECT_TASKS_RESEARCH.md：low、原生Schema、路径提示、两步/八步和16K隔离额度此前已有真实失败，不能再把换同类提示或增大预算当作下一方案。此次引用指导/错误定位修复有独立真实样本和恢复证据，可提交；默认Agent完整验收仍失败。下一步对执行前声明职责与实际Agent/返回后核验的重叠做架构审查，并继续其他独立模块，不继续盲重发。没有改生产预算、强制所有模型使用同一协议或放宽canonical。

另外检查CasePlanRunService.create/build_plan_run、CasePlanExecutionSnapshot、RunStore领取和CaseActionBinding：当前没有追加计划修订操作；运行快照只有单份plan，动作绑定context_sha256。简单覆盖plan会改变已存操作依据，失败后新建回归也不是在途修订。已执行部分的固定上下文、未知副作用及原子领取必须纳入修订设计；本批只确认缺口，未宣称实现。
