# JMeter正式执行、报告持久化与恢复调研

基线79c72775。用户已决定先做其他模块，Step实名认证完成后再做真实模型验收。本批不调用模型。

## 依据与范围

- `runtime.py`从plan_resolved生成JMX、guard、smoke、load、analysis到report；PHASE_ANALYZED已有报告恢复入口。
- `perf_metrics_store.py:save_run`返回bool，初始化或写入失败为False；原`_handle_report`未读取返回值，异常也仅debug后继续report_ready。
- `ToolRuntimeService`已有partial状态和工件持久化，PostgresToolJobStore已有旧终态保存和新调用身份；无需新执行器或重置已结束条目。
- 本地课程`性能测试-通用篇.html`6.1/6.2要求单用户完整旅程和逐级负载；`评测工程化与CICD.html`5.1—5.3要求结果可追溯。3VU/4秒仅验证短时正式执行，不能代表容量验收。
- [JMeter组件文档](https://jmeter.apache.org/usermanual/component_reference.html)区分连接和响应超时；ThreadGroup结束不能证明每个请求都按时完成。沿现有非GUI执行、JTL/statistics和报告，不新增引擎协议。
- [Python socketserver文档](https://docs.python.org/3/library/socketserver.html#socketserver.BaseServer.request_queue_size)说明连接请求队列和serve_forever轮询。实际解释器3.11.16的默认request_queue_size=5。

## 原始实测

`p5-jmeter-report-before`失败1项129.93秒：正常旅程smoke成功，正式load82样本、全部状态/提取/header事实通过，但P99=69315ms，报告正确fail。最慢样本JTL为Connect=69312ms、Latency=69315ms；证据定位在连接建立，不能推断为业务鉴权或解析失败，也不把它称为已证明的scheduler错误。

受控目标的默认5连接队列、0.5秒轮询可能影响短时并发（此处是推断）。新增验收目标显式128队列、0.01秒轮询，保留现有Target默认行为；不改变生产JMX、并发/时长/SLA，也不放宽报告判定。旧失败完整保留。

## 实际判定与采用实现

使用隔离MySQL/PG/RustFS/Redis、独立生产API和实际缓存JMeter5.5。测试直接提供固定计划，仅覆盖生产工具/guard/执行/报告；缓存镜像选择注入不证明默认5.6.3镜像配置。pg-before失败1项141.90秒，真实PG CHECK约束拒绝新指标记录，工具却返回completed/report_ready，无对应指标行。不是注入Python假的save_run返回值。

已修正为读取save_run返回值：失败保留已完成引擎、报告及原件，工具partial、阶段analyzed；恢复时用新ToolJob仅重试已有报告保存，不再次压测、不覆盖旧partial结果。持久化状态与实际SLA结论分开，并同步顶层输出、metrics JSON和提示。结构化日志记录run/plan/table/error_type，数据库异常字符串可能包含绑定参数，不直接打印。

最终verified 1通过199.85秒：三种正式load共6次引擎，三线程87/87/92样本；两次恢复/重放类核验均0新增引擎，PG三行pass/fail/pass，旧partial保留。独立API重启后1084次下载均校验，服务器所有实际签发值未进入下载内容；76份重要原件保留。六份清理回执及完整容器不存在已核对。21模块149通过；真实k6测量44.27秒、工具/原件66.16秒均1通过。原失败和最终工件不覆盖，详见PLAN6.92/IMPLEMENTATION。

未覆盖：模型/完整Agent/App、逐级容量/流式指标、JMeter复杂body/数组/跨迭代、缺依赖跳过及更多独立目标、安全工具、人工录制和其余P0—P5待办。另发现PerfMetricsStore基线按URL查找且无project_id，需要单独复现跨项目行为后处理，不能宣称项目基线隔离已验。
