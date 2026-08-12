---
name: selenium-testing
description: 根据 Java Selenium 项目实际 WebDriver、POM、TestNG/JUnit、Grid、等待和浏览器矩阵审查跨浏览器端到端测试。用于 UI/兼容性代码审批。
---

# Selenium 测试

先读取 Java 构建、WebDriver 管理、浏览器版本、Grid 和现有 POM；不要假设 Selenium 服务或浏览器可用。Grid/capability 细节按需读取 `references/source-1.md`。

## 输入契约

确认用户旅程、页面对象、浏览器/OS 矩阵、driver/Grid、账号和数据清理策略。

## 工作流程

1. 以用户行为和稳定可访问属性定义页面对象与断言。
2. 明确 driver 生命周期、并行隔离、窗口/iframe/下载和清理。
3. 使用显式等待和业务完成条件，禁止隐式/显式等待混乱或固定 sleep。
4. 运行浏览器/OS 组合矩阵，记录版本、能力、截图、日志和失败步骤。
5. 对失败区分 locator、同步、环境、应用和 Grid 资源原因。

## 质量门禁

1. 页面对象只封装交互和稳定定位；断言放在场景层并面向用户结果。
2. 使用显式等待和业务完成条件，统一 driver 超时；禁止固定 sleep 或混用冲突等待。
3. 每个 session/窗口/iframe/下载都要有生命周期和清理，写操作必须隔离。
4. 记录 capability、浏览器/驱动版本、Grid 节点、截图、日志和退出码。

## 输出

输出兼容性矩阵、POM/定位风险、同步与资源问题、执行命令及证据；无 Selenium Runner 时只做规划。

## 执行边界

当前系统没有 Selenium Runner；只做兼容性规划和代码审查，不声称已执行 Selenium。
