---
name: selenium-testing
description: 根据 Java Selenium 项目实际 WebDriver、POM、TestNG/JUnit、Grid、等待和浏览器矩阵审查跨浏览器端到端测试。用于 UI/兼容性代码审批。
---

# Selenium 测试

先读取 Java 构建、WebDriver 管理、浏览器版本、Grid 和现有 POM；不要假设 Selenium 服务或浏览器可用。

## 工作流程

1. 以用户行为和稳定可访问属性定义页面对象与断言。
2. 明确 driver 生命周期、并行隔离、窗口/iframe/下载和清理。
3. 使用显式等待和业务完成条件，禁止隐式/显式等待混乱或固定 sleep。
4. 运行浏览器/OS 组合矩阵，记录版本、能力、截图、日志和失败步骤。
5. 对失败区分 locator、同步、环境、应用和 Grid 资源原因。

## 执行边界

当前系统没有 Selenium Runner；只做兼容性规划和代码审查，不声称已执行 Selenium。
