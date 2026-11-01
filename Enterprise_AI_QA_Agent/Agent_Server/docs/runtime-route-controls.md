# Deep Agents 新回合路由与存量执行控制

本说明对应 v4 §14.2 和实施记录 §106，描述当前实现，不是生产灰度或 E4 回滚演练的验收证明。

## 只停止新接入

在已经具备兼容运行能力的服务配置中设置：

```dotenv
DEEP_AGENTS__NEW_TURNS_ENABLED=false
```

它只关闭 `RuntimeService` 对**无绑定新请求**的 Deep Agents 选路，使其沿原 legacy 路径执行。默认值为 `true`，因此不改变已有选路行为；运行能力总开关 `DEEP_AGENTS__ENABLED` 仍默认 `false`，不会因为新增配置就启用生产流量。

已有 `runtime_binding` 先按原身份与运行能力校验，再沿原框架路径执行，不受此开关影响。已经进入框架的调用不会被取消，已有待审批回合也不会被切成 legacy 重跑。请求正文或 context 中同名字段不能覆盖服务端配置。

当前正式入口尚未全面开放新 v2 绑定生产。此开关不是底层 `submit_v2` 的全局禁投开关：预先建立绑定的任务仍属绑定执行路径。未来新绑定生产者必须在创建绑定前接入相应选路控制，不能声称本开关已经覆盖所有未来入口。

## 三种职责不能混用

| 控制 | 影响 | 不代表什么 |
| --- | --- | --- |
| `deep_agents.new_turns_enabled` | 无绑定新回合是否可进入 Deep Agents；仍受模式白名单和总能力约束 | 不撤销绑定、不停止在途工具、不清空审批或任务 |
| `deep_agents.enabled` 和 `checkpoint_enabled` | 兼容 Worker 的运行能力、checkpoint 装配及 v2 claim 前准入 | 不是“只停止新接入”；关闭后可能使存量任务无法继续 |
| 既有显式中断入口 | 按原 Session/Task 停止、owner 撤销与未知效果契约处理 | 不等于远端领域 Runner 已停止，也不由本开关触发 |

| `enabled` | `new_turns_enabled` | 无绑定新回合 | 已有 Deep Agents 绑定 |
| --- | --- | --- | --- |
| false | 任意 | legacy | 运行能力不足；不能静默 fallback |
| true | false | legacy | 兼容能力及原 owner/审批校验通过后继续原路径 |
| true | true | 命中模式白名单且 adapter 可用才进入框架 | 兼容能力及原 owner/审批校验通过后继续原路径 |

兼容 v2 Worker 仍要求 `enabled && checkpoint_enabled`，并保留 runtime/version、依赖锁、operation 和持久格式检查。停止新接入时，**不要同时移除排空 Worker 已经具备的这些能力**。身份漂移、不支持的 runtime 或缺失 adapter 仍明确拒绝，不转换为 legacy 文本重放。

## 配置生效与部署边界

- Settings 由现有缓存加载，Runtime 在 lifespan 装配时捕获配置；没有新增热更新 API。修改文件不会自动改变已经运行的对象。测试中修改私有属性只模拟选路变化，不是运维接口。
- 依照既有部署流程让新配置进入实际消费者，同时保留承接存量回合的兼容制品。旧制品可能忽略新字段；只有兼容读者已覆盖对应新流量入口，才能依赖本控制。
- 清点待审批、排队、在途、恢复及未知效果绑定后，才能判断旧 Worker 是否可排空；不要删除记录、重置终态、改写 checkpoint 或盲目重发外部操作来“排空”。
- 本变更没有 DDL、依赖升级、绑定格式或公开 API 变更；未开启生产路由，未提供远端 Runner 停止协议。双制品、备份恢复及真实部署回滚仍待 E4 验收。

## 日志与验证落点

### 可选制品安装预检（v4 §14.1）

`DEEP_AGENTS__RELEASE_STORE` 指向可信本机制品仓库，`DEEP_AGENTS__RELEASE_REFERENCE` 指向该仓库导出保存的引用 JSON。两项同时配置或同时留空，推荐使用绝对路径；不是用户请求字段，不接收客户端 context 覆盖。配置路径的实例文件和制品仍不应提交 Git。

