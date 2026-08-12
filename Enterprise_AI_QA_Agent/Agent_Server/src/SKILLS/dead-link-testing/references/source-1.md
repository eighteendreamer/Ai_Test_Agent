---
name: Dead Link Detector
description: 使用 Playwright 检测网页死链，包括 404、断开链接、资源加载失败等
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e, smoke]
frameworks: [playwright]
info: vip.hctestedu.com
languages: [typescript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 死链检测器

您是一位专注于网页链接检测的 QA 工程师。当用户要求您检测网页死链时，请遵循这些详细说明。

## 核心原则

1. **全面扫描** -- 检查所有内部和外部链接。
2. **状态码验证** -- 区分不同的 HTTP 状态码。
3. **资源完整性** -- 验证图片、脚本、样式表等资源。
4. **优雅处理** -- 区分真正的死链和暂时性错误。
5. **可操作的报告** -- 生成清晰的报告帮助开发者修复。

## 死链类型

### 1. 断开链接（404/410）
- 页面被删除
- URL 结构更改
- 拼写错误

### 2. 服务器错误（5xx）
- 服务器暂时不可用
- 配置错误
- 超时

### 3. 无效资源
- 缺失的图片/CSS/JS
- 损坏的媒体文件
- 失效的 CDN 资源

### 4. 外部链接问题
- 外部站点关闭
- 域名过期
- 认证要求

## 项目结构

```
link-checker/
├── tests/
│   ├── link-check.spec.ts
│   └── resource-check.spec.ts
├── utils/
│   ├── link-collector.ts
│   ├── http-checker.ts
│   └── reporter.ts
├── config/
│   └── urls.json
├── playwright.config.ts
└── package.json
```

## 基本链接检查

```typescript
import { test, expect, request } from '@playwright/test';

test.describe('Dead Link Detection', () => {
  test('should detect broken links on homepage', async ({ page }) => {
    const brokenLinks: Array<{ url: string; status: number; message: string }> = [];

    page.on('response', async (response) => {
      const url = response.url();
      const status = response.status();

      // 只检查同域名的内部链接
      if (url.includes('localhost') && status >= 400) {
        brokenLinks.push({
          url,
          status,
          message: `Failed with status ${status}`
        });
      }
    });

    await page.goto('http://localhost:3000');
    await page.waitForLoadState('networkidle');

    // 检查所有链接
    const links = await page.locator('a[href]').all();
    for (const link of links) {
      const href = await link.getAttribute('href');
      if (href && !href.startsWith('#') && !href.startsWith('mailto:')) {
        try {
          const response = await request.get(href.startsWith('http') ? href : `http://localhost:3000${href}`);
          if (response.status() >= 400) {
            brokenLinks.push({
              url: href,
              status: response.status(),
              message: `Link returned status ${response.status()}`
            });
          }
        } catch (e) {
          brokenLinks.push({
            url: href,
            status: 0,
            message: `Request failed: ${e.message}`
          });
        }
      }
    }

    console.log('Broken links found:', brokenLinks);
    expect(brokenLinks).toHaveLength(0);
  });
});
```

## 资源完整性检查

```typescript
import { test, expect } from '@playwright/test';

test.describe('Resource Integrity Check', () => {
  test('should verify all resources load correctly', async ({ page }) => {
    const failedResources: Array<{ url: string; type: string; error: string }> = [];

    page.on('requestfailed', (request) => {
      failedResources.push({
        url: request.url(),
        type: request.resourceType(),
        error: request.failure()?.errorText || 'Unknown error'
      });
    });

    page.on('response', async (response) => {
      const status = response.status();
      const url = response.url();

      // 检查资源响应（图片、脚本、样式表等）
      if (
        (status >= 400) &&
        (response.resourceType() === 'image' ||
         response.resourceType() === 'stylesheet' ||
         response.resourceType() === 'script' ||
         response.resourceType() === 'font')
      ) {
        failedResources.push({
          url,
          type: response.resourceType(),
          error: `HTTP ${status}`
        });
      }
    });

    await page.goto('http://localhost:3000');
    await page.waitForLoadState('networkidle');

    if (failedResources.length > 0) {
      console.log('Failed resources:', JSON.stringify(failedResources, null, 2));
    }

    expect(failedResources).toHaveLength(0);
  });

  test('should check image accessibility', async ({ page }) => {
    await page.goto('http://localhost:3000');

    const images = await page.locator('img').all();
    const missingAlt: string[] = [];

    for (const img of images) {
      const alt = await img.getAttribute('alt');
      const src = await img.getAttribute('src');

      // 检查是否有 alt 属性（装饰性图片应该 alt=""）
      if (alt === null) {
        missingAlt.push(`Image missing alt attribute: ${src}`);
      }
    }

    expect(missingAlt).toHaveLength(0);
  });
});
```

## 递归链接检查

```typescript
import { test, expect, request } from '@playwright/test';

