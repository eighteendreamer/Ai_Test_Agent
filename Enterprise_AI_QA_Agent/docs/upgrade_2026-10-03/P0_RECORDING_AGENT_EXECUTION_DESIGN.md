# 录制知识进入正式 Agent 执行：调研与实际验收

调研日期2026-10-08，基线d9702edd。完整P0—P5目标保持，本步验证知识使用与真实操作，不能以知识查询或任务就绪替代执行。

## 实际依据 → 采用做法

| 查证位置 | 事实与采用做法 |
|---|---|
| application/test_cases/planning_sources.py，catalog/query | 当前项目录制目录、完成状态和图成功检查；query(kind=recording)取得完整脱敏事件、响应和固化诊断并生成证据hash。用默认规划模型主动查询和引用该证据，不在测试中手工塞入最终计划 |
| application/test_runs/agent_case_context.py | 正式目标项按evidence_ids读取冻结证据；录制payload保留完整，正式用例JSON及自身说明单列。验证实际模型请求收到录制及完整用例，模板不进入执行 |
| application/test_runs/agent_case_execution.py、agent_case_assessment.py | 现有正式Agent/session/graph/ToolJob/审批/只读回执核验，不把用例编译为UI模式参数。创建正式运行并走生产Worker、审批和结果接口 |
| modes/ui_automation_mode/contracts.py、runtime.py | test_execution尚不可用，知识命中只返回partial/task_generation_ready。保持模式，先验证已有正式Agent与browser-control可执行路径，再根据真实失败决定是否改接入职责 |
| registry/tools.py browser-control、application/context/mcp_runtime_service.py | 已有run_actions、command等原生Python Playwright执行及截图/最终snapshot/HTML/命令stdout/stderr。使用实际浏览器，不能以伪造ToolJob结果验收 |
| tests/test_execution_worker_cli.py | 已有隔离MySQL配置表、空PG库、真实RustFS、Redis和生产API/Worker进程夹具。复用，不向全局模型配置或生产业务写入测试数据 |
| tests/test_recording_scope_product.py、api/routes/recordings.py | 已有真实Chromium/CDP→录制API→PG/图/原件流程。本步真实点击与DOM响应、截图上传后结束并查询知识，页面和模型发送原文保存 |
| 项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html，01轨迹评测 | 同时检查plan/action/observation/replan/termination；最终答案不能代替合法工具轨迹和实际业务验证。核对物理HTTP、正式状态与页面真实效果，不按固定工具数量要求模型 |

## 实验设计

使用受控动态页面：点击“展开详情”后服务端生成本次唯一确认串，浏览器追加“已展开”及确认串。正式用例只写业务操作与预期，不把录制选择器预先复制到用例。先通过实际录制完成该操作、保存原事件/知识/截图；再让当前默认模型在正式项目内查询录制、形成固定计划并执行。服务器分别记录录制与执行阶段真实调用，最终HTML/snapshot及回执需要体现真实状态。

当前默认模型由MySQL只读取得；复制到夹具隔离的MySQL模型表。保持model_id、传输、能力、输出/上下文参数和现有SDK/Agent时限，仅将base_url指向本地转发观察器，再透明转发原provider。观察器保存请求body、真实响应body和时间/状态，不保存Authorization或其他header，不生成模型回复，不替模型补计划。它只能证明观察到的HTTP发送，不能证明上游内部没有重试。

生产API启动后建立正式项目、录制和启用用例，正常规划/运行入口与独立Worker；工具按现有审批流程继续。待审批可以由本地测试决定批准已授权的本页操作，任何其他目标或工具均拒绝测试通过。确认结果和真实动作后重放原终态任务，要求没有新增页面副作用或模型发送。

所有失败、未知响应和原始输出保留。首次失败先沿实际输入、模型响应、Agent轨迹和原生工具定位根因；不放宽断言、不改全局预算、不重复原样失败模型流程。当前页面测试不替代人工DeepSeek、完整Vue App、清洗遗漏、场景/别名/报告、其他模式、Docker及生产历史迁移。

## 初步实验与夹具修正

