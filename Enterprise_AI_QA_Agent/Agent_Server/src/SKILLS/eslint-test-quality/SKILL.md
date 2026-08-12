---
name: eslint-test-quality
description: 根据项目实际 ESLint 版本和 RuleTester 配置审查自定义规则的有效/无效样本、错误位置、修复输出、选项 schema 和配置回归。用于 ESLint 插件或规则代码审批。
---

# ESLint 规则测试

先读取项目 ESLint 版本、模块格式、parser、RuleTester 设置和现有规则测试；不要套用不兼容版本 API。

## 工作流程

1. 从规则语义定义允许、拒绝和自动修复契约。
2. 覆盖默认 parser、项目实际 parser、语言选项和规则选项组合。
3. 无效样本检查稳定的 messageId、错误数量和精确位置。
4. 可修复规则同时验证 output，并增加已修复结果再次 lint 的幂等性检查。
5. 覆盖语法边界、嵌套作用域、别名、注释和近似但合法的代码。
6. 使用仓库现有规则测试命令执行并保存输出。

## 审查重点

- 只测试一个 happy path 或只匹配宽泛错误文本。
- fixer 改变语义、产生无效代码或多次运行继续变化。
- RuleTester 配置与生产 ESLint 配置不一致。

## 输出边界

未确认 ESLint 主版本和 parser 时，先报告兼容性缺口，不编造配置。