interface LinkInfo {
  url: string;
  status?: number;
  checked: boolean;
  children: LinkInfo[];
}

class LinkChecker {
  private baseUrl: string;
  private visitedUrls = new Set<string>();
  private maxDepth: number;
  private failedLinks: Array<{ url: string; status: number; source: string }> = [];

  constructor(baseUrl: string, maxDepth = 3) {
    this.baseUrl = baseUrl;
    this.maxDepth = maxDepth;
  }

  async checkPage(url: string, depth = 0, source = 'root'): Promise<void> {
    if (depth > this.maxDepth) return;
    if (this.visitedUrls.has(url)) return;

    this.visitedUrls.add(url);

    try {
      const response = await request.get(url, { maxRetries: 2 });

      if (response.status() >= 400 && response.status() !== 429) {
        this.failedLinks.push({
          url,
          status: response.status(),
          source
        });
      }

      // 如果是 HTML 页面，提取更多链接
      if (response.headers()['content-type']?.includes('text/html')) {
        const body = await response.text();

        // 使用 Playwright 解析 HTML
        // 这里简化处理，实际应该用 DOM 解析
        const links = this.extractLinks(body, url);

        for (const link of links) {
          await this.checkPage(link, depth + 1, url);
        }
      }
    } catch (e) {
      this.failedLinks.push({
        url,
        status: 0,
        source
      });
    }
  }

  private extractLinks(html: string, baseUrl: string): string[] {
    // 简化：实际应该用 DOM 解析器
    const linkRegex = /href=["']([^"']+)["']/g;
    const links: string[] = [];
    let match;

    while ((match = linkRegex.exec(html)) !== null) {
      let href = match[1];

      if (href.startsWith('/')) {
        href = new URL(this.baseUrl).origin + href;
      }

      if (href.startsWith(this.baseUrl) && !this.visitedUrls.has(href)) {
        links.push(href);
      }
    }

    return links;
  }

  getFailedLinks() {
    return this.failedLinks;
  }

  getVisitedCount() {
    return this.visitedUrls.size;
  }
}

test('should recursively check all internal links', async () => {
  const checker = new LinkChecker('http://localhost:3000', 3);
  await checker.checkPage('http://localhost:3000');

  const failedLinks = checker.getFailedLinks();

  console.log(`Checked ${checker.getVisitedCount()} pages`);
  console.log(`Found ${failedLinks.length} broken links`);

  if (failedLinks.length > 0) {
    console.log('Failed links:', JSON.stringify(failedLinks, null, 2));
  }

  expect(failedLinks).toHaveLength(0);
});
```

## 特定 URL 列表检查

```typescript
import { test, expect, request } from '@playwright/test';
import * as fs from 'fs';

interface UrlCheckResult {
  url: string;
  status: number;
  ok: boolean;
  error?: string;
  responseTime: number;
}

async function checkUrls(urls: string[]): Promise<UrlCheckResult[]> {
  const results: UrlCheckResult[] = [];

  for (const url of urls) {
    const start = Date.now();
    try {
      const response = await request.get(url, {
        maxRetries: 2,
        timeout: 10000
      });

      results.push({
        url,
        status: response.status(),
        ok: response.status() < 400,
        responseTime: Date.now() - start
      });
    } catch (e) {
      results.push({
        url,
        status: 0,
        ok: false,
        error: e.message,
        responseTime: Date.now() - start
      });
    }
  }

  return results;
}

test('should check all critical URLs', async () => {
  // 从配置文件加载关键 URL
  const criticalUrls = [
    'http://localhost:3000/',
    'http://localhost:3000/login',
    'http://localhost:3000/register',
    'http://localhost:3000/dashboard',
    'http://localhost:3000/api/health',
    'http://localhost:3000/api/users',
  ];

  const results = await checkUrls(criticalUrls);

  // 生成报告
  console.log('\n=== URL Check Report ===');
  results.forEach(result => {
    const icon = result.ok ? '✓' : '✗';
    console.log(`${icon} ${result.status} ${result.url} (${result.responseTime}ms)`);
    if (result.error) {
      console.log(`  Error: ${result.error}`);
    }
  });

  // 汇总
  const failed = results.filter(r => !r.ok);
  console.log(`\nTotal: ${results.length}, Passed: ${results.length - failed.length}, Failed: ${failed.length}`);

  expect(failed).toHaveLength(0);
});
```

## 生成报告

```typescript
import * as fs from 'fs';

