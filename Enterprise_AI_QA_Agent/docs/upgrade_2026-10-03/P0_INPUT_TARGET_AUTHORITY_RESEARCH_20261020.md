# 执行目标可信上下文来源隔离调研与实测

日期：2026-10-20。

## 范围与依据

原方案要求保留现有 Agent、模式、工具及权限。默认性能 Agent 验收发现普通项目 `base_url` 未接入服务端会话可信范围后，先核对真实入口，不能用客户端消息自行声明授权来补齐这一缺口。

- `application/orchestration/input_orchestrator_service.py`：消息 `context` 先复制到执行上下文，已有清理循环移除客户端提供的运行身份及安全授权，再从服务端会话元数据恢复可信字段。
- `application/security/execution_safety_policy.py`：`_trusted_target_hosts` 消费 `trusted_resource_scope` 和 `trusted_target_hosts`，据此决定私有目标是否可访问。
- `application/security/resource_access_policy.py`：资源范围也可回退读取 `resource_scope`。
- `application/sessions/session_service.py` 及既有 `test_session_project_binding.py`：公开会话元数据不允许直接写入服务端环境、范围及安全授权。

根因是现有输入边界漏清理四个服务端权威字段，不是需要新增一套目标授权协议。修复沿用已有清理与来源恢复机制，不修改执行策略及审批逻辑。

## 修复前实际复现

新增四参数入口回归，分别把 `trusted_resource_scope`、`resource_scope`、`trusted_target_hosts`、`trusted_environment` 放入 `SendMessageRequest.context`，通过生产 `InputOrchestratorService`。实际 **4 failed、17 deselected，3.75 秒**：四字段均残留于执行上下文。

真实浏览器专项使用本机独占 HTTP 服务、生产输入编排、生产 `_resolve_tool_call` 和原生 Chromium。输入文本仅“读取受控页面”，具体目标在工具参数中，避免把轮次级确认与目标判定混为一谈。实际 **1 failed、20 deselected，6.25 秒**：第一个伪造 `trusted_resource_scope` 被接受，工具状态 `completed`，HTTP 服务收到 `GET /`，预期应为 `denied` 且无请求。失败后即停止该参数循环；没有声称修复前其余三个字段都实际放行网络。浏览器与 HTTP 服务在 `finally` 中回收。

原始失败文件在本地保留：

- `p0-input-target-authority-before-20261020.xml`
- `p0-input-target-authority-live-before-20261020.xml`
- `p0-input-target-authority-before-20261020/input-context-probes.json`

## 实施与修复后验证

只在现有清理字段集合加入上述四字段，复用 `client_execution_context_removed` 结构化日志。随后仍从服务端会话 `resource_scope`、`environment` 恢复真实范围与环境。客户端不能扩大范围或覆盖环境；既有服务端来源保持有效。

实际执行（`Agent_Server` 工作目录）：

```powershell
$env:RUN_LIVE_BROWSER_TESTS='1'
$env:QA_BROWSER_COMMAND_EVIDENCE_DIR=Join-Path (Get-Location).Path '..\docs\upgrade_2026-10-03\p0-input-target-authority-fixed-20261020'
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_browser_command_target_policy.py tests/test_intent_safety_routing.py tests/test_tool_executor_safety_gate.py tests/test_session_project_binding.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p0-input-target-authority-fixed-20261020.xml
```

结果 **133 passed，无跳过，18.91 秒**。真实浏览器专项证明：

1. 四个客户端字段分别经生产入口处理后，工具均 `denied`，正向测试开始前 HTTP 请求累计为 0。
2. 服务端会话登记本次独占目标和 `testing` 环境，同时客户端提交冲突地址与 `production` 环境，输出仍保留服务端范围和环境。
3. 真正打开受控页面后，服务端收到一次 `GET /`。Chromium 快照含页面当次随机标识，已完成 ToolJob 的输出也包含相同标识。

此专项 ToolJob 使用现有 `InMemoryToolJobStore`，工件导出到本地；不把它报告成 PostgreSQL/RustFS 持久化或公开 API/Worker 产品验收。四文件回归覆盖原安全意图、工具执行及项目绑定行为。`compileall` 对本轮两个 Python 文件实际通过，仅作附加检查。

修复后原件保留在 `p0-input-target-authority-fixed-20261020/`。提交的 `verification-safe.json` 从原始对照、快照和 JUnit 提取状态、计数及一致性结果，不含请求头、配置凭据或模型私有推理。复验必须使用新工件目录，测试明确拒绝覆盖旧目录。

## 当前分类及边界

**已有实现且本次验收：** 客户端不能直接提供这四个服务端可信字段；真实服务端会话范围仍能使具体浏览器工具访问受控目标。

**仍需补实现或验收：** 普通用户执行目标的公开登记及其产品语义；真实默认安全 Agent；其他 P0—P5 未闭环项。项目 `base_url` 不因本次修复自动成为授权来源，不以服务端测试夹具替代公开入口。没有生产迁移、数据库配置或前端契约变更，不重复执行前端构建。

供应商协议保持原样：Anthropic 仍调用 `client.messages.create`；`await_provider_io` 仅等待原 SDK 操作并归一网络异常。OpenAI Responses 使用独立客户端。本批没有修改任何模型客户端，也没有新增真实模型调用。
