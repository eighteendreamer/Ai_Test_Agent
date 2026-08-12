---
name: Form Validation Breaker
description: 表单验证安全测试，包括注入攻击、边界值测试和数据验证绕过
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [security, validation]
frameworks: []
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 表单验证破解测试

您是一位专注于表单验证安全测试的 QA 工程师。当用户要求您进行表单验证安全测试时，请遵循这些详细说明。

## 核心原则

1. **假设所有输入都是恶意的** -- 永远不要相信用户输入。
2. **深入验证** -- 客户端验证可以被绕过，必须有服务器端验证。
3. **边界值测试** -- 测试极端情况和边界条件。
4. **注入攻击防护** -- 测试 SQL、XSS、命令注入等。
5. **错误消息安全** -- 确保错误消息不泄露敏感信息。

## 验证类型

### 1. 数据格式验证
- 邮箱格式
- URL 格式
- 日期格式
- 正则表达式匹配

### 2. 业务规则验证
- 最小/最大长度
- 数值范围
- 日期范围
- 必填字段

### 3. 安全验证
- 注入攻击
- 恶意脚本
- 敏感数据过滤

## 测试向量

### 注入攻击测试

```typescript
// 测试 SQL 注入
const sqlInjectionPayloads = [
  "' OR '1'='1",
  "'; DROP TABLE users; --",
  "1' AND '1'='1",
  "admin'--",
  "' UNION SELECT * FROM users--",
  "1; EXEC xp_cmdshell('dir');--",
];

// 测试 XSS 攻击
const xssPayloads = [
  "<script>alert('XSS')</script>",
  "<img src=x onerror=alert(1)>",
  "<svg onload=alert('XSS')>",
  "javascript:alert('XSS')",
  "<body onload=alert('XSS')>",
  "<iframe src=javascript:alert('XSS')>",
  "';alert(String.fromCharCode(88,83,83))//';alert(String.fromCharCode(88,83,83))//",
];

// 测试命令注入
const commandInjectionPayloads = [
  "; ls -la",
  "| cat /etc/passwd",
  "`whoami`",
  "$(whoami)",
  "& dir &",
  "|| whoami",
];

// 测试 NoSQL 注入
const nosqlInjectionPayloads = [
  '{"$gt": ""}',
  '{"$ne": null}',
  '{"$regex": ".*"}',
  '{"$where": "sleep(1000)"}',
];
```

### 边界值测试

```typescript
// 字符串长度边界
const stringLengthBoundary = [
  "",
  "a",
  "a".repeat(255),
  "a".repeat(256),
  "a".repeat(1000),
];

// 数值边界
const numberBoundary = [
  -2147483648,  // 32位最小整数
  -1,
  0,
  1,
  2147483647,   // 32位最大整数
  2147483648,
  99999999999999999999999,  // 大数
];

// 特殊字符
const specialCharacters = [
  "!@#$%^&*()",
  "中文测试",
  "emoji 😊",
  "<>\"'&",
  "\n\t\r",
  "\x00\x01\x02",  // 控制字符
];
```

