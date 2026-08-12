---
name: graphql-testing
description: 审查和设计 GraphQL query、mutation、subscription、schema、变量、错误、权限、分页、复杂度和批量加载测试。用于 GraphQL API 变更、API 测试和代码审批。
---

# GraphQL 测试

先读取 schema、客户端实际 operation、认证约定和项目测试工具；不要假设 Apollo、Jest 或特定服务器存在。

## 工作流程

1. 从 schema 和真实 operation 建立字段、变量、nullable、枚举和输入约束矩阵。
2. 覆盖成功、空结果、部分数据+errors、未知字段、类型错误、未授权和资源不存在。
3. 对 mutation 验证幂等、权限、并发、事务和错误后的数据一致性。
4. 对 subscription 验证连接认证、重连、事件顺序、过滤和资源释放。
5. 检查深度/复杂度限制、分页游标、批量加载和 N+1 风险。
6. 使用项目已有 API Runner 或测试命令，保存 query、variables、响应和退出码。

## 输出边界

没有 GraphQL Runner 时可做契约静态审查；不得把 REST 工具结果冒充 GraphQL 执行证据。
