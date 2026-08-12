---
name: rest-assured-api-testing
description: 根据 Java 项目实际构建和测试栈审查 REST Assured 请求、认证、序列化、JSON schema、状态断言、数据隔离和报告。用于 Java API 测试与代码审批。
---

# REST Assured API 测试

先读取 Maven/Gradle、Java、REST Assured、JUnit/TestNG 和现有测试配置；不要假设依赖或版本。版本 API、过滤器和 schema 示例按需读取 `references/source-1.md`。

## 输入契约

确认 base URI、环境变量、序列化模型、认证 fixture、数据库准备/清理和报告插件。将接口契约转换为请求、响应和错误矩阵。

## 工作流程

1. 以接口契约定义请求、响应、headers、错误和字段级断言。
2. 复用项目 request specification、序列化模型、认证 fixture 和报告配置。
3. 覆盖成功、校验、认证/授权、空、分页、幂等、超时和 5xx。
4. 写操作使用隔离数据和清理，避免并行测试相互污染。
5. 失败报告包括 URL 摘要、状态、脱敏响应、schema 差异和测试栈。
6. 运行项目现有 Maven/Gradle 测试任务并核对退出码。

## 质量门禁

1. 复用 `RequestSpecification`、对象映射和项目 matcher，避免每个用例复制认证与超时配置。
2. 对状态码、headers、schema、错误字段和业务结果做显式断言，不能只断言 2xx。
3. 写操作必须隔离数据并可重复清理；并行运行需证明资源不冲突。
4. 记录脱敏请求摘要、响应摘要、schema 差异、日志和构建任务退出码。

## 输出

输出依赖/版本依据、用例矩阵、数据策略、失败分类和可复现 Maven/Gradle 命令；无 Java Runner 时仅设计和审查。

## 输出边界

当前无 Java Runner 时只做代码审查和用例设计，不声称执行 REST Assured。
