# 性能响应的原生类型、路径语法与下游传值

基线06ae1f33。读取现有两adapter、关联绑定、JTL/check读取、冒烟、报告、Prompt、实际Docker/清理Worker与原件服务后实施。保留当前测试模式、Agent和正式项目归属；编辑配置MySQL、报告PG、原件RustFS职责不变，无新增依赖或表。

## 依据与做法

- 当前JMeter stock JMESPathExtractor将数组拆为匹配列表，matchNumber=1只保留第一项，其他值转为字符串；旧代码以字符串是否为`null`判断可用性。实际[5.5源码](https://raw.githubusercontent.com/apache/jmeter/rel/v5.5/src/components/src/main/java/org/apache/jmeter/extractor/json/jmespath/JMESPathExtractor.java)证明类型丢失发生在提取器。采用原生JSR223PostProcessor、公开[JMESPathCache](https://jmeter.apache.org/api/org/apache/jmeter/extractor/json/jmespath/JMESPathCache.html)及已随引擎提供的Jackson树解析；不自写路径解析器。
- [JMeterVariables](https://jmeter.apache.org/api/org/apache/jmeter/threads/JMeterVariables.html)支持getObject/putObject，Jackson [JsonNode](https://javadoc.io/static/com.fasterxml.jackson.core/jackson-databind/2.13.3/com/fasterxml/jackson/databind/JsonNode.html)支持原生类型判断。完整值仅在线程局部保存；JTL只保存类型、是否提取和是否可用于header。对象/数组可提取，但不能隐式变成首项或字符串header；0、false和字符串`null`有效，JSON null/空字符串/缺值无效。
- 来源状态必须符合已有expect_status才能为消费者提供值；缺值只跳过相关消费者，独立目标继续，沿已有线程/轮次隔离和正式清理回执，不放开所有权或配额。
- [k6 JSON(selector)](https://grafana.com/docs/k6/latest/javascript-api/k6-http/response/response-json/)使用[GJSON](https://github.com/tidwall/gjson)，JMeter使用[JMESPath](https://jmespath.org/tutorial.html)。数组索引、筛选和含点键名使用各自原生语法；Prompt明确差异，不承诺复杂表达式跨引擎通用。实际[k6 v2.2.0源码](https://raw.githubusercontent.com/grafana/k6/v2.2.0/js/modules/k6/http/response.go)已验证完整JSON字节，当前k6无需修改。
- Jackson [DeserializationFeature 2.13源码](https://raw.githubusercontent.com/FasterXML/jackson-databind/2.13/src/main/java/com/fasterxml/jackson/databind/DeserializationFeature.java)说明FAIL_ON_TRAILING_TOKENS默认关闭、初始化须先配置再使用。扩展实际复现证实JMeter尾部额外JSON仍通过并发送下游；在首次创建线程局部mapper时启用该标准选项，后续复用，不把仅解析第一个值当作完整响应合法。
- 本地课程`接口测试-通用篇.html`6.2与`性能测试-通用篇.html`6.1—6.2要求实际上下游和单用户旅程证据。受控HTTP记录消费者是否收到正确值的哈希/匹配事实，不记录凭据。实际两引擎、runner、report、PG和RustFS验证，不用exit0代替请求成功。

## 已确认的原始失败

p5-perf-typed-reproduction：1失败78.01秒，两个真实缓存Docker引擎各四场景。k6符合预期；JMeter把字符串`null`误判缺值，把对象及数组错误地发送给下游，来源403但期望200仍向下游传值。8个容器完成正式清理回执且finally确认不存在；原脚本/JTL/响应事实/对象下载字节保留。此前before 1失败81.06秒因夹具在指标表惰性初始化前查询表；纠正查询时机，不修改生产存储算法。

## 首轮修复验收

p5-perf-typed-fixed：1通过110.36秒，原八项冒烟及四份实际PG报告。字符串`null`pass、对象header fail，均exit0且SLA通过；类型与下游binding独立展示。12个completed清理回执。旧JTL缺少新类型字段仍按原布尔观测读取，不宣称历史类型已验证，不改历史结果。

6项回放回归通过0.16秒，覆盖实际类型、来源状态、缺少header事实不能推断准备完成、旧JTL观察契约；原24模块169通过26.61秒。扩展首轮69.49秒失败是测试向非nullable字符串into_header传None，改为现有空字符串契约；原失败输出保留，不扩大生产接口。

p5-perf-typed-expanded-reproduction：1失败331.43秒，36项实际冒烟全部执行。JMeter尾部拼接JSON错误通过，三轮消费请求实际发送；启用完整输入解析前的脚本、JTL、下载及测试源码固定保存。k6同异常正确失败；此轮筛选场景其中一次来源dial i/o timeout，0/200状态真实记录为失败，前一批同表达式成功证据保留，未修改k6路径机制或添加自动重试。

p5-perf-typed-verified：1失败346.96秒，36项冒烟均符合预期，四份报告实际PG入库pass/fail/pass/fail，但最终清理只有38/40回执。p5-perf-typed-dependencies-regression：1失败120.88秒，五个旅程和两份报告符合预期，清理只有6/7回执。原stderr证明Docker全局inventory遇到RustFS临时目录lstat失败，生产handler留下错误并转交已有延迟重试；每次只执行一轮的测试夹具不能证明队列已排空。改为启动独立生产清理Worker持续处理，逐项查询实际PG completed回执，60秒期限并检查进程存活；不修改生产清理、配额、Redis重试或Docker所有权约束。失败原件和完整容器不存在证据保留。

最终受影响25模块182通过28.33秒、0跳过，1条既有Starlette/AnyIO弃用警告。新增13回放来自实际原JTL和生产失败输入。两引擎正文回归1通过68.65秒，原字节、来源bool/消费header、84个实际HTTP请求及两份PG报告保持正确。完整清理Worker最终结果另列，不能把上述失败批次改写为通过。

## 范围

实际缓存k6 v2.2.0/JMeter5.5，无镜像升级，不证明当前默认镜像。固定计划直调生产runner/report，没有真实Agent、Step或完整App，也不是持续负载或容量验收。更多数值/编码/非法表达式、动态正文传值、form/multipart、基线可比性、逐级容量/AI流式、安全、人工DeepSeek/UI清洗与知识、全阶段HTTP和其余P0—P5继续；Step按用户决定等账号处理后验收。

最终delivery **1通过279.29秒**：36项冒烟与四份正式项目PG报告，40条completed回执/40个完整容器不存在；独立dependencies-verified **1通过104.40秒**，原5旅程、2报告和7回执。失败前批保持，最终原件/历史/三份方案由p5-perf-typed-audit.py核对；具体数量见validation。最终compileall退出0仅为附加检查。全部实际进程已结束，无默认AI调用。

本批审计：12212次实际对象下载SHA/大小核验、2888份本地原件校验；旧3662份工件保持，三份方案逐字节一致。上述数量包含重复回归对象读取，不表示独立功能覆盖数。
