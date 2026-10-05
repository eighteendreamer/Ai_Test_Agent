# 增量生成任务池：实施前研究

研究基线：afd9d303。原目标附件已重新读取。此文件先于实现创建；实际结果随后记录。

## 查证结果与采用做法

- `schemas/task_pool.py`、`runtime/postgres_task_pool_store.py`：公开类型只有会话、规划、比较、影响；列表从业务表联合查询并分页，没有独立投影表。增加case_generation分支，仍从PG读取，不建第二份状态存储。
- `runtime/postgres_generation_task_store.py`：已有case_gen_groups/tasks/retries；任务终态及原调用检查点保存。retry.task_id唯一，父任务和原请求编号有独立列，策略在固定record中。子任务内部creation_key是retry:<随机关系ID>，不是用户重试请求编号；不能从子任务字段推断原请求。
- 生成组record只保存module_ids及RustFS固定scope引用，不能从PG直接取得所有模块业务种类。列表仅取已保存receipt.kind；未形成收据则种类未确定。详情读取原scope，不每行下载RustFS、不编造旧元数据。
- `main.py:496`、`generation_worker_runtime.py`初始化已有生成服务/业务表；现有任务池专项夹具未初始化生成表，需同步夹具与清理依赖，避免旧类型查询因新增UNION缺表失败。
- `CaseGenerationPanel.vue`、`GenerationRetryPanel.vue`和身份核对函数已有完整固定范围/收据/版本/原响应及显式重试。路由尚无生成详情。新增固定project/document/group/task路由；重试链接传原parent_task_id及creation_key，通过现有by-request API核对真实关系，不改历史Schema/hash或增加恢复调用模型的机制。
- 本地课程《Agent轨迹与协议鲁棒性》《评测工程化与CICD》的版本/轨迹/终态原则沿用：列表与导航只读；恢复保存的关系和完整依据；终态不重领，原失败不覆盖。实际课程摘录与新验证一起记录。

## 契约与页面

公开摘要保留GenerationTaskRecord的目录字段，排除checkpoint，附白名单retry引用（id、parent_task_id、creation_key、strategy）和可空结果种类。公开列表不返回seed/配置/Prompt/响应/对象地址/租约。项目/文档关联同时核对project_id；同一模块原失败和所有新子任务分别展示。

任务详情按URL明确身份读取固定task、group、scope；重试读取父task和原请求GET，复用assertGenerationRetry。详情可读完整JSON/自身说明、固定需求/评审、来源限制及原响应，复用原失败模块的GenerationRetryPanel。项目归档时禁新重试但保留历史/完整读取。新页面读取失败不回退latest，不执行生成，晚到响应按固定上下文丢弃；定时只刷新活动任务。完成仅表示草稿/候选保存。

## 真实验收

1. MD/DOCX/PDF原件上传、独立解析/比较及已有受控模型链路，形成真实生成组/原失败/两策略子任务和完整12步草稿。PG读取类型、项目/状态筛选、字面量搜索、分页及白名单，保存实际列表/关系/对象hash/物理请求。
2. 旧四类任务专项，确认初始化/分页/类型兼容；组件验证固定身份/重试来源/晚到响应/归档读取及错误可见，类型/Vite构建。
3. 真实DOCX正式App、独立生产API/Worker，任务池进入正常/失败/停用/两种重试，查看完整草稿和原响应。API重启关闭派发、项目归档后恢复同URL，同一PG历史和发送数量不变。受控模型证明流程，不替代外部默认模型业务质量。

没有新增依赖、配置键或业务表；可编辑策略仍MySQL，业务和目录PG，原件/固定大范围RustFS。完整P0—P5继续未完成。

## 实施后核对


重新读取目标附件，核对任务池联合分页、生成/重试权威表、固定读取及本地课程后实施。新增公开case_generation类型和白名单摘要，前后端消费方同步；固定详情复用现有生成/重试API和JSON树，不增加依赖、配置键或业务表。MySQL配置、PG业务和RustFS原件/大依据职责保持。

真实MD/DOCX/PDF上传、解析/比较进程、影响及生成/重试形成实际记录。最终新目录专项和旧四类兼容 **10通过、0跳过（46.58秒）**；另外旧任务池首轮5通过。验证queued/running/completed/failed、字面量搜索、项目/状态筛选、稳定分页、两种重试关系、终态不重领、归档与重建读取。实际发送取消后租约恢复保留unknown，不创建新重试或额外发送。最终组件 **108通过、0跳过，9文件**，包括15项新固定详情场景、旧任务池/生成/重试/需求/覆盖/评审/API。

失败原样保留：测试夹具将checkpoint赋空触发TS2322，改为正式类型；浏览器owned context不能新增page，改显式独立context。分页测试误改业务更新时间，改为创建同时间会话而不改冻结任务历史，最终10项通过。完整App先错误要求8条任务；转用真实存储的独立浏览器诊断（4及8条均通过），再保存正式App响应与DOM，确认同为7条，任务身份无遗漏。8份来源引用中原失败任务出现两次，验收按唯一身份计7；此前“API8条/DOM7条”判断已纠正。最后一次URL即时断言在异步导航完成前失败131.99秒，按项目已有expect.to_have_url等待实际路由，保留原失败。

最终正式DOCX built App、独立生产API/解析/比较/影响/生成CLI及MySQL/PG/Redis/RustFS **1通过、0跳过（169.57秒）**。任务池读取7个唯一任务、8份来源引用；进入正常/失败/停用/两策略子任务六项详情，查看完整草稿/自身说明/原响应，再刷新URL。API重启关闭派发、归档后重复读取同一PG任务/关系及完整历史，禁新重试。全过程6次物理模型发送，列表/导航/恢复0次新发送；原失败与unknown不重写。截图、DOM、响应及SQL留存。

旧正式增量App单独 **1通过、0跳过（111.36秒）**，池场景开关关闭，仍原4次发送，未采用新增完整创建夹具。实际构建由两次正式App harness完成，8个受影响Python文件py_compile及diff检查另作补充。所有失败/log/XML/原件/对象/消息/截图在p4-generation-pool-*；审计见p4-generation-pool-validation.json，5953份旧原始证据不改写，三份方案同步，用户.gitignore保持。

受控模型验证流程和固定依据，未调用外部默认生成模型、执行生产历史迁移或代替用户业务评审。运行/环境/回归闭环及P2/P1/P0/P5剩余目标未完成，完整目标保持active。
