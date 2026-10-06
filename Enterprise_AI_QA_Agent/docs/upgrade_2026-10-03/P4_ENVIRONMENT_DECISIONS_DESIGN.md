# B步：固定需求范围的环境生效与用例适用评审

研究基线：1ec34cef，2026-10-06。保留完整P0—P5目标。

## 查证结果与采用方式

| 依据 | 实际事实 → 本步做法 |
|---|---|
| postgres_requirement_coverage_store.py、postgres_requirement_execution_store.py | 同项目锁内先恢复原请求，再核对生命周期、固定版本和预期上一确认；采用相同追加事务，不修改需求正文或旧执行关联 |
| RequirementVersion、RequirementCoverageReview | 覆盖属于具体需求版本，initial与后续覆盖均可作为明确依据；提取现有固定覆盖读取到共享函数，执行关联与新确认共同调用，不复制两套版本判断 |
| postgres_business_environment_store.py、business_tool_observation.py | 环境快照已冻结MySQL实际配置；工具来源来自PG原输出。新提交在同一PG事务内重新读取二者并核对客户端明确选择的hash；不读取当前配置、不发送探测HTTP |
| requirement_execution_checks.py及原矩阵 | 正文检查只证明所选检查，deployment_status始终unknown；独立增加环境评审记录，原矩阵和旧hash不变，不把一条观察转成整个需求通过 |
| 本地Agent轨迹与协议鲁棒性Action/Observation；评测工程化与CICD版本化条目 | 原始观察与评审决定分开；固定需求、覆盖、用例正文和来源，保留撤回与回滚历史 |

## 本步契约

一个追加链绑定project、固定environment_snapshot及requirement_version，不跨环境或版本继承。提交显式指定document/requirement/version、快照SHA、需求SHA、coverage_id和coverage SHA、预期上一决定及请求身份。覆盖变更仍需明确选择，不偷偷取最新。注册、捕获、覆盖及原ToolJob均在保存时核对正式身份。

决定范围是选中的验证点和精确用例版本。每个点为effective/not_effective/unknown；每个用例为valid/invalid/unknown并指明涉及点。已确定的点必须引用本固定目标内的实际网络观察；地址不匹配、旧回放、缺来源或脱敏值仅支持unknown。invalid须明确引用本次已确认effective的相关点，不能根据文档更新或unknown停用用例。语义理由由显式业务评审提交，后端不编造行业规则或根据7/9自动判断。

来源请求只携带原ToolJob、字面量字段路径和已选择的输出/字段SHA，不接受客户端原值、来源或匹配判定。事务重新读原输出及检查点并保存系统派生观察。评审保存需求版本完整固定正文、覆盖引用/SHA、所选精确用例版本完整正文与hash和观察，历史读取只核对固定记录，不再依赖当前配置或工具重新发送。

撤回是新记录action=revoke，指向明确上一决定，空点/用例/观察，表示当前范围unknown，不恢复旧effective或删除历史。重新确认/回滚追加新review并重新选择证据。unknown也可以无观察保存具体缺口；初次revoke无意义，拒绝。并发由既有项目→业务行锁顺序及expected_review_id解决；同请求丢回执优先返回原记录，归档后仍可恢复。

新项目环境子API提供提交、按原请求读取、具体历史读取及按固定需求版本分页。前端类型/服务/统一API同步。正式页面、AI候选、矩阵环境区域、计划/启动/领取/回归约束属于后续C/D，本步不宣称已实现执行限制。不引入部署轮询、独立测试模式、新依赖或MySQL历史接口。

## 实际验收

使用原有真实MD/DOCX/PDF上传、独立解析、确定性差异和影响任务得到固定需求及覆盖，再通过ToolRuntime调用两个各自固定7/9天且release相同的受控HTTP目标。核对旧目标not_effective/valid，新目标effective/invalid，跨目标unknown、未评审点无继承，精确case/point/version/hash错误拒绝。验证追加、撤回、原请求恢复、归档、四路并发、真实PG提交回执丢失及当前配置删除后恢复；保留原件、实际ToolJob、SQL和请求记录。扩大既有覆盖/正文执行关联回归，并独立验证正式main API及前端构建。
