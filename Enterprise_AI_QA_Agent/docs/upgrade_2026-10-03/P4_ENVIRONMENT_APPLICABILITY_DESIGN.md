# 环境适用性：代码查证、固定依据与实施边界

研究日期：2026-10-06；源码基线：`d47ab66d`。本文件是实施前的工程设计，尚未实现环境登记、部署确认或执行限制。完整范围仍为PLAN中的P0—P5。

## 已确认的现状

| 实际依据 | 事实及设计影响 |
|---|---|
| [项目契约](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/project.py)、[项目服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/projects/project_service.py) | `base_url`可修改；没有正式业务环境身份。不能按名称、URL相似或当前项目URL把历史结果迁移到另一环境 |
| [规划契约](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/case_planning.py)、[规划服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_service.py) | `runtime_context`是任意JSON；字符串environment进入规划上下文，但不证明部署。规划任务已冻结project_context，当前Run中的计划没有正式目标环境绑定 |
| [影响依据](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/documents/impact_analysis_context.py)、[影响环境契约](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/document_impact_analysis.py)、[影响提交页](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/agent_web/src/features/tools/plugins/DocumentImpactPanel.vue) | **已有**environment_key和target_release声明，固定provenance=user_declared及deployment_status=unknown；Prompt禁止据标签推断已生效。保留这条已有链路，不能再说系统完全没有环境信息 |
| [资源作用域](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/resource.py)、[兼容矩阵](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/compatibility_testing_mode/environment_matrix.py)、[安全执行环境](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/execution_environment_service.py) | environment_id是资源作用域，兼容矩阵描述浏览器/设备，安全环境承载工具进程；它们与被测业务系统的部署身份不同，保留现有用途 |
| [配置存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/infrastructure/application_config_store.py) | MySQL配置当前按scope/key保存payload与revision，save更新原行；get没有指定历史revision参数，save没有expected_revision参数。不能设计成“运行恢复时按旧revision回读MySQL”，也不能宣称已有配置CAS |
| [计划映射](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/plan_mapping.py)、[运行创建](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/plan_run_service.py)、[运行存储](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/run_store.py) | 创建hash包含完整plan和request；PG事务内锁项目、核对计划/用例/父回归并写套件、运行、条目及Outbox。适用性必须接入这条链路，不能在网页预检后认为没有并发变化 |
| [执行上下文](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_case_context.py)、[回归服务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/agent_regression_service.py)、[需求契约](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/schemas/requirements.py) | 执行复制原runtime_context，回归复用原输入；需求矩阵部署只允许unknown。已有正文通过与原失败结果不能自动转换成已部署结论 |

本地课程的版本基线、工具轨迹、协议变更、幂等及恢复原则作为专业依据；课程里的Git存储、比例、阈值或模型版本示例不作为本系统事实，不引入Git保存上传原件。

## 可复现检查与结论

[研究脚本](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/environment_research_20261006.py)核对上一阶段335份原始证据及17个已提交源码blob，读取真实DOCX完整App所存的两个完整RunDetail，以当前映射重建普通运行和失败回归：原run_id、creation_hash和整个plan_snapshot均一致，两个用例各12步，原结果均failed，部署仍unknown。

两个真实运行的runtime_context均为空，没有正式environment_id/deployment_id。受控HTTP已证明7天失败与9天正文通过、清理失败的区别；它们还没有对应正式环境及部署确认，因此不能给整个项目贴“新需求已生效”的标签。本次只重读保存证据，不重启API/Worker、不重新发HTTP、不调用模型，不把历史实测算作本次现场实测。

直接在旧request序列化里添加environment_id=null，会改变这两个原创建hash。后续新契约必须显式版本化/兼容原hash输入；不能仅凭“字段是可选的”宣称历史恢复不受影响。原始事实与限制保存在[p4-environment-research-facts-verified.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p4-environment-research-facts-verified.json)。

## 最小领域边界〔待实施〕

分清三件事：**准备测试哪个目标、实际观察到什么、评审确认某条规则在该目标是否生效**。它们不能共用一个可随时改写的environment字符串。

| 记录 | 内容与存储 |
|---|---|
| 项目环境身份 | PG中的稳定environment_id、project_id及生命周期。名称和地址不是业务主键，不复用资源池的environment_id |
| 当前目标配置 | MySQL中的显示名称、非敏感目标地址、已有连接配置引用和可编辑策略。每次提交实际读取整份配置及revision；不设计不存在的历史revision读取API |
| 固定环境依据 | PG中的项目/环境身份、实际捕获配置修订、非敏感payload及SHA、捕获时间/请求身份。旧计划和运行只读此依据；当前配置改动不能替换历史目标 |
| 部署/行为证据 | PG保存固定环境依据、原run/item/attempt/ToolJob/result、字段路径、原值/hash及观察时间；大原始输出和附件沿用RustFS。证据引用必须重新核对项目、目标与已保存输出 |
| 生效与适用确认 | PG追加记录：固定环境依据、需求版本/hash、覆盖确认、精确用例版本、具体证据、理由、上一确认及请求身份。不原地修改需求正文、用例版本或旧结果 |

环境身份登记与配置保存是两个明确操作：登记成功而配置尚未保存时显示“未配置”，不能伪造一个跨PG/MySQL的原子事务。新规划明确选择环境时，配置缺失就返回缺口；历史无绑定运行继续按原契约读取/恢复，显示环境未知，不强制迁移。

保存固定环境依据采用现有“读取实际配置→冻结提交依据”的做法。没有历史MySQL读取接口时，完整非敏感配置快照必须随业务提交保存；密钥只保留现有凭据引用，不复制进PG/普通日志/模型。配置写入并发语义应先验证现有save行为，再决定是否需要新版本写入契约，不能凭空声称已有CAS。

