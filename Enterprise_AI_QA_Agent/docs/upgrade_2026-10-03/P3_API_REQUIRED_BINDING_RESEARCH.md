# API 前置实际输出绑定与缺失阻断研究

基线 `fec6234b`。本批只处理已存在的 `InputBinding` 执行契约，不新增业务前置清单，不把模板示例值当成运行数据。

## 调研依据与错误方案排除

- 方案原文要求“前面用例产生数据时，传递实际输出”“前置成功但输出缺失，不将依赖视为满足”；`ApiTestTask` 已有 `input_bindings`、`depends_on` 和目标路径字段。现有本地 `ApiTestCoordinator` 与 Worker `ApiSubagentCoordinator` 各自复制绑定逻辑，缺失来源时 `continue`，随后仍发送任务中残留的 request body/query/header 示例值。
- 本地课程 `接口测试-实战篇.html` 1.5 使用响应体字段传递 `access_token`、`user_id`；因此来源必须是上游响应，不是同名变量或计划示例。现有正式测试链路的 `literal_evidence.py` 也使用显式路径并区分路径不存在和实际 `null`。
- `ApiTaskExecutor` 的实际请求顺序是 `request_headers`、`request_query`、`request_body`，Worker 派发前也需绑定；所以检查必须发生在两种协调器 claim/dispatch 之前。不能只在 HTTP 执行器里返回错误，否则 Worker 已经派发，且报告无法区分计划缺口与目标响应。
- 失败后续依赖应沿现有 `ApiTaskPool` 状态传播为 skipped；独立任务继续执行。绑定缺口不是网络瞬时故障，不能进入重试。修改应收敛到已有 `input_binding.py`，移除两个协调器的重复实现，避免两条路径继续漂移。

## 最终实现

新增共享 `apply_input_bindings`：在副本上逐项读取上游 `response_body`，支持点路径和数组下标；使用内部缺失哨兵保留实际 `null`、`false`、`0`。只有声明的 `literal_value` 才能作为显式备用值；没有来源/备用值的空旧绑定保持兼容跳过。

目标支持现有 `body.*`、`request_body.*`、`query.*`、`request_query.*`、`request_headers.*`、`path.*`/`url.path.*`。请求头和路径只接受标量并做 CR/LF 检查。任意绑定缺失、目标不支持或值类型不合法时，副本不提交，任务在发送/Worker 派发前失败，记录来源任务、路径和目标，不记录凭据或响应值。

执行器仍保留目标用例自己的负向断言；只有计划声明的依赖绑定失败才阻断。绑定失败不会重试；终态再次运行不会产生 HTTP。报告保留失败任务、跳过的消费者和独立任务结果。

没有新增表、配置、依赖、模型协议或前端契约。配置仍按项目规则放 MySQL，运行数据与证据仍走 PostgreSQL；本批没有需要迁移的字段。

## 真实验证

- 修复前 `p3-api-required-binding-before.xml` **10失败、11.14秒**：本地/Worker 的缺失 body/query/header 仍发送，实际 `null/false/0` 断言也失败。原 XML、日志和事件保留，不把 10 个参数误报成 10 个独立生产缺陷。
- 初步修复 `p3-api-required-binding-after.xml` **10通过、10.65秒**，验证核心缺失与值绑定。
- 扩展 `p3-api-required-binding-expanded2/` 覆盖两协调器、body/query/header、`body` 与 `request_body` 别名、literal fallback、实际 `null`、非法目标/类型和原子不提交。每个事件只记录字段事实，不保存秘密。
- 受影响回归 `p3-api-required-binding-final.xml` **150通过、8跳过、64.24秒**。包含真实 HTTP、已有 API 依赖/恢复、Cookie/Token、多账户、请求体、Worker 结果和模式隔离；跳过的真实存储门控不计入通过。
- 独立进程证据 `p3-api-required-binding-process/`：上游成功后进程退出73；新进程读取原检查点，不重发已完成请求，绑定缺失的消费者保持失败且发送次数为0。外部 AI 调用0。
- `compileall`、`git diff --check` 和白名单审计在交付前执行；没有前端修改，所以本批不重跑前端构建。

## 未完成边界

本批不自动推断业务字段、不从其他项目借数据、不为缺失绑定生成接口或选择器。响应字段选择仍由计划/Agent 明确声明；默认 AI 的项目选取→排序→补全质量和完整正式用例运行仍未验收。自动 401 续期、未知副作用只读查证、执行中计划修订、复杂 UI 知识和完整 P0—P5 仍保持进行中。
