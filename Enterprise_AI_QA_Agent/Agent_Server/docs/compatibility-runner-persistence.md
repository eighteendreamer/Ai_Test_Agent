# 兼容性 Runner 状态持久化

`CompatibilityRunnerService` 使用配置的 `data_dir/compatibility/runner_state.json` 保存 Runner、任务和工件索引。临时文件写入后替换正式文件，替换成功才推进内存中的已提交快照。HTTP 请求只有在本次变更成功保存后才返回成功。

写入、序列化或替换失败时，服务恢复上次已提交的 Runner、任务和工件索引，记录 `compatibility_runner_state_save_failed`，API 返回 **503** 和固定错误正文。上传索引提交失败沿用已有工件补偿删除；补偿失败记录 `compatibility_artifact_cleanup_failed`，原始提交错误仍返回给调用方。临时文件清理失败也单独记录，不删除目录或正式文件。

503 表示状态没有提交成功；重新提交结果与重新执行工具是不同操作。工具已有外部副作用时，需要对账并保留原结果，不应因为收到 503 就重跑工具。新版 CLI 已保存确定的待提交报告，使用领取身份与幂等回执重传；未知执行/旧协议上传结果仍需核查。

缺失状态文件允许初始化空服务。存在但无法读取、JSON/编码损坏、未知 schema、集合格式或记录不合法、重复身份的文件会触发 `CompatibilityRunnerStateError`，阻止初始化并保留原文件。日志仅记录类型、系统错误号及数量，不复制结果、元数据或验证失败的输入值。恢复前应保留原文件，核验备份及外部任务实际状态；服务不会自动清空损坏文件或把无法确认的执行结果标成成功。

该实现仍是单服务实例的 JSON 存储：没有新增跨进程锁、数据库事务或掉电持久化保证。内存保留一份已提交 JSON 值用于撤销失败保存，历史保留/大目录容量和多个进程共享同一路径仍需单独治理。

心跳过期、Runner 记录缺失和服务启动恢复发现未完成的 `assigned` 任务时，直接进入 `recovery_required`，原因是 `assignment_outcome_unknown`。任务已派发就可能到达外部执行器，缺少模式调用开始标记不证明未执行。服务器不再因预算尚有余额自动重新入队，也不把中断改成工具成功或失败。

自动待核查保留原 Runner、assignment、任务定义、结果、工件、错误、模式调用执行元数据、已有 `retry_history` 和次数，不归档、不清空、不生成新 assignment。记录 `recovery_source=restart/heartbeat` 与核查时间；缺失 Runner 归入 heartbeat 回收路径。原领取只用于证据关联，不是继续写入或执行许可：报告、上传、模式调用开始/完成均拒绝，上传的外部存储 await 后也重新核查。其他排队任务仍可领取，重复读取/服务重建不反复改写待核查记录。报告与前端明确提示核查原 Runner 日志、目标状态及副作用，只有显式包含 `recovery_required` 的人工 requeue 才沿原入口归档并重排队；默认仅重跑失败任务不会包含它。该入口不自动查询目标，不证明已经对账或停止外部执行。

原 `orchestration.compatibility_runner_max_automatic_retries` 配置与环境变量 `ORCHESTRATION__COMPATIBILITY_RUNNER_MAX_AUTOMATIC_RETRIES` 继续兼容读取，默认 3、最小 0，不改变设置 schema；上限只保留为历史诊断信息，**任何值都不授权重派未知 assignment**。已有 `automatic_retry_count` 不递增或重置，缺此字段的旧记录保持缺省，不从最近十条历史猜测次数；人工重排队仍按旧格式归档、累计 `retry_count` 并重置兼容计数。原 `automatic_retry_budget_exhausted` 待核查记录仍可读取且保留其原因，不改写成新历史。预算和核查字段归服务器所有，派发元数据/Runner report 不能覆盖；正式 Settings 读取 orchestration，简化平面设置仍受支持。单服务 JSON 并发限制、跨机器 Runner 排他、目标对账与停止回执仍未解决。

