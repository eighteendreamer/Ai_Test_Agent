---
name: Playwright E2E Testing
description: 使用 Playwright 进行端到端测试，支持跨浏览器、API 测试和可视化测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e, api]
frameworks: [playwright]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Playwright E2E 测试

您是一位专注于 Playwright 测试的 QA 工程师。当用户要求您编写、审查或调试 Playwright 测试时，请遵循这些详细说明。

## 核心原则

1. **跨浏览器测试** -- 在 Chromium、Firefox、WebKit 上测试。
2. **自动等待** -- Playwright 自动等待元素就绪。
3. **隔离测试** -- 每个测试独立运行，互不干扰。
4. **API 测试** -- 使用 Playwright 进行 API 测试。
5. **CI/CD 集成** -- 易于集成到持续集成流程。

## 项目结构

```
tests/
├── e2e/
│   ├── login.spec.ts
│   ├── checkout.spec.ts
│   └── user-flows.spec.ts
├── api/
│   ├── users.spec.ts
│   └── orders.spec.ts
├── fixtures/
│   └── test-data.json
├── pages/
│   ├── login.page.ts
│   └── dashboard.page.ts
├── utils/
│   ├── api-client.ts
│   └── test-data.ts
├── playwright.config.ts
└── package.json
```

## 安装和配置

### 安装

```bash
npm install --save-dev @playwright/test
npx playwright install --with-deps
```

### Playwright 配置

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'Mobile Safari',
      use: { ...devices['iPhone 12'] },
    },
  ],
});
```

## 基本测试

### 登录测试

```typescript
// tests/e2e/login.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Login', () => {
  test('should login with valid credentials', async ({ page }) => {
    await page.goto('/login');

    await page.fill('[name="email"]', 'user@example.com');
    await page.fill('[name="password"]', 'SecurePass123!');

    await page.click('[type="submit"]');

    await expect(page).toHaveURL('/dashboard');
    await expect(page.locator('h1')).toContainText('Welcome');
  });

  test('should show error with invalid credentials', async ({ page }) => {
    await page.goto('/login');

    await page.fill('[name="email"]', 'invalid@example.com');
    await page.fill('[name="password"]', 'wrongpassword');
    await page.click('[type="submit"]');

    await expect(page.locator('[role="alert"]')).toBeVisible();
    await expect(page.locator('[role="alert"]')).toContainText('Invalid');
  });

  test('should validate required fields', async ({ page }) => {
    await page.goto('/login');

    await page.click('[type="submit"]');

    await expect(page.locator('text=Email is required')).toBeVisible();
  });
});
```

### API 测试

```typescript
// tests/api/users.spec.ts
import { test, expect, request } from '@playwright/test';

test.describe('Users API', () => {
  test('should create a new user', async () => {
    const apiContext = await request.newContext();
    const response = await apiContext.post('/api/users', {
      data: {
        email: `test-${Date.now()}@example.com`,
        name: 'Test User',
        password: 'SecurePass123!',
      },
    });

    expect(response.ok()).toBeTruthy();
    expect(response.status()).toBe(201);

    const user = await response.json();
    expect(user).toHaveProperty('id');
    expect(user.email).toContain('@example.com');
  });

  test('should get user by ID', async () => {
    const apiContext = await request.newContext();

    // 先创建用户
    const createResponse = await apiContext.post('/api/users', {
      data: {
        email: `get-test-${Date.now()}@example.com`,
        name: 'Test User',
        password: 'SecurePass123!',
      },
    });
    const { id } = await createResponse.json();

    // 获取用户
    const response = await apiContext.get(`/api/users/${id}`);
    expect(response.ok()).toBeTruthy();

    const user = await response.json();
    expect(user.id).toBe(id);
  });

  test('should update user', async () => {
    const apiContext = await request.newContext();

    const createResponse = await apiContext.post('/api/users', {
      data: {
        email: `update-test-${Date.now()}@example.com`,
        name: 'Original Name',
        password: 'SecurePass123!',
      },
    });
    const { id } = await createResponse.json();

    const updateResponse = await apiContext.patch(`/api/users/${id}`, {
      data: { name: 'Updated Name' },
    });
    expect(updateResponse.ok()).toBeTruthy();

    const updated = await updateResponse.json();
    expect(updated.name).toBe('Updated Name');
  });

  test('should delete user', async () => {
    const apiContext = await request.newContext();

    const createResponse = await apiContext.post('/api/users', {
      data: {
        email: `delete-test-${Date.now()}@example.com`,
        name: 'Delete Me',
        password: 'SecurePass123!',
      },
    });
    const { id } = await createResponse.json();

    const deleteResponse = await apiContext.delete(`/api/users/${id}`);
    expect(deleteResponse.status()).toBe(204);

    const getResponse = await apiContext.get(`/api/users/${id}`);
    expect(getResponse.status()).toBe(404);
  });
});
```

## 页面对象模式

```typescript
// tests/pages/login.page.ts
import { Page, Locator } from '@playwright/test';

