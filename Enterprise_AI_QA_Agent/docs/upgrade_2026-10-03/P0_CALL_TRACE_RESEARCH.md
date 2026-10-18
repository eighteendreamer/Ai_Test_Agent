# P0 模型调用追踪调研

日期：2026-10-18

## 调研基线与当前代码事实

系统已有内部 `ModelInvocationRequest` 和按传输类型选择的 provider client。各传输保留自己的协议与请求映射：Anthropic 调用 `client.messages.create`；OpenAI Responses 独立位于 `OpenAIResponsesClient`；Gemini 使用原生客户端。`await_provider_io` 只等待 SDK 操作，并将网络异常转换为 `ProviderClientError`。

当前存在多种有用但不可互相替代的身份：

- 主图 turn 使用 `session_id`、`turn_id`、`trace_id`；图请求在可用时携带运行、条目和执行尝试上下文。
- 用例规划每个规划轮次持久保存 `logical_call_id`，Worker 使用 `attempt_id`。
- 文档生成以 `request_sha256` 标识冻结请求，生成任务另有 Worker `attempt_id`。
- 需求影响调用持久保存 `call_id` 和 Worker `attempt_id`。
- 回执纠正检查点已有逻辑调用身份。

调研基线时，`TraceContext` 不接受 `logical_call_id`（`extra="forbid"`），模型运行时也没有将统一调用身份传给 provider SDK。7.78 实施后，`TraceContext` 与 runtime 已传播逻辑调用身份，并由各 provider 的注入 client 记录 SDK HTTP 请求尝试和收到的响应；该观察尚未贯通所有逻辑调用入口，也不是精确的物理发送计量。

## Provider 观察入口

对照当前 `uv.lock` 锁定的 SDK，并在该环境读取构造器签名，确认以下注入接口：

| 路径 | 锁定依赖 | HTTP client 注入接口 |
|---|---|---|
| 原生 OpenAI client | `openai==3.14.0`、`httpx2==2.13.0` | `AsyncOpenAI(http_client=...)` |
| Anthropic Messages | `anthropic==1.5.0`、`httpx2==2.13.0` | `AsyncAnthropic(http_client=...)` |
| Google GenAI | `google-genai==2.23.0` | `HttpOptions(httpx_async_client=...)` |
| LangChain OpenAI-compatible | `langchain-openai==1.6.2` | `ChatOpenAI(http_async_client=...)` |

仓库已经分离 provider 请求序列化和响应解析，因此观察层应保持每个 provider 的原生协议。构造器接受注入只证明入口存在；这些锁定版本的 timeout、retry、streaming、关闭及并发语义已通过本地真实 SDK/TCP 故障矩阵首轮验收（见下文），外部供应商完整验收及全阶段追踪仍未完成。

## 决策与下一步

不统一供应商协议，不把 Anthropic Messages 替换为 OpenAI Responses。逻辑 ID 已增加并接入 SDK request attempt/response 观察；这不构成物理网络发送已发生的证明。

## 观察层实施与实测边界

基于上面的 SDK 注入入口，在既有 runtime 调用上下文内实现 `ProviderCallObservation`，沿用/生成 `logical_call_id`，将 httpx2 client 注入 OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini 和 LangChain OpenAI-compatible 调用。日志仅保存 transport、脱敏路由、HTTP method、attempt 序号、响应状态和耗时，不保存凭据、请求体或响应体。成功及 HTTP 错误结果带 `logical_call_id` 和 `http_request_attempt_count`；旧字段 `http_send_count` 已移除，避免误称为物理发送数。

