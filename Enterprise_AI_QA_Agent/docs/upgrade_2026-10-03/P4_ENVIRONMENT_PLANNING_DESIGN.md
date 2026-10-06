# C1 固定环境接入现有规划与执行依据

研究基线：67602761。先读现有规划提交/Worker、plan_mapping、PG运行事务、Agent上下文、回归、环境捕获与适用确认，以及本地《Agent轨迹与协议鲁棒性》的版本/恢复/幂等章节。

## 查证与采用做法

- `CasePlanningTaskService.submit` 先按原请求读PG，再准备输入；`PostgresPlanningTaskStore._submit` 重新核对seed请求摘要。采用这个顺序，环境捕获/确认只在新任务准备时读，恢复读取原seed，不读当前MySQL、不发HTTP。
- `CasePlanningWorkflow` 已有统一query/finish和持久回合；在它的真实模型输入增加服务端固定环境区域，不另起环境规划器。完整正式用例及准备仍走原流程，模板不参与执行。
- `CasePlanRecord` 已随Run冻结；增加独立版本化环境依据，包括所选snapshot和当时各需求版本的最新追加确认。它不是部署事实，也不把部分确认扩大到所有点。
- `plan_mapping.build_plan_run` 已特判旧regression字段维护创建hash。新增选择/依据的None必须从旧摘要中去掉；使用显式、集中JSON函数而非全局exclude_none，否则旧合法null数据会丢失。旧正文、业务null、False、0和历史hash均保留。
- PG计划运行事务已先锁project、优先重放、核对固定plan和用例。新绑定在同一事务核对原snapshot/确认PG记录，不使用客户端复制的自报hash。当前MySQL改址不能改变绑定。
- `build_agent_case_context` 是执行依据入口。绑定的新计划通过独立类型字段`AgentCaseExecutionContext.business_environment`注入完整固定依据；`runtime_inputs`保持用户原值，包括同名业务键。缺省的新字段从旧wire/hash省略，业务null/False/0保留。会话创建/重放、审批恢复、上下文hash和核验输入统一使用`execution_json()`。

## 本步界限与后续C2

本步交付正式规划请求/DTO、固定依据、模型输入、PG创建核对及Agent上下文，实际用文件、两个固定业务目标和完整12步JSON验证。配置MySQL、业务依据PG，原件RustFS；不新增依赖、模式、目标探测、任务平台或Git保存机制。

本步**不声称执行限制已实现**。C2还要在创建/领取/开始事务中核对最新确认，精确失效项及依赖形成未执行回执、独立项继续；已执行或未知副作用不能重置/重发。领取与开始之间的确认竞态、运行时选取确认、父失败及新目标回归要真实验收。D正式页面和默认Agent自主执行也保留。

新请求可选择environment_id、snapshot_id及expected_snapshot_sha256；缺省保持旧行为。新规划固定读取完整快照和当时确认，unknown仍是unknown。没有登记/跨项目/错hash的新选择明确拒绝，不静默回退项目base_url。已存计划或运行恢复不要求当前配置仍存在，归档后仍可读取原固定历史。

首轮实际收集发现schema→API模式包→运行时→规划schema的导入环。根因是纯观察契约放在会初始化完整模式的模块内。将三个纯HTTP观察类型移到schemas/http_observation.py，旧模块兼容导出，HTTPX捕获/目标匹配仍留原模块；不改旧字段、版本或序列化。需要用不同导入顺序的独立进程及原HTTP来源专项验收。持久规划checkpoint固定字段也须包括新environment，防止续写时替换原依据。

## 实际复核及已发现根因

三格式固定规划与工具核验实测使用完整12步JSON、两个独立7/9天目标和同release字符串；MySQL改址、追加撤回和归档不替换原计划/Run。创建事务拒绝自重算hash但与PG原快照不一致的伪造依据。独立生产main/API、Outbox、规划CLI完成一条规划物理发送及正式运行创建；原影响两条发送保留，重启恢复没有新发送。该生产场景运行仍排队，不能当作自主Agent执行验收。

复核发现最初把固定环境放入`runtime_inputs`会覆盖合法同名业务输入，已移到独立字段。之后实际恢复回归发现`execute_case_turn`仍用`model_dump`而存档用新wire函数，导致相同执行身份被误报改变；这是序列化契约漏同步，统一到`execution_json()`，保留原失败并复验原图、审批、未知副作用和旧检查点。首次扩大回归另有两项相对证据路径失败，原因是命令在仓库根目录；复验使用测试约定的Agent_Server工作目录，没有修改原证据或放宽核对。

各轮失败原因、实际命令和最终统计见IMPLEMENTATION.md及PLAN的6.36。受控模型/回执证明链路与证据，默认外部模型及完整页面另行验收；本步没有实现最新确认的创建/领取/开始限制。

最终序列化恢复专项85通过、2个外部模型跳过；纯HTTP类型移动后原来源/恢复24通过。最后实际提示/SDK模型HTTP与旧上下文19通过、1个外部模型跳过：三格式各一次真实SDK请求保留完整独立环境JSON与用户同名输入，提示明确固定目标和原权限范围；旧未绑定提示省略新缺省字段。
