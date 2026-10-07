# 逐项回执核验与共享消费结束调研

2026-10-07，研究基线a4d6c7e4；生产修改前保存。

## 当前事实与采用做法

- `preparation_scopes.normalize_preparation_scopes`只固定依赖、拥有者和消费者；`plan_mapping`把附属清理挂到最后消费者的after阶段。它没有实际消费结束记录。
- `test_case_shared_effect_research`已有真实POST→GET左→DELETE→GET右的反例：右目标404，清理却成功。`case_cleanup_coverage`/`case_relation_coverage`明确返回consumption_completion_verified=False，不能用清理覆盖声称消费结束。
- 不能等待整个当前RunItem终态才允许其after清理，终态本身要等清理完成；正式项内还可能有produced/cleanup多种效果，不能把item_key或HTTP方法自动等同消费阶段。
- 当前`AgentCaseAssessmentService.build_assessment_request(requested_item_keys=...)`已支持只请求指定项，保留完整固定上下文。但`assess`只接受全部项回执；把上下文裁剪后核验会改变原动作context_sha256。已有原Job/审批/逻辑操作校验必须继续使用完整原上下文。
- 第一项必要实现为复用原核验内核，增加明确的`assess_steps`：请求项集合必须唯一、存在，回执恰好对应这个集合；依然用原完整上下文及持久工具证据，返回逐项结果而不完成RunItem、不写消费结束状态。完整`assess`仍要求所有项，并复用同一内核，避免另建一套判定/输出还原逻辑。
- 不新增配置、表、协议或前端契约。现有配置MySQL、运行/原Job/图检查点PG、原件RustFS保持。历史完整回执与context_hash不改变。
- 本地`Agent轨迹与协议鲁棒性.html`结果/轨迹/终止条件原则适用于本实验；当前ModelRuntimeService及本地HTTP样例作为真实模型传输依据，分别验证原生与LangChain SDK。受控HTTP不是默认外部模型验收。

## 功能实验与后续生命周期边界

创建实际完整八步JSON，上传并读回原件、评审/启用、冻结当前项目计划；通过原Agent图、运行API和审批执行POST/GET。在DELETE的原审批尚未通过时请求/核验当前正式项回执，核对原ToolJob身份与context_hash，证明结果可以从已结束的两次原调用取得，而不报告清理或整个条目完成。核验之后run/attempt/审批/现场对象必须保持原状；再批准原清理，原完整终态及不可重领需实际验证。

真实失败场景先保存，不提前计为通过。错误项集合、缺项/重复/越界、伪造摘要、错误证据来源、False/0/null、必要完整上下文及旧完整回执均需回归。

后续必须研究并接入不可变消费结束检查点及清理动作领取同事务读取：外部消费者以原终态/结果为依据，当前拥有者必须有明确逐项结束证据。清理与普通业务动作要按固定效果声明区分，正式混合项不能一概锁死。关闭后的新业务动作、unknown、恢复/丢回执、多Worker和完整App须另验。当前assess_steps基础不等于实现这些状态或拦住早删；完整P0—P5和默认自主执行目标保持。

实际夹具前两批分别8失败19.58秒和8失败19.53秒：四个live场景先误用只接running的Adapter._load读取waiting_approval，改读原上下文构造函数；随后误判快照只有两条工具记录，实际包含第三条waiting_approval清理。另四个边界均为assess_steps方法不存在。两批不能记为真实逐项功能复现通过。

核对原_snapshot和_load_evidence后，采用保留完整原快照/未结束兄弟调用的路线：逐项请求可展示这些待审批身份，但只允许引用已结束的原Job。完整终态核验仍拒绝任何未结束调用，不能通过删除记录掩盖进行中清理。新逐项核验返回值没有completion；它不等于已保存消费结束或可以清理。

## 实际复现、修复与验证

`pending-reproduction`在修改生产代码前保存四个原Agent图场景的原件和pending.json，8失败21.43秒：四个真实场景被“工具证据尚未结束，不能形成终态回执”阻断，四个边界缺assess_steps。没有模型HTTP，不计为功能通过。

生产实现仅改agent_case_assessment.py：完整assess仍严格要求全项且全部原Job已结束，抽出共享核验内核；assess_steps使用完整原上下文、明确请求项和持久Job，返回结果/核验/使用来源/未结束身份，不生成completion。检查和三种输出来源都通过同一个observe核对原Job已结束；紧凑ToolRecord伪报completed不改变原状态。原日志和身份核对继续，增加逐项计数及run_item_completed=False。

`fixed`8通过22.67秒。新增边界和旧完整路径配对后，`final`4失败11通过24.72秒，四个live失败来自新增断言误按json.dumps字符串比较，PG固定用例JSON键顺序与原件不同；实际八步及False/0/null完整。读取before.json并解析实际untrusted_content后，改结构比较，并核对实际SDK发送messages与冻结请求完全相同；没有因此改生产代码。原失败保留。

最终`verified`15通过26.90秒，0跳过：真实MySQL/PG/RustFS、原Agent图/审批，原生与LangChain SDK各核验期望正确/错误两场景，各一物理模型HTTP。原件8步和最后一步完整，context_sha256等于原PG检查点，待审批清理不能作观察/输出证据，裁剪上下文被原动作身份拒绝。逐项核验run/attempt/审批/现场对象不变；随后批准原清理，完整终态对应passed或failed，现场0剩余，终态不可重领。另11项覆盖请求集合、错误/未结束/跨身份来源以及旧完整路径：旧请求字节完全一致，完整结果只归一核验随机ID和时间后与a4d6c7e4一致。

回归原assessment/execution/context/runtime-input/output-structure五模块及正式混合效果internal-all/external-all两场景：103通过3跳过182.74秒。跳过为两个外部模型及一个独立Redis Worker开关未启用；本批没有重复未改变的默认超时，没有跑完整App/前端。2源/test文件py_compile及原始证据核对另记录。精确cmd/cwd/exit_code、合并stdout/stderr的log和XML均保存在p2-consumer-receipt-*。

**交付仅为逐项核验内核基础。**没有接自动consumer结束检查点/清理领取事务，不能声称已经阻止旧早删反例。正式混合效果同一项的消费与清理仍需明确阶段语义；unknown查证、关闭后动作、多Worker和完整产品仍待研究实施。配置/存储、旧终态与全P0—P5目标保持。

本批审计首次误要求待审批清理已有case_action_admission而KeyError退出1；实际原Job只有绑定、没有执行准入，已完成seed/target才有准入。核对保存metadata后修正审计分支，生产代码未变；audit-first.log保留，不计功能失败或通过。
