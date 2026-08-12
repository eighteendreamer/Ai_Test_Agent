---
name: supertest-api-testing
description: 根据 Node.js 应用实际框架审查 SuperTest HTTP 请求、Express/Koa/Fastify 边界、schema、认证、错误和资源清理。用于 Node API 测试与代码审批。
---

# SuperTest API 测试

先读取应用启动方式、测试 runner、HTTP 框架、数据库 fixture 和 package scripts；不要把监听端口或生命周期写死。框架差异和 agent 生命周期示例按需读取 `references/source-1.md`。

## 输入契约

确认 app 实例/服务器边界、路由、中间件、认证、数据 fixture、并行策略和资源关闭责任。

## 工作流程

1. 优先测试应用实例边界，避免无必要地启动外部服务器。
2. 对方法、路径、headers、body、状态、响应 schema 和错误结构做显式断言。
3. 覆盖认证/授权、校验、404、异常中间件、分页和幂等。
4. 使用项目既有数据 fixture、事务或临时数据库，测试后关闭连接和 server。
5. 失败时保留请求摘要、响应脱敏摘要、日志和退出码。
6. 实际运行仓库声明的 Node 测试命令。

## 质量门禁

1. 优先向应用实例发请求，只有需要真实网络边界时才监听端口，并记录原因。
2. 每个请求断言方法、路径、headers、状态、schema、错误结构和用户可观察结果。
3. 覆盖认证/授权、校验、404、异常中间件、分页、超时和幂等；写入数据必须可清理。
4. 测试结束关闭 server、数据库和消息连接，失败时保留脱敏响应、日志和退出码。

## 输出

输出 Node/框架依据、请求矩阵、生命周期与数据隔离、缺口、执行命令和证据；没有 Runner 时不声称执行。

## 输出边界

当前无 Node Runner 集成时只提供测试设计或静态审查，不声称已执行 SuperTest。
