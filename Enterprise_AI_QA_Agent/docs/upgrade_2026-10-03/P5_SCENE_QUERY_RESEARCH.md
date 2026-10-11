# 规划按需检索项目场景

基线b8c69d4e；上一批固定关联预读不是未关联场景检索，完整目标仍未完成，外部AI按用户决定暂缓。

## 依据与实施判断

- recording_scene_store.search已有项目过滤、名称/说明查询、limit+1分页、未归档头及固定version_id/content_hash导航；scene_knowledge及规划read_scene_basis复用同一PG完整content/source快照与别名投影，不建立另一个知识库。
- PlanningQuery/BusinessPlanningQuery当前没有scenes/scene类型；Workflow目录只有用例/文档/录制。缺口属于查询契约和启动后的规划消费能力，不是新增业务检查清单。用新录制/API场景验证原Schema拒绝，再实施。
- 原MySQL政策冻结到PG seed，旧Prompt/Schema存在逐项等价证据。新增可选scene_query_protocol，旧协议保持原Schema与序列化；新查询类型进入原logic/effects、pending_decision和恢复流程，最终可执行计划仍保留原结构。
- 目录只用于导航，不产生scene已读证据；正文请求必须包含场景、版本和完整哈希，调用原固定读取。错误来源保留具体query_result，不能把scene退化为recording或静默读取当前头。
- 查询请求及逐项结果沿原PG检查点保存；确认丢失后的恢复使用原pending_decision/版本，已有结果不重复查询。原回合/截止时间共用，不另起递归或Agent循环。
- 既有正式Worker和main均已注入同一场景store。本地Agent轨迹与协议鲁棒性课程末节要求超时/空返回/部分失败注入与恢复；本批用真实新HTML/CDP/API/MySQL/PG/Redis、头/政策变化和提交确认丢失验证。受控模型仅证明协议和资料到达，默认AI选择质量与实际业务执行另验。

无新表、依赖、默认模型变更、全局配置回填；配置MySQL，任务/版本PG，大原件RustFS。项目开关/API/类型、阶段Schema、查询消费和历史恢复需同步。

## 实际验证与边界

基线b8c69d4e，依据见P5_SCENE_QUERY_RESEARCH.md。已有场景search能给项目隔离、分页及version_id/content_hash导航，固定正文读取也已存在；原PlanningQuery/BusinessPlanningQuery不接受scene/scenes，规划目录未列场景。本批新HTML/CDP/main/PG场景复现before1失败20.46秒：数据库有实际场景，但旧Schema拒绝scenes。属于需补查询契约与消费能力，不能只把场景目录当正文。

新增MySQL可选scene_query_protocol；旧配置不回填，新政策冻结到PG seed。新增独立查询/两阶段Schema和v13 Prompt，保留旧Schema/Prompt/序列化；同步原pending_decision、logic/effects解析和查询消费。scenes按项目查询目录并分页；scene必须有source_id/version_id/content_hash，沿原PG固定读取完整步骤/原始响应/别名及双哈希投影。目录不产生已读证据；not_executed不代表通过。查询请求/逐项结果沿原检查点，共用原回合/时间，不新建规划循环。关闭查询策略仍保留历史任务恢复。

项目页面增加“允许规划时查询项目场景”；类型/API/保存与依赖策略关闭同步。不要求用例先关联场景，不修改原测试模式、不将用例编译为模式参数、不读取生成模板。main和正式Worker沿上批已接的同一场景store，未新增模型依赖或单一模型限制。

| 实际验证 | 结果与范围 |
|---|---|
| before | 1失败20.46秒，实际新场景存在、旧Schema拒绝。原PG导航与错误原件保留 |
| verified | 4失败146.66秒，分页新增1个真实场景，原产品夹具将总数写死2而实际3；按additional_scenes参数核对，默认夹具行为不变。原失败/SQL保留 |
| complete | 4通过186.79秒，0跳过；logic/effects×查询请求/读取结果提交确认丢失。每例真实HTML/CDP/main/App/MySQL/PG/Redis；正式用例无场景来源引用，page0/page1导航无scene证据，读取才产生正文；API重启、场景头/当前政策变化后原seed及V2保持。原任务5个受控模型回合、固定读取1次、终态追加0。证据阶段E还原正式ID；跨项目/错误hash/关闭策略拒绝，原事件整行不变 |
| 合同及回归 | 新合同12通过0.68秒，读取实际原导航与4份v12 seed/checkpoint/Prompt，保持等价。初查27通过9门控跳过4.48秒；实际规划存储开关启用后回归85通过3格式解析门控跳过68.89秒，最后恢复旧协议V4 query转换的原行为再验85通过3跳过61.03秒；不累加两次回归数量 |
| 页面/构建 | 73通过3.30秒；新开关保存及关闭依赖策略同步。4次实际product build包含vue-tsc/Vite通过、页面错误0。compileall退出0仅补语法 |

确认丢失发生在真实PG commit之后；新建服务/租约恢复不等同真实进程kill。规划handler在测试进程使用实际存储与受控模型；不宣称独立CLI完成新查询、更不宣称默认AI自主查询/理解/UI执行。已有查询契约与恢复已验，默认AI质量仍待验。

仍需推进：场景预期用于技术效果声明、独立CLI/Worker进程故障、人工DeepSeek、清洗/遗漏/复杂网络及跨文档/Shadow DOM响应关联、selector修复与失败定位报告；准备状态/未知副作用/执行修订/其他模式，API完整Agent及续期，性能容量/流式与其他安全工具，用户业务评审/部署/历史迁移及全阶段物理HTTP、旧Worker原件。完整P0—P5继续active，外部AI按用户决定暂缓。无新表/依赖/全局模型或旧项目配置修改。原件、前序456+149工件、保护文件及三份方案同步见p5-scene-query-validation.json。
