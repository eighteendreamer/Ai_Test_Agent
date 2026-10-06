# 原对象清理覆盖实施设计

研究基线：82de0568；前一目标回合为进展：方案正文、三份同步及实际上传2项回归已提交。完整P0—P5继续实施。

## 先查证的事实与采用做法

- P4_CLEANUP_COVERAGE_RESEARCH.md及原baseline XML：旧回执只引用最后输出，合法创建两条仅删一条、借目标GET及after GET被误判清理通过。新路径必须读取原操作索引里的全部原Job，不复制回执来制造范围。
- case_action_binding.py::CaseActionBinding：原动作有project/run/plan/run_item/item_key，没有target_key。首轮新路径1失败/3通过/7夹具错误记录保留；七个原生场景未运行。
- case_planning.py::CasePlanExecutionSnapshot：原PG固定快照已含run_item_ids、owners和preparation_scopes。agent_case_context.py已有正式项映射核对及跨项真实输出绑定。采用覆盖策略专用effect_owner_item_ids，从原run_item_ids冻结进入执行上下文；核对当前项及每个来源拥有者。关闭新策略省略该字段，保持旧上下文wire/hash；不读当前配置回填。
- postgres_case_effect_evidence.py：每个原可靠返回事务保存对象证据，list_run_evidence按项目/运行/计划读取，验证原逻辑操作、绑定、观察及hash。ToolJobService仅提供紧凑投影；别名不新增原证据，未知原Job仍留在清单。
- native api_testing_mode.runtime::_execute_dispatched_task使用ApiTestTask返回task_result；注册工具campaign schema省略此分支。规划查询固定实际工具接口及该原生输出变体，冻结在原PG seed/证据，不依赖恢复时的当前注册表。
- 本地Agent轨迹与协议鲁棒性课程L3/L4：工具成功不等于语义通过，半成功链路需要端到端核对。范围为声明的原对象证据，不能当成业务对象全集或消费完成。

## 实施契约

MySQL原项目规划策略增加可选case-effect-coverage-v1和1024000字节覆盖预算；新计划冻结到PG。对象声明可选字面量输入匹配，预期来自完整固定JSON或prepare.instructions；不同值排除该调用，缺字段保留未知，False/0/null保持类型。旧无匹配声明不改写。

清理以原cleanup_sources、固定来源拥有者、每个origin_job_id/对象路径、typed值hash和真实清理观察核对。清理早于来源不能覆盖；检查失败为failed，来源未知/已知未覆盖/预算不足为blocked。每份来源记录都保留，不按数量或最后ID去重。观察时间沿已有ensure_utc_datetime归一。

沿原assessment只读请求、VerificationResult、Result.actual及证据引用接入；不新增业务执行器，不在只读评估中发HTTP。完整清单超过固定预算时保存诊断及已知数量，不伪装零对象。前端项目策略和原运行详情同步预算/声明范围/已覆盖/未覆盖/未知及消费完成边界。

## 实际验收安排与仍未完成范围

先修复身份缺口并运行原失败来源和七个真实HTTP场景。然后补完整正式前置八步JSON跨RunItem来源、原别名不重发、数组/多来源与typed/重复值边界、MySQL关闭后固定上下文、超限未知、终态不重领及正式结果读取。前端使用实际结果渲染并检查策略级联关闭，运行类型检查和构建。

保留首轮及旧三个失败，不修改旧历史，不用xfail遮盖。当前功能测试采用受控声明和实际原生工具，不能当成默认自主Agent验收。独立生产新协议、完整App、消费完成、未知只读查证、运行修订及其余完整升级仍需推进。

## 最终本批结果

综合167通过/3外部模型跳过150.36秒；新增21项覆盖真实创建/清理、正式前置跨项、数组/多来源、原别名、固定工具查询恢复及拥有者/时间边界。前端22通过2.54秒及vue-tsc/Vite9.06秒通过。原首轮、受控回合/封装、持久记录及前端类型失败均保留，详见PLAN 6.49和p4-cleanup-coverage-validation.json。配置MySQL、固定事实/结果PG、原件RustFS。独立生产新协议、默认自主Agent、完整App、消费完成、未知只读查证及完整升级仍未验收。
