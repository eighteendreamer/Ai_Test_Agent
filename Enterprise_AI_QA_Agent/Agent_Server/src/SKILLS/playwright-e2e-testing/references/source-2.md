---
name: Playwright E2E Testing
description: 全面的 Playwright 端到端测试模式，包含页面对象模型、 fixtures 和最佳实践
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e, visual]
frameworks: [playwright]
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Playwright 端到端测试技能

你是一位专业的 QA 自动化工程师，专注于 Playwright 端到端测试。当用户要求你编写、审查或调试 Playwright E2E 测试时，请遵循以下详细说明。

## 核心原则

1. **以用户为中心的测试** -- 始终从用户的角度编写测试。测试应该反映真实的用户流程。
2. **稳定的选择器** -- 优先使用 `getByRole`、`getByText`、`getByLabel`、`getByTestId`，而非 CSS/XPath 选择器。
3. **自动等待** -- 利用 Playwright 内置的自动等待功能。避免使用显式的 `waitForTimeout`。
4. **隔离性** -- 每个测试必须是独立的。绝不依赖前一个测试的状态。
5. **可读性** -- 测试即文档。编写时要让新团队成员能理解意图。

## 项目结构

始终使用以下结构组织 Playwright 项目：

```
tests/
  e2e/
    auth/
      login.spec.ts
      signup.spec.ts
    dashboard/
      dashboard.spec.ts
    checkout/
      cart.spec.ts
      payment.spec.ts
  fixtures/
    auth.fixture.ts
    db.fixture.ts
  pages/
    login.page.ts
    dashboard.page.ts
    base.page.ts
  utils/
    test-data.ts
    helpers.ts
playwright.config.ts
```

## 页面对象模型

始终实现页面对象模型（POM）。每个页面类封装单个页面或组件的选择器和操作。

### 基类页面

```typescript
import { Page, Locator } from '@playwright/test';

export abstract class BasePage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async navigate(path: string): Promise<void> {
    await this.page.goto(path);
  }

  async waitForPageLoad(): Promise<void> {
    await this.page.waitForLoadState('networkidle');
  }

  async getTitle(): Promise<string> {
    return this.page.title();
  }

  async takeScreenshot(name: string): Promise<Buffer> {
    return this.page.screenshot({ path: `screenshots/${name}.png`, fullPage: true });
  }
}
```

### 具体页面类

```typescript
import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './base.page';

export class LoginPage extends BasePage {
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;
  readonly forgotPasswordLink: Locator;

  constructor(page: Page) {
    super(page);
    this.emailInput = page.getByLabel('Email');
    this.passwordInput = page.getByLabel('Password');
    this.submitButton = page.getByRole('button', { name: 'Sign in' });
    this.errorMessage = page.getByRole('alert');
    this.forgotPasswordLink = page.getByRole('link', { name: 'Forgot password?' });
  }

  async goto(): Promise<void> {
    await this.navigate('/login');
  }

  async login(email: string, password: string): Promise<void> {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }

  async expectErrorMessage(message: string): Promise<void> {
    await expect(this.errorMessage).toBeVisible();
    await expect(this.errorMessage).toHaveText(message);
  }
}
```

## 编写测试规范

### 基本测试结构

```typescript
import { test, expect } from '@playwright/test';
import { LoginPage } from '../pages/login.page';

test.describe('Login functionality', () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    await loginPage.goto();
  });

  test('should login with valid credentials', async ({ page }) => {
    await loginPage.login('user@example.com', 'SecurePass123!');
    await expect(page).toHaveURL('/dashboard');
    await expect(page.getByRole('heading', { name: 'Welcome' })).toBeVisible();
  });

  test('should show error for invalid credentials', async () => {
    await loginPage.login('user@example.com', 'wrongpassword');
    await loginPage.expectErrorMessage('Invalid email or password');
  });

  test('should navigate to forgot password page', async ({ page }) => {
    await loginPage.forgotPasswordLink.click();
    await expect(page).toHaveURL('/forgot-password');
  });
});
```

## 选择器 -- 优先级顺序

始终按以下优先级顺序选择选择器：

1. **`getByRole`** -- 首选。匹配无障碍树。
   ```typescript
   page.getByRole('button', { name: 'Submit' });
   page.getByRole('heading', { level: 1 });
   page.getByRole('link', { name: 'Read more' });
   page.getByRole('textbox', { name: 'Email' });
   ```

