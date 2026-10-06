# B步：HTTP来源证据与环境核对

研究基线：bd1172d6，日期2026-10-06。完整目标继续为PLAN中的P0—P5。

## 查证结果 → 实施决定

| 依据 | 事实与采用做法 |
|---|---|
| modes/api_testing_mode/executor.py | 已follow_redirects=True，只存响应码/headers/body，没有最终地址或跳转历史。请求参数full_url不能证明响应来源；从实际HTTP响应捕获每一跳请求方法、来源和响应码 |
| HTTPX官方QuickStart的Redirection and History；已装httpx/_models.py的Response.request/url/history | response.history为有序历史，response.request为实际请求；使用history加最终响应，不猜测Location或输入地址。官方链接：https://www.python-httpx.org/quickstart/#redirection-and-history |
| ApiTestTask.execution_checkpoint及runtime._checkpoint_execution_state、subagent_coordinator._apply_worker_output | 已有JSON字典在任务/Worker/状态/恢复之间传递。新观察放在现有字典下版本化键，不给旧模型新增缺省字段；保存完成请求时原样附加已有观察 |
| executor._restore_completed_request；runtime._execute_dispatched_task | 外部输入可提供completed_request；该恢复是旧执行契约，不足以证明本次实际HTTP。恢复保留旧行为，但观察必须标记completed_request，不能冒充本次network；旧无观察继续unknown |
| ToolRuntimeService.execute及ToolJobService.observe_output | 服务端上下文持久project/run/item/attempt，真实返回先脱敏再保存ToolJob。环境核对引用PG原ToolJob与固定环境快照，不以客户端提交的原值或verdict为准 |
| 旧test_requirement_execution.retention_http/actual_run | days由查询参数决定；证明真实核验与False/0/null保留，不证明两个部署不同。新验收使用两个各自固定7/9天、相同release标签的实际HTTP目标，请求不能指定响应规则 |
| 本地课程Agent轨迹与协议鲁棒性：Action/Observation/Replan | 测试轨迹与工具返回来源；正确body不代表正确目标，版本标签不代表需求生效 |

## 实施边界

新增api-http-observation-v1：实际每跳方法、scheme/host/port/path、是否省略query或userinfo/fragment、状态码、观察时间及取得方式。新字段不复制headers、Cookie、query值或凭据。正常HTTP错误响应仍有观察；超时/未收到最终响应不伪造完整链路。新实际尝试清除旧响应与旧观察，防止失败重试沿用前一次成功证据。

观察产生于执行器收到真实response之后。输入的同名键先清除；恢复仅从completed_request读取并明确标记来源。旧完成请求没有该键时保持旧输出结构与恢复，不为补证据重新发送HTTP。完成检查点、Worker合并、结构化报告与任务证据同步保留新观察。

在已有项目环境API增加只读工具观察核对：加载PG固定环境快照和当前项目原ToolJob，读取具体output_path_tokens与值，返回原输出SHA、调用身份、真实HTTP观察、目标匹配/不匹配/未知和原因。新读取不发送HTTP、不读当前MySQL、不修改或停用用例、不生成部署确认。已归档项目/环境可以读固定历史；跨项目或缺服务端项目归属不可读取。

匹配只核对scheme、host、有效port与路径边界，所有实际hop均应处于选定目标范围；/app不能匹配/application。不做DNS别名、URL相似或名称推断。旧记录、完成请求回放、其他工具、缺有效观察均unknown，不拦截正常执行。输入地址、release标签或成功状态均不直接转换为effective。

这一步是B的实际证据读取入口；需求版本/验证点/精确用例版本的追加确认、撤回/并发与C运行约束仍须继续实施。历史部署unknown不重写。无新依赖、工具模式、Git存储或部署轮询。

## 必须验证

先实际重现跨端口跳转仍返回9天却没有最终地址；保留失败记录。随后实际验证两个固定规则目标、同源/跨源/回到原源跳转、303方法变化、query不复制、失败重试、完成检查点与旧回放不重发；通过ToolRuntime/PG保存并核对原输出，使用真实MD/DOCX/PDF、MySQL配置与PG固定环境捕获，改址/删除/归档后历史仍可核对。扩大现有API依赖、Worker恢复与正文执行关联回归；独立main API重新读取实际PG工具观察。结果只证明来源与读取，不代替部署业务评审或默认Agent自主执行。
