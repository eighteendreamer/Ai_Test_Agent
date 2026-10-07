"""Apply the researched phase update to the three authorized plan mirrors."""
from pathlib import Path
import hashlib
import json

folder = Path(__file__).resolve().parent
prior = json.loads((folder/'p2-consumer-receipt-validation.json').read_text('utf-8'))
plans = [Path(p) for p in prior['plans']]
raw = plans[0].read_bytes()
assert hashlib.sha256(raw).hexdigest() == prior['plan_sha256']
assert all(p.read_bytes() == raw for p in plans)
text = raw.decode('utf-8')

def replace(old, new):
    global text
    assert text.count(old) == 1, old
    text = text.replace(old,new)

replace('本次研究基线 `a4d6c7e4`','本次研究基线 `e356905d`')
replace('本次复现并修复“清理仍等待审批时，无法单独核验已完成查询”的证据读取缺口，见 2.3.11、6.68。逐项核验沿用完整固定上下文和原工具证据，不完成运行或释放共享数据；实际消费结束状态与清理领取约束尚未接入。上一批输出组合已提交为 `a4d6c7e4`，见 2.3.10、6.67。默认外部模型自主执行仍未通过；受控 Agent 图/业务 HTTP 验证不能代替该验收。',
    '上一批只读逐项核验已提交 `e356905d`，见2.3.11/6.68。本次按真实提前删除反例，接入新可选“消费阶段结束”回执与原清理领取事务，见2.3.12/6.69。完整固定JSON、原证据与原审批保持；关闭消费不完成用例，失败结果仍保留。当前正式项内部两对象场景已验，外部多个消费者、完整生命周期、默认外部模型与完整App仍待验收，完整P0—P5继续。')
replace('本次原件、回执、工具证据与三份同步核对见 [p2-consumer-receipt-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumer-receipt-validation.json)',
    '本次原件、关闭回执、工具证据与三份同步核对见 [p2-consumption-phase-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-phase-validation.json)')
replace('### 2.3.11 清理待审批期间的逐项核验〔内核已验，消费结束状态尚未接入〕',
    '### 2.3.11 清理待审批期间的逐项核验〔前批只读内核；消费状态接入见2.3.12〕')
replace('**本批尚未连接该状态机制，未证明阻止早删；旧真实清理反例继续保留。**',
    '**6.68批次没有连接该状态机制，不能用其通过数证明阻止早删；本次连接范围见2.3.12/6.69，旧真实清理反例继续保留。**')
section = '''### 2.3.12 消费阶段结束与清理领取〔新固定策略；内部混合正式项已验〕

检测缺口仍按2.3的简单方法：AI阅读完整用例，判断必要条件由谁提供、是否要补全，不增加注册/登录等固定清单。执行期间需要另分清“必要消费结束”与“整个用例完成”：同一正式用例包含清理时，清理若等待自己的RunItem终态会互等；按HTTP方法或after顺序也无法证明消费结束。

实际研究依据是原[动作领取事务](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/runtime/postgres_case_action_admission.py)、固定效果声明选择器、完整回执内核及原Agent/API/审批反例，见[P2_CONSUMPTION_PHASE_DESIGN.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_CONSUMPTION_PHASE_DESIGN.md)。采用下列工程做法：

1. 项目配置提供可选`case-consumption-phase-v1`，沿现有MySQL规划配置保存；新计划冻结到PG，要求已有多效果v2和原动作领取协议。旧计划缺该字段继续原行为，关闭当前配置也不替换已冻结运行。
2. Agent首次执行固定声明匹配的清理动作时，在原`_case_action.consumption_closure`附上理由及当前正式目标的消费回执。读取完整固定预期，引用原已保存普通ToolJob的实际输出；不读取生成模板。系统按已有`select_when`匹配清理，不按DELETE/GET命名猜测。
3. 后端在原PG领取事务内核验固定身份、上下文、原普通动作是否已结束，以及消费回执的实际检查。确证passed或failed都能结束消费；空检查、错误来源、未结束/unknown动作不能关闭。系统核验的是提交的明确消费检查及原证据，不保证AI已经理解所有业务预期。
4. 不可变消费结束记录、原逻辑操作登记与首次清理领取同事务提交。事务回滚不留下单独关闭记录；提交响应丢失保留原running任务和关闭记录，原调用恢复不再发送，别名返回unknown及原任务引用。
5. 后续清理复用原关闭记录，可逐个清理两个或更多实际对象；改变关闭依据不能覆盖历史。关闭后新的普通动作被拒；同身份/输入的已知逻辑操作先复用原结果，不新发送。对象产生与清理覆盖仍由原证据链分别核验。
6. 当前拥有者使用明确的消费阶段记录；外部正式消费者实现按同一运行的已保存终态结果及未结束原动作核验，**该外部多消费者组合尚未实际验收**。专门的关闭记录存PG，原件仍RustFS；没有新工具、执行器、模型预算或模式参数映射。

实际验收见6.69：内部正式项创建两个对象→查询→关闭消费→分别清理；正确/错误预期、提前清理、并发、回滚、丢响应、未结束/未知普通动作和错误回执均验证。两项原Agent图/API/六次审批也已通过。关闭记录`run_item_completed=False`，最终完整回执仍保存passed/failed，终态不可重领。关闭证据已在原ToolJob准入记录可查，独立报告展示、外部多个消费者、跨租约恢复、多Worker、完整App、默认自主Agent和生产历史迁移继续待验。

'''
replace('### 2.4 冻结、执行与证据',section+'### 2.4 冻结、执行与证据')
replace('6.68补齐保留pending兄弟身份的只读逐项核验，但未写消费结束状态或接入清理领取事务。新固定策略的逐对象清理覆盖已接并通过6.49实测；实际消费完成及完整生命周期仍待验收，旧策略真实反例继续保留。',
    '6.68补齐只读逐项核验；本次6.69把内部消费回执、关闭记录和清理领取接入原事务并实际验证。新固定策略的逐对象清理覆盖已接并通过6.49实测；外部多个消费者、完整生命周期及未知查证仍待验收，旧策略真实反例继续保留。')
