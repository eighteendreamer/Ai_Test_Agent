# 失败增量模块显式新任务：研究与实施边界

研究基线12481ef2。先读取用户完整目标、GenerationTaskService/Store/Schema、固定范围/hash、公共路由/客户端、AgentRegressionService、共享文档任务和原真实unknown/父冲突样本。本地Agent轨迹课程04.5要求验证重复操作后的实际状态；已有回归新运行保留原失败的模式作为依据。

## 依据 → 做法

- GenerationGroup和IncrementalGenerationScope严格对完整正文做hash。给旧组加默认字段或改模块清单会破坏历史hash。采用PG独立不可变retry记录关联新task与原失败，保留原group/scope/module原字节；组报告继续显示原任务，重试另列，不能用成功子任务遮盖失败。
- PostgresDocumentTaskStore已经提供原子领取、deadline、seed、Outbox、终态不可重领。新子任务复用这套表和Handler，不新建执行平台；提交retry记录/新task/Outbox同事务。父任务和原检查点/hash绑定，原seed支持可选retry引用，原组/范围schema不改。
- GenerationTaskService当前按group策略执行；新子任务允许冻结当前MySQL任务策略，但原模型/Prompt/Skill/模板/来源/预期用例父版本不能改变。无响应的确定性发送前失败可执行原固定请求；新策略transport必须与固定authoring相同，否则新生成处理。已存响应只重读原对象校验/解析/提交，不再次调用模型。
- 原invoking没有持久response（包括实际在途取消unknown）不能通过新任务绕过unknown。只有保存原响应或者确定未发送的失败能创建子任务。completed/queued/running不作为失败重试来源；有receipt的异常失败拒绝。父版本冲突保留，不把新版本当旧基线；需要重新生成时走明确新范围。
- 新公开POST只接受creation_key，GET按原请求恢复、分页列原失败的子任务。同key跨父任务冲突，回执未知只读查询；关闭派发和归档后已接受retry仍可查，新retry拒绝。不新增MySQL配置键/依赖/.env修改，新增PG lineage表由现有启动初始化。
- 前端DTO/客户端同步新契约。正式按钮、pending缓存/历史展示与完整浏览器将在后续接入；本阶段先交付后端真实模块/API、独立CLI和契约验证，不能宣称正式页面已完成。

## 必须实际验证

真实MD/DOCX/PDF→影响接受→固定模块：发送前容量失败后改变MySQL策略，新task使用原完整消息产生草稿；原失败/原组/scope不变。响应容量失败后扩大策略，子task复用同RustFSresponse且无第二次HTTP。实际在途取消unknown、当前模型改变、父版本冲突、并发同key、多worker、终态重投、提交回执丢失、归档/关闭派发及项目越界实际验证。SQL读取retry/task/Outbox并对照原hash；保存实际原件、对象、物理请求、日志/XML。旧组hash和旧公开流程回归，前端实际客户端、类型构建验证。独立生产API/Worker与正式浏览器按后续阶段记录；不缩减完整P0—P5。

## 实际结果

首次真实新增模块 **7失败/2通过（77.65秒）**：我漏了内部GenerationTaskRecord到公开GenerationTaskView的显式转换，7项在新任务已保存后结果转换失败；不是未提交。原日志/XML保留，unknown拒绝路径与旧组hash读取通过。按既有公开结果方式转换完整model_dump后 **9通过、0跳过（74.21秒）**，实际MD/DOCX/PDF及MySQL/PG/RustFS/受控HTTP。之后新增真实父任务编号/模型改变、PG响应引用替换、RustFS原响应损坏与停用候选期限失败场景。

新增模块、原持久模块和公共API三模块回归 **33通过、0跳过（267.30秒）**，其中新增13项和旧20项。复查证据发现新helper把直接服务场景泛标为公共API，并与旧父冲突保存目录重名；原文件保留，旧混合目录不作为新增父冲突的完整依据。修正测试标记与独立目录，并将真实数据库重试提交回执丢失通过公共POST实际验证503，再按原编号恢复。标记/目录修正轮新增模块 **13通过、0跳过（104.70秒）**，与上述13项重叠，不累加。交付校验随后发现SQLAlchemy RowMapping被default=str写成字符串：测试中的SQL核对已通过，但保存文件不便按JSON验证。将SQL行明确转为dict并移除这份文件的字符串兜底后，最新交付轮 **13通过、0跳过（111.91秒）**，原旧文件保留；最新SQL字段/Outbox证据使用delivery目录。

最新范围包括旧组hash、3格式发送前预算失败/新策略/原完整消息/草稿，持久响应超过原预算后无新HTTP完成，实际在途取消unknown的公共409，四路同编号提交/四路领取、PG提交回执丢失503、分页、同编号改父任务409、当前模型变化发送前失败、原用例父冲突继续失败、PG子响应引用替换拒绝后恢复、实际RustFS损坏拒绝后恢复、项目越界404/归档/关闭派发历史，以及原停用候选期限失败后仅形成候选。原失败/终态/原group/scope保持；完整12步JSON及False/0/null/扩展字段、自身说明实际送达，草稿记录新retry引用。实际新task/不可变retry/Outbox同事务并通过SQL对照，原版本继续启用。

独立生产API/解析、比较、影响及生成CLI，真实DOCX、MySQL/PG/Redis/RustFS流程 **1通过、0跳过（135.10秒）**。原影响2次HTTP、原正常生成2次HTTP，另验证发送前失败后执行原请求1次和响应预算失败时原发送1次，总计6个物理POST。复用原响应的新任务没有第7次调用；两类原失败继续在原组显示。子任务完成后连同原父任务分别由独立CLI终态重投，没有新发送。API重启关闭派发后两类原请求/新task/原响应可读可重放，新编号503。所有物理消息按完整冻结请求核对；内容相同的明确新任务仍保留各自身份，不按文本误去重。

前端实际客户端与旧增量/需求/影响组件 **57通过、4文件（3.16秒）**；vue-tsc/Vite构建通过10.77秒。本轮没有正式重试按钮/缓存恢复页面的浏览器验收，没有调用外部默认生成模型、执行生产历史迁移或证明用户业务评审。受控模型证明链路及固定依据，不能证明需求已部署或测试通过。

原件、scope/response、PG重试关系/seed/收据/Outbox、实际物理消息、独立进程、首轮失败和各轮日志/XML在p4-generation-retry-*；最新功能依据使用delivery独立目录与production-first，旧4394份原文件及新证据、三份方案、用户.gitignore和暂存字节由p4-generation-retry-validation.json核对。具体命令见IMPLEMENTATION.md。

下一步实施正式重试页面与完整App恢复，继续任务池投影、草稿评审后精确关联、运行/环境适用性及P2/P1/P0/P5剩余项；完整P0—P5未完成，目标保持active。
