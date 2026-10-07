# 准备输出组合与固定消费者传值调研

2026-10-07，基线55475b8e；生产修改前记录。

## 已核对事实与采用做法

- 原`CaseStepReceipt`只有`outputs: dict[str, CaseToolEvidenceReference]`和`output_source`。前者只能为每个顶层字段选一次工具返回，后者只能复制一次工具返回的完整对象。
- `agent_case_assessment.assess`实际按以上两种方式构造`preparations/<key>/output`；不是规划里的原生API `task_result`。`plan_mapping.source_output_path`和`agent_case_context`从该固定回执读取后续绑定。
- 单个API `execute_task`有一个返回事实。两个POST形成两个原ToolJob；各自产生ID不能通过现有回执组合成`ids=[第一个实际ID,第二个实际ID]`。要求模型虚构数组实际值、只保留最后一个ID或按调用次序推测业务角色都不符合目标。
- 现有`test_case_shared_effect_research`已证明原Agent图、API、审批、MySQL策略、PG运行及RustFS文件链路能执行单个共享对象。本批沿用这条真实链路，扩展为两次创建、两个消费者、两次清理，不另建执行器。
- 保留原回执字段，追加可选`output_structure`，使用明确的reference/object/array节点；每个叶子仍是已有call_id及字面量输出路径，服务端从原Job读取。对象键、数组顺序由模型声明，实际值由系统还原，不自动猜测输出含义。
- 新字段缺省序列化时省略，旧回执字节和两种输出方式不变；禁止同时声明多个输出方式。新树只存在回执，不修改固定执行上下文或context_hash，不新增配置、数据库表、模式或依赖。
- 现有Schema已使用Pydantic的kind判别联合；递归节点按[Pydantic前向引用](https://docs.pydantic.dev/latest/concepts/forward_annotations/)及[Schema重建](https://docs.pydantic.dev/latest/concepts/models/#rebuilding-model-schema)定义。使用项目当前安装版本实际解析/生成Schema验证，不靠记忆假定。
- 本地课程`项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html`要求结合结果、轨迹和协议。验证必须包括完整JSON原件、真实业务HTTP、原Job与审批、固定结果、消费者引用及终态不重领。

## 验收要求与边界

完整8步JSON文件上传、草稿评审/启用、当前项目规划及冻结；Agent通过既有工具创建两条真实订单，源回执组合ids数组和包含False/0/null的嵌套对象。第二消费者使用固定结果绑定的ids[1]，清理两条实际ID。每次操作继续原工具权限/审批、逻辑操作身份及持久回执；最终核对两个消费者与现场对象。

原始失败输入、配置、消息和工具记录先保存。Leaf不能写实际值；未知call_id、错误输出位置、未完成来源与混用输出方式必须明确处理。旧字段继续回放相同JSON。不得把新增受控图测试说成默认外部模型或完整App通过，也不得把数组组合与共享消费完成/未知查证混为一项。

## 实际复现、根因修复及验证

生产修改前，p2-output-structure-before实际2失败12.19秒：新树字段extra_forbidden；真实图已创建两条数据并查询第一条，最终回执校验失败。没有运行第二消费者/清理，现场2剩余，原失败输入与日志/XML保留。

改三处原路径：Schema的kind判别递归树及缺省省略，assessment复用observe还原所有原叶子与原来源终态核验，结果契约说明及Prompt v3。新增结构日志只记身份、节点种类和引用数量，不记值。不是按业务角色猜数据、不是新增执行器；输出结构与原对象证据范围分开。

首次修复2通过9.89秒；增加旧canonical字节/各类实际证据边界及真实后续输入核对后，最终15通过14.50秒、0跳过。两份真实八步JSON、MySQL/PG/RustFS和原图/审批/运行API实现2POST、2GET、2DELETE，两个消费者passed，原对象现场0剩余。ids/1、False、0来自已保存源回执实际注入。执行模型受控，无外部模型HTTP。

四原模块回归86通过3跳过170.49秒，跳过实际外部回执模型、实际完整上下文模型、独立Redis Worker。四文件py_compile实际退出0；项目pyproject没有独立lint配置。本批未改前端契约，没有重跑前端/完整App或默认外部模型。共享消费完成状态机、unknown只读查证和完整生命周期不能以本场景代替。

精确命令、cwd、exit code及完整stdout/stderr在p2-output-structure-{fixed,final,regression}.command.json/.log/.xml；失败before的原日志/XML保留。核心方案及当前状态同步，历史工件不改写，审计见p2-output-structure-validation.json。

本批审计首次KeyError: value/exit1，脚本误猜已保存策略DTO；读实际policy字段后修正并退出0，原audit-first.log保留。46当前工件和35上一批原始HEAD字节核对通过。测试临时数据库/对象/本地服务器按归属清理，保存证据为当时快照。
