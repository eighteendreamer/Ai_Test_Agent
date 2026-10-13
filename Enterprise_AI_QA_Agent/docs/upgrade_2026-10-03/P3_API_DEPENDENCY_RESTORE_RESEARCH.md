# API 已完成前置输出恢复与实际请求路径核验

基线：`b25d15cf`。按原方案 2.4 和 5.3，前置产生的数据必须在恢复后仍可用于依赖项；已经完成的写入不能为获取输出而重新发送。本专项不改变模型协议、业务依赖推理、认证策略或正式 RunItem 生命周期。

## 依据 → 采用做法

- `subagent_coordinator.py:74` 已从当前 pool 的 `TASK_COMPLETED` 条目重建 `_task_outputs`；`coordinator.py:56` 却初始化为空。`runtime.py` 两种执行分支都使用持久 Campaign 中的同一 `ApiTestTask` 模型，`_checkpoint_execution_state` 已保存响应、检查与请求结局。采用已有 Worker 做法，使本地协调器读取同一 Campaign 内的已完成响应，不引入额外持久数据、配置或模型调用。
- `executor.py:51` 实际发送地址是 `task.full_url or task.path`；`input_binding.py:_path_text` 原来优先 `task.path`，使检查对象与发送对象不同。绑定与未解析检查统一按照执行器实际选用的地址，仅解析其中 URL path，继续排除端口、query 和 fragment。
- `tests/test_api_worker_result_reader.py:test_recreated_completed_outputs_supply_downstream_zero_value` 已验证 Worker 从完成前置恢复 `id=0`，`test_api_document_dependency_http.py` 已验证显式映射、推断映射和嵌套前置来源。新增测试使用相同模型、协调器、实际 Executor 与 ReportBuilder，但通过独立进程退出后重建协调器验证恢复。
- 本地课程 `Agent轨迹与协议鲁棒性.html` 强调结果、轨迹、幂等和恢复应共同验证。验收同时核对进程、真实 HTTP 顺序、原完成任务不变、终态再次执行无新增请求，不以编译代替验收。

这是两个既有执行分支在恢复数据生命周期和有效请求地址上的错位；修复与现有分支对齐，不新增通用业务门卫或另一套绑定语言。

## 修复前实测

`p3-api-dependency-restore-before.xml`：**7 failed，15.33 秒**，失败原件保留。

五个场景的首进程均在真实 `POST /orders` 完成并写下 Campaign 检查点后以退出码 73 立即退出。新进程读取检查点：

- `{id}`、`:id` 和显式 `data.public_id` 绑定都无法读取已保存的 `id=0`，查询未执行。
- body/query 绑定丢失 `False` 和 `0`，真实请求变成无参数、无 body 的 `POST /confirm`，返回 422。
- `path=/orders`、`full_url=/orders/{id}` 时实际发送了 `/orders/%7Bid%7D`，返回 404。
- 没有前置输出但有效 URL 含占位符时，本地分支产生了业务 HTTP，Worker 分支尝试派发。这两种行为都不能把缺失输入准确表示为派发前的缺口。

## 验收范围与限制

修复后原七项 **7 passed，14.92 秒**。新增已完成/失败/unknown 输出隔离及有效地址/query/fragment 边界后，五模块最终回归 `p3-api-dependency-restore-regression2.xml` **68 passed、2 skipped，19.95 秒**。两个跳过项是原文档依赖实测中的真实 PostgreSQL/RustFS门控；没有将其算作通过。

第一次扩展回归误用了已有 `after` 原件目录，保护原件的 `exist_ok=False` 正确拒绝覆盖，结果 **5 failed、63 passed、2 skipped，7.55 秒**；该轮 log/XML 保留。之后使用新的 `after2` 目录，原件未覆盖，最终回归通过。这次失败是执行配置失误，不是修复后业务行为失败。

实际恢复后的请求包含 `/orders/0` 和 `/confirm?id=0`，body 保持 `enabled=False`、嵌套 `count=0`。原已完成创建任务整个 JSON 保持一致，每个场景 HTTP 总数为 2，终态重投没有额外请求。

本专项证明固定 Campaign 的本地执行器在“完成前置检查点 → 进程退出 → 新进程恢复”边界可恢复响应；测试检查点使用文件序列化，与生产 Campaign 使用相同模型，但不宣称本专项已验收 PostgreSQL/API/Redis Worker 的完整重启流程。

显式字段绑定有来源时能恢复 body/query 的实际值；任意缺失 body/query 输出的校验、自动认证续期、多认证流隔离、未知副作用只读查证及执行中计划修订继续属于后续工作。默认 AI 的项目选择、排序和补全质量本轮不调用外部模型、不作通过声明。完整 P0—P5 目标保持进行中。