配置后，真实应用 lifespan 在 SQL 池归属、Redis、业务 store、reaper 等资源初始化前，复用 `FrameworkWorkerCapabilities.from_release()` 核对完整引用、安装源码、当前环境和定义；失败只记录错误类型并拒绝启动，不回退到未绑定制品。已导入的本机源码必须本来可信：这是服务启动预检，不是阻止 Python import、热修改、供应链或文件权限攻击的沙箱。

旧程序可能静默忽略新配置字段，必须核实实际安装版本和预检日志，不能把设置了环境变量当作所有初始化读者已升级的证明。

校验成功的能力对象传给原 AgentTaskDispatcher，不修改任何历史绑定，也不创建新绑定。`enabled && checkpoint_enabled` 继续决定运行能力，`new_turns_enabled` 仍独立决定新回合选路，路径配置不自动开启任何开关。留空时应用保持原可选能力为空，不额外要求 legacy 应用安装源码仓库／根锁；v2 CLI 保留原无引用 `from_artifact()` 能力，不把根锁摘要当成实物归档。

v2 CLI 未传显式 `--release-store`／`--release-reference` 时也消费同一配置预检。原成对 CLI 参数仍可选择 Worker 的消费身份；应用 lifespan 仍校验其进程配置的引用，不能靠参数绕过启动校验。两者同时使用时应选择同一引用，且所有声明均必须与安装源码一致。配置只在启动捕获，不提供热更新。

日志 `framework_release_preflight_verified` 带有限标量 `framework_release_revision`／`framework_release_sha256`；未配置为 `framework_release_preflight_unconfigured`，不声称已验证归档；失败为 `framework_release_preflight_failed`／error_type，不渲染路径、原 JSON、异常正文或凭据。

新源码制品的 `release/formats.json` 增加 `message_stage_materials`：复用运行时同一 codec 目录，保存 `source_manifest` 中现有 12 类严格材料的 JSON schema（包括原结果资产、图输入 v1/v2、逐审批图输入映射、投影回执及派生 Memory 材料）。每份 schema 保留自身嵌套定义；安装预检继续比较完整定义，不能仅凭 ZIP 摘要有效就接受缺失／漂移的材料清单。schema 是结构清单，不代替 Python 校验器、业务身份核对或执行权限，也不代表任意自由 metadata 都已版本化。

旧源码未导出这份目录时，归档探针保持旧清单原样，不补造空目录或回写旧制品；旧清单缺失不等于旧读者支持了新格式。现有普通读取、JSON 模式读取和 claim 前格式分类仍走原 reader，无新写入格式／版本上界变化。

**发布限制不变**：所有旧初始化读者升级／隔离前，不得启用引用写者。应用默认不创建消息材料 loader，API 普通消息受理仍走原路径；现在 main 可消费下述显式服务端策略，但本轮没有配置或启用生产实例。源码归档验收复用当前 venv，不等于双制品独立依赖安装、完整后端部署或 E4。

- `tests/test_framework_release_startup.py`：配置、原安装校验 factory、实际 lifespan 的资源前顺序、CLI 配置分支、日志有限输出，以及恢复当前源码快照的新进程预检。新进程的 SQL／业务初始化明确替换为空端口，不是全后端 lifespan 验收。
- `tests/test_framework_release_binding.py`／`test_framework_release_workers.py`：安装源码一致性、历史读者／真实 PG 与 Redis 的既有消费契约；具体结果和限制见实施记录。

### 持久绑定的兼容读取

v2 Worker 未装配能力对象时，在任何持久任务 lookup 前以 `framework_worker_capabilities_unavailable` 保留原投递，不尝试用默认严格读者解码新绑定。一次投递只捕获一份能力对象，lookup、格式核对与 claim 复用它；安装替换能力仅影响后续投递，不是运维紧急停止或撤销已授予 owner 的入口。命令格式解析仍先使用原 codec，legacy 投递不受此能力前置影响。

