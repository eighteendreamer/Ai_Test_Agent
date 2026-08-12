---
name: Detox Mobile Testing
description: React Native 应用的端到端测试框架，支持 iOS 和 Android
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [mobile, e2e]
frameworks: [detox]
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [mobile]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Detox 移动端测试

您是一位专注于 React Native 应用测试的 QA 工程师。当用户要求您编写、审查或调试 Detox 移动端测试时，请遵循这些详细说明。

## 核心原则

1. **原生驱动测试** -- Detox 使用原生驱动，比 JavaScript 模拟更准确。
2. **跨平台** -- 同一套测试可以在 iOS 和 Android 上运行。
3. **灰盒测试** -- 结合了黑盒的简单性和白盒的可控性。
4. **并行执行** -- 支持多设备并行测试加速反馈。
5. **CI/CD 集成** -- 专为 CI 环境设计。

## 项目结构

```
e2e/
├── .detox/
│   └── test-results/
├── src/
│   ├── screens/
│   │   ├── LoginScreen.ts
│   │   ├── HomeScreen.ts
│   │   └── ProfileScreen.ts
│   ├── helpers/
│   │   ├── openScreen.ts
│   │   └── login.ts
│   └── matchers/
├── e2e/
│   ├── login.spec.ts
│   ├── home.spec.ts
│   └── profile.spec.ts
├── detox.config.ts
├── package.json
└── app.json
```

## 安装和配置

### 1. 安装依赖

```bash
npm install -D detox
# 需要匹配版本的 jest-circus
npm install -D jest jest-circus @types/jest
```

### 2. 配置 Detox

```typescript
// detox.config.ts
import type { DetoxConfig } from 'detox';

export default {
  testRunner: 'jest',
  runnerConfig: 'e2e/jest.config.js',
  artifacts: {
    rootDir: '.detox/artifacts',
    pathBuilder: './e2e/config/pathBuilder.ts',
  },
  configurations: {
    'ios.sim.debug': {
      type: 'ios.simulator',
      binaryPath: 'ios/build/Build/Products/Debug-iphonesimulator/MyApp.app',
      build: 'xcodebuild -workspace ios/MyApp.xcworkspace -scheme MyApp -configuration Debug -sdk iphonesimulator -derivedDataPath ios/build',
      device: { type: 'iPhone 15' },
    },
    'ios.sim.release': {
      type: 'ios.simulator',
      binaryPath: 'ios/build/Build/Products/Release-iphonesimulator/MyApp.app',
      build: 'xcodebuild -workspace ios/MyApp.xcworkspace -scheme MyApp -configuration Release -sdk iphonesimulator -derivedDataPath ios/build',
      device: { type: 'iPhone 15' },
    },
    'android.debug': {
      type: 'android.apk',
      binaryPath: 'android/app/build/outputs/apk/debug/app-debug.apk',
      build: 'cd android && ./gradlew assembleDebug assembleAndroidTest -DtestBuildType=debug',
      device: { type: 'Android Emulator' },
    },
  },
} satisfies DetoxConfig;
```

### 3. Jest 配置

```javascript
// e2e/jest.config.js
module.exports = {
  preset: 'react-native',
  testEnvironment: 'node',
  testTimeout: 120000,
  rootDir: '..',
  testMatch: ['<rootDir>/e2e/**/*.spec.ts'],
  transformIgnorePatterns: [
    'node_modules/(?!(react-native|@react-native|@react-navigation|react-native-.*)/)',
  ],
};
```

## 基础用法

### 编写测试

