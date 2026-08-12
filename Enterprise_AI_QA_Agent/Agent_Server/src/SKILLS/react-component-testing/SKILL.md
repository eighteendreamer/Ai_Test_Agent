---
name: react-component-testing
description: 按 React Testing Library 的用户视角规划、编写或审查组件查询、交互、异步状态、表单和可访问行为测试。用于 React 组件、hook 或页面行为变更。
---

# React 组件测试

先读取项目实际 runner、DOM 环境、测试工具版本和现有 render wrapper；不要假设候选资料中的包已安装。

## 工作流程

1. 以用户可访问的 role、name、label 和可见文本描述行为。
2. 复用项目的 provider/router/store 包装器，避免每个测试构造不一致环境。
3. 用真实用户事件驱动交互，等待可观察的异步结果而不是实现细节。
4. 在网络边界模拟响应，不直接 mock 组件内部函数或 hook 实现。
5. 覆盖加载、空、成功、失败、禁用、校验和恢复路径。
6. 运行项目声明的组件测试命令，保存失败 DOM、栈和退出码。

## 审查重点

- 查询是否反映真实可访问性，而非滥用 test id。
- 是否把 state、className 或调用次数当作最终业务断言。
- 异步查找、等待和清理是否会产生假通过或 act 警告。

## 输出边界

区分组件行为测试、hook 测试和 E2E；不要把 jsdom 结果声称为真实浏览器验证。

## 用户行为矩阵

用 role/name、label、文本和可访问状态表达正常、加载、空、错误、禁用、校验、权限和恢复；仅在语义无法稳定表达时使用 test id。复用 provider/router/store wrapper，网络只在边界模拟，断言结果而非 state/className/内部调用次数。

## 异步与参考

使用真实用户事件和可观察等待，处理 act 警告、清理和跨测试状态。记录 runner、环境、命令、失败 DOM、栈和退出码。需要查询、事件、异步、mock 和 provider 示例时读取 `references/source-1.md`。
