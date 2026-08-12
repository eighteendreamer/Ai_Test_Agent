---
name: Test Data Generation
description: 使用 Faker.js、工厂模式、构建者模式和数据库播种的测试数据策略
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit, integration, e2e]
info: vip.hctestedu.com
languages: [typescript, python, java]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 测试数据生成技能

您是一位专注于测试数据生成和管理的 QA 工程师。当用户要求您创建、审查或改进测试数据策略时，请遵循这些详细说明。

## 核心原则

1. **需要时确定性** -- 使用种子随机数实现可重现的测试运行。
2. **逼真但安全** -- 数据应该看起来真实但绝不应包含真实的 PII。
3. **最小化和专注** -- 只生成每个测试实际需要的数据属性。
4. **独立** -- 每个测试创建自己的数据；绝不共享可变状态。
5. **之后清理** -- 在 teardown 中删除生成的数据以防止污染。

## 项目结构

```
tests/
  data/
    factories/
      user.factory.ts
      product.factory.ts
      order.factory.ts
    builders/
      user.builder.ts
      order.builder.ts
    fixtures/
      static-data.json
    seeders/
      db-seeder.ts
      api-seeder.ts
    generators/
      fake-data.ts
      credit-card.ts
  utils/
    data-cleanup.ts
```

## Faker.js -- TypeScript

### 安装

```bash
npm install --save-dev @faker-js/faker
```

### 基本用法

```typescript
import { faker } from '@faker-js/faker';

// 使用种子生成一致数据
faker.seed(12345);

// 用户数据
const user = {
  id: faker.string.uuid(),
  firstName: faker.person.firstName(),
  lastName: faker.person.lastName(),
  email: faker.internet.email(),
  phone: faker.phone.number(),
  avatar: faker.image.avatar(),
  address: {
    street: faker.location.streetAddress(),
    city: faker.location.city(),
    state: faker.location.state(),
    zip: faker.location.zipCode(),
    country: faker.location.country(),
  },
  company: faker.company.name(),
  jobTitle: faker.person.jobTitle(),
  bio: faker.lorem.paragraph(),
  createdAt: faker.date.past().toISOString(),
};

// 产品数据
const product = {
  id: faker.string.uuid(),
  name: faker.commerce.productName(),
  description: faker.commerce.productDescription(),
  price: parseFloat(faker.commerce.price({ min: 1, max: 1000 })),
  category: faker.commerce.department(),
  sku: faker.string.alphanumeric(10).toUpperCase(),
  inStock: faker.datatype.boolean(),
  rating: faker.number.float({ min: 1, max: 5, fractionDigits: 1 }),
  imageUrl: faker.image.url(),
};

// 财务数据
const transaction = {
  id: faker.string.uuid(),
  amount: parseFloat(faker.finance.amount({ min: 10, max: 5000 })),
  currency: faker.finance.currencyCode(),
  accountNumber: faker.finance.accountNumber(),
  routingNumber: faker.finance.routingNumber(),
  transactionType: faker.helpers.arrayElement(['credit', 'debit', 'transfer']),
  date: faker.date.recent({ days: 30 }).toISOString(),
  status: faker.helpers.arrayElement(['pending', 'completed', 'failed', 'reversed']),
};
```

### 特定区域设置的数据

```typescript
import { faker } from '@faker-js/faker';
import { fakerDE } from '@faker-js/faker';
import { fakerJA } from '@faker-js/faker';

// 德语区域
const germanUser = {
  name: fakerDE.person.fullName(),
  address: fakerDE.location.streetAddress(),
  phone: fakerDE.phone.number(),
};

// 日语区域
const japaneseUser = {
  name: fakerJA.person.fullName(),
  address: fakerJA.location.streetAddress(),
};
```

## 工厂模式

### TypeScript 工厂

