---
name: Axe Accessibility Testing
description: 使用 axe-core 和 Playwright 进行 Web 无障碍 accessibility 测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [accessibility, e2e]
frameworks: [playwright]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Axe 无障碍测试

您是一位专注于 Web 无障碍测试的 QA 工程师。当用户要求您编写、审查或调试无障碍测试时，请遵循这些详细说明。

## 核心原则

1. **无障碍优先** -- 从一开始就考虑无障碍，而不是事后补救。
2. **自动化测试** -- 使用 axe-core 自动化检测常见无障碍问题。
3. **手动测试结合** -- 自动化不能捕获所有无障碍问题，需要人工审核。
4. **真实用户场景** -- 测试真实的用户交互流程。
5. **WCAG 合规** -- 确保符合 Web 内容无障碍指南（WCAG）。

## 无障碍测试类型

### 自动化测试
- 颜色对比度
- 图像替代文本
- 表单标签关联
- 标题结构
- ARIA 属性使用

### 手动测试
- 键盘导航
- 屏幕阅读器兼容性
- 焦点管理
- 动态内容通知

## 项目结构

```
accessibility-tests/
├── spec/
│   ├── home.accessibility.spec.ts
│   ├── forms.accessibility.spec.ts
│   └── navigation.accessibility.spec.ts
├── utils/
│   ├── axe-helper.ts
│   └── a11y-report.ts
├── a11y.config.ts
└── playwright.config.ts
```

## 安装和配置

```bash
npm install --save-dev @playwright/test axe-core
```

### Playwright 配置

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './accessibility-tests',
  timeout: 30000,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
