---
name: WebdriverIO E2E
description: 跨浏览器 E2E 测试，支持 WebdriverIO、Cucumber 集成和可视化回归
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e, visual]
frameworks: [selenium]
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# WebdriverIO E2E 测试

本技能使 AI 代理编写和配置 WebdriverIO (WDIO) 端到端测试：正确的 `wdio.conf.ts`、使用自动等待的 `$`/`$$` 选择器用法、`waitUntil` 用于自定义条件、Mocha 结构化的 specs、页面对象、通过 `maxInstances` 和多 capabilities 的并行执行，以及服务连接（可视化回归、用于移动端的 Appium）。当仓库 devDependencies 中包含 `@wdio/cli`、`wdio.conf.*` 文件、或用户要求 WebdriverIO/WDIO 测试时，触发此技能。

## 核心原则

1. **WDIO 命令自动等待——不要添加手动暂停。** `$('button').click()` 重试直到元素可交互（由 `waitforTimeout` 治理）。提交代码中的 `browser.pause()` 是一个 bug，不是修复。
2. **`$` 返回可链式调用的元素，不是句柄。** 每次命令都会重新定位，所以 stale-element 错误很少见。在页面对象中存储选择器链，绝不存储等待的快照。
3. **按优先级使用 WDIO 选择器优势：** 无障碍文本选择器（`button=Submit`、`*=partial`）、然后是 `[data-testid="x"]` 的 `data-testid`、然后是 CSS。只有在父轴遍历时才使用 XPath。
4. **一个 spec = 一个用户可见行为。** WDIO workers 按 spec 文件隔离；长的多旅程 specs 序列化你的套件并隐藏哪个行为坏了。
5. **并行性是配置，不是代码。** `maxInstances` + `capabilities` 数组在浏览器间展开；specs 不得共享账户或可变服务器状态。
6. **服务做重活。** 可视化差异（`@wdio/visual-service`）、Appium（`@wdio/appium-service`）和 Selenium Grid 连接属于 `services:`，而不是在 hooks 中手写。

## 设置

```bash
npm init wdio@latest .   # 交互式脚手架
# 或手动：
npm install --save-dev @wdio/cli @wdio/local-runner @wdio/mocha-framework @wdio/spec-reporter tsx
```

```typescript
// wdio.conf.ts
import type { Options } from '@wdio/types';

export const config: Options.Testrunner = {
  runner: 'local',
  specs: ['./test/specs/**/*.ts'],
  maxInstances: 5,
  capabilities: [
    {
      browserName: 'chrome',
      'goog:chromeOptions': {
        args: process.env.CI ? ['--headless=new', '--disable-gpu', '--window-size=1366,900'] : [],
      },
    },
  ],
  logLevel: 'warn',
  baseUrl: process.env.BASE_URL ?? 'http://localhost:3000',
  waitforTimeout: 10_000,        // 默认 $ 自动等待预算
  connectionRetryTimeout: 120_000,
  framework: 'mocha',
  reporters: ['spec'],
  mochaOpts: { ui: 'bdd', timeout: 60_000 },

  // 在 CI 中快速失败，本地保持完整运行
  bail: process.env.CI ? 1 : 0,

  afterTest: async function (_test, _context, { passed }) {
    if (!passed) {
      await browser.takeScreenshot(); // 附加到 runner 日志目录
    }
  },
};
```

## 选择器和自动等待

```typescript
// test/specs/login.spec.ts
import { browser, $, expect } from '@wdio/globals';

describe('login', () => {
  beforeEach(async () => {
    await browser.url('/login'); // 相对于 baseUrl 解析
  });

  it('signs in with valid credentials', async () => {
    await $('[data-testid="email"]').setValue('user@example.com');
    await $('[data-testid="password"]').setValue('s3cret!');
    await $('button=Sign in').click();           // 文本选择器，自动等待

    // expect-webdriverio 断言重试直到超时——无手动等待
    await expect($('h1')).toHaveText('Dashboard');
    await expect(browser).toHaveUrl(expect.stringContaining('/dashboard'));
  });

  it('shows a validation error for a bad password', async () => {
    await $('[data-testid="email"]').setValue('user@example.com');
    await $('[data-testid="password"]').setValue('wrong');
    await $('button=Sign in').click();

    const alert = $('[role="alert"]');
    await expect(alert).toBeDisplayed();
    await expect(alert).toHaveText(expect.stringContaining('Invalid credentials'));
  });
});
```

`$$` 用于集合：

```typescript
const rows = $$('[data-testid="cart-row"]');
await expect(rows).toBeElementsArrayOfSize(3);
const titles = await rows.map((row) => row.$('.title').getText());
```

