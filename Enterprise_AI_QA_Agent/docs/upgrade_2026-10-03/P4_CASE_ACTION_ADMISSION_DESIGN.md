# C2c2：当前确认与原工具领取的同事务处理

研究基线：fc7b7b8f。本步继续完整升级；不是整个C2c或P0—P5完成。

## 依据与已复现事实

- 已阅读原目标附件、本地《Agent轨迹与协议鲁棒性》04.5，以及原固定计划/运行/Attempt/Agent检查点、审批和ToolJob代码。
- 首轮`reproduction`1失败15.45秒、`live-first`1失败13.47秒的夹具实际确认旧版本，运行却选择另一条完整用例；保存的PG回执为no_explicit_invalid，继续发送符合精确范围，不能当作限制失败。原错误记录保留并向用户说明。
- 修正为同一完整用例与覆盖确认后，关闭新策略再复现：先start、工具等待审批、追加固定目标精确版本invalid、批准原审批，HTTP仍从[2,2]到[2,3]。`p4-action-admission-exact-reproduction.xml`为1失败13.51秒。启用新策略后三格式`exact-fixed`为3通过34.30秒，不新增待执行HTTP。受控模型回执blocked不代表业务通过。
- `PostgresBusinessPlanning.current_reviews_in_cursor`与追加确认共用项目行锁；只读固定PG环境快照，不读当前MySQL目标配置。
- `PostgresToolJobStore`已提供原queued/waiting到running的原子领取。DeepAgent治理先领取，原图ToolRuntime仍使用整行mark_running。两者不能各自再加一次独立检查，否则存在确认/领取窗口及双重领取。
- 原Attempt检查点保存完整上下文hash、原执行Attempt、Session/Turn；租约恢复保留原祖先身份。不可只信工具参数或传入项目名。

## 实施决策

新增显式`case-action-admission-v1`策略，放在原MySQL规划策略中；要求已启用binding-v1。新计划冻结该策略，执行上下文携带，缺省字段省略，旧计划保持原语义。原binding-v1仍仅表示归属。

共享PG领域服务依次读取固定运行、取得原项目确认锁、锁当前RunItem/Attempt及原Job，核对当前租约、原Agent检查点与恢复祖先、固定执行项拥有者/阶段、完整上下文和原参数SHA；读取当前确认并同事务保存领取或拒绝回执。回执存原ToolJob.metadata，不新增平行任务/业务表。

明确invalid且无点级冲突时，未领取的case及before动作被拒绝，工具处理器不调用；其他版本/目标、unknown、撤销、冲突均沿既有规则从宽。after阶段先保持可执行并记录`after_not_restricted`：阶段并不能证明真实清理，真实创建数据的拥有者、必要清理和逻辑操作身份仍需后续实施，不能在本步宣称已验证。无固定目标保存`no_fixed_environment`，不伪造部署/有效判断。

领取事务提交是本步线性化边界。之后新确认不能撤销已领取或可能已发送的动作；不得承诺“任何更晚确认都能阻止网络”。后续的执行中修订和发送证据处理继续追加。

同原Job终态先重放原结果；已开始无终态/领取提交结果未知不再发送，也不把它写成已失败且未发生。原Job身份、输入或回执不符必须显式报错；不能使用旧整行mark_running覆盖原子结果。治理入口将服务端实际领取对象交给ToolRuntime，避免重复领取。

## 必须实际验证

三格式完整用例与真实确认/PG/HTTP；原Agent图待审批变化及无新增HTTP；治理入口原Job终态重放；before/case拒绝、after保留且标注未证明清理；unknown/撤销/其他目标不误杀；多领取、确认锁竞争；实际PG提交回执丢失、原领取找回且不重发；租约/祖先/跨项目/上下文及原参数修改拒绝；旧策略回归。独立生产进程和真实SDK另验，不用内存通过替代生产接入。

默认模型、真实有状态准备/清理、稳定逻辑操作身份、新call_id重复副作用、执行中修订、换目标回归及其余P0—P5继续保留在总方案中。

