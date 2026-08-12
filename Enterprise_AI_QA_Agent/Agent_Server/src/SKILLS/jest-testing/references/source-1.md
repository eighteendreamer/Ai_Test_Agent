---
name: Jest Unit Testing
description: Jest 单元测试模式,包含模拟、间谍、快照和异步测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit]
frameworks: [jest]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Jest 单元测试技能

你是一位专注于 Jest 单元测试的软件工程专家。当用户要求你编写、审查或调试 Jest 单元测试时,请遵循以下详细说明。

## 核心原则

1. **测试行为,而不是实现** -- 测试应验证代码做什么,而不是如何做。
2. **每个测试一个断言焦点** -- 每个测试应验证一个逻辑概念。
3. **Arrange-Act-Assert** -- 将每个测试结构化为设置、执行和验证。
4. **快速且隔离** -- 单元测试必须在毫秒内运行,没有外部依赖。
5. **描述性名称** -- 测试名称应作为代码行为的规格说明。

## 项目结构

```
src/
  services/
    user.service.ts
    user.service.test.ts
    order.service.ts
    order.service.test.ts
  utils/
    validators.ts
    validators.test.ts
    formatters.ts
    formatters.test.ts
  models/
    user.model.ts
  __mocks__/
    axios.ts
    database.ts
  __tests__/
    integration/
      user-order.test.ts
jest.config.ts
```

## 配置

```typescript
// jest.config.ts
import type { Config } from 'jest';

const config: Config = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: ['**/*.test.ts', '**/*.spec.ts'],
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts',
    '!src/**/*.test.ts',
    '!src/**/index.ts',
  ],
  coverageThresholds: {
    global: {
      branches: 80,
      functions: 80,
      lines: 80,
      statements: 80,
    },
  },
  coverageReporters: ['text', 'lcov', 'json-summary'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  setupFilesAfterSetup: ['<rootDir>/jest.setup.ts'],
  clearMocks: true,
  restoreMocks: true,
};

export default config;
```

## 编写测试

### 基本测试结构

```typescript
// validators.ts
export function isValidEmail(email: string): boolean {
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return regex.test(email);
}

export function isStrongPassword(password: string): boolean {
  return (
    password.length >= 8 &&
    /[A-Z]/.test(password) &&
    /[a-z]/.test(password) &&
    /[0-9]/.test(password) &&
    /[!@#$%^&*]/.test(password)
  );
}
```

```typescript
// validators.test.ts
import { isValidEmail, isStrongPassword } from './validators';

describe('isValidEmail', () => {
  it('should return true for valid email addresses', () => {
    expect(isValidEmail('user@example.com')).toBe(true);
    expect(isValidEmail('first.last@domain.co.uk')).toBe(true);
    expect(isValidEmail('user+tag@example.com')).toBe(true);
  });

  it('should return false for invalid email addresses', () => {
    expect(isValidEmail('')).toBe(false);
    expect(isValidEmail('not-an-email')).toBe(false);
    expect(isValidEmail('@missing-local.com')).toBe(false);
    expect(isValidEmail('missing-at.com')).toBe(false);
    expect(isValidEmail('spaces here@bad.com')).toBe(false);
  });
});

describe('isStrongPassword', () => {
  it('should accept a strong password', () => {
    expect(isStrongPassword('SecurePass1!')).toBe(true);
  });

  it('should reject passwords shorter than 8 characters', () => {
    expect(isStrongPassword('Ab1!')).toBe(false);
  });

  it('should reject passwords without uppercase letters', () => {
    expect(isStrongPassword('lowercase1!')).toBe(false);
  });

  it('should reject passwords without lowercase letters', () => {
    expect(isStrongPassword('UPPERCASE1!')).toBe(false);
  });

  it('should reject passwords without numbers', () => {
    expect(isStrongPassword('NoNumbers!')).toBe(false);
  });

  it('should reject passwords without special characters', () => {
    expect(isStrongPassword('NoSpecial1')).toBe(false);
  });
});
```

### 测试类和服务

```typescript
// user.service.ts
import { UserRepository } from './user.repository';
import { EmailService } from './email.service';

export class UserService {
  constructor(
    private userRepo: UserRepository,
    private emailService: EmailService
  ) {}

  async createUser(email: string, name: string): Promise<User> {
    const existing = await this.userRepo.findByEmail(email);
    if (existing) {
      throw new Error('User already exists');
    }

    const user = await this.userRepo.create({ email, name });
    await this.emailService.sendWelcomeEmail(user.email, user.name);
    return user;
  }

  async getUser(id: string): Promise<User | null> {
    return this.userRepo.findById(id);
  }

  async deleteUser(id: string): Promise<void> {
    const user = await this.userRepo.findById(id);
    if (!user) {
      throw new Error('User not found');
    }
    await this.userRepo.delete(id);
  }
}
```

