---
name: playwright-e2e-testing
description: 使用 Playwright 规划和执行基于用户行为的 Web UI 探索、端到端场景、断言与证据采集。用于 UI 自动化模式；当前运行时只执行已注册的页面探索和浏览器命令，完整套件执行能力不足时必须明确报告。
---

# Playwright E2E 测试

以用户可观察行为、ARIA 语义和实际页面状态为依据。先调用 `ui-automation-runner` 判断目标和知识是否充分，再使用已注册的页面探索或浏览器工具；不要假设候选文档中的 Node Playwright Test runner 已安装。

## 当前执行边界

- 使用 `ui-automation-runner` 进入 UI 模式状态机并确认方向、子方向和缺失信息。
- 使用 `ui-page-explorer` 建立页面、实体、元素和可达状态图。
- 使用 `browser-automation` 或 `browser-control` 执行当前系统支持的 Playwright 风格命令。
- 使用 `file-artifact-manager` 和 `report-writer` 保存截图、日志、trace 元数据与结论。
- 当前 UI runtime 报告测试执行员工未实现时，停止声称完整 E2E 套件已执行，并输出所缺能力。

## 工作流程

1. 确认目标 URL、业务目标、测试范围、登录前置条件和允许的状态变更。
2. 先探索页面，优先使用 role、label、text 和 test id 等稳定语义定位；避免依赖易变 CSS 层级。
3. 将关键用户旅程拆成前置条件、操作、可观察结果、清理动作和证据要求。
4. 断言可见行为、URL、语义状态、网络结果或持久化结果；不要只断言元素存在。
5. 对写操作、文件上传、权限、错误恢复和跨页面状态明确风险与审批边界。
6. 失败时保留截图、当前 URL、ARIA snapshot、日志和可重放步骤，不用固定 sleep 掩盖等待问题。

## 输出

区分“已探索”“已执行”“仅生成场景”和“运行时暂不支持”。每条结果必须包含目标、步骤、断言、证据和失败原因。
