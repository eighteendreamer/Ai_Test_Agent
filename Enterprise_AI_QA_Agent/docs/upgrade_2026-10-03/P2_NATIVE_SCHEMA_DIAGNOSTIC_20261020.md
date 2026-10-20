# P2 绑定阶段的原生 Schema 对照与职责核查

## 已取得的证据

重新读取用户原始附件后，在全新项目和隔离 MySQL/PostgreSQL/Redis/RustFS 中运行三候选场景：创建订单前置、订单查询目标、商品排序干扰项。保持 Step 默认模型及8192输出额度，只在隔离模型副本声明 `json_object/json_schema` 与 `reasoning_effort=low`，规划期限600秒用于诊断。

官方依据：[Step 5 结构化输出示例](https://platform.stepfun.com/cookbook/step-5-vision-structured)、[推理参数说明](https://platform.stepfun.com/cookbook/step-5-reasoning)。2026-10-20通过HTTP实际读取，两页均返回200，示例明确使用 Chat Completions、`response_format.json_schema` 和 `reasoning_effort=low`。当前 MySQL 默认模型只读核验为 `step-5-preview`、`openai_chat_completions`、host `api.stepfun.com`、8192、无request profile。全局模型配置未改。

`p2-default-multi-case-native-schema-low-corrected-20261020.xml` 为 **1失败、2未选取，479.64秒**。逻辑选择正确；后续三份完整 bindings 回执都把前置输出写成 `input_key=id`，而运行输入为空，均被现有校验拒绝。最后一轮达到8192上限。五次SDK调用对应五次物理HTTP，各HTTP200，前四次stop，最后一次length；没有创建Run，业务HTTP为0。不能称原生Schema解决了规划，也不能以正确选取替代执行验收。

首个命令的PowerShell参数构造错误导致pytest未收集测试，原日志 `p2-default-multi-case-native-schema-low-20261020.log` 保留；该次没有模型请求。修正参数后才开始上述实验。

使用同一失败请求的 `PlanInputBinding` Schema 再做一次只读最小验证，明确要求来源为前置项。Step正确返回 `source_item=upstream_fixture`、`input_key=null`，通过原Pydantic校验；stop、206 completion tokens、3.646秒。工件 `p2-binding-source-schema-probe-20261020/`。因此没有证据支持“SDK或原生Schema无法输出source_item”；大上下文中的错误来源选择仍须定位。

## 代码依据 → 本次采用做法

- `planning_effect_tasks.py:effect_task_prompts` 已将任务分为 bindings/produced/cleanup，却把原始工具输出路径、对象身份、检查与选择器规则全部放入三阶段共用指令。bindings并不接受这些声明字段；其 `input_bindings.output_path_tokens` 是来源执行项的解析后输出路径。`plan_mapping.source_output_path` 会为正式项追加 `case_output`，为准备项追加 `preparations/<key>/output`；执行器可根据消费路径组装输出。这两种路径职责不能混写。
- 采用现有分任务架构，给bindings独立的职责说明：前置项使用source_item，用户输入使用runtime_context顶层input_key，明确互斥与路径根；produced/cleanup继续使用原生工具证据规则。完整用例、文档、工具契约和canonical校验保留。
- 沿 `PlanningStagePrompts.task_prompt_revision` 冻结新说明为r4；旧r1/r2/r3从已存seed读取原字符串，不重建。没有增加阶段、回合、超时、模型预算、业务判断算法或协议。
- 三候选验收文档把工具实际返回的 `order.id` 明确写为包含点号的顶层字面量键。旧文档只写“字段order.id”，不足以唯一指定嵌套形状；旧失败仍保留。

这是阶段职责混杂的修正候选。代码结构可证明去除了不属于bindings的指令；它是否改善真实规划，只能由新任务验收证明。

## 当前分类

- **已有实现**：供应商独立原生客户端、字段映射、完整用例与文档读取、阶段冻结、绑定校验、正式Run/Agent执行链路。
- **尚未验收**：生产默认8192配置的多候选规划到真实执行闭环；原生Schema对照仍失败。
- **已有实现、局部验收**：r4阶段职责修正，默认Step已实际通过一次逻辑选择和前置绑定；完整规划及执行仍未通过。
- **独立外部阻塞**：2026-10-20当前embedding模型的无缓存真实请求仍返回HTTP402/30001，未产生向量或索引。见 `p2-embedding-account-recheck-20261020.json`；不将其混同Step认证。

合同回归：`tests/test_model_request_profile.py tests/test_planning_effect_tasks.py` 修改前 **60通过，25.50秒**。后续须运行修改后合同、实际存储/恢复与默认模型场景。

## r4实际验证结果

修改后合同 **62通过，24.21秒**；`test_planning_effect_tasks_live.py` **18通过，24.62秒**，实际使用隔离MySQL/PostgreSQL/RustFS，核验分阶段回执保存、各边界丢失确认后的回读、预算、原版本保留及终态不重新调用。跨模式完整用例/依赖规划专项 **2通过，4.22秒**，其中一项使用真实存储。验收夹具及SDK流观察专项 **2通过，5.13秒**。四组共84通过，不与修改前60项重复相加。编译检查与本轮diff检查通过。没有前端改动，未重复前端构建。

真实默认Step的新项目复验 `p2-default-multi-case-r4-20261020.xml` 为 **1失败、2未选取，369.48秒**。没有profile或输出上限覆盖，实际四次HTTP请求均携带max_tokens=8192、没有reasoning_effort或response_format。阶段如下：

| 阶段 | 实际证据 |
|---|---|
| 逻辑选择 | stop，2975 completion tokens；选择创建订单前置和订单详情目标，排除商品干扰用例 |
| 数据绑定 | stop，7276 tokens；第一份回执被接受，`order_id`来自前置项`create-order-dependency`，路径为`["order.id"]`，不是空runtime_context的输入 |
| produced补查 | stop，2048 tokens；只读查询原生工具依据，没有执行业务 |
| produced声明 | length，8192/8192 tokens，正文0字符，reasoning字段35764字符；原检查点拒绝不完整响应 |

任务在约369秒失败，早于600秒诊断期限；Run未创建，业务HTTP为0，清理/正式结果/执行终态重投尚未进入。故只能确认本场景绑定职责修正后的阶段通过，不能称P2完整闭环通过，也不从单次样本推断稳定性或全部因果关系。后续应继续审查produced声明的职责、完整输出契约和实际工具取证之间的边界，不继续通过提示叠加或扩大预算宣称解决。

复验时还确认旧验收脚本强制输入名`id`及来源输出包装`body.order.id`不符合现有自由JSON/Agent输出组合契约。最终夹具改为按计划的真实source_item与路径核对Run依赖及持久结果，仍要求真实业务请求只使用POST返回的订单ID。当前运行已加载的旧脚本保存在该目录`executed-test-source.py`；本次失败发生在规划终态断言，尚未运行到旧的固定名称断言。最终夹具的非外部模型专项通过，其多用例执行分支仍待完整实测。

审计汇总 `p2-binding-responsibility-audit-20261020.json` 保存请求阶段、物理发送、数值用量、错误、代码和原始工件SHA256。原请求、SDK计数、PG检查点及stdout/stderr在本机各独立证据目录保留，不覆盖旧失败。共享指令/Schema、max_rounds、timeout、工具权限、固定用例正文和正式校验的语义按上述范围核对。配置仍使用MySQL；计划、版本、运行和业务证据使用PostgreSQL；原件沿RustFS。

部署注意：生成r4新任务前，规划API与规划Worker须统一升级到能读取revision=4的版本；旧r1/r2/r3恢复仍使用原冻结字符串。本轮没有执行生产迁移、修改全局模型配置或部署。Anthropic仍为原生Messages，OpenAI Responses仍为独立transport。
