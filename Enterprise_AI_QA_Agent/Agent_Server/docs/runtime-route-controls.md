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

### 真实 MySQL 配置到 HTTP 受理的只读验收（v4 §4／§6）

`tests/test_framework_mysql_submission.py` 以 `RUN_LIVE_MYSQL_TESTS=1` 显式启用，另需原真实 PostgreSQL／Redis 测试环境。它不运行完整 main：复用原隔离 app、正式项目夹具、实际源码制品校验、同一 `from_configuration()` 工厂、真实 `ModelRegistry` 与 `MySQLModelConfigStore`，沿原消息 HTTP 路由受理。策略四字段仅写入测试 Settings 副本，不修改实际 `.env` 或开启生产模式。

现有模型表只通过原 `list_all()` SELECT 读取，结果仍由真实数据库及原行转换器产生；测试专用 cursor 只放行该语句并禁止 initialize，不替换记录、不执行 DDL／UPDATE／种子。每次材料读取均核对工作线程、PG Session 行锁可取和受理事实尚未产生。前后比较原 Store 完整记录投影的内存摘要，不输出原记录、凭据、endpoint 或摘要原件；这不是全表物理快照、数据库权限控制或生产 SQL 安全策略。

explicit／auto／Session preference 分支核对正式项目身份、真实配置的非秘密参数原件和指纹、绑定／预算／原输入摘要、v2 outbox 及固定限额／截止时间。HTTP 202 后只核对持久受理，不启动 relay／Worker；账本调用为空，真实 HTTP transport 发送被测试明确禁止。已有回执重试和同 key 冲突不再读取配置源；注入连接端口故障时，新请求受控失败、无额外部分事实，恢复端口后原失败意图可重试。故障注入没有停止／重启真实 MySQL 服务。

实际命令、结果及限制见实施记录 §178。此验收补齐真实模型表到受理的读取边界，不代表该配置的官方模型对象／供应商发送、正式 AgentCase、完整 main、生产发布或 E3/E4 已通过；执行阶段仍必须核对当前授权与冻结指纹。

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

### 直接受控图内核（v4 E／§12.1；尚未开放执行路由）

`src/graph/controlled_case.py::build_controlled_case_graph()` 直接构建 `START → model → tools → model／END`，由图持有条件回边，不再依靠图外循环完成该内核的多次模型调用。这里没有替换 legacy 图、正式 AgentCase 的外层执行器或主应用组合。`RuntimeService` 与 `FrameworkWorkerCapabilities` 仍不放行 `langgraph`；测试专用版本 `controlled-kernel-test` 不是发布版本，不能用于生产准入。

调用方必须提供已冻结的 Prompt／输入、已治理的 LangChain 工具、当前执行 owner、同一 owner 捕获的 fenced saver 和共享模型策略，并在原根执行 scope 内以 `durability="sync"` 执行。构建时的 owner／kind／policy／saver 一致性检查不是实时授权证明；原模型、工具及 saver 边界继续检查实际执行权与预算。内核不会自己 claim RunItem／AgentTask、创建业务审批、收尾应用结果或决定 Assessment／Verification。

模型请求复用从原 Deep Agents 模块原样移出的 `src/application/models/framework_model_middleware.py::FrameworkModelCallMiddleware`；旧 import 路径保留为同一个类。主调用、摘要及子代理的原组合不变，没有另一套预算／传输／重试机制。工具扇出复用固定 SDK 的 `Send`／`ToolNode`、原生逐调用 HITL 和消息 reducer；`handle_tool_errors=False` 保证执行权错误外抛，不作为普通工具观察继续请求模型。

官方 `add_messages` 会用相同消息 ID 替换旧消息。真实捕获的重复 Responses ID 样本曾导致 source AI 被替换、ToolMessage 失配；内核在共享策略保存并结算原响应后、进入 reducer 前拒绝与输入重复的 AIMessage.id，保留原 source／工具链和已结算账本，不随机更换身份、不改 reducer、不自动重发。日志为 `controlled_case_graph_composition_rejected`、`controlled_case_graph_built`、`controlled_case_model_returned`／`controlled_case_model_failed`，不记录原消息或配置正文。

`tests/test_controlled_case_graph.py` 验证四种官方模型协议到本地合成 HTTP、真实隔离 PG 根账本／saver、同批 A/B 的逐项审批与严格 native messages 对比、预算回边、未知响应不重发、取消及失租。原应用工具桥的独立参数组复用真实 `ToolGovernanceService`／审批投影／`ToolRuntimeService`／PG ToolJob 和结果原件，handler 仍是测试 callback；审批决议直接写本轮隔离记录，不代表公开审批 HTTP 或正式 RunItem 入口已接通。回执文本不等于领域测试通过，详见实施记录 §179 的真实结果与剩余出口。

### 受控图调用与原生结果边界（v4 E／§12.1；未接正式 RunItem）

`src/application/runtime/controlled_case_adapter.py::ControlledCaseGraphAdapter` 将上述内核组合到原 captured saver／模型策略的 `execution_scope()` 中。初次输入只接受原生 LangChain 消息原件，不能以展示 role/content 字典重建历史；session／turn／namespace／runtime kind 必须匹配捕获的 owner，恢复决议与 native continue 不能同时提交。每次继续或重建都读取同一原根 deadline，不创建新的运行时预算或自动重试。调用方仍须从正式来源准备／冻结输入和工具，本适配器不会把普通请求文本升级为正式用例版本。

`src/application/runtime/framework_graph_invocation.py::invoke_and_audit_framework_graph()` 是从原 Deep Agents 适配器提取的共同调用端口：有 saver 时 sync durability；返回后只在计时器未过期时停用该次 timer；逐项比较返回 interrupt 与原生待办，无 interrupt 时复用原严格 `collect_native_terminal()`，最后捕获原根预算。Deep Agents 的正常／审批／continue 与受控图都调用此端口，原子代理事件流仍委托既有实现，不复制调度、消息 reducer、恢复证明或收尾。已有 Deep Agents 结果投影和异常类型保持兼容。

返回的 `FrameworkGraphOutcome` 只携带原始 values、子代理事件、原生中断、`NativeTerminalRead` 和预算原件。native proof／checkpoint timestamp 不是应用完成、正式测试通过或恢复许可；消息不一致会沿原核验异常拒绝返回结果，不忽略 messages。原生审批材料也不能替代业务决议授权。成功／失败日志为 `controlled_case_graph_outcome_captured`／`controlled_case_graph_invocation_failed`，不记录消息／配置正文，取消继续向外传播。

`tests/test_controlled_case_adapter.py` 覆盖四协议官方 SDK＋本地合成 HTTP＋真实 PG saver／根账本／业务 ToolJob、同一终态继续不重发、原生批准／拒绝与重建、根 deadline／取消、错误输入和原生／返回消息不一致。部分审批、计时器及输入反例为明确替身；这些证据不等于独立进程、真实供应商或正式 AgentCase 验收，实际命令与结果见实施记录 §180。原内部 Python 请求／Outcome 没有公开 API；Deep Agents graph-input 契约不能直接用于受控图恢复。下文受控 codec 是另一个明确版本，只完成内部执行／读取消费，不开放正式 RunItem／AgentTask 受理、RuntimeTurnResult／原件与唯一 finalizer、兼容 Worker／发布制品或 main 选路。

### 受控图 source-only 读取（v4 E／§8.2、§12.1；非恢复准入）

`ControlledCaseGraphAdapter.open_native_reader()` 只用显式的 `RuntimeCheckpointSource` 打开原 `BoundPostgresReader`，不从当前上下文取得 owner，不初始化 checkpoint schema，不解析模型／策略或重新构建业务工具。原 Python 请求仅核对 session／turn／物理 namespace 与 runtime kind；它**不是持久化固定用例原件、版本／制品兼容证明、RunItem 租约或应用恢复授权**。不以本端口存在提前发布新资产格式。

执行图和只读 schema 复用 `src/graph/controlled_case.py` 的同一 `StateGraph(MessagesState)` 拓扑与官方通道物化机制；该固定内核的 Prompt／工具／逐项审批配置不增加状态通道。只读 model／tools 节点均显式拒绝执行，saver 另行拒绝写入、setup 和审批材料消费。正常工具执行仍复用原 `ToolNode`／HITL，不复制 reducer、Delta 恢复或审批引擎。官方 `StateSnapshot` 可以观察中断，原 `collect_pending_tool_interrupts()` 是需要执行权的审批材料端口，仍不对 source-only 读者开放；等待状态不能冒充终态。

`src/application/runtime/framework_native_schema.py::native_graph_schema()` 仅选择原 schema builder。原 `require_native_terminal_original()` 在同一应用连接短事务内通过它重读，沿用原 serializer、namespace 映射、搜索路径恢复及 checkpoint／完整 native state／时间／通道／事件前缀比较，增加 proof 与 source 的精确身份比较。Deep Agents schema 现在明确拒绝 graph-input 与 source 的 runtime kind 不同，即使调用者同步更改嵌套 binding 并重新计算资产摘要，也不能将其解释为受控图。这个选择器不放行 Worker、不签发任何恢复或写入凭证。

`tests/test_controlled_case_native_reader.py` 验证四协议官方 SDK 到本地合成 HTTP、真实隔离 PG 的终态原件失租后重开、原生批准／拒绝后读取、真实业务 ToolJob／结果投影原件保留、只读中断观察和写入拒绝、事务内篡改反例及跨类型拒绝。内存 saver／不可调用节点是独立替身边界；实际命令与结果见实施记录 §181。日志 `controlled_case_native_reader_opened`／`controlled_case_native_reader_failed` 只含绑定／任务身份、epoch 或异常类型，不输出消息、模型配置正文或认证。

### 受控图冻结输入消费（v4 E／§9、§12.1；尚未发布业务写者）

`src/schemas/controlled_case_graph_input.py` 定义独立的 `ControlledCaseGraphInputAsset`，显式整数 schema 1／kind=langgraph，包含完整 RuntimeBinding、trace／model key、原 system prompt、官方 LangChain 原生消息字典、按原顺序的完整工具 schema、可 JSON 持久化的逐工具 HITL 配置及规范摘要。工具 schema 复用原模型中间件的官方 `convert_to_openai_tool()`，决定类型复用固定 SDK 的 `DecisionType`；不另建供应商转换器、消息 reducer 或审批引擎。新冻结边界要求稳定且不同的输入消息 ID，避免官方 add_messages 替换原输入；不随机补 ID。描述 callable 不可持久化，不能悄悄改为当前生成的文本。

`freeze_controlled_graph_input()` 不接受 resume／continue 标志，不保存 owner token 或恢复权限。`parse_controlled_graph_input()` 在每个执行／只读边界重新校验摘要，即使传入已构造的 Pydantic 对象，也不能利用其可变嵌套字典绕过。内容摘要不是签名或授权，资产必须仍来自服务器原件；本对象也没有冒充固定用例、完整有效输入清单、RunItem attempt 或应用结果资产。

`ControlledCaseGraphAdapter.execute_frozen()` 只从原资产重建值，显式传入当前已治理工具并用既有规范 JSON 摘要核对其完整 schema／顺序，避免 Python 相等判断混淆布尔值与数字；工具 iterable 只物化一次。schema 相等不证明 handler 代码／目标授权相等，工具治理、制品与 Runner 执行权继续独立校验。随后进入原 `_execute()`，在解析模型／调用策略前核对完整捕获 binding，不因 session／turn 相同就接受其他版本／Prompt／Skill／工具／制品身份。正常／native resume／continue 共用原图、根策略、预算和 sync saver，不新增第二个循环。决定是调用方独立提供的受治理控制输入，不从资产取得审批交付权或自动恢复资格。

