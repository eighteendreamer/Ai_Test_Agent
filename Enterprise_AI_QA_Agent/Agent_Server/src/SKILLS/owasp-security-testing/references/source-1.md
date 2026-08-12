---
name: OWASP Security Testing
description: OWASP Top 10 安全测试，包括注入、身份验证漏洞、XSS、CSRF 等
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [security]
frameworks: [playwright]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# OWASP 安全测试

您是一位专注于 Web 应用安全测试的 QA 工程师。当用户要求您进行 OWASP Top 10 安全测试时，请遵循这些详细说明。

## OWASP Top 10 (2021)

1. **A01: 访问控制失效** (Broken Access Control)
2. **A02: 加密失败** (Cryptographic Failures)
3. **A03: 注入** (Injection)
4. **A04: 不安全设计** (Insecure Design)
5. **A05: 安全配置错误** (Security Misconfiguration)
6. **A06: 易受攻击的过时组件** (Vulnerable and Outdated Components)
7. **A07: 身份验证和授权失败** (Identification and Authentication Failures)
8. **A08: 数据完整性失败** (Software and Data Integrity Failures)
9. **A09: 安全日志和监控失败** (Security Logging and Monitoring Failures)
10. **A10: 服务器端请求伪造** (Server-Side Request Forgery)

## 项目结构

```
security-tests/
├── tests/
│   ├── owasp/
│   │   ├── a01-access-control.spec.ts
│   │   ├── a03-injection.spec.ts
│   │   ├── a07-authentication.spec.ts
│   │   └── a10-ssrf.spec.ts
│   ├── payloads/
│   │   ├── injection-payloads.ts
│   │   └── xss-payloads.ts
│   └── utils/
│       ├── security-helpers.ts
│       └── report-generator.ts
├── playwright.config.ts
└── package.json
```

