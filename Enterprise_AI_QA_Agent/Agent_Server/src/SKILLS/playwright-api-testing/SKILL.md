---
name: playwright-api-testing
description: 根据项目实际 Playwright 配置规划 APIRequestContext 的请求、认证、schema、错误、并发和证据测试。用于 Playwright API 测试代码和 API 变更审查。
---

# Playwright API 测试

先读取 package scripts、Playwright 版本、baseURL、环境变量和现有 fixtures；不要假设 Node runner 已安装或凭空创建配置。

## 工作流程

1. 从 OpenAPI/接口实现建立方法、headers、参数、请求体和响应断言矩阵。
2. 复用项目 request fixture 和安全凭据注入，禁止把 token 写入源码或日志。
3. 覆盖成功、认证、授权、校验、空数据、分页、超时和服务端错误。
4. 对写操作声明幂等、隔离、清理和并行限制。
5. 保存请求摘要、响应摘要、schema 差异、trace 和退出码，过滤敏感字段。
6. 运行项目现有命令；若只有后端 API Runner，明确不是 Playwright 执行。

## 输出边界

当前系统未注册独立 Playwright API Runner 时仅做方案和代码审查，不声称已运行 Playwright API 测试。
