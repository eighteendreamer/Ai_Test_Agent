---
name: Flaky Test Quarantine
description: 识别、隔离和管理不稳定的测试，构建稳定的测试套件
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [code-quality, unit, integration]
frameworks: [jest, playwright]
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 不稳定测试隔离

您是一位专注于测试稳定性优化的 QA 工程师。当用户要求您处理不稳定测试（flaky tests）时，请遵循这些详细说明。

## 核心原则

1. **识别 flaky 测试** -- 使用统计方法检测不稳定的测试。
2. **隔离和分析** -- 将 flaky 测试隔离，识别根本原因。
3. **修复而非跳过** -- 修复 flaky 测试而不是简单地跳过。
4. **预防为主** -- 建立机制防止新的 flaky 测试进入。
5. **监控趋势** -- 跟踪 flaky 测试的历史和趋势。

## 不稳定测试类型

### 1. 时间依赖
- 硬编码的 sleep 或超时
- 竞态条件
- 异步操作未正确等待

### 2. 环境依赖
- 日期/时间相关测试
- 随机数据依赖
- 并发冲突

### 3. 外部依赖
- 网络请求不稳定
- 第三方服务故障
- 数据库连接问题

### 4. 测试隔离问题
- 共享状态
- 测试顺序依赖
- 资源竞争

## 项目结构

```
flaky-test-manager/
├── src/
│   ├── detectors/
│   │   ├── flaky-detector.ts
│   │   └── pattern-analyzer.ts
│   ├── quarantine/
│   │   ├── quarantined-tests.json
│   │   └── quarantine-manager.ts
│   ├── reporters/
│   │   └── flaky-report.ts
│   └── runners/
│       └── retry-runner.ts
├── tests/
├── flaky.config.ts
└── package.json
```

## Flaky 检测器

### 统计检测

```typescript
// src/detectors/flaky-detector.ts
interface TestResult {
  testId: string;
  testName: string;
  passed: boolean;
  duration: number;
  timestamp: number;
  retries: number;
}

interface FlakyAnalysis {
  testId: string;
  testName: string;
  totalRuns: number;
  passCount: number;
  failCount: number;
  passRate: number;
  flakinessScore: number;  // 0-1, 越高越不稳定
  patterns: string[];
  recommendation: string;
}

export class FlakyDetector {
  private results: Map<string, TestResult[]> = new Map();

  recordResult(result: TestResult): void {
    if (!this.results.has(result.testId)) {
      this.results.set(result.testId, []);
    }
    this.results.get(result.testId)!.push(result);
  }

  analyzeFlakiness(minRuns = 5): FlakyAnalysis[] {
    const analyses: FlakyAnalysis[] = [];

    for (const [testId, results] of this.results) {
      if (results.length < minRuns) continue;

      const passCount = results.filter(r => r.passed).length;
      const failCount = results.filter(r => !r.passed).length;
      const passRate = passCount / results.length;

      // Flakiness score: 0 = 完全稳定, 1 = 完全不稳定
      // 考虑失败频率和失败模式
      const flakinessScore = this.calculateFlakinessScore(results);

      if (flakinessScore > 0.2) {  // 超过 20% 的不确定性
        analyses.push({
          testId,
          testName: results[0].testName,
          totalRuns: results.length,
          passCount,
          failCount,
          passRate,
          flakinessScore,
          patterns: this.detectPatterns(results),
          recommendation: this.getRecommendation(flakinessScore, results),
        });
      }
    }

    return analyses.sort((a, b) => b.flakinessScore - a.flakinessScore);
  }

  private calculateFlakinessScore(results: TestResult[]): number {
    const passResults = results.filter(r => r.passed);
    const failResults = results.filter(r => !r.passed);

    // 基础分数：失败率
    let score = failResults.length / results.length;

    // 检查连续失败（更严重的问题）
    let maxConsecutiveFails = 0;
    let currentConsecutiveFails = 0;

    for (const result of results) {
      if (!result.passed) {
        currentConsecutiveFails++;
        maxConsecutiveFails = Math.max(maxConsecutiveFails, currentConsecutiveFails);
      } else {
        currentConsecutiveFails = 0;
      }
    }

    // 如果有连续失败，增加分数
    if (maxConsecutiveFails >= 2) {
      score += maxConsecutiveFails * 0.1;
    }

    // 检查重试后是否通过（典型的 flaky 行为）
    const retriedAndPassed = results.filter(r => r.retries > 0 && r.passed).length;
    if (retriedAndPassed > 0) {
      score += 0.2;  // 表明测试需要重试才能通过
    }

    return Math.min(score, 1.0);
  }

  private detectPatterns(results: TestResult[]): string[] {
    const patterns: string[] = [];

    // 检查时间模式
    const hourOfDay = results.filter(r => !r.passed).map(r => {
      const date = new Date(r.timestamp);
      return date.getHours();
    });

    if (hourOfDay.length >= 2) {
      const avgHour = hourOfDay.reduce((a, b) => a + b) / hourOfDay.length;
      if (avgHour < 6 || avgHour > 22) {
        patterns.push('time-of-day-correlated');
      }
    }

    // 检查持续时间模式
    const failedDurations = results.filter(r => !r.passed).map(r => r.duration);
    const passedDurations = results.filter(r => r.passed).map(r => r.duration);

    if (failedDurations.length > 0 && passedDurations.length > 0) {
      const avgFailedDuration = failedDurations.reduce((a, b) => a + b) / failedDurations.length;
      const avgPassedDuration = passedDurations.reduce((a, b) => a + b) / passedDurations.length;

      if (avgFailedDuration > avgPassedDuration * 1.5) {
        patterns.push('slow-when-failing');
      }
    }

    // 检查重试模式
    if (results.some(r => r.retries > 0 && r.passed)) {
      patterns.push('requires-retry-to-pass');
    }

    return patterns;
  }

  private getRecommendation(score: number, results: TestResult[]): string {
    if (score >= 0.8) {
      return 'Critical: Quarantine immediately and fix or remove';
    }
    if (score >= 0.5) {
      return 'High: Investigate and fix flaky behavior';
    }
    if (score >= 0.3) {
      return 'Medium: Add retry logic and monitor closely';
    }
    return 'Low: Monitor and address if trend worsens';
  }
}
```

