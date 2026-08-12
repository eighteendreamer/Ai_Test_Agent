---
name: testcafe-testing
description: 根据 TestCafe 项目实际 fixture、selector、role、请求 hook、等待和并行配置审查端到端测试。用于 UI/兼容性代码审批。
---

# TestCafe 测试

先读取 TestCafe 版本、脚本、浏览器目标和现有 fixture；不要假设当前系统可以启动 TestCafe。

## 工作流程

1. 用用户旅程描述动作、结果、错误和清理。
2. 优先稳定语义/属性选择器，避免依赖位置和动态 class。
3. 使用 TestCafe 自动等待及明确请求完成条件，不叠加固定 sleep。
4. 对 request hook、认证、文件、窗口和并行状态声明隔离策略。
5. 保存浏览器、命令、截图、日志、失败步骤和退出码。

## 执行边界

当前没有 TestCafe Runner；只做代码审查和迁移建议，不声称执行测试。
