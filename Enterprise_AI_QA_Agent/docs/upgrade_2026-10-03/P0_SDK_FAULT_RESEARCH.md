# 多模型 SDK 故障边界实测〔2026-10-12〕

基线 `7fe85eca`。用户决定先继续其他模块，外部模型验收暂缓。本批不改变模型、数据库配置、依赖版本或历史结果。

## 依据与采用做法

- 现有 `test_sdk_stream_observation.py` 只用真实 HTTP 覆盖原生 Chat；`test_sdk_midstream_retry_boundary.py` 对 Chat/Anthropic 使用 MockTransport，且允许 Anthropic 的裸 `httpx2.ReadError`。这些不能证明五条传输的统一业务错误边界。
- `test_model_completion_http.py:provider` 已提供 Chat、Responses、Anthropic、Google 的实际 JSON/SSE，LangChain 使用 Chat 协议；复用其正常响应，不重新发明协议。
- `ModelRuntimeService._invoke_with_client` 只将 `ProviderClientError` 变成 `http_error`。原生适配器是跨 SDK 错误统一的所属层，不在业务阶段加供应商特判；取消应继续抛出 `CancelledError`。
- 当前实际安装 OpenAI 3.14.0、Anthropic 1.5.0、google-genai 2.23.0、httpx 0.28.1、langchain-openai 1.6.2。查阅已安装官方 SDK 源码：OpenAI `_streaming.AsyncStream._iter_events` 将网络错误包装为 SDK 错误；Anthropic `_streaming.AsyncStream._iter_events` 直接迭代 httpx2 字节流。Google `_api_client.HttpResponse` 的流迭代也在建流请求后的重试之外。
- Google `types.HttpRetryOptions.attempts` 包含首次请求，默认 5；不能把 OpenAI/Anthropic 的 `max_retries=2` 映射成 Google 默认策略。本批保持原 SDK 设置，仅实际记录收到的物理请求。

## 功能验收

受控故障网关使用真实本地 TCP/HTTP，收到真实 SDK 的 POST 后记录安全的请求正文、哈希、顺序及响应字节。429/503 首次失败后正常响应，403直接拒绝；流发送有效首片后断开、超时或由调用方取消。网关不记录认证头，不改变 SDK 重试器或生产发送器。

每种传输走真实 `ModelRuntimeService`。判定：重试成功只产生一次完整业务返回、正文不重复拼接；403 不重试；已收到流片段后断开/超时不得重新发送或返回成功；错误统一返回业务 `http_error`，取消仍抛出 `CancelledError`。保留每批失败输出，不放宽原断言。

本批只能覆盖本地协议与 SDK 故障行为。全阶段 Agent 逻辑调用关联、真实外部模型质量、进程故障与生产历史迁移仍需其他验收。API 父子审批挂起/恢复缺口继续保留，完整 P0—P5 未完成。

## 首轮事实与方案纠正

首轮真实 HTTP 30参数：24通过、6失败，49.43秒。Anthropic/Google 各有断开时 `RemoteProtocolError`、超时时 `ReadTimeout` 逃出运行时，共4个真实缺陷；每项已收到正文片段，物理发送1，没有业务成功返回。官方流迭代源码与这些实际异常一致。

另外2个失败来自本批测试错误假设 Google 默认重试：当前生产 `HttpOptions.retry_options=None`，实际 SDK `_api_client.retry_args(None)` 使用 `stop_after_attempt(1)`，与 `HttpRetryOptions` 对象自身的默认 attempts 文档是两个层次。已按代码和实测纠正为429/503各一次、正式失败；不修改配置或强迫 Google 和其他模型使用同样重试策略。首轮原件保留。

修复在原生适配器边界包装已经证实的 httpx/httpx2 RequestError，使用共同错误类而非 catch Exception；官方 SDK 版本切换时支持现有 httpx 与当前 httpx2 两种异常族，未新增依赖。取消不在 Exception族中，不转换。保留异常因果链；运行时日志及公开错误摘要增加安全的 `cause_error_type`，不记录认证头或底层异常正文。原重试、正文组装、工具、预算、数据库与发送机制不变。