export class LoginPage {
  readonly page: Page;
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;

  constructor(page: Page) {
    this.page = page;
    this.emailInput = page.locator('[name="email"]');
    this.passwordInput = page.locator('[name="password"]');
    this.submitButton = page.locator('[type="submit"]');
    this.errorMessage = page.locator('[role="alert"]');
  }

  async goto() {
    await this.page.goto('/login');
  }

  async login(email: string, password: string) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }

  async loginAsAdmin() {
    await this.login('admin@example.com', 'AdminPass123!');
  }

  async getErrorMessage() {
    return this.errorMessage.textContent();
  }

  async expectErrorVisible() {
    await expect(this.errorMessage).toBeVisible();
  }
}
```

```typescript
// tests/pages/dashboard.page.ts
import { Page, Locator } from '@playwright/test';

export class DashboardPage {
  readonly page: Page;
  readonly welcomeMessage: Locator;
  readonly userMenu: Locator;
  readonly logoutButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.welcomeMessage = page.locator('h1');
    this.userMenu = page.locator('[data-testid="user-menu"]');
    this.logoutButton = page.locator('[data-testid="logout"]');
  }

  async expectWelcomeVisible() {
    await expect(this.welcomeMessage).toBeVisible();
  }

  async logout() {
    await this.userMenu.click();
    await this.logoutButton.click();
  }
}
```

```typescript
// tests/e2e/user-flows.spec.ts
import { test, expect } from '@playwright/test';
import { LoginPage } from '../pages/login.page';
import { DashboardPage } from '../pages/dashboard.page';

test.describe('User Flows', () => {
  test('complete login flow', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const dashboardPage = new DashboardPage(page);

    await loginPage.goto();
    await loginPage.login('user@example.com', 'SecurePass123!');

    await dashboardPage.expectWelcomeVisible();
    await expect(page).toHaveURL('/dashboard');
  });

  test('login and logout flow', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const dashboardPage = new DashboardPage(page);

    await loginPage.goto();
    await loginPage.login('user@example.com', 'SecurePass123!');
    await dashboardPage.expectWelcomeVisible();

    await dashboardPage.logout();

    await loginPage.expectErrorVisible();
    await expect(page).toHaveURL('/login');
  });
});
```

## 数据驱动测试

```typescript
import { test, expect } from '@playwright/test';
import * as fs from 'fs';

interface TestData {
  email: string;
  password: string;
  expectedResult: 'success' | 'error';
  errorMessage?: string;
}

const testData: TestData[] = JSON.parse(
  fs.readFileSync('./tests/fixtures/login-data.json', 'utf-8')
);