真实 TCP fixture 首轮失败揭示实现根因：锁定的 httpx2 在异步请求链中 `await` request hook，而观察函数最初是同步函数。Google 直接抛出 `TypeError: object NoneType can't be used in 'await' expression`；OpenAI 将同一 hook 错误包装并 SDK 重试，fixture 收到 0 个 HTTP 请求。将 hook 改为 async 后，重跑原 SDK/TCP 故障矩阵 **59通过**；覆盖五条 transport、SDK 对 429/503 的实际重试、403、发送前连接断开、流式截断、超时、取消、endpoint/协议体、并发隔离和回调异常。受影响模型回归 `tests/test_model_clients.py tests/test_model_completion_http.py tests/test_model_sdk_fault_http.py` **95通过**。该证据验证锁定 SDK 下自定义 client 的请求映射、重试、流式与关闭路径；不代表每家外部供应商均已真实调用验收。

httpx2 request event hook 的执行位置在 transport 发送之前，因此 `model_http_request_attempt_started` 严格表示“SDK 已把请求交给 HTTP client”，不能单独证明字节已到达网络或服务端。response hook 证明收到 HTTP 响应；本地 TCP fixture 的服务端计数证明测试环境中实际收到的请求与 attempt 顺序一致。遇到 DNS/连接建立阶段失败时，生产 attempt 计数可能大于服务端实际收到数。

继续检查锁定 `httpx2==2.13.0` 的本地 `_client.py` 后，发现传入自定义 `transport` 时客户端会设置 `allow_env_proxies = trust_env and transport is None`，也就是显式传输会停用自动环境代理映射；为保留所有默认 mount 再包装 `_transport` 则需要访问客户端私有成员。更关键的是 `AsyncBaseTransport.handle_async_request()` 只表示请求交给 transport/core，DNS 或连接建立仍可能在此之后失败，它也不能证明远端收到。因此 transport wrapper 既会增加私有耦合或改变代理行为，也不能满足“远端实际收到”的证明目标，不采用。

当前 observer 不篡改 SDK 默认 transport、proxy、TLS、连接池或重试。生产侧可可靠报告 SDK request attempt 和收到的 response；精确远端接收需服务端/网关接入日志或供应商侧请求 ID/日志，不能由客户端 pre-send hook 推断。

## 当前决策与下一步

按 provider 保留原生协议；Anthropic 继续 `client.messages.create` 与 `/v1/messages`，OpenAI Responses 仍只属于独立 OpenAI adapter。统一适配层的必要范围是内部调用/结果契约及各 provider 的定制字段映射，不是统一成 Responses 协议。

已完成逻辑调用 ID 到 SDK HTTP request attempt/response 的生产观察接入及本地真实 SDK/TCP 验收。全阶段覆盖仍未完成：普通 turn、持久 Worker、生成、规划、影响分析等 ID 传播要继续逐入口核对；生产日志无法单方面证明远端实际接收。真实发送端/远端接收需服务端或网关证据。本地 SDK/TCP 矩阵保留这种端到端对照。下一步核验并发/请求取消与日志投影中的 call ID 覆盖，再沿各业务逻辑入口检查持久调用标识是否稳定穿过 retry/recovery。

## 发送前失败出口补核

`ModelRuntimeService.invoke` 的模型缺失、参数不支持、认证失败、密钥缺失和运行参数漂移出口会在 provider SDK 之前结束。它们没有 SDK attempt，仍必须保留应用层 `logical_call_id`，否则规划、生成或影响任务的持久调用意图无法与最终失败结果对账。本轮补齐这些结果摘要和结构化日志字段；不把“没有 HTTP attempt”误报为 HTTP 失败，也不改变各 provider 的原生协议。

回归结果：`tests/test_model_clients.py tests/test_model_completion_http.py tests/test_model_sdk_fault_http.py tests/test_sdk_midstream_retry_boundary.py` **102通过**；发送前失败、请求映射和客户端回归 `tests/test_model_failure_lifecycle.py tests/test_model_request_profile.py tests/test_model_clients.py` **67通过**。持久需求影响、回执纠正和规划的真实存储测试在未设置对应 `RUN_LIVE_*` 门控时仍跳过，不能据此声称全阶段 retry/recovery 已验收。