v2 Dispatcher 的任务读取与 claim 锁内的 task／owner 绑定均使用原 `RuntimeBinding` codec。只有明确注入 Worker 能力的读取才把严格新增字段、未知 runtime kind／model transport 或引用版本分类为 `framework_binding_format_unavailable`，沿原 TaskDeferred 保留 Redis 原投递，不增加 retry／dead letter 或领取副作用。claim 仍按 Session → owner → task 原顺序加锁，并在等待 Session 锁后重新读取；不能靠锁前校验代替事务内核对。

已知约束、摘要／fingerprint、合法格式下的身份不一致仍是原错误，不转为兼容等待；读能力不授予执行或恢复权限。普通 task API／未注入能力的底层调用、其他 owner 写权限端口和初始化读者保留原契约，不能据此认定所有旧初始化读者已升级。固定诊断事件为锁前 `agent_v2_binding_reader_unavailable` 和锁内 `framework_binding_reader_unavailable`，保留任务／Worker 标识，不输出原绑定或异常正文。真实 PG／Redis、失租／终态和锁等待反例见 `tests/test_framework_command_reader_admission.py`。

### 服务端内部的新绑定准备（v4 B／§4、F／§14.1）

`src/runtime/framework_submission_preparation.py::prepare_release_submission()` 接收显式的稳定绑定／Session／Turn 标识、checkpoint namespace、根模型 key、模型配置集合、限额及带时区时间。它先捕获配置，再用原安装 verifier 校验**本执行模块所在源码**与明确给定的真实归档引用，返回 `binding` 和 `model_budget`，交给原 `PostgresAgentTaskStore.submit_v2()` 同连接事务受理；自身不写数据库／队列、不领取或执行、不发模型请求。它不是公共 API，不根据环境或历史记录推断制品、模型或预算，也不检查／代替上层新接入路由授权。

- 新绑定使用现有完整上下文 runtime v2.3.0／state v2、`thread_id=turn_id`；Prompt／Skill 的 `release:<archive_sha256>` 标识已校验、可读取的**归档源码束**，工具 `sha256:<digest>` 对应真实 `release/tools.json`，依赖锁对应真实 `uv.lock`。不是任意版本字符串，也不是完整动态有效 Prompt／Skill、Memory 或检索资产已在 API 受理时冻结；这些仍须沿原 graph-input 唯一装配资产链补齐。
- 新预算格式 v2 的 `model_configurations` 保存与原 `model_invocation_parameters()` 白名单投影一致的语义参数原件，逐 key／fingerprint 校验，含明确 request profile／映射版本／摘要（有 profile 时）。不保存认证字段、endpoint 或 header 原值；endpoint／header 哈希只能核对，**不能还原连接或凭据**。执行仍需从既有配置端口解析当前授权并核对原 fingerprint，不能用快照授予永久调用权。
- 原预算 v1 仍为默认读写格式，省略新增字段，历史 wire 和摘要不自动改变或回填；v2 必须保存完整映射。原受理事务继续一次性保存 binding／owner、预算、task、原消息、stage／outbox，任何失败回滚，幂等请求不能替换已冻结配置或限额。无生产 DDL 或数据迁移。
- 当前能力追加 `model_budget_max_version=2`；call 上界仍 3，upload 仍 1。上界为 1 的预算消费者遇到 v2 沿原 TaskDeferred 保留 Redis 原 PEL，不增加 retry／dead letter／attempt；兼容读者可接续原 message_id。未知嵌套严格字段仍分类为格式不可用，已知摘要／身份／约束错误不伪装成兼容等待。实际旧源码的离线 codec 与实库读能力模拟分别验证，不混称旧源码 Worker 已完成独立部署。
- 失败日志为 `framework_submission_preparation_failed`，只记录固定阶段 `status=models/release/binding` 与 `error_type`，不记录配置／路径／原异常正文；畸形配置的序列化诊断转为受控错误，不向 warnings 输出输入。成功为 `framework_submission_preparation_verified`，仅附既有制品 revision／sha256 标量。

这一步不自动配置新写者或打开生产路由；普通 API 新消息仍走原 legacy 受理。所有初始化读者升级／隔离、完整有效输入冻结、发布与真实部署验收仍是上线前提。运行进程仍依赖可信且生命周期不可变的源码／依赖，不是热更新、ACL 或安全沙箱。