冻结执行将 `controlled_graph_input_sha256` 放入固定 SDK 的 invoke metadata，由官方 checkpoint codec 与原 fenced saver 保存为内容引用。已有 checkpoint 必须引用同一资产；无引用的旧 checkpoint 不得被新资产收编。发现已有 checkpoint 后拒绝再次提交初次输入，只接受显式 resume／continue；没有 checkpoint 的裸资产也不获得继续资格。这是读取原件后的顺序检查，不是正式 case 的原子受理／CAS，不证明并发调用幂等，也不授予审批交付权。

原 `open_native_reader()`／`native_schema()` 现在也消费该对象或其 JSON 字典：先校验完整资产与原 source binding，再打开 source-only provider；不重构工具或解析当前模型。解析使用独立 JSON 副本，provider await 后不重新读取调用者可能修改的 raw 字典。读者入口校验当时最新 checkpoint 的输入引用；schema builder 保持惰性、不执行 IO，其本身不是原件核验证明。同事务原件复核沿原选择器消费同一捕获资产，并检查 proof 指向的精确 checkpoint 引用，而非仅比较资产自身摘要。旧 Python 请求的窄读取接口保留，不将它升级为固定用例证明。日志为 `controlled_case_graph_input_loaded`／`controlled_case_graph_input_rejected`，只记录绑定／turn、资产摘要或错误类型，不输出 Prompt、消息、工具配置或异常正文；执行和读者失败继续使用原拒绝日志。

本步仅为内部受控图 checkpoint metadata 增加上述引用，没有发布既有 message stage、AgentTask、RunItem 或 outbox 的新格式写者，也没有将 Deep Agents codec 偷换为 union。现有 Worker／Runtime 仍拒绝 langgraph；正式 RunItem 输入／上下文封存、AgentTask 自身 attempt 与 RunItem 原执行 attempt 的双归属、专用受理命令、投影和发布兼容能力仍须接通。`tests/test_controlled_case_graph_input.py` 的真实 PG／根账本和四种官方协议只连接本地合成 HTTP，工具为测试 callback；原生批准／拒绝不代表业务审批授权。结果和限制见实施记录 §183。

### 正式用例输入与命令读者（v4 E／§9、§12.1；reader-first）

`src/application/test_runs/controlled_case_preparation.py::prepare_controlled_case_input()` 只构建值：复用原 `execution_attempt()`、领取时计划 pin 校验和 `build_agent_case_context()`，核对调用者／正式条目／最新 attempt 的项目、用例版本、运行状态及租约身份，拒绝缺少原 pin、计划漂移、已有 checkpoint 或 recovered attempt 重新首次冻结。不读取当前计划来补历史 pin，不创建 Session／任务／outbox，不调用模型或工具；观察到租约字段相同不证明此刻仍持有执行权，同事务受理仍须锁定真实行、使用数据库时间并重验。

新 `ControlledCaseTaskInputAsset` 为独立显式整数 schema 1，保存原正式 `AgentCaseExecutionContext`（含目标完整内容、执行说明、来源及已解析依赖）、原 `RunPlanSnapshotIdentity`、独立 `run_item_execution_attempt_id` 和上节受控 `graph_input`，用原规范 JSON 摘要覆盖全部内容。核对唯一正式目标／版本、plan_id 和沿用旧 AgentCase 的 UUID5 `case-run:{run}:{item}:{original_attempt}` Turn 规则。parser 对字典和已构造对象均重新校验、复制深层 JSON，不能把 frozen 模型当成深层不可变。摘要与自洽身份不是签名、正式来源授权或租约；本函数仍接受由服务器装配者提供的图请求，**不证明完整有效输入／Prompt／Skill／Memory 管道已接通**。

原 `AgentTaskCommandV2` 增加内部 `case_execute` 操作及专用 `case_execution_input`，不新增队列或 v3 命令。case 只允许 `agent_turn`／`langgraph`，完整 binding、trace、project／run／item／version reference 必须匹配输入；禁止在公共 `attempt_id` 填入 RunItem attempt，禁止预填 worker／resource，也不接受第二份自由 payload。AgentTask 自身 attempt 仍应由其 claim 独立生成。其他操作不能携带 case 输入；缺省字段不输出 null，原 message wire 保持不变。公开 `AgentTaskRecord` 与前端只同步 operation 读者，不公开输入原件。

原 `FrameworkWorkerCapabilities.require_command_format()` 直接复用严格 codec：新资产／嵌套 graph 的未来版本或额外字段沿原兼容性等待分类，普通摘要损坏仍是验证错误。能力准入仍拒绝 langgraph，旧 Worker stream 仍拒绝 v2，现有 `submit_v2()` message writer 仍拒绝 case 操作；生产尚未装配 case 写者、双租约 CAS／fencing、受控结果 finalizer 或 main 选路。下节新增的是未装配的内部受理端口，不是生产开放。版本制品的命令 JSON schema 会自然包含嵌套新定义，但定义库存不等于制品兼容执行能力。

日志 `controlled_case_input_prepared`／`controlled_case_input_preparation_rejected` 只带标量身份、摘要或 error_type，不输出租约 token、完整 context／Prompt／工具／异常正文；成功摘要复用原 formatter 的 `artifact_id`，拒绝阶段复用固定 `recovery_reason`，不开放任意 extras。新专项 `tests/test_controlled_case_task_input.py` 的正式草稿→评审→启用→冻结计划→领取来源使用真实业务服务与内存存储，规划／对象后端为替身；真实 PG／Redis 组明确模拟未来任务／outbox 行，只验证读者拒领和事实无变化，不是正式 RunItem／AgentTask 同事务受理或实际执行验收。实际结果与剩余出口见实施记录 §185。

### 内部正式用例原子受理（v4 E／§9、§12.1）

`application/test_runs/controlled_case_admission.py::ControlledCaseAdmission.submit()` 接收完整冻结 case 命令、原 RunItem 的 lease token 和 schema 2 模型预算；仅受信服务端调用，**main／CLI／公共路由均未装配**。不会创建 Session、调用模型／图／工具、领取 AgentTask attempt，也不会延长 RunItem 租约。参与者复用原 PostgreSQL Run／Case／Task stores，连接身份用既有 `postgres_url()` 比较（不渲染认证），不把无关 Redis／MySQL 设置差异当成拒绝依据。

事务沿既有 Session → 计划共享投影锁 → RunItem → 原 Attempt 顺序，读取并锁定正式固定版本，复用原领取 pin／归档读者和纯准备器重新构造完整输入，再与原资产摘要核对。保持原合法兼容路径：归档缺失但当前完整快照仍精确匹配 pin 可以复用；归档缺失且当前投影漂移拒绝。取得绑定／模型预算 owner 锁后，**再次以数据库 `clock_timestamp()` 的现场时间检查原 running 租约和预算截止**，然后在同一事务保存 task、完整 input、原 Attempt 关联、绑定／预算、事件和既有 Outbox；任一步失败整笔回滚，不使用事务开始的 `NOW()` 或调用者时间代替现场检查。

`controlled_case_admission` 是新的保留 checkpoint key，原 Attempt 的 schema 1 关联保存正式身份、独立 `run_item_execution_attempt_id`、task、binding、plan pin、输入／提交摘要和数据库时间；严格读者不把继承 checkpoint 的新 Attempt 当作原执行。原内存／PG 通用 checkpoint 写口统一拒绝安装或覆盖此 key，必须走原子写者。其他旧 key 保持原行为，旧 AgentCase 遇到不同执行策略 checkpoint 仍走其既有显式对账分支。没有 DDL、历史回填、运行条目复用或旧失败覆盖。

同一原 Attempt 的并发首次受理由原 Item／Attempt 锁串行化；幂等身份由该原 Attempt 派生，重试核对完整输入／绑定／预算和持久关联，不重新生成或覆盖任何原事实。丢 ACK、Run 取消或原 lease 已过期后可以读取已受理回执，但**回执不是执行授权**。继承原 checkpoint 的新 RunItem attempt 不得重新首次冻结／派发原执行，须按后续独立恢复协议对账；AgentTask 自身 attempt 仍保持 None／0，未来由兼容 Worker 生成。

原完整 `case_execution_input` 形式继续可读，供内部首次受理使用；新增严格、互斥的 `case_execution_reference={run_item_execution_attempt_id,input_sha256}` 用于实际 Outbox／Redis 投递。投递不复制 Prompt、原生消息或业务 JSON；完整原件只在 PostgreSQL task input。非 case 操作不得携带任一形式，原 message wire 不增 null 字段。新参考字段对旧 extra=forbid 读者仍可能不兼容；未装配写者和当前 Worker 拒领测试不等于旧制品滚动部署已验收，不能据此打开写者。

日志 `controlled_case_admission_committed`／`reused`／`failed` 仅保存身份、固定失败阶段和摘要；原 RunItem attempt 用既有 `execution_id` 渲染，不冒充 AgentTask `attempt_id`。事件为通用字符串类型 `test_run.case_admitted`，payload 显式携原 RunItem attempt；不制造用户消息或 message-stage。协程等待者取消不能停止 `to_thread` 中的 SQL，日志 `controlled_case_admission_waiter_cancelled` 明确 `admission_receipt_unknown`，不谎称回滚、不自动重跑命令；重试只核对原持久回执。

`tests/test_controlled_case_admission.py` 的正式草稿／评审／启用、Suite／计划 Run、领取／start、受理和 Outbox 使用真实 PostgreSQL；规划模型、配置和对象后端仍为明确替身。原 Relay 真实发布到隔离 Redis，默认 Worker 两次读取实际 reference-only Outbox 后仍在 claim 前拒绝 langgraph。冷进程只验证导入／codec，不冒称独立执行进程验收。受理验证见实施记录 §186；后续内部引用核对／双租约见下节与 §187。完整服务器上下文装配、实际受控运行与 RuntimeTurnResult／唯一 finalizer、强杀和真实领域链路仍待实现／验收。

### 内部正式用例双租约领取与写入 fence（v4 E／§8、§12.1）

`PostgresAgentTaskStore.claim_controlled_case()` 是未装配的内部执行准入口，复用原 claim／Attempt／事件事务，不创建第二个调度器。仅接受 reference-only 命令；在 Session 锁内严格比较持久 task 身份、完整 case／graph 输入、原 RunItem checkpoint、plan pin、输入／提交摘要和根预算。未知输入／预算格式沿原兼容性等待分类，损坏／漂移不领取；不重新查询、分类或生成当前 Prompt 替换原件。通用 claim 不能绕过该端口直接领取 case。

新增 `FrameworkWorkerCapabilities.controlled_case_available` 默认 false，仅内部 Harness 显式提供 true；`from_artifact`／`from_configuration`／main／CLI 未启用。即使提供此能力，当前 Dispatcher 仍不走专用 case claim／执行／finalizer，不能据此开放生产 Worker。完整领域执行、正式结果投影和滚动制品验收仍待完成。

claim 沿 Session → 同一物理 Run 表的计划共享投影锁 → Item → 原 Attempt → runtime owner → task 的锁顺序，取得现有模型账本兼容读锁后，以数据库现场时间复核原 RunItem lease／deadline，再原子生成独立 AgentTask attempt／token／epoch。原 RunItem attempt、租约、冻结输入和预算不重置、不续期。两项并发只允许一次领取。原 running task 已失去自身 owner 时仍沿原逻辑进入 recovery_required，不重新运行图。

Worker 内存中的 `RuntimeExecutionOwner.controlled_case_lease` 捕获原 RunItem 身份、attempt_no、worker、lease capability 和输入摘要，始终从序列化与 repr 排除；不进入 Redis、checkpoint source、图状态或公开 TaskRecord。剥离这个字段不能继续写正式 case；source 仍只读，不是执行许可。两个 attempt／token／worker 各有独立含义，不相互冒充。

既有 `assert_runtime_owner()` 对正式 case 在同连接取得业务锁，再取 runtime owner 锁，用同一个锁后 DB 时间检查两项 lease。原官方 saver／pending writes、模型账本以及其他调用此 owner 端口的事务共用此规则；不在图运行期间持锁。saver 的 checkpoint-only search_path 下，业务查询及计划锁的 regclass 均明确使用 public 物理表；不换成另一个 advisory 锁资源。双租约 heartbeat 也沿同连接，原子更新 Session／runtime owner／RunItem 的到期时间，任一步失败全部回滚，任一 lease 已失效都不复活。

