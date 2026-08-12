---
name: web-performance-audit
description: 结合项目实际 Lighthouse、PageSpeed 或浏览器指标规划页面性能、Core Web Vitals、资源、缓存和可访问性审计。用于 Web 性能模式和代码审批。
---

# Web 性能审计

先确认审计工具、浏览器、网络/CPU 模拟、页面状态和基线；不要将单次实验室分数当作真实用户结论。

## 工作流程

1. 定义 URL、登录状态、设备、网络、缓存冷热、采样次数和版本。
2. 记录 LCP、INP、CLS、TTFB、资源瀑布、JS/CSS 体积和错误。
3. 区分实验室指标、现场数据、业务交互和后端瓶颈。
4. 对每项建议关联资源/代码证据、用户影响、风险和回归指标。
5. 重复采样并报告波动范围，禁止只挑最好分数。
6. 保存原始报告、配置、页面版本和退出码。

## 执行边界

当前没有 Lighthouse 专用 Runner；可复用浏览器探索但不得声称完成 Lighthouse/PageSpeed 自动审计。
