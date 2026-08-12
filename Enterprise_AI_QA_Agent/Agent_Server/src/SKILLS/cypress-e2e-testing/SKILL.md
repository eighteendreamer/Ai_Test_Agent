---
name: cypress-e2e-testing
description: 根据 Cypress 项目实际配置审查端到端、组件、网络拦截、fixture、选择器、重试和异步同步。用于 Cypress 测试代码或 UI 代码审批。
---

# Cypress E2E 测试

先读取 Cypress 版本、config、support、fixtures、CI 命令和应用启动方式；不要假设 Cypress Runner 已接入当前系统。

## 工作流程

1. 从用户旅程定义前置、操作、断言、清理和证据。
2. 使用稳定 data 属性或语义选择器，避免脆弱 CSS 和坐标。
3. 网络拦截必须对应真实契约，并覆盖成功、错误、延迟和重试。
4. 依赖 Cypress 重试机制和可观察条件，不使用固定 sleep。
5. 覆盖认证、刷新、跨页面、错误恢复和写操作隔离。
6. 运行项目已有 Cypress 命令并保留视频、截图、日志和退出码。

## 执行边界

当前 UI Runner 基于 Playwright；没有 Cypress Runner 时只做代码审查/迁移建议，不声称执行 Cypress。
