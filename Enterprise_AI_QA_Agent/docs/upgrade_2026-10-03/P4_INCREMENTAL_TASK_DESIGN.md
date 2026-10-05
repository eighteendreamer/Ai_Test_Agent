# 增量模块任务与草稿事务研究

研究基线06471590。范围是把确认需求/冻结生成实际接到持久模块、调用响应及草稿事务；正式API、生产CLI和页面仍需后续接入及独立产品验收，完整P0—P5不缩减。

## 当前代码依据 → 采用做法

- postgres_document_operation_store.PostgresDocumentTaskStore已提供项目/文档锁、原请求幂等、同事务Outbox、原子领取、租约、原总期限和终态不可重领。DocumentOperationTaskHandler提供真实Worker心跳/取消/终态重投。复用这两层，生成每模块一任务，不复制租约体系。将已有插入任务/Outbox SQL抽成事务内辅助，单任务提交语义不变，组提交调用同辅助。
- case_store.insert_drafts_in_transaction已经用于导入同事务收据；_append_version_sync会锁case、保留启用指针、追加draft review和新版本，但没有调用方事务入口。抽出append_version_in_transaction，旧方法复用；新调用检查原项目/预期最新版本，不自动重定基线。模块收据和case/version写入同一事务，最后再核验租约，行锁等待期间过期则全部回滚。
- incremental_generation_sources读取原确认需求版本/review/固定input，已经捕获完整请求和MySQL模板/Skill/model/runtime。组只保存身份、请求hash和RustFS对象引用，任务seed保存组/模块hash及固定MySQL策略，不重复把大请求塞入PG。锁内重核原需求/review/doc版本及case版本归属/hash；恢复只读组对象，不重新捕获当前依据。
- impact_calls.estimate以完整system/messages UTF8估算、framing预留及固定输出/context窗口约束；impact_checkpoint_io先存对象/PG调用意图、原响应，再解析，未知提交不授权重发。首版生成沿用已验证的UTF8内容估算方法，不自称供应商token精确计量；字节/响应/时间预算作为新MySQL设置，固定在seed，不改全局模型。SDK默认或single_attempt沿既有ModelTransportOptions冻结。
- planning_workflow对已存调用意图/未存响应保留unknown，保存响应后恢复不发送。生成同样仅允许一次模块调用；unknown只标失败和保留证据，显式新任务重试另行设计，不重置终态。实际收到供应商错误/工具/截断等原响应也先存RustFS再校验，不丢失败依据。
- 原生成/版本治理和本地合成、评测工程化、Agent轨迹课程要求来源/模型/Prompt/Skill可追溯、原失败保留。草稿generation_metadata由系统追加确认需求/验证点/模板/固定请求/响应/模块身份，模型不能覆盖这些来源。生成不自动启用、停用或证明部署生效；removed只形成PG收据中的停用候选，不调用模型。

## 状态与验证

组提交：按原creation_key/hash查已有 → 捕获一次范围/策略 → RustFS原对象 → PG组+模块任务+Outbox同事务。回执不确定保留对象/原请求只读回查；健康并发返回先存组，确认无引用的多余对象才删除。已有提交/归档历史恢复不读取当前配置/Skill/资料。

模块：原子领取 → 验固定范围/完整预算/活动项目 → 保存调用意图 → 实际HTTP → RustFS原响应+PG检查点 → 候选校验 → 原子草稿+模块收据/终态。重启只读response_saved；invoking没有响应一律unknown不重发。模块独立失败，组报告保留全部模块，不把task completed当业务评审通过。

必须使用真实MD/DOCX/PDF/解析比较、MySQL/PG/RustFS/HTTP及Worker核验：更新/新建/移除，固定12步JSON及未知字段，当前配置变化，原请求/并发/丢真实PG提交回执，响应恢复禁止HTTP，真实在途取消unknown，过期租约、原case父版本冲突、duplicate key事务回滚及终态不可重领。新增共享事务辅助需跑原文档提交/旧用例版本/导入相关回归；证据与失败保留。未接公开/生产入口时如实说明，不以内部可调用代替正式产品。

## 原总期限的实际缺口与修正

2026-10-05核对共享`_owned`发现它只核验租约/attempt，而不核验deadline。新模块在调用前已检查期限，提交草稿却没有在用例行锁等待后重查。真实事务持有原case行锁，任务timeout=1秒、lease=30秒，原响应已入库后继续等待1.1秒；旧提交行为实际产生V2并完成任务。`p4-generation-tasks-deadline-before`保存这个失败及真实完成收据，不将它归类为时序测试错误。

修正复用既有`clock_timestamp()`和原deadline_at：保存发送意图前、草稿事务开始与锁等待后均核验原期限；最后一处与租约fence同事务，过期整笔回滚。失败终态仍允许在有效租约下保存，原响应保留；新租约不重置原deadline。租约过期可按原响应恢复，期限到期不再提交草稿。关闭派发先查原组，原提交可读；新提交在捕获配置/范围及上传对象前明确拒绝。

## 实际验证与剩余产品范围

- 原实现首轮6通过82.42秒；扩大后8通过/1失败104.76秒，失败是测试LIKE字面百分号未参数化。改成已有`%s`参数绑定，原失败记录保留。
- 原提交期限缺口实测1失败/9 deselected，23.62秒；恢复修正代码后，新模块14通过168.72秒，真实MD/DOCX/PDF、数据库、RustFS、HTTP及Redis Worker。增加关闭派发场景后1通过/14 deselected17.76秒；当前15项均实际验过，不累计重叠测试为新覆盖。
- 共享解析/比较/用例评审/导入四模块49通过107.73秒；独立生产API、解析/比较/影响CLI与需求评审恢复1通过120.66秒。后者只证明旧生产路径兼容，没有调用新增量模块。
- 正常三格式场景才使用同进程Redis Worker；其他边界直接领取实际PG任务执行。旧证据中泛写的actual_module_worker不能解释成独立生产进程；新证据明确区分leased module、Redis Worker和独立进程。

正式增量API、独立生产Worker/CLI、前端契约和页面、用户业务评审与外部默认模型尚未接入/验证。失败模块的显式新任务重试仍需研究保存原范围和原失败身份的契约，不能重置终态，也不能把新请求重新捕获当前依据冒充原失败恢复。生产历史迁移/对象对账与其余P0—P5保持未完成。