## waitUntil 用于自定义条件

仅在没有内置匹配器适合时使用（例如，轮询应用状态）：

```typescript
await browser.waitUntil(
  async () => (await $('[data-testid="job-status"]').getText()) === 'COMPLETE',
  {
    timeout: 30_000,
    interval: 500,
    timeoutMsg: 'job never reached COMPLETE',
  },
);
```

## 页面对象

```typescript
// test/pageobjects/login.page.ts
import { $, browser } from '@wdio/globals';

class LoginPage {
  // getters 返回新鲜的可链式选择器——绝不缓存等待的元素
  get email()    { return $('[data-testid="email"]'); }
  get password() { return $('[data-testid="password"]'); }
  get submit()   { return $('button=Sign in'); }

  async open() {
    await browser.url('/login');
  }

  async login(email: string, password: string) {
    await this.email.setValue(email);
    await this.password.setValue(password);
    await this.submit.click();
  }
}

export default new LoginPage();
```

```typescript
// 使用
import LoginPage from '../pageobjects/login.page';

it('logs in', async () => {
  await LoginPage.open();
  await LoginPage.login('user@example.com', 's3cret!');
  await expect($('h1')).toHaveText('Dashboard');
});
```

## 并行 + 多浏览器 Capabilities

```typescript
// wdio.conf.ts（节选）
maxInstances: 6,
capabilities: [
  { browserName: 'chrome',  'goog:chromeOptions': { args: ['--headless=new'] } },
  { browserName: 'firefox', 'moz:firefoxOptions': { args: ['-headless'] }, maxInstances: 2 },
],
```

每个 spec 文件在自己的 worker 中运行；`maxInstances` 在全局限制并发，per-capability `maxInstances` 按浏览器限制。在 CI 中用 `--spec` glob 进一步分片每个 job。

## 服务

```typescript
// 可视化回归
// npm i -D @wdio/visual-service
services: [['visual', {
  baselineFolder: './test/baseline',
  screenshotPath: './test/screenshots',
  blockOutStatusBar: true,
}]],
```

```typescript
// 在 spec 中
await expect(browser).toMatchFullPageSnapshot('dashboard', { misMatchTolerance: 0.2 });
```

```typescript
// Appium 移动端（原生或移动 Web）
// npm i -D @wdio/appium-service
services: ['appium'],
capabilities: [{
  platformName: 'Android',
  'appium:automationName': 'UiAutomator2',
  'appium:deviceName': 'Pixel_8_API_34',
  'appium:app': './apps/app-release.apk',
}],
```

## CI (GitHub Actions)

```yaml
e2e:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with: { node-version: 20, cache: npm }
    - run: npm ci
    - run: npm run start:test &   # 被测应用
    - run: npx wait-on http://localhost:3000
    - run: npx wdio run wdio.conf.ts
      env: { CI: 'true' }
    - uses: actions/upload-artifact@v4
      if: failure()
      with: { name: wdio-screenshots, path: ./test/screenshots }
```

## 最佳实践

- 将 `waitforTimeout` 保持在 10-15s；为已知慢流程全局提升（`{ timeout }` 参数）而不是全局提升。
- 使用 `expect-webdriverio` 匹配器（`toHaveText`、`toBeDisplayed`）——它们重试；裸 `getText()` + chai 不会。
- 通过 `before` hooks 中的 API 调用重置状态，而不是 UI 点击。
- 按行为命名 specs：`checkout-applies-coupon.spec.ts`，而不是 `test1.spec.ts`。
- 在 CI 中固定浏览器版本（chrome-for-testing）以停止随意破坏。

## 反模式

1. 在提交代码的任何地方使用 `browser.pause(3000)` —— 用匹配器或 `waitUntil` 替换。
2. 在跨导航的变量中缓存 `const el = await $(sel)` —— 通过 getters 重新定位。
3. 一个 mega-spec 覆盖 login→cart→checkout→refund —— 杀死并行性和分类。
4. 在异步 UI 上使用非重试 chai 断言 —— flaky 工厂。
5. 在不分离配置的情况下在一个 capability 集中驱动 Appium 和桌面 web —— 拆分 `wdio.web.conf.ts` / `wdio.mobile.conf.ts` 共享一个基础。

## 何时触发此技能

- "为 X 编写 WebdriverIO 测试" / "添加 wdio spec"
- 仓库有 `wdio.conf.ts|js` 或 `@wdio/cli` 依赖
- 从 Selenium JS 或 Protractor 套件迁移到 WDIO
- 通过 WDIO 服务设置可视化回归或 Appium
