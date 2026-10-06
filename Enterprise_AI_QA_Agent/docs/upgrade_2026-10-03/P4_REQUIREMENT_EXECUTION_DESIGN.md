# 需求验证点关联实际执行：实施前研究

最初研究基线12d2ebd4，实施基线c27efe0e。已重新读取原目标附件、当前方案和本地Agent轨迹/版本化评测课程。以下决定基于实际调用链，不把设计覆盖或模型声明当执行通过。

当前固定契约、原核验检查、PG追加存储、服务与公开API已接，前端类型/客户端、身份核对及矩阵只读展示同步。真实三格式、实际HTTP与工具/结果存储、独立生产API均已有实测，详见PLAN 6.30与p4-requirement-execution-validation.json。正式关联编辑页面、浏览器完整App及外部默认Agent自主执行未验收。

## 实际依据与采用做法

- requirements.py / requirement_service.py：调研时矩阵只有not_assessed；覆盖确认按具体需求版本和用例版本冻结。现已独立追加执行关联，矩阵读取匹配固定覆盖的确认；原需求或覆盖确认不改写。
- run_management.py / run_store.py：结果绑定run/item/case/version/attempt，结果payload_hash来自RunItemCompleteRequest内容。终态条目不重领，回归新建运行并引用原失败。读取时核对数据库身份、原结果hash和固定版本，不用最新用例代替。
- agent_case_assessment.py：结果actual内已有完整VerificationResult和逐项checks；phase为before/case/after，固定预期位置、实际工具观测、确定性或模型判定均保留。整条结果可能因准备/清理失败而失败；正文检查不等于整条结果，准备成功也不等于需求通过。
- postgres_requirement_coverage_store.py：项目锁、同请求恢复、明确预期父确认、追加记录和历史hash已实际使用。执行确认复用此事务惯例，存PG，不增加配置键或可变投影。

## 采用契约

执行确认明确选定需求版本、coverage_id（initial或明确UUID）、预期父执行确认及原请求编号。每项引用实际result_id、verification_id、check_index和验证点key；客户端不提供判定或替换正文。只接受该覆盖依据中明确关联的完整用例版本及正文phase=case核验。服务器读取原结果、run/item/attempt、版本和核验，核对项目、身份、内容hash与实际工具引用；确定性status由现有比较算子核对，语义保留原方法/模型/理由。无结构化正文核验的旧结果明确说明缺口，保留原读取，不伪造点级通过。

执行确认保存结果/版本hash、必要运行来源和选中核验，不复制整份运行或全部工具输出。允许明确撤销关联并保留旧确认；并发预期父冲突返回错误，未知回执按原请求恢复。历史恢复只读已保存确认，不再查询当前结果/模型/配置。归档可读和恢复原请求，禁止新确认。

矩阵指定版本、覆盖和执行确认；不带执行确认时取匹配该固定覆盖依据的最新确认，initial明确表示未关联。逐验证点汇总选中核验，展示passed/failed/partial/not_assessed及原运行总体状态，旧版本或不同覆盖的通过不迁移。execution_scope=selected_checks明确只判断所选检查；部分通过不推导整个验证点或需求通过。确认revision及预期父记录以同一需求版本的全局历史为准，矩阵按覆盖依据读取，两个范围不能混淆。环境尚无正式可信绑定，不从自由文本runtime_context推断部署，deployment仍unknown；后续环境适用性单独实现。

公开候选按当前固定覆盖版本和项目分页列出实际结果身份及正文核验摘要，完整证据沿现有结果接口读取。不调用模型、不执行工具、不修改原结果。

## 验证与边界

先验证Schema和纯函数的真实保存样本，再用MD/DOCX/PDF原件、实际MySQL/PG/RustFS/解析比较进程、真实本地业务HTTP和ToolRuntime/Agent回执形成结果；公共API确认部分验证点、失败/回归新结果、撤销/历史、跨项目/错误版本/hash/前置核验、未知提交及并发。核对真实SQL和物理HTTP，不仅编译。

API、Schema、前端类型/客户端及矩阵展示同步；独立生产API/任务进程已验，正式执行确认编辑页面、浏览器完整App、环境/规划冻结限制和其余P0—P5仍需后续实施验收。没有变更既有hash算法、测试模式或终态结果。

## 实施中纠正与真实验证

首轮真实存储专项1通过/6失败：固定计划夹具漏传现有必需skill_versions，按已用契约补manual:v1，没有放宽生产约束。扩大专项67通过/1跳过；唯一跳过为外部实际模型，受控回执不代替自主Agent。前端49项通过，vue-tsc/Vite构建通过。生产API新场景与原覆盖兼容2通过，使用独立进程和真实存储。

实际大写UUID输入首轮1失败：格式允许大写，后续字符串比较却拒绝原正文核验。根因在输入身份未规范化，现于Schema统一UUID表示；最新源码11项专项通过、独立生产API场景1通过。check原算子/路径/观测先按现有CaseExecutionCheck检查，确定性比较复用VerificationService，保留原语义方法/模型/理由。

验证关联、读取和恢复均没有模型发送；实际业务HTTP与ToolRuntime保存的结果、原失败及新回归、清理失败/正文通过分别核对。未知PG回执、并发、篡改、归档与新覆盖/新需求不继承均实测。命令与所有原失败见IMPLEMENTATION.md，新证据p4-requirement-execution-*保留；生产历史迁移未执行。