自动待核查留下 `compatibility_task_recovery_required_staged`，记录 task、Runner、assignment、原因、来源与兼容计数；该事件表示暂存，持久化失败仍由既有保存错误及回滚日志说明，不冒充已提交。待核查写入被拒绝时留下 `compatibility_task_recovery_write_rejected`。沿现有日志 formatter 的标量白名单输出诊断，不打印任务正文、结果、原始 metadata 或凭据。

每次 poll 领取在任务 metadata 中生成新的 `assignment_id`。本仓库 Runner 注册时声明 `assignment_fencing_version: 1`，报告和上传 JSON 使用顶层 assignment_id，模式调用请求使用同名查询参数；服务器对这些领取强制核对身份，缺失或旧值返回 409，外来报告 metadata 无权修改身份或降级校验。领取身份用于区分执行轮次，不替代 API 鉴权。

上传在外部存储 await 后再次核对开始时捕获的领取身份与待核查状态，换轮或转待核查则拒绝挂入任务并尝试补偿删除。模式调用完成使用入口捕获的身份，旧完成回执不能更新新一轮或待核查元数据，拒绝情况留下日志。只有人工重排队将原身份记入历史，下一次 poll 产生新值；自动中断保留原身份，不授予新执行权。

旧 Runner 未声明协议时，缺失身份的普通写入继续兼容；显式提供旧身份仍会被拒绝。兼容缺失身份的旧报告无法完全隔离，需升级旧部署的 Runner。即使旧客户端不发送身份，本服务上传的异步提交及模式调用完成仍使用服务器捕获的身份复核。新版 CLI 的恢复阶段见下文；旧客户端和未知副作用仍需单独处置。

携带领取身份的报告保存规范化请求 SHA-256 回执。相同身份、相同内容的再次提交只返回已保存终态，不改文件、心跳、结果或工件索引；旧身份、终态后的不同内容仍返回 409。规范化字段使用请求 DTO，状态别名先按既有规则归一化，JSON 对象键排序；不是只按 summary 判断相同。没有身份的旧报告继续按原终态规则拒绝重复。回执随当前任务持久化，重新排队时清空，不允许下一轮复用。新版 CLI 使用该契约恢复 report_ready 阶段，不重新执行工具或上传日志。

在 `Agent_Server` 目录运行：

```powershell
uv run --no-sync python scripts/run_test_quality_gate.py --profile targeted --target tests/test_compatibility_runner_persistence.py --target tests/test_compatibility_runner_service.py --target tests/test_compatibility_mode_skills.py
```

持久化用例使用独立临时目录，分别检查坏文件、临时写入失败、替换被拒绝和无法序列化的值。它们是受控健壮性契约，不是生产磁盘事故复现；正常注册、领取、报告、人工重试和上传的既有用例同时验证成功路径。错误前后文件、内存、API 状态及上传补偿均有检查。

领取身份回归通过普通人工重试后同一 Runner 再领取、提交上一轮请求验证隔离，不替换时钟或抛执行异常。CLI 测试以成功 ASGI 传输调用正式路由和服务、使用 dry-run 和空模式调用；另有受控存储等待竞态，不称为真实 RustFS 故障或完整外部执行链路。

依据：项目现有 `MCPServerStore._load_catalog` 的损坏文件明确报错策略、`settings._DataImportBodyLimitRoute` 的路由处理模板，以及本服务既有临时文件替换和上传补偿实现。公共 DTO 字段与 URL 保持不变，新增 503 错误行为。

