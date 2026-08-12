---
name: webdriverio-e2e-testing
description: 根据 WebdriverIO 项目实际 runner、services、Cucumber/mocha 配置、选择器和等待策略审查跨浏览器 E2E 测试。用于 UI/兼容性代码审批。
---

# WebdriverIO E2E 测试

先读取 wdio 配置、版本、browser capabilities、services 和测试命令；不要假设 cloud/grid provider 可用。Runner/service 差异按需读取 `references/source-1.md`。

## 输入契约

取得旅程、页面对象、capabilities、浏览器/OS、worker 并行度、认证和测试数据边界。

## 工作流程

1. 将业务旅程拆成可重放步骤、可观察断言和清理。
2. 复用 page object 和稳定 accessibility/data 选择器。
3. 使用元素状态、网络或业务完成条件等待，避免固定延时。
4. 明确 session、cookie、窗口、下载、并行 worker 和失败重试隔离。
5. 按浏览器/OS 矩阵记录能力、日志、截图、视频和退出码。

## 质量门禁

1. 使用稳定 accessibility/data 选择器和元素状态等待；禁止坐标与固定延时。
2. 明确 session、cookie、窗口、下载、worker 和重试的隔离/清理策略。
3. 断言用户可观察结果与业务副作用，失败保留步骤、命令、能力和环境。

## 输出

输出配置依据、场景/能力矩阵、同步与并行风险、命令和证据；未接入 Runner 时不声称执行。

## 执行边界

当前 UI Runner 未接入 WebdriverIO；只做规划/静态审查，不声称执行。