终态重复投递严格读原受理身份／原件，仅返回没有 runtime_owner 的回执；Run 已取消不应误杀该只读分支，但绝不借此授予执行权。未实现 case 的唯一应用 finalizer 前，通用 finish 禁止将它标成 completed，并留下 `controlled_case_finalizer_required`；失败／取消／对账沿原终态规则。锁等待复用原 SQLSTATE 55P03 → TaskDeferred 分类；协程等待者取消记 `claim_receipt_unknown`，不谎称同步 SQL 已停止，不自动重领。

实际红绿与验证范围见实施记录 §187。真实 PG 正式来源、隔离 Redis、官方 saver、模型账本预留属于本轮范围；没有实际模型 HTTP／领域 Runner、受控图独立 Worker 强杀或正式结果 finalizer 验收。数据库 fence 不能撤销已发送的外部操作，未知效果仍需原 journal／停止回执或人工对账。

### 内部正式 case 的一次性原生执行（v4 E／§9、§12.1）

`ControlledCaseExecutor.execute()` 接受专用 claim 捕获的双租约 owner、调用者已取得的资源 fencing 身份，以及异步 `build_business_tools(prepared)` 工具装配端口。它不是 Dispatcher、重领／续租循环或 finalizer，main／CLI／生产 Worker 均未装配，默认 case capability 仍为 false。正式完成仍被原 `finish(completed)` 拦截。

执行器深拷贝 Worker 对象，保留不可序列化的第二租约、隔离可变 input；先读取严格原件，再复用原 task `begin` 事务。在 Session／原 RunItem／owner／task 锁内，对 case 额外核对持久原输入、captured input、绑定／正式身份、原受理 checkpoint／plan pin 和提交摘要。取得原预算的共享锁后，再以原 owner assertion 复核两项租约和 DB deadline。只有原事务提交 running／Attempt／事件后，才调用工具装配和原 `execute_frozen()`；没有新 DDL、checkpoint 格式或中间 result_payload，也没有伪造 message-stage。

工具装配保留原“running owner 才能读取 gates”的规则，不能提前构造再放宽检查。装配得到的工具仍须与冻结 schema／顺序一致；执行继续走原模型策略、根账本、官方 saver、ToolNode 和业务 ToolJob／权限治理。共享工具事件依据原 binding 标注 `langgraph.tool_governance`／`harness=langgraph`，Deep Agents 的既有值不变，不将 controlled case 伪贴为 Deep Agents 或 DA-E 阶段。事件名和公开 DTO 不变，消费方按原通用事件处理。

执行器复用原 owner-bound `SessionEventSink.run()` 与 `graph_event_scope`，治理事件逐项 await PostgreSQL ACK，失败阻断后续执行，不使用内存溢出后的静默最终补刷。这里只证明已确认的单项事件持久化；没有声明完整事件集合、RuntimeTurnResult、应用提交或正式测试裁决。

返回类型仍是原 `FrameworkGraphOutcome`：原生终态、逐项 interrupt、消息和根预算材料，不是应用结果资产。图返回、等待审批、模型 unknown、工具迟到或协程取消均不自动把 AgentTask／RunItem 置 completed。原 running 意图在重复／并发执行时阻断二次 invocation；失租由原 recovery_required／对账规则处理，没有恢复授权便不能重跑。begin 的 `to_thread` 等待者取消可能仍提交 SQL，固定日志保留 `begin_receipt_unknown`，不谎称回滚；原生只读 reader 不因旧租约失效而丢弃原件，也不授予重执行权。

真实正式来源＋本地合成 HTTP／工具、实际官方框架与 PG／Redis 的验收见实施记录 §188。完整服务器上下文与工具装配、确定性 RuntimeTurnResult 原件、唯一 Session finalizer、Assessment／Verification／证据与正式 RunItem 完成事务、case 审批继续／强杀恢复及领域／真实供应商验收仍待接入，不能据本内部执行出口开放生产。

### 正式 case 的完整上下文准备与冻结消费（内部端口）

`ControlledCaseRuntime.prepare_input()` 复用原 `RuntimeService._build_initial_state()` 与
`TurnContextAssembly` 的 context_builder → router → permission_gate → prompt_assembler，
不运行 legacy 图、不重分类、不新增模型循环。先核正式版本、原 RunItem attempt／plan pin、
专用空 Session、绑定模型和预留投影身份，再冻结真实装配的完整应用 state、Prompt、
原生输入、Skill 内容和工具描述。准备阶段不调用执行模型／业务工具，也不写业务库；
检索与配置端口仍由应用装配；其真实 embedding 等依赖另需环境验收与调用授权，
不能由本测试替身推断已验收或保证不发生外部检索调用。

正式来源使用原 `AgentCaseExecutionContext`，无业务用户行／message-stage／跨 Turn transcript。
原生协议中的 HumanMessage 显式标记 `source=test_run.agent_execution`，身份来自原固定 Turn；
不能把它解释为伪造的持久用户消息或 Session 历史发布。request 中的框架／Deep Agents 保留标记
不授予新格式，原 managed UI 的重复工作流 Skill 排除规则保留，trusted direct security 仍关闭。

新增 task input schema 2，复用 `FrameworkRuntimePreparation` 保存绑定与预留 assistant／snapshot
身份和图前 state；graph input 仍为原 schema 1。摘要同时覆盖准备材料，读者核对正式上下文、
原生来源、Prompt、工具 schema／顺序、审批面、模型协议和原事件身份／时间／顺序。
原 schema 1 保持可读、不得在新应用端口内静默补装配。原受理事务复用同一纯准备函数重构原件，
核当前专用 Session；claim 在原写入前检查 `controlled_case_input_max_version` 和 assembly 能力。
旧格式能力显式不足则 defer，不重写输入、不领取租约；默认 `controlled_case_available=false` 不变。

`ControlledCaseRuntime.execute()` 仍委托 §188 的一次性 executor 和双租约 begin；running 提交后，
用原 `SessionEventSink` 逐项确认冻结准备事件的原 ID／时间，再从冻结 state／描述装配治理工具。
新 Worker 不重新检索项目、Memory、Skill、MCP 或配置来替换原上下文；工具执行仍走现有实时治理。
ACK 失败保留 running 意图且禁止重跑，不能把准备时间冒充执行／数据库写入时间。
schema 2 的 `ControlledCaseRuntimeOutcome` 仍只含应用状态、ToolJob 记录和原生读材料，
不静默升级历史输入。§190 首次引入 task input schema 3，单独冻结结果投影版本、
`current_final_ai_text` 输出契约与 `native_checkpoint.ts` 时间来源；graph input、binding 和命令
envelope 格式不变。旧格式／assembly 能力不足的 Worker 在 claim 前 defer，默认受控能力仍关闭。

schema 3 原生正常结束后，在原 executor 的 owner／sink 作用域内，复用同连接官方原生读者核对
精确 checkpoint、冻结输入、原模型预算／已结算账本、原 ToolJob／工具展示与 runtime slot 和
已 ACK 的有序事件。确定性投影器复用通用 snapshot／RuntimeTurnResult 装配，不贴 DA stage，
不重新检索或执行模型／工具，不从 JSONB 重新拼造工具字符串。snapshot 身份／时间保留执行前
预留值，终态事件身份按冻结规则派生，时间明确是原生 checkpoint 创建时间而非应用提交时间。
原 sink 逐项 ACK 终态事件并持久事件集合声明后，才返回可选 `runtime_result`。

同一原 live 双租约下的 `ControlledCaseResultReader.read()` 可从精确原生读材料与已声明集合重读
同一结果；**不是失租恢复／A2-2 授权**。已知截断／阻止响应不标完成；当前 AI 文本为空时不回退
旧答案，也不假装支持受控图尚未提供的结构化空结果。原生审批等待返回 `runtime_result=None`，
不把它变成业务审批许可。无 Job 的执行前拒绝／失败只复用匹配的治理审计时间和原件，仍无
`output_reference`，不是外部执行证据。模型账本结算不等于缺失的模型响应对象已被导入。

终态事件／集合 ACK 失败、失租或提交后 ACK 丢失均不自动重跑；保留原 running 意图与实际
持久事实。schema 3 的结果与事件集合仍**不是不可变结果资产、Session 最终提交、审批恢复许可或
正式测试裁决**。本端口未安装到 main／CLI／生产 Dispatcher；原 `finish(completed)` 仍要求
尚待接入的唯一应用 finalizer，不得仅凭文本、RuntimeTurnResult 或事件声明完成 RunItem。
唯一 Session finalizer、正式 Assessment／证据与 RunItem 原子完成、业务审批继续、强杀恢复、
真实供应商／embedding／RustFS／领域 Runner 及部署验收仍未完成；不得据此开启生产。
schema 4 的原结果资产留存见下节。真实来源／替身、实际命令和验证限制见实施记录 §189–191。

### 正式 case 原结果资产与 Verification 输入留存（内部端口）

§191 的准备端口生成 task input schema 4，在原完整准备中冻结
`FrameworkVerificationPreparation`（规则版本、原绑定／trace、预留时间与能力快照），
并核对图前 state 中的同一原件；不在图后用最新配置替换。schema 1／2／3 含义不变，
schema 2 只执行、schema 3 只投影，不能静默获得 schema 4 的留存行为。

正常原生终态与事件集合已 ACK 后，原 owner／sink 内调用
`FrameworkRuntimeResults.freeze_controlled_case()`。在同一短事务复核原 live 双租约、
精确 native checkpoint、原任务／输入／预算、模型账本、ToolJob 和事件，核对完整确定性 JSON
投影，复用原 Verification builder 首次生成结果。`ControlledCaseRuntimeResultAsset` schema 2
沿用同一结果资产契约，显式增加正式 run／item／原 RunItem attempt，绝不伪造 message-stage。

原 RunItem attempt 的 checkpoint 从 1 扩为 2，包装原 admission 与结果资产，原 key／
`checkpoint_at`／admission 事实不变；`retained_at` 使用本次实际 DB 留存时间，不冒充图结束时间。
同事务追加稳定身份的 `test_run.case_result_prepared` 审计回执，绑定原资产摘要，
`application_finalized=false`；它不是 completion 事件或业务裁决。claim 前与锁内均检查
`controlled_case_input_max_version=4`／checkpoint strict codec（§191 上限 2，当前上限 4，见后两节），
旧能力或未来格式 defer、不改原件；默认 `controlled_case_available=false` 不变，
本兼容测试不是双制品部署验收。

同一 live 双租约下的 `load_controlled_case()`／重复 freeze 复核原件、资产与原审计回执，
复用已留存 Verification，不重新调用 builder。未知提交、协程等待者取消或 ACK 丢失不得自动
重跑图／模型／工具，也不能声称同步 DB 线程已停止。审批等待仍无 result_asset；
原 `finish(completed)` 仍拒绝。本留存端口不承担 Session 分项写入、正式 Assessment／RunItem 完成、
失租恢复许可、独立 case Worker 强杀或 main／CLI／生产 Dispatcher 接线。

### 正式 case 共享分项写入与原 attempt 回执（内部端口）

§192 为原 snapshot／event／observation writer 增加显式正式来源端口，不复制算法或新增 finalizer。
`FrameworkProjectionWrite.controlled_case_native` 仅传递内存中的精确官方原生读材料，默认 None；
不是持久字段或公开 DTO，不从文本／简化 JSON 重造 native。普通 message 来源仍走原 stage，
原 message-stage schema 8／9／10 写法与最终应用提交不变。

