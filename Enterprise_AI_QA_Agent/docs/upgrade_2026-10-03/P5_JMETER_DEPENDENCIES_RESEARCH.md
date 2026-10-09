# JMeter依赖缺失、独立目标与轮次隔离调研

基线d3d03ec4。完整P0—P5继续；默认Step按用户决定待账号处理。本批不调用模型。

## 依据与根因

已读JMeter adapter、request_validation、runner、报告/Prompt和实际JMeter响应测试。原生成器仅以JSR223PreProcessor记录qa_dependency布尔值，无请求前依赖控制；ThreadGroup.on_sample_error=continue，所以缺值仍发送header。JMESPathExtractor在实际来源请求之后运行，没有首轮/下一轮统一重置；被跳过的来源不能允许沿用上一轮变量。k6已有按目标局部跳过、继续独立目标的同类实现。

本地接口测试通用篇6.2要求用真实受保护请求验证token，性能测试通用篇6.1/6.2要求完整单用户旅程和逐级负载。本批先补真实短程旅程，不冒充容量测试。

官方[If Controller](https://jmeter.apache.org/usermanual/component_reference.html#If_Controller)说明条件控制子采样并支持Groovy变量表达式；[组件参考](https://jmeter.apache.org/usermanual/component_reference.html#JSR223_PreProcessor)和[JMeterVariables](https://jmeter.apache.org/api/org/apache/jmeter/threads/JMeterVariables.html)支持采样前执行、线程局部变量、getIteration和remove；[标准JTL变量](https://jmeter.apache.org/usermanual/listeners.html#sample_variables)可保存附加列。官方主分支IfController及Schema源码核对condition/useExpression/evaluateAll字段；v5.5 raw地址404后改读官方主分支，不推断已升级版本。实际使用已有缓存JMeter5.5，字段与行为须通过实际容器验证。

## 实际反例

p5-jmeter-dependencies-before：1失败55.61秒。真实受控HTTP、实际JMeter、生产Docker资源管理/runner/RustFS、正式API项目/会话/隔离存储；三轮缺失来源每轮仍调用bridge和final，均401，且发送空值Authorization。独立目标也执行。物理请求12个而合理执行仅source/independent共6个。原JMX、汇总/JTL、双流/对象下载、清理回执、脱敏HTTP事实及修复前测试源码保留。该失败并非只看XML或mock执行器。

## 采用方式

这是依赖执行约束缺失，复用JMeter标准IfController包裹每个消费目标，且全部必要来源当轮已提取才执行。没有依赖的目标保持执行。第一目标的JSR223PreProcessor清空全部线程局部关联/核验变量，记录标准轮次；不共享跨线程/跨轮凭据。迭代身份和布尔事实通过已有sample_variables保存，不增加假采样污染吞吐/时延。

读取器按threadName/qa_iteration关联来源和消费者，有明确来源提取false或已证实上游跳过且实际没有请求，才记录skipped_targets；单纯没有样本仍是证据缺口。意外发送缺值请求列失败。跳过没有HTTP响应，不计为200/成功；当前目标和关联缺口仍判失败，独立请求实际指标保留。老JTL无轮次列继续原核验方式，不重写历史。报告/JSON/Prompt同步跳过详情，凭据值仅存线程内，实际下载逐字节核对无临时凭据。

## 验证状态

原反例1失败55.61秒；首次缺值实测fixed 1通过53.27秒，HTTP请求由12个变6个，两级消费目标各跳过3轮，独立目标保留。首次43项合同通过6.67秒；增加从实际JTL出发的缺证据/非法身份/重复来源/旧格式回放和报告测试后52通过7.17秒。

verified 1通过147.32秒，五个实际冒烟场景：全部缺值、单线程成功/缺值交替、双线程正常三轮、消费方在末尾、一个消费方同时依赖两个来源。总请求分别6/12/24/6/9，正常场景通过，其余保留失败和具体跳过，不扩大为停止整条旅程。另两次实际execute使用明确的三轮/单线程短程选项，经真实parser/report/runtime保存PG：缺值仅6个实际HTTP样本，SLA通过但report fail；正常12个实际样本、report pass；均退出0、保存成功。7个受管引擎容器不存在，7completed清理回执。非持续load或容量验收。

最终delivery补充标准[线程函数](https://jmeter.apache.org/usermanual/functions.html#__threadNum)及getIteration只作测试追踪HTTP头，每个来源/消费方按线程和轮次比较凭据哈希，1通过149.28秒。正常凭据逐轮改变，两级依赖没有跨线程/上一轮误用；不是只检查凭据全局存在。全批每次RustFS下载均校验SHA/大小并核对不存在临时凭据，原字节保留。

原五场景JMeter响应回归1通过116.76秒：状态200、错误201、预期401、缺值、正常链；缺值只请求来源3次。最终23模块163通过35.21秒、0跳过，既有Starlette/AnyIO警告1条。前一次扩大命令误写test_mode_skills.py，退出4、0测试；原.log/XML保留，改用实际test_performance_mode_skills.py后才计通过。compileall退出0仅为附加检查。

旧JTL没有轮次列仍按原格式核验，有缺口不猜跳过；原工件不重写。没有新配置、数据库迁移、依赖或前端代码；JSON字典和原结果工件透传，报告/Prompt添加跳过信息。固定计划使用缓存JMeter5.5和生产runner/实际存储，不证明完整Agent/App、默认5.6.3镜像配置、HTTP body与复杂JMESPath/数组值处理或容量测试。完整P0—P5保持未完成。
