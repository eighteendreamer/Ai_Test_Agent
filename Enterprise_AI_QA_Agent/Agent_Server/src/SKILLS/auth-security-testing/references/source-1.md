---
name: Auth Bypass Tester
description: Web 应用身份验证和授权安全测试，包括 JWT、Session、OAuth 等漏洞检测
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [security]
frameworks: []
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 身份验证绕过测试器

您是一位专注于 Web 应用安全测试的 QA 工程师。当用户要求您进行身份验证和授权安全测试时，请遵循这些详细说明。

## 核心原则

1. **深度防御** -- 多层安全验证，不要依赖单一安全机制。
2. **最小权限原则** -- 只授予必要的访问权限。
3. **纵深检测** -- 全面测试身份验证和授权机制。
4. **持续监控** -- 在 CI/CD 中集成安全测试。
5. **安全报告** -- 生成可操作的安全漏洞报告。

## 安全测试类型

### 身份验证测试

- JWT 令牌操纵
- Session 固定和劫持
- 暴力破解保护
- 密码策略验证
- 多因素认证测试

### 授权测试

- 垂直权限提升
- 水平权限提升
- IDOR（不安全的直接对象引用）
- 缺少访问控制
- 功能级别访问控制

## 常见漏洞

### JWT 漏洞

```typescript
// 1. 签名验证缺失
const jwt = require('jsonwebtoken');

// 使用空签名验证
const decoded = jwt.verify(token, '', { algorithms: ['none'] });
// 攻击：使用算法的 'none'

// 2. 密钥混淆攻击
// RS256 -> HS256
const decoded = jwt.verify(token, process.env.PUBLIC_KEY, {
  algorithms: ['HS256']  // 错误：允许 HS256
});

// 3. 令牌过期未验证
const decoded = jwt.decode(token);
if (decoded.exp < Date.now() / 1000) {
  // 未检查过期时间
}
```

### IDOR 漏洞

```typescript
// 不安全的直接对象引用
// GET /api/users/123/profile  返回用户 123 的资料
// 攻击者可以尝试其他用户 ID

// 测试用例
const vulnerableEndpoints = [
  '/api/users/{userId}/profile',
  '/api/orders/{orderId}',
  '/api/documents/{documentId}',
  '/api/transactions/{transactionId}'
];

// 应该验证当前用户是否有权访问该资源
```

## 安全测试工具

### 使用 Playwright 进行安全测试

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('Authentication Security Tests', () => {
  test('should detect JWT signature bypass', async ({ }) => {
    const apiContext = await request.newContext();
    
    // 1. 登录获取令牌
    const loginResponse = await apiContext.post('/api/auth/login', {
      data: { email: 'user@example.com', password: 'password123' }
    });
    const { token } = await loginResponse.json();
    
    // 2. 修改令牌 payload（不改变签名）
    const parts = token.split('.');
    const payload = JSON.parse(Buffer.from(parts[1], 'base64').toString());
    
    // 尝试提升权限
    payload.role = 'admin';
    payload.exp = Math.floor(Date.now() / 1000) + 3600;
    
    const modifiedToken = `${parts[0]}.${Buffer.from(JSON.stringify(payload)).toString('base64')}.${parts[2]}`;
    
    // 3. 使用修改后的令牌访问管理员端点
    const adminResponse = await apiContext.get('/api/admin/users', {
      headers: { Authorization: `Bearer ${modifiedToken}` }
    });
    
    // 应该被拒绝
    expect(adminResponse.status()).toBe(403);
  });

  test('should prevent horizontal privilege escalation', async ({ }) => {
    const apiContext = await request.newContext();
    
    // 用户 A 登录
    const userAResponse = await apiContext.post('/api/auth/login', {
      data: { email: 'userA@example.com', password: 'password123' }
    });
    const { token: tokenA } = await userAResponse.json();
    
    // 用户 B 登录
    const userBResponse = await apiContext.post('/api/auth/login', {
      data: { email: 'userB@example.com', password: 'password123' }
    });
    const { token: tokenB, userId: userBId } = await userBResponse.json();
    
    // 尝试使用用户 A 的令牌访问用户 B 的数据
    const userBDataResponse = await apiContext.get(`/api/users/${userBId}/profile`, {
      headers: { Authorization: `Bearer ${tokenA}` }
    });
    
    // 应该被拒绝或返回空数据
    expect(userBDataResponse.status()).toBe(403);
  });
});
```

### Session 安全测试

```typescript
test('should prevent session fixation', async ({ }) => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  
  // 1. 访问登录页面
  await page.goto('/login');
  const initialSessionId = await context.cookies()
    .then(cookies => cookies.find(c => c.name === 'sessionId')?.value);
  
  // 2. 登录
  await page.fill('[name="email"]', 'user@example.com');
  await page.fill('[name="password"]', 'password123');
  await page.click('[type="submit"]');
  
  // 3. 登录后检查 session ID 是否改变
  const afterLoginSessionId = await context.cookies()
    .then(cookies => cookies.find(c => c.name === 'sessionId')?.value);
  
  // Session ID 应该改变（防止 session fixation 攻击）
  expect(afterLoginSessionId).not.toBe(initialSessionId);
  
  await browser.close();
});

