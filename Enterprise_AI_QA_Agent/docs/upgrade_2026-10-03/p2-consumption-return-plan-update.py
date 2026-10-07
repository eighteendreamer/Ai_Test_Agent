"""Update the authorized plan mirrors from the previous verified bytes."""
from pathlib import Path
import hashlib
import json

folder = Path(__file__).resolve().parent
prior = json.loads((folder/'p2-consumption-phase-validation.json').read_text('utf-8'))
plans = [Path(p) for p in prior['plans']]
raw = plans[0].read_bytes()
assert hashlib.sha256(raw).hexdigest() == prior['plan_sha256']
assert all(p.read_bytes() == raw for p in plans)
text = raw.decode('utf-8')

def replace(old,new):
    global text
    assert text.count(old) == 1, (text.count(old),old)
    text = text.replace(old,new)

replace('本次研究基线 `e356905d`','本次研究基线 `67d134ae`')
replace('上一批只读逐项核验已提交 `e356905d`，见2.3.11/6.68。本次按真实提前删除反例，接入新可选“消费阶段结束”回执与原清理领取事务，见2.3.12/6.69。完整固定JSON、原证据与原审批保持；关闭消费不完成用例，失败结果仍保留。当前正式项内部两对象场景已验，外部多个消费者、完整生命周期、默认外部模型与完整App仍待验收，完整P0—P5继续。',
    '消费阶段与原领取事务已提交 `67d134ae`，见2.3.12/6.69。本次实际复现并修复“取消无可靠返回仍被当作消费结束”和“自动清理等待误要求其他消费者通过”，见2.3.13/6.70。内部十五项、外部九种原图/API/审批场景及四项历史/来源边界已验；明确业务前置仍要求通过，旧运行按原映射恢复。完整固定JSON、原证据和审批保持；默认自主Agent、完整生命周期与App及其余P0—P5继续未完成。')
replace('本次原件、关闭回执、工具证据与三份同步核对见 [p2-consumption-phase-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-phase-validation.json)',
    '本次原件、返回观察、共享消费/依赖及三份同步核对见 [p2-consumption-return-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-return-validation.json)')
replace('**该外部多消费者组合尚未实际验收**','**6.69批次未验外部组合；本次固定共享来源的两正式消费者实测见2.3.13/6.70**')
replace('独立报告展示、外部多个消费者、跨租约恢复、多Worker、完整App、默认自主Agent和生产历史迁移继续待验。',
    '本段6.69当时未验外部多个消费者；本次两正式消费者共享来源见2.3.13/6.70。独立报告展示、跨租约恢复、多Worker、完整App、默认自主Agent和生产历史迁移继续待验。')
