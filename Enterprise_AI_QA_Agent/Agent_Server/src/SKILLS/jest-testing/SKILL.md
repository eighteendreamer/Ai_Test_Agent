---
name: jest-testing
description: 根据 JavaScript 或 TypeScript 项目现有 Jest 配置规划、编写或审查单元测试、mock、spy、异步断言、fake timer 和快照。用于检测到 Jest 依赖、配置或 Jest 测试文件时。
---

# Jest 测试

先读取 package scripts、Jest 配置、转换器、测试环境和邻近测试；不要假设候选文档中的版本或包可用。

## 工作流程

1. 明确被测行为的输入、输出、副作用、错误和时间边界。
2. 选择最小真实边界：纯逻辑直接调用；外部网络、时间或随机性才使用可控替身。
3. 在每个用例前恢复 mock、timer 和共享模块状态，避免顺序依赖。
4. 异步测试必须等待 Promise、事件或可观察状态；不得以固定延时替代完成条件。
5. 快照只用于稳定且有审查价值的结构，业务规则仍使用显式断言。
6. 执行仓库现有 Jest 命令，检查退出码、失败栈、未处理 Promise 和资源泄漏提示。

## 审查重点

- mock 路径是否与实际导入边界一致。
- fake timer 是否推进了正确阶段并在结束后恢复。
- `only`、无理由 skip、宽泛快照更新和仅为通过而放松断言。
- TypeScript、ESM/CJS 与项目配置是否一致。

## 输出边界

说明依据的配置和测试命令；未实际运行时标记为静态方案。

## 测试结构与替身

以公开行为组织 suite/test，覆盖正常、边界、错误、状态和副作用。只在外部网络、时间、随机、进程或昂贵依赖边界使用 mock/spy；每个用例恢复 mock、fake timer、模块和环境变量。异步测试必须等待 Promise、事件或可观察状态。

## 快照与配置

快照应小且稳定，关键业务结果仍用显式断言。核对 Jest environment、transform、ESM/CJS、alias、coverage、worker 和 CI 命令，禁止 only、无理由 skip、宽泛快照更新和只为通过而放松断言。

## 执行与参考

保存测试命令、配置、失败栈、DOM/日志、未处理 Promise、资源泄漏和退出码。需要 mock、fake timers、异步、快照、覆盖率和配置示例时读取 `references/source-1.md`。