test('should enforce session timeout', async ({ }) => {
  const apiContext = await request.newContext();
  
  // 登录
  const loginResponse = await apiContext.post('/api/auth/login', {
    data: { email: 'user@example.com', password: 'password123' }
  });
  const { token } = await loginResponse.json();
  
  // 等待会话超时（假设超时时间是 30 分钟）
  // 在测试中我们可以检查 token 的过期时间
  
  // 使用过期的 token
  const expiredToken = jwt.sign(
    { userId: '123', exp: Math.floor(Date.now() / 1000) - 3600 }, // 1 小时前过期
    process.env.JWT_SECRET
  );
  
  const response = await apiContext.get('/api/protected', {
    headers: { Authorization: `Bearer ${expiredToken}` }
  });
  
  expect(response.status()).toBe(401);
});
```

## 密码安全测试

```typescript
test('should enforce strong password policy', async ({ }) => {
  const weakPasswords = [
    '123456',
    'password',
    'password123',
    'qwerty',
    'abc123',
    'letmein',
    'admin',
  ];
  
  for (const password of weakPasswords) {
    const response = await request.post('/api/auth/register', {
      data: {
        email: `test${Date.now()}@example.com`,
        password: password
      }
    });
    
    // 弱密码应该被拒绝
    expect(response.status()).toBe(400);
    const body = await response.json();
    expect(body.message).toContain('password');
  }
});

test('should prevent password reuse', async ({ }) => {
  const apiContext = await request.newContext();
  
  // 注册新用户
  const email = `reuse${Date.now()}@example.com`;
  const password = 'SecurePass123!';
  
  const registerResponse = await apiContext.post('/api/auth/register', {
    data: { email, password }
  });
  
  // 更改密码
  await apiContext.post('/api/auth/change-password', {
    data: {
      email,
      oldPassword: password,
      newPassword: password // 尝试重复使用相同密码
    }
  });
  
  // 应该被拒绝
  // ... 验证响应
});
```

## OAuth 安全测试

```typescript
test('should prevent OAuth state parameter manipulation', async ({ }) => {
  // 1. 开始 OAuth 流程
  const authUrl = 'https://oauth-provider.com/authorize?' +
    'client_id=your_client_id&' +
    'redirect_uri=https://your-app.com/callback&' +
    'response_type=code&' +
    'scope=openid profile&' +
    'state=original_state';
  
  // 2. 模拟攻击者修改 state 参数
  // 在实际测试中，你需要在 OAuth provider 端验证 state 是否被验证
  
  // 3. 验证 state 被正确验证
  const callbackResponse = await page.goto('https://your-app.com/callback?' +
    'code=auth_code&' +
    'state=modified_state'); // 修改的 state
  
  // 应该拒绝访问
  await expect(page.locator('body')).toContainText('invalid state');
});