### 模式分析

```typescript
// src/detectors/pattern-analyzer.ts
interface FlakyPattern {
  type: 'timing' | 'network' | 'state' | 'random' | 'concurrent';
  severity: 'critical' | 'high' | 'medium' | 'low';
  indicators: string[];
  causes: string[];
  fixes: string[];
}

export class PatternAnalyzer {

  analyzeCode(testCode: string): FlakyPattern[] {
    const patterns: FlakyPattern[] = [];

    // 检测 timing 问题
    if (this.hasHardcodedSleep(testCode)) {
      patterns.push({
        type: 'timing',
        severity: 'high',
        indicators: ['setTimeout', 'Thread.sleep', 'sleep(', 'await page.waitForTimeout'],
        causes: [
          'Hardcoded delays do not account for variable system load',
          'May be insufficient on slower machines or under load',
        ],
        fixes: [
          'Use dynamic waits with condition checks',
          'Replace sleep with explicit waits for element state',
        ],
      });
    }

    // 检测网络问题
    if (this.hasUnreliableNetworkCalls(testCode)) {
      patterns.push({
        type: 'network',
        severity: 'high',
        indicators: ['fetch(', 'axios', 'http.get', 'page.goto('],
        causes: [
          'Network requests may timeout or fail under poor conditions',
          'No retry logic for transient failures',
        ],
        fixes: [
          'Add retry logic with exponential backoff',
          'Implement circuit breaker pattern',
        ],
      });
    }

    // 检测状态问题
    if (this.hasSharedState(testCode)) {
      patterns.push({
        type: 'state',
        severity: 'critical',
        indicators: ['global.', 'beforeAll', 'let ', 'shared'],
        causes: [
          'Tests sharing mutable state may interfere with each other',
          'Order-dependent failures indicate state leakage',
        ],
        fixes: [
          'Use test isolation - create fresh state per test',
          'Avoid global variables in test setup',
        ],
      });
    }

    // 检测随机数据问题
    if (this.hasRandomData(testCode)) {
      patterns.push({
        type: 'random',
        severity: 'medium',
        indicators: ['Math.random', 'faker.', 'Date.now()'],
        causes: [
          'Tests using random data may produce inconsistent results',
          'Boundary conditions may not be consistently tested',
        ],
        fixes: [
          'Seed random generators for reproducibility',
          'Use deterministic test data factories',
        ],
      });
    }

    return patterns;
  }

  private hasHardcodedSleep(code: string): boolean {
    const sleepPatterns = [
      /setTimeout\s*\(\s*\w+\s*,\s*\d+\s*\)/,
      /Thread\.sleep\s*\(\s*\d+\s*\)/,
      /sleep\s*\(\s*\d+\s*\)/,
      /page\.waitForTimeout\s*\(\s*\d+\s*\)/,
      /await\s+\w+\.sleep\s*\(\s*\d+\s*\)/,
    ];
    return sleepPatterns.some(p => p.test(code));
  }

  private hasUnreliableNetworkCalls(code: string): boolean {
    const networkPatterns = [
      /fetch\s*\(/,
      /axios\.\w+\(/,
      /http\.get\s*\(/,
      /page\.goto\s*\(/,
      /request\s*\(/,
    ];
    return networkPatterns.some(p => p.test(code));
  }

  private hasSharedState(code: string): boolean {
    return /^\s*let\s+\w+\s*=/m.test(code) ||
           (code.includes('beforeAll') && code.includes('global.'));
  }

  private hasRandomData(code: string): boolean {
    return /Math\.random\s*\(\s*\)/.test(code) ||
           /faker\.\w+/.test(code) ||
           /Date\.now\s*\(\s*\)/.test(code);
  }
}
```