replace('当前已提交基线`a4d6c7e4`，本次逐项核验基础见6.68，上一批输出组合专项见6.67；',
    '当前已提交基线`e356905d`，本次消费阶段结束与原清理事务见6.69，前批逐项核验基础见6.68，输出组合专项见6.67；')
replace('清理待审批期间的逐项核验基础见6.68；旧完整请求/回执语义保持 | 默认新任务逻辑完成但证据阶段超时，尚无业务执行；共享消费完成、未知只读查证、计划修订、其他模式/完整App及多清理通道组合仍待验；历史失败保留 |',
    '逐项核验基础见6.68，内部消费结束与清理领取同事务及原Agent/API/审批见6.69；旧完整请求/回执语义保持 | 默认新任务逻辑完成但证据阶段超时，尚无默认自主业务执行；外部多个消费者、完整生命周期、未知只读查证、计划修订、其他模式/完整App及多清理通道组合仍待验；历史失败保留 |')
replace('继续核对准备输出/依赖传值的真实契约再推进自主执行；共享消费完成、完整准备状态、未知只读查证、计划修订、其他模式及完整产品流程继续 |',
    '继续核对真实契约再推进自主执行；内部消费关闭及原事务已验见2.3.12/6.69，外部多个消费者、完整准备状态、未知只读查证、计划修订、其他模式及完整产品流程继续 |')
text += '''
### 6.69 消费阶段关闭与原清理领取事务〔研究基线e356905d〕

生产修改前研究并再次运行真实提前删除反例，1通过8.05秒表示反例采集测试成功，业务第二目标failed/404；不改写原失败。新可选策略保存MySQL，完整计划/上下文及消费记录在PG，实际原件RustFS。记录与原逻辑登记/清理领取同事务，没有新执行器、依赖或全局模型/预算改动。

最终新模块 **14通过、0跳过（41.02秒）**，实际MySQL/PG/RustFS、完整八步JSON/False/0/null与原工具业务HTTP。十一个直接工具场景及两个原Agent/API/审批场景，另一个旧策略/前置约束测试。原图两场景各七次受控模型决定、六次审批、五次实际业务HTTP；提前清理已拒、两个对象均清理，正确/错误预期分别passed/failed。当前配置关闭后固定策略不变，终态不可重领。受控Agent不等于默认外部模型验收。

并发四领取只实际DELETE一次；关闭INSERT后人为抛错使原领取/登记/关闭一起回滚，重试原未发送任务可继续。实际PG已提交但领取响应丢失时，原running Job无可靠返回且没有DELETE；别名返回case_operation_outcome_unknown及原引用，同原Job恢复抛明确未知，现场两对象保持。不能把关闭存在当作清理成功，也没有实现该unknown的自动只读查证。

空检查、未保存调用、错误目标、queued/running无返回普通动作均拒绝关闭，现场对象保持；历史关闭依据改变被拒，后续逐对象清理仍可继续。已知查询别名只复用原结果；关闭后新普通动作拒绝。原件/hash、固定配置/上下文、原Job、消费记录、HTTP及历史保护核对见[p2-consumption-phase-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-phase-validation.json)。

早期失败完整保留：首6失败1通过10.96秒为旧fixture未支持新分阶段策略；次6失败1通过13.05秒发现新DDL误用TEXT外键，实际原RunItem为UUID；定向1失败5.72秒为夹具遗漏候选和四阶段模型返回；再1失败6.17秒发现逻辑操作消费者未识别新回执字段。对应生产根因已按真实SQL/原调用链修正。随后1失败6通过18.86秒是测试误期待别名也抛异常；按实际unknown别名与原Job分别验证后7通过17.79秒，再边界11通过26.27秒。原图首2失败10.73秒、次2失败15.64秒分别是受控夹具读取错误消息字段和漏mode=ok；核对原tool_message/返回契约后2通过17.08秒，最终14项包含全部当前场景。

前端最终项目配置组件 **65通过（2.42秒）**；当前build含vue-tsc，结果与后端受影响回归见IMPLEMENTATION.md及本批核对记录。源码编译只作附加检查，不作为实际功能通过依据。三份方案全文同步；既有117份原证据、用户.gitignore和无关当前文件分别校验保留。两份旧Worker汇总原字节未恢复限制保持。

当前消费协议仅对新固定策略接入；外部多个消费者、租约恢复/多Worker、独立消费报告展示、完整生命周期、未知只读查证、计划修订及其他模式/完整App尚未验收。生产表初始化沿原动作服务，但未进行生产历史迁移。默认自主Agent原超时未原样重跑，P1正式模板/Excel导入产品、DeepSeek人工录制、Docker真实性能/安全、报告及其余P0—P5继续未完成。
'''
updated = text.replace('\r\n','\n').encode('utf-8')
for path in plans:
    path.write_bytes(updated)
assert all(p.read_bytes() == updated for p in plans)
print(json.dumps({'plans_byte_identical':True,'sha256':hashlib.sha256(updated).hexdigest()},ensure_ascii=False))
