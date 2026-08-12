---
name: api-mocking-msw
description: 根据项目实际 MSW 版本和运行环境规划浏览器/Node 网络级 API mock、默认 handlers、用例覆盖、异常响应和未处理请求门禁。用于前端组件与集成测试。
---

# MSW API 模拟

先读取项目 MSW 版本、setup、handlers 和测试 runner；不要假设候选示例 API 与当前版本兼容。

## 工作流程

1. 从真实 API 契约建立最小默认 handler，保持字段、状态码和错误结构一致。
2. 用例级 override 只表达该场景的差异，并在测试结束后恢复。
3. 覆盖成功、空、延迟、4xx、5xx、网络错误、分页和认证过期。
4. 未处理请求默认作为可见失败或警告，禁止静默访问外网。
5. 测试用户可观察结果和客户端恢复，不只断言 handler 被调用。
6. 将 mock 契约与 OpenAPI/真实服务差异纳入审查，避免长期漂移。

## 审查重点

- handler 是否过度宽泛、无请求断言或返回不真实数据。
- 默认 handler 是否泄漏跨测试状态。
- mock 是否掩盖序列化、headers 或认证问题。

## 输出边界

未确认 MSW 已安装时只给出网络 mock 策略，不新增依赖或声称运行。
