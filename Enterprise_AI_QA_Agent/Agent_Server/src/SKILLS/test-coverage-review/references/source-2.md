---
name: Code Coverage Analysis
description: 使用 Istanbul/nyc 追踪测试覆盖率,生成报告并强制执行阈值
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit, code-quality]
frameworks: [jest]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 代码覆盖率分析

此技能使 AI 代理正确配置覆盖率收集(Istanbul 插桩或 V8 原生覆盖率)、设置使构建失败的阈值、阅读覆盖率报告以发现真正未测试的分支,并排除生成的或配置文件以使数字有意义。当用户询问"我们的覆盖率是多少"、需要在 CI 中设置覆盖率门禁,或项目中出现 `coverage/` 目录、`--coverage` 标志、`.nycrc` 或 `coverageThreshold` 时触发。

## 核心原则

1. **分支覆盖率才是最重要的数字。** 行覆盖率即使只有四个分支结果中的一个被执行,也标记该行为已覆盖。守卫子句 `if (a && b) return x;` 可以达到 100% 的行覆盖,但其四个分支结果中有三个未测试。
2. **覆盖率是一个差距检测器,不是质量分数。** 95% 的覆盖率加上没有断言的测试什么都证明不了。用覆盖率找到未测试的代码路径;用变异测试检查断言强度。
3. **阈值必须使构建失败。** 没人看的覆盖率报告只是装饰。用 `coverageThreshold`(Jest)、`thresholds`(Vitest) 或 `--check-coverage`(nyc/c8) 让 CI 在回归时变红。
4. **按当前现实设置阈值,然后逐步提高。** 将 90% 的全局门禁放到 60% 的代码库上会让团队删除这个门禁。从今天的数字减 1 开始,随着覆盖率提高逐步提高。
5. **测量 `all` 文件,不仅仅是导入的文件。** 默认情况下,某些工具只报告被测试触及的文件,因此完全未测试的模块是不可见的。启用 `all: true`(nyc)、`coverage.all`(Vitest)或广泛的 `collectCoverageFrom`(Jest)。
6. **明确排除生成的代码。** Protobuf 存根、GraphQL 代码生成、迁移和 `*.d.ts` 会随机膨胀或压低数字。在配置中排除它们,不要在生成的文件中散布 ignore 注释。

## 设置和模式

### 1. Jest: 收集、阈值和按目录门禁

```js
// jest.config.js
module.exports = {
  preset: 'ts-jest',
  collectCoverage: true,
  coverageProvider: 'v8',
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/__generated__/**',
    '!src/**/*.stories.tsx',
    '!src/test-utils/**',
  ],
  coverageReporters: ['text', 'lcov', 'json-summary'],
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 85,
      lines: 90,
      statements: 90,
    },
    // 金钱处理代码使用更严格的门禁
    './src/lib/payments/': {
      branches: 95,
      lines: 98,
    },
  },
};
```

```bash
npx jest --coverage --ci
# 如果任何阈值未达到,退出代码为 1;CI 自动失败
```

### 2. nyc (Istanbul) 用于 Mocha 或纯 Node 脚本

```json
{
  "all": true,
  "include": ["src/**/*.ts"],
  "exclude": ["**/*.spec.ts", "src/generated/**", "src/migrations/**"],
  "reporter": ["text", "html", "lcov"],
  "check-coverage": true,
  "branches": 80,
  "lines": 90,
  "functions": 85,
  "statements": 90
}
```

保存为 `.nycrc.json`,然后:

```bash
npm install --save-dev nyc
npx nyc mocha 'test/**/*.spec.ts'
npx nyc report --reporter=text-summary
```

### 3. c8: 零插桩的原生 V8 覆盖率

c8 读取 V8 的内置覆盖率,因此它与 Node 测试运行器配合使用,无需转译时插桩:

```bash
npm install --save-dev c8
npx c8 --all --src src --reporter=text --reporter=lcov \
  --lines 90 --branches 80 --check-coverage \
  node --test test/
```

### 4. Vitest: 内置 V8 覆盖率与阈值

```ts
// vitest.config.ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    coverage: {
      provider: 'v8',
      all: true,
      include: ['src/**/*.ts'],
      exclude: ['src/generated/**', 'src/**/*.test.ts', '**/*.config.ts'],
      reporter: ['text', 'lcov', 'json-summary'],
      thresholds: {
        lines: 90,
        branches: 80,
        functions: 85,
        statements: 90,
        // 如果覆盖率低于自动更新的值则失败
        autoUpdate: false,
      },
    },
  },
});
```