新版 CLI 默认在当前工作目录 `.compatibility-runner/<服务器与 Runner 身份哈希>.sqlite3` 保存本地事务日志，可用 `--state-file <稳定的本地绝对路径>` 指定。恢复必须使用相同路径、服务器 URL 和 runner_id；更换目录/URL不会自动搬迁日志。SDK 构造函数新增可选 state_file，省略时只保留当前进程状态；调用结束须 close，CLI 在 finally 中关闭。无需新依赖，使用 Python 标准库 sqlite3。

领取批次一次事务入库，逐项记录 queued → executing → executed → uploading（旧协议）/uploading_idempotent（已协商）→ report_ready。执行前提交 executing；得到结果后先保存完整 executed 结果，上传成功后先保存完整报告，再发 report。报告确认成功才删除本地待办。报告响应丢失/503 后，客户端重启原样重传 report_ready，只有服务器当前领取仍允许写入或已有匹配终态回执时才能确认；不会再次执行或上传。服务器重启/过期已将领取转为待核查时返回 409，本地保留 conflict 与原报告，不自动提交或抹除证据。run_once 返回本次确认报告的数量，待核查/冲突不计成功。此前无 dispatcher 的内部续跑队列不在本客户端修复范围内。

恢复 queued 项前查询正式任务列表核对当前 Runner、assigned 状态和领取身份；已换轮或已转待核查则保留 conflict，不执行旧任务。404/409 的报告/上传冲突也保留本地证据和原因。executing 或旧协议 uploading 被中断时，客户端不能证明副作用/远端工件是否已提交，转本地 review；保留原结果，不伪造 failed、不重传无幂等身份的上传。review 不影响同批其他任务；同一 task_id 的后续领取也暂停，防止旧部署自动换轮绕过未知结果。review 是本地阶段，不等同于服务器 recovery_required；服务端自动中断治理也不依赖原客户端仍在线或共享同一日志。未知工具副作用对账及证据化人工处置接口仍未完成。核查前应保留数据库与关联证据，不应通过更换日志路径来绕过。

日志身份包含版本，身份不匹配、坏 SQLite 文件或非法阶段拒绝启动并保留原文件。`.sqlite3.lock` 连接持有 SQLite 排他事务至 close/进程退出，同一路径的另一个进程不能操作日志；主数据库仍可逐次提交阶段。不要删运行中的锁文件；只支持有可靠文件锁的本地磁盘，网络共享、多机器同 Runner 身份、多路径启动没有全局排他保证。数据库含任务与完整结果，应放在 Runner 专用私有目录并按部署权限管理。确认成功项从本地移除，未知/冲突项保留，长期归档和容量治理仍需后续实施。

验证包括正式 API 提交后受控丢弃 report/upload 响应、关闭并重新打开客户端、剩余批次恢复，以及两个真实 Python 进程竞争同一日志锁。写入 executing 后正常退出的子进程仅验证阶段持久化和操作系统释放锁，未执行工具，不能称为真实进程崩溃事故。普通人工重试后恢复旧 queued 项验证身份核对；坏文件和不完整批次是受控健壮性契约。未验证掉电、网络盘、真实供应商或长时间运行。

