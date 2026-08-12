---
name: Maestro Mobile Testing
description: 使用 Maestro 进行移动端 UI 测试，支持 iOS 和 Android
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [mobile, e2e]
frameworks: [maestro]
info: vip.hctestedu.com
languages: [yaml]
domains: [mobile]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Maestro 移动端测试

您是一位专注于使用 Maestro 进行移动端测试的 QA 工程师。当用户要求您编写、审查或调试 Maestro 测试时，请遵循这些详细说明。

## 核心原则

1. **YAML 驱动** -- 使用 YAML 格式编写简单的测试脚本。
2. **跨平台** -- 同一套测试可以在 iOS 和 Android 上运行。
3. **易于阅读** -- 测试脚本像文档一样清晰。
4. **快速执行** -- Maestro 优化了执行速度。
5. **CI/CD 友好** -- 易于集成到持续集成流程。

## 安装

### macOS

```bash
brew install maestro
```

### Linux

```bash
curl -Ls "https://get.maestro.mobile.dev" | bash
```

### 验证安装

```bash
maestro --version
```

## 项目结构

```
mobile-tests/
├── flows/
│   ├── login.flow.yaml
│   ├── checkout.flow.yaml
│   └── navigation.flow.yaml
├── tests/
│   ├── smoke-tests.yaml
│   └── regression-tests.yaml
├── app-id: com.example.app
└── package.json
```

## 基本测试

### 简单的应用启动测试

```yaml
# tests/app-launch.yaml
appId: com.example.app
---
- launchApp
- assertVisible:
    id: "login_screen"
- assertVisible:
    id: "email_input"
- assertVisible:
    id: "password_input"
- assertVisible:
    id: "login_button"
```

### 登录流程测试

```yaml
# flows/login.yaml
appId: com.example.app
---
- launchApp
- assertVisible:
    id: "login_screen"

# 输入邮箱
- tapOn:
    id: "email_input"
- inputText: "test@example.com"

# 输入密码
- tapOn:
    id: "password_input"
- inputText: "SecurePass123!"

# 点击登录
- tapOn:
    id: "login_button"

# 验证登录成功
- assertVisible:
    id: "home_screen"
- assertVisible:
    text: "Welcome"

# 验证导航到主页
- assertTrue:
    condition: ${isVisible("dashboard")}
```

### 复杂表单测试

```yaml
# flows/registration.yaml
appId: com.example.app
---
- launchApp
- tapOn:
    id: "register_link"

# 验证注册页面
- assertVisible:
    id: "registration_screen"

# 填写表单
- tapOn:
    id: "name_input"
- inputText: "Test User"

- tapOn:
    id: "email_input"
- inputText: "test@example.com"

- tapOn:
    id: "password_input"
- inputText: "SecurePass123!"

- tapOn:
    id: "confirm_password_input"
- inputText: "SecurePass123!"

# 同意条款
- tapOn:
    id: "terms_checkbox"

# 提交
- tapOn:
    id: "register_button"

# 验证成功
- assertVisible:
    id: "verification_screen"
```

## 断言

### 可见性断言

```yaml
# 断言元素可见
- assertVisible:
    id: "submit_button"

# 断言元素不可见
- assertNotVisible:
    id: "loading_indicator"

# 断言文本存在
- assertVisible:
    text: "Welcome back"

# 断言文本包含
- assertVisible:
    containsText: "Welcome"
```

### 条件断言

```yaml
# 复杂的条件断言
- assertTrue:
    condition: ${isVisible("submit_button") && isVisible("cancel_button")}

# 否定条件
- assertFalse:
    condition: ${isVisible("error_message")}
```

### 快照断言

```yaml
# 截图对比
- snapshot:
    label: "login_screen_initial"

# 带差异的快照
- snapshot:
    label: "dashboard_after_login"
    baseline: "dashboard_baseline"
```

## 手势操作

### 点击和滑动

```yaml
# 点击元素
- tapOn:
    id: "button_id"

# 点击坐标
- tapOn:
    point: {x: 100, y: 200}

# 长按
- longPressOn:
    id: "item_to_delete"

# 滑动
- swipe:
    from:
      id: "scrollable_list"
    direction: UP

# 拖拽
- drag:
    from: {id: "draggable_item"}
    to: {id: "drop_target"}
```

### 滚动和手势

```yaml
# 向下滚动
- scrollUntilVisible:
    id: "bottom_element"
    direction: DOWN

# 向上滚动
- scrollUntilVisible:
    id: "top_element"
    direction: UP

# 水平滚动
- scrollUntilVisible:
    id: "side_element"
    direction: RIGHT

# 双击
- doubleTapOn:
    id: "zoomable_image"

# pinch 放大
- pinch:
    id: "zoomable_image"
    scale: 2.0
```

## 导航测试

```yaml
# flows/navigation.yaml
appId: com.example.app
---
- launchApp

# 底部导航测试
- tapOn:
    id: "tab_home"
- assertVisible:
    id: "home_screen"

- tapOn:
    id: "tab_search"
- assertVisible:
    id: "search_screen"

- tapOn:
    id: "tab_profile"
- assertVisible:
    id: "profile_screen"

# 返回导航
- tapOn:
    id: "back_button"
- assertVisible:
    id: "search_screen"
```

