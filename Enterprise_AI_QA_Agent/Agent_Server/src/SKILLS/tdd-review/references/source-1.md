---
name: TDD Red-Green-Refactor
description: 测试驱动开发工作流程模式，包含红-绿-重构循环和重构技术
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [tdd, unit]
info: vip.hctestedu.com
frameworks: [jest]
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# TDD 模式

这个技能使 AI 代理以测试优先的方式开发功能：编写一个完全失败的测试，看着它因正确的原因失败，编写最小化的生产代码使其通过，然后在绿色时重构。它强制执行大多数"TDD"会话跳过的纪律——永远不在有失败测试要求的情况下编写生产代码。当用户要求 TDD、测试优先开发、或在已有测试运行器连接好的代码库中实现新逻辑时，触发此技能。

## 核心原则

1. **一次一个失败的测试。** 不是十个待处理测试的套件——一个。多个红色测试意味着你在脑海中设计而不是让测试驱动。
2. **在看它失败后再让它通过。** 一个你从未见过红色的测试可能空洞地通过（错误的断言、测试 mock、输入错误）。红色步骤验证测试本身。
3. **编写最小化通过的代码——即使很傻。** 返回硬编码值是合法的；下一个测试强制泛化。"Fake it till you make it。"
4. **只在绿色时重构，重构两边。** 生产代码和测试代码同样腐坏。测试中的重复是设计气味，正如在 `src/` 中一样。
5. **通过公共 API 测试行为，绝不测试内部。** 如果重命名一个私有方法破坏了测试，测试就与实现耦合了，将会抵制每次重构而不是启用它。
6. **将测试命名为行为句子。** `rejects expired coupons at the boundary minute` 告诉下一个读者规则；`test_coupon_3` 什么也没说。
7. **循环是分钟级，不是小时级。** 如果你已经红色 20 分钟了，步骤太大了——删除，采取更小的咬。

## 工作流程：Jest（TypeScript）中的一个完整周期

功能：一个 `PriceCalculator`，应用分层批量折扣。

**红色 — 编写最小的失败测试：**

```typescript
// src/price-calculator.test.ts
import { describe, expect, it } from '@jest/globals';
import { calculateTotal } from './price-calculator';

describe('calculateTotal', () => {
  it('在 10 单位以下无折扣时返回单价乘以数量', () => {
    // Arrange
    const unitPrice = 4.0;
    const quantity = 3;

    // Act
    const total = calculateTotal(unitPrice, quantity);

    // Assert
    expect(total).toBe(12.0);
  });
});
```

```bash
npx jest price-calculator
# FAIL — Cannot find module './price-calculator'  <- 因正确的原因失败
```

**绿色 — 最小化代码，不推测：**

```typescript
// src/price-calculator.ts
export function calculateTotal(unitPrice: number, quantity: number): number {
  return unitPrice * quantity;
}
```

**再次红色 — 下一个测试强制折扣规则：**

```typescript
it('在 10 单位或以上应用 10% 折扣', () => {
  expect(calculateTotal(4.0, 10)).toBe(36.0); // 40 - 10%
});

it('在 50 单位或以上应用 20% 折扣', () => {
  expect(calculateTotal(2.0, 50)).toBe(80.0); // 100 - 20%
});
```

**绿色：**

```typescript
export function calculateTotal(unitPrice: number, quantity: number): number {
  const subtotal = unitPrice * quantity;
  if (quantity >= 50) return subtotal * 0.8;
  if (quantity >= 10) return subtotal * 0.9;
  return subtotal;
}
```

**重构 — 在绿色下，提取层级表：**

```typescript
const DISCOUNT_TIERS: ReadonlyArray<{ minQty: number; multiplier: number }> = [
  { minQty: 50, multiplier: 0.8 },
  { minQty: 10, multiplier: 0.9 },
  { minQty: 0, multiplier: 1.0 },
];

export function calculateTotal(unitPrice: number, quantity: number): number {
  const tier = DISCOUNT_TIERS.find((t) => quantity >= t.minQty)!;
  return unitPrice * quantity * tier.multiplier;
}
```

重构后运行套件。仍然绿色，行为不变，结构改进。这是一个完整的周期。

## 工作流程：pytest 中的相同纪律

功能：一个密码强度验证器，边界优先驱动。