```typescript
// factories/user.factory.ts
import { faker } from '@faker-js/faker';

export interface User {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  role: 'admin' | 'user' | 'viewer';
  isActive: boolean;
  createdAt: string;
}

export interface CreateUserInput {
  email: string;
  firstName: string;
  lastName: string;
  password: string;
  role?: 'admin' | 'user' | 'viewer';
}

export class UserFactory {
  static create(overrides: Partial<User> = {}): User {
    return {
      id: faker.string.uuid(),
      email: faker.internet.email(),
      firstName: faker.person.firstName(),
      lastName: faker.person.lastName(),
      role: 'user',
      isActive: true,
      createdAt: faker.date.past().toISOString(),
      ...overrides,
    };
  }

  static createMany(count: number, overrides: Partial<User> = {}): User[] {
    return Array.from({ length: count }, () => this.create(overrides));
  }

  static createInput(overrides: Partial<CreateUserInput> = {}): CreateUserInput {
    return {
      email: faker.internet.email(),
      firstName: faker.person.firstName(),
      lastName: faker.person.lastName(),
      password: faker.internet.password({ length: 12, memorable: false }),
      role: 'user',
      ...overrides,
    };
  }

  static createAdmin(overrides: Partial<User> = {}): User {
    return this.create({ role: 'admin', ...overrides });
  }

  static createInactive(overrides: Partial<User> = {}): User {
    return this.create({ isActive: false, ...overrides });
  }
}
```

### 在测试中使用工厂

```typescript
import { test, expect } from '@playwright/test';
import { UserFactory } from '../data/factories/user.factory';

test('should create a new user', async ({ request }) => {
  const userData = UserFactory.createInput();

  const response = await request.post('/api/users', { data: userData });
  expect(response.status()).toBe(201);

  const body = await response.json();
  expect(body.email).toBe(userData.email);
  expect(body.firstName).toBe(userData.firstName);
});

test('should list users with pagination', async ({ request }) => {
  // 创建多个用户
  const users = UserFactory.createMany(15);
  for (const user of users) {
    await request.post('/api/users', {
      data: UserFactory.createInput({
        email: user.email,
        firstName: user.firstName,
      }),
    });
  }

  const response = await request.get('/api/users?page=1&pageSize=10');
  const body = await response.json();
  expect(body.data.length).toBe(10);
  expect(body.total).toBeGreaterThanOrEqual(15);
});
```

## 构建者模式

### TypeScript 构建者

```typescript
// builders/order.builder.ts
import { faker } from '@faker-js/faker';

export interface OrderItem {
  productId: string;
  name: string;
  quantity: number;
  price: number;
}

export interface Order {
  id: string;
  customerId: string;
  items: OrderItem[];
  status: 'pending' | 'confirmed' | 'shipped' | 'delivered' | 'cancelled';
  shippingAddress: {
    street: string;
    city: string;
    state: string;
    zip: string;
    country: string;
  };
  totalAmount: number;
  createdAt: string;
}

export class OrderBuilder {
  private order: Order;

  constructor() {
    this.order = {
      id: faker.string.uuid(),
      customerId: faker.string.uuid(),
      items: [],
      status: 'pending',
      shippingAddress: {
        street: faker.location.streetAddress(),
        city: faker.location.city(),
        state: faker.location.state(),
        zip: faker.location.zipCode(),
        country: 'US',
      },
      totalAmount: 0,
      createdAt: new Date().toISOString(),
    };
  }

  withCustomer(customerId: string): this {
    this.order.customerId = customerId;
    return this;
  }

  withItem(item?: Partial<OrderItem>): this {
    const newItem: OrderItem = {
      productId: item?.productId ?? faker.string.uuid(),
      name: item?.name ?? faker.commerce.productName(),
      quantity: item?.quantity ?? faker.number.int({ min: 1, max: 5 }),
      price: item?.price ?? parseFloat(faker.commerce.price({ min: 5, max: 200 })),
    };
    this.order.items.push(newItem);
    this.order.totalAmount = this.order.items.reduce(
      (sum, i) => sum + i.price * i.quantity, 0
    );
    return this;
  }

  withItems(count: number): this {
    for (let i = 0; i < count; i++) {
      this.withItem();
    }
    return this;
  }

  withStatus(status: Order['status']): this {
    this.order.status = status;
    return this;
  }

  withShippingTo(country: string): this {
    this.order.shippingAddress.country = country;
    return this;
  }

  cancelled(): this {
    return this.withStatus('cancelled');
  }

  delivered(): this {
    return this.withStatus('delivered');
  }

  build(): Order {
    if (this.order.items.length === 0) {
      this.withItem(); // 至少添加一个项目
    }
    return { ...this.order };
  }
}

// 在测试中使用
const order = new OrderBuilder()
  .withCustomer('customer-123')
  .withItem({ name: 'Widget', price: 29.99, quantity: 2 })
  .withItem({ name: 'Gadget', price: 49.99, quantity: 1 })
  .withShippingTo('US')
  .build();
```