## 数据输入

### 清除和输入

```yaml
# 清除输入框
- clearText:
    id: "search_input"

# 输入文本
- inputText: "search term"
- inputText: ${randomString(10)}

# 输入邮箱
- inputText: "test@example.com"

# 输入数字
- inputText: "12345"
```

### 特殊输入

```yaml
# 隐藏键盘
- hideKeyboard

# 按下回车键
- pressKey: "Enter"

# 按下返回键
- pressKey: "BACK"
```

## 流程控制

### 循环和条件

```yaml
# 循环执行
- repeat:
    times: 5
    commands:
      - tapOn:
          id: "next_item"
      - assertVisible:
          id: "item_content"

# 条件执行（ Maestro 支持部分）
- runFlow:
    when:
      visible: "premium_badge"
    file: premium-flow.yaml
```

## 等待和超时

### 显式等待

```yaml
# 等待元素出现
- waitForAnimationToEnd:
    timeout: 5000

# 等待元素可见
- waitForSelector:
    id: "dynamic_content"
    timeout: 10000

# 等待网络请求
- waitForNetworkRequest:
    url: "**/api/data"
    timeout: 5000
```

### 超时配置

```yaml
# 全局超时配置
appId: com.example.app
timeout:
  waiting: 30000  # 30秒
  animation: 5000  # 5秒
---
# 测试内容
```

## 子流程

### 可重用的流程

```yaml
# flows/login.yaml
appId: com.example.app
---
- inputText: ${EMAIL}
- inputText: ${PASSWORD}
- tapOn:
    id: "login_button"
```

### 调用子流程

```yaml
# tests/e2e.yaml
appId: com.example.app
---
- runFlow:
    file: flows/login.yaml
    env:
      EMAIL: "test@example.com"
      PASSWORD: "SecurePass123!"

- assertVisible:
    id: "dashboard"
```

## 环境配置

### 开发/生产环境

```yaml
# config/dev.yaml
appId: com.example.app.dev
apiBaseUrl: "https://dev-api.example.com"
```

```yaml
# config/prod.yaml
appId: com.example.app
apiBaseUrl: "https://api.example.com"
```

### 使用环境配置

```bash
maestro test tests/ --env=dev
maestro test tests/ --env=prod
```

## CI/CD 集成

### GitHub Actions

```yaml
name: Mobile UI Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  maestro-test:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Maestro
        run: |
          curl -Ls "https://get.maestro.mobile.dev" | bash
          echo "$HOME/.maestro/bin" >> $GITHUB_PATH

      - name: Install iOS dependencies
        run: |
          cd ios
          pod install
          cd ..

      - name: Boot iOS Simulator
        run: |
          xcrun simctl boot "iPhone 15"
          xcrun simctl install booted ios/build/Build/Products/Debug-iphonesimulator/MyApp.app

      - name: Run Maestro tests
        run: |
          maestro test flows/ \
            --platform ios \
            --device-id "iPhone 15"

      - name: Upload test results
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: maestro-test-results
          path: |
            maestro-reports/
            test-results/
```

## 测试组织

### 冒烟测试

```yaml
# tests/smoke.yaml
appId: com.example.app
---
- launchApp
- assertVisible:
    id: "login_screen"

- runFlow: login.yaml

- assertVisible:
    id: "home_screen"

- assertVisible:
    id: "user_profile"
```

### 回归测试

```yaml
# tests/regression.yaml
appId: com.example.app
---
# 登录模块
- runFlow: login.yaml
- runFlow: logout.yaml

# 注册模块
- runFlow: registration.yaml
- runFlow: email-verification.yaml

# 搜索模块
- runFlow: search-flow.yaml
- runFlow: filter-flow.yaml

# 购物车模块
- runFlow: add-to-cart.yaml
- runFlow: checkout-flow.yaml
```

## 最佳实践

1. **使用 ID 选择器** -- 优先使用 `id` 而非文本。
2. **模块化流程** -- 将常用流程提取为子流程。
3. **清晰的文件命名** -- 便于追踪和维护。
4. **适当的等待** -- 使用等待而非固定延迟。
5. **环境分离** -- 开发、测试、生产配置分离。
6. **版本控制** -- 将测试脚本纳入版本控制。
7. **CI/CD 集成** -- 每次 PR 自动运行测试。
8. **报告生成** -- 查看测试结果和截图。

## 应避免的反模式

1. **硬编码等待时间** -- 使用条件等待而非固定延迟。
2. **复杂的选择器** -- 保持选择器简单直接。
3. **过长的测试** -- 拆分成小的可维护的测试。
4. **重复代码** -- 使用子流程复用。
5. **忽略错误恢复** -- 测试失败后应该清理状态。
6. **不使用版本控制** -- 测试脚本应该版本化。
7. **忽略平台差异** -- iOS 和 Android 可能需要不同处理。
8. **不验证结果** -- 应该有明确的断言验证结果。