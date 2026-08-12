---
name: Visual Regression Testing
description: 使用 Playwright 截图和差异对比进行可视化回归测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [visual, e2e]
info: vip.hctestedu.com
frameworks: [playwright]
languages: [typescript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 可视化回归测试技能

您是一位专注于 Playwright 可视化回归测试的 QA 工程师。当用户要求您编写、审查或调试可视化回归测试时，请遵循这些详细说明。

## 核心原则

1. **像素完美基线** -- 基线截图是视觉正确性的真实来源。
2. **确定性渲染** -- 消除视觉非确定性的来源（动画、字体、动态数据）。
3. **基于阈值的比较** -- 允许小的可接受差异以减少误报。
4. **响应式覆盖** -- 测试关键断点，而不仅仅是桌面分辨率。
5. **组件和页面级别** -- 测试单个组件和完整页面布局。

## 项目结构

```
tests/
  visual/
    pages/
      homepage.visual.spec.ts
      login.visual.spec.ts
      dashboard.visual.spec.ts
    components/
      navigation.visual.spec.ts
      footer.visual.spec.ts
      card.visual.spec.ts
    responsive/
      homepage.responsive.spec.ts
      checkout.responsive.spec.ts
    utils/
      visual-helpers.ts
      mask-helpers.ts
  visual.config.ts
  snapshots/               <-- 基线截图（提交到 git）
    homepage-chromium.png
    login-chromium.png
playwright.config.ts
```

## 配置

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/visual',
  snapshotDir: './tests/snapshots',
  snapshotPathTemplate: '{snapshotDir}/{testFileDir}/{testFileName}-snapshots/{arg}{-projectName}{ext}',
  fullyParallel: true,
  retries: 0, // 可视化测试不应重试 -- flaky 视觉表示真实问题
  use: {
    baseURL: 'http://localhost:3000',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixels: 100,           // 最多允许 100 像素差异
      maxDiffPixelRatio: 0.01,      // 或总像素的 1%
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
      use: {
        ...devices['Desktop Chrome'],
        // 强制一致的字体渲染
        launchOptions: {
          args: ['--font-render-hinting=none', '--disable-skia-runtime-opts'],
        },
      },
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
      name: 'mobile-portrait',
      use: {
        ...devices['iPhone 13'],
      },
    },
    {
      name: 'tablet',
      use: {
        ...devices['iPad Pro 11'],
      },
    },
  ],
});
```

## 编写可视化测试

### 全页面截图

```typescript
import { test, expect } from '@playwright/test';