## 实际验证与恢复修正

首两轮失败是夹具确认另一旧版本，原PG正确返回no_explicit_invalid；修正为运行实际选择的完整用例和覆盖后，关闭新策略再复现真实缺口，HTTP由[2,2]到[2,3]。启用策略后MD/DOCX/PDF的同一精确版本待审批动作均拒绝，新HTTP为0。保留最初错误记录和真正失败，不混为同一根因。

| 独立轮次，前缀p4-action-admission- | 实际结果 |
|---|---|
| boundaries-first | 14通过、0失败、0跳过、113.14秒 |
| current-final | 32通过、1失败、0跳过、144.55秒 |
| current-fixed | 33通过、0失败、0跳过、145.57秒 |
| exact-fixed | 3通过、0失败、0跳过、34.29秒 |
| exact-reproduction | 0通过、1失败、0跳过、13.50秒 |
| live-first | 0通过、1失败、0跳过、13.46秒 |
| no-target-final | 1通过、0失败、0跳过、13.38秒 |
| production-current-fixed | 1通过、0失败、0跳过、93.80秒 |
| production-final | 1通过、0失败、0跳过、92.42秒 |
| production-first | 1通过、0失败、0跳过、87.66秒 |
| production-observed-final | 1通过、0失败、0跳过、94.16秒 |
| regression-final | 226通过、0失败、24跳过、547.01秒 |
| reproduction | 0通过、1失败、0跳过、15.43秒 |
| frontend-test / frontend-build | 11组件测试通过、2.20秒；vue-tsc/Vite构建通过，Vite8.92秒；完整浏览器页面未验 |

regression-final的226通过、24跳过验证当时源码；20跳过缺少deepagents、3外部真实模型和1真实Redis开关。之后审查发现新领取路径需保留既有可靠输出恢复，使用原recover_observed_output追加实际保存HTTP返回、交付中断和后来invalid的恢复测试。current-final为32通过/1失败，旧denied终态被误标为新拒绝；修正共享claim的旧终态None契约后，current-fixed对最终受影响源码33通过。原响应恢复为partial而非业务通过，原响应/确认保留且没有重发HTTP。各轮不累加为当前覆盖数。

实际四路同Job领取只有一条业务GET，原确认项目事务锁等待后精确拒绝；领取PG提交回执丢失和HTTP返回丢失保留running/未知，同原Job不重发。领取事务提交是本步边界，之后的新确认不能撤销已经领取或可能已发送的动作；跨新call_id的稳定逻辑操作身份仍未实现。原工具审批/终态重放和回执进入证据manifest已验；完整DeepAgent框架因缺依赖未验。

独立production-current-fixed使用正式main/API、Outbox、规划/执行CLI、SDK和实际HTTP，原完整十二步JSON/False/0/null进入固定依据，正文实际核验200/7/0/False/null，Result passed。共6次物理模型HTTP：影响2、既有C1规划1、新策略规划1、Agent调用/工具后2；本次新增业务GET1。原Job保存固定case归属、原参数和no_explicit_invalid当前unknown确认，未知确认没有充当业务通过证据。终态重投/API重启/归档/删除当前目标配置后历史保持，新模型/业务发送均0。模型及评审为受控夹具，不等于外部默认Agent。

完整命令/失败见[IMPLEMENTATION.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/IMPLEMENTATION.md)；原件、物理消息、PG回执、旧8973份原字节及三方案核对见[p4-action-admission-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-action-admission-validation.json)。用户.gitignore及此前environment-action未提交诊断保持原状态。

真实有状态准备/效果拥有者/必要清理、稳定逻辑操作身份、执行中追加修订、换目标新回归、默认Agent、完整环境App/AI候选、用户业务评审/点级完整性、生产历史迁移及其余P0—P5继续未完成。模板只生成、执行完整用例、项目内简单衔接/补全和上传独立于Git的约定保持。
