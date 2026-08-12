---
name: storybook-component-testing
description: 根据项目现有 Storybook 配置规划、编写或审查 stories、args、decorators、play 交互和组件状态覆盖。用于 Storybook stories、组件目录或交互测试变更。
---

# Storybook 组件测试

先读取 Storybook 版本、配置、addons、测试命令和同类 stories；不要假设候选文档中的 runner 或插件存在。

## 工作流程

1. 枚举组件的默认、边界、加载、空、错误、禁用和权限状态。
2. 让 args 表达输入契约，decorator 只提供真实需要的上下文。
3. 用 play 流程执行用户可观察交互，并以语义查询和结果断言结束。
4. 稳定时间、网络、随机数据、字体和动画，确保状态可重放。
5. 将无障碍或视觉检查关联到明确 story，不用单一默认状态代表全部覆盖。
6. 执行项目声明的 Storybook 测试或构建命令并保存输出。

## 审查重点

- story 是否只是展示而没有关键状态和交互覆盖。
- decorator 是否隐藏生产环境缺失的依赖。
- play 是否依赖测试间共享状态或固定延时。

## 输出边界

未实际运行 Storybook 构建/测试时，不声称 story 可执行或视觉基线通过。

## Story 状态矩阵

为组件枚举默认、边界、加载、空、错误、禁用、权限、主题和响应式状态。args 表达输入契约，decorator 只提供真实上下文；play 执行用户交互并以语义查询和业务结果断言结束。

## 稳定性与参考

固定时间、网络、随机、字体和动画，避免 story 间共享状态。检查 play 的等待、清理和失败证据；无障碍/视觉检查关联到明确状态，不用默认 story 代表全部覆盖。需要 stories、args、decorators、play、交互测试和 CI 细节时读取 `references/source-1.md`。