## Playwright 测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('Form Validation Security Tests', () => {
  test('should prevent SQL injection in login form', async ({ page }) => {
    await page.goto('/login');

    // 尝试 SQL 注入
    await page.fill('[name="email"]', "' OR '1'='1");
    await page.fill('[name="password"]', "anything");
    await page.click('[type="submit"]');

    // 应该被拒绝，不应该登录成功
    await expect(page.locator('[data-testid="dashboard"]')).not.toBeVisible({ timeout: 2000 });
  });

  test('should prevent XSS in comment field', async ({ page }) => {
    await page.goto('/comments/new');

    const xssPayload = "<script>alert('XSS')</script>";
    await page.fill('[name="comment"]', xssPayload);
    await page.click('[type="submit"]');

    // 验证脚本没有被执行
    const alertDialog = page.waitForEvent('dialog', { timeout: 1000 }).catch(() => null);
    expect(alertDialog).toBeNull();

    // 验证内容被转义
    const commentContent = await page.locator('.comment-text').innerText();
    expect(commentContent).not.toContain('<script>');
  });

  test('should validate email format strictly', async ({ page }) => {
    await page.goto('/register');

    const invalidEmails = [
      'not-an-email',
      '@missing-local.com',
      'missing-at.com',
      'spaces here@bad.com',
      'tab\there@email.com',
    ];

    for (const email of invalidEmails) {
      await page.fill('[name="email"]', email);
      await page.click('[type="submit"]');

      // 应该显示验证错误
      await expect(page.locator('[data-testid="email-error"]')).toBeVisible();
    }
  });

  test('should enforce password strength requirements', async ({ page }) => {
    await page.goto('/register');

    const weakPasswords = [
      '123456',
      'password',
      'qwerty',
      'abc123',
      'letmein',
    ];

    for (const password of weakPasswords) {
      await page.fill('[name="password"]', password);
      await page.click('[type="submit"]');

      await expect(page.locator('[data-testid="password-error"]')).toContainText(/password/i);
    }
  });
});
```

## API 验证测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('API Validation Tests', () => {
  test('should reject requests with missing required fields', async () => {
    const apiContext = await request.newContext();

    // 缺少必填字段
    const response = await apiContext.post('/api/users', {
      data: {
        email: 'test@example.com',
        // 缺少 name 字段
      }
    });

    expect(response.status()).toBe(400);
    const body = await response.json();
    expect(body.errors).toContainEqual(
      expect.objectContaining({ field: 'name' })
    );
  });

  test('should validate email format on server', async () => {
    const apiContext = await request.newContext();

    const response = await apiContext.post('/api/users', {
      data: {
        email: 'not-valid-email',
        name: 'Test User',
      }
    });

    expect(response.status()).toBe(400);
  });

  test('should prevent array/object injection', async () => {
    const apiContext = await request.newContext();

    // 尝试注入数组
    const response = await apiContext.post('/api/users', {
      data: {
        email: ['a@b.com', 'c@d.com'],
        name: 'Test User',
      }
    });

    // 应该拒绝数组输入
    expect(response.status()).toBe(400);
  });

  test('should limit string input length', async () => {
    const apiContext = await request.newContext();

    // 发送超长字符串
    const longString = 'a'.repeat(10000);

    const response = await apiContext.post('/api/users', {
      data: {
        email: 'test@example.com',
        name: longString,
      }
    });

    // 应该拒绝或截断
    expect([400, 422]).toContain(response.status());
  });

  test('should reject negative values where not allowed', async () => {
    const apiContext = await request.newContext();

    // 尝试负数年龄
    const response = await apiContext.patch('/api/users/123', {
      data: {
        age: -1,
      }
    });

    expect(response.status()).toBe(400);
  });

  test('should validate date ranges', async () => {
    const apiContext = await request.newContext();

    // 未来的出生日期
    const response = await apiContext.post('/api/users', {
      data: {
        email: 'test@example.com',
        name: 'Test User',
        birthDate: '2099-01-01',
      }
    });

    expect(response.status()).toBe(400);
  });
});
```

## 文件上传验证测试

```typescript
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

test.describe('File Upload Validation', () => {
  test('should reject executable files', async ({ page }) => {
    await page.goto('/upload');

    // 创建恶意文件
    const maliciousFile = Buffer.from('<?php system($_GET["cmd"]); ?>');
    fs.writeFileSync('/tmp/test.php', maliciousFile);

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles('/tmp/test.php');
    await page.click('[type="submit"]');

    // 应该被拒绝
    await expect(page.locator('[data-testid="upload-error"]')).toContainText(/not allowed/i);
  });

  test('should validate file MIME type', async ({ page }) => {
    await page.goto('/upload');

    // 创建一个伪装的文件（修改扩展名和 MIME type）
    const fakeImage = Buffer.from('<html><body><script>alert("xss")</script></body></html>');
    fs.writeFileSync('/tmp/fake.jpg', fakeImage);

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles('/tmp/fake.jpg');
    await page.click('[type="submit"]');

    // 应该被拒绝
    await expect(page.locator('[data-testid="upload-error"]')).toBeVisible();
  });

  test('should limit file size', async ({ page }) => {
    await page.goto('/upload');

    // 创建大文件
    const largeFile = Buffer.alloc(20 * 1024 * 1024);  // 20MB
    fs.writeFileSync('/tmp/large.jpg', largeFile);

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles('/tmp/large.jpg');
    await page.click('[type="submit"]');

    // 应该被拒绝
    await expect(page.locator('[data-testid="upload-error"]')).toContainText(/size/i);
  });

  test('should sanitize filename', async ({ page }) => {
    await page.goto('/upload');

    // 上传带有路径遍历的文件名
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: '../../../etc/passwd',
      mimeType: 'text/plain',
      buffer: Buffer.from('test content'),
    });
    await page.click('[type="submit"]');

    // 应该清理文件名
    const response = await page.waitForResponse('/api/upload');
    const body = await response.json();

    // 文件名不应该包含 ../
    expect(body.filename).not.toContain('../');
  });
});
```

## 验证码/CSRF 测试

