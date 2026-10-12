# 原工具等待子任务与快照续跑〔2026-10-12〕

基线2df2e1ca，完整P0—P5保持active；外部AI验收仍暂缓。本步沿7.43跨层方案实现，不再使用子代理审批参数恢复父工具。

依据→做法：RuntimeService._build_snapshot/_build_pending_turn及SessionService.resume_session、AgentTaskDispatcher.submit_resume、PG submit_resume已有原snapshot/turn/预算的持久恢复机制。使用该机制保存明确waiting_tool检查点，再恢复原ToolJob；不使用coordinator_resume的新消息路径。会话沿已有interrupted安全暂停和可恢复snapshot语义保存，pending_turn中的waiting_tool区分子任务等待与人工中断。新增ToolJob/ToolExecution的waiting_children表示未结算，不伪装partial或resource准入。

ToolRuntime负责保存等待原Job、固定call/input及可信server_approval_granted，不把授权放到模型参数。Runtime普通工具与审批后工具两入口都将更新context保存到snapshot，并在模型下一次调用前停止。恢复核对原Job/session/turn/call/input，继承原预算，恢复原工具后才交还模型。终态仍原Job回读，不重执行HTTP。API保持running任务/原child，先读原结果再派发依赖；拒绝/未知仍有明确结论。缺服务历史独立调用保留已有语义。

CoordinatorRuntime已有父锁、通知投影、子watcher和周期dispatch reaper；复用这些入口权威重读等待子会话，结算后通过submit_resume及snapshot身份幂等入outbox。早到通知由周期核对补回；API重启不依赖旧watcher。子审批复用现有代理，不自动批准，不从用户普通消息/子参数构造父继续。中断会话只有实际waiting_tool才自动继续，人工中断不自动恢复。

需要实际证明：普通与审批后入口均无提前报告/模型调用；原snapshot/预算跨服务重建；延迟/拒绝/重复/早到通知、PG/outbox/lease继续、终态0HTTP；两种真实Agent子启动全鉴权及API重启。未运行前不宣称已实现或通过。新增状态所有消费方需检索同步；MySQL配置不变，等待/快照/outbox为原PG业务事实，无新依赖或表。

## 实施与实际结果

依据7.43撤回的三次局部实验，本轮没有继续微调原轮询。`tool_child_wait.py`保存原Job/call/input/child IDs；ToolRuntime通过PG条件更新将running停到waiting_children、将waiting_children原子领取回running。取消后的记录不能再停驻或领取，领取未确认/恢复结果未知沿日志抛出，不能进入通用mark_failed覆盖其他所有者。等待记录没有completed_at。API campaign先保存未结算任务与等待子身份，既有报告生成在此返回前不执行。

`RuntimeService`普通图工具与父审批恢复两入口停在原快照；自动恢复只能提交原snapshot的manual_resume，不使用子代理审批正文。可信授权只来自原Job服务端metadata；固定调用身份和原输入重核，ExecutionSafetyPolicy仍可拒绝，预算从原graph_state继承。`CoordinatorRuntimeService`已有子通知/watch和dispatch reaper权威读取子状态；`AgentTaskDispatcher`提交完成后再核对等待，补通知早到的窗口。重复通知使用同一个snapshot幂等键，单个父记录读取失败留下结构化日志并继续处理其他等待父记录。前端工具状态类型、两面板和中英文文本同步，Session仍使用原interrupted安全暂停语义。

完整独立验收保留原严格断言：本地HTTP供应商受控输出，但main API、Agent CLI、PG/MySQL/RustFS/Redis和业务HTTP真实运行。固定campaign_ready三任务，不冒充用户自然语言、文档发现、项目用例选取或默认外部AI质量。

| 批次 | 实际结果 | 范围 |
|---|---|---|
| first | 2通过、2排除，190.84秒 | 两种子启动首次完整闭环；在最终原子领取和提交后通知优化之前 |
| restart | 2通过、2排除，196.30秒 | 两种路径真实子审批等待时重启API；原快照保持 |
| final | 2通过、2排除，177.71秒 | 最终代码再验同进程/持久子两路径，包括审批等待重启和终态重投 |
| regression | 56通过、4门控跳过，25.61秒 | 10模块，含真实PG/HTTP原结果恢复；新增PG竞争测试之前 |
| agent-regression | 61通过、1跳过，41.23秒 | 原五Agent/Coordinator/领取/生命周期/CLI模块，使用已有owned隔离环境 |
| pg-races-verified | 12通过，7.60秒 | 实际PG八路领取、取消，恢复身份/快照/人工中断及逐父错误边界 |
| owner-final | 38通过、3门控跳过，10.91秒 | 上述实际PG、真实领取未确认样本、结果回读、预算/审批/心跳；重叠不累加 |
| anthropic | 14通过、60排除，20.80秒 | 官方Anthropic SDK真实本地HTTP故障及已有client；/v1/messages正文和流式事件仍为Messages |

final每种路径实际login→refresh→profile均200；三子会话正式project一致，唯一父报告3完成/0失败；子等待期间无提前报告。审批等待重启后同一快照和graph_state保持，额外业务HTTP和模型HTTP均0。五份父快照同一turn：waiting_approval及三次waiting_tool均model_call_count=1/max_iterations=8，最终completed为2/8；没有恢复预算重置。最终API重启后历史任务投影一致，重投原已终态Agent消息任务业务/模型HTTP增量均0。两个凭据原文不在公共请求、会话、Job或密文中，测试保留安全事实。

实际PG测试首轮失败因漏建agent_projects；修正顺序后又发现测试SessionRecord漏传四个必需字段。按main初始化及既有Session构造修正夹具，不改生产表或放宽断言。两轮1失败/3通过原log/XML保留。最后实际PG八路竞争仅一领取成功，取消记录前后相同；另用真实ToolRuntime执行领取未确认样本，处理器不进入、竞争所有者仍running，无完成/失败写入。不是宣称数据库连接丢失全阶段已验。

`npm run build`实际成功，含vue-tsc和Vite（9.70秒）；compileall退出0仅附加。Anthropic的公共await封装当前名`await_provider_io`，仅统一I/O异常，调用仍是`client.messages.create`；该命名在此前SDK提交已修改，本轮只复验，没有改模型配置、协议或供应商重试。

## 尚未覆盖及后续

原7.41/7.43/7.45完整失败证据仍保留；本轮把其固定campaign的独立Agent审批/鉴权闭环补到通过，不宣布整个API或P0—P5完成。拒绝审批、未知写入只读查证/进程中断、Cookie/自动过期401续期、多认证并发、正式用例管理运行及多工具混合等待/审批、Deep Agents治理入口仍须单独验收或补实现；当前只在原图Runtime的API单个父工具路径实际证明。外部AI按用户决定暂缓，不能用此本地模型替代默认AI意图/用例选取/排序/补全质量。复杂UI/性能容量/需求业务评审/部署/历史迁移和两份旧Worker原件仍在全目标中。配置仍MySQL、业务仍PG、原件仍既有RustFS，未新增依赖、表或生产配置。