正式来源每次在同一连接锁定原 RunItem／attempt 和 AgentTask 双 owner，读取持久 schema 4 输入，
复用完整 `ControlledCaseResultReader.read_in_transaction()` 复核 native、预算、模型账本、ToolJob、
有序事件集合和原投影，再核留存资产与稳定审计回执。缺 native、失租、输入或任一原件漂移拒绝，
不绕过旧检查。写回前再次用 DB clock 核双租约，原分项行与回执同事务。

原 attempt checkpoint 3 保留 admission／原结果资产／retained_at、原 key 和 checkpoint_at；
只增加 `projection_receipts`，复用现有 snapshot、event、observation asset／commits 类型。
观察资产与回执成对、绑定与原结果摘要严格匹配，未知字段拒读；
§192 的 checkpoint 上限为 3（当前为 4，见下节），旧 reader2／未来格式 defer，不修改原件。
默认 `controlled_case_available=false` 仍不变，未做双制品滚动部署验收。

snapshot 重入核实际行头、完整内容与原回执；数据库合法分配 version 不改冻结意图，DTO 时间仍遵守
既有 UTC-naive 契约，物理 TIMESTAMPTZ 与原 aware 时间一致。runtime event 可核原行后补分项回执，
新生成事件的裸行不可收养；观察沿用一次准备的原资产／请求，无 embedding 调用，已存在行逐项
核内容和回执，缺失／漂移转对账，不覆盖。并发分项在原锁内合并，不丢其他回执。

snapshot 等待者取消仍传播 CancelledError，并记录 `snapshot_write_receipt_unknown`、
`automatic_replay=false`；这不是后台 DB 线程停止或事务回滚证明。提交后 ACK 丢失保留真实事实，
不得自动重跑图／模型／工具。上述分项端口本身只保存部分回执，不证明应用已最终提交，
不创建 message-stage／业务用户行；唯一 finalizer 的正式来源入口见下节。
正式 Assessment／RunItem 完成、业务审批继续、失租恢复／独立进程与 main／生产路由仍待，
不能凭分项回执调用 finish 或判通过。

### 正式 case 唯一 Session finalizer 与应用提交（内部显式接线）

§193 的 `ControlledCaseRuntime(runtime_service=..., session_service=...)` 可显式连接原 SessionService。
仅 schema 4 正常原生终态／原事件集合／结果资产已留存后，在原 executor owner／sink 关闭前，
调用 `finalize_controlled_case_result(owner, native=...)`。默认未传 SessionService 仍只准备结果，
历史 schema 1／2／3、审批等待不静默升级；未装配到 main／CLI／生产 Dispatcher。

该内部端口要求当前捕获的原 live 双 owner，先完整核原件并读应用提交证明；未提交时加载原资产，
进入**现有唯一** `_finalize_runtime_result`，重读原资产、原消息／时间／Verification，
不重跑图、模型、工具或 Verification builder。原消息来源仍要求其真实 user 行，正式来源明确没有，
不伪造 message-stage 或业务 user 行。工具展示与 assistant 原身份／内容、Verification 原结果，
沿同一 Session rebase 和 immutable-message 写法提交，保留并发偏好、排队输入和其他 Turn 消息。

checkpoint 4 继承原 admission／资产／部分回执，增加原 FrameworkProjectionCommit 和
post_commit_memory_intent／plan／可选 execution；原 key／checkpoint_at／retained_at 不变。
证明 scope 仍为 `session_messages_completion_events`，不是正式测试结果或 RunItem 结算。
实际 snapshot／必需事件、消息、Verification、模型／ToolJob 摘要与 owner 均在原最终事务复核，
写 proof 前再次按 DB clock 核双租约，Session 行／消息／completion 事件／proof 同事务。
合法 snapshot version 分配不回写原意图；缺失、漂移或裸行不冒充完整提交。
`controlled_case_checkpoint_max_version=4`，input 上限仍 4，reader3／未来字段／version5 defer；
默认 controlled_case_available=false 不变，本读者测试不代表双制品部署验收。

正常提交沿原纯策略冻结 Memory 意图／计划，原 Memory 执行器复用来源端口与精确 native，
原行／执行回执同事务；没有第二个派生写入算法。Memory／cleanup 仍是提交后独立效果，
失败保留已提交的 Session 和原 unknown 事件，不伪称效果完成。
已有完整应用证明时只读／ACK，不再次调用 finalizer、Verification／观察 builder 或 Memory；
派生 Memory 行后续缺失不会触发自动修复，未知效果不因重入重派。

取消／ACK 丢失保留真实 SQL 事实并记录 application_commit_receipt_unknown，不自动重放；
等待者取消不是后台线程停止证明。事务内日志 application_commit_in_transaction 仅说明已写待提交，
只有最终事务实际返回后才记录应用 finalization 成功。
这里的 Session completion 不等于 case passed：非空 Verification 的 failed 原结果仍保留，
AgentTask／RunItem 仍 running／无正式 result_id，原 finish(completed) 仍拒绝。
正式 Assessment／证据与 RunItem fenced 原子完成、业务审批继续、失租后的正式恢复／只读 Memory
对账与独立 case Worker 强杀、main／逐模式／真实领域／E3/E4 出口尚未完成，不能据此开启生产。

### 正式 case 原 Assessment 留存（v4 E／§12.1；准备，不是结算）

`ControlledCaseRuntime` 仅在显式装配 `session_service` 和 `assessment_service` 时，
于原 executor 的双 owner／事件 sink callback 内，依次完成原结果留存、唯一 Session finalizer、
`ControlledCaseAssessments.freeze(owner, native=...)`。默认不装配评估；无 Session finalizer 的评估装配拒绝。
历史输入、审批等待不自动升级，main／CLI／生产 Dispatcher 未接线，默认 controlled_case_available=false。

原固定用例、模型已保存的完整回执、ToolJob／artifact／操作和 effect inventory，使用原
`AgentCaseAssessmentService` 的同一解析、归属与评估算法；不调用新模型／工具或自动纠正回执。
实际值由原 Job 输出读取，空 checks 不得通过，模型自报 passed 不是正式判定。
读原应用证明与证据、评估前后复核及最终 DB-clock 双租约检查均在同连接事务；
这里冻结证据快照，不证明全计划所有并发操作已经排空，也不授予 consumption completion 权限。

checkpoint 5 继承原 admission／结果／分项／Session proof／Memory 回执，增加严格版本化的
`ControlledCaseAssessmentAsset`，scope=`controlled_case_assessment_preparation_only`。
保留原 Verification 身份、时间、证据及无 lease 的标准 RunItemCompletion 内容/hash；
原 key／checkpoint_at／retained_at 不变。原应用 proof 的 scope/hash 不纳入新增评估。
原件摘要同时保留完整 artifact SQL 行，不能因 DTO 隐藏字段而忽略物理证据漂移。
同一事务写 checkpoint 与稳定 `test_run.case_assessment_prepared` 审计，事件明确 run_item_completed=false。
重入／load 复核原输入、native、账本、结果、应用行、证据、评估及原审计，不再生成 Verification。
缺失／冲突不覆盖，load 不创建；取消／提交后 ACK 丢失记 assessment_write_receipt_unknown，
automatic_replay=false，等待者取消不是 SQL 线程停止证明。

原件校验复用工具首发／回放的纯输出投影（原压缩、下载字段、操作元数据），
不再把投影后输出直接等同原 Job payload；仍逐字段精确比较，不忽略 artifacts／messages。
checkpoint 读者上限为5、input仍4；reader4、未知字段及version6 defer，不重写原件。
schema5下原分项／派生 Memory 回执写入保留评估，完整应用证明的 Session ACK 仍只读。

该端口尚不插入正式 TestCaseResult、不更新 RunItem／attempt／run totals、不完成 AgentTask；
原 finish(completed) 仍拒绝。后续需复用原 RunStore 的 fenced 原子结算，不能把本资产当完成许可。
业务审批继续、失租后的正式恢复、独立 case Worker 强杀、main／逐模式／真实领域与 E3/E4
仍保留为独立验收出口。测试证据与剩余范围见实施记录 §194。

### 正式 case 完成旁路封闭与原 RunStore 事务核（v4 E／§12.1）

原 `complete_item` 和 `complete_denied_approval` 现在均拒绝原 attempt 的
`checkpoint_key=controlled_case_admission`，包括已存在 result 的重复确认分支；
内存与 PG 存储保持一致。识别依据是已有精确保留 key，不解析、降级或覆盖未知 checkpoint。
旧 `agent_case_execution`／其他非受控 key 的合法完成与幂等语义不变。
通用 HTTP 完成接口沿既有 ValueError 映射返回409，不新增请求字段或绕过开关。
拒绝时记录 `controlled_case_result_atomic_writer_required`，reason 为
`controlled_case_atomic_settlement_required`，不把 lease 或原 payload 写入日志。

原 PG 完成路径抽为 `_complete_item_in_transaction`，只使用调用方 cursor，仍执行原幂等、
lease 校验及上述保留检查。原结果／item／attempt／时长／run totals 写入算法统一移入
`_complete_locked_item_in_transaction`，没有复制另一套完成算法。
后者只是与 `_write_item` 同级的内部存储原语：调用方必须持原锁并完成权限核验，
它不是可供 Worker／HTTP 调用的结算端口，不自行创建连接、commit、重试或发起执行。

这一步只封闭两个完成入口的半提交旁路并准备原事务核，不补造既存半结果的结算证明，
也不代表所有历史异常或领取／回收路径已验收。正式双 owner 结算、原子证明和终态只读 ACK
由下面的独立端口承担；业务审批继续及失租恢复仍待实现，不以 Session completed 或原 Assessment
资产替代。该前置修复本身未改变 main／CLI／生产 Worker 装配、checkpoint 版本或默认能力开关。
事务核证据见实施记录 §195。

### 正式 case 原子结算与终态 ACK（v4 E／§12.1；内部显式接线）

`ControlledCaseSettlements.complete` 在同一 PG 短事务锁原 Session、plan、RunItem／attempt、
runtime owner、AgentTask／attempt 与 run；核原输入、原生终态、模型／ToolJob、原结果资产、
应用 proof 和原 Assessment／证据后，按新 DB clock 再核双租约。
复用唯一 `_complete_locked_item_in_transaction` 写 result／item／attempt／时长／run totals，
复用原 task 状态、attempt 结束、owner 结束及 Session lease 清除算法，不串联两个独立提交。
原 failed／blocked 结论不被 Session completed 或模型自报通过覆盖。

原 attempt 的 checkpoint6 追加严格 `ControlledCaseSettlementReceipt` 与稳定
`test_run.case_settled` 审计，保留 admission、原结果、应用提交、Assessment 和派生计划原件。
证明关联原 Assessment 与完整 result／item／attempt／task／task attempt／owner／完成事件行摘要；
attempt 摘要仅归一化自引用的 receipt 外壳，不丢弃原 schema5 内容或物理未知字段。
Worker checkpoint 读者上限5→6，input仍4，默认 `controlled_case_available=false` 不变。
旧读者／未来字段或版本沿原协议 defer，不创建新领取权或降级重写。

`load` 只确认已存在的完整结算；`complete` 重入已有 checkpoint6 也只读 ACK。
它先核真实终态行和完整证明，再复用原输入／原生／应用／评估原件校验。
此窄端口不以过期 owner 冒充 live owner，不重建 Assessment、finalizer、图、模型、工具或 Memory。
缺原件、裸半结果、漂移或未知结算均不自动修复／重跑；取消等待者或提交后 ACK 丢失记录
`settlement_write_receipt_unknown`、`automatic_replay=false`，不声称后台 SQL 已停止。
普通原结果读者、Assessment、分项写者和 generic finish 仍拒绝已终态 owner。