## A01: 访问控制失效测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('A01: Broken Access Control', () => {

  test('should not allow accessing admin endpoints without admin role', async ({ page }) => {
    // 以普通用户登录
    await page.goto('/login');
    await page.fill('[name="email"]', 'user@example.com');
    await page.fill('[name="password"]', 'UserPass123!');
    await page.click('[type="submit"]');

    // 尝试访问管理员页面
    const response = await page.goto('/admin/users');

    // 应该被拒绝
    expect(response?.status()).toBe(403);
  });

  test('should not allow accessing other user data via direct reference', async () => {
    const apiContext = await request.newContext();

    // 用户 A 登录
    const userAResponse = await apiContext.post('/api/auth/login', {
      data: { email: 'userA@example.com', password: 'Password123!' }
    });
    const { token: tokenA } = await userAResponse.json();

    // 用户 B 登录
    const userBResponse = await apiContext.post('/api/auth/login', {
      data: { email: 'userB@example.com', password: 'Password123!' }
    });
    const { userId: userBId } = await userBResponse.json();

    // 使用用户 A 的 token 尝试访问用户 B 的数据
    const response = await apiContext.get(`/api/users/${userBId}/profile`, {
      headers: { Authorization: `Bearer ${tokenA}` }
    });

    // 应该被拒绝（IDOR 防护）
    expect([403, 404]).toContain(response.status());
  });

  test('should enforce horizontal access control', async () => {
    const apiContext = await request.newContext();

    // 登录用户 1
    const login1 = await apiContext.post('/api/auth/login', {
      data: { email: 'user1@example.com', password: 'Password123!' }
    });
    const { token: token1 } = await login1.json();

    // 登录用户 2
    const login2 = await apiContext.post('/api/auth/login', {
      data: { email: 'user2@example.com', password: 'Password123!' }
    });
    const { token: token2 } = await login2.json();

    // 用户 1 创建私有数据
    const createResponse = await apiContext.post('/api/notes', {
      headers: { Authorization: `Bearer ${token1}` },
      data: { title: 'User 1 Private Note', content: 'Secret data', public: false }
    });
    const { id: noteId } = await createResponse.json();

    // 用户 2 尝试访问用户 1 的私有数据
    const accessResponse = await apiContext.get(`/api/notes/${noteId}`, {
      headers: { Authorization: `Bearer ${token2}` }
    });

    // 应该被拒绝
    expect(accessResponse.status()).toBe(403);
  });

  test('should not expose directory structure', async ({ page }) => {
    const sensitivePaths = [
      '/.git/config',
      '/.env',
      '/wp-admin/',
      '/admin/config.php',
      '/backup.sql',
      '/debug=true',
    ];

    for (const path of sensitivePaths) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(404);
    }
  });
});
```

## A03: 注入测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('A03: Injection', () => {

  // SQL 注入测试
  test('should prevent SQL injection in login', async ({ page }) => {
    await page.goto('/login');

    const sqlInjectionPayloads = [
      "' OR '1'='1",
      "admin'--",
      "1' OR '1'='1",
      "'; DROP TABLE users;--",
    ];

    for (const payload of sqlInjectionPayloads) {
      await page.fill('[name="email"]', payload);
      await page.fill('[name="password"]', 'anything');
      await page.click('[type="submit"]');

      // 验证没有登录成功
      await expect(page.locator('[data-testid="dashboard"]')).not.toBeVisible({ timeout: 2000 });
    }
  });

  test('should prevent SQL injection in search', async () => {
    const apiContext = await request.newContext();

    const response = await apiContext.get('/api/products?search=test\' OR \'1\'=\'1');

    // 应该正确处理转义或返回空结果
    if (response.status() === 200) {
      const body = await response.json();
      // 不应该返回所有产品
      expect(body.products).toBeDefined();
    }
  });

  // NoSQL 注入测试
  test('should prevent NoSQL injection', async () => {
    const apiContext = await request.newContext();

    const noSqlPayloads = [
      '{"$gt": ""}',
      '{"$ne": null}',
      '{"$regex": ".*"}',
    ];

    for (const payload of noSqlPayloads) {
      const response = await apiContext.post('/api/users/login', {
        headers: { 'Content-Type': 'application/json' },
        data: JSON.parse(payload)
      });

      // 应该被拒绝或正确处理
      expect([400, 401, 403]).toContain(response.status());
    }
  });

  // XSS 测试
  test('should prevent stored XSS', async ({ page }) => {
    await page.goto('/comments/new');

    const xssPayloads = [
      '<script>alert("XSS")</script>',
      '<img src=x onerror=alert(1)>',
      '<svg onload=alert("XSS")>',
      'javascript:alert(1)',
    ];

    for (const payload of xssPayloads) {
      await page.fill('[name="comment"]', payload);
      await page.click('[type="submit"]');

      // 验证脚本没有被执行
      page.on('dialog', dialog => {
        expect(dialog.message()).not.toContain('XSS');
      });

      // 验证内容被转义存储
      await page.goto('/comments');
      const commentContent = await page.locator('.comment-text').first().innerText();
      expect(commentContent).not.toContain('<script>');
    }
  });

  test('should prevent reflected XSS', async ({ page }) => {
    const xssPayload = '<script>alert("XSS")</script>';
    const response = await page.goto(`/search?q=${encodeURIComponent(xssPayload)}`);

    // 验证脚本没有被执行
    page.on('dialog', dialog => {
      expect(dialog.message()).not.toContain('XSS');
    });
  });

  // 命令注入测试
  test('should prevent command injection', async () => {
    const apiContext = await request.newContext();

    const commandPayloads = [
      '; ls -la',
      '| cat /etc/passwd',
      '`whoami`',
      '$(whoami)',
    ];

    for (const payload of commandPayloads) {
      const response = await apiContext.get(`/api/ping?host=${payload}`);

      // 应该拒绝或正确处理
      expect(response.status()).not.toBe(200);
    }
  });

  // LDAP 注入测试
  test('should prevent LDAP injection', async () => {
    const apiContext = await request.newContext();

    const ldapPayloads = [
      '*)(uid=*))(|(uid=*)',
      'admin)(&(password=*)',
      ')(cn=*',
    ];

    for (const payload of ldapPayloads) {
      const response = await apiContext.get(`/api/users/search?filter=${payload}`);
      // 应该正确处理
      expect(response.status()).toBeDefined();
    }
  });
});
```

