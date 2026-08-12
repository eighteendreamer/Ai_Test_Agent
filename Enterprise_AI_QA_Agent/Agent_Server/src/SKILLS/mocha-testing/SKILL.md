---
name: mocha-testing
description: 根据 Node.js 项目现有 Mocha 配置规划、编写或审查 BDD/TDD 测试、hooks、异步流程、断言库和依赖替身。用于检测到 Mocha 依赖、配置或测试套件时。
---

# Mocha 测试

先读取项目 scripts、Mocha 配置、断言库、mock 库和现有测试风格；Mocha 不自带所有断言或替身能力，不得凭候选示例引入依赖。

## 工作流程

1. 用项目已有 suite/test 接口表达公开行为和失败路径。
2. 让 hooks 只承担明确的初始化和清理，避免隐藏用例关键步骤。
3. 异步用例只使用一种完成协议，并确保错误能够传播到测试运行器。
4. 隔离全局、数据库、文件、timer 和网络状态，验证失败后清理仍执行。
5. 使用仓库既有 reporter、timeout 和并行策略，任何调整都说明依据。
6. 实际运行最小相关套件并保存 stdout、stderr、退出码和失败栈。

## 审查重点

- callback 与 Promise 混用导致的提前通过或重复完成。
- 过大的 before hook、顺序依赖和共享可变 fixture。
- 只断言 stub 调用而没有验证用户可观察结果。

## 输出边界

没有运行证据时明确标注为静态审查或测试设计。
