---
name: postman-api-testing
description: 审查 Postman collection、environment、变量作用域、预请求/测试脚本、Newman 命令和 API 证据。用于已有 Postman 流程、API 变更和代码审批。
---

# Postman API 测试

先读取仓库中的 collection、environment 模板、CI 脚本和凭据注入方式；不要提交真实 token 或环境密钥。Newman 版本和 reporter 细节按需读取 `references/source-1.md`。

## 输入契约

确认 collection 版本、请求依赖顺序、变量作用域、认证、测试数据、目标环境和允许的网络范围。environment 文件中的敏感变量必须是占位符。

## 工作流程

1. 按业务流程和依赖顺序审查请求、变量、认证和清理。
2. 对状态码、headers、schema、错误结构、分页、幂等和安全数据做显式断言。
3. 将环境变量按作用域分层，敏感值从 CI secret 注入并在日志中脱敏。
4. 失败时保留请求标识、响应摘要、脚本错误和 collection 版本。
5. Newman 只使用项目已声明的命令、reporter 和退出码门禁。
6. 将 mock、监控和真实环境执行清楚区分。

## 审查门禁

1. 每个请求显式断言状态码、关键 headers、schema、错误结构、分页和幂等性。
2. pre-request 脚本只准备当前请求所需变量，不隐藏业务断言或跨用例可变状态。
3. tests 脚本按 message、字段路径和业务条件失败，禁止只检查响应存在。
4. Newman 命令必须来自仓库脚本，固定 collection/environment 版本、超时和 reporter；退出码纳入 CI 门禁。
5. 失败证据保留请求名、脚本错误、脱敏响应、运行环境和 collection commit。

## 输出

输出请求流程图、变量风险、断言矩阵、脚本兼容性、执行命令和退出码；无 Newman Runner 时仅静态审查。

## 输出边界

当前系统无 Postman/Newman Runner 时只做 collection 静态审查，不声称已执行 Newman。