验证落点：`test_framework_model_admission.py`（v1/v2、原子回滚／幂等、原结果材料及官方模型＋本地 HTTP）、`test_framework_model_reader_admission.py`（实库格式／身份错误分类、原 PEL 接续）、`test_framework_release_binding.py`（真实源码归档、内部准备、历史读者与恢复源码新进程）。临时源码仓库只用自身 `.gitattributes` 保留实际锁文件原始字节，不改用户 Git 配置；同一 venv 的恢复源码进程不代表独立依赖安装或完整 E4。

### 显式接入原消息受理事务（v4 §4.1／§14.1）

`AgentTaskDispatcher` 可由受信服务端装配 `framework_submission_preparer(session, execution_request, command)`；该同步端口默认 `None`，main／CLI 不安装同步回调。它返回上面的 `PreparedFrameworkSubmission`，或明确返回 `None` 保留该模式的 legacy 路径；异常不回退。客户端 metadata／context 不得选择、替换或安装端口。`new_turns_enabled=false` 时不调用准备端口，新意图仍走 legacy；重试则继续返回首次已受理任务，不改其已有绑定。正式异步组合与服务端配置见下文。

原 `PostgresAgentTaskStore.submit_prepared_message()` 在既有 Session 行锁内先查同一消息幂等身份，再为首次请求调用准备端口，复用原同连接事务保存绑定／owner、模型预算、消息、stage、任务、事件和 outbox。准备端口必须同步、只读、无执行副作用；可校验已捕获的配置和本机制品，不能在持锁期间调用模型、图、工具、队列、额外 DB／网络或异步服务。此处不提供跨系统事务或撤销外部副作用的保证，也未验收生产并发性能。

- **请求幂等与执行证明分开**：API 重试按原存储 payload 核对请求意图，沿原未加前缀的消息 key 与 legacy 共用首次受理身份；不使用新配置、随机 Turn 或新时间重新决定绑定。任务 `input_hash` 仍复用原 `_v2_input_hash(payload, binding, budget)`，stage／恢复仍逐项核验同一绑定与预算，不新增宽松执行摘要。直接 `submit_v2()` 及其他操作继续使用各自原严格摘要／幂等规则，无 DDL、格式升级或历史回填。
- 并发、提交成功但响应丢失、后续关闭／移除／新增准备端口均不能生成第二个 Turn、替换模型快照或刷新预算 deadline；相同 key 不同 payload 仍 409。原准备失败、身份不符、预算／阶段／事件写入失败均回滚，允许同一请求重新受理，没有自动重跑已执行命令。
- 首次新绑定须由显式的同制品 Worker 能力和原预算 reader 准入；能力缺失／制品不符／旧预算 reader 不可作为写入许可。绑定与预算经原 codec 重验，不能用 `model_copy()` 绕过。该本机检查不证明所有其他旧初始化读者已升级／隔离，仍不能据此开放生产写者。
- 后续编排、上下文装配、graph-input、模型账本、原生终态及应用投影全部沿原 Worker 链；未在 Dispatcher 新造图、finalizer 或恢复分支。消息提交的日志／metric 按实际返回任务的绑定选择流，不能用本次候选 command 把复用的 v2 任务记为 legacy。
- 完整上下文 Prompt 只投影分类得到的路由参数，不把 `mode_intent.parameters.objective` 中的原请求副本再次提升到 system；原分类原件及其其他参数仍在冻结资产中保留，当前用户正文继续只由原生消息身份提供。legacy Prompt 不变，已冻结图输入的恢复不重新渲染。

日志 `agent_message_admission_reused`／`agent_message_admission_committed` 记录有限任务／绑定身份和 schema；`agent_message_preparation_failed`／`agent_message_prepared_values_invalid` 只记错误类型，不输出输入或原异常；关闭新意图为 `agent_message_new_turn_admission_disabled`。验证见 `test_framework_release_binding.py` 的真实 HTTP／PG／Redis、事务回滚、丢响应重试及官方框架＋本地 HTTP 完整上下文用例；证据等级与实际结果见实施记录 §173。普通 headless／Coordinator／审批等入口不因该端口自动迁移，发布门、独立部署与真实领域验收仍保留。

