# 用例字段引用与阶段范围查证

研究基线6f4bb0ea；完整P0—P5继续保持未完成。

## 依据来源 → 采用做法

- p4-default-model-single-attempt-first保存的真实第2响应/核验：invalid_case_citation；模型写content_json/title，但实际cases.json的title在case元数据，content_json只有objective及完整steps等。其他引用也必须逐项事实核验，不能因模型结论看似合理修正旧路径。
- case_management的TestCaseBundle将case身份/标题与version业务JSON分开。impact_batches.validate_case_citation只允许固定version的content_json/execution_notes，按StrictStr/StrictInt字面路径和规范JSON实际值核对。因此不新增元数据root，不能用版本hash替代未版本化标题依据。
- impact_text_evidence及identity_bindings已为实际已读原文生成T引用，绑定后仍走原切片核验。本任务对完整已读用例采用同样职责：模型选F引用，后端持有case/version/hash/root/path/value，不要求模型复制路径和值。
- 每个F对应实际JSON节点（包括容器），支持任意JSON键、数组、false/0/null/空字符串/空对象/空数组；不会只登记前六步或固定业务字段。原canonical协议允许引用整个expected对象，不能在新索引中丢失这类合法引用。完整JSON与执行说明仍原样提供。模型目录只增加case_ref/root/path，不重复值；服务器重新读取实际已交付完整版本取得值，核对版本hash与固定清单。
- 每次调用按已有user消息累积目录，首次实际交付顺序产生编号；重复同一固定版本复用编号，补读只追加。校验重新计算每轮目录，拒绝未知编号、跨用例编号、伪造目录或伪造完整版本；仍调用原canonical事实校验，不把字段存在当语义正确。
- 实际第3响应补读另外两条固定用例，后续109708字节/4消息超时。旧协调器已经逐条执行当前用例；新wire明确每个R对primary_ref所指当前用例作判断，其他用例仅补充依据。保留按需补读能力，不根据标题/业务词硬拦读取，不把不同固定请求当SDK重复。
- 本地Agent轨迹与协议鲁棒性课程166附近要求区分规划、行动、观察、重规划和终止，避免答案正确但轨迹失真；Agent测试专项435附近要求工具参数、上下文和阶段验收。采用实际轨迹和固定输入/输出验证，不把schema通过当业务闭环。

## 版本与范围

新增impact-call-v7/model-facing impact-response-v5；仍消费coordinator-v2固定计划并输出canonical-v2，核验版本为validator-v3。原v3/v4/v5/v6请求构造、协议快照hash、回复解析、核验版本和unknown/终态历史不改。PG读取核验按任务冻结协议选择原版本，不能用新默认版本覆盖旧检查点。

沿用现有MySQL调用/推理设置、PG任务/计划/调用/核验、RustFS对象；无新依赖、新表或复制配置。正式新任务默认捕获v7，配置和旧模型保持原值。API/前端类型增加新核验版本，所有消费方同步；阶段指引随新协议冻结，不原地修改旧v6指引或全局Skill。

## 验收

先用保存的失败原件和旧v6请求回放证明旧错误仍拒绝、旧hash不变；再覆盖完整字段、目录篡改、跨用例、续读稳定性及字节预算。创建MD/DOCX/PDF原件，通过真实MySQL/PG/RustFS/HTTP完成分组发现、完整12步用例分析和恢复，保留false/0/null/未知字段及实际F到canonical映射。独立生产API/CLI、正式App和真实默认模型全三用例分别验收；仍保留严格业务断言和历史失败，不根据通过概率缩小范围。

此文为实施前查证与设计，不表示新增功能或默认模型语义已经通过。


## 实测阶段说明与unresolved职责

首次离线8失败/22通过/25跳过9.50秒：索引最初仅登记叶子，漏掉实际响应已有合法expected对象引用。按原canonical契约补全部节点，复验30通过/25跳过5.00秒；没有删除合法引用以迁就新结构。真实专项16通过43.61秒，生产五项288.04秒，正式App一项88.67秒，前端24通过及构建8.43秒。

实际默认v7新任务首次严格断言仍失败207.39秒，Handler200.015秒，4逻辑/4物理，无补读或SDK重复，四条完整响应均接受；八证据/三用例11项analyzed，三规则、九用例判断符合固定样本。旧错误title引用不再出现。但discovery和case的unresolved包含部署未知、删除仅是停用候选、未变鉴权/其他用例后续分析等说明，导致严格“没有未解决项”断言失败。difference_warning:extracted_text_positions仍在系统依据警告中，快照partial；不抹去警告或据此宣称实际部署/测试通过。

查证旧BoundDecision.unresolved没有字段语义描述，新v7指引仍只说Report missing evidence explicitly；Skill要求记录未知部署、评审和未处理范围，未给出这些信息在分阶段输出中的位置。采用新任务冻结的字段描述及阶段指引：unresolved仅表示阻止当前阶段判断的具体证据缺口，并说明受影响规则/成员；部署/评审状态继续unknown/pending_review，相关说明保留summary/reason/候选uncertainties。其他阶段的任务不作为当前任务未完成证据。真正缺失的来源/依据仍明确报告、evidence_insufficient或uncertain，不自动删除或分类模型字符串。

这属于阶段职责契约的澄清，不改变wire字段或canonical事实校验；已保存v7的原Schema/指引/hash仍从seed恢复，后续新提交冻结新的描述/hash。先前11模块在描述修改前完成，再实际运行受影响v7专项、生产入口及新的默认任务；原首次失败和所有unresolved原文保留。九项样本判断核对另存p4-case-anchors-semantic-first.json，不作为用户业务评审批准。


最终描述澄清后的v7专项18通过44.19秒，独立生产MD一项58.86秒。新默认任务严格断言1通过329.88秒，Handler322.734秒，4逻辑/4物理；11项analyzed且无unresolved，九判断独立核对与固定样本一致。将业务基线加入未来默认集成测试后，实际保存结果回放1通过4.68秒，0新HTTP；未重复外发未变业务请求。源模型/全局超时不变，部署unknown、pending_review、原文位置警告和snapshot.partial保留，完整目标仍未完成。详细范围/阶段关系及命令见PLAN.md 6.12和IMPLEMENTATION.md。
