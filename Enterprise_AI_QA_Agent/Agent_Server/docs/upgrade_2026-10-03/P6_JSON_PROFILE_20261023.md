# 模型 JSON 输出声明与产品链路验收

## 调研结论与范围

现有系统已经有模型适配层，无需重新统一供应商协议。本轮只补验收测试，没有修改模型客户端、数据库结构或正式模型配置。

依据当前代码：

- `src/schemas/model_request_options.py` 将 `ModelRequestProfile` 定义为独立于旧能力默认值的明确兼容声明；`output_formats` 区分 `json_object` 和 `json_schema`，并校验 transport。
- `src/infrastructure/model_config_store.py` 在 MySQL 保存和重新读取 `request_profile`；省略时保留、显式 null 时清除。
- `src/application/model_clients/request_mapping.py` 根据当前模型的声明和请求的 `required` 决定原生字段、可选回退或发送前返回不支持。旧 `capabilities.json_mode` 不能独自声明某个模型接受哪些协议参数，也不覆盖明确的请求配置。
- `src/application/models/model_runtime_service.py` 保存映射决策并在 HTTP 之前处理确定的不支持情况；四种原生客户端各自使用官方 SDK。
- 本地课程 `项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html` 的结果与轨迹双评估要求用于选择断言：同时核对消费者收到的 JSON、请求路径、实际参数和物理发送次数。

此前 `test_actual_profile_fields` 已覆盖原生与 LangChain 请求参数，但设置页面测试在保存 JSON 声明之后没有实际请求 JSON 输出。本轮补上这条衔接，并验证真实 MySQL 重新读取后的四种原生协议。

## 实际执行

第一组在真实 MySQL 随机表上执行新增八个场景，并运行既有请求配置回归：

```powershell
$env:RUN_LIVE_MODEL_REQUEST_PROFILE='1'
$env:QA_MODEL_JSON_PROFILE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p6-json-profile-native-current-20261023-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_json_profile_live.py tests/test_model_request_profile.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p6-json-profile-native-current-20261023-01.xml
```

结果：**61 passed in 58.57s**。八个新增场景为四种原生协议 × 流式/非流式；本机 HTTP 服务返回包含中文、emoji、整数、false 和 null 的固定 JSON，消费者实际解析并核对内容。

| transport | 实际 SDK HTTP 证据 |
|---|---|
| OpenAI Chat Completions | `/v1/chat/completions`，`response_format.json_schema.schema` |
| OpenAI Responses | `/v1/responses`，`text.format.schema` |
| Anthropic Messages | `/v1/messages`，`output_config.format.schema` |
| Google Gemini | `generateContent` / `streamGenerateContent`，`generationConfig.responseMimeType` 和 `responseJsonSchema` |

每个新增场景从 MySQL 重建 Store 和 Runtime：旧 `json_mode=false` 与明确的 Schema 声明同时存在时正常发送；随后把旧布尔值改为 true 并清除明确声明，必需的 Schema 请求返回 `unsupported_request/format_not_declared`，物理发送次数不增加；将请求设为可选后发送普通请求，保留 `prompt_fallback` 决策，未夹带其他协议的 JSON 参数。八个新增场景合计16次实际本机 HTTP；这个数量不包含既有53项回归。

第二组使用生产 API 进程、构建版设置页面、真实 MySQL/隔离 PostgreSQL/RustFS 和已有 Redis：

```powershell
$env:RUN_LIVE_MODEL_REQUEST_PROFILE='1'
$env:QA_ISOLATED_RUNTIME_REDIS_URL='redis://127.0.0.1:6379/13'
$env:QA_MODEL_REQUEST_PROFILE_PRODUCT_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p6-json-profile-product-current-20261023-01'
$env:QA_MODEL_REQUEST_PROFILE_STORAGE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p6-json-profile-storage-current-20261023-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_request_profile_product.py tests/test_model_request_profile_storage.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p6-json-profile-product-current-20261023-01.xml
```

结果：**2 passed in 84.14s**。

- 设置页面填写并保存声明，刷新读回；非法 JSON、非法字段和不兼容 transport 明确返回错误。
- 真实 MySQL 自有旧表缺少 `request_profile` 列时，重复初始化恢复该列，旧模型仍可读取。
- 保存后的 Runtime 实际请求 `json_object`，检查 `response_format`；API 重启后新 Runtime 再次读回并发送相同 JSON 参数，消费者读回相同 JSON。清除配置后固定依据校验阻止原任务续写，没有新增发送。
- 另一个存储场景现场创建并上传 Markdown，固定模型配置到 PostgreSQL 规划任务，将生成请求和响应写入 RustFS 后重新读取；通过正式生成器解析并保存八步负向查询草稿。修改配置 revision 后，原冻结任务不能用新配置续写。

生产页面测试中的 `product_browser` 实际执行 `npm run build`，包含 `vue-tsc` 类型检查和 Vite 构建，结果通过。两个改动测试文件另经 `py_compile` 检查通过；环境未安装 ruff，未执行 Python lint。

## 资源与证据

本轮没有创建 Docker 容器。Redis DB 13 开始为0键；测试完成并关闭 API 后，按构建版页面网络记录核对两个本轮会话事件键，连同本轮初始化的 `qa:quota:limits` 精确清理，最终为0键，没有使用 FLUSHDB。再次查询没有 `qa-execution-cli-*` 容器。

独立只读回查确认：八个原生场景及产品场景合计13张随机 MySQL 表全部不存在，产品场景的独立 PostgreSQL 数据库不存在。生成存储场景由既有夹具清理，不计入上述13张表的独立核对数量。

原始 JSON、页面截图、网络记录、API/构建 stdout/stderr、JUnit 和资源核对保留在上面三个本地证据目录；产品目录另有 `cleanup-verification.json`。不把原始日志和测试租约批量加入提交。

## 尚未证明

以上证明实际 SDK 到本机 HTTP 的协议映射、MySQL 配置持久化、设置页面和冻结恢复边界。服务端响应为受控内容，不证明外部供应商接受所有 Schema、模型语义质量或默认 Step 全流程。文件/PDF 原生传输及其他能力边界也不由本轮证明。

Step 认证按用户说明已经完成；最近真实调用的阻塞为 `no active step plan subscription`。本轮未请求外部模型、未切换默认模型、未修改模型协议；该默认模型验收仍待订阅可用后继续。
