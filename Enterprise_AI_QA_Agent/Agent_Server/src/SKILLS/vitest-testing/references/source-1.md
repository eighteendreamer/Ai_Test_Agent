---
name: Vitest Unit Testing
description: 快速的 Vite 原生单元测试,具有 Jest 兼容的 API 和 ESM 支持
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit]
frameworks: [jest]
languages: [typescript, javascript]
domains: [web]
info: vip.hctestedu.com
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Vitest 测试

此技能使 AI 代理编写和配置 Vitest 测试套件:正确的 `vitest.config.ts`、使用 `vi.mock` 和 `vi.hoisted` escape hatch 的模块模拟、间谍和假计时器、内联快照、V8 覆盖率门禁,以及 monorepo 的 `projects` 配置。在任何基于 Vite 的项目上、在 `devDependencies` 中有 `vitest` 的任何仓库上,或从 Jest 迁移时触发。

## 核心原则

1. **Vitest 重用你的 Vite 配置 — 不要复制解析逻辑。** 别名、插件和来自 `vite.config.ts` 的转换自动应用于测试。单独的 Babel/转换设置是一种 Jest 习惯;放弃它。
2. **`vi.mock` 被提升;工厂变量不是。** mock 工厂在导入之前运行,因此在其中引用顶级变量会抛出。使用 `vi.hoisted()` 当工厂需要共享句柄时。
3. **优先使用通过参数注入的 `vi.fn` 而不是整个模块的 `vi.mock`。** 模块模拟是大锤;依赖注入保持测试诚实和重构安全。
4. **内联快照而不是文件快照用于小值。** `toMatchInlineSnapshot` 将期望放在测试中,审阅者可以看到;文件快照被盲目地 `--update`。
5. **覆盖率阈值存在于配置中并使运行失败。** 没人门禁的覆盖率报告只是壁纸。门禁 `lines`、`functions` 和 `branches` — 分支覆盖率是 bugs 隐藏的地方。
6. **除非渲染 DOM,否则使用默认 `node` 环境。** `jsdom`/`happy-dom` 每个文件都要支付启动时间;使用 docblock 按文件设置,而不是全局设置。

## 设置

```bash
npm install --save-dev vitest @vitest/coverage-v8
```

```typescript
// vitest.config.ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: false, // 显式导入;保持文件可 grep 和 TS 干净
    environment: 'node',
    include: ['src/**/*.test.ts'],
    setupFiles: ['./test/setup.ts'],
    restoreMocks: true, // 在测试之间撤销间谍实现
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'html'],
      include: ['src/**'],
      exclude: ['src/**/*.test.ts', 'src/types/**', 'src/main.ts'],
      thresholds: {
        lines: 85,
        functions: 85,
        branches: 75,
        statements: 85,
      },
    },
  },
});
```

```json
// package.json scripts
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  }
}
```

监视模式是默认的 `vitest` 命令,只重新运行被更改模块图触及的测试 — 在开发时保持运行。

## 模式

### vi.fn、间谍和依赖注入

```typescript
// src/notifier.ts
export type SendEmail = (to: string, subject: string) => Promise<void>;

export async function notifyOnFailure(
  jobName: string,
  failures: number,
  sendEmail: SendEmail,
): Promise<boolean> {
  if (failures === 0) return false;
  await sendEmail('oncall@example.com', `${jobName} failed ${failures} times`);
  return true;
}
```

```typescript
// src/notifier.test.ts
import { describe, expect, it, vi } from 'vitest';
import { notifyOnFailure } from './notifier';

describe('notifyOnFailure', () => {
  it('emails oncall with the failure count in the subject', async () => {
    const sendEmail = vi.fn().mockResolvedValue(undefined);

    const sent = await notifyOnFailure('nightly-sync', 3, sendEmail);

    expect(sent).toBe(true);
    expect(sendEmail).toHaveBeenCalledExactlyOnceWith(
      'oncall@example.com',
      'nightly-sync failed 3 times',
    );
  });

  it('stays silent when there are no failures', async () => {
    const sendEmail = vi.fn();
    await expect(notifyOnFailure('nightly-sync', 0, sendEmail)).resolves.toBe(false);
    expect(sendEmail).not.toHaveBeenCalled();
  });
});
```

### vi.mock 与 vi.hoisted(提升陷阱,解决)

```typescript
import { beforeEach, expect, it, vi } from 'vitest';
import { getInvoice } from './invoice-service';

// 工厂在导入之上提升 — 通过 vi.hoisted 捕获句柄
const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));

vi.mock('./billing-client', () => ({
  fetchInvoice: fetchMock,
}));

beforeEach(() => {
  fetchMock.mockReset();
});

it('retries once on a 503 from the billing client', async () => {
  fetchMock
    .mockRejectedValueOnce(new Error('503 Service Unavailable'))
    .mockResolvedValueOnce({ id: 'inv_42', total: 1999 });

  const invoice = await getInvoice('inv_42');

  expect(invoice.total).toBe(1999);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
```

