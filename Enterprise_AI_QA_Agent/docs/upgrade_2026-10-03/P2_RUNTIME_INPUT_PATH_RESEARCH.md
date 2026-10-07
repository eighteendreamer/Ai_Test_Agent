# 运行输入路径调研（2026-10-07，基线64cc650c）

本批研究完整固定计划到Agent输入的实际链路，不修改模式、模板、业务用例或全局模型预算。

## 依据 → 结论

- `schemas/case_planning.py:144`：`PlanInputBinding`只允许`source_item`或`input_key`之一；`path_tokens`同时支持字面量数组和旧点分路径，空数组表示完整来源值。
- `tests/test_plan_binding_paths.py`已有实际JSON文件规划测试：`input_key=settings`搭配`output_path_tokens=['', 'enabled']`，明确期望取得`False`。因此运行输入路径并非本批自创语义。
- `test_cases/planning_service.py`及`test_runs/plan_mapping.py`保留完整绑定，核对顶层输入身份；不存在必须抛弃路径的转换。
- `test_runs/agent_case_context.py`的运行输入分支只调用`runtime_context.get(input_key)`，未消费`binding.path_tokens`；前序结果分支则确实读取路径。已存在的规划契约在执行入口丢失。
- `test_runs/dependencies.py:output_at_path`是当前依赖、旧路径和字面量键/数组索引共同读取函数。原测试明确`null`不可用、`False/0`可用；本批继续该行为，不变更历史依赖语义。
- `prompting/prompt_assembly_service.py`实际将`execution_json()`包装为完整固定Agent输入；一个错误的`resolved/value`会成为后续模型的执行依据。
- 本地课程`项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html`要求联合验证结果、轨迹、工具契约和恢复；据此验证真实上传/固定计划/领取/物理模型请求，而非仅编译。

## 复现及采用做法

创建有8步、空键、含点号键、数组、False/0/null的实际JSON文件，上传并读回原件；使用项目正式用例创建计划与运行，实际领取后构造上下文。核对运行输入子字段、旧点分路径、完整来源和缺失必要字段，并通过生产SDK和两种模型适配路径发到受控本地HTTP服务，检查实际发送的完整上下文。实时组使用MySQL配置、PostgreSQL固定计划/运行和RustFS原件，测试夹具只清理自己创建的资源。

修复应位于上下文的运行输入分支：顶层键存在后，复用现有`output_at_path(value,binding.path_tokens)`；之后沿用必要输入、空值判断和拷贝。无需新增解析器、策略、表或执行框架，也不按字段名猜测业务含义。

这项复现不能证明它导致规划模型超时，不能代替准备步骤多对象输出/后续工具传值、未知副作用查证、共享消费清理或默认自主Agent的完整验收。上述仍需继续研究。

## 实际结果

- 修改前运行新测试文件：4失败、4实时组未启用而跳过，6.92秒。两条取值测试实际得到完整settings对象，另两条必要缺失/null测试没有抛错。保存`p2-runtime-input-reproduction.log/xml`。
- 修改现有上下文的一行，复用原读取函数；不新增配置、表、依赖或策略版本。开启`RUN_LIVE_PLAN_RUN_CREATION=1`后8通过、0跳过，11.95秒；两种SDK适配实际发送完整上下文。
- 加入现有ToolRuntime/API工具/真实HTTP/持久工具返回/事实核验/终态不可重领后8通过、0跳过，14.33秒，见`p2-runtime-input-business.log/xml`及四份场景原件、固定记录和物理请求。每场景只GET一次，ID来自实际上传文件的数组子字段；回执4项确定性比较通过。受控规划和模型响应不代表默认自主Agent成功。
- 四个既有模块`test_agent_case_context.py test_run_item_dependencies.py test_plan_binding_paths.py test_agent_case_assessment.py`，启用真实领取/路径存储开关后72通过、3跳过，34.35秒。跳过的是三个外部实际模型验收：完整用例、字面量路径规划、实际HTTP回执；对应开关未启用，按XML逐项记录，不把它们计为通过。
- 前端契约未改变，本批未重跑前端构建或完整App；不重复未改变的默认超时场景。
- 检查历史恢复后补验2通过、0跳过，6.61秒：实际保存旧完整父对象的上下文检查点，升级后上下文hash不同，现有适配器拒绝悄悄替换。PG原检查点字节/版本未改，模型/业务HTTP均0；这是原保护生效，不是旧错误上下文自动恢复成功。上线前应核对这些已admitted条目和原执行证据，历史协调仍待完成。

实时测试创建自己独占的MySQL配置、PG业务表与RustFS文件，结束后清理测试资源；提交的是原始字节及当时记录，不能把其中测试身份当作仍存活的生产任务。
