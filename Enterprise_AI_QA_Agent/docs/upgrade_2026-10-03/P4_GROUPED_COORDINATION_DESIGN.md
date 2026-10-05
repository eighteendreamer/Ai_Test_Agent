# 分组协调与逐证据处理协议

研究基线f5705aef。此步骤属于完整P0—P5，不能以协议或兼容检查代替完整交付。

## 已查证依据 → 采用方式

- impact_workflow.execute/_unit逐证据发现；_read_result要求单元候选等于响应候选。不能把组级候选复制到全部成员来满足旧结果格式。新增coordinator-v2任务结果与逐证据覆盖，旧v1按原Schema/请求/结果读取。
- impact_reasoning_plan及服务已有完整成员、固定选择、来源和PG/RustFS检查点。v2消费该计划；组锚点沿用现有selection.unit，组成员由冻结计划绑定，现有call_key/租约/补读/预算/unknown机制继续使用。
- impact-response-v3的T目录来自真实读取；v6请求/v4 wire继续使用T，并新增逐证据member_ref/outcome/reason。读取元数据不代表原文已读，声明覆盖必须核对实际消息里的完整分段/差异。
- response_checks已按call_id/validator_version做不可变主键。新增validator-v2及canonical决定v2，旧v1核验不能回写；规则传递必须读取原checkpoint版本，不从默认核验版本取另一个答案。
- 现有Skill要求同时读三方，与发现单元阶段冲突。新任务冻结阶段说明，发现阶段读组内原文和差异，用例阶段读规则/完整JSON/当时来源；整体仍三方分析。旧任务保存的Skill/Prompt不重写。
- 现有公开view和Vue依赖units/rules。v2保留全证据units及完整case判断，另展示discovery_tasks及计划引用；成员units不复制候选，逐证据reason与处理状态来自模型实际声明。snapshot.unit_count依然全范围，不偷换成组数。
- 本地Agent轨迹与协议鲁棒性、评测工程化课程强调阶段/工具轨迹及数据/版本可追溯。采用真实请求与范围验收，不采用课程示例阈值。

## 实施及验证

新增版本化Schema/绑定/核验，复用现有调用和持久响应服务；随后接组调度及完整用例来源预读、结果检查点/恢复、正式API/前端。每一步运行原v5实际失败输入和历史请求，实际MD/DOCX/PDF上传解析比较/存储/HTTP，遗漏覆盖、补读、unknown、回执丢失与终态恢复。接入生产后另跑API/独立CLI，再跑真实默认模型完整3条8步用例及人工业务基线。保留失败，不复制答案、不放宽事实核验、不暗增源模型参数。

字节分组不是Token预算；发送仍经过既有estimate。无合法规则与未完成发现明确呈现，不能把没有进入用例判断说成无影响。配置继续MySQL，业务记录PG，大对象RustFS；新增参数必须在新提交冻结。

## 实际结果与契约纠正

实现已接生产调度、独立API/Worker及正式App。九模块166通过576.99秒、生产三场景3通过186.01秒、完整App1通过86.35秒，前端24通过/构建通过。真实文件、完整JSON/原来源、原HTTP、配置MySQL、检查点PG、对象RustFS各有保存证据。受控模型通过不表示默认模型业务通过；完整P0—P5保持未完成。

首次真实默认模型4逻辑/6物理发送，发现返回三项变化及八条证据声明，但结束unit_outcome=null。查证DocumentImpactGroupedWireDecision继承的可空字段和DocumentImpactDecision.validate_action的结束必填约定不一致，新v6提示也没说明必填。保存真实失败不修改；对齐分组wire的动作校验、字段描述与冻结指令，finish必须明确analyzed/evidence_insufficient、read为null，不从成员声明自动代填。新的实际失败正文回归拒绝原null响应；新完整受控响应仍接受。旧任务读取已有原核验，不用当前Schema改写历史判断。

最后63项三模块174.49秒通过、独立生产MD1通过58.60秒。再次真实默认模型发现请求60秒SDK超时，SDK重试到逻辑120秒截止后unknown；三条完整用例均收到依据不足的结束响应，核验通过。结果8unknown/3evidence_insufficient、规则0、partial；源模型/输出/SDK/逻辑预算没有暗改。原v6首次8rejected/1unknown/2insufficient保留。当前有契约修正的受控成功依据，没有真实默认模型完整发现+三条用例的成功证据。

初始回放6失败、真实7失败、供应商失败分类1失败、生产旧断言1失败及随后缩进收集错误均保留；详见PLAN6.10和IMPLEMENTATION。接下来优先查证实际响应耗时/推理预算及传输契约，避免再次未变更重复请求，保留unknown与终态不自动重发；需求评审/矩阵/增量及其余P0—P5仍在原范围。
