---
name: Playwright Visual Regression
description: 使用 Playwright 截图对比进行可视化回归测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [visual, e2e]
info: vip.hctestedu.com
frameworks: [playwright]
languages: [typescript, javascript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Playwright 可视化回归测试

您是一位专注于可视化回归测试的 QA 工程师。当用户要求您编写、审查或调试可视化测试时，请遵循这些详细说明。

## 核心原则

1. **像素完美匹配** -- 基线截图是视觉正确性的标准。
2. **确定性渲染** -- 消除视觉非确定性的来源。
3. **阈值比较** -- 允许小的可接受差异减少误报。
4. **响应式覆盖** -- 测试关键断点而非仅桌面分辨率。
5. **组件和页面级别** -- 测试单个组件和完整页面布局。

## 项目结构

```
visual-tests/
├── tests/
│   ├── pages/
│   │   ├── homepage.visual.spec.ts
│   │   └── dashboard.visual.spec.ts
│   └── components/
│       ├── button.visual.spec.ts
│       └── card.visual.spec.ts
├── baselines/                 # 基线截图
│   ├── chromium/
│   │   ├── homepage.png
│   │   └── dashboard.png
│   ├── firefox/
│   │   └── homepage.png
│   └── webkit/
│       └── homepage.png
├── results/                   # 测试结果和差异
├── playwright.config.ts
└── package.json
```

## 配置

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests',
  timeout: 60000,
  updateSnapshots: process.env.UPDATE_SNAPSHOTS === 'true',

  // 截图配置
  screenshot: {
    fullPage: true,
  },

  // 视觉比较配置
  expect: {
    toHaveScreenshot: {
      maxDiffPixels: 100,           // 最多 100 像素差异
      maxDiffPixelRatio: 0.01,      // 或 1% 的总像素
      threshold: 0.2,               // 每像素颜色阈值 (0-1)
      animations: 'disabled',       // 禁用 CSS 动画
    },
    toMatchSnapshot: {
      maxDiffPixelRatio: 0.01,
    },
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
      name: 'mobile',
      use: { ...devices['iPhone 12'] },
    },
  ],
});
```

## 基础可视化测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Homepage Visual Tests', () => {
  test('homepage should match baseline', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveScreenshot('homepage.png');
  });

  test('homepage full page should match baseline', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveScreenshot('homepage-full.png', {
      fullPage: true,
    });
  });
});
```

## 组件级可视化测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Button Component Visual Tests', () => {
  test('primary button states', async ({ page }) => {
    await page.goto('/components/buttons');

    // 默认状态
    await expect(page.locator('.btn-primary')).toHaveScreenshot('btn-primary-default.png');

    // 悬停状态
    await page.locator('.btn-primary').hover();
    await expect(page.locator('.btn-primary')).toHaveScreenshot('btn-primary-hover.png');

    // 按下状态
    await page.locator('.btn-primary').press('Mouse.down');
    await expect(page.locator('.btn-primary')).toHaveScreenshot('btn-primary-active.png');

    // 禁用状态
    await page.locator('.btn-primary').press('Tab');
    await expect(page.locator('.btn-primary')).toHaveScreenshot('btn-primary-focus.png');
  });

  test('button variants should differ visually', async ({ page }) => {
    await page.goto('/components/buttons');

    const primaryBtn = page.locator('.btn-primary');
    const secondaryBtn = page.locator('.btn-secondary');

    // 两个按钮应该看起来不同
    const primaryImage = await primaryBtn.screenshot();
    const secondaryImage = await secondaryBtn.screenshot();

    expect(primaryImage).not.toEqual(secondaryImage);
  });
});
```

## 响应式可视化测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Responsive Visual Tests', () => {
  const viewports = [
    { name: 'mobile', width: 375, height: 667 },
    { name: 'tablet', width: 768, height: 1024 },
    { name: 'desktop', width: 1280, height: 720 },
    { name: 'wide', width: 1920, height: 1080 },
  ];

  for (const viewport of viewports) {
    test(`homepage at ${viewport.name} (${viewport.width}x${viewport.height})`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/');
      await page.waitForLoadState('networkidle');

      await expect(page).toHaveScreenshot(`homepage-${viewport.name}.png`, {
        fullPage: true,
      });
    });
  }
});
```

## 动态内容处理

### 遮罩动态元素

