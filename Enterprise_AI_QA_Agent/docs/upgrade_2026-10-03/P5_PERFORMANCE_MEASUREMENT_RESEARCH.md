# 2026-10-09 Docker性能指标的真实输出与缺失语义

基线055bf573。上一目标轮为进展：提交回执纠正97ac33b6、模板/Excel完整产品验收055bf573。默认Step根据用户决定暂缓，本步不调用真实提供商，也不对DeepSeek压测或扫描。

代码依据 → 验证路径：

- K6EngineAdapter.build_script/run_command/parse_results：生成脚本没有summaryTrendStats，执行使用--summary-export，缺失p(99)被get(...,0)投影为0。JMeter同样将缺失字段读0。先用当前缓存Docker镜像与现有生产PerfRunnerService及DockerResourceManager测量原件；不能先声称只是配置P99即可解决。
- RawMetrics → PerfResultParser → PerfMetrics → PerfReportBuilder：数字默认0，不携带采集可用性；P99缺失时不能显示实测0或判定相应SLA通过。现有SLAResult/验证服务可区分未完成，必须保留“缺少数据”与“超阈值”的区别，不能把缺失伪造成违规数值。
- DockerResourceManager/MockTargetRunner：沿现有Redis租约、PG分配意图及完整容器ID，使用当前缓存http-echo受控目标，不绕过原资源体系。真实stdout/stderr、版本、生成脚本、summary、解析/报告及分配记录保存；测试只清理自身资源。
- 性能测试-通用篇.html第650—709行：逐级加压及记录QPS/P50/P95/P99/错误率；本步小规模受控测试只验证采集/解析链路，不当作容量基线。
- 官方[k6选项](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/)：默认summaryTrendStats为avg,min,med,max,p(90),p(95)，允许p(99)。[自定义汇总](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/)给出handleSummary原对象输出；官方旧issue1611/5736提示--summary-export可能不尊重趋势统计，需以本地镜像版本实测。

这是测量结果契约缺少“是否采到”的架构缺口。先记录真实原件，再确定最小统一契约，在两引擎、SLA/基线/报告、模式输出/验证与既有消费方同步。保留原模式及数值字段兼容，新可用性信息不改写历史。无新依赖或数据库表；不扩大到Mock产品功能或变更其他运行协议。原输出工件缺失、烟测错误处理和完整报告持久化同属原目标，但各自需要实际反例，不能顺手盲修。

## 原件复现后的决定

实际命令见 p5-perf-measurement-before.log/XML：1失败29.93秒。缓存镜像sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec，k6 v2.2.0。默认40个样本、p95约0.534ms，原件没有p(99)，现代码显示P99=0并报告pass。相同生产runner加入--summary-trend-stats后原件确有p(99)=0.7414812。因此保留--summary-export，生成的load/smoke选项都声明summaryTrendStats，不引入handleSummary替换机制。

对应版本[summary-wrapper.js第33—51行](https://github.com/grafana/k6/blob/v2.2.0/internal/js/summary-wrapper.js#L33)明确旧JSON中的嵌套threshold布尔值是!threshold.ok；[summary.go第142—149行](https://github.com/grafana/k6/blob/v2.2.0/internal/js/summary.go#L142)中values包裹格式使用{ok: !LastFailed}。按实际两种格式转换为统一{ok}，保留指标和表达式身份；再用真实违约输出核对。

统一结果采用nullable测量字段：缺失为None/JSON null，实测0仍为0；无样本或SLA所需字段缺失返回inconclusive、passed=null及missing_metrics，确定违约优先fail。两引擎解析、曲线、观测、基线显示、Markdown/HTML同步；PG现有JSON列和前端通用JSON树支持null，无迁移。原件不改写，历史0无法倒推是否缺失。非法汇总抛明确解析错误，由runner已有异常日志/失败路径记录，不再静默空指标。核对现有验证服务对passed=null/非通过报告返回partial，增加跨层回归证据。完整原件生产持久化和烟测行为继续独立调查，本步不虚报已完成。

首次修复后Docker四场景1通过42.29秒、扩大96通过17.36秒。但新违约原件同时证明生成器将0.001ms通过int截为0，脚本/engine表达式和业务SLA不相同。按现有浮点SLA及error_rate字符串生成方式保留延迟小数，新增原件表达式断言并重验；不把首次成功当作修复了这个数值缺陷。中间:g版本Docker1通过34.04秒/扩大97通过17.54秒；最终采用str仅移除整数末尾.0，避免:g默认六位有效数字继续损失精度，追加123.456789断言，重新运行受影响适配器/契约及真实Docker。原有引擎严格<与分析器最大值>在刚好等于阈值时仍可能分歧，本批分歧返回inconclusive，不默认为pass；比较语义的产品统一另需依据，历史结果不重写。
