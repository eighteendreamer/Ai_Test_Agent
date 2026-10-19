# P2 文本引用 Schema 与解析约束一致性

2026-10-19。完整 P0—P5 继续进行中；此处只报告具体失败与对应修复。

## 真实失败

`p2-default-single-receipt-finish-live4-20261019/` 的真实默认 Step 复验：1 failed，443.53 秒。5轮模型调用均有完整响应，任务最终 failed，未创建 Run，目标业务请求为0。配置在隔离 MySQL、计划及检查点在 PostgreSQL、原件在 RustFS；16K输出/600秒规划时限仅为隔离诊断值，全局配置没有改变。

第3轮 produced 把工具契约 E3 当作业务文档预期来源，被正确拒绝；第4轮产生声明通过。第5轮 cleanup 在 T4 引用里复制了原文到 `excerpt`，虽已提供 `literal=order.id` 与 `encoding=text`，仍被解析器拒绝。错误消息笼统写成“T引用需要literal/encoding”，实际违反的是 `excerpt` 必须为空。

## 根因复现与采用做法

发给模型的 Schema 将 E/T 共用一个对象，允许 `excerpt`、`payload_path_tokens`、`literal` 和 `encoding` 各自为空或非空；Python 解析器另用跨字段校验施加约束。于是模型可以输出满足声明 Schema、但被实际解析器拒绝的对象。不是断网、认证或生成截断；增加重试并不能修正契约。

从 PG 第5轮原始公开响应提取 `tests/fixtures/planning_cleanup_reference_with_excerpt.json`，不含认证信息或私有推理。通过已有测试依赖 `jsonschema` 校验实际发出的 `effect_task_schema`：真实样本与3个相邻非法形状均出现 Schema 允许、Python 拒绝。修复前回归为 **4 failed、3 passed，1.20秒**。

依据项目已有 Pydantic 联合类型（`CasePlanItem` 等）表达三个互斥来源形状：

| 形状 | 约束 |
|---|---|
| E 文档字段 | E引用、明确字段路径；不携带文本解码参数 |
| E 文档原文标量 | E引用、路径、摘录、唯一字面值及解码方式齐全 |
| T 已定位原文标量 | T引用、字面值及解码方式齐全；路径和摘录由系统提供，模型字段为空 |

仍使用原 reference/payload_path_tokens/excerpt/literal/encoding 字段，不新增业务协议或执行引擎。有效的既有 v2 序列化形状保持；v1解析、历史计划和历史结果保留。来源是否真为文档、原文是否唯一、业务预期是否有效，仍由原目录绑定与canonical校验负责，不让 Schema 代替证据校验。工具 E3 冒充文档仍会被拒绝，不能声称本次已经解决所有模型引用错误。

v2 引用的工作区接入涉及策略、分阶段提示/解析、声明任务、场景查询及目录绑定；交付需同步这些实际消费方，不能只提交联合类型。没有引入新依赖，Provider 原生协议及现有适配层不变。

## 已验证与边界

修复后运行：

```powershell
uv run --project . python -m pytest -q tests/test_planning_reference_schema.py tests/test_planning_effect_tasks.py tests/test_planning_evidence_references.py tests/test_case_effect_relations.py
```

结果 **40 passed、21 skipped，6.80秒**。新测试核对真实失败样本、3种非法文本形状，以及3种既有有效引用的Schema/解析/序列化一致性；另有既有产生/清理声明及canonical绑定回归。跳过项需要各自实时存储开关，不计通过。

新的真实默认 Step 复验：`p2-default-single-reference-schema-live5-20261019/`，结果 **1 failed，626.71秒**。仍为同一个完整正式用例、同样隔离参数和原5轮额度；规划4轮完成、创建正式Run，目标端POST 201和DELETE 200各一次，订单最终为空。一次只读回执纠正成功，四个实际验证点通过，但Run正式结果blocked，不能计入完整通过。

核对PG原工具输出和冻结计划，确认两个新问题：首次execute_task缺task.path，原生验证返回invalid_task_payload和http_request_sent=false；改参数复用原operation_key被正确拒绝，新operation_key后才实际创建。这个确定未发送请求的失败仍进入产生来源unknown。另一个问题是produced和cleanup的object_path_tokens均为[]，所取整个工具输出包含不同操作元数据，不能证明同一订单被清理。不能简单禁止根路径（某些工具根值本来就是对象身份），也不能忽略未知副作用或以HTTP状态代替对象匹配。下一步须查实际输入Schema、执行生命周期及对象身份契约，再用真实样本回归。

多候选选取、正式前置补入、排序/真实输出传值、运行内准备以及在途计划修订继续单列，不能用本次单用例实验替代。

## 扩大回归与实际恢复

首次扩大实时回归命令漏设 `QA_EFFECT_RELATIONS_DIR`，14项在保存验收材料时失败；另4项旧测试仍断言没有 `+linked-navigation-v1` 后缀的版本。结果18 failed、83 passed、1 skipped，65.14秒，不计为通过。核对现有 `linked_context_prompt_version` 和冻结导航实现后，仅修正旧测试的预期后缀，设置独立证据目录，重新运行 `tests/test_case_effect_relations.py`：**24 passed，50.94秒**。未放宽生产校验。

`RUN_LIVE_CASE_PLANNING_TASKS=1`，`QA_EFFECT_TASK_DIR=.../p2-reference-schema-durable-20261019` 下运行 `tests/test_planning_effect_tasks_live.py`：**14 passed，28.25秒**。v1/v2各覆盖正常、bindings提交回执丢失、produced提交回执丢失、cleanup响应提交回执丢失、额度耗尽、未知引用和produced补查。实际MySQL/PG/RustFS、新Store/Handler恢复；已保存任务不重发、原期限/轮数保持、终态重投无新增模型调用。模型响应受控，不当作外部AI业务正确性证明。

后续新增“工具契约不能冒充业务预期”v2回归；相关Schema、声明及目录测试重跑 **35 passed、3 skipped，1.48秒**。跳过项是独立实时目录测试，此前扩大命令已经实际执行这些场景。

跨层搜索发现前端类型只声明v1，启用独立声明任务会把已加载v2改成v1。同步类型，启用时保留已有引用协议，新启用默认仍沿原v1；新增页面保存/启用的契约回归。`npm test -- src/features/projects/CasePlanDialog.test.ts`：**84 passed**；`npm run build` 中 `vue-tsc` 与Vite生产构建均通过。此处是组件行为与构建验证，不冒称真实完整浏览器操作验收。