```typescript
// user.service.test.ts
import { UserService } from './user.service';
import { UserRepository } from './user.repository';
import { EmailService } from './email.service';

// Mock 依赖
jest.mock('./user.repository');
jest.mock('./email.service');

describe('UserService', () => {
  let userService: UserService;
  let mockUserRepo: jest.Mocked<UserRepository>;
  let mockEmailService: jest.Mocked<EmailService>;

  beforeEach(() => {
    mockUserRepo = new UserRepository() as jest.Mocked<UserRepository>;
    mockEmailService = new EmailService() as jest.Mocked<EmailService>;
    userService = new UserService(mockUserRepo, mockEmailService);
  });

  describe('createUser', () => {
    it('should create a user and send welcome email', async () => {
      const newUser = { id: '1', email: 'new@example.com', name: 'New User' };
      mockUserRepo.findByEmail.mockResolvedValue(null);
      mockUserRepo.create.mockResolvedValue(newUser);
      mockEmailService.sendWelcomeEmail.mockResolvedValue(undefined);

      const result = await userService.createUser('new@example.com', 'New User');

      expect(result).toEqual(newUser);
      expect(mockUserRepo.findByEmail).toHaveBeenCalledWith('new@example.com');
      expect(mockUserRepo.create).toHaveBeenCalledWith({
        email: 'new@example.com',
        name: 'New User',
      });
      expect(mockEmailService.sendWelcomeEmail).toHaveBeenCalledWith(
        'new@example.com',
        'New User'
      );
    });

    it('should throw error if user already exists', async () => {
      mockUserRepo.findByEmail.mockResolvedValue({
        id: '1',
        email: 'existing@example.com',
        name: 'Existing',
      });

      await expect(
        userService.createUser('existing@example.com', 'Duplicate')
      ).rejects.toThrow('User already exists');

      expect(mockUserRepo.create).not.toHaveBeenCalled();
      expect(mockEmailService.sendWelcomeEmail).not.toHaveBeenCalled();
    });
  });

  describe('getUser', () => {
    it('should return user when found', async () => {
      const user = { id: '1', email: 'user@example.com', name: 'User' };
      mockUserRepo.findById.mockResolvedValue(user);

      const result = await userService.getUser('1');

      expect(result).toEqual(user);
      expect(mockUserRepo.findById).toHaveBeenCalledWith('1');
    });

    it('should return null when user not found', async () => {
      mockUserRepo.findById.mockResolvedValue(null);

      const result = await userService.getUser('nonexistent');

      expect(result).toBeNull();
    });
  });

  describe('deleteUser', () => {
    it('should delete an existing user', async () => {
      const user = { id: '1', email: 'user@example.com', name: 'User' };
      mockUserRepo.findById.mockResolvedValue(user);
      mockUserRepo.delete.mockResolvedValue(undefined);

      await userService.deleteUser('1');

      expect(mockUserRepo.delete).toHaveBeenCalledWith('1');
    });

    it('should throw error when deleting non-existent user', async () => {
      mockUserRepo.findById.mockResolvedValue(null);

      await expect(userService.deleteUser('nonexistent')).rejects.toThrow(
        'User not found'
      );
    });
  });
});
```

## 模拟模式

### 手动模拟

```typescript
// __mocks__/axios.ts
const axios = {
  get: jest.fn(() => Promise.resolve({ data: {} })),
  post: jest.fn(() => Promise.resolve({ data: {} })),
  put: jest.fn(() => Promise.resolve({ data: {} })),
  delete: jest.fn(() => Promise.resolve({ data: {} })),
  create: jest.fn(function () {
    return axios;
  }),
  interceptors: {
    request: { use: jest.fn() },
    response: { use: jest.fn() },
  },
};

export default axios;
```

### 监视方法

```typescript
it('should call console.error on failure', async () => {
  const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

  await processData(invalidData);

  expect(consoleSpy).toHaveBeenCalledWith(
    expect.stringContaining('Processing failed')
  );

  consoleSpy.mockRestore();
});
```

### 模拟计时器

```typescript
describe('Debounce function', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('should debounce function calls', () => {
    const fn = jest.fn();
    const debounced = debounce(fn, 300);

    debounced();
    debounced();
    debounced();

    expect(fn).not.toHaveBeenCalled();

    jest.advanceTimersByTime(300);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('should reset timer on subsequent calls', () => {
    const fn = jest.fn();
    const debounced = debounce(fn, 300);

    debounced();
    jest.advanceTimersByTime(200);
    debounced(); // 重置计时器
    jest.advanceTimersByTime(200);

    expect(fn).not.toHaveBeenCalled();

    jest.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
```

### 模拟模块

```typescript
// 模拟整个模块
jest.mock('fs', () => ({
  readFileSync: jest.fn(() => 'mocked content'),
  writeFileSync: jest.fn(),
  existsSync: jest.fn(() => true),
}));

// 使用工厂函数模拟
jest.mock('./config', () => ({
  getConfig: () => ({
    apiUrl: 'http://test-api.example.com',
    timeout: 1000,
  }),
}));

// 部分模拟 -- 保留一些原始实现
jest.mock('./utils', () => ({
  ...jest.requireActual('./utils'),
  fetchData: jest.fn(),
}));
```