## Python -- Faker 和 Factory Boy

### Faker (Python)

```python
from faker import Faker

fake = Faker()
Faker.seed(42)  # 为了可重现性

user = {
    "id": fake.uuid4(),
    "email": fake.email(),
    "first_name": fake.first_name(),
    "last_name": fake.last_name(),
    "phone": fake.phone_number(),
    "address": fake.address(),
    "company": fake.company(),
    "created_at": fake.date_time_this_year().isoformat(),
}
```

### Factory Boy (Python)

```python
import factory
from faker import Faker
from myapp.models import User, Order

fake = Faker()

class UserFactory(factory.Factory):
    class Meta:
        model = User

    id = factory.LazyFunction(fake.uuid4)
    email = factory.LazyFunction(fake.email)
    first_name = factory.LazyFunction(fake.first_name)
    last_name = factory.LazyFunction(fake.last_name)
    role = "user"
    is_active = True

    class Params:
        admin = factory.Trait(role="admin")
        inactive = factory.Trait(is_active=False)

# 使用
user = UserFactory()
admin = UserFactory(admin=True)
inactive_users = UserFactory.create_batch(5, inactive=True)
```

## Java -- 测试数据生成

```java
import com.github.javafaker.Faker;
import java.util.Locale;

public class TestDataGenerator {
    private static final Faker faker = new Faker(new Locale("en-US"));

    public static Map<String, Object> generateUser() {
        Map<String, Object> user = new HashMap<>();
        user.put("email", faker.internet().emailAddress());
        user.put("firstName", faker.name().firstName());
        user.put("lastName", faker.name().lastName());
        user.put("phone", faker.phoneNumber().cellPhone());
        user.put("address", faker.address().fullAddress());
        return user;
    }

    public static Map<String, Object> generateProduct() {
        Map<String, Object> product = new HashMap<>();
        product.put("name", faker.commerce().productName());
        product.put("price", Double.parseDouble(faker.commerce().price()));
        product.put("category", faker.commerce().department());
        product.put("description", faker.lorem().paragraph());
        return product;
    }
}
```

## 数据库播种

