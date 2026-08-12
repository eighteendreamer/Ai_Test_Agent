---
name: postman-api-testing
description: 审查 Postman collection、environment、变量作用域、预请求/测试脚本、Newman 命令和 API 证据。用于已有 Postman 流程、API 变更和代码审批。
---

# Postman API 测试

先读取仓库中的 collection、environment 模板、CI 脚本和凭据注入方式；不要提交真实 token 或环境密钥。

## 工作流程

1. 按业务流程和依赖顺序审查请求、变量、认证和清理。
2. 对状态码、headers、schema、错误结构、分页、幂等和安全数据做显式断言。
3. 将环境变量按作用域分层，敏感值从 CI secret 注入并在日志中脱敏。
4. 失败时保留请求标识、响应摘要、脚本错误和 collection 版本。
5. Newman 只使用项目已声明的命令、reporter 和退出码门禁。
6. 将 mock、监控和真实环境执行清楚区分。

## 输出边界

当前系统无 Postman/Newman Runner 时只做 collection 静态审查，不声称已执行 Newman。