## A07: 身份认证和授权失败测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('A07: Authentication Failures', () => {

  test('should enforce strong password policy', async ({ page }) => {
    await page.goto('/register');

    const weakPasswords = [
      '123456',
      'password',
      'qwerty',
      'abc123',
      'letmein',
      'admin',
    ];

    for (const password of weakPasswords) {
      await page.fill('[name="email"]', `test${Date.now()}@example.com`);
      await page.fill('[name="password"]', password);
      await page.click('[type="submit"]');

      await expect(page.locator('[data-testid="password-error"]')).toBeVisible();
    }
  });

  test('should implement account lockout', async () => {
    const apiContext = await request.newContext();

    // 尝试多次失败登录
    const maxAttempts = 5;
    for (let i = 0; i < maxAttempts + 3; i++) {
      const response = await apiContext.post('/api/auth/login', {
        data: { email: 'user@example.com', password: 'wrongpassword' }
      });

      if (i < maxAttempts) {
        expect([401, 400]).toContain(response.status());
      } else {
        // 账户应该被锁定
        expect([429, 403, 423]).toContain(response.status());
      }
    }
  });

  test('should not reveal user existence', async () => {
    const apiContext = await request.newContext();

    // 尝试注册已存在的邮箱
    const response = await apiContext.post('/api/auth/register', {
      data: {
        email: 'existing@example.com',
        password: 'Password123!'
      }
    });

    // 错误消息不应该直接说明邮箱已存在
    const body = await response.json();
    expect(body.message).not.toMatch(/email.*exists/i);
    expect(body.message).toMatch(/already.*taken|already.*registered/i);
  });

  test('should require re-authentication for sensitive actions', async ({ page }) => {
    // 登录
    await page.goto('/login');
    await page.fill('[name="email"]', 'user@example.com');
    await page.fill('[name="password"]', 'Password123!');
    await page.click('[type="submit"]');

    // 访问敏感操作页面（无需重新验证）
    await page.goto('/settings/password');

    // 尝试更改密码
    await page.fill('[name="currentPassword"]', 'OldPassword123!');
    await page.fill('[name="newPassword"]', 'NewPassword123!');
    await page.fill('[name="confirmPassword"]', 'NewPassword123!');
    await page.click('[type="submit"]');

    // 应该要求重新验证或验证当前密码
    // 取决于安全策略
  });

  test('should implement secure session management', async ({ page }) => {
    // 登录
    await page.goto('/login');
    await page.fill('[name="email"]', 'user@example.com');
    await page.fill('[name="password"]', 'Password123!');
    await page.click('[type="submit"]');

    // 获取 session cookie
    const cookies = await page.context().cookies();
    const sessionCookie = cookies.find(c => c.name === 'session');

    if (sessionCookie) {
      // 验证 cookie 安全属性
      expect(sessionCookie.httpOnly).toBe(true);
      expect(sessionCookie.secure).toBe(true);
      expect(sessionCookie.sameSite).toBe('strict' || 'lax');
    }
  });

  test('should timeout inactive sessions', async ({ page }) => {
    // 登录
    await page.goto('/login');
    await page.fill('[name="email"]', 'user@example.com');
    await page.fill('[name="password"]', 'Password123!');
    await page.click('[type="submit"]');

    // 等待会话超时（假设超时为 30 分钟）
    // 在测试中验证会话是否正确过期
    await page.waitForTimeout(1000);  // 短暂等待

    // 验证 session 仍然有效（30分钟内）
    const response = await page.request.get('/api/me');
    expect(response.status()).toBe(200);
  });
});
```

## A10: 服务器端请求伪造 (SSRF) 测试

```typescript
test.describe('A10: Server-Side Request Forgery', () => {

  test('should prevent SSRF attacks on URL-fetching endpoints', async () => {
    const apiContext = await request.newContext();

    const ssrfPayloads = [
      'http://localhost:22',
      'http://127.0.0.1:6379',
      'http://169.254.169.254/latest/meta-data/',  // AWS metadata
      'http://internal.corp.local/admin',
      'file:///etc/passwd',
    ];

    for (const payload of ssrfPayloads) {
      const response = await apiContext.get(`/api/fetch?url=${encodeURIComponent(payload)}`);

      // 应该被拒绝或正确处理
      expect(response.status()).toBe(400);
    }
  });

  test('should validate URLs before fetching', async () => {
    const apiContext = await request.newContext();

    const invalidUrls = [
      'not-a-url',
      'ftp://example.com',
      'javascript:alert(1)',
      '../etc/passwd',
    ];

    for (const url of invalidUrls) {
      const response = await apiContext.get(`/api/fetch?url=${encodeURIComponent(url)}`);
      expect(response.status()).toBe(400);
    }
  });

  test('should block access to internal networks', async () => {
    const apiContext = await request.newContext();

    const internalTargets = [
      'http://10.0.0.1/admin',
      'http://192.168.1.1/router-login',
      'http://172.16.0.1:8080/admin',
    ];

    for (const target of internalTargets) {
      const response = await apiContext.get(`/api/fetch?url=${encodeURIComponent(target)}`);
      expect(response.status()).toBe(400);
    }
  });
});
```

## A05: 安全配置错误测试

```typescript
test.describe('A05: Security Misconfiguration', () => {

  test('should not expose stack traces in production', async () => {
    const apiContext = await request.newContext();

    // 触发错误
    const response = await apiContext.get('/api/broken-endpoint');

    const body = await response.text();

    // 不应该包含堆栈跟踪
    expect(body).not.toContain('at com.example.');
    expect(body).not.toContain('at java.lang.');
    expect(body).not.toContain('Traceback');
    expect(body).not.toContain('Error:');
  });

  test('should have security headers', async ({ page }) => {
    await page.goto('/');

    const securityHeaders = [
      'Content-Security-Policy',
      'X-Content-Type-Options',
      'X-Frame-Options',
      'X-XSS-Protection',
    ];

    const response = await page.request.get('/');

    for (const header of securityHeaders) {
      // 这些头部应该存在（某些可能只在特定页面设置）
      const headerValue = response.headers()[header.toLowerCase()];
      if (header === 'X-Content-Type-Options') {
        expect(headerValue).toBe('nosniff');
      }
    }
  });

  test('should disable directory listing', async ({ page }) => {
    const response = await page.goto('/images/');
    expect(response?.status()).toBe(403);
  });

  test('should not have default credentials', async ({ page }) => {
    // 检查常见默认凭证
    const defaultCredentials = [
      { user: 'admin', pass: 'admin' },
      { user: 'admin', pass: 'password' },
      { user: 'root', pass: 'toor' },
    ];

    for (const creds of defaultCredentials) {
      await page.goto('/login');
      await page.fill('[name="email"]', creds.user);
      await page.fill('[name="password"]', creds.pass);
      await page.click('[type="submit"]');

      // 应该拒绝默认凭证
      await expect(page.locator('[data-testid="error-message"]')).toBeVisible();
    }
  });

  test('should enforce HTTPS', async ({ page }) => {
    // 尝试 HTTP 访问
    const httpResponse = await page.request.get('http://example.com/');
    const httpsRedirect = httpResponse.url().startsWith('https://');

    expect(httpsRedirect).toBe(true);
  });
});
```

## A06: 易受攻击的过时组件测试

```typescript
test.describe('A06: Vulnerable Components', () => {

  test('should not use outdated JavaScript dependencies with known vulnerabilities', async () => {
    // 使用 npm audit 检查依赖
    const { execSync } = require('child_process');

    try {
      const output = execSync('npm audit --json', { encoding: 'utf-8' });
      const auditResult = JSON.parse(output);

      const vulnerabilities = auditResult.vulnerabilities || {};
      const criticalVulns = Object.values(vulnerabilities)
        .flat()
        .filter((v: any) => v.severity === 'critical');

      expect(criticalVulns).toHaveLength(0);
    } catch (error) {
      // npm audit 返回非零退出码如果有漏洞
      // 这是预期的，不应该导致测试失败
    }
  });

  test('should keep server-side libraries updated', async () => {
    const apiContext = await request.newContext();

    // 检查服务器版本（如果有暴露）
    const response = await apiContext.get('/api/version');

    if (response.ok()) {
      const body = await response.json();

      // 验证不使用已弃用的版本
      if (body.nodeVersion) {
        const majorVersion = parseInt(body.nodeVersion.split('.')[0]);
        expect(majorVersion).toBeGreaterThanOrEqual(18);
      }
    }
  });
});
```

## 安全测试工具集成

```typescript
// tests/utils/security-helpers.ts
export const injectionPayloads = {
  sql: [
    "' OR '1'='1",
    "admin'--",
    "'; DROP TABLE users;--",
    "1' AND '1'='1",
    "' UNION SELECT * FROM users--",
  ],
  xss: [
    "<script>alert('XSS')</script>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert('XSS')>",
    "javascript:alert('XSS')",
    "<body onload=alert('XSS')>",
  ],
  nosql: [
    '{"$gt": ""}',
    '{"$ne": null}',
    '{"$regex": ".*"}',
  ],
  command: [
    "; ls -la",
    "| cat /etc/passwd",
    "`whoami`",
    "$(whoami)",
  ],
};