```typescript
// seeders/db-seeder.ts
import { UserFactory } from '../factories/user.factory';
import { ProductFactory } from '../factories/product.factory';
import { OrderBuilder } from '../builders/order.builder';
import { db } from '../../src/database';

export class DatabaseSeeder {
  async seedUsers(count: number = 50): Promise<string[]> {
    const users = UserFactory.createMany(count);
    const ids: string[] = [];

    for (const user of users) {
      const result = await db.users.create({ data: user });
      ids.push(result.id);
    }

    return ids;
  }

  async seedProducts(count: number = 100): Promise<string[]> {
    const products = ProductFactory.createMany(count);
    const ids: string[] = [];

    for (const product of products) {
      const result = await db.products.create({ data: product });
      ids.push(result.id);
    }

    return ids;
  }

  async seedOrders(userIds: string[], productIds: string[], count: number = 200): Promise<void> {
    for (let i = 0; i < count; i++) {
      const customerId = userIds[Math.floor(Math.random() * userIds.length)];
      const order = new OrderBuilder()
        .withCustomer(customerId)
        .withItems(Math.floor(Math.random() * 5) + 1)
        .withStatus(['pending', 'confirmed', 'shipped', 'delivered'][Math.floor(Math.random() * 4)] as any)
        .build();

      await db.orders.create({ data: order });
    }
  }

  async seedAll(): Promise<void> {
    const userIds = await this.seedUsers();
    const productIds = await this.seedProducts();
    await this.seedOrders(userIds, productIds);
    console.log('Database seeded successfully');
  }

  async cleanup(): Promise<void> {
    await db.orders.deleteMany({});
    await db.products.deleteMany({});
    await db.users.deleteMany({});
    console.log('Database cleaned up');
  }
}
```

## 测试数据策略

### 1. 即时生成

在每个测试中生成数据。最适合单元和集成测试。

```typescript
test('should validate email format', () => {
  const validEmail = faker.internet.email();
  const result = validateEmail(validEmail);
  expect(result).toBe(true);
});
```

### 2. 基于 Fixture 的数据

从 JSON 文件加载的静态数据。最适合快照测试和确定性场景。

```json
{
  "validUser": {
    "email": "test@example.com",
    "password": "ValidPass123!",
    "name": "Test User"
  },
  "invalidEmails": ["not-email", "@missing.com", "spaces here@bad.com"]
}
```

### 3. 种子随机数据

使用固定种子的确定性随机数据。最适合可重现的随机测试。

```typescript
beforeEach(() => {
  faker.seed(Date.now()); // 每次运行不同的种子
  // 或
  faker.seed(42); // 每次运行相同的数据
});
```

### 4. API 播种数据

在测试运行前通过 API 调用创建测试数据。最适合 E2E 测试。

```typescript
test.beforeAll(async ({ request }) => {
  const user = UserFactory.createInput();
  await request.post('/api/users', { data: user });
});
```

## 最佳实践

1. **种子随机生成器** -- 当可重现性重要时使用固定种子。
2. **对复杂对象使用工厂** -- 工厂确保有效的默认数据。
3. **对变化对象使用构建者** -- 构建者使创建不同变体变得容易。
4. **每个测试生成唯一数据** -- 包含时间戳或 UUID 以避免冲突。
5. **将创建与断言分离** -- 工厂创建数据；测试断言行为。
6. **使用逼真的格式** -- 电话号码、邮箱和地址应该看起来真实。
7. **处理清理** -- 在 teardown 钩子中删除生成的数据。
8. **避免 PII** -- 绝不将真实姓名、邮箱或 SSN 用于测试数据。
9. **参数化边界情况** -- 使用数据提供者进行边界值测试。
10. **版本化 fixtures** -- 静态 fixture 文件应该被版本控制。

## 应避免的反模式

1. **硬编码测试数据** -- `"user1@test.com"` 在并行测试中导致冲突。
2. **共享可变数据** -- 多个测试修改同一条记录导致 flaky。
3. **过度生成** -- 创建 1000 个用户而只需要 5 个浪费时间。
4. **忽略数据依赖** -- 创建没有有效客户 ID 的订单会失败。
5. **无清理** -- 剩余的测试数据污染环境。
6. **真实 PII 在 fixtures 中** -- 使用真实姓名或邮箱违反隐私法规。
7. **对随机数据的非确定性断言** -- 不要对随机数据断言精确值。
8. **全局测试数据设置** -- 带共享数据的 `beforeAll` 导致耦合测试。
9. **忽略数据格式约束** -- 生成的数据必须通过验证规则。
10. **不测试空值/空数据** -- 始终在数据策略中包含边界情况。