```python
# tests/test_password_policy.py
import pytest

from app.password_policy import validate


class TestValidate:
    def test_rejects_passwords_shorter_than_12_chars(self):
        # Arrange / Act
        result = validate("Short1!aaaa")  # 11 chars

        # Assert
        assert result.ok is False
        assert "at least 12 characters" in result.errors

    def test_accepts_a_12_char_password_meeting_all_rules(self):
        result = validate("Sturdy-Pass1")  # exactly 12

        assert result.ok is True
        assert result.errors == []
```

```bash
pytest tests/test_password_policy.py -x
# ModuleNotFoundError: No module named 'app.password_policy'  <- 正确的红色
```

最小绿色：

```python
# app/password_policy.py
from dataclasses import dataclass, field


@dataclass
class Result:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate(password: str) -> Result:
    if len(password) < 12:
        return Result(ok=False, errors=["at least 12 characters"])
    return Result(ok=True)
```

下一个红色驱动剩余规则——`parametrize` 让每个规则一个逻辑测试：

```python
    @pytest.mark.parametrize(
        ("password", "missing"),
        [
            ("alllowercase-12", "an uppercase letter"),
            ("ALLUPPERCASE-12", "a lowercase letter"),
            ("NoDigitsHere-Ab", "a digit"),
        ],
    )
    def test_reports_each_missing_character_class(self, password, missing):
        result = validate(password)

        assert result.ok is False
        assert missing in result.errors
```

绿色，然后重构规则检查为 `(predicate, message)` 对的表——与上面的折扣层级相同的移动。

## 模式

### 三角测量
当一个示例让你可以假它（`return 36.0`），添加第二个不同输入的示例。两个数据点强制泛化实现；这正是何时泛化，而不是之前。

### Arrange-Act-Assert，通过空白强制
每个测试读作三个由空行分隔的块。一个 Act 每个测试。如果你需要第二个 Act，你需要第二个测试。

### 测试列表作为草稿
开始前，将行为作为注释记下；一次将一个转换为真正的失败测试：

```typescript
// TODO test list — price-calculator
// [x] no discount under 10 units
// [x] 10% at 10+
// [x] 20% at 50+
// [ ] 用 RangeError 拒绝负数量
// [ ] 四舍五入到小数点后 2 位 (0.1 + 0.2 金钱 bug)
```

### 边界优先排序
在舒适的中点之前编写边界测试（正好 10 单位、正好 12 个字符）。边界错误生活在边界上；TDD 跳过它们什么也认证不了。

## 最佳实践

- 在周期中只运行专注的测试文件（`jest price-calculator --watch`，`pytest -x -k password`）；提交前运行完整套件。
- 在每个绿色时提交——`git commit` 在每个周期后给你一个可二分的历史和一个失败的免费撤销。
- 当错误报告到达时，在触摸修复之前编写重现它的失败测试。错误成为一个永久的回归防护。
- 保持单元测试无 I/O。如果一个测试需要网络或文件系统，它是一个集成测试；移动它并在单元测试中模拟端口。
- 将难以编写的测试视为设计反馈：太多 mock 意味着太多依赖；一个巨大的 Arrange 块意味着单元做得太多。

## 反模式

- **先写实现，再回填测试。** 那是测试后置；你失去了设计压力和验证红色的保证。测试将镜像代码的错误。
- **在任何实现之前编写一批失败的测试。** 你在反馈之前承诺了一个设计。一次一个红色。
- **跳过重构步骤数周。** 没有重构的红-绿-红-绿产生工作的意大利面条；第三个步骤是设计发生的地方。
- **断言你自己的代码的 mock**（`expect(repo.save).toHaveBeenCalled()` 作为唯一断言）。验证可观察的结果；仅交互测试在行为损坏时通过。
- **100% 覆盖率崇拜。** 覆盖率是 TDD 的副产品，不是目标。追逐 getter 的最后 4% 产生脆弱、无价值的测试。
- **当某些东西失败时同时改变测试和代码。** 改变一边，重新运行，然后另一边——否则你无法判断哪个改变修复了（或掩盖了）失败。

## 何时触发此技能

- 用户说 TDD、test-first、red-green-refactor 或"在代码之前写测试"。
- 实现一个新的纯逻辑模块（定价、验证、解析、日期数学），快速单元循环发挥优势。
- 请求错误修复——先用重现的失败测试驱动它。
- 用户想要学习或强制执行 Jest 或 pytest 中的 AAA 结构和行为测试命名。
- 代码审查揭示与内部耦合的测试或事后编写的测试，团队想要扭转这个习惯。
