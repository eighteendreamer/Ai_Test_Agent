---
name: vitest-testing
description: 根据 Vite 项目实际配置规划、编写或审查 Vitest 单元测试、mock、异步行为、覆盖率和组件测试。用于检测到 Vitest 依赖、vitest 配置或 Vite 原生测试时。
---

# Vitest 测试

先核对 package scripts、Vite/Vitest 配置、测试环境和现有导入风格；不要把 Jest 的配置或行为直接套用到 Vitest。

## 工作流程

1. 从公开行为和真实失败路径建立测试矩阵。
2. 复用现有 setup、alias、environment 和 coverage provider，不新增重复配置。
3. 只在外部边界使用 mock；核对模块提升、ESM 导入和恢复时机。
4. 对异步 UI 或网络状态等待明确条件，清理 timer、DOM 和模块状态。
5. 快照和覆盖率只作为证据之一，关键业务结果使用显式断言。
6. 运行项目声明的 Vitest 命令，并记录配置、退出码和失败输出。

## 审查重点

- 测试环境与生产运行环境的差异。
- alias、动态导入和 mock 目标是否匹配。
- watch 模式是否被误用于 CI。
- 覆盖率阈值是否只追数字而遗漏风险路径。

## 输出边界

未发现 Vitest 依赖或无法运行命令时，只提供迁移/测试建议，不声称已执行。

## 配置和行为矩阵

核对 Vite/Vitest 配置、alias、environment、setup、coverage provider、pool/worker 和 package script。用行为矩阵覆盖正常、边界、异常、网络、时间和状态恢复；只在外部边界 mock，并清理 timer、DOM、模块和网络状态。

## ESM 与异步门禁

检查 ESM 导入、mock 提升、动态导入和生产/测试环境差异。等待可观察完成条件，不用 sleep；watch 模式不得作为 CI 门禁。覆盖率只作为风险证据之一，不能替代分支和错误路径断言。

## 输出与参考

记录实际配置、命令、seed、失败 DOM/栈、退出码和未验证能力。需要 Vitest API、Vite 配置、mock、coverage 和组件环境细节时读取 `references/source-1.md`。
