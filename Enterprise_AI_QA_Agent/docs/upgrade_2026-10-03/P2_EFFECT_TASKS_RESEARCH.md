# 分任务证据规划：实现、恢复与默认 Step 未通过项

基线 `28fc566c`。保持完整P0—P5目标，本批没有完成默认AI执行验收。

## 依据与设计

原 `planning_evidence_references.reference_effects_prompt` 在同一响应要求传值、产生及清理声明；真实Step多次在此阶段length/8192/正文0。`case_effect_relations.compile_relations/validate_relations` 已负责固定预期、来源和路径的确定性校验，原ToolJob事务保存实际输出。不能再让规划阶段成为另一套可执行脚本，也不能删除完整来源来让测试变绿。

采用现有阶段配置的第三种策略 `case-planning-staged-evidence-v3`，没有新增测试模式或第二套模型框架。可编辑项目策略仍沿现有MySQL配置API存储；新Prompt、完整逻辑决定、回执、查询和剩余预算沿现有PG seed/checkpoint保存。原件仍RustFS。旧v1/v2默认及旧seed不改；前端类型、项目设置、父选项取消联动和后端验证同步。

```text
项目完整用例/来源 → 固定逻辑 → 传值绑定 → 产生声明 → 清理声明
→ 合并原 canonical 声明 → 既有校验 → 原套件/运行/Agent
```

按固定清理关系保留必须的产生/清理范围，AI可在传值任务注明其他必要产生角色；没有该角色时不添加空调用。逻辑缺少内部清理关系不能在技术阶段擅自改写。所有阶段共享原max_rounds和deadline，不另增轮数、期限或自动重发。完整用例、原文、工具契约仍提供，模板不进入执行。

新Schema分别接收传值和单角色声明，引用沿已有E/T绑定器还原。每块先校验固定项、引用、字面量预期及输入依赖，最后沿原关系/覆盖校验合并。已接受回执单独追加，不改变原stage固定逻辑；PG拒绝改写/丢弃已接受回执、跳步或不对应原调用的追加。查询仍只读取原固定来源。没有新增数据库表/列、依赖、模式或供应商字段。

本地专业信源 `项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html` 强调结果与轨迹、状态恢复、幂等及未知结果分别检查。本批采用已有项目冻结/检查点/原事务规则，没有移植课程示例为新的执行引擎。

## 验证及纠错

- 用真实成功声明原件拆分并重新合并，canonical最终决定精确相同，完整JSON末尾false/0/null保持；旧真实失败seed/checkpoint精确序列化。
- 实际MySQL/PG/RustFS分任务专项：初批5通过10.20秒；随后含合同15通过11.89秒；补充坏引用反馈和真实固定工具契约查询后18通过13.00秒。原回执提交后丢失、清理响应提交后丢失、当前策略/文档变化恢复、原3轮预算耗尽均验证；终态重投新增模型调用0。它们是受控模型，不证明Step质量。
- 旧链路实际回归首次17失败/53通过：本轮抽取共同校验后遗漏无目标分支的`versions`引用。查到原调用点后改用共同`validate_plan`，原失败证据不覆盖；独立目录复跑70通过64.57秒，含实际md/docx/pdf解析、来源读取、引用、场景和规划恢复。
- 扩大回归首次78通过/1失败/53跳过：旧测试把当前工厂与186679a8比较，而录制正文指导早已变化。AST提取验证本轮三种工厂输出与28fc566c完全相同；测试基线改为真实前一提交，旧快照精确读取断言仍在。复跑79通过53门控跳过7.34秒。没有用该修订覆盖本轮引入的无目标错误。
- 最终保护指令补充后，合同/实际分任务/原对象证据及旧快照29通过9门控跳过13.97秒。与上述有重叠，不相加作功能数量。
- 前端项目对话框83通过3.26秒；npm run build（含vue-tsc）exit0，Vite10.04秒。范围为设置联动和构建，不是完整App实操。compileall及diff检查实际exit0。

## 两批真实默认 Step 结果

均使用新隔离项目、完整八步JSON用例、正式生产API/规划CLI，原180秒/8192及显式隔离JSON/low profile；全局MySQL模型参数保持。未改模型适配器。

