# JMeter响应与依赖传值调研

研究基线：8a2b63f3，2026-10-09。配置MySQL、业务PG、原件RustFS的边界保持；不增加引擎、依赖、数据库表或测试模式。

## 依据与根因

原jmeter_engine_adapter.py为每个target创建ThreadGroup，只生成静态header；SLA下的ResponseAssertion没有预期字符串；smoke命令只设置无消费方的qa_perf.smoke。原runner只读取statistics.json，不能核对具体响应或关联。

真实Docker反例p5-jmeter-response-before：正常通过，期望201/实际200误通过，期望401误失败，login/profile独立线程无法传凭据。使用缓存justb4/jmeter:5.5完整镜像ID，不升级或证明默认alpine/jmeter:5.6.3。

本地课程《接口测试-通用篇》6.2要求获取Token后访问受保护接口验证；《性能测试-通用篇》6.1/6.2要求单用户完整旅程再逐级加压。当前只验短时旅程，不当作容量验收。

官方依据：

- [组件参考](https://jmeter.apache.org/usermanual/component_reference.html)：JMESPathExtractor从JSON生成线程变量，Header Manager覆盖请求header；Response Assertion使用Response Code、Equals/OR和首个断言的Ignore Status，使预期4xx可以通过。使用标准断言后移除临时JSR223状态归一。
- [结果监听器](https://jmeter.apache.org/usermanual/listeners.html)：JTL保存label、responseCode、success等；sample_variables会保存变量原值，因此不能保存Token。仅导出qa_extract_N/qa_dependency_N布尔事实。
- JSR223 Pre/PostProcessor官方提供vars.get/put和Groovy缓存，分别观察消费前和提取后；脚本引用数字索引，不插入用户变量名。

## 采用做法

一个有序ThreadGroup保留target顺序和同线程变量。现有Correlation解析from_target/into_target/header_prefix，默认第一来源和紧邻消费；header只进入指定消费请求。原CSV、吞吐Timer、思考时间和SyncTimer能力保留。

smoke显式覆盖线程数、循环次数并关闭定时调度，避免正式hold_seconds截断所需迭代。load仍使用正式并发和持续时间；这里没有证明原open模型实际速率或spike容量正确。

JTL保留实际状态和不含凭据的布尔字段。共享request_validation读取来源/消费对应label，缺观测为missing，明确false为failed；原报告结合SLA、退出与证据判定。不依据stdout变量名或exit0推断通过。原历史缺JTL标not_observed。

## 实测边界与待做

真实runner/Docker/HTTP/对象存储与清理Worker已执行；独立API创建正式项目/会话。当前不调用Agent或模型，没有完整App或生产ToolJob/JMeter报告恢复验收。缺字段消费当前仍发送空值并返回401，保存失败事实；不把它描述为k6的缺依赖跳过策略。后续应研究同样的跳过/独立目标规则及数组/复杂JMESPath、跨迭代缺失、请求body、更多load/完整报告。

早期sample_variables保存临时凭据的批次原件只留本地、不加入Git，失败日志/XML仍保留。最终布尔证据批次下载原件逐一比对SHA/字节，并用服务器内存签发值验证无泄露。以上不替代完整P0—P5验收；默认Step遵照用户决定待实名认证处理。
