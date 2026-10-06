# C2c1：固定执行项与工具调用关联

研究基线：38643f9c。完整P0—P5继续实施；本步为运行中动作处理的必要基础，未实现逐动作适用性限制、计划修订或未知副作用核对。

## 查证与采用方式

| 现有依据 | 采用方式 |
|---|---|
| `plan_mapping.py`、`agent_case_context.py`保存owners和before/case/after | 由固定上下文确定调用归属，不通过HTTP方法、工具名或模型call_id推断 |
| `CasePlanningService`将MySQL规划策略冻结到计划/任务 | 在原`test_case.planning_policy`增加显式可选协议，已有快照缺省保持原JSON/hash；项目页面设置只影响后续计划 |
| `ModelToolCall`、模型适配器及Registry工具结构 | 不改模型调用类型；在新策略的工具参数schema中增加`_case_action.item_key`，原消息保留这个选择，进入处理器前分离 |
| 原图工具执行、DeepAgent治理、ToolRuntime、ToolJob及原审批scope | 共用一个绑定函数，PG原Job及审批保存服务器派生归属；scope继续绑定完整原参数；恢复重算并核对原关联 |
| `AgentCaseAssessmentService._load_evidence`核对原实际工具证据 | 校验绑定与完整固定上下文、原wire参数哈希一致，manifest展示归属；存在关联不等于业务通过 |
| 本地《Agent轨迹与协议鲁棒性》04.5 | 保留调用、检查点、未知结果和必要清理；本步不新增部署判断或全面工具禁止规则 |

`case-action-binding-v1`是本项目协议设计。已查现有Registry、原图、DeepAgent治理和本地课程，未发现把任意JSON用例执行项绑定到工具调用的既有协议。字段含义和消费者在本次一并实现，不冒充外部标准。

## 固定与调用契约

MySQL原策略可显式选择`action_binding_protocol=case-action-binding-v1`，未选择时字段从原策略序列化省略。提交规划冻结策略；执行只读原计划中的值，不读取当前设置替换旧策略。Agent上下文省略新的缺省字段，保留原业务null/False/0。

开启策略后，每个已注册工具的模型schema要求`_case_action={"item_key":"固定执行项key"}`。服务器只接受所属项，不接受客户端自报phase、项目或运行身份。绑定包括固定项目/Run/条目/计划、原执行attempt/session/turn、item_key、服务器推导的phase、完整上下文SHA及完整原参数SHA。

完整模型消息、审批参数、ToolJob.input_payload和ToolExecutionRecord.input保留原wire。处理器接收去掉关联元数据后的业务参数副本；嵌套修改不能回写原wire。旧策略仍传原业务参数，不把旧业务字段`_case_action`解释成新协议。schema装饰不修改共享Registry。

原图与DeepAgent治理先验证选择，再创建关联Job/审批；ToolRuntime在实际入口再次核对。原审批恢复核对原Job绑定及完整参数，终态复用仍走原机制。治理上下文是frozen dataclass，使用不可变替换；不对它赋值。现有工具权限、模式、审批scope和运行策略继续使用。

## 实际验证与边界

测试使用实际MD/DOCX/PDF原件、解析任务、PG固定计划/Run、MySQL项目设置、RustFS、真实规则HTTP及原Agent会话/审批。三种所属阶段分别实际调用GET，只证明归属和参数传递，不证明创建或清理改变了业务状态。

还验证项目隔离、模型伪造phase/缺失或陌生item_key、完整12步和False/0/null、设置改变后的原计划恢复、DeepAgent治理入口的实际PG/HTTP审批及终态复用。独立API/Outbox/规划和执行CLI、真实SDK另验。模型和业务确认来自受控夹具，外部默认模型与完整DeepAgent框架不能由这些结果替代。

失败与最终结果见PLAN.md第6.40及IMPLEMENTATION.md。当前Python环境缺少deepagents；对应框架测试明确跳过，不安装或替换项目依赖掩盖限制。

## 下一步

在原项目PG事务、固定执行项绑定及原工具领取中保存逐动作适用性依据。先复现新协议下的6.39失败，再实现对尚未执行目标/必要依赖的处理及真实有状态清理。调用领取与已发送结果未知必须分开；不能把一次领取宣称为确认之后所有网络动作绝不发生。逻辑操作身份也不能等同模型call_id。准备拥有者/结果、动作修订、换目标回归及其余P0—P5继续未完成。