```bash
npx vitest run --coverage
```

### 5. 分支与行覆盖率,具体说明

```ts
// shipping.ts
export function shippingCost(country: string, total: number): number {
  if (country === 'US' && total > 50) return 0;
  return country === 'US' ? 5 : 15;
}

// shipping.test.ts -- 这个单一测试产生 100% 的 LINE 覆盖率
import { shippingCost } from './shipping';

it('ships free for large US orders', () => {
  expect(shippingCost('US', 100)).toBe(0);
});
// 但分支覆盖率显示:total <= 50 未测试,非美国未测试,
// 5 美元国内路径未测试。三个真实行为没有测试。
```

分支报告(`text` 报告器打印每个文件的 `% Branch`,HTML 报告高亮显示黄色 `I`/`E` 标记)是你找到这些问题的方式。

### 6. 诚实地忽略不可达代码

```ts
// Istanbul 插桩的运行器 (nyc, babel-plugin-istanbul)
/* istanbul ignore next -- @preserve defensive guard, unreachable after zod validation */
if (typeof input !== 'string') throw new TypeError('input must be a string');

// V8 基础运行器 (vitest --coverage.provider=v8, c8)
/* v8 ignore next 2 */
if (process.platform === 'win32') {
  pathSeparator = '\\';
}
```

每个 ignore 注释都需要一个原因后缀;没有理由的 ignore 是等待腐烂的覆盖率谎言。

### 7. CI 门禁加上 PR 摘要

```yaml
# .github/workflows/test.yml (节选)
- name: Test with coverage gate
  run: npx vitest run --coverage

- name: Print coverage summary to job log
  if: always()
  run: |
    pct_lines=$(jq -r '.total.lines.pct' coverage/coverage-summary.json)
    pct_branches=$(jq -r '.total.branches.pct' coverage/coverage-summary.json)
    echo "### Coverage: ${pct_lines}% lines / ${pct_branches}% branches" >> "$GITHUB_STEP_SUMMARY"

- name: Upload lcov for review tooling
  uses: actions/upload-artifact@v4
  with:
    name: lcov-report
    path: coverage/lcov.info
```

## 最佳实践

- 为遗留代码编写测试时,查看 HTML 报告(`coverage/index.html`);红/黄高亮比阅读源代码更快找到未测试的分支。
- 在 monorepo 中按包追踪覆盖率趋势,而不是用一个混合的全局数字(会平均掉问题)。
- 为速度使用 `json-summary` 总数,在编辑器和显示差异中逐行覆盖率的审查工具中保留 `lcov` 输出。
- TypeScript 项目优先使用 V8 提供者;Istanbul 对转译输出的插桩可能错误地将分支归因于源映射。
- 删除死代码而不是忽略它;对可达代码的 `istanbul ignore` 是有收据的技术债务。
- 对于 bug 修复,检查覆盖率差异:修复的行必须被新的回归测试覆盖。

## 反模式

- 追求 100%:最后几点通常为 getter 和日志分支购买测试,而真正的风险存在于未测试的集成接缝中。
- 编写不进行任何断言纯执行代码的测试,纯粹为了移动数字。变异测试会立即暴露这些。
- 因为"难以测试"而从覆盖率中排除文件 - 这是仓库中最高风险的文件。
- 全局阈值如此之低(40%),永远不会触发;不能失败的门禁教会团队忽略它们。
- 只在单元测试上测量覆盖率,而大多数行为由集成测试执行;在判断之前合并报告(`nyc merge`,`--coverage.reportsDirectory` 每套件加 `lcov` 合并)。
- 让 `coverage/` 被提交;添加到 `.gitignore`。

## 何时触发此技能

- 用户询问"我们的测试覆盖率是多少"、"添加覆盖率门禁"、"为什么覆盖率在下降"或"强制执行 80% 覆盖率"。
- PR 向项目添加 `--coverage`、`coverageThreshold`、`.nycrc`、`c8` 或 `@vitest/coverage-v8`。
- CI 需要与 lines/branches/functions 绑定的质量门禁,或者在覆盖率推送后需要逐步提高阈值。
- 生成的代码(GraphQL codegen、protobuf、ORM 迁移)正在扭曲数字,需要原则性的排除。
- 当用户怀疑高覆盖率数字但断言薄弱时,将其与变异测试配对。