### 在受理事务外读取当前装配材料（v4 §4／§6）

需要查询当前模型配置与制品时，受信组合可改装配异步 `framework_submission_loader(session, execution_request, command)`，与同步 preparer 互斥，默认策略为空时返回 None。`FrameworkReleaseSubmissionLoader` 在工作线程中复用真实 Agent／Model Registry 的选模规则和 `get_runtime_config()`，再调用原 `prepare_release_submission()`；每次新意图读取当前材料，不使用启动缓存。模式集合、namespace、调用／Token 上限和截止时长必须由可信组合提供，没有自行推导的生产限额。未命中模式返回 None，不读取模型或制品。

Dispatcher 先无锁只读查询原 API 消息回执；已有相同请求直接返回，冲突仍 409，不再依赖当前材料源。未命中才读取材料，且不持有 Session 行锁、不写受理事实、不执行模型或工具。材料读取失败后只再查询一次并发提交回执；仍不存在则明确失败，不静默回退 legacy。候选若与原 payload 的显式模型 key 不同，受理前拒绝，避免先返回 202 再被 Worker 的冻结根模型规则拒绝；继承的 Session 偏好不冒充显式输入，仍允许 Registry 原有选择策略。

读取成功不授予受理权：原 `submit_prepared_message()` 在 Session 行锁内重新核对幂等、当前新接入开关、候选能力及全部既有事务约束，只提交胜出的绑定／预算／任务／消息／outbox。并发候选不能替换原材料；关闭开关可丢弃未提交候选，但不撤销已提交绑定。读取期间取消后，即使只读工作线程随后结束，也不产生受理事实。该两阶段边界不是 MySQL／PG 分布式事务，配置在采集期间变化仍以最终捕获并入库的参数原件为准，执行端继续核对当前授权与原指纹。

新增成功／回执复用日志为 `framework_submission_materials_loaded`／`agent_message_loaded_admission_reused`；失败 `agent_message_material_loading_failed` 只附任务身份和错误类型，不输出配置、请求或源异常正文。`test_framework_submission_loading.py` 验证真实 PG 行锁、并发、失败、取消、关闭和重试；`test_framework_release_binding.py` 的 external 参数用真实 Registry 加受控配置存储端口接通原 HTTP→Worker→官方框架与本地合成 HTTP。原装配子步结果见实施记录 §175；后续 main 可选组合见下节，仍不代表 MySQL 实库、付费供应商或完整 main 启动验收。

### main 的可选服务端受理策略（v4 §4／§14.1–14.2）

`deep_agents.message_submission_policy` 默认 None，不因安装依赖、配置制品或开启运行能力就自动创建新绑定。非空时，以下字段全部必填，没有内置生产数值；未知字段拒绝，不静默忽略：

| 环境变量（既有 Settings 嵌套规则） | 约束／含义 |
| --- | --- |
| `DEEP_AGENTS__MESSAGE_SUBMISSION_POLICY__CHECKPOINT_NAMESPACE` | 非空且非纯空白，最长 255 字符；逻辑 namespace，不替代物理 PostgreSQL schema |
| `DEEP_AGENTS__MESSAGE_SUBMISSION_POLICY__MAX_CALLS` | 1–100000；原根预算的总调用上限 |
| `DEEP_AGENTS__MESSAGE_SUBMISSION_POLICY__MAX_TOKENS` | 1–1000000000000；原根预算的总 Token 上限 |
| `DEEP_AGENTS__MESSAGE_SUBMISSION_POLICY__DEADLINE_SECONDS` | 有限正数；从任务创建时刻计算原根截止时间，排队／采集不会刷新它 |

策略要求 `enabled=true`、`checkpoint_enabled=true` 和原成对制品配置；缺项在 Settings 校验时拒绝。`new_turns_enabled` 独立，可保持 false 以停止新接入而保留配置与存量运行能力。模式集合复用原 `pilot_mode_keys`，不新建第二份白名单；配置并不是该模式质量／兼容部署已验收的证明，发布前仍须完成原发布门。

