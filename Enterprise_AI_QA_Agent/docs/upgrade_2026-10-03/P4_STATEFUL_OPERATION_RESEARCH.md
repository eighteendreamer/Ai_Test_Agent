# C2c3：真实准备、清理与跨调用恢复研究

研究基线5102413d。完整P0—P5目标保持；本步先验证现状并定位架构缺口，不将研究样例称为功能修复。

已查原目标、plan_mapping/agent_case_context、原Agent检查点及审批恢复、ToolJob构建/领取/输出交付、API executor/campaign idempotency key，以及本地《Agent轨迹与协议鲁棒性》04.5。优先复用已有真实订单状态的regression_business_http与原Agent图，不再用固定响应或DELETE返回200证明真实清理。

现有固定计划表达before/case/after、准备拥有者、输入绑定和完整指令。结果在最终回执中保存preparations及真实工具引用；它们还不是执行中的准备/效果状态机。原ToolJob按Session/Turn/provider call_id定位，原图及新领取只恢复同原Job；API完成请求只有显式传回匹配检查点时复用，不自动查PG其他Job。其idempotency_key包含task_id/方法/地址，并不是业务端幂等承诺，也不核对请求体/鉴权等完整作用域。

拟定实际验证：使用原MySQL配置冻结binding/admission，原PG计划/运行/检查点/审批，真实JSON文件上传RustFS。第一场景创建真实订单、用实际ID查询，业务期望count=0实际count=1时明确failed，仍按原准备ID删除并检查服务器为空。第二场景真实POST创建后丢失返回，同原Job恢复不能再发；第三场景仅换provider call_id、保持原task_id及参数，再验证是否多建订单。保存服务器状态、原请求/Job/完整计划及模型轨迹；研究失败不改成成功。

后续设计不能只按相同参数或文本去重：同参数两次创建、重复负向测试、不同身份/目标/输入及只读刷新都可能是合法操作。稳定逻辑操作与provider call_id必须分开；新语义应显式冻结，保留每次原模型调用/审批，关联原操作/效果和可靠输出，未知效果先查证。改变参数不是同一次重试；实际效果拥有者和清理还须独立验证，after标签不能证明清理。

实际验证已经结束，原始文件、模型消息、PG记录、HTTP和服务器状态保存在`p4-stateful-research-*`目录。没有新增生产修复；新研究测试的两个真实失败仍保持失败，不改成xfail或删除断言。

## 实际结果与失败分类

| 独立轮次 | 结果 | 含义 |
|---|---|---|
| first | 2通过、1失败，16.01秒 | 单次准备/传值/清理与同原Job未知恢复通过；新provider call_id重复创建 |
| agent-first | 1失败、3未选，9.09秒 | 研究夹具误读tool消息的input字段，图中断；不是重复操作证据 |
| agent-fixed | 1失败、3未选，8.69秒 | 研究夹具误把内部tool_calls当作SDK的function结构；不是业务缺口证据 |
| agent-wire-corrected | 1失败、3未选，11.32秒 | 按原内部消息arguments更正后，原图/四次审批实际创建两条，只清理第一条 |
| current | 2通过、2失败，18.92秒 | 最终研究源码完整四项；原图已知返回后换调用ID及共享runtime未知返回后换调用ID均真实失败 |
| compat-final | 67通过、2跳过，160.09秒 | 既有assessment/execution受影响模块；实际模型与公开Redis Worker专项因开关未启跳过，不代表它们已验 |

`current/stateful-lifecycle`：三次实际HTTP为POST创建、GET实际ID、DELETE实际ID。服务器created=1、orders=0；目标期望count=0实际count=1，PG结果仍failed，准备seed/cleanup为passed。它证明已知单次效果的正常链路能够清理，不能把整个执行体系称为尚无清理能力。

`current/original-agent-changed-call`：保留完整原Agent图与四次原审批、五次受控模型请求、四条原Job。seed与seed-recovery的task_id和完整原参数一致，仅调用ID变化，均completed；服务器created=2、最终orders=1。查询及DELETE使用第一条实际ID，第二条未清除；结果failed，最终preparations仍只体现seed/cleanup通过。这里的completed是工具完成，不是业务目标通过。受控Agent为继续原目标/清理而使用的局部决策视图不是原始消息，所有真实请求另行完整保存。

`current/same-original-job`：POST实际创建后返回在持久观察前丢失，原Job为running且无可靠输出；同原Job恢复要求查证，HTTP仍1、created=1。`current/changed-provider-call`：相同未知创建只换provider call_id进入共享ToolRuntime，HTTP变2、created=2；原Job仍running，另一Jobcompleted。后者不是原Agent图在未知状态自动继续的证明，必须分别记录两个入口。未知测试没有执行清理，服务器关闭仅是隔离夹具销毁，不能称业务已清理。

上述使用实际MySQL策略冻结、PG计划/运行/Job/检查点/审批、原件JSON上传RustFS和真实有状态HTTP服务器。模型为受控Agent，无外部默认模型、独立生产进程或完整浏览器App验收；本轮没有三格式解析专项。两次消息结构错误及其原日志保留，不能混入最终真实失败。

## 根因及下一步采用方式

1. **继续原链路。** 现有固定计划、输出绑定和正常清理有效，扩展原Job/领取/输出恢复，而非新建模式执行器。
2. **声明操作与传输身份分开。** 现有`ToolJobService.build_job`按session/turn/provider call_id定位；原Job未知保护因此不能覆盖另一个调用ID。新语义须显式冻结，操作作用域绑定固定运行/归属项及完整输入；每次原调用/审批仍保留。相同参数不是充分的业务身份，不能全局自动去重。
3. **原结果与未知分别处理。** 可靠保存输出可通过原恢复交付；未知不得复制成功或重新发送效果操作。具体新契约及存储消费方继续查证后实施，当前未承诺全局exactly-once或业务端幂等支持。
4. **效果及清理按真实对象追踪。** 已观察输出的字段路径/hash、效果拥有者、消费者和具体对象进入执行期证据，终态preparations不能代替它。不能仅从POST/DELETE或after推断效果/清理完成；需要真实状态、失败后清理和中断恢复验证。
5. **防止误拦。** 后续必须同时验证原失败输入、合法两次同参数创建、不同身份/目标、只读刷新、负向重复、并发、丢提交回执及旧策略。未知查证依据项目实际可用的只读能力，不能编造业务接口。

配置继续存原MySQL规划设置并在提交时冻结；运行/操作/效果及证据引用由PG持有，大原始工件在RustFS。旧协议、hash、终态和未知历史保持。准备/效果状态机、稳定操作身份、修订、换目标回归、默认Agent、环境App及其余P0—P5仍未完成。
