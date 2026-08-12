---
name: graphql-testing
description: 审查和设计 GraphQL query、mutation、subscription、schema、变量、错误、权限、分页、复杂度和批量加载测试。用于 GraphQL API 变更、API 测试和代码审批。
---

# GraphQL 测试

先读取 schema、客户端实际 operation、认证约定和项目测试工具；不要假设 Apollo、Jest 或特定服务器存在。复杂度、订阅和框架示例按需读取 `references/source-1.md`。

## 输入契约

收集 schema 版本、query/mutation/subscription 文档、变量样本、认证角色、分页约定、错误格式、数据清理方式和执行命令。

## 工作流程

1. 从 schema 和真实 operation 建立字段、变量、nullable、枚举和输入约束矩阵。
2. 覆盖成功、空结果、部分数据+errors、未知字段、类型错误、未授权和资源不存在。
3. 对 mutation 验证幂等、权限、并发、事务和错误后的数据一致性。
4. 对 subscription 验证连接认证、重连、事件顺序、过滤和资源释放。
5. 检查深度/复杂度限制、分页游标、批量加载和 N+1 风险。
6. 使用项目已有 API Runner 或测试命令，保存 query、variables、响应和退出码。

## 断言矩阵

- Query：字段类型/nullable、空结果、分页游标、部分 data+errors、未知字段和变量校验。
- Mutation：权限、幂等、并发、事务回滚、重复提交和错误后数据一致性。
- Subscription：连接认证、过滤、事件顺序、断线重连、重复事件和资源释放。
- 性能/安全：深度与复杂度限制、批量加载/N+1、别名滥用、敏感字段和 introspection 策略。

## 门禁与输出

每个 operation 必须关联 schema 字段和用户可观察结果；不得只断言 HTTP 200。失败按 schema、resolver、认证、数据状态、传输和复杂度分类。输出 query/variables（脱敏）、响应、字段差异、运行命令、退出码及未覆盖风险。

## 输出边界

没有 GraphQL Runner 时可做契约静态审查；不得把 REST 工具结果冒充 GraphQL 执行证据。