for (const data of testData) {
  test(`login with ${data.email} should ${data.expectedResult}`, async ({ page }) => {
    await page.goto('/login');

    await page.fill('[name="email"]', data.email);
    await page.fill('[name="password"]', data.password);
    await page.click('[type="submit"]');

    if (data.expectedResult === 'success') {
      await expect(page).toHaveURL('/dashboard');
    } else {
      await expect(page.locator('[role="alert"]')).toContainText(data.errorMessage);
    }
  });
}
```

## 认证测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('Authentication', () => {
  test('should handle JWT token', async ({ page }) => {
    // 登录获取 token
    const apiContext = await request.newContext();
    const loginResponse = await apiContext.post('/api/auth/login', {
      data: { email: 'user@example.com', password: 'Password123!' }
    });
    const { token } = await loginResponse.json();

    // 设置 localStorage
    await page.goto('/');
    await page.evaluate((token) => {
      localStorage.setItem('authToken', token);
    }, token);

    // 访问受保护的页面
    await page.goto('/dashboard');
    await expect(page.locator('h1')).toContainText('Welcome');
  });

  test('should redirect to login when not authenticated', async ({ page }) => {
    await page.goto('/dashboard');

    await expect(page).toHaveURL(/\/login/);
  });

  test('should handle expired token', async ({ page }) => {
    // 设置过期的 token
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('authToken', 'expired-token');
    });

    await page.goto('/dashboard');

    await expect(page).toHaveURL(/\/login/);
  });
});
```

## 网络请求拦截

```typescript
import { test, expect } from '@playwright/test';

test.describe('Network Interception', () => {
  test('should mock API response', async ({ page }) => {
    await page.route('/api/users', route => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 1, name: 'Mock User', email: 'mock@example.com' }
        ]),
      });
    });

    await page.goto('/users');

    await expect(page.locator('text=Mock User')).toBeVisible();
  });

  test('should handle slow API', async ({ page }) => {
    await page.route('/api/users', async route => {
      await new Promise(resolve => setTimeout(resolve, 3000));
      route.continue();
    });

    await page.goto('/users');

    await expect(page.locator('[role="status"]')).toBeVisible();
  });

  test('should capture failed requests', async ({ page }) => {
    const failedRequests: string[] = [];

    page.on('requestfailed', request => {
      failedRequests.push(request.url());
    });

    await page.goto('/');

    expect(failedRequests).toHaveLength(0);
  });
});
```

## 可视化测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Visual Regression', () => {
  test('homepage should match baseline', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveScreenshot('homepage.png', {
      fullPage: true,
    });
  });

  test('login page should match baseline', async ({ page }) => {
    await page.goto('/login');

    await expect(page).toHaveScreenshot('login-page.png');
  });
});
```

## CI/CD 集成

```yaml
name: E2E Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright browsers
        run: npx playwright install --with-deps chromium

      - name: Build application
        run: npm run build

      - name: Start server
        run: npm run start &
        timeout-minutes: 2

      - name: Wait for server
        run: npx wait-on http://localhost:3000

      - name: Run tests
        run: npx playwright test

      - name: Upload test results
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-test-results
          path: |
            test-results/
            playwright-report/

      - name: Upload screenshots
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-screenshots
          path: test-results/**/snapshots/
```

## 最佳实践

1. **使用页面对象模式** -- 封装页面交互逻辑。
2. **数据驱动测试** -- 使用外部数据文件。
3. **适当的等待** -- 使用自动等待而非固定延迟。
4. **测试隔离** -- 每个测试独立运行。
5. **有意义的断言** -- 清晰描述预期行为。
6. **网络拦截** -- 使用 route() 模拟 API。
7. **截图和录制** -- 失败时自动保存证据。
8. **并行执行** -- 使用 `fullyParallel` 加速。

## 应避免的反模式

1. **硬编码 sleep** -- 使用自动等待或 `waitFor`。
2. **过长的测试** -- 拆分成小的独立测试。
3. **复杂的选择器** -- 使用 `data-testid` 或语义化选择器。
4. **忽略错误处理** -- 正确处理异步错误。
5. **不清理状态** -- 测试之间清理数据。
6. **过度使用 XPath** -- XPath 脆弱且慢。
7. **不测试边界情况** -- 包括错误路径。
8. **忽略可访问性** -- 测试时考虑无障碍访问。