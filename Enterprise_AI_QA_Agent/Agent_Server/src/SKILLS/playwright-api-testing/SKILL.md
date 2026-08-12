---
name: playwright-api-testing
description: 根据项目实际 Playwright 配置规划 APIRequestContext 的请求、认证、schema、错误、并发和证据测试。用于 Playwright API 测试代码和 API 变更审查。
---

# Playwright API 测试

先读取 package scripts、Playwright 版本、baseURL、环境变量和现有 fixtures；不要假设 Node runner 已安装或凭空创建配置。APIRequestContext 选项和证据格式按需读取 `references/source-1.md`。

## 输入契约

取得 OpenAPI/实现路由、认证与凭据注入、数据种子、并行限制、服务地址和现有 Playwright project 配置；敏感值只允许来自受控环境。

## 工作流程

1. 从 OpenAPI/接口实现建立方法、headers、参数、请求体和响应断言矩阵。
2. 复用项目 request fixture 和安全凭据注入，禁止把 token 写入源码或日志。
3. 覆盖成功、认证、授权、校验、空数据、分页、超时和服务端错误。
4. 对写操作声明幂等、隔离、清理和并行限制。
5. 保存请求摘要、响应摘要、schema 差异、trace 和退出码，过滤敏感字段。
6. 运行项目现有命令；若只有后端 API Runner，明确不是 Playwright 执行。

## 用例门禁

1. 每个请求明确方法、路径、headers、query/body、状态码和字段级 schema 断言。
2. 覆盖成功、空、校验、认证/授权、404、限流、超时、5xx、分页和幂等写操作。
3. 写操作使用独立数据、清理或事务；并行执行前证明不会共享账号和资源。
4. 禁止将 token、完整响应或个人数据写入 trace、console 或报告；日志只保留脱敏摘要。
5. 失败时保留请求摘要、响应摘要、trace、关联服务日志和退出码。

## 输出

输出 fixture/环境依据、接口矩阵、数据隔离、断言缺口、实际 Playwright 命令和执行证据。未注册 Runner 时仅做设计和审查，不声称执行。

## 输出边界

当前系统未注册独立 Playwright API Runner 时仅做方案和代码审查，不声称已运行 Playwright API 测试。
