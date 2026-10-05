# 确认需求范围的增量生成研究

研究基线e88add3a。完整目标是持久模块任务、冻结依据、可恢复调用、原子草稿提交、正式产品入口；本阶段先实现其共用冻结生成和确认范围读取内核，不把内核验证当作完整任务/草稿交付。

## 实际代码 → 设计结论

- generation_pipeline.py的ProjectTestCaseContextProvider重新读当前文档预览、知识图谱/历史，并有字符截断；ModelTestCaseGenerator每次读当前Skill和模板，返回后按max_cases截取。case_service.generate调用create_many，只能新建。不能直接拿这条当前资料链当作固定需求的增量恢复。
- requirement_service/PostgresRequirementStore已经有稳定需求/版本/点、明确接受、原影响rule/用例判断/响应检查点；RequirementVersion.review_id可回到当次决定。但没有按review_id读固定决定的接口，需要添加项目/文档隔离的内部读取。当前要求以指定不可变版本为依据，不用current_version_id代替。
- impact_task_service.read_input核验RustFS字节/SHA、输入内容hash和冻结seed，返回完整旧/新解析、差异、固定case JSON和当时来源。增量读取复用这条固定输入，不再调用项目预览collector。
- SkillRuntimeService.capture_prompt_blocks验证完整Skill正文/hash；model_invocation_parameters有非敏感白名单；ModelRuntimeService.invoke支持expected_parameters/expected_runtime_parameters和可选transport_options。采用这些已有接口，原模板仍MySQL；业务版本仍PG；冻结大请求由后续任务存RustFS。禁止保存凭据或头部原值。
- 既有持久规划/影响工作流先存调用意图再发送、保存响应后只读恢复；导入复用case_store.insert_drafts_in_transaction。后续增量任务同样需要模块独立检查点、调用未知状态、同事务草稿/模块收据；不能用API内同步调用或循环create_version拼成假事务。
- 本地《测试数据合成与用例自动生成》来源/质量治理、《评测工程化与CICD》版本/变更与回归原则 → 记录真实模型/Prompt/Skill/模板及固定需求/案例/原文；不照搬课程阈值。

## 冻结内核

抽取原生成器的请求构造，旧generate行为保留；新增显式冻结接口，保存完整authoring request、生成模板、Skill正文/sha、Prompt版本、实际模型非敏感参数和SDK/runtime参数。恢复使用保存请求，不读取当前模板/Skill；实际发送仍核对模型/runtime参数，变化时明确拒绝，不用新配置续写旧任务。统一结果解析，新冻结路径拒绝截断/工具调用/失败/超出max_cases，不静默删掉额外候选。

确认范围读取要求明确需求ID和版本ID。读取原决定，核验原请求hash、所产需求版本及原影响来源；完整旧/新/差异及当时编写来源由保存input读取。每个更新模块只针对一个固定case版本，保持其原case_id/case_key/mode；新建模块明确新用例模式。一个case选择多个基线时需明确消除歧义；移除只形成停用候选，不调用生成模型、不自动停用。

所有模块在同次捕获中复用同一模板和模型/Skill快照。来源不足/partial保持原声明，不能因需求接受而消除；不截断旧JSON或未知字段。生成指令只覆盖确认版本及验证点，不扩大为全项目测试。

本阶段不自动提交任何草稿，不接后台/公开API或页面；后续任务需要固定模块输入对象、PG模块/调用/响应/提交收据、MySQL策略、Outbox/独立Worker，以及正式UI，才可声称增量生成产品可用。旧启用版本、历史结果及原失败不修改。

## 必须实际验证

真实MD/DOCX/PDF → 上传/解析/差异/受控HTTP影响 → PG明确需求评审 → 原对象完整读取 → 冻结模块。将完整模块写入真实RustFS并按hash读取，创建新服务恢复；改变模板/Skill/当前文档/需求版本后，已存请求不变。新模型参数/SDK变化拒绝发送。

实际HTTP模型收到完整固定用例超过6步、False/0/null/未知字段、旧/新与确认验证点，产生更新/新建JSON候选；核验请求正文、版本与Skill/模板来源。另验移除不发HTTP、外项目/版本/重复case基线、损坏对象/hash、空/截断/越界输出和副作用工具拒绝。受控模型不当作默认模型业务语义通过，内核候选不当作已提交草稿。跑旧生成/模板及需求服务回归，保留原失败证据。

## 已实施与实际验证

新增case_generation.py、incremental_generation_sources.py，抽出generation_pipeline共用请求构造与capture_authoring/prepare_frozen/invoke_frozen/parse_frozen。新增项目/文档下按review_id读取与请求hash检查，RequirementService.read_generation_basis核验指定版本、原接受决定及原影响来源。invoke_frozen返回整个实际ModelInvocationResult，调用方需先持久化再解析，不能直接返回batch导致原失败丢失。

实际旧生成/模板首轮7通过/3跳过7.89秒；新测试首轮5失败/4通过58.63秒，修正测试DTO父版本/实际模型字段/用例启用记录；第二轮3失败/6通过59.54秒，修正误读create_version返回类型。首轮日志/XML保留，不删失败。

四模块真实回归31通过/1跳过152.89秒：13新范围、9需求、10旧生成/模板；跳过外部当前默认模型场景，本轮没有重复调用。补LangChain和真实存储故障后新模块最终15通过/0跳过107.77秒。11场景实际MD/DOCX/PDF/解析比较进程/MySQL/PG/RustFS/HTTP，原生三格式和MD LangChain完整12步JSON/False/0/null/未知字段及原来源均进入固定请求。模板/Skill/文档/用例/需求后续变化不换已存依据；参数/runtime变化在HTTP前拒绝。四种实际失败响应先写RustFS再校验，每种1次物理POST。实际PG请求和来源损坏/实际RustFS原输入损坏被拒绝并恢复原字节；不是mock读取结果。移除不读authoring、不生成、不停用。

独立生产API/三类CLI/真实存储及HTTP需求评审/API重启/关闭派发恢复1通过102.20秒；原影响两次模型POST。该场景只验证现有生产链兼容，不调用新增范围/增量任务。完整新范围的RustFS保存和新生成器重读是基础测试，生产并没有模块任务或提交收据。受控候选字段完整，不证明默认模型内容一定正确或已通过用户评审。

复现命令、失败/跳过和证据见IMPLEMENTATION末节、PLAN 3.6.3/6.15和p4-incremental-sources-*。无新依赖/配置键/表/列/公开契约或前端变更；未做生产历史迁移，未重跑浏览器。后续任务还需实际输入容量/预算、模块领取/调用unknown/原响应只读恢复、同事务草稿提交及收据、Worker/API/UI；不得把本内核当成完整P4交付。