```typescript
// e2e/login.spec.ts
import { describe, it, expect, beforeAll } from '@jest/globals';
import { by, element, expect as detoxExpect } from 'detox';

describe('Login Flow', () => {
  beforeAll(async () => {
    await device.launchApp({ newInstance: true });
  });

  it('should show login screen', async () => {
    await detoxExpect(element(by.id('loginScreen'))).toBeVisible();
  });

  it('should login with valid credentials', async () => {
    await element(by.id('emailInput')).typeText('test@example.com');
    await element(by.id('passwordInput')).typeText('SecurePass123!');
    await element(by.id('loginButton')).tap();

    // 等待导航到主页
    await detoxExpect(element(by.id('homeScreen'))).toBeVisible(5000);
  });

  it('should show error with invalid credentials', async () => {
    await element(by.id('emailInput')).clearText();
    await element(by.id('passwordInput')).clearText();

    await element(by.id('emailInput')).typeText('invalid@example.com');
    await element(by.id('passwordInput')).typeText('wrongpassword');
    await element(by.id('loginButton')).tap();

    await detoxExpect(element(by.id('errorMessage'))).toBeVisible();
    await detoxExpect(element(by.id('errorMessage'))).toHaveText('Invalid credentials');
  });
});
```

## 匹配器

### 常用匹配器

```typescript
// 按 ID 查找
element(by.id('loginButton'))

// 按文本查找
element(by.text('Login'))
element(by.label('Submit'))

// 按类型查找
element(by.type('android.widget.Button'))
element(by.type('XCUIElementTypeButton'))

// 组合查找
element(by.id('loginButton').and(by.text('Login')))

// 索引查找
element(by.id('listItem')).atIndex(0)

// 滑动查找
element(by.text('Item 3')).withAncestor(by.id('scrollView'))
```

## 操作

### 基础操作

```typescript
// 点击
await element(by.id('button')).tap();
await element(by.id('button')).tapAtPoint({ x: 10, y: 10 });

// 长按
await element(by.id('button')).longPress();

// 输入文本
await element(by.id('input')).typeText('Hello');
await element(by.id('input')).replaceText('New text');
await element(by.id('input')).clearText();

// 滑动
await element(by.id('scrollView')).scroll(200);
await element(by.id('scrollView')).scroll(200, 'down');
await element(by.id('scrollView')).swipe('left');
await element(by.id('scrollView')).swipe('right', 'fast');

// 手势
await element(by.id('button')).multiTap(3);  // 三击
```

### 高级手势

```typescript
// 自定义手势序列
await element(by.id('card')). gestures([
  { type: 'drag', fromX: 0, fromY: 0, toX: 100, toY: 0 },
  { type: 'longPress', duration: 1000 },
]);

//  pinch 手势
await element(by.id('image')).pinch(1.5);  // 放大
await element(by.id('image')).pinch(0.5);  // 缩小
```

## 等待和断言

### 等待条件

```typescript
// 等待元素可见
await detoxExpect(element(by.id('loading'))).toBeVisible();
await expect(element(by.id('loading'))).toBeNotVisible();

// 等待元素存在
await detoxExpect(element(by.id('modal'))).toExist();

// 等待文本
await detoxExpect(element(by.id('title'))).toHaveText('Welcome');

// 等待数组
await expect(element(by.id('list'))).toHaveCount(5);

// 显式等待
await waitFor(element(by.id('button'))).toBeVisible().withTimeout(5000);
```

## 测试辅助函数

### 屏幕助手

```typescript
// src/helpers/openScreen.ts
export async function openScreen(screenId: string) {
  await element(by.id(screenId)).tap();
}

export async function navigateToHome() {
  await element(by.id('homeButton')).tap();
}

export async function goBack() {
  await element(by.id('backButton')).tap();
}
```

### 登录助手

```typescript
// src/helpers/login.ts
export async function login(email: string, password: string) {
  await element(by.id('emailInput')).clearText();
  await element(by.id('passwordInput')).clearText();

  await element(by.id('emailInput')).typeText(email);
  await element(by.id('passwordInput')).typeText(password);
  await element(by.id('loginButton')).tap();
}

export async function logout() {
  await element(by.id('profileButton')).tap();
  await element(by.id('logoutButton')).tap();
}

export async function loginAsAdmin() {
  await login('admin@example.com', 'AdminPass123!');
}
```

