---
name: testcafe-testing
description: 根据 TestCafe 项目实际 fixture、selector、role、请求 hook、等待和并行配置审查端到端测试。用于 UI/兼容性代码审批。
---

# TestCafe 测试

先读取 TestCafe 版本、脚本、浏览器目标和现有 fixture；不要假设当前系统可以启动 TestCafe。selector、request hook 和 CLI 细节按需读取 `references/source-1.md`。

## 输入契约

确认旅程、fixture、浏览器、认证、请求范围、文件/窗口操作、并行策略和数据清理。

## 工作流程

1. 用用户旅程描述动作、结果、错误和清理。
2. 优先稳定语义/属性选择器，避免依赖位置和动态 class。
3. 使用 TestCafe 自动等待及明确请求完成条件，不叠加固定 sleep。
4. 对 request hook、认证、文件、窗口和并行状态声明隔离策略。
5. 保存浏览器、命令、截图、日志、失败步骤和退出码。

## 质量门禁

1. 优先语义或稳定属性 selector，断言用户结果而非 DOM 位置。
2. 使用 TestCafe 自动等待和请求完成条件，禁止固定 sleep。
3. request hook 必须与真实契约一致并覆盖错误/延迟；账号和写入资源隔离。
4. 失败按 selector、同步、网络、环境和产品原因分类，保留完整上下文。

## 输出

输出 fixture/selector 设计、覆盖矩阵、隔离策略、执行命令和退出码；无 Runner 时只做审查和迁移建议。

## 执行边界

当前没有 TestCafe Runner；只做代码审查和迁移建议，不声称执行测试。