section = '''### 2.3.13 消费返回证据与清理等待语义〔真实反例修复；共享两消费者已验〕

用例选取、缺口检测仍按2.3：AI读完整用例，判断信息/状态由谁提供，查询项目依据、补全并排序。这里完善的是执行后的清理条件；不增加注册/登录等业务检测清单，不把用例转换成模式参数。

依据原[规划轮次](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_cases/planning_workflow.py)、[范围归一](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/preparation_scopes.py)、[运行映射](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/test_runs/plan_mapping.py)、原ToolOutputObservation和实际原图/审批/HTTP反例，调研见[P2_CONSUMPTION_RETURN_RESEARCH.md](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/P2_CONSUMPTION_RETURN_RESEARCH.md)。采用两项根因修复：

- **确认已开始动作确有可靠返回。** 原取消只修改状态，不产生返回观察。内部普通动作、外部消费者已开始的原Job和关闭记录恢复共用判定：状态结束；若已经领取开始，还必须具有结构有效的原可靠输出观察。真实执行后返回丢失，即使随后取消，也保留unknown现场。已有返回后取消，或后续工件交付partial，不误判为无返回；最终工件处理可改变output，不能要求它的SHA与原观察SHA相等。可靠返回只证明工具返回检查点，成功、原对象和业务通过仍由各自证据判定。
- **清理自动等待与明确业务前置分开。** 原logic轮保存模型声明、preparation_scope_declaration和logical_hash。后端按原归一/排序重建并核对原哈希及最终固定逻辑，识别系统为共享清理自动加入的等待边；新映射用现有terminal语义等待消费者结束。模型明确声明的业务depends_on及实际required输出绑定仍保持passed/原数据要求，没有原声明就不猜测放宽。

新消费记录在既有normal_job_refs追加原返回观察SHA；读取旧记录不补字段、不重写原hash，但仍核验原动作。外部准入保存同一运行原终态结果及所有已开始Job的身份、原准入/逻辑索引、输出/返回观察SHA；原PG清理领取事务内只读核验，不在锁内调用AI。外部结果failed可结束消费，但它仍作为failed历史保存；未返回动作不能被该结果掩盖。

新启用消费协议的运行快照固定`dependency_mapping_version=case-cleanup-waits-v1`，纳入创建SHA；缺该字段的旧快照维持旧序列化、原依赖和创建SHA。首次提交选新映射，原创建请求重放先读取已存在运行，再按它原映射核对；事务校验也按固定版本重建。此批没有新配置键、表、依赖、执行器或工具；MySQL配置、PG业务/运行与RustFS原件分工保持，前端快照类型同步。

实际验证见6.70：共享来源由首个正式消费者的before准备产生，两个正式消费者使用；最后清理由after准备或正式项自身执行。明确通过要求失败时不自动放宽；自动清理等待可在另一个正式项failed且原prepare已成功时继续。终态不能重领。受控模型证明原图/API/审批及工具链路，不代表默认自主模型或完整App验收。

'''
replace('### 2.4 冻结、执行与证据',section+'### 2.4 冻结、执行与证据')
replace('正式前置要求通过；准备条件按准备自身结果判断，所属目标失败不抹去准备成功。',
    '明确业务前置要求通过；系统为共享清理自动增加的等待在新固定映射中要求消费者结束，见2.3.13。准备条件仍按准备自身结果和必要实际输出判断，所属目标失败不抹去准备成功。')
replace('外部多个消费者、完整生命周期及未知查证仍待验收，旧策略真实反例继续保留。',
    '本次两正式消费者共享来源和返回未知边界见6.70；完整生命周期及未知只读查证仍待验收，旧策略真实反例继续保留。')
replace('内部消费关闭及原事务已验见2.3.12/6.69，外部多个消费者、完整准备状态、未知只读查证、计划修订、其他模式及完整产品流程继续',
    '内部消费关闭及原事务见2.3.12/6.69，实际返回与两消费者共享清理/自动等待修复见2.3.13/6.70；完整准备状态、未知只读查证、计划修订、其他模式及完整产品流程继续')
replace('当前已提交基线`e356905d`，本次消费阶段结束与原清理事务见6.69，',
    '当前已提交基线`67d134ae`，本次可靠返回、两消费者共享清理及版本化自动等待修复见6.70，原消费阶段与清理事务见6.69，')
replace('内部消费结束与清理领取同事务及原Agent/API/审批见6.69；旧完整请求/回执语义保持 | 默认新任务逻辑完成但证据阶段超时，尚无默认自主业务执行；外部多个消费者、完整生命周期、未知只读查证、计划修订、其他模式/完整App及多清理通道组合仍待验；历史失败保留 |',
    '内部消费结束/原事务见6.69，可靠返回及共享两消费者、明确业务依赖和旧映射重放见6.70；旧完整请求/回执语义保持 | 默认新任务逻辑完成但证据阶段超时，尚无默认自主业务执行；更多消费者/来源组合、多Worker/跨租约、完整生命周期、未知只读查证、计划修订、其他模式/完整App仍待验；历史失败保留 |')