```typescript
import { test, expect } from '@playwright/test';

test.describe('Dynamic Content Handling', () => {
  test('dashboard with masked dynamic content', async ({ page }) => {
    await page.goto('/dashboard');

    // 遮罩动态元素
    await expect(page).toHaveScreenshot('dashboard.png', {
      mask: [
        page.locator('[data-testid="current-time"]'),      // 当前时间
        page.locator('[data-testid="user-avatar"]'),       // 用户头像
        page.locator('[data-testid="notification-count"]'), // 通知数量
        page.locator('.ad-banner'),                         // 广告
      ],
    });
  });

  test('profile page with replaced dynamic text', async ({ page }) => {
    await page.goto('/profile');

    // 替换动态文本
    await page.evaluate(() => {
      // 替换时间戳
      document.querySelectorAll('[data-testid="timestamp"]').forEach(el => {
        el.textContent = 'January 1, 2024';
      });

      // 替换用户名
      const nameEl = document.querySelector('[data-testid="user-name"]');
      if (nameEl) nameEl.textContent = 'Test User';
    });

    await expect(page).toHaveScreenshot('profile-page.png');
  });
});
```

### 禁用动画

```typescript
import { test, expect } from '@playwright/test';

test.describe('Animations Disabled', () => {
  test.beforeEach(async ({ page }) => {
    // 禁用所有 CSS 动画
    await page.addStyleTag({
      content: `
        *, *::before, *::after {
          animation-duration: 0s !important;
          animation-delay: 0s !important;
          transition-duration: 0s !important;
          transition-delay: 0s !important;
        }
      `,
    });
  });

  test('animated page should match baseline', async ({ page }) => {
    await page.goto('/landing-page');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveScreenshot('landing-page-no-animation.png');
  });
});
```

## 状态测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Form Visual States', () => {
  test('empty form should match baseline', async ({ page }) => {
    await page.goto('/register');

    await expect(page.locator('form')).toHaveScreenshot('form-empty.png');
  });

  test('form with validation errors should match baseline', async ({ page }) => {
    await page.goto('/register');
    await page.click('button[type="submit"]');

    // 等待验证消息出现
    await page.waitForSelector('[data-testid="error-message"]');

    await expect(page.locator('form')).toHaveScreenshot('form-errors.png');
  });

  test('form with filled data should match baseline', async ({ page }) => {
    await page.goto('/register');

    await page.fill('[name="name"]', 'John Doe');
    await page.fill('[name="email"]', 'john@example.com');
    await page.fill('[name="password"]', 'SecurePass123!');

    await expect(page.locator('form')).toHaveScreenshot('form-filled.png');
  });
});
```

## 主题测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Theme Visual Tests', () => {
  test('dark mode should match baseline', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/');

    await expect(page).toHaveScreenshot('homepage-dark.png');
  });

  test('light mode should match baseline', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto('/');

    await expect(page).toHaveScreenshot('homepage-light.png');
  });

  test('high contrast mode', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });

    // 强制高对比度
    await page.addStyleTag({
      content: `
        * {
          contrast: high;
        }
      `,
    });

    await page.goto('/');
    await expect(page).toHaveScreenshot('homepage-high-contrast.png');
  });
});
```

## CI/CD 集成

```yaml
name: Visual Regression Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  visual-tests:
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

      - name: Run visual tests
        run: npx playwright test --project=chromium

      - name: Upload visual diffs
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: visual-diffs
          path: |
            test-results/**/diff-*.png
            test-results/**/*-actual.png

      - name: Upload baseline comparison
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: visual-comparison
          path: test-results/
```

## 更新基线

```bash
# 更新所有基线
UPDATE_SNAPSHOTS=true npx playwright test

# 更新特定测试的基线
UPDATE_SNAPSHOTS=true npx playwright test tests/visual/homepage.spec.ts

# 更新特定项目的基线
UPDATE_SNAPSHOTS=true npx playwright test --project=chromium
```

## 最佳实践

1. **禁用动画** -- CSS 动画会导致不确定的截图。
2. **等待内容加载** -- 等待动态内容、图像和字体加载。
3. **使用确定性数据** -- 模拟 API 响应确保一致的测试数据。
4. **遮罩动态区域** -- 覆盖时间戳、头像和第三方组件。
5. **测试关键断点** -- 至少覆盖移动端、平板和桌面。
6. **设置合理的阈值** -- 太严格导致误报，太宽松可能漏检。
7. **使用一致的浏览器** -- 不同浏览器渲染略有不同。
8. **审查差异** -- 不仅仅是像素差异，可能是真正的 bug。

## 应避免的反模式

1. **不控制动画** -- 动画使截图不确定。
2. **使用实时数据** -- 实时 API 数据会变化，导致误报。
3. **零像素容差** -- 即使抗锯齿也会导致差异。
4. **只测试全页面** -- 组件级截图能发现更具体的回归。
5. **忽略字体加载** -- 字体未加载会产生空白文本。
6. **不遮罩动态内容** -- 时间戳和计数器每次运行都会变化。
7. **只在本地运行** -- 不同操作系统渲染不同。
8. **过多可视化测试** -- 只维护关键页面和组件的基线。