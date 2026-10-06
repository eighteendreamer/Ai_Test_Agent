# 清理覆盖进入独立生产进程的调研

源码基线：56af7313；业务实现基线：a009b0f8。

本轮先读用户目标原附件、原生产生命周期夹具、规划任务、对象证据、动作领取、Agent适配器和结果核验代码。本地课程《Agent轨迹与协议鲁棒性》要求核对半成功链路与工具轨迹；本轮采用实际副作用和最终结果共同验收。

## 依据与采用做法

- `tests/test_execution_worker_cli.py::isolated_runtime` 已有完整main生命周期、独立API/CLI、隔离MySQL表/PG数据库/Redis及实际RustFS。沿用这个夹具，不替换AppContainer服务。
- `tests/test_case_operations_production.py` 只验证规则GET及同操作别名，不能证明新对象覆盖策略；不将旧专项重跑作为新策略验收。
- `planning_service.py::capture_seed` 已冻结覆盖策略与实际工具接口；`planning_workflow.py` 使用原seed查询和回合检查点。通过正式任务API提交，再关闭当前策略，独立规划CLI应仍使用原配置。
- `agent_case_execution.py::_finish` 从原图最终回执调用 `assess`；`build_assessment_request` 当前没有生产调用方。因此生产验证首先核对实际图消息、全部原操作及正式结果，不能宣称额外只读评估模型已在生产调用。
- `postgres_case_action_admission.py::_claim` 支持无固定环境，决议为 `no_fixed_environment`；本场景采用项目明确受控地址与完整用例。环境适用性已有专项，本轮不以它替代对象覆盖。
- `test_case_effect_evidence.py` 与原实际失败保存了两次创建、一个回执输出及原对象清单。使用相同本地业务服务，完整八步JSON提供创建/查询/清理依据；部分清理故意产生错误的passed回执，服务端必须保存blocked和未覆盖对象。

## 实际验证目标

上传真实需求文件并读回RustFS；正式API创建/评审/启用完整八步用例；MySQL启用新策略，提交任务后关闭；独立规划CLI按冻结接口查询/生成；API创建固定运行，独立执行CLI进入审批。审批执行两次创建、一次原操作别名、目标查询和一条/全部清理；第一次创建后重启API，不能重发。核对真实业务对象、PG原Job/原对象清单、正式结果、固定owner映射及终态投递。

受控模型仅验证生产传输/协议与实际工具链，不等于默认自主Agent。共享消费完成、未知查证、正式完整App及P0—P5其余要求仍需继续实施。

## 首轮结果与验证修正

first：1失败，19.01秒；正式任务提交返回503，未进入模型或业务HTTP。核对`main.py:451`后确认原生命周期夹具未登记规划流，沿原生产专项显式配置`redis_task_outbox_streams`，没有修改业务服务。

stream-fixed：2通过，95.02秒；独立API/规划CLI/执行CLI、实际SDK、HTTP/PG/RustFS及审批/重启/终态链路通过。随后补API鉴权和直接MySQL/PG对账，authenticated：2通过，91.59秒。

审查保存结果发现受控回执的seed输出引用第二个ID，但目标实际查询第一个ID。两项覆盖结论成立，但该夹具不能证明声明的数据传递一致。最终改为明确输出第一个ID，增加prepare输出、目标实际输出及全部对象清单的独立核对；另一条未被回执输出的记录仍必须参与覆盖。旧记录保留，不将它们作为正确数据传递的证据。

当前MySQL默认模型只读核对为step-5-preview、8192输出、128000窗口和流式开启，未修改全局配置。本轮尚未向该模型发送执行请求；既有length失败仍保留，不能用受控SDK成功替代默认自主Agent。

## 最终结果

final：2通过、0失败、0跳过，92.65秒。鉴权、直接MySQL/PG、完整八步及原始HTTP/SDK、正确seed输出和所有原对象均已核对；模板未注入、当前策略关闭后的固定恢复、审批/API重启和终态投递通过。命令与限制见PLAN 6.50；原失败和早期数据输出不一致记录保留。默认自主Agent、额外只读评估、完整App与共享消费完成未验收。