## 隔离管理器

```typescript
// src/quarantine/quarantine-manager.ts
import * as fs from 'fs';

interface QuarantinedTest {
  testId: string;
  testName: string;
  quarantinedAt: string;
  quarantinedBy: string;
  reason: string;
  lastRun?: string;
  passCountSinceQuarantine?: number;
  failCountSinceQuarantine?: number;
}

export class QuarantineManager {
  private quarantineFile: string;
  private quarantinedTests: Map<string, QuarantinedTest> = new Map();

  constructor(quarantineFile = './quarantine/quarantined-tests.json') {
    this.quarantineFile = quarantineFile;
    this.load();
  }

  private load(): void {
    try {
      if (fs.existsSync(this.quarantineFile)) {
        const data = JSON.parse(fs.readFileSync(this.quarantineFile, 'utf-8'));
        for (const test of data.quarantinedTests || []) {
          this.quarantinedTests.set(test.testId, test);
        }
      }
    } catch (e) {
      console.error('Failed to load quarantine file:', e);
    }
  }

  private save(): void {
    const dir = require('path').dirname(this.quarantineFile);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.writeFileSync(
      this.quarantineFile,
      JSON.stringify({
        updatedAt: new Date().toISOString(),
        quarantinedTests: Array.from(this.quarantinedTests.values()),
      }, null, 2)
    );
  }

  quarantine(testId: string, testName: string, reason: string): void {
    this.quarantinedTests.set(testId, {
      testId,
      testName,
      quarantinedAt: new Date().toISOString(),
      quarantinedBy: process.env.USER || 'unknown',
      reason,
    });
    this.save();
  }

  release(testId: string): void {
    this.quarantinedTests.delete(testId);
    this.save();
  }

  isQuarantined(testId: string): boolean {
    return this.quarantinedTests.has(testId);
  }

  getQuarantinedTests(): QuarantinedTest[] {
    return Array.from(this.quarantinedTests.values());
  }

  updateRunResult(testId: string, passed: boolean): void {
    const test = this.quarantinedTests.get(testId);
    if (test) {
      if (passed) {
        test.passCountSinceQuarantine = (test.passCountSinceQuarantine || 0) + 1;
      } else {
        test.failCountSinceQuarantine = (test.failCountSinceQuarantine || 0) + 1;
      }
      test.lastRun = new Date().toISOString();
      this.save();
    }
  }

  shouldAutoRelease(testId: string, requiredPasses = 5): boolean {
    const test = this.quarantinedTests.get(testId);
    if (!test) return false;

    return (test.passCountSinceQuarantine || 0) >= requiredPasses;
  }
}
```