## 最终实现与功能结果

网络包装在 `await_provider_io` 的 SDK 请求及 `provider_stream_events` 的取流片段处发生，消费端回调在包装范围之外；实际回调抛出 httpx/httpx2 ReadError 四参数证明原异常保持。这依据官方 OpenAI `_iter_events` 的取流边界，不把整段调用 `catch Exception` 后冒充供应商问题。日志保留模型、供应商、传输和原异常类型。

- `p0-sdk-fault-before`：24通过6失败，49.43秒，保留4个实际裸异常和2个本批错误重试假设。
- `p0-sdk-fault-verified`：28通过2失败，49.90秒。4个裸异常已消失；余下2项是本批断言遗漏 LangChain 转换后的 `OpenAIConnectionError` / `OpenAITimeoutError` 名称。按实际因果链增加精确类型，没有修改生产异常分类或放宽结果判定。
- `p0-sdk-fault-expanded`：61通过，82.97秒。55项通过真实 TCP/HTTP 的运行时（流式7种、非流式4种，各5条传输）；4项真实HTTP消费回调边界；2项既有MockTransport中断测试，现收紧为必须 ProviderClientError。收到流片段后断开、超时、取消均只有1次物理发送；拒绝403只有1次；Chat/Responses/Anthropic/LangChain的首个429/503/响应头前断开后重试一次成功，正文不重复；当前Google三类都1次正式失败。
- `p0-sdk-fault-regression`：原生客户端、完成契约、真实HTTP完成、流终态、请求映射、项目追踪、SDK观察、LangChain模型/消息适配及审批预算十模块152通过3门控跳过，52.89秒。3项为未启用的真实PG审批参数；本批没有冒充PG生产恢复或默认模型质量通过。
- compileall退出0为附加验证，没有前端改动和前端构建。未新增依赖、配置项、数据表、公共路由或更换模型。

当前环境Google使用httpx后端（官方 `_api_client.has_aiohttp=False`）。本批没有验证可选aiohttp后端，也没有验证不同SDK版本的全矩阵。证据目录不记录认证头；最终审计核对配置凭据、旧工件、4份保护文件、3份同步方案及新原件字节。

## Anthropic协议核对及最终路径断言

用户指出辅助函数的response命名容易与OpenAI Responses混淆。生产Anthropic前后均调用`client.messages.create(**params)`，客户端仍`AsyncAnthropic`，从未改为`client.responses.create`；辅助等待函数改名`await_provider_io`，参数名operation明确其用途。已安装官方`anthropic/resources/messages/messages.py`的实际路由是`/v1/messages`。

前批共用正常响应夹具给所有传输`/v1`地址，Anthropic SDK再追加`/v1/messages`，所以受控服务接受了`/v1/v1/messages`。这是测试夹具漏验路径，不是生产协议变更；历史原件保留并明确范围。修正共用夹具Anthropic基址为根URL，故障矩阵逐请求断言正式路由及Messages请求正文；不修改生产数据库地址。

最终`p0-sdk-fault-protocol-final` 87通过86.51秒：原61参数加既有客户端/LangChain模型适配26项，实际Anthropic必须`/v1/messages`且含messages、不含Responses的input；Responses必须`/v1/responses`，Chat/Google各自路由也校验。前一`p0-sdk-fault-final` 55通过4排除81.14秒仅补采了物理响应与请求序号/hash，未断言正式路由，不能当最终协议证明。

修正共用夹具后十模块再跑`p0-sdk-fault-regression-final` 152通过3门控跳过53.84秒。重叠批次不累加；新版compileall与审计记录最终文件状态。配置MySQL、业务PG、外部AI暂缓及完整目标范围保持。
