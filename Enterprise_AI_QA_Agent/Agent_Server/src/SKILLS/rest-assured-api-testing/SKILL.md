---
name: rest-assured-api-testing
description: 根据 Java 项目实际构建和测试栈审查 REST Assured 请求、认证、序列化、JSON schema、状态断言、数据隔离和报告。用于 Java API 测试与代码审批。
---

# REST Assured API 测试

先读取 Maven/Gradle、Java、REST Assured、JUnit/TestNG 和现有测试配置；不要假设依赖或版本。

## 工作流程

1. 以接口契约定义请求、响应、headers、错误和字段级断言。
2. 复用项目 request specification、序列化模型、认证 fixture 和报告配置。
3. 覆盖成功、校验、认证/授权、空、分页、幂等、超时和 5xx。
4. 写操作使用隔离数据和清理，避免并行测试相互污染。
5. 失败报告包括 URL 摘要、状态、脱敏响应、schema 差异和测试栈。
6. 运行项目现有 Maven/Gradle 测试任务并核对退出码。

## 输出边界

当前无 Java Runner 时只做代码审查和用例设计，不声称执行 REST Assured。