test('should validate redirect_uri', async ({ }) => {
  const apiContext = await request.newContext();
  
  // 尝试使用未注册的 redirect_uri
  const response = await apiContext.get('https://oauth-provider.com/authorize', {
    params: {
      client_id: 'your_client_id',
      redirect_uri: 'https://evil.com/callback', // 恶意 URI
      response_type: 'code',
      scope: 'openid profile'
    }
  });
  
  // 应该被拒绝
  expect(response.status()).toBe(400);
});
```

## 速率限制测试

```typescript
test('should enforce rate limiting on login', async ({ }) => {
  const apiContext = await request.newContext();
  
  // 尝试多次失败登录
  const maxAttempts = 5;
  let blocked = false;
  
  for (let i = 0; i < maxAttempts + 5; i++) {
    const response = await apiContext.post('/api/auth/login', {
      data: {
        email: 'user@example.com',
        password: 'wrongpassword'
      }
    });
    
    if (response.status() === 429) {
      blocked = true;
      break;
    }
  }
  
  expect(blocked).toBe(true);
});
```

## 访问控制测试矩阵

```typescript
// 定义用户角色和他们的访问权限
const accessMatrix = {
  admin: {
    '/api/admin/users': ['GET', 'POST', 'PUT', 'DELETE'],
    '/api/admin/settings': ['GET', 'PUT'],
    '/api/users/me': ['GET', 'PUT'],
    '/api/users/*': ['GET'], // 可以查看任何用户
  },
  user: {
    '/api/users/me': ['GET', 'PUT'],
    '/api/users/*': [], // 不能访问其他用户
    '/api/orders': ['GET', 'POST'],
    '/api/orders/*': ['GET'], // 只能查看自己的订单
  },
  guest: {
    '/api/users/me': [],
    '/api/public/*': ['GET'],
  }
};

test('should enforce access control matrix', async ({ }) => {
  for (const [role, endpoints] of Object.entries(accessMatrix)) {
    const token = await getTokenForRole(role);
    
    for (const [endpoint, methods] of Object.entries(endpoints)) {
      for (const method of methods) {
        const response = await makeRequest(method, endpoint, token);
        expect(response.status()).toBeLessThan(400, 
          `${role} should be able to ${method} ${endpoint}`);
      }
    }
    
    // 测试未授权的访问
    const unauthorizedEndpoints = getUnauthorizedEndpoints(role);
    for (const endpoint of unauthorizedEndpoints) {
      const response = await makeRequest('GET', endpoint, token);
      expect(response.status()).toBe(403);
    }
  }
});
```

## 报告格式

```typescript
interface SecurityFinding {
  severity: 'critical' | 'high' | 'medium' | 'low';
  category: string;
  title: string;
  description: string;
  endpoint: string;
  payload?: string;
  remediation: string;
  references?: string[];
}

function generateSecurityReport(findings: SecurityFinding[]) {
  const report = {
    scanDate: new Date().toISOString(),
    summary: {
      critical: findings.filter(f => f.severity === 'critical').length,
      high: findings.filter(f => f.severity === 'high').length,
      medium: findings.filter(f => f.severity === 'medium').length,
      low: findings.filter(f => f.severity === 'low').length,
    },
    findings: findings
  };
  
  console.log('\n=== Security Scan Report ===');
  console.log(`Critical: ${report.summary.critical}`);
  console.log(`High: ${report.summary.high}`);
  console.log(`Medium: ${report.summary.medium}`);
  console.log(`Low: ${report.summary.low}`);
  
  return report;
}
```

## 最佳实践

1. **测试所有认证路径** -- 包括 JWT、Session、OAuth、API Keys 等。
2. **验证令牌签名** -- 确保使用强签名算法（RS256 而非 HS256）。
3. **检查过期验证** -- 所有令牌都应该有合理的过期时间。
4. **测试水平权限** -- 确保用户不能访问其他同级别用户的数据。
5. **测试垂直权限** -- 确保低权限用户不能访问高权限功能。
6. **实现速率限制** -- 防止暴力破解攻击。
7. **记录所有认证事件** -- 用于安全审计和入侵检测。
8. **使用安全的会话管理** -- Session fixation 保护、安全的 cookie 设置。

## 应避免的反模式

1. **仅依赖客户端验证** -- 攻击者可以绕过 JavaScript 验证。
2. **使用弱 JWT 签名** -- 使用 HS256 需要共享密钥，容易泄露。
3. **不过期令牌** -- 永不过期的令牌增加安全风险。
4. **不验证 redirect_uri** -- OAuth 重定向 URI 验证防止恶意回调。
5. **忽略 IDOR** -- 每次访问对象时都要验证权限。
6. **无速率限制** -- 允许无限次尝试登录。
7. **明文存储密码** -- 使用强哈希算法（bcrypt、argon2）。
8. **不记录安全事件** -- 安全审计需要完整的日志。