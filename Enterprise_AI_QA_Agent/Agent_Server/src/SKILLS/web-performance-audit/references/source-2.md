---
name: Page Speed Critic
description: 使用 Lighthouse 和 Core Web Vitals 进行页面性能测试和优化
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [performance]
info: vip.hctestedu.com
frameworks: [playwright, lighthouse]
languages: [typescript, javascript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 页面速度评测

您是一位专注于页面性能测试的 QA 工程师。当用户要求您进行页面性能测试时，请遵循这些详细说明。

## 核心原则

1. **Core Web Vitals** -- 关注 LCP、FID、CLS 三大指标。
2. **真实用户体验** -- 模拟真实设备和网络条件。
3. **全面分析** -- 分析性能瀑布图和关键指标。
4. **持续监控** -- 建立性能基准并追踪变化。
5. **优化建议** -- 提供具体的性能优化建议。

## Core Web Vitals

### LCP (Largest Contentful Paint)
- **定义**: 最大内容元素渲染时间
- **良好**: < 2.5秒
- **需要改进**: 2.5 - 4秒
- **差**: > 4秒

### FID (First Input Delay)
- **定义**: 首次输入延迟
- **良好**: < 100ms
- **需要改进**: 100 - 300ms
- **差**: > 300ms

### CLS (Cumulative Layout Shift)
- **定义**: 累计布局偏移
- **良好**: < 0.1
- **需要改进**: 0.1 - 0.25
- **差**: > 0.25

## 项目结构

```
performance-tests/
├── src/
│   ├── lighthouse/
│   │   └── audit.ts
│   ├── core-vitals/
│   │   └── metrics.ts
│   └── utils/
│       ├── report.ts
│       └── thresholds.ts
├── tests/
│   ├── performance/
│   │   └── page-speed.spec.ts
│   └── core-vitals/
│       └── vitals.spec.ts
├── lighthouse.config.ts
└── package.json
```

## Lighthouse 测试

### 安装

```bash
npm install --save-dev @playwright/test lighthouse
```

### Playwright 配置

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/performance',
  timeout: 60000,
  use: {
    baseURL: 'http://localhost:3000',
    headless: true,
    launchOptions: {
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
```

### 基本 Lighthouse 测试

```typescript
import { test, expect } from '@playwright/test';
import * as lighthouse from 'lighthouse';
import { launch } from 'playwright';

async function runLighthouse(url: string) {
  const browser = await launch({ headless: true });
  const page = await browser.newPage();

  const result = await lighthouse(url, {
    port: 9222,
    output: 'json',
    logLevel: 'info',
    onlyCategories: ['performance', 'accessibility', 'best-practices', 'seo'],
  });

  await browser.close();
  return result;
}

test.describe('Page Performance', () => {
  test('homepage should meet performance targets', async () => {
    const result = await runLighthouse('http://localhost:3000/');

    const { categories, audits } = result.lhr;

    console.log('Performance Score:', categories.performance.score * 100);
    console.log('First Contentful Paint:', audits['first-contentful-paint'].numericValue);
    console.log('Largest Contentful Paint:', audits['largest-contentful-paint'].numericValue);
    console.log('Cumulative Layout Shift:', audits['cumulative-layout-shift'].numericValue);

    // 验证性能分数
    expect(categories.performance.score).toBeGreaterThan(0.8);

    // 验证 LCP
    const lcpValue = audits['largest-contentful-paint'].numericValue;
    expect(lcpValue).toBeLessThan(2500);  // 2.5秒

    // 验证 CLS
    const clsValue = audits['cumulative-layout-shift'].numericValue;
    expect(clsValue).toBeLessThan(0.1);
  });

  test('should have fast First Input Delay', async () => {
    const result = await runLighthouse('http://localhost:3000/dashboard');

    const { audits } = result.lhr;
    const fidValue = audits['max-potential-fid'].numericValue;

    console.log('Max Potential FID:', fidValue);
    expect(fidValue).toBeLessThan(100);
  });
});
```

## Core Web Vitals 测试

```typescript
// tests/core-vitals/vitals.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Core Web Vitals', () => {
  test('homepage should pass Core Web Vitals', async ({ page }) => {
    await page.goto('http://localhost:3000/');
    await page.waitForLoadState('networkidle');

    // 获取 Performance metrics
    const metrics = await page.evaluate(() => {
      return JSON.parse(
        JSON.stringify({
          lcp: performance.getEntriesByType('largest-contentful-paint'),
          cls: performance.getEntriesByType('layout-shift'),
          fid: performance.getEntriesByType('first-input'),
        })
      );
    });

    // 计算 LCP
    const lcpEntry = metrics.lcp[metrics.lcp.length - 1];
    const lcpTime = lcpEntry ? lcpEntry.startTime : 0;

    console.log('LCP:', lcpTime);

    // 验证 LCP < 2.5s
    expect(lcpTime).toBeLessThan(2500);
  });

  test('should have minimal Cumulative Layout Shift', async ({ page }) => {
    await page.goto('http://localhost:3000/');

    // 等待一段时间让布局稳定
    await page.waitForTimeout(2000);

    const clsValue = await page.evaluate(() => {
      const entries = performance.getEntriesByType('layout-shift') as any[];
      return entries
        .filter((entry: any) => !entry.hadRecentInput)
        .reduce((sum: number, entry: any) => sum + entry.value, 0);
    });

    console.log('CLS:', clsValue);
    expect(clsValue).toBeLessThan(0.1);
  });

  test('should have fast First Input Delay', async ({ page }) => {
    await page.goto('http://localhost:3000/');
    await page.waitForLoadState('networkidle');

    // 模拟首次输入
    const fidTime = await page.evaluate(() => {
      return new Promise<number>((resolve) => {
        let fidCaptured = false;

        new PerformanceObserver((entryList) => {
          const entries = entryList.getEntries();
          if (!fidCaptured && entries.length > 0) {
            fidCaptured = true;
            resolve(entries[0].processingStart - entries[0].startTime);
          }
        }).observe({ type: 'first-input', buffered: true });

        // 触发首次输入
        setTimeout(() => {
          if (!fidCaptured) {
            document.dispatchEvent(new Event('click'));
          }
        }, 100);

        // 超时处理
        setTimeout(() => {
          if (!fidCaptured) resolve(0);
        }, 5000);
      });
    });

    console.log('FID:', fidTime);
    expect(fidTime).toBeLessThan(100);
  });
});
```

## 性能预算测试

```typescript
// tests/performance/budget.spec.ts
import { test, expect } from '@playwright/test';

interface PerformanceBudget {
  maxLoadTime: number;
  maxFirstContentfulPaint: number;
  maxLargestContentfulPaint: number;
  maxCumulativeLayoutShift: number;
  maxTotalPageWeight: number;
  maxImageWeight: number;
}

const BUDGET: PerformanceBudget = {
  maxLoadTime: 3000,
  maxFirstContentfulPaint: 1800,
  maxLargestContentfulPaint: 2500,
  maxCumulativeLayoutShift: 0.1,
  maxTotalPageWeight: 3 * 1024 * 1024,  // 3MB
  maxImageWeight: 1 * 1024 * 1024,      // 1MB
};

test.describe('Performance Budget', () => {
  test('should not exceed performance budget', async ({ page }) => {
    await page.goto('http://localhost:3000/');
    await page.waitForLoadState('networkidle');

    const metrics = await page.evaluate(() => {
      const entries = performance.getEntriesByType('resource');
      let totalWeight = 0;
      let imageWeight = 0;

      entries.forEach((entry) => {
        const resource = entry as any;
        const size = resource.transferSize || 0;
        totalWeight += size;

        if (resource.name.match(/\.(jpg|jpeg|png|gif|webp|svg)$/i)) {
          imageWeight += size;
        }
      });

      const navigation = performance.getEntriesByType('navigation')[0] as any;

      return {
        loadTime: navigation.loadEventEnd - navigation.startTime,
        fcp: performance.getEntriesByName('first-contentful-paint')[0]?.startTime || 0,
        lcp: Math.max(...performance.getEntriesByType('largest-contentful-paint').map((e: any) => e.startTime)),
        totalWeight,
        imageWeight,
      };
    });

    console.log('Load Time:', metrics.loadTime);
    console.log('Total Page Weight:', (metrics.totalWeight / 1024 / 1024).toFixed(2), 'MB');
    console.log('Image Weight:', (metrics.imageWeight / 1024 / 1024).toFixed(2), 'MB');

    expect(metrics.loadTime).toBeLessThan(BUDGET.maxLoadTime);
    expect(metrics.totalWeight).toBeLessThan(BUDGET.maxTotalPageWeight);
    expect(metrics.imageWeight).toBeLessThan(BUDGET.maxImageWeight);
  });
});
```

## 移动端性能测试

```typescript
test.describe('Mobile Performance', () => {
  test.use({ viewport: { width: 375, height: 667 } });  // iPhone SE

  test('should be fast on mobile', async ({ page }) => {
    // 模拟 4G 网络
    const client = await page.context().newCDPSession(page);
    await client.send('Network.emulateNetworkConditions', {
      offline: false,
      downloadThroughput: (4 * 1024 * 1024) / 8,  // 4 Mbps
      uploadThroughput: (3 * 1024 * 1024) / 8,    // 3 Mbps
      latency: 40,                                 // 40ms RTT
    });

    await page.goto('http://localhost:3000/');
    await page.waitForLoadState('networkidle');

    const metrics = await page.evaluate(() => {
      const navigation = performance.getEntriesByType('navigation')[0] as any;
      return {
        loadTime: navigation.loadEventEnd - navigation.startTime,
        fcp: performance.getEntriesByName('first-contentful-paint')[0]?.startTime || 0,
      };
    });

    console.log('Mobile Load Time:', metrics.loadTime);

    // 移动端允许稍长的时间
    expect(metrics.loadTime).toBeLessThan(5000);
  });
});
```

## 资源分析

```typescript
test.describe('Resource Analysis', () => {
  test('should optimize images', async ({ page }) => {
    await page.goto('http://localhost:3000/');

    const largeImages = await page.evaluate(() => {
      const images = Array.from(document.querySelectorAll('img'));
      return images
        .filter(img => {
          const size = (img as any).naturalWidth * (img as any).naturalHeight;
          const displaySize = img.getBoundingClientRect();
          // 如果图片实际尺寸远大于显示尺寸
          return size > displaySize.width * displaySize.height * 4;
        })
        .map(img => ({
          src: img.src,
          naturalSize: `${(img as any).naturalWidth}x${(img as any).naturalHeight}`,
          displaySize: `${img.getBoundingClientRect().width}x${img.getBoundingClientRect().height}`,
        }));
    });

    if (largeImages.length > 0) {
      console.log('Unoptimized images:', largeImages);
    }

    expect(largeImages).toHaveLength(0);
  });

  test('should not have render-blocking resources', async ({ page }) => {
    await page.goto('http://localhost:3000/');

    const blockingResources = await page.evaluate(() => {
      const resources = performance.getEntriesByType('resource') as any[];
      return resources
        .filter(r => {
          // 同步脚本或同步 XHR 会阻塞渲染
          return (
            (r.initiatorType === 'script' && !r.async && !r.defer) ||
            r.initiatorType === 'xmlhttprequest'
          );
        })
        .map(r => ({
          name: r.name,
          type: r.initiatorType,
          duration: r.duration,
        }));
    });

    expect(blockingResources).toHaveLength(0);
  });

  test('should use efficient caching', async ({ page }) => {
    await page.goto('http://localhost:3000/');

    const uncachedResources = await page.evaluate(() => {
      const resources = performance.getEntriesByType('resource') as any[];
      return resources
        .filter(r => {
          // 检查是否使用了缓存
          return r.transferSize > 0 && r.encodedBodySize === r.decodedBodySize;
        })
        .map(r => ({
          name: r.name,
          type: r.initiatorType,
          size: r.transferSize,
        }));
    });

    // 大部分资源应该被缓存
    const cacheHitRate = 1 - uncachedResources.length / performance.getEntriesByType('resource').length;
    console.log('Cache Hit Rate:', (cacheHitRate * 100).toFixed(1) + '%');

    expect(cacheHitRate).toBeGreaterThan(0.5);
  });
});
```

## CI/CD 集成

```yaml
name: Performance Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lighthouse:
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

      - name: Run Lighthouse
        run: |
          node -e "
            const { chromium } = require('playwright');
            const lighthouse = require('lighthouse');

            (async () => {
              const browser = await chromium.launch({ headless: true });
              const page = await browser.newPage();

              const result = await lighthouse('http://localhost:3000/', {
                port: 9222,
                output: 'json',
                logLevel: 'info',
              });

              const { categories, audits } = result.lhr;

              console.log('Performance Score:', (categories.performance.score * 100).toFixed(0));
              console.log('LCP:', audits['largest-contentful-paint'].numericValue);
              console.log('FID:', audits['max-potential-fid'].numericValue);
              console.log('CLS:', audits['cumulative-layout-shift'].numericValue);

              // 检查是否通过阈值
              if (categories.performance.score < 0.8) {
                console.error('Performance score below threshold!');
                process.exit(1);
              }

              await browser.close();
            })();
          "

      - name: Upload Lighthouse Report
        uses: actions/upload-artifact@v4
        with:
          name: lighthouse-report
          path: .lighthouseci/
```

## 性能优化建议

### 1. 减少 LCP
- 优化服务器响应时间
- 使用 CDN
- 优化关键资源的加载顺序
- 预加载 LCP 元素

### 2. 减少 CLS
- 为图片和视频设置尺寸
- 避免在内容加载后插入广告
- 使用 `transform` 而非改变位置属性

### 3. 减少 FID
- 分割长任务
- 优化第三方脚本
- 使用 Web Workers

## 最佳实践

1. **设置性能预算** -- 明确的性能目标和阈值。
2. **测试真实设备** -- 模拟真实用户条件。
3. **监控关键指标** -- 关注 Core Web Vitals。
4. **分析资源加载** -- 优化图片、脚本、样式表。
5. **持续集成** -- 在 CI 中自动运行性能测试。
6. **渐进式优化** -- 从影响最大的问题开始。
7. **记录基准** -- 建立性能基线追踪变化。
8. **用户体验优先** -- 性能优化服务于用户体验。

## 应避免的反模式

1. **只测试开发环境** -- 开发环境和生产环境性能可能不同。
2. **忽略移动端** -- 移动用户占大多数。
3. **只看加载时间** -- Core Web Vitals 更全面。
4. **过度优化** -- 平衡性能和开发成本。
5. **不测量实际用户** -- 实验室数据需要真实数据验证。
6. **忽略网络条件** -- 真实用户网络条件各异。
7. **忘记缓存** -- 缓存严重影响性能。
8. **不测试多次** -- 性能测试结果有波动。