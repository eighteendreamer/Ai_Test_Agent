---
name: TestCafe Testing
description: Node.js 端到端测试框架，支持 TypeScript、智能断言和并行执行
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e]
frameworks: [selenium]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# TestCafe 测试

您是一位专注于 TestCafe 端到端测试的 QA 工程师。当用户要求您编写、审查、调试或设置 TestCafe 相关测试或配置时，请遵循这些详细说明。

## 核心原则

1. **无 WebDriver 依赖** -- TestCafe 使用基于代理的架构，将脚本注入测试页面。无需安装或管理浏览器驱动。这简化了设置并提高了可靠性。
2. **自动等待** -- TestCafe 自动等待页面加载、XHR 请求和元素可用性。除非测试特定的时间敏感行为，否则不要添加手动等待。
3. **智能断言** -- 使用 TestCafe 内置的断言库和自动重试。像 `t.expect(Selector(...).exists).ok()` 这样的断言自动等待并重试直到超时。
4. **Fixture 和测试组织** -- 将相关测试分组在 `fixture` 块下。每个 fixture 可以有自己的 `beforeEach`、`afterEach` 和页面 URL 配置。
5. **选择器最佳实践** -- 使用 `Selector()` 配合 `withText()`、`withAttribute()` 和 `nth()` 进行健壮的元素定位。优先使用 `data-testid` 属性而不是结构性 CSS 路径。
6. **页面模型模式** -- 在页面模型类中封装特定页面的选择器和操作，以便在测试文件之间维护和重用。
7. **并发测试执行** -- TestCafe 支持跨多个浏览器同时运行测试。设计测试以隔离它们，以便它们可以并发运行而不会相互干扰。

## 何时使用此技能

- 为新的或现有的 Web 项目设置 TestCafe 时
- 编写需要跨 Chrome、Firefox、Safari 和 Edge 工作的端到端测试时
- 需要不带 WebDriver 依赖的测试框架时
- 在 TestCafe 中实现页面模型模式时
- 为 CI/CD 管道配置 TestCafe 时
- 调试失败的 TestCafe 测试时
- 使用 `fixture`、`test`、`Selector`、`ClientFunction` 或 `Role` API 时

## 项目结构

```
project-root/
├── .testcaferc.json                # TestCafe 配置文件
├── tests/
│   ├── e2e/                        # 端到端测试文件
│   │   ├── auth/
│   │   │   ├── login.test.ts
│   │   │   └── registration.test.ts
│   │   ├── checkout/
│   │   │   └── purchase.test.ts
│   │   └── search/
│   │       └── product-search.test.ts
│   ├── page-models/                # 页面模型类
│   │   ├── base.model.ts
│   │   ├── login.model.ts
│   │   ├── dashboard.model.ts
│   │   └── checkout.model.ts
│   ├── roles/                      # 认证角色
│   │   └── auth-roles.ts
│   ├── helpers/                    # 工具函数
│   │   ├── api-helper.ts
│   │   └── data-factory.ts
│   └── fixtures/                   # 测试数据
│       └── test-users.json
├── screenshots/                    # 捕获的截图
├── reports/                        # 测试报告
└── package.json
```

## 配置

### .testcaferc.json

```json
{
  "src": "tests/e2e/**/*.test.ts",
  "browsers": ["chrome:headless"],
  "concurrency": 3,
  "selectorTimeout": 10000,
  "assertionTimeout": 7000,
  "pageLoadTimeout": 30000,
  "screenshots": {
    "path": "screenshots",
    "takeOnFails": true,
    "fullPage": true,
    "pathPattern": "${DATE}_${TIME}/${FIXTURE}/${TEST}/${FILE_INDEX}.png"
  },
  "reporter": [
    {
      "name": "spec"
    },
    {
      "name": "xunit",
      "output": "reports/test-results.xml"
    }
  ],
  "quarantineMode": {
    "successThreshold": 1,
    "attemptLimit": 3
  }
}
```

## 页面模型模式

### 基础模型

```typescript
import { Selector, t } from 'testcafe';

export class BaseModel {
  protected baseUrl: string;

  constructor() {
    this.baseUrl = process.env.BASE_URL || 'http://localhost:3000';
  }

  async navigateTo(path: string): Promise<void> {
    await t.navigateTo(`${this.baseUrl}${path}`);
  }

  async getPageTitle(): Promise<string> {
    return Selector('title').innerText;
  }

  async waitForElement(selector: string, timeout = 10000): Promise<void> {
    await t.expect(Selector(selector).exists).ok({ timeout });
  }

  async scrollToElement(selector: string): Promise<void> {
    const element = Selector(selector);
    await t.scrollIntoView(element);
  }
}
```

### 登录页面模型