main 在原 Dispatcher 构造处调用 `FrameworkReleaseSubmissionLoader.from_configuration()`，使用启动预检返回的能力与完整制品引用、当前真实 Agent／Model Registry 和显式策略。未配置返回 None；能力缺失／无引用／不可运行时明确拒绝组合，不猜测源码版本、制品或预算。工厂不读取模型配置、不执行模型、不写数据库，当前材料仍到每个新消息受理前才读取。CLI 复用 main lifespan，没有另一套策略解析。仅移除策略停止生成新绑定，不会取消存量；无绑定执行仍受原 Runtime 路由开关控制。

组合日志 `framework_submission_unconfigured`／`framework_submission_configured`／`framework_submission_configuration_failed` 仅记录既有制品身份或错误类型，不输出路径、策略原件和认证。验证由 `test_framework_submission_configuration.py` 的配置／环境／工厂与 main AST 审计、原生命周期和预检测试、原完整 HTTP external 用例共同覆盖。HTTP 用例使用真实配置预检与同一组合工厂，但应用资源仍手动装配，不冒称完整 main lifespan、MySQL 模型配置链路或 E4 已通过。另有已运行本机 MySQL 的只读 SELECT 1／连接池归属清理验收，不代替配置表或正式受理验收。实际结果和剩余限制见实施记录 §176；没有修改实际 `.env` 或启用生产策略。

### 正式项目数据与现有图输入原件（v4 §4.1）

main 将既有 `ProjectService` 注入同一个 `build_turn_context_assembly()`；新完整上下文（v2.2／v2.3）只在上下文节点按 Runtime 已从 Session 写入的正式 `project_id` 读取一次项目正文。原 Session／Worker 的项目存在性和活动状态校验仍独立保留，不把一次正文读取说成整个请求只访问项目库一次。无项目不读取；有项目但服务未组合、记录不存在／已归档、返回身份不符或源异常时明确失败并记录 `framework_project_context_unavailable`，不使用客户端正文或切换 legacy。日志只有 Session／Turn／项目身份与错误类型，不输出原异常和项目正文。

新装配先丢弃请求 context 中的 `project_context`，再用正式来源写入该内部业务资料。只投影 id、project_key、name、description、base_url、graph_scope_key、status、created_at、updated_at、archived_at，不加入 created_by 或自动包含未来新增字段。原 HTTP payload／冻结 ExecutionRequest 仍保留输入证据；它们不是可信项目正文来源。Prompt 复用原转义／不可信内容包装，新增一次 `official_project` 资料段，来源为 `project_registry`，不授予目标权限、不覆盖固定业务环境、工具范围或用例版本。成功日志为 `framework_project_context_loaded`。

同一已装配正文和 Prompt 随原 graph-input 的 context／initial_state 入库，并直接进入实际框架请求。冻结后修改项目不改变该次调用；审批与原生终态后的结果读取／投影继续消费原图输入，不重新读取项目正文。新一次准备读取当时当前资料，没有启动缓存。原有项目活动状态／身份检查继续适用，冻结资料不意味着永久授权或归档后可继续发送。

这里保存的是现有 ProjectRecord 白名单字段的原件，不是新增项目版本表；updated_at 不是伪造的正式版本号。完整原件受既有图输入摘要保护，仅属于通用业务 context／Prompt 内容，不增加恢复权限、持久 schema、能力上界或 DDL。无此资料的历史已冻结图输入仍按原格式读取，不补齐／重渲染；无绑定及 v2.0／v2.1 的旧准备路径不增加项目正文读取。独立组合者若启用带项目的完整上下文，必须提供原项目服务，不能用缺失依赖冒充已接线。

验证位置：`test_framework_project_context.py`、原完整 HTTP 的 `test_framework_release_binding.py`、真实 PG 项目与审批／原件恢复的 `test_framework_context_runtime.py`。main 组合另有 AST 审计，不代替完整资源启动；实际命令、失败记录、结果与证据边界见实施记录 §177。正式用例版本执行、真实 embedding／供应商和领域 Runner 不由这一项目资料子步自动验收。

### 消费受理时的根模型选择（v4 §4／§6）

