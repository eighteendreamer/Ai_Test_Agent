# 正式执行核验关联页面：实施前研究

研究基线8efcdd99。已重读用户目标、PLAN 3.6.12/5.2.2、固定执行契约/PG事务、现有覆盖管理与完整App夹具，以及Agent轨迹课程。上一阶段是真实API与存储验证；本阶段完成正式编辑和浏览器实测，保留完整P0—P5。

## 实际依据 → 采用做法

- RequirementMatrix.vue、RequirementCoveragePanel.vue及useRequirementCoverage.ts：沿用固定需求版本下的展开面板、上下文代次、sessionStorage原请求、显式恢复/原请求重试、409重新读取、归档只读。覆盖和执行编辑互斥，防止两个控制器互相回写矩阵。
- postgres_requirement_execution_store.py：父确认按同需求版本全局最新记录，矩阵按coverage_id匹配。读取全局历史取得父确认，编辑冻结打开时的覆盖身份/hash；其他覆盖的历史仅提示，不混入当前矩阵。指定历史不改变待提交集合和父确认。
- requirements.api.ts、projects.api.ts与TestRunDetail：候选按固定覆盖分页，完整依据从实际run结果与固定case version读取；候选不是正文依据。用户明确选择check及其允许的point_keys，服务器继续验证真实ToolJob和hash，不新增模型调用。
- 原回执actual.verification_results：只选phase=case正文检查，展示原check中的预期/实际/算子/理由、整条result.status、回归源及原工具引用。仅所选检查通过不代表完整需求通过，deployment保持unknown。
- test_document_impact_full_product.py及覆盖产品场景：采用既有built App、独立生产API/解析/比较/影响CLI、真实DOCX/PG/MySQL/Redis/RustFS夹具，新增可选执行场景；默认旧场景保持。实际业务HTTP和ToolRuntime生成失败及新回归，浏览器通过正式页面关联、撤销、历史、未知回执、重启归档读取；SQL/对象/原消息/截图/DOM留存。

## 本阶段契约与状态

后端公开契约保持不变；前端候选摘要补精确类型，新增固定确认/完整结果校验和独立编辑控制器。每次提交完整选择集合及原request_id、coverage_id、expected_execution_id和说明。sessionStorage按项目/文档/需求/版本/覆盖隔离，冻结需求及覆盖hash；恢复前先检查原参数。未知POST继续保留原编号，不自动重新发送；404不能证明未提交，明确拒绝才能重读后作新决定。

候选和历史分页验证身份、顺序与是否前进；迟到响应不得写入新上下文。选中正文检查必须先成功读取完整结果和固定用例；归档允许这些只读操作及原请求恢复，禁新确认。空集合加说明为撤销，旧确认保留。固定确认读取后不查询当前工具重新计算历史。

## 验收

先用上一阶段真实保存样本做组件验收：完整12步JSON/False/0/null、清理失败而正文通过、正文失败与回归、同覆盖历史/撤销、全局父确认、异覆盖、原请求恢复、404/503/409、迟到响应、项目/版本/hash错位和归档。再运行真实DOCX完整App实际提交/刷新/重启/归档和原覆盖App兼容，核对SQL及物理发送；页面操作/恢复新增模型发送0。外部默认Agent自主执行、环境适用、用户业务评审和生产历史迁移另行验收，不由本阶段代替。

## 实际实现及验证

已接RequirementExecutionPanel/useRequirementExecution及固定确认/完整结果核对；原覆盖编辑保留，两个控制器互斥，固定上下文改变时原响应不能写入。候选先读取原完整run结果与固定case，已有覆盖的content_hash同时核对；服务器仍负责原版本/结果/工具内容hash，不在浏览器重算Python历史hash。历史按固定覆盖读取，提交父记录按全局历史取值。

最终120项组件/8文件通过，其中21项新编辑场景；实际DOCX built App87.75秒通过、独立生产API62.33秒通过、原覆盖App152.65秒通过。App读取真实业务HTTP/ToolRuntime与受控回执产生的原失败/清理失败，五条实际PG确认、回执丢失/原GET恢复、撤销/固定历史、实际409、重启归档完整读取均核对。执行/恢复新增模型发送0，业务HTTP6次，原影响2次发送与固定请求一致。两个App实际构建通过。

保留首次构建TS2352（非法负向测试转换）及共享测试夹具抽取后的NameError，分别改正测试表达和f.jobs引用；扩大测试命令未命中的两个名称已按实际文件纠正，XML按8文件120项记录。全部证据p4-execution-product-*；新实测不回写旧证据。默认Agent自主执行和环境部署适用仍待实施验收。