1. `p2-effect-tasks-default` **1失败130.96秒**：6次SDK/6次物理HTTP，均200。规划两轮完成，但AI省略内部cleanup_sources且角色为空，因此只完成传值任务，**不能证明拆分解决原截断**。Agent实际POST两次、GET一次、DELETE两次，两条本次记录均删除；正式回执未逐项对应固定执行项，结果error。此批只读纠正未启用，没有终态重投验收。
2. 依据实际遗漏补充“内部清理与外部依赖分开”的固定逻辑说明及角色指导；`p2-effect-tasks-default-lifecycle` 在隔离策略启用已有一次只读回执纠正，但未进入该阶段。**1失败207.65秒**：3次SDK/3次HTTP200。logic18.625秒stop/正文1122，bindings69.357秒stop/正文412，produced90.504秒被原总期限取消，正文0、无finish_reason、已观察completion_tokens6621；不能称length/8192。保存逻辑及绑定回执、interrupted调用与failed终态，未创建Run/业务HTTP0，不重发。

第二批证明冻结分任务确实进入产生任务并保留先前回执，也证明单角色技术声明仍未在原预算内完成。不能以受控样本或第一批跳过声明的规划成功称默认AI通过。现有只读纠正仍需真实API/UI成功和故障恢复更广验收。

为下次失败定位，默认模型夹具新增白名单公开回执快照导出，只选公开response/final_response、身份及计数，不导出整个图或私有推理。第二批没进入执行，该导出路径尚未被本轮真实模型触达；第一批原终态公开回执未导出，不能补造原件。旧Worker汇总原件恢复也未在本轮解决。

## 下一步

先核查真实产生任务的完整工具模型、包装路径及文档预期是否存在歧义，再审查“执行前推导所有技术路径”与Agent按完整用例实际执行的职责边界。不能继续相同请求盲试、扩大预算、强迫拆分完整正式用例或伪造清理/回执通过。多候选项目选择、实际准备/未知副作用/修订、默认UI知识、其他模式/App、普通文档模块生成及剩余完整P0—P5要求仍保持。

## 扩展现场复核（2026-10-17）

为确认旧研究中未覆盖的分任务分支，运行现有 `test_planning_effect_tasks_live.py::test_actual_separate_receipts_restore_without_resending_completed_tasks`，没有改生产代码或改变模型协议。环境使用隔离 MySQL、PostgreSQL、RustFS 和实际 PostgreSQL 规划任务表；模型是测试用受控模型，业务 HTTP 明确为 0。

`p4-effect-task-live-current.xml/log` **7通过、0失败、0跳过、18.65秒**，覆盖 `none`、`bindings-ack`、`produced-ack`、`cleanup-response-ack`、`budget`、`unknown-reference`、`produced-query`。每种变体均保存 `verification.json`，验证已接受的分任务回执不会被后续策略/文档改写，回执提交后丢确认可从 PG 检查点恢复，坏引用被拒绝，产生阶段查询仍沿原预算继续，终态重投不新增模型请求；`business_HTTP=0`，因此不把它扩大为业务执行或默认 AI 质量通过。审计见 `p4-effect-task-live-current-audit.py` 和 `p4-effect-task-live-current-validation.json`。

这批把“分任务规划回执与恢复”从旧的五变体受控证据扩展到七变体实际存储验收，但没有关闭共享对象实际消费完成、未知副作用只读查证、运行中计划修订、正式 Run/Agent 执行或默认 AI 多用例闭环。

## 默认 Step produced 阶段现场根因复核（2026-10-18）

用户确认 Step 账号已认证，继续真实模型验收。先读取 live16 的真实 PostgreSQL 规划快照、完整八步 JSON 用例、上传需求文档、每轮冻结请求、SDK/HTTP 记录及当前规划/工具契约代码；不重发同一请求，不改生产模型配置。

### 实际失败阶段

live16 共四个逻辑规划轮次：logic 成功；第一次 bindings 因模型返回已知证据的完整 UUID，而本轮合同要求 `E1/E2` 短引用，被确定性验证拒绝；第二次 bindings 按短引用成功；produced 调用收到 HTTP 200，最终 `finish_reason=length`，`completion_tokens=16384`，正文为空。任务约 392 秒后以 `incomplete_response` 结束，尚未到 600 秒规划截止，没有创建 Run，目标业务 HTTP 为 0。

逐轮产物保存在 `p2-default-coverage-live16/PG-planning.json` 和 `physical/`：bindings 失败输出 696 字符，随后修正为 513 字符；最终 produced 请求的 user 输入 17,692 字符、system 输入 6,840 字符，传输为 OpenAI-compatible Chat Completions `json_object`，模型为 `step-5-preview`。这排除了“最终请求因巨型上下文超时”的说法；也没有证据证明提高期限会有帮助。`finish_reason=length` 和精确达到该隔离请求的输出上限表明，本次 produced 回复被生成上限截断，不能用空内容解释为合法的空效果声明。