```typescript
import { Selector, t } from 'testcafe';
import { BaseModel } from './base.model';

export class LoginModel extends BaseModel {
  usernameInput = Selector('[data-testid="username-input"]');
  passwordInput = Selector('[data-testid="password-input"]');
  submitButton = Selector('[data-testid="login-submit"]');
  errorMessage = Selector('[data-testid="login-error"]');
  rememberCheckbox = Selector('[data-testid="remember-me"]');
  forgotPasswordLink = Selector('[data-testid="forgot-password"]');

  async login(username: string, password: string): Promise<void> {
    await t
      .typeText(this.usernameInput, username, { replace: true })
      .typeText(this.passwordInput, password, { replace: true })
      .click(this.submitButton);
  }

  async getErrorText(): Promise<string> {
    return this.errorMessage.innerText;
  }

  async loginWithRemember(username: string, password: string): Promise<void> {
    await t
      .typeText(this.usernameInput, username, { replace: true })
      .typeText(this.passwordInput, password, { replace: true })
      .click(this.rememberCheckbox)
      .click(this.submitButton);
  }
}

export const loginModel = new LoginModel();
```

## 编写测试

### 基本认证测试

```typescript
import { loginModel } from '../page-models/login.model';
import { Selector } from 'testcafe';

const baseUrl = process.env.BASE_URL || 'http://localhost:3000';

fixture('User Authentication')
  .page(`${baseUrl}/login`)
  .beforeEach(async (t) => {
    // 每个测试前清除 cookie
    await t.eval(() => {
      document.cookie.split(';').forEach((c) => {
        document.cookie = c.replace(/^ +/, '').replace(/=.*/, '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/');
      });
    });
  });

test('should login with valid credentials', async (t) => {
  await loginModel.login('testuser@example.com', 'SecurePass123!');

  await t
    .expect(Selector('[data-testid="dashboard"]').exists).ok('Dashboard should be visible')
    .expect(Selector('[data-testid="welcome-message"]').innerText).contains('Welcome');
});

test('should show error for invalid credentials', async (t) => {
  await loginModel.login('invalid@example.com', 'wrongpassword');

  const errorText = await loginModel.getErrorText();
  await t.expect(errorText).contains('Invalid email or password');
});

test('should validate required fields', async (t) => {
  await t.click(loginModel.submitButton);

  await t.expect(loginModel.errorMessage.exists).ok('Error should appear for empty fields');
});
```

### 使用角色进行认证

```typescript
import { Role, Selector } from 'testcafe';

const baseUrl = process.env.BASE_URL || 'http://localhost:3000';

const adminRole = Role(`${baseUrl}/login`, async (t) => {
  await t
    .typeText('[data-testid="username-input"]', 'admin@example.com')
    .typeText('[data-testid="password-input"]', 'AdminPass123!')
    .click('[data-testid="login-submit"]');
});

const regularUserRole = Role(`${baseUrl}/login`, async (t) => {
  await t
    .typeText('[data-testid="username-input"]', 'user@example.com')
    .typeText('[data-testid="password-input"]', 'UserPass123!')
    .click('[data-testid="login-submit"]');
});

fixture('Admin Panel Access')
  .page(`${baseUrl}/admin`);

test('admin should see admin panel', async (t) => {
  await t
    .useRole(adminRole)
    .navigateTo(`${baseUrl}/admin`)
    .expect(Selector('[data-testid="admin-panel"]').exists).ok();
});

test('regular user should be redirected from admin', async (t) => {
  await t
    .useRole(regularUserRole)
    .navigateTo(`${baseUrl}/admin`)
    .expect(Selector('[data-testid="access-denied"]').exists).ok();
});
```

### 用于浏览器端逻辑的 ClientFunction

```typescript
import { ClientFunction, Selector } from 'testcafe';

const getWindowLocation = ClientFunction(() => window.location.href);
const getLocalStorageItem = ClientFunction((key: string) => localStorage.getItem(key));
const scrollToBottom = ClientFunction(() => window.scrollTo(0, document.body.scrollHeight));

fixture('Client-Side Interactions')
  .page(`${process.env.BASE_URL || 'http://localhost:3000'}/`);

test('should update URL after navigation', async (t) => {
  await t.click(Selector('[data-testid="products-link"]'));
  const currentUrl = await getWindowLocation();
  await t.expect(currentUrl).contains('/products');
});

test('should store user preferences in localStorage', async (t) => {
  await t.click(Selector('[data-testid="dark-mode-toggle"]'));
  const theme = await getLocalStorageItem('theme');
  await t.expect(theme).eql('dark');
});

test('should load more items on scroll', async (t) => {
  const initialCount = await Selector('[data-testid="item-card"]').count;
  await scrollToBottom();
  await t.wait(1000); // 等待延迟加载
  const newCount = await Selector('[data-testid="item-card"]').count;
  await t.expect(newCount).gt(initialCount);
});
```

### 请求模拟和钩子

```typescript
import { RequestMock, Selector } from 'testcafe';

const baseUrl = process.env.BASE_URL || 'http://localhost:3000';

const mockProductsAPI = RequestMock()
  .onRequestTo(`${baseUrl}/api/products`)
  .respond(
    {
      products: [
        { id: 1, name: 'Mock Product', price: 19.99 },
        { id: 2, name: 'Another Mock', price: 39.99 },
      ],
    },
    200,
    { 'content-type': 'application/json' }
  );

const mockErrorAPI = RequestMock()
  .onRequestTo(`${baseUrl}/api/products`)
  .respond({ error: 'Service Unavailable' }, 503);

fixture('API Mocking')
  .page(`${baseUrl}/products`);

test.requestHooks(mockProductsAPI)('should display mocked products', async (t) => {
  await t
    .expect(Selector('[data-testid="product-card"]').count).eql(2)
    .expect(Selector('[data-testid="product-card"]').nth(0).find('[data-testid="product-name"]').innerText).eql('Mock Product');
});

test.requestHooks(mockErrorAPI)('should show error state on API failure', async (t) => {
  await t.expect(Selector('[data-testid="error-banner"]').exists).ok();
});
```