部署证据优先引用现有Agent工具产生的真实持久输出。初期不引入自动轮询、Git同步、CI发布系统或默认/version探测接口。配置/版本号声明只作线索；观察到某个release标签也不等于每条业务规则已经实现。人工资料必须保留来源及明确确认，缺少可核对的目标或依据时保留unknown。

## 简单的生效判断与执行行为〔待实施〕

评审只回答：**在这个固定目标中，这个需求版本的变化是否已生效，哪些精确用例版本因此仍适用或不再适用？** AI读取原差异、完整用例和具体证据提出候选，用户按已有评审方式确认。后端核对引用和状态，不另建行业规则清单，也不把模型解释当真实部署证明。

| 确认情况 | 对执行的影响 |
|---|---|
| 依据不足、声明或目标无法对应 | unknown；展示缺口，不据此停用旧用例 |
| 有依据确认尚未生效 | 旧版本仍可用于该目标；新草稿关联未来需求版本，不提前替换旧执行依据 |
| 已生效，但原用例仍符合规则 | 精确旧版本仍适用；无需因为文档更新而强制换版 |
| 已生效且重大变化使原用例失效 | 只限制已确认的用例版本在该固定环境依据下的新执行；依赖它的相关目标显示原因，独立项目/环境/版本继续运行 |
| 撤回确认、换目标、部署回滚或证据冲突 | 追加新确认/观察，重新判断；原确认和运行保留，不删除旧记录或重置终态条目 |

“旧用例预期7天，而新文档写9天”的例子：仅上传新文档不能阻断旧用例；目标A仍返回7天时旧版本可适用；目标B依据确认9天规则已生效且旧预期不再成立后，限制的是旧用例版本在B的新执行。负向用例、未变化的验证点和其他目标不自动停用。

适用性读取按project_id、固定环境依据、requirement_version/hash和精确case_version_id定位，不能取全项目最新确认。环境名称相同、URL变化、另一版本通过、另一环境通过均不转移证据。明确确认只覆盖选中的验证点/用例范围，不把部分正文检查扩大为完整需求通过。

## 接入现有规划、运行与恢复〔待实施〕

1. **规划时固定目标。** 新提交可以选项目内正式环境并保存固定依据；AI理解用户目标、项目选取、初排、补必要前置、最终排序。目标绑定不重新加载生成模板，不把用例参数编译成测试模式配置。初始目标和补入正式前置都检查精确版本适用性，prepare不能绕过已确认失效的正式依据。
2. **启动时核对新变化。** 创建服务读原计划目标，并在既有PG事务中核对同一目标最新有效确认及预期确认身份。原请求已提交时优先返回原Run；未提交而适用确认已变化时显式冲突/受影响范围，重新决定，不偷偷改原计划。共享锁顺序继续沿用项目→业务记录；网络读取及MySQL访问不持有PG锁。
3. **执行时保存采用的确认。** Agent上下文注入固定目标/采用的确认与完整用例。启动后部署或确认变化，未开始的相关条目通过既有调度与状态流明确等待/阻断；实际领取事务及恢复入口需专项验证。正在调用工具的副作用不能靠改环境标签撤销，沿用未知副作用核对机制，不盲重发、不重置终态。
4. **回归是新运行。** 默认引用父运行的固定目标，原失败结果保留；明确选另一个目标时形成新固定依据及新创建请求，展示与父目标的不同，不继承父目标的“已适用”结论。新版本替换仍走评审、启用和固定版本校验。
5. **旧契约保留。** 旧request/plan/result/调用hash输入、原Checkpoint与丢回执重放不变。新增字段缺省不能进入旧hash。采用独立版本化的环境绑定依据，再同步新请求/执行上下文/PG比较和前端消费方；迁移只补标unknown，不伪造历史环境或部署。

原需求影响及正文执行关联记录中的deployment_status=unknown保持原语义。新矩阵在独立“环境生效/适用确认”区域展示新确认，不重写旧分析或旧执行关联为effective。界面分别展示需求设计覆盖、选中正文检查、环境生效和用例适用性，不能合成一个“通过”。

## 实施与真实验收次序

| 小步 | 交付与必须运行的验收 |
|---|---|
| A 环境身份/配置/固定依据 | 正式服务与API、MySQL配置、PG身份/捕获；实际隔离存储验证跨项目、配置改址、缺配置、未知提交及归档重读。先证明历史两个Run的原hash仍可恢复 |
| B 具体证据/确认 | 引用实际ToolRuntime结果及原件；受控两个真实业务目标分别返回7/9天，验证字段/hash/目标不一致、缺依据、版本标签与规则生效区分、追加/撤回/冲突和同请求恢复 |
| C 规划/启动/领取/回归 | 用完整12步JSON实际执行；验证启动与确认并发、正式依赖、独立项继续、配置改变不换历史、执行中变化、未知副作用、父失败/新目标/新版本回归。事务、状态、API/DTO/类型/服务同步，保留旧模式 |
| D 正式页面与完整产品 | 项目环境、影响环境选择、需求矩阵确认、固定计划/运行/回归详情；真实文件→独立API/Worker→built App，实测丢回执/409/项目切换/重启归档，随后独立验默认Agent自主执行 |

各小步查证后再落具体schema/路由/表名，不把这里的领域名称宣称为现有API。先小步验证，再接消费方；不先建未使用的通用部署平台。生产历史迁移、点级完整性、默认Agent、P2准备状态与修订、P1模板导入、P0录制/Docker/物理发送及P5知识报告仍保留。
