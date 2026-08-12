---
name: artillery-load-testing
description: 审查 Artillery HTTP、WebSocket 或 Socket.io 场景、阶段、变量、阈值、流量护栏和结果分析。用于 Artillery 项目或性能代码审批，不替代系统现有 k6/JMeter Runner。
---

# Artillery 负载测试

先确认 Artillery 版本、协议插件、目标授权和现有执行命令；不要把 k6/JMeter 配置混入 Artillery。阶段语法和报告字段按需读取 `references/source-1.md`。

## 输入契约

确认协议、业务场景、目标环境、arrival/VU 模型、持续时间、SLA、数据相关性、凭据、并发上限和停止条件。

## 工作流程

1. 以业务目标定义 arrival/ramp/持续阶段、场景权重和数据相关性。
2. 设定响应时间、错误率、吞吐和资源阈值，并说明基线与样本量。
3. 控制并发、目标环境、凭据、测试时段和停止条件，禁止无审批高负载。
4. 对 HTTP、WebSocket 等协议分别验证连接、消息、重试和清理。
5. 保留脚本版本、配置、原始指标、摘要、环境和退出码。
6. 失败区分脚本、目标服务、网络、资源和阈值原因。

## 负载门禁

1. 先运行低负载 smoke 验证脚本、认证、数据和协议，再逐步 ramp；生产须有明确审批。
2. 阶段、权重和样本量必须能解释业务流量，不用无限 VU 或无界重试。
3. 同时记录 p95/p99、错误率、吞吐、业务失败率、资源指标和基线波动。
4. HTTP、WebSocket、Socket.io 分别验证连接、消息、重连、顺序和清理。

## 输出

输出脚本版本、负载模型、阈值、授权、原始指标、环境、分类结论和退出码；当前系统只支持 k6/JMeter，未接 Runner 时不得声称执行 Artillery。

## 执行边界

当前性能运行时只支持 k6/JMeter；Artillery Skill 只能做规划/代码审批，不能声称由现有 Runner 执行。