## 异步测试

```typescript
// 测试已解决的 promise
it('should resolve with data', async () => {
  const result = await fetchUser('1');
  expect(result.name).toBe('John');
});

// 测试已拒绝的 promise
it('should reject with error', async () => {
  await expect(fetchUser('invalid')).rejects.toThrow('Not found');
});

// 测试回调
it('should call callback with data', (done) => {
  fetchUserCallback('1', (err, data) => {
    expect(err).toBeNull();
    expect(data.name).toBe('John');
    done();
  });
});

// 测试事件发射器
it('should emit data event', (done) => {
  const emitter = new DataEmitter();
  emitter.on('data', (payload) => {
    expect(payload).toEqual({ id: 1 });
    done();
  });
  emitter.start();
});
```

## 快照测试

```typescript
// 组件快照
it('should render correctly', () => {
  const output = renderComponent({ name: 'Test', count: 5 });
  expect(output).toMatchSnapshot();
});

// 内联快照
it('should format user display name', () => {
  const result = formatDisplayName({ first: 'John', last: 'Doe' });
  expect(result).toMatchInlineSnapshot(`"John Doe"`);
});

// 自定义序列化器
expect.addSnapshotSerializer({
  test: (val) => val instanceof Date,
  print: (val) => `Date(${(val as Date).toISOString()})`,
});
```

## 自定义匹配器

```typescript
// jest.setup.ts
expect.extend({
  toBeWithinRange(received: number, floor: number, ceiling: number) {
    const pass = received >= floor && received <= ceiling;
    return {
      pass,
      message: () =>
        `expected ${received} to be within range ${floor} - ${ceiling}`,
    };
  },

  toBeValidEmail(received: string) {
    const pass = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(received);
    return {
      pass,
      message: () => `expected "${received}" to be a valid email address`,
    };
  },

  toContainObject(received: any[], expected: Record<string, any>) {
    const pass = received.some((item) =>
      Object.entries(expected).every(([key, value]) => item[key] === value)
    );
    return {
      pass,
      message: () =>
        `expected array to contain object matching ${JSON.stringify(expected)}`,
    };
  },
});

// 类型声明
declare global {
  namespace jest {
    interface Matchers<R> {
      toBeWithinRange(floor: number, ceiling: number): R;
      toBeValidEmail(): R;
      toContainObject(expected: Record<string, any>): R;
    }
  }
}
```

## 测试工具

### 测试数据辅助函数

```typescript
export function createMockUser(overrides: Partial<User> = {}): User {
  return {
    id: '1',
    email: 'test@example.com',
    name: 'Test User',
    role: 'user',
    createdAt: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

export function createMockResponse<T>(data: T, status = 200) {
  return {
    data,
    status,
    headers: {},
    config: {},
    statusText: 'OK',
  };
}
```

## 最佳实践

1. **每个测试一个逻辑断言** -- 如果验证一个概念,多个 `expect` 调用是可以的。
2. **使用 `describe` 块** 按方法或功能组织测试。
3. **将测试命名为规格说明** -- `it('should return null when user not found')`。
4. **在边界模拟** -- 模拟外部服务,而不是内部函数。
5. **使用 `beforeEach` 进行设置** -- 确保每个测试都有干净状态。
6. **在配置中设置 `clearMocks: true`** -- 自动清除测试之间的模拟状态。
7. **优先使用 `mockResolvedValue` 而不是 `mockImplementation`** 用于简单返回。
8. **测试边界情况** -- 空字符串、null、undefined、零、负数。
9. **保持测试快速** -- 慢的单元测试通常测试太多。
10. **维护覆盖率阈值** -- 设置最小值并在 CI 中强制执行。

## 应避免的反模式

1. **测试实现细节** -- 重构不应破坏测试。
2. **过度模拟** -- 如果你模拟一切,你什么都没有测试。
3. **共享可变状态** -- 永远不要使用在测试之间修改的 `let` 变量而没有 `beforeEach`。
4. **直接测试私有方法** -- 通过公共 API 测试。
5. **快照滥用** -- 不要对大对象进行快照;diff 变得毫无意义。
6. **没有断言** -- 没有 `expect()` 的测试总是通过,什么都测试不了。
7. **忽略测试失败** -- 永远不要在提交的代码中使用 `test.skip` 或 `.only`。
8. **测试框架代码** -- 不要测试 `Array.map` 是否有效。
9. **巨大的测试文件** -- 保持测试文件专注且少于 300 行。
10. **不测试错误路径** -- catch/error 分支也需要测试。

## 运行测试

```bash
# 运行所有测试
npx jest

# 运行特定文件
npx jest src/services/user.service.test.ts

# 运行匹配模式的测试
npx jest --testPathPattern="user"

# 带覆盖率运行
npx jest --coverage

# 监视模式
npx jest --watch

# 只运行更改的文件
npx jest --onlyChanged

# 详细输出
npx jest --verbose
```