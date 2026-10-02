# 兼容性 Runner 状态持久化

`CompatibilityRunnerService` 使用配置的 `data_dir/compatibility/runner_state.json` 保存 Runner、任务和工件索引。临时文件写入后替换正式文件，替换成功才推进内存中的已提交快照。HTTP 请求只有在本次变更成功保存后才返回成功。

写入、序列化或替换失败时，服务恢复上次已提交的 Runner、任务和工件索引，记录 `compatibility_runner_state_save_failed`，API 返回 **503** 和固定错误正文。上传索引提交失败沿用已有工件补偿删除；补偿失败记录 `compatibility_artifact_cleanup_failed`，原始提交错误仍返回给调用方。临时文件清理失败也单独记录，不删除目录或正式文件。

503 表示状态没有提交成功；重新提交结果与重新执行工具是不同操作。工具已有外部副作用时，需要对账并保留原结果，不应因为收到 503 就重跑工具。当前 CLI 的完整结果重传与旧尝试回执隔离仍需后续治理。

缺失状态文件允许初始化空服务。存在但无法读取、JSON/编码损坏、未知 schema、集合格式或记录不合法、重复身份的文件会触发 `CompatibilityRunnerStateError`，阻止初始化并保留原文件。日志仅记录类型、系统错误号及数量，不复制结果、元数据或验证失败的输入值。恢复前应保留原文件，核验备份及外部任务实际状态；服务不会自动清空损坏文件或把无法确认的执行结果标成成功。

该实现仍是单服务实例的 JSON 存储：没有新增跨进程锁、数据库事务或掉电持久化保证。内存保留一份已提交 JSON 值用于撤销失败保存，历史保留/大目录容量和多个进程共享同一路径仍需单独治理。

心跳过期和启动恢复共用 `orchestration.compatibility_runner_max_automatic_retries` 自动预算，环境变量为 `ORCHESTRATION__COMPATIBILITY_RUNNER_MAX_AUTOMATIC_RETRIES`，默认 3、最小 0。0 表示中断后不自动重新入队；预算是自动恢复次数，不含首次领取。人工明确重排队开启新的自动预算，累计 `retry_count` 继续保留。未有 automatic_retry_count 的旧文件从 0 开始统计，不凭最近十条历史猜测全部过往次数。

预算耗尽进入新状态 `recovery_required`：保留上一轮 Runner 身份、结果、工件和模式调用状态，记录原因、来源、次数和上限；不会归为工具执行成功/失败，也不会继续被 poll 领取或再次开始模式调用。报告和环境状态显示待核查，前端列出数量并提示核验副作用；人工 requeue 请求需明确包含该状态。其他排队任务可继续领取。人工重试会按既有历史快照方式归档本轮，再清空当前执行字段。

预算和核查字段归服务器所有，派发元数据/Runner report 不能覆盖。正式 Settings 的预算与心跳超时均读取 orchestration；简化兼容调用的平面设置仍受支持。预算只限制现有自动恢复，不保证剩余预算内的重复执行一定无副作用；旧尝试回执隔离和供应商对账仍需后续实现。

在 `Agent_Server` 目录运行：

```powershell
uv run --no-sync python scripts/run_test_quality_gate.py --profile targeted --target tests/test_compatibility_runner_persistence.py --target tests/test_compatibility_runner_service.py --target tests/test_compatibility_mode_skills.py
```

持久化用例使用独立临时目录，分别检查坏文件、临时写入失败、替换被拒绝和无法序列化的值。它们是受控健壮性契约，不是生产磁盘事故复现；正常注册、领取、报告、人工重试和上传的既有用例同时验证成功路径。错误前后文件、内存、API 状态及上传补偿均有检查。

依据：项目现有 `MCPServerStore._load_catalog` 的损坏文件明确报错策略、`settings._DataImportBodyLimitRoute` 的路由处理模板，以及本服务既有临时文件替换和上传补偿实现。公共 DTO 字段与 URL 保持不变，新增 503 错误行为。