test.describe('Homepage Visual Tests', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
  });

  test('homepage should match baseline', async ({ page }) => {
    await expect(page).toHaveScreenshot('homepage-full.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  test('homepage above-the-fold should match baseline', async ({ page }) => {
    await expect(page).toHaveScreenshot('homepage-above-fold.png', {
      fullPage: false, // 仅视口
    });
  });

  test('homepage with content loaded should match baseline', async ({ page }) => {
    // 等待所有动态内容
    await page.getByRole('heading', { name: 'Featured Products' }).waitFor();
    await page.waitForSelector('img[src*="product"]', { state: 'visible' });

    await expect(page).toHaveScreenshot('homepage-loaded.png', {
      fullPage: true,
    });
  });
});
```

### 组件级截图

```typescript
test.describe('Navigation Visual Tests', () => {
  test('desktop navigation should match baseline', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Main' });

    await expect(nav).toHaveScreenshot('nav-desktop.png');
  });

  test('navigation hover state should match baseline', async ({ page }) => {
    await page.goto('/');
    const productsLink = page.getByRole('link', { name: 'Products' });

    await productsLink.hover();
    await expect(page.getByRole('navigation')).toHaveScreenshot('nav-hover.png');
  });

  test('navigation dropdown should match baseline', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Account' }).click();

    const dropdown = page.getByRole('menu');
    await expect(dropdown).toHaveScreenshot('nav-dropdown.png');
  });
});
```

### 基于状态的可视化测试

```typescript
test.describe('Form Visual States', () => {
  test('empty form should match baseline', async ({ page }) => {
    await page.goto('/register');
    await expect(page.locator('form')).toHaveScreenshot('form-empty.png');
  });

  test('form with validation errors should match baseline', async ({ page }) => {
    await page.goto('/register');
    await page.getByRole('button', { name: 'Submit' }).click();

    // 等待验证消息出现
    await page.getByText('Email is required').waitFor();

    await expect(page.locator('form')).toHaveScreenshot('form-errors.png');
  });

  test('form with filled data should match baseline', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('Name').fill('John Doe');
    await page.getByLabel('Email').fill('john@example.com');
    await page.getByLabel('Password').fill('SecurePass123!');

    await expect(page.locator('form')).toHaveScreenshot('form-filled.png');
  });

  test('disabled button state should match baseline', async ({ page }) => {
    await page.goto('/register');
    const button = page.getByRole('button', { name: 'Submit' });

    await expect(button).toHaveScreenshot('button-disabled.png');
  });
});
```

### 响应式可视化测试

```typescript
test.describe('Responsive Layout Tests', () => {
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

## 处理动态内容

### 遮罩动态元素

```typescript
test('dashboard should match baseline with dynamic content masked', async ({ page }) => {
  await page.goto('/dashboard');

  await expect(page).toHaveScreenshot('dashboard.png', {
    mask: [
      page.locator('[data-testid="current-time"]'),
      page.locator('[data-testid="user-avatar"]'),
      page.locator('[data-testid="notification-count"]'),
      page.locator('.chart-container'), // 动态图表数据
      page.locator('.ad-banner'),        // 第三方广告
    ],
    fullPage: true,
  });
});
```

### 替换动态内容

```typescript
test('profile page should match baseline', async ({ page }) => {
  await page.goto('/profile');

  // 用一致的值替换动态文本
  await page.evaluate(() => {
    // 替换时间戳
    document.querySelectorAll('[data-testid="timestamp"]').forEach((el) => {
      el.textContent = 'January 1, 2024';
    });

    // 替换用户特定数据
    const nameEl = document.querySelector('[data-testid="user-name"]');
    if (nameEl) nameEl.textContent = 'Test User';

    // 移除随机元素
    document.querySelectorAll('.random-recommendation').forEach((el) => el.remove());
  });

  await expect(page).toHaveScreenshot('profile-page.png', {
    fullPage: true,
  });
});
```

### 禁用动画

```typescript
test.beforeEach(async ({ page }) => {
  // 禁用所有 CSS 动画和过渡
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay: 0s !important;
        transition-duration: 0s !important;
        transition-delay: 0s !important;
        scroll-behavior: auto !important;
      }
    `,
  });
});
```

### 等待字体加载

```typescript
test('page with custom fonts should match baseline', async ({ page }) => {
  await page.goto('/');

  // 等待字体加载
  await page.evaluate(() => document.fonts.ready);

  // 额外的字体渲染等待
  await page.waitForTimeout(500); // 对于字体渲染是可以接受的

  await expect(page).toHaveScreenshot('page-with-fonts.png');
});
```

## 基线管理

### 更新基线

```bash
# 更新所有基线
npx playwright test --update-snapshots

# 更新特定测试的基线
npx playwright test tests/visual/homepage.visual.spec.ts --update-snapshots

# 更新特定项目的基线
npx playwright test --project=chromium --update-snapshots
```

### 基线工作流程

```markdown
## 基线更新流程

1. **有意变更：** 开发者有意修改 UI
2. **可视化测试失败：** CI 检测到视觉差异
3. **审查差异：** 下载制品，检查视觉差异
4. **批准变更：** 如果变更是有意的：
   a. 本地运行 `npx playwright test --update-snapshots`
   b. 提交更新的基线截图
   c. 推送并验证 CI 通过
5. **拒绝变更：** 如果变更是无意的：
   a. 还原导致视觉差异的代码更改
   b. 验证可视化测试再次通过
```

### 用于基线的 Git LFS

```bash
# 安装 Git LFS
git lfs install

# 追踪截图文件
git lfs track "tests/snapshots/**/*.png"
git lfs track "tests/snapshots/**/*.jpg"

# 添加 .gitattributes
git add .gitattributes
git commit -m "Track visual baselines with Git LFS"
```

## 视觉差异分析

### 理解差异输出

当可视化测试失败时，Playwright 生成三张图片：

```
test-results/
  homepage-visual-spec-ts/
    homepage-full-chromium-expected.png    <-- 基线（应该的样子）
    homepage-full-chromium-actual.png      <-- 当前（现在看起来的样子）
    homepage-full-chromium-diff.png        <-- 差异（突出显示的差异）
