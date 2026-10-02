# 兼容性 Runner 状态持久化

`CompatibilityRunnerService` 使用配置的 `data_dir/compatibility/runner_state.json` 保存 Runner、任务和工件索引。临时文件写入后替换正式文件，替换成功才推进内存中的已提交快照。HTTP 请求只有在本次变更成功保存后才返回成功。

写入、序列化或替换失败时，服务恢复上次已提交的 Runner、任务和工件索引，记录 `compatibility_runner_state_save_failed`，API 返回 **503** 和固定错误正文。上传索引提交失败沿用已有工件补偿删除；补偿失败记录 `compatibility_artifact_cleanup_failed`，原始提交错误仍返回给调用方。临时文件清理失败也单独记录，不删除目录或正式文件。

503 表示状态没有提交成功；重新提交结果与重新执行工具是不同操作。工具已有外部副作用时，需要对账并保留原结果，不应因为收到 503 就重跑工具。新版 CLI 已保存确定的待提交报告，使用领取身份与幂等回执重传；未知执行/上传结果仍需核查。

缺失状态文件允许初始化空服务。存在但无法读取、JSON/编码损坏、未知 schema、集合格式或记录不合法、重复身份的文件会触发 `CompatibilityRunnerStateError`，阻止初始化并保留原文件。日志仅记录类型、系统错误号及数量，不复制结果、元数据或验证失败的输入值。恢复前应保留原文件，核验备份及外部任务实际状态；服务不会自动清空损坏文件或把无法确认的执行结果标成成功。

该实现仍是单服务实例的 JSON 存储：没有新增跨进程锁、数据库事务或掉电持久化保证。内存保留一份已提交 JSON 值用于撤销失败保存，历史保留/大目录容量和多个进程共享同一路径仍需单独治理。

心跳过期和启动恢复共用 `orchestration.compatibility_runner_max_automatic_retries` 自动预算，环境变量为 `ORCHESTRATION__COMPATIBILITY_RUNNER_MAX_AUTOMATIC_RETRIES`，默认 3、最小 0。0 表示中断后不自动重新入队；预算是自动恢复次数，不含首次领取。人工明确重排队开启新的自动预算，累计 `retry_count` 继续保留。未有 automatic_retry_count 的旧文件从 0 开始统计，不凭最近十条历史猜测全部过往次数。

预算耗尽进入新状态 `recovery_required`：保留上一轮 Runner 身份、结果、工件和模式调用状态，记录原因、来源、次数和上限；不会归为工具执行成功/失败，也不会继续被 poll 领取或再次开始模式调用。报告和环境状态显示待核查，前端列出数量并提示核验副作用；人工 requeue 请求需明确包含该状态。其他排队任务可继续领取。人工重试会按既有历史快照方式归档本轮，再清空当前执行字段。

预算和核查字段归服务器所有，派发元数据/Runner report 不能覆盖。正式 Settings 的预算与心跳超时均读取 orchestration；简化兼容调用的平面设置仍受支持。预算只限制现有自动恢复，不保证剩余预算内的重复执行一定无副作用；旧部署协议升级和供应商对账仍需后续处理。

每次 poll 领取在任务 metadata 中生成新的 `assignment_id`。本仓库 Runner 注册时声明 `assignment_fencing_version: 1`，报告和上传 JSON 使用顶层 assignment_id，模式调用请求使用同名查询参数；服务器对这些领取强制核对身份，缺失或旧值返回 409，外来报告 metadata 无权修改身份或降级校验。领取身份用于区分执行轮次，不替代 API 鉴权。

上传在外部存储 await 后再次核对开始时捕获的领取身份，换轮则拒绝挂入新任务并尝试补偿删除。模式调用完成使用入口捕获的身份，旧完成回执不能更新新一轮元数据，拒绝情况留下日志。人工/自动重排队将原身份记入历史，下一次 poll 产生新值。

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

领取批次一次事务入库，逐项记录 queued → executing → executed → uploading → report_ready。执行前提交 executing；得到结果后先保存完整 executed 结果，上传成功后先保存完整报告，再发 report。报告确认成功才删除本地待办。报告响应丢失/503 后，重启原样重传 report_ready，服务器复用当前领取的幂等回执；不会再次执行或上传。run_once 返回本次确认报告的数量，待核查/冲突不计成功。此前无 dispatcher 的内部续跑队列不在本客户端修复范围内。

恢复 queued 项前查询正式任务列表核对当前 Runner、assigned 状态和领取身份；已换轮则保留 conflict，不执行旧任务。404/409 的报告/上传冲突也保留本地证据和原因。executing 或 uploading 被中断时，客户端不能证明副作用/远端工件是否已提交，转本地 review；保留原结果，不伪造 failed、不重传上传。review 不影响同批其他任务；同一 task_id 的后续领取也暂停，防止服务器自动换轮绕过未知结果。review 是本地阶段，不等同于服务器 recovery_required；自动对账、人工处置接口和上传幂等仍未完成。核查前应保留数据库与关联证据，不应通过更换日志路径来绕过。

日志身份包含版本，身份不匹配、坏 SQLite 文件或非法阶段拒绝启动并保留原文件。`.sqlite3.lock` 连接持有 SQLite 排他事务至 close/进程退出，同一路径的另一个进程不能操作日志；主数据库仍可逐次提交阶段。不要删运行中的锁文件；只支持有可靠文件锁的本地磁盘，网络共享、多机器同 Runner 身份、多路径启动没有全局排他保证。数据库含任务与完整结果，应放在 Runner 专用私有目录并按部署权限管理。确认成功项从本地移除，未知/冲突项保留，长期归档和容量治理仍需后续实施。

验证包括正式 API 提交后受控丢弃 report/upload 响应、关闭并重新打开客户端、剩余批次恢复，以及两个真实 Python 进程竞争同一日志锁。写入 executing 后正常退出的子进程仅验证阶段持久化和操作系统释放锁，未执行工具，不能称为真实进程崩溃事故。普通人工重试后恢复旧 queued 项验证身份核对；坏文件和不完整批次是受控健壮性契约。未验证掉电、网络盘、真实供应商或长时间运行。

SQLite 使用依据：[Python 3.11 sqlite3 文档](https://docs.python.org/3.11/library/sqlite3.html)的事务提交、参数绑定和连接关闭接口，以及 [SQLite 事务说明](https://www.sqlite.org/lang_transaction.html)的 BEGIN EXCLUSIVE。项目参考为服务端持久领取身份/报告回执和正式任务 outbox 的提交顺序；Runner 客户端此前没有独立持久日志实现。