## 自动重试运行器

```typescript
// src/runners/retry-runner.ts
import { execSync } from 'child_process';

interface RetryOptions {
  maxRetries: number;
  retryDelay: number;
  passOnRetry?: boolean;  // 重试后通过是否算通过
}

interface TestRunResult {
  testId: string;
  passed: boolean;
  attempts: number;
  duration: number;
  error?: string;
}

export class RetryRunner {
  private options: RetryOptions;

  constructor(options: RetryOptions) {
    this.options = options;
  }

  async runTest(testCommand: string): Promise<TestRunResult> {
    let lastError: Error | undefined;
    let attempts = 0;

    while (attempts < this.options.maxRetries + 1) {
      attempts++;
      const startTime = Date.now();

      try {
        const result = this.runCommand(testCommand);

        if (result.passed) {
          return {
            testId: testCommand,
            passed: true,
            attempts,
            duration: Date.now() - startTime,
          };
        }

        lastError = new Error(`Test failed: ${result.output}`);
      } catch (e) {
        lastError = e as Error;
      }

      if (attempts <= this.options.maxRetries) {
        await this.sleep(this.options.retryDelay * attempts);  // 指数退避
      }
    }

    return {
      testId: testCommand,
      passed: this.options.passOnRetry === true && attempts > 1,
      attempts,
      duration: 0,
      error: lastError?.message,
    };
  }

  private runCommand(command: string): { passed: boolean; output: string } {
    try {
      const output = execSync(command, { encoding: 'utf-8', stdio: 'pipe' });
      return { passed: true, output };
    } catch (e) {
      return { passed: false, output: (e as Error).message };
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
```

## CI/CD 集成

```yaml
# .github/workflows/flaky-detection.yml
name: Flaky Test Detection
on:
  schedule:
    - cron: '0 */6 * * *'  # 每 6 小时运行一次
  workflow_dispatch:

jobs:
  detect-flaky:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run tests with tracking
        run: npm run test:with-tracking

      - name: Analyze flaky tests
        run: npx ts-node src/detectors/analyze-flaky.ts

      - name: Update quarantine list
        run: npx ts-node src/quarantine/update-quarantine.ts

      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: flaky-test-report
          path: flaky-report-*.json

      - name: Post to Slack
        if: failure()
        uses: slackapi/slack-github-action@v1
        with:
          payload: |
            {
              "text": "Flaky Test Report Available",
              "attachments": [{"text": "View the report at ${{ steps.report.outputs.url }}"}]
            }
```

## Jest 集成

```typescript
// jest.flaky-handler.js
module.exports = {
  // 在测试运行前后记录结果
  setup: async () => {
    const fs = require('fs');
    const results = [];

    global.__FLAKY_RECORD = (testResult) => {
      results.push({
        ...testResult,
        timestamp: Date.now(),
      });
    };

    process.on('exit', () => {
      fs.writeFileSync(
        './test-results/tracking.json',
        JSON.stringify(results, null, 2)
      );
    });
  },

  // 自定义测试 runner
  testRunner: 'jest-circus/runner',

  // 失败时自动重试
  globals: {
    'ts-jest': {
      diagnostics: false,
    },
  },
};
```

## Playwright 集成

```typescript
// playwright/flaky-handler.ts
import { test, expect } from '@playwright/test';

export const flakyTestHandler = {
  retryCount: new Map<string, number>(),

  async handleFlakyTest(
    testFn: () => Promise<void>,
    testId: string,
    maxRetries = 2
  ): Promise<void> {
    let lastError: Error | undefined;

    for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
      try {
        await testFn();
        this.retryCount.set(testId, 0);  // 重置计数
        return;
      } catch (e) {
        lastError = e as Error;
        const current = this.retryCount.get(testId) || 0;
        this.retryCount.set(testId, current + 1);

        if (attempt <= maxRetries) {
          console.log(`Retrying flaky test ${testId} (attempt ${attempt}/${maxRetries})`);
          await new Promise(r => setTimeout(r, 1000 * attempt));  // 退避
        }
      }
    }

    throw lastError!;
  },
};

// 使用示例
test('should handle flaky API call', async ({ request }) => {
  await flakyTestHandler.handleFlakyTest(
    async () => {
      const response = await request.get('https://api.example.com/data');
      expect(response.ok()).toBeTruthy();
    },
    'api-data-fetch',
    3
  );
});
```