SQLite 使用依据：[Python 3.11 sqlite3 文档](https://docs.python.org/3.11/library/sqlite3.html)的事务提交、参数绑定和连接关闭接口，以及 [SQLite 事务说明](https://www.sqlite.org/lang_transaction.html)的 BEGIN EXCLUSIVE。项目参考为服务端持久领取身份/报告回执和正式任务 outbox 的提交顺序；Runner 客户端此前没有独立持久日志实现。

新版工件上传请求新增可选 `upload_id`（1–128 字符），必须与 `assignment_id` 一起提供。缺少领取身份返回 400。服务器按当前任务、领取身份和上传 ID 查持久工件回执；规范化全部请求字段并以解码后内容的 SHA-256 参与摘要。同身份同请求返回原工件，不再次存储或写索引；相同上传 ID 的不同内容/标签/元数据等返回 409。缺省 upload_id 的旧请求保持原行为。报告内嵌工件和上传 metadata 不能覆盖服务器维护的上传回执字段。

上传先校验当前 Runner/领取、查回执；存储 await 后重新核对领取、待核查状态和回执。两个请求同时存储时，只有先提交者挂入任务，另一个返回已有回执并补偿自己的对象；每次对象存储前缀包含该请求的 artifact_id，避免删除已挂接的同名文件。旧对象 URI 无需迁移。回执在终态文件中保留；服务器重启把未完成 assigned 领取保留为待核查，原身份的写入也被拒绝，不因为匹配上传 ID 绕过恢复或身份检查。进程在对象存储成功但索引保存前退出仍可能留下孤儿对象，未声称跨存储原子提交。

新版服务在注册响应 metadata 声明 `artifact_upload_idempotency_version: 1`，客户端只依据服务器声明启用，不把用户 metadata 中同名值发送出去。确认支持后，Runner 日志上传固定使用本次领取内的 `runner-log-v1` ID，并将上传前阶段保存为 uploading_idempotent。响应丢失后，重启先注册协商，再从保存结果重传同请求，得到原工件后持久化最终报告并提交；不会重跑工具。旧服务未声明能力、无领取身份、原日志为 uploading、或当前服务不再支持时保持 review，不推断重传安全。SDK 在处理任务前应 register；回退旧客户端不能读新的幂等上传阶段，将拒绝而非盲重放。

普通相同请求、不同内容冲突、终态文件重载与旧身份拒绝，以及受控上传提交后丢弃响应/客户端重开均有回归。并发存储等待使用隔离替身验证两个不同 URI 及只删除未挂接者，不是一次真实 RustFS 故障验证。未知工具执行、旧协议上传、人工恢复接口、长期证据归档、跨机器 Runner 排他与多进程 JSON 存储仍需后续治理。

新版领取请求增加三个可选查询参数：`request_id`、正整数 `sequence`、`epoch`，三者须一起提供（缺项 400）。limit 仍为 1..20。服务器注册响应声明 `poll_request_idempotency_version: 1`、当前 poll_epoch 和 poll_next_sequence；客户端过滤用户 metadata 中这些服务器字段，收到声明后才启用。SDK 的 poll_tasks 参数和前端 pollCompatibilityRunnerTasks 的可选 receipt 参数已同步。

每个 Runner 保存最新一份冻结批次回执（含空批次），与任务领取一起提交 JSON。相同 epoch/sequence/request_id/limit 返回该冻结响应，不再次派发；心跳过期回收可能另行持久化待核查状态，回执重放本身不创建新领取。同序号不同请求/参数、过期序号、越序或旧 epoch 返回 409。下一请求须为最后序号加一，以顺序号避免被淘汰的旧 ID 重新变成派发请求，不保留无界的请求历史。重注册、heartbeat 不能覆盖回执和代次；回执非法明确返回状态错误并保留原状态。服务重建保留原回执，未完成领取则保持原 assignment 并转待核查；客户端收到冻结旧快照后仍核对当前 assigned 状态与身份，不能借回执重跑旧尝试。正常清理离线 Runner 后重新注册生成新代次，旧请求不用于新 Runner。

客户端发请求前将 UUID、limit、sequence 和 epoch 保存至 SQLite pending_poll。响应收到后，整批 queued 项入库与 pending_poll 移除在同一事务；任何失败保留原 pending_poll，不执行半个批次。响应丢失后客户端重开并注册，继续使用原请求的完整四字段，不能按新注册响应中的下一序号跳过未确认领取。收到整批并成功入库后才推进本地下一序号。旧服务器无能力声明维持原协议，不能恢复早先未返回的领取；若已有新协议 pending_poll 而当前服务不支持，则明确停止并保留日志供核查，不退回无身份请求。

验证：普通重复领取/重注册、同请求文件不变、不同参数冲突、heartbeat 不能覆盖回执、空批次重传、过期请求不能领取新任务、服务重开和离线 Runner 清理重建；另有正式 poll 提交后受控丢弃响应及 SQLite 非法批次事务检查。后两项是受控恢复/健壮性契约，不是生产网络事故。仅支持同 Runner 单个未确认领取、同一稳定本地日志；多机器/多日志同身份会产生序号冲突，服务端多进程 JSON 并发仍不安全。恢复日志不会清理未知执行证据，缺失日志或旧协议的历史领取仍需对账。依据为既有报告/上传回执、本地 RunnerJournal 事务及 `Agent轨迹与协议鲁棒性.html` 的幂等/补偿原则。

## 本地 journal 与服务端报告的只读对账

`CompatibilityRunnerClient.reconcile_journal()` 读取每个本地 entry 对应的 `GET /api/v1/compatibility/tasks/{task_id}/snapshot`。新端口在现有服务锁内读取已提交任务并返回深拷贝，不执行心跳过期回收、不分配新 assignment、不写 JSON。普通 `/tasks` 列表仍保留原有回收行为，**不得作为对账查询的 fallback**。新端口沿用既有 API 访问控制，不扩张 Runner 的写权限。

对账先核对原 task/dispatch/plan/environment、固定用例/模式调用、assignment 与 Runner，再用报告提交路径共享的状态归一化、DTO 摘要、结果投影和 metadata 规则核对原报告。`completed/report_verified` 仅表示本地原报告与服务端当前终态回执及投影匹配；`server_status` 仍明确保留 `completed/failed/cancelled`，失败报告核对成功不等于测试通过。工件仅核对索引归属与结果投影，未读取或验证其实际字节。

本地报告缺失、服务端回执缺失、非终态或 snapshot 404 均保留 `unknown`；原身份、固定定义、回执或结果漂移为 `drifted`。404 也可能来自尚未升级的服务器，不能推断旧任务没有执行。HTTP 503/网络错误原样传播；HTTP 200 的非法正文明确报协议错误，只记录类型，不输出私有输入/结果。查询不删除 entry、不改 phase，不把任何分类变成执行、重试、上传、报告或 ACK 权限。

在 `Agent_Server` 目录使用原服务器 URL、原 Runner 身份和原私有 SQLite 路径运行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m src.application.compatibility.runner_client `
  --base-url <原服务器URL> --runner-id <原Runner身份> `
  --state-file <原SQLite绝对路径> --reconcile-only
```

CLI 不注册、不发心跳、不领取任务；每个 entry 输出一条只含身份/状态/摘要的 JSON。要求日志文件已存在，不能以新空日志替代原证据；`--once` 与 `--reconcile-only` 互斥。仍使用既有本地排他锁，应在原本地客户端释放锁后查询，不要删除锁文件；这不证明远端进程或外部副作用已经停止。SDK 未传 `state_file` 的进程内 journal 仍可调用只读方法，但不能获得跨进程证据。

CLI 沿用原 `_json_request` 传输，本步没有新增 Bearer 凭据配置能力；启用全局 Bearer 认证的环境会将未授权请求以 401 原样拒绝。不能为运行此命令关闭认证、改用普通列表或绕过网关；这些部署仍需通过既有授权 API 调用方式读取 snapshot。

测试包含真实 SQLite/JSON 文件、正式 ASGI API、提交后受控丢弃报告 ACK、以及在 `executing` 提交后真实终止本地 journal 子进程。强杀子进程不执行领域工具，ASGI 传输不代表真实供应商/浏览器/压测/扫描 Runner。新增受控本地文件副作用与结果丢失测试，验证服务重建、心跳过期/Runner 缺失后，另一 Runner 与独立日志不能再次领取该任务，原 journal 仍可只读核对为 unknown；它不是真实领域 Runner 验收。外部执行结果查询、旧执行的非权威迟到观察、有依据补偿及停止回执仍未完成；本接口不把这些缺口变成已验收能力。