`ControlledCaseRuntime` 仅在显式同时装配 Session、Assessment、settlement 服务时结算；
先等原 executor 的事件 sink 排空关闭，再使用捕获身份调用结算端口，并由该端口重核 DB 权限。
不在仍可写事件的 graph callback 中清 lease，不添加第二套调度或循环。
`settlement_receipt` 是可选内部结果字段；只配置原 Session／Assessment 的已有调用保持原语义。
main／CLI／生产 Dispatcher 未装配；业务审批继续、正式失租恢复与独立 case Worker 强杀仍待验收。
本步真实测试与剩余出口见实施记录 §196；不能等同全系统切换。

### 正式 case 持久交付终态只读确认（v4 E／§12.1；内部端口）

`ControlledCaseSettlements.acknowledge_delivery(delivery, capabilities=...)` 只消费原
reference-only outbox 交付，不需要旧进程的 owner／native Python 对象。
先用原严格 command／binding／input／checkpoint 读者核精确交付身份与制品兼容性，
再锁原 Session、plan／item／attempt、owner／task／attempt、run，并核 checkpoint6、
完整物理终态行摘要与唯一结算审计。没有完整原子结算即拒绝，不能从 Session completed、
schema5 Assessment、单独 result 或原生终态推断结算已成功。

身份读取仅构造不含执行 token 的 `RuntimeCheckpointSource`；通过摘要校验后，使用同连接
`BoundPostgresReader` 精确读取证明指向的原生 checkpoint／完整消息，复用原 native、
冻结输入、账本、ToolJob／artifact、结果资产、application proof 和 Assessment 校验。
原 `require_native_terminal_original` 与新 source-only 读取共享同一物化算法；
普通 live reader 仍前后核双租约，不新增公开 skip-owner 或重建 `RuntimeExecutionOwner`。

制品／运行时／状态版本检查从原 `require` 抽为 `require_runtime_artifact`，
正常 claim 的检查顺序和语义保持；ACK 不需要开启新执行开关，
`controlled_case_available=false`／`deepagents_available=false` 的兼容只读进程也可确认原件。
不兼容版本、未来字段和 reader5 仍 defer，不降级写回；本轮不新增持久格式或 DDL。

该端口不 claim／begin／续租／修复／重新裁决，不补 Memory，不执行图、模型或工具；
缺失、漂移、裸半结果和取消均保留原结果，日志 `settlement_read_unsettled`、
`automatic_replay=false`。调用者取消不证明后台只读 SQL 已停止，更不产生可重跑资格。
已有普通 claim 对 completed task 只返回终态原记录，不授予新 owner。

独立进程验收涵盖原 Assessment 后、结算事务内审计已写但未提交、提交后 ACK 前强杀：
前两者保留 schema5、无 result／结算审计，新的只读进程拒绝补结算；后者保留 schema6，
新进程只凭 settings＋交付重复确认原 receipt，原件摘要不变。本地模型返回空 checks，
正式结论仍 blocked，没有 ToolJob／artifact／工具效果，仅一次本地 HTTP；
这是显式受控服务端口的真实进程／PG／Redis 验收，不是生产 Dispatcher／资源 heartbeat
接入、正式失租后的恢复资格、实际领域 Runner 或真实供应商验收。
main／CLI／生产开关不变；详细红绿与剩余出口见实施记录 §197。

### 正式 case 失租原件与通用回收边界（v4 E／§12.1；恢复前置）

内存／PostgreSQL RunStore 的 `recover_expired`、`recover_all_expired` 只对普通条目
执行原 expire-attempt／重新排队算法。原 attempt 的精确 checkpoint key 为
`controlled_case_admission` 时，保留原 item／attempt 的身份、状态、租约、计时与完整
checkpoint，不解析、升级或丢弃其 payload；旧版本和未知未来格式同样保留。
这不是恢复成功、续租、停止回执或新执行许可，不从过期事实推断模型／工具未发生。

被保留条目不计入 `recovered_count`，同 run 的普通兄弟条目仍可正常回收、领取，
回收失败沿原 SQL 事务回滚。公开恢复响应结构不变；0 仅表示本次没有条目被通用重排，
不能据此宣称正式 case 已恢复。日志 `controlled_case_expiry_requires_reconciliation`
携原 run／item／attempt 身份、`recovery_reason=controlled_case_original_attempt_preserved`、
`automatic_replay=false`，不记录 token 或输入。

保留原件不放宽现有双租约 fence：过期旧 owner 不能写、续租或结算；Session lease
失活后，原 task 的既有失租分类仍返回 `recovery_required`，不创建新执行 attempt。
已由历史通用回收产生的身份漂移不会自动修复，其后继不能继承原 task 执行权。
合法 schema5 Assessment 不因保留而得到结算许可；schema6 仍只按原完整证明 ACK。

本步不增加 task／checkpoint 格式、DDL、恢复后继、Memory 补偿或 main／Worker 装配，
不改变生产开关。正式失租后原件对账与仅结算资格仍是后续出口，不借普通
`message_recover` 的准入伪造 case 的用户消息／阶段。实际验证与剩余项见实施记录 §198。

### 正式 case 失租 Assessment 只读原件（v4 E／§12.1；内部端口）

`ControlledCaseSettlements.read_expired_assessment(delivery, capabilities=...)` 消费原
reference-only 交付，在原 Session→plan／RunItem／attempt→owner／task／task attempt
锁序内核对严格格式、制品、完整交付／冻结输入、原双执行身份和失租分类。
只接受已有 schema5 Assessment、同一原 task／attempt／owner 的
`recovery_required`／`execution_owner_expired`；未准备、仍执行、手工待对账、
已结算、未知格式或身份漂移不自动转换为该读取范围。

读取只使用无 token 的 `RuntimeCheckpointSource`，不重建 `RuntimeExecutionOwner`。
与 schema6 终态 ACK 共用原生终态／完整消息物化、模型账本、ToolJob／artifact、
事件完成、结果资产及留存审计、application commit、原 Assessment／证据／审计校验。
不复制评估器或 finalizer，不只凭原件 ID 或摘要存在便返回成功。
兼容只读制品无需开启新执行开关；旧 reader／未来版本和字段仍 defer，不降级重写。

返回值是原 `ControlledCaseAssessmentAsset`，scope 仍是
`controlled_case_assessment_preparation_only`，不是结算 receipt 或恢复资格。
不 claim／begin／续租／重评／写结果／改终态／补 Memory／创建后继，也不运行模型、
图、工具或 Session finalizer。派生 Memory 当前是否存在不在本端口证明范围内。
新读者也不能把原 asset 传回旧 owner 绕过 live fence；原 ACK 仍要求完整 schema6。

成功日志 `controlled_case_expired_assessment_read`；拒绝／取消记录
`controlled_case_expired_assessment_unsettled`、
`recovery_reason=expired_assessment_originals_unsettled`、`automatic_replay=false`。
取消等待者不证明后台只读线程已结束，不产生可重跑许可。
未增加持久格式、DDL、公开 REST 或生产装配；真实验证与剩余出口见实施记录 §199。

### 正式 case 失租派生 Memory 只读对账（v4 E／§12.1＋A3；内部端口）

`ControlledCaseSettlements.read_expired_memory(delivery, capabilities=...)` 与失租
Assessment 读者共用同一事务内的原交付、schema5、制品兼容、双执行身份、真正失租分类
及 native／result／application／Assessment 原件校验。只构造无 token 的
`RuntimeCheckpointSource`，不复用前一次读取结果作为本次准入，不伪造 message-stage。
未准备、仍执行、手工待对账、已 schema6 结算及不兼容原件不属于此端口范围。

原 message-stage 和正式 RunItem source 复用
`read_source_memory_reconciliation`：从原冻结 plan／execution 读取稳定 request ID、
真实 Memory 完整物理行摘要及该 effect 的原 `session.post_commit_effect_failed` 事件，
沿原算法返回 `completed`／`partial`／`unknown`／`drifted`。回执存在不等于行存在；
裸行、缺行、行变化或执行状态矛盾不冒充已完成；格式／身份不兼容则拒绝，不降级重写。
返回 DTO 的 scope 仍是 `derived_turn_memory_reconciliation_only`，不是持久恢复 proof。

必需观察与派生副本按业务职责区分，不能按是否存于 Memory 服务区分。
共享 `require_application_commit` 现在同时复用原 `require_observation_projections`，
核对列入应用 scope 的必需观察原行及分项回执；缺失／漂移拒绝应用 ACK、Assessment
只读及正式结算／终态 ACK。独立派生 Memory 当前行缺失仍不阻断原应用／结算 ACK，
本端口只把其状态如实报告为 drifted／unknown，不补写、不回滚正式结果。

不构造执行 owner、不 claim／重评／运行图／模型／工具／finalizer／补 embedding，
不补偿、不写结果或终态、不创建恢复后继。兼容只读制品可关闭全部新执行开关。
成功日志 `controlled_case_expired_memory_read` 携 effect、status／reason；
拒绝／取消记录 `controlled_case_expired_memory_unsettled` 和
`recovery_reason=expired_memory_originals_unsettled`，均有 `automatic_replay=false`。
公共物理读者对 unknown／drifted 继续记录 warning。取消等待者不证明后台只读线程停止。

未增加持久格式、DDL、公开 REST／前端契约、main／Worker 装配或生产路由。
实际验证与未完成出口见实施记录 §200；只读对账不等于 A3 补偿或正式仅结算恢复已完成。

### 正式 case 失租后的仅结算后继准入（v4 E／§12.1；内部显式端口）

`ControlledCaseSettlements.ensure_recovery(delivery, capabilities=...)` 复用 §199–200
的失租 Assessment 原件读者和原 Session／RunItem／attempt／owner／task 锁序。
只有原 `case_execute` 已进入 `recovery_required/execution_owner_expired`、
schema5 原 Assessment、应用提交及必需观察等完整原件仍可核验时，才可准入。
首次写入还核对 Session 无活动租约、非 running／waiting_approval／cancelled，
Run 未 completed／cancelled、原 RunItem／attempt 仍 running 且无 result_id。
不借用 `message_recover`，不伪造用户消息或阶段，不重建执行 owner。

同一原 task／attempt 派生稳定的 `case_settle` 后继，沿已有 AgentTask／v2 outbox
和 `test_run.case_settlement_admitted` 审计同事务写入。其 schema1
`controlled_case_settlement_admission_only` 原件仅保存身份与摘要，绑定原输入、
应用提交、Assessment、原 task／attempt／owner 和 RunItem／attempt 物理事实。
原生产者终态、owner、RunItem 与 checkpoint 不修改；Session 只更新既有事件头。
Redis 只携谱系引用，不复制 Prompt／业务输入／原租约 token。

`controlled_case_settlement_admission_available` 默认 false；
`controlled_case_settlement_max_version` 的格式读者默认支持 1，但**可读不等于可写或可领**。
关闭写者后可核对已存在的同一回执；不兼容格式 TaskDeferred，原件／outbox／审计漂移
则拒绝且不补造。普通 `claim` 明确拒绝 case_settle，Dispatcher 能力检查在领取前 defer。
准入只创建 queued 后继，无 attempt／worker／执行 owner；专用领取与结算见下一节。
准入本身未新增 DDL、公开受理 API 或生产路由，受限 Dispatcher 接线见下节。旧读者不能因 wire 主版本仍为 2
就视为兼容新增 operation／引用字段；须 reader-first 或隔离消费者后才可开放写者。

成功日志 `controlled_case_settlement_admitted/reused`；拒绝日志
`controlled_case_settlement_admission_rejected` 保留错误类型／兼容性原因。
等待者取消记录 `controlled_case_settlement_admission_unknown`，
`recovery_reason=admission_receipt_unknown`，不宣称后台事务已回滚，必须复核原回执。
全部日志带 `automatic_replay=false`。实际红绿、并发／回滚和独立强杀范围见实施记录 §201。

### 正式 case 失租后的专用领取、原子结算与持久 ACK（内部显式端口）