replace('当前消费协议仅对新固定策略接入；外部多个消费者、租约恢复/多Worker、独立消费报告展示、完整生命周期、未知只读查证、计划修订及其他模式/完整App尚未验收。',
    '6.69当时仅验证内部新固定策略，未验外部多个消费者；本次两消费者共享来源见6.70。租约恢复/多Worker、独立消费报告展示、完整生命周期、未知只读查证、计划修订及其他模式/完整App尚未验收。')
text += '''
### 6.70 可靠返回、外部共享消费者与自动清理等待〔研究基线67d134ae〕

实现前查原取消/可靠返回、逻辑原声明和依赖映射，并保留实际失败输入。内部原GET真实返回后在持久观察前异常，再用原cancel_job取消；旧代码仍写关闭记录并DELETE。新断言首次**1失败/14未选中（6.57秒）**，不是“已验证可用”。修复后定向**1通过（5.90秒）**；最终当前模块**15通过、0跳过（44.15秒）**，包含前批十四项和这个新增真实反例，原件/Job/HTTP及失败现场均保留。

外部首次**4通过、2失败（34.03秒）**。取消无返回已经阻止DELETE，但受控Agent错误引用被拒清理不存在的response_status，最终error；按原返回构造blocked回执修正测试。正式项内共享清理则确认生产缺陷：左消费者实际200与固定201不符，原failed结果保留，seed已成功；自动加入的left等待被映射为passed，right无法领取。新版本只将可重建的自动等待映射terminal，明确业务前置不变。修复六场景**6通过（34.47秒）**。

增加明确业务依赖/必要数据绑定失败、旧映射真实PG重放及原67d文件的旧快照/创建SHA、篡改原声明/哈希/固定依赖边界。首次**12通过、1失败（48.76秒）**为新旧运行测试遗漏原create_planned_run必需关键字；按原方法签名修正夹具，没有修改生产契约。最终外部模块**13通过、0跳过（48.14秒）**：九个实际MySQL/PG/RustFS、原Agent图/API/审批/业务HTTP场景，另四项原输入历史和篡改边界。

六个实际执行到最后消费者的场景均保留完整八步/False/0/null原件和送达上下文：正常及failed消费者分别清理成功；可靠返回后cancelled及交付partial也允许结束；取消无返回场景最终blocked、零DELETE、共享对象保留。提前清理拒绝、不发送。正常场景4业务HTTP，可靠返回边界5HTTP；取消未知场景4HTTP且没有DELETE。明确业务前置失败、必要输出失败与旧映射三场景均不领取右项，2HTTP、零清理、保留对象。原创建请求在当前配置关闭及左项终结后仍重放原运行；已结束运行不重领。

前端仅增加可选快照DTO，实际`npm.cmd run typecheck`退出0。受影响回归的最终命令与结果在IMPLEMENTATION.md及[p2-consumption-return-validation.json](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/p2-consumption-return-validation.json)记录；源码编译是附加检查，不能替代上述功能测试。三个方案全文同步，上一批227份原证据/HEAD及用户.gitignore/无关当前文件按SHA检查，历史两份Worker汇总原字节未恢复限制保留。

这批验证的是工具可靠返回与原清理准入，不证明所有远端副作用已结束或全部业务通过。更多消费者/来源组合、跨租约/多Worker、独立消费报告、完整准备生命周期、unknown只读查证、计划修订/其他模式、完整App和默认自主Agent继续待验。未重复同配置默认超时、未进行生产历史迁移；P1模板/Excel正式产品、DeepSeek人工录制、Docker真实性能/安全、P0产品链路及其余P0—P5保持未完成。
'''
updated = text.encode('utf-8')
for p in plans:
    p.write_bytes(updated)
assert all(p.read_bytes() == updated for p in plans)
print(json.dumps({'plans':len(plans),'byte_identical':True,'sha256':hashlib.sha256(updated).hexdigest()},ensure_ascii=False))
