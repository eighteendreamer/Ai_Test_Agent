---
name: vue-component-testing
description: 根据 Vue 项目现有 Vue Test Utils 和 runner 规划、编写或审查组件挂载、props、emits、slots、组合式 API、异步更新和交互测试。用于 Vue 组件变更。
---

# Vue 组件测试

先读取 Vue 版本、runner、插件注册方式和邻近测试；不要混用 Vue 2/3 或不同 runner 的假设。

## 工作流程

1. 将 props、slots、用户事件、emits 和渲染结果映射为行为矩阵。
2. 复用项目已有 global plugins、router、store 和 stubs 配置。
3. 触发用户交互并等待 Vue 更新周期或业务 Promise 完成，避免固定 sleep。
4. 断言可见文本、语义状态、事件载荷和外部效果，不直接依赖内部 ref。
5. 覆盖空、加载、错误、条件渲染、卸载和清理路径。
6. 使用项目现有命令运行相关用例并保留输出。

## 审查重点

- shallow/stub 是否隐藏了本应验证的集成行为。
- emits 和 v-model 契约是否覆盖有效及无效输入。
- 组合式函数的外部依赖是否在正确边界替换。

## 输出边界

未确认 Vue 与测试工具版本时，不生成版本相关 API 示例。