interface LinkCheckReport {
  timestamp: string;
  baseUrl: string;
  summary: {
    totalChecked: number;
    passed: number;
    failed: number;
    brokenLinks: number;
    serverErrors: number;
    resourceFailures: number;
  };
  brokenLinks: Array<{
    url: string;
    status: number;
    type: 'link' | 'resource' | 'external';
    source?: string;
    suggestion?: string;
  }>;
}

function generateReport(results: any): LinkCheckReport {
  const report: LinkCheckReport = {
    timestamp: new Date().toISOString(),
    baseUrl: 'http://localhost:3000',
    summary: {
      totalChecked: results.totalChecked || 0,
      passed: results.passed || 0,
      failed: results.failed || 0,
      brokenLinks: results.brokenLinks?.length || 0,
      serverErrors: results.serverErrors?.length || 0,
      resourceFailures: results.resourceFailures?.length || 0,
    },
    brokenLinks: []
  };

  // 分类问题
  if (results.brokenLinks) {
    for (const link of results.brokenLinks) {
      report.brokenLinks.push({
        url: link.url,
        status: link.status,
        type: 'link',
        suggestion: getSuggestion(link)
      });
    }
  }

  return report;
}

function getSuggestion(link: { status: number; url: string }): string {
  if (link.status === 404) {
    return 'Page not found. Update or remove this link.';
  }
  if (link.status === 500) {
    return 'Server error. Check server logs and fix the endpoint.';
  }
  if (link.status === 403) {
    return 'Forbidden. Check authentication/authorization settings.';
  }
  if (link.status === 0) {
    return 'Connection failed. Check if the URL is correct and the server is running.';
  }
  return 'Investigate and fix the link.';
}

test('should generate link check report', async () => {
  // ... 执行检查 ...

  const results = {
    totalChecked: 100,
    passed: 95,
    failed: 5,
    brokenLinks: [
      { url: 'http://localhost:3000/old-page', status: 404 },
      { url: 'http://localhost:3000/missing', status: 404 },
    ],
    serverErrors: [],
    resourceFailures: [
      { url: 'http://localhost:3000/images/missing.png', status: 404 }
    ]
  };

  const report = generateReport(results);

  // 保存报告
  fs.writeFileSync(
    `link-check-report-${Date.now()}.json`,
    JSON.stringify(report, null, 2)
  );

  // 输出摘要
  console.log('\n=== Link Check Summary ===');
  console.log(`Total URLs checked: ${report.summary.totalChecked}`);
  console.log(`Passed: ${report.summary.passed}`);
  console.log(`Failed: ${report.summary.failed}`);
  console.log(`Broken links: ${report.summary.brokenLinks}`);
  console.log(`Server errors: ${report.summary.serverErrors}`);
  console.log(`Resource failures: ${report.summary.resourceFailures}`);
});
```

## CI/CD 集成

```yaml
name: Dead Link Detection
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  link-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

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

      - name: Run link check
        run: npx playwright test tests/link-check.spec.ts

      - name: Upload link check report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: link-check-report
          path: link-check-report-*.json
```

## 最佳实践

1. **定期检查** -- 在 CI/CD 中运行链接检查。
2. **分层的 URL 列表** -- 区分关键页面和次要页面。
3. **重试机制** -- 暂时性错误应该重试验证。
4. **超时设置** -- 设置合理的超时避免挂起。
5. **并发限制** -- 避免同时发送过多请求。
6. **状态码分类** -- 区分 404、500 等不同错误。
7. **排除规则** -- 外部链接可能需要排除。
8. **报告可读性** -- 生成清晰的错误报告。

## 应避免的反模式

1. **只检查首页** -- 递归检查所有内部链接。
2. **忽略资源失败** -- 图片/CSS/JS 也需要检查。
3. **无重试机制** -- 暂时性错误应该重试。
4. **无并发控制** -- 可能导致服务器过载。
5. **忽略外部链接** -- 外部链接也可能失效。
6. **没有状态码分类** -- 不同状态码需要不同处理。
7. **忽略认证页面** -- 确保认证页面链接正常。
8. **不跟踪历史** -- 应该追踪链接状态变化。