# 性能请求正文：实际字节、原生发送与兼容边界

基线：1e97963e。沿当前PerfTarget、API文档ResolvedEndpoint、工具registry、planner、两adapter、runner、实际Docker/清理CLI、报告/PG和既有原件服务查证。正文类型原已允许dict/string/None；没有查到公开的正文插值语法或依赖该语法的消费方。不是新增测试模式或执行器，也不引入依赖。

依据 → 做法：

- 本地`项目借鉴/llm-testing-course/接口测试-通用篇.html`6.2的实际上下游验证，以及`性能测试-通用篇.html`6.1—6.2的单用户完整旅程 → 独立HTTP接收端比较实际正文、方法和Content-Type，再评价标准引擎结果，不以脚本生成/退出0当作业务成功。
- [k6 post](https://grafana.com/docs/k6/latest/javascript-api/k6-http/post/)接受字符串正文、JSON示例显式JSON.stringify → dict保持现有JSON.stringify，string以JSON编码成JavaScript字符串数据，避免反引号/${...}/反斜杠被解释。没有把正文作为脚本运行的可靠契约。
- [JMeter HTTP Request](https://jmeter.apache.org/usermanual/component_reference.html#HTTP_Request)支持原始Body Data和未命名argument；[HTTPSamplerBase](https://jmeter.apache.org/api/org/apache/jmeter/protocol/http/sampler/HTTPSamplerBase.html)明确postBodyRaw/getArguments，[Arguments.getArgument](https://jmeter.apache.org/api/org/apache/jmeter/config/Arguments.html)及[Argument.setValue](https://jmeter.apache.org/api/org/apache/jmeter/config/Argument.html) → 原生HTTPArgument、postBodyRaw、UTF-8，不做urlencode或表单转换。
- 现有JMeter JSR223PreProcessor和[官方组件说明](https://jmeter.apache.org/usermanual/component_reference.html#JSR223_PreProcessor) → 使用既有sampler对象设置正文。Python标准库base64与[Java Base64](https://docs.oracle.com/javase/8/docs/api/java/util/Base64.html)/[Decoder.decode(String)](https://docs.oracle.com/javase/8/docs/api/java/util/Base64.Decoder.html)只运输数据，在树编译后设置argument值，防止XML控制字符/换行规范化以及JMeter函数提前展开；没有加载或执行正文代码。

## 实际复现

`p5-perf-body-before`1失败39.78秒，两个真实缓存Docker引擎均退出0：k6 JSON正确，但三次文本`${1+1}`变2、反斜杠丢失；JMeter六次POST正文全部0字节。接收端422，冒烟均失败。原计划/脚本/接收字节(base64)/SHA/引擎汇总/JTL/双流/原件下载清单和修复前测试源码保留。不是模拟引擎输出。

## 修复及实际验证

`p5-perf-body-fixed`1通过51.35秒，复现的两个目标均正确；两引擎各三轮冒烟和原execute三轮短程报告，四引擎容器/清理回执及两条正式项目PG报告。

`p5-perf-body-verified`1通过50.26秒，七目标：POST对象、POST文本、PUT特殊字节、PATCH嵌套JSON、POST空字符串、PUT None、POST正文与来源bool关联header同时使用。中文/emoji/反引号/字面JavaScript及JMeter函数/反斜杠/CRLF/tab/NUL原文逐字节匹配；对象里的false/0/null/嵌套/未知字段保留。三轮重复执行，两引擎各21次冒烟请求、21次报告请求，共84个真实请求。实际方法和明确的Content-Type一致；当轮来源/消费方正常，标准请求核验通过。四容器/四completed清理回执、两条正式project_id PG报告均pass。

每个RustFS对象均实际下载并校验SHA-256/长度；关键脚本/JTL/统计/manifest/stdout/stderr/log保存本地原字节，厂商dashboard资产只保存实际下载校验清单，不复制整套vendor静态文件。具体数量见validation。所有受管完整容器ID在finally检查不存在，不重置Redis配额或清理状态。

24受影响模块169通过26.56秒，0跳过，1条既有Starlette/AnyIO弃用警告；六个新增回归来自修复前真实输入和响应。compileall只作为附加检查。旧证据不覆写；所有真实运行使用唯一隔离MySQL/PG/Redis和原RustFS，编辑配置/业务报告/原件职责保持。

## 范围与下一步

测试固定计划直接调用生产runner/report，缓存k6 v2.2.0/JMeter5.5；不调用Agent/Step，不代表默认镜像、完整App、持续load或容量验收。未修改前端、表、配置和依赖，未执行生产迁移。Prompt/工具描述同步正文语义。字符串中曾被k6意外执行的`${...}`现在按文字发送；没有新增动态替换语法。原头部关联保持，body动态传值、CSV正文注入、form/multipart、GET/DELETE正文、更多数值边界仍待明确契约和实际验证。

复杂JMESPath、原生null与字符串"null"、数组/对象提取及header类型一致性还未实际验证，不用此批正文测试替代。继续完整Agent/App、负载/环境/版本基线可比性、逐级容量/AI流式、安全工具、人工DeepSeek录制/清洗/遗漏/场景/别名/失败定位/AI使用、全阶段物理HTTP、准备/未知副作用/修订/其他模式、普通文档生成/恢复、业务评审及生产历史迁移。Step按用户决定待账号处理后验收；完整P0—P5保持未完成。