## 报告生成

```typescript
// src/reporters/flaky-report.ts
import * as fs from 'fs';

interface FlakyReport {
  generatedAt: string;
  summary: {
    totalTestsTracked: number;
    flakyTests: number;
    healthyTests: number;
    flakinessRate: number;
  };
  flakyTests: Array<{
    testId: string;
    testName: string;
    passRate: number;
    flakinessScore: number;
    patterns: string[];
    recommendation: string;
  }>;
  quarantineStatus: {
    totalQuarantined: number;
    autoReleased: number;
    pendingFix: number;
  };
}

export function generateReport(
  analyses: any[],
  quarantineManager: any
): FlakyReport {
  const report: FlakyReport = {
    generatedAt: new Date().toISOString(),
    summary: {
      totalTestsTracked: analyses.length,
      flakyTests: analyses.filter(a => a.flakinessScore > 0.2).length,
      healthyTests: analyses.filter(a => a.flakinessScore <= 0.2).length,
      flakinessRate: 0,
    },
    flakyTests: analyses
      .filter(a => a.flakinessScore > 0.2)
      .sort((a, b) => b.flakinessScore - a.flakinessScore),
    quarantineStatus: {
      totalQuarantined: quarantineManager.getQuarantinedTests().length,
      autoReleased: 0,
      pendingFix: 0,
    },
  };

  report.summary.flakinessRate =
    report.summary.totalTestsTracked > 0
      ? report.flakyTests.length / report.summary.totalTestsTracked
      : 0;

  return report;
}

export function printReport(report: FlakyReport): void {
  console.log('\n=== Flaky Test Report ===');
  console.log(`Generated: ${report.generatedAt}`);
  console.log(`\nSummary:`);
  console.log(`  Total Tests: ${report.summary.totalTestsTracked}`);
  console.log(`  Flaky Tests: ${report.summary.flakyTests}`);
  console.log(`  Healthy Tests: ${report.summary.healthyTests}`);
  console.log(`  Flakiness Rate: ${(report.summary.flakinessRate * 100).toFixed(1)}%`);

  if (report.flakyTests.length > 0) {
    console.log(`\nFlaky Tests:`);
    for (const test of report.flakyTests.slice(0, 10)) {
      console.log(`  - ${test.testName} (${(test.flakinessScore * 100).toFixed(0)}% flaky)`);
      console.log(`    ${test.recommendation}`);
    }
  }

  console.log(`\nQuarantine Status:`);
  console.log(`  Total Quarantined: ${report.quarantineStatus.totalQuarantined}`);
}
```

## 最佳实践

1. **识别 flaky 而非跳过** -- 找到根本原因并修复。
2. **使用统计方法** -- 多次运行检测真正的 flaky 测试。
3. **隔离 flaky 测试** -- 防止影响其他测试。
4. **跟踪趋势** -- 监控 flaky 测试随时间的变化。
5. **自动重试有限制** -- 重试不应该掩盖真正的问题。
6. **记录每次运行** -- 保留历史数据用于分析。
7. **分类处理** -- 根据 flakiness score 采用不同策略。
8. **预防措施** -- 代码审查时注意潜在的 flaky 模式。

## 应避免的反模式

1. **盲目禁用 flaky 测试** -- 应该修复而不是禁用。
2. **无限重试** -- 重试掩盖问题，不解决根本原因。
3. **忽视 flaky 测试** -- 小的 flakiness 也会影响 CI 可靠性。
4. **不记录 flaky 测试** -- 不知道哪些测试需要关注。
5. **测试顺序依赖** -- 测试应该能任意顺序运行。
6. **共享全局状态** -- 每个测试需要独立的测试数据。
7. **硬编码超时** -- 使用动态等待条件。
8. **不监控 flaky 趋势** -- 新的 flaky 测试需要立即发现。