2. **`getByLabel`** -- 用于与标签关联的表单输入。
   ```typescript
   page.getByLabel('Email address');
   page.getByLabel('Password');
   ```

3. **`getByPlaceholder`** -- 当没有标签时使用。
   ```typescript
   page.getByPlaceholder('Search...');
   ```

4. **`getByText`** -- 用于带有可见文本的非交互元素。
   ```typescript
   page.getByText('Welcome back');
   page.getByText(/total: \$\d+/i);
   ```

5. **`getByTestId`** -- 当语义选择器不可行时使用。
   ```typescript
   page.getByTestId('checkout-total');
   ```

6. **CSS/XPath** -- 仅作为最后手段。使用时需说明为什么其他选项不适用。
   ```typescript
   // 除非绝对必要，否则避免使用
   page.locator('.legacy-widget >> nth=0');
   ```

## 断言

使用 Playwright 的 web 优先断言，会自动重试：

```typescript
// 可见性
await expect(locator).toBeVisible();
await expect(locator).toBeHidden();

// 文本内容
await expect(locator).toHaveText('Expected text');
await expect(locator).toContainText('partial');
await expect(locator).toHaveText(/regex pattern/);

// 输入值
await expect(locator).toHaveValue('expected value');
await expect(locator).toBeChecked();
await expect(locator).toBeDisabled();

// 页面级别
await expect(page).toHaveURL('/expected-path');
await expect(page).toHaveURL(/\/users\/\d+/);
await expect(page).toHaveTitle('Page Title');

// 数量
await expect(page.getByRole('listitem')).toHaveCount(5);

// CSS
await expect(locator).toHaveCSS('color', 'rgb(255, 0, 0)');
await expect(locator).toHaveClass(/active/);

// 截图对比
await expect(page).toHaveScreenshot('homepage.png');
await expect(locator).toHaveScreenshot('button-hover.png');
```

## Fixtures

使用自定义 fixtures 来共享设置逻辑和认证状态：

```typescript
import { test as base, Page } from '@playwright/test';
import { LoginPage } from '../pages/login.page';
import { DashboardPage } from '../pages/dashboard.page';

type MyFixtures = {
  loginPage: LoginPage;
  dashboardPage: DashboardPage;
  authenticatedPage: Page;
};

export const test = base.extend<MyFixtures>({
  loginPage: async ({ page }, use) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await use(loginPage);
  },

  dashboardPage: async ({ page }, use) => {
    await use(new DashboardPage(page));
  },

  authenticatedPage: async ({ browser }, use) => {
    const context = await browser.newContext({
      storageState: 'playwright/.auth/user.json',
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

export { expect } from '@playwright/test';
```

### 认证状态复用

```typescript
// auth.setup.ts -- 运行一次以存储认证状态
import { test as setup, expect } from '@playwright/test';

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill('admin@example.com');
  await page.getByLabel('Password').fill('AdminPass123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL('/dashboard');
  await page.context().storageState({ path: 'playwright/.auth/user.json' });
});
```

## 配置最佳实践

```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ['html', { open: 'never' }],
    ['json', { outputFile: 'test-results/results.json' }],
    process.env.CI ? ['github'] : ['list'],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['setup'],
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
      dependencies: ['setup'],
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      dependencies: ['setup'],
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 5'] },
      dependencies: ['setup'],
    },
    {
      name: 'mobile-safari',
      use: { ...devices['iPhone 13'] },
      dependencies: ['setup'],
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
```

## 处理常见场景

### 导航和路由

```typescript
test('should navigate through multi-step wizard', async ({ page }) => {
  await page.goto('/wizard');

  // Step 1
  await page.getByLabel('Full name').fill('Jane Doe');
  await page.getByRole('button', { name: 'Next' }).click();

  // Step 2
  await expect(page).toHaveURL('/wizard/step-2');
  await page.getByLabel('Email').fill('jane@example.com');
  await page.getByRole('button', { name: 'Next' }).click();

  // Step 3 -- confirmation
  await expect(page).toHaveURL('/wizard/step-3');
  await expect(page.getByText('Jane Doe')).toBeVisible();
  await expect(page.getByText('jane@example.com')).toBeVisible();
});
```

### 处理对话框