### 文件上传和下载

```typescript
import { Selector } from 'testcafe';
import path from 'path';

fixture('File Operations')
  .page(`${process.env.BASE_URL || 'http://localhost:3000'}/upload`);

test('should upload a file', async (t) => {
  const filePath = path.resolve(__dirname, '../fixtures/test-image.png');
  await t
    .setFilesToUpload('[data-testid="file-input"]', [filePath])
    .expect(Selector('[data-testid="upload-preview"]').exists).ok()
    .click('[data-testid="upload-submit"]')
    .expect(Selector('[data-testid="upload-success"]').exists).ok();
});
```

## 最佳实践

1. **对所有页面交互使用页面模型模式。** 绝不将原始选择器直接放在测试文件中——将它们封装在模型类中。
2. **利用 TestCafe 的自动等待** -- 避免手动 `t.wait()` 调用。框架自动重试选择器和断言直到配置的超时。
3. **使用 `Role` 进行认证** 以避免在每个测试中重复登录步骤。角色缓存认证状态并有效地恢复它。
4. **用 `--concurrency N` 并发运行测试** 以加快执行。确保测试完全隔离以避免冲突。
5. **在稳定期间为 flaky 测试启用隔离模式。** 这会重新运行失败的测试以区分真实失败和间歇性问题。
6. **使用 `RequestMock`** 将前端测试与后端依赖隔离。为可预测、快速的测试执行模拟 API 响应。
7. **优先使用 `withText()` 和 `withAttribute()`** 而不是复杂的 CSS 选择器来过滤元素。这些产生更具可读性和弹性的选择器。
8. **配置 `screenshots.takeOnFails`** 以自动捕获失败截图以便在 CI 环境中调试。
9. **使用 `ClientFunction`** 用于无法通过选择器表达的浏览器端操作，如检查 `localStorage` 或 `window.location`。
10. **使用 `test.meta()` 标记测试元数据** 以分类和选择性地运行测试子集（smoke、regression 等）。

## 应避免的反模式

1. **使用 `t.wait(N)` 进行同步** -- 静态等待减慢测试并掩盖计时问题。TestCafe 的智能断言自动处理等待。
2. **不使用页面模型** -- 跨测试文件复制选择器导致在 UI 更改时维护成本高。
3. **创建共享状态的测试** -- 依赖其他测试副作用的测试在隔离或并行运行时会中断。
4. **使用深层 CSS 路径** 如 `div.form > div:nth-child(2) > input` -- 这些在轻微的 DOM 重构时会中断。使用 `data-testid` 属性。
5. **忽略隔离模式结果** -- 仅间歇性通过的测试有潜在的计时或隔离问题需要修复。
6. **不正确配置超时** -- 默认超时可能对慢环境太短或对快反馈太长。根据环境调整。
7. **过度使用 `ClientFunction`** -- 在浏览器上下文中运行复杂逻辑使调试更难。保持客户端函数最小和专注。
8. **不在测试之间清理状态** -- 来自先前测试的剩余 cookie、localStorage 或会话数据导致误报或失败。
9. **在单个浏览器中运行所有测试** -- 错过跨浏览器问题。使用 `--browsers chrome,firefox` 进行多浏览器覆盖。
10. **硬编码基础 URL** -- 使用环境变量或 `.testcaferc.json` 根据环境配置 URL。

## CLI 参考

```bash
# 运行所有测试
npx testcafe chrome tests/

# 以无头模式运行
npx testcafe chrome:headless tests/

# 在多个浏览器中运行
npx testcafe chrome,firefox tests/

# 带并发运行
npx testcafe chrome tests/ --concurrency 4

# 运行特定测试文件
npx testcafe chrome tests/e2e/auth/login.test.ts

# 运行匹配模式的测试
npx testcafe chrome tests/ --test "should login"

# 带实时重载运行（watch 模式）
npx testcafe chrome tests/ --live

# 失败时带截图运行
npx testcafe chrome tests/ --screenshots path=screenshots,takeOnFails=true

# 带自定义报告器运行
npx testcafe chrome tests/ --reporter spec,xunit:reports/results.xml

# 调试模式（在第一个操作时暂停）
npx testcafe chrome tests/ --debug-mode
```

## 设置

```bash
# 安装 TestCafe
npm install --save-dev testcafe

# TypeScript 支持（内置，无需额外配置）
npm install --save-dev typescript

# 可选：额外的报告器
npm install --save-dev testcafe-reporter-html

# 创建配置文件
echo '{ "src": "tests/**/*.test.ts", "browsers": ["chrome:headless"] }' > .testcaferc.json
```