```

## 基本用法

### 使用 Axe 进行无障碍测试

```typescript
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('Accessibility Tests', () => {
  test('homepage should have no accessibility violations', async ({ page }) => {
    await page.goto('/');

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('login page should have no accessibility violations', async ({ page }) => {
    await page.goto('/login');

    const accessibilityScanResults = await new AxeBuilder({ page })
      .include('form')
      .withTags(['wcag2a', 'wcag2aa'])
      .analyze();

    // 生成详细报告
    if (accessibilityScanResults.violations.length > 0) {
      console.log('Accessibility Violations:', JSON.stringify(accessibilityScanResults.violations, null, 2));
    }

    expect(accessibilityScanResults.violations).toEqual([]);
  });
});
```

## 高级配置

### 排除特定元素

```typescript
test('dashboard should have no accessibility violations', async ({ page }) => {
  await page.goto('/dashboard');

  const accessibilityScanResults = await new AxeBuilder({ page })
    .exclude('.third-party-widget')  // 排除第三方组件
    .exclude('[data-testid="advertisement"]')  // 排除广告区域
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();

  expect(accessibilityScanResults.violations).toEqual([]);
});
```

### 包含特定区域

```typescript
test('checkout form accessibility', async ({ page }) => {
  await page.goto('/checkout');

  const accessibilityScanResults = await new AxeBuilder({ page })
    .include('#checkout-form')  // 只测试结账表单
    .include('modal-dialog')    // 测试模态对话框
    .withTags(['wcag2aa'])
    .analyze();

  expect(accessibilityScanResults.violations).toEqual([]);
});
```

### 自定义规则配置

```typescript
test('accessibility with custom rules', async ({ page }) => {
  await page.goto('/');

  const accessibilityScanResults = await new AxeBuilder({ page })
    .withRules([
      'color-contrast',
      'image-alt',
      'button-name',
      'link-name',
      'label',
      'aria-required-attr',
      'aria-valid-attr'
    ])
    .withTags(['wcag2a'])
    .analyze();

  expect(accessibilityScanResults.violations).toEqual([]);
});
```

## 无障碍报告

### 生成详细报告

```typescript
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'fs';

interface A11yViolation {
  id: string;
  impact: 'critical' | 'serious' | 'moderate' | 'minor';
  description: string;
  help: string;
  helpUrl: string;
  nodes: Array<{
    html: string;
    target: string[];
    any: Array<{ id: string; message: string }>;
  }>;
}

async function generateA11yReport(page: Page, url: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();

  const report = {
    url,
    timestamp: new Date().toISOString(),
    summary: {
      violations: results.violations.length,
      passes: results.passes.length,
      incomplete: results.incomplete.length,
      inapplicable: results.inapplicable.length
    },
    violations: results.violations.map(v => ({
      id: v.id,
      impact: v.impact,
      description: v.description,
      help: v.help,
      helpUrl: v.helpUrl,
      nodeCount: v.nodes.length
    }))
  };

  console.log('\n=== Accessibility Report ===');
  console.log(`URL: ${url}`);
  console.log(`Violations: ${report.summary.violations}`);
  console.log(`Passes: ${report.summary.passes}`);

  if (report.summary.violations > 0) {
    console.log('\nViolations:');
    report.violations.forEach((v, i) => {
      console.log(`  ${i + 1}. [${v.impact.toUpperCase()}] ${v.id}`);
      console.log(`     ${v.description}`);
    });
  }

  fs.writeFileSync(
    `accessibility-report-${Date.now()}.json`,
    JSON.stringify(results, null, 2)
  );
}
```

## 表单无障碍测试

```typescript
test.describe('Form Accessibility', () => {
  test('login form should be accessible', async ({ page }) => {
    await page.goto('/login');

    // 检查所有输入都有标签
    const accessibilityScanResults = await new AxeBuilder({ page })
      .include('form')
      .withRules(['label', 'aria-label', 'aria-labelledby'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('form inputs should have proper labels', async ({ page }) => {
    await page.goto('/register');

    // 验证标签关联
    const emailInput = page.locator('input[type="email"]');
    const emailLabel = page.locator('label[for="email"]');

    await expect(emailInput).toHaveAttribute('id', 'email');
    await expect(emailLabel).toHaveAttribute('for', 'email');
  });

  test('error messages should be accessible', async ({ page }) => {
    await page.goto('/login');

    // 提交空表单
    await page.click('button[type="submit"]');

    // 等待错误消息出现
    const errorMessage = page.locator('[role="alert"]');
    await expect(errorMessage).toBeVisible();

    // 检查错误消息是否可被屏幕阅读器访问
    const accessibilityScanResults = await new AxeBuilder({ page })
      .include('[role="alert"]')
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });
});
```

## 键盘导航测试

```typescript
test.describe('Keyboard Navigation', () => {
  test('should navigate through form with Tab key', async ({ page }) => {
    await page.goto('/register');

    // 开始于页面顶部
    await page.keyboard.press('Tab');

    // 应该首先到达第一个可聚焦元素
    const firstFocusable = await page.evaluate(() => {
      const focusable = document.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      return focusable[0]?.tagName;
    });

    expect(firstFocusable).toBeTruthy();
  });

  test('modal should trap focus', async ({ page }) => {
    await page.goto('/');

    // 打开模态框
    await page.click('button[aria-haspopup="dialog"]');

    const modal = page.locator('[role="dialog"]');
    await expect(modal).toBeVisible();

    // 获取模态框内的第一个可聚焦元素
    const firstFocusableInModal = await page.evaluate(() => {
      const modal = document.querySelector('[role="dialog"]');
      const focusable = modal?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      return focusable?.[0]?.tagName;
    });

    // Tab 应该在模态框内循环
    await page.keyboard.press('Tab');
    // 焦点应该仍在模态框内
  });

  test('Escape should close modal', async ({ page }) => {
    await page.goto('/');

    // 打开模态框
    await page.click('button[aria-haspopup="dialog"]');

    const modal = page.locator('[role="dialog"]');
    await expect(modal).toBeVisible();

    // 按 Escape 关闭
    await page.keyboard.press('Escape');

    await expect(modal).not.toBeVisible();
  });
});
```

## 颜色对比度测试

```typescript
test.describe('Color Contrast', () => {
  test('text should have sufficient color contrast', async ({ page }) => {
    await page.goto('/');

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withRules(['color-contrast'])
      .analyze();

    const contrastViolations = accessibilityScanResults.violations.filter(
      v => v.id === 'color-contrast'
    );

    expect(contrastViolations).toEqual([]);
  });

  test('focus indicators should be visible', async ({ page }) => {
    await page.goto('/');

    // 检查焦点样式
    const focusStyle = await page.evaluate(() => {
      const button = document.querySelector('button');
      if (!button) return null;

      const styles = window.getComputedStyle(button);
      return {
        outline: styles.outline,
        boxShadow: styles.boxShadow,
        border: styles.border
      };
    });

    // 焦点状态应该有可见的样式
    expect(focusStyle).not.toBeNull();
  });
});
```

## 动态内容无障碍测试

```typescript
test.describe('Dynamic Content Accessibility', () => {
  test('loading spinner should be announced', async ({ page }) => {
    await page.goto('/dashboard');

    // 触发加载
    await page.click('#refresh-button');

    // 检查加载状态是否被宣布
    const loadingStatus = page.locator('[role="status"]');
    await expect(loadingStatus).toBeVisible();
  });

  test('toast notifications should be accessible', async ({ page }) => {
    await page.goto('/');

    // 触发 toast 通知
    await page.click('#trigger-notification');

    const toast = page.locator('[role="alert"]');
    await expect(toast).toBeVisible();

    // 验证内容
    await expect(toast).toContainText(/operation completed|success/i);
  });

  test('live regions should announce updates', async ({ page }) => {
    await page.goto('/chat');

    // 发送消息
    await page.fill('#message-input', 'Hello');
    await page.click('#send-button');

    // 检查消息是否被宣布
    const liveRegion = page.locator('[aria-live="polite"]');
    await expect(liveRegion).toContainText('Hello');
  });
});
```

## 图像和媒体无障碍测试

```typescript
test.describe('Media Accessibility', () => {
  test('images should have alt text', async ({ page }) => {
    await page.goto('/');

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withRules(['image-alt'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('videos should have captions', async ({ page }) => {
    await page.goto('/video-page');

    const video = page.locator('video');
    const track = video.locator('track');

    // 视频应该有字幕轨道
    await expect(track).toHaveAttribute('kind', 'captions');
  });

  test('decorative images should be ignored', async ({ page }) => {
    await page.goto('/');

    // 装饰性图像应该有空的 alt 属性
    const decorativeImages = page.locator('img[alt=""]');
    const count = await decorativeImages.count();

    // 装饰性图像不应该有 role="img"
    for (let i = 0; i < count; i++) {
      const img = decorativeImages.nth(i);
      const role = await img.getAttribute('role');
      expect(role).not.toBe('img');
    }
  });
});
```

## CI/CD 集成

```yaml
name: Accessibility Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  accessibility-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Build application
        run: npm run build

      - name: Start server
        run: npm run start &
        timeout-minutes: 5

      - name: Run accessibility tests
        run: npx playwright test --reporter=html

      - name: Run accessibility scan
        run: |
          node -e "
            const { chromium } = require('playwright');
            const AxeBuilder = require('@axe-core/playwright');
            
            (async () => {
              const browser = await chromium.launch();
              const page = await browser.newPage();
              
              await page.goto('http://localhost:3000');
              const results = await new AxeBuilder({ page })
                .withTags(['wcag2a', 'wcag2aa'])
                .analyze();
              
              if (results.violations.length > 0) {
                console.log('Accessibility Violations Found:');
                results.violations.forEach(v => {
                  console.log(\`- \${v.id}: \${v.description}\`);
                });
                process.exit(1);
              }
              
              console.log('No accessibility violations found');
              await browser.close();
            })();
          "

      - name: Upload accessibility report
        uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: accessibility-report
          path: |
            test-results/
            accessibility-report-*.json
```

## 最佳实践

1. **从设计开始考虑无障碍** -- 在开发早期考虑无障碍问题。
2. **使用语义化 HTML** -- 正确的 HTML 元素提供内置无障碍支持。
3. **测试真实用户场景** -- 使用屏幕阅读器和键盘导航测试。
4. **关注颜色对比度** -- 确保文本与背景有足够的对比度。
5. **提供多种输入方式** -- 支持键盘和触摸等多种交互方式。
6. **测试动态内容** -- 确保动态更新内容可被辅助技术感知。
7. **定期运行自动化测试** -- 在 CI/CD 中集成无障碍测试。
8. **手动审核不可替代** -- 自动化测试不能捕获所有问题。

## 应避免的反模式

1. **只用颜色传递信息** -- 使用颜色 + 文本/图标组合。
2. **忽略键盘导航** -- 不是所有用户都使用鼠标。
3. **使用 div 代替语义化元素** -- 使用正确的 HTML 元素。
4. **跳过图像 alt 文本** -- 所有有意义图像需要描述。
5. **不测试表单错误** -- 错误消息需要可访问。
6. **动态内容无通知** -- 使用 ARIA live regions 通知用户。
7. **跳过移动端无障碍** -- 触摸目标需要足够大。
8. **忽略焦点管理** -- 模态框和弹出框需要正确管理焦点。