```typescript
test('should handle confirmation dialog', async ({ page }) => {
  page.on('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toBe('Are you sure you want to delete?');
    await dialog.accept();
  });

  await page.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Item deleted')).toBeVisible();
});
```

### 文件上传

```typescript
test('should upload a file', async ({ page }) => {
  const fileInput = page.getByLabel('Upload document');
  await fileInput.setInputFiles('test-data/sample.pdf');
  await expect(page.getByText('sample.pdf')).toBeVisible();
  await page.getByRole('button', { name: 'Submit' }).click();
  await expect(page.getByText('Upload successful')).toBeVisible();
});
```

### iframe 处理

```typescript
test('should interact with iframe content', async ({ page }) => {
  const iframe = page.frameLocator('#payment-iframe');
  await iframe.getByLabel('Card number').fill('4111111111111111');
  await iframe.getByLabel('Expiry').fill('12/25');
  await iframe.getByLabel('CVC').fill('123');
});
```

### 网络拦截

```typescript
test('should mock API response', async ({ page }) => {
  await page.route('**/api/products', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 1, name: 'Mocked Product', price: 9.99 },
      ]),
    });
  });

  await page.goto('/products');
  await expect(page.getByText('Mocked Product')).toBeVisible();
});

test('should wait for specific API call', async ({ page }) => {
  const responsePromise = page.waitForResponse('**/api/submit');
  await page.getByRole('button', { name: 'Submit' }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
});
```

### 处理下拉框和选择元素

```typescript
// Native select
await page.getByLabel('Country').selectOption('US');
await page.getByLabel('Country').selectOption({ label: 'United States' });

// Custom dropdown
await page.getByRole('combobox', { name: 'Country' }).click();
await page.getByRole('option', { name: 'United States' }).click();
```

## 最佳实践

1. **永远不要使用 `page.waitForTimeout()`** -- 使用自动等待或显式事件等待代替。
2. **始终使用 `test.describe` 块** 来分组相关测试。
3. **使用 `test.beforeEach`** 进行通用设置，但保持最小化。
4. **为测试添加标签** 以便选择性执行：
   ```typescript
   test('checkout flow @smoke @critical', async ({ page }) => { ... });
   ```
5. **使用软断言** 进行非阻塞检查：
   ```typescript
   await expect.soft(locator).toHaveText('expected');
   await expect.soft(other).toBeVisible();
   ```
6. **参数化测试** 使用 `test.describe` 和数组：
   ```typescript
   const users = [
     { role: 'admin', canDelete: true },
     { role: 'viewer', canDelete: false },
   ];
   for (const { role, canDelete } of users) {
     test(`${role} delete permission`, async ({ page }) => { ... });
   }
   ```
7. **在配置级别设置合理的超时时间**，而不是在单个测试中。
8. **使用 trace viewer 进行调试**：`npx playwright show-trace trace.zip`
9. **明智地并行化** -- 使用 `fullyParallel: true` 但确保测试隔离。
10. **清理测试数据** 在 `afterEach` 中或使用具有自动清理功能的 fixtures。

## 应该避免的反模式

1. **硬编码等待** -- `await page.waitForTimeout(3000)` 是不稳定的且缓慢的。
2. **测试之间共享可变状态** -- 每个测试必须独立。
3. **测试实现细节** -- 测试行为，而不是 DOM 结构。
4. **过于具体的选择器** -- `div.container > ul > li:nth-child(3) > span.text` 在任何布局变化时都会失效。
5. **巨大的测试文件** -- 保持测试文件专注于单个功能或页面。
6. **忽视测试隔离** -- 依赖于执行顺序的测试在并行模式下会失败。
7. **不使用 base URL** -- 始终配置 `baseURL` 并在 `goto` 中使用相对路径。
8. **跳过断言消息** -- 当断言不明确时添加上下文。
9. **直接测试第三方服务** -- 模拟外部 API 和支付网关。
10. **不进行清理** -- 文件上传、数据库记录和其他副作用必须清理。

## 调试技巧

- 以有头模式运行：`npx playwright test --headed`
- 以 UI 模式运行：`npx playwright test --ui`
- 调试单个测试：`npx playwright test --debug tests/login.spec.ts`
- 生成代码：`npx playwright codegen https://example.com`
- 查看 trace：`npx playwright show-trace test-results/trace.zip`
- 使用 `test.only` 在开发期间隔离单个测试。
- 使用 `await page.pause()` 暂停执行并检查页面。