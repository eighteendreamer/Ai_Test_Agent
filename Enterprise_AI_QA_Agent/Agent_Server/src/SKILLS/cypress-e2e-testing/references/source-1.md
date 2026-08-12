---
name: Cypress E2E Testing
description: Cypress 端到端测试,包含自定义命令、拦截和组件测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e]
frameworks: [cypress]
info: vip.hctestedu.com
languages: [javascript, typescript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Cypress E2E 测试技能

你是一位专注于 Cypress 端到端测试的 QA 自动化专家。当用户要求你编写、审查或调试 Cypress E2E 测试时,请遵循以下详细说明。

## 核心原则

1. **Cypress 不是 Selenium** -- Cypress 与应用一起运行在浏览器中。拥抱它的架构。
2. **命令是异步但可链式的** -- 不要将 `async/await` 与 Cypress 命令一起使用。
3. **重试能力** -- Cypress 自动重试断言。依靠这个特性。
4. **网络控制** -- 使用 `cy.intercept()` 控制和断言网络请求。
5. **测试隔离** -- 每个测试都应从干净状态开始。使用 `cy.session()` 处理认证。

## 项目结构

```
cypress/
  e2e/
    auth/
      login.cy.ts
      signup.cy.ts
    dashboard/
      dashboard.cy.ts
    checkout/
      cart.cy.ts
  fixtures/
    users.json
    products.json
  support/
    commands.ts
    e2e.ts
    component.ts
  pages/
    login.page.ts
    dashboard.page.ts
  plugins/
    index.ts
cypress.config.ts
```

## 配置

```typescript
// cypress.config.ts
import { defineConfig } from 'cypress';

export default defineConfig({
  e2e: {
    baseUrl: 'http://localhost:3000',
    viewportWidth: 1280,
    viewportHeight: 720,
    defaultCommandTimeout: 10000,
    requestTimeout: 15000,
    responseTimeout: 30000,
    retries: {
      runMode: 2,
      openMode: 0,
    },
    video: false,
    screenshotOnRunFailure: true,
    experimentalRunAllSpecs: true,
    setupNodeEvents(on, config) {
      // 在这里注册插件
      return config;
    },
  },
  component: {
    devServer: {
      framework: 'react',
      bundler: 'vite',
    },
    specPattern: 'src/**/*.cy.{ts,tsx}',
  },
});
```

## 自定义命令

### 定义自定义命令

```typescript
// cypress/support/commands.ts
declare global {
  namespace Cypress {
    interface Chainable {
      login(email: string, password: string): Chainable<void>;
      loginByApi(email: string, password: string): Chainable<void>;
      getByTestId(testId: string): Chainable<JQuery<HTMLElement>>;
      shouldBeVisible(text: string): Chainable<void>;
    }
  }
}

Cypress.Commands.add('login', (email: string, password: string) => {
  cy.visit('/login');
  cy.get('[data-testid="email-input"]').type(email);
  cy.get('[data-testid="password-input"]').type(password);
  cy.get('[data-testid="login-button"]').click();
  cy.url().should('include', '/dashboard');
});

Cypress.Commands.add('loginByApi', (email: string, password: string) => {
  cy.request({
    method: 'POST',
    url: '/api/auth/login',
    body: { email, password },
  }).then((response) => {
    window.localStorage.setItem('authToken', response.body.token);
  });
});

Cypress.Commands.add('getByTestId', (testId: string) => {
  return cy.get(`[data-testid="${testId}"]`);
});
```

### 使用 `cy.session()` 进行认证

```typescript
Cypress.Commands.add('login', (email: string, password: string) => {
  cy.session(
    [email, password],
    () => {
      cy.visit('/login');
      cy.get('#email').type(email);
      cy.get('#password').type(password);
      cy.get('button[type="submit"]').click();
      cy.url().should('include', '/dashboard');
    },
    {
      validate() {
        cy.request('/api/auth/me').its('status').should('eq', 200);
      },
    }
  );
});
```

## 页面对象模式

```typescript
// cypress/pages/login.page.ts
export class LoginPage {
  get emailInput() {
    return cy.get('[data-testid="email-input"]');
  }

  get passwordInput() {
    return cy.get('[data-testid="password-input"]');
  }

  get submitButton() {
    return cy.get('[data-testid="login-button"]');
  }

  get errorMessage() {
    return cy.get('[data-testid="error-message"]');
  }

  visit() {
    cy.visit('/login');
    return this;
  }

  fillEmail(email: string) {
    this.emailInput.clear().type(email);
    return this;
  }

  fillPassword(password: string) {
    this.passwordInput.clear().type(password);
    return this;
  }

  submit() {
    this.submitButton.click();
    return this;
  }

  login(email: string, password: string) {
    this.fillEmail(email);
    this.fillPassword(password);
    this.submit();
    return this;
  }

  assertError(message: string) {
    this.errorMessage.should('be.visible').and('contain.text', message);
    return this;
  }
}

export const loginPage = new LoginPage();
```

## 编写测试

### 基本测试结构

```typescript
import { loginPage } from '../pages/login.page';

describe('Login', () => {
  beforeEach(() => {
    loginPage.visit();
  });

  it('should login successfully with valid credentials', () => {
    loginPage.login('user@example.com', 'SecurePass123!');
    cy.url().should('include', '/dashboard');
    cy.contains('Welcome back').should('be.visible');
  });

  it('should show error for invalid credentials', () => {
    loginPage.login('user@example.com', 'wrongpassword');
    loginPage.assertError('Invalid email or password');
  });

  it('should disable submit button when form is empty', () => {
    loginPage.submitButton.should('be.disabled');
  });
});
```

### 网络拦截模式

```typescript
describe('Product listing', () => {
  it('should display products from API', () => {
    cy.intercept('GET', '/api/products', {
      fixture: 'products.json',
    }).as('getProducts');

    cy.visit('/products');
    cy.wait('@getProducts');

    cy.get('[data-testid="product-card"]').should('have.length', 3);
  });

  it('should show error state on API failure', () => {
    cy.intercept('GET', '/api/products', {
      statusCode: 500,
      body: { error: 'Internal Server Error' },
    }).as('getProductsFail');

    cy.visit('/products');
    cy.wait('@getProductsFail');

    cy.contains('Something went wrong').should('be.visible');
    cy.get('[data-testid="retry-button"]').should('be.visible');
  });

  it('should show loading state', () => {
    cy.intercept('GET', '/api/products', (req) => {
      req.on('response', (res) => {
        res.setDelay(2000);
      });
    }).as('getProductsSlow');

    cy.visit('/products');
    cy.get('[data-testid="loading-spinner"]').should('be.visible');
    cy.wait('@getProductsSlow');
    cy.get('[data-testid="loading-spinner"]').should('not.exist');
  });

  it('should send correct query parameters', () => {
    cy.intercept('GET', '/api/products*').as('getProducts');

    cy.visit('/products');
    cy.get('[data-testid="search-input"]').type('laptop');
    cy.get('[data-testid="search-button"]').click();

    cy.wait('@getProducts').then((interception) => {
      expect(interception.request.url).to.include('q=laptop');
    });
  });
});
```

### 使用 Fixtures

```json
// cypress/fixtures/users.json
{
  "validUser": {
    "email": "user@example.com",
    "password": "SecurePass123!",
    "name": "Test User"
  },
  "adminUser": {
    "email": "admin@example.com",
    "password": "AdminPass123!",
    "name": "Admin User"
  }
}
```

```typescript
describe('User management', () => {
  beforeEach(() => {
    cy.fixture('users.json').as('users');
  });

  it('should login with fixture data', function () {
    const { email, password } = this.users.validUser;
    cy.login(email, password);
    cy.url().should('include', '/dashboard');
  });
});
```

### 表单测试

```typescript
describe('Registration form', () => {
  beforeEach(() => {
    cy.visit('/register');
  });

  it('should validate required fields', () => {
    cy.get('button[type="submit"]').click();
    cy.contains('Name is required').should('be.visible');
    cy.contains('Email is required').should('be.visible');
    cy.contains('Password is required').should('be.visible');
  });

  it('should validate email format', () => {
    cy.get('#email').type('not-an-email');
    cy.get('#email').blur();
    cy.contains('Please enter a valid email').should('be.visible');
  });

  it('should validate password strength', () => {
    cy.get('#password').type('123');
    cy.get('#password').blur();
    cy.contains('Password must be at least 8 characters').should('be.visible');
  });

  it('should complete registration successfully', () => {
    cy.intercept('POST', '/api/auth/register', {
      statusCode: 201,
      body: { id: '123', email: 'new@example.com' },
    }).as('register');

    cy.get('#name').type('New User');
    cy.get('#email').type('new@example.com');
    cy.get('#password').type('SecurePass123!');
    cy.get('#confirmPassword').type('SecurePass123!');
    cy.get('button[type="submit"]').click();

    cy.wait('@register');
    cy.url().should('include', '/login');
    cy.contains('Registration successful').should('be.visible');
  });
});
```

### 文件上传

```typescript
it('should upload a file', () => {
  cy.get('[data-testid="file-input"]').selectFile('cypress/fixtures/sample.pdf');
  cy.contains('sample.pdf').should('be.visible');
  cy.get('[data-testid="upload-button"]').click();
  cy.contains('Upload successful').should('be.visible');
});

it('should drag and drop a file', () => {
  cy.get('[data-testid="file-input"]').selectFile('cypress/fixtures/image.png', {
    action: 'drag-drop',
  });
});
```

### 多标签和窗口处理

```typescript
it('should handle links opening in new tab', () => {
  // 移除 target="_blank" 以保持在同一标签中导航
  cy.get('a[data-testid="external-link"]')
    .invoke('removeAttr', 'target')
    .click();

  cy.url().should('include', '/external-page');
});

it('should verify external link href', () => {
  cy.get('a[data-testid="external-link"]')
    .should('have.attr', 'href')
    .and('include', 'https://external-site.com');
});
```

## 组件测试

```typescript
// src/components/Button.cy.tsx
import { Button } from './Button';

describe('Button component', () => {
  it('should render with correct text', () => {
    cy.mount(<Button>Click me</Button>);
    cy.contains('Click me').should('be.visible');
  });

  it('should handle click events', () => {
    const onClick = cy.stub().as('onClick');
    cy.mount(<Button onClick={onClick}>Click me</Button>);
    cy.contains('Click me').click();
    cy.get('@onClick').should('have.been.calledOnce');
  });

  it('should be disabled when disabled prop is true', () => {
    cy.mount(<Button disabled>Click me</Button>);
    cy.get('button').should('be.disabled');
  });

  it('should apply variant styles', () => {
    cy.mount(<Button variant="primary">Primary</Button>);
    cy.get('button').should('have.class', 'btn-primary');
  });
});
```

## 最佳实践

1. **使用 `cy.intercept()` 而不是 `cy.server()`/`cy.route()`** -- 新的 API 更强大。
2. **认证优先使用 `cy.session()`** -- 它在测试之间缓存会话状态。
3. **使用 `data-testid` 属性** -- 比类选择器更能承受重构。
4. **永远不要使用 `cy.wait(ms)`** -- 使用 `cy.wait('@alias')` 等待网络请求或 DOM 断言。
5. **保持测试独立** -- 不要依赖测试执行顺序。
6. **使用 `beforeEach` 而不是 `before`** -- 每个测试都应设置自己的状态。
7. **Cypress 命令不返回任何内容** -- 命令是可链式的,不是基于 promise 的。
8. **避免条件测试** -- Cypress 测试应该是确定性的。
9. **使用 API 快捷方式设置状态** -- 使用 `cy.request()` 而不是 UI 点击来设置数据。
10. **限制 `.then()` 的使用** -- 大多数操作应该是可链式断言的。

## 应避免的反模式

1. **使用 `async/await`** -- Cypress 命令不是 Promise。它们对命令进行排队。
2. **将 Cypress 命令赋值给变量** -- `const el = cy.get('.foo')` 不能按预期工作。
3. **使用任意等待** -- `cy.wait(5000)` 是 flaky 的保证来源。
4. **访问外部站点** -- Cypress 不能很好地支持跨域导航。
5. **直接测试第三方小部件** -- 将它们 stub 或使用它们的测试钩子。
6. **对简单断言使用 `.then()`** -- 使用 `.should()`,它会重试。
7. **深度嵌套的回调** -- 扁平化测试逻辑;避免回调地狱。
8. **过度使用 `cy.wrap()`** -- 仅在真正需要包装非 Cypress 值时使用。
9. **测试实现细节** -- 专注于用户看到和做的。
10. **在单个文件中运行太多 specs** -- 按功能区域分割大文件。

## 调试技巧

- 使用 `cy.log()` 打印消息到 Cypress 命令日志。
- 使用 `cy.debug()` 暂停并在 DevTools 中检查。
- 使用 `cy.pause()` 一次一步地执行命令。
- 使用 `.then(console.log)` 在测试执行期间检查值。
- 在交互模式下打开 Cypress:`npx cypress open`。
- 检查 Cypress 命令日志侧边栏以进行时间旅行调试。
- 使用 `cy.screenshot()` 捕获当前状态用于调试。

## CI 集成

```yaml
# .github/workflows/cypress.yml
name: Cypress Tests
on: [push, pull_request]

jobs:
  cypress:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        browser: [chrome, firefox, edge]
    steps:
      - uses: actions/checkout@v4
      - uses: cypress-io/github-action@v6
        with:
          browser: ${{ matrix.browser }}
          start: npm run dev
          wait-on: 'http://localhost:3000'
          record: true
        env:
          CYPRESS_RECORD_KEY: ${{ secrets.CYPRESS_RECORD_KEY }}
```