```

### 自定义差异阈值

```typescript
// 对品牌关键页面进行严格比较
test('brand logo should be pixel-perfect', async ({ page }) => {
  await page.goto('/');
  const logo = page.locator('[data-testid="brand-logo"]');
  await expect(logo).toHaveScreenshot('brand-logo.png', {
    maxDiffPixels: 0,        // 零容差
    threshold: 0,            // 精确像素匹配
  });
});

// 对内容密集页面进行宽松比较
test('blog listing visual check', async ({ page }) => {
  await page.goto('/blog');
  await expect(page).toHaveScreenshot('blog-listing.png', {
    maxDiffPixelRatio: 0.05, // 允许 5% 差异
    threshold: 0.3,          // 更多颜色容差
  });
});
```

## 暗模式和主题测试

```typescript
test.describe('Dark Mode Visual Tests', () => {
  test('homepage in dark mode', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/');

    await expect(page).toHaveScreenshot('homepage-dark.png', { fullPage: true });
  });

  test('homepage in light mode', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto('/');

    await expect(page).toHaveScreenshot('homepage-light.png', { fullPage: true });
  });

  test('reduced motion preference', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');

    // 验证没有动画可见
    await expect(page).toHaveScreenshot('homepage-reduced-motion.png');
  });
});
```

## CI 集成

### 用于可视化测试的 GitHub Actions

```yaml
visual-tests:
  name: Visual Regression Tests
  runs-on: ubuntu-latest
  timeout-minutes: 30
  container:
    image: mcr.microsoft.com/playwright:v1.42.0-jammy
  steps:
    - uses: actions/checkout@v4
      with:
        lfs: true  # 重要：获取 LFS 基线

    - uses: actions/setup-node@v4
      with:
        node-version: '20'
        cache: 'npm'

    - run: npm ci

    - name: Run Visual Tests
      run: npx playwright test tests/visual/

    - name: Upload Visual Diff
      if: failure()
      uses: actions/upload-artifact@v4
      with:
        name: visual-diffs
        path: |
          test-results/**/
        retention-days: 14

    - name: Comment PR with Visual Diff
      if: failure() && github.event_name == 'pull_request'
      uses: actions/github-script@v7
      with:
        script: |
          github.rest.issues.createComment({
            owner: context.repo.owner,
            repo: context.repo.repo,
            issue_number: context.issue.number,
            body: '## Visual Regression Detected\n\nVisual differences were found. Please download the artifacts to review the diffs.\n\n[View workflow run](${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }})'
          });
```

## 最佳实践

1. **禁用动画** -- CSS 动画导致非确定性截图。
2. **等待内容** -- 始终等待动态内容、图像和字体加载。
3. **使用确定性数据** -- 模拟 API 响应以确保一致的测试数据。
4. **遮罩动态区域** -- 覆盖时间戳、头像和第三方小部件。
5. **测试关键断点** -- 至少覆盖移动端、平板和桌面。
6. **设置合理的阈值** -- 太严格导致误报；太宽松会遗漏真实 bug。
7. **使用一致的环境** -- 在 Docker 容器中运行可视化测试以获得一致的渲染。
8. **仔细审查差异** -- 不是每个像素变化都是 bug；有些是预期的。
9. **版本化基线** -- 将基线提交到源代码控制（对大型仓库使用 Git LFS）。
10. **测试组件状态** -- 覆盖悬停、聚焦、激活、禁用、错误和加载状态。

## 应避免的反模式

1. **无动画控制** -- 动画使截图非确定性。
2. **使用实时数据测试** -- 真实 API 数据会变化，导致误报。
3. **零像素容差** -- 即使抗锯齿差异也会触发失败。
4. **只做全页面截图** -- 组件级截图捕获更具体的回归。
5. **忽略字体加载** -- 未加载的字体在截图中产生空白文本。
6. **不遮罩动态内容** -- 时间戳和计数器每次运行都会变化。
7. **只在本地运行可视化测试** -- 不同操作系统渲染字体不同。
8. **过多的可视化测试** -- 只为关键页面和组件维护基线。
9. **不审查失败** -- 不经审查自动更新基线会隐藏真实回归。
10. **缺少响应式测试** -- 仅桌面可视化测试会遗漏移动端布局 bug。