### 执行契约与职责边界核验

1. `capture_tool_operations()` 冻结 `api-test-runner.execute_task` 的命名操作输入与 `ApiTestTask` 输出变体；native 结果通过 `task_result` 返回。执行器运行每个真实 API task 时才取得真实 status、headers、body 和 checkpoint。规划阶段不能预先制造这些运行结果。
2. 正式用例自身包含两条 POST、一次 GET、两条 DELETE、字段断言和两个实际 ID 的依赖传递。规划需要知道操作间的动态输出依赖及清理关系，但绑定阶段已确认没有外部 ID 依赖；后续由执行器根据完整正式用例运行这些操作。
3. `planning_effect_tasks.py` 当前让模型按固定 item 声明 `produced` / `cleanup` 角色，最终再映射回现有 canonical effect relation 校验。没有证据表明 adapter 把模型调用转换成了其他线协议，也没有证据支持跳过真实运行时对象来源检查或直接删除 canonical 校验。
4. 失败的绑定短引用可解释一次额外模型调用，但即使消除这次约 50 秒重试，产生阶段仍以达到 16,384 completion-token 上限失败；因此它不是本次规划未完成的根因修复。

### 调研结论与下一步

本次没有发现适合立即合入的安全生产代码修复。不能通过换 Anthropic/Responses 协议、全局压低 reasoning、增大生产 token/超时、放宽来源校验、虚构效果路径或删除执行证据来掩盖。现有适配层已按 `openai_chat_completions`、`openai_responses`、`anthropic_messages`、Gemini 分传输映射，Anthropic 调用仍为 `client.messages.create`；本次 Step 请求走的是 OpenAI-compatible Chat Completions。

下一实验应换成有项目文档和真实 ToolRuntime 契约的单目标、少操作用例，保持同一策略、Step 协议和当前生产预算，记录规划/执行器/HTTP 证据，用来判明成本来自效果声明规模还是 Step 对单角色 Schema 的推理；若仍截断，再以输入中可确定性派生的部分为边界设计服务端预填充候选，经现有校验与 AI 审阅，不直接自动接受。只有小场景能稳定完成后，才推进正式八步用例的下一次闭环。此前的 live16 失败证据保持原样。

### 单创建/清理场景复测（live17）

为验证上述假设，扩展既有真实隔离验收夹具的用例形状开关，不改生产代码、模型全局配置、协议和预算。新建 Markdown 需求（一次 POST、使用真实返回的 `order.id` 做一次 DELETE）及对应两步 JSON 正式用例；保留相同真实 Step、`low + json_object`、分任务回执、来源身份、链接来源、证据引用和执行项操作预读。隔离 MySQL 配置快照显示 max output 8192；规划期限为默认 180 秒。文档原件确实上传并从对象存储读回；仅使用本地 HTTP 业务夹具。

`p2-default-coverage-live17.xml` 实测 **1失败、221.65秒**。规划只有三轮：logic 24.24秒通过，bindings 46.99秒通过，produced 在剩余 108.02 秒达到规划总期限后中断。3 次模型物理 HTTP 均为 200；produced 流收到 3,189 个 chunk，reasoning 字段 30,672 字符、completion usage 6,964 tokens，content 正文 0，`finish_reason` 未收到；不是 8,192 token `length`。PG 任务以 `planning_failed` 结束，Run 未创建，目标业务 HTTP 为 0；生产配置在夹具 `finally` 断言中与启动时一致。

请求快照显示 produced user 输入 17,103 字符、system 6,840 字符；输入中 evidence 8,374、工具目录 2,537、证据短引用目录 2,028、linked source snapshot 932、completed bindings 681 字符。正式用例仅两步且只有一个创建对象，却仍与八步 live16 的 produced 请求输入同量级（17,692 user 字符）。这不支持“步骤数/操作数本身是主要成本”的简单解释；但输入长度和 reason 字符数不能揭示私有推理内容，不能单独证明具体哪个 Schema 字段导致耗时。

**调研结论更新**：同预算下，单目标创建+清理也无法在 produced 阶段完成。因此下一步不再缩减业务步骤或重复此场景；应沿当前提示构造链拆分 produced 请求的静态系统指令、所需原始证据、API ToolTask 模型/执行器包装契约和 canonical 声明 Schema，找出可由现有冻结事实确定性提供的重复描述。只有证明某字段可从已固定的正式用例/ToolTask 与父子关系无歧义派生，并在独立验证中证明不损失覆盖/恢复/项目适配，才实施服务端候选预填；不自动假设所有 API POST 是副作用，不改变最终关系校验。live17 原件完整保留。