`ControlledCaseSettlementRecovery.claim(delivery, worker_id=..., capabilities=...)`、
`renew(lease, lease_seconds=..., capabilities=...)`、`complete(lease, capabilities=...)`
使用独立的 `ControlledCaseSettlementLease`。它只有后继 Task／Attempt／Session 租约，
不是 `RuntimeExecutionOwner`，不能启动图或调用原通用 begin／finish；token 不序列化。
不接管、重置或覆盖原生产者的 task／attempt／runtime owner，也不重领原 RunItem。
复用原 Task 领取／终态事务核和 RunStore 的唯一完成核，不另建调度器或 finalizer。

领取、续租、结算每次都重新核对原交付、冻结 schema5、完整原件、准入、outbox、审计和
原物理身份；所有可能阻塞的锁／读取之后，用 PostgreSQL `clock_timestamp()` 核最终租约。
并发只产生一个有效租约；失租后仅结束旧后继 Attempt，再新建后继 Attempt，旧 token 不可结算。
后继 Task 不先改成终态再重置；已完成后继不可重领。取消和漂移拒绝，不自动重跑。

唯一事务根据原 application commit 恢复 Session completed／control（保留 scope 外键），
按原 Assessment 写 Result、RunItem／RunItem Attempt 与 totals，完成后继 Task／Attempt、
清该 Session 租约，再在后继既有 `result_payload.case_recovery_settlement_receipt` 写入
schema1 `controlled_case_recovery_settlement_only` 回执及 `test_run.case_recovery_settled`
审计。以上一同提交或回滚；原 schema5 checkpoint 内容不改写，不引入 schema7。
原失败／blocked Assessment 不升级为 passed；Task completed 只表示结算执行已完成。

`acknowledge(delivery, capabilities=...)`、原 `case_execute` 交付的
`ControlledCaseSettlements.acknowledge_delivery`、已有后继的 `ensure` 均只核对已持久化
回执与原件，不领取、不修补、不重新写结果／事件。相同租约重复 complete 也只 ACK，
旧 Attempt 租约不能冒充新 Attempt。ACK 不构造执行 owner、不运行图／模型／工具／
Assessment／Session finalizer；后续合法 Session 活动不被旧回执覆盖。

`controlled_case_settlement_available` 默认 false，领取／续租／结算需显式能力；
`controlled_case_recovery_receipt_max_version` 默认支持 1，只读 ACK 不需要打开写者。
不兼容回执字段／版本／scope defer；缺失或漂移拒绝且不补造。新字段与操作仍须
reader-first 或隔离消费者。普通图执行能力继续拒绝 case_settle；专用 Dispatcher 端口
及 main 受限装配见下节，不等于生产路由、REST 开放或领域 Runner／E3／E4 验收。

成功日志 `controlled_case_recovery_settlement_claim/renew/complete/ack`；拒绝日志
`controlled_case_recovery_settlement_rejected` 带 operation／task、错误类型及兼容原因。
等待者取消记录 `controlled_case_recovery_settlement_unknown`、
`recovery_reason=settlement_transaction_receipt_unknown`；这不证明后台事务已停止或回滚，
必须查询原回执。日志均带 `automatic_replay=false`。验证范围见实施记录 §202。

### 仅结算 Dispatcher／Worker 的受限接线

main 显式注入 `ControlledCaseSettlementRecovery(agent_task_store)`，不打开
`controlled_case_settlement_available`，不新增 CLI 启用参数。没有端口／能力或读者不兼容
时保持 TaskDeferred。只有显式授予专用 writer 的隔离验收才可领取；默认生产写者仍关闭。

`case_settle` 在 durable command／binding 核对后进入专用端口，不经过普通图
capability／claim／begin／finish，不构造 RuntimeExecutionOwner，不运行 Runtime／
模型／工具／Session finalizer。已 completed 的交付只读核对持久回执，不领取或申请资源，
关闭 writer 仍可 ACK 兼容回执。原 Task／Attempt／RuntimeOwner 的完整物理身份保留。

领取后复用原 agent 资源配额、`_monitor` 及清理逻辑。监控同时续 Redis 资源与 scoped
Session 租约，不调用 Runtime.request_interrupt。资源不足通过新内部
`defer(lease, reason=..., capabilities=...)` 原子结束本次 Attempt 为 waiting_resource、
清本次 Session 租约；资源可用后新建 Attempt，不重置原终态或复用已结束 Attempt。
续租／取消／来源漂移／提交回复丢失均不通过通用 finish/fail 改写原件。

真实 `tasks.interrupt` 根据 Task 的权限范围区分停止结算与撤销图 owner：仅结算取消只写
后继 Task／Attempt、Session 控制和审计，记录 owner_revoked=false，并保留原图 owner。
scope／task 身份保存在 Session control，重复取消只读核验；is_resumable=false，不从
共享 binding 反向获得图恢复资格。该历史 scope 不参与图 owner 的历史绑定查找；后来
新排队 Turn 的取消解析自己的 binding，不撤销原生产者。取消后不重领、不自动结算或 ACK 缺失回执。

Worker 仍在 handler 成功确认持久结果后才执行 `ack_owned`。提交回复丢失、ACK 返回0或
Redis 传输失败不撤销已提交结果，后续兼容 reader 只读 ACK。取消 asyncio 等待者不代表
to_thread 内 SQL 已停止；日志 `agent_case_settlement_receipt_unknown` 保留不确定性，
不声称回滚或自动重放。资源清理失败沿原 `agent_task_slot_release_pending` 留待清理。

实现与实际红绿／独立进程窗口见实施记录 §203。main 目前只有静态装配审计及受影响
lifespan 回归，不把它称为完整生产启动或发布验收。通用 Worker 总截止／交付所有权变化
与 SQL 在途的联合窗口仍须后续闭合；本步不改变通用重试／转移策略，也不据此开放 writer。

### 通用 Worker 总截止与仅结算交付的不确定性

后续 §204 将通用 Worker 自己的等待截止与 handler 报告的失败分开：
`redis_task_execution_deadline_unconfirmed` 带原 task／delivery／worker／stream、等待时长和
`recovery_reason=execution_deadline_receipt_unknown`。复用 TaskDeferred 保留原 PEL，
不 ACK／transfer／耗尽后死信；收拢 asyncio 任务不等于停止 to_thread SQL 或远程效果。
指标记 deferred，不记 handler error。handler 自己抛出的 TimeoutError 等异常仍沿原失败策略，
此处没有将全部领域超时改成可恢复成功，也没有禁止正常 reclaim 原 pending。

仅结算在后续投递时依然必须核对持久身份、原件与 scoped 租约；未提交的过期 Attempt
只能新建后继 Attempt，旧 token 被 fence；已提交的结果由兼容 reader 核对并 ACK，
writer 关闭时不领取、不申请资源、不改写 Result／审计或原生产者。
联合故障的实际范围和保留失败见实施记录 §204；生产 writer 继续关闭。

### 正式 case_execute 的显式 Worker 组合（§205，writer 仍关闭）

main 将 `ControlledCaseGraphAdapter`／`ControlledCaseExecutor`／`ControlledCaseRuntime`
接入原 Dispatcher；复用原 model resolver、checkpoint provider、根模型账本、RuntimeService、
SessionService、Assessment 和原子结算，不新建队列／调度器或打开生产开关。
`controlled_case_available` 默认仍为 false，生产正式用例入口的受理／选路不由此开放。

原 v2 durable identity／binding 核对后，case_execute 不经过通用 claim／begin／finish。
首次执行必须在领取前具备完整 schema-4 输入、executor／saver／模型政策、唯一 finalizer／
Assessment／结算端口与所需工具治理；不兼容或缺装配保持 pending，不分配资源。
专用 claim 原子核原 RunItem 执行归属，再复用 agent 配额与原监控，同时续资源和捕获的
Session／RuntimeOwner／原 RunItem 双归属，不调用 legacy Runtime interrupt。
Executor 仍独占 running 意图、一次性原生图与事件 sink；只由原结算回执证明可 ACK。

资源不足仍沿原 defer 结束本次 AgentTask Attempt、清 Session 租约；资源恢复后创建新
AgentTask Attempt，不重置或新建原正式执行 Attempt。失租、取消、图／投影／结算异常或
提交回执丢失不调用通用 finish/fail，不 transfer 或自动重跑。取消等待者不证明 SQL 已停止。
原 `_monitor` 回调统一为内部 `renew_owner`；仅结算保留原 settlement 日志，新执行使用
`agent_case_execution_renew_failed`。日志 `agent_case_execution_receipt_unknown`／
`agent_case_execution_deferred` 保留不确定性与 automatic_replay=false。

终态交付必须通过原 `ControlledCaseSettlements.acknowledge_delivery` 验证原件／回执，
关闭 writer 或缺少当前 executor 仍可只读确认；不重建 owner、不领取、不申请资源。
没有完整回执的终态不裸 ACK。原生审批等待尚未投影为正式业务审批，保持 running／pending，
记录 `framework_case_approval_projection_unavailable`；不宣称完成、不恢复工具或自动续跑。
完整正式业务审批与异常恢复仍是后续出口，不能据此开放生产 writer。

`tests/test_controlled_case_dispatch.py` 验证真实隔离 PG／Redis、原 Worker 和四种固定官方 SDK
对本机 HTTP 的图／工具／结果链路；工具 handler 与上游配置／检索为受控夹具。
main 的组合校验是静态 AST 审计，不能替代真实完整启动、供应商／领域 Runner、独立正式执行
Worker 强杀或 E3／E4 部署验收。最终回归、失败与边界见实施记录 §205。

### 同批原生审批的共享事务投影（§206，非正式停车回执）

绑定 Runtime 先按原治理准备全部候选，再调用 `FrameworkApprovalProjection.project_many`，
在同一个 PostgreSQL 事务保存该批 ToolJob／approval／native link／approval.created；
任一项失败全部回滚。原单条 `project` 委托同一内核，重投保留已有决定和 ToolJob，
不重置终态。空批次是经过 owner 校验的无写入操作，重复业务身份明确拒绝。

`project_many_in_transaction(cur, owner, proposals)` 供调用方组合原事务；不打开连接或提交，
调用方负责事务配置、错误回滚及业务等待／回执。两端口共用原身份与 native checkpoint
校验，在批次开始和全部潜在等待写入之后重验捕获的 owner；正式执行必须保留原 RunItem
第二租约，不能用 JSON 序列化后缺租约的 owner 绕过。线程前冻结候选，取消只表示
`approval_batch_write_receipt_unknown`，SQL 仍可能提交；不自动重跑、交付决定或继续图。
`framework_approval_projected` 只由自持事务端口提交后记录，caller-owned 端口仅记录
`framework_approval_projection_staged`／transaction_committed=false，不是持久完成证明。

本步尚未将正式 `case_execute` 的 native wait 接为业务停车：§205 的自动执行分支仍保持
running／pending 和 `framework_case_approval_projection_unavailable`。专门测试显式调用
共享投影端口，证明正式原件／双租约适用，不代表 Worker 已提交 waiting_approval、暂停预算、
获得 ACK 或后继恢复资格。完整停车事务、决定准入／交付、继续和故障矩阵仍待后续实现。
公共 HTTP／DTO、数据库 schema、依赖、生产开关不变。

### 正式原生审批的完整批次原件与专用范围（§207，仍非业务停车）

正式 `case_execute` 的非空投影批次现在必须在原双租约锁下读取持久 Task 的唯一冻结输入，
对账原 admission／计划／RunItem／执行 Attempt，再通过同一 PostgreSQL 连接上的官方
`get_tuple`／`get_state(subgraphs=True)` 读取最新原生批次。候选顺序可不同，但必须逐字段
覆盖全部当前待审批身份；遗漏、重复、参数／路径／位置漂移、未结 native error、只有
resume 而没有原生工具结果，以及试图拿历史等待冒充最新状态，均不能发布正式审批。
空投影仍是 §206 的 fenced noop，**不是**“没有其他待审批”的证明。