first原件：1失败28.31秒，生产启动和录制ready/start已完成，但测试误从launch连接读另一CDP客户端创建的context/page，IndexError；零模型调用。按现有CDP连接实现，操作端重新连接真实endpoint读取页面，不改生产驱动。

connected原件：真实点击/有限响应、PG/图、RustFS截图及当前默认模型两次调用已完成；模型主动query(recording)并finish引用完整知识。测试误取CasePlanRecord顶层items，实际应为decision.items，1失败88.48秒，尚未创建正式运行。对保存的真实plan按现有生产Schema离线验证及完整图相等检查通过，不改变模型决定。

connected观察器把SSE全部读完后才发送，虽然两次已返回200，这种观察方式会改变SDK读超时，不作为后续正式流式执行验收。已按[HTTPX官方流式响应文档](https://www.python-httpx.org/quickstart/#streaming-responses)确认stream/iter_raw，后续按收到的原始chunk立即转发，保留Content-Encoding并记录原字节；不调高SDK/Agent预算。保留connected原件及观察局限。最终验收不限定模型必须采用run_actions或特定HTML文件名，依据真实动作、唯一确认串、正式结果及属于本次运行的工具证据核对。

streamed原件：1失败110.29秒，实际默认两轮规划、正式运行、独立Worker与冻结上下文/审批已保存，四次真实模型HTTP返回200。测试自设工具白名单漏掉现有ui-automation-runner，拒绝其status审批；这不证明运行时执行失败。三次夹具失败后重审测试路线：不再自设三种浏览器工具名单，改由原UI Agent注册表确认支持工具，并核对审批的正式项目/运行/条目及目标来源。现有模式Prompt要求先调用runner，不能为了让测试通过删去这个合法流程。真实探索可能也触发页面动作，验收检查真实执行和独立确认串，不要求固定工具数量；额外动作如实保留。终态重投仍要求原实际动作数和模型发送数不增加。

新增独立TCP观察器验证1通过7.54秒：上游发送第一原始SSE块后等待，客户端在上游结束之前已收到它，释放后全部原字节一致、单次实际发送。不是默认模型验收；原件p0-recording-agent-observer.xml/.log。

## 已确认的产品缺口与采用设计

registered原件1失败77.80秒：默认模型一次真实HTTP就finish，reason把用例source_refs中的录制ID当作“已引用”，实际evidence只有完整用例，没有录制正文。不是接口/审批夹具错误；本次没有启动正式执行。规划服务_validate只检查已提交evidence_ids存在，不能知道用户另外明确要求使用的来源遗漏了。把自由文本再加粗不能建立可验证契约。

采用显式可选required_sources输入，每项为kind=document/recording与source_id；它表达本次规划必须实际读取并让目标项引用的来源，不把所有历史source_refs强制变成必读，也不扫描用户文本关键词。旧请求不带此字段继续原行为、原JSON/hash不变。新要求通过现有只读query取得当前项目证据；finish前共用_validate核对目标项真实evidence身份，未读/未引用反馈回原规划循环，仍受原预算和时限。无目标的no_match不创建执行，不把“找不到用例”伪造成已执行。

来源要求进入PG请求及seed；专用指导全文在seed冻结并有required-sources-v1版本后缀，恢复不读当前指导。同步直接/持久规划、阶段版本检查、TS请求类型及正式生产试验。配置仍沿用MySQL策略；请求、检查点和结果为PG；录制/工件存储沿原路径，没有新表/依赖/执行器。这里只保证明确选定来源的读取/引用和冻结，不把模型语义正确、录制完整性、文档全范围覆盖或所有历史来源读完视为已证明。

回归采用registered实际失败候选的结构及reason，再替换fixture身份重放：先拒绝仅用例ID，查询真实文档后仍拒绝遗漏引用，加入真实来源引用才发布。另检查请求旧序列化、不同来源请求hash、跨项目读取和持久seed/检查点。完成这些验证后再使用新显式输入执行真实默认UI链路，历史失败保留。

## 明确来源契约和恢复实测

required-first：5通过9.70秒，含实际文件/应用路由和MySQL/PG/RustFS一项；observer验证原始SSE及时转发。boundary首轮8通过1失败8.00秒，跨项目来源确实被拒，测试却把实际planning_failed写成failed；修正后9通过1失败7.33秒，保存审批夹具错误假定assess必有target_url。使用真实recording.entry_url后10通过6.92秒。

required-regression最终11通过7.20秒，包含实际PG保存来源检查点后丢回执、两个新store/Worker handler恢复（直接与阶段协议）、冻结指导不被当前常量替换、原超时/预算、终态重投无调用。旧请求/hash、旧seed、未读/读后未引用、跨项目、no_match分别检查。prepared regression固定计划已经完成，不需再次生成Prompt；它只校验冻结的来源引用。直接和持久重新规划继承原required_sources，依赖项被提升为回归目标且缺引用时走已有重新规划。父运行/失败选择的完整集成仍由既有regression测试核对，不把该单测中的合成parent/result身份冒充真实失败运行。

前端只增加请求类型，projects.api.ts原JSON透传支持，未新增选择来源的App表单。原build日志以“built in10.20s”结束；对应历史进程96356的handle已经消失，不能补称观察到其退出码。本轮后续构建以新句柄/日志单列。

## 默认模型正式运行的实际边界

required-production：1失败106.90秒。当前默认模型两轮主动读取并引用录制，full graph与公开图一致；正式运行/独立Worker待审批。夹具误把AgentRegistry.get返回的AgentDescriptor再取descriptor；已用真实PG审批记录离线复核supported_tools和所属项目/运行/条目，而不是删掉runner或绕过审批。

owned-production：1失败154.81秒。真实默认模型7个物理HTTP均200，前两轮规划读录制并冻结；执行依次请求带URL的runner assess、无URL的assess、browser-control与browser-automation，再写blocked报告和最终回执。只有实际录制时的一次POST /activate，执行阶段没有页面副作用。工具目标http://127.0.0.1:<本次端口>被原ExecutionSafetyPolicy拒绝（untrusted_private_target）；原模型明确报告未执行，不能称AI假报passed。最终回执引用browser-control#1等不存在的call_id以及带output前缀的错误路径；服务器严格拒绝，正式结果error。前述只是失败轨迹证据，不证明UI执行可用。

沿代码确认：ExecutionSafetyPolicy._trusted_target_hosts依赖可信resource_scope或已核验授权的intent；InputOrchestrator取服务端session.metadata.resource_scope。正式AgentAdapter只在run.session_id存在时继承父session的服务端授权字段，本次独立运行没有父会话。项目base_url/录制里的页面地址提供业务导航，不等于可信授权。需继续研究“无父会话的正式运行如何绑定授权目标”和“不执行/denied的回执如何引用真实证据”；不能关闭安全策略、把录制文本升级为授权，或删除回执身份校验来通过测试。本轮保留失败并完成来源契约修复，不重试不变的默认模型流程。

## 扩大回归与原件保持

planning-regression初始命令误写不存在的test_recording_planning_knowledge.py，零测试；configured实际124通过、2失败、7跳过75.40秒，两失败仅为漏传QA_TOOL_NAVIGATION_DIR，七跳过为未开默认模型专项及解析器专项。contract-regression开启真实解析器/回归/运行后63通过2失败55.48秒：旧hash断言未去掉历史environment空字段；真实回归Worker流程已运行，但夹具未释放其新增parse元数据表，文档版本清理被FK拒绝。

修正测试的旧结构比较和原进程所建表的所有权登记/子表释放；不改业务hash或FK。先保存本轮生成的Worker汇总，恢复被硬编码旧路径覆盖的历史汇总原字节；新证据目录由已有QA_PLANNING_TASK_EVIDENCE_DIR或tmp控制，后续不写旧证据。清理只使用本次失败日志对应的UUID表名，查询外键后按所属顺序释放，没有CASCADE。首个手动清理遗漏同属原夹具的assets/submissions，整个事务回滚；补齐原history_tables后10项精确所属PG/MySQL清理已核验，记录owned-cleanup.json。扩大回归最终结果见新XML/log，失败原件均保留。
