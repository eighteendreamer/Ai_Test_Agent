# C2b 允许项的开始确认回执与原 Agent 恢复

研究基线afa9381e。目标仍为完整P0—P5；本步补执行当前依据，不代替运行中动作修订/未知副作用/换目标回归或正式产品。

## 查证依据 → 采用做法

- run_store._start_item_sync已经在项目锁内读取该固定快照当前确认，却只为明确invalid保存未执行Result。允许项的Attempt没有当前依据；AgentCaseContext只读plan.environment，可能继续看到旧确认。首次开始在原PG事务保存完整当前确认回执，随后传入原Agent上下文。
- TestRunAttemptRecord及原record JSONB已经保存租约、开始时间、检查点和恢复祖先；两个claim实现都从过期Attempt复制检查点。增加可选environment_admission，复用同一行，不建新配置/业务表。恢复复制原回执，不读当前MySQL或重写原规划。
- AgentCaseExecutionAdapter._load对完整上下文hash和恢复祖先进行核对；SessionService首次/重放/审批和Prompt/assessment都用execution_json。新回执进入该固定上下文及原hash，模型/工具后推理/核验同用一份；恢复前核对root execution_attempt_id属于本条目的实际祖先链。
- 过期恢复会清空item.started_at，但原Attempt.started_at/checkpoint存在。新回执只在从未开始且无检查点的首次start生成。旧已开始运行没有回执则明确保留旧上下文/hash，不按当前确认补造历史。新已开始运行保留原回执，即使之后撤回/invalid也不替换；后续动作如何响应新确认另行研究。
- Pydantic已安装2.13.5但项目声明>=2.8。当前官方Field.exclude_if不能当作2.8契约；采用2.8已有的[model_serializer wrap](https://docs.pydantic.dev/2.8/api/functional_serializers/)调用原handler，仅省略新缺省None字段。不全局exclude_none，不改变原业务null/False/0、旧PG记录/API/hash。context沿用现有execution_json省略规则。
- 本地Agent轨迹与协议鲁棒性04.5要求检查真实发送/原检查点/恢复；不从课程阈值推断实测表现。真正HTTP、ToolJob、PG结果、模型物理消息以及历史hash分别验。

## 固定回执

business-execution-admission-v1保存project/run/item/plan、精确case/version、原规划依据SHA、environment/snapshot/SHA、root execution_attempt_id、checked_at、完整当前确认和回执SHA。decision明确为no_explicit_invalid，不能把unknown/未提及/撤回/冲突改称valid或已部署。

start失效仍沿C2a未执行结果；允许首次start保存回执后才转running/进入Agent。实际开始时间、Attempt和回执同事务；丢回执回查原Attempt，不能启动第二回合。claim前过期尚未开始可在新Attempt开始时固定新确认；started/checkpoint恢复只能使用原回执，旧没有则保留缺失。Agent结果保存同一回执，报告可追溯；回执存在不等于用例通过。

## 验证和交付边界

先用真实MD上传/解析/比较/确认/固定计划复现允许项缺少当前依据，保留失败输入；然后MD/DOCX/PDF验证提交后unknown/撤回/冲突和固定目标、完整12步、False/0/null、实际GET及结果。核对事务丢回执、实际锁竞争、started/checkpoint祖先、篡改/错项目、旧历史回放及无环境序列化。独立API/规划/执行Worker和原Agent实际graph/模型消息另验；不以纯编译或手动HTTP代替自主Agent。

所有跨边界DTO/Attempt前端类型/上下文/结果消费方同步。配置继续MySQL，Attempt/业务依据PG，原件/大工件RustFS。默认Agent、新目标回归、运行中动作修订、完整App及其余P0—P5未验时明示，不能缩小目标后声称完成。

## 实测结果

先复现允许项缺当前回执及真实PG提交回复丢失，各1失败；修复后当时19项三格式/原限制回归通过。原Agent图专项1通过，过期/后续确认不重发模型或业务HTTP。扩大回归171通过/2外部默认模型跳过，547.98秒；前端类型及构建通过。独立生产首次失败是夹具忽略Agent SSE，完整请求/error结果保留；按既有CLI流式夹具修正后1通过，114.46秒，总6真实SDK POST、1新增实际业务GET，审批/终态重投/重启历史恢复通过。无新表或依赖。具体命令、警告和局限见IMPLEMENTATION.md及PLAN.md第6.38；受控模型不冒充默认Agent验收。