预算 v2 的非秘密配置原件通过原 `PostgresFrameworkModelCalls.frozen_policy()` 只读加载；`FrameworkModelBudget.root_configuration(binding.model_config_fingerprint)` 必须精确匹配唯一根配置，不以字典首项、角色名称或当前默认猜选。该派生读取不增加 JSON 字段／格式版本，不授予发送、owner 或恢复权限。历史预算 v1 没有配置原件，保留原图前编排选模契约，不推断或回填根身份。

尚未冻结 ExecutionRequest 的普通执行及获准 `orchestrating` 续接，使用仅替换 `model_key` 的临时编排参数，确保新语义准备和最终 ExecutionRequest 消费同一根 key。`auto`／空值不再根据执行时默认或 Session 偏好重选；明确 key 与冻结根配置冲突时，在编排前拒绝，不静默改写用户显式选择。原任务 payload、原消息元数据、source manifest 的请求摘要及完整绑定＋预算执行摘要保持原值。`orchestrating` 仍按原 PreparedSemanticInput 和账本状态复用／接管调用，不重新准备分类输入；`orchestrated` 直接消费已冻结请求，结果投影／ACK-only 不进入新选模读取。

bound Runtime 初始状态只查询 ExecutionRequest 的明确 key，不再回退 Session 偏好或当前默认。配置不可用时保留原 key 和绑定协议元数据并记录 `framework_bound_model_configuration_unavailable`，不能将当前默认当成可用替代；真实模型工厂与共享策略仍负责配置可用性、fingerprint、owner、预算及发送许可校验，快照不包含可恢复凭据／endpoint，不授权离线或永久调用。无绑定路径继续复用原默认 fallback。选模消费成功／失败日志为 `framework_message_model_selection_frozen`／`framework_message_model_selection_rejected`，仅记录任务／绑定身份、预算版本或错误类型，不输出原请求／配置正文。

验证落点：`test_framework_release_binding.py` 原 HTTP→PG／Redis→完整上下文官方框架链路覆盖 explicit／auto／受理后偏好变化；`test_framework_message_stages.py` 核对原 payload／摘要不变与显式冲突拒绝；`test_framework_orchestrating_recovery.py` 覆盖 v2 的 skip／未预留／ready／completed；`test_framework_orchestrated_message_recovery.py` 明确禁止已冻结请求恢复时重新读取选模策略；`test_framework_turn_context.py` 区分 bound 无 fallback 与 unbound 原行为。该消费修复的结果和证据边界见实施记录 §174；后续 main／CLI 默认关闭的可选写者组合见 §176 及本说明对应小节，不以消费修复或可选接线代替生产发布。

### 原路由与恢复验证

关闭新选路时记录 `deep_agent_new_turn_route_disabled`，带 `session_id`、`turn_id` 和 `recovery_reason=new_turn_admission_disabled`；这是路由决定，不是取消/停止回执。

- `tests/test_framework_route_rollback.py`：配置、实际 Runtime 选路、绑定矩阵、在途调用、请求覆盖负例，以及真实 CLI/dispatcher 搭配替身队列的能力准入。main 的接线审计为静态 AST 检查，不替代完整 lifespan。
- `tests/test_framework_approval_runtime.py`：固定官方框架和真实隔离 PostgreSQL/Redis 下，关闭新接入后重建 Runtime，仍保留审批重放、后继、工具效果次数和原消息身份。该开关场景本身不是独立进程部署验收。
- `tests/test_deep_agent_runtime_adapter.py`：原无绑定 pilot 在途审批的批准、拒绝、范围改变与混合批次；官方框架、内存 saver、受控工具，不是实际 CLI 工具或供应商验收。
- `tests/test_agent_v2_worker_contract.py`、`test_runtime_binding_persistence.py`、`test_framework_task_control.py`、`test_framework_approval_recovery.py`：沿既有版本/绑定、停止和恢复契约回归；具体命令、实际结果及证据限制见实施记录 §106。

实现依据是当前 `src/application/runtime/runtime_service.py`、`src/main.py`、`src/cli/run_agent_v2_worker.py` 和 `src/runtime/framework_worker_capabilities.py`。不新增调度层，不把路由权限当成外部执行停止证明。