`require_pending_tool_interrupts_original` 只授予 `native_pending_identities_only` 的读取语义。
它复用原共享身份解析核和 `BoundPostgresReader` 的物理 namespace 映射；显式 journal
回调仅在原连接读取 pending writes，不伪造 owner，不放开 source-only saver 的审批／
写入入口，不执行图／模型／工具、不部署 checkpoint 表，也不提交调用方事务。
独立的 source-only 读取即使在过期后成功，也不能获得双归属写权、恢复或 ACK。

审批 `source=test_run_agent_execution`、project/run/item/attempt 范围由锁定的正式原件生成；
其中 attempt 指正式执行 Attempt，不是 AgentTask Attempt。候选和已有 ToolJob 的同组
范围也须匹配。已有审批缺少专用标记或范围漂移时不自动补写／覆盖人工决定，保留原件，
要求显式对账。普通 v2 决定受理还会核对相同 binding 下的持久 `case_execute` 原任务，
不依靠可变 metadata 标记决定路由；旧的未标记正式审批同样被拒绝，不能创建普通决定、
receipt、续跑任务或 outbox。已存在的错误续跑不在本步删除、改写或重新投递。

现有 Session 决定入口／普通审批进度视图仍按既有专用 source 分流；旧正式 RunItem
决定入口要求原 legacy checkpoint，不因新增标记就获得新框架继续权限。本步不接通
Session／RunItem／AgentTask 原子 waiting 状态、预算等待边界、持久停车回执、正式决定
准入／后继或 ACK。§205 自动执行遇到 native wait 仍保持原 running／pending 及
unavailable 语义，生产 writer／默认能力和公共 DTO／数据库 schema 不变。

### 正式原子停车的显式组合（§208，未接 Redis 停车 ACK）

`ControlledCaseApprovalWaits.park` 在同一 PostgreSQL 事务复用 §207 完整批次投影、
原 RunStore waiting 核、AgentTask 返回事件及 Session 元数据写入。要求捕获的双 owner、
原 ContextVar 和已 drain 的事件 sink；核原冻结输入／admission、真实 Worker claim、
原模型账本和全部 Turn ToolJob。未结模型、未知工具尾巴、控制冲突、已有人工作出决定、
已有结果回执或不完整批次不停车，不覆盖原件、不重跑外部操作。

事务将原 RunItem／正式执行 Attempt 置为 waiting_approval，停止其 active 段并开始 waiting 段，
保持原 Attempt 和 admission checkpoint1；Task／Agent Attempt completed 且 result_status=waiting_approval，
通过真实 execution_returned 事件关闭 Worker 活动尾巴，**不表示用例完成或外部进程停止**。
根活动读取同步纳入 case_execute，避免模型返回后漏计仍在运行的正式 Worker；根预算、usage、
原冻结 deadline 和 native checkpoint 不重置／修改。

Task.result_payload 的 controlled_case_approval_wait 保存独立 schema1、scope=
controlled_case_atomic_approval_wait_only 回执：全批 approval／ToolJob／interrupt 身份与 native／link 摘要、
原输入／admission／模型账本摘要、原 Worker claim／return 摘要及停车时间。无租约 token、
原参数、Prompt 或结果正文；不是 completed result asset。原 Item 的单组 approval_id／tool_job_id
只用于已有展示，不能代表全批。Session.pending_turn 保存全批 ID、原 binding 和回执摘要，
Session 置 waiting_approval，原 turn lease 四字段清除；原子审计为 test_run.case_approval_waiting。

全部可能等待的锁后重验双租约；状态与租约被清除后，最后一项写入之后再用新数据库时钟核对
同一组锁下捕获的原到期时间，超期整笔回滚。调用者取消不证明 SQL 已停止，记录
approval_wait_write_receipt_unknown／automatic_replay=false；回执丢失不得自动重复 park。
已返回的失败 ToolJob 沿原 output_observed 事实闭合，不能把应用层合法丰富的最终输出
与原始响应哈希混为一物；仅 failed／cancelled 标签不证明未知外部效果停止。

ControlledCaseRuntime 可显式注入 approval_wait_service，并只在 executor 已 drain／关闭 sink 后
组合上述事务，返回独立 approval_wait_receipt；原 state 仍是准备材料，不冒充 Session 快照或
RuntimeTurnResult。**main／Dispatcher 不安装本端口**，带该可选服务的 require_dispatch_ready 明确
拒绝尚未实现的停车 ACK 组合；默认 native wait 的 unavailable 语义不变，生产开关保持关闭。
普通决定仍被专用范围隔离，既有终态 settlement reader 拒绝把本回执当成用例结算 ACK。
专用只读停车原件复核／Redis ACK、决定准入／双归属后继、恢复和正式停车跨进程强杀仍待实现。
新增内部 Python 回执格式和可选结果字段，无数据库 DDL、公共 HTTP 或依赖变更。

### 正式停车历史原件 ACK 与 Worker 组合（§209，非决定／恢复权限）

本节更新 §208 当时的接线边界：main 现在将同一个 FrameworkApprovalProjection 实例交给
RuntimeService 和 ControlledCaseApprovalWaits，正式 Dispatcher 可识别 approval_wait_receipt。
只有完整执行组合且 Worker 支持 controlled_case_approval_wait_max_version=1 时，才允许首次领取；
默认 controlled_case_available 仍为 false，不因安装读者或 main 接线开放生产 writer。

首次执行完成原子停车后，Dispatcher 还必须调用专用 acknowledge_delivery 复核持久原件，
回执与本次返回一致才能正常返回给原 RedisTaskWorker；只有原 Worker 的 ack_owned 确认投递。
重复投递按持久 Task.result_status 显式分流，不尝试 settlement 失败后 fallback。停车日志使用
wait_sha256／result_status=waiting_approval，不伪装 result_id 或正式用例完成。旧 settlement 读者
仍不接受停车 scope；缺读者、不兼容、原件缺失或漂移保持 pending，不通用 finish／fail 或重跑图。

专用只读核沿原 reference-only delivery、冻结输入和 admission、原 Task／AgentAttempt 身份、
完整 claim／execution_returned 事件摘要、原子停车审计及全批 link／业务身份复核。
模型账本只取 created_at 不晚于原数据库停车时刻的保留调用，并与原根预算、已结记录完整摘要
比较；后来新增调用不成为旧 ACK 的材料。历史 native 按 link 的精确 checkpoint_id／namespace
在调用方同一 PostgreSQL 连接读取，复用官方 get_tuple／get_state、source-only saver、原 interrupt
journal 和来源 AI 核；不读“最新根”代替原件，不放开 saver 的决定／写入端口，不部署表或 invoke。
原件拒绝日志包含 original_stage、Task／binding 和 error_type，不记录 token、参数或响应正文。

旧 ACK 不要求当前 Session 仍 waiting、当前 RuntimeOwner 仍原 epoch、RunItem 未被合法取消，
也不要求历史 interrupt 尚未有后来 resume。它不覆盖人工决定或当前 Job 状态，不把后来的变化
还原成停车时状态。receipt 格式解析本身仍不是 ACK：缺原输入、原审计、原 batch 或历史 native
不能修复或合成。取消等待者不证明 SQL 已停止；提交后回执丢失／Redis ACK 丢失只走原件确认。

真实隔离 PG／Redis 与本机 HTTP 已覆盖首次停车、重复只读、SQL／Redis 回执丢失和取消等待者
后 SQL 仍提交，另有手工隔离后续根／resume／owner 变化及真实 RunStore 取消的历史读取测试。
这些后续材料不构成正式决定受理或恢复授权；本轮未验收正式停车独立进程强杀、真实供应商／
领域 Runner 或完整 main lifespan。普通决定入口仍隔离正式审批，专用决定准入／双归属后继与
继续执行尚待实现。无公共 HTTP／数据库 schema／依赖变更。

### 正式停车独立 Worker 强杀（§210，仍无决定／继续权限）

新增专项沿原 Dispatcher／RedisTaskWorker 验证四个进程窗口：进入停车事务前、最后停车审计
写入后但未提交、实际提交后 Python 回执返回前、原 ack_owned 之前。仅一次性执行子进程领取
正式任务并调用官方图及本机合成 HTTP；父进程只完成正式版本冻结／受理，不预先执行图。
未提交窗口没有半批审批、停车回执或正式结果；提交窗口保留原完整批次，独立只读 Worker
在 writer 关闭时核历史原件并 ACK，原投递再次入队仍只读。旧格式读者保持 pending。

测试同时核对原正式 Attempt、Task 输入／代次、全表与 native 摘要、唯一模型请求／已结账本、
零工具效果和零正式结果。读者的执行／新归属／资源获取误调用会写隔离 Redis 计数，避免
Dispatcher 的延期处理掩盖测试禁用路径。未提交失租仅沿原机制分类 recovery_required，不能
重领原正式条目或凭原生 pending 自动重跑。资源槽使用实际 TTL／reaper 回收，不借用已死
Worker 的资源凭据；SQL 租约到期采用隔离夹具的受控到期，不称为生产长时间失租演练。

这补齐了该正式停车组合的独立进程故障证据，不是实际供应商／领域 Runner、完整 main
lifespan、部署滚动／回滚或正式决定后继续执行验收。默认 writer 未改变；专用决定准入、
双归属后继与继续执行仍待实现。真实测试结果及命令见实施记录 §210。

扩大组实际发现停车提交清除运行租约与监控轮询竞态。正式 Dispatcher 的原 renew 回调现在
只有在续租返回 false 后，才读取持久 Task；必须 completed 且仍为本次 Agent Attempt／代次，
再经既有 Runtime 专用读者核回执、原件和本次 owner epoch／binding，才能设置原 stop Event
结束运行态监控。缺审计／回执／身份漂移仍失败延期，不重续终态租约、不捕获任意异常转成功。
此处只结束监控，不直接 ACK Redis，也不跳过原 execution 返回及正常回执比较；仍需原
Worker ack_owned。确定性回归同时覆盖停车和正式结算的原件完好／审计漂移／回执缺失。

### 正式审批与通用 RunItem 维护的所有权边界（§211）

原子停车后，原 `case_execute` Task 已完成的是本次执行阶段，不是正式用例。通用
`resume_waiting_approval` 不能只恢复 RunItem 租约而不恢复框架所有权，也不能将旧 claimed
记录作为幂等恢复成功返回。PostgreSQL 在原 Item／Attempt 锁内、内存实现的同一互斥区内，
统一依据不可变 `controlled_case_admission` 检查点键拒绝这两条路径；不依赖可变审批 source，
不解码／迁移未知保留版本，不重置原 Task 或原正式 Attempt。普通 legacy／其他检查点键
仍沿原恢复与幂等协议。

通用拒绝审批补偿扫描遇到该保留 Attempt 时，记录
`test_run_denied_approval_dedicated_continuation_required`，不尝试写专用 ToolJob 或正式结果，
继续处理同批普通条目。该日志只说明需要专用继续，不代表已经创建后继或交付拒绝决定。
人的拒绝仍须经过原生交付／结果证明，不能直接等同整个正式条目 blocked。

真实隔离 PG／Redis、本机合成模型 HTTP 及内存样本覆盖等待／旧 claimed、保留格式未知、
普通检查点兼容、混合补偿批次及重复扫描。正式决定受理与双归属继续仍未实现；默认
writer、公共 HTTP／DTO、数据库 schema 和依赖不变。命令、红测与最终结果见实施记录 §211。

### 正式停车决定的内部原子受理（§212，尚无领取／图继续）

`ControlledCaseApprovalSubmission` 仅供显式内部组合，未安装到公共审批路由、main 或默认 Worker。
它复用同连接的停车历史原件核，但新决定还须在 Session → plan／RunItem／原正式 Attempt →
RuntimeOwner 锁内确认当前仍是原完整停车批次、无取消／控制冲突、原生批次仍 pending、
无新增模型活动且所有本批 ToolJob 未开始。历史 ACK 的有效性不授予决定受理权。