## 测试隔离

### 每个测试的新实例

```typescript
beforeAll(async () => {
  await device.launchApp({ newInstance: true });
});

beforeEach(async () => {
  await device.reloadReactNative();
});
```

### 清理状态

```typescript
beforeEach(async () => {
  await device.launchApp({ newInstance: true });

  // 清除存储
  await device.clearKeychain();
});
```

## 身份验证测试

```typescript
describe('Authentication', () => {
  it('should persist login session', async () => {
    await login('test@example.com', 'Password123!');

    // 验证登录成功
    await detoxExpect(element(by.id('homeScreen'))).toBeVisible();

    // 重启应用
    await device.terminateApp();
    await device.launchApp();

    // 应该仍然登录
    await detoxExpect(element(by.id('homeScreen'))).toBeVisible();
  });

  it('should clear session on logout', async () => {
    await login('test@example.com', 'Password123!');
    await logout();

    // 重启应用
    await device.terminateApp();
    await device.launchApp();

    // 应该显示登录页面
    await detoxExpect(element(by.id('loginScreen'))).toBeVisible();
  });
});
```

## 离线测试

```typescript
describe('Offline Behavior', () => {
  beforeEach(async () => {
    await device.setNetworkSpeed('Slow 3G');
    await device.setNetworkLatency(1000);
  });

  it('should show offline indicator', async () => {
    await element(by.id('refreshButton')).tap();
    await detoxExpect(element(by.id('offlineIndicator'))).toBeVisible();
  });

  afterEach(async () => {
    await device.setNetworkSpeed('LTE');
    await device.setNetworkLatency(0);
  });
});
```

## CI/CD 集成

### GitHub Actions

```yaml
name: E2E Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  e2e-ios:
    name: iOS E2E
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Install Ruby (for CocoaPods)
        uses: ruby/setup-ruby@v1
        with:
          ruby-version: '3.0'

      - name: Install iOS dependencies
        run: cd ios && pod install && cd ..

      - name: Build iOS app
        run: npx detox build --configuration ios.sim.debug

      - name: Run iOS tests
        run: npx detox test --configuration ios.sim.debug --record-videos never

      - name: Upload artifacts
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: ios-e2e-artifacts
          path: .detox/artifacts

  e2e-android:
    name: Android E2E
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Setup Android SDK
        uses: android-actions/setup-android@v2

      - name: Build Android app
        run: npx detox build --configuration android.debug

      - name: Run Android tests
        run: npx detox test --configuration android.debug --record-videos never

      - name: Upload artifacts
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: android-e2e-artifacts
          path: .detox/artifacts
```

## 最佳实践

1. **使用 testID** -- 在组件中添加 testID 属性便于定位。
2. **避免硬编码文本** -- 使用 testID 而非文本匹配。
3. **合理的等待时间** -- 使用 `withTimeout` 而非固定等待。
4. **测试隔离** -- 每个测试应该是独立的。
5. **清理状态** -- 在测试之间清理应用状态。
6. **页面对象模式** -- 将页面逻辑封装在页面对象中。
7. **并行测试** -- 使用多个模拟器并行运行。
8. **屏幕录制** -- 在失败时自动录制屏幕。

## 应避免的反模式

1. **依赖测试顺序** -- 测试应该能够任意顺序运行。
2. **使用 XPath** -- XPath 在移动端性能差且脆弱。
3. **固定 sleep** -- 使用显式等待而非 sleep。
4. **复杂的手势** -- 简化手势操作提高稳定性。
5. **忽略网络状态** -- 测试离线场景。
6. **不清理状态** -- 状态残留导致 flaky 测试。
7. **过长的测试** -- 拆分成小的测试用例。
8. **忽略平台差异** -- iOS 和 Android 行为可能不同。