---
name: supertest-api-testing
description: 根据 Node.js 应用实际框架审查 SuperTest HTTP 请求、Express/Koa/Fastify 边界、schema、认证、错误和资源清理。用于 Node API 测试与代码审批。
---

# SuperTest API 测试

先读取应用启动方式、测试 runner、HTTP 框架、数据库 fixture 和 package scripts；不要把监听端口或生命周期写死。

## 工作流程

1. 优先测试应用实例边界，避免无必要地启动外部服务器。
2. 对方法、路径、headers、body、状态、响应 schema 和错误结构做显式断言。
3. 覆盖认证/授权、校验、404、异常中间件、分页和幂等。
4. 使用项目既有数据 fixture、事务或临时数据库，测试后关闭连接和 server。
5. 失败时保留请求摘要、响应脱敏摘要、日志和退出码。
6. 实际运行仓库声明的 Node 测试命令。

## 输出边界

当前无 Node Runner 集成时只提供测试设计或静态审查，不声称已执行 SuperTest。
