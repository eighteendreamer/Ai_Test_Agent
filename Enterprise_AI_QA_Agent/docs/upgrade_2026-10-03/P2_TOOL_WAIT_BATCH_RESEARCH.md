# 同批工具等待与剩余审批〔2026-10-12〕

基线8df999ef，原7.46单父工具闭环已实测，完整P0—P5保持active，外部AI暂缓。本步针对多个工具同批输出的状态一致性，不改用例生成模板/测试模式/模型协议。

现有依据→采用做法：

- RuntimeService._build_pending_turn已保存完整graph_state、原conversation、工具结果、审批ID及模型预算，_state_from_pending_turn优先从graph_state恢复。tool_child_wait.park_tool_wait新检查点仅保存预算/单工具，覆盖现有完整pending，导致剩余审批无法通过原入口恢复。复用既有完整序列化，不另建第二套字段映射。
- tool_executor原来支持同一assistant返回多个工具及多审批；7.46只选第一个waiting_children。等待列表应覆盖本轮原Job/call/input，原单waiting_tool历史快照继续可读。每个工具仍按原原子领取/终态回读，不产生新Job。
- RuntimeService._resume_after_approval已有审批逐项结算和最后一项才交回模型的合同；等待与未决审批都属于同一批未结算工具。剩余父审批优先保持waiting_approval，不能自动跳过；所有父审批已结算后才进入子等待interrupted。完成子等待后重建原assistant调用对应的全部结果，不给模型缺失或重复结果。
- CoordinatorRuntimeService已有原snapshot幂等submit_resume和PG权威读，自动继续核对等待列表的全部child，而不是只核对首项。实际原项目/父/turn校验保持。人工中断无waiting标记仍不自动恢复。
- 本地课程Agent轨迹与协议鲁棒性强调检查中间工具选择、参数、观察与幂等/重放，故以实际原调用/快照/HTTP轨迹作为证据，不以最终答复和编译成功代替。

先保存当前失败复现：等待检查点保留剩余审批；两等待工具恢复必须全部结算再交回模型。随后实际PG/真实本地HTTP核验逐审批/重建Runtime/顺序与唯一工具回执、预算、取消、旧单等待兼容及原单工具独立Agent回归。普通图Runtime范围，Deep Agents治理入口需独立调研/验收；本步不将其默认为支持。

## 实测发现的并发保存根因

首个PG夹具观察到snapshot已写却Session仍running，关闭服务取消了未完成审批续跑；按实际SessionService._finalize_runtime_result保存顺序，夹具需等会话状态与续跑任务一起结算，不以快照单独出现视为完成。纠正后真实PG/内存、批准与拒绝四组及边界共9通过20.58秒。

独立Agent同进程子仍失败〔1失败1通过142.95秒〕：父快照已interrupted，但会话被改回running、旧pending覆盖；无业务HTTP。沿调用源发现_project_worker_notification原本用行锁update_metadata_and_append_event投影worker，却随后读取整会话、修改故障计数/报告并save_session，旧status/pending_turn可覆盖同时由Worker保存的新快照。实际内存与PG强制在这次旧读取后写入新等待，均复现running/旧pending覆盖，notification-before 2失败7.99秒，原件保留。这是并发所有权缺失，不能靠等待更久或回写最新status修补。

依据原update_metadata_and_append_event/mutate_metadata行锁能力→通知仅原子更新自己的故障计数/报告字段，再重读当前会话交付后续；不用过时整会话写回。持久子无需自动coordinator继续的分支原本追加persist_transcript=False的通知，再整会话save，也应保留已有事件投影并重新读取后核对等待，不覆盖Runtime状态。三次故障原停止策略不改，真正stop仍执行原授权的控制动作。测试需证明原status/pending不丢、通知事实保留及旧Agent/持久子合同通过。

## 最终实现与证据