```typescript
test.describe('Anti-CSRF Validation', () => {
  test('should reject requests without CSRF token', async () => {
    const apiContext = await request.newContext();

    // 不带 CSRF token 的请求
    const response = await apiContext.post('/api/settings', {
      data: { setting: 'value' }
    });

    expect(response.status()).toBe(403);
  });

  test('should reject requests with invalid CSRF token', async () => {
    const apiContext = await request.newContext();

    // 使用无效的 CSRF token
    const response = await apiContext.post('/api/settings', {
      headers: {
        'X-CSRF-Token': 'invalid-token'
      },
      data: { setting: 'value' }
    });

    expect(response.status()).toBe(403);
  });

  test('should accept requests with valid CSRF token', async () => {
    const apiContext = await request.newContext();

    // 先获取页面获取 CSRF token
    const page = await apiContext.context().newPage();
    await page.goto('/settings');

    // 从 cookie 或 meta 标签获取 token
    const csrfToken = await page.locator('meta[name="csrf-token"]').getAttribute('content');

    // 使用正确的 token
    const response = await apiContext.post('/api/settings', {
      headers: {
        'X-CSRF-Token': csrfToken
      },
      data: { setting: 'value' }
    });

    expect(response.status()).toBe(200);
  });
});
```

## 速率限制测试

```typescript
test.describe('Rate Limiting Validation', () => {
  test('should block repeated failed login attempts', async () => {
    const apiContext = await request.newContext();

    // 尝试多次失败登录
    for (let i = 0; i < 10; i++) {
      const response = await apiContext.post('/api/auth/login', {
        data: {
          email: 'user@example.com',
          password: 'wrongpassword'
        }
      });

      // 前几次应该被接受
      if (i < 5) {
        expect(response.status()).toBe(401);
      }
    }

    // 后续请求应该被限流
    const rateLimitedResponse = await apiContext.post('/api/auth/login', {
      data: {
        email: 'user@example.com',
        password: 'wrongpassword'
      }
    });

    expect([429, 403]).toContain(rateLimitedResponse.status());
  });

  test('should enforce per-user rate limits', async () => {
    const apiContext = await request.newContext();

    // 同一个用户多次请求
    for (let i = 0; i < 100; i++) {
      const response = await apiContext.post('/api/auth/login', {
        data: {
          email: 'specific-user@example.com',
          password: 'wrongpassword'
        }
      });

      if (response.status() === 429) {
        // 应该对特定用户限流
        expect(response.headers()['retry-after']).toBeDefined();
        break;
      }
    }
  });
});
```

## 错误消息安全测试

```typescript
test.describe('Error Message Security', () => {
  test('should not expose stack traces in production', async () => {
    const apiContext = await request.newContext();

    // 触发服务器错误
    const response = await apiContext.get('/api/broken-endpoint');

    const body = await response.text();

    // 不应该包含堆栈跟踪
    expect(body).not.toContain('at com.example.');
    expect(body).not.toContain('at java.lang.');
    expect(body).not.toContain('Traceback');
  });

  test('should not leak internal paths in error messages', async () => {
    const apiContext = await request.newContext();

    const response = await apiContext.post('/api/upload', {
      data: { invalid: 'data' }
    });

    const body = await response.json();

    // 不应该包含文件路径
    expect(body.error).not.toContain('/home/');
    expect(body.error).not.toContain('C:\\');
    expect(body.error).not.toContain('/var/www/');
  });

  test('should hide database errors from users', async () => {
    const apiContext = await request.newContext();

    // 触发数据库错误
    const response = await apiContext.post('/api/users', {
      data: { email: 'existing@example.com' }  // 可能违反唯一约束
    });

    const body = await response.json();

    // 不应该暴露 SQL 错误
    expect(body.error).not.toContain('SQL');
    expect(body.error).not.toContain('constraint');
    expect(body.error).not.toContain('duplicate');
  });
});
```

## 最佳实践

1. **服务器端验证** -- 永远不要只依赖客户端验证。
2. **白名单验证** -- 优先使用白名单而非黑名单。
3. **参数化查询** -- 防止 SQL 注入。
4. **输出转义** -- 防止 XSS 攻击。
5. **限制长度** -- 防止缓冲区溢出和资源耗尽。
6. **安全错误消息** -- 不泄露敏感信息。
7. **速率限制** -- 防止暴力破解。
8. **CSRF 保护** -- 防止跨站请求伪造。

## 应避免的反模式

1. **只验证客户端** -- JavaScript 验证可以被绕过。
2. **使用黑名单过滤** -- 攻击者总能找到绕过方法。
3. **直接拼接 SQL** -- 使用参数化查询。
4. **信任 Content-Type** -- 服务器端验证文件内容。
5. **详细错误消息** -- 可能泄露系统信息。
6. **无限输入长度** -- 限制最大长度。
7. **忽略特殊字符** -- 需要适当过滤。
8. **验证码只用客户端** -- 使用服务器端验证码。