同一事务保存人工决定、原 journal queued 回执、`case_approval` Task、identity-only v2 outbox
及两条审计事件。新 schema1 admission scope 仅为 `controlled_case_approval_admission_only`，
不能作为原生交付、RunItem 续租、正式结果或恢复证明。原生产 Task／AgentAttempt、正式
RunItem／Attempt／输入预算、原生 checkpoint 和工具状态不重置。拒绝决定也不直接结算用例。

相同决定重试只核原 admission／journal／outbox／审计并返回原 Task，保留原理由和时间；
反向决定或原件漂移明确失败，不补造丢失事实。取消等待者不证明 SQL 回滚；提交后回执
丢失记录 `decision_admission_receipt_unknown`，不自动重跑，显式相同重试才能确认持久原件。

新增格式读者与执行权限分开：旧格式读者不能受理，普通 Task.claim 明确拒绝 `case_approval`，
默认 Dispatcher 在 claim 前延期；即使正式首次执行 writer 打开也不放行决定后继。前端
AgentTaskRecord operation 类型同步，但公共审批入口与生产 writer 均未切换。
双归属后继领取、逐项原生决定交付、B 执行期间受理 A、继续与强杀恢复仍待后续子步。
本步无数据库 DDL／依赖升级；实际测试及证据范围见实施记录 §212。

### 正式审批后继的双归属领取与启动（§213，尚无原生决定交付）

`ControlledCaseApprovalContinuations` 仅供显式内部组合。新能力
`controlled_case_approval_claim_available` 默认 false，claim 与 begin 均须显式启用；
它不改变普通能力检查，默认 Dispatcher／普通 Task.claim 仍不能执行 `case_approval`。
公共审批路由、main 与生产 writer 没有安装或启用这个组合。

领取在原 Session → plan／RunItem／正式 Attempt → RuntimeOwner 锁序内，重新核对停车、
决定受理、原 outbox、完整 pending/native 批次、未开始 ToolJob、原模型账本与根期限。
只领取尚无执行历史且 journal 仍 queued／未交付的原 Task；同 Session FIFO 串行，其他
Turn 的排队新消息不阻塞当前审批。重复投递、失租 claimed/running 或丢失回执不自动接管、
重领或重跑命令；取消等待者不代表后台 SQL 已回滚。

后继建立新 AgentAttempt 和 owner epoch，同时轮换正式 RunItem／原 Attempt 的当前
worker／lease capability；原正式 Attempt ID、attempt_no、started_at、冻结 admission
checkpoint 和原 `case_execute` Task／AgentAttempt 保留。关系租约列与 JSON 原件同写。
live fence 从持久 admission 找到原 reservation，不信调用方提供的原 Task 提示，任一
租约、owner 代次或捕获身份失效即拒绝。审计只保存前后租约摘要，不输出凭据。

专用 begin 复用 Task 状态事务及 RunStore 时长内核，原子提交正式 Attempt／Task／Session
running 并返回原冻结输入；最后写入后仍核双租约和原根期限。通用 RunStore start／resume、
普通 Task.begin／defer／completed 不能拆开双归属；原普通非保留 Attempt 的启动不变。
新后继纳入原 Worker 活动计费，但未触发图、模型或工具，也不修改 queued 决定回执。

这不是正式审批闭环：逐项原生决定交付、运行期间受理兄弟决定、后继重新停车、后继结果
资产谱系／唯一正式结算、独立进程强杀与真实供应商／领域验收仍待完成。当前初始停车
领取不承诺重新停车后的第二项推进。无公共 DTO／DDL／依赖变更；实测及限制见 §213。

### 正式后继的单项原生决定交付与事实对账（§214）

内部 `ControlledCaseExecutor` 的原 sink／owner scope／图调用边界现在可显式组合
`ControlledCaseApprovalContinuations`。只有独立的
`controlled_case_approval_delivery_available` 和领取能力同时打开，才可启动后继并
交付一个原决定；两项能力默认均关闭。普通 Dispatcher、公共审批路由和 main 未安装
这套组合，首次正式执行能力不隐含后继交付权限。

发送前重新读取完整受理／停车／outbox 原件、捕获的 Task 身份、双租约、当前完整
pending/native 批次、未开始 ToolJob、原模型账本与根期限，同事务写原 journal sending
和 `test_run.case_approval_sending`。最后写入后再次用 DB 时钟检查双租约和根期限。
统一适配器只收到一个 interrupt 的 `Command(resume)`，不发送其他审批，也不自动传
None 再继续。原 Fenced saver 在同事务提交原生 `__resume__` 和 delivered 证据。

sending／回执未知不能再次 prepare 或重跑。显式 `reconcile_delivery` 复用普通审批的
原生决定／ToolJob 读核、journal 和幂等交付审计；必须证实决定已持久消费，ToolJob
效果已知安全，才可保留首次交付证明或恢复缺失投影。原生事实缺失、决定／证明漂移或
已启动但效果未知均失败，不补造事实。对账仍核当前捕获身份与双租约，但不把历史原件、
变化后的 pending 清单或根预算变成新的执行资格；返回 plan 不授权执行图、模型或工具。

当前验证出口是单项原生交付：批准只运行被选中的合成 handler，拒绝不运行 handler；
其余审批保留原身份，批次未闭合不调用下一轮模型。实时权限撤销仍由原工具执行边界治理。
原正式 Attempt、冻结输入／预算／checkpoint 与原生产 Task 不重置。

这不是审批闭环或用例通过：本步不写 applied、不完成后继 Task、不重新停车、不生成
正式结果或结算，也不支持在执行中受理兄弟决定。后继仍 running，需要后续子步完善这些
生命周期和恢复出口；不能据此开启生产。独立进程交付强杀、真实供应商／领域 Runner、
E3／E4 仍待验收。无公共 API／DTO、DDL、依赖或生产选路变化，实测结果见实施记录 §214。

### 正式后继的单项工具结果应用（§215）

内部 `ControlledCaseApprovalContinuations.apply_decision`／`read_tool_projection` 复用现有
delivery＋claim 能力组合，两项仍默认关闭；没有接入默认 Dispatcher、main、公共 HTTP
或首次正式 Runtime 的结果 finalizer。它们不调用图、模型或工具，不完成后继 Task／
RunItem，不授予 replay／继续许可；applied 仅表示这一项已知工具结果的应用事实。

先在同一连接核原正式 admission／停车／outbox、完整捕获身份与当前双租约，再核原
decision／native 消费及已知安全 ToolJob。普通审批的原 `_settle` 结果应用体提取为共享
同事务内核，正式路径调用该核而非伪装普通 approval_resume Task。交付对账、人工拒绝
Job、application proof 与幂等 `approval.decision_applied` 审计在同一事务；最后严格读回
原投影材料，再用 DB 时钟核双租约。原材料缺失、漂移或最终读取期间失租均整体回滚。
普通历史审批保留原有兼容语义，不因正式新路径要求而补造材料。

人工拒绝首次应用须显式提供既有纯同步投影策略；该策略只处理锁内已知事实，不得 I/O、
执行或授权重跑。重复只读原 applied 材料，不重新运行投影器。批准从原 native artifact
读取材料；治理拒绝仍是未执行事实而非成功效果。已过根期限可以核已知历史结果，但不
延长预算或获得执行权。取消等待者／提交后丢回执不证明事务回滚，不自动重跑。

初始正式停车尚未持久化完整应用 waiting state／tool slots；批次排序不是原 UI ordinal。
本步拒绝正例采用单材料夹具显式显示策略，不能作为生产顺序或完整 Runtime 状态恢复
证明。等待 state／slot 原件、正式继续与重新停车、后继结果谱系／唯一正式结算、运行中
受理兄弟决定、独立强杀及 E3／E4 仍待实现或验收。无公共契约、DDL、依赖或生产选路变更，
实际红绿与受影响回归见实施记录 §215。

### 正式初次停车的原应用等待材料（§216）

兼容读者现在理解原 RunItem Attempt checkpoint **7** 和停车 receipt **2**；
`controlled_case_approval_state_available` 独立控制新写入，默认 false，main／CLI／
Dispatcher 未启用它。旧 checkpoint 1／receipt 1 保持原格式，读取时绝不补造历史 state。
旧读者 max checkpoint 6 或 max wait 1 遇到新格式，在取得后继双 owner 前延期。
checkpoint 7 是等待分支，不是 result checkpoint，不授予完成／结算或重跑权限。

显式 Runtime 组合在原事件 sink 已排空后，将原完整 application waiting state、工具槽位、
原生身份／消息、已结算工具材料、根模型账本材料、审批实际行头和 ACK 事件引用保留到
**原正式 Attempt**，不新建表、不重置 Attempt。停车、材料、RunItem／Task 转换及审计同事务，
全部写入后仍核原三项租约截止。公开 Task.result_payload 只保存 receipt 和 state 摘要，
不把完整 state 放进 Task 回执；现有 RunItem checkpoint 的访问边界不在此改变。

复用普通审批的 waiting record、原 ToolJob／artifact 输出读核和 runtime tool message 投影器。
已完成槽位保留捕获时的 completion ordinal／原字符串；**首次新建** waiting 槽位显式按
source-AI tool_calls 顺序追加并持久化。这不是从 UUID、时间戳或 SQL 行序猜历史 UI 顺序。
原始基础消息来自指定 checkpoint，已完成 pending task 的结果单独读取和核验；不将
LangGraph 最新状态自动合入 pending writes 的便捷投影当作不可变历史原件。

历史 `read_state`／receipt ACK 同连接重核生产者、admission、原 Worker 边界、完整批次、
原模型账本、历史 native 和确定性应用投影；不依赖当前可变 pending 清单重建旧 state。
后续单项拒绝／领取或新 task error 不把旧材料改成新结果，也不授权继续执行。材料丢失／
漂移、ACK 事件缺失、重算 envelope 摘要但改了来源／槽位，均拒绝且不自动修复。

本步仅补齐**初次停车材料**：专项实际覆盖混合已完成＋待审批、真实 B 项拒绝使用原
第二槽位、Runtime 显式返回同一材料、原子回滚、失租、取消等待者与丢回执。未实现正式
后继 Runtime 完整继续／重新停车、运行中兄弟决定受理、后继结果谱系和唯一正式结算；
没有新格式独立进程强杀、真实供应商／领域 Runner、E3／E4 或生产开放验收。§215 首次
扩大回归两项账本拒绝仍保留为未确认根因的稳定性风险。命令与真实结果见实施记录 §216。

### 原路由与恢复验证

关闭新选路时记录 `deep_agent_new_turn_route_disabled`，带 `session_id`、`turn_id` 和 `recovery_reason=new_turn_admission_disabled`；这是路由决定，不是取消/停止回执。

- `tests/test_framework_route_rollback.py`：配置、实际 Runtime 选路、绑定矩阵、在途调用、请求覆盖负例，以及真实 CLI/dispatcher 搭配替身队列的能力准入。main 的接线审计为静态 AST 检查，不替代完整 lifespan。
- `tests/test_framework_approval_runtime.py`：固定官方框架和真实隔离 PostgreSQL/Redis 下，关闭新接入后重建 Runtime，仍保留审批重放、后继、工具效果次数和原消息身份。该开关场景本身不是独立进程部署验收。
- `tests/test_deep_agent_runtime_adapter.py`：原无绑定 pilot 在途审批的批准、拒绝、范围改变与混合批次；官方框架、内存 saver、受控工具，不是实际 CLI 工具或供应商验收。
- `tests/test_agent_v2_worker_contract.py`、`test_runtime_binding_persistence.py`、`test_framework_task_control.py`、`test_framework_approval_recovery.py`：沿既有版本/绑定、停止和恢复契约回归；具体命令、实际结果及证据限制见实施记录 §106。

实现依据是当前 `src/application/runtime/runtime_service.py`、`src/main.py`、`src/cli/run_agent_v2_worker.py` 和 `src/runtime/framework_worker_capabilities.py`。不新增调度层，不把路由权限当成外部执行停止证明。