### 当前提示与运行证据边界核查

live17 之后继续沿 `planning_workflow.py`、`planning_effect_tasks.py`、`case_planning_effect_tasks.py`、`case_planning_references.py`、`case_effect_sources.py` 和 `case_effect_evidence.py` 追踪 `produced` 任务。现有工作流在逻辑选择后确实固定完整案例、工具操作契约和已接受的绑定回执；`ProducedTaskDecision` 要模型为固定 item 产出声明。Schema 还要求每份声明指定真实输入 `select_when`、真实输出 `object_path_tokens`、至少一项与完整固定依据比对的 `checks` 及证据引用。正式运行时服务端编译固定依据，实际 ToolJob 返回后再核实原 Job 身份、输入绑定、响应哈希并保存对象证据；清理覆盖最后由既有 canonical 关系校验验证。

这些路径/选择/预期无法仅从 `ApiTestTask` 的字段列表唯一推导：JSON 响应体字段和请求操作语义由具体项目文档及用例决定；不能将所有 POST 等同“产生对象”，也不能在运行前用占位响应伪造实际对象。故当前**不能安全地将整个 produced 阶段改成服务端自动声明，也不能删除它**。一个可继续评估的候选是缩小角色请求的冗余指令/Schema表达，同时保留模型对项目语义的判断及原有服务端校验；实施前需逐字段映射可从父回执、固定逻辑、完整用例或工具快照直接计算的来源，并用至少两个不同 API 形状、含负向/重复调用的真实运行对照。

这项代码读取没有发现足够确定的生产修复，故本批只调整验收夹具和调研记录。适配层、模型请求参数和生产 MySQL 策略均未修改；小场景默认链路尚未过 planning，因此不能宣称正式执行功能可用。

### JSON Schema 标题注释缩减与同预算 Step 复测（2026-10-18）

检查三个 effect-task Pydantic Schema 的生成来源及消费方后，确认提示中内嵌的是 `model_json_schema()` 的文本副本；真正接收规划回执时仍由原 Pydantic 模型和 canonical 校验验证，模型 transport 参数、输出格式和校验模型均不依赖 Schema 的 `title` 注释。递归省略 JSON Schema `title` 只影响发给模型的说明文本，不改 required、properties、类型、枚举、引用、默认值和边界约束。

依据这一范围做了局部改动：`effect_task_prompts()` 只对待嵌入提示的三个 Schema 副本递归移除 `title`；运行时模型 Schema 原样保留。新增结构测试逐任务对比“提示Schema”与“原Schema去除title后的结构”，不是只测字符串长度。原 Schema 字符数分别为 bindings 3,300、produced 6,050、cleanup 6,048；移除后分别为 2,626、4,899、4,898，produced 提示Schema减少 1,151 字符。生产输出限制、deadline、MySQL模型配置及canonical执行校验均未调整。

确定性回归命令 `python -m pytest -q tests/test_planning_effect_tasks.py tests/test_planning_effect_tasks_live.py tests/test_planning_operation_prefetch.py tests/test_planning_item_operation_selection.py tests/test_scene_query_contract.py` 实际 **39 passed、25 skipped**；门控跳过不计通过。

真实验收沿用 live17 的隔离MySQL/PG/Redis/RustFS/API/Worker、已认证默认 Step、`low + json_object` profile、单创建/清理文档及正式两步用例，并启用相同 operation selection、linked sources、evidence references 和 effect tasks 策略。真实模型实际 HTTP 请求三次均有 SDK 流；live18d **1失败、200.17秒**。逻辑阶段 62.97秒、bindings 40.93秒，produced 流在剩余期限耗尽时被取消（74.86秒，4,703 completion tokens，正文0，未收到 finish_reason）；规划失败，未创建 Run，目标业务 HTTP 为0。没有保存推理正文或响应体。请求追踪显示 produced 请求 JSON 由 29,331 字节降至 27,886 字节，system 字符由 6,840 降至 5,689，user 字符从 17,103 到 17,073；整体缩短约 1.4 KB，但真实 Step 仍未完成 produced 声明。

因此保留这项无验证语义变化的提示压缩，但不把它称为规划超时修复。它证明了可减少冗余Schema注释，不证明模型决策质量、推理速度或完整用例执行改善。原 live17/live18d 失败证据均保留。准备态、未知副作用查证、运行中计划修订、跨多个候选的排序补全和正式Run闭环仍需真实验收；下一步应继续核对 planning 失败前其余可省略内容是否是重复的确定性上下文，避免以更高预算掩盖问题。