部分模拟保留模块的其余部分为真实:

```typescript
vi.mock('./config', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./config')>();
  return { ...actual, isFeatureEnabled: vi.fn().mockReturnValue(true) };
});
```

### 假计时器

```typescript
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { debounce } from './debounce';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('fires once after the trailing edge of 300ms', () => {
  const fn = vi.fn();
  const debounced = debounce(fn, 300);

  debounced();
  debounced();
  vi.advanceTimersByTime(299);
  expect(fn).not.toHaveBeenCalled();

  vi.advanceTimersByTime(1);
  expect(fn).toHaveBeenCalledTimes(1);
});
```

### 快照和错误断言

```typescript
import { expect, it } from 'vitest';
import { formatReport, parseDuration } from './report';

it('formats a compact summary line', () => {
  expect(formatReport({ passed: 12, failed: 1, skipped: 2 })).toMatchInlineSnapshot(
    `"12 passed | 1 failed | 2 skipped"`,
  );
});

it('throws a typed error on malformed durations', () => {
  expect(() => parseDuration('5parsecs')).toThrowErrorMatchingInlineSnapshot(
    `[RangeError: unknown duration unit "parsecs"]`,
  );
});
```

### Monorepo 项目和源码内测试

```typescript
// vitest.config.ts at the monorepo root
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    projects: [
      { test: { name: 'shared', root: './packages/shared', environment: 'node' } },
      { test: { name: 'web', root: './packages/web', environment: 'jsdom' } },
    ],
  },
});
```

```bash
vitest run --project shared   # one package
vitest run                    # everything, parallelized
```

用于小型内部工具的源码内测试(通过 `define: { 'import.meta.vitest': 'undefined' }` 从生产构建中剥离):

```typescript
// src/slug.ts
export function slugify(input: string): string {
  return input.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

if (import.meta.vitest) {
  const { expect, it } = import.meta.vitest;
  it('collapses punctuation runs into single hyphens', () => {
    expect(slugify('  Hello, World! ')).toBe('hello-world');
  });
}
```

## 最佳实践

- 全局设置 `restoreMocks: true` 而不是在每个 `afterEach` 中散布 `vi.restoreAllMocks()`。
- 在 pre-commit 钩子中使用 `vitest related src/pricing.ts` 只运行触及更改文件的测试。
- 使用 `await expect(p).rejects.toThrow(...)` 断言 promise 拒绝 — 不带 await 的裸 `expect(p).rejects` 可能在结算之前通过。
- 当只有一些测试需要 DOM 时,按文件固定环境:在文件顶部使用 `// @vitest-environment jsdom`。
- 优先使用 `test.each` 用于输入表而不是复制粘贴的测试;每一行报告为自己的案例。
- 从 Jest 迁移时:`vi` 替换 `jest`,mock 工厂必须显式返回模块形状(不是 automock),`jest.requireActual` 变成 `importOriginal`。

## 反模式

- **在 `vi.mock` 工厂内引用顶级变量。** 提升使它们在工厂时为 `undefined` — 错误消息提到提升,相信它。使用 `vi.hoisted`。
- **`globals: true` 加上缺少 TS 类型。** 如果启用 globals,将 `"types": ["vitest/globals"]` 添加到 tsconfig,否则导入在编辑器中静默破坏。
- **对完整 API 响应的大型 `.toMatchSnapshot()`。** 百行快照被橡皮图章更新。快照小、稳定切片;使用匹配器断言动态字段。
- **`vi.mock` 被测模块。** 你最终测试你自己的 mock。模拟依赖,而不是主题。
- **忘记 `vi.useRealTimers()` 清理** — 假计时器泄漏到后续测试并挂起任何真正等待的东西。
- **在 `test.alias` 内部重新实现 Vite 别名** 而它们已经存在于 `vite.config.ts`;两者之间的漂移只在测试中破坏解析。

## 何时触发此技能

- 项目基于 Vite 或 `devDependencies` 中有 `vitest`。
- 用户要求添加单元测试、模拟模块、假计时器或在 TS/JS 仓库中快照输出,而没有 Jest。
- 设置覆盖率门禁或带有包特定环境的 monorepo 测试项目。
- 将 Jest 套件迁移到 Vitest(jest → vi API 映射,mock 工厂差异)。
- 测试因提升错误、环境不匹配或泄漏 mock 而失败 — 经典的 Vitest 错误配置。