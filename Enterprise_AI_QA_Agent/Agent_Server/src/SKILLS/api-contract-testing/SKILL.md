---
name: api-contract-testing
description: 根据 OpenAPI、Swagger、JSON Schema 和消费者契约审查 API 请求/响应、错误格式、版本兼容性与契约漂移。用于 API 测试、接口文档变更和跨模式动态加载。
---

# API 契约测试

以仓库中实际选定的 OpenAPI/Swagger/JSON Schema 或消费者契约为事实来源。先读取文档和实现边界，再通过现有 API 工具验证，不凭记忆补造接口。

## 审查与验证顺序

1. 枚举路径、方法、参数、请求体、认证要求、成功响应、错误响应、headers 和 content-type。
2. 检查必填字段、类型、格式、枚举、范围、分页和空集合行为；请求与响应都要覆盖。
3. 检查 4xx/5xx 错误结构、错误码、敏感信息泄露和未知字段策略。
4. 对版本或 DTO 变更检查删除字段、类型改变、状态码改变、认证改变和非向后兼容变更。
5. 将每个结论关联到文档、路由/handler、测试或实际响应证据；无法运行时明确标记为静态审查。

## 反模式

- 只验证 200 和少量 happy path。
- 仅按行覆盖率判断契约完整性。
- 文档、实现和测试各自维护一份不一致的 schema。
- 通过放宽断言或忽略字段掩盖契约漂移。
- 把消费者契约、服务端文档或 mock 当成未经核对的绝对事实。

## 输出

输出接口/字段级矩阵：来源、预期、实际证据、严重度、兼容性影响和建议验证动作。需要执行时使用已注册的 `api-tester` 或 `api-test-runner`，不要直接调用未注册框架。

## 契约解析顺序

选择唯一事实源并记录 OpenAPI 版本、schema 文件、operationId、生成来源和提交。展开 `$ref`、组合 schema、nullable、oneOf/anyOf、默认值和 discriminator；循环引用要显式标记。分别检查请求参数、请求体、响应 headers、content-type、成功响应和每一种错误响应，再与路由/handler、DTO、消费者使用和现有测试逐项对照。

## 验证策略

- 静态验证：列出文档与实现差异及证据位置。
- 工具验证：通过当前暴露的 `api-tester`/`api-test-runner` 执行已批准请求，保存脱敏请求/响应。
- 消费者验证：检查提供者是否满足消费者实际字段和错误使用。
- 版本验证：比较基线与当前 schema，按阻断、需确认和可接受分类。

需要 OpenAPI response、JSON Schema、向后兼容、GraphQL、错误响应和调试细节时读取 `references/source-1.md`。