### Accepted bindings 证据继承单变量对照（2026-10-18）

沿 live18d 的冻结 checkpoint 与 produced 请求逐字段审查后，找到一个可确定性派生的候选：同一 `item_key` 的 `evidence_ids` 已存在于此前已接受的 bindings 回执；produced 声明再让模型原样复制会增加输出要求。先从 accepted bindings 构建按 `item_key` 的继承投影并写单元测试：保留 produced 声明次序；只对目标 item 精确映射；缺失、重复、额外或未知 item 一律拒绝；原 wire 声明不被修改。该测试用例的4项确定性断言通过。

随后使用 live18d 的真实冻结请求/checkpoint 作一次**独立只读模型调用**，仅省略 produced Schema 和提示中的重复 `evidence_ids`，来源限定为同 item_key 的 accepted bindings。仍使用已认证 Step、原 `low/json_object`、8192 max tokens、隔离 MySQL profile 和原 produced 阶段约74.87秒窗口；不恢复原任务、不暴露业务工具、不调用业务 HTTP、不接受 canonical 结果。源请求与 checkpoint 哈希记录在 `p2-produced-inherit-bound-evidence-current/source-verification.json`。

该对照 **1失败，74.879秒**：单个 SDK/物理 HTTP 请求，HTTP 200；流持续73.964秒、2,103 chunks，reasoning 与 reasoning_content 各显示20,850字符（视作同一内容的重复呈现，不相加），正文0、SDK迭代未完成、无 finish_reason；usage 为 prompt 7,785、completion 4,532。调用按原阶段窗口取消，未收到响应；canonical未接受、业务HTTP为0、全局模型配置保持不变。证据在 `p2-produced-inherit-bound-evidence-current/` 和 `p2-produced-inherit-bound-evidence-current.xml`。这否定“仅复制 evidence_ids 是该 produced 阶段超时根因”的假设，不否定其为可确定性继承字段；因没有真实声明输出，继承投影尚未在成功的端到端阶段验证。

**决策**：不把这个实验变成生产改动，也不再重复同一请求/同一字段对照。Schema 标题缩减及 evidence 继承两项输入简化均未让默认 Step 在相同 produced 窗口内返回正文；这使“继续微缩提示就能解决问题”的依据不足。下一步回到模型调用轨迹与规划角色契约的完整调研，优先确认已有数据能否区分 SDK逻辑调用、重试和物理发送，以及正式方案是否允许规划拆分而不损失模型项目语义判断。任何候选必须先通过对照代码/契约证明行为正确，再用至少两个不同 API 形状（含负向或重复调用）覆盖成功声明、canonical接受、恢复及原始预算；在此之前不提交生产实现，不增加预算，不削弱校验。按用例执行仍未创建 Run，完整 P0—P5 继续未完成。

### 默认模型回执纠正进程中断现场复核（2026-10-19）

执行 `tests/test_default_receipt_process_recovery_live.py -k invoking`，使用隔离 MySQL/PostgreSQL/Redis/RustFS、独立 API/Worker、实际已认证 `step-5-preview` 和本地健康 HTTP。规划与执行模型调用均真实完成：最终回执流 HTTP 200，439 个 chunk，`finish_reason=stop`，正文 1,721 字符，SDK usage 为 prompt 13,883、completion 1,426；初始只读 GET 业务请求只发生 1 次。

该用例 **1失败，104.42 秒**，失败点是测试等待“回执纠正请求”的屏障时一直没有出现纠正 marker。执行日志显示首个回执已通过完整性/结构检查并进入 `waiting_approval`，审批后没有触发 `AgentCaseReceiptService.correct`，因此没有可杀 API 的 `invoking` 边界。失败原因是验收夹具假定默认模型必然产生无效回执，不是恢复代码或模型协议错误；不能把本次结果计为纠正恢复通过，也不能通过延长轮询或重发审批掩盖分支未被触发。

受控 HTTP 的 `test_receipt_process_recovery_live.py` 仍负责确定性制造无效初始回执，并已覆盖真实生产 Worker 在 `invoking`/`response` 两个持久化边界杀 API、自然租约过期、恢复重放和终态重投不新增业务/模型请求。默认模型本次只证明正常回执路径，真实默认纠正边界仍未验收；若要验收该边界，需要在不改生产逻辑的测试夹具中明确制造可验证的结构错误，再单独保留真实模型正常路径证据。