export async function checkSecurityHeaders(page: Page) {
  const requiredHeaders = {
    'x-content-type-options': 'nosniff',
    'x-frame-options': /(DENY|SAMEORIGIN)/,
    'strict-transport-security': /.+/,
  };

  const response = await page.request.get(page.url());
  const headers = response.headers();

  for (const [header, expected] of Object.entries(requiredHeaders)) {
    const actual = headers[header];
    if (typeof expected === 'string') {
      expect(actual).toBe(expected);
    } else {
      expect(actual).toMatch(expected);
    }
  }
}
```

## CI/CD 集成

```yaml
name: OWASP Security Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  security-test:
    runs-on: ubuntu-latest
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

      - name: Run OWASP tests
        run: npx playwright test tests/owasp/

      - name: Run dependency audit
        run: npm audit --audit-level=high

      - name: Upload security report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: security-test-results
          path: |
            test-results/
            nlp-scan-results/
```

## 最佳实践

1. **全面覆盖 OWASP Top 10** -- 测试所有主要安全类别。
2. **使用真实攻击载荷** -- 使用已知的恶意输入模式。
3. **测试服务器端和客户端** -- 两端都需要验证。
4. **检查安全头部** -- 确保安全头部正确配置。
5. **测试身份认证和授权** -- 验证访问控制有效。
6. **定期更新测试** -- 跟上新的攻击手法。
7. **集成到 CI/CD** -- 每次 PR 都运行安全测试。
8. **记录和修复** -- 记录发现的问题并跟踪修复。

## 应避免的反模式

1. **只测试 Happy Path** -- 必须测试边界和错误情况。
2. **假设用户是善意的** -- 所有输入都可能是恶意的。
3. **忽略第三方组件** -- 第三方库也可能有漏洞。
4. **不测试配置** -- 安全配置错误是常见漏洞。
5. **忽略日志和监控** -- 安全事件需要可检测。
6. **只依赖自动化** -- 手动渗透测试也重要。
7. **不更新安全测试** -- 新的攻击手法需要新测试。
8. **忽略移动端** -- 移动端也有相同的安全风险。