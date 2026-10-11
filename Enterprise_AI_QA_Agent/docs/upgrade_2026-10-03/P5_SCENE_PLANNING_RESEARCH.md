# 正式用例关联场景进入规划

基线fee770f0；完整P0—P5未完成，外部默认模型验收按用户决定暂缓。

## 依据与本批做法

- scene_service.create_draft保存ui_recording_scene来源的scene_id/version_id/content_hash；场景PG store按项目读取并重算完整content/source哈希。知识工具已能完整读同一版本，不创建另一份知识数据库。
- ProjectPlanningSources.capture_document_basis与Workflow sources阶段仅处理文档。linked_source_queue已保留所有明确来源，场景目前成为错误记录，而不是已读依据。先以新HTML/CDP/production main创建实际场景和正式用例复现。
- CasePlanningPolicy及MySQL配置已经固定到PG seed；旧Prompt/Schema和检查点有历史等价测试。新增关联来源v2，v1继续原行为，不修改旧任务。沿原basis字段增加严格场景身份类型，旧DocumentContentBasis序列化保持。
- 源件固定分两步：校验明确引用并保存scene/version/hash基准→按基准读取完整知识并保存证据。缺版本/hash不选择当前版本；跨项目、错误版本/hash明确记录失败；必要信息不足交给模型判断影响，不全面阻断独立项。
- 原PG checkpoint检查禁止替换已保存basis、引用和读取结果；沿原确认丢失/租约恢复，不新增任务运行体系。证据单独kind=scene，整理状态标记not_executed，不能假称recording或已执行。
- 当前规划页面提供关联来源开关，改为文档及明确关联场景；前后端政策Literal/类型与实际保存同步。无新表、依赖或全局配置回填。配置仍MySQL、业务依据/计划为PG。
- 本地Agent轨迹与协议鲁棒性课程要求版本、轨迹、恢复与漂移证据；评测工程化课程要求固定数据/评分依据。完整内容预读仅证明资料到达，不证明AI判断质量或测试通过。

## 验证与未覆盖

新的真实录制/场景/正式启用用例→MySQL项目策略→PG规划任务，首次推理前证据固定；在基准和读取结果提交确认丢失后停止，API重启、场景头更新及策略变化后重新创建服务恢复；终态重投不新增调用。跨项目、缺版本/hash、错误hash、完整正文/别名与旧v1恢复单独验证；原事件和场景版本不变。

受控响应只驱动原规划状态机，不调用外部模型；不宣称默认Agent已理解/执行。未关联场景的自主目录检索、录制清洗/遗漏、人工DeepSeek、失败定位/报告和完整其他模块保持待办，不能用关联预读替代。

## 实际结果

旧实现真实before1失败21.00秒，明确引用被文档读取拒绝，PG entries.read=false。新实现verified1失败55.25秒为夹具误访问CasePlanningTaskRecord.summary，实际字段message；主恢复已完成。delivery1通过52.32秒；扩大基准和读取结果两种确认丢失位置，final2通过95.15秒，0跳过。每例新HTML/CDP/main/built App和实际MySQL/PG；模型前保存，API重启和场景头/当前策略变化后仍读原V2，seed不变、2个受控模型回合和终态0追加。4种错误来源及2种检查点修改真实拒绝，原事件不变。

受影响59通过3格式解析门控跳过55.75秒；新合同5通过5.23秒，真实旧快照/Prompt和知识投影保持，v2下实际Markdown/RustFS/PG/MySQL链通过。页面旧标题断言首轮1失败66通过保存；最终68通过3.36秒，实际产品build/vue-tsc通过，页面错误0。编译退出0仅补查。

确认丢失为实际SQL commit之后注入异常，租约恢复和服务重建不等同真实Worker进程kill。main真实创建资产、接受新策略和展示App，规划handler在测试进程读取同一数据库并用受控响应驱动；不宣称默认Agent或主进程自主规划。未关联场景查询、场景预期用于技术效果声明、完整AI/执行及生产迁移仍未验。

## 追加契约与调用方核对


交叉核对实际已保存场景证据，发现旧短引用目录将content_hash与整个payload哈希比较；场景content_hash是原始content/source快照哈希，两者不同。真实原件复现见p5-scene-planning-reference-before.log。新增可选payload_hash覆盖完整知识投影，目录校验同时核对原始快照与投影，场景E目录包含投影哈希防止同一快照下投影漂移；旧证据省略该可选字段，旧seed/checkpoint/Prompt和文档目录保持。logic仍用正式ID，开启短引用时effects使用E并由系统还原，不把场景冒充文档预期。

| 本次追加实际验证 | 结果及边界 |
|---|---|
| reference-final | 4失败189.70秒：新增篡改检查误用alias字段，实际SceneAlias为name；原失败及SQL保留，修正夹具后重新验收 |
| reference-complete | 4通过189.44秒，0跳过；普通/短引用×基准/已读结果提交确认丢失。每例独立HTML/CDP/main/App/真实数据库，首次推理前固定；头版本/当前策略变化和服务恢复后原seed与V2保持；E还原正式场景ID。每例3种快照/投影篡改、4种错误来源与2种检查点变更拒绝；原始事件整行不变，终态0追加调用 |
| regression-final | 69通过3格式解析门控跳过64.05秒；包含实际文档兼容、旧快照/Prompt等价、原知识投影、实际场景双哈希/目录漂移核验。3跳过不计完成 |
| worker-before/worker-complete | 新场景经正式planning_worker_runtime复现1失败19.88秒，未配置场景store。补齐原启动流程的初始化/注入及日志，最终5通过53.53秒；其中1项真实main/App/CDP/MySQL/PG/Redis和两次Worker Runtime初始化/关闭，API重启后读相同V2完整证据；其余4项CLI导入隔离和原部分启动清理回归，模型调用0 |

Worker专项是正式启动函数在测试进程的真实存储读取，不是独立CLI完成规划、更不是默认AI执行；上述规划仍由受控模型响应驱动。真实进程kill、独立CLI完整场景规划、未关联场景按需查询、场景预期技术声明和全部默认AI业务验收仍待推进。最新compile-worker-final退出0仅补语法。无新表/依赖、全局模型修改或旧配置自动升级；Worker初始化已有场景表沿既有PG初始化权限执行，生产部署/历史迁移未验。原件和旧工件校验、三份方案同步见p5-scene-planning-validation.json。