将RuntimeService已有完整_build_pending_turn原函数体搬至state_persistence.build_pending_turn，共用原字段合同；AST核对与8df999ef原函数体相同，旧Runtime入口保留。park_tool_wait不再使用另一个精简字段映射，保存整批waiting_tools及兼容waiting_tool；有剩余父审批时沿waiting_approval，父审批均结算后才进入interrupted子等待。Runtime先核验整批原Job/session/turn/call/input，再逐个原子领取/回读；所有调用已结算且审批为空后，按原assistant调用顺序构造唯一工具回复再进入模型。Coordinator核验全部子身份/状态及完整等待列表，与原snapshot幂等继续。

已修复的通知并发问题复用原mutate_metadata；只更新通知自己的failure_guard/report元数据并重读Session，不整会话回写。无自动父继续的持久通知分支保留既有事件记录，去掉不落库的临时聊天与整会话save。故障停止、正式项目隔离、预算及权限不改变，无新表/配置/依赖或模型协议变更。

| 最终验证 | 实际结果 | 范围 |
|---|---|---|
| notification-verified | 11通过19.19秒 | 内存/实际PG完整图、批准/拒绝、重建Runtime/服务、两个等待/两个审批、真实HTTP、旧单等待/二项冲突/通知并发 |
| agent-final | 2通过2排除159.46秒 | 真实main API/独立Agent CLI、同进程与持久子，固定三任务，子审批等待重启及原终态重投 |
| agent-regression | 61通过1跳过40.74秒 | 原五Agent/持久子/领取/生命周期/CLI模块，owned隔离环境 |
| regression-final | 79通过42门控跳过91.12秒 | 11个相关模块，普通case/操作/预算/审批/结果回读/治理/心跳；PG等门控未全启用 |
| notification-regression | 41通过66排除10.57秒 | 原Coordinator取消/去重/故障停止/恢复及报告测试 |

PG两组事实：批准全部产生POST、POST、GET、GET；拒绝第二审批产生POST、GET、GET且第二原Job为denied，无拒绝HTTP。4个原Job保持，无新Job；父审批剩余时未调用下一次模型，只有一个子完成时未续跑；服务重建后原turn保持、模型count1→2/max8，新的默认99不捐赠预算。最终模型实际收到one/two/write-one/write-two四份唯一回复，布尔false/零值保持；终态检查无额外模型/HTTP。子等待生产者与模型决策受控，不是默认AI、正式case Run或独立进程验收。

独立agent-final两路径真实login→refresh→profile均200，唯一父报告3完成0失败；等待审批重启原snapshot/graph_state保持，业务/模型HTTP增量0。原消息Agent任务终态重投0业务/模型HTTP；随机凭据不在公开事实中。仍是固定campaign_ready+本地模型，不代替用户意图/文档发现/项目用例选取质量。原并发强制样本在内存和PG均保持interrupted及新pending，通知派发事实与故障元数据保留。

首轮before两项逻辑失败9.94秒；first 18通过1跳过5.57秒；functional-first 4通过2跳过8.37秒。live 2失败4通过21.62秒和pg-probe 2失败7排除13.46秒是提前close夹具错误，现场保存Session仍running/control=续跑，snapshot已interrupted；pg-settled 9通过20.58秒证明等待真实完成后恢复。notification-before内存/PG2失败9排除7.99秒、agent首批1失败1通过2排除142.95秒是确证的旧通知覆盖；全部log/XML/现场保留。早期regression 78通过41跳过90.55秒，最终批次重叠不累加。新增通知测试插入位置曾误放在旧函数中间，失败断言前已有效复现覆盖，独立归位后运行完整11项，不以提前结束夹具代替通过。

compileall退出0仅附加；本轮未改前端，未重跑构建。三方案同步/原件SHA/历史与4保护文件/配置凭据核验另见audit与validation。完整P0—P5保持active；正式用例管理等待/unknown只读查证、计划修订、Deep Agents入口及更复杂等待中断/生产迁移仍待验证或补实现；默认AI和复杂UI、持续容量、安全故障、需求业务评审/部署、全阶段SDK追踪及两份旧Worker原件保持全目标范围。
